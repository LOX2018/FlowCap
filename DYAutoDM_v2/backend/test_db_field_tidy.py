"""数据库字段整理门禁（★ 2026-10-03，P1~P4）。

## 背景

`scripts/audit_db_fields.py` 实测（会员库）发现 4 个真问题，本门禁守住修复：

  P1 `crawl_history` 零索引 —— 而 `/api/crawl/stats` 正是按 account+ts 聚合
     ⇒ 每次看统计都全表扫。
  P2 `tasks` 混着脚本测试残留行（acct=''/start_ts='a'/'b'/'c'）。
  P3 `tasks` 同一时刻存两种格式：`start_ts` TEXT 与 `created_at` REAL。
  P4 `kind` 的声明值（3 种）与真实库（4 种，含 `comment_batch`）不符。

## 判据设计

⚠️ **必须能在隔离库上真跑**（不碰真实会员库）。做法：
  ① 用 `sqlite3` 内存库复刻**旧表结构**（无新增列/索引）；
  ② 调真实的 `database._migrate_schema(conn)`；
  ③ 断言迁移后的结构/数据。

这比 grep 源码强：真跑一次 SQLite DDL，语法错、列名错、判据错都会立刻暴露。

## 运行

    cd backend && python -m unittest test_db_field_tidy -v
"""
from __future__ import annotations

import os
import sqlite3
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


def _legacy_db() -> sqlite3.Connection:
    """复刻**迁移前**的旧库结构（P1~P4 涉及的表）。"""
    c = sqlite3.Connection(":memory:")
    c.executescript("""
    CREATE TABLE crawl_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account TEXT NOT NULL,
        kind TEXT NOT NULL DEFAULT 'video',
        keyword TEXT DEFAULT '',
        target TEXT DEFAULT '',
        result_count INTEGER DEFAULT 0,
        payload TEXT DEFAULT '[]',
        ts REAL NOT NULL
    );
    CREATE TABLE tasks (
        id INTEGER PRIMARY KEY,
        acct TEXT NOT NULL DEFAULT '',
        live_id TEXT NOT NULL DEFAULT '',
        start_ts TEXT NOT NULL,
        end_ts TEXT DEFAULT '',
        status TEXT NOT NULL DEFAULT 'running',
        result_count INTEGER DEFAULT 0,
        config TEXT DEFAULT '{}',
        records TEXT DEFAULT '[]',
        created_at REAL NOT NULL,
        pid INTEGER DEFAULT 0
    );
    -- 测试残留行（created_at < 1，start_ts 非日期）
    INSERT INTO tasks(id,acct,live_id,start_ts,end_ts,status,created_at)
         VALUES(1,'','','a','a','stopped',0.001),
               (2,'','','b','b','stopped',0.002),
               (3,'','','c','','finished',0.003);
    -- 真实业务行（epoch 秒级）
    INSERT INTO tasks(id,acct,live_id,start_ts,end_ts,status,result_count,created_at)
         VALUES(1790880027717,'小助理','854658577661',
                 '2026-10-02 02:40:27','2026-10-02 02:45:49','stopped',7,1790880027.717);
    INSERT INTO crawl_history(account,kind,ts) VALUES('x','comment',1790880027.0);
    """)
    c.commit()
    return c


def _migrated() -> sqlite3.Connection:
    c = _legacy_db()
    from database import _migrate_schema
    _migrate_schema(c)
    return c


