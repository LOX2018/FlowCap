"""引擎控制路由：start / pause / resume / stop

取代原版 AutoDM 的多标志位状态机（_running/listen_active/hard_stopped/no_new/paused），
改用单一 enum EngineState。
"""
from fastapi import APIRouter, Request, HTTPException
from models.task import TaskConfig
from core.auto_dm import AutoDM

router = APIRouter()


@router.post("/start")
async def start_engine(request: Request, config: TaskConfig):
    adm: AutoDM = request.app.state.adm
    try:
        await adm.start(config)
        return {"ok": True, "state": adm.state.value}
    except Exception as e:
        raise HTTPException(500, f"启动失败: {e}")


@router.post("/pause")
async def pause_engine(request: Request):
    adm: AutoDM = request.app.state.adm
    await adm.pause()
    return {"ok": True, "state": adm.state.value}


@router.post("/resume")
async def resume_engine(request: Request):
    adm: AutoDM = request.app.state.adm
    await adm.resume()
    return {"ok": True, "state": adm.state.value}


@router.post("/stop")
async def stop_engine(request: Request):
    """硬停止：立即清队列"""
    adm: AutoDM = request.app.state.adm
    await adm.stop(hard=True)
    return {"ok": True, "state": adm.state.value}


@router.post("/stop-soft")
async def stop_soft(request: Request):
    """软停止：停止监听，存量队列发完"""
    adm: AutoDM = request.app.state.adm
    await adm.stop(hard=False)
    return {"ok": True, "state": adm.state.value}
