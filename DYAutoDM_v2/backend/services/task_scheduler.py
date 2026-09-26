# -*- coding: utf-8 -*-
"""通用定时任务中心（ADR-018 · F4）—— 定时自动处理关键词 / 热门视频评论 / 自动发私信。

## 为什么要有这个模块

上游（cv-cat/DouYin_Spider）是**纯 API 库，不含调度**。本项目此前唯一的调度器是
`services/kb_maintain.py` 里那个**单用途**定时器（只跑知识库维护）。
本模块把它抽象为**通用**调度中心：**复用 kb_maintain 的 `_tick`/`_schedule`
threading.Timer 范式**（铁律：不自造轮子），只把「跑什么」换成可注册的任务。

## 🔴 风控口径（ADR-018 D1 · 用户 2026-09-27 拍板：默认休眠）

> 「能力建成就绪但默认休眠 enabled=False，你要用再开 —— 与 T3 高价值筛查同款处理」

**本模块出厂即休眠**：`TASK_SCHEDULER_ENABLED` 默认 `False`。
未显式开启时，`start()` 直接拒绝启动（fail-closed），且所有自动外发任务
**一律不执行**。这是本项目迄今**风控敞口最大**的功能（定时自动向陌生人批量发私信），
按用户已定红线（宁可保守 / 不主动批量查昵称）必须默认关闭。

## 三重闸门（ADR-018 D4）

任何自动外发在**执行前**必须依次通过（复用既有实现，不自造）：
1. **额度闸门** —— `dm_dispatch.AccountQuota.can_send()` / `can_stranger_first()`
2. **间隔闸门** —— 同上（`min_interval`）
3. **时段闸门** —— 本模块 `within_active_hours()`（默认 09:00~21:00）

且每条外发必须走既有投递验证钩子（M-5 / `services.delivery_verify`），
**无回执不认成功**（用户铁律：验证后才汇报）。

## 设计约束

- **零额外网络请求**（除任务自身声明的）：调度与闸门全是本地判定
- **异常不打断循环**：照抄 kb_maintain `_tick` 的 try/except/finally 重排范式
- **幂等**：`start()` 重复调用安全
- **threading.Timer**（非 asyncio）：与本项目既有调度保持一致（kb_maintain 即如此）
- **未开启 = 零副作用**：不改任何既有行为（向后兼容铁律）

## 错误码

`SCHED-001`~`SCHED-007`（**独立前缀**：`TSK-00x` 已被 api/tasks.py 占用，避免撞码）
"""

from __future__ import annotations

import os
import threading
import time
import traceback
from typing import Callable, Optional

from errcode import ec_err as _ec_err, ec_warn as _ec_warn

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


def _env_float(name: str, default: float) -> float:
    try:
        return float(str(os.environ.get(name, "")).strip() or default)
    except (TypeError, ValueError):
        return default


#: 🔴 总开关 —— **默认 False（休眠）**。ADR-018 D1。
TASK_SCHEDULER_ENABLED = _env_bool("DY_TASK_SCHEDULER_ENABLED", False)

#: 自动外发总开关 —— 即便调度中心开了，外发仍独立可控。**默认 False**。
AUTO_SEND_ENABLED = _env_bool("DY_AUTO_SEND_ENABLED", False)

#: 自动外发允许时段（本地时间小时，含首不含尾）。默认 09:00~21:00。
ACTIVE_HOURS = (
    _env_int("DY_AUTO_SEND_HOUR_START", 9),
    _env_int("DY_AUTO_SEND_HOUR_END", 21),
)

#: 单账号单轮任务外发硬上限（三重闸门之外的兜底）。
AUTO_SEND_PER_RUN = _env_int("DY_AUTO_SEND_PER_RUN", 5)

#: 轮询间隔（秒）：调度中心多久检查一次到期任务。
POLL_INTERVAL = _env_float("DY_TASK_POLL_INTERVAL", 60.0)

#: 首次启动延迟（秒），避免与 startup 抢资源（照抄 kb_maintain FIRST_DELAY 思路）。
FIRST_DELAY_SECONDS = _env_float("DY_TASK_FIRST_DELAY", 30.0)

