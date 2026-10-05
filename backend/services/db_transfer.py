# -*- coding: utf-8 -*-
"""数据库导入 / 导出 / 迁移（2026-09-17 新增，对照上游的「数据库迁移工具」）。

## 背景（用户原话）

> 「数据库导入/导出/迁移本项目留了出口，但只能支持 csv 改成该功能」

即：我方原先只有 `api/ai.py`（线索 CSV）与 `api/tasks.py`（统计 CSV）两处 CSV 出口，
**没有整库导入/导出/迁移**。本模块补齐。

## 两种格式（各有明确用途）

| format | 实现 | 用途 | 是否含密钥 |
|---|---|---|---|
| `sqlite`（完整） | `sqlite3.Connection.backup()` 物理复制 | 整库备份 / 换机迁移 / 灾难恢复 | **含**（需显式 `include_secrets=True`） |
| `json`（逻辑，默认） | 逐表 `SELECT` → JSON | 数据交换 / 跨版本迁移 / 审计 | **默认脱敏** |

## 安全契约（本项目铁律对齐）

1. **默认脱敏**：`json` 导出会扫描每个 JSON 值的**键名**
   （`*_key` / `api_key` / `token` / `secret` / `password` / `cert` …）并把值替换为空串。
   实测依据：本库 `kv_store` 的 `model_hub` 存着各模型的 `api_key`，
   但**键名本身不含敏感词** → 只按 kv 键名过滤会漏，必须做**内容级**脱敏。
2. **完整模式需显式开关**：`include_secrets=True` 才会原样导出；调用方须自行提示风险。
3. **导入前自动备份**：目标库先复制为 `<db>.bak.<时间戳>`，失败可回滚（与本项目
   `api/ai.py` 既有「先备份 → 写入 → 失败回滚」写法一致）。
4. **不联网**：全程本地文件 I/O。

## 迁移语义

`migrate()` = 导出（逻辑）→ 导入（merge/replace），用于把旧库/旧机器数据并入当前库。
`merge` 以 `INSERT OR REPLACE` 逐表写入（按主键/唯一索引去重）；`replace` 先清空目标表。
"""
from __future__ import annotations

import io
import json
import os
import shutil
import sqlite3
import time
from pathlib import Path

from loguru import logger

# 需要排除/脱敏的键名模式（小写子串匹配）
_SENSITIVE_HINTS = (
    "api_key", "apikey", "secret", "token", "password", "passwd",
    "private_key", "privatekey", "cert", "credential", "authorization",
    "cookie", "session_key", "access_key", "skey",
)


def _is_sensitive_name(name: str) -> bool:
    """键名是否像凭证（用于 JSON 值的内容级脱敏与 kv 键过滤）。"""
    n = str(name or "").lower()
    if not n:
        return False
    # 精确语义优先：以 _key/_token/_secret 结尾，或含敏感子串
    if n.endswith(("_key", "_token", "_secret", "_password", "_cert")):
        return True
    return any(h in n for h in _SENSITIVE_HINTS)


def _scrub(obj):
    """递归脱敏：命中敏感键名 → 值替换为 ""（保留键，便于看出「这里曾有值」）。"""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if _is_sensitive_name(k):
                # 保留「是否曾设置」的信息，但不泄露内容
                out[k] = ""
            else:
                out[k] = _scrub(v)
        return out
    if isinstance(obj, list):
        return [_scrub(x) for x in obj]
    return obj


def _lists_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [r[0] for r in rows]


