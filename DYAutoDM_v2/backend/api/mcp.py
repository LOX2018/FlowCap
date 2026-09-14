# -*- coding: utf-8 -*-
"""MCP 管理面路由（挂 `/api/mcp`）。

对标 better-douyin `mcp.rs` 的配置面 + 前端 `settings-mcp.tsx` 的管理动作：

| 蓝本 UI 文案 | 本端点 |
|---|---|
| 启用本地 MCP 服务 | `POST /config` (enabled) |
| 首选端口 / 端口占用自动向后探测 | `POST /config` (preferred_port) |
| 显示 Bearer 令牌 / 复制 Bearer 令牌 | `POST /token/reveal` |
| 重新生成令牌 / 轮换后旧令牌立即失效 | `POST /token/rotate` |
| 允许写操作（默认只读） | `POST /config` (allow_write_actions) |
| 每次调用写操作都必须显式确认 | `POST /config` (require_confirmation) |
| 重启 MCP 服务 | `POST /restart` |
| 脱敏记录工具名、字段摘要、耗时和错误码 | `GET /audit` |

**安全约定**：`token/reveal` 是本机 UI 显式动作，返回全量令牌；
其余任何端点只给脱敏串（`token_masked`）。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from loguru import logger
from pydantic import BaseModel

from mcp import audit as mcp_audit
from mcp import config as mcp_config
from mcp import registry as mcp_registry

router = APIRouter()

_RUNTIME: dict[str, Any] = {"httpd": None, "port": None, "thread": None}


class McpConfigBody(BaseModel):
    enabled: bool | None = None
    preferred_port: int | None = None
    allow_write_actions: bool | None = None
    require_confirmation: bool | None = None
    log_retention: int | None = None


def _status() -> dict[str, Any]:
    cfg = mcp_config.instance()
    out = cfg.all_public()
    out["running"] = bool(_RUNTIME.get("httpd"))
    out["bound_port"] = _RUNTIME.get("port")
    out["tools"] = len(mcp_registry.all_tools())
    out["pending_confirmations"] = mcp_registry.pending_tickets()
    out["listen_host"] = "127.0.0.1"
    return out


@router.get("")
async def get_mcp_config() -> dict[str, Any]:
    """读配置 + 运行态（**不含全量令牌**）。"""
    from mcp.tools import register_all
    register_all()
    return {"ok": True, "data": _status()}


@router.post("/config")
async def save_mcp_config(body: McpConfigBody) -> dict[str, Any]:
    """保存配置。端口/开关变更需 `/restart` 才生效（对齐蓝本「重启 MCP 服务」）。"""
    cfg = mcp_config.instance()
    cfg.update(
        enabled=body.enabled,
        preferred_port=body.preferred_port,
        allow_write_actions=body.allow_write_actions,
        require_confirmation=body.require_confirmation,
        log_retention=body.log_retention,
    )
    logger.info("MCP-001", "MCP 配置已保存")
    return {"ok": True, "data": _status()}


@router.post("/token/rotate")
async def rotate_token() -> dict[str, Any]:
    """轮换令牌。**世代号 +1 → 旧令牌立即失效**（无需黑名单）。"""
    cfg = mcp_config.instance()
    cfg.ensure_token(rotate=True)
    logger.info("MCP-002", f"令牌已轮换 epoch={cfg.token_epoch}")
    return {"ok": True, "data": _status(), "message": "令牌已更新，旧令牌已立即失效"}


@router.post("/token/reveal")
async def reveal_token() -> dict[str, Any]:
    """【本机 UI 显式动作】返回全量令牌供复制。

    这是唯一返回明文令牌的端点：由用户在本机界面主动点击触发，
    不经过任何 AI 客户端路径。
    """
    cfg = mcp_config.instance()
    token = cfg.reveal_token()
    return {"ok": True, "token": token, "token_epoch": cfg.token_epoch}


@router.post("/restart")
async def restart_mcp() -> dict[str, Any]:
    """按当前配置启动/重启本机 HTTP 服务。"""
    from mcp.server import start_background
    cfg = mcp_config.instance()
    _stop_runtime()
    if not cfg.enabled:
        return {"ok": True, "data": _status(), "message": "MCP 未启用，服务未启动"}
    cfg.ensure_token()
    try:
        info = start_background(int(cfg.get("preferred_port")))
        _RUNTIME["port"] = info["port"]
        _RUNTIME["httpd"] = True  # 标记运行中（线程生命周期随进程）
        logger.info("MCP-003", f"本机 HTTP MCP 已启动 :{info['port']}（仅监听本机）")
        return {"ok": True, "data": _status(),
                "message": f"已启动，端口 {info['port']}"}
    except Exception as e:
        logger.warning("MCP-004", f"MCP 服务启动失败: {e}")
        return {"ok": False, "message": f"启动失败: {type(e).__name__}"}


def _stop_runtime() -> None:
    _RUNTIME["httpd"] = None
    _RUNTIME["port"] = None


@router.get("/tools")
async def list_tools() -> dict[str, Any]:
    """工具清单（含 READ/WRITE 分级，让用户看清哪些是写操作）。"""
    from mcp.tools import register_all
    register_all()
    return {"ok": True, "tools": mcp_registry.describe()}


@router.post("/confirm")
async def issue_confirm(body: dict[str, Any]) -> dict[str, Any]:
    """为一次写操作签发一次性确认票据（对应蓝本 require_confirmation）。"""
    tool = str((body or {}).get("tool") or "")
    if mcp_registry.get(tool) is None:
        return {"ok": False, "message": f"工具不存在: {tool}"}
    t = mcp_registry.issue_ticket(tool, (body or {}).get("args") or {})
    return {"ok": True, **t, "message": "确认票据已签发（一次性，120s 内有效）"}


@router.get("/audit")
async def get_audit(limit: int = 50) -> dict[str, Any]:
    """审计日志（**脱敏**：只有工具名/字段摘要/耗时/错误码）。"""
    return {"ok": True, "entries": mcp_audit.tail(limit),
            "stats": mcp_audit.stats()}


@router.post("/audit/trim")
async def trim_audit() -> dict[str, Any]:
    """按 log_retention 裁剪审计文件。"""
    cfg = mcp_config.instance()
    n = mcp_audit.trim(int(cfg.get("log_retention")))
    return {"ok": True, "kept": n}


@router.delete("/audit")
async def clear_audit() -> dict[str, Any]:
    mcp_audit.clear()
    return {"ok": True, "message": "审计日志已清空"}
