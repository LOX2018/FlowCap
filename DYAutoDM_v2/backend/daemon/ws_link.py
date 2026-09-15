# coding=utf-8
"""WS 长连接稳态治理（2026-09-16 v0.43.36）。

设计契约
--------
把「连接生命周期」从消息解析中彻底剥离：`RecvChannel` 只负责协议解析，
建连 / 保活 / 断线检测 / 重连退避 / 重连后追赶全部收敛到本模块。

为什么必须独立成模块（根因回溯）
--------------------------------
`recv_daemon.py` 原实现把生命周期散在 `on_error` / `on_close` / `_restart_ws`
三处，且 **回调线程内直接 `time.sleep` + 递归调用 `_restart_ws`**，造成：

  1. **递归重连**：`on_error → sleep(5) → _restart_ws → run_forever → on_error …`
     每轮栈 +1 层，实测 9/15 19:20:48 同秒出现 3 次 CLOSE。
  2. **30s 定时自杀**：`run_forever(ping_interval=20, ping_timeout=10)` 依赖
     协议层 Ping/Pong。而 frontier-im **不回 Pong**（`last_pong_tm` 恒 0），
     websocket-client `_app.py:502-522` 的
     `has_pong_not_arrived_after_last_ping = last_pong_tm - last_ping_tm < 0`
     恒成立 ⇒ 20+10=30s 必然抛 `ping/pong timed out`。
     实机验证（本地 mock 服务端收 Ping 不回 Pong，同参数）：**精确 t+30.0s 断连**，
     与抖音日志 `OPEN +30s` 完全吻合。
  3. **无追赶**：重连后不补拉，掉线期消息永久丢失（WS 不重推历史）。

四层修复
--------
L0 关闭协议层 ping（消除定时自杀）
L1 应用层心跳 `PushFrame(payloadType="hb")`（同 `dy_live` 已验证做法）
L2 单一重连循环 + 指数退避 + jitter（消除递归/风暴）
L3 重连后按节流补拉（HTTP 2043 首包，INSERT OR IGNORE 去重）
"""
from __future__ import annotations

import random
import threading
import time
from typing import Any, Callable

from loguru import logger

# --- 可调参数（环境变量覆盖，便于实机调参，不写死） -------------------------
# 应用层心跳间隔：抖音直播侧实测 5s 稳；私信侧保守取 15s（< 服务端空闲阈值）。
HB_INTERVAL = float(__import__("os").environ.get("DY_WS_HB_INTERVAL", "15") or 15)
# 心跳模式：hb=应用层 hb 帧（默认，直播同协议族已验证）；off=不发（仅 L0+L2）
HB_MODE = (__import__("os").environ.get("DY_WS_HB_MODE", "hb") or "hb").lower()
# 死连接判定：超过该时长未收到任何下行帧 → 判定半开，主动重连。
# 业务低峰本就无消息，靠心跳上行失败 + 本超时双保险。
DEAD_TIMEOUT = float(__import__("os").environ.get("DY_WS_DEAD_TIMEOUT", "120") or 120)
# 重连退避：base * 2^n，封顶 max，加 ±20% jitter 防同账号多实例齐步重连。
BACKOFF_BASE = float(__import__("os").environ.get("DY_WS_BACKOFF_BASE", "2") or 2)
BACKOFF_MAX = float(__import__("os").environ.get("DY_WS_BACKOFF_MAX", "60") or 60)
# 连续成功在线超过该时长则重置退避计数（网络已恢复，不必再慢慢退避）
BACKOFF_RESET_AFTER = float(
    __import__("os").environ.get("DY_WS_BACKOFF_RESET_AFTER", "120") or 120)
# 重连后补拉的最小间隔（秒）：频繁重连时避免打爆 HTTP 接口
CATCHUP_MIN_INTERVAL = float(
    __import__("os").environ.get("DY_WS_CATCHUP_MIN_INTERVAL", "120") or 120)