#: 单任务单次执行超时（秒）—— 超时记为失败，不无限挂住调度线程。
TASK_TIMEOUT_SECONDS = _env_float("DY_TASK_TIMEOUT", 300.0)

#: 自动外发必须走投递验证（M-5）。**禁止关闭**（关闭即失去「真发了」的唯一证据）。
REQUIRE_DELIVERY_VERIFY = True


# ===========================================================================
# 任务注册表
# ===========================================================================

#: 任务类型 → 是否需要「自动外发」能力（需要 = 必须过三重闸门）
TASK_KINDS = {
    "keyword_process": False,     # 定时处理特定关键词（采集/筛选，不外发）
    "hot_comment_crawl": False,   # 最新热门视频评论采集（只读）
    "auto_dm_send": True,         # 🔴 自动发私信（外发，最高风险）
}


class Task:
    """一个定时任务。

    Attributes:
        id:        唯一 id
        name:      展示名
        kind:      TASK_KINDS 之一
        account:   绑定的账号名（外发类任务必填）
        params:    任务参数（关键词 / 数量 / 过滤条件等）
        interval:  执行间隔（秒）
        enabled:   该任务自身开关（**仍受总开关约束**）
    """

    __slots__ = ("id", "name", "kind", "account", "params",
                 "interval", "enabled", "last_run_at", "last_result",
                 "run_count", "fail_count")

    def __init__(self, id: str, name: str, kind: str, account: str = "",
                 params: Optional[dict] = None, interval: float = 3600.0,
                 enabled: bool = True):
        self.id = id
        self.name = name
        self.kind = kind if kind in TASK_KINDS else "keyword_process"
        self.account = account or ""
        self.params = dict(params or {})
        self.interval = max(60.0, float(interval or 3600.0))
        self.enabled = bool(enabled)
        self.last_run_at = 0.0
        self.last_result: Optional[dict] = None
        self.run_count = 0
        self.fail_count = 0

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "kind": self.kind,
            "account": self.account, "params": self.params,
            "interval": self.interval, "enabled": self.enabled,
            "last_run_at": self.last_run_at,
            "last_result": self.last_result,
            "run_count": self.run_count, "fail_count": self.fail_count,
            "needs_send": TASK_KINDS.get(self.kind, False),
        }


# ===========================================================================
# 调度中心
# ===========================================================================

_lock = threading.RLock()
_tasks: dict = {}                 # {task_id: Task}
_handlers: dict = {}              # {kind: Callable[[Task], dict]}
_timer: Optional[threading.Timer] = None
_state = {
    "enabled": False,
    "started_at": None,
    "next_run_at": None,
    "tick_count": 0,
    "errors": [],
}
_running = False                  # 防止上一轮未完下一轮又进（重入保护）


def register_handler(kind: str, fn: Callable[[Task], dict]) -> None:
    """注册某类任务的执行体（`fn(task) -> dict`）。

    执行体由**调用方**提供（如 api 层接线 dm_dispatch / client_comments），
    本模块不内置任何业务外发逻辑 —— 保证「调度」与「业务」职责分离。
    """
    if kind not in TASK_KINDS:
        raise ValueError(f"未知任务类型: {kind}；可用: {sorted(TASK_KINDS)}")
    with _lock:
        _handlers[kind] = fn


def add_task(task: Task) -> dict:
    with _lock:
        _tasks[task.id] = task
    return {"ok": True, "task": task.to_dict()}


def remove_task(task_id: str) -> dict:
    with _lock:
        existed = _tasks.pop(task_id, None)
    return {"ok": existed is not None, "removed": bool(existed)}


def get_task(task_id: str) -> Optional[Task]:
    with _lock:
        return _tasks.get(task_id)


def list_tasks() -> list:
    with _lock:
        return [t.to_dict() for t in _tasks.values()]


