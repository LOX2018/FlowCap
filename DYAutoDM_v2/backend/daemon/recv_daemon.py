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

# 2026-09-06 全局治理（D：系统死代理隔离，同 main.py）：
# 独立 exe 进程同样被 Windows 注册表系统代理毒害（requests 继承
# getproxies_registry）。本进程 requests 全链路不用代理（DY_PROXY 只进浏览器），
# NO_PROXY=* 禁用环境/注册表代理探测——仅本进程，不动系统设置。
os.environ.setdefault("NO_PROXY", "*")
os.environ.setdefault("no_proxy", "*")

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
        self.avatar: str | None = None

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
            "avatar": self.avatar or "",
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
        # my_uid：用于从 conv_id 0:1:uid_a:uid_b 提取对端 UID（WS 消息 sender 常为空/自己）
        self.my_uid = None
        self._refresh_my_uid()
        self._load_from_db()

    def _refresh_my_uid(self) -> bool:
        """（重新）读取本机 uid。

        2026-09-06 全局治理（2.1b uid 轮换自愈）：
        原实现只在 AccountInbox.__init__ 里算一次，之后**永不刷新**。
        而抖音存在 uid 轮换（知识库 08 §24.9 实测：query/user 返回新 uid，
        imapi 仍挂老 uid），一旦轮换，方向判定 `sender == my_uid` 就全错
        （自己发的被判成对方发的，反之亦然）。

        改：抽成本方法 + 由 WS 循环定期调用（见 RecvChannel 里的
        MY_UID_REFRESH_INTERVAL），轮换后自动自愈。
        返回是否读取成功。
        """
        try:
            from auto_dm import accounts as _acc
            from dy_apis.login_api import DYLoginApi
            env_path = _acc.env_path_of(self.name)
            if not env_path:
                return False
            _auth = DYLoginApi._load_auth_from_env(env_path)
            new_uid = str(_auth.get_uid()) if _auth else None
            if new_uid and new_uid != self.my_uid:
                if self.my_uid:
                    logger.warning("RECV-001", 
                        f"[recv][{self.name}] my_uid 发生轮换：{self.my_uid} → {new_uid}"
                        f"（已自动更新方向判定基准）")
                self.my_uid = new_uid
            return bool(self.my_uid)
        except Exception:
            return False

    def _extract_peer_uid(self, conv_id: str) -> str | None:
        """从 conv_id 0:1:uid_a:uid_b 提取对端 UID（排除自己）。"""
        if not conv_id:
            return None
        parts = conv_id.split(":")
        if len(parts) >= 4:
            uid_a, uid_b = parts[2], parts[3]
            if self.my_uid and uid_a == self.my_uid:
                return uid_b
            if self.my_uid and uid_b == self.my_uid:
                return uid_a
            # 无 my_uid 兜底：取与 my_uid 不同的那个
            return uid_b
        return None

    def _db(self):
        from database import get_db
        return get_db()

    def _load_from_db(self) -> None:
        """启动时从 SQLite 加载会话骨架到内存（消息按需查询，不全量加载）。"""
        try:
            conn = self._db()
            rows = conn.execute(
                "SELECT conv_id,peer_id,peer_name,short_id,last_ts,unread,avatar "
                "FROM dm_conversations WHERE account=? ORDER BY last_ts DESC",
                (self.name,),
            ).fetchall()
            for r in rows:
                c = Conversation(r["conv_id"], r["peer_id"], r["peer_name"])
                c.short_id = r["short_id"]
                c.last_ts = r["last_ts"] or 0
                c.unread = r["unread"] or 0
                c.avatar = r["avatar"] or None
                self.convs[r["conv_id"]] = c
            if self.convs:
                logger.info(f"[recv][{self.name}] 已从数据库加载 {len(self.convs)} 个会话")
        except Exception as e:
            logger.warning("RECV-002", f"[recv][{self.name}] 数据库加载会话失败: {e}")

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
                    # 保护已由 capture_all 关联的昵称 / 已有对端 UID：
                    # 仅当数据库现有 peer_name 为空或缺失时才用本次值覆盖；
                    # 若现有已有值（昵称或对端UID），绝不回退成自己或空值。
                    conn.execute(
                        "UPDATE dm_conversations SET peer_id=?,"
                        "peer_name=COALESCE("
                        "  (SELECT CASE WHEN peer_name IS NOT NULL AND peer_name != '' "
                        "THEN peer_name ELSE ? END), ?) "
                        "WHERE account=? AND conv_id=?",
                        (peer_id, peer_name or peer_id, peer_name or peer_id,
                         self.name, conv_id),
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
                    "SELECT conv_id,peer_id,peer_name,short_id,last_ts,unread,avatar "
                    "FROM dm_conversations WHERE account=? ORDER BY last_ts DESC",
                    (self.name,),
                ).fetchall()
                for r in rows:
                    if r["conv_id"] not in self.convs:
                        c = Conversation(r["conv_id"], r["peer_id"], r["peer_name"])
                        c.short_id = r["short_id"]
                        c.last_ts = r["last_ts"] or 0
                        c.unread = r["unread"] or 0
                        c.avatar = r["avatar"] or None
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
                        "SELECT conv_id,peer_id,peer_name,short_id,last_ts,unread,avatar "
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
                    "avatar": row["avatar"] or "",
                }
            except Exception as e:
                logger.warning("RECV-003", f"[recv][{self.name}] 数据库加载会话详情失败: {e}")
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
                    msg_type: str = "text", extra: dict | None = None,
                    msg_id: str | None = None) -> Conversation:
        # 回执类消息（msg_type=50001「对方已读」）不落库：
        # 这类消息无 msg_id（唯一索引管不到去重），WS 每次同步都会重复写入，
        # 实测全库堆积 1938 条，把真实聊天记录挤掉、也让会话 last_ts 被无效刷新。
        # 仅更新会话的 last_ts 感知活跃度，不写 dm_messages。
        if str(msg_type) == "50001":
            return self.get_or_create(
                conv_id,
                peer_id or self._extract_peer_uid(conv_id),
                peer_name,
            )
        # 2026-09-06 全局治理（脏数据过滤前移到写侧）：
        # 此前过滤只在【读侧】SQL（api/messages.py 的 NOT LIKE），脏数据
        # 依然入库且 unread+1 已累加 ⇒ 会话列表显示未读数，点开却是空白/
        # 内容对不上。这里在写库前拦截，与读侧规则保持一致：
        #   - 系统占位提示（陌生会话首次打开，抖音自动塞入）
        #   - [未知媒体] 解析噪音 / [分享视频] 脏数据 / iesdouyin 分享链接
        # 命中则不写 dm_messages、不累加 unread。
        if _is_noise_text(text):
            logger.debug(f"[recv][{self.name}] 写侧拦截脏数据（不入库/不计未读）: "
                         f"{(text or '')[:40]}")
            return self.get_or_create(
                conv_id,
                peer_id or self._extract_peer_uid(conv_id),
                peer_name,
            )
        ts = time.time()
        with self.lock:
            # peer_id 为空/等于自己 → 从 conv_id 提取对端 UID（WS sender 常空/自己）
            if not peer_id or str(peer_id) == self.my_uid:
                peer_id = self._extract_peer_uid(conv_id) or peer_id
            c = self.get_or_create(conv_id, peer_id, peer_name)
            c.add(role, text, msg_type, extra, ts=ts)
            # 持久化到 SQLite（单条消息 + 会话 last_ts 更新）
            try:
                conn = self._db()
                # 2026-09-06 全局并发治理（双通道重复落库）：
                # 裸 INSERT 会绕过 uniq_dmmsg / uniq_dmmsg_fallback 两个唯一
                # 索引而直接抛 IntegrityError（不是去重）。改 OR IGNORE 后
                # WS 回声与 WP 页面轮询写入同一条消息时自动去重，
                # 与 conversation_capture / 首包补全路径行为一致。
                conn.execute(
                    "INSERT OR IGNORE INTO dm_messages("
                    "account,conv_id,role,text,msg_type,extra,ts,msg_id)"
                    " VALUES(?,?,?,?,?,?,?,?)",
                    (self.name, conv_id, role, text, msg_type,
                     json.dumps(extra or {}, ensure_ascii=False), ts,
                     str(msg_id) if msg_id else None),
                )
                conn.execute(
                    "UPDATE dm_conversations SET last_ts=?,unread=unread+? "
                    "WHERE account=? AND conv_id=?",
                    (ts, 1 if role == "them" else 0, self.name, conv_id),
                )
                # B 机制：运行时收到带 sender_nickname 的新消息，回写会话昵称。
                # 仅在当前 peer_name 为空 / 等于 peer_id(数字) / 等于自己昵称 时覆盖，
                # 避免覆盖首包+im/user/info 已写入的正确昵称。
                # 注意：WS 推送的 sender 常是自己的 UID/昵称（系统会话或自己发出的），
                # 这种 peer_name 不可作为对端昵称，必须排除，否则污染成「自己」。
                if peer_name and peer_name != conv_id and str(peer_name) != str(self.my_uid):
                    conn.execute(
                        "UPDATE dm_conversations SET peer_name=? "
                        "WHERE account=? AND conv_id=? AND "
                        "(peer_name IS NULL OR peer_name='' OR peer_name=? OR peer_name=?)",
                        (peer_name, self.name, conv_id, peer_id, peer_name),
                    )
                conn.commit()
            except Exception as e:
                logger.warning("RECV-004", f"[recv][{self.name}] 消息持久化失败: {e}")
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
        auth = DYLoginApi._load_auth_from_env(self.env_path)
        # WS 长连接同样要求实时 cookie（陈旧 cookie 会 KICK/拒绝）
        DYLoginApi.refresh_cookie_from_profile(auth, self.env_path)
        return auth

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
            # 2026-09-06（2.1b uid 轮换自愈）：每次建连/重连都刷新一次
            # my_uid —— 放在 on_open 而非定时器，零额外开销且覆盖重连场景。
            # uid 轮换后方向判定（sender == my_uid）自动恢复正确。
            try:
                self.inbox._refresh_my_uid()
            except Exception:
                pass

        def on_message(ws, message):
            try:
                self._handle(message)
            except Exception as e:
                logger.warning("RECV-005", f"[recv][{self.name}] 消息解析异常: {e}")
                # 2026-09-13 抓真因：loguru 的 warning(code, detail) 会把 detail
                # 当 format 参数吞掉，只显示错误码。这里把完整堆栈落到独立文件，
                # 便于定位「WS 收到消息但不落库」的真实异常（抓完即移除）。
                try:
                    import traceback
                    _tp = r"C:\temp\dyautodm_test\logs\recv005_trace.log"
                    with open(_tp, "a", encoding="utf-8") as _f:
                        _f.write(
                            "\n===== " + time.strftime('%Y-%m-%d %H:%M:%S')
                            + f" account={self.name} =====\n"
                            + f"ERR: {type(e).__name__}: {e}\n"
                            + traceback.format_exc() + "\n")
                except Exception:
                    pass

        def on_error(ws, error):
            self.inbox.connected = False
            self.inbox.last_error = str(error)
            logger.warning("RECV-006", f"[recv][{self.name}] WS 错误: {error}")
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
            # 2026-09-07：加 ping 保活。实测不加时抖音 imapi 每 ~30s 掐一次
            # 空闲连接（recv_daemon_20260907.log：4 建连/2 断连），频繁重连
            # 既浪费又增加风控暴露。ping_interval=20s < 30s 空闲阈值，
            # ping_timeout=10s：10s 内无 pong 判死重连。
            self._ws.run_forever(
                origin="https://www.douyin.com",
                ping_interval=20,
                ping_timeout=10,
            )
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
            # 注：图片消息结构排查用的 WS 原始帧 dump 已移除（2026-08-31）。
            # 排查方法已固化到知识库 08 §21.5，需要时按该节重建即可。
            text, extra = self._extract(content_json, msg_type)
            if text is None:
                return
            # 过滤系统引导消息(如"微信"/"在哪个地区受伤的"快捷回复建议):
            # 这类消息 msg_type=7 且 msg_id=None(非真实聊天),不应展示在聊天记录中
            if int(msg_type) == 7 and not getattr(msg, "msg_id", None):
                return
            peer_name = content_json.get("sender_nickname") or sender or conv_id
            # 08 §13.5 铁律：方向只能用 sender UID 判断，不可用消息类型推断。
            # sender == 自己 UID → 我发(me)；否则对方发(them)。
            # 自动欢迎语等自己发送的消息 sender 就是 my_uid，硬编码 them 会错配。
            role = "me" if sender and str(sender) == str(self.inbox.my_uid) else "them"
            logger.info(f"[recv][{self.name}][会话 {conv_id[:8]}…] 新消息: peer_name={peer_name}, sender={sender}, role={role}, content_json.sender_nickname={content_json.get('sender_nickname')}")
            self.inbox.add_message(
                conv_id, role, text, peer_id=sender,
                peer_name=peer_name, msg_type=str(msg_type), extra=extra,
                # 2026-09-06 双通道去重：写入抖音消息唯一 ID，让 WS / WP
                # 两条通道写入同一条消息时命中 uniq_dmmsg 唯一索引去重。
                msg_id=str(mid) if (mid := getattr(msg, "msg_id", None)) else None,
            )
            logger.info(f"[recv][{self.name}][会话 {conv_id[:8]}…] {peer_name}: {text}")
        elif frame.payloadType == "text/json":
            try:
                logger.debug(f"[recv][{self.name}] json 控制帧: {json.loads(frame.payload)}")
            except Exception:
                pass

    def _sync_conversations(self, conv_list: list) -> None:
        """把 WS 下发的已有会话列表建立成会话骨架并持久化到 SQLite。

        日志策略：同步帧为高频推送（WS 实时），逐会话打印会刷屏。改为静默处理，
        仅在「出现新会话」（n_new>0）时打印一句摘要，符合「轮询补充静默」要求。
        """
        n_new = 0
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
            logger.info(f"[recv][{self.name}] 同步帧新增 {n_new} 个会话（已静默入库）")

    @staticmethod
    def _extract(content_json: dict, msg_type: Any) -> tuple[str | None, dict]:
        """把 content JSON 按消息类型转成可读文本。返回 (text, extra)。"""
        try:
            t = int(msg_type)
        except Exception:
            t = msg_type
        if t == 7:
            text = content_json.get("text", "") or ""
            # 2026-09-06 系统占位提示过滤（用户实测反馈）：
            # 抖音在「未发过消息的陌生会话」首次被打开时，会自动塞入一条
            # msg_type=7 的占位提示，内容形如：
            #   "对方回复你或互关之前，可发送一条文字消息。请礼貌发言，自觉遵守抖音社区规范"
            # sender 来自对方（陌生人），role=them 被当真实消息入库污染聊天记录。
            # 识别特征：文本含固定模板串（"对方回复你或互关之前"/"请礼貌发言"/
            # "自觉遵守" 三选一即中），一律丢弃返回 None 让调用方不入库。
            _sys_templates = (
                "对方回复你或互关之前",
                "可发送一条文字消息",
                "请礼貌发言",
                "自觉遵守",
            )
            if any(tpl in text for tpl in _sys_templates):
                return None, {}
            return text, {}
        elif t == 5:
            # 表情包：url.url_list[0]，也可能在 resource_url 下
            def _pick(d, *keys):
                if not isinstance(d, dict):
                    return ""
                for k in keys:
                    lst = d.get(k)
                    if isinstance(lst, list) and lst:
                        return lst[0]
                return ""

            u = _pick(content_json.get("url"), "url_list") or _pick(
                content_json.get("resource_url"), "origin_url_list", "url_list"
            )
            if u:
                return f"[表情包] {u}", {}
            return "[表情包]", {}
        elif t == 17:
            try:
                return f"[语音] {content_json['resource_url']['url_list'][0]}", {}
            except Exception:
                return "[语音]", {}
        elif t == 27:
            # 图片：优先原图，其次普通图链；都没有才退化成占位（不能丢 URL，
            # 否则前端无法渲染缩略图预览）
            def _pick(d, *keys):
                if not isinstance(d, dict):
                    return ""
                for k in keys:
                    lst = d.get(k)
                    if isinstance(lst, list) and lst:
                        return lst[0]
                return ""

            res = content_json.get("resource_url")
            u = _pick(res, "origin_url_list", "url_list")
            # 2026-09-01：图片解密要素必须落库（08 §三十五 实机证真）。
            # 抖音 IM 图片是 AES-256-GCM 加密，密钥就在 resource_url.skey；
            # 此前恒返回 {} 导致 skey 丢失、原图永远无法解密。
            # 注意 JSON 内 & 被转义成 \u0026，必须还原，否则带签名的 URL 失效。
            extra = {}
            if isinstance(res, dict) and res.get("skey"):
                _origin = _pick(res, "origin_url_list", "large_url_list",
                                "medium_url_list", "thumb_url_list")
                if _origin:
                    extra = {"skey": res["skey"],
                             "origin_url": _origin.replace("\\u0026", "&")}
            if u:
                return f"[图片] {u}", extra
            u = _pick(content_json.get("origin_url"), "url_list")
            if u:
                return f"[图片] {u}", extra
            return "[图片]", extra
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
# 2026-09-06 全局治理：脏数据判定（写侧与读侧共用的唯一规则源）
# 必须与 api/messages.py 读侧 SQL 的 NOT LIKE 规则保持一致，
# 否则会出现「写侧认为正常入库 + 读侧过滤不显示」⇒ 未读数虚高、点开空白。
_NOISE_PATTERNS = (
    "对方回复你或互关之前",   # 陌生会话系统占位提示
    "可发送一条文字消息",
    "请礼貌发言",
    "自觉遵守",
    "[未知媒体]",                          # 解析噪音
    "[分享视频]",                          # WS 错误解析脏数据
    "https://www.iesdouyin.com/share/",    # 群聊分享链接脏数据
)


