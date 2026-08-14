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

router = APIRouter()


@router.get("")
async def list_accounts(request: Request) -> list[AccountInfo]:
    """轻量列表（仅端口探活，替代原版 getAccounts 的轮询用）"""
    # TODO: 迁移 accounts.py 逻辑
    return []


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
