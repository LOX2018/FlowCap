# coding=utf-8
"""私信接收守护进程（重构版）

迁移自 DY_Spider_base/auto_dm/recv_daemon.py。
关键变化：
- http.server → FastAPI（路由更清晰）
- Tauri sidecar 模式：由 Rust SidecarManager 管理生命周期
- 业务逻辑（Conversation / AccountInbox / RecvChannel）完整保留

运行方式（Tauri sidecar）：
    dyautodm-recv-daemon --accounts A,B --port P

功能：
  - 每账号一个 RecvChannel 线程，监听 frontier-im WS 长连接
  - 收到私信存入 AccountInbox（持久化到 dm_history.json）
  - 暴露 /status /conversations /conversation /send /quit HTTP 接口
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import threading
import time
from typing import Any

from fastapi import FastAPI, HTTPException
from loguru import logger
from pydantic import BaseModel

# 无控制台模式下 sys.stdout/stderr 可能为 None
if sys.stdout is not None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if sys.stderr is not None:
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from vbrowser import app_root

_ROOT = app_root()

# 日志同步输出到 stderr（enqueue=True 避免 Windows GBK 控制台中文编码失败中断主线程），
# 这样 Tauri Rust 侧能捕获到守护进程的日志，也会经由 backend 的日志桥接展示到前端「运行日志」。
logger.remove()
logger.add(
    sys.stderr,
    level="INFO",
    colorize=False,
    enqueue=True,
    format="{time:HH:mm:ss} | {level: <8} | {message}",
)

app = FastAPI(title="recv-daemon")

# 全局状态
_state: dict[str, Any] = {
    "accounts": [],
    "port": 0,
    "inboxes": {},   # name -> AccountInbox
    "channels": {},  # name -> RecvChannel
}


# ----------------------------------------------------------------------------
# 会话与收件箱（迁移自旧版 Conversation / AccountInbox）
# ----------------------------------------------------------------------------
class Conversation:
    """单个会话（conversation_id 唯一），保存收发双方标识与历史消息。"""

    def __init__(self, conv_id: str, peer_id: Any = None, peer_name: str | None = None) -> None:
        self.conv_id = conv_id
        self.peer_id = peer_id
        self.peer_name = peer_name or (peer_id or conv_id)
        self.short_id: Any = None
        self.messages: list[dict] = []
        self.unread = 0
        self.last_ts = 0

    def add(self, role: str, text: str, msg_type: str = "text",
            extra: dict | None = None, ts: float | None = None) -> None:
        ts = ts or time.time()
        self.messages.append({
            "role": role,
            "text": text,
            "msg_type": msg_type,
            "extra": extra or {},
            "ts": ts,
        })
        self.last_ts = ts
        if role == "them":
            self.unread += 1

    def to_dict(self) -> dict:
        return {
            "conv_id": self.conv_id,
            "peer_id": self.peer_id,
            "peer_name": self.peer_name,
            "short_id": self.short_id,
            "unread": self.unread,
            "last_ts": self.last_ts,
            "messages": self.messages,
        }


class AccountInbox:
    """一个账号的收件箱：conversation_id -> Conversation，线程安全。

    会话消息持久化到账号目录的 dm_history.json。
    """

    def __init__(self, name: str, history_path: str | None = None) -> None:
        self.name = name
        self.lock = threading.RLock()
        self.convs: dict[str, Conversation] = {}
        self.connected = False
        self.last_error = ""
        self.history_path = history_path
        if history_path:
            self._load_history()

    def _load_history(self) -> None:
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
                c.short_id = d.get("short_id")
                c.messages = d.get("messages") or []
                c.last_ts = d.get("last_ts") or 0
                c.unread = 0
                self.convs[conv_id] = c
            if self.convs:
                logger.info(f"[recv][{self.name}] 已从历史加载 {len(self.convs)} 个会话")
                for cid, c in self.convs.items():
                    logger.info(f"[recv][{self.name}]   历史会话: conv_id={cid}, peer_name={c.peer_name}, peer_id={c.peer_id}, messages={len(c.messages)} 条")
        except Exception as e:
            logger.warning(f"[recv][{self.name}] 历史会话加载失败: {e}")

    def _save_history(self) -> None:
        try:
            if not self.history_path:
                return
            os.makedirs(os.path.dirname(self.history_path), exist_ok=True)
            data = {}
            for cid, c in self.convs.items():
                data[cid] = {
                    "peer_id": c.peer_id,
                    "peer_name": c.peer_name,
                    "short_id": c.short_id,
                    "last_ts": c.last_ts,
                    "messages": c.messages,
                }
            tmp = self.history_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.history_path)
        except Exception as e:
            logger.warning(f"[recv][{self.name}] 历史会话保存失败: {e}")

    def get_or_create(self, conv_id: str, peer_id: Any = None,
                      peer_name: str | None = None) -> Conversation:
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

    def list_convs(self) -> list[dict]:
        with self.lock:
            return [c.to_dict() for c in sorted(
                self.convs.values(), key=lambda x: x.last_ts, reverse=True)]

    def get_conv(self, conv_id: str) -> dict | None:
        with self.lock:
            c = self.convs.get(conv_id)
            return c.to_dict() if c else None

    def mark_read(self, conv_id: str) -> None:
        with self.lock:
            c = self.convs.get(conv_id)
            if c:
                c.unread = 0

    def add_message(self, conv_id: str, role: str, text: str,
                    peer_id: Any = None, peer_name: str | None = None,
                    msg_type: str = "text", extra: dict | None = None) -> Conversation:
        with self.lock:
            c = self.get_or_create(conv_id, peer_id, peer_name)
            c.add(role, text, msg_type, extra)
            self._save_history()
        return c


# ----------------------------------------------------------------------------
# 接收通道（迁移自旧版 RecvChannel）
# ----------------------------------------------------------------------------
class RecvChannel(threading.Thread):
    """单个账号的私信接收通道（守护线程）。"""

    def __init__(self, name: str, env_path: str, inbox: AccountInbox,
                 auto_reconnect: bool = True) -> None:
        super().__init__(daemon=True)
        self.name = name
        self.env_path = env_path
        self.inbox = inbox
        self.auto_reconnect = auto_reconnect
        self._stop = threading.Event()
        self._ws: Any = None
        self._auth: Any = None

    def _build_auth(self) -> Any:
        from dy_apis.login_api import DYLoginApi
        return DYLoginApi._load_auth_from_env(self.env_path)

    def _make_ws(self) -> Any:
        from websocket import WebSocketApp
        from dy_apis.douyin_api import DouyinAPI
        from builder.header import HeaderBuilder
        from builder.params import Params

        auth = self._build_auth()
        self._auth = auth
        device_id = DouyinAPI.get_device_id(auth=auth)
        app_key = "e1bd35ec9db7b8d846de66ed140b1ad9"
        fp_id = "9"
        access_key = f"{fp_id + app_key + device_id}f8a69f1719916z"
        access_key = hashlib.md5(access_key.encode("utf-8")).hexdigest()
        params = Params()
        (params
         .add_param("aid", "6383")
         .add_param("device_platform", "douyin_pc")
         .add_param("fpid", fp_id)
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
                "Pragma": "no-cache",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
                "User-Agent": HeaderBuilder.ua,
                "Cache-Control": "no-cache",
                "Sec-WebSocket-Protocol": "binary, base64, pbbp2",
                "Sec-WebSocket-Extensions": "permessage-deflate; client_max_window_bits",
            },
            cookie=auth.cookie_str,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
            on_open=on_open,
        )
        return ws

    def _restart_ws(self) -> None:
        try:
            self._ws = self._make_ws()
            self._ws.run_forever(origin="https://www.douyin.com")
        except Exception as e:
            if not self._stop.is_set() and self.auto_reconnect:
                time.sleep(5)
                self._restart_ws()

    def _handle(self, message: bytes) -> None:
        """解析 PushFrame -> Response。

        - new_message_notify：新私信消息，按 content 提取可读文本入库。
        - get_conversation_info_list_v2_response_body：连接初始化时抖音下发的
          已有会话列表同步帧；据此建立会话骨架（conversation_id），使守护启动
          即拿到全部历史会话（peer 信息待后续新消息到达时补全）。
        """
        from static import Live_pb2, Response_pb2
        frame = Live_pb2.PushFrame()
        frame.ParseFromString(message)
        if frame.payloadType == "pb":
            resp = Response_pb2.Response()
            resp.ParseFromString(frame.payload)
            # 1) 已有会话列表同步帧（连接初期下发一次）
            sync = resp.body.get_conversation_info_list_v2_response_body
            if sync and getattr(sync, "conversation_info_list", None):
                self._sync_conversations(sync.conversation_info_list)
                # 同步帧本身不携带消息体，直接返回；消息走 new_message_notify
                return
            # 2) 新私信消息
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
            logger.info(f"[recv][{self.name}][会话 {conv_id[:8]}…] 新消息: peer_name={peer_name}, sender={sender}, content_json.sender_nickname={content_json.get('sender_nickname')}")
            self.inbox.add_message(
                conv_id, "them", text, peer_id=sender,
                peer_name=peer_name, msg_type=str(msg_type), extra=extra,
            )
            logger.info(f"[recv][{self.name}][会话 {conv_id[:8]}…] {peer_name}: {text}")
        elif frame.payloadType == "text/json":
            try:
                logger.debug(f"[recv][{self.name}] json 控制帧: {json.loads(frame.payload)}")
            except Exception:
                pass

    def _sync_conversations(self, conv_list: list) -> None:
        """把 WS 下发的已有会话列表建立成会话骨架（无消息、peer 信息待补）。"""
        n_new = 0
        logger.info(f"[recv][{self.name}] 收到同步帧，含 {len(conv_list)} 个会话")
        for i, item in enumerate(conv_list):
            conv_id = getattr(item, "conversation_id", "") or ""
            short_id = getattr(item, "conversation_short_id", None) or None
            logger.info(f"[recv][{self.name}]   同步帧会话#{i}: conv_id={conv_id}, short_id={short_id}")
        with self.inbox.lock:
            for item in conv_list:
                conv_id = getattr(item, "conversation_id", "") or ""
                if not conv_id:
                    continue
                if conv_id in self.inbox.convs:
                    continue
                # 同步帧只给 conversation_id（+short_id），真实对方 uid 待新私信补全
                c = Conversation(conv_id, None, None)
                c.short_id = getattr(item, "conversation_short_id", None) or None
                self.inbox.convs[conv_id] = c
                n_new += 1
        if n_new:
            self.inbox._save_history()
            logger.info(
                f"[recv][{self.name}] 已从同步帧加载 {n_new} 个已有会话"
            )

    @staticmethod
    def _extract(content_json: dict, msg_type: Any) -> tuple[str | None, dict]:
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
        return f"[未知类型{t}] {json.dumps(content_json, ensure_ascii=False)[:200]}", {}

    def run(self) -> None:
        self._restart_ws()

    def stop(self) -> None:
        self._stop.set()
        try:
            if self._ws:
                self._ws.close()
        except Exception:
            pass


# ----------------------------------------------------------------------------
# FastAPI 路由
# ----------------------------------------------------------------------------
@app.on_event("startup")
async def _startup() -> None:
    logger.info(
        f"recv_daemon 启动 accounts={_state['accounts']} port={_state['port']}"
    )
    # 为每个账号启动 RecvChannel
    from auto_dm import accounts as acc
    for name in _state["accounts"]:
        try:
            env_path = acc.env_path_of(name)
            history_path = os.path.join(
                acc._ACCOUNTS_DIR, name, "dm_history.json"
            )
            inbox = AccountInbox(name, history_path=history_path)
            _state["inboxes"][name] = inbox
            channel = RecvChannel(name, env_path, inbox, auto_reconnect=True)
            _state["channels"][name] = channel
            channel.start()
            logger.info(f"[recv] 账号 {name} 接收通道已启动")
        except Exception as e:
            logger.error(f"[recv] 账号 {name} 启动失败: {e}")


@app.on_event("shutdown")
async def _shutdown() -> None:
    for ch in _state["channels"].values():
        ch.stop()


@app.get("/status")
async def status() -> dict:
    out = {}
    for name, ib in _state["inboxes"].items():
        out[name] = {
            "connected": ib.connected,
            "error": ib.last_error,
            "conv_count": len(ib.convs),
            "total_unread": sum(c.unread for c in ib.convs.values()),
        }
    return {"ok": True, "accounts": out}


@app.get("/conversations")
async def conversations(account: str) -> dict:
    ib: AccountInbox | None = _state["inboxes"].get(account)
    if not ib:
        raise HTTPException(404, "账号不存在")
    convs = ib.list_convs()
    # 兜底：收件箱还没有任何会话（守护刚启动/WS 同步帧未到）时，
    # 直接调 Douyin IM API 拉一次真实会话列表建立骨架，私信中心不再一片空白。
    if not convs:
        _pull_conversations_api(ib)
        convs = ib.list_convs()
    logger.info(f"[recv][{account}] /conversations 返回 {len(convs)} 个会话")
    for i, c in enumerate(convs):
        logger.info(f"[recv][{account}]   会话#{i}: conv_id={c.get('conv_id')}, peer_name={c.get('peer_name')}, peer_id={c.get('peer_id')}, messages={len(c.get('messages') or [])} 条")
    return {"ok": True, "conversations": convs}


def _pull_conversations_api(ib: AccountInbox) -> int:
    """直接调 Douyin IM get_conversation_list 拉真实会话列表并建立骨架。

    绕过 WS 长连接同步时序：守护启动几秒内 WS 未同步时列表也能即刻出现；
    名称/消息等由后续 WS 新消息与历史补全。返回新增骨架数。
    """
    try:
        from auto_dm import accounts as acc
        from dy_apis.login_api import DYLoginApi
        from dy_apis.douyin_api import DouyinAPI
        env_path = acc.env_path_of(ib.name)
        auth = DYLoginApi._load_auth_from_env(env_path)
        conv_list = DouyinAPI.get_conversation_list(auth)
    except Exception as e:
        logger.warning(f"[recv][{ib.name}] API 拉取会话列表失败: {e}")
        return 0
    n = 0
    with ib.lock:
        for info in conv_list or []:
            if not isinstance(info, dict):
                continue
            conv_id = info.get("conversation_id") or ""
            if not conv_id or conv_id in ib.convs:
                continue
            c = Conversation(conv_id, None, None)
            c.short_id = info.get("conversation_short_id") or None
            ib.convs[conv_id] = c
            n += 1
    if n:
        ib._save_history()
        logger.info(f"[recv][{ib.name}] 由 API 拉取到 {n} 个会话骨架")
    return n


@app.get("/conversation")
async def conversation(account: str, conv_id: str) -> dict:
    ib: AccountInbox | None = _state["inboxes"].get(account)
    if not ib:
        raise HTTPException(404, "账号不存在")
    conv = ib.get_conv(conv_id)
    if conv is None:
        # 未见过的会话：先 API 拉取骨架再查，保证点开的会话存在
        _pull_conversations_api(ib)
        conv = ib.get_conv(conv_id)
    if conv is None:
        raise HTTPException(404, "会话不存在")
    ib.mark_read(conv_id)
    return {"ok": True, "conversation": conv}


class SendBody(BaseModel):
    account: str
    conv_id: str
    text: str


@app.post("/send")
async def send(body: SendBody) -> dict:
    """用该账号的 send_msg 回复。"""
    from dy_apis.login_api import DYLoginApi
    from dy_apis.douyin_api import DouyinAPI
    from auto_dm import accounts as acc

    ib: AccountInbox | None = _state["inboxes"].get(body.account)
    if not ib:
        return {"ok": False, "error": "账号不存在"}
    peer_id = None
    with ib.lock:
        c = ib.convs.get(body.conv_id)
        if c:
            peer_id = c.peer_id
    if not peer_id:
        return {"ok": False, "error": "无法定位会话对方 uid"}
    env_path = acc.env_path_of(body.account)
    if not env_path:
        return {"ok": False, "error": "账号 .env 路径缺失"}
    try:
        auth = DYLoginApi._load_auth_from_env(env_path)
        conversation_id, conversation_short_id, ticket = DouyinAPI.create_conversation(
            auth, int(peer_id)
        )
        ok, detail = DouyinAPI.send_msg(
            auth, conversation_id, conversation_short_id, ticket, body.text
        )
        if ok:
            ib.add_message(body.conv_id, "me", body.text, peer_id=peer_id)
            logger.info(f"[recv][{body.account}] 已回复会话 {body.conv_id[:8]}…: {body.text}")
            return {"ok": True}
        logger.warning(f"[recv][{body.account}] 回复失败原因: {detail}")
        return {"ok": False, "error": detail or "send_msg 返回 False（可能触发私信风控）"}
    except Exception as e:
        logger.error(f"[recv][{body.account}] 回复失败: {e}")
        return {"ok": False, "error": str(e)}


@app.post("/quit")
async def quit_() -> dict:
    for ch in _state["channels"].values():
        ch.stop()
    threading.Timer(0.5, lambda: os._exit(0)).start()
    return {"ok": True}


# ----------------------------------------------------------------------------
# 主入口
# ----------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="私信接收守护进程")
    parser.add_argument("--accounts", required=True, help="账号名列表（逗号分隔）")
    parser.add_argument("--port", type=int, required=True, help="HTTP 控制端口")
    args = parser.parse_args()

    _state["accounts"] = [a.strip() for a in args.accounts.split(",") if a.strip()]
    _state["port"] = args.port

    # 日志落盘
    try:
        from datetime import datetime
        log_dir = os.path.join(_ROOT, "logs")
        os.makedirs(log_dir, exist_ok=True)
        log_file = os.path.join(
            log_dir, f"recv_daemon_{datetime.now().strftime('%Y%m%d')}.log"
        )
        logger.add(
            log_file,
            level="DEBUG",
            encoding="utf-8",
            format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} - {message}",
            retention="15 days",
        )
    except Exception:
        pass

    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")


if __name__ == "__main__":
    main()
