"""
加密原图解析器（2026-09-02 实机落地）

目标:把数据库 dm_messages.extra 里存的 (skey, origin_url) 解密出真原图,
     按尺寸分流:小图本地静态托管,大图上图床。

与 image_host.py 的关系:
- image_host.upload_base64 上传**已有的图片字节**(inline_pic)到图床 → 缩略图场景
- 本模块从**加密密文**解密 → **原图**场景,优先级高于缩略图(原图才是用户想要的)

风控边界(2026-09-02 实测):
- 拉的是 p*-sign.douyinpic.com 的加密 HTTP(GET),Referer 设 douyin.com
- 不复用账号 cookie,只用图片 URL 自带的签名(lk3s/x-expires)
- 频次低:首次访问时解密,落本地缓存 + sha1 去重,后续 0 请求
- 解密全在本地完成,密钥不外发

依赖:cryptography(已在 PyInstaller 冻结,但未写进 requirements.txt;
      落地后必须补进 requirements.txt 并打包验证)
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional, Tuple

from loguru import logger


# ---------------- 配置 ----------------

# 阈值:解密后字节数 ≤ 该值 → 本地静态托管(避免每次都调图床);
#                     > 该值 → 上图床。
# 与 image_host._inline_max_kb 保持同一阈值,语义一致。
def _local_threshold_bytes() -> int:
    """解密后字节阈值;≤ 该值本地托管,> 该值走图床。
    默认 32KB,与 image_host 内联 base64 阈值一致——保持"小图内联,大图外链"统一语义。
    """
    try:
        from config import settings
        v = getattr(settings, "image_inline_max_kb", None)
        if v is not None:
            return int(v) * 1024
    except Exception:
        pass
    try:
        return int(os.environ.get("IMAGE_INLINE_MAX_KB", "32")) * 1024
    except Exception:
        return 32 * 1024


def _origin_cache_dir() -> Path:
    """本地静态目录根:<app_root>/data/origin_images/。
    app_root 在 frozen 态 = exe 所在目录(随附 data/),源码态 = 项目根。
    """
    try:
        from auto_dm import accounts as _acc
        root = _acc.app_root()
    except Exception:
        root = os.path.dirname(os.path.abspath(__file__))
    p = Path(root) / "data" / "origin_images"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _ttl_seconds() -> int:
    """本地原图的存活时长(秒)。超过且期间未被访问 → 被 sweep 清理。

    默认 30 天。可用 settings.origin_image_ttl_days 或环境变量
    ORIGIN_IMAGE_TTL_DAYS 覆盖;设为 0 = 永不过期(不清理)。

    ⚠️ 与 resolve() 的 max_age_sec 默认保持一致 —— 否则会出现
    「内存缓存说命中、但磁盘文件已被 sweep 删除」→ 前端 404。
    """
    try:
        from config import settings
        v = getattr(settings, "origin_image_ttl_days", None)
        if v is not None:
            return int(v) * 86400
    except Exception:
        pass
    try:
        return int(os.environ.get("ORIGIN_IMAGE_TTL_DAYS", "30")) * 86400
    except Exception:
        return 30 * 86400


def _max_cache_bytes() -> int:
    """本地原图目录的体积上限(字节);sweep 时若超限,从最旧的开始删。

    默认 2 GB。0 = 不限。环境变量 ORIGIN_IMAGE_MAX_MB。
    设置理由:实测单张原图 170~410KB,2GB 约容纳 5000~10000 张,
    对私信场景足够,同时兜住磁盘不被长跑撑爆。
    """
    try:
        from config import settings
        v = getattr(settings, "origin_image_max_mb", None)
        if v is not None:
            return int(v) * 1024 * 1024
    except Exception:
        pass
    try:
        return int(os.environ.get("ORIGIN_IMAGE_MAX_MB", "2048")) * 1024 * 1024
    except Exception:
        return 2048 * 1024 * 1024


def touch_local(filename: str) -> None:
    """刷新本地文件的 mtime(表示"最近访问过"),避免被 sweep 清掉。

    由 serve_origin_image 每次命中时调用 —— 这样"最近看过"的图不会被删,
    而长期不看的图到期后自然回收。
    """
    if not filename:
        return
    try:
        fpath = _origin_cache_dir() / filename
        if fpath.exists():
            os.utime(fpath, None)
    except Exception:
        pass


# ---------------- 进程内缓存 ----------------

# sha1(skey|origin_url) -> {"kind","url","format","size","ts"}
_cache: dict[str, dict] = {}
_lock = threading.Lock()
_stats = {"hit": 0, "miss": 0, "decrypt_fail": 0, "hosted": 0, "local": 0, "err": 0}

# HTTP 内存缓存:url -> 密文 bytes(短 TTL,避免短时间内重复拉取)
_http_cache: dict[str, tuple[float, bytes]] = {}
_HTTP_TTL = 300  # 5 分钟


def _stats_snapshot() -> dict:
    with _lock:
        return dict(_stats)


# ---------------- 核心:拉密文 + 解密 ----------------

def _http_get(url: str, timeout: int = 20) -> bytes:
    """GET 拉密文;带 Referer 模拟 douyin 域;走 5 分钟内存缓存。"""
    now = time.time()
    cached = _http_cache.get(url)
    if cached and (now - cached[0]) < _HTTP_TTL:
        return cached[1]
    req = urllib.request.Request(url, method="GET", headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Referer": "https://www.douyin.com/",
        "Accept": "*/*",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.reason}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"URL 错误: {e.reason}") from e
    _http_cache[url] = (now, data)
    return data


def _decrypt(cipher: bytes, skey_hex: str) -> bytes:
    """AES-256-GCM 解密(MEMORY + 08 §三十五 一致):
        key = bytes.fromhex(skey)        # 64 hex = 32 字节
        iv  = cipher[:12]
        plain = AESGCM(key).decrypt(iv, cipher[12:], None)
    """
    if not skey_hex:
        raise RuntimeError("skey 为空")
    try:
        key = bytes.fromhex(skey_hex)
    except Exception as e:
        raise RuntimeError(f"skey 解析失败: {e}")
    if len(key) != 32:
        raise RuntimeError(f"skey 长度 {len(key)} 字节,期望 32")
    if len(cipher) < 28:
        raise RuntimeError(f"密文过短: {len(cipher)} 字节")
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    iv = cipher[:12]
    body = cipher[12:]
    return AESGCM(key).decrypt(iv, body, None)


def _detect_format(head: bytes) -> Tuple[str, str]:
    """识别图片格式;返回 (ext, mime)。
    HEIC/HEIF 实测占比 40%(MEMORY);Windows 默认不支持,这里同时输出
    extension = 'jpg' 让前端 <img> 可显示(配合 HEIC→JPEG 转码)。
    """
    if head.startswith(b"\xff\xd8\xff"):
        return "jpg", "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png", "image/png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp", "image/webp"
    if head[:4] == b"GIF8":
        return "gif", "image/gif"
    # HEIC/HEIF: 第 4~7 字节 'ftyp', 第 8~11 字节 'heic'/'heix'/'mif1'/'msf1'
    if len(head) >= 12 and head[4:8] == b"ftyp":
        brand = head[8:12].lower()
        if brand in (b"heic", b"heix", b"hevc", b"mif1", b"msf1"):
            return "heic", "image/heic"
    return "bin", "application/octet-stream"


def _heic_to_jpeg(data: bytes) -> Optional[bytes]:
    """HEIC → JPEG 转码;pillow_heif 未装则返回 None 让调用方保留 HEIC。"""
    try:
        from PIL import Image
        try:
            from pillow_heif import register_heif_opener
            register_heif_opener()
        except Exception:
            pass
        from io import BytesIO
        img = Image.open(BytesIO(data))
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGB")
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=92)
        return buf.getvalue()
    except Exception as e:
        logger.debug(f"[origin_image] HEIC→JPEG 转码失败（保留原格式）: {e}")
        return None


# ---------------- 公开 API ----------------

def resolve(account: str, msg_id: str, skey: str, origin_url: str,
             max_age_sec: int | None = None) -> dict:
    """主入口:解析一条加密图片消息 → 返回前端可直接使用的 image_url。

    max_age_sec 默认取 _ttl_seconds()(30 天),与磁盘 TTL 一致 ——
    否则会出现「内存缓存命中但磁盘文件已被 sweep 删除」→ 前端 404。

    返回:
      {
        "ok": True,
        "kind": "local" | "hosted" | "inline_base64",
        "url": "http://127.0.0.1:9912/api/messages/origin_image/<msg_id>.jpg"
             | "https://tucdn.wpon.cn/..."
             | "data:image/jpeg;base64,...",
        "format": "jpeg"|"png"|"webp"|"gif"|"heic",
        "size": 12345,
        "cached": bool,
      }
    失败:{"ok": False, "error": "..."}
    """
    if not skey or not origin_url:
        return {"ok": False, "error": "skey/origin_url 缺失"}

    if max_age_sec is None:
        max_age_sec = _ttl_seconds()

    # 缓存键:sha1(skey + origin_url) —— 同一张图不同 msg_id 也能命中
    cache_key = hashlib.sha1(f"{skey}|{origin_url}".encode()).hexdigest()
    now = time.time()

    with _lock:
        cached = _cache.get(cache_key)
    if cached and (now - cached["ts"]) < max_age_sec and cached["url"]:
        with _lock:
            _stats["hit"] += 1
        return {
            "ok": True,
            "kind": cached["kind"],
            "url": cached["url"],
            "format": cached["format"],
            "size": cached["size"],
            "cached": True,
        }

    # 拉密文 + 解密
    try:
        cipher = _http_get(origin_url)
    except Exception as e:
        with _lock:
            _stats["err"] += 1
        return {"ok": False, "error": f"拉密文失败: {e}"}

    try:
        plain = _decrypt(cipher, skey)
    except Exception as e:
        with _lock:
            _stats["decrypt_fail"] += 1
        return {"ok": False, "error": f"解密失败: {e}"}

    with _lock:
        _stats["miss"] += 1

    # HEIC → JPEG(浏览器默认不支持 HEIC)
    head = plain[:16]
    ext, mime = _detect_format(head)
    if ext == "heic":
        jpeg = _heic_to_jpeg(plain)
        if jpeg:
            plain = jpeg
            ext, mime = "jpg", "image/jpeg"

    size = len(plain)
    sha = hashlib.sha1(plain).hexdigest()

    # 阈值分流
    threshold = _local_threshold_bytes()
    # 2026-09-02 实测：tucdn 图床偶发 502/SSL 超时(MEMORY 已知);
    # imgbb 频繁 SSL 超时。对原图场景(每张都解密后上传),
    # 网络抖动会拖累会话详情响应,且 30 天缓存已能避免重复拉取。
    # 故大图也走本地,仅在显式开启 IMAGE_FORCE_HOSTED=1 时上图床。
    force_hosted = os.environ.get("IMAGE_FORCE_HOSTED", "0") == "1"
    if size <= threshold or not force_hosted:
        # 小图:本地静态托管,文件名带 sha 防撞
        cache_dir = _origin_cache_dir()
        fpath = cache_dir / f"{msg_id or sha[:12]}_{sha[:8]}.{ext}"
        if not fpath.exists():
            try:
                fpath.write_bytes(plain)
            except Exception as e:
                logger.warning(f"[origin_image] 写本地失败 {fpath}: {e}")
                return {"ok": False, "error": f"写本地失败: {e}"}
        # URL 由 messages.py 端点提供,这里只返回 path 供端点拼装
        kind = "local"
        url = f"/api/messages/origin_image/{fpath.name}"
        with _lock:
            _stats["local"] += 1
    else:
        # 大图:走图床;失败则降级为本地
        try:
            from auto_dm import image_host
            hosted = image_host.upload(plain, name=f"origin_{msg_id or sha[:8]}.{ext}")
        except Exception as e:
            logger.warning(f"[origin_image] 图床上传模块导入/调用失败: {e}")
            hosted = None
        if hosted:
            kind, url = "hosted", hosted
            with _lock:
                _stats["hosted"] += 1
        else:
            # 降级:仍走本地
            cache_dir = _origin_cache_dir()
            fpath = cache_dir / f"{msg_id or sha[:12]}_{sha[:8]}.{ext}"
            if not fpath.exists():
                try:
                    fpath.write_bytes(plain)
                except Exception as e:
                    return {"ok": False, "error": f"写本地失败: {e}"}
            kind, url = "local", f"/api/messages/origin_image/{fpath.name}"
            with _lock:
                _stats["local"] += 1

    # 分流后顺手节流清理(TTL/超容)—— 1 小时节流内实际不扫盘,开销可忽略。
    # 这样即使 backend 从未重启,本地目录也能持续回收,不会无限膨胀。
    try:
        sweep(force=False)
    except Exception:
        pass

    with _lock:
        _cache[cache_key] = {
            "kind": kind, "url": url, "format": ext, "size": size, "ts": now,
        }
    return {
        "ok": True,
        "kind": kind,
        "url": url,
        "format": ext,
        "size": size,
        "cached": False,
    }


def clear_cache() -> None:
    """清理进程内缓存(测试用)。"""
    with _lock:
        _cache.clear()
        _http_cache.clear()
        for k in _stats:
            _stats[k] = 0


# ---------------------------------------------------------------------------
# TTL 清理（2026-09-03）
# ---------------------------------------------------------------------------
# 本地原图目录会随会话增长而膨胀(实测单张 170~410KB)。两条回收规则:
#   ① 过期(TTL):文件 mtime 距今 > TTL → 删。mtime 由 touch_local() 在每次
#      被访问时刷新,所以"最近看过"的图不会被误删。
#   ② 超容(MAX_MB):目录总字节 > 上限 → 按 mtime 升序(最旧的优先)删到限额内。
# 判龄用 mtime 而非内存状态:_origin_cache_dir() 随 app_root 变化,
# 进程重启后内存缓存已空,只有 mtime 是跨进程可靠的年龄依据。
# ---------------------------------------------------------------------------

# 进程内节流:距上次 sweep 不足该秒数则跳过(避免高频接口反复扫盘)
_sweep_last_ts = 0.0
_SWEEP_MIN_INTERVAL = 3600  # 1 小时


def sweep(force: bool = False, ttl_sec: int | None = None,
          max_bytes: int | None = None) -> dict:
    """清理本地原图缓存。返回 {ok, removed, freed_bytes, remaining, kept, elapsed}。

    - force=True:忽略 1 小时节流,强制执行(启动时调用)。
    - ttl_sec / max_bytes:覆盖默认配置(测试用)。
    """
    global _sweep_last_ts
    now = time.time()
    if not force and (now - _sweep_last_ts) < _SWEEP_MIN_INTERVAL:
        return {"ok": True, "skipped": True, "reason": "throttled（1 小时内已清理过）"}
    _sweep_last_ts = now

    ttl = _ttl_seconds() if ttl_sec is None else ttl_sec
    cap = _max_cache_bytes() if max_bytes is None else max_bytes

    try:
        cache_dir = _origin_cache_dir()
    except Exception as e:
        return {"ok": False, "error": f"缓存目录不可用: {e}"}

    # 收集所有文件及其 (mtime, size)
    items = []
    try:
        for p in cache_dir.iterdir():
            if not p.is_file():
                continue
            try:
                st = p.stat()
                items.append((p, st.st_mtime, st.st_size))
            except Exception:
                continue
    except Exception as e:
        return {"ok": False, "error": f"遍历目录失败: {e}"}

    removed = 0
    freed = 0

    # ① 过期清理(TTL)。ttl<=0 视为永不过期。
    if ttl and ttl > 0:
        for p, mtime, size in items:
            if (now - mtime) > ttl:
                try:
                    p.unlink()
                    removed += 1
                    freed += size
                except Exception:
                    pass

    # ② 超容清理:重新统计剩余,按 mtime 升序删到限额内
    remaining_items = []
    total = 0
    try:
        for p in cache_dir.iterdir():
            if not p.is_file():
                continue
            try:
                st = p.stat()
                remaining_items.append((p, st.st_mtime, st.st_size))
                total += st.st_size
            except Exception:
                continue
    except Exception:
        remaining_items = []
        total = 0

    if cap and cap > 0 and total > cap:
        remaining_items.sort(key=lambda x: x[1])  # 最旧的在前
        for p, mtime, size in remaining_items:
            if total <= cap:
                break
            try:
                p.unlink()
                total -= size
                removed += 1
                freed += size
            except Exception:
                continue

    result = {
        "ok": True,
        "removed": removed,
        "freed_bytes": freed,
        "freed_mb": round(freed / 1024 / 1024, 2),
        "remaining": len(remaining_items),
        "remaining_mb": round(total / 1024 / 1024, 2),
        "ttl_days": round(ttl / 86400, 1) if ttl else 0,
        "max_mb": round(cap / 1024 / 1024, 1) if cap else 0,
        "elapsed": round(time.time() - now, 3),
    }
    if removed:
        # 删除后重新统计剩余,保证返回数字准确
        try:
            kept = sum(1 for p in cache_dir.iterdir() if p.is_file())
            total = sum(p.stat().st_size for p in cache_dir.iterdir() if p.is_file())
        except Exception:
            kept, total = 0, 0
        result["remaining"] = kept
        result["remaining_mb"] = round(total / 1024 / 1024, 2)
        logger.info(
            f"[origin_image] TTL 清理:删除 {removed} 张,释放 {result['freed_mb']}MB,"
            f"剩余 {kept} 张 / {result['remaining_mb']}MB"
        )
    return result


def sweep_background() -> None:
    """后台线程入口:启动后延迟执行,不阻塞主流程。"""
    import threading

    def _run():
        # 延迟 60s:避开启动高峰(此时正在拉会话/解密图片)
        time.sleep(60)
        try:
            res = sweep(force=True)
            if res.get("ok") and res.get("removed"):
                logger.info(f"[origin_image] 启动清理完成: {res}")
        except Exception as e:
            logger.debug(f"[origin_image] 启动清理失败(忽略): {e}")

    threading.Thread(target=_run, daemon=True).start()
