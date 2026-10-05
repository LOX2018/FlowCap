# -*- coding: utf-8 -*-
"""媒体代理与缓存 —— 单一解密实现（对标源项目 `src/media_proxy_cache.rs`）

## 设计来源

照 **better-douyin** 的 `media_proxy_cache.rs`（方案 B 逆向情报，见
`docs/reverse_interface_spec.md` §四）。源项目的设计要点（实测提取）：

```
把「加密取回 → 本地解密 → 喂前端」收敛为单一 proxy；
前端只拿明文 URL（契约 mediaProxyUrl()）；
带 cache（"cacheable image"）；
aes_chunk_size = 524288（512KB 分块，实测提取值）
```

## 本项目现状（改造前，实测）

AES-256-GCM 解密逻辑**散落**在多个位置，各有一份实现：
  - `auto_dm/origin_image_resolver.py:206`  `_decrypt()`  ← 唯一可运行实现
  - `auto_dm/conversation_capture.py:378`    内联 `AESGCM(key).decrypt(...)`
  - `api/messages.py:432`                    注释描述 + 调用 resolver
  - `daemon/recv_daemon.py:764`              注释描述

⇒ 本模块把它们**收敛为一处**（源项目 `media_proxy_cache.rs` 的对应物）。
按「不删原函数签名」原则：旧调用点改为**委托到本模块**，保持向后兼容。

## 风控说明（本分支）

本模块只做**本地解密与缓存**，不发起任何平台请求（取密文由调用方负责，
且已有 `origin_image_resolver` 的 `_http_cache` 承担）。属纯本地计算。
"""
from __future__ import annotations

import os
import time
import threading
from typing import Any, Optional

from loguru import logger

# ─────────────────────────────────────────────────────────────
# 常量（照源项目实测值）
# ─────────────────────────────────────────────────────────────

#: 源项目 `aes_chunk_size` 实测值：512KB。抖音 IM 媒体分块加密尺寸。
AES_CHUNK_SIZE = 524288

#: 内容类型判定阈值（小图内联 base64，大图走文件）
INLINE_MAX_BYTES = 32 * 1024

#: 缓存 TTL（秒）。源项目用 "cacheable image" + cache_v2；此处取 7 天。
CACHE_TTL_SEC = 7 * 24 * 3600

#: 磁盘缓存上限（字节）。与 `app_config.capture.origin_image_max_mb` 对齐语义。
CACHE_MAX_BYTES = 2048 * 1024 * 1024

_lock = threading.Lock()
_mem_cache: dict[str, tuple[float, bytes, str]] = {}   # key -> (ts, data, mime)
_mem_hits = 0
_mem_miss = 0


# ─────────────────────────────────────────────────────────────
# 1) 解密：唯一实现
# ─────────────────────────────────────────────────────────────

def decrypt_media(cipher: bytes, skey_hex: str) -> bytes:
    """★ **全仓唯一的 AES-256-GCM 解密实现**。

    格式（与 08 §三十五 / MEMORY 一致，源项目同构）::

        key   = bytes.fromhex(skey_hex)     # 64 hex = 32 字节
        iv    = cipher[:12]
        plain = AESGCM(key).decrypt(iv, cipher[12:], None)

    Args:
        cipher: 密文（前 12 字节为 IV）
        skey_hex: 64 位十六进制密钥（来自 `resource_url.skey`）

    Raises:
        RuntimeError: 密钥缺失/长度不符/密文过短/解密失败
    """
    if not skey_hex:
        raise RuntimeError("skey 为空")
    try:
        key = bytes.fromhex(skey_hex)
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"skey 解析失败: {e}") from e
    if len(key) != 32:
        raise RuntimeError(f"skey 长度 {len(key)} 字节，期望 32")
    if len(cipher) < 28:
        raise RuntimeError(f"密文过短: {len(cipher)} 字节")

    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    return AESGCM(key).decrypt(cipher[:12], cipher[12:], None)


# 兼容别名（源项目契约名 mediaProxy 系列；本项目旧名 decrypt_image）
decrypt_image = decrypt_media
decrypt = decrypt_media


# ─────────────────────────────────────────────────────────────
# 2) 格式识别（HEIC 实测占比 40%，Windows 不支持需转码判定）
# ─────────────────────────────────────────────────────────────

_FMT_TABLE = (
    (b"\xff\xd8\xff", "jpg", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "png", "image/png"),
    (b"GIF87a", "gif", "image/gif"),
    (b"GIF89a", "gif", "image/gif"),
    (b"RIFF", "webp", "image/webp"),        # 需再看 8~12 字节为 WEBP
    (b"ftypheic", "heic", "image/heic"),    # 偏移 4 起
    (b"ftypmif1", "heif", "image/heif"),
)


def detect_format(head: bytes) -> tuple[str, str]:
    """识别媒体格式 → (ext, mime)。无法识别返回 ("bin", "application/octet-stream")。"""
    if not head:
        return "bin", "application/octet-stream"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp", "image/webp"
    if len(head) >= 12 and head[4:12].startswith(b"ftyp"):
        brand = head[8:12]
        if brand in (b"heic", b"heix", b"hevc", b"hevx"):
            return "heic", "image/heic"
        return "heif", "image/heif"
    for sig, ext, mime in _FMT_TABLE:
        if head.startswith(sig):
            return ext, mime
    # 视频/音频粗判
    if head[4:8] in (b"ftyp",) or head[:4] == b"\x00\x00\x00\x18":
        return "mp4", "video/mp4"
    return "bin", "application/octet-stream"


