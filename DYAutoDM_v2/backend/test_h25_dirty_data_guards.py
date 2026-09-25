# -*- coding: utf-8 -*-
"""H-25 张老师账号库脏数据治理 —— 回归门禁（防复发）。

治理三类实测缺陷（2026-09-25）：
  ① 会话昵称/头像被污染成本账号身份 —— 根因 `recv_daemon._extract_peer_uid`
     在 my_uid 未就绪时兜底 `return uid_b`，而 conv_id 实为
     `0:1:<对端>:<本号>` ⇒ uid_b 恰是本号自己（实测 136 会话被污染）。
  ② 内联 base64 图片原样进 AI prompt（25 条 / 10.3 万字符）。
  ③ 系统提示/系统消息/未知类型当会话内容展示与入 prompt。

门禁遵循项目纪律：断言**行为机制**（函数被调用/输出被改变），
不断言字面量（注释里出现旧写法不应让门禁失效）。
"""
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


class TestPlaceholderSelfName(unittest.TestCase):
    """判据 SSOT：本账号昵称必须被判为占位（污染态）。"""

    def test_self_name_is_placeholder(self):
        from services import verdicts
        self.assertTrue(verdicts.is_placeholder(
            "四川工伤-张老师", self_name="四川工伤-张老师"))
        self.assertFalse(verdicts.is_placeholder(
            "异时空", self_name="四川工伤-张老师"))

    def test_thin_wrapper_forwards_self_name(self):
        from services import verdicts
        self.assertTrue(verdicts.is_placeholder_name(
            "X", self_name="X"))
        # 既有调用方（不传 self_name）行为不变
        self.assertFalse(verdicts.is_placeholder_name("张三"))


class TestConvIdentityDelegate(unittest.TestCase):
    """① 会话身份解析必须委托 SSOT，且 my_uid 缺失时不返回本号。"""

    def test_peer_uid_contract(self):
        from services import conv_identity as ci
        # 已知本号：本号在 idx3 → 返回 idx2 的对端
        self.assertEqual(ci.peer_uid("0:1:P:ME", "ME"), "P")
        # 已知本号：本号在 idx2 → 返回 idx3
        self.assertEqual(ci.peer_uid("0:1:ME:P", "ME"), "P")
        # 两侧都不是本号 → 不猜
        self.assertIsNone(ci.peer_uid("0:1:A:B", "ME"))
        # a==b（自发自收）→ None
        self.assertIsNone(ci.peer_uid("0:1:S:S", "ME"))

    def test_recv_daemon_delegates_and_never_returns_self(self):
        """机制断言：recv_daemon._extract_peer_uid 必须调用 conv_identity。

        这是「旧兜底 `return uid_b`（=本号）绕过全部防线」的直接负控 ——
        若有人把内联实现写回来，本用例变红。
        """
        import daemon.recv_daemon as rd
        inbox = rd.AccountInbox.__new__(rd.AccountInbox)   # 绕过 __init__（免 DB）
        inbox.my_uid = "ME"
        with mock.patch("services.conv_identity.peer_uid",
                        return_value="P") as m:
            got = inbox._extract_peer_uid("0:1:P:ME")
        self.assertTrue(m.called, "必须委托 services.conv_identity.peer_uid")
        self.assertEqual(got, "P")

    def test_extract_peer_uid_returns_none_without_my_uid(self):
        """my_uid 缺失时**不得**返回本号（conv_id 形态 0:1:<对端>:<本号>）。"""
        import daemon.recv_daemon as rd
        inbox = rd.AccountInbox.__new__(rd.AccountInbox)
        inbox.my_uid = None
        got = inbox._extract_peer_uid("0:1:PEER:3887506227210423")
        self.assertNotEqual(got, "3887506227210423",
                            "my_uid 未知时不得把本号当对端")


