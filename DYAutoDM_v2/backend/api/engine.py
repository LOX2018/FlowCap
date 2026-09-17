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
    #
    # 2026-09-17 修补（OCR 审查 HIGH —— check-then-act 竞态 + 丢弃 Task）：
    # 原实现只**读** `adm.state` 就 create_task；而 `start()` 里的
    # `self.state = EngineState.STARTING` 要等该协程真正被调度才执行 →
    # 两个并发的 /start 请求都会看到 IDLE 而各自拉起一个 start 任务
    # （第二个会在任务内抛 RuntimeError("当前状态 ... 无法启动")）。
    # 且 `asyncio.create_task(...)` 的返回值被丢弃：任务内异常无人取回，
    # 只会在 GC 时打 "Task exception was never retrieved"，而路由仍返回
    # {"ok": True, "state": "starting"} —— 假成功。
    # 现用 asyncio.Lock 串行化「检查 + 拉起」，并保留任务引用 + 完成回调，
    # 让启动失败能被记录（而不是静默变成假成功）。
    lock = getattr(request.app.state, "_engine_start_lock", None)
    if lock is None:
        lock = asyncio.Lock()
        request.app.state._engine_start_lock = lock
    async with lock:
        if adm.state in (EngineState.RUNNING, EngineState.STARTING):
            logger.info(f"[engine] start 请求但已在 {adm.state.value}，忽略重复启动")
            return {"ok": True, "state": adm.state.value, "already": True}

        def _on_start_done(t: asyncio.Task) -> None:
            if t.cancelled():
                logger.warning("[ENG-012] [engine] 启动任务被取消")
                return
            exc = t.exception()
            if exc is not None:
                logger.error(f"[ENG-011] [engine] 启动任务异常: "
                             f"{type(exc).__name__}: {exc}")

        logger.info(f"[engine] 引擎启动中 live_url={cfg.live_url}")
        task = asyncio.create_task(adm.start(cfg))
        # 保留引用（避免被 GC 提前回收），并挂完成回调以观测异常
        running = getattr(request.app.state, "_engine_start_tasks", None)
        if running is None:
            running = set()
            request.app.state._engine_start_tasks = running
        running.add(task)
        task.add_done_callback(running.discard)
        task.add_done_callback(_on_start_done)
        return {"ok": True, "state": "starting"}


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