def _peer_closed(sock) -> bool:
    """探测对端是否已关闭（半开连接检测）。

    为什么需要：`ws.send()` 只把数据写进内核发送缓冲，即使**对端已经发过
    FIN** 也会「成功」返回，因此「心跳发送无异常」并不能证明连接还活着。
    这是 recv_daemon 历史上反复看不出的坑。

    原理：对端关闭后在内核缓冲留下可读 EOF，`recv(MSG_PEEK)` 返回 b""
    即代表对端已关（非阻塞，不影响正常数据 —— 有真实数据时返回非空，
    我们不消费它，留给 dispatcher 线程读取）。

    2026-09-16 实机引入：替代「仅按 rx_age 超时判死」的旧判据 ——
    私信业务低峰本就长期零下行，旧判据把健康连接误杀（实测 240s 误杀 2 次）。
    """
    try:
        import errno
        import socket as _so
        raw = getattr(sock, "sock", sock)      # WebSocket 包装 → 裸 socket
        if raw is None:
            return False
        prev = raw.gettimeout()
        raw.settimeout(0)                      # 非阻塞 peek
        try:
            data = raw.recv(1, _so.MSG_PEEK)   # 只看不取
            return data == b""                 # EOF = 对端已关
        except BlockingIOError:
            return False                       # 无数据可读 = 还活着
        except OSError as e:
            # EAGAIN/EWOULDBLOCK 同上；其它错误交给上层
            if getattr(e, "errno", None) in (
                    getattr(errno, "EAGAIN", 11),
                    getattr(errno, "EWOULDBLOCK", 11)):
                return False
            return True
        finally:
            try:
                raw.settimeout(prev)
            except Exception:
                pass
    except Exception:
        return False


class Backoff:
    """指数退避 + jitter。成功在线超时重置计数。"""
    def __init__(self, base: float = BACKOFF_BASE,
                 cap: float = BACKOFF_MAX,
                 reset_after: float = BACKOFF_RESET_AFTER) -> None:
        self.base = base
        self.cap = cap
        self.reset_after = reset_after
        self._n = 0
        self._online_since = 0.0

    def mark_online(self) -> None:
        self._online_since = time.time()

    def next(self) -> float:
        """返回本次重连应等待的秒数，并递增退避计数。"""
        now = time.time()
        # 稳定在线足够久 → 视为网络已恢复，退避计数归零
        if self._online_since and now - self._online_since >= self.reset_after:
            self._n = 0
        d = min(self.base * (2 ** self._n), self.cap)
        self._n += 1
        # ±20% jitter
        return d * (0.8 + 0.4 * random.random())


