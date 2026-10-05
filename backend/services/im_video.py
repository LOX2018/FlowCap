# -*- coding: utf-8 -*-
"""IM 视频：下载 → CENC 解密 → 落缓存（2026-09-17 新增）。

对照上游 `extractor/video_downloader.py` + `extractor/cenc.py`。

## 设计意图

抖音私信视频是 **CDN 上的 CENC 加密 MP4**。要变成可播放文件需要三步：
下载密文 → AES-128-CTR 原地解密样本 → 落盘缓存并提供给播放器/下载。

## 与既有图片链路的关系（**继承既定范式，不新引入网络行为**）

图片原图链路（`auto_dm/origin_image_resolver.py`）已确立的做法：
· **不复用账号 cookie**，只用 CDN 直链自带的签名（`lk3s`/`x-expires`）；
· `urllib` GET + referer 模拟 douyin 域 + 5 分钟内存缓存；
· 落盘缓存在 `origin_cache_dir()`，Redis 风格 TTL + 容量淘汰。

本模块**照搬同一范式**（同一 `_http` 语义、同一缓存目录策略、同一 TTL 治理），
因此**没有引入新的对外请求形态**：只是把「图片密文」换成「视频密文」，
解密算法从 AES-256-GCM 换成 CENC 的 AES-128-CTR。

## 契约

· `download_and_decrypt(url, skey, ...)` → `{ok, path, bytes, mime, decrypted, cached}`
· 只接受 `http(s)` URL，拒绝其它 scheme（防 SSRF 到 `file://`）；
· skey 非法/缺失 → 明确报错，**不静默返回密文**（否则前端会拿到放不出来的文件）；
· 明文命中缓存则零网络请求直接返回；
· 全部失败路径返回 `{ok: False, error}`，不抛给调用方（端点层负责映射 HTTP 码）。
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from loguru import logger

from services.cenc_video import decrypt_cenc_mp4, probe_mp4

_HTTP_CACHE: dict[str, tuple[float, bytes]] = {}
_HTTP_TTL = 300          # 5 分钟内存缓存（与图片链路一致）
_MAX_VIDEO_BYTES = 200 * 1024 * 1024    # 200MB 上限（防超大文件撑爆内存）
_ALLOWED_SCHEMES = ("http", "https")


def _cfg(section: str, key: str, default):
    """读 app_config（与图片链路同款容错读取）。"""
    try:
        from services import app_config  # type: ignore
        v = app_config.get(section, key)
        return default if v in (None, "") else v
    except Exception:
        return default


def _cache_dir(app_root: str | None = None) -> Path:
    """视频明文缓存目录（与图片 origin 缓存并列，互不干扰）。"""
    root = app_root or os.environ.get("FLOWCAP_APP_ROOT") or os.getcwd()
    p = Path(root) / "data" / "video_cache"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _ttl_seconds() -> int:
    try:
        return max(60, int(_cfg("media", "video_cache_ttl_sec", 7 * 86400)))
    except Exception:
        return 7 * 86400


def _max_cache_bytes() -> int:
    try:
        return max(64 * 1024 * 1024,
                   int(_cfg("media", "video_cache_max_bytes", 2 * 1024 ** 3)))
    except Exception:
        return 2 * 1024 ** 3


def _http_get(url: str, timeout: int = 60) -> bytes:
    """拉密文（**不带 cookie**，仅靠 CDN 直链自带签名）。

    scheme 白名单：只允许 http/https —— 防 `file://`/`ftp://` 之类被注入读取本地文件。
    """
    scheme = urllib.parse.urlparse(url).scheme.lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise ValueError(f"不支持的 URL scheme: {scheme!r}")
    now = time.time()
    hit = _HTTP_CACHE.get(url)
    if hit and (now - hit[0]) < _HTTP_TTL:
        return hit[1]
    req = urllib.request.Request(url, method="GET", headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Referer": "https://www.douyin.com/",
        "Accept": "*/*",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        cl = r.headers.get("Content-Length")
        if cl and int(cl) > _MAX_VIDEO_BYTES:
            raise RuntimeError(f"文件过大: {cl} 字节")
        data = r.read(_MAX_VIDEO_BYTES + 1)
    if len(data) > _MAX_VIDEO_BYTES:
        raise RuntimeError(f"文件过大（>{_MAX_VIDEO_BYTES} 字节）")
    _HTTP_CACHE[url] = (now, data)
    return data


def cache_key(url: str, skey: str) -> str:
    """缓存键：sha1(skey|url) —— 同一视频换 msg_id 也能命中（与图片链路同思路）。"""
    return hashlib.sha1(f"{skey}|{url}".encode("utf-8")).hexdigest()


def _evict_if_needed(d: Path) -> None:
    """按 mtime 淘汰，直到总容量降到上限的 80%（与图片缓存的容量治理一致）。"""
    try:
        files = []
        total = 0
        for n in os.listdir(d):
            p = d / n
            try:
                st = p.stat()
            except OSError:
                continue
            files.append((st.st_mtime, st.st_size, p))
            total += st.st_size
        cap = _max_cache_bytes()
        if total <= cap:
            return
        files.sort()
        for _mt, sz, p in files:
            if total <= cap * 0.8:
                break
            try:
                p.unlink()
                total -= sz
            except OSError:
                pass
    except OSError:
        pass


def touch_local(filename: str, app_root: str | None = None) -> None:
    """刷新缓存文件 mtime（TTL 以 mtime 判龄 → 「最近看过」的不被回收）。"""
    try:
        p = _cache_dir(app_root) / filename
        if p.exists():
            os.utime(p, None)
    except OSError:
        pass


def sweep(app_root: str | None = None, ttl_sec: int | None = None,
          force: bool = False) -> dict[str, Any]:
    """清理过期视频缓存（与图片链路的 sweep 语义一致）。"""
    d = _cache_dir(app_root)
    ttl = _ttl_seconds() if ttl_sec is None else int(ttl_sec)
    now = time.time()
    removed, kept, freed = 0, 0, 0
    for n in os.listdir(d):
        p = d / n
        try:
            st = p.stat()
        except OSError:
            continue
        if force or (now - st.st_mtime) > ttl:
            try:
                p.unlink()
                removed += 1
                freed += st.st_size
            except OSError:
                pass
        else:
            kept += 1
    return {"ok": True, "removed": removed, "kept": kept, "freed_bytes": freed,
            "dir": str(d)}


def stats(app_root: str | None = None) -> dict[str, Any]:
    """缓存统计（供 /media/stats 汇总）。"""
    d = _cache_dir(app_root)
    files, total = 0, 0
    now = time.time()
    for n in os.listdir(d):
        try:
            st = (d / n).stat()
        except OSError:
            continue
        files += 1
        total += st.st_size
    return {"dir": str(d), "files": files, "bytes": total,
            "ttl_sec": _ttl_seconds(), "max_bytes": _max_cache_bytes(),
            "expired": sum(1 for n in os.listdir(d)
                           if _safe_age(d / n, now) > _ttl_seconds())}


def _safe_age(p: Path, now: float) -> float:
    try:
        return now - p.stat().st_mtime
    except OSError:
        return 0.0


def _faststart_mp4(path: Path) -> None:
    """把 moov 移到文件头（`-movflags +faststart`，**仅重封装不重编码**）。

    为什么必须做：CENC 是**原地解密**，moov 位置不变；若 moov 在文件尾，
    HTML5 `<video>` 必须**读完整文件**才能起播（无法边下边播/Range 拖动）。
    上游 `video_downloader._faststart_mp4()` 同做法。
    ffmpeg 缺失时**静默跳过**（视频仍可播放，只是要缓冲完）。
    """
    import shutil
    import subprocess
    if not shutil.which("ffmpeg"):
        logger.info("[VID-006] ffmpeg 不可用，跳过 faststart（视频仍可播，起播需缓冲）")
        return
    tmp = path.with_suffix(".faststart.mp4")
    try:
        proc = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(path),
             "-c", "copy", "-movflags", "+faststart", "-f", "mp4", str(tmp)],
            capture_output=True, timeout=120)
        if proc.returncode == 0 and tmp.exists() and tmp.stat().st_size > 0:
            os.replace(tmp, path)
            logger.info(f"[VID-007] faststart 重封装完成: {path.name}")
        else:
            tmp.unlink(missing_ok=True)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[VID-008] faststart 失败（保留原文件，不影响播放）: {e}")
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def download_and_decrypt(url: str, skey: str, *, app_root: str | None = None,
                         force: bool = False, timeout: int = 60
                         ) -> dict[str, Any]:
    """下载 → CENC 解密 → 缓存，返回可播放文件路径。

    后置条件（成功时）：`path` 指向**已解密的明文 MP4**，且 `decrypted=True`。
    缓存命中时零网络请求。
    """
    if not url or not isinstance(url, str):
        return {"ok": False, "error": "url 缺失"}
    if not skey or not isinstance(skey, str):
        return {"ok": False, "error": "skey 缺失（无法解密 CENC 视频）"}
    key = cache_key(url, skey)
    d = _cache_dir(app_root)
    ext = "mp4"
    out = d / f"{key}.{ext}"
    if out.exists() and not force:
        try:
            data = out.read_bytes()
            if not force:
                return {"ok": True, "path": str(out), "bytes": len(data),
                        "mime": "video/mp4", "decrypted": True, "cached": True,
                        "probe": probe_mp4(data)}
        except OSError:
            pass

    try:
        cipher = _http_get(url, timeout=timeout)
    except urllib.error.HTTPError as e:
        logger.warning(f"[VID-001] " + f"下载失败 HTTP {e.code}: {url[:80]}")
        return {"ok": False, "error": f"下载失败 HTTP {e.code}"}
    except ValueError as e:
        # URL 校验类错误（scheme 白名单等）——**保留原文**，便于调用方与运维定位
        logger.warning(f"[VID-002] " + f"URL 被拒: {e}")
        return {"ok": False, "error": str(e)}
    except (urllib.error.URLError, OSError, RuntimeError) as e:
        logger.warning(f"[VID-002] " + f"下载失败 {type(e).__name__}: {e}")
        return {"ok": False, "error": f"下载失败: {type(e).__name__}"}

    before = probe_mp4(cipher)
    try:
        plain = decrypt_cenc_mp4(cipher, skey)
    except ValueError as e:
        logger.warning(f"[VID-003] " + f"解密失败: {e}")
        return {"ok": False, "error": f"解密失败: {e}",
                "probe_before": before}
    after = probe_mp4(plain)
    # 一致性校验：解密后仍是合法 MP4，且样本数没变（原地替换，长度必须相等）
    if len(plain) != len(cipher):
        return {"ok": False, "error": "解密后长度发生变化（实现异常）"}
    if not after["is_mp4"]:
        return {"ok": False, "error": "解密结果不是有效 MP4", "probe_after": after}

    try:
        out.write_bytes(plain)
    except OSError as e:
        # 2026-09-18 审查修复（MEDIUM·「假成功」族）：原先返回 `ok=True, path=""`，
        # 调用方只判 `ok` → 响应既无 `url` 也宣称成功，前端拿到空地址无从分辨。
        # 无路径即无可用产物 → 必须显式失败（宁缺勿错，成本未降低）。
        logger.warning(f"[VID-004] " + f"缓存写入失败: {e}")
        return {"ok": False, "error": f"解密成功但缓存写入失败（磁盘/权限）: {e}",
                "bytes": len(plain), "mime": "video/mp4",
                "decrypted": True, "cached": False, "data_written": False,
                "probe_after": after}
    _evict_if_needed(d)
    # moov 移到文件头（否则 HTML5 <video> 需读完整文件才能起播）
    _faststart_mp4(out)
    try:
        final_bytes = out.stat().st_size
    except OSError:
        final_bytes = len(plain)
    logger.info(f"[VID-005] " + f"视频就绪 {out.name} {final_bytes}B "
                f"（密文样本 {before['encrypted_samples']} → 解密后同长）")
    return {"ok": True, "path": str(out), "bytes": final_bytes,
            "mime": "video/mp4", "decrypted": True, "cached": False,
            "probe_before": before, "probe_after": after}


def resolve_by_message(account: str, msg_id: str, *, tkey: str = "",
                       skey: str = "", url: str = "", exec_js=None,
                       app_root: str | None = None, force: bool = False,
                       db=None) -> dict[str, Any]:
    """按**消息**取用视频：缺 URL 时用 `tkey` 经页面上下文换签名地址再下载解密。

    设计意图（2026-09-17）：视频消息**只带 `tkey`+`skey`**，故调用方（前端）
    只需给出 `account`+`msg_id`；本函数负责
    ① 从 DB 的 `extra.video` 取要素（或吃调用方显式传入的值）；
    ② 缺 URL 时经 `exec_js`（BCC 页面上下文）换签名地址；
    ③ 下载密文 → CENC 解密 → faststart → 落缓存。

    参数 `exec_js` 由端点注入（与语音转写同款范式），使本模块**不依赖 BCC**。
    """
    from services.cenc_video import resolve_play_urls

    tk, sk, u = tkey, skey, url
    if (not tk or not sk) and account and msg_id and db is None:
        try:
            from database import get_db
            db = get_db()
        except Exception:
            db = None
    if (not tk or not sk) and db is not None:
        try:
            row = db.execute(
                "SELECT extra FROM dm_messages WHERE account=? AND msg_id=?",
                (account, msg_id)).fetchone()
            if row is not None:
                ex = {}
                try:
                    ex = json.loads(row["extra"] or "{}")
                except Exception:
                    ex = {}
                v = ex.get("video") if isinstance(ex, dict) else None
                if isinstance(v, dict):
                    tk = tk or str(v.get("tkey") or "")
                    sk = sk or str(v.get("skey") or "")
                    u = u or str(v.get("url") or "")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[VID-014] " + f"读消息视频要素失败: {type(e).__name__}")

    if not u:
        if not tk:
            return {"ok": False, "error": "消息内没有视频要素（tkey 缺失）"}
        if not exec_js:
            return {"ok": False, "error": "需要 BCC 页面上下文换取播放地址"}
        got = resolve_play_urls([tk], exec_js)
        u = got.get(tk) or ""
        if not u:
            return {"ok": False, "error": "换取播放地址失败（tkey 无效或登录态失效）"}
    if not sk:
        # 少数分享卡是明文（无 skey）；此时按「不加密」直接下载返回
        logger.info("[VID-015] 无 skey，按明文直链处理（可能为站内分享视频）")
        return download_plain(u, app_root=app_root, force=force)
    return download_and_decrypt(u, sk, app_root=app_root, force=force)


def download_plain(url: str, *, app_root: str | None = None,
                   force: bool = False, timeout: int = 60) -> dict[str, Any]:
    """直接下载**未加密**的视频（站内分享视频可能不带 skey）。

    与 `download_and_decrypt` 同缓存策略；先探测是否真是明文 MP4，
    是加密的（含 senc）则明确报错而不是存下不可播的文件。
    """
    if not url or not isinstance(url, str):
        return {"ok": False, "error": "url 缺失"}
    key = cache_key(url, "plain")
    d = _cache_dir(app_root)
    out = d / f"{key}.mp4"
    if out.exists() and not force:
        try:
            data = out.read_bytes()
            return {"ok": True, "path": str(out), "bytes": len(data),
                    "mime": "video/mp4", "decrypted": False, "cached": True,
                    "probe": probe_mp4(data)}
        except OSError:
            pass
    try:
        data = _http_get(url, timeout=timeout)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"下载失败: {type(e).__name__}"}
    pr = probe_mp4(data)
    if not pr["is_mp4"]:
        return {"ok": False, "error": "下载内容不是 MP4", "probe": pr}
    if pr.get("encrypted_samples"):
        return {"ok": False, "error": "该视频是 CENC 加密的，缺少 skey 无法解密",
                "probe": pr}
    try:
        out.write_bytes(data)
    except OSError as e:
        return {"ok": False, "error": f"缓存写入失败: {type(e).__name__}"}
    _faststart_mp4(out)
    _evict_if_needed(d)
    return {"ok": True, "path": str(out), "bytes": out.stat().st_size,
            "mime": "video/mp4", "decrypted": False, "cached": False,
            "probe": pr}
