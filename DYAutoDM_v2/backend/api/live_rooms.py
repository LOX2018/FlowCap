"""直播间登记表（**房间层**）路由 —— kv ``live_rooms``。

## 为什么有这一层（规格 = ADR-003，2026-09-22 用户决策）

2026-09-19 用户曾定调「没有『目标直播间』体系，策略是自足的」。2026-09-22 用户
**部分逆转**该决定：引入**房间登记层**，但坚持**房间与策略分离**——

| 层 | 载体 | 内容 |
|---|---|---|
| **策略**（怎么发） | kv ``live_room_configs``（`api/live_config.py`） | 发送参数，**不含身份字段** |
| **房间**（在哪发） | kv ``live_rooms``（**本模块**） | 身份 + **策略引用** + 脱敏开关 |

⇒ 一份策略可被多个房间**引用**（房间只存 ``strategy_id``），因此**删除策略必须自动
解绑引用它的房间**，禁止悬空引用（见 `api/live_config.delete_strategy`）。

## 记录契约（房间 id 形如 ``lr_<epoch_ms>``）

  { "lr_1758...": {
      "id": "lr_1758...",
      "room_id": "992931212705",
      "live_url": "https://www.douyin.com/follow/live/992931212705?anchor_id=...",
      "name": "自营-工伤咨询",          // 备注（用户填）
      "strategy_id": "lc_1758...",      // 引用 live_room_configs；空 = 未绑定
      "allow_desensitized": false,      // 该房间是否允许「检测到脱敏仍继续监听（仅统计）」
      "updated_at": 1690000000
  }, ... }

## 脱敏布尔语义（ADR-003 §3.3，**一级概念、两级粒度**，勿造第三套）

- 全局「脱敏处理策略」（ADR-002 §3.6，默认「跳过」）= 默认行为；
- 本页 ``allow_desensitized`` = **该房间的覆盖开关**：
  ``false``（默认）⇒ 按全局策略走（默认即「跳过，不监听」）；
  ``true`` ⇒ 即使检测到脱敏也继续监听，但**只做仅统计**（在线数/热度/开播检测），
  **不采真实昵称/uid、不发送私信**（无解密权时本就拿不到身份）。
- 🔴 **诚实标注**：解密权取决于**房间归属**（自营有 / 他人默认脱敏，与凭证无关）。
  故该开关的实际用途是「允许盯一个我没解密权的房间做统计」，**不是**「能拿到它的昵称」。

## 对外端点（挂 /api/live/rooms）

- GET    ``""``             列出全部已登记房间
- GET    ``/{rid}``         取单条
- POST   ``""``             新建 / 更新（按 id upsert；id 为空则生成）
- DELETE ``/{rid}``         删除房间（只删房间记录，**不动策略**）
- POST   ``/migrate``       一次性迁移：存量「房间形」旧策略记录 → live_rooms（带干跑）
"""
from __future__ import annotations

import time

from fastapi import APIRouter
from loguru import logger
from pydantic import BaseModel

from database import get_kv_json, set_kv_json

router = APIRouter()

_KV_KEY = "live_rooms"
_STRATEGY_KV = "live_room_configs"

# 允许写入的房间字段白名单（**含身份字段** —— 这与 live_config._FIELDS 相反，
# 因为身份本来就属于房间层；策略层才排身份）。
_FIELDS = {
    "id", "room_id", "live_url", "name", "strategy_id", "allow_desensitized",
}


def _load_all() -> dict:
    data = get_kv_json(_KV_KEY, {}) or {}
    return data if isinstance(data, dict) else {}


def _save_all(data: dict) -> None:
    set_kv_json(_KV_KEY, data)


def new_room_id() -> str:
    return f"lr_{int(time.time() * 1000)}"


def _normalize_room(rec: dict, key: str) -> dict:
    """读取路径统一出口：补齐缺失键、id 以**键名**为准（键才是真源）。"""
    c = dict(rec or {})
    for junk in ("force_rescan", "forceRescan", "tag_id"):
        c.pop(junk, None)
    c["id"] = str(key)
    c.setdefault("room_id", "")
    c.setdefault("live_url", "")
    c.setdefault("name", "")
    c.setdefault("strategy_id", "")
    c.setdefault("allow_desensitized", False)
    return c


