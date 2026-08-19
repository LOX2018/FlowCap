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

    def to_dict(self, limit_messages: int = 0) -> dict:
        msgs = self.messages if not limit_messages else self.messages[-limit_messages:]
        return {
            "conv_id": self.conv_id,
            "peer_id": self.peer_id,
            "peer_name": self.peer_name,
            "short_id": self.short_id,
            "unread": self.unread,
            "last_ts": self.last_ts,
            "messages": msgs,
        }


class AccountInbox:
    """一个账号的收件箱：会话与消息持久化到 SQLite（data/dyautodm.db）。

    取代旧的 dm_history.json 方案：JSON 并发写入易损坏、无事务、无分页。
    SQLite WAL 模式允许 recv_daemon 独立进程与 backend 共享同一 db 文件。
    """

    def __init__(self, name: str, history_path: str | None = None) -> None:
        self.name = name
        self.lock = threading.RLock()
        self.convs: dict[str, Conversation] = {}  # 内存缓存（WS 实时消息用）
        self.connected = False
        self.last_error = ""
        self._api_pulled = False  # 标记是否已调 get_message_by_init 拉全量会话
        self._load_from_db()

    def _db(self):
        from database import get_db
        return get_db()

    def _load_from_db(self) -> None:
        """启动时从 SQLite 加载会话骨架到内存（消息按需查询，不全量加载）。"""
        try:
            conn = self._db()
            rows = conn.execute(
                "SELECT conv_id,peer_id,peer_name,short_id,last_ts,unread "
                "FROM dm_conversations WHERE account=? ORDER BY last_ts DESC",
                (self.name,),
            ).fetchall()
            for r in rows:
                c = Conversation(r["conv_id"], r["peer_id"], r["peer_name"])
                c.short_id = r["short_id"]
                c.last_ts = r["last_ts"] or 0
                c.unread = r["unread"] or 0
                self.convs[r["conv_id"]] = c
            if self.convs:
                logger.info(f"[recv][{self.name}] 已从数据库加载 {len(self.convs)} 个会话")
        except Exception as e:
            logger.warning(f"[recv][{self.name}] 数据库加载会话失败: {e}")

    def get_or_create(self, conv_id: str, peer_id: Any = None,
                      peer_name: str | None = None) -> Conversation:
        with self.lock:
            c = self.convs.get(conv_id)
            if c is None:
                c = Conversation(conv_id, peer_id, peer_name)
                self.convs[conv_id] = c
                # 持久化到 SQLite（INSERT OR IGNORE 避免重复）
                try:
                    conn = self._db()
                    conn.execute(
                        "INSERT OR IGNORE INTO dm_conversations(account,conv_id,peer_id,"
                        "peer_name,short_id,last_ts,unread) VALUES(?,?,?,?,?,?,?)",
                        (self.name, conv_id, peer_id, peer_name, None, 0, 0),
                    )
                    conn.commit()
                except Exception:
                    pass
            elif peer_id and not c.peer_id:
                c.peer_id = peer_id
                if peer_name:
                    c.peer_name = peer_name
                try:
                    conn = self._db()
                    conn.execute(
                        "UPDATE dm_conversations SET peer_id=?,peer_name=? "
                        "WHERE account=? AND conv_id=?",
                        (peer_id, peer_name, self.name, conv_id),
                    )
                    conn.commit()
                except Exception:
                    pass
            return c

    def list_convs(self) -> list[dict]:
        """返回会话列表（按最后消息时间倒序），含最近若干条消息预览。"""
        with self.lock:
            # 优先用内存缓存（WS 实时更新），但补全 SQLite 中可能多的会话
            try:
                conn = self._db()
                rows = conn.execute(
                    "SELECT conv_id,peer_id,peer_name,short_id,last_ts,unread "
                    "FROM dm_conversations WHERE account=? ORDER BY last_ts DESC",
                    (self.name,),
                ).fetchall()
                for r in rows:
                    if r["conv_id"] not in self.convs:
                        c = Conversation(r["conv_id"], r["peer_id"], r["peer_name"])
                        c.short_id = r["short_id"]
                        c.last_ts = r["last_ts"] or 0
                        c.unread = r["unread"] or 0
                        self.convs[r["conv_id"]] = c
            except Exception:
                pass
            return [c.to_dict(limit_messages=20) for c in sorted(
                self.convs.values(), key=lambda x: x.last_ts, reverse=True)]

    def get_conv(self, conv_id: str) -> dict | None:
        """返回单个会话详情（从 SQLite 加载完整消息历史）。"""
        with self.lock:
            c = self.convs.get(conv_id)
            peer_id = c.peer_id if c else None
            peer_name = c.peer_name if c else None
            short_id = c.short_id if c else None
            try:
                conn = self._db()
                # 确保会话骨架存在
                if c is None:
                    row = conn.execute(
                        "SELECT conv_id,peer_id,peer_name,short_id,last_ts,unread "
                        "FROM dm_conversations WHERE account=? AND conv_id=?",
                        (self.name, conv_id),
                    ).fetchone()
                    if not row:
                        return None
                    peer_id = row["peer_id"]
                    peer_name = row["peer_name"]
                    short_id = row["short_id"]
                # 从 SQLite 加载消息（分页最近 200 条）
                mrows = conn.execute(
                    "SELECT role,text,msg_type,extra,ts FROM dm_messages "
                    "WHERE account=? AND conv_id=? ORDER BY ts DESC LIMIT 200",
                    (self.name, conv_id),
                ).fetchall()
                messages = [
                    {
                        "role": m["role"],
                        "text": m["text"],
                        "msg_type": m["msg_type"],
                        "extra": json.loads(m["extra"] or "{}"),
                        "ts": m["ts"],
                    }
                    for m in reversed(mrows)
                ]
                last_ts = mrows[0]["ts"] if mrows else 0
                return {
                    "conv_id": conv_id,
                    "peer_id": peer_id,
                    "peer_name": peer_name,
                    "short_id": short_id,
                    "unread": 0,
                    "last_ts": last_ts,
                    "messages": messages,
                }
            except Exception as e:
                logger.warning(f"[recv][{self.name}] 数据库加载会话详情失败: {e}")
                return None

    def mark_read(self, conv_id: str) -> None:
        with self.lock:
            c = self.convs.get(conv_id)
            if c:
                c.unread = 0
            try:
                conn = self._db()
                conn.execute(
                    "UPDATE dm_conversations SET unread=0 WHERE account=? AND conv_id=?",
                    (self.name, conv_id),
                )
                conn.commit()
            except Exception:
                pass

    def add_message(self, conv_id: str, role: str, text: str,
                    peer_id: Any = None, peer_name: str | None = None,
                    msg_type: str = "text", extra: dict | None = None) -> Conversation:
        ts = time.time()
        with self.lock:
            c = self.get_or_create(conv_id, peer_id, peer_name)
            c.add(role, text, msg_type, extra, ts=ts)
            # 持久化到 SQLite（单条消息 + 会话 last_ts 更新）
            try:
                conn = self._db()
                conn.execute(
                    "INSERT INTO dm_messages(account,conv_id,role,text,msg_type,extra,ts)"
                    " VALUES(?,?,?,?,?,?,?)",
                    (self.name, conv_id, role, text, msg_type,
                     json.dumps(extra or {}, ensure_ascii=False), ts),
                )
                conn.execute(
                    "UPDATE dm_conversations SET last_ts=?,unread=unread+? "
                    "WHERE account=? AND conv_id=?",
                    (ts, 1 if role == "them" else 0, self.name, conv_id),
                )
                conn.commit()
            except Exception as e:
                logger.warning(f"[recv][{self.name}] 消息持久化失败: {e}")
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
        """把 WS 下发的已有会话列表建立成会话骨架并持久化到 SQLite。"""
        n_new = 0
        logger.info(f"[recv][{self.name}] 收到同步帧，含 {len(conv_list)} 个会话")
        for i, item in enumerate(conv_list):
            conv_id = getattr(item, "conversation_id", "") or ""
            short_id = getattr(item, "conversation_short_id", None) or None
            logger.info(f"[recv][{self.name}]   同步帧会话#{i}: conv_id={conv_id}, short_id={short_id}")
        with self.inbox.lock:
            try:
                conn = self.inbox._db()
            except Exception:
                conn = None
            for item in conv_list:
                conv_id = getattr(item, "conversation_id", "") or ""
                if not conv_id:
                    continue
                if conv_id in self.inbox.convs:
                    continue
                c = Conversation(conv_id, None, None)
                c.short_id = getattr(item, "conversation_short_id", None) or None
                self.inbox.convs[conv_id] = c
                n_new += 1
                if conn:
                    conn.execute(
                        "INSERT OR IGNORE INTO dm_conversations(account,conv_id,"
                        "peer_id,peer_name,short_id,last_ts,unread) VALUES(?,?,?,?,?,?,?)",
                        (self.inbox.name, conv_id, None, None, c.short_id, 0, 0),
                    )
            if conn:
                conn.commit()
        if n_new:
            logger.info(f"[recv][{self.name}] 已从同步帧加载 {n_new} 个已有会话")

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
    # 确保 SQLite 已初始化（recv_daemon 独立进程也打开同一个 db 文件）
    try:
        import database
        database.get_db()
    except Exception as e:
        logger.error(f"[recv] 数据库初始化失败: {e}")
    for name in _state["accounts"]:
        try:
            env_path = acc.env_path_of(name)
            inbox = AccountInbox(name)
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
    # 兜底：守护启动后首次拉取时，不管收件箱有没有 WS 同步帧的骨架，
    # 都强制调 Douyin IM API 拉一次全量真实会话，确保私信中心看到完整列表。
    # 避免 WS 同步帧给了 1 个骨架会话导致 list_convs 非空、跳过 API 拉取。
    if not ib._api_pulled:
        _pull_conversations_api(ib)
        ib._api_pulled = True
        convs = ib.list_convs()
    logger.info(f"[recv][{account}] /conversations 返回 {len(convs)} 个会话")
    for i, c in enumerate(convs):
        logger.info(f"[recv][{account}]   会话#{i}: conv_id={c.get('conv_id')}, peer_name={c.get('peer_name')}, peer_id={c.get('peer_id')}, messages={len(c.get('messages') or [])} 条")
    return {"ok": True, "conversations": convs}


