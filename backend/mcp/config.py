# -*- coding: utf-8 -*-
"""MCP 配置与令牌——唯一真源。

对标 better-douyin `mcp.rs` 的配置面（二进制实测）：
    preferred_port / allow_write_actions / require_confirmation /
    log_retention / token（+ regenerate 后旧票立即失效）

**令牌世代号设计（`token_epoch`）**：蓝本只说"轮换后旧令牌立即失效"。
本项目用**世代号**表达该不变式 —— 校验时比对 epoch，世代号一变，
所有旧 token 自动失效，无需维护黑名单，也无需等 TTL。
"""
from __future__ import annotations

import json
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any

from loguru import logger

_LOCK = threading.RLock()

DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "preferred_port": 39144,      # 沿用蓝本首选端口
    "allow_write_actions": False,  # 默认只读（蓝本默认值）
    "require_confirmation": True,  # 写操作需二次确认
    "log_retention": 200,          # 审计环形保留条数
}

TOKEN_BYTES = 32
KV_KEY = "mcp_config"


def _kv_path() -> Path:
    """配置落盘位置：随应用根，与既有 kv 存储同级。"""
    root = os.environ.get("FLOWCAP_APP_ROOT", "").strip().strip('"')
    if not root or not os.path.isdir(root):
        root = str(Path(__file__).resolve().parents[2])
    d = Path(root) / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d / "mcp_config.json"


def _mask(token: str) -> str:
    """脱敏展示：只留前 4 后 4（审计与 UI 用，绝不落全量）。"""
    if not token:
        return ""
    if len(token) <= 12:
        return "*" * len(token)
    return f"{token[:4]}{'*' * 8}{token[-4:]}"


def _new_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


class McpConfig:
    """MCP 配置容器。全程加锁，进程内唯一。"""

    def __init__(self) -> None:
        self._p = _kv_path()
        self._data: dict[str, Any] = dict(DEFAULTS)
        self._token: str = ""
        self._token_epoch: int = 0
        self._created_at: float = 0.0
        self._load()

    # ---------- 持久化 ----------

    def _load(self) -> None:
        try:
            if self._p.is_file():
                raw = json.loads(self._p.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    for k in DEFAULTS:
                        if k in raw:
                            self._data[k] = raw[k]
                    self._token = str(raw.get("token") or "")
                    self._token_epoch = int(raw.get("token_epoch") or 0)
                    self._created_at = float(raw.get("created_at") or 0.0)
        except Exception:
            # 配置损坏不应阻断启动：回落默认值（enabled=False 是最安全的默认）
            pass

    def _save(self) -> None:
        try:
            payload = dict(self._data)
            payload["token"] = self._token
            payload["token_epoch"] = self._token_epoch
            payload["created_at"] = self._created_at
            tmp = self._p.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            os.replace(tmp, self._p)
        except Exception as e:
            # 2026-09-17 修补（审查 P2-14）：原为 `except Exception: pass`。
            # ensure_token(rotate=True) 声称「旧令牌立即失效」，若落盘失败则
            # 内存失效而磁盘仍是旧令牌 → 重启后旧令牌复活，违反核心不变式。
            # 落盘失败必须留下 error 级日志，不再静默吞掉。
            logger.error(f"[mcp] 配置落盘失败，磁盘仍为旧值: "
                         f"{type(e).__name__}: {e}")

    # ---------- 公开接口 ----------

    def get(self, key: str, default: Any = None) -> Any:
        with _LOCK:
            return self._data.get(key, DEFAULTS.get(key, default))

    def all_public(self) -> dict[str, Any]:
        """对外视图：**绝不返回全量 token**，只给脱敏串与是否已设置。"""
        with _LOCK:
            out = dict(self._data)
            out["token_set"] = bool(self._token)
            out["token_masked"] = _mask(self._token)
            out["token_epoch"] = self._token_epoch
            return out

    def update(self, **kw: Any) -> dict[str, Any]:
        with _LOCK:
            for k, v in kw.items():
                if k in DEFAULTS and v is not None:
                    self._data[k] = v
            self._save()
            return self.all_public()

    # ---------- 令牌 ----------

    def ensure_token(self, rotate: bool = False) -> str:
        """确保存在令牌；`rotate=True` 强制轮换（世代号 +1，旧票立即失效）。"""
        with _LOCK:
            if rotate or not self._token:
                self._token = _new_token()
                self._token_epoch += 1
                self._created_at = time.time()
                self._save()
                return self._token
            return self._token

    def reveal_token(self) -> str:
        """仅本机 UI 显式要求时返回全量（每次调用都重新生成，降低驻留）。"""
        with _LOCK:
            if not self._token:
                self._token = _new_token()
                self._token_epoch += 1
                self._save()
            return self._token

    def check_token(self, candidate: str) -> bool:
        """恒定时间比对；空 token 或未启用一律拒绝。"""
        with _LOCK:
            # 2026-09-17 修补（OCR 审查 MEDIUM —— 未按 docstring 判定 enabled）：
            # 原实现只比对 token，**从不检查 `enabled`** → 即便用户在设置里
            # 关闭了 MCP（默认就是 False），只要请求带上 token 就仍能调用工具，
            # 与 docstring/设置项语义（"关闭即不提供 MCP 能力"）相矛盾。
            if not self._data.get("enabled"):
                return False
            if not self._token or not candidate:
                return False
            return secrets.compare_digest(self._token, candidate)

    @property
    def enabled(self) -> bool:
        with _LOCK:
            return bool(self._data.get("enabled"))

    @property
    def allow_write(self) -> bool:
        with _LOCK:
            return bool(self._data.get("allow_write_actions"))

    @property
    def require_confirmation(self) -> bool:
        with _LOCK:
            return bool(self._data.get("require_confirmation"))

    @property
    def token_epoch(self) -> int:
        with _LOCK:
            return self._token_epoch


_INSTANCE: McpConfig | None = None
_INST_LOCK = threading.Lock()


def instance() -> McpConfig:
    """进程内单例（惰性）。"""
    global _INSTANCE
    if _INSTANCE is None:
        with _INST_LOCK:
            if _INSTANCE is None:
                _INSTANCE = McpConfig()
    return _INSTANCE
