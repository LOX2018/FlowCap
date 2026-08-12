# coding=utf-8
"""私信接收常驻守护进程（多账户隔离）。

设计目标：
  - 独立常驻进程（pythonw 无窗口启动），与 GUI / 自动私信发送进程解耦；
  - 为每个抖音账号建立一条独立的 IM 长连接（frontier-im.douyin.com），
    账户之间完全隔离（各自 cookie / 各自会话缓存 / 各自重连）；
  - 收到的私信按 conversation_id 隔离到不同的“会话”，存入进程内缓存；
  - 通过本地 HTTP API（默认 127.0.0.1:9912）把消息 / 会话列表 / 账号状态
    暴露给 GUI 聚合面板，GUI 不直连抖音，只跟本守护通信；
  - 支持回复：GUI 调 POST /send 由守护用对应账号的 send_msg 发回（复用 dy_apis）。

启动：
  python -m auto_dm.recv_daemon                 # 启动 accounts.json 里全部账号
  python -m auto_dm.recv_daemon --accounts 主,小号2   # 仅启动指定账号
  python -m auto_dm.recv_daemon --port 9912 --no-browser

依赖：websocket-client（与直播监听同一套）。
"""

import argparse
import json
import os
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from auto_dm.vbrowser import app_root  # 统一应用根：源码态=项目根，打包态=exe 所在目录
from loguru import logger

logger.remove()
logger.add(sys.stderr, level="INFO")
try:
    _LOG_DIR = os.path.join(app_root(), "logs")
    os.makedirs(_LOG_DIR, exist_ok=True)
    from datetime import datetime
    logger.add(os.path.join(_LOG_DIR, f"recv_daemon_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"),
               level="DEBUG", encoding="utf-8", enqueue=True, retention="30 days")
except Exception:
    pass

# 延迟导入：避免在 argparse 之前触发重型依赖报错
from auto_dm import accounts as ACCOUNTS


# ---------------------------------------------------------------------------
# 会话 / 消息模型（进程内，按账户 + conversation_id 隔离）
# ---------------------------------------------------------------------------
class Conversation:
    """单个会话（conversation_id 唯一），保存收发双方标识与历史消息。"""

    def __init__(self, conv_id, peer_id=None, peer_name=None):
        self.conv_id = conv_id
        self.peer_id = peer_id           # 对方数字 uid / sec_uid
        self.peer_name = peer_name or (peer_id or conv_id)
        self.messages = []               # [{role, text, ts, msg_type, extra}]
        self.unread = 0
        self.last_ts = 0

    def add(self, role, text, msg_type="text", extra=None, ts=None):
        ts = ts or time.time()
        self.messages.append({
            "role": role,                # "them" / "me"
            "text": text,
            "msg_type": msg_type,
            "extra": extra or {},
            "ts": ts,
        })
        self.last_ts = ts
        if role == "them":
            self.unread += 1

    def to_dict(self):
        return {
            "conv_id": self.conv_id,
            "peer_id": self.peer_id,
            "peer_name": self.peer_name,
            "unread": self.unread,
            "last_ts": self.last_ts,
            "messages": self.messages,
        }