def _is_noise_text(text: str | None) -> bool:
    """是否为应丢弃的脏数据/系统占位提示（不入库、不计未读）。"""
    if not text:
        return False
    return any(p in text for p in _NOISE_PATTERNS)


def _safe_capture(name):
    """守护启动补一次捕获（首包解析+写库，不抢 profile）。失败不影响守护运行。"""
    try:
        from auto_dm.conversation_capture import capture_all
        capture_all(name, with_browser=False)
    except Exception as e:
        logger.warning("RECV-007", f"[recv][{name}] 启动前移捕获失败（忽略）: {e}")


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
        logger.error("RECV-008", f"[recv] 数据库初始化失败: {e}")
    for name in _state["accounts"]:
        try:
            env_path = acc.env_path_of(name)
            inbox = AccountInbox(name)
            _state["inboxes"][name] = inbox
            channel = RecvChannel(name, env_path, inbox, auto_reconnect=True)
            _state["channels"][name] = channel
            channel.start()
            logger.info(f"[recv] 账号 {name} 接收通道已启动")
            # 守护启动顺带补一次前移捕获（首包解析写库，不抢 profile）
            try:
                from auto_dm.conversation_capture import capture_all
                threading.Thread(
                    target=lambda: _safe_capture(name), daemon=True
                ).start()
            except Exception as e:
                logger.warning("RECV-009", f"[recv] 启动捕获注册失败: {e}")
        except Exception as e:
            logger.error("RECV-010", f"[recv] 账号 {name} 启动失败: {e}")


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
        # .env 里保存的 cookie 会过期，导致 imapi 返回 50 字节空响应。
        # 拉取前尝试从账号 profile 读取实时 cookie 刷新凭证（失败则退回原凭证）。
        DYLoginApi.refresh_cookie_from_profile(auth, env_path)
        my_uid = str(auth.get_uid())
    except Exception as e:
        logger.warning("RECV-011", f"[recv][{ib.name}] 加载凭证失败: {e}")
        return 0

    # 1) get_message_by_init 拉全量会话（250KB，含全部会话 ID + 消息 + peer uid）
    try:
        raw = DouyinAPI.get_message_by_init(auth)
        if len(raw) < 2000:
            logger.warning("RECV-012", f"[recv][{ib.name}] get_message_by_init 返回 {len(raw)} 字节"
                           f"（非全量，疑似凭证失效/限频）：{raw[:80]}")
        # 新版：protobuf 精确解析（field 6 = conversation 数组，消息内嵌 conv_id 链接键）
        from auto_dm.conversation_capture import parse_init_protobuf
        convs = parse_init_protobuf(raw, my_uid)
    except Exception as e:
        logger.warning("RECV-013", f"[recv][{ib.name}] get_message_by_init 失败: {e}")
        return 0
    if not convs:
        logger.info(f"[recv][{ib.name}] get_message_by_init 返回 0 个会话")
        return 0

    # 2) 写入会话骨架 + 消息到 SQLite + 内存
    n = 0
    n_msg = 0
    with ib.lock:
        try:
            conn = ib._db()
        except Exception:
            conn = None
        for c in convs:
            conv_id = c["conversation_id"]
            peer_uid = c["peer_uid"]
            sec_uid = c.get("sec_uid")
            if conv_id in ib.convs:
                # 已存在：补全 peer_id（WS 建立的骨架可能没有 peer_id）
                if not ib.convs[conv_id].peer_id and peer_uid:
                    ib.convs[conv_id].peer_id = peer_uid
                    if conn:
                        conn.execute(
                            "UPDATE dm_conversations SET peer_id=? WHERE account=? AND conv_id=?",
                            (peer_uid, ib.name, conv_id),
                        )
                # 补全消息（增量）
                if conn and c.get("messages"):
                    for m in c["messages"]:
                        try:
                            conn.execute(
                                "INSERT OR IGNORE INTO dm_messages("
                                "account,conv_id,role,text,msg_type,extra,ts) VALUES(?,?,?,?,?,?,?)",
                                (ib.name, conv_id, m["role"], m["text"], "text", "{}", m["ts"]),
                            )
                            n_msg += 1
                        except Exception:
                            pass
                continue
            # 新会话：建立骨架
            conv = Conversation(conv_id, peer_uid, peer_uid)
            ib.convs[conv_id] = conv
            n += 1
            if conn:
                conn.execute(
                    "INSERT OR IGNORE INTO dm_conversations(account,conv_id,"
                    "peer_id,peer_name,short_id,last_ts,unread,avatar) VALUES(?,?,?,?,?,?,?,?)",
                    (ib.name, conv_id, peer_uid, peer_uid, None, 0, 0, None),
                )
                # 写消息
                for m in c.get("messages", []):
                    try:
                        conn.execute(
                            "INSERT OR IGNORE INTO dm_messages("
                            "account,conv_id,role,text,msg_type,extra,ts) VALUES(?,?,?,?,?,?,?)",
                            (ib.name, conv_id, m["role"], m["text"], "text", "{}", m["ts"]),
                        )
                        n_msg += 1
                    except Exception:
                        pass
        if conn:
            conn.commit()
    logger.info(f"[recv][{ib.name}] get_message_by_init 提取 {len(convs)} 个会话，"
                f"新增 {n} 个，写入消息 {n_msg} 条")
    # 昵称/头像补全：不再走 BCC 批量查（风控），改由 verify_account 时
    # 无头浏览器截 im/user/info 写入；运行时 WS 新消息也会带 sender_nickname。
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


