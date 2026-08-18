# -*- coding: utf-8 -*-
"""SQLite 数据库层（替代 JSON 文件存储）

选用 SQLite 的理由：
- 桌面应用单用户本地存储，不需要 PostgreSQL/MySQL 重型服务
- Python 标准库自带 sqlite3，零依赖零配置
- ACID 事务 + WAL 并发，不会像 JSON 那样写入中途崩溃导致数据损坏
- 支持分页/索引/条件查询，数据量增长后依然高效
- 单文件 data/dyautodm.db，备份方便

表结构：
- tasks: 任务历史（替代 task_history.json）
- dm_conversations: 私信会话骨架（替代 dm_history.json 的会话层）
- dm_messages: 私信消息明细（替代 dm_history.json 的消息层）
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any

from loguru import logger

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None


def _db_path() -> Path:
    try:
        from config import settings
        p = settings.data_dir / "dyautodm.db"
    except Exception:
        p = Path("data") / "dyautodm.db"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def get_db() -> sqlite3.Connection:
    """获取全局 SQLite 连接（线程安全，WAL 模式）。

    recv_daemon 是独立进程，也会打开同一个 db 文件——WAL 模式允许
    多进程并发读 + 单写者，桌面应用量级完全够用。
    """
    global _conn
    if _conn is not None:
        return _conn
    with _lock:
        if _conn is not None:
            return _conn
        p = _db_path()
        _conn = sqlite3.connect(str(p), check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")  # 写前日志（并发友好）
        _conn.execute("PRAGMA synchronous=NORMAL")  # 正常同步（比 FULL 快，仍比 JSON 安全得多）
        _conn.execute("PRAGMA foreign_keys=ON")
        _init_tables(_conn)
        _migrate_json(_conn)
        logger.info(f"[db] SQLite 已初始化: {p}")
    return _conn


def _init_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY,
        acct TEXT NOT NULL DEFAULT '',
        live_id TEXT NOT NULL DEFAULT '',
        start_ts TEXT NOT NULL,
        end_ts TEXT DEFAULT '',
        status TEXT NOT NULL DEFAULT 'running',
        result_count INTEGER DEFAULT 0,
        config TEXT DEFAULT '{}',
        records TEXT DEFAULT '[]',
        created_at REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_tasks_created ON tasks(created_at DESC);
    CREATE INDEX IF NOT EXISTS idx_tasks_acct ON tasks(acct);

    CREATE TABLE IF NOT EXISTS dm_conversations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account TEXT NOT NULL,
        conv_id TEXT NOT NULL,
        peer_id TEXT,
        peer_name TEXT,
        short_id TEXT,
        last_ts REAL DEFAULT 0,
        unread INTEGER DEFAULT 0,
        UNIQUE(account, conv_id)
    );
    CREATE INDEX IF NOT EXISTS idx_dmconv_account ON dm_conversations(account, last_ts DESC);

    CREATE TABLE IF NOT EXISTS dm_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account TEXT NOT NULL,
        conv_id TEXT NOT NULL,
        role TEXT NOT NULL,
        text TEXT,
        msg_type TEXT DEFAULT 'text',
        extra TEXT DEFAULT '{}',
        ts REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_dmmsg_account_conv ON dm_messages(account, conv_id, ts DESC);

    -- 通用键值存储：替代 config.json / accounts.json 等小配置文件
    CREATE TABLE IF NOT EXISTS kv_store (
        key TEXT PRIMARY KEY,
        value TEXT  -- JSON 字符串
    );
    """)
    conn.commit()


