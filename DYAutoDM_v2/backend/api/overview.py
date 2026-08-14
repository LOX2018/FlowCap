"""总览路由：getOverview / getStats"""
from fastapi import APIRouter, Request
from models.overview import OverviewResponse, StatusResponse

router = APIRouter()


@router.get("/overview")
async def get_overview(request: Request) -> OverviewResponse:
    """总览（替代原版 WebBridge.getOverview）"""
    adm = request.app.state.adm
    return OverviewResponse(
        engine_state=adm.state.value,
        sent=adm.sent_count,
        limit=adm.limit,
        captured=adm.captured_count,
        accounts=[],
        daemons=[],
    )


@router.get("/stats")
async def get_stats() -> dict:
    """实时统计（前端轮询 / WebSocket 推送）"""
    return {"sent": 0, "captured": 0, "queue": 0}
