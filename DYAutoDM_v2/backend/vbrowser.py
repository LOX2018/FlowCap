# coding=utf-8
"""指纹浏览器后端接入层（唯一浏览器来源，禁止回退原生 Playwright）。

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

【硬性约束】调用时只用指纹浏览器，禁止使用原生 Playwright：
  - USE_VIRTUAL_BROWSER 必须为 True（项目默认已开启）；
  - 指纹内核不可用（exe 内核文件缺失 / cdp 服务不可达）时，should_use_vb / launch_*
    直接抛 RuntimeError 让上层报错，绝不静默回退原生 Playwright。

注意：指纹伪装由对应内核自身完成，本模块只负责“启动内核 + 把 page/context 交给调用方”。
"""

import os
import shutil
import sys
import time

import requests
from loguru import logger


def app_root():
    """应用根目录（持久化数据基准）：

    - 源码态：本文件位于 backend/vbrowser.py，向上两级（backend 的上一级）即项目根
      DYAutoDM_v2/，随附资源（vb_chromium / vb_profile_* / pw_profile_dm / .env / logs）
      都放在项目根下，accounts.py 等也以项目根为基准，保持一致；
    - PyInstaller 打包态（onefile）：exe 所在目录。build_sidecar.py 的 _add_data 会把
      vb_chromium / vb_profile_* / pw_profile_dm / .env / logs 等运行时资源
      拷贝到 exe 旁边，因此打包后所有相对路径都必须以【exe 所在目录】为基准，
      否则会按 __file__ 解析到 _MEIxxx 临时解压目录导致资源找不到、回退原生 Playwright 而崩溃。
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    # 本文件: <root>/backend/vbrowser.py -> 向上两级(backend 的上一级) = <root>
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _resolve_exe(rel_or_abs):
    """把 VB_CHROME_EXE 解析为绝对路径：绝对路径原样返回，相对路径按应用根目录解析。"""
    if not rel_or_abs:
        return rel_or_abs
    if os.path.isabs(rel_or_abs):
        return rel_or_abs
    return os.path.join(app_root(), rel_or_abs)


def _wipe_user_data_dir(user_data_dir):
    """强制重扫（方案B）：删除整个 profile 目录，等同全新浏览器。

    - 先递归重置只读属性（Windows 下 Chromium 缓存/偏好文件常为只读，否则 shutil 删除失败）；
    - 目录不存在则忽略（首次扫码本就无目录）；
    - 删除失败仅告警，不阻断启动（浏览器会自行创建空目录）。
    """
    if not user_data_dir:
        return
    if not os.path.exists(user_data_dir):
        logger.info(f"[vbrowser] profile 目录不存在，视为全新: {user_data_dir}")
        return
    try:
        for root, dirs, files in os.walk(user_data_dir):
            for name in dirs + files:
                p = os.path.join(root, name)
                try:
                    os.chmod(p, 0o755)
                except OSError:
                    pass
        shutil.rmtree(user_data_dir)
        logger.info(f"[vbrowser] 已删除旧 profile 目录: {user_data_dir}")
    except Exception as e:
        logger.warning(f"[vbrowser] 清空 profile 目录失败（将仍尝试启动）: {e}")


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

async def launch_async(mode, cfg, headless=False, user_data_dir=None, force=False):
    """异步启动指纹内核，返回 (playwright, browser, context, backend)。

    backend 用于调用方决定收尾时是否关闭 context：
      - "exe" 模式：context 由我们 launch 出来，结束时需关闭（与原生 Playwright 一致）；
      - "cdp" 模式：context 由外部客户端管理，不应主动关闭。

    user_data_dir：exe 模式使用持久化 profile（launch_persistent_context）。
      - force=True（强制重新扫码）：使用【临时目录】作为 profile，确保浏览器一定是“未登录”全新态，
        必然弹出二维码等待用户真实扫码；避免因持久化 profile 残留旧 sessionid 而“跳过扫码、误捕获旧凭证”
        （这正是“还没扫码就登录了”的根因）。扫完即弃，不留旧登录态。
      - force=False：用 user_data_dir（默认 vb_profile_dm），可复用已有登录态免扫码。
    """
    from playwright.async_api import async_playwright

    if mode == "exe":
        exe = _resolve_exe(getattr(cfg, "VB_CHROME_EXE", "") or "")
        if not exe or not os.path.exists(exe):
            # 禁止回退原生 Playwright：内核缺失是配置错误，直接抛错让上层明确感知
            raise RuntimeError(
                f"[vbrowser] 指纹浏览器内核不存在: {exe}（已禁用原生 Playwright，不会回退）。"
                f"请确认 VB_CHROME_EXE 配置正确且 vb_chromium 随附在应用根目录。")
        logger.info(f"[vbrowser] 使用 fingerprint-chromium 内核(exe): {exe}")
        if user_data_dir is None:
            user_data_dir = os.path.join(app_root(), "vb_profile_dm")
        if force:
            # 方案B：强制重扫时，直接清空该账号的 profile 整个目录（等同全新浏览器），
            # 再用原目录启动——绕过一切持久化登录态（残留 sessionid 等），确保必须真实扫码；
            # 扫完的真实登录态会写回原目录（区别于临时目录，不会扫完即弃）。
            _wipe_user_data_dir(user_data_dir)
            logger.info(f"[vbrowser] 强制重扫模式：已清空原 profile 目录（等同全新浏览器）: {user_data_dir}")
        p = await async_playwright().start()
        context = await p.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            executable_path=exe,
            headless=headless,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        browser = context.browser
        return p, browser, context, "exe"

    # 默认 cdp 模式
    port = launch_vb_env(cfg.VB_ENV_ID, cfg.VB_API_BASE, cfg.VB_LAUNCH_TIMEOUT)
    if not port:
        # 禁止回退原生 Playwright：CDP 指纹服务不可达是配置错误，直接抛错
        raise RuntimeError(
            f"[vbrowser] 指纹浏览器 CDP 服务不可达: {cfg.VB_API_BASE}（已禁用原生 Playwright，不会回退）。"
            f"请先启动 VirtualBrowser/Ant-Browser 本地服务并核对 VB_API_BASE/VB_ENV_ID。")
    p = await async_playwright().start()
    browser = await p.chromium.connect_over_cdp(f"http://localhost:{port}")
    context = browser.contexts[0] if browser.contexts else await browser.new_context()
    return p, browser, context, "cdp"


def launch_sync(mode, cfg, headless=False, user_data_dir=None):
    """同步版启动指纹内核，返回 (playwright, browser, context, backend)。"""
    from playwright.sync_api import sync_playwright

    if mode == "exe":
        exe = _resolve_exe(getattr(cfg, "VB_CHROME_EXE", "") or "")
        if not exe or not os.path.exists(exe):
            # 禁止回退原生 Playwright：内核缺失是配置错误，直接抛错让上层明确感知
            raise RuntimeError(
                f"[vbrowser] 指纹浏览器内核不存在: {exe}（已禁用原生 Playwright，不会回退）。"
                f"请确认 VB_CHROME_EXE 配置正确且 vb_chromium 随附在应用根目录。")
        logger.info(f"[vbrowser] 使用 fingerprint-chromium 内核(exe): {exe}")
        if user_data_dir is None:
            user_data_dir = os.path.join(app_root(), "vb_profile_dm")
        p = sync_playwright().start()
        context = p.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            executable_path=exe,
            headless=headless,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        browser = context.browser
        return p, browser, context, "exe"

    port = launch_vb_env(cfg.VB_ENV_ID, cfg.VB_API_BASE, cfg.VB_LAUNCH_TIMEOUT)
    if not port:
        # 禁止回退原生 Playwright：CDP 指纹服务不可达是配置错误，直接抛错
        raise RuntimeError(
            f"[vbrowser] 指纹浏览器 CDP 服务不可达: {cfg.VB_API_BASE}（已禁用原生 Playwright，不会回退）。"
            f"请先启动 VirtualBrowser/Ant-Browser 本地服务并核对 VB_API_BASE/VB_ENV_ID。")
    p = sync_playwright().start()
    browser = p.chromium.connect_over_cdp(f"http://localhost:{port}")
    context = browser.contexts[0] if browser.contexts else browser.new_context()
    return p, browser, context, "cdp"


def should_use_vb(cfg):
    """总开关 + 可用性探测。

    禁止回退原生 Playwright：指纹浏览器不可用时直接抛 RuntimeError（返回 False 仅用于
    上游无条件跳过——实际所有调用点都应把不可用当作错误上报，而不是降级到原生内核）。
    正常返回 (True, mode)（mode 为 "exe" / "cdp"）。
    """
    if not getattr(cfg, "USE_VIRTUAL_BROWSER", False):
        raise RuntimeError(
            "[vbrowser] 已禁用原生 Playwright：USE_VIRTUAL_BROWSER 必须为 True（指纹浏览器强制启用）。"
            "请检查 config.py / 前端配置，不要关闭指纹浏览器开关。")
    mode = getattr(cfg, "VB_MODE", "cdp")
    if mode == "exe":
        # exe 模式只需文件存在即可（相对路径按应用根解析，与启动目录无关）
        exe = _resolve_exe(getattr(cfg, "VB_CHROME_EXE", "") or "")
        if not os.path.exists(exe):
            raise RuntimeError(
                f"[vbrowser] 指纹浏览器内核不存在: {exe}（已禁用原生 Playwright，不会回退）。"
                f"请确认 VB_CHROME_EXE 配置正确且 vb_chromium 随附在应用根目录。")
        return True, "exe"
    # cdp 模式需本地服务可达
    api = getattr(cfg, "VB_API_BASE", "http://localhost:9000")
    if is_vb_available(api):
        return True, "cdp"
    raise RuntimeError(
        f"[vbrowser] 指纹浏览器 CDP 服务不可达: {api}（已禁用原生 Playwright，不会回退）。"
        f"请先启动 VirtualBrowser/Ant-Browser 本地服务并核对 VB_API_BASE/VB_ENV_ID。")