def export_db(dest: str, *, fmt: str = "json", include_secrets: bool = False,
              db_path: str | None = None) -> dict:
    """导出当前库。

    fmt="json"   → 逻辑导出（默认**脱敏**）；
    fmt="sqlite" → 物理整库复制（**含密钥**，必须显式 include_secrets=True）。

    返回 `{ok, path, format, bytes, tables, redacted}`。
    """
    # 2026-09-18 审查清理：`get_db` 未在此函数使用（本函数刻意用独立只读连接）
    from database import _db_path as _proj_db_path
    src = str(db_path or _proj_db_path())
    if not os.path.exists(src):
        raise FileNotFoundError(f"数据库不存在: {src}")
    dest_p = Path(dest)
    dest_p.parent.mkdir(parents=True, exist_ok=True)

    if fmt == "sqlite":
        if not include_secrets:
            # 物理复制无法脱敏 —— 必须显式确认，避免「以为导出的是干净数据」
            raise ValueError("sqlite 格式为整库物理复制，含账号/密钥，"
                             "须显式 include_secrets=True")
        # ⚠️ 用**独立的只读连接**打开源库做 backup，**不要**用 get_db()：
        #    应用连接可能处于活跃读写事务，`backup()` 会一直等待锁 →
        #    调用线程**永久挂起**（实测：以 get_db() 为源时该用例 20s 超时）。
        #    独立连接无业务事务，backup 立即完成且一致（WAL 安全）。
        src_conn = sqlite3.connect(f"file:{Path(src).as_posix()}?mode=ro", uri=True)
        out = sqlite3.connect(str(dest_p))
        try:
            with out:
                src_conn.backup(out)
        finally:
            try:
                src_conn.close()
            finally:
                out.close()
        n = dest_p.stat().st_size
        logger.info(f"[DB-010] " + f"整库导出(sqlite): {dest_p.name} {n}B")
        return {"ok": True, "path": str(dest_p), "format": "sqlite",
                "bytes": n, "tables": _lists_tables(sqlite3.connect(
                    f"file:{dest_p.as_posix()}?mode=ro", uri=True)),
                "redacted": False}

    if fmt != "json":
        raise ValueError("format 只能是 json 或 sqlite")

    # ⚠️ 必须打开 `src`（可能由 db_path 指定为**另一个库**），
    #    不能图省事用 get_db()：用应用连接会把「迁移另一个库」变成
    #    「导出当前库再导入自己」——静默变成空操作（实测 migrate 用例即此症状）。
    conn = sqlite3.connect(f"file:{Path(src).as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        payload: dict = {
            "_comment": "FlowCap 逻辑导出（默认脱敏；密钥类字段已被清空）",
            "exported_at": int(time.time()),
            "redacted": not include_secrets,
            "tables": {},
        }
        tabs = _lists_tables(conn)
        for t in tabs:
            try:
                rows = conn.execute(f"SELECT * FROM {t}").fetchall()
            except sqlite3.Error as e:
                logger.warning(f"[DB-011] " + f"导出跳过表 {t}: {e}")
                continue
            cols = [d[0] for d in
                    (conn.execute(f"SELECT * FROM {t} LIMIT 0").description or [])]
            items = [dict(zip(cols, r)) for r in rows]
            if not include_secrets:
                for it in items:
                    for k in list(it.keys()):
                        if _is_sensitive_name(k):
                            it[k] = ""
                        else:
                            # 列值是 JSON 字符串（如 extra/kv_store.value）
                            # → 内容级脱敏（键名不含敏感词也会被清）
                            v = it.get(k)
                            if isinstance(v, str) and v[:1] in ("{", "["):
                                try:
                                    it[k] = json.dumps(_scrub(json.loads(v)),
                                                       ensure_ascii=False)
                                except Exception:
                                    pass
            payload["tables"][t] = items
    finally:
        try:
            conn.close()
        except Exception:
            pass

    with io.open(dest_p, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    n = dest_p.stat().st_size
    logger.info(f"[DB-012] " + f"逻辑导出(json): {dest_p.name} {n}B 表 {len(tabs)} 个"
                f" 脱敏={'是' if not include_secrets else '否'}")
    return {"ok": True, "path": str(dest_p), "format": "json", "bytes": n,
            "tables": tabs, "redacted": not include_secrets}


def _backup_file(path: str) -> str:
    ts = time.strftime("%Y%m%d_%H%M%S")
    bak = f"{path}.bak.{ts}"
    shutil.copy2(path, bak)
    return bak


def import_db(src: str, *, mode: str = "merge",
              db_path: str | None = None) -> dict:
    """从导出文件导入。

    mode="merge"   → 逐表 `INSERT OR REPLACE`（保留目标库中未出现在源里的行）；
    mode="replace" → 先 `DELETE FROM 表` 再写入（**目标表数据被清空**，有备份兜底）。

    仅支持 **json** 导出（sqlite 完整备份请直接替换文件，不经此函数）。

    返回 `{ok, mode, tables, rows, backup}`。
    """
    from database import _db_path as _proj_db_path, get_db
    p = Path(src)
    if not p.exists():
        raise FileNotFoundError(f"导出文件不存在: {p}")
    with io.open(p, encoding="utf-8") as _f:
        data = json.loads(_f.read())
    if not isinstance(data, dict) or not isinstance(data.get("tables"), dict):
        raise ValueError("不是本模块导出的 JSON（缺 tables）")
    if mode not in ("merge", "replace"):
        raise ValueError("mode 只能是 merge 或 replace")

    target = str(db_path or _proj_db_path())
    backup = _backup_file(target) if os.path.exists(target) else ""
    conn = get_db()
    done_rows = 0
    done_tabs: list[str] = []
    try:
        for t, items in data["tables"].items():
            if not items:
                continue
            # 目标表不存在则跳过（跨版本迁移时源表可能已废弃）
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (t,)).fetchone()
            if not exists:
                logger.info(f"[DB-013] " + f"导入跳过（目标无此表）: {t}")
                continue
            cols = [d[1] for d in conn.execute(f"PRAGMA table_info({t})").fetchall()]
            src_cols = [c for c in cols if c in (items[0] or {})]
            if not src_cols:
                continue
            if mode == "replace":
                conn.execute(f"DELETE FROM {t}")
            ph = ",".join("?" for _ in src_cols)
            sql = (f"INSERT OR REPLACE INTO {t}({','.join(src_cols)}) "
                   f"VALUES({ph})")
            for it in items:
                try:
                    conn.execute(sql, tuple(it.get(c) for c in src_cols))
                    done_rows += 1
                except sqlite3.Error as e:
                    logger.debug(f"[DB-014] 导入单行失败 {t}: {e}")
            done_tabs.append(t)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    logger.info(f"[DB-015] " + f"导入完成({mode}): 表 {len(done_tabs)} 行 {done_rows} "
                f"备份={os.path.basename(backup) if backup else '无'}")
    return {"ok": True, "mode": mode, "tables": done_tabs, "rows": done_rows,
            "backup": backup, "redacted_source": bool(data.get("redacted"))}


def migrate(src: str, *, mode: str = "merge",
            db_path: str | None = None) -> dict:
    """迁移：把 `src`（导出文件或**另一个库文件**）并入当前库。

    · `src` 是 .json → 直接 `import_db`
    · `src` 是 .db   → 先临时导出为 json（脱敏关闭，迁移要保真）再导入
    """
    p = Path(src)
    if not p.exists():
        raise FileNotFoundError(f"源不存在: {p}")
    if p.suffix.lower() == ".json":
        return import_db(str(p), mode=mode, db_path=db_path)

    # 另一个 sqlite 库：临时导出（保真，不做脱敏 —— 迁移目的是搬迁完整数据）
    tmp = p.with_suffix(f".migrate.{int(time.time())}.json")
    export_db(str(tmp), fmt="json", include_secrets=True, db_path=str(p))
    try:
        return import_db(str(tmp), mode=mode, db_path=db_path)
    finally:
        try:
            tmp.unlink()
        except Exception:
            pass