class TestHistorySanitizer(unittest.TestCase):
    """② base64 不得进 prompt；③ 噪音前缀不得进 prompt。"""

    @staticmethod
    def _rowcon(path):
        """与生产同形态的连接（row_factory=Row）。

        教训：mock 库连接若不设 row_factory，`_build_history` 会因
        `r["role"]`（tuple 下标）抛异常 → 返回空 → 测试假绿/假红。
        探针必须复用生产形态（见项目「复现保真」纪律）。
        """
        c = sqlite3.connect(path)
        c.row_factory = sqlite3.Row
        return c

    def test_sanitize_strips_base64(self):
        from services.ai_reply import _sanitize_history_text
        out = _sanitize_history_text("[图片] data:image/webp;base64,UklGR" + "A" * 5000)
        self.assertNotIn("data:image", out)
        self.assertNotIn("base64", out)
        self.assertEqual(out, "[图片]")

    def test_sanitize_keeps_plain_text(self):
        from services.ai_reply import _sanitize_history_text
        self.assertEqual(_sanitize_history_text("你好呀"), "你好呀")

    def test_noise_sql_covers_all_prefixes(self):
        from services.ai_reply import _HISTORY_NOISE_SQL
        for p in ("[投递验证]", "[系统提示]", "[系统消息]",
                  "[未知类型", "[未知媒体]"):
            self.assertIn(p, _HISTORY_NOISE_SQL)

    def test_build_history_actually_filters(self):
        """端到端：把脏数据写进临时库，断言 _build_history 输出不含之。"""
        from services.ai_reply import AutoReplyWorker
        from database import get_db
        tmp = tempfile.mkdtemp()
        dbf = os.path.join(tmp, "t.db")
        con = sqlite3.connect(dbf)
        con.execute("CREATE TABLE dm_messages(id INTEGER PRIMARY KEY, account TEXT,"
                    " conv_id TEXT, role TEXT, text TEXT, msg_type TEXT,"
                    " extra TEXT, ts REAL, msg_id TEXT)")
        rows = [
            ("acc", "0:1:P:ME", "them", "客户真实提问", "text", "{}", 1),
            ("acc", "0:1:P:ME", "them", "[图片] data:image/webp;base64," + "A" * 3000,
             "text", "{}", 2),
            ("acc", "0:1:P:ME", "them", "[系统提示] 陌生人消息确认（#1）", "text", "{}", 3),
            ("acc", "0:1:P:ME", "them", "[投递验证] conv_id=0:1:P:ME", "text", "{}", 4),
            ("acc", "0:1:P:ME", "them", "[未知类型1] {\"awe\":1}", "text", "{}", 5),
            ("acc", "0:1:P:ME", "me", "我方回答", "7", "{}", 6),
        ]
        for r in rows:
            con.execute("INSERT INTO dm_messages(account,conv_id,role,text,msg_type,"
                        "extra,ts,msg_id) VALUES(?,?,?,?,?,?,?,NULL)", r)
        con.commit()
        con.close()
        with mock.patch("database.get_db", return_value=self._rowcon(dbf)):
            w = AutoReplyWorker.__new__(AutoReplyWorker)
            hist = w._build_history("acc", "0:1:P:ME", 999999,
                                    {"max_history": 0, "vision_enabled": False,
                                     "context_window": 65536})
        text = "\n".join(h["content"] for h in hist)
        self.assertIn("客户真实提问", text)
        self.assertIn("我方回答", text)
        self.assertNotIn("data:image", text)
        self.assertNotIn("[系统提示]", text)
        self.assertNotIn("[投递验证]", text)
        self.assertNotIn("[未知类型", text)


class TestReadSideFilter(unittest.TestCase):
    """③ 前端读侧详情 SQL 必须排除系统提示/系统消息/未知类型。"""

    def test_source_contains_new_filters(self):
        src = open(os.path.join(_HERE, "api", "messages.py"),
                   encoding="utf-8").read()
        for p in ("'[系统提示]%'", "'[系统消息]%'", "'[未知类型%'"):
            self.assertIn(p, src, f"读侧缺少 H-25 过滤 {p}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
