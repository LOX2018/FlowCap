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

    def test_g6_wp_recv_uses_exit(self):
        src = open(os.path.join(_BACKEND, "daemon", "wp_recv.py"), encoding="utf-8").read()
        self.assertIn("MessageRecord.build", src)
        self.assertIn("rec.extra_json()", src)

    def test_g6b_capture_uses_exit(self):
        src = open(os.path.join(_BACKEND, "auto_dm", "conversation_capture.py"),
                   encoding="utf-8").read()
        self.assertIn("MessageRecord.build", src)
        self.assertIn("_rec_of(m, _extra).tuple", src)
        # 负控：不得再出现裸写 m["text"] 的插入元组
        self.assertNotIn('m["role"], m["text"], "text", _extra', src)


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