class AccountInbox:
    """一个账号的收件箱：conversation_id -> Conversation，线程安全。

    会话消息会持久化到账号目录的 dm_history.json：退出软件 / 重启 recv_daemon 后，
    重新进入仍能查看之前收到/发出的全部历史私信。未读数（unread）不落盘，重启清零。
    """

    def __init__(self, name, history_path=None):
        self.name = name
        self.lock = threading.RLock()
        self.convs = {}                  # conv_id -> Conversation
        self.connected = False
        self.last_error = ""
        self.history_path = history_path
        if history_path:
            self._load_history()

    # -- 历史会话持久化 ------------------------------------------------------
    def _load_history(self):
        """启动时从磁盘加载历史会话（保留完整 messages，未读清零）。"""
        try:
            if not self.history_path or not os.path.exists(self.history_path):
                return
            with open(self.history_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return
            for conv_id, d in data.items():
                if not isinstance(d, dict) or not conv_id:
                    continue
                c = Conversation(conv_id, d.get("peer_id"), d.get("peer_name"))
                c.messages = d.get("messages") or []
                c.last_ts = d.get("last_ts") or 0
                c.unread = 0
                self.convs[conv_id] = c
            if self.convs:
                logger.info(f"[recv][{self.name}] 已从历史加载 {len(self.convs)} 个会话")
        except Exception as e:
            logger.warning(f"[recv][{self.name}] 历史会话加载失败: {e}")

    def _save_history(self):
        """把当前全部会话写盘（在调用方已持有 self.lock 时调用）。"""
        try:
            if not self.history_path:
                return
            os.makedirs(os.path.dirname(self.history_path), exist_ok=True)
            data = {}
            for cid, c in self.convs.items():
                data[cid] = {
                    "peer_id": c.peer_id,
                    "peer_name": c.peer_name,
                    "last_ts": c.last_ts,
                    "messages": c.messages,
                }
            tmp = self.history_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.history_path)
        except Exception as e:
            logger.warning(f"[recv][{self.name}] 历史会话保存失败: {e}")

    # -- 会话操作 ------------------------------------------------------------
    def get_or_create(self, conv_id, peer_id=None, peer_name=None):
        with self.lock:
            c = self.convs.get(conv_id)
            if c is None:
                c = Conversation(conv_id, peer_id, peer_name)
                self.convs[conv_id] = c
            elif peer_id and not c.peer_id:
                c.peer_id = peer_id
                if peer_name:
                    c.peer_name = peer_name
            return c

    def list_convs(self):
        with self.lock:
            return [c.to_dict() for c in sorted(
                self.convs.values(), key=lambda x: x.last_ts, reverse=True)]

    def get_conv(self, conv_id):
        with self.lock:
            c = self.convs.get(conv_id)
            return c.to_dict() if c else None

    def mark_read(self, conv_id):
        with self.lock:
            c = self.convs.get(conv_id)
            if c:
                c.unread = 0

    def add_message(self, conv_id, role, text, peer_id=None, peer_name=None,
                    msg_type="text", extra=None):
        with self.lock:
            c = self.get_or_create(conv_id, peer_id, peer_name)
            c.add(role, text, msg_type, extra)
            self._save_history()
        return c