class RoomBody(BaseModel):
    """一条房间登记记录（**身份 + 策略引用 + 脱敏开关**）。"""

    id: str = ""
    room_id: str = ""
    live_url: str = ""
    name: str = ""
    strategy_id: str = ""
    allow_desensitized: bool | None = None


class MigrateBody(BaseModel):
    """迁移入参：``dry_run=True`` 只报告将发生什么，不写库。"""

    dry_run: bool = True
    apply: bool = False     # 兼容别名：apply=True 也等于真写


def _find_room_shaped_configs() -> list[tuple[str, dict]]:
    """找出 ``live_room_configs`` 里的**房间形**旧记录。

    判据（ADR-003 §3.5）：键**非** ``lc_*``，且体内带身份字段
    （``room_id`` 或 ``live_url``）。这些是 v0.43.92 及更早的「一房一配置」形态，
    读取时身份字段被 `_normalize_stored()` pop 掉 ⇒ 信息当前**已 orphan**。
    """
    cfgs = get_kv_json(_STRATEGY_KV, {}) or {}
    if not isinstance(cfgs, dict):
        return []
    out: list[tuple[str, dict]] = []
    for k, v in cfgs.items():
        if not isinstance(v, dict):
            continue
        if str(k).startswith("lc_"):
            continue
        if v.get("room_id") or v.get("live_url"):
            out.append((str(k), dict(v)))
    return out


def migrate_from_room_configs(dry_run: bool = True) -> dict:
    """把「房间形」旧策略记录迁移为 ``live_rooms`` 房间记录（ADR-003 §3.5）。

    迁移映射（**不丢信息**）：
      - ``room_id`` / ``live_url`` / ``name`` → 房间记录的身份与备注；
      - 记录里残留的**发送参数**（max_target/interval/delay/dm_pool/...）若确实存在，
        则另建一条 ``lc_*`` 策略承接，房间的 ``strategy_id`` 指向它 —— 否则给空串；
      - 原房间形键**删除**（身份字段不得继续留在策略层，否则读取路径会再 pop 一次）。

    ``dry_run=True`` 只返回计划，不写任何 kv。可复现且留日志。
    """
    rooms = _load_all()
    cfgs = get_kv_json(_STRATEGY_KV, {}) or {}
    cfgs = dict(cfgs) if isinstance(cfgs, dict) else {}
    found = _find_room_shaped_configs()

    plan: list[dict] = []
    for key, body in found:
        rid = str(body.get("room_id") or key).strip()
        url = str(body.get("live_url") or "").strip()
        # live_url 存的是裸房间号时要归一化成可点链接
        if url and not url.startswith("http") and rid.isdigit():
            url = f"https://live.douyin.com/{rid}"
        name = str(body.get("name") or "").strip()
        # 是否还有策略参数可承接
        has_params = any(
            body.get(k) not in (None, "", [], {})
            for k in ("max_target", "interval", "delay", "dm_pool",
                      "auto_link_mic", "link_mic_mode")
        )
        existing = next(
            (kk for kk, vv in rooms.items()
             if isinstance(vv, dict) and str(vv.get("room_id") or "") == rid),
            None,
        )
        plan.append({
            "source_key": key,
            "room_id": rid,
            "live_url": url,
            "name": name,
            "has_params": has_params,
            "action": "skip-already-migrated" if existing else "migrate",
            "target_room_id_key": existing,
        })

    result = {
        "ok": True,
        "dry_run": bool(dry_run),
        "found": [p["source_key"] for p in plan],
        "plan": plan,
        # 形状稳定：干跑/无内容时也返回空列表，调用方无需猜键是否存在
        "migrated_rooms": [],
        "created_strategies": [],
        "removed_config_keys": [],
    }
    if dry_run or not plan:
        return result

    migrated, created_strategies, removed = [], [], []
    for p in plan:
        if p["action"] != "migrate":
            continue
        key = p["source_key"]
        body = dict(cfgs.get(key) or {})
        sid = ""
        if p["has_params"]:
            sid = f"lc_{int(time.time() * 1000)}"
            strat = {"id": sid, "updated_at": int(time.time())}
            if body.get("name"):
                strat["name"] = str(body.get("name"))
            else:
                strat["name"] = p["room_id"] or sid
            for k in ("max_target", "interval", "delay", "dm_pool",
                      "acct", "auto_link_mic", "link_mic_mode"):
                if body.get(k) is not None:
                    strat[k] = body[k]
            cfgs[sid] = strat
            created_strategies.append(sid)
        new_key = new_room_id()
        rooms[new_key] = {
            "id": new_key,
            "room_id": p["room_id"],
            "live_url": p["live_url"],
            "name": p["name"],
            "strategy_id": sid,
            "allow_desensitized": False,
            "updated_at": int(time.time()),
        }
        cfgs.pop(key, None)
        removed.append(key)
        migrated.append(new_key)

    _save_all(rooms)
    set_kv_json(_STRATEGY_KV, cfgs)
    logger.info(
        f"[live-rooms] 迁移完成：房间 {len(migrated)} 条、新建策略 {len(created_strategies)} 条、"
        f"移除房间形旧键 {removed}"
    )
    result.update({
        "migrated_rooms": migrated,
        "created_strategies": created_strategies,
        "removed_config_keys": removed,
    })
    return result