class SendByUidBody(BaseModel):
    account: str
    peer_uid: str | int
    text: str


class SendImageBody(BaseModel):
    account: str
    conv_id: str
    # 图片二进制 base64（≤20MB 原始大小）
    image_b64: str
    filename: str = "image.jpg"


# ============================================================================
# 统一发送闸门（2026-09-06 第五轮治理 P1-A）
# ----------------------------------------------------------------------------
# 背景（09 台账 5.2B1）：同账号三个发送源——① dispatch 直播弹幕私信（backend 进程
# imapi 直发）② AI 智能回复（经本 daemon /send）③ 手动发送（/send）——各自为政，
# 同时活跃时频率叠加，是最现实的频率风控面。
#
# 收敛方式：本 daemon 是所有发送的唯一点（后端 /send、AI 回复都转发到这里），
# 在此加 per-account 令牌闸门，dispatch 也改走 /send_by_uid（见 core/sender.py），
# 三源一配额。
#
# 闸门参数（可调）：
#   DY_SEND_MIN_INTERVAL —— 同账号两次发送的最小间隔（秒），默认 8s。
#   超过等待上限（DY_SEND_MAX_WAIT，默认 30s）仍拿不到令牌则快速失败
#   {ok:false, error:"rate_limited"}，调用方自行决定重试/放弃——绝不静默堆积。
# ============================================================================
# ⚠️ 以下两个常量仅作**兜底**（配置中心不可用时保持接线前行为）。
# 运行时实际值走 `services.app_config`（统一配置中心），每次调用读取
# → 改为热生效：设置页保存后无需重启本 daemon。
_SEND_GATE_MIN_INTERVAL = float(os.environ.get("DY_SEND_MIN_INTERVAL", "8") or 8)
_SEND_GATE_MAX_WAIT = float(os.environ.get("DY_SEND_MAX_WAIT", "30") or 30)