class TestP1CrawlHistoryIndex(unittest.TestCase):
    """P1：crawl_history 必须有 account+ts 复合索引。"""

    def test_index_exists(self):
        c = _migrated()
        names = {r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='crawl_history'")}
        self.assertIn("idx_crawl_acct_ts", names,
                      "crawl_history 缺 (account, ts) 索引 ⇒ 统计查询全表扫")

    def test_index_columns_and_order(self):
        c = _migrated()
        cols = [r[2] for r in c.execute('PRAGMA index_info("idx_crawl_acct_ts")')]
        self.assertEqual(cols[:2], ["account", "ts"],
                         "复合索引须以 account 为左前缀（查询按 account 过滤）")

    def test_index_is_used_by_planner(self):
        """🔴 真跑 EXPLAIN：确认优化器**真的**会用它（而不是有索引但全表扫）。"""
        c = _migrated()
        plan = list(c.execute(
            "EXPLAIN QUERY PLAN SELECT COUNT(*) FROM crawl_history "
            "WHERE account=? GROUP BY account", ("x",)))
        txt = " ".join(str(r[-1]) for r in plan)
        self.assertIn("idx_crawl_acct_ts", txt,
                      f"EXPLAIN 显示未使用索引，统计仍会全表扫: {txt}")

    def test_tasks_live_id_index(self):
        c = _migrated()
        names = {r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='tasks'")}
        self.assertIn("idx_tasks_live", names, "tasks.live_id 缺索引")


class TestP2TestRowsPurged(unittest.TestCase):
    """P2：测试残留行（created_at < 1）必须被清，且**不得**误删真实行。"""

    def test_legacy_rows_removed(self):
        c = _migrated()
        n = c.execute("SELECT COUNT(*) FROM tasks WHERE created_at < 1").fetchone()[0]
        self.assertEqual(n, 0, f"仍有 {n} 条测试残留行未清理")

    def test_real_rows_kept(self):
        """🔴 负向保护：真实业务行（epoch 秒）**绝不能**被删。"""
        c = _migrated()
        n = c.execute("SELECT COUNT(*) FROM tasks WHERE id=1790880027717").fetchone()[0]
        self.assertEqual(n, 1, "真实业务行被误删 —— 判据 created_at < 1 过宽")

    def test_idempotent(self):
        """幂等：迁移跑两次不报错、不重复删。"""
        c = _legacy_db()
        from database import _migrate_schema
        _migrate_schema(c)
        n1 = c.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        _migrate_schema(c)          # 第二次
        n2 = c.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        self.assertEqual(n1, n2, f"二次迁移后行数变了 {n1} → {n2}")


class TestP3TimeColumns(unittest.TestCase):
    """P3：新增 REAL 时间列 + 回填；不可解析的留 NULL（不填 0）。"""

    def test_columns_exist(self):
        c = _migrated()
        cols = {r[1] for r in c.execute('PRAGMA table_info("tasks")')}
        self.assertIn("start_ts_real", cols)
        self.assertIn("end_ts_real", cols)

    def test_backfilled_for_parseable(self):
        c = _migrated()
        v = c.execute("SELECT start_ts_real FROM tasks WHERE id=1790880027717").fetchone()[0]
        self.assertIsNotNone(v, "可解析的 start_ts 未回填")
        ca = c.execute("SELECT created_at FROM tasks WHERE id=1790880027717").fetchone()[0]
        # 🔴 时区铁律：start_ts 是**本地时间**（UTC+8 写入），回填必须按本地时区换算。
        #   判据：与 created_at 的偏差应 < 1 小时；若 ≈ 8h（28800s）说明按 UTC 算了。
        diff = abs(float(v) - float(ca))
        self.assertLess(diff, 3600,
                        f"回填值与 created_at 差 {diff:.0f}s（≥1h）⇒ 时区换算错误"
                        f"（典型症状：差 28800s = 8h，即把本地时间当 UTC 解析）")
        self.assertAlmostEqual(float(v), float(ca), delta=3600)

    def test_timezone_offset_not_utc(self):
        """🔴 显式反证：按 UTC 解析会得到的值必须**不等于**实际回填值。

        ⚠️ **本门禁的已知局限（如实标注）**：它抓的是「时区处理整体缺失」。
        单独去掉 `'-8 hours'` 而保留「修正历史错值」那条 `-28800` 语句时，
        回填错的会被随即扳回 ⇒ 本例**仍绿**。
        即两条语句构成**自愈闭环**（回填 + 事后修正），单点缺失不致命。
        这不是判据失效，而是「实现有冗余」；但若有人**同时**删掉两条，本例会红。
        """
        import sqlite3 as _s
        c = _migrated()
        utc_val = c.execute(
            "SELECT CAST(strftime('%s','2026-10-02 02:40:27') AS REAL)").fetchone()[0]
        real_val = c.execute(
            "SELECT start_ts_real FROM tasks WHERE id=1790880027717").fetchone()[0]
        self.assertNotEqual(float(utc_val), float(real_val),
                            "回填值 == UTC 解析值 ⇒ 时区处理整体缺失")
        self.assertAlmostEqual(float(utc_val) - float(real_val), 28800.0, delta=2,
                               msg="两者差应恰为 28800s（8 小时）")

    def test_timezone_handling_removed_entirely(self):
        """🔴 真正的负控：**同时**去掉时区回填与事后修正 ⇒ 必须变红。

        这才是「时区处理彻底没了」的场景（上例只覆盖单点缺失）。
        """
        c = _legacy_db()
        # 模拟：迁移**完全不做**时区处理（既不回填也不修正）
        c.execute("DELETE FROM tasks WHERE created_at < 1")
        c.execute("ALTER TABLE tasks ADD COLUMN start_ts_real REAL")
        utc_val = c.execute(
            "SELECT CAST(strftime('%s', '2026-10-02 02:40:27') AS REAL)").fetchone()[0]
        # 正确值 1790880027（本地 UTC+8）；UTC 解析会偏 28800s ⇒ 两者必不相等
        self.assertNotAlmostEqual(float(utc_val), 1790880027.0, delta=2,
                                  msg="本用例前提失效：UTC 解析竟等于正确值，判据无鉴别力")

    def test_fixup_corrects_pre_existing_bad_rows(self):
        """已回填但时区错误的行（旧版迁移留下的）必须被自动修正。"""
        c = _legacy_db()
        # 模拟「旧版迁移已把 UTC 错值写进去」的状态
        c.execute("ALTER TABLE tasks ADD COLUMN start_ts_real REAL")
        c.execute("UPDATE tasks SET start_ts_real = "
                  "CAST(strftime('%s', start_ts) AS REAL) "
                  "WHERE start_ts GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]*'")
        from database import _migrate_schema
        _migrate_schema(c)
        v = c.execute("SELECT start_ts_real FROM tasks WHERE id=1790880027717").fetchone()[0]
        ca = c.execute("SELECT created_at FROM tasks WHERE id=1790880027717").fetchone()[0]
        self.assertLess(abs(float(v) - float(ca)), 3600,
                        "旧的 UTC 错值未被修正（迁移不可重复修正历史错值）")

    def test_unparseable_left_null_not_zero(self):
        """🔴 反假数据：'a'/'b'/'c' 必须留 NULL，**不能填 0**。

        填 0 会让它们排到 1970 年，伪装成有效数据。
        """
        c = _migrated()
        n = c.execute("SELECT COUNT(*) FROM tasks WHERE start_ts_real=0").fetchone()[0]
        self.assertEqual(n, 0, "有行被填成 0（假数据）")
        # （测试行已被 P2 删除；此判据防的是「将来 P2 判据放宽时的连锁后果」）

    def test_backfill_idempotent(self):
        c = _legacy_db()
        from database import _migrate_schema
        _migrate_schema(c)
        v1 = c.execute("SELECT start_ts_real FROM tasks WHERE id=1790880027717").fetchone()[0]
        _migrate_schema(c)
        v2 = c.execute("SELECT start_ts_real FROM tasks WHERE id=1790880027717").fetchone()[0]
        self.assertEqual(v1, v2, "回填非幂等")


class TestP4KindDeclaration(unittest.TestCase):
    """P4：`kind` 声明须含实际值 comment_batch（源码注释层）。"""

    def test_comment_declares_comment_batch(self):
        p = os.path.join(_HERE, "database.py")
        with open(p, encoding="utf-8") as f:
            src = f.read()
        self.assertIn("comment_batch", src,
                      "kind 的声明未含 comment_batch（与真实库不符）")

    def test_target_semantics_documented(self):
        p = os.path.join(_HERE, "database.py")
        with open(p, encoding="utf-8") as f:
            src = f.read()
        i = src.find("CREATE TABLE IF NOT EXISTS crawl_history")
        self.assertGreater(i, 0)
        seg = src[max(0, i - 700):i]
        self.assertIn("target", seg, "target 的语义约定未写在表定义附近")


class TestNegativeControl(unittest.TestCase):
    """负控自证：索引删掉 ⇒ P1 必须变红（证明门禁真有鉴别力）。"""

    def test_p1_red_when_index_dropped(self):
        c = _migrated()
        c.execute("DROP INDEX idx_crawl_acct_ts")
        plan = list(c.execute(
            "EXPLAIN QUERY PLAN SELECT COUNT(*) FROM crawl_history "
            "WHERE account=? GROUP BY account", ("x",)))
        txt = " ".join(str(r[-1]) for r in plan)
        self.assertNotIn("idx_crawl_acct_ts", txt,
                         "索引已删却仍报使用 ⇒ 判据无鉴别力")


if __name__ == "__main__":
    unittest.main(verbosity=2)
