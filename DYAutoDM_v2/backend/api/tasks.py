"""任务路由

取代原版 WebBridge.getTasks / saveDmPool。
注意：删除原版 tasks.js 的本地 setInterval 模拟任务进度（误导）。
"""
from fastapi import APIRouter, Request
from models.task import TaskConfig, TaskListResponse

router = APIRouter()


@router.get("")
async def get_tasks(request: Request) -> TaskListResponse:
    """任务配置 + 发送记录"""
    adm = request.app.state.adm
    # TODO: 返回真实数据
    return TaskListResponse(
        config=TaskConfig(live_url=adm.live_url or "", max_target=adm.limit),
        records=[],
        sent=adm.sent_count,
        captured=adm.captured_count,
    )


@router.post("/config")
async def save_config(body: TaskConfig):
    """保存任务配置"""
    # TODO: 持久化到 data/config.json
    return {"ok": True}


@router.post("/dm-pool")
async def save_dm_pool(items: list[str]):
    """保存私信模板池"""
    return {"ok": True, "count": len(items)}