def _cfg_min_interval() -> float:
    """发送闸门最小间隔（配置中心优先，失败回落模块级兜底）。"""
    try:
        from services.app_config import get

        v = get("send", "min_interval")
        if v:
            return float(v)
    except Exception:
        pass
    return _SEND_GATE_MIN_INTERVAL


def _cfg_max_wait() -> float:
    """闸门排队等待上限（配置中心优先，失败回落模块级兜底）。"""
    try:
        from services.app_config import get

        v = get("send", "max_wait")
        if v:
            return float(v)
    except Exception:
        pass
    return _SEND_GATE_MAX_WAIT
_send_gate_lock = threading.Lock()
_send_gate_last: dict[str, float] = {}   # account -> 上次放行时间戳


def _send_gate_acquire(account: str) -> tuple[bool, float]:
    """尝试获取该账号的发送令牌。返回 (ok, 等待秒数)。

    忙等实现（轮询 0.2s）：发送频率低（秒级间隔），锁内 sleep 可接受；
    且保证「先到先得」的发出顺序，避免两个源同时发同一会话时乱序。

    2026-09-08：闸门参数改为**每次调用时**从统一配置中心读取（热生效），
    不再是模块级常量——设置页保存后无需重启本 daemon。
    """
    min_interval = _cfg_min_interval()
    max_wait = _cfg_max_wait()
    deadline = time.time() + max_wait
    waited = 0.0
    while True:
        with _send_gate_lock:
            now = time.time()
            last = _send_gate_last.get(account, 0.0)
            remain = min_interval - (now - last)
            if remain <= 0:
                _send_gate_last[account] = now
                return True, waited
        if now >= deadline:
            return False, waited
        time.sleep(min(0.2, max(remain, 0.05)))
        waited = time.time() - (deadline - max_wait)


