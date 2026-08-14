"""全局配置

迁移自 DY_Spider_base/auto_dm/config.py，改用 pydantic-settings：
- 环境变量 / .env 文件 / 运行时覆盖 三方合并
- 取消"写回 .py 源码"的反模式
- 配置持久化用 JSON 文件（backend/data/config.json）
"""
from __future__ import annotations

from pathlib import Path
from typing import Tuple

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="allow",
    )

    # ===== 服务 =====
    backend_port: int = 8000
    log_level: str = "INFO"

    # ===== 业务默认值（迁移自 auto_dm/config.py）=====
    live_url: str = ""
    max_target: int = 3
    send_delay_sec: Tuple[int, int] = (40, 65)
    send_interval: float = 60.0

    # 监听关键词（来自原 config.KEYWORDS）
    keywords: list[str] = Field(default_factory=list)

    # ===== 路径 =====
    data_dir: Path = Path("data")
    accounts_dir: Path = Path("accounts")

    # ===== 守护进程端口范围 =====
    browser_port_base: int = 10000
    browser_port_range: int = 500
    recv_port_base: int = 10500
    recv_port_range: int = 500


settings = Settings()

# 确保数据目录存在
settings.data_dir.mkdir(parents=True, exist_ok=True)
settings.accounts_dir.mkdir(parents=True, exist_ok=True)