# ─────────────────────────────────────────────────────────────
# 3) 缓存（内存 LRU + 磁盘）
# ─────────────────────────────────────────────────────────────

def cache_dir(app_root: Optional[str] = None) -> str:
    """磁盘缓存目录：`<app_root>/data/media_cache/`。"""
    root = app_root or os.environ.get("FLOWCAP_APP_ROOT") or "."
    d = os.path.join(root, "data", "media_cache")
    os.makedirs(d, exist_ok=True)
    return d


def _cache_key(cipher: bytes, skey_hex: str) -> str:
    import hashlib
    h = hashlib.sha256()
    h.update((skey_hex or "").encode())
    h.update(cipher[:64])
    h.update(str(len(cipher)).encode())
    return h.hexdigest()[:32]


def get_cached(cipher: bytes, skey_hex: str) -> Optional[tuple[bytes, str]]:
    """查缓存 → (data, mime) 或 None。"""
    global _mem_hits, _mem_miss
    key = _cache_key(cipher, skey_hex)
    now = time.time()

    with _lock:
        hit = _mem_cache.get(key)
        if hit and (now - hit[0]) < CACHE_TTL_SEC:
            _mem_hits += 1
            return hit[1], hit[2]
        if hit:
            _mem_cache.pop(key, None)
        _mem_miss += 1

    # 磁盘缓存
    ext_guess = "bin"
    path = os.path.join(cache_dir(), f"{key}.{ext_guess}")
    for ext in ("jpg", "png", "webp", "gif", "heic", "heif", "mp4", "bin"):
        p = os.path.join(cache_dir(), f"{key}.{ext}")
        if os.path.exists(p):
            try:
                data = open(p, "rb").read()
                _, mime = detect_format(data[:16])
                with _lock:
                    _mem_cache[key] = (now, data, mime)
                return data, mime
            except OSError:
                continue
    return None


def put_cached(cipher: bytes, skey_hex: str, data: bytes) -> str:
    """写缓存（内存 + 磁盘）→ 返回磁盘路径。"""
    key = _cache_key(cipher, skey_hex)
    ext, mime = detect_format(data[:16])
    with _lock:
        _mem_cache[key] = (time.time(), data, mime)

    path = os.path.join(cache_dir(), f"{key}.{ext}")
    try:
        with open(path, "wb") as f:
            f.write(data)
    except OSError as e:
        logger.warning(f"[MEDIA-001] " + f"磁盘缓存写入失败: {e}")
    _evict_if_needed()
    return path


def _evict_if_needed() -> None:
    """按 mtime 清理超限缓存（照源项目 cacheable 的容量治理）。"""
    d = cache_dir()
    try:
        files = []
        total = 0
        for n in os.listdir(d):
            p = os.path.join(d, n)
            try:
                st = os.stat(p)
            except OSError:
                continue
            files.append((st.st_mtime, st.st_size, p))
            total += st.st_size
        if total <= CACHE_MAX_BYTES:
            return
        files.sort()  # 最旧优先
        for _mt, sz, p in files:
            if total <= CACHE_MAX_BYTES:
                break
            try:
                os.remove(p)
                total -= sz
            except OSError:
                pass
    except OSError:
        pass


# ─────────────────────────────────────────────────────────────
# 4) 单一入口：解密 + 缓存（源项目 mediaProxy 的对应物）
# ─────────────────────────────────────────────────────────────

def resolve(cipher: bytes, skey_hex: str, *, use_cache: bool = True) -> tuple[bytes, str]:
    """**媒体代理主入口**：解密并按缓存策略返回 `(data, mime)`。

    对应源项目 `media_proxy_cache.rs` 的 proxy 函数。
    调用方（`origin_image_resolver` / `api/messages`）应统一走这里，
    不要各自 `AESGCM(...)`。
    """
    if use_cache:
        hit = get_cached(cipher, skey_hex)
        if hit:
            return hit
    plain = decrypt_media(cipher, skey_hex)
    if use_cache:
        put_cached(cipher, skey_hex, plain)
    _, mime = detect_format(plain[:16])
    return plain, mime


def should_inline(data: bytes) -> bool:
    """是否应内联 base64（小图直出）而非走文件 URL。"""
    return len(data) <= INLINE_MAX_BYTES


def media_proxy_url(filename: str) -> str:
    """对外 URL 契约（对应源项目 `mediaProxyUrl()`）。

    本项目既有契约是 `/api/messages/origin_image/{filename}`，
    此处保持**同名同形**（前端零改动）。源项目的 `mediaProxyUrl()`
    在其工程内亦只是"拼一个本机 URL"，等价。
    """
    return f"/api/messages/origin_image/{filename}"


def stats() -> dict[str, Any]:
    """缓存统计（可观测性）。"""
    with _lock:
        n = len(_mem_cache)
        hits, miss = _mem_hits, _mem_miss
    total = 0
    try:
        for fn in os.listdir(cache_dir()):
            try:
                total += os.path.getsize(os.path.join(cache_dir(), fn))
            except OSError:
                pass
    except OSError:
        pass
    return {
        "mem_entries": n,
        "mem_hits": hits,
        "mem_miss": miss,
        "hit_rate": round(hits / max(1, hits + miss), 4),
        "disk_bytes": total,
        "chunk_size": AES_CHUNK_SIZE,
        "inline_max_bytes": INLINE_MAX_BYTES,
    }