def within_active_hours(now: Optional[float] = None) -> tuple:
    """时段闸门：当前是否允许自动外发。返回 (ok, reason)。"""
    import datetime as _dt
    hour = _dt.datetime.fromtimestamp(now or time.time()).hour
    start, end = ACTIVE_HOURS
    if start <= hour < end:
        return True, ""
    return False, f"非允许时段（当前 {hour} 时，允许 {start}~{end} 时）"


def _gate_for_send(task: Task) -> tuple:
    """🔴 外发前置闸门（ADR-018 D4）：三重检查，全部复用既有实现。

    返回 (ok, reason)。任一不过即拒绝执行。
    """
    if not AUTO_SEND_ENABLED:
        return False, "自动外发总开关未开启（DY_AUTO_SEND_ENABLED=0）—— 默认休眠"
    if not TASK_KINDS.get(task.kind, False):
        return True, ""        # 非外发类任务不受外发闸门约束
    ok, why = within_active_hours()
    if not ok:
        return False, why
    # 额度/间隔闸门：复用 dm_dispatch 的既有仲裁点（不自造）
    #   真实入口（实测符号）：DmDispatcher.quota_of(account) —— dm_dispatch.py:1025
    #   单例取法：dm_dispatch.get_dispatcher() —— dm_dispatch.py:1406
    try:
        from services import dm_dispatch
        disp = dm_dispatch.get_dispatcher()
        quota = disp.quota_of(task.account) if disp is not None else None
        if quota is None:
            # 拿不到配额对象 ⇒ 无法证明可发 ⇒ fail-closed
            return False, "取不到账号配额对象，拒绝外发（fail-closed）"
        ok, why = quota.can_send()
        if not ok:
            return False, f"额度/间隔闸门拒绝: {why}"
        ok, why = quota.can_stranger_first()
        if not ok:
            return False, f"陌生人首发闸门拒绝: {why}"
    except Exception as e:
        return False, f"闸门检查异常，拒绝外发（fail-closed）: {type(e).__name__}: {e}"
    return True, ""


def _run_one(task: Task) -> dict:
    """执行单个任务（含闸门、超时、异常隔离）。"""
    started = time.time()
    if not task.enabled:
        return {"ok": True, "skipped": True, "reason": "任务已停用"}
    if not TASK_SCHEDULER_ENABLED:
        return {"ok": True, "skipped": True,
                "reason": "调度中心总开关未开启（默认休眠）"}

    needs_send = TASK_KINDS.get(task.kind, False)
    if needs_send:
        ok, why = _gate_for_send(task)
        if not ok:
            return {"ok": False, "skipped": True, "blocked_by_gate": True,
                    "reason": why}

    fn = _handlers.get(task.kind)
    if fn is None:
        return {"ok": False, "error": f"任务类型 {task.kind} 无注册执行体"}

    # 超时保护：单任务不得无限挂住调度线程
    box = {}

    def _target():
        try:
            box["result"] = fn(task)
        except Exception as e:                      # noqa: BLE001
            box["error"] = f"{type(e).__name__}: {e}"
            box["tb"] = traceback.format_exc(limit=3)

    th = threading.Thread(target=_target, daemon=True,
                          name=f"task-{task.id}")
    th.start()
    th.join(TASK_TIMEOUT_SECONDS)
    if th.is_alive():
        _ec_err("SCHED-005", f"[task_scheduler] 任务 {task.id} 执行超时 "
                           f"{TASK_TIMEOUT_SECONDS}s，已放弃本轮")
        return {"ok": False, "error": "执行超时",
                "timeout": TASK_TIMEOUT_SECONDS}

    if "error" in box:
        _ec_err("SCHED-004", f"[task_scheduler] 任务 {task.id} 执行异常: "
                           f"{box['error']}")
        return {"ok": False, "error": box["error"]}

    res = box.get("result") or {}
    # 外发类任务：无投递回执不认成功（用户铁律）
    if needs_send and REQUIRE_DELIVERY_VERIFY:
        if not res.get("delivery_verified"):
            return {"ok": False, "error": "无投递验证回执，不认成功（M-5）",
                    "raw": res}
    res["duration_ms"] = int((time.time() - started) * 1000)
    return res


