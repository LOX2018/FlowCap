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

from fastapi import APIRouter
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
    for sec, values in (body.sections or {}).items():
        if sec not in ac.SECTIONS:
            continue
        before = ac.get_section(sec)
        ac.save_section(sec, values or {})
        after = ac.get_section(sec)
        saved.append(sec)
        changed = [k for k in after if before.get(k) != after.get(k)]
        restart.update(ac.apply_modes_of(sec, changed))
    logger.info(
        f"[settings] 已保存 sections={saved} restart_required={sorted(restart)}")
    return {
        "ok": True,
        "saved_sections": saved,
        "restart_required": sorted(restart),
        "config": ac.get_all(),
    }


class ResetBody(BaseModel):
    sections: list[str] = []


@router.post("/reset")
async def reset_config(body: ResetBody) -> dict:
    done = []
    for sec in body.sections or []:
        if sec in ac.SECTIONS:
            ac.reset_section(sec)
            done.append(sec)
    return {"ok": True, "reset_sections": done, "config": ac.get_all()}
