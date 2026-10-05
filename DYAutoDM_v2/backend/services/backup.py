# -*- coding: utf-8 -*-
"""配置与业务数据备份（导出 / 导入）。

## 为什么（2026-10-02 用户要求）

系统页需要「备份」子板块，支持**自定义选择范围**的导出与导入
（用户明确：不以套餐形式，按范围自由勾选）。

## 范围（scope）清单 —— 唯一真源

| scope | 中文 | 载体 |
|---|---|---|
| `app_config` | 全局配置 | kv `app_config` |
| `config_tags` | 配置标签（含参数与绑定） | kv `app_config::<tag>` + `config_tags`/`config_tag_bind`/`config_tag_bind_section` |
| `ai_agents` | Agent 与账号绑定 | kv `ai_agents`/`ai_account_agent` |
| `model_hub` | 模型中心（提供商/模型/链路） | kv `model_hub` |
| `notify` | 通知渠道与指令 | kv `config`（通知段） |
| `crawl_policies` | 采集策略 | kv `crawl_policies` |
| `live_rooms` | 直播间与房间配置 | kv `live_rooms`/`live_room_configs` |
| `high_value_keywords` | 高价值关键词 | kv `high_value_keywords` |
| `dm_conversations` | 私信会话 | 表 `dm_conversations` |
| `dm_messages` | 私信消息 | 表 `dm_messages` |
| `leads` | 留资线索 | 表 `ai_leads` |
| `knowledge_base` | AI 知识库 | kv `ai_reply_knowledge_base` |
| `sink` | 沉淀池（跨账号去重） | 表 `dm_uid_sink`/`dm_cross_sink` |

## 契约

- 导出 = 纯读；导入 = 按范围**覆盖**（写前对当前值做进程内快照，失败回滚）。
- **不包含**：账号凭证（`.env.enc`）、浏览器 profile、日志 —— 属敏感/体积物，
  绝不进备份包（避免把密钥写进可分享的 json）。
- 导入采用「合并写入」而非删库：只覆盖所选范围内的键/表。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from loguru import logger

import database
from services.kv_store import kv_get, kv_set

FORMAT = "chuanliu-backup"
FORMAT_VERSION = 1

# scope -> (中文名, 描述)
SCOPES: dict[str, tuple[str, str]] = {
    "app_config": ("全局配置", "配置中心所有分区的全局值"),
    "config_tags": ("配置标签", "标签元数据 + 各标签参数 + 账号/板块绑定"),
    "ai_agents": ("Agent 与绑定", "AI Agent 模版与账号绑定"),
    "model_hub": ("模型中心", "提供商 / 模型 / 避障链路 / 消费方绑定"),
    "notify": ("通知与指令", "IM 通知渠道与指令解析配置"),
    "crawl_policies": ("采集策略", "可复用的采集参数"),
    "live_rooms": ("直播间", "房间清单与房间级配置"),
    "high_value_keywords": ("高价值关键词", "关键词权重表"),
    "dm_conversations": ("私信会话", "会话列表（不含消息正文）"),
    "dm_messages": ("私信消息", "聊天记录（体积较大）"),
    "leads": ("留资线索", "AI 获客留资记录"),
    "knowledge_base": ("AI 知识库", "知识库条目"),
    "sink": ("沉淀池", "跨账号去重沉淀（含冷却）"),
}

# kv 型范围：scope -> 该范围涉及的 kv 键
_KV_MAP: dict[str, list[str]] = {
    "app_config": ["app_config"],
    "config_tags": ["config_tags", "config_tag_bind", "config_tag_bind_section"],
    "ai_agents": ["ai_agents", "ai_account_agent"],
    "model_hub": ["model_hub"],
    "notify": ["config"],
    "crawl_policies": ["crawl_policies"],
    "live_rooms": ["live_rooms", "live_room_configs"],
    "high_value_keywords": ["high_value_keywords"],
    "knowledge_base": ["ai_reply_knowledge_base"],
}

# 表型范围：scope -> 表名列表
_TABLE_MAP: dict[str, list[str]] = {
    "dm_conversations": ["dm_conversations"],
    "dm_messages": ["dm_messages"],
    "leads": ["ai_leads"],
    "sink": ["dm_uid_sink", "dm_cross_sink"],
}


def _table_columns(conn, table: str) -> list[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [r[1] for r in rows]


def _table_exists(conn, table: str) -> bool:
    r = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return bool(r)


def scopes_meta() -> list[dict]:
    """范围清单（供前端渲染勾选）。"""
    return [
        {"key": k, "label": v[0], "desc": v[1],
         "kind": "kv" if k in _KV_MAP else "table"}
        for k, v in SCOPES.items()
    ]


def export_scopes(scopes: list[str]) -> dict:
    """按范围导出（纯读）。返回备份包 dict（不落盘）。"""
    want = [s for s in (scopes or []) if s in SCOPES]
    if not want:
        want = list(SCOPES.keys())
    pkg: dict[str, Any] = {
        "format": FORMAT,
        "version": FORMAT_VERSION,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "scopes": want,
        "kv": {},
        "tables": {},
    }
    # 标签 scope：需要把 app_config::<tag_id> 一并带上，否则标签参数丢失
    tag_ids: list[str] = []
    if "config_tags" in want:
        tags = kv_get("config_tags", {}) or {}
        tag_ids = list(tags.keys()) if isinstance(tags, dict) else []

    for s in want:
        for k in _KV_MAP.get(s, []):
            pkg["kv"][k] = kv_get(k, None)
        for tid in tag_ids:
            key = f"app_config::{tid}"
            pkg["kv"][key] = kv_get(key, None)

    conn = database.get_db()
    for s in want:
        for t in _TABLE_MAP.get(s, []):
            if not _table_exists(conn, t):
                continue
            cols = _table_columns(conn, t)
            rows = conn.execute(f"SELECT * FROM {t}").fetchall()
            pkg["tables"][t] = {
                "columns": cols,
                "rows": [list(r) for r in rows],
            }
    return pkg


def _default_export_dir() -> Path:
    """导出目录：**委派 `services/export_paths`**（2026-10-02 分类 SSOT 归位）。

    旧实现在此内联读 `system.export_dir` 并 `try/except` 静默回落 ——
    ① 与 `chatlab_export.default_export_dir` 各自算一套根目录；
    ② 配了个坏路径也静默回落，用户在别处找不到文件且无提示。
    现只保留薄壳，根目录解析与分类全在 `export_paths` 一处。
    """
    from services import export_paths

    return export_paths.dir_for("backup")


def write_export(pkg: dict, filename: str = "") -> dict:
    """把备份包写盘，返回 {ok, path, bytes}。"""
    d = _default_export_dir()
    if not filename:
        filename = "chuanliu-backup-" + time.strftime("%Y%m%d_%H%M%S") + ".json"
    path = d / filename
    data = json.dumps(pkg, ensure_ascii=False, indent=1).encode("utf-8")
    path.write_bytes(data)
    return {"ok": True, "path": str(path), "bytes": len(data)}


def validate(pkg: dict) -> dict:
    """校验备份包结构。返回 {ok, error?, scopes?}。"""
    if not isinstance(pkg, dict):
        return {"ok": False, "error": "备份包不是 JSON 对象"}
    if pkg.get("format") != FORMAT:
        return {"ok": False, "error": f"格式不匹配（期望 {FORMAT}）"}
    scopes = pkg.get("scopes")
    if not isinstance(scopes, list):
        return {"ok": False, "error": "缺少 scopes 字段"}
    known = [s for s in scopes if s in SCOPES]
    if not known:
        return {"ok": False, "error": "备份包内无任何可识别的范围"}
    return {"ok": True, "scopes": known,
            "unknown": [s for s in scopes if s not in SCOPES]}


def import_scopes(pkg: dict, scopes: list[str] | None = None) -> dict:
    """按范围导入（覆盖）。写前快照当前值，失败回滚。

    scopes=None 时导入包内声明的全部范围；否则取「包内 ∩ 指定」。
    """
    v = validate(pkg)
    if not v.get("ok"):
        return {"ok": False, "error": v.get("error")}
    want = [s for s in (scopes or v["scopes"]) if s in v["scopes"]]
    if not want:
        return {"ok": False, "error": "无可导入范围（与备份包不交集）"}

    # 快照（进程内），用于失败回滚
    snap_kv: dict[str, Any] = {}
    snap_tbl: dict[str, list[tuple]] = {}
    kv_data: dict = pkg.get("kv") or {}
    tbl_data: dict = pkg.get("tables") or {}

    applied_kv: list[str] = []
    applied_tbl: list[str] = []
    try:
        for s in want:
            for k in _KV_MAP.get(s, []):
                if k in kv_data:
                    snap_kv[k] = kv_get(k, None)
                    kv_set(k, kv_data[k])
                    applied_kv.append(k)
            # 标签 scope 的参数键（app_config::tg_xxx）
            if s == "config_tags":
                for k, val in kv_data.items():
                    if k.startswith("app_config::"):
                        snap_kv[k] = kv_get(k, None)
                        kv_set(k, val)
                        applied_kv.append(k)

        conn = database.get_db()
        for s in want:
            for t in _TABLE_MAP.get(s, []):
                payload = tbl_data.get(t)
                if not payload:
                    continue
                cols = payload.get("columns") or []
                rows = payload.get("rows") or []
                if not cols or not _table_exists(conn, t):
                    continue
                cur_cols = _table_columns(conn, t)
                use = [c for c in cols if c in cur_cols]
                if not use:
                    continue
                snap_tbl[t] = conn.execute(f"SELECT * FROM {t}").fetchall()
                conn.execute(f"DELETE FROM {t}")
                ph = ",".join("?" * len(use))
                collist = ",".join(use)
                idx = [cols.index(c) for c in use]
                conn.executemany(
                    f"INSERT INTO {t} ({collist}) VALUES ({ph})",
                    [[r[i] for i in idx] for r in rows],
                )
                applied_tbl.append(t)
        conn.commit()
    except Exception as e:  # noqa: BLE001
        # 回滚
        logger.error(f"[BAK-002] 导入失败，回滚: {type(e).__name__}: {e}")
        for k, val in snap_kv.items():
            try:
                kv_set(k, val)
            except Exception:
                pass
        try:
            conn = database.get_db()
            for t, rows in snap_tbl.items():
                cols = _table_columns(conn, t)
                conn.execute(f"DELETE FROM {t}")
                if rows:
                    ph = ",".join("?" * len(cols))
                    conn.executemany(
                        f"INSERT INTO {t} VALUES ({ph})", [list(r) for r in rows]
                    )
            conn.commit()
        except Exception as e2:  # noqa: BLE001
            logger.error(f"[BAK-003] 回滚失败: {e2}")
        return {"ok": False, "error": f"导入失败已回滚: {e}"}

    return {"ok": True, "imported_scopes": want,
            "kv_keys": applied_kv, "tables": applied_tbl}
