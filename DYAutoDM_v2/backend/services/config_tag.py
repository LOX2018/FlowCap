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
from services.kv_store import kv_get as _kv_get, kv_set as _kv_set

_KV_TAGS = "config_tags"        # {tag_id: {name, created_at, updated_at}}
_KV_BIND = "config_tag_bind"    # {account: tag_id}
_KV_BIND_SECTION = "config_tag_bind_section"  # {account: {section: tag_id}} —— 板块级绑定（2026-09-24 B-4）

# 标签能指引的分区
MANAGED_SECTIONS = ("send", "live", "capture")

_lock = threading.RLock()


# ---------------------------------------------------------------------------
# kv
# ---------------------------------------------------------------------------

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


def save_tag(tag_id: str, name: str, biz_line: str = "",
             scene: str = "", sections: list | None = None) -> dict:
    """新建或更新标签元数据（不存参数）。

    2026-09-24（B-4）：新增**业务维度**字段 —— 旧标签只有 {id,name}，
    建出来的全是「高频/保守」这类风控档位，不是业务标签。
      - biz_line : 行业线（如 工伤 / 车险 / 电商）
      - scene    : 场景（如 四川 / 华东 / 直播间引流）
      - sections : 该标签**适用板块**（send/live/capture 子集；
                   空 = 沿用全部 MANAGED_SECTIONS —— 旧数据零回归）
    三个字段都可缺省，未传时行为与改造前逐字一致。
    """
    with _lock:
        data = _kv_get(_KV_TAGS, {}) or {}
        tid = tag_id or _new_id()
        now = time.time()
        prev = data.get(tid, {}) or {}
        data[tid] = {
            "name": name,
            "biz_line": biz_line if biz_line is not None else prev.get("biz_line", ""),
            "scene": scene if scene is not None else prev.get("scene", ""),
            "sections": (list(sections) if sections is not None
                          else prev.get("sections") or list(MANAGED_SECTIONS)),
            "created_at": prev.get("created_at") or now,
            "updated_at": now,
        }
        _kv_set(_KV_TAGS, data)
    return get_tag(tid)


def tag_sections(tag_id: str) -> tuple:
    """该标签适用的板块；未配置或旧数据 → 全部 MANAGED_SECTIONS（零回归）。"""
    t = get_tag(tag_id) or {}
    secs = t.get("sections")
    if not secs:
        return tuple(MANAGED_SECTIONS)
    out = tuple(s for s in secs if s in MANAGED_SECTIONS)
    return out or tuple(MANAGED_SECTIONS)


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

def scope_of(account: str, section: str = "", room_tag: str = "") -> Optional[str]:
    """返回该账号应读取的参数 scope；未绑定标签返回 None（= 读全局）。

    ## 优先级链（高 → 低）

      1. **房间级** `room_tag`（ADR-018 F1-D1，2026-09-27 新增）
      2. **板块级** `<account>.<section>`（2026-09-24 B-4）
      3. **整账号级** `tag_of(account)`
      4. 都没有 → `None`（调用方读全局）

    ## 参数语义

      - `section`：传了就参与第 2 级；不传则跳过（与改造前逐字一致，零回归）
      - `room_tag`：传了就参与第 1 级。**传空串 = 该房间未绑标签** ⇒ 自然回落。
        调用方（如直播链路）应先从 `live_rooms[room].tag_id` 取出再传入；
        本函数**不查 live_rooms**（避免 services 层反向依赖 api 层）。

    ## 为什么每级都要 `get_tag(...)["name"]` 校验

    悬空引用（标签已被删）若直接返回 id，调用方会拿到一个取不到参数的
    scope，表现为「静默失效」—— 宁愿回落，也不要给出一个空 scope。
    """
    # ① 房间级（最高优先）
    rt = str(room_tag or "").strip()
    if rt and (get_tag(rt) or {}).get("name"):
        return rt
    # ② 板块级
    if section:
        per_sec = _kv_get(_KV_BIND_SECTION, {}) or {}
        sec_map = per_sec.get(account) or {}
        tid = sec_map.get(section)
        if tid and (get_tag(tid) or {}).get("name"):
            return tid
    # ③ 整账号级
    return tag_of(account)


def bind_section(account: str, section: str, tag_id: str) -> dict:
    """绑定某账号的**单个板块**到指定标签（tag_id 为空 = 解绑该板块）。"""
    if section not in MANAGED_SECTIONS:
        return {"ok": False, "error": f"板块 {section} 不受标签管理"}
    with _lock:
        per_sec = _kv_get(_KV_BIND_SECTION, {}) or {}
        sec_map = per_sec.get(account) or {}
        if tag_id:
            sec_map[section] = tag_id
        else:
            sec_map.pop(section, None)
        if sec_map:
            per_sec[account] = sec_map
        else:
            per_sec.pop(account, None)
        _kv_set(_KV_BIND_SECTION, per_sec)
    return {"ok": True, "bindings_section": per_sec}


def bindings_section() -> dict:
    """全部板块级绑定 {account: {section: tag_id}}。"""
    return _kv_get(_KV_BIND_SECTION, {}) or {}


def effective_bindings(account: str) -> dict:
    """该账号的**最终生效**绑定（供 UI 聚合展示）：板块优先，回落整账号。"""
    whole = tag_of(account) or ""
    sec_map = (_kv_get(_KV_BIND_SECTION, {}) or {}).get(account) or {}
    out = {}
    for s_ in MANAGED_SECTIONS:
        tid = sec_map.get(s_) or whole
        out[s_] = tid if tid and (get_tag(tid) or {}).get("name") else ""
    return {"account": account, "whole": whole, "sections": out}


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
