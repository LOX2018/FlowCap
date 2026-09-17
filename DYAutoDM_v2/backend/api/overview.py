"""总览路由：getOverview / getStats"""
import asyncio

from fastapi import APIRouter, Request
from models.overview import OverviewResponse, StatusResponse
from auto_dm import accounts as acct_core

router = APIRouter()


def _daemon_alive(port: int) -> bool:
    return acct_core._port_open(port, timeout=0.3)


@router.get("/overview")
async def get_overview(request: Request) -> dict:
    """总览（替代原版 WebBridge.getOverview）

    返回字段对齐前端 client.ts 的 Overview 接口：
    running / paused / sent / limit / queue /
    browserDaemon{alive,signReady} / recvDaemon{alive}
    """
    adm = request.app.state.adm
    bport = acct_core.browser_daemon_port()
    rport = acct_core.recv_daemon_port()
    # 2026-09-17 修补（OCR 审查 HIGH —— 在 async 处理器里做阻塞 IO）：
    # `_port_open` 是同步 `connect_ex`（超时 0.3s）。原来在事件循环里**串行**
    # 调两次 → 每次 /overview 轮询最高阻塞约 0.6s，拖住所有其它请求。
    # 改到线程里并发探测（asyncio.to_thread）。
    b_alive, r_alive = await asyncio.gather(
        asyncio.to_thread(_daemon_alive, bport),
        asyncio.to_thread(_daemon_alive, rport),
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
