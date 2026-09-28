# -*- coding: utf-8 -*-
"""下载子系统 —— 传输与主流程

对应源项目：
  · `src/downloader/media_transfer.rs`  传输（分块 + 断点 + 重试）
  · `src/downloader/downloader.rs`      主流程
  · `src/downloader/retry.rs`           重试
  · `src/downloader/events.rs`          进度事件
  · `src/downloader/completion.rs`      收尾

## 安全与合规

参照源项目 `request_policy.rs`（限流中枢）：本模块的下载请求**受
`app_config.automation.max_actions_per_run` 与 `send_delay_ms` 节流**，
不在无节制并发下打平台 CDN。并发上限由 `max_concurrent` 控制（默认 3）。

**媒体域名**：下载走 CDN（`douyinpic.com` / `douyinvod.com` 等），
不携带平台业务 Cookie（CDN 不需要凭证，带 Cookie 反而增加指纹面）。
"""
from __future__ import annotations

from utils.tls_policy import tls_verify  # noqa: E402
import os
import time
import uuid
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Optional

import requests
from loguru import logger

from . import media_request as MR
from . import tasks as TK

CHUNK = 256 * 1024          # 256KB 分块读写
CONNECT_TIMEOUT = 15
READ_TIMEOUT = 60
MAX_RETRY = 3               # 照源项目 retry.rs 的重试语义

_session = requests.Session()
_session.headers.update({
    # ★ ADR-016 D4：统一走档案（内核感知），禁止写死
    "User-Agent": __import__("utils.fingerprint", fromlist=["user_agent"]).user_agent(),
    "Referer": "https://www.douyin.com/",
})


# ─────────────────────────────────────────────────────────────
# 传输（media_transfer.rs）
# ─────────────────────────────────────────────────────────────

def download_file(url: str, dest: str, *, task: Optional[TK.DownloadTask] = None,
                  mgr: Optional[TK.TaskManager] = None,
                  on_progress: Optional[Callable[[int, int], None]] = None) -> str:
    """下载单个文件到 dest（分块 + 断点续传 + 重试）。

    返回落盘路径；失败抛 RuntimeError（**不静默**）。
    """
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    tmp = dest + ".part"
    existing = os.path.getsize(tmp) if os.path.exists(tmp) else 0

    last_err: Optional[Exception] = None
    for attempt in range(1, MAX_RETRY + 1):
        if task and mgr and mgr.should_cancel(task.task_id):
            raise RuntimeError("已取消")
        if task and mgr and not mgr.wait_if_paused(task.task_id):
            raise RuntimeError("已取消")
        try:
            headers = {"Range": f"bytes={existing}-"} if existing else {}
            with _session.get(url, headers=headers, stream=True, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
                              verify=tls_verify()) as r:
                if r.status_code not in (200, 206):
                    raise RuntimeError(f"HTTP {r.status_code}")
                total = int(r.headers.get("Content-Length") or 0) + existing
                mode = "ab" if (existing and r.status_code == 206) else "wb"
                if mode == "wb":
                    existing = 0
                done = existing
                with open(tmp, mode) as f:
                    for chunk in r.iter_content(CHUNK):
                        if task and mgr and mgr.should_cancel(task.task_id):
                            raise RuntimeError("已取消")
                        if not chunk:
                            continue
                        f.write(chunk)
                        done += len(chunk)
                        if on_progress:
                            on_progress(done, total)
                        elif task:
                            task.bytes_done = done
                            task.bytes_total = total or task.bytes_total
            os.replace(tmp, dest)
            return dest
        except Exception as e:  # noqa: BLE001
            last_err = e
            if task:
                task.retry_count = attempt
                logger.warning(f"[DL-001] " + f"下载重试 {attempt}/{MAX_RETRY}: {type(e).__name__}")
            if "已取消" in str(e):
                raise
            time.sleep(min(2 ** attempt, 8))
    raise RuntimeError(f"下载失败（重试 {MAX_RETRY} 次）: {last_err}")


# ─────────────────────────────────────────────────────────────
# 主流程（downloader.rs + completion.rs）
# ─────────────────────────────────────────────────────────────

