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
import re
import time
from pathlib import Path

from fastapi import APIRouter, Query
from loguru import logger
from pydantic import BaseModel, Field

router = APIRouter()

# 日志目录：与 main.py 中 loguru 的 logs/run_{time}.log 保持一致
LOG_DIR = Path("logs")


class LogWriteBody(BaseModel):
    level: str = "INFO"
    text: str


class SessionDeleteBody(BaseModel):
    # 2026-09-17 修补（OCR 审查 HIGH —— 可变默认参数）：
    # 原为 `files: list[str] = []`。虽然 pydantic 会在校验时复制默认值，
    # 但模块级共享的可变对象仍是隐患（且不同 pydantic 大版本语义不同）。
    # 改用 `Field(default_factory=list)` 显式每次新建。
    files: list[str] = Field(default_factory=list)


# 2026-08-31 修复：守护进程日志用完整日期格式
#   2026-08-31 00:04:55.682 | INFO     | mod:fn:123 - 文本
# 后端主日志用短格式
#   00:04:55 | INFO     | 文本
# 原解析只认短格式，导致守护日志的 ts 被解析成整行前缀，
# 再按字符串排序时这些行挤占尾部，把真正的日志挤出可视区。
_TS_FULL = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})[ T](?P<time>\d{2}:\d{2}:\d{2})(?P<ms>\.\d+)?"
    r"\s*\|\s*(?P<level>[A-Z]+)\s*\|(?P<rest>.*)$"
)
_TS_SHORT = re.compile(
    r"^(?P<time>\d{2}:\d{2}:\d{2})\s*\|\s*(?P<level>[A-Z]+)\s*\|(?P<rest>.*)$"
)


# 2026-08-31：高频噪音日志（会淹没用户真正关心的操作记录）。
# 实测：私信详情页每 20s 轮询一次会话详情，BCC keepalive 每 5 分钟一次，
# 这些日志在 500 行窗口内会把「更新会话完成」等结果挤出可视区。
_NOISE_PAT = re.compile(
    r"\[私信拉取\]|"          # 详情页轮询会话详情
    r"\[bcc\] 登录态正常|"     # BCC keepalive
    r"json 控制帧|"           # WS 心跳
    r"_keepalive|"
    r"会话详情 conv_id="
)


def _is_noise(text: str) -> bool:
    return bool(_NOISE_PAT.search(text))


def _parse_line(line: str) -> tuple[str, dict] | None:
    """解析一行日志，返回 (sort_key, {ts, level, text})；无法识别则返回 None。

    sort_key 保证：可识别时间戳的行按 (日期, 时间) 排序，
    守护日志与后端日志能正确交织在同一条时间线上。
    """
    m = _TS_FULL.match(line)
    if m:
        ts = m.group("time")
        # 排序键：日期 + 时间，保证跨文件时间线正确
        key = m.group("date") + " " + ts
        rest = m.group("rest").strip()
        # 去掉 loguru 的 "module:func:line - " 前缀，只留正文
        rest = re.sub(r"^[\w\.]+:\d+\s*-\s*", "", rest)
        return key, {"ts": ts, "level": m.group("level"), "text": rest or line}

    m = _TS_SHORT.match(line)
    if m:
        ts = m.group("time")
        # 无日期，用当天日期补齐，确保与带日期的行可比
        today = time.strftime("%Y-%m-%d")
        return (
            f"{today} {ts}",
            {"ts": ts, "level": m.group("level"),
             "text": m.group("rest").strip() or line},
        )

    return None


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
    with_noise: bool = Query(False, description="是否包含高频噪音日志（私信轮询/keepalive），默认过滤"),
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
        # 2026-09-17 修补（OCR 审查 HIGH —— 路径穿越）：
        # 原实现 `target = LOG_DIR / name` 直接拼接**来自查询串的名字**。
        # pathlib 语义下 `Path("logs") / "../../secret.txt"` 会逃逸到上层目录，
        # `Path("logs") / "/etc/passwd"` 更会**整体替换**为绝对路径（实测：
        # log_dir/"C:\Windows\win.ini" → C:\Windows\win.ini），
        # 于是 `GET /api/logs?name=../../secret.txt` 可读任意文件。
        # 现要求：① 只接受纯文件名（无目录分隔符/无上级引用）；
        #          ② 解析后必须仍在 LOG_DIR 之内（双重保险，防符号链接/UNC）。
        safe_name = os.path.basename(name.replace("\\", "/").strip())
        if (not safe_name or safe_name != name.replace("\\", "/").strip()
                or safe_name in (".", "..")):
            logger.warning(f"[logs] 拒绝非法日志文件名: {name!r}")
            return {"ok": False, "file": None, "error": "非法文件名", "lines": []}
        target = LOG_DIR / safe_name
        try:
            root = LOG_DIR.resolve()
            if target.resolve().parent != root:
                logger.warning(f"[logs] 拒绝越界路径: {name!r}")
                return {"ok": False, "file": None, "error": "非法文件名", "lines": []}
        except Exception:  # noqa: BLE001
            return {"ok": False, "file": None, "error": "非法文件名", "lines": []}
        if not (target.exists() and target.is_file()):
            return {"ok": True, "file": None, "lines": []}
        return _read_tail(target, limit, target.name)

    # 合并所有 *.log，按行首 HH:mm:ss 时间戳排序后取末尾 limit
    files = sorted(LOG_DIR.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return {"ok": True, "file": None, "lines": []}

    merged: list[tuple[str, dict]] = []
    skipped = 0
    noises = 0
    for fp in files:
        try:
            with fp.open("r", encoding="utf-8", errors="replace") as f:
                for raw in f:
                    line = raw.rstrip("\n")
                    if not line:
                        continue
                    parsed = _parse_line(line)
                    if parsed is None:
                        # 无法识别的行（堆栈续行等）不参与排序，
                        # 否则会挤占尾部把正常日志挤出可视区。
                        skipped += 1
                        continue
                    if not with_noise and _is_noise(parsed[1].get("text", "")):
                        noises += 1
                        continue
                    merged.append(parsed)
        except Exception:  # noqa: BLE001
            continue

    # 按 (日期 时间) 排序，跨文件（后端 + 两个守护）时间线正确
    merged.sort(key=lambda x: x[0])
    out = [m[1] for m in merged]
    out = out[-limit:]
    if skipped:
        logger.debug(f"[logs] 跳过 {skipped} 行无法解析的日志（非标准格式）")
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
    """读日志尾部 limit 条。

    2026-09-17 修补（OCR 审查 HIGH —— 全量读入内存）：
    原实现 `for raw in f: ... out.append(...)` 把**整个文件**解析成对象列表
    再 `out[-limit:]`；而 `run_*.log` 按 `rotation="20 MB"` 轮转，单个文件可达
    20MB —— 一次 `?name=<file>` 请求就要解析数万行并占用同等内存。
    现只保留最后 `limit*5` 行（留冗余以抵消无法解析的堆栈续行），
    内存与解析量都与 `limit` 成正比。
    """
    from collections import deque

    out: list[dict] = []
    tail: deque = deque(maxlen=max(limit * 5, 200))
    try:
        with target.open("r", encoding="utf-8", errors="replace") as f:
            for raw in f:
                tail.append(raw)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "file": fname, "error": str(e), "lines": []}
    for raw in tail:
        line = raw.rstrip("\n")
        if not line:
            continue
        parsed = _parse_line(line)
        if parsed is not None:
            out.append(parsed[1])
    return {"ok": True, "file": fname, "lines": out[-limit:]}
