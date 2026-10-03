# coding=utf-8
"""批量直播监听 API 路由。

## 风控红线（继承项目铁律）

1. **默认关闭（fail-closed）**：总开关来自**配置中心**
   `live_orchestration.batch_enabled`（默认 `False`），未开启时所有写操作拒绝。
   env `DY_LIVE_BATCH_ENABLED` 降级为回退，保证旧部署不破。
2. **fail-closed**：任何异常不静默放行，明确返回失败原因。
3. **显式配置门**：不显式开启 = 零副作用（可断言的零回归）。

## 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/live-batch/status` | 全局状态 |
| GET | `/api/live-batch/tasks` | 列出所有任务 |
| POST | `/api/live-batch/tasks` | 创建任务 |
| GET | `/api/live-batch/tasks/{task_id}` | 任务详情 |
| DELETE | `/api/live-batch/tasks/{task_id}` | 删除任务 |
| POST | `/api/live-batch/tasks/{task_id}/start` | 启动任务 |
| POST | `/api/live-batch/tasks/{task_id}/stop` | 停止任务 |
| GET | `/api/live-batch/instances/{instance_id}` | 实例详情 |
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from loguru import logger
from typing import Optional

from services.live_batch import (
    get_manager,
    LiveBatchConfig,
    batch_enabled,
)

router = APIRouter()


# ===========================================================================
# 请求模型
# ===========================================================================

class CreateTaskRequest(BaseModel):
    """创建批量任务请求体。"""
    name: str
    rooms: list[str]
    accounts: list[str]
    strategy: str = "round_robin"
    max_concurrent: int = 3
    enabled: bool = False
    dm_pool: Optional[list[str]] = None
    delay_range: Optional[list[int]] = None
    interval: float = 60.0
    #: 2026-10-03：每个直播间的私信条数上限（与 max_concurrent 正交）。
    max_target: int = 3
    #: 2026-10-03（用户定调「词库复用私信策略的标签」）：绑定配置标签 id。
    tag_id: str = ""


class StartTaskRequest(BaseModel):
    """启动任务请求体（可选覆盖）。"""
    enabled: Optional[bool] = None


class UpdateTaskRequest(BaseModel):
    """更新批量任务请求体（部分更新）。"""
    name: Optional[str] = None
    rooms: Optional[list[str]] = None
    accounts: Optional[list[str]] = None
    strategy: Optional[str] = None
    max_concurrent: Optional[int] = None
    enabled: Optional[bool] = None
    dm_pool: Optional[list[str]] = None
    delay_range: Optional[list[int]] = None
    interval: Optional[float] = None
    max_target: Optional[int] = None
    tag_id: Optional[str] = None


class CreateTemplateRequest(BaseModel):
    """创建任务模板请求体。"""
    name: str
    rooms: list[str]
    accounts: list[str]
    strategy: str = "round_robin"
    max_concurrent: int = 3
    dm_pool: Optional[list[str]] = None
    delay_range: Optional[list[int]] = None
    interval: float = 60.0
    max_target: int = 3
    tag_id: str = ""


# ===========================================================================
# 端点
# ===========================================================================

@router.get("/status")
async def get_global_status() -> dict:
    """获取批量采集全局状态。"""
    mgr = get_manager()
    return mgr.get_global_status()


@router.get("/tasks")
async def list_tasks() -> dict:
    """列出所有批量任务及其实例状态。"""
    mgr = get_manager()
    tasks = mgr.list_tasks()
    return {"ok": True, "tasks": tasks, "global_enabled": batch_enabled()}


@router.post("/tasks")
async def create_task(req: CreateTaskRequest) -> dict:
    """创建批量任务（仅注册配置，不启动）。"""
    if not batch_enabled():
        return {"ok": False, "error": "批量采集总开关未开启",
                "hint": "在设置页「直播编排策略」开启批量采集总开关"}

    # 参数校验
    if not req.name or not req.name.strip():
        raise HTTPException(400, "任务名不能为空")
    if not req.rooms:
        raise HTTPException(400, "至少需要一个直播间")
    if not req.accounts:
        raise HTTPException(400, "至少需要一个账号")
    if req.strategy not in ("round_robin", "fixed", "cartesian"):
        raise HTTPException(400, f"未知策略: {req.strategy}")
    if req.max_concurrent < 1 or req.max_concurrent > 10:
        raise HTTPException(400, "并发上限须在 1~10 之间")

    import uuid
    task_id = f"batch_{uuid.uuid4().hex[:8]}"

    # 转换 delay_range
    delay_range = None
    if req.delay_range and len(req.delay_range) == 2:
        delay_range = (req.delay_range[0], req.delay_range[1])

    config = LiveBatchConfig(
        task_id=task_id,
        name=req.name.strip(),
        rooms=[r.strip() for r in req.rooms if r.strip()],
        accounts=[a.strip() for a in req.accounts if a.strip()],
        strategy=req.strategy,
        max_concurrent=req.max_concurrent,
        enabled=req.enabled,
        dm_pool=req.dm_pool,
        delay_range=delay_range,
        interval=req.interval,
        max_target=req.max_target,
        tag_id=(req.tag_id or "").strip(),
    )

    mgr = get_manager()
    result = mgr.create_task(config)
    if not result.get("ok"):
        raise HTTPException(400, result.get("error", "创建失败"))
    return result


@router.get("/tasks/{task_id}")
async def get_task(task_id: str) -> dict:
    """获取任务详情。"""
    mgr = get_manager()
    result = mgr.get_status(task_id)
    if not result.get("ok"):
        raise HTTPException(404, result.get("error", "任务不存在"))
    return result


@router.delete("/tasks/{task_id}")
async def delete_task(task_id: str) -> dict:
    """删除任务（先停止所有实例）。"""
    mgr = get_manager()
    result = mgr.delete_task(task_id)
    if not result.get("ok"):
        raise HTTPException(404, result.get("error", "任务不存在"))
    return result


@router.post("/tasks/{task_id}/start")
async def start_task(task_id: str, req: Optional[StartTaskRequest] = None) -> dict:
    """启动批量任务。"""
    if not batch_enabled():
        return {"ok": False, "error": "批量采集总开关未开启",
                "hint": "在设置页「直播编排策略」开启批量采集总开关"}

    mgr = get_manager()
    # 可选：随启动一并改「启用」状态。
    # 🔴 2026-10-03：原先直接 `cfg.enabled = req.enabled` 绕过管理器
    # ⇒ 不落盘（重启后回退）。改走 `mgr.update_task()` 单一写入路径。
    if req and req.enabled is not None and mgr.get_task(task_id):
        mgr.update_task(task_id, {"enabled": bool(req.enabled)})

    result = mgr.start_task(task_id)
    if not result.get("ok"):
        raise HTTPException(400, result.get("error", "启动失败"))
    return result


@router.post("/tasks/{task_id}/stop")
async def stop_task(task_id: str) -> dict:
    """停止批量任务。"""
    mgr = get_manager()
    result = mgr.stop_task(task_id)
    if not result.get("ok"):
        raise HTTPException(404, result.get("error", "任务不存在"))
    return result


@router.get("/instances/{instance_id}")
async def get_instance(instance_id: str) -> dict:
    """获取实例详细状态（含弹幕流、热度、私信记录）。"""
    mgr = get_manager()
    result = mgr.get_instance_status(instance_id)
    if not result.get("ok"):
        raise HTTPException(404, result.get("error", "实例不存在"))
    return result


@router.put("/tasks/{task_id}")
async def update_task(task_id: str, req: UpdateTaskRequest) -> dict:
    """更新批量任务（部分更新）。

    🔴 2026-10-03：改为**委托管理器** `mgr.update_task()`。
    原实现在这里直接改 `mgr.get_task()` 返回的 `cfg` 属性 ——
    既不落盘（重启即丢）也不持锁。HTTP 层只保留**入参校验**，
    字段落盘与并发安全由管理器负责（单一写入路径）。
    """
    if not batch_enabled():
        return {"ok": False, "error": "批量采集总开关未开启",
                "hint": "在设置页「直播编排策略」开启批量采集总开关"}

    mgr = get_manager()
    if not mgr.get_task(task_id):
        raise HTTPException(404, "任务不存在")

    # ---- 入参校验（不通过就不该进管理器）----
    if req.strategy is not None and req.strategy not in ("round_robin", "fixed", "cartesian"):
        raise HTTPException(400, f"未知策略: {req.strategy}")
    if req.max_concurrent is not None and not (1 <= req.max_concurrent <= 10):
        raise HTTPException(400, "并发上限须在 1~10 之间")
    if req.max_target is not None and not (1 <= req.max_target <= 999):
        raise HTTPException(400, "每房私信上限须在 1~999 之间")

    patch = req.model_dump(exclude_none=True)
    # 列表字段去空白（与 create 口径一致）
    for k in ("rooms", "accounts"):
        if k in patch:
            patch[k] = [str(x).strip() for x in patch[k] if str(x).strip()]
    if "delay_range" in patch and len(patch["delay_range"] or []) != 2:
        patch.pop("delay_range")
    for k in ("name", "tag_id"):
        if k in patch:
            patch[k] = str(patch[k]).strip()

    result = mgr.update_task(task_id, patch)
    if not result.get("ok"):
        raise HTTPException(404, result.get("error", "任务不存在"))
    logger.info(f"[LiveBatch] 任务已更新: {task_id}")
    return result


@router.post("/tasks/{task_id}/restart")
async def restart_task(task_id: str) -> dict:
    """重启批量任务（先停止再启动）。"""
    if not batch_enabled():
        return {"ok": False, "error": "批量采集总开关未开启",
                "hint": "在设置页「直播编排策略」开启批量采集总开关"}

    mgr = get_manager()
    # 先停止
    stop_result = mgr.stop_task(task_id)
    if not stop_result.get("ok"):
        raise HTTPException(404, stop_result.get("error", "任务不存在"))
    
    # 再启动
    start_result = mgr.start_task(task_id)
    if not start_result.get("ok"):
        raise HTTPException(400, start_result.get("error", "启动失败"))
    
    return {
        "ok": True,
        "stopped": stop_result.get("stopped", []),
        "started": start_result.get("started", []),
        "failed": start_result.get("failed", []),
    }


@router.post("/instances/{instance_id}/start")
async def start_instance(instance_id: str) -> dict:
    """启动单个实例。"""
    if not batch_enabled():
        return {"ok": False, "error": "批量采集总开关未开启",
                "hint": "在设置页「直播编排策略」开启批量采集总开关"}

    mgr = get_manager()
    # 获取实例信息
    inst = mgr._instances.get(instance_id)
    if not inst:
        raise HTTPException(404, "实例不存在")
    
    # 检查任务是否启用
    cfg = mgr.get_task(inst.task_id)
    if not cfg or not cfg.enabled:
        raise HTTPException(400, "任务已停用，无法启动实例")

    # 启动实例
    result = mgr._start_instance(inst.task_id, inst.room_url, inst.account, cfg)
    if not result.get("ok"):
        raise HTTPException(400, result.get("error", "启动失败"))
    return result


@router.post("/instances/{instance_id}/stop")
async def stop_instance(instance_id: str) -> dict:
    """停止单个实例。"""
    mgr = get_manager()
    result = mgr._stop_instance(instance_id)
    if not result.get("ok"):
        raise HTTPException(404, result.get("error", "实例不存在"))
    return result


@router.post("/instances/{instance_id}/restart")
async def restart_instance(instance_id: str) -> dict:
    """重启单个实例。"""
    if not batch_enabled():
        return {"ok": False, "error": "批量采集总开关未开启",
                "hint": "在设置页「直播编排策略」开启批量采集总开关"}

    mgr = get_manager()
    # 获取实例信息
    inst = mgr._instances.get(instance_id)
    if not inst:
        raise HTTPException(404, "实例不存在")
    
    # 先停止
    stop_result = mgr._stop_instance(instance_id)
    if not stop_result.get("ok"):
        raise HTTPException(404, stop_result.get("error", "实例不存在"))
    
    # 再启动
    cfg = mgr.get_task(inst.task_id)
    if not cfg:
        raise HTTPException(404, "任务不存在")
    
    start_result = mgr._start_instance(inst.task_id, inst.room_url, inst.account, cfg)
    if not start_result.get("ok"):
        raise HTTPException(400, start_result.get("error", "启动失败"))
    
    return {
        "ok": True,
        "stopped": stop_result.get("ok", False),
        "started": start_result.get("ok", False),
    }


# ===========================================================================
# 任务模板
# ===========================================================================

@router.get("/templates")
async def list_templates() -> dict:
    """列出所有任务模板。"""
    from database import get_kv_json
    templates = get_kv_json("live_batch_templates", {}) or {}
    return {"ok": True, "templates": list(templates.values())}


@router.post("/templates")
async def create_template(req: CreateTemplateRequest) -> dict:
    """创建任务模板。"""
    if not batch_enabled():
        return {"ok": False, "error": "批量采集总开关未开启",
                "hint": "在设置页「直播编排策略」开启批量采集总开关"}
    
    if not req.name or not req.name.strip():
        raise HTTPException(400, "模板名不能为空")
    if not req.rooms:
        raise HTTPException(400, "至少需要一个直播间")
    if not req.accounts:
        raise HTTPException(400, "至少需要一个账号")
    
    import uuid
    import time
    template_id = f"tmpl_{uuid.uuid4().hex[:8]}"
    
    template = {
        "id": template_id,
        "name": req.name.strip(),
        "rooms": [r.strip() for r in req.rooms if r.strip()],
        "accounts": [a.strip() for a in req.accounts if a.strip()],
        "strategy": req.strategy,
        "max_concurrent": req.max_concurrent,
        "dm_pool": req.dm_pool,
        "delay_range": req.delay_range,
        "interval": req.interval,
        "max_target": req.max_target,
        "tag_id": (req.tag_id or "").strip(),
        "created_at": time.time(),
    }
    
    from database import get_kv_json, set_kv_json
    templates = get_kv_json("live_batch_templates", {}) or {}
    templates[template_id] = template
    set_kv_json("live_batch_templates", templates)
    
    logger.info(f"[LiveBatch] 模板已创建: {template_id}")
    return {"ok": True, "template": template}


@router.delete("/templates/{template_id}")
async def delete_template(template_id: str) -> dict:
    """删除任务模板。"""
    from database import get_kv_json, set_kv_json
    templates = get_kv_json("live_batch_templates", {}) or {}
    if template_id not in templates:
        raise HTTPException(404, "模板不存在")
    templates.pop(template_id, None)
    set_kv_json("live_batch_templates", templates)
    return {"ok": True}


# ===========================================================================
# 数据导出
# ===========================================================================

@router.get("/export/{task_id}")
async def export_task(task_id: str) -> dict:
    """导出任务数据（JSON 格式）。"""
    mgr = get_manager()
    result = mgr.get_status(task_id)
    if not result.get("ok"):
        raise HTTPException(404, result.get("error", "任务不存在"))
    
    # 获取所有实例的详细数据
    instances_data = []
    for inst in result.get("instances", []):
        inst_detail = mgr.get_instance_status(inst["instance_id"])
        if inst_detail.get("ok"):
            instances_data.append(inst_detail.get("instance", {}))
    
    return {
        "ok": True,
        "task": result.get("task", {}),
        "instances": instances_data,
        "exported_at": __import__("time").time(),
    }
