import hashlib
import json

import websocket
from loguru import logger
from websocket import WebSocketApp

from douyin_api import DouyinAPI
from builder.auth import DouyinAuth
from builder.header import HeaderBuilder
from builder.params import Params
from static import Live_pb2, Response_pb2


class DouyinRecvMsg:
    appKey = "e1bd35ec9db7b8d846de66ed140b1ad9"
    fpId = '9'

    def __init__(self, auth: DouyinAuth, auto_reconnect=True):
        self.auto_reconnect = auto_reconnect
        self.auth = auth
        self.ws = None
        deviceId = DouyinAPI.get_device_id(auth=self.auth)
        accessKey = f'{self.fpId + self.appKey + deviceId}f8a69f1719916z'
        accessKey = hashlib.md5(accessKey.encode(encoding='UTF-8')).hexdigest()
        params = Params()
        (params
         .add_param("aid", "6383")
         .add_param("device_platform", "douyin_pc")
         .add_param("fpid", self.fpId)
         .add_param("device_id", deviceId)
         .add_param("token", self.auth.cookie["sessionid"])
         .add_param("access_key", accessKey)
         )
        self.url = f"wss://frontier-im.douyin.com/ws/v2?{params.toString()}"

    def on_open(self, ws):
        print("WebSocket connection open.")

    def on_message(self, ws, message):
        # 2026-09-17 修补（OCR 审查 HIGH）：原实现整个回调体**无任何异常保护**，
        # 而 `json.loads(content)` 与各级下标（`content["url"]["url_list"][0]`、
        # `content["resource_url"]["origin_url_list"][0]`、`content["text"]`）都可能抛：
        #   - 风控/空响应：content 为 None 或 "" → json.loads 抛 JSONDecodeError
        #   - 结构变化：url_list / origin_url_list 为空列表 → IndexError
        #   - 键缺失：text / itemId / read_index 不存在 → KeyError
        # WebSocketApp 的回调抛异常会中断本次分发且无上层捕获，表现为
        # **消息静默丢失**（服务在跑，但该条消息从未被处理）——极难定位。
        # 现包一层 try/except 并记录日志，保证单条坏消息不拖垮整个接收循环。
        try:
            self._handle_message(message)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[RECV-MSG] 消息解析失败，已跳过该条: "
                           f"{type(e).__name__}: {e}")

    @staticmethod
    def _first_url(node) -> str:
        """从 {"url_list": [...]} / {"origin_url_list": [...]} 安全取首个 URL。

        2026-09-17：原实现直接 `content["resource_url"]["url_list"][0]`，
        空列表或键缺失即抛 IndexError/KeyError，整条消息被丢弃。
        """
        if not isinstance(node, dict):
            return ""
        for key in ("url_list", "origin_url_list"):
            lst = node.get(key)
            if isinstance(lst, list) and lst:
                return str(lst[0])
        return ""

    def _handle_message(self, message):
        frame = Live_pb2.PushFrame()
        frame.ParseFromString(message)
        if frame.payloadType == 'pb':
            response = Response_pb2.Response()
            response.ParseFromString(frame.payload)
            sender = response.body.new_message_notify.message.sender
            content = response.body.new_message_notify.message.content
            msg_type = response.body.new_message_notify.message.message_type
            conversation_id = response.body.new_message_notify.message.conversation_id
            index = response.body.new_message_notify.message.index_in_conversation
            # content 可能是空串/None（风控或心跳）→ json.loads 会抛，兜底为空 dict
            try:
                content = json.loads(content) if content else {}
            except (TypeError, ValueError):
                content = {}
            if not isinstance(content, dict):
                content = {}
            if msg_type == 7:
                print(f'【消息编号:{index}】【聊天室ID:{conversation_id}】【来自:{sender}】文本消息:{content.get("text", "")}')
            elif msg_type == 5:
                print(f'【消息编号:{index}】【聊天室ID:{conversation_id}】【来自:{sender}】用户表情包消息:{self._first_url(content.get("url"))}')
            elif msg_type == 17:
                print(f'【消息编号:{index}】【聊天室ID:{conversation_id}】【来自:{sender}】语音信息:{self._first_url(content.get("resource_url"))}')
            elif msg_type == 27:
                print(f'【消息编号:{index}】【聊天室ID:{conversation_id}】【来自:{sender}】图片信息:{self._first_url(content.get("resource_url"))}')
            elif msg_type == 8:
                print(f'【消息编号:{index}】【聊天室ID:{conversation_id}】【来自:{sender}】分享视频信息:视频ID{content.get("itemId", "")}')
            elif msg_type == 50001:
                print(f'对方已读，消息标号:{content.get("read_index", "")}')
        elif frame.payloadType == 'text/json':
            print(json.loads(frame.payload))

    def on_error(self, ws, error):
        print("\033[31m### error ###")
        print(error)
        print("### ===error=== ###\033[m")
        if type(error) == ConnectionRefusedError or type(
                error) == websocket._exceptions.WebSocketConnectionClosedException and self.auto_reconnect:
            self.start()

    def on_close(self, ws, close_status_code, close_msg):
        print("\033[31m### closed ###")
        print(f"status_code: {close_status_code}, msg: {close_msg}")
        print("### ===closed=== ###\033[m")

    def start(self):
        self.ws = WebSocketApp(
            url=self.url,
            header={
                'Pragma': 'no-cache',
                'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6',
                'User-Agent': HeaderBuilder.ua,
                'Cache-Control': 'no-cache',
                'Sec-WebSocket-Protocol': 'binary, base64, pbbp2',
                'Sec-WebSocket-Extensions': 'permessage-deflate; client_max_window_bits'
            },
            cookie=self.auth.cookie_str,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close,
            on_open=self.on_open
        )
        try:
            self.ws.run_forever(origin='https://www.douyin.com')
        except KeyboardInterrupt:
            self.ws.close()
        except:
            self.ws.close()


if __name__ == '__main__':
    # websocket.enableTrace(True)
    web_protect_str = r''
    keys_str = r''
    cookies_str = r''
    auth_ = DouyinAuth()
    auth_.perepare_auth(cookies_str, web_protect_str, keys_str)
    douyinMsg = DouyinRecvMsg(auth_)
    douyinMsg.start()
