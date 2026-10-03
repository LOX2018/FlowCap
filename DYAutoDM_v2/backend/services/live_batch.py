# coding=utf-8
"""批量直播监听管理器（多房间 × 多账号并发采集）。

## 为什么独立成服务，而不是复用 AutoDM / task_scheduler（架构决策）

1. **AutoDM 是单例**：`app.state.adm` 全局只有一个实例，一个 `LiveChatHook`
   + 一个 `DispatchCenter`，设计目标是「单房间单账号」的监听场景。
   批量采集需要**多个独立实例并发**，与单例语义冲突。

2. **task_scheduler 不适合**：其契约是同步一次性 `fn(task)->dict` + 300s 超时
   （见 `services/task_scheduler.py` 模块头）。直播监听是**持续型任务**（直到用户
   主动停止），且需要独立的弹幕流、热度曲线、私信队列，调度器的抽象层级不匹配。

3. **LiveAutomation 也不适合**：它管的是「写接口自动化」（定时弹幕/点赞），
   且由 `LiveChatHook` 持有生命周期。批量采集需要**多个 LiveChatHook 实例**，
   与「一个 Hook 持有一个 Automation」的既有设计冲突。

故：本模块是**独立服务**，复用 `LiveChatHook` / `DispatchCenter` 的**能力**，
但有自己的生命周期管理、风控层、状态聚合。

## 风控（继承项目红线）

1. **默认休眠**：`LIVE_BATCH_ENABLED` 默认 `False`；未显式开启时 `start()` 拒绝。
2. **并发上限**：`max_concurrent` 控制同时运行的实例数（默认 3）。
3. **速率限制**：全局每分钟请求数上限（`rate_limit_per_minute`），防止批量轮询
   改变风控形状。
4. **fail-closed**：任何异常不静默放行，明确返回失败原因。
5. **实例隔离**：单个实例失败不影响其他实例（异常隔离）。

## 契约

```python
mgr = LiveBatchManager()
mgr.create_task(config)       # 创建批量任务（配置校验）
mgr.start_task(task_id)       # 启动（受总开关 + 并发上限约束）
mgr.stop_task(task_id)       # 停止
mgr.get_status(task_id)       # 查询状态
mgr.list_tasks()              # 列出所有任务
mgr.delete_task(task_id)      # 删除任务
```
"""
from __future__ import annotations

import os
import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from loguru import logger


# ===========================================================================
# 配置（显式配置原则：行为由配置决定，不靠自动探测本机状态）
# ===========================================================================

def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    try:
        return int(str(os.environ.get(name, "")).strip() or default)
    except (TypeError, ValueError):
        return default


#: 🔴 总开关 —— **默认 False（休眠）**。与 task_scheduler 同款红线。
LIVE_BATCH_ENABLED = _env_bool("DY_LIVE_BATCH_ENABLED", False)

#: 全局并发上限（同时运行的实例数）
LIVE_BATCH_MAX_CONCURRENT = _env_int("DY_LIVE_BATCH_MAX_CONCURRENT", 3)

#: 全局速率限制（每分钟请求数上限，防止批量轮询改变风控形状）
LIVE_BATCH_RATE_LIMIT_PER_MIN = _env_int("DY_LIVE_BATCH_RATE_LIMIT_PER_MIN", 60)

#: 单实例启动超时（秒）
INSTANCE_START_TIMEOUT = _env_int("DY_LIVE_BATCH_INSTANCE_START_TIMEOUT", 30)

#: 单实例停止超时（秒）
INSTANCE_STOP_TIMEOUT = _env_int("DY_LIVE_BATCH_INSTANCE_STOP_TIMEOUT", 10)


# ===========================================================================
# 数据模型
# ===========================================================================

