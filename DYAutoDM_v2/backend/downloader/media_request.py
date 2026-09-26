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

本模块做**媒体地址解析与质量选择**，多数函数为纯计算。
唯一例外是 `resolve_playable()`：抖音的无签名入口是 **302 跳转入口**，
必须发一次请求跟随它才能拿到真实地址 —— 该次请求用
`Range: bytes=0-0` 只取 1 字节，**不下载视频本体**（见其文档字符串）。

实际传输在 `media_transfer.py`，任务状态在 `tasks.py`。
"""
from __future__ import annotations

import re
from typing import Any, Optional

from loguru import logger

# 质量档位（照源项目 `quality.rs` 的语义；数值越高越好）
# 降级链：由高到低（2026-09-15 修正 —— 原为 lowbr 在首位，会永远选最低画质）。
# 语义：hd/origin 优先，其次 h265/h264，再次 lowbr，最后 download 兜底。
# 注：download_addr 可能是音频（图集作品），必须排在真正的视频档之后。
QUALITY_ORDER = ("hd", "origin", "h265", "h264", "lowbr", "download")

# ─────────────────────────────────────────────────────────────────────────
# 会话签名直链识别（2026-09-21 根因修复，详见下方 resolve_playable()）
# ─────────────────────────────────────────────────────────────────────────
# 抖音 `play_addr.url_list` 同时给出两类链接：
#   · **会话签名直链**：host 为 `*-web*.douyinvod.com`，带 tk=/signature=/policy=，
#     签名绑定取址时的浏览器会话 —— 脱离该会话（播放器 <video> / 后端 requests）
#     请求，CDN 一律 **403 Forbidden**（openresty）。实测与 UA / Referer /
#     cookie / IPv4-IPv6 / policy 取值 / URL 编码 **全部无关**。
#   · **无签名入口**：`https://www.douyin.com/aweme/v1/play/?video_id=...`
#     跟随 302 才拿到真实流（实测 200 / video/mp4 / 49,821,029 字节 / ftyp isom）。
# 上游佐证（GitHub，同根因）：
#   · Evil0ctal/Douyin_TikTok_Download_API issues #500 / #583 / #720
#   · Jane-xiaoer/xiaoer-videolab commit b455eda「优先选无签名链接，根除 403」
_SIGNED_HOST_RE = re.compile(r"-web[a-z]*\.douyinvod\.com", re.I)
_SIGNED_PARAM_RE = re.compile(r"[?&](?:tk|signature|policy)=", re.I)


def is_session_signed(url: str) -> bool:
    """是否「会话签名直链」（脱离取址会话请求必 403）。

    判据（二者具一即判定）：host 命中 `*-web*.douyinvod.com`；或
    query 含 `tk=` / `signature=` / `policy=`。
    """
    if not isinstance(url, str) or not url:
        return False
    return bool(_SIGNED_HOST_RE.search(url) or _SIGNED_PARAM_RE.search(url))


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

    ## 入参兼容（2026-09-15 补）
    既接受**完整作品对象**（`video`/`images`/`music` 在顶层），也接受前端
    从 `/feed` 等接口拿到的**裁剪对象**（媒体在 `media` 子树下，见
    `api/platform.py:_pick_aweme`）——后者若不下钻会得到空媒体，
    表现为「取址失败」。

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
    # 下钻：若本层无 media 字段，但存在 media 子树，则用子树（保留外层 desc/aweme_id）
    if not aweme.get("video") and not aweme.get("images"):
        inner = aweme.get("media")
        if isinstance(inner, dict) and (inner.get("video") or inner.get("images")):
            merged = dict(inner)
            for k in ("aweme_id", "desc", "create_time"):
                if not merged.get(k) and aweme.get(k) is not None:
                    merged[k] = aweme[k]
            aweme = merged
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
    # 2026-09-15 修复：原把 `download_addr` 也标为 "origin"，导致
    #   ① 图集作品的 download_addr 是**音频(.mp3)** 时会被当作视频返回；
    #   ② pick_quality 首选 origin → 直接给出该 mp3，播放器必然失败。
    # 现改为：download_addr 归入 "download" 档（降级链末位），仅作最后兜底。
    for key, tag in (("play_addr", "h264"), ("play_addr_h264", "h264"),
                     ("play_addr_lowbr", "lowbr"), ("download_addr", "download")):
        us = _urls_of(video.get(key))
        if us:
            out["videos"].setdefault(tag, [])
            for u in us:
                if u not in out["videos"][tag]:
                    out["videos"][tag].append(u)

    # bit_rate_list（更精细的档位，含 gear_name / 码率）
    # 2026-09-15：原实现把所有档都归并成 h264/h265，丢失了「高清/标清」层次，
    # 导致 hd 档永远选不到。现按 gear_name 里的画质标识细分（normal/hd/super/lossless）。
    brl = video.get("bit_rate") or video.get("bit_rate_list") or []
    if isinstance(brl, list):
        for br in brl:
            if not isinstance(br, dict):
                continue
            gear = str(br.get("gear_name") or br.get("quality_type") or "").lower()
            us = _urls_of(br.get("play_addr"))
            if not us:
                continue
            if "h265" in gear or "hevc" in gear:
                tag = "h265"
            elif "lossless" in gear:
                tag = "hd"
            elif "super" in gear or "1080" in gear:
                tag = "hd"
            elif "normal" in gear or "720" in gear:
                tag = "h264"
            else:
                tag = "h264"
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

    ## 2026-09-21 修正：同档位内**优先无签名链接**

    原实现取 `us[0]` —— 而抖音 `url_list` 的 **前两位就是会话签名直链**
    （播放器 `<video>` 裸请求必 403，见 `is_session_signed()`）。
    现改为：同一 quality 档位内，先把**无签名**的候选排在前面；
    全部有签名时才回退首条（交由 `resolve_playable()` 跟随 302 兜底）。
    """
    videos: dict[str, list[str]] = media.get("videos") or {}
    if not videos:
        return None
    # 2026-09-21 修正：`download` 档**不参与**视频降级链 —— 图集作品的
    # download_addr 是**音频(.mp3)**（见 2026-09-15 记录），降级到它会给出
    # 一个「能拉到 200 但播不了」的地址。仅当**没有任何视频档位**时才用它兜底。
    real = [q for q in (QUALITY_ORDER + (preferred,)) if q != "download"]
    chain = [preferred] + [q for q in real if q != preferred]
    for q in chain:
        us = videos.get(q)
        if us:
            return _prefer_unsigned(us)
    # 兜底：任意第一档（仍优先无签名）
    for q, us in videos.items():
        if q != "download" and us:
            return _prefer_unsigned(us)
    # 最后才用 download 档（可能是音频，但总比没有强）
    dl = videos.get("download")
    return _prefer_unsigned(dl) if dl else None


