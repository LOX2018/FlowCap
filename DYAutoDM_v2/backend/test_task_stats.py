# -*- coding: utf-8 -*-
"""ADR-035 §3.4 下游：任务表统计回归（2026-10-04）。

## 覆盖

| 判据 | 说明 |
|---|---|
| T1 聚合正确 | 插入 live/crawl/scheduled 三类任务 ⇒ kinds 各计数正确 |
| T2 状态拆分 | finished / failed / stopped / running 各自归类正确 |
| T3 时间口径 | 今日/累计按本地日切（tz=8）正确 |
| T4 老数据兼容 | 空 kind + 有 live_id ⇒ 归入 live（与任务中心一致） |
| T5 空表不炸 | 空 tasks 表 ⇒ 返回结构完整、全 0（不 500） |
| T6 只读边界 | 源码不含任何写/网络调用（只读本地库） |

## 运行

    cd backend
    python -m unittest test_task_stats -v
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

_ROOT = os.path.join(tempfile.gettempdir(), f"task_stats_test_{os.getpid()}")
os.makedirs(_ROOT, exist_ok=True)
# 🔴 2026-10-04 必修：下方有 `DELETE FROM tasks`（无 WHERE，清全表）。
# 旧写法用 setdefault —— 不覆盖已存在的 DY_APP_ROOT ⇒ 隔离失效，实测直接删掉**生产库**的 tasks。改用显式赋值。
from test_isolation import env_isolate  # noqa: E402  必须在 import database 前
env_isolate("task_stats", root=_ROOT)

from api.tasks import _task_stats_sync  # noqa: E402
from database import get_db  # noqa: E402

_COLS = ("id,acct,live_id,start_ts,end_ts,status,result_count,config,records,"
         "created_at,pid,kind,params,error_code,updated_at")


def _ins(conn, tid, kind, status, rc, created_at, live_id=""):
    conn.execute(
        f"INSERT INTO tasks({_COLS}) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (tid, "acct", live_id, "2026-10-04 10:00:00", "2026-10-04 10:05:00",
         status, rc, "{}", "[]", created_at, 0, kind, "{}", "", created_at),
    )


class TestTaskStats(unittest.TestCase):
    def setUp(self):
        conn = get_db()
        conn.execute("DELETE FROM tasks")
        conn.commit()

    def test_T5_empty_table_ok(self):
        r = _task_stats_sync(8, 7)
        self.assertTrue(r["ok"])
        self.assertEqual(r["total"]["runs"], 0)
        self.assertEqual(r["kinds"], {})
        self.assertEqual(r["status"], {"ok": 0, "failed": 0, "running": 0})
        self.assertEqual(len(r["trend"]), 7)
        self.assertEqual(r["recent"], [])

    def test_T1_kind_aggregation(self):
        conn = get_db()
        now = __import__("time").time()
        _ins(conn, 1, "live", "finished", 5, now)
        _ins(conn, 2, "live", "stopped", 3, now)
        _ins(conn, 3, "crawl", "finished", 10, now)
        _ins(conn, 4, "scheduled", "finished", 2, now)
        conn.commit()
        r = _task_stats_sync(8, 7)
        self.assertEqual(r["kinds"]["live"]["runs"], 2)
        self.assertEqual(r["kinds"]["live"]["results"], 8)
        self.assertEqual(r["kinds"]["crawl"]["runs"], 1)
        self.assertEqual(r["kinds"]["crawl"]["results"], 10)
        self.assertEqual(r["kinds"]["scheduled"]["runs"], 1)
        self.assertEqual(r["total"]["runs"], 4)
        self.assertEqual(r["total"]["results"], 20)

    def test_T2_status_split(self):
        conn = get_db()
        now = __import__("time").time()
        _ins(conn, 1, "live", "finished", 0, now)
        _ins(conn, 2, "crawl", "failed", 0, now)
        _ins(conn, 3, "crawl", "stopped", 0, now)
        _ins(conn, 4, "live", "running", 0, now)
        conn.commit()
        r = _task_stats_sync(8, 7)
        self.assertEqual(r["status"]["ok"], 1)       # finished
        self.assertEqual(r["status"]["failed"], 2)   # failed + stopped
        self.assertEqual(r["status"]["running"], 1)

    def test_T3_today_vs_total(self):
        conn = get_db()
        now = __import__("time").time()
        _ins(conn, 1, "live", "finished", 1, now)             # 今日
        _ins(conn, 2, "live", "finished", 1, now - 5 * 86400)  # 5 天前
        conn.commit()
        r = _task_stats_sync(8, 7)
        self.assertEqual(r["total"]["runs"], 2)
        self.assertEqual(r["today"]["runs"], 1)

    def test_T4_old_row_fallback_to_live(self):
        conn = get_db()
        now = __import__("time").time()
        # 空 kind + 非空 live_id（老数据）
        _ins(conn, 1, "", "finished", 4, now, live_id="73513323440762")
        conn.commit()
        r = _task_stats_sync(8, 7)
        self.assertEqual(r["kinds"].get("live", {}).get("runs"), 1)
        self.assertEqual(r["recent"][0]["kind"], "live")

    def test_T6_readonly_source(self):
        import inspect
        src = inspect.getsource(_task_stats_sync)
        for bad in ("INSERT", "UPDATE", "DELETE", "requests", "urllib", "httpx"):
            self.assertNotIn(bad, src, f"统计函数不得包含 {bad}（只读本地库）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
