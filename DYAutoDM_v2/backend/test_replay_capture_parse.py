# -*- coding: utf-8 -*-
"""回放驱动测试：`parse_init_protobuf` 的回归验证（替代此前的"空壳验收"）。

## 背景：为什么必须重写

原 `backend/scripts/verify_capture_parse.py` 的 C 段（功能验收）依赖
`<root>/_repro352_fresh/init_live.bin`；该文件随 2026-09-20 测试根删除而消失
⇒ 每次运行都走 `[SKIP] 未找到真实首包样本` + `check("C0 首包样本存在", False)`。
即 **那段功能验收长期是空的**，"首包解析修复已验收"从未真正执行过。

现在它跑在冻结样本上：**零 BCC、零网络、零真实数据根**，秒级，可并发。

## 判据来源（禁止硬编码）

期望读数一律从 `replay/fixtures/manifest.json`（SSOT）读，避免"用例写死 44、
样本换成 45"的双写漂移。

## 单进程串库防护（必读）

`unittest discover` 会把全部 `test_*.py` 导入**同一进程**，而 `database.py` 在
**导入时**按 `DY_APP_ROOT` 固化 DB 路径（本项目 §5.6 记录过该缺陷的真实发作）。
故本模块：
  · 用 `setdefault` 而**非**直接赋值 —— 套件里已由 `test_config_isolation` 钉过的根
    不被本模块抢占；
  · 独立运行时才回落到固定的临时根（**不删**，避免"路径被固化后目录消失"）；
  · 断言当前根**不是**真实数据根。
"""

import os
import sys
import tempfile
import unittest

_BACKEND = os.path.dirname(os.path.abspath(__file__))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

# 回落根：独立运行时用；已存在则不动（不抢套件的隔离根）。
# 🔴 2026-10-04 收编（M-31 ③）：setdefault → 显式赋值（抗外部污染 + 抗同进程串扰）。
_FALLBACK_ROOT = os.path.join(tempfile.gettempdir(), "dyautodm_replay_root")
os.makedirs(_FALLBACK_ROOT, exist_ok=True)
os.environ["DY_APP_ROOT"] = _FALLBACK_ROOT

from replay import loader, selftest                # noqa: E402
from replay.sandbox import DESIGN_ROOT, LEGACY_ROOT  # noqa: E402

import auto_dm.conversation_capture as cc          # noqa: E402
import auto_dm.im_protobuf as pb                   # noqa: E402


def _self_uid(raw: bytes) -> str:
    """从 conv_id 自愈推断本账号 uid（与 parse_init_protobuf 内部同法）。"""
    from collections import Counter
    cnt = Counter()
    for c in set(m.decode() for m in pb.CONV_RE.findall(raw)):
        a, b = c.split(":")[2], c.split(":")[3]
        cnt[a] += 1
        cnt[b] += 1
    return cnt.most_common(1)[0][0]


def _measure(raw: bytes, my_uid: str) -> dict:
    convs = cc.parse_init_protobuf(raw, my_uid)
    cids = [c["conversation_id"] for c in convs]
    roles = {}
    for c in convs:
        for m in (c.get("messages") or []):
            r = m.get("role")
            roles[r] = roles.get(r, 0) + 1
    return {
        "convs": len(convs),
        "unique_conv_id": len(set(cids)),
        "short_id_coverage": sum(1 for c in convs if c.get("short_id")),
        "total_msgs": sum(len(c.get("messages") or []) for c in convs),
        "convs_with_msgs": sum(1 for c in convs if (c.get("messages") or [])),
        "roles_them": roles.get("them", 0),
        "roles_me": roles.get("me", 0),
    }


