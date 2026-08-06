# coding=utf-8
"""VirtualBrowser 指纹浏览器后端接入层。

集成方式（基于 VirtualBrowser 仓库 automation/test-api.py 的真实示例，非臆测）：
  1) VirtualBrowser 客户端运行一个本地服务（默认 http://localhost:9000）；
  2) 通过 REST API 启动一个已配置好指纹的浏览器环境：
         POST {api_base}/api/launchBrowser  json={"id": <环境ID>}
     -> 返回 {"success": true, "data": {"debuggingPort": <端口>}}
  3) 用 Playwright 的 connect_over_cdp 接管该端口，直接复用 VirtualBrowser 建好的
     带指纹上下文（browser.contexts[0]），**不要**自己 launch_persistent_context
     （那样建的是无指纹的空上下文）。

注意：
- 环境ID（worker-id）需在 VirtualBrowser 客户端里提前创建好，否则 API 返回 success=false。
- 本模块只负责“启动环境 + 连 CDP”，指纹伪装由 VirtualBrowser 自身完成。
- 若 VirtualBrowser 未安装 / 服务未启动 / 未启用，调用方应回退到原生 Playwright。
"""

import time

import requests
from loguru import logger


def launch_vb_env(env_id, api_base="http://localhost:9000", timeout=30):
    """调用 VirtualBrowser 本地 API 启动指定环境，返回 CDP 调试端口（int）或 None。

    :param env_id: 在 VirtualBrowser 客户端中创建的环境 ID（整数）
    :param api_base: VirtualBrowser 本地服务地址
    :param timeout: 请求超时（秒）
    :return: debuggingPort（int）或 None（启动失败）
    """
    try:
        resp = requests.post(
            f"{api_base}/api/launchBrowser",
            json={"id": env_id},
            headers={"Content-Type": "application/json"},
            timeout=timeout,
        )
        data = resp.json()
    except Exception as e:
        logger.warning(f"[vbrowser] 调用启动 API 失败（服务未启动？）: {e}")
        return None
    if not data.get("success"):
        logger.warning(f"[vbrowser] 启动环境失败: {data}")
        return None
    port = (data.get("data") or {}).get("debuggingPort")
    if not port:
        logger.warning(f"[vbrowser] 响应缺少 debuggingPort: {data}")
        return None
    logger.info(f"[vbrowser] 环境 {env_id} 已启动，CDP 端口={port}")
    return int(port)


def is_vb_available(api_base="http://localhost:9000", timeout=3):
    """探测 VirtualBrowser 本地服务是否可达（用于决定回退）。"""
    try:
        r = requests.get(f"{api_base}/api/status", timeout=timeout)
        return r.status_code < 500
    except Exception:
        return False


# ---- 异步接管（供 login_api.py / web_probe.py 使用）----
async def connect_async(port, api_base="http://localhost:9000"):
    """connect_over_cdp 拿到 (browser, context)，context 为 VirtualBrowser 建好的指纹上下文。"""
    from playwright.async_api import async_playwright
    p = await async_playwright().start()
    browser = await p.chromium.connect_over_cdp(f"http://localhost:{port}")
    context = browser.contexts[0] if browser.contexts else await browser.new_context()
    return p, browser, context


# ---- 同步接管（供 link_resolve.py 使用）----
def connect_sync(port, api_base="http://localhost:9000"):
    """同步版 connect_over_cdp，返回 (playwright, browser, context)。"""
    from playwright.sync_api import sync_playwright
    p = sync_playwright().start()
    browser = p.chromium.connect_over_cdp(f"http://localhost:{port}")
    context = browser.contexts[0] if browser.contexts else browser.new_context()
    return p, browser, context
