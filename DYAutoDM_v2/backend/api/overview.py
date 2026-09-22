"""总览路由：getOverview / getStats"""
import asyncio

from fastapi import APIRouter, Request
from models.overview import OverviewResponse, StatusResponse
from auto_dm import accounts as acct_core

router = APIRouter()


def _daemon_alive(port: int) -> bool:
    return acct_core._port_open(port, timeout=0.3)


def _any_daemon_alive(port_of) -> bool:
    """聚合判定：**任一已登记账号**的守护在监听即为真。

    设计意图（SoC，2026-09-22 修复）：`/overview` 是**会员级聚合视图**，不应依赖
    「某个默认账号」的语境。本会员的 `accounts_index.current` 可为 None（新版已取消
    「默认账号」概念），旧实现用 `browser_daemon_port()` 的默认参数 →
    `name = name or current_name()` → `hash(None)` → 端口 10288/12258（无监听）
    → 总览页**恒报** `alive:false`，与账号页的 `browserDaemonAlive:true` 自相矛盾。

    判据（可判定）：`/api/overview.browserDaemon.alive == /api/accounts[任一].browserDaemonAlive`
    在同机同刻一致。
    """
    try:
        names = [n for n, _p in acct_core.list_accounts()]
    except Exception:
        names = []
    for n in names:
        try:
            if _daemon_alive(port_of(n)):
                return True
        except Exception:
            continue
    return False


@router.get("/overview")
async def get_overview(request: Request) -> dict:
    """总览（替代原版 WebBridge.getOverview）

    返回字段对齐前端 client.ts 的 Overview 接口：
    running / paused / sent / limit / queue /
    browserDaemon{alive,signReady} / recvDaemon{alive}
    """
    adm = request.app.state.adm
    # 2026-09-22 修复（聚合视图 SoC）：此处**不再**取「默认账号」端口。
    # 旧实现 `browser_daemon_port()` / `recv_daemon_port()` 走默认参数
    # `name = name or current_name()`；本会员 `current` 常为 None（已取消默认账号
    # 概念）→ 端口按 hash(None) 算成 10288/12258（无监听）→ 总览恒报「守护未运行」。
    # 现改为「任一已登记账号的守护在监听」即为真（与 /api/accounts 的口径一致）。
    # 2026-09-17 修补（OCR 审查 HIGH —— 在 async 处理器里做阻塞 IO）：
    # `_port_open` 是同步 `connect_ex`（超时 0.3s）。改到线程里并发探测（asyncio.to_thread）。
    b_alive, r_alive = await asyncio.gather(
        asyncio.to_thread(_any_daemon_alive, acct_core.browser_daemon_port),
        asyncio.to_thread(_any_daemon_alive, acct_core.recv_daemon_port),
    )
    # running 用 adm.is_running（含 starting/stopping）：软停止后存量私信仍在发送，
    # 任务中心必须保留「运行中」行，否则停止存量私信、进入任务的入口就消失了。
    return {
        "running": adm.is_running,
        "paused": adm.state.value == "paused",
        "engineState": adm.state.value,
        "statusMsg": adm.status_msg,
        "sent": adm.sent_count,
        "limit": adm.limit,
        "queue": adm.dispatch.queue_size() if adm.dispatch else 0,
        "browserDaemon": {"alive": b_alive, "signReady": b_alive},
        "recvDaemon": {"alive": r_alive},
        "engine_state": adm.state.value,  # 冗余字段，兼容旧调用
    }


@router.get("/stats")
async def get_stats(request: Request) -> dict:
    """实时统计（前端轮询 / WebSocket 推送）

    返回对齐前端 overview.tsx 的 StatsResp：
    { ok, total, sent, list:[{captureTs,status,nickname,comment,content}] }
    """
    adm = request.app.state.adm
    records = (adm.dispatch.records if adm.dispatch else {}) or {}
    return {
        "ok": True,
        "total": len(records),
        "sent": adm.sent_count,
        "list": [
            {
                "captureTs": r.captured_at,
                "status": r.status.value if hasattr(r.status, "value") else r.status,
                "nickname": r.nickname,
                "comment": r.comment,
                "content": r.content,
            }
            for r in records.values()
        ],
    }