class TestReplayIsolation(unittest.TestCase):
    """环境隔离门禁（防回退：这层永远不许碰真机/真库）。"""

    def test_app_root_not_real_root(self):
        r = os.path.abspath(os.environ["DY_APP_ROOT"])
        self.assertFalse(r.lower().startswith(DESIGN_ROOT.lower()),
                         f"DY_APP_ROOT 指向真实数据根：{r}")
        self.assertFalse(r.lower().startswith(LEGACY_ROOT.lower()),
                         f"DY_APP_ROOT 指向已废弃主分支根：{r}")

    def test_replay_pkg_has_no_network_imports(self):
        hits = selftest._scan_forbidden_imports(
            os.path.dirname(os.path.abspath(loader.__file__)))
        self.assertEqual(hits, [], f"回放层引入了打网/浏览器模块：{hits}")

    def test_loader_rejects_tampered_manifest(self):
        """篡改门禁（在假 store 内做，不碰仓库样本）。"""
        with selftest._temp_fixture_store() as tmp:
            rel = "f/f.bin"
            p = os.path.join(tmp, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as fh:
                fh.write(b"REAL")
            loader.write_manifest({"f": {"file": rel,
                                         "sha256": loader.sha256_of(b"OTHER"),
                                         "size": 4, "desc": "", "provenance": ""}})
            with self.assertRaises(loader.FixtureTampered):
                loader.load_fixture("f")

    def test_unknown_fixture_raises(self):
        with selftest._temp_fixture_store():
            loader.write_manifest({})
            with self.assertRaises(loader.FixtureMissing):
                loader.load_fixture("__nope__")


class TestInitPacketReplay(unittest.TestCase):
    """首包解析的回归判据（期望值来自 manifest SSOT）。"""

    @classmethod
    def setUpClass(cls):
        # P3-1：夹具缺失 = hard fail（不再 SkipTest 静默通过）
        cls.entry = loader.require_fixture("init_packet")
        cls.raw = loader.load_fixture("init_packet")
        cls.uid = cls.entry.get("account_uid") or _self_uid(cls.raw)
        cls.got = _measure(cls.raw, cls.uid)

    def test_fixture_integrity(self):
        self.assertEqual(loader.sha256_of(self.raw), self.entry["sha256"])
        self.assertEqual(len(self.raw), self.entry["size"])

    def test_expected_readings_match_manifest(self):
        expected = self.entry.get("expected") or {}
        self.assertTrue(expected, "清单缺少 expected 块，判据无来源")
        for k, want in expected.items():
            with self.subTest(metric=k):
                self.assertEqual(self.got.get(k), want,
                                 f"{k}: 实测 {self.got.get(k)} ≠ 清单 {want}")

    def test_short_id_fully_covered(self):
        """缺陷①（承载消息的会话 short_id 恒 None）必须不复发。"""
        self.assertEqual(self.got["short_id_coverage"], self.got["convs"])

    def test_messages_parsed(self):
        """缺陷②（首包消息恒 0）必须不复发。"""
        self.assertGreater(self.got["total_msgs"], 0)

    def test_conversation_with_messages_has_short_id(self):
        convs = cc.parse_init_protobuf(self.raw, self.uid)
        t = [c for c in convs if (c.get("messages") or [])]
        self.assertTrue(t, "解析结果中无含消息的会话")
        self.assertTrue(t[0].get("short_id"), "承载消息的会话 short_id 为空")

    def test_peer_uid_never_self(self):
        """D 方案事故形态：peer_uid 被写成自己。"""
        convs = cc.parse_init_protobuf(self.raw, self.uid)
        offenders = [c["conversation_id"] for c in convs
                     if str(c.get("peer_uid")) == str(self.uid)]
        self.assertEqual(offenders, [], f"peer_uid 被写成自己：{offenders[:3]}")


class TestSensitivity(unittest.TestCase):
    """证明断言是敏感的（不是恒真装饰）——必须真跑一次。"""

    def test_truncated_input_changes_reading(self):
        # 夹具缺失已在 setUpClass hard fail；此处 expected 缺失也当失败（判据无来源）
        expected = loader.describe("init_packet").get("expected") or {}
        self.assertTrue(expected, "清单缺 expected 块 ⇒ 判据无来源（hard fail）")
        raw = loader.load_fixture("init_packet")
        truncated = raw[:512]
        got = _measure(truncated, _self_uid(truncated))
        self.assertNotEqual(got.get("convs"), expected.get("convs"),
                            "截断输入仍得同样会话数 ⇒ 判据不敏感")


if __name__ == "__main__":
    unittest.main(verbosity=2)
