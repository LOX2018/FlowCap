"""图片图床上传（imgbb）——把抖音私有加密图片换成可直渲染的公开链接。

## 为什么需要

抖音 IM 图片消息有两个来源（2026-08-30 实测）：

| 来源 | 格式 | 能否内嵌 |
|---|---|---|
| `inline_pic`（消息体内嵌 base64） | 标准 WebP | ✅ 但 data URI 体积大、占库、检索慢 |
| `resource_url.*_url_list`（远程） | **私有加密**（熵 7.999/8.0，无图片魔数） | ❌ 浏览器解不开 |

内联图虽然能渲染，但**把几千字节的 base64 塞进每条消息**会让 SQLite 迅速膨胀
（实测 35 条图片消息合计 94KB，规模化后会是 MB 级），且前端每条都要解析一大坨字符串。

上传图床后库里只存 50 字节左右的 URL，前端 `<img src>` 直接渲染。

## 设计约束

- **零风控风险**：上传的是**本地已有的字节**，不发任何请求给抖音。
- **失败降级**：上传失败不影响主流程，返回 None，调用方保留原 data URI。
- **不硬编码密钥**：从 `settings.imgbb_api_key` 读（env `IMGBB_API_KEY`）；
  2026-10-02 起改为**配置中心优先**（`capture.image_host_custom_url/_key`，
  支持自建图床），回落 settings/env。
- **去重缓存**：同图（SHA1）只传一次，进程内缓存结果。
- **隐私提示**：imgbb 图床链接**公开可访问且 expiration=0 永久保存**，
  接入前必须经用户确认（已在 SKILL/知识库记录）。
"""
from __future__ import annotations

import base64
import hashlib
import json
import threading
import urllib.parse
import urllib.request
from typing import Optional

from loguru import logger

UPLOAD_URL = "https://api.imgbb.com/1/upload"

# 2026-08-31 实测对比（真实 4066B WebP）：
#   imgbb（境外）    上传频繁 SSL 超时，下载平均 2.98s，最慢 5.75s，还出现 31s 超时
#   tucdn.wpon.cn    上传 0.92s，下载平均 0.302s —— 提速 9.9×，且字节一致
# 故改为**国内图床优先**，imgbb 作为备选（可通过 IMAGE_HOST_BACKEND 切换）。
TUCDN_URL = "https://tucdn.wpon.cn/api/upload"

# 进程内缓存：sha1(data) -> 图床 URL
_cache: dict[str, Optional[str]] = {}
_lock = threading.Lock()
_stats = {"ok": 0, "fail": 0, "skip": 0}


def _get_key() -> str:
    """读 API Key（环境变量 IMGBB_API_KEY / .env）。不硬编码。"""
    try:
        from config import settings

        key = getattr(settings, "imgbb_api_key", "") or ""
        if key:
            return key
    except Exception:
        pass
    import os

    return os.environ.get("IMGBB_API_KEY", "") or ""


def _get_tucdn_token() -> str:
    """读国内图床 token（TUCDN_TOKEN / settings.tucdn_token）。不硬编码。"""
    try:
        from config import settings

        tok = getattr(settings, "tucdn_token", "") or ""
        if tok:
            return tok
    except Exception:
        pass
    import os

    return os.environ.get("TUCDN_TOKEN", "") or ""


def _cfg(key: str, default=None):
    """读配置中心（capture 分区）的值；未配置/异常回落 default。

    2026-10-02：本模块原只读 `config.settings`（= env/默认），配置中心里
    保存的值**不生效**（典型「填了没用」）。现统一为：
    **配置中心 → settings/env → 模块默认**（与 `cred_refresh_mode` 等消费方同构）。
    """
    try:
        from services.app_config import get as _ac_get

        return _ac_get("capture", key, default)
    except Exception:
        return default


def _backend() -> str:
    """图床后端：tucdn（默认，国内快）/ imgbb（境外备选）/ custom（自建）。"""
    b = ""
    try:
        from config import settings

        b = (getattr(settings, "image_host_backend", "") or "").lower()
    except Exception:
        pass
    import os

    b = b or (os.environ.get("IMAGE_HOST_BACKEND", "") or "").lower()
    # 配置中心优先（用户可在 UI 里切换）
    b = str(_cfg("image_host_backend", b) or b).lower()
    return b if b in ("tucdn", "imgbb", "custom") else "tucdn"