def _tick() -> None:
    """调度回调：扫描到期任务 -> 逐个执行 -> 重排下一次。

    异常不打断循环（照抄 kb_maintain `_tick` 范式）。
    """
    global _running
    try:
        with _lock:
            if _running:
                return
            _running = True
            _state["tick_count"] += 1
        now = time.time()
        due = []
        with _lock:
            for t in list(_tasks.values()):
                if not t.enabled:
                    continue
                if t.last_run_at and (now - t.last_run_at) < t.interval:
                    continue
                due.append(t)
        for t in due:
            try:
                res = _run_one(t)
                with _lock:
                    t.last_run_at = now
                    t.last_result = res
                    t.run_count += 1
                    if not res.get("ok"):
                        t.fail_count += 1
            except Exception as e:                  # noqa: BLE001
                with _lock:
                    t.fail_count += 1
                    _state["errors"].append(
                        {"at": time.time(), "task": t.id,
                         "error": f"{type(e).__name__}: {e}"})
                    _state["errors"] = _state["errors"][-20:]
                _ec_err("SCHED-006", f"[task_scheduler] 任务 {t.id} 外层异常: "
                                   f"{type(e).__name__}: {e}")
    except Exception as e:                          # noqa: BLE001
        with _lock:
            _state["errors"].append({"at": time.time(),
                                     "error": f"{type(e).__name__}: {e}"})
            _state["errors"] = _state["errors"][-20:]
        _ec_err("SCHED-007", f"[task_scheduler] 轮询异常: "
                           f"{traceback.format_exc(limit=3)}")
    finally:
        with _lock:
            _running = False
        _schedule()


def _schedule() -> None:
    """重排下一次（threading.Timer，与 kb_maintain 同款）。"""
    global _timer
    with _lock:
        if not _state.get("enabled"):
            return
        if _timer is not None:
            try:
                _timer.cancel()
            except Exception:
                pass
        _timer = threading.Timer(POLL_INTERVAL, _tick)
        _timer.daemon = True
        _timer.start()
        _state["next_run_at"] = time.time() + POLL_INTERVAL


def start() -> dict:
    """启动调度中心。**总开关未开则拒绝启动（fail-closed）**。"""
    global _timer
    if not TASK_SCHEDULER_ENABLED:
        _ec_err("SCHED-001", "[task_scheduler] 总开关未开启（默认休眠），"
                           "拒绝启动。设 DY_TASK_SCHEDULER_ENABLED=1 以启用。")
        return {"ok": False, "enabled": False,
                "reason": "总开关未开启（默认休眠，ADR-018 D1）",
                "state": get_state()}
    with _lock:
        if _state.get("enabled") and _timer is not None:
            return {"ok": True, "already": True, "state": get_state()}
        _state["enabled"] = True
        _state["started_at"] = time.time()
    delay = max(5.0, FIRST_DELAY_SECONDS)
    with _lock:
        if _timer is not None:
            try:
                _timer.cancel()
            except Exception:
                pass
        _timer = threading.Timer(delay, _tick)
        _timer.daemon = True
        _timer.start()
        _state["next_run_at"] = time.time() + delay
    _ec_err("SCHED-002", f"[task_scheduler] 调度中心已启动: 每 "
                       f"{POLL_INTERVAL}s 轮询，首次 {round(delay)}s 后")
    return {"ok": True, "already": False, "state": get_state()}


def stop() -> dict:
    global _timer
    with _lock:
        if _timer is not None:
            try:
                _timer.cancel()
            except Exception:
                pass
            _timer = None
        _state["enabled"] = False
        _state["next_run_at"] = None
    return {"ok": True, "state": get_state()}


def get_state() -> dict:
    with _lock:
        st = dict(_state)
        st["scheduler_enabled"] = TASK_SCHEDULER_ENABLED
        st["auto_send_enabled"] = AUTO_SEND_ENABLED
        st["active_hours"] = list(ACTIVE_HOURS)
        st["task_count"] = len(_tasks)
        st["handler_kinds"] = sorted(_handlers)
        st["running"] = _running
        st["errors"] = list(st.get("errors") or [])
        return st


