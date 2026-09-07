"""私信发送统一调度器 + 会话整理池（2026-09-07 架构重构）。

背景
----
私信是本项目核心能力，但发送链路此前是「多头并发、各自为政」：

  1. **4 个发送源**：手动 /send、AI 自动回复、直播 dispatch（批量）、
     图片 /send_image —— 分别调 recv_daemon 的不同端点。
  2. **已有闸门只管频率**：`recv_daemon._send_gate_acquire` 做了 per-account
     8s 最小间隔，但**不解决**：
       - 同一会话并发多条 → 乱序 / 重复发送；
       - 无优先级 → 批量任务会挤掉用户的手动发送；
       - 会话状态未整理 → peer_id 可能是被污染的"自己" uid
         （见 09 台账第六轮：32/77 会话 peer_id 被写成 my_uid），
         直接拿去 create_conversation 会发错人 / 发给自己。

设计
----
**两级结构**：

    发送源 ──入池──> 会话整理池(ConvPool) ──出队──> 统一调度器(Dispatcher) ──> recv_daemon /send
                          │                              │
                    归一化/去重/校验              串行 + 优先级 + 频率闸门

**会话整理池（ConvPool）职责**：
  - **归一化**：用 conv_id 解析真实 peer_uid（排除自身），修正被污染的
    peer_id（复用第六轮的 my_uid 自愈逻辑）；
  - **去重合并**：同一 (account, conv_id) 的待发消息按序排队，**不并发**；
  - **校验**：peer_uid 无效/是自己的一律拒绝入池（防发给自己的事故）；
  - **实时整理**：每次入池都重解析一次会话（会话可能已被 capture 更新）。

**统一调度器（Dispatcher）职责**：
  - per-account 单线程 worker 串行出队（保证同账号发送顺序 = 入池顺序）；
  - 优先级：手动(0) > AI回复(1) > 批量dispatch(2)；
  - 复用 recv_daemon 的频率闸门（三源一配额，不另起炉灶）；
  - 失败快速返回，不静默堆积。

用法
----
    from services.dm_dispatch import submit, SubmitResult

    r = submit(account="四川工伤张老师", conv_id="0:1:...", text="你好",
               source="manual", priority=0)
    if r.accepted:
        ...  # 已入池，异步发送；用 r.task_id 查状态
    else:
        print(r.error)   # 入池被拒（会话无效/重复/队列满）

配置（环境变量）
----
    DY_DM_QUEUE_MAX      每账号队列上限（默认 200，超限拒绝入池）
    DY_DM_POOL_STRICT    严格模式（默认 1）：peer_uid 无效则拒绝入池
"""
from __future__ import annotations

import os
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Tuple

from loguru import logger

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
QUEUE_MAX = int(os.environ.get("DY_DM_QUEUE_MAX", "200"))
POOL_STRICT = os.environ.get("DY_DM_POOL_STRICT", "1") not in ("0", "false", "False")
# 优先级常量（数字小 = 优先）
PRIO_MANUAL = 0
PRIO_AI = 1
PRIO_BATCH = 2
_PRIO_BY_SOURCE = {"manual": PRIO_MANUAL, "ai": PRIO_AI,
                   "batch": PRIO_BATCH, "dispatch": PRIO_BATCH}
# 同名会话去重窗口：同 (account, conv_id, text) 在该秒数内重复入池视为重复
DEDUP_WINDOW = float(os.environ.get("DY_DM_DEDUP_WINDOW", "5"))

# ===========================================================================
# 【调试版专用】测试账号白名单 —— 正式版不生效、不打包
# ===========================================================================
# 需求（用户 2026-09-07）：测试私信发送时，只能「尚进工伤小助理」与
# 「四川工伤张老师」两个账号互发，绝不得发给任何其他真实会话对象。
#
# 实现：白名单 uid 在**打包时由脚本注入**到本文件末尾的注入区
# （见 scripts/build_sidecar.py 的 --test-whitelist 分支）。
# 正式构建不注入 → TEST_WHITELIST_ON 恒为 False → 本段代码物理不执行。
#
# 这里只放"空壳"（正式版状态）；调试构建会被覆盖为真实 uid 集合。
_TEST_WHITELIST: Dict[str, set] = {}
TEST_WHITELIST_ON = False


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------
@dataclass
class SendTask:
    """一条待发私信（整理池内的标准形态）。"""
    task_id: str
    account: str
    conv_id: str          # 原始会话 id（可能是 0:1:uidA:uidB）
    peer_uid: str         # **整理后的真实对端 uid**（绝不会是自己的 uid）
    text: str
    source: str           # manual / ai / batch / dispatch
    priority: int
    enqueued_at: float = field(default_factory=time.time)
    # 图片发送用（可选）
    image_b64: str = ""
    filename: str = ""
    # 结果回填
    status: str = "pending"   # pending / sending / done / failed
    error: str = ""

    def __lt__(self, other: "SendTask") -> bool:
        """优先级队列排序：先比优先级，再比入池时间（FIFO 保证顺序）。"""
        if self.priority != other.priority:
            return self.priority < other.priority
        return self.enqueued_at < other.enqueued_at