def unbind_strategy(strategy_id: str) -> int:
    """把引用该策略的房间 ``strategy_id`` 置空（删除策略时调用）。

    返回解绑数量。**禁止**静默留下悬空引用（同族先例：删 Agent 必自动解绑账号）。
    """
    sid = str(strategy_id or "").strip()
    if not sid:
        return 0
    rooms = _load_all()
    n = 0
    for key, rec in rooms.items():
        if isinstance(rec, dict) and str(rec.get("strategy_id") or "") == sid:
            rec["strategy_id"] = ""
            rec["updated_at"] = int(time.time())
            rooms[key] = rec
            n += 1
    if n:
        _save_all(rooms)
        logger.info(f"[live-rooms] 删除策略 {sid} → 自动解绑 {n} 个房间")
    return n


@router.get("")
async def list_rooms() -> dict:
    data = _load_all()
    items = [_normalize_room(v, k) for k, v in data.items()]
    items.sort(key=lambda x: x.get("updated_at", 0), reverse=True)
    return {"ok": True, "items": items}


@router.get("/{rid}")
async def get_room(rid: str) -> dict:
    key = str(rid)
    rec = _load_all().get(key)
    if not rec:
        return {"ok": False, "error": "未找到该直播间"}
    return {"ok": True, "room": _normalize_room(rec, key)}


@router.post("")
async def save_room(body: RoomBody) -> dict:
    """新建 / 更新一条房间登记（按 id upsert；id 为空则生成新 id）。"""
    rid_key = str(body.id or "").strip()
    data = _load_all()
    key = rid_key or new_room_id()
    old = data.get(key) or {}

    room_id = str(body.room_id or "").strip() or str(old.get("room_id") or "")
    if not room_id and not str(body.live_url or "").strip():
        return {"ok": False, "error": "请填写直播间链接或房间号（失焦自动解析）"}

    upd = {
        "id": key,
        "room_id": room_id,
        "live_url": str(body.live_url or "").strip() or str(old.get("live_url") or ""),
        "name": str(body.name or "").strip() or str(old.get("name") or ""),
        "strategy_id": str(body.strategy_id or "").strip() or str(old.get("strategy_id") or ""),
        "allow_desensitized": bool(
            old.get("allow_desensitized") if body.allow_desensitized is None
            else body.allow_desensitized
        ),
        "updated_at": int(time.time()),
    }
    data[key] = upd
    _save_all(data)
    logger.info(f"[live-rooms] 已保存房间 {key} room_id={room_id} strategy={upd['strategy_id']}")
    return {"ok": True, "room": upd}


@router.delete("/{rid}")
async def delete_room(rid: str) -> dict:
    """删除房间记录（**只删房间**，不动任何策略）。"""
    data = _load_all()
    key = str(rid)
    if key not in data:
        return {"ok": False, "error": "未找到该直播间"}
    del data[key]
    _save_all(data)
    logger.info(f"[live-rooms] 已删除房间 {key}")
    return {"ok": True, "deleted": key}


@router.post("/migrate")
async def migrate(body: MigrateBody | None = None) -> dict:
    """一次性迁移存量「房间形」旧策略记录 → ``live_rooms``（默认干跑）。"""
    b = body or MigrateBody()
    return migrate_from_room_configs(dry_run=not (b.apply or not b.dry_run))
