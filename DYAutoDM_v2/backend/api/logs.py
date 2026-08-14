"""运行日志路由

供前端「运行日志」页签轮询读取后端落盘的日志文件
（logs/run_*.log，loguru 按 rotation 滚动）。
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, Query

router = APIRouter()

# 日志目录：与 main.py 中 loguru 的 logs/run_{time}.log 保持一致
LOG_DIR = Path("logs")


@router.get("")
async def get_logs(
    limit: int = Query(500, ge=1, le=5000, description="最多返回行数"),
    name: str | None = Query(None, description="指定日志文件名（不含目录），省略则取最新"),
):
    """读取运行日志，返回 {ok, file, lines:[{ts, level, text}]}

    - 未指定 name 时自动选最新的 run_*.log
    - 返回末尾 limit 行（倒序后的尾部），前端按原顺序展示
    """
    if not LOG_DIR.exists():
        return {"ok": True, "file": None, "lines": []}

    logs = sorted(LOG_DIR.glob("run_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not logs:
        return {"ok": True, "file": None, "lines": []}

    target = None
    if name:
        cand = LOG_DIR / name
        if cand.exists() and cand.is_file():
            target = cand
    if target is None:
        target = logs[0]

    out: list[dict] = []
    try:
        with target.open("r", encoding="utf-8", errors="replace") as f:
            for raw in f:
                line = raw.rstrip("\n")
                if not line:
                    continue
                # 格式：HH:mm:ss | LEVEL    | message
                parts = line.split(" | ", 2)
                ts = parts[0].strip() if len(parts) > 0 else ""
                level = parts[1].strip() if len(parts) > 1 else ""
                text = parts[2] if len(parts) > 2 else line
                out.append({"ts": ts, "level": level, "text": text})
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "file": target.name, "error": str(e), "lines": []}

    # 取末尾 limit 行，保持原始时间顺序
    out = out[-limit:]
    return {"ok": True, "file": target.name, "lines": out}
