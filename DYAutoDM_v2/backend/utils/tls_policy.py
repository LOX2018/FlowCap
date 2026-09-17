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
"""
from __future__ import annotations

import os

_WARNED = False


def _insecure() -> bool:
    return os.environ.get("DY_TLS_INSECURE", "") == "1"


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
                logger.warning(
                    "TLS-001",
                    "[tls] ⚠️ DY_TLS_INSECURE=1：全库已关闭 TLS 证书校验，"
                    "存在中间人窃取账号 cookie 的风险（仅内网/自签证书环境使用）")
            except Exception:
                pass
        return False
    return True