# ---------------------------------------------------------------------------
# 接收通道（适配本项目的 auth 加载，封装基座 frontier-im websocket）
# ---------------------------------------------------------------------------
class RecvChannel(threading.Thread):
    """单个账号的私信接收通道（守护线程）。"""

    def __init__(self, name, env_path, inbox: AccountInbox, auto_reconnect=True):
        super().__init__(daemon=True)
        self.name = name
        self.env_path = env_path
        self.inbox = inbox
        self.auto_reconnect = auto_reconnect
        self._stop = threading.Event()
        self._ws = None
        self._auth = None

    def _build_auth(self):
        # 关键适配：基座 DouyinRecvMsg 用 pearpare_auth(cookies, web_protect, keys)，
        # 本项目凭证已落在各账号 .env，直接用 DYLoginApi._load_auth_from_env(env_path) 读回完整 auth。
        from dy_apis.login_api import DYLoginApi
        return DYLoginApi._load_auth_from_env(self.env_path)

    def _make_ws(self):
        """构造基座 frontier-im websocket（与 douyin_recv_msg.py 同构）。"""
        import hashlib
        from websocket import WebSocketApp
        from dy_apis.douyin_api import DouyinAPI
        from builder.header import HeaderBuilder
        from builder.params import Params

        auth = self._build_auth()
        self._auth = auth
        device_id = DouyinAPI.get_device_id(auth=auth)
        appKey = "e1bd35ec9db7b8d846de66ed140b1ad9"
        fpId = "9"
        access_key = f"{fpId + appKey + device_id}f8a69f1719916z"
        access_key = hashlib.md5(access_key.encode("utf-8")).hexdigest()
        params = Params()
        (params
         .add_param("aid", "6383")
         .add_param("device_platform", "douyin_pc")
         .add_param("fpid", fpId)
         .add_param("device_id", device_id)
         .add_param("token", auth.cookie.get("sessionid", ""))
         .add_param("access_key", access_key))
        url = f"wss://frontier-im.douyin.com/ws/v2?{params.toString()}"

        def on_open(ws):
            self.inbox.connected = True
            self.inbox.last_error = ""
            logger.info(f"[recv][{self.name}] 私信长连接已建立")

        def on_message(ws, message):
            try:
                self._handle(message)
            except Exception as e:
                logger.warning(f"[recv][{self.name}] 消息解析异常: {e}")

        def on_error(ws, error):
            self.inbox.connected = False
            self.inbox.last_error = str(error)
            logger.warning(f"[recv][{self.name}] WS 错误: {error}")
            if self.auto_reconnect and not self._stop.is_set():
                logger.info(f"[recv][{self.name}] 5s 后重连…")
                time.sleep(5)
                self._restart_ws()

        def on_close(ws, *args):
            self.inbox.connected = False
            logger.info(f"[recv][{self.name}] WS 关闭")
            if self.auto_reconnect and not self._stop.is_set():
                time.sleep(3)
                self._restart_ws()

        ws = WebSocketApp(
            url=url,
            header={
                'Pragma': 'no-cache',
                'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6',
                'User-Agent': HeaderBuilder.ua,
                'Cache-Control': 'no-cache',
                'Sec-WebSocket-Protocol': 'binary, base64, pbbp2',
                'Sec-WebSocket-Extensions': 'permessage-deflate; client_max_window_bits',
            },
            cookie=auth.cookie_str,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
            on_open=on_open,
        )
        return ws

    def _restart_ws(self):
        try:
            self._ws = self._make_ws()
            self._ws.run_forever(origin="https://www.douyin.com")
        except Exception as e:
            if not self._stop.is_set() and self.auto_reconnect:
                time.sleep(5)
                self._restart_ws()

    def _handle(self, message):
        """解析 PushFrame -> Response.new_message_notify。"""
        from static import Live_pb2, Response_pb2
        frame = Live_pb2.PushFrame()
        frame.ParseFromString(message)
        if frame.payloadType == "pb":
            resp = Response_pb2.Response()
            resp.ParseFromString(frame.payload)
            nm = resp.body.new_message_notify
            if not nm or not nm.message:
                return
            msg = nm.message
            conv_id = msg.conversation_id
            sender = msg.sender
            content = msg.content
            msg_type = msg.message_type
            try:
                content_json = json.loads(content) if content else {}
            except Exception:
                content_json = {}
            text, extra = self._extract(content_json, msg_type)
            if text is None:
                return
            peer_name = content_json.get("sender_nickname") or sender or conv_id
            self.inbox.add_message(
                conv_id, "them", text, peer_id=sender,
                peer_name=peer_name, msg_type=str(msg_type), extra=extra)
            logger.info(f"[recv][{self.name}][会话 {conv_id[:8]}…] {peer_name}: {text}")
        elif frame.payloadType == "text/json":
            try:
                logger.debug(f"[recv][{self.name}] json 控制帧: {json.loads(frame.payload)}")
            except Exception:
                pass

    @staticmethod
    def _extract(content_json, msg_type):
        """把 content JSON 按消息类型转成可读文本。返回 (text, extra)。"""
        try:
            t = int(msg_type)
        except Exception:
            t = msg_type
        if t == 7:
            return content_json.get("text", ""), {}
        elif t == 5:
            try:
                return f"[表情包] {content_json['url']['url_list'][0]}", {}
            except Exception:
                return "[表情包]", {}
        elif t == 17:
            try:
                return f"[语音] {content_json['resource_url']['url_list'][0]}", {}
            except Exception:
                return "[语音]", {}
        elif t == 27:
            try:
                return f"[图片] {content_json['resource_url']['origin_url_list'][0]}", {}
            except Exception:
                return "[图片]", {}
        elif t == 8:
            return f"[分享视频] 视频ID {content_json.get('itemId', '')}", {}
        elif t == 50001:
            return f"[对方已读 标号 {content_json.get('read_index', '')}]", {}
        # 未知类型：dump 原文，避免丢消息
        return f"[未知类型{t}] {json.dumps(content_json, ensure_ascii=False)[:200]}", {}

    def run(self):
        self._restart_ws()

    def stop(self):
        self._stop.set()
        try:
            if self._ws:
                self._ws.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# HTTP API 层（GUI 聚合面板通过它读取 / 发送）
# ---------------------------------------------------------------------------
class _State:
    def __init__(self):
        self.inboxes = {}                # name -> AccountInbox
        self.channels = {}              # name -> RecvChannel
        self.lock = threading.RLock()


