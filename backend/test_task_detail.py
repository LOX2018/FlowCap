# -*- coding: utf-8 -*-
"""「任务详情」页数据源：`GET /api/tasks/{id}` 回归（2026-10-04）。

## 覆盖
| 判据 | 说明 |
|---|---|
| D1 详情可取 | 插入任务 ⇒ 端点返回该行（kind/params/records 已规范化） |
| D2 不存在 | 未知 id ⇒ `{ok: False}`，不抛异常 |
| D3 路由顺序 | `/{task_id}` 必须声明在 `/stats`、`/history` 等**之后**（否则吃掉它们） |
| D4 老数据回落 | 空 kind + 有 live_id ⇒ kind 回落 `live` |

## 运行
    cd backend
    python -m unittest test_task_detail -v
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

_ROOT = os.path.join(tempfile.gettempdir(), f"task_detail_test_{os.getpid()}")
os.makedirs(_ROOT, exist_ok=True)
# 🔴 2026-10-04 必修：下方有 `DELETE FROM tasks`（无 WHERE，清全表）。
# 旧写法用 setdefault —— 不覆盖已存在的 FLOWCAP_APP_ROOT ⇒ 隔离失效，实测直接删掉**生产库**的 tasks。改用显式赋值。
from test_isolation import env_isolate  # noqa: E402  必须在 import database 前
env_isolate("task_detail", root=_ROOT)

from api.tasks import get_task_detail, router  # noqa: E402
from database import get_db  # noqa: E402

_COLS = ("id,acct,live_id,start_ts,end_ts,status,result_count,config,records,"
         "created_at,pid,kind,params,error_code,updated_at")


def _ins(conn, tid, kind, live_id="", status="finished", rc=0, records="[]", params="{}"):
    conn.execute(
        f"INSERT INTO tasks({_COLS}) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (tid, "acct", live_id, "2026-10-04 10:00:00", "2026-10-04 10:05:00",
         status, rc, "{}", records, 1.0, 0, kind, params, "", 1.0),
    )


class TestTaskDetail(unittest.TestCase):
    def setUp(self):
        conn = get_db()
        conn.execute("DELETE FROM tasks")
        conn.commit()

    def test_D1_detail_ok(self):
        conn = get_db()
        _ins(conn, 101, "live", live_id="735", rc=3,
             records='[{"nickname":"u1","status":"sent"}]', params='{"live_id":"735"}')
        conn.commit()
        r = asyncio.run(get_task_detail(101))
        self.assertTrue(r["ok"])
        self.assertEqual(r["task"]["kind"], "live")
        self.assertEqual(r["task"]["result_count"], 3)
        self.assertEqual(len(r["task"]["records"]), 1)
        self.assertEqual(r["task"]["params"].get("live_id"), "735")

    def test_D2_missing(self):
        r = asyncio.run(get_task_detail(999999))
        self.assertFalse(r["ok"])
        self.assertIn("不存在", r.get("error", ""))

    def test_D3_route_order(self):
        # 逐条读 router.routes 的 path，`/{task_id}` 必须在具名路径之后
        paths = [getattr(rt, "path", "") for rt in router.routes]
        idx_param = paths.index("/{task_id}")
        for named in ("/stats", "/history", "/current", "/scheduler"):
            if named in paths:
                self.assertLess(paths.index(named), idx_param,
                                f"{named} 必须声明在 /{{task_id}} 之前，否则被它吃掉")

    def test_D4_old_row_fallback(self):
        conn = get_db()
        _ins(conn, 202, "", live_id="888")  # 空 kind + 有 live_id
        conn.commit()
        r = asyncio.run(get_task_detail(202))
        self.assertEqual(r["task"]["kind"], "live")


if __name__ == "__main__":
    unittest.main(verbosity=2)
