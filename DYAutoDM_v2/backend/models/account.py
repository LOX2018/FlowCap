"""账号相关协议"""
from pydantic import BaseModel
from .enums import AccountRole, AccountStatus


class AccountInfo(BaseModel):
    name: str
    role: AccountRole
    status: AccountStatus
    uid: str | None = None
    sec_uid: str | None = None
    cookie_valid: bool = False
    sign_ready: bool = False
    last_refresh: float | None = None


class AddAccountRequest(BaseModel):
    name: str


class SetRoleRequest(BaseModel):
    role: AccountRole


class CheckAccountResponse(BaseModel):
    name: str
    wp_ok: bool = False  # 浏览器引擎校验
    dm_ok: bool = False  # 私信引擎校验
    reason: str | None = None


class ScanLoginResponse(BaseModel):
    ok: bool
    msg: str
