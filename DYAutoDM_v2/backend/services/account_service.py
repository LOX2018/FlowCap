"""账号管理服务（重构版）

⚠️ 2026-09-06 全局治理：**本模块为孤儿代码，未启用**。

- 迁移自 DY_Spider_base/auto_dm/accounts.py
- 但全项目实际使用的是 `auto_dm/accounts.py` 的
  `browser_daemon_port()` / `recv_daemon_port()`（已加 salt 防碰撞）
- 本文件的 `account_port()` 是**第二套端口实现**，与前者重复且
  **无 salt**（存在 browser/recv 同步撞车缺陷，已在 09 台账 2.2 修复版本中解决）
- 实测：除 `core/auto_dm.py:684` 一句 TODO 注释外，无任何调用方

**保留原因**：暂作迁移参考。请勿新增调用；若启用必须先对齐
`auto_dm/accounts.py` 的 salt 算法，否则端口会与真实守护错位。

正确的端口分配入口：`from auto_dm import accounts; accounts.browser_daemon_port(name)`
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
