# coding=utf-8
"""WP 通道「协议层」监听器 —— 替代 JS 注入的被动截获实现（方案 C）。

## 为什么需要本模块（设计意图 / DbC）

**上游契约**（`daemon/browser_daemon.py:capture_wp_messages` 的公开语义）：

    返回 [{kind: 'http'|'ws', url, body, ts}, ...]
    —— kind='http' 时 body 为**响应原文**（JSON 或 protobuf 二进制字符串），
       kind='ws'   时 body 为**WS 帧原文**（字符串；二进制帧以 'B64:' 前缀 + base64）。
    消费方 `daemon/wp_recv.py` 的 `parse_http_init` / `parse_ws_frame` 按此约定解析。

**原实现的传输层**：`CAP_WP_MESSAGE_HOOK_JS` 注入页面，改写
`window.fetch / XMLHttpRequest / WebSocket`。

**问题（2026-09-25 实证）**：`browser_daemon.py:522` 在 Camoufox 模式下
**跳过全部 init script**（`6c9b09e` v0.44.0，为规避抖音「安全风险…已阻止此次访问」
弹窗 —— JS 注入痕迹本身是可被检测的）。而 WP 通道**完全依赖**该 init script，
且**没有 B 计划**（昵称 hook 另有 `CAP_IDB_USERINFO_JS` + `exec_js` 兜底，故昵称
存活、WP 死亡，属**定向失效**）。实证：生产库 `source='wp'` **0 行**，日志
「取回 WP 私信事件」**0 次**，「跳过 JS 注入」**113 次**。

**本模块 = 传输层替换（不是功能新增）**：
    用 patchright **协议层**事件（`context.on("response")` / `page.on("websocket")`
    + `framereceived`）被动读取同一份数据，**零 JS 注入、零页面痕迹** ⇒
    既保住 Camoufox 的反检测优势，又恢复 WP 通道。

**契约不变性**：本模块只负责「取回原始事件」，**解析与落库仍由 wp_recv 负责**
（单一职责：页面/协议侧不做语义解析 —— 与原 hook「页面内不做解析，保持极简」一致）。

## 实测依据（最小复现，非推断）

本机 Camoufox（`AsyncCamoufox`，与项目 `launch_camoufox_async` 同构）下实测：
    context.on('response') 命中且响应体可读（89 B 真实 JSON）；
    page.on('websocket') + framereceived 收到帧。
⇒ 机制成立（脚本见 `cache/scratch/probe_camoufox_protocol_events.py`）。

## 风控边界（红线，必须保持）

纯**被动**读取浏览器**自身已发生**的请求/帧；**绝不**主动发起任何请求，
**绝不**遍历/批量查询用户信息。与 `CAP_WP_MESSAGE_HOOK_JS` 同一风控姿态。
"""
from __future__ import annotations

import asyncio
import base64
import json
import re
import time
from typing import Any

from loguru import logger

# ── IM 接口路径匹配 ─────────────────────────────────────────────────────────
# 与原 JS hook 的 `new RegExp('/(v1|v2)/.*(get_message_by_init|...)')` **逐项对齐**，
# 保证「换传输层不换过滤口径」。按**路径特征**匹配而非绑死域名 —— 抖音真实私信
# 接口在 imapi.douyin.com（知识库 08 §33.1 实证；www.douyin.com/aweme/v1/web/im/...
# 实测 404 Janus）。camoufox 走 Firefox 内核，URL 形态可能含 v1/v2 之外的段落，
# 故此处**同时**保留「路径特征」与「关键字」两条判据（见 _WP_IM_KEYWORDS）。
_WP_IM_PATH_RE = re.compile(
    r"/(?:v1|v2)/.*(?:"
    + "|".join([
        "get_message_by_init", "get_by_conversation", "get_user_message",
        "get_info_list", "mark_read", "message/send",
        "conversation/create", "conversation/info",
    ])
    + r")"
)

# 兜底关键字判据（Firefox 下 URL 若不含 /v1|v2/ 前缀仍可命中）
_WP_IM_KEYWORDS = (
    "get_message_by_init", "get_by_conversation", "get_user_message",
    "get_info_list", "message/send", "conversation/create",
)

# WS 连接 URL 判据 —— 对齐原 JS hook 的 `/im|message|conversation/i` 启发式
_WP_WS_URL_RE = re.compile(r"im|message|conversation", re.I)