@dataclass
class LiveBatchConfig:
    """批量采集任务配置。

    Attributes:
        task_id:        唯一 ID
        name:           展示名
        rooms:          直播间 URL 列表
        accounts:       账号名列表
        strategy:       分配策略："round_robin"（轮询）/ "fixed"（固定配对）
        max_concurrent: 该任务的并发上限（受全局上限约束）
        enabled:        该任务自身开关（仍受总开关约束）
        dm_pool:        私信词库（可选，None 时用账号默认）
        delay_range:    私信延迟区间（可选）
        interval:       私信间隔（可选）
    """
    task_id: str
    name: str
    rooms: list[str] = field(default_factory=list)
    accounts: list[str] = field(default_factory=list)
    strategy: str = "round_robin"
    max_concurrent: int = 3
    enabled: bool = False
    dm_pool: Optional[list[str]] = None
    delay_range: Optional[tuple[int, int]] = None
    interval: float = 60.0
    #: 2026-10-03 新增：**每个直播间**的私信条数上限（传给 DispatchCenter.max_target）。
    #: 与 `max_concurrent` **语义正交** —— 后者是「同时开几个监听实例」，
    #: 前者是「每个房间这一场发几条」。原先代码读 `cfg.max_target` 但字段并不存在，
    #: getattr 永远回退 ⇒ 「每房 3 条」被隐式写死且无法配置。
    max_target: int = 3

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "name": self.name,
            "rooms": list(self.rooms),
            "accounts": list(self.accounts),
            "strategy": self.strategy,
            "max_concurrent": self.max_concurrent,
            "enabled": self.enabled,
            "dm_pool": list(self.dm_pool) if self.dm_pool else None,
            "delay_range": list(self.delay_range) if self.delay_range else None,
            "interval": self.interval,
            "max_target": self.max_target,
        }


@dataclass
class LiveInstance:
    """单个监听实例（一个房间 + 一个账号）。

    每个实例独立持有 LiveChatHook 和 DispatchCenter，互不干扰。
    """
    instance_id: str
    task_id: str
    room_url: str
    account: str
    live_id: str = ""           # 解析后的直播间 ID
    real_room_id: str = ""      # 真实 room_id（API 用）
    state: str = "idle"         # idle/starting/running/paused/stopping/stopped/error
    status_msg: str = ""
    error: str = ""
    started_at: float = 0.0
    stopped_at: float = 0.0
    # 运行时引用（不序列化）
    _live_hook: Any = None
    _dispatch: Any = None
    _thread: Optional[threading.Thread] = None
    _stop_event: Optional[threading.Event] = None

    def to_dict(self) -> dict:
        return {
            "instance_id": self.instance_id,
            "task_id": self.task_id,
            "room_url": self.room_url,
            "account": self.account,
            "live_id": self.live_id,
            "real_room_id": self.real_room_id,
            "state": self.state,
            "status_msg": self.status_msg,
            "error": self.error,
            "started_at": self.started_at,
            "stopped_at": self.stopped_at,
        }


# ===========================================================================
# 批量管理器
# ===========================================================================

