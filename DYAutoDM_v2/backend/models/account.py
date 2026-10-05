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
    # 2026-09-26（H-20）：/show 是**异步受理** —— ok=True 只代表「已受理」，
    # **不代表**「窗口已就绪」。故用结构化字段如实传递达成态，
    # 前端**不得**凭 ok 宣称「已打开」（H-20 假阳性根治的呈现层闭环）。
    settled: bool | None = None      # True=已达成可见；False=仅受理/切换中；None=不适用
    switching: bool | None = None    # True=正在切换（冷启动约 1~3 分钟）
    # 2026-09-30（honsest-report）：重新捕获/登录这类**异步受理**动作，
    # ok=True 仅表示「已发起」。started 显式区分「发起」与「完成」，
    # 前端**不得**凭 ok 宣称「已更新成功」（需轮询 /recapture-status 查实际结果）。
    started: bool | None = None
