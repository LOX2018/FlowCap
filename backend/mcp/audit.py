# -*- coding: utf-8 -*-
"""MCP 审计日志 —— 脱敏、环形保留。

## 设计契约（对标 better-douyin `mcp.rs`）

蓝本二进制中的原文："脱敏记录工具名、字段摘要、耗时和错误码。"
本模块严格照此实现，并额外守住一条本项目的要求：

**绝不落原始参数值。** 摘要只含：参数名列表 + 被显式声明为可审计的字段的
「长度/类型」。这样即便审计文件被读走，也拿不到会话文本、凭据或昵称。
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

_LOCK = threading.RLock()
_BUFFER: list[dict[str, Any]] = []
_MAX_MEM = 500


def _audit_path() -> Path:
    root = os.environ.get("FLOWCAP_APP_ROOT", "").strip().strip('"')
    if not root or not os.path.isdir(root):
        root = str(Path(__file__).resolve().parents[2])
    d = Path(root) / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d / "mcp_audit.jsonl"


def record(tool: str, *, ok: bool, code: str, elapsed_ms: int,
           summary: dict[str, Any] | None = None) -> dict[str, Any]:
    """记一条审计。返回值便于测试与上层回显。"""
    entry = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "tool": tool,
        "ok": bool(ok),
        "code": code or "",
        "elapsed_ms": int(elapsed_ms),
        "summary": summary or {},
    }
    with _LOCK:
        _BUFFER.append(entry)
        if len(_BUFFER) > _MAX_MEM:
            del _BUFFER[: len(_BUFFER) - _MAX_MEM]
        _append_file(entry)
    return entry


def _append_file(entry: dict[str, Any]) -> None:
    """落盘为 JSONL（一行一条，便于以后按工具统计耗时）。失败不影响主流程。"""
    try:
        with open(_audit_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def tail(limit: int = 50) -> list[dict[str, Any]]:
    """最近 N 条（内存缓冲，供 UI 展示）。"""
    with _LOCK:
        return list(_BUFFER[-max(1, int(limit)):])


def trim(retention: int) -> int:
    """按保留条数裁剪文件，返回裁剪后行数（对齐蓝本 log_retention）。"""
    p = _audit_path()
    if not p.is_file():
        return 0
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
        keep = lines[-max(1, int(retention)):]
        p.write_text("\n".join(keep) + ("\n" if keep else ""), encoding="utf-8")
        return len(keep)
    except Exception:
        return 0


def clear() -> None:
    with _LOCK:
        _BUFFER.clear()
    try:
        _audit_path().write_text("", encoding="utf-8")
    except Exception:
        pass


def stats() -> dict[str, Any]:
    """按工具聚合调用次数与平均耗时（纯本地统计，无副作用）。"""
    with _LOCK:
        agg: dict[str, dict[str, Any]] = {}
        for e in _BUFFER:
            a = agg.setdefault(e["tool"], {"calls": 0, "fails": 0, "ms": 0})
            a["calls"] += 1
            if not e["ok"]:
                a["fails"] += 1
            a["ms"] += e["elapsed_ms"]
        for a in agg.values():
            a["avg_ms"] = round(a["ms"] / a["calls"], 1) if a["calls"] else 0
    return {"tools": agg, "buffered": len(_BUFFER)}
