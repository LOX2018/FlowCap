# -*- coding: utf-8 -*-
"""配置标签（v0.38.2）—— 纯粹的「指引」，不存参数副本。

设计（用户 2026-09-09 纠正）
----------------------------
> 「标签是指引，具体的参数任由原单位隔离存储」

- 标签**只存元数据**：{id, name, created_at, updated_at} + 账号绑定关系
- **参数值仍由 `services/app_config`（原单位）存储**，按标签 scope 隔离：
    - `app_config.get(section, key)`                  → 全局
    - `app_config.get(section, key, scope="tg_xxx")`  → 该标签的参数
- 标签不持有任何参数副本 → 不存在两处存储不一致的问题

职责边界（与 Agent 并列，不重叠）
--------------------------------
- **Agent** 管「回什么」：回复内容逻辑（prompt / 知识库 / 话术 / 档位）
- **标签** 管「怎么发」：私信发送与风控频率、直播监听、历史捕获策略

  账号 = 1 个 Agent（内容）+ 1 个标签（策略）

受管分区
--------
`send` / `live` / `capture`（`general` 是前端与系统行为，不进标签）

兼容性铁律
----------
未绑定标签 → scope=None → 读全局，**行为与改造前逐字一致**。
"""

from __future__ import annotations

import threading
import uuid
import time
from typing import Optional

import database

_KV_TAGS = "config_tags"        # {tag_id: {name, created_at, updated_at}}
_KV_BIND = "config_tag_bind"    # {account: tag_id}

# 标签能指引的分区
MANAGED_SECTIONS = ("send", "live", "capture")

_lock = threading.RLock()


# ---------------------------------------------------------------------------
# kv
# ---------------------------------------------------------------------------

def _kv_get(key: str, default=None):
    import json
    try:
        conn = database.get_db()
        cur = conn.execute("SELECT value FROM kv_store WHERE key=?", (key,))
        row = cur.fetchone()
        if row is None:
            return default
        return json.loads(row[0])
    except Exception:
        return default


def _kv_set(key: str, value) -> None:
    import json
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
# 标签 CRUD（只管元数据）
# ---------------------------------------------------------------------------

def _new_id() -> str:
    """生成唯一标签 id。

    曾用 int(time.time()*1000) + id(object())：同毫秒内建两个标签会生成
    **相同的 id**（CPython 复用刚释放的临时对象地址）→ 后者覆盖前者。
    实测复现：连续建两个标签后第二个拿不到自己的参数。改用 uuid4。
    """
    return "tg_" + uuid.uuid4().hex[:16]


def list_tags() -> list[dict]:
    """标签列表 + 每个标签实际存了多少参数（从 app_config 按 scope 读）。"""
    from services import app_config as ac

    data = _kv_get(_KV_TAGS, {}) or {}
    out = []
    for tid, t in data.items():
        cnt = 0
        secs = []
        for sec in MANAGED_SECTIONS:
            stored = (ac._load(ac.scope_key(tid)).get(sec) or {})
            if stored:
                secs.append(sec)
                cnt += len(stored)
        out.append({
            "id": tid,
            "name": t.get("name") or tid,
            "sections": secs,
            "field_count": cnt,
            "updated_at": t.get("updated_at", 0),
        })
    return sorted(out, key=lambda x: x.get("updated_at") or 0, reverse=True)


def get_tag(tag_id: str) -> Optional[dict]:
    data = _kv_get(_KV_TAGS, {}) or {}
    t = data.get(tag_id)
    if not t:
        return None
    return {"id": tag_id, "name": t.get("name") or tag_id,
            "created_at": t.get("created_at", 0),
            "updated_at": t.get("updated_at", 0)}


def save_tag(tag_id: str, name: str) -> dict:
    """新建或更新标签元数据（不存参数）。"""
    with _lock:
        data = _kv_get(_KV_TAGS, {}) or {}
        tid = tag_id or _new_id()
        now = time.time()
        data[tid] = {
            "name": name,
            "created_at": data.get(tid, {}).get("created_at") or now,
            "updated_at": now,
        }
        _kv_set(_KV_TAGS, data)
    return get_tag(tid)


def delete_tag(tag_id: str) -> dict:
    """删除标签：解绑账号 + 清掉该 scope 的参数（避免残留孤儿数据）。"""
    from services import app_config as ac

    with _lock:
        data = _kv_get(_KV_TAGS, {}) or {}
        data.pop(tag_id, None)
        _kv_set(_KV_TAGS, data)

        binds = _kv_get(_KV_BIND, {}) or {}
        unbound = [a for a, t in binds.items() if t == tag_id]
        for a in unbound:
            binds.pop(a, None)
        _kv_set(_KV_BIND, binds)

        ac.drop_scope(tag_id)
    return {"ok": True, "unbound_accounts": unbound}


# ---------------------------------------------------------------------------
# 账号 ↔ 标签绑定
# ---------------------------------------------------------------------------

def get_bindings() -> dict:
    return _kv_get(_KV_BIND, {}) or {}


def bind(account: str, tag_id: str) -> dict:
    with _lock:
        binds = _kv_get(_KV_BIND, {}) or {}
        if tag_id:
            binds[account] = tag_id
        else:
            binds.pop(account, None)
        _kv_set(_KV_BIND, binds)
    return {"ok": True, "bindings": binds}


def tag_of(account: str) -> Optional[str]:
    return (_kv_get(_KV_BIND, {}) or {}).get(account)


# ---------------------------------------------------------------------------
# 解析（消费方唯一入口）
# ---------------------------------------------------------------------------

def scope_of(account: str) -> Optional[str]:
    """返回该账号应读取的参数 scope；未绑定标签返回 None（= 读全局）。"""
    return tag_of(account)


def resolve(account: str, section: str, base: dict) -> dict:
    """按账号解析某分区的最终配置（标签值覆盖全局值）。

    未绑定标签 → 原样返回 base（零回归）。
    """
    if section not in MANAGED_SECTIONS:
        return base
    tid = tag_of(account)
    if not tid:
        return base
    from services import app_config as ac

    override = (ac._load(ac.scope_key(tid)).get(section) or {})
    if not override:
        return base
    merged = dict(base)
    merged.update(override)
    return merged


def resolve_all(account: str) -> dict:
    """该账号三个受管分区的最终配置（未绑定则返回空 dict）。"""
    from services import app_config as ac

    out = {}
    tid = tag_of(account)
    if not tid:
        return out
    data = ac._load(ac.scope_key(tid))
    for sec in MANAGED_SECTIONS:
        override = data.get(sec) or {}
        if override:
            merged = dict(ac.get_section(sec))
            merged.update(override)
            out[sec] = merged
    return out
