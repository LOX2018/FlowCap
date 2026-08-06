# coding=utf-8
"""指纹浏览器后端接入层（可切换，默认不启用，回退原生 Playwright）。

支持两种指纹内核：

模式 A —— fingerprint-chromium（直接启动带指纹的 chrome.exe，无需服务）：
  - 基于 Ungoogled Chromium 源码级随机化（Canvas/WebGL/音频/字体等），
    编译产物就是一个标准 chrome.exe，可被 Playwright 直接以 executable_path 启动。
  - 配置：VB_MODE="exe"，VB_CHROME_EXE="路径/chrome.exe"。
  - 优势：无需安装任何 GUI 客户端，无本地 REST 服务依赖。

模式 B —— VirtualBrowser / Ant-Browser（CDP 接管，需本地服务）：
  - 客户端运行本地服务（默认 http://localhost:9000），POST /api/launchBrowser
    启动环境并返回 CDP debuggingPort，外部脚本 connect_over_cdp 接管其指纹上下文。
  - 配置：VB_MODE="cdp"，VB_API_BASE / VB_ENV_ID。

两种模式都通过 config.USE_VIRTUAL_BROWSER 总开关控制；未启用或服务/文件不可达时，
调用方（login_api / web_probe / link_resolve）自动回退到原生 Playwright，行为不变。

注意：指纹伪装由对应内核自身完成，本模块只负责“启动内核 + 把 page/context 交给调用方”。
"""

import os
import time

import requests
from loguru import logger


# ---------- 模式 B：CDP 接管（VirtualBrowser / Ant-Browser）----------

def launch_vb_env(env_id, api_base="http://localhost:9000", timeout=30):
    """调用本地 API 启动指定环境，返回 CDP 调试端口（int）或 None。"""
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
    """探测本地服务是否可达（决定是否走 CDP 模式）。"""
    try:
        r = requests.get(f"{api_base}/api/status", timeout=timeout)
        return r.status_code < 500
    except Exception:
        return False


# ---------- 统一启动入口（被 3 处浏览器使用点调用）----------

async def launch_async(mode, cfg, headless=False):
    """异步启动指纹内核，返回 (playwright, browser, context, backend)。

    backend 用于调用方决定收尾时是否关闭 context：
      - "exe" 模式：context 由我们 launch 出来，结束时需关闭（与原生 Playwright 一致）；
      - "cdp" 模式：context 由外部客户端管理，不应主动关闭。
    """
    from playwright.async_api import async_playwright

    if mode == "exe":
        exe = cfg.VB_CHROME_EXE
        if not exe or not os.path.exists(exe):
            logger.warning(f"[vbrowser] VB_CHROME_EXE 未配置或不存在: {exe}，回退原生 Playwright")
            return None, None, None, None
        logger.info(f"[vbrowser] 使用 fingerprint-chromium 内核(exe): {exe}")
        p = await async_playwright().start()
        browser = await p.chromium.launch(
            executable_path=exe,
            headless=headless,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context()
        return p, browser, context, "exe"

    # 默认 cdp 模式
    port = launch_vb_env(cfg.VB_ENV_ID, cfg.VB_API_BASE, cfg.VB_LAUNCH_TIMEOUT)
    if not port:
        logger.warning("[vbrowser] CDP 启动失败，回退原生 Playwright")
        return None, None, None, None
    p = await async_playwright().start()
    browser = await p.chromium.connect_over_cdp(f"http://localhost:{port}")
    context = browser.contexts[0] if browser.contexts else await browser.new_context()
    return p, browser, context, "cdp"


def launch_sync(mode, cfg, headless=False):
    """同步版启动指纹内核，返回 (playwright, browser, context, backend)。"""
    from playwright.sync_api import sync_playwright

    if mode == "exe":
        exe = cfg.VB_CHROME_EXE
        if not exe or not os.path.exists(exe):
            logger.warning(f"[vbrowser] VB_CHROME_EXE 未配置或不存在: {exe}，回退原生 Playwright")
            return None, None, None, None
        logger.info(f"[vbrowser] 使用 fingerprint-chromium 内核(exe): {exe}")
        p = sync_playwright().start()
        browser = p.chromium.launch(
            executable_path=exe,
            headless=headless,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context()
        return p, browser, context, "exe"

    port = launch_vb_env(cfg.VB_ENV_ID, cfg.VB_API_BASE, cfg.VB_LAUNCH_TIMEOUT)
    if not port:
        logger.warning("[vbrowser] CDP 启动失败，回退原生 Playwright")
        return None, None, None, None
    p = sync_playwright().start()
    browser = p.chromium.connect_over_cdp(f"http://localhost:{port}")
    context = browser.contexts[0] if browser.contexts else browser.new_context()
    return p, browser, context, "cdp"


def should_use_vb(cfg):
    """总开关 + 可用性探测：是否启用指纹内核。"""
    if not getattr(cfg, "USE_VIRTUAL_BROWSER", False):
        return False, None
    mode = getattr(cfg, "VB_MODE", "cdp")
    if mode == "exe":
        # exe 模式只需文件存在即可
        if not os.path.exists(getattr(cfg, "VB_CHROME_EXE", "") or ""):
            logger.warning("[vbrowser] VB_MODE=exe 但 VB_CHROME_EXE 不存在，回退原生 Playwright")
            return False, None
        return True, "exe"
    # cdp 模式需本地服务可达
    if is_vb_available(getattr(cfg, "VB_API_BASE", "http://localhost:9000")):
        return True, "cdp"
    logger.warning("[vbrowser] VB_MODE=cdp 但本地服务不可达，回退原生 Playwright")
    return False, None
