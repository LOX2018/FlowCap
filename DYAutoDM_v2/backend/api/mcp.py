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

import os
import secrets
from typing import Any

from fastapi import APIRouter, Request
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
async def save_mcp_config(body: McpConfigBody, request: Request) -> dict[str, Any]:
    """保存配置。端口/开关变更需 `/restart` 才生效（对齐蓝本「重启 MCP 服务」）。

    2026-09-17 修补（审查 P1-7）：本端点可把 allow_write_actions 改成 true，
    属敏感操作，加入管理员令牌校验（可选启用）。
    """
    if not _require_mcp_admin(request):
        return {"ok": False, "message": "需要管理员令牌"}
    cfg = mcp_config.instance()
    cfg.update(
        enabled=body.enabled,
        preferred_port=body.preferred_port,
        allow_write_actions=body.allow_write_actions,
        require_confirmation=body.require_confirmation,
        log_retention=body.log_retention,
    )
    logger.info(f"[MCP-001] " + "MCP 配置已保存")
    return {"ok": True, "data": _status()}


@router.post("/token/rotate")
async def rotate_token(request: Request) -> dict[str, Any]:
    """轮换令牌。**世代号 +1 → 旧令牌立即失效**（无需黑名单）。

    2026-09-17 修补（审查 P1-7）：轮换会使所有已配置的 AI 客户端失效，
    属敏感操作，加入管理员令牌校验（可选启用）。
    """
    if not _require_mcp_admin(request):
        return {"ok": False, "message": "需要管理员令牌"}
    cfg = mcp_config.instance()
    cfg.ensure_token(rotate=True)
    logger.info(f"[MCP-002] " + f"令牌已轮换 epoch={cfg.token_epoch}")
    return {"ok": True, "data": _status(), "message": "令牌已更新，旧令牌已立即失效"}


# ============================================================================
# MCP 管理面鉴权（2026-09-17 审查 P1-7 修补）
# ----------------------------------------------------------------------------
# 背景：会员中间件（main.py）只校验「会话是否存在」，**不区分是否管理员**；
# 而 api/mcp.py 全部端点都不再读 request.state.member。于是任意已登录会话都能：
#   ① 把 allow_write_actions 改成 true；
#   ② 用 POST /api/mcp/confirm 给自己签一次性票据（写操作的第二道闸门被自签自销）；
#   ③ 删除 /api/mcp/audit 抹掉审计痕迹。
#
# 修补策略（最小可行、可渐进启用）：
#   - 设 DY_MCP_ADMIN_TOKEN 后，标注为「敏感」的管理端点必须带
#     X-MCP-Admin-Token 头（恒定时间比对）；
#   - 未设置时维持现状（向后兼容），但每次敏感操作都会留 warning 提示。
# 敏感端点：改配置 / 取明文令牌 / 轮换令牌 / 自签票据 / 清审计 / 启停服务。
_SENSITIVE_MCP_PATHS = (
    "/api/mcp/config", "/api/mcp/token/reveal", "/api/mcp/token/rotate",
    "/api/mcp/confirm", "/api/mcp/audit", "/api/mcp/restart",
)


def _require_mcp_admin(request) -> bool:
    """敏感 MCP 管理端点是否需要管理员令牌；返回 True 表示放行。"""
    admin = os.environ.get("DY_MCP_ADMIN_TOKEN", "") or ""
    if not admin:
        logger.warning(
            "MCP-006",
            "[mcp] 未设 DY_MCP_ADMIN_TOKEN：MCP 管理面未启用管理员校验"
            "（任意已登录会话可改配置/取令牌/清审计）")
        return True
    got = request.headers.get("x-mcp-admin-token", "")
    return secrets.compare_digest(got, admin)


@router.post("/token/reveal")
async def reveal_token(request: Request) -> dict[str, Any]:
    """【本机 UI 显式动作】返回全量令牌供复制。

    这是唯一返回明文令牌的端点：由用户在本机界面主动点击触发，
    不经过任何 AI 客户端路径。

    2026-09-17 修补（审查 P1-7）：加入管理员令牌校验（可选启用）。
    拿到令牌即可直连 127.0.0.1 调用全部 READ 工具、绕开会员会话，
    且令牌不过期（只在 rotate 时失效）—— 不应允许任意登录会话取走。
    """
    if not _require_mcp_admin(request):
        return {"ok": False, "message": "需要管理员令牌"}
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
        # 2026-09-17 修补（审查 P2-13）：保留真实 httpd 句柄，供 _stop_runtime
        # 真正 shutdown，避免多次重启遗留多个监听实例。
        _RUNTIME["httpd"] = info.get("httpd")
        logger.info(f"[MCP-003] " + f"本机 HTTP MCP 已启动 :{info['port']}（仅监听本机）")
        return {"ok": True, "data": _status(),
                "message": f"已启动，端口 {info['port']}"}
    except Exception as e:
        logger.warning(f"[MCP-004] " + f"MCP 服务启动失败: {e}")
        return {"ok": False, "message": f"启动失败: {type(e).__name__}"}


def _stop_runtime() -> None:
    """停止 MCP 服务。

    2026-09-17 修补（审查 P2-13）：原实现只把标记置 None，**从不调用
    httpd.shutdown()** —— 每点一次「重启」就遗留一个 ThreadingHTTPServer
    并占住回环端口；多次重启后多实例并存，而 _status() 只显示最后一次的
    bound_port，管理员以为旧实例已停、实际仍在服务。
    """
    httpd = _RUNTIME.get("httpd")
    if httpd is not None and httpd is not True:
        try:
            from mcp.server import shutdown_httpd
            shutdown_httpd(httpd)
        except Exception as e:
            logger.warning("MCP-005", f"[mcp] 关闭旧 MCP 实例失败: "
                                      f"{type(e).__name__}: {e}")
    _RUNTIME["httpd"] = None
    _RUNTIME["port"] = None


@router.get("/tools")
async def list_tools() -> dict[str, Any]:
    """工具清单（含 READ/WRITE 分级，让用户看清哪些是写操作）。"""
    from mcp.tools import register_all
    register_all()
    return {"ok": True, "tools": mcp_registry.describe()}


@router.post("/confirm")
async def issue_confirm(body: dict[str, Any], request: Request) -> dict[str, Any]:
    """为一次写操作签发一次性确认票据（对应蓝本 require_confirmation）。

    2026-09-17 修补（审查 P1-7）：本端点是写操作「双重闸门」的第二道。
    原实现允许任意已登录会话**给自己签票再自己用**，等于闸门自签自销。
    现加入管理员令牌校验（可选启用）。
    """
    if not _require_mcp_admin(request):
        return {"ok": False, "message": "需要管理员令牌"}
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
async def clear_audit(request: Request) -> dict[str, Any]:
    """清空审计日志。

    2026-09-17 修补（审查 P1-7）：清空审计会抹掉操作痕迹，属敏感操作，
    加入管理员令牌校验（可选启用）。
    """
    if not _require_mcp_admin(request):
        return {"ok": False, "message": "需要管理员令牌"}
    mcp_audit.clear()
    return {"ok": True, "message": "审计日志已清空"}