_STATE = _State()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _json_body(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length <= 0:
                return {}
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return {}

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/status":
            out = {}
            with _STATE.lock:
                for name, ib in _STATE.inboxes.items():
                    out[name] = {
                        "connected": ib.connected,
                        "error": ib.last_error,
                        "conv_count": len(ib.convs),
                        "total_unread": sum(c.unread for c in ib.convs.values()),
                    }
            return self._send(200, {"ok": True, "accounts": out})
        if path == "/conversations":
            name = parse_qs(parsed.query).get("account", [None])[0]
            with _STATE.lock:
                ib = _STATE.inboxes.get(name)
            if not ib:
                return self._send(404, {"ok": False, "error": "账号不存在"})
            return self._send(200, {"ok": True, "conversations": ib.list_convs()})
        if path == "/conversation":
            q = parse_qs(parsed.query)
            name = q.get("account", [None])[0]
            cid = q.get("conv_id", [None])[0]
            with _STATE.lock:
                ib = _STATE.inboxes.get(name)
            if not ib:
                return self._send(404, {"ok": False, "error": "账号不存在"})
            conv = ib.get_conv(cid)
            if conv is None:
                return self._send(404, {"ok": False, "error": "会话不存在"})
            ib.mark_read(cid)
            return self._send(200, {"ok": True, "conversation": conv})
        return self._send(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/send":
            data = self._json_body()
            name = data.get("account")
            conv_id = data.get("conv_id")
            text = data.get("text", "").strip()
            if not (name and conv_id and text):
                return self._send(400, {"ok": False, "error": "缺少 account/conv_id/text"})
            ok, err = self._send_dm(name, conv_id, text)
            return self._send(200, {"ok": ok, "error": err})
        if path == "/quit":
            threading.Thread(target=_shutdown, daemon=True).start()
            return self._send(200, {"ok": True})
        return self._send(404, {"ok": False, "error": "not found"})

    def _send_dm(self, name, conv_id, text):
        """用该账号的 send_msg 回复（复用 dy_apis.douyin_api.create_conversation + send_msg）。"""
        from dy_apis.login_api import DYLoginApi
        from dy_apis.douyin_api import DouyinAPI
        with _STATE.lock:
            ib = _STATE.inboxes.get(name)
        if not ib:
            return False, "账号不存在"
        # 会话里记录的对方 uid（create_conversation 需要 to_user_id: int）
        peer_id = None
        with ib.lock:
            c = ib.convs.get(conv_id)
            if c:
                peer_id = c.peer_id
        if not peer_id:
            return False, "无法定位会话对方 uid"
        env_path = _env_of(name)
        if not env_path:
            return False, "账号 .env 路径缺失"
        try:
            auth = DYLoginApi._load_auth_from_env(env_path)
            # 先建会话（幂等）拿到 conversation_id / short_id / ticket
            conversation_id, conversation_short_id, ticket = DouyinAPI.create_conversation(auth, int(peer_id))
            ok = DouyinAPI.send_msg(auth, conversation_id, conversation_short_id, ticket, text)
            if ok:
                ib.add_message(conv_id, "me", text, peer_id=peer_id)
                logger.info(f"[recv][{name}] 已回复会话 {conv_id[:8]}…: {text}")
                return True, ""
            return False, "send_msg 返回 False（可能触发私信风控）"
        except Exception as e:
            logger.error(f"[recv][{name}] 回复失败: {e}")
            return False, str(e)

    def log_message(self, fmt, *args):
        pass


def _env_of(name):
    for n, p in ACCOUNTS.list_accounts():
        if n == name:
            return p
    return None


def _shutdown():
    time.sleep(0.2)
    os._exit(0)


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="私信接收常驻守护（多账户隔离）")
    parser.add_argument("--accounts", default="", help="逗号分隔的账号名，留空=全部")
    parser.add_argument("--port", type=int, default=9912)
    args = parser.parse_args()

    accs = ACCOUNTS.list_accounts()
    if args.accounts.strip():
        want = [a.strip() for a in args.accounts.split(",") if a.strip()]
        accs = [(n, p) for n, p in accs if n in want]

    if not accs:
        logger.error("没有任何账号可启动接收，请先添加账号并扫码。")
        return

    for name, env_path in accs:
        # 历史会话统一持久化到账号目录 auto_dm/accounts/<name>/dm_history.json，
        # 重启 recv_daemon 后仍可查看全部历史私信（不依赖 .env 是否在根目录）。
        history_path = os.path.join(ACCOUNTS._ACCOUNTS_DIR, name, "dm_history.json")
        ib = AccountInbox(name, history_path=history_path)
        ch = RecvChannel(name, env_path, ib, auto_reconnect=True)
        with _STATE.lock:
            _STATE.inboxes[name] = ib
            _STATE.channels[name] = ch
        ch.start()
        logger.info(f"[recv] 已为账号 [{name}] 启动私信接收通道")

    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    logger.info(f"[recv] HTTP API 监听 http://127.0.0.1:{args.port}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        for ch in _STATE.channels.values():
            ch.stop()


if __name__ == "__main__":
    main()
