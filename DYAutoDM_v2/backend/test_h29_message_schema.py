# -*- coding: utf-8 -*-
"""ADR-012 消息落库 Schema 门禁（防复发）。

判据分三层，每层**都带负控**（证明门禁真会拦，不是装饰）：
  G1  未知类型强制降级（前向兼容铁律）
  G2  平台提示文案被识别为 system_notice 且不进 prompt
  G3  正常用户消息 / 媒体 必须放行（不被误伤）
  G4  读侧白名单按 kind；无 kind 的存量行回落 msg_type 判定
  G5  写入出口 tuple() 与 dm_messages 八列同序
  G6  写入点已收敛（wp_recv / capture 不再裸写列名）
  G7  生产库：全部行已标注 kind（迁移幂等）
  G8  生产库：AI 读侧无系统文案泄漏（真实调用 _build_history）
  ——— 负控 ———
  N1  从注册表删掉一个类型 → 该类型变 unknown 且不放行（证明判据依赖注册表，而非硬编码）
"""
import json
import os
import sqlite3
import sys
import unittest
from unittest import mock

_BACKEND = os.path.dirname(os.path.abspath(__file__))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from services import message_schema as ms  # noqa: E402

# 本次实测样本（2026-09-25，本账号 183 条）
SYS_SAMPLES = (
    "对方回复或关注你之前，只能发送一条文字消息。请礼貌发言，自觉遵守{{0}}",
    "对方回复你或互关之前，可发送一条文字消息。请礼貌发言，自觉遵守{{0}}",
)


class TestUnknownDegrades(unittest.TestCase):
    """G1：前向兼容铁律 —— 未登记类型绝不以「疑似用户消息」形态进 text。"""

    def test_g1_unknown_downgrades(self):
        r = ms.MessageRecord.build(text='{"weird":1}', msg_type="99999")
        self.assertEqual(r.kind, "unknown")
        self.assertNotIn('{"weird"', r.text)      # 原文不得留在 text
        self.assertTrue(r.text.startswith("[未知类型]"))
        self.assertEqual(r.extra.get("raw"), '{"weird":1}')  # 原文存 raw 可回溯

    def test_g1b_unknown_not_readable(self):
        r = ms.MessageRecord.build(text="x", msg_type="99999")
        self.assertFalse(ms.readable(r.text, r.msg_type, r.extra))


