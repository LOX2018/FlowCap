"""运行日志路由

供前端「运行日志」页签轮询读取后端落盘的日志文件
（logs/run_*.log，loguru 按 rotation 滚动）。
也提供写入接口，让前端操作（启停守护、引擎校验等）写入日志。
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, Query
from loguru import logger
from pydantic import BaseModel

router = APIRouter()

# 日志目录：与 main.py 中 loguru 的 logs/run_{time}.log 保持一致
LOG_DIR = Path("logs")


class LogWriteBody(BaseModel):
    level: str = "INFO"
    text: str


@router.post("/write")
async def write_log(body: LogWriteBody):
    """前端操作写入运行日志

    用法： POST /api/logs/write  {"level":"SUCCESS","text":"凭证守护已启动"}
    支持的 level: DEBUG, INFO, SUCCESS, WARNING, ERROR
    """
    level = body.level.upper()
    if level not in ("DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR"):
        level = "INFO"
    log_method = getattr(logger, level.lower(), logger.info)
    log_method(body.text)
    return {"ok": True}


@router.get("")
async def get_logs(
    limit: int = Query(500, ge=1, le=5000, description="最多返回行数"),
    name: str | None = Query(None, description="指定日志文件名（不含目录），省略则合并所有日志"),
):
    """读取运行日志，返回 {ok, file, lines:[{ts, level, text}]}

    - 未指定 name 时合并 logs/ 目录下全部 *.log（run_*.log 后端主日志 +
      browser_daemon_*.log / recv_daemon_*.log 守护进程日志），按时间倒序后取尾部，
      这样前端「运行日志」页能一览后端与两个守护的全部活动。
    - 指定 name 时仅读取该文件。
    """
    if not LOG_DIR.exists():
        return {"ok": True, "file": None, "lines": []}

    if name:
        target = LOG_DIR / name
        if not (target.exists() and target.is_file()):
            return {"ok": True, "file": None, "lines": []}
        return _read_tail(target, limit, target.name)

    # 合并所有 *.log，按行首 HH:mm:ss 时间戳排序后取末尾 limit
    files = sorted(LOG_DIR.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return {"ok": True, "file": None, "lines": []}

    merged: list[tuple[str, dict]] = []
    for fp in files:
        try:
            with fp.open("r", encoding="utf-8", errors="replace") as f:
                for raw in f:
                    line = raw.rstrip("\n")
                    if not line:
                        continue
                    parts = line.split(" | ", 2)
                    ts = parts[0].strip() if len(parts) > 0 else ""
                    level = parts[1].strip() if len(parts) > 1 else ""
                    text = parts[2] if len(parts) > 2 else line
                    merged.append((ts, {"ts": ts, "level": level, "text": text}))
        except Exception:  # noqa: BLE001
            continue

    # 按时间戳字符串排序（同文件内同秒顺序不保证，但整体时间线正确）
    merged.sort(key=lambda x: x[0])
    out = [m[1] for m in merged]
    out = out[-limit:]
    return {"ok": True, "file": "all", "lines": out}


def _read_tail(target: Path, limit: int, fname: str) -> dict:
    out: list[dict] = []
    try:
        with target.open("r", encoding="utf-8", errors="replace") as f:
            for raw in f:
                line = raw.rstrip("\n")
                if not line:
                    continue
                parts = line.split(" | ", 2)
                ts = parts[0].strip() if len(parts) > 0 else ""
                level = parts[1].strip() if len(parts) > 1 else ""
                text = parts[2] if len(parts) > 2 else line
                out.append({"ts": ts, "level": level, "text": text})
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "file": fname, "error": str(e), "lines": []}
    out = out[-limit:]
    return {"ok": True, "file": fname, "lines": out}
