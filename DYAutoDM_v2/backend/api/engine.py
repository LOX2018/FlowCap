"""引擎控制路由：start / pause / resume / stop（**按账号**，ADR-002）

取代原版 AutoDM 的多标志位状态机（_running/listen_active/hard_stopped/no_new/paused），
改用单一 enum EngineState —— 且自 ADR-002（v0.44.39）起该状态机是**每账号一个实例**。

## 账号维度（ADR-002 §5.3）

| 请求 | 行为 |
|---|---|
| `acct` 非空 | 取/建该账号的引擎实例 |
| `acct` 为空且**只有一个**任务在跑 | 作用于该任务（单账号零回归） |
| `acct` 为空且有**多个**任务在跑 | **409**（歧义必须显式失败，不猜账号） |
| 同账号重复 `/start` | **409**「该账号已在监听」（**不再是静默 `already:true`**） |

⚠️ 旧实现的静默假成功（实测：张老师 RUNNING 时对小助理 `/start` 返回
`{"ok":true,"already":true}`，实际什么都没发生）违反项目规则「拿不到资源必须显式失败」。
"""
import asyncio

from loguru import logger

from fastapi import APIRouter, Request, HTTPException, Query
from models.task import TaskConfig
from models.enums import EngineState
from core.auto_dm import AutoDM
from services.engine_registry import EngineRegistry, ANONYMOUS_KEY

router = APIRouter()


# ── 编排层访问（兼容未初始化 registry 的场景：退回旧单例语义）──────────────
def _registry(request: Request) -> EngineRegistry | None:
    return getattr(request.app.state, "engines", None)


def _resolve_adm(request: Request, acct: str | None, *, for_start: bool = False,
                 create: bool = True) -> AutoDM:
    """按账号取引擎实例。

    ``create=False`` 时只取已存在的实例（查询/控制路径不该把实例「建」出来），
    且**取不到必须显式失败**（绝不退回别人的实例 —— 那会停错任务）。
    ``acct`` 为空时的单任务回落：恰好一个任务在跑 → 用它；多个 → 409。
    """
    reg = _registry(request)
    if reg is None:                       # 未挂 registry（旧调用方/测试替身）
        return request.app.state.adm
    a = str(acct or "").strip()
    if a:
        eng = reg.get(a) if create else reg.get_or_none(a)
        if eng is None:
            # 🔴 不得回落到 app.state.adm：那会「停掉别的账号的任务」而调用方不知道
            raise HTTPException(
                404, f"账号「{a}」没有进行中的直播任务（该账号尚未启动引擎）")
        return eng
    busy = reg.busy_keys()
    if len(busy) == 1:
        return reg.get(busy[0]) if create else (reg.get_or_none(busy[0])
                                               or request.app.state.adm)
    if len(busy) > 1:
        raise HTTPException(
            409,
            f"存在多个进行中的直播任务（{', '.join(busy)}）—— 请显式指定 acct，"
            f"不猜测账号",
        )
    # 无任务在跑
    if create:
        return reg.get(ANONYMOUS_KEY)     # 等价于旧单例语义
    eng = reg.get_or_none(ANONYMOUS_KEY)
    if eng is None:
        raise HTTPException(404, "当前没有进行中的直播任务")
    return eng


def _account_of(adm: AutoDM) -> str:
    return str(getattr(adm, "target_acct", "") or getattr(adm, "_acct", "") or "")


