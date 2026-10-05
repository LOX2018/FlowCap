"""总览相关协议"""
from pydantic import BaseModel
from .enums import AccountRole, AccountStatus, DaemonKind


class DaemonStatus(BaseModel):
    kind: DaemonKind
    alive: bool
    account: str | None = None


class AccountBrief(BaseModel):
    name: str
    role: AccountRole
    status: AccountStatus
    uid: str | None = None


class OverviewResponse(BaseModel):
    engine_state: str
    sent: int
    limit: int
    captured: int
    accounts: list[AccountBrief]
    daemons: list[DaemonStatus]


class StatusResponse(BaseModel):
    ok: bool = True
    running: bool = False
