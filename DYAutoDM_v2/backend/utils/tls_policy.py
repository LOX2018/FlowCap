# -*- coding: utf-8 -*-
"""全局 TLS 校验策略（2026-09-17 引入，收敛全库 64 处 verify=False）。

## 背景

第三方静态审查（OCR）指出：`dy_apis/`、`link_resolve.py`、`downloader/`
等模块共 **64 处** requests 调用硬编码 `verify=False`，其中多数**携带账号
登录态 cookie**（sessionid / sid_tt / ttsid）。这意味着同网段中间人可
窃取凭据或篡改响应，属真实传输层风险。

## 策略

统一由本模块的 `tls_verify()` 提供校验开关，**默认开启校验**：

    from utils.tls_policy import tls_verify
    requests.get(url, ..., verify=tls_verify())

仅当确实需要（企业代理 / 自签根证书未安装）时，设环境变量
`DY_TLS_INSECURE=1` 才关闭，并在首次读取时打一条 warning，便于审计。

## 为什么不是简单删掉 verify=False

部分部署环境（内网代理、证书拦截）确实依赖跳过校验；直接开启会让
请求全部失败。故保留**显式开关**而非硬编码，把「默认安全 + 按需降级」
的选择权交给部署方（符合项目「显式配置」原则）。

## 2026-09-17 增强：原生 OS 信任库（对照上游 douyin-chat-export 的 common/tls.py）

上游做法：`truststore.SSLContext(...)` + `certifi` 显式 CA，**绝不关闭校验**，
并额外尊重 `SSL_CERT_FILE` / `SSL_CERT_DIR`（自签 CA 的规范注入点）。

本项目对应实现 `install_truststore()`：
  · 用 `truststore.inject_into_ssl()` 让 stdlib/requests 的 TLS **信任原生 OS
    信任库** —— 企业内网把自签根证书装进系统信任库后即可正常校验，
    **不必**再退回 `verify=False`（这是「跳过校验」之外的正解）；
  · 同时加载 `certifi` 公共 CA（两者**叠加**，不做替换）；
  · 全程**只增加信任来源，绝不关闭 hostname / 证书校验**。

开关（显式配置原则）：
  · 默认**开启**（与上游默认行为一致，属纯增强：只多信 OS 里已受信的根）；
  · `DY_TLS_NO_TRUSTSTORE=1` → 不注入，完全维持注入前行为（回退开关）；
  · truststore 未安装 → 静默降级为「不注入」，不影响任何请求。

调用点：`backend/main.py` 启动时调用一次（best-effort，失败不阻断启动）。
"""
from __future__ import annotations

import os
import ssl
from functools import lru_cache

_WARNED = False
_INSTALLED = False
_INSTALL_LOGGED = False


def _insecure() -> bool:
    return os.environ.get("DY_TLS_INSECURE", "") == "1"


def _truststore_disabled() -> bool:
    return os.environ.get("DY_TLS_NO_TRUSTSTORE", "") == "1"


def tls_verify() -> bool:
    """返回 requests 的 verify 参数值。

    默认 True（开启证书校验）。设 DY_TLS_INSECURE=1 时返回 False
    并输出一次性告警。
    """
    global _WARNED
    if _insecure():
        if not _WARNED:
            _WARNED = True
            try:
                from loguru import logger
                logger.warning(f"[TLS-001] " + "[tls] ⚠️ DY_TLS_INSECURE=1：全库已关闭 TLS 证书校验，"
                    "存在中间人窃取账号 cookie 的风险（仅内网/自签证书环境使用）")
            except Exception:
                pass
        return False
    return True


@lru_cache(maxsize=1)
def client_ssl_context():
    """构造「原生 OS 信任库 + certifi 公共 CA」叠加的 SSLContext。

    供需要显式传 context 的调用点使用（httpx / urllib3 高级用法）。
    只叠加信任来源，**不关闭** hostname / 证书校验。
    返回 None 表示环境不支持（调用方应回退到默认校验）。
    """
    try:
        import truststore  # type: ignore
    except Exception:
        return None
    try:
        ctx = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception:
        return None
    # 叠加 certifi（若可用）；失败不影响 OS 信任库已生效的部分。
    try:
        import certifi  # type: ignore
        ctx.load_verify_locations(cafile=certifi.where())
    except Exception:
        pass
    for env, kw in (("SSL_CERT_FILE", "cafile"), ("SSL_CERT_DIR", "capath")):
        val = os.environ.get(env)
        if not val:
            continue
        try:
            ctx.load_verify_locations(**{kw: val})
        except Exception:
            pass
    return ctx


def install_truststore() -> bool:
    """把原生 OS 信任库接入 stdlib SSL（requests / urllib3 随之生效）。

    best-effort：任何异常都吞掉并返回 False，绝不阻断启动。
    幂等：重复调用只生效一次。返回 True 表示已完成（或此前已完成）注入。
    """
    global _INSTALLED, _INSTALL_LOGGED
    if _INSTALLED:
        return True
    if _truststore_disabled():
        if not _INSTALL_LOGGED:
            _INSTALL_LOGGED = True
            try:
                from loguru import logger
                logger.info(f"[TLS-002] " + "[tls] DY_TLS_NO_TRUSTSTORE=1："
                            "跳过原生 OS 信任库注入（维持原有信任来源）")
            except Exception:
                pass
        return False
    try:
        import truststore  # type: ignore
        truststore.inject_into_ssl()
        _INSTALLED = True
        if not _INSTALL_LOGGED:
            _INSTALL_LOGGED = True
            try:
                from loguru import logger
                _extra = ""
                try:
                    import certifi  # type: ignore
                    _extra = f"，certifi={certifi.where()}"
                except Exception:
                    pass
                logger.info(f"[TLS-003] " + "[tls] 已接入原生 OS 信任库"
                            f"（truststore inject_into_ssl）{_extra}——"
                            "自签根证书装入系统信任库即可正常校验，无需关闭校验")
            except Exception:
                pass
        return True
    except Exception as e:  # noqa: BLE001
        if not _INSTALL_LOGGED:
            _INSTALL_LOGGED = True
            try:
                from loguru import logger
                logger.info(f"[TLS-004] " + f"[tls] 未接入原生 OS 信任库（{type(e).__name__}）："
                            "继续使用默认信任来源（certifi）")
            except Exception:
                pass
        return False