# 缓冲区上限（对齐原 JS hook 的 `arr.length > 500` 防爆）
_MAX_EVENTS = 500

# 单条 body 截断上限（对齐原 JS hook 的 `slice(0, 400000)`）
_MAX_BODY = 400000


def fix_ws_text(s: str) -> str:
    """修正 Firefox/juggler 交付 WS 文本帧的编码缺陷。

    ## 实测事实（2026-09-25，本机 Camoufox 最小复现）

    Camoufox（Firefox 内核，经 juggler 驱动）把 WS **文本帧**的 UTF-8 字节按
    **latin-1** 交付 —— 实测发 '在吗：中文测试'，`framereceived` 收到的
    `payload` 是 `str`，但内容为 `'å\\x9c¨å\\x90\\x97ï¼\\x9aä¸\\xadæ\\x96\\x87æµ\\x8bè¯\\x95'`
    （即 UTF-8 字节被逐字节当成 latin-1 码点）。而**同一份数据走 HTTP 响应**
    则完全正常（`resp.text()` 直接得正确中文）⇒ 差异在 WS 文本帧这一条路径上。

    ## 判据（为什么本函数是安全的）

    仅当「原串含非 ASCII」**且**「latin-1→utf-8 还原成功」**且**「还原后不含
    U+FFFD」时才采纳还原结果。三重守卫的效果：
      · 纯 ASCII → 原样返回（零开销，绝大多数帧走这条）
      · **已正确解码**的中文（如 '在吗'）→ `encode('latin-1')` 抛 UnicodeEncodeError
        → 原样返回 ⇒ **绝不会被二次破坏**
      · 真正的 mojibake → 还原出正确中文 ⇒ 采纳
      · 还原出含替换字符（非 UTF-8 字节序列）→ 原样返回 ⇒ 不制造新乱码
    """
    if not s or s.isascii():
        return s
    try:
        fixed = s.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s
    if "\ufffd" in fixed:
        return s
    return fixed


def wp_im_url(url: str | None) -> bool:
    """判定该 URL 是否为抖音 IM 接口（HTTP 侧过滤判据）。"""
    if not url:
        return False
    if _WP_IM_PATH_RE.search(url):
        return True
    return any(k in url for k in _WP_IM_KEYWORDS)


def wp_ws_url(url: str | None) -> bool:
    """判定该 WebSocket URL 是否可能是 IM 长连接（WS 侧过滤判据）。"""
    return bool(url) and bool(_WP_WS_URL_RE.search(url or ""))


