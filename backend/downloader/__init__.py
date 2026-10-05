# -*- coding: utf-8 -*-
"""下载子系统（后端能力层）

## 设计来源（照源项目 better-douyin）

对标 `src/downloader/`（16 个 `.rs` 模块，方案 B 逆向实测，见
`docs/reverse_interface_spec.md` §四·2）。模块对照：

| 源项目 | 本模块 |
|---|---|
| `media_request.rs` / `quality.rs` / `media_group.rs` | `media_request.py` |
| `media_transfer.rs` / `retry.rs` / `events.rs` | `downloader.py`（传输/重试/进度） |
| `tasks.rs` / `control.rs` | `tasks.py`（状态机/暂停恢复取消） |
| `filename.rs` | `tasks.py` 内（命名模板/归档目录） |
| `downloaded_cache.rs` | `tasks.py` 内（`DownloadedCache`） |
| `batch.rs` | `downloader.run_batch` |
| `completion.rs` | `downloader._write_meta` |
| `request_policy.rs` | 由 `app_config.automation` 节流参数承担 |

## 未覆盖（诚实记录）

源项目的 `streaming.rs`（流式边下边播）与 `image_media.rs`（图片专用通道）
在本实现中并入通用传输路径；`downloaded_cache.json` 的磁盘格式与源项目
不同（本实现为逐行 SHA 键，源项目为 JSON），但语义一致（去重）。
"""
from __future__ import annotations

from . import media_request, tasks
from .downloader import (
    download_file,
    run_batch,
    run_task,
    stats,
)

__all__ = [
    "media_request",
    "tasks",
    "download_file",
    "run_task",
    "run_batch",
    "stats",
]
