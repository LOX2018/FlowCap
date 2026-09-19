"""目标直播间管理路由（kv ``live_target_rooms``）。

## 设计契约（2026-09-19 用户定调）

用户原话：「直播监听的配置管理修改只能对现有的配置反复覆盖，没有实现多配置标签的
功能。目标直播间可以管理，但别放到配置管理中，单独加一个目标直播间管理，在该页面
中选择是否绑定配置。」

职责**严格分离**（本模块只管前三项，第四项属于配置标签）：

| 关注点 | 归属 | 存储 |
|---|---|---|
| 监听哪个直播间（room_id） | **本模块** | kv ``live_target_rooms`` |
| 备注名 / 是否启用 | **本模块** | 同上 |
| 绑定哪条配置标签（tag_id） | **本模块**（只存**引用**） | 同上 |
| 参数（上限/间隔/抖动/词库/连麦/强制重扫/账号） | ``api/live_config.py`` | kv ``live_room_configs`` |

## 三条铁律

1. **本模块不存任何参数**，只有 ``tag_id`` 引用 —— 与 `config_tag`（标签只存元数据）
   同一设计原则，杜绝两处存储不一致。
2. **解绑 ≠ 删除**：``tag_id=None`` 只清引用，配置标签本身必须保留。
   （``DELETE /api/live/config-tags/{id}`` 才删标签，并由本模块负责反向解绑。）
3. **绑定目标必须存在**：``tag_id`` 非空时校验 `live_config.get_tag()`，防止悬空引用。

## 对外端点（``/api/live/target-rooms``）

- GET    ``""``              列出全部目标直播间
- GET    ``/{room_id}``      取单个
- POST   ``""``              新建/更新（按 room_id upsert）
- DELETE ``/{room_id}``      移除目标直播间（不删配置标签）
- GET    ``/{room_id}/resolve``  解析出该直播间生效的完整配置（目标 + 标签参数）
"""
import time

from fastapi import APIRouter
from loguru import logger
from pydantic import BaseModel

from database import get_kv_json, set_kv_json

router = APIRouter()

_KV_KEY = "live_target_rooms"


def _load_all() -> dict:
    data = get_kv_json(_KV_KEY, {}) or {}
    return data if isinstance(data, dict) else {}


def _save_all(data: dict) -> None:
    set_kv_json(_KV_KEY, data)


def _norm_room_id(raw: str) -> str:
    """从输入中提取纯数字 room_id（容忍粘贴整个 URL）。"""
    import re

    s = str(raw or "").strip()
    if not s:
        return ""
    m = re.search(r"live\.douyin\.com/(\d+)", s)
    if m:
        return m.group(1)
    m = re.match(r"^(\d+)$", s)
    return m.group(1) if m else ""


class TargetRoomBody(BaseModel):
    room_id: str
    name: str | None = None
    #: 绑定的配置标签 id；None / "" = 不绑定配置（解绑）
    tag_id: str | None = None
    enabled: bool | None = None


@router.get("")
async def list_target_rooms() -> dict:
    data = _load_all()
    items = sorted(data.values(), key=lambda x: x.get("updated_at", 0), reverse=True)
    return {"ok": True, "items": items}


@router.get("/{room_id}")
async def get_target_room(room_id: str) -> dict:
    cfg = _load_all().get(str(room_id))
    if not cfg:
        return {"ok": False, "error": "未找到该目标直播间"}
    return {"ok": True, "target": cfg}


@router.post("")
async def save_target_room(body: TargetRoomBody) -> dict:
    """新建 / 更新目标直播间（按 room_id upsert）。**只写身份与绑定引用。**"""
    rid = _norm_room_id(body.room_id)
    if not rid:
        return {"ok": False, "error": "直播间号无效（需纯数字或直播间 URL）"}

    tag_id = str(body.tag_id or "").strip()
    if tag_id:
        # 铁律 3：绑定目标必须存在，否则拒绝（防悬空引用）
        from api import live_config

        if not live_config.get_tag(tag_id):
            return {"ok": False, "error": f"配置标签 {tag_id} 不存在，请先在「直播配置标签」中创建"}

    data = _load_all()
    old = data.get(rid) or {}
    upd = {
        "room_id": rid,
        "updated_at": int(time.time()),
        "name": (body.name or "").strip() or old.get("name") or rid,
        "tag_id": tag_id or None,
        "enabled": bool(body.enabled) if body.enabled is not None else bool(old.get("enabled", True)),
    }
    data[rid] = upd
    _save_all(data)
    logger.info(f"[target-room] 已保存目标直播间 {rid} tag_id={upd.get('tag_id')} enabled={upd.get('enabled')}")
    return {"ok": True, "target": upd}


@router.delete("/{room_id}")
async def delete_target_room(room_id: str) -> dict:
    """移除目标直播间。**绝不删除配置标签**（用户铁律：解绑 ≠ 删除）。"""
    data = _load_all()
    rid = str(room_id)
    if rid not in data:
        return {"ok": False, "error": "未找到该目标直播间"}
    del data[rid]
    _save_all(data)
    logger.info(f"[target-room] 已移除目标直播间 {rid}（配置标签保留）")
    return {"ok": True, "removed": rid}


@router.get("/{room_id}/resolve")
async def resolve_target_room(room_id: str) -> dict:
    """解析该直播间生效的完整配置（目标身份 + 绑定标签的参数）。"""
    rid = str(room_id)
    tgt = _load_all().get(rid)
    if not tgt:
        return {"ok": False, "error": "未找到该目标直播间"}
    out = resolve(rid)
    return {"ok": True, **out}


def unbind_tag(tag_id: str) -> list[str]:
    """反向解绑：把所有引用了该标签的目标直播间置为「不绑定配置」。

    ``api/live_config.delete_room_config`` 删除标签时调用 —— 目标直播间记录
    **保留**，只是不再引用已删除的标签（避免悬空引用）。
    """
    if not tag_id:
        return []
    data = _load_all()
    hit: list[str] = []
    for rid, t in data.items():
        if str(t.get("tag_id") or "") == str(tag_id):
            t["tag_id"] = None
            t["updated_at"] = int(time.time())
            hit.append(rid)
    if hit:
        _save_all(data)
    return hit


def resolve(room_id: str) -> dict:
    """返回 {target, tag, params}：该目标直播间生效的配置。

    ``params`` 为标签参数（未绑定 / 标签已删 → 空 dict，表示「保持任务原有参数」）。
    """
    tgt = _load_all().get(str(room_id)) or {}
    tag_id = str(tgt.get("tag_id") or "")
    tag = None
    if tag_id:
        from api import live_config

        tag = live_config.get_tag(tag_id)
    return {"target": tgt, "tag": tag, "params": dict(tag or {})}
