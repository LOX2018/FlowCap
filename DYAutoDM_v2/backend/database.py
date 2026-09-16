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
    # 会员体系（v0.37.0）：登录后数据库切到会员数据空间
    # <app_root>/members/<member_id>/data/dyautodm.db，各会员完全隔离；
    # 未登录（如刚启动/未登录态健康探测）保持原路径不变。
    try:
        from services import member_ctx
        mp = member_ctx.db_path()
        if mp:
            p = Path(mp)
            p.parent.mkdir(parents=True, exist_ok=True)
            return p
    except Exception:
        pass
    try:
        from vbrowser import app_root
        p = Path(app_root()) / "data" / "dyautodm.db"
    except Exception:
        try:
            from config import settings
            p = settings.data_dir / "dyautodm.db"
        except Exception:
            p = Path("data") / "dyautodm.db"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def reset_connection() -> None:
    """关闭并丢弃当前全局连接（会员切换/登出时调用）。

    下一处 get_db() 会按新会员的 _db_path() 重新建连并跑建表/迁移。
    recv_daemon 等子进程各自持有独立连接，不受影响。
    """
    global _conn
    with _lock:
        if _conn is not None:
            try:
                _conn.close()
            except Exception:
                pass
            _conn = None


def get_db() -> sqlite3.Connection:
    """获取全局 SQLite 连接（线程安全，WAL 模式）。

    会员体系（v0.37.0）：每次调用校验当前连接指向的库文件与
    当前会员数据空间是否一致 —— 不一致（登录了别的会员）自动重建，
    防止任何绕过 reset_connection 的路径把上个会员的数据读出来。

    recv_daemon 是独立进程，也会打开同一个 db 文件——WAL 模式允许
    多进程并发读 + 单写者，桌面应用量级完全够用。
    """
    global _conn
    if _conn is not None:
        # 会员一致性校验：连接的库文件必须属于当前会员空间
        try:
            from services import member_ctx
            want = member_ctx.db_path()
            if want:
                with _lock:
                    if _conn is not None:
                        cur_path = str(
                            _conn.execute("PRAGMA database_list").fetchone()[2])
                        if os.path.abspath(cur_path) != os.path.abspath(want):
                            # 2026-09-17 修补（审查 P2-8）：原实现在**未持
                            # `_lock`** 的情况下直接 `_conn.close()` —— 另一
                            # 线程可能正持锁使用该连接（exec_query/exec_modify），
                            # 导致 `Cannot operate on a closed database`；
                            # 或两线程同时判定不匹配而重复 close。
                            # 现把整段校验移入 `_lock`，close 后再置空全局引用。
                            try:
                                _conn.close()
                            except Exception:
                                pass
                            _conn = None
        except Exception:
            pass
        if _conn is not None:
            return _conn
    with _lock:
        if _conn is not None:
            return _conn
        p = _db_path()
        _conn = sqlite3.connect(str(p), check_same_thread=False, timeout=30)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")  # 写前日志（并发友好）
        # 2026-09-06 全局并发治理（多进程写竞争）：
        # backend + N×recv_daemon + BCC 会并发写同一个 db。WAL 只解决
        # 「读写不互斥」，不解决「写写竞争」——没有 busy_timeout 时，
        # 并发写会立刻抛 "database is locked"（默认超时 5s 且不重试）。
        # 设 30s 忙等 + 进程内串行写锁，彻底消灭并发写崩溃。
        _conn.execute("PRAGMA busy_timeout=30000")  # 30s 忙等重试
        _conn.execute("PRAGMA synchronous=NORMAL")  # 正常同步（比 FULL 快，仍比 JSON 安全得多）
        _conn.execute("PRAGMA foreign_keys=ON")
        _init_tables(_conn)
        _migrate_schema(_conn)
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
        created_at REAL NOT NULL,
        pid INTEGER DEFAULT 0
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
        avatar TEXT,
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

    -- 数据采集历史（crawl.py）：关键词搜索 / 评论采集的结果摘要
    CREATE TABLE IF NOT EXISTS crawl_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account TEXT NOT NULL,
        kind TEXT NOT NULL DEFAULT 'video',  -- video | user | comment
        keyword TEXT DEFAULT '',             -- 搜索关键词（comment 类为空）
        target TEXT DEFAULT '',              -- 评论类=aweme_id
        result_count INTEGER DEFAULT 0,
        payload TEXT DEFAULT '[]',           -- 完整结果 JSON
        ts REAL NOT NULL
    );
    -- 直播弹幕/视频采集 UID 沉淀池（2026-09-07）：
    -- 同一 UID 多次发弹幕只保留一次有效记录，避免对同一陌生人重复发送私信。
    -- 跨任务/跨来源（采集 + 监听）共享，进程重启不丢。
    CREATE TABLE IF NOT EXISTS dm_uid_sink (
        account TEXT NOT NULL,
        peer_uid TEXT NOT NULL,
        nickname TEXT DEFAULT '',
        source TEXT DEFAULT '',          -- live(弹幕) | crawl(采集) | manual
        first_seen_ts REAL NOT NULL,     -- 首次沉淀时间
        sent_ts REAL,                    -- 已发送时间（NULL=仅沉淀未发）
        send_count INTEGER DEFAULT 0,    -- 已发送次数（正常应 <=1）
        PRIMARY KEY (account, peer_uid)
    );
    CREATE INDEX IF NOT EXISTS idx_uid_sink_ts ON dm_uid_sink(sent_ts DESC);
    """)
    conn.commit()


def _migrate_schema(conn: sqlite3.Connection) -> None:
    """迁移旧表结构（新增列等），幂等。"""
    try:
        conn.execute("ALTER TABLE dm_conversations ADD COLUMN avatar TEXT")
    except Exception:
        pass  # 列已存在
    try:
        conn.execute("ALTER TABLE tasks ADD COLUMN pid INTEGER DEFAULT 0")
    except Exception:
        pass  # 列已存在
    # dm_messages: 新增 msg_id（抖音消息唯一 ID，protobuf field 3）
    # 并建唯一索引，让 INSERT OR IGNORE 真正生效，杜绝重复落库。
    try:
        conn.execute("ALTER TABLE dm_messages ADD COLUMN msg_id TEXT")
    except Exception:
        pass  # 列已存在
    try:
        # 仅对 msg_id 非空的行生效（旧数据 msg_id IS NULL 不受唯一约束影响）
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uniq_dmmsg "
            "ON dm_messages(account, conv_id, msg_id) WHERE msg_id IS NOT NULL"
        )
    except Exception:
        pass
    try:
        # 兜底去重：同一会话同一角色同一文本同一毫秒时间戳视为同一条
        #
        # ⚠️ 2026-09-17 修补（审查 P2-11）：本索引有两个已知副作用，此前被
        # `except: pass` 完全掩盖：
        #   ① 同一毫秒内同文本的**两条真实消息**，第二条会被 INSERT OR IGNORE
        #      静默丢弃（以去重为名造成数据丢失）；
        #   ② 在已有重复数据的旧库上创建会**失败**，此时去重实际未生效，
        #      但没有任何告警 —— 运维以为已去重。
        # 现改为：创建失败必须告警（不再静默）。副作用 ① 属设计取舍
        # （宁可极偶发丢重复，也要防 WS 回声重复入库），保留现状并在此注明。
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uniq_dmmsg_fallback "
            "ON dm_messages(account, conv_id, role, text, CAST(ts*1000 AS INTEGER))"
        )
    except Exception as _e:
        logger.warning(
            "DB-002",
            f"[db] 兜底去重索引 uniq_dmmsg_fallback 创建失败，"
            f"同毫秒重复消息将**不会被去重**：{type(_e).__name__}: {_e}；"
            f"通常由库内已存在重复行引起，可手工清理后重启以启用")


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
            logger.warning(f"[DB-001] " + f"[db] task_history.json 迁移失败（不影响使用）: {e}")

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
            logger.warning(f"[DB-002] " + f"[db] config.json 迁移失败: {e}")

    # 1.6) accounts.json -> kv_store("accounts_index")
    try:
        from vbrowser import app_root
        accounts_json = os.path.join(app_root(), "auto_dm", "accounts", "accounts.json")
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
        logger.warning(f"[DB-003] " + f"[db] accounts.json 迁移失败: {e}")

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
                        # 2026-09-06 全局并发治理：迁移路径也改 OR IGNORE
                        # （此前裸 INSERT，重复运行迁移会重复灌入消息）
                        conn.execute(
                            "INSERT OR IGNORE INTO dm_messages("
                            "account,conv_id,role,text,msg_type,extra,ts,msg_id)"
                            " VALUES(?,?,?,?,?,?,?,?)",
                            (acct, conv_id, msg.get("role", "them"), msg.get("text", ""),
                             msg.get("msg_type", "text"),
                             json.dumps(msg.get("extra", {}), ensure_ascii=False),
                             msg.get("ts", 0),
                             str(msg.get("msg_id")) if msg.get("msg_id") else None),
                        )
                        m_migrated += 1
                if m_migrated:
                    logger.info(f"[db] 从 {acct}/dm_history.json 迁移 {m_migrated} 条消息")
            except Exception as e:
                logger.warning(f"[DB-004] " + f"[db] {acct}/dm_history.json 迁移失败: {e}")
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
