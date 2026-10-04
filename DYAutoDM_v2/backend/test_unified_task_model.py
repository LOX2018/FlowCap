# -*- coding: utf-8 -*-
"""ADR-035 统一任务模型回归（A1–A6）—— 2026-10-04。

## 覆盖（对应用户拍板的完成标准）

| 编号 | 标准 | 判据 |
|---|---|---|
| A1 | 采集落任务 | `_save_history` 执行 ⇒ tasks 新增一行 kind='crawl'，params 含 keyword/target |
| A2 | 直播任务带 kind | 不传 kind 的既有调用 ⇒ kind='live'，params.live_id 非空（零回归） |
| A3 | 老数据可读 | 空 kind + 非空 live_id ⇒ 回落 'live'；无 live_id ⇒ 空（不臆造） |
| A5 | 溯源可用 | records 快照可写可读，行结构保持 |
| A6 | 定时任务落任务 | `_sched_record` ⇒ 新增 kind='scheduled' 行 |
| —  | 失败原因码 | finish_task(error_code=…) 可写入 |

## 负控（D-07 纪律）

`test_A3` 内含负控：**无 live_id 且无 kind 的行必须回落为空**（若有人把回落逻辑
写成「一律 live」或「一律空」，负控会红）。另 `test_column_really_exists` 断言
4 个新列真实存在（证明加列迁移生效，而非「读一个不存在的字段恒为 None」）。

## 运行

    cd backend
    python -m unittest test_unified_task_model -v
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# 独立隔离库：只用 TEMP 下按 pid 命名的 root，绝不碰 backend/data
_ROOT = os.path.join(tempfile.gettempdir(), f"unified_task_test_{os.getpid()}")
os.makedirs(_ROOT, exist_ok=True)
os.environ.setdefault("DY_APP_ROOT", _ROOT)

import tasks_history  # noqa: E402
from database import get_db  # noqa: E402


def _count(kind=None) -> int:
    conn = get_db()
    if kind is None:
        return conn.execute("SELECT COUNT(*) c FROM tasks").fetchone()["c"]
    return conn.execute(
        "SELECT COUNT(*) c FROM tasks WHERE kind=?", (kind,)
    ).fetchone()["c"]


class TestUnifiedTaskModel(unittest.TestCase):
    def setUp(self):
        conn = get_db()
        conn.execute("DELETE FROM tasks")
        conn.commit()

    # ── 迁移自证：4 个新列真实存在（防「读不存在的字段恒 None」假通过）──
    def test_column_really_exists(self):
        conn = get_db()
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(tasks)").fetchall()}
        for c in ("kind", "params", "error_code", "updated_at"):
            self.assertIn(c, cols, f"tasks 表缺列 {c}（加列迁移未生效）")

    # ── A1 采集落任务（契约层：_save_history 正是这样调）──
    def test_A1_crawl_task_lands(self):
        tid = tasks_history.start_task(
            "acct_c", "", kind="crawl",
            params={"keyword": "美食", "kind": "comment", "target": "w1", "limit": 3},
        )
        tasks_history.finish_task(tid, status="finished", result_count=3)
        row = tasks_history.get_task(tid)
        self.assertEqual(row["kind"], "crawl")
        self.assertEqual(row["params"]["keyword"], "美食")
        self.assertEqual(row["status"], "finished")
        self.assertEqual(row["result_count"], 3)

    # ── A2 直播默认 kind（既有调用点不传 kind ⇒ 零回归）──
    def test_A2_live_default_kind(self):
        tid = tasks_history.start_task("acct_l", "73513323440762")
        row = tasks_history.get_task(tid)
        self.assertEqual(row["kind"], "live")
        self.assertEqual(row["params"].get("live_id"), "73513323440762")

    # ── A3 老数据回落 + 负控 ──
    def test_A3_old_row_fallback(self):
        conn = get_db()
        _SQL = (
            "INSERT INTO tasks(id,acct,live_id,start_ts,end_ts,status,result_count,"
            "config,records,created_at,pid,kind,params,error_code,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
        )
        # 空 kind + 非空 live_id ⇒ live
        conn.execute(_SQL, (9999999999999, "old_acct", "old_live", "2026-10-03 03:22:41",
                            "", "finished", 0, "{}", "[]", 1.0, 0, "", "{}", "", 0))
        conn.commit()
        self.assertEqual(tasks_history.get_task(9999999999999)["kind"], "live")
        # 负控：空 kind + 空 live_id ⇒ 空（不臆造类型）
        conn.execute(_SQL, (9999999999998, "old2", "", "2026-10-03 03:22:41",
                            "", "finished", 0, "{}", "[]", 1.0, 0, "", "{}", "", 0))
        conn.commit()
        self.assertEqual(tasks_history.get_task(9999999999998)["kind"], "")

    # ── A5 records 快照可写可读 ──
    def test_A5_records_roundtrip(self):
        recs = [{"ts": "2026-10-04 10:00:00", "target": "u1", "result": "ok", "reason": ""}]
        tid = tasks_history.start_task("acct_r", "live_r", kind="live", records=recs)
        tasks_history.finish_task(tid, status="finished", result_count=1, records=recs)
        row = tasks_history.get_task(tid)
        self.assertEqual(len(row["records"]), 1)
        self.assertEqual(row["records"][0]["target"], "u1")

    # ── A6 定时任务落任务 ──
    def test_A6_scheduled_lands(self):
        from services import task_scheduler as ts
        task = ts.Task(id="s1", name="t", kind="hot_comment_crawl", account="acct_s")
        ts._sched_record(task, "finished", 5, "")
        self.assertEqual(_count("scheduled"), 1)
        row = [r for r in tasks_history.list_history() if r["kind"] == "scheduled"][0]
        self.assertEqual(row["params"].get("sched_id"), "s1")
        self.assertEqual(row["result_count"], 5)

    # ── 失败原因码 ──
    def test_error_code(self):
        tid = tasks_history.start_task("acct_e", "", kind="crawl", params={})
        tasks_history.finish_task(tid, status="failed", result_count=0, error_code="CRAWL-X")
        self.assertEqual(tasks_history.get_task(tid)["error_code"], "CRAWL-X")


class TestCrawlHistoryIntegration(unittest.TestCase):
    """A1 的端到端：真调 api/crawl.py 的 `_save_history`，验证它确实落了任务表。"""

    def setUp(self):
        conn = get_db()
        conn.execute("DELETE FROM tasks")
        conn.commit()

    def test_A1_save_history_lands_task(self):
        try:
            from api import crawl as crawl_mod
        except Exception as e:  # noqa: BLE001
            self.skipTest(f"api.crawl 导入失败（环境依赖），跳过端到端 A1: {e}")
        asyncio.run(crawl_mod._save_history(
            "acct_ce", "comment", "美食", "w1", [{"c": 1}, {"c": 2}]
        ))
        self.assertEqual(_count("crawl"), 1)
        row = [r for r in tasks_history.list_history() if r["kind"] == "crawl"][0]
        self.assertEqual(row["params"].get("keyword"), "美食")
        self.assertEqual(row["result_count"], 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