def _custom_url() -> str:
    """自建图床上传端点（配置中心 capture.image_host_custom_url）。"""
    return str(_cfg("image_host_custom_url", "") or "").strip()


def _custom_key() -> str:
    """自建图床 API Key（配置中心 capture.image_host_custom_key；留空则不鉴权）。"""
    return str(_cfg("image_host_custom_key", "") or "").strip()


def _enabled() -> bool:
    try:
        from config import settings

        return bool(getattr(settings, "imgbb_enabled", True))
    except Exception:
        return True


def _timeout() -> int:
    try:
        from config import settings

        return int(getattr(settings, "imgbb_timeout", 10) or 10)
    except Exception:
        return 10


def upload(data: bytes, name: str = "") -> Optional[str]:
    """上传图片字节到图床，返回可直接 <img> 渲染的 URL；失败返回 None。

    **2026-08-31 重要修正**：实测表明对本项目而言**内联 base64 优于图床**。

    实测数据（60 张真实私信图片）：
      - 图片平均 2.9KB（中位 4KB，最大 4.9KB），60 张合计仅 121KB
      - 全部内联进 SQLite：+161KB，数据库 1.21MB → 1.37MB，**仅增 13%**
      - 而图床渲染每张要多花 0.35s（tucdn）~2.5s（imgbb）跨网络下载

    结论：图片本来就只有 3KB，为省这点空间付出百毫秒级网络延迟是**本末倒置**。
    故调用方改为：小图（默认 ≤ 32KB）直接内联 base64，零网络请求、瞬时渲染；
    只有大图才走图床（本函数），避免单条消息过大拖慢列表查询。

    阈值由 settings.image_inline_max_kb 控制（0 = 永远内联，图床仅作兜底）。
    """
    if not data or len(data) < 16:
        return None
    if not _enabled():
        with _lock:
            _stats["skip"] += 1
        return None

    backend = _backend()

    if backend == "tucdn":
        token = _get_tucdn_token()
        if not token:
            with _lock:
                _stats["skip"] += 1
                if _stats["skip"] == 1:
                    logger.info(
                        "[图床] 未配置 TUCDN_TOKEN，跳过上传（大图将内联渲染）"
                    )
            return None
    elif backend == "custom":
        # 自建图床：只需 URL；Key 可空（部分自建服务不鉴权）
        if not _custom_url():
            with _lock:
                _stats["skip"] += 1
                if _stats["skip"] == 1:
                    logger.info(
                        "[图床] 已选「自定义」但未填「图床 API 地址」，"
                        "跳过上传（大图将内联渲染）"
                    )
            return None
    else:
        key = _get_key()
        if not key:
            with _lock:
                _stats["skip"] += 1
                if _stats["skip"] == 1:
                    logger.info(
                        "[imgbb] 未配置 API Key（IMGBB_API_KEY），跳过上传，"
                        "图片将以内联 base64 形式渲染"
                    )
            return None

    sha = hashlib.sha1(data).hexdigest()
    with _lock:
        if sha in _cache:
            return _cache[sha]

    try:
        if backend == "tucdn":
            url = _upload_tucdn(data, token, name)
        elif backend == "custom":
            url = _upload_custom(data, _custom_url(), _custom_key(), name)
        else:
            url = _upload_imgbb(data, key, name)
        if not url:
            raise RuntimeError("响应缺少 url 字段")
        with _lock:
            _cache[sha] = url
            _stats["ok"] += 1
        logger.debug(f"[图床][{backend}] 上传成功 {len(data)}B -> {url}")
        return url
    except Exception as e:
        with _lock:
            _cache[sha] = None  # 失败也缓存，避免反复重试拖慢捕获
            _stats["fail"] += 1
        logger.warning(f"[IMG-001] " + f"[图床][{backend}] 上传失败（降级内联）: {str(e)[:100]}")
        return None


