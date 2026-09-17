# -*- coding: utf-8 -*-
"""下载子系统 —— 传输与状态机

## 设计来源（照源项目 better-douyin）

对标三组模块（方案 B 逆向情报）：
  · `src/downloader/media_transfer.rs`  传输（分块 / 断点 / 重试）
  · `src/downloader/tasks.rs`          任务状态机
  · `src/downloader/filename.rs`       命名与归档目录
  · `src/downloader/downloaded_cache.rs` 去重缓存
  · `src/downloader/retry.rs`          重试
  · `src/downloader/completion.rs`     收尾（写记录）

源项目实测状态字段（`all_strings`）：queued / running / downloading / paused /
canceling / canceled / completed / failed / retrying / pending。

## 与源项目的差异（诚实记录）

源项目是 Rust 实现、含 S3 风格签名（`x-amz-content-sha256` / `AWS4-HMAC-SHA256`
/ `cn-north-1/vod/aws4_request`）用于 VOD 直传。本模块**只做客户端下载**，
不实现上传侧签名（那是"发布/投稿"，非源项目公开能力）。
"""
from __future__ import annotations

import os
import re
import time
import threading
from dataclasses import dataclass, field, asdict
from typing import Any, Optional

from loguru import logger

# ─────────────────────────────────────────────────────────────
# 状态机（照源项目 tasks.rs 的字段）
# ─────────────────────────────────────────────────────────────

QUEUED = "queued"
RUNNING = "running"
PAUSED = "paused"
CANCELING = "canceling"
CANCELED = "canceled"
COMPLETED = "completed"
FAILED = "failed"
RETRYING = "retrying"

TERMINAL = (CANCELED, COMPLETED, FAILED)


@dataclass
class DownloadTask:
    """单个下载任务（对齐源项目 struct 字段）。"""
    task_id: str
    aweme_id: str
    desc: str = ""
    nickname: str = ""
    status: str = QUEUED
    media_type: str = "video"          # video / images / live_photo
    quality: str = "origin"
    save_path: str = ""
    total_files: int = 0
    done_files: int = 0
    bytes_total: int = 0
    bytes_done: int = 0
    error: str = ""
    retry_count: int = 0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    @property
    def progress(self) -> float:
        if self.bytes_total > 0:
            return round(min(1.0, self.bytes_done / self.bytes_total), 4)
        if self.total_files > 0:
            return round(min(1.0, self.done_files / self.total_files), 4)
        return 0.0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["progress"] = self.progress
        return d


# ─────────────────────────────────────────────────────────────
# 命名与归档（照源项目 filename.rs）
# ─────────────────────────────────────────────────────────────

_BAD = re.compile(r'[\\/:*?"<>|\r\n\t]')


def safe_name(s: str, fallback: str = "untitled", max_len: int = 80) -> str:
    """文件名安全化（跨平台）。"""
    s = _BAD.sub("_", (s or "").strip())
    s = re.sub(r"\s+", " ", s).strip(" .")
    if not s:
        s = fallback
    return s[:max_len]


def render_template(tpl: str, *, desc: str = "", aweme_id: str = "",
                    nickname: str = "", index: int = 0, ext: str = "mp4",
                    date: Optional[str] = None) -> str:
    """渲染命名模板（照源项目 `filename_template`）。

    占位符：{desc} {aweme_id} {nickname} {index} {date} {ext}
    """
    d = date or time.strftime("%Y%m%d")
    s = (tpl or "{nickname}_{desc}_{aweme_id}")
    for k, v in (("{desc}", desc), ("{aweme_id}", aweme_id),
                 ("{nickname}", nickname), ("{index}", str(index)),
                 ("{date}", d), ("{ext}", ext)):
        s = s.replace(k, safe_name(str(v)) if k != "{ext}" else str(v))
    return safe_name(s, fallback=aweme_id or "untitled")


def archive_dir(base: str, *, nickname: str = "", auto_folder: bool = True,
                folder_tpl: str = "{nickname}") -> str:
    """计算归档目录（照源项目 `auto_create_folder` + `folder_name_template`）。"""
    root = os.path.abspath(base)
    if not auto_folder or not nickname:
        return root
    sub = render_template(folder_tpl, nickname=nickname, desc="", aweme_id="")
    return os.path.join(root, safe_name(sub, fallback="unknown"))


# ─────────────────────────────────────────────────────────────
# 去重缓存（照源项目 downloaded_cache.rs）
# ─────────────────────────────────────────────────────────────