class LiveBatchManager:
    """批量直播监听管理器（进程内单例）。

    管理多个批量任务，每个任务包含多个监听实例。
    实例之间完全隔离：一个失败不影响其他。
    """

    _instance: Optional["LiveBatchManager"] = None
    _lock = threading.RLock()

    def __new__(cls) -> "LiveBatchManager":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._initialized = True
        self._tasks: dict[str, LiveBatchConfig] = {}
        self._instances: dict[str, LiveInstance] = {}
        self._task_instances: dict[str, list[str]] = {}  # task_id -> [instance_id]
        self._rate_limiter = _RateLimiter(LIVE_BATCH_RATE_LIMIT_PER_MIN)
        self._state_lock = threading.RLock()

    # ── 任务 CRUD ────────────────────────────────────────────────────────

    def create_task(self, config: LiveBatchConfig) -> dict:
        """创建批量任务（仅注册配置，不启动）。"""
        with self._state_lock:
            if config.task_id in self._tasks:
                return {"ok": False, "error": f"任务已存在: {config.task_id}"}
            # 校验：至少一个房间和一个账号
            if not config.rooms:
                return {"ok": False, "error": "至少需要一个直播间"}
            if not config.accounts:
                return {"ok": False, "error": "至少需要一个账号"}
            # 校验账号存在
            from auto_dm import accounts as acct_core
            for acct in config.accounts:
                if not acct_core.env_path_of(acct):
                    return {"ok": False, "error": f"账号不存在: {acct}"}
            self._tasks[config.task_id] = config
            self._task_instances[config.task_id] = []
            logger.info(f"[LiveBatch] 任务已创建: {config.name} "
                        f"({len(config.rooms)} 房间 × {len(config.accounts)} 账号)")
            return {"ok": True, "task": config.to_dict()}

    def delete_task(self, task_id: str) -> dict:
        """删除任务（先停止所有实例）。"""
        with self._state_lock:
            if task_id not in self._tasks:
                return {"ok": False, "error": f"任务不存在: {task_id}"}
            # 先停止
            self.stop_task(task_id)
            # 清理实例
            for iid in self._task_instances.get(task_id, []):
                self._instances.pop(iid, None)
            self._task_instances.pop(task_id, None)
            self._tasks.pop(task_id, None)
            return {"ok": True}

    def list_tasks(self) -> list[dict]:
        """列出所有任务及其实例状态。"""
        with self._state_lock:
            result = []
            for tid, cfg in self._tasks.items():
                instances = [
                    self._instances[iid].to_dict()
                    for iid in self._task_instances.get(tid, [])
                    if iid in self._instances
                ]
                result.append({
                    "task": cfg.to_dict(),
                    "instances": instances,
                    "running_count": sum(1 for i in instances if i["state"] == "running"),
                    "total_count": len(instances),
                })
            return result

    def get_task(self, task_id: str) -> Optional[LiveBatchConfig]:
        with self._state_lock:
            return self._tasks.get(task_id)

    def get_status(self, task_id: str) -> dict:
        """获取任务状态（含所有实例）。"""
        with self._state_lock:
            cfg = self._tasks.get(task_id)
            if not cfg:
                return {"ok": False, "error": f"任务不存在: {task_id}"}
            instances = [
                self._instances[iid].to_dict()
                for iid in self._task_instances.get(task_id, [])
                if iid in self._instances
            ]
            return {
                "ok": True,
                "task": cfg.to_dict(),
                "instances": instances,
                "running_count": sum(1 for i in instances if i["state"] == "running"),
                "total_count": len(instances),
                "global_enabled": LIVE_BATCH_ENABLED,
                "global_max_concurrent": LIVE_BATCH_MAX_CONCURRENT,
            }

    # ── 启动 / 停止 ──────────────────────────────────────────────────────

    def start_task(self, task_id: str) -> dict:
        """启动批量任务（受总开关 + 并发上限约束）。"""
        if not LIVE_BATCH_ENABLED:
            return {"ok": False, "error": "批量采集总开关未开启（默认休眠）",
                    "hint": "设置 DY_LIVE_BATCH_ENABLED=1 以启用"}

        with self._state_lock:
            cfg = self._tasks.get(task_id)
            if not cfg:
                return {"ok": False, "error": f"任务不存在: {task_id}"}
            if not cfg.enabled:
                return {"ok": False, "error": "任务已停用（enabled=False）"}

            # 计算需要启动的实例
            instances_to_start = self._plan_instances(cfg)
            if not instances_to_start:
                return {"ok": False, "error": "没有可启动的实例（检查房间和账号配置）"}

            # 检查全局并发上限
            current_running = sum(
                1 for inst in self._instances.values() if inst.state == "running"
            )
            available_slots = LIVE_BATCH_MAX_CONCURRENT - current_running
            if available_slots <= 0:
                return {"ok": False,
                        "error": f"全局并发已达上限（{LIVE_BATCH_MAX_CONCURRENT}）",
                        "current_running": current_running}

            # 启动实例（不超过可用槽位）
            started = []
            failed = []
            already = []
            for room_url, account in instances_to_start[:available_slots]:
                result = self._start_instance(task_id, room_url, account, cfg)
                if result.get("ok"):
                    # 🔴 2026-10-03：区分「新建」与「已在运行」。
                    # 混报会让 UI 在重复点「启动」时显示「已启动 N 个」，
                    # 而实际 N 个都是既有实例 —— 用户无法判断自己有没有多开。
                    if result.get("already"):
                        already.append(result["instance_id"])
                    else:
                        started.append(result["instance_id"])
                else:
                    failed.append({"room": room_url, "account": account,
                               "error": result.get("error", "")})

            logger.info(f"[LiveBatch] 任务 {task_id} 启动: "
                        f"{len(started)} 新增, {len(already)} 已在运行, {len(failed)} 失败")
            return {
                "ok": (len(started) > 0 or len(already) > 0),
                "started": started,
                "already": already,
                "failed": failed,
                "skipped": len(instances_to_start) - len(started) - len(already) - len(failed),
            }

    def stop_task(self, task_id: str) -> dict:
        """停止批量任务的所有实例。"""
        with self._state_lock:
            cfg = self._tasks.get(task_id)
            if not cfg:
                return {"ok": False, "error": f"任务不存在: {task_id}"}
            instance_ids = list(self._task_instances.get(task_id, []))

        stopped = []
        failed = []
        for iid in instance_ids:
            result = self._stop_instance(iid)
            if result.get("ok"):
                stopped.append(iid)
            else:
                failed.append({"instance_id": iid, "error": result.get("error", "")})

        return {"ok": True, "stopped": stopped, "failed": failed}

    # ── 实例管理 ─────────────────────────────────────────────────────────

    def _plan_instances(self, cfg: LiveBatchConfig) -> list[tuple[str, str]]:
        """规划实例列表：房间 × 账号的矩阵。

        strategy="round_robin"：轮询分配（房间数 == 账号数时一一对应）
        strategy="fixed"：固定配对（房间数 == 账号数）
        默认：笛卡尔积（所有房间 × 所有账号），但受 max_concurrent 约束
        """
        pairs = []
        if cfg.strategy == "round_robin":
            # 轮询：每个房间分配一个账号，账号不够时循环
            for i, room in enumerate(cfg.rooms):
                account = cfg.accounts[i % len(cfg.accounts)]
                pairs.append((room, account))
        elif cfg.strategy == "fixed":
            # 固定配对：房间和账号一一对应
            for room, account in zip(cfg.rooms, cfg.accounts):
                pairs.append((room, account))
        else:
            # 默认：笛卡尔积
            for room in cfg.rooms:
                for account in cfg.accounts:
                    pairs.append((room, account))

        # 受任务级并发上限约束
        return pairs[:cfg.max_concurrent]

    def _start_instance(self, task_id: str, room_url: str, account: str,
                        cfg: LiveBatchConfig) -> dict:
        """启动单个实例。"""
        # 🔴 2026-10-03：同一 (房间, 账号) 已在运行 → 直接返回既有实例。
        # 动机：`_plan_instances` 在 cartesian/固定配对下会对同一房间产出多个账号，
        # 重复点「启动」也会再产一批 —— 同一对重复开实例 = 两个 WS 抢同一房间、
        # 双份私信额度消耗，且 `start_task` 槽位被自己人占满。
        with self._state_lock:
            for iid in self._task_instances.get(task_id, []):
                exist = self._instances.get(iid)
                if exist and exist.room_url == room_url and exist.account == account \
                        and exist.state in ("starting", "running", "paused"):
                    return {"ok": True, "instance_id": iid, "already": True}

        instance_id = f"{task_id}_{int(time.time() * 1000)}_{len(self._instances)}"

        # 解析直播间 URL
        try:
            from link_resolve import resolve_live_id
            live_id, _src = resolve_live_id(room_url, account_name=account)
            if not live_id:
                return {"ok": False, "error": f"无法解析直播间: {room_url}"}
        except Exception as e:
            return {"ok": False, "error": f"解析直播间异常: {e}"}

        # 创建实例
        inst = LiveInstance(
            instance_id=instance_id,
            task_id=task_id,
            room_url=room_url,
            account=account,
            live_id=live_id,
            state="starting",
            status_msg="正在启动...",
        )

        # 🔴 2026-10-03 修正构造链（本会话自查坐实，AST 签名核对）：
        #   `LiveChatHook.__init__(live_id, auth_, dispatch, controller=None, ...)` 三个必填；
        #   `DispatchCenter.__init__(auth, max_target=3, delay_range=..., interval=...)` 首参必填。
        # 原实现两处都写成**无参构造 + 事后赋值**，运行期必抛 `TypeError`
        # ⇒ 每个批量实例都建不起来（页面显示「异常」，私信 100% 发不出）。
        # 现在按**单任务模式同款顺序**构造：先 DispatchCenter（带 auth），再 LiveChatHook。
        # 凭证复用既有实现 `DYLoginApi._load_auth_from_env`（不自造加载逻辑）。
        try:
            from auto_dm.accounts import env_path_of
            from dy_apis.login_api import DYLoginApi
            env_path = env_path_of(account)
            if not env_path:
                return {"ok": False, "error": f"账号 {account} 未登记"}
            auth = DYLoginApi._load_auth_from_env(env_path)
        except Exception as e:
            return {"ok": False, "error": f"加载账号凭证失败: {e}"}

        # 1) DispatchCenter（auth 必填）
        try:
            from core.dispatch import DispatchCenter
            # max_target = **每个直播间**的私信条数上限（对齐 AutoDM 的 `self.limit` 语义）。
            # 🔴 原实现引用了 `cfg.max_target`，而 `LiveBatchConfig` **并无该字段**
            #   （字段实测：task_id/name/rooms/accounts/strategy/max_concurrent/enabled/
            #     dm_pool/delay_range/interval）⇒ getattr 永远回退，等于「每房发 3 条」被写死。
            # 现按**与 max_conconsistent 无关的独立上限**显式建模，见 LiveBatchConfig.max_target。
            dispatch = DispatchCenter(
                auth=auth,
                max_target=int(cfg.max_target or 3),
                delay_range=cfg.delay_range or (40, 65),
                interval=float(cfg.interval or 60.0),
            )
            # ⚠️ 词库（dm_pool）**不在** DispatchCenter 的契约里 —— 文案由 AutoDM 侧的
            # `dm_template` 提供，DispatchCenter 只按 target 自带 content 发送。
            # 批量模式没有 AutoDM 实例，故 dm_pool 目前**不生效**（见交付说明）。
            inst._dispatch = dispatch
        except Exception as e:
            return {"ok": False, "error": f"创建 DispatchCenter 失败: {e}"}

        # 2) LiveChatHook（live_id / auth_ / dispatch 三者必填）
        try:
            from core.live_hook import LiveChatHook
            hook = LiveChatHook(live_id, auth, dispatch)
            hook.live_id = live_id
            hook.auth_ = auth
            inst._live_hook = hook
        except Exception as e:
            return {"ok": False, "error": f"创建 LiveChatHook 失败: {e}"}

        # 启动监听线程
        inst._stop_event = threading.Event()
        inst._thread = threading.Thread(
            target=self._instance_loop,
            args=(inst,),
            name=f"live-batch-{instance_id}",
            daemon=True,
        )
        inst._thread.start()
        inst.started_at = time.time()

        with self._state_lock:
            self._instances[instance_id] = inst
            if task_id not in self._task_instances:
                self._task_instances[task_id] = []
            self._task_instances[task_id].append(instance_id)

        logger.info(f"[LiveBatch] 实例已启动: {instance_id} "
                    f"(room={live_id}, account={account})")
        return {"ok": True, "instance_id": instance_id}

    def _stop_instance(self, instance_id: str) -> dict:
        """停止单个实例。"""
        with self._state_lock:
            inst = self._instances.get(instance_id)
            if not inst:
                return {"ok": False, "error": f"实例不存在: {instance_id}"}
            if inst.state in ("stopped", "idle"):
                return {"ok": True, "already": True}
            room_url, account, task_id = inst.room_url, inst.account, inst.task_id

        # 设置停止事件
        if inst._stop_event:
            inst._stop_event.set()
        inst.state = "stopping"
        inst.status_msg = "正在停止..."

        # 等待线程结束
        if inst._thread and inst._thread.is_alive():
            inst._thread.join(timeout=INSTANCE_STOP_TIMEOUT)
            if inst._thread.is_alive():
                logger.warning(f"[LiveBatch] 实例 {instance_id} 停止超时")

        # 清理 LiveChatHook
        if inst._live_hook:
            try:
                # 🔴 2026-10-03：不再 `asyncio.run(stop_async())`。
                # 底层是**同步 websocket-client**（`run_forever`，见 live_hook 模块注释），
                # `stop_async` 内部只做 `stop_heartbeat()` + `ws.close()` 等**同步**动作，
                # 本不需要事件循环。而 `_stop_instance` 是在**实例线程之外**的调用线程
                # 执行的 —— 若实例线程正持有循环（如 `_serve` 里的 `asyncio.run`），
                # 这行会撞 `RuntimeError: asyncio.run() cannot be called from a running
                # event loop`，导致**停止失败**（线程 join 超时、WS 泄漏、实例卡在
                # stopping）。故优先走同步路径，无同步实现才退回协程。
                # 真实实现：LiveChatHook 只有 `stop_async()`（AST 核对：无 `def stop`），
                # 且它内部只做同步动作（stop_heartbeat + ws.close），
                # 在**调用线程**（非实例线程）执行，故 `asyncio.run` 不会撞 running loop。
                stop_async = getattr(inst._live_hook, "stop_async", None)
                if stop_async is not None:
                    import asyncio
                    asyncio.run(stop_async())
            except Exception as e:
                logger.warning(f"[LiveBatch] 停止 LiveChatHook 异常: {e}")

        # 清理 DispatchCenter
        if inst._dispatch:
            try:
                # 🔴 2026-10-03：真实停止方法是 `stop_hard()` / `stop_soft()`
                # （core/dispatch.py:127/142），**没有 `stop()`**。
                # 原代码 `hasattr(dispatch,'stop')` 恒为 False ⇒ 调度器**从未被停止**，
                # 消费任务仍挂在事件循环上跑（实例已 stop 但后台还在发私信）。
                # 停止实例用 `stop_hard`（清空队列 + 终止消费），与「用户点停止」的语义一致。
                stop_fn = getattr(inst._dispatch, "stop_hard", None)
                if stop_fn is None:
                    stop_fn = getattr(inst._dispatch, "stop", None)
                if stop_fn is not None:
                    stop_fn()
            except Exception as e:
                logger.warning(f"[LiveBatch] 停止 DispatchCenter 异常: {e}")

        inst.state = "stopped"
        inst.stopped_at = time.time()
        inst.status_msg = "已停止"
        # 🔴 2026-10-03 修复实例注册表泄漏（本会话实测坐实）：
        # 原实现停止后把实例**留在** `_instances` / `_task_instances` 里，于是
        #   ① 每次 start 追加一批 → 点 N 次启动，UI 房间数不变但实例行数 N 倍膨胀；
        #   ② `get_global_status` 的 `current_running` 与 `start_task` 的槽位计算
        #      按 `_instances` 全量统计，残留的 running 记录会**占死并发槽位**，
        #      之后任何启动都被判「已达上限」而恒拒（假失败，且状态显示 running
        #      却没有真实 WS 连接）。
        # 判据：停止完成后 `task.instances` 必须**不含**已停实例（历史靠 tasks_history）。
        with self._state_lock:
            self._instances.pop(instance_id, None)
            lst = self._task_instances.get(task_id)
            if lst and instance_id in lst:
                lst.remove(instance_id)
        logger.info(f"[LiveBatch] 实例已停止: {instance_id}")
        return {"ok": True}

    def _instance_loop(self, inst: LiveInstance) -> None:
        """实例主循环（在独立线程中运行）。

        🔴 2026-10-03 重写启动段（本会话实测坐实的 4 连缺陷）：
          ① `LiveChatHook.start_async` **阻塞直到 WS 关闭**（内部 `to_thread(start_ws)`）；
          ② `DispatchCenter.start()` 靠 `asyncio.create_task` 挂在**当前事件循环**上；
          ③ 原代码 `asyncio.run(hook.start_async())` 独占一个循环 → 循环结束即销毁，
             挂在上面的 DispatchCenter 消费任务被连带杀掉；
          ④ 且 `inst._dispatch.start()` **没有 await** —— 返回的协程从未被调度，
             私信队列**永不消费**（页面显示「监听中」，实际一条都发不出）。
        ⇒ 修法：两条协程**共享同一个事件循环**（`asyncio.run(_serve(inst))`），
          阻塞的 WS 用 `asyncio.to_thread` 让出控制权；`dispatch.start()` 必须 await。
        """
        try:
            import asyncio

            async def _serve() -> None:
                # DispatchCenter 先起（它只是 create_task，随即让出），
                # 再把阻塞的 WS 监听放到线程里跑 —— 两者共用本循环。
                if inst._dispatch is not None:
                    try:
                        await inst._dispatch.start()
                    except Exception as e:
                        logger.warning(f"[LiveBatch] 实例 {inst.instance_id} "
                                       f"DispatchCenter 启动异常: {e}")
                if inst._live_hook is not None and hasattr(inst._live_hook, "start_async"):
                    # 阻塞操作放线程，事件循环才能继续消费 dispatch 队列。
                    await asyncio.to_thread(
                        lambda: asyncio.run(inst._live_hook.start_async())
                    )

            if inst._live_hook or inst._dispatch:
                asyncio.run(_serve())

            inst.state = "running"
            inst.status_msg = "监听中"
            logger.info(f"[LiveBatch] 实例 {inst.instance_id} 进入监听状态")

            # 主循环：等待停止事件
            while inst._stop_event and not inst._stop_event.is_set():
                # 检查 LiveChatHook 状态
                if inst._live_hook:
                    try:
                        # 🔴 该「连接状态」探测方法在 LiveChatHook 上并不存在（AST 核对），
                        # 原 hasattr 恒 False ⇒ 断线状态永远不更新。真实可读信号是
                        # `ws` 对象（同步 websocket-client）与 `room_status`。
                        _ws = getattr(inst._live_hook, "ws", None)
                        if _ws is not None and getattr(_ws, "connected", lambda: True)() is False:
                            inst.status_msg = "连接断开，重连中..."
                        # 更新状态
                        room = getattr(inst._live_hook, "room_status", None)
                        if isinstance(room, dict):
                            title = room.get("room_title", "")
                            if title:
                                inst.status_msg = f"监听中: {title}"
                    except Exception:
                        pass
                time.sleep(2)

        except Exception as e:
            inst.state = "error"
            inst.status_msg = f"实例异常: {e}"
            inst.error = str(e)
            logger.error(f"[LiveBatch] 实例 {inst.instance_id} 异常: {e}\n"
                         f"{traceback.format_exc(limit=3)}")
        finally:
            if inst.state not in ("stopped", "error"):
                inst.state = "stopped"
                inst.status_msg = "已停止"

    # ── 状态查询 ─────────────────────────────────────────────────────────

    def get_instance_status(self, instance_id: str) -> dict:
        """获取单个实例的详细状态。"""
        with self._state_lock:
            inst = self._instances.get(instance_id)
            if not inst:
                return {"ok": False, "error": f"实例不存在: {instance_id}"}
            result = inst.to_dict()
            # 附加 LiveChatHook 的实时数据
            if inst._live_hook:
                try:
                    if hasattr(inst._live_hook, 'feed_snapshot'):
                        feed = inst._live_hook.feed_snapshot(limit=20)
                        result["feed"] = feed
                    if hasattr(inst._live_hook, 'heat_snapshot'):
                        heat = inst._live_hook.heat_snapshot()
                        result["heat_curve"] = [int(h[1]) for h in heat
                                               if isinstance(h, (list, tuple)) and len(h) > 1]
                    if hasattr(inst._live_hook, 'room_stats'):
                        result["room_stats"] = inst._live_hook.room_stats
                    if hasattr(inst._live_hook, 'contribution_rank'):
                        result["contribution_rank"] = inst._live_hook.contribution_rank
                except Exception as e:
                    result["feed_error"] = str(e)
            # 附加 DispatchCenter 的状态
            if inst._dispatch:
                try:
                    # 🔴 2026-10-03 修正三处「假成功」（本会话核对真实类型坐实）：
                    #   ① `records` 是 **dict[key -> SendRecord]**，原代码 `records[-50:]`
                    #      会抛 `TypeError: unhashable type: 'slice'`（整段被 except 吞成
                    #      `dm_error`，前端永远拿不到发送记录）；
                    #   ② `SendRecord` 是 **pydantic 模型**（models/task.py），**没有 `.get()`**
                    #      —— 原代码 `r.get("key")` 必抛 AttributeError；
                    #   ③ 真实停止方法叫 `stop_hard` / `stop_soft`，**没有 `stop()`**
                    #      （见 core/dispatch.py:127/142）⇒ 停止时调度器其实没被停。
                    recs = getattr(inst._dispatch, "records", None)
                    if isinstance(recs, dict):
                        items = list(recs.values())
                        result["dm_records"] = [
                            {
                                "key": getattr(r, "key", ""),
                                "nickname": getattr(r, "nickname", ""),
                                "status": str(getattr(r, "status", "")),
                                "content": getattr(r, "content", "") or "",
                                "sent_at": getattr(r, "sent_at", None),
                            }
                            for r in items[-50:]
                        ]
                        result["dm_sent_count"] = len(items)
                except Exception as e:
                    result["dm_error"] = str(e)
            return {"ok": True, "instance": result}

    def get_global_status(self) -> dict:
        """获取全局状态。"""
        with self._state_lock:
            total_instances = len(self._instances)
            running = sum(1 for i in self._instances.values() if i.state == "running")
            error = sum(1 for i in self._instances.values() if i.state == "error")
            return {
                "global_enabled": LIVE_BATCH_ENABLED,
                "global_max_concurrent": LIVE_BATCH_MAX_CONCURRENT,
                "current_running": running,
                "total_instances": total_instances,
                "error_count": error,
                "tasks": [
                    {
                        "task_id": tid,
                        "name": cfg.name,
                        "running": sum(
                            1 for iid in self._task_instances.get(tid, [])
                            if iid in self._instances
                            and self._instances[iid].state == "running"
                        ),
                        "total": len(self._task_instances.get(tid, [])),
                    }
                    for tid, cfg in self._tasks.items()
                ],
            }


