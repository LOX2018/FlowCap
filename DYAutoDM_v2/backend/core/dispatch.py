# coding=utf-8
"""中央调度：聚合双路来源（弹幕 + 中控台采集），去重后触发私信。

迁移自 DY_Spider_base/auto_dm/core.py。

重构变化（相对旧版）：
- threading + Condition → asyncio.PriorityQueue + asyncio.Task（适配 FastAPI 异步栈）
- 三套停止标志（hard_stopped/no_new/paused）→ 由 AutoDM 的 EngineState 统一表达：
    * EngineState.STOPPING + clear_queue=True  = 旧版 hard_stopped
    * EngineState.STOPPING + clear_queue=False = 旧版 no_new（软停止，存量发完）
    * EngineState.PAUSED                        = 旧版 paused
- records.status 中文字符串 → RecordStatus 枚举
- 失败原因放 reason 字段（不再拼到 status 里，前端不再字符串比较）
- _do_send 用 asyncio.to_thread 包装同步的 send_target

延迟发送设计（保留旧版逻辑）：
  - 捕获到评论/弹幕后，立即做去重并入队（记录捕获时刻 capture_ts）；
  - 后台任务按 capture_ts + SEND_DELAY_SEC 到点后才真正调用 send_target；
  - 这样"看到弹幕→过一会再私信"的节奏更拟真，降低机器批量感与风控识别。
"""
from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from loguru import logger

from models.enums import RecordStatus
from models.task import SendRecord
from core.sender import send_target_async


@dataclass(order=True)
class _QueueItem:
    """队列项：按 send_at 排序"""

    send_at: float
    key: str = field(compare=False)
    target: dict = field(compare=False)


