# -*- coding: utf-8 -*-
"""任务详情「留资情况」时间窗筛选回归（2026-10-04，用户要求：任务详情也显示留资）。

## 背景
`ai_leads` 表**没有 task_id 字段**（只有 account / conv_id），无法把线索精确归属到
「具体哪个任务」。故任务详情页按 **账号 + 任务时间窗** 近似匹配：`list_leads` 加可选
`start_ms` / `end_ms`（毫秒）过滤。

## 时区口径（2026-10-04 实测确认）
- `tasks.start_ts` 是**本地时间字符串**（`'2026-10-04 15:00:00'`）；
- `ai_leads.created_at` 是**秒级 epoch REAL**（系统 localtime）。
- 实测：SQLite `strftime('%s','now','localtime')` == `time.mktime(time.localtime())`
  （服务器 tzname = 中国标准时间），且 `start_ts` 字符串经 `strptime` 回解的 epoch
  与 `created_at` epoch 一致 ⇒ **同一墙上时钟口径，前端用 `Date.parse` 转毫秒后
  与 `created_at*1000` 可直接比较**。
- 🔴 边界：该对齐要求**浏览器时区与服务器一致（CST/UTC+8）**。用户换时区运行前端时
  会系统性偏移，届时需改为带时区标记的时间戳传输。见测试 B5 的说明。

## 覆盖
| 判据 | 说明 |
|---|---|
| B1 向后兼容 | 不传时间窗 ⇒ 与旧行为一致（全部返回） |
| B2 窗口内命中 | 只返回窗口内的条目 |
| B3 边界包含 | `>=` / `<=` 闭区间，边界值本身命中 |
| B4 单边窗口 | 只传 `start_ms` 或只传 `end_ms` 各自生效 |
| B5 空窗口 | 窗口不含任何条目 ⇒ 返回 0（不报错） |
| B6 limit 仍生效 | 时间窗与 LIMIT 组合不冲突 |

## 运行
    cd backend
    python -m unittest test_leads_window -v
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

_ROOT = os.path.join(tempfile.gettempdir(), f"leads_window_test_{os.getpid()}")
os.makedirs(_ROOT, exist_ok=True)

# 🔴 2026-10-04 事故后必修：_clear() 里有 `DELETE FROM ai_leads`，
# 旧写法 `setdefault("FLOWCAP_APP_ROOT", _ROOT)` 在数据库层无效 ⇒ 一直在清生产库。
from test_isolation import isolate  # noqa: E402  必须在 import database 之前
isolate("leads_window")

from database import get_db  # noqa: E402
from services import ai_reply  # noqa: E402

BASE = int(time.time())


def _put(offset_s: int, value: str, account: str = "acctA") -> None:
    """插入一条 created_at = BASE - offset_s 的线索。"""
    get_db().execute(
        "INSERT INTO ai_leads(account,conv_id,peer_name,contact_type,contact_value,"
        "source_text,created_at,source) VALUES(?,?,?,?,?,?,?,?)",
        (account, f"c{value}", f"p{value}", "phone", value, "t",
         float(BASE - offset_s), "realtime"),
    )
    get_db().commit()


def _clear():
    get_db().execute("DELETE FROM ai_leads")
    get_db().commit()


class TestLeadsWindow(unittest.TestCase):
    def setUp(self):
        ai_reply.ensure_tables()
        _clear()

    def test_B1_backward_compat_no_window(self):
        _put(60, "v1")
        _put(3600, "v2")
        rows = ai_reply.list_leads()
        self.assertEqual(len(rows), 2, "不传时间窗应返回全部（旧行为）")

    def test_B2_window_hit(self):
        _put(60, "n60")      # 窗口内（BASE-1800 <= BASE-60）
        _put(600, "n600")    # 窗口内
        _put(3600, "n3600")  # 窗口外
        _put(7200, "n7200")  # 窗口外
        rows = ai_reply.list_leads(start_ms=(BASE - 1800) * 1000,
                                   end_ms=BASE * 1000)
        vals = {r["contact_value"] for r in rows}
        self.assertEqual(vals, {"n60", "n600"}, f"应只命中窗口内 2 条，实得 {vals}")

    def test_B3_boundary_inclusive(self):
        # 边界值本身必须命中（闭区间 >= / <=）
        _put(1800, "edge_start")   # created_at == BASE-1800 == start_ms/1000
        _put(0, "edge_end")        # created_at == BASE == end_ms/1000
        _put(1801, "outside")      # 刚出下界
        rows = ai_reply.list_leads(start_ms=(BASE - 1800) * 1000,
                                   end_ms=BASE * 1000)
        vals = {r["contact_value"] for r in rows}
        self.assertIn("edge_start", vals, "下界闭区间应命中")
        self.assertIn("edge_end", vals, "上界闭区间应命中")
        self.assertNotIn("outside", vals)

    def test_B4_single_side(self):
        _put(60, "recent")
        _put(9999, "old")
        # 只传 start_ms ⇒ 只筛下界
        rows = ai_reply.list_leads(start_ms=(BASE - 3600) * 1000)
        vals = {r["contact_value"] for r in rows}
        self.assertEqual(vals, {"recent"}, f"只筛下界，实得 {vals}")
        # 只传 end_ms ⇒ 只筛上界：BASE-9999 <= BASE-3600 命中；BASE-60 不满足
        rows2 = ai_reply.list_leads(end_ms=(BASE - 3600) * 1000)
        self.assertEqual({r["contact_value"] for r in rows2}, {"old"},
                         f"只筛上界应只命中更早那条，实得 {rows2}")

    def test_B5_empty_window(self):
        _put(60, "x")
        rows = ai_reply.list_leads(start_ms=(BASE - 1) * 1000,
                                   end_ms=(BASE - 3600) * 1000)
        self.assertEqual(rows, [], "空窗口应返回空列表而非报错")

    def test_B6_limit_still_applies(self):
        for i in range(10):
            _put(60 + i, f"m{i}")
        rows = ai_reply.list_leads(limit=3, start_ms=(BASE - 1000) * 1000,
                                   end_ms=BASE * 1000)
        self.assertEqual(len(rows), 3, "LIMIT 应在时间窗之上继续生效")

    def test_B7_account_indep_of_window(self):
        # 窗口过滤不应顺带引入账号过滤（账号过滤是前端职责，保持后端语义单纯）
        _put(60, "same", account="acctA")
        _put(120, "other", account="acctB")
        rows = ai_reply.list_leads(start_ms=(BASE - 1800) * 1000,
                                   end_ms=BASE * 1000)
        self.assertEqual({r["account"] for r in rows}, {"acctA", "acctB"},
                         "后端按时间窗筛，账号过滤由前端完成")


if __name__ == "__main__":
    unittest.main(verbosity=2)