def _load_send_auth(account: str, env_path: str):
    """加载发送用 auth 并刷新实时 cookie（/send 与 /send_by_uid 共用）。"""
    from dy_apis.login_api import DYLoginApi
    auth = DYLoginApi._load_auth_from_env(env_path)
    try:
        DYLoginApi.refresh_cookie_from_profile(auth, env_path)
    except Exception as _e:
        logger.warning("RECV-014", f"[recv][{account}] 刷新实时 cookie 失败（沿用 .env）: {_e}")
    return auth


@app.post("/send")
async def send(body: SendBody) -> dict:
    """用该账号的 send_msg 回复（统一发送闸门内，P1-A）。"""
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
    # 2026-09-07：会话整理防线——peer_id 绝不能是本账号 uid。
    # 实测（09 台账第六轮）capture 写入时 peer_id 曾被污染成 my_uid，
    # 若直接拿去 create_conversation 会「发给自己」。此处用 conv_id
    # 重解析真实对端，与 services/dm_dispatch 的整理池保持一致。
    try:
        _my = ""
        try:
            from services.uid_probe import get_uid as _gu
            _my = str(_gu(body.account) or "")
        except Exception:
            pass
        _parts = (body.conv_id or "").split(":")
        if len(_parts) >= 4 and _parts[2] != _parts[3] and _my:
            _real = _parts[3] if _parts[2] == _my else (
                _parts[2] if _parts[3] == _my else None)
            if _real and str(peer_id) != _real:
                logger.warning("RECV-015", 
                    f"[recv][{body.account}] 会话 peer_id 已订正: "
                    f"{peer_id} -> {_real}（conv_id 重解析）")
                peer_id = _real
        if _my and str(peer_id) == _my:
            return {"ok": False,
                    "error": f"拒绝发送：对端 uid 等于本账号 uid（{_my}）"}
    except Exception:
        pass
    if not peer_id:
        return {"ok": False, "error": "无法定位会话对方 uid"}
    env_path = acc.env_path_of(body.account)
    if not env_path:
        return {"ok": False, "error": "账号 .env 路径缺失"}
    # 统一发送闸门：三源（手动/AI/直播 dispatch）一配额
    ok_gate, waited = _send_gate_acquire(body.account)
    if not ok_gate:
        logger.warning("RECV-016", 
            f"[recv][{body.account}] 发送闸门限流：等待 {waited:.0f}s 仍未放行"
            f"（最小间隔 {_cfg_min_interval()}s），快速失败")
        return {"ok": False, "error": "rate_limited",
                "msg": f"发送过于频繁（≥{_cfg_min_interval():.0f}s/条），请稍后重试"}
    try:
        auth = _load_send_auth(body.account, env_path)
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
        logger.warning("RECV-017", f"[recv][{body.account}] 回复失败原因: {detail}")
        return {"ok": False, "error": detail or "send_msg 返回 False（可能触发私信风控）"}
    except Exception as e:
        logger.error("RECV-018", f"[recv][{body.account}] 回复失败: {e}")
        return {"ok": False, "error": str(e)}