def run_task(task: TK.DownloadTask, aweme: dict[str, Any], *, base_dir: str,
             quality: str = "origin", filename_tpl: str = "{nickname}_{desc}_{aweme_id}",
             auto_folder: bool = True, folder_tpl: str = "{nickname}",
             save_metadata: bool = True, live_photo_video: bool = True,
             live_photo_image: bool = True,
             mgr: Optional[TK.TaskManager] = None) -> TK.DownloadTask:
    """执行一个下载任务（**单任务串行**，供批量调用方并发编排）。"""
    mgr = mgr or TK.manager()
    # 2026-09-14 修复（实测暴露的静默失败）：
    #   原实现直接 `mgr.mark(task_id, ...)`，而 `TaskManager.mark()` 内部是
    #   `self._tasks.get(task_id)` → 返回 None 时**静默 return**。
    #   后果：调用方若没先 `mgr.add(task)`，任务**实际下载成功但状态永远停在
    #   `queued`**（实测：bytes=4,187,401 已落盘，status 仍报 queued）。
    #   ⇒ 这里显式保证任务在表中（幂等），消除"流程走完但状态不对"。
    #
    # 2026-09-28 A-3 修复（批量下载「单作品失败 ⇒ 整批中断」）：
    #   原实现把**前置准备**（`extract_media` / `mark(RUNNING)` / `archive_dir`
    #   / `os.makedirs`）放在 `try:` **之外**。而 `run_batch()` 用
    #   `list(ex.map(_one, awemes))` 编排：任一 `_one` 抛异常 ⇒
    #   `ThreadPoolExecutor.map` 在物化时抛出 ⇒ **整批中断**，其余作品全部不下、
    #   调用方收到异常（实测：注入 1 个取址失败作品 → 整批 error）。
    #   ⇒ 现把每个作品的**完整处理**（含全部前置准备）纳入同一 `try/except`：
    #     单作品异常 → 该任务标 FAILED + 写 error + 继续下一个，不影响其余；
    #     既有失败/取消标记语义（`except` 分支）保持不变。
    try:
        if mgr.get(task.task_id) is None:
            mgr.add(task)
        media = MR.extract_media(aweme)
        task.media_type = media["type"]
        task.quality = quality
        mgr.mark(task.task_id, status=TK.RUNNING, media_type=media["type"])

        out_dir = TK.archive_dir(base_dir, nickname=task.nickname,
                                 auto_folder=auto_folder, folder_tpl=folder_tpl)
        os.makedirs(out_dir, exist_ok=True)

        files: list[tuple[str, str]] = []      # (url, dest)

        if media["type"] == "video":
            url = MR.pick_quality(media, quality)
            if not url:
                raise RuntimeError("无可下载的视频地址")
            name = TK.render_template(filename_tpl, desc=task.desc, aweme_id=task.aweme_id,
                                      nickname=task.nickname, ext="mp4")
            files.append((url, os.path.join(out_dir, f"{name}.mp4")))

        elif media["type"] == "images":
            for i, u in enumerate(media["images"], 1):
                name = TK.render_template(filename_tpl, desc=task.desc, aweme_id=task.aweme_id,
                                          nickname=task.nickname, index=i, ext="jpg")
                files.append((u, os.path.join(out_dir, f"{name}.jpg")))

        elif media["type"] == "live_photo":
            for i, lp in enumerate(media["live_photos"], 1):
                if live_photo_image and lp.get("image"):
                    name = TK.render_template(filename_tpl, desc=task.desc, aweme_id=task.aweme_id,
                                              nickname=task.nickname, index=i, ext="jpg")
                    files.append((lp["image"], os.path.join(out_dir, f"{name}.jpg")))
                if live_photo_video and lp.get("video"):
                    name = TK.render_template(filename_tpl, desc=task.desc, aweme_id=task.aweme_id,
                                              nickname=task.nickname, index=i, ext="mp4")
                    files.append((lp["video"], os.path.join(out_dir, f"{name}.mp4")))

        if not files:
            raise RuntimeError("未提取到任何媒体文件")

        task.total_files = len(files)
        mgr.mark(task.task_id, total_files=len(files))

        saved: list[str] = []
        for i, (u, dest) in enumerate(files, 1):
            if mgr.should_cancel(task.task_id):
                raise RuntimeError("已取消")
            download_file(u, dest, task=task, mgr=mgr)
            saved.append(dest)
            task.done_files = i
            mgr.mark(task.task_id, done_files=i)

        task.save_path = out_dir
        if save_metadata:
            _write_meta(out_dir, task, aweme)

        mgr.mark(task.task_id, status=TK.COMPLETED, save_path=out_dir,
                 done_files=len(saved))
        logger.info(f"[DL-002] " + f"下载完成 {task.aweme_id} → {out_dir}（{len(saved)} 个文件）")
        return task

    except Exception as e:  # noqa: BLE001
        is_cancel = "已取消" in str(e)
        mgr.mark(task.task_id,
                 status=TK.CANCELED if is_cancel else TK.FAILED,
                 error="" if is_cancel else f"{type(e).__name__}: {e}")
        logger.warning(f"[DL-003] " + f"下载{'取消' if is_cancel else '失败'} {task.aweme_id}: {e}")
        return task