class WSLink:
    """一条 WS 长连接的完整生命周期管理（单循环，绝不递归）。

    用法::

        link = WSLink(
            name=account,
            make_ws=build_ws_app,          # 返回新的 WebSocketApp
            on_message=handle_bytes,        # 下行帧回调
            on_connected=after_open,        # 建连后（含重连）回调，用于追赶
            on_disconnected=mark_offline,
        )
        link.start()   # 后台线程，非阻塞
        link.stop()
    """

    def __init__(self, name: str,
                 make_ws: Callable[[], Any],
                 on_message: Callable[[bytes], None],
                 on_connected: Callable[[], None] | None = None,
                 on_disconnected: Callable[[str], None] | None = None) -> None:
        self.name = name
        self._make_ws = make_ws
        self._on_message = on_message
        self._on_connected = on_connected
        self._on_disconnected = on_disconnected

        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

        self._ws: Any = None                 # 当前 WebSocketApp
        self._ws_lock = threading.Lock()
        self._hb_stop: threading.Event | None = None
        self._hb_thread: threading.Thread | None = None

        self._backoff = Backoff()
        self._last_rx = 0.0                  # 最后一次收到下行帧的时间
        self._connected_at = 0.0
        # 可观测性：给 /status 用
        self.stats: dict[str, Any] = {
            "connects": 0, "disconnects": 0, "hb_sent": 0, "hb_failed": 0,
            "last_error": "", "connected": False,
            "last_rx_age": 0.0, "backoff_stage": 0,
        }

    # ------------------------------------------------------------------ 生命周期
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._loop, name=f"wslink-{self.name}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._stop_hb()
        with self._ws_lock:
            ws = self._ws
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass

    # ------------------------------------------------------------------ 主循环
    def _loop(self) -> None:
        """唯一重连循环。回调只置状态，绝不在此递归。"""
        while not self._stop.is_set():
            try:
                self._connect_once()
            except Exception as e:
                self.stats["last_error"] = f"{type(e).__name__}: {e}"
                logger.warning(
                    "RECV-030",
                    f"[recv][{self.name}] WS 建连异常: {type(e).__name__}: {e}")
            if self._stop.is_set():
                break
            delay = self._backoff.next()
            self.stats["backoff_stage"] = self._backoff._n
            logger.info(
                f"[recv][{self.name}] {delay:.1f}s 后重连"
                f"（第 {self._backoff._n} 次退避）")
            # 可中断等待：stop() 能立刻生效，不必等满退避
            if self._stop.wait(delay):
                break

    def _connect_once(self) -> None:
        """建立一次连接并阻塞到其断开。"""
        ws = self._make_ws()
        self._bind_callbacks(ws)
        with self._ws_lock:
            self._ws = ws
        self._last_rx = time.time()
        self._start_hb()

        # L0：ping_interval 默认 0 = 不发协议层 Ping。
        # 绝不可再传 ping_timeout —— frontier-im 不回 Pong，传了必然 30s 自杀。
        ws.run_forever(origin="https://www.douyin.com")

        self._stop_hb()
        self.stats["connected"] = False
        self.stats["disconnects"] += 1
        if self._on_disconnected:
            try:
                self._on_disconnected(self.stats.get("last_error", ""))
            except Exception:
                pass

    # ------------------------------------------------------------------ 回调
    def _bind_callbacks(self, ws) -> None:
        """由 WSLink 统一注入回调（L2 关键：生命周期不依赖调用方自觉）。

        设计要点：`make_ws` 只负责**造一个 app**，回调绑定权归 WSLink。
        若把绑定责任留给调用方，一旦漏绑 cb_open，就会出现
        「连上了但 connects=0、退避永远不重置」的静默故障 —— 实机验证
        （_verify_ws_link.py 首轮）正是因此 FAIL。

        调用方已绑定的回调会被**包裹而非覆盖**（先跑原回调，再跑本类的
        生命周期钩子），保证业务侧逻辑不丢。
        """
        prev = {
            "open": getattr(ws, "on_open", None),
            "message": getattr(ws, "on_message", None),
            "error": getattr(ws, "on_error", None),
            "close": getattr(ws, "on_close", None),
        }

        def _run(fn, *a):
            if fn:
                try:
                    fn(*a)
                except Exception as e:
                    logger.warning(
                        "RECV-037",
                        f"[recv][{self.name}] 用户回调异常: {e}")

        def _open(ws_):
            _run(prev["open"], ws_)
            self.cb_open(ws_)

        def _message(ws_, m):
            _run(prev["message"], ws_, m)
            self.cb_message(ws_, m)

        def _error(ws_, e):
            _run(prev["error"], ws_, e)
            self.cb_error(ws_, e)

        def _close(ws_, *a):
            _run(prev["close"], ws_, *a)
            self.cb_close(ws_, *a)

        ws.on_open = _open
        ws.on_message = _message
        ws.on_error = _error
        ws.on_close = _close

    def cb_open(self, ws) -> None:
        self.stats["connects"] += 1
        self.stats["connected"] = True
        self.stats["last_error"] = ""
        self._last_rx = time.time()
        self._connected_at = time.time()
        self._backoff.mark_online()
        logger.info(f"[recv][{self.name}] 私信长连接已建立"
                    f"（累计第 {self.stats['connects']} 次）")
        # L3：重连后追赶（首连也调，等价于启动补拉）
        if self._on_connected:
            try:
                self._on_connected()
            except Exception as e:
                logger.warning("RECV-031",
                               f"[recv][{self.name}] 建连后回调失败: {e}")

    def cb_message(self, ws, message) -> None:
        self._last_rx = time.time()
        try:
            self._on_message(message)
        except Exception as e:
            logger.warning("RECV-005",
                           f"[recv][{self.name}] 消息解析异常: {e}")

    def cb_error(self, ws, error) -> None:
        self.stats["last_error"] = str(error)
        logger.warning("RECV-006", f"[recv][{self.name}] WS 错误: {error}")

    def cb_close(self, ws, *args) -> None:
        self.stats["connected"] = False
        code = args[0] if args else None
        logger.info(f"[recv][{self.name}] WS 关闭（code={code}）")

    # ------------------------------------------------------------------ L1 应用层心跳
    def _start_hb(self) -> None:
        self._stop_hb()
        if HB_MODE == "off":
            return
        ev = threading.Event()
        self._hb_stop = ev
        t = threading.Thread(target=self._hb_loop, args=(ev,),
                             name=f"wshb-{self.name}", daemon=True)
        self._hb_thread = t
        t.start()

    def _stop_hb(self) -> None:
        if self._hb_stop is not None:
            self._hb_stop.set()
        self._hb_stop = None
        self._hb_thread = None

    def _hb_loop(self, ev: threading.Event) -> None:
        """应用层心跳 + 死连接看门狗。

        心跳：仿 `dy_live/server.py:34-47` 已验证做法，发
        `PushFrame(payloadType="hb")` 二进制帧（opcode=0x02）。
        看门狗：久无下行 → 判半开，主动 close 让主循环重连。

        ⚠️ 2026-09-16 实机修正（真实抖音环境）：看门狗**不能只看下行**。
        私信业务低峰本就长时间零下行（实测 rx_age 达 128s 而连接完全健康），
        只看下行会把健康连接当成半开误杀 —— 实测 240s 内被误杀 2 次
        （RECV-032 128s / 133s），反而制造了新的不稳定。
        正确判据：**心跳发送成功即证明 socket 仍可写**，此时不判死。
        只有「心跳连续失败」或「连接对象已断开」才该重连。
        """
        from static import Live_pb2
        while not ev.is_set():
            if ev.wait(HB_INTERVAL):
                return
            # --- 死连接判定（先判，避免给僵尸连接发心跳） ---
            with self._ws_lock:
                ws = self._ws
            if ws is None:
                return
            age = time.time() - self._last_rx
            self.stats["last_rx_age"] = age
            sock = getattr(ws, "sock", None)
            if sock is None or not getattr(sock, "connected", False):
                logger.warning(
                    "RECV-032",
                    f"[recv][{self.name}] socket 已断开，触发重连")
                try:
                    ws.close()
                except Exception:
                    pass
                return
            # 半开检测（真正的死连接判据）：对端关闭时 TCP 会发 FIN，
            # 内核缓冲留下 0 字节可读；用非阻塞 peek 探测即可发现。
            # 这是唯一能识别「socket 可写但对端已走」的手段 —— 因为
            # ws.send() 只写内核缓冲，对端已关也照样「成功」。
            try:
                if _peer_closed(sock):
                    logger.warning(
                        "RECV-032",
                        f"[recv][{self.name}] 探测到对端已关闭（半开连接），"
                        f"触发重连（rx_age={age:.0f}s）")
                    try:
                        ws.close()
                    except Exception:
                        pass
                    return
            except Exception:
                pass
            # 兜底：心跳连续失败且长时间无下行 → 判死（叠加条件防误杀）
            if (self.stats["hb_failed"] > 0
                    and self.stats["hb_failed"] % 3 == 0
                    and age > DEAD_TIMEOUT):
                logger.warning(
                    "RECV-032",
                    f"[recv][{self.name}] {age:.0f}s 未收到下行帧且心跳连续"
                    f"失败 {self.stats['hb_failed']} 次，判定半开连接，主动重连")
                try:
                    ws.close()
                except Exception:
                    pass
                return
            # --- 发心跳 ---
            if HB_MODE != "hb":
                continue
            try:
                frame = Live_pb2.PushFrame()
                frame.payloadType = "hb"
                ws.send(frame.SerializeToString(), opcode=0x02)
                self.stats["hb_sent"] += 1
            except Exception as e:
                self.stats["hb_failed"] += 1
                # 连续失败即断开交给主循环重连；日志降噪：每 5 次打一条
                if self.stats["hb_failed"] % 5 == 1:
                    logger.warning(
                        "RECV-033",
                        f"[recv][{self.name}] 心跳发送失败"
                        f"（第 {self.stats['hb_failed']} 次）: {e}")
                try:
                    ws.close()
                except Exception:
                    pass
                return