class DispatchCenter:
    """延迟发送队列（asyncio 版）

    去重：user_id / sec_uid / nickname 三套集合
    延迟：send_at = now + delay_range 内随机
    """

    def __init__(
        self,
        auth: Any,
        max_target: int = 3,
        delay_range: tuple[int, int] = (40, 65),
        interval: float = 60.0,
        enable_send: bool = True,
        pick_dm_message: Optional[Any] = None,
    ) -> None:
        self.auth = auth
        self.max_target = int(max_target)
        self.delay_range = delay_range
        self.interval = interval
        self.enable_send = enable_send
        # pick_dm_message: 可调用对象，返回一条私信文案；为 None 时由调用方传入 content
        self.pick_dm_message = pick_dm_message

        # 去重集合
        self.sent_ids: set[str] = set()
        self.sent_sec: set[str] = set()
        self.sent_names: set[str] = set()
        self.sent: set[str] = set()  # 兼容旧字段

        self.count = 0
        self.reached_limit = False

        # 状态控制（由 AutoDM 的 EngineState 间接驱动，这里只保留运行期标志）
        self._clear_queue = False  # True=硬停止（清队列），False=软停止（存量发完）
        self._accept_new = True    # False=软停止后不再接收新目标
        self._paused = False       # 暂停
        self._stopped = False      # 完全停止（_loop 退出）

        self._queue: asyncio.PriorityQueue[_QueueItem] = asyncio.PriorityQueue()
        self.pending: dict[str, dict] = {}  # key -> target（延迟窗口内尚未发送的）
        self.records: dict[str, SendRecord] = {}  # key -> SendRecord（按 key 索引，便于更新）
        self._records_order: list[str] = []  # 保持插入顺序（前端展示用）

        self._task: Optional[asyncio.Task] = None
        self.on_idle: Optional[Any] = None  # 回调：队列自然发空时触发

    # ------------------------------------------------------------------
    # 状态控制
    # ------------------------------------------------------------------
    def stop_hard(self) -> None:
        """硬停止：清空队列，之后 submit 一律忽略，_do_send 也拦截。"""
        self._clear_queue = True
        self._accept_new = False
        self._stopped = True
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        self.pending.clear()
        logger.warning("[调度] 硬停止：已清空待发队列，不再发送任何私信")

    def stop_soft(self) -> None:
        """软停止：停止接收新目标，但保留已入队的延迟私信继续发完。"""
        self._accept_new = False
        logger.info(
            f"[调度] 软停止（保留延迟队列）：已停止接收新目标，"
            f"已捕获未发的待发私信将在延迟后继续发出"
        )

    def pause(self) -> None:
        self._paused = True
        logger.info("[调度] 已暂停：停止监听采集与私信发送")

    def resume(self) -> None:
        self._paused = False
        logger.info("[调度] 已继续：恢复私信发送")

    def set_max_target(self, n: int) -> None:
        self.max_target = int(n)
        if self.count < self.max_target:
            self.reached_limit = False
        logger.info(f"[调度] 发送上限调整为 {self.max_target}（已发 {self.count}）")

    def queue_size(self) -> int:
        return self._queue.qsize()

    # ------------------------------------------------------------------
    # 去重
    # ------------------------------------------------------------------
    def _dedup_key(self, target: dict) -> str:
        return str(
            target.get("user_id") or target.get("sec_uid") or target.get("nickname") or ""
        )

    def _already_seen(self, target: dict) -> bool:
        uid = target.get("user_id")
        sec = target.get("sec_uid")
        name = target.get("nickname")
        if uid and str(uid) in self.sent_ids:
            return True
        if sec and str(sec) in self.sent_sec:
            return True
        if name and name in self.sent_names:
            return True
        if self._dedup_key(target) in self.sent:
            return True
        return False

    def _register_seen(self, target: dict) -> None:
        uid = target.get("user_id")
        sec = target.get("sec_uid")
        name = target.get("nickname")
        if uid:
            self.sent_ids.add(str(uid))
        if sec:
            self.sent_sec.add(str(sec))
        if name:
            self.sent_names.add(name)
        self.sent.add(self._dedup_key(target))

    def _ensure_record(self, key: str, target: dict) -> None:
        """确保 key 对应一条「已捕获」记录存在（幂等）。

        原 submit 的 records 写入逻辑提取到此，并在所有拒绝点之前调用，
        杜绝「捕获到了但连记录都没有」的漏发（停止态/去重/暂停/缺字段均不丢）。
        """
        if key in self.records:
            return
        rec = SendRecord(
            key=key,
            uid=str(target.get("user_id") or ""),
            nickname=target.get("nickname") or "",
            sec_uid=target.get("sec_uid"),
            status=RecordStatus.CAPTURED if self.enable_send else RecordStatus.SKIPPED,
            captured_at=time.time(),
            comment=target.get("comment") or "",
        )
        self.records[key] = rec
        self._records_order.append(key)

    # ------------------------------------------------------------------
    # 提交目标
    # ------------------------------------------------------------------
    def submit(self, target: dict) -> bool:
        """提交一个私信目标。返回是否入队（去重/停止/上限时返回 False）。

        关键不变量：只要 key 有效（有 nickname/user_id/sec_uid 任一标识），
        无论是否真发、是否去重、是否处于停止态，都先在本方法内写一条
        CAPTURED 记录（见下方 _ensure_record）。这样可彻底消除「部分目标
        连发送记录都没有」的漏发——调用方（live_hook / web_probe / api）在
        停止后或去重时丢弃的目标，至少能在统计里看到「已捕获（未发）」。
        """
        if not target.get("capture_ts"):
            target["capture_ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
        key = self._dedup_key(target)
        if not key:
            return False

        # 先确保有「已捕获」记录（即使后面因停止/去重/暂停被拒，也不丢数据）
        self._ensure_record(key, target)

        # 停止/暂停态：拒绝真发，但记录已保留（用户可在统计里看到「已捕获未发」）
        if self._clear_queue or not self._accept_new:
            logger.debug("[调度] 已停止接收新目标，忽略提交（已保留捕获记录）")
            return False
        if self._paused:
            logger.debug("[调度] 已暂停，忽略提交（已保留捕获记录）")
            return False

        if self._already_seen(target) or key in self.pending:
            return False
        if not self.enable_send:
            logger.debug(f"[采集]（仅采集模式）{target.get('nickname')}: {target}")
            return False
        if self.reached_limit:
            return False

        self.pending[key] = target
        self._register_seen(target)

        # 延迟抖动
        lo, hi = int(self.delay_range[0]), int(self.delay_range[1])
        delay = random.randint(lo, hi) if hi > lo else lo
        send_at = time.time() + delay
        self._queue.put_nowait(_QueueItem(send_at=send_at, key=key, target=target))
        logger.info(
            f"[调度] 已捕获「{target.get('nickname')}」评论，"
            f"将在 {delay}s 后发送私信（队列长度={self._queue.qsize()}）"
        )
        return True

    # ------------------------------------------------------------------
    # 后台消费循环
    # ------------------------------------------------------------------
    async def start(self) -> None:
        """启动后台消费任务"""
        if self._task is None or self._task.done():
            self._stopped = False
            self._task = asyncio.create_task(self._loop())

    async def _loop(self) -> None:
        """消费循环：到点后真正发送（捕获时刻 + delay）"""
        while not self._stopped:
            # 暂停态：不消费，等待恢复
            if self._paused:
                await asyncio.sleep(0.5)
                continue
            # 已达上限：清空待发队列，不再发送
            if self.reached_limit:
                while not self._queue.empty():
                    try:
                        self._queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                self.pending.clear()
                await asyncio.sleep(0.5)
                continue

            try:
                item = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                # 队列空：触发 on_idle（只触发一次）
                if self._queue.empty() and not self.pending and self.on_idle:
                    try:
                        await self.on_idle() if asyncio.iscoroutinefunction(self.on_idle) else self.on_idle()
                    except Exception as e:
                        logger.warning(f"[调度] on_idle 异常: {e}")
                    self.on_idle = None
                continue

            # 等到 send_at 时刻
            now = time.time()
            if item.send_at > now:
                await asyncio.sleep(item.send_at - now)

            if self._stopped or self._clear_queue:
                logger.warning(f"[调度] 停止中，丢弃待发私信「{item.target.get('nickname')}」")
                continue

            await self._do_send(item.key, item.target)
            await asyncio.sleep(self.interval)

    async def _do_send(self, key: str, target: dict) -> None:
        """实际发送（asyncio.to_thread 包装同步 send_target）

        成功 → status=SENT
        失败 → status=FAIL, reason=具体原因（不再拼到 status 里）
        """
        rec = self.records.get(key)
        if rec is None:
            return

        content = ""
        if self.pick_dm_message is not None:
            try:
                content = self.pick_dm_message()
            except Exception as e:
                logger.warning(f"[调度] pick_dm_message 异常: {e}")
                content = ""

        try:
            ok, reason = await send_target_async(self.auth, target, content)
        except Exception as e:
            ok, reason = False, f"发送异常: {e}"

        rec.send_at = rec.send_at  # 保留计划发送时间
        rec.sent_at = time.time()
        rec.content = content if ok else None

        if ok:
            rec.status = RecordStatus.SENT
            self.pending.pop(key, None)
            self.count += 1
            logger.info(
                f"[进度] 已发送 {self.count}/{self.max_target}（目标「{target.get('nickname')}」）"
            )
            if self.count >= self.max_target:
                self.reached_limit = True
                logger.info(f"[完成] 达到目标数量 {self.max_target}，停止私信")
        else:
            rec.status = RecordStatus.FAIL
            rec.reason = reason
            # 发送失败不计入 sent，但必须从 pending 移除，
            # 否则 wait_done（软停止等存量）会永久卡在 STOPPING（pending 永不空）。
            self.pending.pop(key, None)
            logger.warning(f"[跳过] 「{target.get('nickname')}」发送失败，待重试: {reason}")

    async def wait_done(self) -> None:
        """等待队列发空（用于软停止后等存量发完）"""
        while not self._queue.empty() or self.pending:
            await asyncio.sleep(0.5)

    def records_list(self) -> list[SendRecord]:
        """按插入顺序返回记录（前端展示用）"""
        return [self.records[k] for k in self._records_order if k in self.records]