class TestSystemNotice(unittest.TestCase):
    """G2：平台提示文案 → system_notice，不进 prompt。"""

    def test_g2_system_texts_recognized(self):
        for t in SYS_SAMPLES:
            self.assertTrue(ms.is_system_text(t), f"未识别：{t[:20]}")

    def test_g2b_system_not_readable(self):
        for t in SYS_SAMPLES:
            r = ms.MessageRecord.build(text=t, msg_type="text")
            self.assertEqual(r.kind, "system_notice")
            self.assertFalse(ms.readable(r.text, r.msg_type, r.extra))

    def test_g2c_noise_prefix_is_system_notice(self):
        """G2c：噪音前缀行（H-25）在**写侧**即升格 system_notice，不得标 user_text。

        背景（2026-09-28）：`readable()` 对**有 kind** 的行只按白名单放行、
        不再回看前缀 ⇒ 若写侧把 `[系统提示]…` 标成 user_text，反而比无 kind 的
        存量行**更宽松**（存量行走 `is_noise_text` 分支被拦下）。
        """
        for t in ("[系统提示] 陌生人消息确认（#1）", "[投递验证] conv_id=x",
                  "[系统消息] 对方已上线", "[未知媒体] {}", "[未知类型1] {}"):
            r = ms.MessageRecord.build(text=t, msg_type="text")
            self.assertEqual(r.kind, "system_notice", f"未升格：{t[:12]}")
            self.assertFalse(ms.readable(r.text, r.msg_type, r.extra),
                             f"噪音前缀行仍可读：{t[:12]}")

    def test_g2d_delivery_marker_not_overridden(self):
        """G2d：msg_type 已登记为 delivery_marker 时，前缀规则不得再改写它。"""
        r = ms.MessageRecord.build(text="[投递验证] conv_id=x",
                                   msg_type="delivery_marker")
        self.assertEqual(r.kind, "delivery_marker")

    def test_g9_write_side_matches_migration_plan(self):
        """G9：写侧 kind 必须与 `scripts/migrate_message_kind.plan()` 判定一致。

        迁移的幂等规则是「已有 kind 即跳过」⇒ 写侧一旦错标，迁移**永不纠正**。
        故两侧口径必须同源；本门禁把 `plan()` 当**被测对象**跑同一批样本对拍。
        """
        import importlib.util
        p = os.path.join(os.path.dirname(_BACKEND), "scripts",
                         "migrate_message_kind.py")
        spec = importlib.util.spec_from_file_location("_mk_kind_migrate", p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        samples = [
            ("你好", "text"),
            ("[系统提示] 陌生人消息确认（#1）", "text"),
            ("[投递验证] conv_id=x", "text"),
            ("[投递验证] conv_id=x", "delivery_marker"),
            ("对方回复或关注你之前，只能发送一条文字消息。请礼貌发言，自觉遵守{{0}}", "text"),
            ("[未知类型9] {}", "99999"),
            ("你好", "99999"),
            ("", "text"),
        ]
        rows = [{"id": i, "text": t, "msg_type": mt, "extra": ""}
                for i, (t, mt) in enumerate(samples)]
        todo = {x["id"]: x["kind"] for x in mod.plan(rows)}
        self.assertEqual(set(todo), set(range(len(samples))),
                         "样本应全部未标注（plan 只收未标注行）")
        for i, (t, mt) in enumerate(samples):
            made = ms.MessageRecord.build(text=t, msg_type=mt)
            self.assertEqual(todo[i], made.kind,
                             f"迁移与写侧判定分叉：{t[:12]!r} / msg_type={mt!r}")


class TestNoFalsePositive(unittest.TestCase):
    """G3：正常消息必须放行（否则门禁会误伤业务）。"""

    def test_g3_plain_text_ok(self):
        r = ms.MessageRecord.build(text="工伤怎么赔", msg_type="text")
        self.assertEqual(r.kind, "user_text")
        self.assertTrue(ms.readable(r.text, r.msg_type, r.extra))

    def test_g3b_media_ok(self):
        r = ms.MessageRecord.build(text="[图片] data:image/webp;base64,AAAA", msg_type="27")
        self.assertEqual(r.text, "[图片]")
        self.assertEqual(r.kind, "media")
        self.assertTrue(ms.readable(r.text, r.msg_type, r.extra))

    def test_g3c_empty_text_ok(self):
        r = ms.MessageRecord.build(text="", msg_type="text")
        self.assertEqual(r.kind, "user_text")


class TestReadableFallback(unittest.TestCase):
    """G4：无 kind 的存量行回落 msg_type + 文案判定（不得静默放行）。"""

    def test_g4_no_kind_uses_msgtype(self):
        self.assertTrue(ms.readable("你好", "text", {}))
        self.assertFalse(ms.readable("你好", "99999", {}))   # 未登记 → 不放行

    def test_g4b_no_kind_system_text_blocked(self):
        # 存量行：无 kind，但文案是系统提示 ⇒ 仍须拦下
        self.assertFalse(ms.readable(SYS_SAMPLES[0], "text", {}))

    def test_g4c_kind_wins(self):
        # 有 kind 时以 kind 为准（白名单）
        self.assertFalse(ms.readable("你好", "text", {"kind": "system_notice"}))


class TestWriteExit(unittest.TestCase):
    """G5 / G6：单一写入出口。"""

    def test_g5_tuple_order(self):
        r = ms.MessageRecord.build(text="t", msg_type="text")
        got = r.tuple("acct", "cid", ts=1.0, msg_id="m1", role="me")
        self.assertEqual(len(got), 8)
        self.assertEqual(got[0], "acct")
        self.assertEqual(got[1], "cid")
        self.assertEqual(got[2], "me")
        self.assertEqual(got[3], "t")
        self.assertEqual(got[5], r.extra_json())

    def test_g6_all_seven_write_points_converged(self):
        """G6：7 处写入点**全部**经单一出口（不得再裸写列名元组）。"""
        import re
        targets = {
            "auto_dm/conversation_capture.py": [
                (b"_rec_of(m, _extra).tuple", 2),     # 首包 + 补全，共两处
            ],
            "daemon/recv_daemon.py": [
                (b"_msg_tuple(m, ib.name, conv_id)", 2),   # init 同步两处
                (b"_ws_tuple(self.name, conv_id", 1),       # WS 实时
            ],
            "daemon/wp_recv.py": [
                (b"rec.extra_json()", 1),
            ],
            "database.py": [
                (b"_rec.tuple(", 1),                        # 迁移路径
            ],
        }
        for rel, pats in targets.items():
            src = open(os.path.join(_BACKEND, rel), "rb").read()
            for pat, want in pats:
                self.assertEqual(src.count(pat), want,
                                 f"{rel} 出口调用 {pat!r} 命中 {src.count(pat)}，期望 {want}")

    def test_g6b_no_raw_column_tuple_left(self):
        """负控：不得再有「手写列名元组」形态的插入。

        ⚠️ 模式必须锚定**裸元组起始**（缩进后紧跟 `(`），否则会误命中
        已修好的 `_ws_tuple(self.name, conv_id, …)` 那一行本身（实测踩到）。
        """
        import re
        checks = {
            # ⚠️ 锚定「行首缩进后紧跟 ( 」才算裸元组；否则会命中
            # `_ws_tuple(self.name, …)` 的参数列表（该函数名本身以 ( 结尾）。
            "daemon/recv_daemon.py":
                rb'(?m)^\s*\(\s*self\.name, conv_id, role, text, msg_type,',
            "database.py":
                rb'(?m)^\s*\(\s*acct, conv_id, msg\.get\("role", "them"\),',
            "auto_dm/conversation_capture.py":
                rb'(?m)^\s*\(\s*name, cid, m\["role"\], m\["text"\], "text", _extra',
        }
        for rel, pat in checks.items():
            src = open(os.path.join(_BACKEND, rel), "rb").read()
            hits = re.findall(pat, src)
            self.assertEqual(len(hits), 0,
                             f"{rel} 仍存在裸写列名元组 {len(hits)} 处")

    def test_g6c_patch_path_extra_carries_kind(self):
        """G6c：补写路径（`UPDATE dm_messages SET extra=?`）写的 JSON 必须自带 kind。

        背景（2026-09-28 实测）：`capture_all` 的「补写」逻辑有两条
        `UPDATE dm_messages SET extra=?` —— 它们**直接写原始 `_extra` JSON，
        不经 `MessageRecord.build`**（ADR-012 层 2 唯一出口）⇒ 该路径写入的行
        永缺 `extra.kind`。实测生产库 **103 行**未标注全部出自此路径（G7 红）。

        本门禁锁死「写这两条 UPDATE 之前，kind 已并入 `_ex`」这一形态；
        **行为兜底是 G7**（活体库全行已标注）。
        """
        import re
        src = open(os.path.join(_BACKEND, "auto_dm", "conversation_capture.py"),
                   encoding="utf-8").read()
        # ① 补写路径仍是两条 UPDATE（形态变更时本门禁必须被同步审视，不得默默失效）
        self.assertEqual(src.count("UPDATE dm_messages SET extra=?"), 2,
                         "补写路径的 UPDATE 数量变了 —— 请同步审视本门禁判据")
        # ② `_extra` 唯一产出点：产出前必须已把 kind 并入 _ex
        m = re.search(r"if _ex:\s*(.{0,800}?)_extra = _json\.dumps\(_ex",
                      src, re.S)
        self.assertIsNotNone(m, "未找到 `_extra` 产出点（写侧结构已变）")
        self.assertIn('"kind"', m.group(1),
                      "补写路径写库前未并入 extra.kind —— ADR-012 唯一出口被绕过（G7 将复发）")


class TestLiveData(unittest.TestCase):
    """G7 / G8：生产数据面（库不存在则跳过，不假装通过）。"""

    @classmethod
    def setUpClass(cls):
        cls.db = ""
        roots = []
        ev = os.environ.get("DY_APP_ROOT", "")
        if ev:
            roots.append(ev)
        roots += [r"C:\temp\dyautodm_design", r"C:\temp\dyautodm_test"]
        for root in roots:
            mdir = os.path.join(root, "members")
            if not os.path.isdir(mdir):
                continue
            for name in os.listdir(mdir):
                p = os.path.join(mdir, name, "data", "dyautodm.db")
                if not os.path.isfile(p):
                    continue
                try:
                    c = sqlite3.connect(p)
                    hit = c.execute(
                        "SELECT 1 FROM dm_conversations WHERE account='四川工伤张老师' "
                        "LIMIT 1").fetchone()
                    c.close()
                except Exception:
                    continue
                if hit:
                    cls.db = p
                    break
            if cls.db:
                break

    def _conn(self):
        if not self.db:
            self.skipTest("未找到生产库（设 DY_APP_ROOT 后重跑）")
        c = sqlite3.connect(self.db)
        c.row_factory = sqlite3.Row
        return c

    def test_g7_all_rows_labeled(self):
        c = self._conn()
        left = c.execute(
            "SELECT COUNT(*) n FROM dm_messages WHERE extra IS NOT NULL "
            "AND extra<>'' AND extra NOT LIKE '%\"kind\"%'").fetchone()["n"]
        total = c.execute("SELECT COUNT(*) n FROM dm_messages").fetchone()["n"]
        c.close()
        self.assertEqual(left, 0, f"仍有 {left}/{total} 行未标注 kind")

    def test_g8_no_system_text_in_prompt(self):
        from services.ai_reply import AutoReplyWorker
        c = self._conn()
        convs = c.execute(
            "SELECT DISTINCT conv_id FROM dm_conversations "
            "WHERE account='四川工伤张老师'").fetchall()
        c.close()
        w = AutoReplyWorker.__new__(AutoReplyWorker)
        bad = 0
        for (cid,) in convs:
            h = w._build_history("四川工伤张老师", cid, 10**9,
                                 {"max_history": 0, "vision_enabled": False,
                                  "context_window": 65536})
            t = "\n".join(x["content"] for x in h)
            for s in ("对方回复或关注你之前", "对方回复你或互关之前",
                      "[投递验证]", "[系统提示]", "base64", "data:image"):
                if s in t:
                    bad += 1
        self.assertEqual(bad, 0, f"{bad} 处系统文案/base64 仍进 prompt")


class TestNegativeControl(unittest.TestCase):
    """N1：删掉注册表条目 → 该类型必须变 unknown 且不放行。

    证明门禁依赖**注册表**（SSOT），而非把结论硬编码在判据里。
    """

    def test_n1_remove_registry_entry(self):
        key = "15"
        self.assertIn(key, ms.MSG_TYPES)
        saved = ms.MSG_TYPES[key]
        try:
            del ms.MSG_TYPES[key]
            kind, known = ms.kind_of(key)
            self.assertFalse(known, "删表后仍被认作已知 → 判据未依赖注册表")
            self.assertEqual(kind, "unknown")
            r = ms.MessageRecord.build(text="平台提示", msg_type=key)
            self.assertEqual(r.kind, "unknown")
            self.assertFalse(ms.readable(r.text, r.msg_type, r.extra))
        finally:
            ms.MSG_TYPES[key] = saved
        # 还原后行为恢复
        self.assertTrue(ms.kind_of(key)[1])


class TestDedupKeyUsesIdentity(unittest.TestCase):
    """M-16 防复发门禁（2026-09-27）。

    背景：消息去重键原为 `(conv_id, text[:50])` —— 键选了**内容字段**。
    H-25 统一落库契约把图片消息 text 归一为「[图片]」后，同会话多张图前 50 字符
    完全相同 ⇒ 后发的图被当重复**静默丢弃**（实测 3 张只留 1 张，史实丢 9 条）。

    判据：去重键必须选**标识类字段**（msg_id）；内容字段只能作 fallback。
    """

    SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "auto_dm", "conversation_capture.py")

    def test_dedup_key_prefers_identity_field(self):
        """去重键必须优先用 msg_id，不得只按文本去重。"""
        src = open(self.SRC, encoding="utf-8").read()
        self.assertIn(
            "_mid_for_key", src,
            "去重键未使用消息标识字段（msg_id）—— 会被契约变更引爆（M-16 复发）")
        self.assertIn(
            "key = (mcid, _mid_for_key)",
            src,
            "去重键未优先用 _mid_for_key —— 内容字段不得单独构成去重键")

    def test_dedup_fallback_has_no_undefined_var(self):
        """fallback 分支不得引用不存在的变量（曾引入 _ts_for_key 未定义）。"""
        src = open(self.SRC, encoding="utf-8").read()
        self.assertNotIn(
            "_ts_for_key", src,
            "fallback 引用了未定义变量 _ts_for_key（运行期 NameError）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
