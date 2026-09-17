"""设置路由（统一配置网关）。

取代原版 WebBridge.saveConfig / _write_config_file，并**取代此前的空壳实现**
（旧版 GET 恒返回 {"config":{}}、POST 只打日志「持久化 TODO」）。

对外端点
--------
- `GET  /api/settings`        全量配置 + schema
- `POST /api/settings`        按 section 保存，返回需重启目标
- `GET  /api/settings/schema` 仅下发表单元数据（前端首屏用）
- `POST /api/settings/reset`  清空某 section 回默认

配置持久化走 `services/app_config`（SQLite kv_store["app_config"]），
**不写回任何 .py 源码**。
"""

from typing import Any

from fastapi import APIRouter, HTTPException
from loguru import logger
from pydantic import BaseModel

from services import app_config as ac

router = APIRouter()


class SaveBody(BaseModel):
    """按 section 保存。未知 section / 未知字段 / 越界值一律丢弃。"""

    sections: dict[str, dict[str, Any]] = {}


@router.get("")
async def get_config() -> dict:
    return {"ok": True, "config": ac.get_all(), "schema": ac.schema()}


@router.get("/schema")
async def get_schema() -> dict:
    return {"ok": True, "schema": ac.schema()}


@router.post("")
async def save_config(body: SaveBody) -> dict:
    """按 section 保存，返回本次改动需要重启的目标。

    restart_required 元素语义：
      - "daemon"  → 需重启 recv_daemon / browser_daemon 才生效
      - "backend" → 需重启 backend 才生效
    """
    saved: list[str] = []
    restart: set[str] = set()
    try:
        for sec, values in (body.sections or {}).items():
            if sec not in ac.SECTIONS:
                continue
            before = ac.get_section(sec)
            ac.save_section(sec, values or {})
            after = ac.get_section(sec)
            saved.append(sec)
            changed = [k for k in after if before.get(k) != after.get(k)]
            restart.update(ac.apply_modes_of(sec, changed))
    except RuntimeError as e:
        # 2026-09-17：save_section 落盘失败会抛 RuntimeError（见 app_config._save）。
        # 转成明确的 HTTP 错误，避免"接口看似成功但配置未持久化"。
        logger.error(f"[settings] 保存失败: {e}")
        raise HTTPException(500, f"配置保存失败：{e}") from e
    logger.info(
        f"[settings] 已保存 sections={saved} restart_required={sorted(restart)}")
    return {
        "ok": True,
        "saved_sections": saved,
        "restart_required": sorted(restart),
        "config": ac.get_all(),
    }


class TagMetaBody(BaseModel):
    """标签元数据（不存参数值）。"""
    id: str = ""
    name: str = ""


@router.get("/tags")
async def list_tags():
    from services import config_tag

    return {"ok": True, "tags": config_tag.list_tags(),
            "bindings": config_tag.get_bindings()}


@router.post("/tags")
async def save_tag(body: TagMetaBody):
    from services import config_tag

    t = config_tag.save_tag(body.id, body.name or "未命名标签")
    return {"ok": True, "tag": t, "tags": config_tag.list_tags()}


@router.delete("/tags/{tag_id}")
async def delete_tag(tag_id: str):
    from services import config_tag

    if not config_tag.get_tag(tag_id):
        raise HTTPException(404, "标签不存在")
    return config_tag.delete_tag(tag_id)


class TagBindBody(BaseModel):
    account: str
    tag_id: str = ""   # 空 = 解绑


@router.post("/tags/bind")
async def bind_tag(body: TagBindBody):
    from services import config_tag

    if body.tag_id and not config_tag.get_tag(body.tag_id):
        raise HTTPException(404, "标签不存在")
    return config_tag.bind(body.account, body.tag_id)


@router.get("/tags/bind")
async def get_tag_bindings():
    from services import config_tag

    return {"ok": True, "bindings": config_tag.get_bindings(),
            "tags": config_tag.list_tags()}


class SaveScopedBody(BaseModel):
    """按 section 保存到指定 scope（标签）。"""
    sections: dict[str, dict[str, Any]] = {}
    scope: str = ""


@router.post("/scoped")
async def save_scoped(body: SaveScopedBody):
    """保存某标签的参数。scope 为空则等同全局保存。"""
    from services import config_tag

    scope = body.scope or None
    if scope and not config_tag.get_tag(scope):
        raise HTTPException(404, "标签不存在")
    saved = []
    try:
        for sec, values in (body.sections or {}).items():
            if sec not in ac.SECTIONS:
                continue
            ac.save_section(sec, values or {}, scope=scope)
            saved.append(sec)
    except RuntimeError as e:
        # 2026-09-17：同 save_config，落盘失败显式报错。
        logger.error(f"[settings] 标签保存失败: {e}")
        raise HTTPException(500, f"标签配置保存失败：{e}") from e
    return {"ok": True, "saved_sections": saved,
            "config": {s: ac.get_section(s, scope=scope) for s in saved},
            "tags": config_tag.list_tags()}


@router.get("/scoped/{tag_id}")
async def get_scoped(tag_id: str):
    """读某标签的参数（不含全局回落，纯看标签存了什么）。"""
    return {"ok": True, "config": ac._load(ac.scope_key(tag_id))}


class ResetBody(BaseModel):
    sections: list[str] = []


@router.post("/reset")
async def reset_config(body: ResetBody) -> dict:
    done = []
    try:
        for sec in body.sections or []:
            if sec in ac.SECTIONS:
                ac.reset_section(sec)
                done.append(sec)
    except RuntimeError as e:
        # 2026-09-17：同 save_config，落盘失败显式报错。
        logger.error(f"[settings] 重置失败: {e}")
        raise HTTPException(500, f"配置重置失败：{e}") from e
    return {"ok": True, "reset_sections": done, "config": ac.get_all()}
