# -*- coding: utf-8 -*-
"""MCP HTTP 传输 —— 只监听本机 + Bearer 校验。

## 设计契约（对标 better-douyin `mcp.rs`）

蓝本二进制实测的设计面：
```
"本机 HTTP MCP 已可连接。"
"仅监听本机；端口占用时自动向后探测。"
"显示 Bearer 令牌" / "轮换后旧令牌立即失效"
"每次调用写操作都必须显式确认。"
```

本模块守住三条不变式：
1. **只 bind 127.0.0.1**（绝不 0.0.0.0）——服务永不暴露到公网；
2. **除健康检查外，一切调用必须带有效 Bearer**；
3. **令牌轮换即时失效**（靠 `config.token_epoch`，无需黑名单）。

实现用 `http.server`（标准库）而非引入新依赖 —— 该服务是可选组件，
不应因为「装了个 MCP」就让打包体积再涨（性能主线要求）。
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from loguru import logger

from . import audit as _audit
from . import config as _cfg

HOST = "127.0.0.1"          # 铁律：只监听回环
PORT_SCAN_RANGE = 20        # 端口占用时向后探测的窗口


class _Handler(BaseHTTPRequestHandler):
    server_version = "DYAutoDM-MCP/1.0"
    protocol_version = "HTTP/1.1"

    # 静音默认访问日志（避免刷屏；审计走 audit.py）
    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        return

    # ---------- 工具 ----------

    def _json(self, code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        # 本机服务，无需 CORS；显式禁止被跨站页读取
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _bearer(self) -> str:
        auth = self.headers.get("Authorization", "") or ""
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        return ""

    def _authorized(self) -> bool:
        return _cfg.instance().check_token(self._bearer())

    def _read_body(self) -> dict[str, Any]:
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0:
                return {}
            raw = self.rfile.read(n)
            return json.loads(raw.decode("utf-8")) or {}
        except Exception:
            return {}

    # ---------- 路由 ----------

    def do_GET(self) -> None:  # noqa: N802
        path = (self.path or "").split("?")[0]

        if path in ("/health", "/api/health"):
            cfg = _cfg.instance()
            self._json(200, {"ok": True, "service": "dyautodm-mcp",
                             "enabled": cfg.enabled, "token_epoch": cfg.token_epoch})
            return

        if not self._authorized():
            self._json(401, {"ok": False, "code": "MCP-401",
                             "message": "缺少或无效的 Bearer 令牌"})
            return

        if path in ("/tools", "/api/tools"):
            from .registry import describe
            self._json(200, {"ok": True, "tools": describe()})
            return

        if path in ("/audit", "/api/audit"):
            self._json(200, {"ok": True, "entries": _audit.tail(50),
                             "stats": _audit.stats()})
            return

        self._json(404, {"ok": False, "code": "MCP-404", "message": "未知路径"})

    def do_POST(self) -> None:  # noqa: N802
        path = (self.path or "").split("?")[0]

        if not self._authorized():
            self._json(401, {"ok": False, "code": "MCP-401",
                             "message": "缺少或无效的 Bearer 令牌"})
            return

        body = self._read_body()

        if path in ("/call", "/api/call"):
            from .registry import McpError, call
            name = str(body.get("tool") or "")
            args = body.get("args") or {}
            ticket = str(body.get("ticket") or "")
            try:
                res = call(name, args if isinstance(args, dict) else {}, ticket=ticket)
                self._json(200, res)
            except McpError as e:
                self._json(200, {"ok": False, "code": e.code,
                                 "message": e.message, **e.extra})
            return

        if path in ("/confirm", "/api/confirm"):
            # 为写操作签发一次性确认票据（需已授权）
            from .registry import issue_ticket
            tool = str(body.get("tool") or "")
            t = issue_ticket(tool, body.get("args") or {})
            self._json(200, {"ok": True, **t})
            return

        self._json(404, {"ok": False, "code": "MCP-404", "message": "未知路径"})


def pick_port(preferred: int, scan: int = PORT_SCAN_RANGE) -> int:
    """端口占用时向后探测；全占则回落 0（由系统分配）。"""
    import socket
    for p in range(preferred, preferred + scan):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind((HOST, p))     # 只试回环
                return p
            except OSError:
                continue
    return 0


def serve(port: int | None = None) -> ThreadingHTTPServer:
    """启动服务并返回实例（调用方负责 shutdown）。**仅本机**。"""
    cfg = _cfg.instance()
    want = int(port if port is not None else cfg.get("preferred_port"))
    real = pick_port(want)
    httpd = ThreadingHTTPServer((HOST, real), _Handler)
    httpd.daemon_threads = True
    return httpd


def serve_forever(port: int | None = None) -> None:
    httpd = serve(port)
    host, real = httpd.server_address[0], httpd.server_address[1]
    print(f"[mcp] 本机 HTTP MCP 已启动: http://{host}:{real}  (仅监听本机)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.shutdown()


def start_background(port: int | None = None) -> dict[str, Any]:
    """在后台线程启动（供 backend 进程内挂载用）。

    2026-09-17 修补（审查 P2-13）：返回值新增 `httpd` 句柄（非序列化字段，
    仅进程内使用）。原实现丢弃该句柄，导致 api/mcp.py 的 restart 只能置空
    标记而无法真正 shutdown —— 每次重启都遗留一个 ThreadingHTTPServer 并
    占住回环端口，多次重启后多实例并存却无人知晓。
    """
    httpd = serve(port)
    t = threading.Thread(target=httpd.serve_forever, daemon=True,
                         name="mcp-http")
    t.start()
    return {"host": httpd.server_address[0],
            "port": httpd.server_address[1],
            "httpd": httpd}


def shutdown_httpd(httpd) -> bool:
    """优雅关闭由 start_background 启动的服务。返回是否成功。

    2026-09-17 修补（审查 P2-13）配套。
    """
    if httpd is None:
        return False
    try:
        httpd.shutdown()      # 停止 serve_forever 循环
        httpd.server_close()  # 释放监听套接字
        return True
    except Exception as e:
        logger.warning(f"[mcp] 关闭 MCP 服务失败: {type(e).__name__}: {e}")
        return False
