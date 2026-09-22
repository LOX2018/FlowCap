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
import inspect
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
        # 2026-09-20：直播/采集来源的 AI 生成回调。由上层（AutoDM）传入；
        # 与 pick_dm_message 同为 None 时由调用方自带 content。
        gen_dm_message: Optional[Any] = None,
    ) -> None:
        self.auth = auth
        self.max_target = int(max_target)
        self.delay_range = delay_range
        self.interval = interval
        self.enable_send = enable_send
        # pick_dm_message: 可调用对象，返回一条私信文案；为 None 时由调用方传入 content
        self.pick_dm_message = pick_dm_message
        # gen_dm_message: 按目标生成 AI 文案；None 或返回空 → 回落词库/调用方 content
        self.gen_dm_message = gen_dm_message

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
        # 停止信号（ENG-016）：用于**打断"睡到发送时刻"的等待**。
        # 语义：置位 = 不再接收新发送，正在等待延迟窗口的目标立即清理。
        # 为什么需要它：软停止后 `_accept_new=False` 会让待发目标必然走
        # 「不入池」路径，却仍要睡满整个随机延迟（最长 ~120s）才清 `pending`；
        # 而 wait_done()/on_idle 的收尾条件都要求 `pending` 为空
        # → 前端「停止监听」最长 2 分钟无反应。置位即让等待立刻返回。
        self._stop_signal = asyncio.Event()

    def detach_on_idle(self) -> None:
        """解绑队列空回调（由上层在真正收尾时调用）。

        2026-09-21（ENG-015）：「只触发一次」的语义从调度器移到上层 ——
        调度器不再自行 `on_idle = None`，改由 AutoDM 收尾时显式解绑，
        避免握手窗口吃掉唯一一次触发机会后，真正的收尾再无回调可用。
        """
        self.on_idle = None

    # ------------------------------------------------------------------
    # 状态控制
    # ------------------------------------------------------------------
    def stop_hard(self) -> None:
        """硬停止：清空队列，之后 submit 一律忽略，_do_send 也拦截。"""
        self._clear_queue = True
        self._accept_new = False
        self._stopped = True
        # 打断正在等待延迟窗口的目标（ENG-016）
        self._stop_signal.set()
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        self.pending.clear()
        logger.warning(f"[SEND-002] " + "[调度] 硬停止：已清空待发队列，不再发送任何私信")

    def stop_soft(self) -> None:
        """软停止：停止接收新目标，但保留已入队的延迟私信继续发完。"""
        self._accept_new = False
        # 打断正在等待延迟窗口的目标（ENG-016）：软停止后这些目标必然
        # 走不到发送（_accept_new=False），继续空等只会把收尾卡住。
        self._stop_signal.set()
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

    def apply_runtime(
        self,
        max_target: int | None = None,
        interval: float | None = None,
        delay_range: tuple[int, int] | None = None,
        pick_dm_message: Any | None = None,
        gen_dm_message: Any | None = None,
    ) -> list[str]:
        """运行期热更调度参数（**不打断消费循环、不清队列、不重置去重/计数**）。

        设计契约（用户 2026-09-15 定调）：「重启」= 只把变更的配置内容补进正在运行的
        监听任务，**不中断监听**。调度器是唯一持有这些运行时字段的地方，所以热更必须
        经由本方法（调用方不直接改字段，避免漏掉 `reached_limit` 之类派生状态）。

        返回实际生效的字段名列表（供上层如实回报，不做「假成功」）。
        """
        applied: list[str] = []
        if max_target is not None:
            self.set_max_target(int(max_target))
            applied.append("max_target")
        if interval is not None:
            self.interval = float(interval)
            applied.append("interval")
        if delay_range is not None:
            lo, hi = int(delay_range[0]), int(delay_range[1])
            self.delay_range = (lo, hi) if lo <= hi else (hi, lo)
            applied.append("delay_range")
        if pick_dm_message is not None:
            self.pick_dm_message = pick_dm_message
            applied.append("dm_pool")
        if gen_dm_message is not None:
            self.gen_dm_message = gen_dm_message
            applied.append("ai_gen")
        if applied:
            logger.info(f"[调度] 运行时参数热更：{applied}")
        return applied

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
            self._stop_signal.clear()   # ENG-016：新一轮任务须清除上一轮的停止信号
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
                # 队列空：触发 on_idle
                #
                # 2026-09-21（ENG-015）：原实现触发后置 `self.on_idle = None`
                # ——把「只触发一次」这一语义硬编码在调度器里。而队列空在
                # **启动握手窗口内是必然的正常中间态**（还没捕获到任何目标），
                # 于是这次唯一的触发机会被握手窗口吃掉，后续真正发空时反而
                # 再也没有回调可用（自然关播的任务永远停在「运行中」）。
                # 何时解绑属于**上层（AutoDM）的状态机语义**，不在调度器职责内：
                # 调度器只负责「队列空了」这一事实的通知，由 AutoDM 在真正
                # 收尾时调 `detach_on_idle()` 解绑。
                if self._queue.empty() and not self.pending and self.on_idle:
                    try:
                        await self.on_idle() if asyncio.iscoroutinefunction(self.on_idle) else self.on_idle()
                    except Exception as e:
                        logger.warning(f"[SEND-003] " + f"[调度] on_idle 异常: {e}")
                continue

            # 等到 send_at 时刻
            #
            # 2026-09-21（ENG-016 根因修复）：休眠必须可被停止打断。
            # 原实现无条件 `await asyncio.sleep(item.send_at - now)` ——
            # 而 `stop(hard=False)` 的语义是「存量私信发完」，实现上却变成
            # 「等最后一条的随机延迟窗口（最长 ~120s）睡满」。更糟的是：
            # 软停止后 `_accept_new=False` 使 `_do_send` 必然走「不入池」路径，
            # 这条记录既不会发出、也要等睡满才清 `pending`；而
            # `wait_done()` 与 `on_idle` 触发条件都要求 `pending` 为空
            # → 两条收尾路径被同一条记录**同时堵死**，状态卡在 STOPPING，
            # 前端「停止监听」点了没反应（实机：11:20:49 点击，11:22:xx 才生效）。
            #
            # 修法：把「睡到点」改成「等 min(到点, 停止信号)」，并显式区分
            # 三种走向 —— 睡满正常发送 / 收到停止信号后丢弃 / 硬停止丢弃。
            now = time.time()
            if item.send_at > now:
                _wake = self._stop_signal.wait()
                try:
                    await asyncio.wait_for(_wake, timeout=item.send_at - now)
                except asyncio.TimeoutError:
                    pass          # 睡满：正常走发送
                else:
                    # 被停止信号唤醒：不再空等延迟窗口，丢弃该目标并清理 pending，
                    # 让 wait_done()/on_idle 能立即收尾（软停止语义本就是
                    # 「停止接收新目标」，未开始的延迟发送不应继续占用收尾）。
                    self.pending.pop(item.key, None)
                    rec = self.records.get(item.key)
                    if rec is not None:
                        rec.status = RecordStatus.SKIPPED
                        rec.reason = "软停止：尚未到发送时刻，已丢弃"
                    logger.info(
                        "[调度] 软停止：丢弃尚未到发送时刻的目标 " +
                        f"「{item.target.get('nickname')}」（不再空等延迟窗口）")
                    continue

            if self._stopped or self._clear_queue:
                logger.warning(f"[SEND-004] " + f"[调度] 停止中，丢弃待发私信「{item.target.get('nickname')}」")
                self.pending.pop(item.key, None)
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
        # 2026-09-20：来源回调优先 —— 有 gen_dm_message 时先按目标生成 AI 文案；
        # 生成失败/被护栏拦/未接线 → 回落 pick_dm_message（词库），行为与改造前一致。
        if self.gen_dm_message is not None:
            try:
                # gen_dm_message 可为同步或 async（AutoDM 传入的是 async 协程函数：
                # AI 生成含阻塞网络请求，必须 await 出去，绝不阻塞事件循环）
                _r = self.gen_dm_message(target)
                if inspect.isawaitable(_r):
                    _r = await _r
                content = str(_r or "")
            except Exception as e:
                # 注：生成的 AI 文案已在 ai_reply.generate_dm_for_live 内经护栏
                # （validate_reply/思考泄漏检测）；此处只兜「回调本身抛异常」。
                logger.warning(f"[SEND-038] " + f"[调度] gen_dm_message 异常: {e}")
                content = ""
        if not content and self.pick_dm_message is not None:
            try:
                content = self.pick_dm_message()
            except Exception as e:
                logger.warning(f"[SEND-005] " + f"[调度] pick_dm_message 异常: {e}")
                content = ""

        # 2026-09-22（M-9）：AI 文案和词库均未启用时，兜底回退弹幕原文。
        # live_hook 传入了 target["comment"]，但 _do_send 从未读取它作为 content。
        # => 空串 -> submit_by_uid 拒绝 -> SEND-006（调度透传缺口，两账号均复现）。
        if not content:
            content = str(target.get("comment") or "").strip()

        try:
            # 2026-09-07：视频采集 / 直播监听的私信统一走 dm_dispatch 调度。
            # 这两个来源目标**绝大多数是陌生人首发**，必须进入
            # 「2 次/分钟、30 次/天 + 频控降权冷静」体系，否则会直接把
            # 账号打到频控。按 uid 直发（目标没有 conv_id）。
            _uid = str(target.get("user_id") or target.get("uid") or "").strip()
            _acct = getattr(self.auth, "account_name", "") or ""
            _routed = False
            if _uid and _acct:
                try:
                    from services.dm_dispatch import get_dispatcher as _gd
                    _r = _gd().submit_by_uid(_acct, _uid, content,
                                             source="dispatch")
                    if _r.accepted:
                        ok, reason = True, "已入池（调度器异步发送）"
                        _routed = True
                    else:
                        ok, reason = False, (_r.error or "调度器拒绝入池")
                        _routed = True
                        logger.warning(f"[SEND-006] " + f"[调度] 私信未入池（账号={_acct} 目标={_uid}）: {reason}")
                except Exception as _e:
                    # 2026-09-13 S2.7：**不再回退直发**。
                    # 直发绕过 dm_dispatch 的统一风控闸门（2次/分钟、
                    # 30次/天 + 频控降权冷静）——发送是最高频风控面，
                    # 绕过闸门等于把账号暴露给风控。宁可"这次不发"，
                    # 也不破坏闸门（用户可从前端看到「调度堵塞」类失败原因）。
                    logger.warning(f"[SEND-037] " + f"[调度] dm_dispatch 接入失败，已放弃发送（不再回退直发绕过风控闸门）: {_e}")
                    _routed = True   # 标记已处理，阻止下方回退直发分支
            if not _routed:
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
                f"[进度] 已发送 {self.count}/{self.max_target}"
                f"（目标「{target.get('nickname')}」文案前20字={content[:20]!r}）"
            )
            if self.count >= self.max_target:
                self.reached_limit = True
                logger.info(f"[完成] 达到目标数量 {self.max_target}，停止私信")
        else:
            rec.status = RecordStatus.FAIL
            rec.reason = reason
            # 2026-09-08：结构化失败分类（前端弹窗展示具体原因 + 可操作建议）
            try:
                from core.sender import explain_fail
                _exp = explain_fail(str(reason or ""))
                rec.fail_kind = _exp["kind"]
                rec.fail_label = _exp["label"]
                rec.fail_advice = _exp["advice"]
            except Exception:
                pass
            # 发送失败不计入 sent，但必须从 pending 移除，
            # 否则 wait_done（软停止等存量）会永久卡在 STOPPING（pending 永不空）。
            self.pending.pop(key, None)
            logger.warning(f"[SEND-008] [私信发送结果] 目标「{target.get('nickname')}」=失败\n"
                f"   原因: {reason}\n"
                f"   文案: {content!r}"
            )

    async def wait_done(self) -> None:
        """等待队列发空（用于软停止后等存量发完）"""
        while not self._queue.empty() or self.pending:
            await asyncio.sleep(0.5)

    def records_list(self) -> list[SendRecord]:
        """按插入顺序返回记录（前端展示用）"""
        return [self.records[k] for k in self._records_order if k in self.records]