def _upload_tucdn(data: bytes, token: str, name: str = "") -> Optional[str]:
    """上传到 tucdn.wpon.cn（国内图床，实测比 imgbb 快 6.4×）。

    接口：POST /api/upload，multipart/form-data，字段名 image，token 放 header。
    响应：{"code":200,"data":{"url":"https://tucdn.wpon.cn/..."}}
    """
    import uuid

    boundary = "----Hermes" + uuid.uuid4().hex
    fname = (name or "image") + ".webp"
    body = (
        f"--{boundary}\r\n".encode()
        + f'Content-Disposition: form-data; name="image"; filename="{fname}"\r\n'.encode()
        + b"Content-Type: image/webp\r\n\r\n"
        + data
        + b"\r\n"
        + f"--{boundary}--\r\n".encode()
    )
    req = urllib.request.Request(
        TUCDN_URL,
        data=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "token": token,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=_timeout()) as r:
        j = json.loads(r.read().decode("utf-8", "replace"))
    if j.get("code") != 200:
        raise RuntimeError(f"code={j.get('code')} msg={j.get('msg')}")
    url = (j.get("data") or {}).get("url") or ""
    # 兼容协议相对路径 //host/path
    if url.startswith("//"):
        url = "https:" + url
    return url or None


def _upload_custom(data: bytes, url: str, key: str, name: str = "") -> Optional[str]:
    """上传到**自建图床**（2026-10-02 用户要求：图床是用户自己的服务）。

    请求形态（最常见自建图床约定）：
      · POST `multipart/form-data`，文件字段名 `image`
      · 鉴权：`Authorization: Bearer <key>`（key 为空则不发送该头）
    响应兼容多种常见形状（尽力而为，取第一个非空 url）：
      · `{"data":{"url": "..."}}`（imgbb / tucdn 风格）
      · `{"url": "..."}` / `{"data":"..."}` / `{"link":"..."}`
    失败抛异常 → 由 `upload()` 统一降级（内联 base64），不阻断主流程。
    """
    import uuid

    boundary = "----Chuanliu" + uuid.uuid4().hex
    fname = (name or "image") + ".webp"
    body = (
        f"--{boundary}\r\n".encode()
        + f'Content-Disposition: form-data; name="image"; filename="{fname}"\r\n'.encode()
        + b"Content-Type: image/webp\r\n\r\n"
        + data
        + b"\r\n"
        + f"--{boundary}--\r\n".encode()
    )
    headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=_timeout()) as r:
        j = json.loads(r.read().decode("utf-8", "replace"))

    if not isinstance(j, dict):
        raise RuntimeError(f"响应非对象: {str(j)[:120]}")
    d = j.get("data")
    cand = None
    if isinstance(d, dict):
        cand = d.get("url") or d.get("link") or d.get("display_url")
    elif isinstance(d, str):
        cand = d
    cand = cand or j.get("url") or j.get("link")
    if not cand:
        raise RuntimeError(f"响应缺少 url 字段: {str(j)[:120]}")
    cand = str(cand)
    # 兼容协议相对路径 //host/path
    if cand.startswith("//"):
        cand = "https:" + cand
    return cand or None


def _upload_imgbb(data: bytes, key: str, name: str = "") -> Optional[str]:
    """上传到 imgbb（境外备选）。响应：{"success":true,"data":{"url":...}}"""
    b64 = base64.b64encode(data).decode("ascii")
    if name:
        post = urllib.parse.urlencode(
            {"key": key, "image": b64, "name": name}).encode()
    else:
        post = urllib.parse.urlencode({"key": key, "image": b64}).encode()
    req = urllib.request.Request(UPLOAD_URL, data=post)
    with urllib.request.urlopen(req, timeout=_timeout()) as r:
        j = json.loads(r.read().decode("utf-8", "replace"))
    if not j.get("success"):
        raise RuntimeError(f"响应 success=false: {str(j)[:120]}")
    d = j.get("data") or {}
    return d.get("url") or d.get("display_url") or None


def upload_base64(b64_or_datauri: str, name: str = "") -> Optional[str]:
    """上传 base64 字符串或 data URI。"""
    if not b64_or_datauri:
        return None
    s = b64_or_datauri
    if s.startswith("data:"):
        s = s.split(",", 1)[-1]
    try:
        raw = base64.b64decode(s + "=" * ((-len(s)) % 4))
    except Exception:
        return None
    return upload(raw, name=name)


def stats() -> dict:
    with _lock:
        return {
            "ok": _stats["ok"],
            "fail": _stats["fail"],
            "skip": _stats["skip"],
            "cached": len(_cache),
        }