class DownloadedCache:
    """已下载记录（aweme_id / 文件路径 去重）。"""

    def __init__(self, path: str = "") -> None:
        self._path = path
        self._ids: set[str] = set()
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        if not self._path or not os.path.exists(self._path):
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                for ln in f:
                    ln = ln.strip()
                    if ln:
                        self._ids.add(ln)
        except OSError:
            pass

    def has(self, key: str) -> bool:
        with self._lock:
            return key in self._ids

    def add(self, key: str) -> None:
        with self._lock:
            if key in self._ids:
                return
            self._ids.add(key)
        if self._path:
            try:
                os.makedirs(os.path.dirname(self._path), exist_ok=True)
                with open(self._path, "a", encoding="utf-8") as f:
                    f.write(key + "\n")
            except OSError:
                pass

    def count(self) -> int:
        with self._lock:
            return len(self._ids)


# ─────────────────────────────────────────────────────────────
# 任务管理器（照源项目 tasks.rs + control.rs）
# ─────────────────────────────────────────────────────────────

class TaskManager:
    """内存任务表 + 控制（暂停/恢复/取消）。单进程内线程安全。"""

    def __init__(self, max_concurrent: int = 3) -> None:
        self._tasks: dict[str, DownloadTask] = {}
        self._order: list[str] = []
        self._lock = threading.RLock()
        self._cancel_flags: set[str] = set()
        self._pause_flags: set[str] = set()
        self.max_concurrent = max_concurrent

    # ---- CRUD ----
    def add(self, task: DownloadTask) -> DownloadTask:
        with self._lock:
            self._tasks[task.task_id] = task
            self._order.append(task.task_id)
        return task

    def get(self, task_id: str) -> Optional[DownloadTask]:
        with self._lock:
            return self._tasks.get(task_id)

    def list(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            out = [self._tasks[t].to_dict() for t in self._order if t in self._tasks]
        return out[-limit:]

    # ---- 控制 ----
    def pause(self, task_id: str) -> bool:
        with self._lock:
            t = self._tasks.get(task_id)
            if not t or t.status in TERMINAL:
                return False
            self._pause_flags.add(task_id)
            t.status = PAUSED
            t.updated_at = time.time()
            return True

    def resume(self, task_id: str) -> bool:
        with self._lock:
            t = self._tasks.get(task_id)
            if not t or t.status != PAUSED:
                return False
            self._pause_flags.discard(task_id)
            t.status = RUNNING
            t.updated_at = time.time()
            return True

    def cancel(self, task_id: str) -> bool:
        with self._lock:
            t = self._tasks.get(task_id)
            if not t or t.status in TERMINAL:
                return False
            self._cancel_flags.add(task_id)
            t.status = CANCELING
            t.updated_at = time.time()
            return True

    # ---- 供传输循环查询 ----
    def should_cancel(self, task_id: str) -> bool:
        with self._lock:
            return task_id in self._cancel_flags

    def wait_if_paused(self, task_id: str, poll: float = 0.2) -> bool:
        """暂停时阻塞等待；返回 False 表示已被取消。

        2026-09-17（OCR 审查 HIGH 核实后：**行为正确，仅文档不精确**）：
        判定顺序是 **先取消、后暂停** —— 若任务同时处于「已取消 + 已暂停」，
        本函数返回 False（取消优先）。这是**有意且必要**的设计：否则一个
        暂停中的任务将永远无法被取消（会一直卡在暂停分支里 sleep）。
        原 docstring 未写明该优先级，易被误读为"暂停应优先"，故补充说明。
        """
        while True:
            if self.should_cancel(task_id):
                return False
            with self._lock:
                paused = task_id in self._pause_flags
            if not paused:
                return True
            time.sleep(poll)

    def running_count(self) -> int:
        with self._lock:
            return sum(1 for t in self._tasks.values() if t.status == RUNNING)

    def mark(self, task_id: str, **fields: Any) -> None:
        with self._lock:
            t = self._tasks.get(task_id)
            if not t:
                # 2026-09-14：原为静默 return —— 实测导致"下载成功但状态停在 queued"
                # 这类**静默失败**（调用方忘了 add）。改为显式告警，便于定位。
                logger.warning(f"[DL-005] " + f"mark 目标不存在: {task_id}（调用方是否漏了 mgr.add?）")
                return
            for k, v in fields.items():
                if hasattr(t, k):
                    setattr(t, k, v)
            t.updated_at = time.time()

    def cleanup_finished(self, keep: int = 200) -> int:
        """保留最近 N 条已完成任务，返回清理数。"""
        with self._lock:
            done = [tid for tid in self._order
                    if self._tasks.get(tid) and self._tasks[tid].status in TERMINAL]
            drop = done[:-keep] if len(done) > keep else []
            for tid in drop:
                self._tasks.pop(tid, None)
                self._cancel_flags.discard(tid)
                self._pause_flags.discard(tid)
            if drop:
                self._order = [t for t in self._order if t not in set(drop)]
        return len(drop)


#: 进程级单例（照源项目 downloader 的单实例语义）
_manager = TaskManager()


def manager() -> TaskManager:
    return _manager
