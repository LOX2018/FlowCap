"""账号管理路由

取代原版 WebBridge 的账号相关方法。
关键改进：
- getAccounts 拆为轻量 list（仅端口探活）+ 重量级 verify（按需触发）
- 新增 /scan-status 查询扫码状态（替代 fire-and-forget 的间接推断）
- /check 返回结构化 CheckAccountResponse
"""
from fastapi import APIRouter, Request, HTTPException
from models.account import (
    AccountInfo,
    AddAccountRequest,
    CheckAccountResponse,
    ScanLoginResponse,
    SetRoleRequest,
)
from auto_dm import accounts as acct_core

router = APIRouter()


def _to_raw_account(name: str) -> dict:
    """把后端真实账号状态映射为前端 RawAccount 兼容结构。

    前端 accounts.tsx 的 RawAccount 期望：
    name/uid/level/label/loggedIn/browserDaemonAlive/wpEngine/dmEngine/
    isCurrent/isMonitor/isSender。
    后端 account_status 提供 level/label/alive/uid；守护端口由 core 计算。
    """
    try:
        st = acct_core.account_status(name, force=False, timeout=3)
    except Exception:
        st = {"name": name, "level": "unknown", "label": "状态获取失败",
              "alive": False, "uid": None}
    is_current = (name == acct_core.current_name())
    monitor = (name == acct_core.monitor_name())
    sender = (name == acct_core.sender_name())
    bport = acct_core.browser_daemon_port(name)
    rport = acct_core.recv_daemon_port(name)
    return {
        "name": name,
        "uid": st.get("uid"),
        "level": st.get("level", "unknown"),
        "label": st.get("label", ""),
        "loggedIn": bool(st.get("alive")),
        "browserDaemonPort": bport,
        "recvDaemonPort": rport,
        "browserDaemonAlive": acct_core._port_open(bport, timeout=0.3),
        "recvDaemonAlive": acct_core._port_open(rport, timeout=0.3),
        "wpEngine": {
            "level": "ok" if st.get("has_web_protect") else "warn",
            "label": "凭证守护已捕获" if st.get("has_web_protect")
                     else "wp 凭证未持久化（四件套兼容）",
            "detail": st.get("label", ""),
        },
        "dmEngine": {"level": "unknown", "label": "待校验", "detail": ""},
        "isCurrent": is_current,
        "isMonitor": monitor,
        "isSender": sender,
    }


@router.get("")
async def list_accounts(request: Request):
    """轻量列表（端口探活 + 状态摘要，对齐前端 getAccounts 期望结构）"""
    names = acct_core.list_accounts()
    return {"ok": True, "accounts": [_to_raw_account(n) for n in names]}


@router.post("/{name}/check")
async def check_account(name: str) -> CheckAccountResponse:
    """引擎校验（重量级，按需触发）"""
    # TODO: 迁移 verify_account(dm_loopback=True) 逻辑
    return CheckAccountResponse(name=name)


@router.post("/{name}/scan")
async def scan_login(name: str) -> ScanLoginResponse:
    """扫码登录（启动后台扫码，立即返回）"""
    # TODO: 迁移 scanLogin 逻辑
    return ScanLoginResponse(ok=True, msg="已弹出（待实现）")


@router.get("/{name}/scan-status")
async def scan_status(name: str):
    """扫码状态查询（新增，替代间接推断）"""
    # TODO: 返回扫码进度
    return {"name": name, "done": False, "loggedIn": False}


@router.post("/{name}/role")
async def set_role(name: str, body: SetRoleRequest):
    return {"ok": True, "name": name, "role": body.role.value}


@router.post("")
async def add_account(body: AddAccountRequest):
    return {"ok": True, "name": body.name}


@router.delete("/{name}")
async def remove_account(name: str):
    return {"ok": True, "name": name}
