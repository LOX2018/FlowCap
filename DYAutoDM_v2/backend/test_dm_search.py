# -*- coding: utf-8 -*-
"""P2 单测：全库检索（services/dm_search）。

真实 SQLite 内存库（非 mock）：建与项目同构的 dm_messages/dm_conversations 表，
插真实形态数据，验证 WHERE/排序/分页/媒体判定/半开区间/噪声过滤。
"""
import os
import sqlite3
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import services.dm_search as S  # noqa: E402

_ACCT = "acct1"
_CONV = "0:1:100:200"


def _mkdb():
    """建与项目同构的最小表结构（不依赖项目 database.py 的路径）。"""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        CREATE TABLE dm_conversations(
            account TEXT, conv_id TEXT, peer_name TEXT,
            UNIQUE(account, conv_id));
        CREATE TABLE dm_messages(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account TEXT, conv_id TEXT, role TEXT, text TEXT,
            msg_type TEXT DEFAULT 'text', msg_code TEXT,
            extra TEXT DEFAULT '{}',
            ts REAL NOT NULL, msg_id TEXT);
        """
    )
    c.execute("INSERT INTO dm_conversations VALUES(?,?,?)", (_ACCT, _CONV, "张三"))
    return c


class _Base(unittest.TestCase):
    def setUp(self):
        self.conn = _mkdb()
        # 注入：让 dm_search 用这个内存库。
        # ⚠️ 必须记下原值并在 tearDown 恢复 —— `unittest discover` 把所有 test_*.py
        #    导入**同一个进程**，泄漏的 monkeypatch 会让后续用例（app_config /
        #    model_hub 等）拿到这个已关闭的内存库 → 大面积 ERROR
        #    （本项目知识库「15_统一配置中心与设置页」已记录此类同进程污染）。
        import database
        self._orig = getattr(database, "get_db", None)
        self._had_orig = hasattr(database, "get_db")
        database.get_db = lambda: self.conn
        sys.modules["database"].get_db = lambda: self.conn

    def tearDown(self):
        # 恢复全局（先恢复再关连接，避免恢复期仍有调用拿到已关闭库）
        try:
            import database
            if self._had_orig:
                database.get_db = self._orig
                sys.modules["database"].get_db = self._orig
        except Exception:
            pass
        try:
            self.conn.close()
        except Exception:
            pass

    def _ins(self, text, ts, msg_type="text", extra="{}", msg_id=None,
             conv=_CONV, role="them"):
        self.conn.execute(
            "INSERT INTO dm_messages(account,conv_id,role,text,msg_type,extra,ts,msg_id)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (_ACCT, conv, role, text, msg_type, extra, ts, msg_id))


class TestSearch(_Base):
    def test_requires_condition(self):
        with self.assertRaises(ValueError):
            S.search_messages(_ACCT)

    def test_time_range_half_open(self):
        self._ins("a", 100.0)
        self._ins("b", 200.0)
        r = S.search_messages(_ACCT, start_time=100.0, end_time=200.0)
        self.assertEqual(r["total"], 1)
        self.assertEqual(r["items"][0]["text"], "a")

    def test_start_ge_end_rejected(self):
        with self.assertRaises(ValueError):
            S.search_messages(_ACCT, start_time=200.0, end_time=200.0)

    def test_keyword_hits_text(self):
        self._ins("工伤认定流程", 1.0)
        self._ins("无关内容", 2.0)
        r = S.search_messages(_ACCT, query="工伤")
        self.assertEqual(r["total"], 1)

    def test_keyword_hits_transcription(self):
        self._ins("[语音] u", 1.0, msg_type="17",
                  extra='{"transcription":"我说的是工伤赔付"}')
        r = S.search_messages(_ACCT, query="赔付")
        self.assertEqual(r["total"], 1)

    def test_keyword_hits_reply_text(self):
        self._ins("回复", 1.0,
                  extra='{"reply":{"text":"被引用的工伤原话"}}')
        r = S.search_messages(_ACCT, query="工伤")
        self.assertEqual(r["total"], 1)

    def test_like_wildcards_escaped(self):
        """用户输入 % 不得当通配符（否则空查询会命中全部）。"""
        self._ins("100%赔付", 1.0)
        self._ins("无关系", 2.0)
        r = S.search_messages(_ACCT, query="100%")
        self.assertEqual(r["total"], 1)
        r2 = S.search_messages(_ACCT, query="%")
        self.assertEqual(r2["total"], 1)   # 只命中真的含 % 的那条

    def test_conv_filter(self):
        self._ins("本会话", 1.0)
        self._ins("他会话", 1.0, conv="0:1:100:999")
        r = S.search_messages(_ACCT, query="会话", conv_id=_CONV)
        self.assertEqual(r["total"], 1)
        self.assertEqual(r["items"][0]["conv_id"], _CONV)

    def test_media_image_vs_video(self):
        self._ins("[图片] x", 1.0, msg_type="27")
        self._ins("[分享视频] 视频ID 9", 2.0, msg_type="8")
        self.assertEqual(S.search_messages(_ACCT, media_type="image")["total"], 1)
        self.assertEqual(S.search_messages(_ACCT, media_type="video")["total"], 1)
        self.assertEqual(S.search_messages(_ACCT, media_type="media")["total"], 2)

    def test_media_video_by_extra(self):
        """msg_type 是 text 但 extra 带 video → 算视频（历史形态兼容）。"""
        self._ins("视频", 1.0, msg_type="text",
                  extra='{"video":{"vid":"v1"}}')
        self.assertEqual(S.search_messages(_ACCT, media_type="video")["total"], 1)

    def test_bad_media_type(self):
        with self.assertRaises(ValueError):
            S.search_messages(_ACCT, media_type="audio")

    def test_noise_filtered(self):
        self._ins("回执", 1.0, msg_type="50001")
        self._ins("[未知媒体] {}", 2.0)
        self._ins("[分享视频]", 3.0)
        r = S.search_messages(_ACCT, start_time=0.0, end_time=1e9)
        self.assertEqual(r["total"], 0)

    def test_order_by_created_at_us(self):
        """排序与聊天页同口径：created_at_us 优先，缺失按其 ts×1e6 量级对齐。"""
        self._ins("c", 3.0, extra='{"created_at_us": 3000000}')
        self._ins("a", 1.0, extra='{"created_at_us": 1000000}')
        self._ins("b_no_f4", 2.0)          # 无 f4 → 2000000，应排在 a 后 c 前
        r = S.search_messages(_ACCT, start_time=0.0, end_time=1e9)
        self.assertEqual([x["text"] for x in r["items"]], ["a", "b_no_f4", "c"])

    def test_pagination(self):
        for i in range(5):
            self._ins(f"m{i}", float(i))
        r = S.search_messages(_ACCT, start_time=0.0, end_time=1e9,
                              page=2, page_size=2)
        self.assertEqual(r["total"], 5)
        self.assertEqual(len(r["items"]), 2)
        self.assertEqual(r["items"][0]["text"], "m2")

    def test_page_size_capped(self):
        r = S.search_messages(_ACCT, start_time=0.0, end_time=1e9, page_size=9999)
        self.assertEqual(r["page_size"], 200)

    def test_snippet_context(self):
        self._ins("A" * 80 + "工伤" + "B" * 80, 1.0)
        r = S.search_messages(_ACCT, query="工伤")
        s = r["items"][0]["snippet"]
        self.assertIn("工伤", s)
        self.assertTrue(s.startswith("…"))


class TestDaily(_Base):
    def test_daily_counts_and_bounds(self):
        base = time.mktime((2026, 9, 17, 10, 0, 0, 0, 0, -1))
        for i in range(3):
            self._ins(f"d1-{i}", base + i * 60)
        self._ins("d2", base + 86400)
        r = S.daily_stats(_ACCT, _CONV, tz_hours=8)
        days = {d["date"]: d["count"] for d in r["days"]}
        self.assertEqual(sum(days.values()), 4)
        self.assertEqual(len(days), 2)
        self.assertTrue(r["bounds"]["min"] > 0)
        self.assertGreater(r["bounds"]["max"], r["bounds"]["min"])
        # 每天的 first_msg_id 必须给出（供「跳到那天第一条」）
        for d in r["days"]:
            self.assertIn("first_ts", d)

    def test_daily_requires_ids(self):
        with self.assertRaises(ValueError):
            S.daily_stats("", _CONV)
        with self.assertRaises(ValueError):
            S.daily_stats(_ACCT, "")

    def test_daily_noise_filtered(self):
        self._ins("回执", 100.0, msg_type="50001")
        r = S.daily_stats(_ACCT, _CONV)
        self.assertEqual(r["total"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
