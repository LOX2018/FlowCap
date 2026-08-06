# coding=utf-8
"""中央调度：聚合双路来源（弹幕 + 中控台采集），去重后触发私信。"""

import time
from loguru import logger
from auto_dm import config as C
from auto_dm.sender import send_target


class DispatchCenter:
    def __init__(self, auth):
        self.auth = auth
        self.sent = set()          # 已发送 uid/sec_uid 去重
        self.count = 0             # 已发送数量
        self.reached_limit = False
        self.stopped = False       # 停止标志：置 True 后不再发送任何私信

    def stop(self):
        """停止调度：之后 submit 一律忽略，确保“停止”后私信不再发出。"""
        self.stopped = True

    def _dedup_key(self, target):
        # 以 user_id 优先，否则 sec_uid，否则 nickname
        return str(target.get("user_id") or target.get("sec_uid") or target.get("nickname") or "")

    def submit(self, target):
        """提交一个私信目标。target 需含 nickname，可选 user_id / sec_uid / comment。"""
        if self.stopped:
            logger.debug("[调度] 已停止，忽略提交目标（不再发送私信）")
            return
        if not C.ENABLE_SEND:
            logger.debug(f"[采集]（仅采集模式）{target.get('nickname')}: {target}")
            return
        if self.reached_limit:
            return
        key = self._dedup_key(target)
        if not key or key in self.sent:
            return
        self.sent.add(key)

        ok, reason = send_target(self.auth, target, C.DM_MESSAGE)
        if ok:
            self.count += 1
            logger.info(f"[进度] 已发送 {self.count}/{C.MAX_TARGET}")
            if self.count >= C.MAX_TARGET:
                self.reached_limit = True
                logger.info(f"[完成] 达到目标数量 {C.MAX_TARGET}，停止私信")
            time.sleep(C.SEND_INTERVAL)
        else:
            # 发送失败不计入 sent，可后续重试（但本次不立即重投，避免刷屏）
            self.sent.discard(key)
            logger.warning(f"[跳过] 「{target.get('nickname')}」发送失败，已移出去重集合待重试: {reason}")