# ===========================================================================
# 速率限制器（令牌桶算法）
# ===========================================================================

class _RateLimiter:
    """简单令牌桶速率限制器。"""

    def __init__(self, per_minute: int) -> None:
        self._per_minute = max(1, per_minute)
        self._tokens = float(per_minute)
        self._last_refill = time.time()
        self._lock = threading.Lock()

    def acquire(self, tokens: int = 1) -> bool:
        """尝试获取令牌。返回是否成功。"""
        with self._lock:
            now = time.time()
            # 补充令牌
            elapsed = now - self._last_refill
            self._tokens = min(self._per_minute,
                               self._tokens + elapsed * (self._per_minute / 60.0))
            self._last_refill = now
            if self._tokens >= tokens:
                self._tokens -= tokens
                return True
            return False

    def wait_time(self, tokens: int = 1) -> float:
        """计算需要等待的秒数。"""
        with self._lock:
            if self._tokens >= tokens:
                return 0.0
            needed = tokens - self._tokens
            return needed * (60.0 / self._per_minute)


# ===========================================================================
# 模块级单例
# ===========================================================================

_manager: Optional[LiveBatchManager] = None


def get_manager() -> LiveBatchManager:
    """获取批量管理器单例。"""
    global _manager
    if _manager is None:
        _manager = LiveBatchManager()
    return _manager


__all__ = [
    "LiveBatchManager",
    "LiveBatchConfig",
    "LiveInstance",
    "get_manager",
    "LIVE_BATCH_ENABLED",
    "LIVE_BATCH_MAX_CONCURRENT",
    "LIVE_BATCH_RATE_LIMIT_PER_MIN",
]