class WpProtocolListener:
    """把 patchright 协议层事件归一成 `capture_wp_messages` 的事件列表契约。

    线程/事件循环语义：**必须在创建 context 的同一 asyncio loop 内使用**
    （patchright 回调由该 loop 驱动）。本模块不加锁 —— 全部读写都在该 loop 内，
    加锁反而会与 BCC 的 `_exec` 锁形成不必要的耦合。
    """

    def __init__(self) -> None:
        self._events: list[dict] = []
        self._tasks: set[asyncio.Task] = set()   # 防止 task 被 GC
        self._context: Any = None
        self._pages: set[int] = set()            # 已挂 page 的 id（幂等）
        self._dropped = 0
        self._attached_at = 0.0
        # 诊断计数（不参与判据，仅供观测「协议层是否真在工作」）
        self.stats = {"http_matched": 0, "ws_conn": 0, "ws_frames": 0,
                      "http_errors": 0}

    # ── 生命周期 ────────────────────────────────────────────────────────────
    async def attach(self, context: Any, page: Any = None) -> None:
        """挂载协议层监听。对同一 context 幂等（重复调用只挂一次）。"""
        if context is None:
            raise ValueError("attach: context 不能为空")
        if self._context is context and self._pages:
            if page is not None:
                self._wire_page(page)
            return
        self._context = context
        self._attached_at = time.time()
        # HTTP 响应：context 级（覆盖该 context 内所有 page / tab）
        try:
            context.on("response", self._on_response)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[WP-062] [wp-proto] 挂 context.response 失败: {e}")
        # 新 page 自动挂 WS 监听（导航 tab 等）
        try:
            context.on("page", lambda p: self._wire_page(p))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[WP-062] [wp-proto] 挂 context.page 失败: {e}")
        if page is not None:
            self._wire_page(page)
        logger.info(
            f"[wp-proto] 协议层监听到位（零 JS 注入）"
            f"——HTTP=context.response, WS=page.websocket")

    async def detach(self) -> None:
        """摘除监听（context 重建前调用；失败不抛）。"""
        ctx = self._context
        self._context = None
        self._pages.clear()
        if ctx is not None:
            for _ev, _fn in (("response", self._on_response),):
                try:
                    ctx.remove_listener(_ev, _fn)
                except Exception:  # noqa: BLE001
                    pass
        for t in list(self._tasks):
            try:
                t.cancel()
            except Exception:  # noqa: BLE001
                pass
        self._tasks.clear()

    def drain(self) -> list[dict]:
        """取回并清空事件（读后清空 —— 与原 JS hook 取值语义一致）。"""
        evs = self._events
        self._events = []
        return evs

    @property
    def buffered(self) -> int:
        return len(self._events)

    # ── 内部：page / WS 挂载 ────────────────────────────────────────────────
    def _wire_page(self, page: Any) -> None:
        if page is None:
            return
        try:
            key = id(page)
            if key in self._pages:
                return
            page.on("websocket", self._on_websocket)
            self._pages.add(key)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[wp-proto] 挂 page.websocket 失败: {e}")

    # ── 内部：事件落缓冲 ────────────────────────────────────────────────────
    def _push(self, kind: str, url: str, body: Any) -> None:
        try:
            if body is None:
                return
            if isinstance(body, (bytes, bytearray)):
                body = "B64:" + base64.b64encode(bytes(body)).decode("ascii")
            elif not isinstance(body, str):
                body = str(body)
            if len(body) > _MAX_BODY:
                body = body[:_MAX_BODY]
            ev = {"kind": kind, "url": url or "", "body": body, "ts": time.time()}
            self._events.append(ev)
            if len(self._events) > _MAX_EVENTS:
                n = len(self._events) - _MAX_EVENTS
                del self._events[:n]
                self._dropped += n
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[wp-proto] 入缓冲失败: {e}")

    # ── 回调：HTTP 响应（context 级）────────────────────────────────────────
    def _on_response(self, resp: Any) -> None:
        """同步回调 → 起 task 读体（读体是协程）。异常绝不外抛。"""
        try:
            url = getattr(resp, "url", "") or ""
            if not wp_im_url(url):
                return
            self.stats["http_matched"] += 1
            task = asyncio.create_task(self._consume_response(resp, url))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[wp-proto] response 回调异常（忽略）: {e}")

    async def _consume_response(self, resp: Any, url: str) -> None:
        try:
            txt = await resp.text()
        except Exception:
            # 二进制响应（protobuf 等）→ 取 bytes 并转 base64（消费方按 'B64:' 识别）
            try:
                raw = await resp.body()
                self._push("http", url, raw)
                return
            except Exception as e2:  # noqa: BLE001
                self.stats["http_errors"] += 1
                logger.debug(f"[wp-proto] 读响应体失败（忽略）: {e2}")
                return
        self._push("http", url, txt)

    # ── 回调：WebSocket（page 级）──────────────────────────────────────────
    def _on_websocket(self, ws: Any) -> None:
        try:
            url = getattr(ws, "url", "") or ""
            if not wp_ws_url(url):
                return
            self.stats["ws_conn"] += 1
            ws.on("framereceived", lambda payload: self._on_frame(url, payload))
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[wp-proto] websocket 挂载异常（忽略）: {e}")

    def _on_frame(self, url: str, payload: Any) -> None:
        try:
            self.stats["ws_frames"] += 1
            # 编码修正：Firefox/juggler 交付的 WS 文本帧是 latin-1 误解码形态
            # （实测见 fix_ws_text 文档）。bytes 帧保持原样 → 由 _push 转 'B64:'。
            if isinstance(payload, str):
                payload = fix_ws_text(payload)
            self._push("ws", url, payload)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[wp-proto] WS 帧回调异常（忽略）: {e}")


def stats_json(stats: dict) -> str:
    """诊断用：把计数打成紧凑 JSON（观测协议层是否在工作）。"""
    try:
        return json.dumps(stats, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        return "{}"
