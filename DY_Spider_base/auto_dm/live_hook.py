# coding=utf-8
"""直播间弹幕来源：包装 DouyinLive，从 WebcastChatMessage 提取发言人直接推给调度中心。

关键：ChatMessage.user 自带 id(数字uid)/sec_uid/nickname，无需任何查询即可直发，
彻底绕开原 DYchajian 卡死的 get_user_info 风控环节。
"""

import sys
import gzip
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import static.Live_pb2 as Live_pb2
from dy_live.server import DouyinLive
from loguru import logger

from auto_dm import config as C


class LiveChatHook(DouyinLive):
    """在基类 DouyinLive 的 on_message 基础上，把公屏弹幕转成私信目标推给 dispatch。"""

    def __init__(self, live_id, auth_, dispatch):
        super().__init__(live_id, auth_)
        self.dispatch = dispatch
        # 诊断计数：本会话遇到的加密昵称 / 其中 sec_uid 缺失的数量
        self._enc_count = 0
        self._enc_no_secuid = 0

    @staticmethod
    def _is_encrypted_nickname(nickname):
        """判定弹幕昵称是否被加密/隐藏：空、含星号(*)、或“用户<数字>”系统占位。"""
        if not nickname:
            return True
        if "*" in nickname:
            return True
        import re
        if re.fullmatch(r"用户\d+", nickname.strip()):
            return True
        return False

    def on_message(self, ws, message):
        try:
            frame = Live_pb2.PushFrame()
            frame.ParseFromString(message)
            origin_bytes = gzip.decompress(frame.payload)
            response = Live_pb2.LiveResponse()
            response.ParseFromString(origin_bytes)
            if response.needAck:
                s = Live_pb2.PushFrame()
                s.payloadType = "ack"
                s.payload = response.internalExt.encode('utf-8')
                s.logId = frame.logId
                ws.send(s.SerializeToString(), opcode=0x02)
            for item in response.messagesList:
                try:
                    if item.method == 'WebcastGiftMessage':
                        m = Live_pb2.GiftMessage()
                        m.ParseFromString(item.payload)
                        print(f'\033[1;37;40m[礼物]SEC_UID = {m.user.sec_uid} - {m.user.nickname}\033[m 送给 \033[1;37;40m{m.toUser.sec_uid} - {m.toUser.nickname}\033[m \033[4;30;44m{m.gift.name}\033[m x {m.comboCount}')
                    elif item.method == "WebcastChatMessage":
                        m = Live_pb2.ChatMessage()
                        m.ParseFromString(item.payload)
                        u = m.user
                        nickname = getattr(u, "nickname", None)
                        user_id = getattr(u, "id", None)
                        sec_uid = getattr(u, "sec_uid", None)
                        target = {
                            "user_id": user_id,
                            "sec_uid": sec_uid,
                            "nickname": nickname,
                            "comment": getattr(m, "content", None),
                        }
                        if C.ENABLE_LIVE_CHAT:
                            logger.info(f"[弹幕] {nickname}(uid={user_id} sec_uid={sec_uid}): {target['comment']}")
                            if self._is_encrypted_nickname(nickname):
                                # 实证（来自运行日志）：昵称被加密时，sec_uid 往往同时为空，
                                # 这是“监测账号凭证/会话态异常”的表现（用存储的旧凭证登录时尤为明显），
                                # 而非抖音对个别用户的脱敏。此时无法用 sec_uid 补全昵称。
                                self._enc_count += 1
                                if not sec_uid:
                                    self._enc_no_secuid += 1
                                    if self._enc_no_secuid == 1 or self._enc_no_secuid % 20 == 0:
                                        logger.error(
                                            f"[昵称加密] 检测到昵称加密且 sec_uid 为空（累计 {self._enc_no_secuid} 次）。\n"
                                            f"       这是监测账号凭证/会话异常的典型表现（旧存储凭证登录会导致弹幕昵称被脱敏、"
                                            f"sec_uid 丢失）。\n"
                                            f"       请对该监测账号执行【重新扫码】以恢复正常会话，昵称与 sec_uid 才会完整。")
                                # 不尝试无意义的 get_user_info 补全（加密时 sec_uid 为空，必失败）；
                                # 私信按数字 uid 直发，不依赖昵称明文。
                            if target.get("nickname") and (target.get("user_id") or target.get("sec_uid")):
                                self.dispatch.submit(target)
                    elif item.method == "WebcastMemberMessage":
                        m = Live_pb2.MemberMessage()
                        m.ParseFromString(item.payload)
                        print(f'\033[1;37;40m[进入]SEC_UID = {m.user.sec_uid} - {m.user.nickname}\033[m 进入直播间')
                    elif item.method == "WebcastLikeMessage":
                        m = Live_pb2.LikeMessage()
                        m.ParseFromString(item.payload)
                        print(f'\033[1;37;40m[点赞]SEC_UID = {m.user.sec_uid} - {m.user.nickname}\033[m 点赞了 {m.count} 次')
                    elif item.method == "WebcastSocialMessage":
                        m = Live_pb2.SocialMessage()
                        m.ParseFromString(item.payload)
                        if m.action == 1:
                            print(f'\033[1;37;40m[关注]SEC_UID = {m.user.sec_uid} - {m.user.nickname}\033[m 关注主播')
                    elif item.method == "WebcastRoomStatsMessage":
                        m = Live_pb2.RoomStatsMessage()
                        m.ParseFromString(item.payload)
                        print(f'\033[1;37;40m[房间信息] {m.displayLong}\033[m')
                except Exception as e:
                    # 单条消息解析/处理异常不应中断整帧，避免漏掉同帧其他消息
                    print('live_hook item error:', str(e))
        except Exception as e:
            # 整帧解析/处理异常不应让 ws 回调崩溃
            print('live_hook on_message error:', str(e))
