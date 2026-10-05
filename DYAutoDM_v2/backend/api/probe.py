# -*- coding: utf-8 -*-
"""能力探针 API（M1）—— 「现在这个能力行不行？」

| 端点 | 用途 |
|---|---|
| `GET /api/probe/capabilities` | 列出已注册的能力探针 |
| `GET /api/probe/run` | 跑探针：`?account=`（缺省=全部账号）`&capability=`（缺省=全部能力） |
| `POST /api/probe/run` | 同上（POST 便于前端携带 body） |
| `GET /api/probe/status` | **定时巡检状态**（是否启用/上次结果/下次时间/历史错误） |
| `POST /api/probe/patrol` | 手动触发一轮巡检（等价于定时器那一次） |

## 零风控边界（与 services/probe.py 一致）

探针**只读本地事实**（SQLite 库 + 本项目运行日志），
**不发起任何网络请求、不触碰/启动浏览器（BCC）、不查抖音接口**。
因此本端点可安全地高频调用（如页面轮询、定时巡检）。

真实链路验证不在这里做 —— 见 `POST /api/messages/{account}/refresh`（更新会话，
既有的用户显式动作路径）。
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Query
from loguru import logger

from services import probe as P

router = APIRouter()


@router.get("/capabilities")
async def list_capabilities():
    """列出已注册的能力探针（供前端渲染探针清单）。"""
    return {
        "ok": True,
        "capabilities": P.list_capabilities(),
        "accounts": P.list_accounts_for_probe(),
    }


def _run(account: str | None, capability: str | None) -> dict:
    accounts = [account] if account else None
    caps = [capability] if capability else None
    if capability and capability not in P.REGISTRY:
        return {"ok": False,
                "error": f"未知能力探针 {capability}；可用：{P.list_capabilities()}"}
    res = P.run_probes(accounts=accounts, capabilities=caps)
    logger.info(
        f"[probe] 探针结果：state={res['state']} "
        f"healthy={res['summary']['healthy']} degraded={res['summary']['degraded']} "
        f"failed={res['summary']['failed']} unknown={res['summary']['unknown']}"
        + (f"；需关注 {res['summary']['attention']}" if res["summary"]["attention"] else ""))
    return res


@router.get("/run")
async def run_probe_get(
    account: str | None = Query(default=None, description="账号名；缺省=全部账号"),
    capability: str | None = Query(default=None, description="能力名；缺省=全部能力"),
):
    """跑能力探针（只读本地事实，零网络零浏览器）。"""
    return _run(account, capability)


@router.post("/run")
async def run_probe_post(
    account: str | None = Body(default=None),
    capability: str | None = Body(default=None),
):
    """跑能力探针（POST 版，便于前端传 body）。"""
    return _run(account, capability)


@router.get("/status")
async def probe_status():
    """定时巡检状态：是否启用 / 上次结果 / 下次时间 / 历史错误。

    `last_result` 由 `run_patrol_once()` 持久化在 kv
    （`capability_probe:patrol:state`），进程重启不丢。
    """
    st = P.patrol_state()
    return {"ok": True, "patrol": st}


@router.post("/patrol")
async def probe_patrol_now():
    """手动触发一轮巡检（与定时器那一轮等价；只读本地事实）。"""
    res = P.run_patrol_once()
    return {"ok": True, "result": res, "patrol": P.patrol_state()}
