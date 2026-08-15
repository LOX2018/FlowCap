"""运行日志路由

供前端「运行日志」页签轮询读取后端落盘的日志文件
（logs/run_*.log，loguru 按 rotation 滚动）。

设计：
- 每次后端启动会新建一个 logs/run_{time}.log，即一次「启动会话」。
- GET /sessions 列出全部会话（按修改时间倒序，最新一个即「本次」）。
- GET / 读取日志：未指定 name 时合并全部；指定 name=<file> 时只读该会话文件。
- DELETE /sessions 批量删除历史会话文件（实质删除，用于批量管理）。
- 注意：本模块不提供「清空显示」接口——该能力是前端本地行为，
  只清空前端视图、不触碰磁盘上的实质日志。
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


class SessionDeleteBody(BaseModel):
    files: list[str] = []


def _parse_run_file(fp: Path) -> dict:
    """从 run_YYYYMMDD_HHMMSS.log 文件名解析会话元信息"""
    stem = fp.stem  # run_20260815_123045
    start = stem[len("run_"):] if stem.startswith("run_") else stem
    try:
        size = fp.stat().st_size
    except OSError:
        size = 0
    return {
        "file": fp.name,
        "start": start,  # YYYYMMDD_HHMMSS，便于排序与展示
        "size": size,
        "mtime": int(fp.stat().st_mtime) if size else 0,
    }


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


@router.get("/sessions")
async def list_sessions():
    """列出全部启动会话（每个 run_*.log = 一次启动）

    返回 {ok, current, sessions:[{file, start, size, mtime}]}
    - current: 最新会话的文件名（即「本次」日志）
    - sessions: 按 mtime 倒序，最新在前
    """
    if not LOG_DIR.exists():
        return {"ok": True, "current": None, "sessions": []}
    files = sorted(LOG_DIR.glob("run_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    sessions = [_parse_run_file(fp) for fp in files]
    current = sessions[0]["file"] if sessions else None
    return {"ok": True, "current": current, "sessions": sessions}


@router.get("")
async def get_logs(
    limit: int = Query(500, ge=1, le=5000, description="最多返回行数"),
    name: str | None = Query(None, description="指定日志文件名（不含目录），省略则合并所有日志"),
):
    """读取运行日志，返回 {ok, file, lines:[{ts, level, text}]}

    - 未指定 name 时合并 logs/ 目录下全部 *.log（run_*.log 后端主日志 +
      browser_daemon_*.log / recv_daemon_*.log 守护进程日志），按时间倒序后取尾部，
      这样前端「运行日志」页能一览后端与两个守护的全部活动。
    - 指定 name 时仅读取该文件（用于查看某次历史启动会话）。
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


@router.delete("/sessions")
async def delete_sessions(body: SessionDeleteBody):
    """批量删除历史会话文件（实质删除，用于批量管理）

    不删除「本次」会话（current）。前端批量管理只允许删历史。
    返回 {ok, deleted:[...], skipped:[...]}
    """
    if not LOG_DIR.exists():
        return {"ok": True, "deleted": [], "skipped": []}

    # 当前会话文件名（保护，不允许删）
    cur = None
    run_files = sorted(LOG_DIR.glob("run_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    if run_files:
        cur = run_files[0].name

    deleted: list[str] = []
    skipped: list[str] = []
    for fname in body.files:
        # 只接受 run_*.log 且位于 LOG_DIR 内，防止目录穿越
        if not fname.startswith("run_") or not fname.endswith(".log") or "/" in fname or "\\" in fname:
            skipped.append(fname)
            continue
        if fname == cur:
            skipped.append(fname)
            continue
        target = LOG_DIR / fname
        if target.exists() and target.is_file():
            try:
                target.unlink()
                deleted.append(fname)
            except OSError:
                skipped.append(fname)
        else:
            skipped.append(fname)
    return {"ok": True, "deleted": deleted, "skipped": skipped}


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