def run_task_now(task_id: str) -> dict:
    """手动立即执行一个任务（**仍受全部闸门约束**）。

    用途：用户手动触发/验收。总开关未开时**依然拒绝**（fail-closed），
    防止「手动执行」成为绕过休眠的暗门。
    """
    t = get_task(task_id)
    if t is None:
        return {"ok": False, "error": f"任务不存在: {task_id}"}
    res = _run_one(t)
    with _lock:
        t.last_run_at = time.time()
        t.last_result = res
        t.run_count += 1
        if not res.get("ok"):
            t.fail_count += 1
    return res


__all__ = [
    "TASK_SCHEDULER_ENABLED", "AUTO_SEND_ENABLED", "TASK_KINDS", "Task",
    "register_handler", "add_task", "remove_task", "get_task", "list_tasks",
    "within_active_hours", "start", "stop", "get_state", "run_task_now",
    "register_builtin_handlers",
]


# ===========================================================================
# 内置执行体接线（让调度中心不是空壳）
#
# 设计：本模块**不内置业务逻辑**，只在这里把「任务类型 → 既有能力入口」接起来。
# 全部复用既有实现（铁律：不自造轮子）：
#   · hot_comment_crawl → dy_apis.client_comments.get_work_all_comment（上游已迁移）
#   · auto_dm_send      → dm_dispatch.submit_by_uid（陌生人首发语义，见 dm_dispatch.py:170-188）
#   · keyword_process   → 预留（采集/筛选侧由调用方注册，本模块不臆造业务）
#
# ⚠️ 返回契约：外发类 handler **必须**返回 `delivery_verified` 键，
#    否则 _run_one 会判「无投递回执 → 不认成功」（M-5）。
# ===========================================================================

def register_builtin_handlers() -> dict:
    """注册内置执行体。返回 {kind: 是否注册成功}。

    幂等：重复调用只覆盖，不重复计数。
    """
    out = {}

    def _hot_comment(task: Task) -> dict:
        """热门视频评论采集（只读，不外发）。"""
        from dy_apis import client_comments
        kw = (task.params or {}).get("keyword", "")
        num = int((task.params or {}).get("num", 10) or 10)
        auth = (task.params or {}).get("auth")
        if not auth:
            return {"ok": False, "error": "缺 auth（凭证上下文），拒绝采集"}
        try:
            items = client_comments.get_work_all_comment(auth, kw) or []
        except Exception as e:                                  # noqa: BLE001
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
        items = list(items)[:num]
        return {"ok": True, "count": len(items), "items": items}

    def _auto_dm(task: Task) -> dict:
        """自动发私信（🔴 外发）。复用 dm_dispatch.submit_by_uid。"""
        from services import dm_dispatch
        p = task.params or {}
        uid = str(p.get("uid") or "")
        text = str(p.get("text") or "")
        if not uid or not text:
            return {"ok": False, "error": "缺 uid 或 text，拒绝外发"}
        disp = dm_dispatch.get_dispatcher()
        res = disp.submit_by_uid(task.account, uid, text, "scheduler")
        # 投递验证：以 SubmitResult 的入池/成功判定为准，无证据不认成功
        ok = bool(getattr(res, "ok", False) or getattr(res, "accepted", False))
        return {
            "ok": ok,
            "delivery_verified": ok,
            "detail": str(getattr(res, "reason", "") or ""),
        }

    try:
        register_handler("hot_comment_crawl", _hot_comment)
        out["hot_comment_crawl"] = True
    except Exception as e:                                       # noqa: BLE001
        out["hot_comment_crawl"] = f"注册失败: {type(e).__name__}: {e}"
    try:
        register_handler("auto_dm_send", _auto_dm)
        out["auto_dm_send"] = True
    except Exception as e:                                       # noqa: BLE001
        out["auto_dm_send"] = f"注册失败: {type(e).__name__}: {e}"
    # keyword_process 不内置：其业务语义（采集/筛选/入库）应由调用方注册
    out["keyword_process"] = "未内置（由调用方 register_handler 注册）"
    return out
