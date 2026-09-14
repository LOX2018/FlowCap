# -*- coding: utf-8 -*-
"""MCP 工具注册表 —— READ / WRITE 分级 + 写操作确认闸。

## 设计契约（对标 better-douyin `mcp.rs`）

蓝本二进制实测出的权限模型：
```
allow_write_actions   = false    # 默认只读
require_confirmation  = true     # 写操作需显式确认
```
本项目把它落成**声明式注册表**：每个工具声明自己的风险级别与后端落点，
写工具在 `allow_write_actions=false` 时**物理不可达**；即使开了写，
`require_confirmation=true` 时仍需一次性确认票据。

## 关于「不采纳」的边界

蓝本暴露 `/aweme/v1/web/im/user/info/`（主动拉昵称）。
**本项目不注册任何主动查询昵称的工具** —— 见 `docs/replication_plan.md` §四。
工具面只包装既有业务能力。
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

READ = "read"
WRITE = "write"

# 确认票据有效期（秒）：一次确认只对一张票、一个工具生效
CONFIRM_TTL_SEC = 120


@dataclass
class Tool:
    name: str
    level: str                       # READ / WRITE
    summary: str
    handler: Callable[..., Any]      # 实际执行体
    params: dict[str, str] = field(default_factory=dict)  # 名称 -> 说明
    # 审计摘要字段：哪些参数允许记入审计（其余一律只记「已省略」）
    audit_fields: tuple[str, ...] = ()


@dataclass
class _Ticket:
    tool: str
    expires_at: float
    payload: dict[str, Any]


_REGISTRY: dict[str, Tool] = {}
_TICKETS: dict[str, _Ticket] = {}
_LOCK = threading.RLock()


def register(tool: Tool) -> Tool:
    with _LOCK:
        _REGISTRY[tool.name] = tool
    return tool


def get(name: str) -> Tool | None:
    with _LOCK:
        return _REGISTRY.get(name)


def all_tools() -> list[Tool]:
    with _LOCK:
        return sorted(_REGISTRY.values(), key=lambda t: t.name)


class McpError(Exception):
    """MCP 调用错误 —— 带错误码，便于审计与前端展示。"""

    def __init__(self, code: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.extra = extra


# ---------------- 确认票据 ----------------

def _prune_tickets(now: float | None = None) -> None:
    now = now or time.time()
    for k in [k for k, t in _TICKETS.items() if t.expires_at <= now]:
        _TICKETS.pop(k, None)


def issue_ticket(tool: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """为一次写操作签发一次性确认票据。"""
    with _LOCK:
        _prune_tickets()
        tid = uuid.uuid4().hex[:16]
        _TICKETS[tid] = _Ticket(tool=tool, expires_at=time.time() + CONFIRM_TTL_SEC,
                                payload=dict(payload or {}))
        return {"ticket": tid, "tool": tool, "expires_in": CONFIRM_TTL_SEC}


def consume_ticket(tid: str, tool: str) -> bool:
    """核销票据：**无论成功与否都删除**（一次性语义，防重放）。"""
    with _LOCK:
        _prune_tickets()
        t = _TICKETS.pop(tid, None)
        if t is None:
            return False
        return t.tool == tool


def pending_tickets() -> int:
    with _LOCK:
        _prune_tickets()
        return len(_TICKETS)


# ---------------- 调用主流程 ----------------

def _audit_summary(tool: Tool, args: dict[str, Any]) -> dict[str, Any]:
    """只输出**声明的**字段名与类型长度，绝不回显原始值（脱敏约定）。"""
    out: dict[str, Any] = {"arg_keys": sorted(args.keys())}
    for f in tool.audit_fields:
        if f in args:
            v = args[f]
            out[f] = (len(v) if isinstance(v, (str, list, dict)) else type(v).__name__)
    return out


def call(name: str, args: dict[str, Any] | None = None, *,
         ticket: str = "") -> dict[str, Any]:
    """统一调用入口：分级 → 确认 → 执行 → 审计。

    返回结构固定为 {ok, code, data|message, elapsed_ms, audit}，
    使调用方与审计口径一致。
    """
    from . import audit as _audit
    from . import config as _cfg

    args = dict(args or {})
    t0 = time.monotonic()
    tool = get(name)
    if tool is None:
        _audit.record(name, ok=False, code="MCP-001", elapsed_ms=0,
                      summary={"arg_keys": sorted(args.keys())})
        raise McpError("MCP-001", f"工具不存在: {name}")

    cfg = _cfg.instance()

    # 闸门 1：写操作必须显式开启（默认只读 → 物理不可达）
    if tool.level == WRITE and not cfg.allow_write:
        ms = int((time.monotonic() - t0) * 1000)
        _audit.record(name, ok=False, code="MCP-002", elapsed_ms=ms,
                      summary=_audit_summary(tool, args))
        raise McpError("MCP-002", "写操作未开启（allow_write_actions=false）",
                       tool=name)

    # 闸门 2：写操作需一次性确认票据
    if tool.level == WRITE and cfg.require_confirmation:
        if not ticket or not consume_ticket(ticket, name):
            ms = int((time.monotonic() - t0) * 1000)
            _audit.record(name, ok=False, code="MCP-003", elapsed_ms=ms,
                          summary=_audit_summary(tool, args))
            raise McpError("MCP-003", "缺少或已失效的确认票据（需先确认）",
                           tool=name)

    try:
        data = tool.handler(**args)
        ms = int((time.monotonic() - t0) * 1000)
        _audit.record(name, ok=True, code="", elapsed_ms=ms,
                      summary=_audit_summary(tool, args))
        return {"ok": True, "code": "", "data": data, "elapsed_ms": ms}
    except McpError:
        raise
    except Exception as e:  # noqa: BLE001 —— 执行体异常必须转成结构化错误
        ms = int((time.monotonic() - t0) * 1000)
        _audit.record(name, ok=False, code="MCP-009", elapsed_ms=ms,
                      summary=_audit_summary(tool, args))
        raise McpError("MCP-009", f"工具执行失败: {type(e).__name__}",
                       tool=name) from e


def describe() -> list[dict[str, Any]]:
    """工具清单（MCP tools/list 的数据源）。"""
    return [{
        "name": t.name,
        "level": t.level,
        "summary": t.summary,
        "params": t.params,
    } for t in all_tools()]
