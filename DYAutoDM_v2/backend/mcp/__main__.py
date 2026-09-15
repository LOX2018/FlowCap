# -*- coding: utf-8 -*-
"""MCP stdio 入口 —— 对齐蓝本的单入口形态。

## 设计来源（对标 better-douyin `src/bin/douyin-dl.rs`）

蓝本二进制里的 CLI 原文：
```
douyin-dl
Cli: "Better Douyin MCP server — Starts the Better Douyin MCP server over stdio.
      Other automation entrypoints are intentionally not exposed."
子命令: serve → "Serve MCP over stdio"
协议:   MCP JSON-RPC，protocolVersion 2025-03-26
```

**关键设计选择照抄蓝本**：自动化只暴露**一个**入口（stdio MCP），
其余能力（HTTP 服务、端口扫描）都不作为独立命令暴露 —— 用「进程边界 + 单一入口」
收紧攻击面。

## 用法

    python -m backend.mcp serve          # stdio（给 Claude Code / Codex 等）
    python -m backend.mcp http [--port]  # 本机 HTTP（浏览器/其他客户端）

stdio 模式下 stdout **只允许**输出 JSON-RPC 报文（协议要求），
所有日志走 stderr。
"""
from __future__ import annotations

import json
import sys
from typing import Any

PROTOCOL_VERSION = "2025-03-26"
SERVER_NAME = "dyautodm-mcp"
SERVER_VERSION = "1.0.0"


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


# ---------------- JSON-RPC over stdio ----------------

def _result(rid: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _error(rid: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def _handle(req: dict[str, Any]) -> dict[str, Any] | None:
    from .registry import McpError, call, describe, get

    method = str(req.get("method") or "")
    rid = req.get("id")
    params = req.get("params") or {}

    # 通知类（无 id）不回复
    if method.startswith("notifications/"):
        return None

    if method == "initialize":
        return _result(rid, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })

    if method in ("tools/list", "list_tools"):
        return _result(rid, {"tools": [
            {"name": t["name"], "description": t["summary"],
             "inputSchema": {"type": "object", "properties": {
                 k: {"type": "string", "description": v}
                 for k, v in (t.get("params") or {}).items()}}}
            for t in describe()
        ]})

    if method in ("tools/call", "call_tool"):
        name = str(params.get("name") or "")
        args = params.get("arguments") or {}
        ticket = str(params.get("ticket") or "")
        if get(name) is None:
            return _error(rid, -32601, f"工具不存在: {name}")
        try:
            res = call(name, args if isinstance(args, dict) else {}, ticket=ticket)
            return _result(rid, {
                "content": [{"type": "text",
                             "text": json.dumps(res, ensure_ascii=False)}],
                "isError": not res.get("ok", False),
            })
        except McpError as e:
            return _result(rid, {
                "content": [{"type": "text",
                             "text": json.dumps({"ok": False, "code": e.code,
                                                 "message": e.message},
                                                ensure_ascii=False)}],
                "isError": True,
            })

    if method == "ping":
        return _result(rid, {})

    return _error(rid, -32601, f"未知方法: {method}")


def serve_stdio() -> int:
    """从 stdin 读 JSON-RPC 行，向 stdout 写应答。"""
    from .tools import register_all
    register_all()
    _log(f"[mcp] stdio 服务就绪（protocolVersion={PROTOCOL_VERSION}）")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            print(json.dumps(_error(None, -32700, "JSON 解析失败")), flush=True)
            continue
        try:
            resp = _handle(req)
        except Exception as e:  # noqa: BLE001
            resp = _error(req.get("id"), -32603, f"内部错误: {type(e).__name__}")
        if resp is not None:
            print(json.dumps(resp, ensure_ascii=False), flush=True)
    return 0


def _main(argv: list[str]) -> int:
    from .tools import register_all

    cmd = argv[0] if argv else "serve"
    if cmd in ("serve", "stdio"):
        return serve_stdio()

    if cmd == "http":
        port = None
        if "--port" in argv:
            try:
                port = int(argv[argv.index("--port") + 1])
            except Exception:
                port = None
        register_all()
        from .server import serve_forever
        serve_forever(port)
        return 0

    if cmd in ("-h", "--help", "help"):
        print("用法: python -m backend.mcp [serve|http [--port N]]")
        print("  serve  默认；MCP JSON-RPC over stdio")
        print("  http   本机 HTTP 服务（仅 127.0.0.1）")
        return 0

    _log(f"未知子命令: {cmd}（仅暴露 serve / http —— 对齐蓝本单入口设计）")
    return 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
