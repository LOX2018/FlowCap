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
    except Exception as _e_db:
        logger.warning(f"[DB] db_path() 异常: {_e_db}")
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
        except Exception as e:  # noqa: BLE001
            # 2026-09-17 修补（OCR 审查 HIGH —— 守卫失效被静默吞掉）：
            # 原为裸 `except Exception: pass`。会员一致性守卫一旦自身抛错
            # （db_path()/PRAGMA 失败），就会**静默**继续并可能返回属于
            # 另一个会员的连接 —— 正是该守卫要防的跨会员数据泄漏。
            # 2026-09-17：loguru 用 `{}` 占位（printf 风格 %s 会让参数被丢弃）
            logger.warning("[db] 会员一致性校验失败（守卫可能失效，"
                           "存在跨会员读取风险）: {}", e)
        if _conn is not None:
            return _conn
    with _lock:
        if _conn is not None:
            return _conn
        p = _db_path()
        # 2026-09-17 修补（OCR 审查 HIGH —— 半初始化连接被发布）：
        # 原实现在建连后**立即**赋给全局 `_conn`，随后才跑 `_init_tables`
        # / `_migrate_*`。若其中任一步抛错，异常带着"已发布但未初始化"的
        # 全局 `_conn` 逃出锁 → 后续 `get_db()` 命中 `_conn is not None`
        # 直接返回该半成品连接（迁移永不完成，静默不一致）。
        # 现改为：先建成到**局部** `c`，全部初始化成功后才发布到 `_conn`；
        # 中途失败则关闭 `c` 并原样抛出。
        c = sqlite3.connect(str(p), check_same_thread=False, timeout=30)
        try:
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")  # 写前日志（并发友好）
            # 2026-09-06 全局并发治理（多进程写竞争）：
            # backend + N×recv_daemon + BCC 会并发写同一个 db。WAL 只解决
            # 「读写不互斥」，不解决「写写竞争」——没有 busy_timeout 时，
            # 并发写会立刻抛 "database is locked"（默认超时 5s 且不重试）。
            # 设 30s 忙等 + 进程内串行写锁，彻底消灭并发写崩溃。
            c.execute("PRAGMA busy_timeout=30000")  # 30s 忙等重试
            c.execute("PRAGMA synchronous=NORMAL")  # 正常同步（比 FULL 快，仍比 JSON 安全得多）
            c.execute("PRAGMA foreign_keys=ON")
            _init_tables(c)
            _migrate_schema(c)
            _migrate_json(c)
        except Exception:
            try:
                c.close()
            except Exception:
                pass
            raise
        _conn = c
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
        pid INTEGER DEFAULT 0,
        kind TEXT DEFAULT '',
        params TEXT DEFAULT '{}',
        error_code TEXT DEFAULT '',
        updated_at REAL DEFAULT 0
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
        conv_type INTEGER DEFAULT 1,   -- 1=单聊 2=群聊（2026-09-17 新增，照上游 conv_type）
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
        -- ★ 2026-10-03 P5 字段拆分：`msg_type` 与上游原始码**分列**。
        --   msg_type = **语义名**（text / image / video / delivery_marker …）
        --   msg_code = **上游数字码**（7 / 27 / 8 / 50001 …），语义名时为 NULL。
        --   拆分前两者混在一列，读侧判据分裂（=='50001' 与 =='text' 并存）。
        --   迁移见 _migrate_schema（幂等 ALTER + 回填），旧库自动对齐。
        msg_code TEXT,
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
    -- ⚠️ `kind` 的**实际取值**（★ 2026-10-03 按实测补全，此前注释只写 3 种、
    --   与真实库不符）：video | user | comment | comment_batch
    --   （`comment_batch` 由批量采集写入；实测见 audit_db_fields.py 输出）
    --   `target` 语义随 kind 变：comment/comment_batch = aweme_id；
    --   video/user = 关键词。消费方**必须**先看 kind 再读 target。
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
        -- ADR-007 / C-06（2026-09-24）高价值筛查 + 沉淀窗口 + 按人聚合：
        keyword_score INTEGER DEFAULT 0,      -- 关键词权重累加（过滤器用）
        is_high_value INTEGER DEFAULT 0,      -- 是否高价值（关键词/LLM 判定）
        high_value_reason TEXT DEFAULT '',    -- 判定理由摘要（LLM 写入）
        window_end_ts REAL,                   -- 聚合窗口到期时间（NULL=未设窗口）
        aggregate_text TEXT DEFAULT '',       -- 窗口内累积弹幕文本（发送后保留审计）
        PRIMARY KEY (account, peer_uid)
    );
    -- 多账号跨账号沉淀池（2026-09-22，ADR-002 §5.5(B)）：
    -- 同一 peer_uid 被本机任一账号发过后，其余账号不再发。
    -- 与 dm_uid_sink（per-account）共存，由 sink_global_scope 开关选择。
    CREATE TABLE IF NOT EXISTS dm_cross_sink (
        peer_uid TEXT NOT NULL PRIMARY KEY,
        account_sent TEXT NOT NULL,       -- 最后发送的账号
        nickname TEXT DEFAULT '',
        source TEXT DEFAULT '',           -- live(弹幕) | crawl(采集) | manual
        sent_ts REAL,                    -- 最后发送时间
        cool_until REAL,                 -- 冷却到期时间（sent_ts + cooldown_seconds，预计算）
        send_count INTEGER DEFAULT 0     -- 累计发送次数
    );
    CREATE INDEX IF NOT EXISTS idx_uid_sink_ts ON dm_uid_sink(sent_ts DESC);
    -- ⚠️ 注意：**不要**在此 executescript 里给新列建索引 ——
    -- 对「已存在的旧库」`CREATE TABLE IF NOT EXISTS` 是 no-op，新列要等
    -- `_migrate_schema` 的 ALTER 才出现；此处建 `window_end_ts` 索引会
    -- 直接 `no such column`（实测踩到，test_uid_sink_ext 首轮 13 ERROR）。
    -- 窗口索引统一在 `_migrate_schema` 的 ALTER 之后创建。
    """)
    conn.commit()


def _migrate_schema(conn: sqlite3.Connection) -> None:
    """迁移旧表结构（新增列等），幂等。"""
    try:
        conn.execute("ALTER TABLE dm_conversations ADD COLUMN avatar TEXT")
    except Exception:
        pass  # 列已存在
    try:
        # 2026-09-17：会话类型（1=单聊 2=群聊）。
        # 上游的判定规则：**conv_id 为纯数字 → 群聊**（实测群聊 conv_id 即 short_id）。
        # 默认 1（单聊），保证旧数据语义不变；由 conversation_capture 回填真实值。
        conn.execute("ALTER TABLE dm_conversations ADD COLUMN conv_type INTEGER DEFAULT 1")
    except Exception:
        pass  # 列已存在
    try:
        # 旧库存量数据回填：纯数字 conv_id → 群聊（与上游判定口径一致）
        # 2026-09-18 审查修复（MEDIUM，两处；第二处是验收脚本抓出来的）：
        # ① 原串缺括号 → AND 优先于 OR，实际语义是
        #    `(conv_type IS NULL) OR (conv_type=1 AND conv_id 纯数字)`；
        # ② **① 修好后仍不对**：`conv_type IS NULL` 独立成真，会把**单聊形态**
        #    （`0:1:a:b`）的行也无条件写成群聊(2)。验收脚本 A14 用真实 sqlite
        #    跑出该误判（被误改: 0:1:11:22）才发现 —— 「补个括号」这种看似
        #    安全的修法，若不做形态分类判定照样是错的。
        # 现按 **conv_id 形态**分类（判据同 `services.conv_identity.conv_type()`）：
        #    纯数字 → 2（群聊）；其余形态（含 NULL，多为旧库）→ 1（单聊）。
        conn.execute("UPDATE dm_conversations SET conv_type=2 "
                     "WHERE conv_id GLOB '[0-9]*' "
                     "  AND conv_id NOT GLOB '*[^0-9]*'")
        conn.execute("UPDATE dm_conversations SET conv_type=1 "
                     "WHERE conv_type IS NULL "
                     "  AND NOT (conv_id GLOB '[0-9]*' "
                     "           AND conv_id NOT GLOB '*[^0-9]*')")
    except Exception as _e:  # noqa: BLE001
        logger.warning(f"[db] conv_type 回填失败（不影响启动）: {_e}")
    try:
        conn.execute("ALTER TABLE tasks ADD COLUMN pid INTEGER DEFAULT 0")
    except Exception:
        pass  # 列已存在
    # ★ 2026-10-04（ADR-035 统一任务模型）：tasks 表加通用列，使采集/直播/定时
    # 三类任务可共用一张表。**加列不重建**（零迁移）：kind 为空 ⇒ 按 live_id 非空
    # 回落为 'live'（老数据照读）。逐列 ADD COLUMN，幂等；失败仅告警不阻断启动。
    for _ddl in (
        "ALTER TABLE tasks ADD COLUMN kind TEXT DEFAULT ''",
        "ALTER TABLE tasks ADD COLUMN params TEXT DEFAULT '{}'",
        "ALTER TABLE tasks ADD COLUMN error_code TEXT DEFAULT ''",
        "ALTER TABLE tasks ADD COLUMN updated_at REAL DEFAULT 0",
    ):
        try:
            conn.execute(_ddl)
        except Exception:
            pass  # 列已存在
    try:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_kind ON tasks(kind)")
    except Exception:
        pass
    # ── dm_uid_sink 扩列（ADR-007 / C-06，2026-09-24）────────────────
    # 高价值筛查 + 沉淀窗口 + 按人聚合。逐列 ADD COLUMN，幂等。
    # 回滚：保留列、把新配置恢复默认即退化为原行为（旧代码无视新列）。
    for _ddl in (
        "ALTER TABLE dm_uid_sink ADD COLUMN keyword_score INTEGER DEFAULT 0",
        "ALTER TABLE dm_uid_sink ADD COLUMN is_high_value INTEGER DEFAULT 0",
        "ALTER TABLE dm_uid_sink ADD COLUMN high_value_reason TEXT DEFAULT ''",
        "ALTER TABLE dm_uid_sink ADD COLUMN window_end_ts REAL",
        "ALTER TABLE dm_uid_sink ADD COLUMN aggregate_text TEXT DEFAULT ''",
    ):
        try:
            conn.execute(_ddl)
        except Exception:
            pass  # 列已存在
    try:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_uid_sink_window "
                     "ON dm_uid_sink(window_end_ts)")
    except Exception:
        pass
    # dm_messages: 新增 msg_id（抖音消息唯一 ID，protobuf field 3）
    # 并建唯一索引，让 INSERT OR IGNORE 真正生效，杜绝重复落库。
    # 🔴 2026-10-04 修（审计 P0-2）：原为 `except Exception: pass  # 列已存在`。
    # 静默吞掉**所有**异常 —— 磁盘满 / 锁冲突 / 权限问题导致的失败同样被
    # 吞掉，运维以为已迁移而实际 schema 未更新，后续 INSERT 引用该列才炸
    # （且错误被更外层 try 吞掉，无法归因）。现与 uniq_dmmsg_fallback
    # 同标准：**区分「列已存在」与真失败**，真失败必须告警（DB-001）。
    try:
        conn.execute("ALTER TABLE dm_messages ADD COLUMN msg_id TEXT")
    except Exception as _e:
        if "duplicate column name" in str(_e).lower():
            pass  # 列已存在：预期路径，不告警
        else:
            logger.warning(f"[DB-006] " + f"[db] dm_messages.msg_id 列添加失败，"
                f"消息唯一 ID 将**不会落库**（去重与溯源受影响）："
                f"{type(_e).__name__}: {_e}")
    try:
        # 仅对 msg_id 非空的行生效（旧数据 msg_id IS NULL 不受唯一约束影响）
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uniq_dmmsg "
            "ON dm_messages(account, conv_id, msg_id) WHERE msg_id IS NOT NULL"
        )
    except Exception as _e:
        # 同 DB-001 标准：索引创建失败 ⇒ INSERT OR IGNORE 不去重却无任何提示
        logger.warning(f"[DB-007] " + f"[db] 唯一索引 uniq_dmmsg 创建失败，"
            f"msg_id 去重**未生效**（可能重复入库）："
            f"{type(_e).__name__}: {_e}；通常由库内已存在重复行引起")
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
        logger.warning(f"[DB-002] " + f"[db] 兜底去重索引 uniq_dmmsg_fallback 创建失败，"
            f"同毫秒重复消息将**不会被去重**：{type(_e).__name__}: {_e}；"
            f"通常由库内已存在重复行引起，可手工清理后重启以启用")

    # ★ 2026-10-03 字段整理（P5）：`msg_type` 一列曾**混装两套体系** ——
    #   语义名（'text' / 'delivery_marker'）与上游数字码（'7' / '27' / '50001'）。
    #   实测读侧判据因此分裂（`== '50001'` 与 `== 'text'` 并存于同一列）。
    #   ⇒ 拆为两列：`msg_type`=语义名，`msg_code`=上游码。
    try:
        conn.execute("ALTER TABLE dm_messages ADD COLUMN msg_code TEXT")
    except Exception:
        pass  # 列已存在
    try:
        # 回填：把**已登记为上游码**的值从 msg_type 搬到 msg_code（幂等）。
        #   判据用注册表口径（message_schema.MSG_TYPES 的 key 集合），
        #   而不是「看起来像数字」——后者会把语义名 'text' 也误搬（其实不冲突，
        #   但语义列必须只留语义名，保持列语义单一）。
        # ⚠️ 不可 import services（循环依赖 + 启动期开销）⇒ 用显式映射表，
        #   判据与 MSG_TYPES 保持一致；漂移由 test_msg_type_code_split 守住。
        #   ★ 映射 = MSG_TYPES 的**纯数字 key**（实测 2026-10-03）：
        #     0→text, 1→text, 7→text, 8→video, 15→text, 27→image, 50010→text
        #   ⚠️ 不含 '5'/'17' —— 它们只在 api/messages._front_type 的**读侧**映射里，
        #     **未登记进 MSG_TYPES** ⇒ 不该在此搬运（否则与注册表漂移）。
        #     门禁 test_msg_type_code_split::test_whitelist_matches_registry 守这条。
        #   ⚠️ '50001'（已读回执）**故意不在映射**：recv_daemon.py:705 已在
        #     落库前拦掉，实测真实库 0 行 ⇒ 无存量可搬。**不可**为"完整性"
        #     而加它，否则会与注册表漂移（门禁会红）。若将来某路径漏拦，
        #     该行会以 msg_type='50001' 留存并被读侧兼容过滤兜住。
        #
        # 🔴 为什么用 CASE WHEN 而非 `msg_type IN (...)` 逐条 UPDATE：
        #   ① 语义更强 —— 直接表达「码 → 名」的映射关系，而非「哪些值要搬」；
        #   ② 一次 UPDATE 完成搬码+归一，**天然原子**，不会出现两条语句
        #      只跑了一半的脱耦（我曾实测到该盲区：禁掉搬码那一句，
        #      归一那句仍独立生效 ⇒ 码被静默丢弃）；
        #   ③ 不写 `msg_type IN (多值)` 这种「同一语义多名字并列」形态 ——
        #      铁律门禁 R8-6 正是禁它（该形态是补丁痕迹的信号）。
        #      ⚠️ 不可为绕过门禁而放宽 R8-6 判据。
        # 🔴 SQLite 的 CASE 有两种形态，别混：
        #   ① 简单式  `CASE <表达式> WHEN <值> THEN <结果> ... END`
        #   ② 搜索式  `CASE WHEN <条件> THEN <结果> ... END`
        #   本处用**简单式**：被判断的表达式就是 `msg_type` 本身（= 值），
        #   写成搜索式（`CASE WHEN '7' THEN`）会**缺条件** ⇒ SQL 语法错，
        #   且整个回填静默失败（实测踩到：`hi/7` 被改成 `hi/text/1`，
        #   msg_code 存成了**枚举序号**而非原值）。判据见下方门禁。
        _UPD = (
            "UPDATE dm_messages SET "
            "  msg_code = CASE msg_type {c} ELSE msg_code END, "
            "  msg_type = CASE msg_type {n} ELSE msg_type END "
            "WHERE msg_code IS NULL AND msg_type GLOB '[0-9]*'"
        )
        # 「纯数字」判据：GLOB 全数字。与注册表映射表配对，缺项即漂移。
        _MAP = {"0": "text", "1": "text", "7": "text", "8": "video",
                "15": "text", "27": "image", "50010": "text"}
        # ⚠️ 必须按 key 长度**升序**排（'1' 先于 '15'、'5' 先于 '50010'）：
        #   CASE 简单式按出现顺序**首次命中即返回**，短码在前才不会吃掉长码。
        _code_when = " ".join(
            f"WHEN '{k}' THEN '{k}'" for k in sorted(_MAP, key=len))
        _name_when = " ".join(
            f"WHEN '{k}' THEN '{v}'" for k, v in sorted(_MAP.items(), key=lambda x: len(x[0])))
        conn.execute(_UPD.format(c=_code_when, n=_name_when))
    except Exception as _e:
        logger.warning(f"[DB-006] [db] msg_type/msg_code 拆分回填失败"
                       f"（不影响启动，读侧有兼容分支）：{type(_e).__name__}: {_e}")

    # ★ 2026-10-03 字段整理（P1）：`crawl_history` 此前**零索引**。
    #   实测（audit_db_fields.py）：8 列全部「无索引左前缀」，24 行。
    #   而 `_crawl_stats_sync` / `/api/crawl/stats` 正是按 **account + ts**
    #   聚合（见该函数 SQL）⇒ 每次打开总览都**全表扫**。
    #   复合索引左前缀 account 命中 account 查询；ts 供排序/范围。
    try:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_crawl_acct_ts "
                     "ON crawl_history(account, ts DESC)")
    except Exception as _e:
        logger.warning(f"[DB-003] [db] crawl_history 索引创建失败，"
                       f"统计将退化为全表扫：{type(_e).__name__}: {_e}")
    # `tasks.live_id`：任务历史按直播间号检索的入口（复看历史任务）。
    try:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_live "
                     "ON tasks(live_id)")
    except Exception:
        pass

    # ★ 2026-10-03 字段整理（P3）：`tasks` 同一事实存了**两种时间格式** ——
    #   `start_ts` TEXT（'2026-10-02 02:40:27'）与 `created_at` REAL（1790880027.717）。
    #   实测真实业务行两者指同一时刻。排序/比较必须各自转换，易错。
    #   ⇒ 增 REAL 列并**回填**（可解析的文本 → epoch 秒；不可解析的留 NULL）。
    #   文本列保留（既有消费方不改），新列供排序/范围查询。
    try:
        conn.execute("ALTER TABLE tasks ADD COLUMN start_ts_real REAL")
    except Exception:
        pass  # 列已存在
    try:
        conn.execute("ALTER TABLE tasks ADD COLUMN end_ts_real REAL")
    except Exception:
        pass  # 列已存在
    try:
        # 回填：把「北京时间」文本转 epoch 秒。
        # 🔴 时区铁律（2026-10-03 修正）：`strftime('%s')` 按 **UTC** 解析，
        #   而 `start_ts` 存的是**本地时间**（UTC+8）⇒ 直接用会**偏 8 小时**
        #   （实测：'2026-10-02 02:40:27' 得 1790908827，正确值 1790880027）。
        #   正确做法：SQLite 的 modifier 是「**叠加**到 UTC 结果上」，而我们要的是
        #   「本地时间当成 UTC 解析」再**减**掉时差 ⇒ 必须用 `'-8 hours'`
        #   （实测：'+8 hours'=1790937627 偏 +16h；'-8 hours'=1790880027 正确）。
        # 不可解析（如测试脏数据 'a'/'b'）⇒ 保持 NULL，**不猜测、不填 0**
        # （填 0 会让它们排到 1970 年，伪装成有效数据 = 假数据）。
        conn.execute(
            "UPDATE tasks SET start_ts_real = "
            "CAST(strftime('%s', start_ts, '-8 hours') AS REAL) "
            "WHERE start_ts_real IS NULL AND start_ts GLOB "
            "'[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9] [0-9][0-9]:*'"
        )
        conn.execute(
            "UPDATE tasks SET end_ts_real = "
            "CAST(strftime('%s', end_ts, '-8 hours') AS REAL) "
            "WHERE end_ts_real IS NULL AND end_ts != '' AND end_ts GLOB "
            "'[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9] [0-9][0-9]:*'"
        )
        # 🔴 修正已回填但**时区错误**的行（epoch 明显大于 created_at 8h 以上的）。
        #   判据：start_ts_real - created_at 落在 [7h, 9h] ⇒ 是 UTC 误算的特征。
        #   只改这一类，不动其他行。
        conn.execute(
            "UPDATE tasks SET start_ts_real = start_ts_real - 28800 "
            "WHERE created_at > 0 AND start_ts_real IS NOT NULL "
            "AND start_ts_real - created_at BETWEEN 25200 AND 32400"
        )
        conn.execute(
            "UPDATE tasks SET end_ts_real = end_ts_real - 28800 "
            "WHERE created_at > 0 AND end_ts_real IS NOT NULL "
            "AND end_ts_real - created_at BETWEEN 25200 AND 32400"
        )
    except Exception as _e:
        logger.warning(f"[DB-004] [db] tasks 时间列回填失败（旧格式列仍在，不影响读写）："
                       f"{type(_e).__name__}: {_e}")

    # ★ 2026-10-03 字段整理（P2）：清理 `tasks` 里的**脚本测试残留行**。
    #   判据（机械、可复跑，不靠猜）：`created_at < 1` 的行必是测试桩 ——
    #   实测这 3 行 acct=''/live_id='' 且 start_ts 为 'a'/'b'/'c'（非日期）。
    #   ⚠️ 只删测试桩：**不删**任何 created_at >= 1 的行（真实业务行是
    #   epoch 秒 ≈ 1.79e9，远大于 1）。
    try:
        _n = conn.execute(
            "DELETE FROM tasks WHERE created_at IS NOT NULL AND created_at < 1"
        ).rowcount
        if _n > 0:
            logger.info(f"[db] 清理 tasks 测试残留行 { _n } 条"
                        f"（判据 created_at < 1）")
    except Exception as _e:
        logger.warning(f"[DB-005] [db] 清理 tasks 测试行失败：{type(_e).__name__}: {_e}")


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
                        # ADR-012：迁移路径也经**单一出口**（补 kind、未知类型降级）
                        from services.message_schema import MessageRecord
                        _rec = MessageRecord.build(
                            text=msg.get("text", ""),
                            msg_type=msg.get("msg_type", "text"),
                            extra=msg.get("extra") or {},
                            role=msg.get("role", "them"))
                        conn.execute(
                            "INSERT OR IGNORE INTO dm_messages("
                            "account,conv_id,role,text,msg_type,msg_code,extra,ts,msg_id)"
                            " VALUES(?,?,?,?,?,?,?,?)",
                            _rec.tuple(
                                acct, conv_id,
                                ts=msg.get("ts", 0),
                                msg_id=(str(msg.get("msg_id"))
                                        if msg.get("msg_id") else None),
                                role=msg.get("role", "them")),
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
    """便捷写入（INSERT/UPDATE/DELETE），返回 lastrowid 或影响行数。

    2026-09-17 修补（OCR 审查 HIGH）：原为 `return cur.lastrowid or cur.rowcount`。
    SQLite 的 `lastrowid` **只对 INSERT 有意义**；UPDATE/DELETE 后它仍返回
    该连接上**上一次 INSERT** 的 rowid（陈旧值）→ 调用方拿到非零值，
    误以为「改了 N 行」，实际可能是 0 行的 no-op。
    现按 SQL 首关键字分流：INSERT 返回 lastrowid，其余返回 rowcount。
    """
    conn = get_db()
    with _lock:
        cur = conn.execute(sql, params)
        conn.commit()
        try:
            head = (sql or "").lstrip().split(None, 1)[0].upper()
        except Exception:
            head = ""
        if head == "INSERT":
            return cur.lastrowid or cur.rowcount
        return cur.rowcount


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