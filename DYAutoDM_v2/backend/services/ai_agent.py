# -*- coding: utf-8 -*-
"""AI Agent 模版存储 + 账号绑定（v0.38.0）。

背景
----
v0.35.1 起 AI 配置是**全局单份**（kv `ai_reply_config`），但消息处理是
按账号维度的 —— 多账号共用一份配置必然紊乱（A 账号的商家名/知识库串到 B）。

方案（用户 2026-09-09 拍板）
----------------------------
**Agent 模版 + 账号绑定**：
  - 设置页定义若干**命名 Agent**（含模型/档位/prompt/知识库/黑名单/兜底话术）
  - 每个账号绑定**一个 Agent**（在设置页做关联，不在 AI 页做，避免两处配置分裂）
  - 改 Agent 一次 → 所有绑定它的账号同步生效

  ⚠️ 知识库 / 黑名单 / 兜底话术**全部跟随 Agent**，账号不单独持有。

兼容性铁律
----------
`ai_reply.get_config()` 仍返回全局配置，**不破坏既有链路**。
新增的 Agent 解析走本模块的 `resolve_config(account)`：
  账号绑定了 Agent → 返回该 Agent 的配置
  未绑定或 Agent 不存在 → 回落全局配置（行为与改造前一致）
"""

from __future__ import annotations

import json
import threading
import uuid
import time
from typing import Optional

import database

_KV_AGENTS = "ai_agents"          # {agent_id: {name, config, created_at, updated_at}}
_KV_BINDINGS = "ai_account_agent"  # {account: agent_id}

_lock = threading.RLock()


# ---------------------------------------------------------------------------
# kv 读写（与 ai_reply 同款，走 database.kv）
# ---------------------------------------------------------------------------

def _kv_get(key: str, default=None):
    try:
        conn = database.get_db()
        cur = conn.execute(
            "SELECT value FROM kv_store WHERE key=?", (key,))
        row = cur.fetchone()
        if row is None:
            return default
        return json.loads(row[0])
    except Exception:
        return default


def _kv_set(key: str, value) -> None:
    try:
        conn = database.get_db()
        conn.execute(
            "INSERT INTO kv_store(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value, ensure_ascii=False)),
        )
        conn.commit()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Agent CRUD
# ---------------------------------------------------------------------------

def _new_id() -> str:
    """生成唯一 Agent id。

    曾用 int(time.time()*1000) + id(object())：同毫秒内建两个 Agent 会
    **生成相同 id**（CPython 复用临时对象地址）→ 后者覆盖前者。
    与 config_tag._new_id 同款缺陷，改用 uuid4。
    """
    return "ag_" + uuid.uuid4().hex[:16]


def list_agents() -> list[dict]:
    """返回全部 Agent（不含 config 全量字段，列表页用）。"""
    data = _kv_get(_KV_AGENTS, {}) or {}
    out = []
    for aid, a in data.items():
        cfg = a.get("config") or {}
        out.append({
            "id": aid,
            "name": a.get("name") or aid,
            "model": cfg.get("model", ""),
            "strict_level": cfg.get("strict_level", ""),
            "merchant_name": cfg.get("merchant_name", ""),
            "enabled": bool(cfg.get("enabled", False)),
            "kb_count": len(cfg.get("knowledge_base") or []),
            "updated_at": a.get("updated_at", 0),
        })
    return sorted(out, key=lambda x: x.get("updated_at") or 0, reverse=True)


def get_agent(agent_id: str) -> Optional[dict]:
    data = _kv_get(_KV_AGENTS, {}) or {}
    a = data.get(agent_id)
    if not a:
        return None
    return {"id": agent_id,
            "name": a.get("name") or agent_id,
            "config": a.get("config") or {},
            "updated_at": a.get("updated_at", 0)}


def save_agent(agent_id: str, name: str, config: dict) -> dict:
    """新建或更新 Agent。agent_id 为空则新建。"""
    with _lock:
        data = _kv_get(_KV_AGENTS, {}) or {}
        aid = agent_id or _new_id()
        data[aid] = {
            "name": name,
            "config": config,
            "created_at": data.get(aid, {}).get("created_at") or time.time(),
            "updated_at": time.time(),
        }
        _kv_set(_KV_AGENTS, data)
    return get_agent(aid)


def delete_agent(agent_id: str) -> dict:
    """删除 Agent，并解绑所有引用它的账号（避免悬空绑定）。"""
    with _lock:
        data = _kv_get(_KV_AGENTS, {}) or {}
        data.pop(agent_id, None)
        _kv_set(_KV_AGENTS, data)

        binds = _kv_get(_KV_BINDINGS, {}) or {}
        unbound = [a for a, aid in binds.items() if aid == agent_id]
        for a in unbound:
            binds.pop(a, None)
        _kv_set(_KV_BINDINGS, binds)
    return {"ok": True, "unbound_accounts": unbound}


# ---------------------------------------------------------------------------
# 账号 ↔ Agent 绑定
# ---------------------------------------------------------------------------

def get_bindings() -> dict:
    return _kv_get(_KV_BINDINGS, {}) or {}


def bind(account: str, agent_id: str) -> dict:
    """账号绑定 Agent。agent_id 为空串 = 解绑。"""
    with _lock:
        binds = _kv_get(_KV_BINDINGS, {}) or {}
        if agent_id:
            binds[account] = agent_id
        else:
            binds.pop(account, None)
        _kv_set(_KV_BINDINGS, binds)
    return {"ok": True, "bindings": binds}


def agent_of(account: str) -> Optional[str]:
    return (_kv_get(_KV_BINDINGS, {}) or {}).get(account)


# ---------------------------------------------------------------------------
# 配置解析（消费方唯一入口）
# ---------------------------------------------------------------------------

def resolve_config(account: str, base_config: dict) -> dict:
    """按账号解析最终生效的 AI 配置。

    base_config 为全局配置（ai_reply.get_config() 的结果）。
      - 账号绑定了 Agent → 用 Agent 的 config 覆盖全局（逐键覆盖，未配置的键保留全局）
      - 未绑定 / Agent 不存在 → 原样返回全局配置（零回归）
    """
    aid = agent_of(account)
    if not aid:
        return base_config
    a = get_agent(aid)
    if not a:
        return base_config
    merged = dict(base_config)
    merged.update(a.get("config") or {})
    return merged


def resolve_knowledge(account: str, base_kb: list) -> list:
    """知识库按 Agent 隔离：绑定了 Agent 就用 Agent 的知识库。"""
    aid = agent_of(account)
    if not aid:
        return base_kb
    a = get_agent(aid)
    if not a:
        return base_kb
    kb = (a.get("config") or {}).get("knowledge_base")
    return kb if kb is not None else base_kb


def resolve_blacklist(account: str, base_bl: list) -> list:
    """黑名单按 Agent 隔离（同知识库）。"""
    aid = agent_of(account)
    if not aid:
        return base_bl
    a = get_agent(aid)
    if not a:
        return base_bl
    bl = (a.get("config") or {}).get("blacklist")
    return bl if bl is not None else base_bl
