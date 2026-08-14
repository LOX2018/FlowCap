"""账号管理服务（重构版）

迁移自 DY_Spider_base/auto_dm/accounts.py。
关键重构：
- 端口哈希分配保留（crc32 % range）
- 但守护进程由 Tauri sidecar 管理，不再 subprocess.Popen
- verify_account 逻辑保留，dm_loopback 缓存机制保留
"""
from __future__ import annotations

import zlib
from typing import Optional

from config import settings
from models.enums import AccountRole, AccountStatus
from models.account import AccountInfo, CheckAccountResponse


def account_port(name: str, kind: str) -> int:
    """端口分配（保留原版 crc32 哈希算法）"""
    h = zlib.crc32(name.encode("utf-8"))
    if kind == "browser":
        return settings.browser_port_base + (h % settings.browser_port_range)
    elif kind == "recv":
        return settings.recv_port_base + (h % settings.recv_port_range)
    raise ValueError(f"未知 kind: {kind}")


class AccountService:
    """账号管理（单例）"""

    def __init__(self) -> None:
        self._accounts: dict[str, AccountInfo] = {}
        self._dm_verify_cache: dict[str, tuple[float, bool, Optional[str]]] = {}

    def list(self) -> list[AccountInfo]:
        return list(self._accounts.values())

    def add(self, name: str) -> AccountInfo:
        info = AccountInfo(name=name, role=AccountRole.BOTH, status=AccountStatus.LOGGED_OUT)
        self._accounts[name] = info
        return info

    def remove(self, name: str) -> bool:
        return self._accounts.pop(name, None) is not None

    def set_role(self, name: str, role: AccountRole) -> None:
        if name in self._accounts:
            self._accounts[name].role = role

    async def verify(self, name: str, dm_loopback: bool = False) -> CheckAccountResponse:
        """校验账号

        dm_loopback=True:  对自身 uid 发回环私信（重量级，手动触发）
        dm_loopback=False: 仅查端口/状态（轻量级，轮询用）
        """
        # TODO: 迁移 verify_account 完整逻辑
        return CheckAccountResponse(name=name)
