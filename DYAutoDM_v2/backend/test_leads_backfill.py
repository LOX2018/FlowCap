# -*- coding: utf-8 -*-
"""历史补全提线索 + 来源标记回归（2026-10-04，用户定调「历史补全也提，标记非实时」）。

## 覆盖
| 判据 | 说明 |
|---|---|
| B1 实时默认源 | `save_lead` 不传 source ⇒ `realtime` |
| B2 补全源 | `save_lead(source='backfill')` ⇒ `backfill` |
| B3 批量提取 | `extract_and_save_leads_from_messages` 从对方文本提取手机号/微信号 |
| B4 去重 | 同会话同号码重复提取 ⇒ 第二次返回 0（UNIQUE 兜底） |
| B5 空文本不误提 | 无号码文本 ⇒ 0 条 |
| B6 幂等迁移 | 老库（无 source 列）跑 `ensure_tables` ⇒ 自动补列 |

## 运行
    cd backend
    python -m unittest test_leads_backfill -v
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

_ROOT = os.path.join(tempfile.gettempdir(), f"leads_backfill_test_{os.getpid()}")
os.makedirs(_ROOT, exist_ok=True)

# 🔴 2026-10-04 事故后必修：本文件 setUp 里有 `DELETE FROM ai_leads`，
# 旧写法 `setdefault("DY_APP_ROOT", _ROOT)` 在数据库层无效（database._db_path()
# 不读该变量）⇒ 该 DELETE 一直在清**生产库**的线索。现走共享隔离助手。
from test_isolation import isolate  # noqa: E402  必须在 import database 之前
isolate("leads_backfill")

from database import get_db  # noqa: E402
from services import ai_reply  # noqa: E402


def _clear():
    conn = get_db()
    conn.execute("DELETE FROM ai_leads")
    conn.commit()


def _row(conv_id: str, value: str):
    conn = get_db()
    return conn.execute(
        "SELECT * FROM ai_leads WHERE conv_id=? AND contact_value=?",
        (conv_id, value),
    ).fetchone()


class TestLeadsBackfill(unittest.TestCase):
    def setUp(self):
        ai_reply.ensure_tables()
        _clear()

    def test_B1_realtime_default(self):
        ai_reply.save_lead("acct", "c1", "张三", "phone", "13800001111", "文本")
        r = _row("c1", "13800001111")
        self.assertIsNotNone(r)
        self.assertEqual(r["source"], "realtime")

    def test_B2_backfill_source(self):
        ai_reply.save_lead("acct", "c2", "李四", "wechat", "wxlisi88", "文本",
                           source="backfill")
        r = _row("c2", "wxlisi88")
        self.assertEqual(r["source"], "backfill")

    def test_B3_batch_extract(self):
        # 微信号识别规则（ai_reply._WECHAT_RE）：须以 wx/weixin/wxid/v_ 开头
        n = ai_reply.extract_and_save_leads_from_messages(
            "acct", "c3", "王五",
            ["我的电话 13800002222", "加微信 wxwangwu88 详聊", "无关内容"],
            source="backfill",
        )
        self.assertEqual(n, 2, "应提取 1 手机号 + 1 微信号")
        self.assertEqual(_row("c3", "13800002222")["source"], "backfill")
        self.assertEqual(_row("c3", "wxwangwu88")["source"], "backfill")

    def test_B4_dedup(self):
        t = ["电话13900003333"]
        n1 = ai_reply.extract_and_save_leads_from_messages("acct", "c4", "赵六", t)
        n2 = ai_reply.extract_and_save_leads_from_messages("acct", "c4", "赵六", t)
        self.assertEqual(n1, 1)
        self.assertEqual(n2, 0, "同会话同号码不得重复入库")

    def test_B5_empty_no_lead(self):
        n = ai_reply.extract_and_save_leads_from_messages(
            "acct", "c5", "钱七", ["今天天气不错", "", None])
        self.assertEqual(n, 0)

    def test_B6_idempotent_migration(self):
        # 造一个「老库」：ai_leads 无 source 列
        conn = get_db()
        conn.execute("DROP TABLE IF EXISTS ai_leads")
        conn.execute(
            "CREATE TABLE ai_leads (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "account TEXT NOT NULL, conv_id TEXT NOT NULL, peer_name TEXT,"
            "contact_type TEXT NOT NULL, contact_value TEXT NOT NULL,"
            "source_text TEXT DEFAULT '', status TEXT DEFAULT 'new',"
            "created_at REAL NOT NULL,"
            "UNIQUE(account, conv_id, contact_type, contact_value))"
        )
        conn.commit()
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(ai_leads)")}
        self.assertNotIn("source", cols, "前置：老表不应有 source 列")
        # 跑迁移
        ai_reply.ensure_tables()
        cols2 = {r["name"] for r in conn.execute("PRAGMA table_info(ai_leads)")}
        self.assertIn("source", cols2, "ensure_tables 应幂等补出 source 列")
        # 再跑一次不报错（幂等）
        ai_reply.ensure_tables()


if __name__ == "__main__":
    unittest.main(verbosity=2)