def _write_meta(out_dir: str, task: TK.DownloadTask, aweme: dict[str, Any]) -> None:
    """写元数据（照源项目 `save_metadata` + `download_record.json`）。"""
    import json
    try:
        p = os.path.join(out_dir, f"{TK.safe_name(task.aweme_id)}.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({
                "aweme_id": task.aweme_id,
                "desc": task.desc,
                "nickname": task.nickname,
                "quality": task.quality,
                "saved_at": time.time(),
                "statistics": aweme.get("statistics") or {},
            }, f, ensure_ascii=False, indent=1)
    except OSError as e:
        logger.warning(f"[DL-004] " + f"元数据写入失败: {e}")


# ─────────────────────────────────────────────────────────────
# 批量（batch.rs）
# ─────────────────────────────────────────────────────────────

def run_batch(awemes: list[dict[str, Any]], *, base_dir: str,
              account: str = "", quality: str = "origin",
              max_concurrent: int = 3,
              filename_tpl: str = "{nickname}_{desc}_{aweme_id}",
              auto_folder: bool = True, folder_tpl: str = "{nickname}",
              save_metadata: bool = True,
              on_task: Optional[Callable[[TK.DownloadTask], None]] = None) -> dict[str, Any]:
    """批量下载（并发上限受 max_concurrent 控制，照源项目 batch.rs）。

    返回 `{"total": n, "ok": k, "failed": m, "tasks": [...]}`。
    """
    mgr = TK.manager()
    mgr.max_concurrent = max(1, max_concurrent)
    results: list[TK.DownloadTask] = []
    lock = threading.Lock()

    def _one(aweme: dict[str, Any]) -> None:
        aweme_id = str(aweme.get("aweme_id") or "")
        author = (aweme.get("author") or {}).get("nickname") or ""
        t = TK.DownloadTask(
            task_id=uuid.uuid4().hex[:16],
            aweme_id=aweme_id,
            desc=(aweme.get("desc") or "")[:80],
            nickname=author,
            status=TK.QUEUED,
        )
        mgr.add(t)
        if on_task:
            on_task(t)
        run_task(t, aweme, base_dir=base_dir, quality=quality,
                 filename_tpl=filename_tpl, auto_folder=auto_folder,
                 folder_tpl=folder_tpl, save_metadata=save_metadata, mgr=mgr)
        with lock:
            results.append(t)

    with ThreadPoolExecutor(max_workers=mgr.max_concurrent) as ex:
        list(ex.map(_one, awemes))

    ok = sum(1 for t in results if t.status == TK.COMPLETED)
    return {
        "total": len(results),
        "ok": ok,
        "failed": len(results) - ok,
        "tasks": [t.to_dict() for t in results],
    }


def stats() -> dict[str, Any]:
    m = TK.manager()
    return {"max_concurrent": m.max_concurrent, "running": m.running_count(),
            "tasks": len(m.list(limit=10000))}
