import gzip
import sys
import threading
import time
from urllib.parse import urlencode

from websocket import WebSocketApp

from loguru import logger

import static.Live_pb2 as Live_pb2
from dy_apis.douyin_api import DouyinAPI
from builder.header import HeaderBuilder
from builder.params import Params
import utils.common_util as common_util
from utils.dy_util import generate_signature

# Windows 控制台是 GBK，弹幕含 emoji 会 UnicodeEncodeError，按 UTF-8 输出。
# 无控制台模式（PyInstaller --noconsole / pythonw）下 sys.stdout 为 None，需判空。
if sys.stdout is not None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


class DouyinLive:
    def __init__(self, live_id, auth_):
        self.auth_ = auth_
        self.live_id = live_id
        self.ws = None
        self._should_stop = False   # 停止开关：置 True 后 on_close 不再自动重连

    def ping(self, ws):
        while True:
            frame = Live_pb2.PushFrame()
            frame.payloadType = "hb"
            try:
                ws.send(frame.SerializeToString(), opcode=0x02)
                time.sleep(5)
            except Exception as e:
                ws.close()
                break

    def on_open(self, ws):
        logger.info(f"[LIVE-022] " + "[live-ws] 连接已建立")
        threading.Thread(target=self.ping, args=(ws,)).start()

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
                # s.payload = frame.headersList[1].value.encode('utf-8')
                s.payload = response.internalExt.encode('utf-8')
                s.logId = frame.logId
                ws.send(s.SerializeToString(), opcode=0x02)
            for item in response.messagesList:
                if item.method == 'WebcastGiftMessage':
                    message = Live_pb2.GiftMessage()
                    message.ParseFromString(item.payload)
                    # print(f'\033[1;37;40m[礼物]SEC_UID = {message.user.sec_uid} - {message.user.nickname}\033[m 送出 \033[4;30;44m{message.gift.name}\033[m x {message.comboCount}')
                    # 谁给谁送了什么礼物
                    logger.debug(f'[礼物]SEC_UID = {message.user.sec_uid} - {message.user.nickname} 送给 {message.toUser.sec_uid} - {message.toUser.nickname} {message.gift.name} x {message.comboCount}')
                elif item.method == "WebcastChatMessage":
                    message = Live_pb2.ChatMessage()
                    message.ParseFromString(item.payload)
                    # 用户等级
                    # print(message.user.badge_image_list[0])
                    logger.debug(f'[消息]SEC_UID = {message.user.sec_uid} - {message.user.nickname} : {message.content}')
                elif item.method == "WebcastMemberMessage":
                    message = Live_pb2.MemberMessage()
                    message.ParseFromString(item.payload)
                    logger.debug(f'[进入]SEC_UID = {message.user.sec_uid} - {message.user.nickname} 进入直播间')
                elif item.method == "WebcastLikeMessage":
                    message = Live_pb2.LikeMessage()
                    message.ParseFromString(item.payload)
                    logger.debug(f'[点赞]SEC_UID = {message.user.sec_uid} - {message.user.nickname} 点赞了 {message.count} 次')
                    logger.debug(f'[点赞]点赞总数 = {message.total}')
                elif item.method == "WebcastSocialMessage":
                    message = Live_pb2.SocialMessage()
                    message.ParseFromString(item.payload)
                    if message.action == 1:
                        logger.debug(f'[关注]SEC_UID = {message.user.sec_uid} - {message.user.nickname} 关注主播')
                elif item.method == "WebcastRoomStatsMessage":
                    message = Live_pb2.RoomStatsMessage()
                    message.ParseFromString(item.payload)
                    logger.debug(f'[房间信息] {message.displayLong}')

            # s = zlib.decompress(decode_str).decode()
        except Exception as e:
            logger.error(f"[LIVE-020] " + f"[live-ws] 消息回调异常: {e}")

    def on_error(self, ws, error):
        logger.error(f"[LIVE-024] " + f"[live-ws] WebSocket on_error: {error}")

    def on_close(self, ws, close_status_code, close_msg):
        # 若主动停止（stop() 已置 _should_stop），不再自动重连，确保 WS 真正断开
        if getattr(self, "_should_stop", False):
            logger.info(f"[LIVE-025] " + "[live-ws] closed（主动停止，不重连）")
            return
        # 此处判断是否需要重连 判断直播间是否关闭
        self.start_ws()
        logger.warning(f"[LIVE-026] " + f"[live-ws] closed status_code={close_status_code} msg={close_msg}")

    def start_ws(self, room_info=None):
        # room_info：可选，由调用方预查并传入（避免重复查询同一直播间，加速启动）。
        # 为 None 时回退原行为：自行调用 get_live_info。
        if room_info and isinstance(room_info, dict) and room_info.get("room_id"):
            logger_info = room_info
        else:
            room_info = DouyinAPI.get_live_info(self.auth_, self.live_id)
            if not room_info or not isinstance(room_info, dict):
                logger.error(f"[LIVE-023] " + "### get_live_info 返回空，无法建立监听（可能直播间不存在或 cookie 失效） ###")
                return
            logger_info = room_info
        room_id = logger_info['room_id']
        user_id = logger_info['user_id']
        ttwid = logger_info['ttwid']
        params = Params()

        res = DouyinAPI.get_webcast_detail(self.auth_, str(user_id), room_id, f"https://live.douyin.com/{self.live_id}")
        frame = Live_pb2.LiveResponse()
        frame.ParseFromString(res)
        (params
         .add_param('app_name', 'douyin_web')
         .add_param('version_code', '180800')
         .add_param('webcast_sdk_version', '1.0.15')
         .add_param('update_version_code', '1.0.15')
         .add_param('compress', 'gzip')
         .add_param('device_platform', 'web')
         .add_param('cookie_enabled', 'true')
         .add_param('screen_width', '1707')
         .add_param('screen_height', '960')
         .add_param('browser_language', 'zh-CN')
         .add_param('browser_platform', 'Win32')
         .add_param('browser_name', 'Mozilla')
         .add_param('browser_version',
                    HeaderBuilder.ua.split('Mozilla/')[-1])
         .add_param('browser_online', 'true')
         .add_param('tz_name', 'Etc/GMT-8')
         .add_param('cursor', str(frame.cursor))
         .add_param('internal_ext', frame.internalExt)
         .add_param('host', 'https://live.douyin.com')
         .add_param('aid', '6383')
         .add_param('live_id', '1')
         .add_param('did_rule', '3')
         .add_param('endpoint', 'live_pc')
         .add_param('support_wrds', '1')
         .add_param('user_unique_id', str(user_id))
         .add_param('im_path', '/webcast/im/fetch/')
         .add_param('identity', 'audience')
         .add_param('need_persist_msg_count', '15')
         .add_param('insert_task_id', '')
         .add_param('live_reason', '')
         .add_param('room_id', room_id)
         .add_param('heartbeatDuration', '0')
         .add_param('signature', generate_signature(room_id, user_id))
         )
        wss_url = f"wss://webcast100-ws-web-hl.douyin.com/webcast/im/push/v2/?{urlencode(params.get())}"
        self.ws = WebSocketApp(
            url=wss_url,
            header={
                'Pragma': 'no-cache',
                'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6',
                'User-Agent': HeaderBuilder.ua,
                'Upgrade': 'websocket',
                'Cache-Control': 'no-cache',
                'Connection': 'Upgrade',
            },
            cookie=self.auth_.cookie_str,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close,
            on_open=self.on_open
        )
        try:
            self.ws.run_forever(origin='https://live.douyin.com')
        except Exception as e:
            logger.debug(str(e))
            self.ws.close()


if __name__ == '__main__':
    common_util.load_env()
    live_id = "432433667143"
    live = DouyinLive(live_id, common_util.dy_auth)
    live.start_ws()
