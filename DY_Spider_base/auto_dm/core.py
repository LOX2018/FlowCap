# coding=utf-8
"""中央调度：聚合双路来源（弹幕 + 中控台采集），去重后触发私信。

延迟发送设计：
  - 捕获到评论/弹幕后，立即做去重并入队（记录捕获时刻 capture_ts）；
  - 后台线程按 capture_ts + SEND_DELAY_SEC 到点后才真正调用 send_target；
  - 这样“看到弹幕→过一会再私信”的节奏更拟真，降低机器批量感与风控识别。
  - 停止时清空队列，确保“停止”后不再补发任何延迟私信。
"""

import time
import random
import threading
from loguru import logger
from auto_dm import config as C
from auto_dm.sender import send_target


class DispatchCenter:
    def __init__(self, auth, max_target=None):
        self.auth = auth
        self.sent = set()          # 兼容旧字段：已处理 key（user_id/sec_uid/nickname 其一）
        self.pending = {}          # 去重 key -> target（延迟窗口内尚未发送的，避免重复入队）
        # 跨来源联合去重：弹幕带 user_id/sec_uid，中控台采集往往只有 nickname。
        # 三套标识分别登记，确保“同一人”无论走哪条来源、带什么字段都不重复发送。
        self.sent_ids = set()      # 已处理目标的数字 user_id
        self.sent_sec = set()      # 已处理目标的 sec_uid
        self.sent_names = set()    # 已处理目标的 nickname（兜底，用于无 uid/sec_uid 的来源）
        self.count = 0             # 已发送数量
        # 发送上限：优先用构造参数（运行时显式传入，避免依赖模块全局的时序问题）
        self.max_target = int(max_target) if max_target is not None else int(getattr(C, "MAX_TARGET", 9999))
        self.reached_limit = False
        self.stopped = False       # 停止标志：置 True 后不再发送任何私信
        self.paused = False        # 暂停标志：置 True 后停止监听采集与发送（不断浏览器/守护）
        self.on_idle = None        # 回调：延迟队列自然发空（_loop 退出前）触发，供 AutoDM 收尾
        self._queue = []           # [(send_at_ts, key, target)]，后台线程消费
        self.records = []           # 已处理目标的完整记录（供 GUI 实时统计）
        self._lock = threading.RLock()
        self._cv = threading.Condition(self._lock)
        self._worker = threading.Thread(target=self._loop, daemon=True)
        self._worker.start()

    def stop(self):
        """硬停止：之后 submit 一律忽略，并清空待发队列，确保不再发送任何私信。
        注意：这是“彻底停止”，会丢弃已捕获未发的延迟私信。一般仅供 force_stop_all 内部使用。"""
        with self._lock:
            self.stopped = True
            self._queue.clear()
            self.pending.clear()
            self._cv.notify_all()

    def stop_keep_queue(self):
        """软停止（监听与发送解耦）：停止接收新目标、不再发送新私信，
        但【保留已入队的延迟私信】，由后台 _loop 继续按延迟时点发完。
        用于“直播间关闭/主动停止监听”场景：私信发送是与监听(WS)独立的线路，
        已捕获的评论不应因监听停止而被丢弃。"""
        with self._lock:
            self.stopped = True
            self._cv.notify_all()
        logger.info(
            f"[调度] 软停止（保留延迟队列）：已停止接收新目标，"
            f"已捕获未发的 {len(self._queue)} 条私信将在延迟后继续发出")

    def queue_size(self):
        """当前延迟队列中待发的私信条数。"""
        with self._lock:
            return len(self._queue)

    def pause(self):
        """暂停：不断浏览器/守护进程，仅停止监听采集（submit 忽略新目标）与发送。"""
        with self._lock:
            self.paused = True
            self._cv.notify_all()
        logger.info("[调度] 已暂停：停止监听采集与私信发送（浏览器/守护进程保持运行）")

    def resume(self):
        """继续：恢复监听采集与私信发送。"""
        with self._lock:
            self.paused = False
            self._cv.notify_all()
        logger.info("[调度] 已继续：恢复监听采集与私信发送")

    def set_max_target(self, n):
        """运行时调整发送上限（GUI 调速即时生效）。"""
        with self._lock:
            self.max_target = int(n)
            if self.count < self.max_target:
                self.reached_limit = False
        logger.info(f"[调度] 发送上限调整为 {self.max_target}（已发 {self.count}）")

    def _dedup_key(self, target):
        # 以 user_id 优先，否则 sec_uid，否则 nickname
        return str(target.get("user_id") or target.get("sec_uid") or target.get("nickname") or "")

    def _already_seen(self, target):
        """跨来源联合去重：只要 user_id / sec_uid / nickname 任一命中已处理集合即判重复。"""
        uid = target.get("user_id")
        sec = target.get("sec_uid")
        name = target.get("nickname")
        # 优先用强标识（uid/sec_uid）判定；仅有 nickname 时再用 nickname 兜底
        if uid and str(uid) in self.sent_ids:
            return True
        if sec and str(sec) in self.sent_sec:
            return True
        if name and name in self.sent_names:
            return True
        # 退化兼容旧 sent 集合（按原 key）
        if self._dedup_key(target) in self.sent:
            return True
        return False

    def _register_seen(self, target):
        """把目标的三种标识都登记进已处理集合（入队即登记，防延迟窗口内重复捕获）。"""
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

    def submit(self, target):
        """提交一个私信目标。target 需含 nickname，可选 user_id / sec_uid / comment。"""
        if self.stopped:
            logger.debug("[调度] 已停止，忽略提交目标（不再发送私信）")
            return
        if self.paused:
            # 暂停态：停止监听采集效果——忽略新目标，不记录不入队（浏览器/守护保持运行）
            return
        # 记录捕获时刻（供 GUI 实时统计的“发言时间”列展示）
        if not target.get("capture_ts"):
            target["capture_ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
        key = self._dedup_key(target)
        if not key:
            return
        with self._lock:
            # 无论是否真发/是否去重，都在统计里留一条“已捕获”记录，
            # 以便实时统计（尤其是查阅模式）能展示同一发言人的【每一次】评论，而非被去重覆盖。
            self.records.append({
                "key": key,
                "capture_ts": target.get("capture_ts", ""),
                "nickname": target.get("nickname") or "",
                "comment": target.get("comment") or "",
                "status": "已捕获" if C.ENABLE_SEND else "采集(未发)",
                "content": "",
                "send_ts": "",
            })
            if self._already_seen(target) or key in self.pending:
                return  # 已处理（含跨来源同一人）或已在延迟队列中，仅去重“是否发送”，不影响上面记录
            if not C.ENABLE_SEND:
                logger.debug(f"[采集]（仅采集模式）{target.get('nickname')}: {target}")
                return
            if self.reached_limit:
                return
            self.pending[key] = target
            self._register_seen(target)   # 入队即登记，避免延迟窗口内同人再次评论重复入队
            # 延迟支持随机抖动：SEND_DELAY_SEC 为 (min,max) 元组时随机取整数秒，单值则固定
            delay_cfg = getattr(C, "SEND_DELAY_SEC", 0)
            if isinstance(delay_cfg, (tuple, list)) and len(delay_cfg) == 2:
                lo, hi = int(delay_cfg[0]), int(delay_cfg[1])
                delay = random.randint(lo, hi)
            else:
                delay = max(0.0, float(delay_cfg or 0))
            send_at = time.time() + delay
            self._queue.append((send_at, key, target))
            self._cv.notify_all()
        logger.info(
            f"[调度] 已捕获「{target.get('nickname')}」评论，"
            f"将在 {delay}s 后发送私信（延迟队列长度={len(self._queue)}）")

    def _loop(self):
        """后台线程：到点后真正发送（捕获时刻 + SEND_DELAY_SEC）。

        注意：self._cv 是绑定在 self._lock(RLock) 上的 Condition，
        wait() 必须在【持锁】状态下调用（wait 内部自动释放/重获锁）。
        因此整个主循环体都包裹在 `with self._lock:` 内，仅 _do_send 在锁外执行。
        """
        while True:
            to_send = None
            with self._lock:
                # 1) 彻底停止且队列已空：退出线程
                if self.stopped and not self._queue:
                    break
                # 2) 延迟队列自然发空：通知控制器收尾（只触发一次）
                if self.on_idle:
                    try:
                        self.on_idle()
                    except Exception:
                        pass
                    self.on_idle = None
                now = time.time()
                # 3) 暂停态：不消费队列（已排队的也暂不发），保持线程存活以便恢复
                if self.paused:
                    self._cv.wait(0.5)
                    continue
                # 4) 已达上限：清空待发队列，不再发送（避免队列里排队的超发）
                if self.reached_limit:
                    if self._queue:
                        self._queue.clear()
                        self.pending.clear()
                    self._cv.wait(0.5)
                    continue
                # 5) 取最早到期的一条
                ready = None
                for i, (send_at, k, t) in enumerate(self._queue):
                    if send_at <= now:
                        ready = (k, t)
                        break
                if ready is None:
                    # 无可发送项：等待直到最近一条到期或被唤醒
                    wait = max(0.1, self._queue[0][0] - now) if self._queue else 1.0
                    self._cv.wait(wait)
                    continue
                to_send = ready
                # 从队列移除（pending 在发送成功后清除）
                self._queue = [x for x in self._queue if x[1] != to_send[0]]
            # 在锁外发送，避免发送阻塞整个调度
            if to_send is not None:
                self._do_send(to_send[0], to_send[1])

    def _do_send(self, key, target):
        if self.stopped:
            return
        content = C.pick_dm_message()
        ok, reason = send_target(self.auth, target, content)
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            self.pending.pop(key, None)
            status = "成功" if ok else f"失败({reason})"
            # 更新该 key 在统计里的记录（submit 已为每次发言留“已捕获”占位），不重复 append。
            # 用正向遍历找【第一条】该 key 的记录：去重后真正被发送的是首次发言，
            # 私信情况/文案应记到实际被发送的那条评论上，其余发言行保持“已捕获”。
            for rec in self.records:
                if rec.get("key") == key:
                    rec["status"] = status
                    rec["content"] = content if ok else ""
                    rec["send_ts"] = ts if ok else ""
                    break
            else:
                # 极端情况：记录缺失时补一条
                self.records.append({
                    "key": key,
                    "capture_ts": target.get("capture_ts") or "",
                    "nickname": target.get("nickname") or "",
                    "comment": target.get("comment") or "",
                    "status": status,
                    "content": content if ok else "",
                    "send_ts": ts if ok else "",
                })
            if ok:
                self.sent.add(key)
                self.count += 1
                logger.info(f"[进度] 已发送 {self.count}/{self.max_target}（目标「{target.get('nickname')}」）")
                if self.count >= self.max_target:
                    self.reached_limit = True
                    logger.info(f"[完成] 达到目标数量 {self.max_target}，停止私信")
                time.sleep(C.SEND_INTERVAL)
            else:
                # 发送失败不计入 sent，可后续重试（但本次不立即重投，避免刷屏）
                logger.warning(f"[跳过] 「{target.get('nickname')}」发送失败，待重试: {reason}")
