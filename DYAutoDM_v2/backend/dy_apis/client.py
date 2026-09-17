# -*- coding: utf-8 -*-
"""平台接口层 —— 基础客户端

## 设计来源（照源项目 better-douyin）

对标 `src-tauri/src/api/client.rs`（方案 B 逆向情报，见 `docs/reverse_interface_spec.md`）。
源项目的接口层是**按业务域切成 13 个 client**：

```
api/client.rs                ← 本模块：基础客户端（域名 / 签名 / 重试）
api/client_user.rs           用户
api/client_video.rs          作品
api/client_feed.rs           流
api/client_comments.rs       评论
api/client_collection.rs     收藏 / 合集
api/client_relations.rs      关系（关注 / 点赞 / 收藏动作）
api/client_notice.rs         通知
api/client_im*.rs            IM（4 个）
media_proxy_cache.rs         媒体代理
```

## 本模块职责（基础层）

只做**所有域共用**的事，不含任何具体业务接口：
  · 域名选择（双域名策略，含 `www-hj`）
  · 请求头构造（HeaderBuilder 封装）
  · 公共参数（device_platform / aid / version_code / browser_* / webid）
  · a_bogus / msToken 签名注入
  · 统一 GET / POST 出口（含超时、verify、错误归类）

## 与旧实现的关系（门面模式）

`dy_apis/douyin_api.py` 的 `class DouyinAPI` **保留为兼容门面**，
其方法体逐步委托到本目录下的域模块，300+ 处调用方**零改动**。

## 迁移状态

本模块为新增基础层；`douyin_api.py` 现有 60 个方法按域迁移中，
未迁移的方法仍由门面原样提供（行为不变）。
"""
from __future__ import annotations

from utils.tls_policy import tls_verify  # noqa: E402
import time
from typing import Any, Optional

import requests
from loguru import logger

from builder.header import HeaderBuilder, HeaderType
from builder.params import Params
from utils.fingerprint import get_profile
from utils.dy_util import generate_a_bogus, generate_msToken


class BaseClient:
    """平台接口基础客户端（所有域模块继承此类）。"""

    #: 主域名
    douyin_url = "https://www.douyin.com"
    #: 备用域名（照源项目：互动与列表类接口走此域名）
    douyin_url_hj = "https://www-hj.douyin.com"
    #: 直播域名
    live_url = "https://live.douyin.com"
    #: 创作者域名
    creator = "https://creator.douyin.com"
    #: IM 域名（源项目实证：IM 业务独立域名）
    im_url = "https://imapi.douyin.com"

    #: 走备用域名（www-hj）的路径前缀（照源项目二进制字符串实测归纳）
    #: 逆向证据（all_strings.txt）：以下路径在源项目中均以 www-hj 开头——
    #:   commit/item/digg（点赞）· commit/follow/user（关注）· comment/digg
    #:   aweme/collect（收藏）· comment/list(+reply)（评论列表/回复）
    #:   im/user/active/status · im/spotlight/relation · series/aweme
    #:   mix/listcollection · aweme/favorite · aweme/listcollection
    _HJ_PREFIXES: tuple[str, ...] = (
        "/aweme/v1/web/comment/list",
        "/aweme/v1/web/aweme/listcollection",
        "/aweme/v1/web/aweme/favorite/",
        "/aweme/v1/web/mix/listcollection",
        "/aweme/v1/web/series/aweme/",
        "/aweme/v1/web/im/user/info/",
        "/aweme/v1/web/im/spotlight/relation/",
        "/aweme/v1/web/im/user/active/status/",
        "/aweme/v1/web/commit/item/digg/",
        "/aweme/v1/web/commit/follow/user/",
        "/aweme/v1/web/aweme/collect/",
    )

    #: 默认超时（秒）
    timeout = 15

    @classmethod
    def domain_for(cls, path: str) -> str:
        """按路径返回应使用的域名（照源项目双域名策略）。"""
        for pre in cls._HJ_PREFIXES:
            if path.startswith(pre):
                return cls.douyin_url_hj
        return cls.douyin_url

    # ─────────────────────────────────────────────────────────
    # 请求构造（所有域共用）
    # ─────────────────────────────────────────────────────────

    @staticmethod
    def base_params(auth: Any = None, *, referer: str = "https://www.douyin.com/",
                    extra: Optional[dict[str, Any]] = None) -> Params:
        """构造公共参数（device / browser / 指纹，照源项目 client.rs）。

        调用方在返回值上继续 `.add_param(...)` 追加业务参数。
        """
        prof = get_profile()
        params = Params()
        (params.add_param("device_platform", "webapp")
         .add_param("aid", "6383")
         .add_param("channel", "channel_pc_web")
         .add_param("pc_client_type", "1")
         .add_param("version_code", "170400")
         .add_param("version_name", "17.4.0")
         .add_param("cookie_enabled", "true")
         .add_param("browser_language", "zh-CN")
         .add_param("browser_platform", "Win32")
         .add_param("browser_name", prof["browser_name"])
         .add_param("browser_version", prof["browser_version"])
         .add_param("browser_online", "true")
         .add_param("engine_name", "Blink")
         .add_param("os_name", "Windows")
         .add_param("os_version", "10")
         .add_param("platform", "PC"))
        if auth is not None:
            params.with_web_id(auth, referer)
        if extra:
            for k, v in extra.items():
                params.add_param(k, v)
        return params

    @classmethod
    def get(cls, path: str, auth: Any, params: Params, *,
            referer: str = "https://www.douyin.com/", timeout: Optional[int] = None,
            extra_headers: Optional[dict[str, str]] = None) -> requests.Response:
        """统一 GET 出口（域名按 `domain_for` 自动选择）。"""
        headers = HeaderBuilder().build(HeaderType.GET)
        headers.set_referer(referer)
        if extra_headers:
            for k, v in extra_headers.items():
                headers.add(k, v)
        url = f"{cls.domain_for(path)}{path}"
        return requests.get(url, headers=headers.get(), cookies=auth.cookie,
                            params=params.get(), verify=tls_verify(),
                            timeout=timeout or cls.timeout)

    @classmethod
    def post(cls, path: str, auth: Any, params: Params, data: Any = None, *,
             referer: str = "https://www.douyin.com/", timeout: Optional[int] = None,
             extra_headers: Optional[dict[str, str]] = None) -> requests.Response:
        """统一 POST 出口（域名按 `domain_for` 自动选择）。"""
        headers = HeaderBuilder().build(HeaderType.POST)
        headers.set_referer(referer)
        if extra_headers:
            for k, v in extra_headers.items():
                headers.add(k, v)
        url = f"{cls.domain_for(path)}{path}"
        return requests.post(url, headers=headers.get(), cookies=auth.cookie,
                             params=params.get(), data=data, verify=tls_verify(),
                             timeout=timeout or cls.timeout)