def _pull_conversations_api(ib: AccountInbox) -> int:
    """直接调 Douyin IM get_message_by_init 拉全量会话 + get_im_user_info 解析昵称。

    实测发现：抖音网页 douyin.com/chat 用 get_message_by_init（cmd 2043）加载全部会话，
    然后调 /aweme/v1/web/im/user/info/ REST API 解析每个会话对方的昵称/头像。
    后端此前用的 get_info_list（cmd 610）是按 user_id 查单个会话的，不是"列全部"。
    """
    try:
        from auto_dm import accounts as acc
        from dy_apis.login_api import DYLoginApi
        from dy_apis.douyin_api import DouyinAPI
        env_path = acc.env_path_of(ib.name)
        auth = DYLoginApi._load_auth_from_env(env_path)
        my_uid = str(auth.get_uid())
    except Exception as e:
        logger.warning(f"[recv][{ib.name}] 加载凭证失败: {e}")
        return 0

    # 1) get_message_by_init 拉全量会话（250KB，含 259 个 conversation_id）
    try:
        raw = DouyinAPI.get_message_by_init(auth)
        convs = DouyinAPI.parse_init_conversations(raw, my_uid)
    except Exception as e:
        logger.warning(f"[recv][{ib.name}] get_message_by_init 失败: {e}")
        return 0
    if not convs:
        logger.info(f"[recv][{ib.name}] get_message_by_init 返回 0 个会话")
        return 0

    # 2) 写入会话骨架到 SQLite + 内存
    n = 0
    with ib.lock:
        try:
            conn = ib._db()
        except Exception:
            conn = None
        for c in convs:
            conv_id = c["conversation_id"]
            peer_uid = c["peer_uid"]
            if conv_id in ib.convs:
                # 已存在：补全 peer_id（WS 建立的骨架可能没有 peer_id）
                if not ib.convs[conv_id].peer_id and peer_uid:
                    ib.convs[conv_id].peer_id = peer_uid
                    if conn:
                        conn.execute(
                            "UPDATE dm_conversations SET peer_id=? WHERE account=? AND conv_id=?",
                            (peer_uid, ib.name, conv_id),
                        )
                continue
            # 新会话：建立骨架
            conv = Conversation(conv_id, peer_uid, peer_uid)
            ib.convs[conv_id] = conv
            n += 1
            if conn:
                conn.execute(
                    "INSERT OR IGNORE INTO dm_conversations(account,conv_id,"
                    "peer_id,peer_name,short_id,last_ts,unread) VALUES(?,?,?,?,?,?,?)",
                    (ib.name, conv_id, peer_uid, peer_uid, None, 0, 0),
                )
        if conn:
            conn.commit()
    logger.info(f"[recv][{ib.name}] get_message_by_init 提取 {len(convs)} 个会话，新增 {n} 个")

    # 3) 批量解析昵称/头像（后台线程，不阻塞 HTTP 响应）
    def _resolve_names():
        resolved = 0
        for c in convs:
            peer_uid = c.get("peer_uid")
            sec_uid = c.get("sec_uid")
            conv_id = c["conversation_id"]
            existing = ib.convs.get(conv_id)
            if existing and existing.peer_name and existing.peer_name != peer_uid:
                continue  # 已有昵称
            nickname = None
            # 优先用 sec_uid 调 get_user_info（已验证可用）
            if sec_uid:
                try:
                    info = DouyinAPI.get_user_info(auth, f"https://www.douyin.com/user/{sec_uid}")
                    user = (info or {}).get("user") or {}
                    nickname = user.get("nickname")
                except Exception as e:
                    logger.debug(f"[recv][{ib.name}] get_user_info sec={sec_uid[:20]} 失败: {e}")
            # 退回 get_im_user_info（REST API）
            if not nickname and peer_uid:
                try:
                    info = DouyinAPI.get_im_user_info(auth, peer_uid)
                    nickname = info.get("nickname")
                except Exception:
                    pass
            if not nickname:
                nickname = peer_uid or conv_id  # 兜底用 uid
            if existing:
                existing.peer_name = nickname
            try:
                db = ib._db()
                db.execute(
                    "UPDATE dm_conversations SET peer_name=? WHERE account=? AND conv_id=?",
                    (nickname, ib.name, conv_id),
                )
                db.commit()
            except Exception:
                pass
            resolved += 1
        if resolved:
            logger.info(f"[recv][{ib.name}] 已解析 {resolved}/{len(convs)} 个会话的昵称")

    threading.Thread(target=_resolve_names, daemon=True).start()
    return n


@app.get("/conversation")
async def conversation(account: str, conv_id: str) -> dict:
    ib: AccountInbox | None = _state["inboxes"].get(account)
    if not ib:
        raise HTTPException(404, "账号不存在")
    conv = ib.get_conv(conv_id)
    if conv is None:
        # 未见过的会话：先 API 拉取骨架再查，保证点开的会话存在
        if not ib._api_pulled:
            _pull_conversations_api(ib)
            ib._api_pulled = True
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