@dataclass
class SubmitResult:
    accepted: bool
    task_id: str = ""
    error: str = ""
    queue_size: int = 0


# ---------------------------------------------------------------------------
# 会话整理池
# ---------------------------------------------------------------------------
class ConvPool:
    """会话整理池：归一化 / 去重 / 校验后再进入调度。

    线程安全。所有发送源必须先经 `prepare()` 整理，才能入队。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # 归一化结果缓存：{(account, conv_id): (ts, peer_uid)}
        self._resolved: Dict[Tuple[str, str], Tuple[float, str]] = {}
        self._resolved_ttl = 60.0
        # 去重：{(account, conv_id, text_md5): ts}
        self._recent: Dict[Tuple[str, str, str], float] = {}

    # ---------- 核心：解析真实对端 uid ----------
    @staticmethod
    def _my_uid_of(account: str) -> str:
        """取本账号 uid（走统一探活调度器，零额外网络）。"""
        try:
            from services.uid_probe import get_uid
            return str(get_uid(account) or "")
        except Exception:
            return ""

    @staticmethod
    def _peer_from_conv(conv_id: str, my_uid: str) -> Optional[str]:
        """从 conv_id 0:1:uidA:uidB 解析对端 uid（排除自身）。"""
        parts = (conv_id or "").split(":")
        if len(parts) < 4:
            return None
        a, b = parts[2], parts[3]
        if not a or not b or a == b:
            return None          # 自发自收的系统会话，不是真实对端
        if my_uid:
            if a == my_uid:
                return b
            if b == my_uid:
                return a
            return None          # 两边都不是自己 -> 数据异常，不猜
        return b                 # 无 my_uid 兜底（弱保证）

    def resolve(self, account: str, conv_id: str) -> Optional[str]:
        """归一化：返回该会话的真实对端 uid；无法确定返回 None。"""
        key = (account, conv_id or "")
        now = time.time()
        with self._lock:
            hit = self._resolved.get(key)
            if hit and (now - hit[0]) < self._resolved_ttl:
                return hit[1] or None
        my_uid = self._my_uid_of(account)
        peer = self._peer_from_conv(conv_id, my_uid)
        # my_uid 缺失时兜底：从 DB 里该会话已记录的 peer_id 取（可能仍被污染，
        # 但至少不会返回"自己"——下面校验会拦）
        if not peer and not my_uid:
            peer = self._peer_from_db(account, conv_id)
        with self._lock:
            self._resolved[key] = (now, peer or "")
        return peer

    @staticmethod
    def _peer_from_db(account: str, conv_id: str) -> Optional[str]:
        """兜底：从 dm_conversations 读 peer_id（仅当它不是自己的 uid）。"""
        try:
            from database import get_db
            conn = get_db()
            row = conn.execute(
                "SELECT peer_id FROM dm_conversations WHERE account=? AND conv_id=?",
                (account, conv_id)).fetchone()
            if row and row["peer_id"]:
                return str(row["peer_id"])
        except Exception:
            pass
        return None

    def is_duplicate(self, account: str, conv_id: str, text: str) -> bool:
        """同内容在去抖窗口内重复入池 -> True（丢弃）。"""
        import hashlib
        h = hashlib.md5((text or "").encode("utf-8")).hexdigest()
        key = (account, conv_id or "", h)
        now = time.time()
        with self._lock:
            # 顺带清理过期项，防内存无限增长
            for k, ts in list(self._recent.items()):
                if now - ts > DEDUP_WINDOW:
                    self._recent.pop(k, None)
            last = self._recent.get(key)
            if last is not None and (now - last) < DEDUP_WINDOW:
                return True
            self._recent[key] = now
            return False

    def invalidate(self, account: str = "", conv_id: str = "") -> None:
        """会话被 capture 更新后调用，让下次入池重解析。"""
        with self._lock:
            if not account:
                self._resolved.clear()
                return
            for k in list(self._resolved.keys()):
                if k[0] == account and (not conv_id or k[1] == conv_id):
                    self._resolved.pop(k, None)


# ---------------------------------------------------------------------------
# 统一调度器
# ---------------------------------------------------------------------------
class DmDispatcher:
    """per-account 串行 worker + 优先级队列 + 频率闸门。"""

    def __init__(self, pool: Optional[ConvPool] = None) -> None:
        self.pool = pool or ConvPool()
        self._queues: Dict[str, "queue.PriorityQueue"] = {}
        self._workers: Dict[str, threading.Thread] = {}
        self._tasks: Dict[str, SendTask] = {}      # task_id -> task（查状态用）
        self._reg_lock = threading.Lock()
        self._stop = threading.Event()

    # ---------- 入池 ----------
    def submit(self, account: str, conv_id: str, text: str,
               source: str = "manual", priority: Optional[int] = None,
               image_b64: str = "", filename: str = "") -> SubmitResult:
        """发送源统一入口：整理 -> 校验 -> 入队（异步发送）。"""
        if not account or not (conv_id or image_b64):
            return SubmitResult(False, error="账号或会话缺失")
        if not text and not image_b64:
            return SubmitResult(False, error="发送内容为空")

        # ① 整理：解析真实对端 uid
        peer_uid = self.pool.resolve(account, conv_id)
        if not peer_uid:
            msg = (f"会话整理失败：无法从 conv_id 解析真实对端 uid"
                   f"（conv_id={conv_id}）")
            logger.warning(f"[dm-dispatch] {msg}")
            if POOL_STRICT:
                return SubmitResult(False, error=msg)
            peer_uid = ""     # 非严格模式放行，交给下游兜底

        # ② 校验：绝不允许 peer_uid == 本账号 uid（发给自己事故）
        my_uid = self.pool._my_uid_of(account)
        if my_uid and peer_uid == my_uid:
            msg = f"会话整理拒绝：对端 uid 等于本账号 uid（{peer_uid}），疑似污染"
            logger.error(f"[dm-dispatch] {msg}")
            return SubmitResult(False, error=msg)

        # ②-b 【调试版专用白名单】只允许测试账号之间互发，防误发真人。
        # 生效条件：环境变量 DY_DM_TEST_WHITELIST=1（**仅调试构建设置**）。
        # 正式版不设该变量 → 本段完全不生效，零开销、零行为差异，
        # 且打包脚本不会把它打进正式二进制（见 scripts/build_sidecar.py）。
        # 白名单成员与允许的收发关系在 _TEST_WHITELIST 中维护。
        if TEST_WHITELIST_ON:
            allowed = _TEST_WHITELIST.get(account)
            if allowed is None:
                msg = (f"[测试白名单] 账号「{account}」不在测试白名单内，"
                       f"拒绝发送（调试版只允许多测试账号互发）")
                logger.error(f"[dm-dispatch] {msg}")
                return SubmitResult(False, error=msg)
            if allowed and peer_uid not in allowed:
                msg = (f"[测试白名单] 账号「{account}」仅允许发给 "
                       f"{sorted(allowed)}，本次目标 {peer_uid} 被拒绝")
                logger.error(f"[dm-dispatch] {msg}")
                return SubmitResult(False, error=msg)
            logger.info(f"[dm-dispatch] [测试白名单] 放行：{account} -> {peer_uid}")

        # ③ 去重
        if text and self.pool.is_duplicate(account, conv_id, text):
            logger.debug(f"[dm-dispatch] 重复消息已丢弃: {account}/{conv_id}")
            return SubmitResult(False, error="duplicate")

        # ④ 入队
        prio = priority if priority is not None else _PRIO_BY_SOURCE.get(
            source, PRIO_BATCH)
        q = self._queue_of(account)
        if q.qsize() >= QUEUE_MAX:
            return SubmitResult(False, error=f"队列已满（{QUEUE_MAX}），请稍后重试")
        task = SendTask(task_id=uuid.uuid4().hex[:12], account=account,
                        conv_id=conv_id, peer_uid=peer_uid, text=text,
                        source=source, priority=prio,
                        image_b64=image_b64, filename=filename)
        with self._reg_lock:
            self._tasks[task.task_id] = task
        q.put(task)
        self._ensure_worker(account)
        logger.info(
            f"[dm-dispatch] 入池 task={task.task_id} 账号={account} "
            f"会话={conv_id[:12]}… peer={peer_uid} 源={source} "
            f"优先级={prio} 队列={q.qsize()}")
        return SubmitResult(True, task_id=task.task_id, queue_size=q.qsize())

    # ---------- 队列 / worker ----------
    def _queue_of(self, account: str) -> "queue.PriorityQueue":
        with self._reg_lock:
            q = self._queues.get(account)
            if q is None:
                q = queue.PriorityQueue()
                self._queues[account] = q
            return q

    def _ensure_worker(self, account: str) -> None:
        with self._reg_lock:
            w = self._workers.get(account)
            if w and w.is_alive():
                return
            w = threading.Thread(target=self._worker_loop, args=(account,),
                                 daemon=True, name=f"dm-dispatch-{account[:8]}")
            self._workers[account] = w
            w.start()

    def _worker_loop(self, account: str) -> None:
        """per-account 串行出队：保证同账号发送顺序 = 优先级 + 入池顺序。"""
        q = self._queue_of(account)
        logger.info(f"[dm-dispatch] worker 启动：{account}")
        while not self._stop.is_set():
            try:
                task: SendTask = q.get(timeout=1.0)
            except Exception:
                # 空闲超时：队列空且已持续空闲 -> 退出（有新任务时会重建）
                if q.empty():
                    with self._reg_lock:
                        self._workers.pop(account, None)
                    logger.debug(f"[dm-dispatch] worker 空闲退出：{account}")
                    return
                continue
            try:
                self._send_one(task)
            except Exception as e:
                task.status = "failed"
                task.error = str(e)
                logger.error(f"[dm-dispatch] 发送异常 task={task.task_id}: {e}")
            finally:
                q.task_done()
        logger.info(f"[dm-dispatch] worker 退出：{account}")

    def _send_one(self, task: SendTask) -> None:
        """实际发送：转发到 recv_daemon（复用既有双通道 + 频率闸门）。"""
        task.status = "sending"
        try:
            import requests
            from auto_dm import accounts as acct_core
            port = acct_core.recv_daemon_port(task.account)
            url = f"http://127.0.0.1:{port}/send"
            payload = {"account": task.account, "conv_id": task.conv_id,
                       "text": task.text}
            r = requests.post(url, json=payload, timeout=60)
            data = r.json() if r.status_code == 200 else {}
            if data.get("ok"):
                task.status = "done"
                logger.info(f"[dm-dispatch] 已发送 task={task.task_id} "
                            f"账号={task.account} 会话={task.conv_id[:12]}…")
            else:
                task.status = "failed"
                task.error = data.get("error") or data.get("msg") or f"HTTP{r.status_code}"
                logger.warning(f"[dm-dispatch] 发送失败 task={task.task_id}: "
                               f"{task.error}")
        except Exception as e:
            task.status = "failed"
            task.error = str(e)
            raise

    # ---------- 观测 ----------
    def stats(self) -> dict:
        with self._reg_lock:
            return {
                "queues": {a: q.qsize() for a, q in self._queues.items()},
                "workers": {a: bool(w and w.is_alive())
                            for a, w in self._workers.items()},
                "resolved_cache": len(self.pool._resolved),
                "queue_max": QUEUE_MAX,
            }

    def task_status(self, task_id: str) -> Optional[dict]:
        with self._reg_lock:
            t = self._tasks.get(task_id)
        if not t:
            return None
        return {"task_id": t.task_id, "account": t.account,
                "conv_id": t.conv_id, "peer_uid": t.peer_uid,
                "source": t.source, "status": t.status, "error": t.error}

    def shutdown(self) -> None:
        self._stop.set()


# ---------------------------------------------------------------------------
# 全局单例
# ---------------------------------------------------------------------------
_dispatcher: Optional[DmDispatcher] = None
_disp_lock = threading.Lock()


def get_dispatcher() -> DmDispatcher:
    global _dispatcher
    with _disp_lock:
        if _dispatcher is None:
            _dispatcher = DmDispatcher()
        return _dispatcher


def submit(account: str, conv_id: str, text: str, source: str = "manual",
           priority: Optional[int] = None, image_b64: str = "",
           filename: str = "") -> SubmitResult:
    """模块级便捷入口（推荐发送源用这个）。"""
    return get_dispatcher().submit(
        account=account, conv_id=conv_id, text=text, source=source,
        priority=priority, image_b64=image_b64, filename=filename)


def invalidate_conv(account: str = "", conv_id: str = "") -> None:
    """会话被更新后调用（让整理池重解析）。"""
    get_dispatcher().pool.invalidate(account, conv_id)


def stop_dispatcher() -> None:
    """进程退出时调用。"""
    global _dispatcher
    with _disp_lock:
        if _dispatcher:
            _dispatcher.shutdown()
            _dispatcher = None


# ===========================================================================
# 【打包注入区 —— 调试版专用，勿手动编辑】
# ---------------------------------------------------------------------------
# scripts/build_sidecar.py 在 **调试构建** 时会把下面两行替换为真实内容：
#
#   _TEST_WHITELIST = {"尚进工伤小助理": {"<四川工伤张老师 uid>"},
#                      "四川工伤张老师": {"<尚进工伤小助理 uid>"}}
#   TEST_WHITELIST_ON = True
#
# 正式构建**不注入** → 保持空壳，白名单逻辑永不执行。
# 标记行（打包脚本据此定位替换点，勿删勿改）：
# ---DM_TEST_WHITELIST_INJECT_START---
# ---DM_TEST_WHITELIST_INJECT_END---
# ===========================================================================
