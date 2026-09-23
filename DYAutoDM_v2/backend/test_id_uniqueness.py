# -*- coding: utf-8 -*-
"""P0-2 唯一性回归：房间/策略/任务 **id 生成不得在同毫秒碰撞**（2026-09-23）。

## 覆盖缺陷（全部为审计实测复现的真实事故）

| 编号 | 缺陷 | 用户可见症状 |
|---|---|---|
| P0-2a | `live_rooms.new_room_id()` / 迁移内联 `lc_*` 用裸 `int(time.time()*1000)` | 同毫秒新建/迁移 N 条 → 同 id → kv upsert **静默覆盖**，只剩 1 条，响应还谎报条数 |
| P0-2b | `tasks_history.start_task` 的 `tid` 是 tasks 表**主键**、用裸 epoch 毫秒 | 多账号同毫秒开任务 → `UNIQUE constraint failed` → 被 `core/auto_dm.py` 吞成 ENG-003 → 任务中心静默少行 |

## D-07 纪律：每条断言都带**负控**

负控 = 把**旧事故形态**（原始那一行代码）内联重现，断言它**必然复现故障**。
若将来有人把生成逻辑改回裸毫秒，负控仍会绿（它测的是旧公式本身），而正常
断言会变红 —— 两者合起来才能证明「测试确实守得住，且守的是对的东西」。

## 运行

    cd backend
    DY_APP_ROOT="$LOCALAPPDATA/Temp/fixA" python -m unittest test_id_uniqueness -v
"""
from __future__ import annotations

import os
import sqlite3
import sys
import time
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# 独立隔离库：只用 TEMP 下的 root，绝不碰 backend/data
_ROOT = os.path.join(os.environ.get("TEMP", "."), "fixA")
os.makedirs(_ROOT, exist_ok=True)
os.environ.setdefault("DY_APP_ROOT", _ROOT)

from api import live_config, live_rooms  # noqa: E402
import tasks_history  # noqa: E402
from database import get_kv_json, set_kv_json  # noqa: E402

# 冻结到同一毫秒（在内存里直接算，避免受 mock 影响）
FROZEN = 1790160000.123
FROZEN_MS = int(FROZEN * 1000)


def _reset() -> None:
    set_kv_json(live_rooms._KV_KEY, {})
    set_kv_json(live_config._KV_KEY, {})
    # 复位进程内单调尾巴，保证用例互不干扰
    live_rooms._last_id_ms = 0
    live_config._last_id_ms = 0
    tasks_history._last_task_id = 0


class TestRoomIdUniqueness(unittest.TestCase):
    """P0-2a：同毫秒两个房间 → 必须是两个不同 id、两条记录。"""

    def setUp(self) -> None:
        _reset()

    def test_two_rooms_same_ms_not_overwritten(self):
        import asyncio

        with mock.patch("time.time", return_value=FROZEN):
            r1 = asyncio.run(live_rooms.save_room(live_rooms.RoomBody(
                room_id="111", name="房A")))
            r2 = asyncio.run(live_rooms.save_room(live_rooms.RoomBody(
                room_id="222", name="房B")))
        id1, id2 = r1["room"]["id"], r2["room"]["id"]
        self.assertNotEqual(id1, id2, "同毫秒两个房间拿到了同一个 id = 会静默覆盖")
        self.assertTrue(id1.startswith("lr_") and id2.startswith("lr_"),
                        "id 形态应仍是 lr_<epoch_ms>（老数据/前端契约零迁移）")
        kv = get_kv_json(live_rooms._KV_KEY, {}) or {}
        self.assertEqual(len(kv), 2, f"库内只剩 {len(kv)} 条 —— 有房间被覆盖")
        self.assertEqual({kv[id1]["room_id"], kv[id2]["room_id"]}, {"111", "222"})

    def test_migration_five_rooms_all_persisted(self):
        """迁移 5 条旧房间形记录：响应条数必须等于**库内实际条数**。"""
        legacy = {}
        for i in range(5):
            rid = f"9000000000{i}"
            legacy[rid] = {"room_id": rid, "live_url": rid, "name": f"旧房{i}",
                           "max_target": 10 + i, "interval": 60.0, "delay": "50,120",
                           "dm_pool": [], "auto_link_mic": False,
                           "link_mic_mode": "audio"}
        set_kv_json(live_config._KV_KEY, legacy)
        with mock.patch("time.time", return_value=FROZEN):
            out = live_rooms.migrate_from_room_configs(dry_run=False)
        self.assertEqual(len(out["migrated_rooms"]), 5)
        self.assertEqual(len(set(out["migrated_rooms"])), 5,
                         "响应里有重复 id —— 说明仍在同毫秒覆盖")
        rooms = get_kv_json(live_rooms._KV_KEY, {}) or {}
        self.assertEqual(len(rooms), 5,
                         f"响应谎报 5 条、库内只有 {len(rooms)} 条 = 谎报条数")
        # 5 条承接策略也必须互不覆盖
        self.assertEqual(len(out["created_strategies"]), 5)
        cfgs = get_kv_json(live_config._KV_KEY, {}) or {}
        self.assertEqual(len([k for k in cfgs if k.startswith("lc_")]), 5)

    def test_new_room_id_avoids_existing_keys(self):
        """跨进程场景：目标 kv 里已有某键 → 生成器必须跳过它。"""
        existing = {f"lr_{FROZEN_MS}": {}}
        with mock.patch("time.time", return_value=FROZEN):
            rid = live_rooms.new_room_id(existing)
        self.assertNotIn(rid, existing)


