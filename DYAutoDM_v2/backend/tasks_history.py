# -*- coding: utf-8 -*-
"""历史任务记录（任务中心「历史任务」数据源）

每次引擎启动记录一条历史任务，停止/结束时更新状态与结果条数。
持久化到 data/task_history.json，重启后仍保留，供任务中心展示与跳转查阅。
"""
import json
import os
import time
import threading
from pathlib import Path

_lock = threading.Lock()
_MAX = 200  # 最多保留 200 条，超出丢弃最旧


def _history_path():
    try:
        from config import settings
        p = settings.data_dir / "task_history.json"
    except Exception:
        p = Path("data") / "task_history.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _load() -> list[dict]:
    p = _history_path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save(items: list[dict]) -> None:
    p = _history_path()
    try:
        p.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def start_task(acct: str, live_id: str, config: dict | None = None, records: list | None = None) -> int:
    """记录一条新历史任务（状态=运行中），返回任务 id。

    config: 启动时的配置快照（供任务中心「进入/复用」回读，如 {live_url,max_target,...}）。
    """
    with _lock:
        items = _load()
        tid = int(time.time() * 1000)  # 毫秒时间戳作 id
        items.append({
            "id": tid,
            "acct": acct or "",
            "live_id": live_id or "",
            "start_ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "end_ts": "",
            "status": "running",      # running / finished / stopped
            "result_count": len(records or []),
            "config": config or {},   # 配置快照（任务中心「复用」数据源）
            "records": records or [], # 结果快照（查阅模式数据源）
        })
        if len(items) > _MAX:
            items = items[-_MAX:]
        _save(items)
        return tid


def finish_task(tid: int, status: str = "finished", result_count: int = 0, records: list | None = None) -> None:
    """结束历史任务：更新状态、结果条数、记录快照。"""
    with _lock:
        items = _load()
        for it in items:
            if it.get("id") == tid:
                it["status"] = status
                it["end_ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
                it["result_count"] = result_count or 0
                if records is not None:
                    it["records"] = records
                break
        _save(items)


def list_history() -> list[dict]:
    """返回历史任务列表（新的在前）。"""
    with _lock:
        items = _load()
    return list(reversed(items))


def clear_history() -> None:
    """清空历史任务。"""
    with _lock:
        _save([])