@app.post("/send_by_uid")
async def send_by_uid(body: SendByUidBody) -> dict:
    """按对端数字 uid 直发私信（P1-A 新增，直播 dispatch 专用入口）。

    与 /send 同闸门、同凭证链（_load_send_auth），区别只在于定位目标的方式：
    /send 用 conv_id 反查 peer_id（需要会话已存在），本端点直接带 peer_uid——
    直播弹幕捕获的数字 user.id 无需建会话即可直发（对齐 core/sender.send_by_uid）。

    成功后同样落库（会话不存在则创建骨架），保证前端列表可见。
    """
    from dy_apis.douyin_api import DouyinAPI
    from auto_dm import accounts as acc

    ib: AccountInbox | None = _state["inboxes"].get(body.account)
    if not ib:
        return {"ok": False, "error": "账号不存在"}
    env_path = acc.env_path_of(body.account)
    if not env_path:
        return {"ok": False, "error": "账号 .env 路径缺失"}
    try:
        peer_id = int(body.peer_uid)
    except Exception:
        return {"ok": False, "error": f"peer_uid 非数字: {body.peer_uid}"}

    # 统一发送闸门：三源一配额（与 /send 同一把锁）
    ok_gate, waited = _send_gate_acquire(body.account)
    if not ok_gate:
        logger.warning("RECV-019", 
            f"[recv][{body.account}] 发送闸门限流(by_uid)：等待 {waited:.0f}s 未放行")
        return {"ok": False, "error": "rate_limited",
                "msg": f"发送过于频繁（≥{_cfg_min_interval():.0f}s/条），请稍后重试"}
    try:
        auth = _load_send_auth(body.account, env_path)
        conversation_id, conversation_short_id, ticket = DouyinAPI.create_conversation(
            auth, peer_id
        )
        ok, detail = DouyinAPI.send_msg(
            auth, conversation_id, conversation_short_id, ticket, body.text
        )
        if ok:
            # conv_id 骨架：0:1:my_uid:peer_uid（方向判定 / 落库与 WS 侧同构）
            conv_id = f"0:1:{ib.my_uid}:{peer_id}" if ib.my_uid else f"0:1::{peer_id}"
            ib.add_message(conv_id, "me", body.text, peer_id=str(peer_id))
            logger.info(f"[recv][{body.account}] 已直发 uid={peer_id}: {body.text[:40]}")
            return {"ok": True, "conv_id": conv_id}
        logger.warning("RECV-020", f"[recv][{body.account}] 直发 uid={peer_id} 失败: {detail}")
        return {"ok": False, "error": detail or "send_msg 返回 False（可能触发私信风控）"}
    except Exception as e:
        logger.error("RECV-021", f"[recv][{body.account}] 直发 uid={peer_id} 异常: {e}")
        return {"ok": False, "error": str(e)}


