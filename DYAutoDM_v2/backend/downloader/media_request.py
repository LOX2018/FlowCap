# -*- coding: utf-8 -*-
"""下载子系统 —— 媒体取址与选质

## 设计来源（照源项目 better-douyin）

对标 `src/downloader/media_request.rs` + `src/downloader/quality.rs` + `media_group.rs`
（方案 B 逆向情报，见 `docs/reverse_interface_spec.md` §四·2）。
源项目实测提取的媒体字段（`all_strings`）::

    play_addr / play_addr_h264 / play_addr_lowbr / download_addr
    bit_rate / bit_rate_list / bitrates / quality_type / gear_name
    is_h265 / support_h265 / support_dash / h264 / h265
    url_list / fallback_url / backup_url / src_url
    media_urls / live_photos / live_photo_urls / image_urls
    preview_addr / dash_addr / audio_addr / music / music_file
    cover / dynamic_cover / origin_cover / ratio / duration

## 职责边界

本模块**只做媒体地址解析与质量选择**（纯计算，不发请求）。
实际传输在 `media_transfer.py`，任务状态在 `tasks.py`。
"""
from __future__ import annotations

from typing import Any, Optional

# 质量档位（照源项目 `quality.rs` 的语义；数值越高越好）
QUALITY_ORDER = ("lowbr", "h264", "h265", "dash", "origin")


def _urls_of(addr: Any) -> list[str]:
    """从各种形态的 addr 结构里取 URL 列表。

    抖音返回三种形态：
      · {"url_list": [...]}
      · {"url_list": [...], "uri": "..."}
      · {"urlList": [...]}（部分接口驼峰）
    """
    if not isinstance(addr, dict):
        return []
    for k in ("url_list", "urlList", "urls"):
        v = addr.get(k)
        if isinstance(v, list):
            return [u for u in v if isinstance(u, str) and u]
    return []


def extract_media(aweme: dict[str, Any]) -> dict[str, Any]:
    """从一个 aweme 对象提取**全部可用媒体地址**（照源项目 media_group）。

    返回::

        {
          "aweme_id": str,
          "type": "video" | "images" | "live_photo",
          "videos": {quality: [url, ...]},   # 按档位归组
          "images": [url, ...],
          "live_photos": [{"image": url, "video": url}, ...],
          "audio": [url, ...],
          "cover": str,
          "duration": int,
          "desc": str,
        }
    """
    out: dict[str, Any] = {
        "aweme_id": str(aweme.get("aweme_id") or ""),
        "type": "video",
        "videos": {},
        "images": [],
        "live_photos": [],
        "audio": [],
        "cover": "",
        "duration": aweme.get("duration") or (aweme.get("video") or {}).get("duration") or 0,
        "desc": aweme.get("desc") or "",
    }
    video = aweme.get("video") or {}

    # ---- 视频：多档位 ----
    for key, tag in (("play_addr", "h264"), ("play_addr_h264", "h264"),
                     ("play_addr_lowbr", "lowbr"), ("download_addr", "origin")):
        us = _urls_of(video.get(key))
        if us:
            out["videos"].setdefault(tag, [])
            for u in us:
                if u not in out["videos"][tag]:
                    out["videos"][tag].append(u)

    # bit_rate_list（更精细的档位，含 gear_name / 码率）
    brl = video.get("bit_rate") or video.get("bit_rate_list") or []
    if isinstance(brl, list):
        for br in brl:
            if not isinstance(br, dict):
                continue
            gear = str(br.get("gear_name") or br.get("quality_type") or "").lower()
            us = _urls_of(br.get("play_addr"))
            if not us:
                continue
            tag = "h265" if ("h265" in gear or "hevc" in gear) else "h264"
            out["videos"].setdefault(tag, [])
            for u in us:
                if u not in out["videos"][tag]:
                    out["videos"][tag].append(u)

    # ---- 图集 / Live Photo ----
    imgs = aweme.get("images")
    if isinstance(imgs, list) and imgs:
        out["type"] = "images"
        for im in imgs:
            if not isinstance(im, dict):
                continue
            us = _urls_of(im)
            if us:
                out["images"].append(us[0])
            # Live Photo：每个图可带 video
            lv = _urls_of(im.get("video"))
            if lv:
                out["live_photos"].append({
                    "image": us[0] if us else "",
                    "video": lv[0],
                })
        if out["live_photos"]:
            out["type"] = "live_photo"

    # ---- 音频（原声 / BGM）----
    music = aweme.get("music") or {}
    for k in ("play_url", "play_url_h264"):
        us = _urls_of(music.get(k))
        if us:
            out["audio"].append(us[0])
            break
    mu = music.get("music_file")
    if isinstance(mu, dict):
        out["audio"].extend(_urls_of(mu))

    # ---- 封面 ----
    for k in ("origin_cover", "cover", "dynamic_cover"):
        us = _urls_of(video.get(k))
        if us:
            out["cover"] = us[0]
            break
    if not out["cover"]:
        us = _urls_of(aweme.get("cover") or {})
        if us:
            out["cover"] = us[0]

    return out


def pick_quality(media: dict[str, Any], preferred: str = "origin") -> Optional[str]:
    """按偏好选一个视频 URL（照源项目 `quality.rs` 的降级链）。

    降级顺序：preferred → origin → dash → h265 → h264 → lowbr。
    找不到返回 None。
    """
    videos: dict[str, list[str]] = media.get("videos") or {}
    if not videos:
        return None
    chain = [preferred] + [q for q in QUALITY_ORDER if q != preferred]
    for q in chain:
        us = videos.get(q)
        if us:
            return us[0]
    # 兜底：任意第一档
    for us in videos.values():
        if us:
            return us[0]
    return None


def summarize(media: dict[str, Any]) -> dict[str, Any]:
    """媒体摘要（供 UI/审计展示，不含长 URL）。"""
    videos = media.get("videos") or {}
    return {
        "aweme_id": media.get("aweme_id"),
        "type": media.get("type"),
        "video_qualities": sorted(videos.keys()),
        "video_url_count": sum(len(v) for v in videos.values()),
        "image_count": len(media.get("images") or []),
        "live_photo_count": len(media.get("live_photos") or []),
        "audio_count": len(media.get("audio") or []),
        "has_cover": bool(media.get("cover")),
        "duration": media.get("duration"),
        "desc": (media.get("desc") or "")[:60],
    }