class TestStrategyIdUniqueness(unittest.TestCase):
    """P0-2a：策略 id 同样不得同毫秒碰撞。"""

    def setUp(self) -> None:
        _reset()

    def test_two_strategies_same_ms_distinct(self):
        import asyncio

        with mock.patch("time.time", return_value=FROZEN):
            r1 = asyncio.run(live_config.save_strategy(live_config.StrategyBody(
                name="策略A")))
            r2 = asyncio.run(live_config.save_strategy(live_config.StrategyBody(
                name="策略B")))
        s1, s2 = r1["config"]["id"], r2["config"]["id"]
        self.assertNotEqual(s1, s2, "同毫秒两条策略 id 相同 = 会互相覆盖")
        self.assertTrue(s1.startswith("lc_") and s2.startswith("lc_"))
        self.assertEqual(len(get_kv_json(live_config._KV_KEY, {}) or {}), 2)


class TestTaskIdUniqueness(unittest.TestCase):
    """P0-2b：tasks 主键必须保持 int 且同毫秒不再碰撞。"""

    def setUp(self) -> None:
        _reset()
        from database import get_db
        conn = get_db()
        conn.execute("DELETE FROM tasks")
        conn.commit()
        self.conn = conn

    def test_two_tasks_same_ms_both_persisted(self):
        with mock.patch("time.time", return_value=FROZEN):
            t1 = tasks_history.start_task("acctA", "live1")
            t2 = tasks_history.start_task("acctB", "live2")
        self.assertIsInstance(t1, int)
        self.assertIsInstance(t2, int)
        self.assertNotEqual(t1, t2, "同毫秒两个任务拿到同一主键 = 第二个必被吞掉")
        n = self.conn.execute("SELECT COUNT(*) AS c FROM tasks").fetchone()["c"]
        self.assertEqual(n, 2, f"tasks 表只剩 {n} 行 —— 有任务被静默丢弃")

    def test_id_monotonic_across_process_restart(self):
        """跨进程兜底：本进程内存没有历史（模拟另一 sidecar 先写过）时，
        新 id 必须 > 库内 MAX(id)，否则会撞上别的进程写的行。"""
        self.conn.execute(
            "INSERT INTO tasks(id,acct,start_ts,created_at) VALUES(?,?,?,?)",
            (FROZEN_MS + 5000, "other", "t", 0))
        self.conn.commit()
        tasks_history._last_task_id = 0          # 模拟「本进程刚启动」
        with mock.patch("time.time", return_value=FROZEN):
            tid = tasks_history.start_task("acctC", "live3")
        self.assertGreater(tid, FROZEN_MS + 5000,
                           "新 id 未越过库内 MAX(id) = 跨进程必撞")
        n = self.conn.execute("SELECT COUNT(*) AS c FROM tasks").fetchone()["c"]
        self.assertEqual(n, 2)

    def test_keeps_int_primary_key_contract(self):
        """硬要求：主键仍是 int（禁止改成 TEXT/复合键，避免破坏既有调用方）。"""
        with mock.patch("time.time", return_value=FROZEN):
            tid = tasks_history.start_task("acctD", "live4")
        self.assertIsInstance(tid, int)
        row = self.conn.execute("SELECT id FROM tasks WHERE id=?", (tid,)).fetchone()
        self.assertIsNotNone(row)


class TestNegativeControls(unittest.TestCase):
    """负控：把**旧事故形态**内联重放，断言它必然复现故障。

    这些用例是「测试本身有效」的证明：若旧公式突然变得不碰撞，说明我们对根因
    的理解错了，这里会变红提醒。
    """

    def test_legacy_room_id_formula_collides(self):
        data = {}
        with mock.patch("time.time", return_value=FROZEN):
            for rid in ("111", "222"):
                key = f"lr_{int(time.time() * 1000)}"   # ← 旧实现原文
                data[key] = {"room_id": rid}            # ← 旧 upsert 原文
        self.assertEqual(len(data), 1,
                         "负控失效：旧公式居然没碰撞（根因认知需重审）")
        self.assertEqual(list(data.values())[0]["room_id"], "222",
                         "旧形态下后写覆盖先写（正是丢用户数据的那一步）")

    def test_legacy_task_pk_formula_collides(self):
        """旧 tid = int(time.time()*1000) 直接当主键 → 第二次 INSERT 必抛 UNIQUE。"""
        import tempfile
        db = os.path.join(tempfile.mkdtemp(), "t.db")
        c = sqlite3.connect(db)
        c.execute("CREATE TABLE tasks(id INTEGER PRIMARY KEY, acct TEXT)")
        with mock.patch("time.time", return_value=FROZEN):
            tid = int(time.time() * 1000)               # ← 旧实现原文
        c.execute("INSERT INTO tasks(id,acct) VALUES(?,?)", (tid, "A"))
        c.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            c.execute("INSERT INTO tasks(id,acct) VALUES(?,?)", (tid, "B"))
        c.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