@app.post("/send_image")
async def send_image(body: SendImageBody) -> dict:
    """发送图片私信（后端直发全链路 ①-⑥，不依赖浏览器点击）。

    2026-09-05 方案A落地：dy_apis.image_sender（AWS4 SigV4 + protobuf 27 型）。
    2026-09-06 P1-A：纳入统一发送闸门。
    """
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
    # 2026-09-07：会话整理防线（同 /send）：peer_id 绝不能是本账号 uid
    try:
        _my = ""
        try:
            from services.uid_probe import get_uid as _gu
            _my = str(_gu(body.account) or "")
        except Exception:
            pass
        _parts = (body.conv_id or "").split(":")
        if len(_parts) >= 4 and _parts[2] != _parts[3] and _my:
            _real = _parts[3] if _parts[2] == _my else (
                _parts[2] if _parts[3] == _my else None)
            if _real and str(peer_id) != _real:
                peer_id = _real
        if _my and str(peer_id) == _my:
            return {"ok": False,
                    "error": f"拒绝发送：对端 uid 等于本账号 uid（{_my}）"}
    except Exception:
        pass
    if not body.image_b64:
        return {"ok": False, "error": "image_b64 为空"}
    env_path = acc.env_path_of(body.account)
    if not env_path:
        return {"ok": False, "error": "账号 .env 路径缺失"}
    # 统一发送闸门（图片同样计入配额）
    ok_gate, waited = _send_gate_acquire(body.account)
    if not ok_gate:
        return {"ok": False, "error": "rate_limited",
                "msg": f"发送过于频繁（≥{_cfg_min_interval():.0f}s/条），请稍后重试"}
    try:
        import base64 as _b64

        image_data = _b64.b64decode(body.image_b64)
    except Exception as e:
        return {"ok": False, "error": f"image_b64 解码失败: {e}"}
    try:
        auth = _load_send_auth(body.account, env_path)
        from dy_apis.image_sender import send_image

        ok, detail, info = send_image(auth, int(peer_id), image_data,
                                      filename=body.filename or "image.jpg")
        if ok:
            # 落库：与接收侧同构（type=image, extra 含 skey/origin_url），
            # 前端直接复用图片渲染逻辑
            extra = {
                "skey": info.get("skey"),
                "oid": info.get("oid"),
                "md5": info.get("md5"),
                "data_size": info.get("data_size"),
                "width": info.get("width"),
                "height": info.get("height"),
            }
            if info.get("origin_url"):
                extra["origin_url"] = info["origin_url"]
            ib.add_message(body.conv_id, "me", "[图片]", peer_id=peer_id,
                           msg_type="image", extra=extra)
            logger.info(f"[recv][{body.account}] 图片已发送会话 {body.conv_id[:8]}… "
                        f"oid={info.get('oid', '')[:40]}")
            return {"ok": True, "info": {k: info.get(k) for k in
                                         ("oid", "origin_url", "conversation_id")}}
        logger.warning("RECV-022", f"[recv][{body.account}] 图片发送失败: {detail}")
        return {"ok": False, "error": detail or "send_image 返回 False"}
    except Exception as e:
        logger.error("RECV-023", f"[recv][{body.account}] 图片发送异常: {e}")
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
    parser.add_argument("--accounts", required=False, help="账号名列表（逗号分隔）")
    # 2026-09-06 全局治理：兼容 daemon_launcher.py:113 历史 bug
    # 传的是单数 --account（应传 --accounts），加 alias 兜住。
    parser.add_argument("--account", required=False, help="单账号（兼容历史参数）")
    parser.add_argument("--port", type=int, required=True, help="HTTP 控制端口")
    args = parser.parse_args()

    accounts_str = args.accounts or args.account or ""

    _state["accounts"] = [a.strip() for a in accounts_str.split(",") if a.strip()]
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