def _migrate_json(conn: sqlite3.Connection) -> None:
    """首次运行时把旧 JSON 数据导入 SQLite（幂等，重复运行无副作用）。"""
    # 1) task_history.json -> tasks
    try:
        from config import settings
        th = settings.data_dir / "task_history.json"
    except Exception:
        th = Path("data") / "task_history.json"
    if th.exists():
        try:
            data = json.loads(th.read_text(encoding="utf-8"))
            migrated = 0
            for it in data if isinstance(data, list) else []:
                tid = it.get("id")
                if not tid:
                    continue
                exists = conn.execute("SELECT 1 FROM tasks WHERE id=?", (tid,)).fetchone()
                if exists:
                    continue
                conn.execute(
                    "INSERT OR IGNORE INTO tasks(id,acct,live_id,start_ts,end_ts,status,"
                    "result_count,config,records,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (tid, it.get("acct", ""), it.get("live_id", ""), it.get("start_ts", ""),
                     it.get("end_ts", ""), it.get("status", "stopped"),
                     it.get("result_count", 0), json.dumps(it.get("config", {}),
                     ensure_ascii=False), json.dumps(it.get("records", []),
                     ensure_ascii=False), tid / 1000.0),
                )
                migrated += 1
            if migrated:
                logger.info(f"[db] 从 task_history.json 迁移 {migrated} 条任务")
                conn.commit()
        except Exception as e:
            logger.warning(f"[db] task_history.json 迁移失败（不影响使用）: {e}")

    # 1.5) config.json -> kv_store("config")
    try:
        from config import settings
        cfg = settings.data_dir / "config.json"
    except Exception:
        cfg = Path("data") / "config.json"
    if cfg.exists():
        try:
            raw = cfg.read_text(encoding="utf-8")
            existing = conn.execute(
                "SELECT 1 FROM kv_store WHERE key='config'"
            ).fetchone()
            if not existing:
                conn.execute(
                    "INSERT INTO kv_store(key,value) VALUES('config',?)",
                    (raw,),
                )
                conn.commit()
                logger.info("[db] 从 config.json 迁移运行时配置")
        except Exception as e:
            logger.warning(f"[db] config.json 迁移失败: {e}")

    # 1.6) accounts.json -> kv_store("accounts_index")
    try:
        accounts_json = os.path.join(_ROOT, "auto_dm", "accounts", "accounts.json")
        if os.path.exists(accounts_json):
            raw = open(accounts_json, encoding="utf-8").read()
            existing = conn.execute(
                "SELECT 1 FROM kv_store WHERE key='accounts_index'"
            ).fetchone()
            if not existing:
                conn.execute(
                    "INSERT INTO kv_store(key,value) VALUES('accounts_index',?)",
                    (raw,),
                )
                conn.commit()
                logger.info("[db] 从 accounts.json 迁移账号索引")
    except Exception as e:
        logger.warning(f"[db] accounts.json 迁移失败: {e}")

    # 2) accounts/[name]/dm_history.json -> dm_conversations + dm_messages
    try:
        from config import settings
        accounts_dir = settings.accounts_dir
    except Exception:
        accounts_dir = Path("accounts")
    if accounts_dir.exists():
        for acct_dir in accounts_dir.iterdir():
            if not acct_dir.is_dir():
                continue
            dh = acct_dir / "dm_history.json"
            if not dh.exists():
                continue
            acct = acct_dir.name
            try:
                data = json.loads(dh.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    continue
                m_migrated = 0
                for conv_id, d in data.items():
                    if not conv_id or not isinstance(d, dict):
                        continue
                    conn.execute(
                        "INSERT OR IGNORE INTO dm_conversations(account,conv_id,peer_id,"
                        "peer_name,short_id,last_ts,unread) VALUES(?,?,?,?,?,?,?)",
                        (acct, conv_id, d.get("peer_id"), d.get("peer_name"),
                         d.get("short_id"), d.get("last_ts", 0), 0),
                    )
                    for msg in d.get("messages", []) or []:
                        conn.execute(
                            "INSERT INTO dm_messages(account,conv_id,role,text,msg_type,extra,ts)"
                            " VALUES(?,?,?,?,?,?,?)",
                            (acct, conv_id, msg.get("role", "them"), msg.get("text", ""),
                             msg.get("msg_type", "text"),
                             json.dumps(msg.get("extra", {}), ensure_ascii=False),
                             msg.get("ts", 0)),
                        )
                        m_migrated += 1
                if m_migrated:
                    logger.info(f"[db] 从 {acct}/dm_history.json 迁移 {m_migrated} 条消息")
            except Exception as e:
                logger.warning(f"[db] {acct}/dm_history.json 迁移失败: {e}")
        conn.commit()


def exec_query(sql: str, params: tuple = ()) -> list[dict]:
    """便捷查询，返回 dict 列表。"""
    conn = get_db()
    with _lock:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def exec_modify(sql: str, params: tuple = ()) -> int:
    """便捷写入（INSERT/UPDATE/DELETE），返回 lastrowid 或 rowcount。"""
    conn = get_db()
    with _lock:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid or cur.rowcount


# ------------------------------------------------------------------
# 通用键值存储（替代 config.json / accounts.json 等小配置文件）
# ------------------------------------------------------------------
def get_kv(key: str, default: str | None = None) -> str | None:
    """读取一个键值。"""
    conn = get_db()
    with _lock:
        r = conn.execute("SELECT value FROM kv_store WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default


def get_kv_json(key: str, default: Any = None) -> Any:
    """读取一个键值并 JSON 反序列化。"""
    v = get_kv(key)
    if v is None:
        return default
    try:
        return json.loads(v)
    except Exception:
        return default


def set_kv(key: str, value: str) -> None:
    """写入一个键值（upsert）。"""
    conn = get_db()
    with _lock:
        conn.execute(
            "INSERT INTO kv_store(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        conn.commit()


def set_kv_json(key: str, value: Any) -> None:
    """把对象 JSON 序列化后写入键值（upsert）。"""
    set_kv(key, json.dumps(value, ensure_ascii=False))