@router.post("/start")
async def start_engine(request: Request, config: TaskConfig):
    """启动自动私信引擎（**按账号**）。

    优化（#51）：改为后台任务立即返回，消除前端 2.4s 同步等待。
    """
    # 把前端别名归一到规范字段
    cfg = config.resolved()
    # 空直播间链接属于不合法的启动参数，应返回结构化 400 而非 500 崩溃
    if not cfg.live_url or not cfg.live_url.strip():
        logger.warning(f"[ENG-010] " + "[engine] 启动被拒：live_url 为空")
        raise HTTPException(400, "live_url 不能为空（需提供直播间链接或房间号）")

    reg = _registry(request)
    adm: AutoDM = _resolve_adm(request, cfg.acct)
    key = str(cfg.acct or "").strip() or ANONYMOUS_KEY

    # 已在运行/启动中 → **显式 409**（ADR-002 §5.3；旧实现静默 ok:true = 假成功）
    #
    # 2026-09-17 修补（OCR 审查 HIGH —— check-then-act 竞态 + 丢弃 Task）保留：
    # `AutoDM.start()` 里的 `self.state = EngineState.STARTING` 要等该协程被真正
    # 调度才执行，故必须在锁内先让出控制权再检查状态，否则并发 /start 都会看到 IDLE。
    # ADR-002 起锁是**每账号一把**（多账号并发时不同账号不得互相阻塞）。
    locks = getattr(request.app.state, "_engine_start_locks", None)
    if locks is None:
        locks = {}
        request.app.state._engine_start_locks = locks
    lock = locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        locks[key] = lock

    async with lock:
        if adm.state in (EngineState.RUNNING, EngineState.STARTING):
            logger.info(f"[engine] acct={key} 已在 {adm.state.value}，拒绝重复启动（409）")
            raise HTTPException(
                409,
                f"该账号已在监听（acct={key}，state={adm.state.value}）—— "
                f"如需换房请先停止",
            )

        def _on_start_done(t: asyncio.Task) -> None:
            if t.cancelled():
                logger.warning("[ENG-012] [engine] 启动任务被取消")
                return
            exc = t.exception()
            if exc is not None:
                logger.error(f"[ENG-011] [engine] 启动任务异常: "
                             f"{type(exc).__name__}: {exc}")

        logger.info(f"[engine] 引擎启动中 acct={key} live_url={cfg.live_url}")
        task = asyncio.create_task(adm.start(cfg))
        # 保留引用（避免被 GC 提前回收），并挂完成回调以观测异常
        running = getattr(request.app.state, "_engine_start_tasks", None)
        if running is None:
            running = set()
            request.app.state._engine_start_tasks = running
        running.add(task)
        task.add_done_callback(running.discard)
        task.add_done_callback(_on_start_done)
        # 关键：让出控制权，等 start 协程把状态推离 IDLE/STOPPED（它进入
        # STARTING 即返回；若已 RUNNING 也视为已启动）。最多等若干轮，
        # 以覆盖 start() 开头的同步解析（resolve_live_id）耗时。
        for _ in range(50):
            if adm.state not in (EngineState.IDLE, EngineState.STOPPED):
                break
            if task.done():        # start 已结束（成功进入 RUNNING 或失败）
                break
            await asyncio.sleep(0)
        # 兼容：把「最近启动的引擎」暴露为 app.state.adm（旧调用方单值读取）
        request.app.state.adm = adm
        return {"ok": True, "state": getattr(adm.state, "value", "starting"),
                "acct": "" if key == ANONYMOUS_KEY else key}


@router.post("/pause")
async def pause_engine(request: Request, acct: str = Query("")):
    adm: AutoDM = _resolve_adm(request, acct, create=False)
    logger.info(f"[engine] 引擎暂停 acct={_account_of(adm) or ANONYMOUS_KEY}")
    await adm.pause()
    return {"ok": True, "state": adm.state.value, "acct": _account_of(adm)}


@router.post("/resume")
async def resume_engine(request: Request, acct: str = Query("")):
    adm: AutoDM = _resolve_adm(request, acct, create=False)
    logger.info(f"[engine] 引擎恢复 acct={_account_of(adm) or ANONYMOUS_KEY}")
    await adm.resume()
    return {"ok": True, "state": adm.state.value, "acct": _account_of(adm)}


@router.post("/stop")
async def stop_engine(request: Request, acct: str = Query("")):
    """硬停止：立即清队列"""
    adm: AutoDM = _resolve_adm(request, acct, create=False)
    logger.warning(f"[ENG-012] " + f"[engine] 引擎硬停止（清空队列）acct={_account_of(adm) or ANONYMOUS_KEY}")
    await adm.stop(hard=True)
    return {"ok": True, "state": adm.state.value, "acct": _account_of(adm)}


@router.post("/stop-soft")
async def stop_soft(request: Request, acct: str = Query("")):
    """软停止：停止监听，存量队列发完"""
    adm: AutoDM = _resolve_adm(request, acct, create=False)
    logger.info(f"[engine] 引擎软停止（存量队列发完）acct={_account_of(adm) or ANONYMOUS_KEY}")
    await adm.stop(hard=False)
    return {"ok": True, "state": adm.state.value, "acct": _account_of(adm)}


@router.get("/accounts")
async def list_engine_accounts(request: Request) -> dict:
    """各账号引擎状态一览（前端多任务卡片的数据源，ADR-002 §5.6）。

    只读：不创建实例（``get_or_none``），未启动的账号不出现。
    """
    reg = _registry(request)
    if reg is None:
        adm: AutoDM = request.app.state.adm
        return {"ok": True, "items": [{
            "acct": _account_of(adm), "state": getattr(adm.state, "value", "idle"),
            "live_url": getattr(adm, "live_url", None),
            "sent": getattr(adm, "sent_count", 0),
        }]}
    items = []
    for key, eng in reg.items():
        if key == ANONYMOUS_KEY and not eng.state:
            continue
        items.append({
            "acct": "" if key == ANONYMOUS_KEY else key,
            "state": getattr(getattr(eng, "state", None), "value", "idle"),
            "live_url": getattr(eng, "live_url", None),
            "live_id": getattr(eng, "live_id", None),
            "sent": getattr(eng, "sent_count", 0),
            "status_msg": getattr(eng, "status_msg", ""),
        })
    return {"ok": True, "items": items}
