# coding=utf-8
"""一次性数据迁移：全局空间 DB -> 会员空间 DB（2026-09-08）。

背景：会员体系（v0.37.0）登录后 database 切到会员空间
<space>/data/dyautodm.db，而历史业务数据仍在全局 <root>/data/dyautodm.db，
导致私信中心会话列表全空（用户体感「数据丢失」）。

策略：
  - 整表复制（INSERT OR IGNORE，幂等，重复执行安全）；
  - 仅当会员库该表为空时才迁移，已有数据不覆盖；
  - 全局库原样保留作为备份，不删除、不移动。

用法：
  python backend/scripts/migrate_global_to_member.py [member_id]
不传 member_id 时自动取注册表里的第一个会员。
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys

# 让脚本可以直接 import backend 下的模块
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import vbrowser  # noqa: E402

# 需要迁移的业务表（结构由 database._init_tables 保证，两边一致）
TABLES = (
    "dm_conversations",
    "dm_messages",
    "tasks",
    "crawl_history",
    "dm_uid_sink",
    "kv_store",
)


def member_db_path(member_id: str | None = None) -> str | None:
    root = vbrowser.app_root()
    members_dir = os.path.join(root, "members")
    if member_id:
        p = os.path.join(members_dir, member_id, "data", "dyautodm.db")
        return p if os.path.isdir(os.path.dirname(p)) else None
    if not os.path.isdir(members_dir):
        return None
    # 自动取第一个会员（按目录名排序，稳定）
    for name in sorted(os.listdir(members_dir)):
        p = os.path.join(members_dir, name, "data", "dyautodm.db")
        if os.path.isfile(p):
            return p
        # 会员刚建、DB 还没初始化时也要能定位
        d = os.path.join(members_dir, name, "data")
        if os.path.isdir(d):
            return p
    return None


def main() -> int:
    member_id = sys.argv[1] if len(sys.argv) > 1 else None
    root = vbrowser.app_root()
    g_db = os.path.join(root, "data", "dyautodm.db")
    m_db = member_db_path(member_id)

    if not os.path.isfile(g_db):
        print(f"[跳过] 全局库不存在: {g_db}")
        return 0
    if not m_db:
        print("[跳过] 未找到会员空间 DB")
        return 0
    os.makedirs(os.path.dirname(m_db), exist_ok=True)
    if os.path.abspath(m_db) == os.path.abspath(g_db):
        print("[跳过] 会员库与全局库是同一个文件")
        return 0

    print(f"源(全局): {g_db}")
    print(f"目标(会员): {m_db}")

    src = sqlite3.connect(g_db, timeout=15)
    dst = sqlite3.connect(m_db, timeout=15)
    src.row_factory = sqlite3.Row
    dst.row_factory = sqlite3.Row
    try:
        for t in TABLES:
            try:
                ex = dst.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                    (t,)).fetchone()
                if not ex:
                    print(f"  {t}: 目标表不存在，跳过（需先初始化会员库）")
                    continue
                cnt = dst.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                if cnt:
                    print(f"  {t}: 已有 {cnt} 行，跳过（不覆盖）")
                    continue
                rows = src.execute(f"SELECT * FROM {t}").fetchall()
                if not rows:
                    print(f"  {t}: 源无数据")
                    continue
                cols = list(rows[0].keys())
                ph = ",".join("?" * len(cols))
                collist = ",".join(cols)
                dst.executemany(
                    f"INSERT OR IGNORE INTO {t} ({collist}) VALUES ({ph})",
                    [tuple(r) for r in rows])
                dst.commit()
                after = dst.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                print(f"  {t}: 迁移 {len(rows)} 行 -> 现有 {after} 行")
            except Exception as e:  # noqa: BLE001
                print(f"  {t}: 失败 {e}")
                try:
                    dst.rollback()
                except Exception:
                    pass
    finally:
        src.close()
        dst.close()

    # 汇总
    c = sqlite3.connect(m_db)
    try:
        conv = c.execute("SELECT COUNT(*) FROM dm_conversations").fetchone()[0]
        msg = c.execute("SELECT COUNT(*) FROM dm_messages").fetchone()[0]
        print(f"[完成] 会员库现有：会话 {conv}，消息 {msg}")
    except Exception as e:
        print("[完成] 但汇总失败:", e)
    finally:
        c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
