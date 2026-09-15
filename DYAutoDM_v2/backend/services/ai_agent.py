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
from services.kv_store import kv_get as _kv_get, kv_set as _kv_set

_KV_AGENTS = "ai_agents"          # {agent_id: {name, config, created_at, updated_at}}
_KV_BINDINGS = "ai_account_agent"  # {account: agent_id}

_lock = threading.RLock()


# ---------------------------------------------------------------------------
# kv 读写（与 ai_reply 同款，走 database.kv）
# ---------------------------------------------------------------------------





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
    """返回全部 Agent（不含 config 全量字段，列表页用）。kind 可选过滤。"""
    data = _kv_get(_KV_AGENTS, {}) or {}
    out = []
    for aid, a in data.items():
        cfg = a.get("config") or {}
        out.append({
            "id": aid,
            "name": a.get("name") or aid,
            "kind": a.get("kind") or "dm",
            "scopes": cfg.get("scopes") or ["dm"],
            "permissions": cfg.get("permissions") or {},
            "model": cfg.get("model", ""),
            "strict_level": cfg.get("strict_level", ""),
            "merchant_name": cfg.get("merchant_name", ""),
            "enabled": bool(cfg.get("enabled", False)),
            "kb_count": len(cfg.get("knowledge_base") or []),
            "updated_at": a.get("updated_at", 0),
        })
    return sorted(out, key=lambda x: x.get("updated_at") or 0, reverse=True)


# Agent 作用域（私信 Agent 用）：AI 智能回复注入哪些模块
AGENT_SCOPES = ("dm", "live", "crawl")
SCOPE_LABELS = {
    "dm": "私信中心",
    "live": "直播监听",
    "crawl": "视频采集",
}


def ensure_default_dispatch_agent() -> dict:
    """预置默认调度 Agent（IM Bot 指令解析用）。幂等：存在即返回。

    调度 Agent 只有一个（固定 id），管理 IM Bot 能对项目做什么：
    permissions 控制各能力的开/关（高危项默认关）。
    """
    with _lock:
        data = _kv_get(_KV_AGENTS, {}) or {}
        if _DISPATCH_ID in data:
            return _KV_AGENTS, data[_DISPATCH_ID]  # 已存在
        data[_DISPATCH_ID] = {
            "name": "调度 Agent（IM Bot 默认）",
            "kind": "dispatch",
            "config": {
                "enabled": True,
                "kind": "dispatch",
                "scopes": [],  # 调度 Agent 不适用模块作用域
                # 指令解析 prompt（cmd_parser 用；空=用内置默认）
                "system_prompt": "",
                # 能力权限（IM Bot 能对项目做什么；用户拍板方案）
                "permissions": {
                    "query_status": True,       # 查询任务进度与收获
                    "create_crawl_task": True,  # 创建视频采集任务（需确认）
                    "create_live_task": True,   # 创建/启动直播监听任务（需确认）
                    "stop_task": True,          # 停止任务
                    "update_kb": True,          # 更新 Agent 知识库（需确认）
                    "create_agent": False,      # 新建私信 Agent（高危，默认关）
                    "recapture": False,         # 凭证重捕获（高危，默认关）
                    # 管理提供商不开放（纯通知即可，用户拍板）
                },
            },
            "created_at": time.time(),
            "updated_at": time.time(),
        }
        _kv_set(_KV_AGENTS, data)
        return _KV_AGENTS, data[_DISPATCH_ID]


_DISPATCH_ID = "ag_dispatch_default"


def get_dispatch_agent() -> Optional[dict]:
    """取默认调度 Agent（不存在则自动预置）。"""
    ensure_default_dispatch_agent()
    return get_agent(_DISPATCH_ID)


def save_dispatch_agent(cfg_update: dict) -> dict:
    """更新默认调度 Agent（只允许改 system_prompt/permissions/enabled）。"""
    ensure_default_dispatch_agent()
    with _lock:
        data = _kv_get(_KV_AGENTS, {}) or {}
        a = data[_DISPATCH_ID]
        cfg = a.get("config") or {}
        for k in ("system_prompt", "permissions", "enabled"):
            if k in cfg_update:
                cfg[k] = cfg_update[k]
        a["config"] = cfg
        a["updated_at"] = time.time()
        _kv_set(_KV_AGENTS, data)
        return get_agent(_DISPATCH_ID)


def get_agent(agent_id: str) -> Optional[dict]:
    data = _kv_get(_KV_AGENTS, {}) or {}
    a = data.get(agent_id)
    if not a:
        return None
    return {"id": agent_id,
            "name": a.get("name") or agent_id,
            "kind": a.get("kind") or "dm",
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

def resolve_config_for(agent_id: str, base_config: dict) -> dict:
    """按 agent_id 直接解析配置（AI 页编辑指定 Agent 用）。

    与 resolve_config(account, base) 的区别：不需要账号，直接给 agent_id。
    Agent 不存在返回 base。
    """
    a = get_agent(agent_id)
    if not a:
        return base_config
    merged = dict(base_config)
    merged.update(a.get("config") or {})
    return merged


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
