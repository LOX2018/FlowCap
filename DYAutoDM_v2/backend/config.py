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

    # 私信词库（[{text, enabled}] 经 tasks/dm-pool 写入）
    dm_pool: list = Field(default_factory=list)
    # 延迟抖动区间（前端 delay "40,65" -> [40, 65]）
    delay_range: list[int] = [40, 65]
    interval: float = 60.0

    # 采集开关（前端 Tasks 页）
    enable_danmaku: bool = True
    enable_console: bool = True
    enable_send: bool = True

    # 监听关键词（来自原 config.KEYWORDS）
    keywords: list[str] = Field(default_factory=list)

    # ===== 路径 =====
    data_dir: Path = Path("data")
    accounts_dir: Path = Path("accounts")

    # ===== 守护进程端口范围 =====
    # 2026-09-06 全局治理（2.2B 扩大 span）：原 500 槽位 20 账号碰撞率
    # ≈32%（生日悖论），扩到 2000 后降到 ≈9.1%。
    # 范围 [10000, 13999]，避开 9911/9912/8765/8877/8080 等常用端口。
    browser_port_base: int = 10000
    browser_port_range: int = 2000
    recv_port_base: int = 12000
    recv_port_range: int = 2000

    # ===== 图片存储策略 =====
    # 2026-08-31 实测（60 张真实私信图片）：
    #   图片平均仅 2.9KB（中位 4KB，最大 4.9KB），60 张合计 121KB
    #   全部内联进 SQLite：+161KB（1.21MB → 1.37MB，仅增 13%）
    #   而图床渲染每张要多花 0.35s（tucdn）~2.5s（imgbb）跨网络下载
    # 结论：为省 3KB 付出百毫秒级网络延迟是本末倒置，故小图默认内联。
    image_inline_max_kb: int = 32  # ≤ 此值内联 base64；超过才上图床；0 = 永远内联

    # 图床（仅大图兜底用）
    # tucdn.wpon.cn：上传 0.83s、下载 0.35s（实测比 imgbb 快 6.4×）
    # imgbb：境外，上传常 SSL 超时、下载 2.1~2.5s，偶发 31s 超时
    # 隐私提示：图床链接公开可访问（imgbb 为 expiration=0 永久）
    image_host_backend: str = "tucdn"  # tucdn / imgbb / custom（自建）
    tucdn_token: str = ""
    imgbb_api_key: str = ""
    imgbb_enabled: bool = True
    imgbb_timeout: int = 20
    # 2026-10-02：自建图床（配置中心 capture.image_host_custom_url/_key 为真源；
    # 此处保留 settings 兜底字段，便于纯 env 部署时也可配）。
    image_host_custom_url: str = ""
    image_host_custom_key: str = ""


settings = Settings()

# 确保数据目录存在
settings.data_dir.mkdir(parents=True, exist_ok=True)
settings.accounts_dir.mkdir(parents=True, exist_ok=True)
