"""引擎控制路由：start / pause / resume / stop

取代原版 AutoDM 的多标志位状态机（_running/listen_active/hard_stopped/no_new/paused），
改用单一 enum EngineState。
"""
import asyncio

from loguru import logger

from fastapi import APIRouter, Request, HTTPException
from models.task import TaskConfig
from models.enums import EngineState
from core.auto_dm import AutoDM

router = APIRouter()


@router.post("/start")
async def start_engine(request: Request, config: TaskConfig):
    """启动自动私信引擎。

    优化（#51）：改为后台任务立即返回，消除前端 2.4s 同步等待。
    - adm.start 内部有 STARTING 轮询（最长 60s）+ _run 前几步网络探活
      （_verify_credential + check_room_live，各 1~2s），同步 await 会让
      前端按钮点击后卡住 2.4s。
    - 改为 asyncio.create_task 后台跑，路由立即返回 {ok:True, state:"starting"}，
      前端由 /api/engine/status 轮询反映真实状态（listening/running 等）。
    """
    adm: AutoDM = request.app.state.adm
    # 把前端别名归一到规范字段
    cfg = config.resolved()
    # 空直播间链接属于不合法的启动参数，应返回结构化 400 而非 500 崩溃
    if not cfg.live_url or not cfg.live_url.strip():
        logger.warning(f"[ENG-010] " + "[engine] 启动被拒：live_url 为空")
        raise HTTPException(400, "live_url 不能为空（需提供直播间链接或房间号）")
    # 已在运行/启动中则直接返回当前状态（不重复拉起）
    if adm.state in (EngineState.RUNNING, EngineState.STARTING):
        logger.info(f"[engine] start 请求但已在 {adm.state.value}，忽略重复启动")
        return {"ok": True, "state": adm.state.value, "already": True}
    try:
        logger.info(f"[engine] 引擎启动中 live_url={cfg.live_url}")
        asyncio.create_task(adm.start(cfg))
        return {"ok": True, "state": "starting"}
    except Exception as e:
        logger.exception(f"[ENG-011] " + f"[engine] 启动失败: {e}")
        raise HTTPException(500, f"启动失败: {e}")


@router.post("/pause")
async def pause_engine(request: Request):
    adm: AutoDM = request.app.state.adm
    logger.info("[engine] 引擎暂停")
    await adm.pause()
    return {"ok": True, "state": adm.state.value}


@router.post("/resume")
async def resume_engine(request: Request):
    adm: AutoDM = request.app.state.adm
    logger.info("[engine] 引擎恢复")
    await adm.resume()
    return {"ok": True, "state": adm.state.value}


@router.post("/stop")
async def stop_engine(request: Request):
    """硬停止：立即清队列"""
    adm: AutoDM = request.app.state.adm
    logger.warning(f"[ENG-012] " + "[engine] 引擎硬停止（清空队列）")
    await adm.stop(hard=True)
    return {"ok": True, "state": adm.state.value}


@router.post("/stop-soft")
async def stop_soft(request: Request):
    """软停止：停止监听，存量队列发完"""
    adm: AutoDM = request.app.state.adm
    logger.info("[engine] 引擎软停止（存量队列发完）")
    await adm.stop(hard=False)
    return {"ok": True, "state": adm.state.value}