def _prefer_unsigned(urls: list[str]) -> str:
    """候选列表内优先返回无签名链接（保持相对顺序）。"""
    if not urls:
        return ""
    for u in urls:
        if not is_session_signed(u):
            return u
    return urls[0]


def resolve_playable(url: str, *, timeout: float = 10.0,
                     max_redirects: int = 5) -> tuple[str, str]:
    """把候选地址解析成**可直接播放的真实地址**。

    ## 为什么需要它（2026-09-21 根因）

    `play_addr.url_list` 里的无签名入口
    `https://www.douyin.com/aweme/v1/play/?video_id=...`
    本身 **不是**视频数据，而是一个 **302 跳转入口**；`<video>` 与大部分
    HTTP 客户端会跟随重定向没问题，但：

      · 若配到的是**会话签名直链**（CDN 403），跟随也没用 —— 必须避开；
      · 部分环境下需要**显式**解析出终极地址（下载器要 Content-Length、
        要鉴 Host），故在此统一解析。

    ## 行为契约

    · 只用 **HEAD**，不下载视频本体（省 ~50MB 流量）；
    · 服务端不支持 HEAD 时自动退回 **GET + Range: bytes=0-0**；
    · 失败/超时**不抛异常**，返回原 URL（调用方照常使用，由播放器自行重试）。

    Returns:
        `(真实地址, 解析方式)` —— 方式 ∈ {`direct`, `head`, `get-range`, `passthrough`}
    """
    if not isinstance(url, str) or not url:
        return ("", "empty")
    try:
        import requests
        from utils.tls_policy import tls_verify

        headers = {
            # ★ ADR-016 D4：统一走档案（内核感知），禁止写死版本
            "User-Agent": __import__("utils.fingerprint", fromlist=["user_agent"]).user_agent(),
            "Accept": "*/*",
            # 🔴 审计 idx5 回归修复（2026-09-26）：抖音 CDN **按 Referer 白名单放行**，
            # 缺该头必 403（同日案例 v0.45.9 §四 实测：仅 UA → 403；UA + Referer → 200/206）。
            # 此头在 ADR-016「统一 UA」提交 f54f554 中被顺带删除 ⇒ 属功能回归，
            # 与 resolve_playable/流代理的一致性契约（本仓其它 CDN 请求均带 Referer）一并恢复。
            "Referer": "https://www.douyin.com/",
        }
        # ★ 探测方法必须是 **GET + Range**，不能用 HEAD（2026-09-21 实测修正）：
        # 抖音 CDN 对 HEAD 与 GET 的签名校验**不一致** —— HEAD 返回 200 而
        # GET 同一 URL 返回 403。用 HEAD 探测会把「必然 403」谎报成「已解析可播」
        # （实测 6 个作品里 3 个因此误判）。故改为与播放器同方法的 GET 探测，
        # 并用 Range: bytes=0-0 只取 1 字节，不下载视频本体（省 ~50MB）。
        try:
            h2 = dict(headers, **{"Range": "bytes=0-0"})
            r = requests.get(url, headers=h2, timeout=timeout, stream=True,
                             allow_redirects=True, verify=tls_verify())
            status = r.status_code
            final = r.url
            had_redirect = bool(r.history)
            r.close()
            if 200 <= status < 300:
                return (final, "get-range" if had_redirect else "direct")
            # 明确失败：记下来，让调用方能区分「解析失败」与「地址本身被拒」
            logger.debug(f"[media_request] 候选地址 GET 返回 {status}: {url[:80]}")
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[media_request] 候选地址探测异常 {type(e).__name__}: {url[:80]}")
    except Exception:  # noqa: BLE001 —— requests / tls_policy 导入失败：不阻断
        pass
    # 解析不了就用原地址（播放器自己会再试；不假装成功）
    return (url, "passthrough")


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
