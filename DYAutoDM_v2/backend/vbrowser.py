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

# 指纹内核（Ungoogled Chromium）启动参数。
# 重要：桌面程序（双击 exe 运行，非 root/容器）【不需要也不该用 --no-sandbox】，
# 加它反而会触发内核“不受支持的命令行标记”警告并可能拖慢渲染进程稳定性。
# 以下参数在去除 --no-sandbox 的基础上，补齐指纹内核友好 + 冷启动提速的组合：
#   --disable-gpu               避免无头/独显切换导致的渲染进程反复重建
#   --disable-dev-shm-usage    用磁盘而非 /dev/shm，防共享内存不足引发的卡顿/崩溃
#   --no-first-run             跳过首次运行向导
#   --no-default-browser-check 不探测系统默认浏览器
#   --disable-background-networking / --disable-extensions / --disable-sync
#                               减少无关后台联网与扩展加载，加快首屏
# 注意：不要加 --disable-blink-features=AutomationControlled。
# 该参数是 Playwright 用来隐藏 navigator.webdriver 的，但 ungoogled-chromium
# 指纹内核在编译期已移除 webdriver 痕迹，此参数对我们是冗余的；而且它会触发
# 内核“不受支持的命令行标记”警告。自动化特征伪装交给指纹内核自身处理即可。
_CHROME_ARGS = [
    "--disable-gpu",
    "--disable-dev-shm-usage",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-background-networking",
    "--disable-extensions",
    "--disable-sync",
]


def app_root():
    """应用根目录（持久化数据基准）：

    - **最高优先级**：环境变量 `DY_APP_ROOT`。设置后直接返回该路径，
      用于**源码态指向隔离测试路径**（如 C:\\temp\\dyautodm_test），
      使账号 .env / data / vb_chromium / logs 全部落到隔离目录，
      不必再把测试库复制回源码仓库（违反隔离铁律且危险）。
      正常开发/打包均不设此变量，行为与之前完全一致。
    - 源码态：本文件位于 backend/vbrowser.py，向上两级（backend 的上一级）即项目根
      DYAutoDM_v2/，随附资源（vb_chromium / vb_profile_* / pw_profile_dm / .env / logs）
      都放在项目根下，accounts.py 等也以项目根为基准，保持一致；
    - PyInstaller 打包态（onefile）：优先 exe 所在目录；若 exe 旁没有 vb_chromium
      但存在 resources/vb_chromium（Tauri bundle.resources 分发位置），则返回
      exe 旁的 resources/ 子目录，让 NSIS/MSI 安装场景也能找到随附资源。
    """
    # 隔离测试专用 override（2026-09-01）：源码态把整套资源根切到隔离路径。
    # 不设时完全不影响既有行为。
    _ov = os.environ.get("DY_APP_ROOT", "").strip().strip('"')
    if _ov:
        try:
            if os.path.isdir(_ov):
                return os.path.abspath(_ov)
            logger.warning(f"[vbrowser] DY_APP_ROOT 指向的目录不存在，忽略: {_ov}")
        except Exception:
            pass
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        # 应用根解析：Tauri sidecar 常被放在 <root>/binaries/ 子目录（或类似 bin/），
        # 而随附资源（vb_chromium / vb_profile_* / .env / logs）在 <root> 下。
        # 若 exe 父目录名为 binaries/bin，则上溯一级作为应用根。
        parent = os.path.dirname(exe_dir)
        if os.path.basename(exe_dir).lower() in ("binaries", "bin"):
            root_candidates = [parent, exe_dir]
        else:
            root_candidates = [exe_dir, parent]
        for cand in root_candidates:
            # 直接有 vb_chromium（复制 exe / 解压资源场景）
            if os.path.isdir(os.path.join(cand, "vb_chromium")):
                return cand
        # fallback: Tauri resources/ 子目录（NSIS 安装场景）
        for cand in root_candidates:
            res_dir = os.path.join(cand, "resources")
            if os.path.isdir(os.path.join(res_dir, "vb_chromium")):
                return res_dir
        # 退化返回 exe 所在目录，让上层报明确的"找不到"错误
        return exe_dir
    # 本文件: <root>/backend/vbrowser.py -> 向上两级(backend 的上一级) = <root>
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _resolve_exe(rel_or_abs):
    """把 VB_CHROME_EXE 解析为绝对路径：绝对路径原样返回，相对路径按应用根目录解析。"""
    if not rel_or_abs:
        return rel_or_abs
    if os.path.isabs(rel_or_abs):
        return rel_or_abs
    return os.path.join(app_root(), rel_or_abs)


# 单 profile 优先级（铁律）：
#   每个账号只有【一个】固定持久化 profile（= accounts.profile_dir_of(env_path)
#   算出的 accounts/<name>/profile），守护/查看/重扫三类浏览器全部复用它。
#   谁需要独占浏览器（重扫 > 查看 > 守护保活），必须先停该账号凭证守护释放
#   Chromium profile 锁，再启动——绝不新建临时 profile（临时 profile 会被抖音
#   识别为新设备/新环境，直接触发风控，是绝对禁止的）。
#   因此：本模块【不再提供任何清空/临时目录逻辑】，user_data_dir 必须由调用方
#   显式传入固定 profile 目录；传 None 时 exe 模式也报错，强制调用方明确指定。


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

    单 profile 铁律（2026-08-17 修订）：
      - 每个账号只有【一个】固定持久化 profile 目录，由调用方显式传入
        accounts.profile_dir_of(env_path)（如 accounts/<name>/profile）。
      - 守护 / 查看 / 重扫三类浏览器全部复用同一目录，谁需独占（重扫 > 查看 > 守护保活）
        必须先停该账号凭证守护释放 Chromium profile 锁，再启动——绝不新建临时目录。
      - 临时 profile 会被抖音识别为新设备/新环境，直接触发风控，绝对禁止。
      - 故 user_data_dir=None 时不再兜底默认 vb_profile_dm，而是直接报错，
        强制调用方明确指定固定 profile 目录。
      - force=True 仅表示“不复用已有登录态、强制重新扫码”，仍使用同一固定 profile 目录，
        不清空、不新建临时目录（清空/临时化都是风控根因）。
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
        # 单 profile 铁律：禁止兜底默认目录，强制调用方显式传入固定 profile。
        if not user_data_dir:
            raise RuntimeError(
                "[vbrowser] 未指定固定 profile 目录（user_data_dir=None）。"
                "单 profile 铁律：禁止临时目录，必须由调用方传入 accounts.profile_dir_of(env_path)")
        if force:
            # 强制重扫：复用同一固定 profile、重新登录，不清空、不临时化。
            logger.info(f"[vbrowser] 强制重扫模式：复用固定 profile（不新建临时目录、不清空）: {user_data_dir}")
        else:
            logger.info(f"[vbrowser] 复用固定 profile: {user_data_dir}")
        p = await async_playwright().start()
        context = await p.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            executable_path=exe,
            headless=headless,
            args=_CHROME_ARGS,
            # Playwright 在 Windows headed 模式下会强制注入 --no-sandbox，
            # 触发指纹内核“不受支持的命令行标记”警告。显式剔除该默认参数。
            ignore_default_args=["--no-sandbox"],
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
    # 注意：connect_over_cdp 接管的是外部指纹客户端已启动的环境，
    # 其默认 context（含指纹伪装）必须由客户端创建，绝不能自己 new_context()
    # —— 否则会拿到一个无指纹特征的空白 context。客户端正常启动时必有 contexts[0]。
    if not browser.contexts:
        raise RuntimeError(
            f"[vbrowser] CDP 指纹环境无可用 context（客户端未创建默认浏览器上下文）。"
            f"请检查 VirtualBrowser/Ant-Browser 启动参数。")
    context = browser.contexts[0]
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
        # 单 profile 铁律：禁止兜底默认目录，强制调用方显式传入固定 profile。
        if not user_data_dir:
            raise RuntimeError(
                "[vbrowser] 未指定固定 profile 目录（user_data_dir=None）。"
                "单 profile 铁律：禁止临时目录，必须由调用方传入 accounts.profile_dir_of(env_path)")
        p = sync_playwright().start()
        context = p.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            executable_path=exe,
            headless=headless,
            args=_CHROME_ARGS,
            # Playwright 在 Windows headed 模式下会强制注入 --no-sandbox，
            # 触发指纹内核“不受支持的命令行标记”警告。显式剔除该默认参数。
            ignore_default_args=["--no-sandbox"],
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


async def open_douyin_home(profile_dir, headless=False, url="https://www.douyin.com/"):
    """单纯拉起指纹浏览器并打开指定页面（默认抖音主页）。

    与扫码登录（get_login_auth/force=True）是两条完全不同的路径：
      - 本函数【不扫码、不抓凭证、不写回 .env】，只是“打开浏览器看一眼”；
      - 复用各账号独占的 profile 目录，从而打开的是该账号已绑定的登录态（若扫码过）；
      - 浏览器前台常驻（headless=False），用户可手动操作，关闭窗口即结束。

    适用于“账号管理双击指纹浏览器”这类纯查看/手动操作场景。
    """
    import asyncio
    from playwright.async_api import async_playwright

    mode = getattr(_CFG, "VB_MODE", "exe")
    if mode == "exe":
        exe = _resolve_exe(getattr(_CFG, "VB_CHROME_EXE", "") or "")
        if not exe or not os.path.exists(exe):
            raise RuntimeError(
                f"[vbrowser] 指纹浏览器内核不存在: {exe}（已禁用原生 Playwright，不会回退）。"
                f"请确认 VB_CHROME_EXE 配置正确且 vb_chromium 随附在应用根目录。")
        logger.info(f"[vbrowser] 打开指纹浏览器(查看模式) 内核={exe} profile={profile_dir}")
        p = await async_playwright().start()
        context = await p.chromium.launch_persistent_context(
            user_data_dir=profile_dir,
            executable_path=exe,
            headless=headless,
            args=_CHROME_ARGS,
            # Playwright 在 Windows headed 模式下会强制注入 --no-sandbox，
            # 触发指纹内核“不受支持的命令行标记”警告。显式剔除该默认参数。
            ignore_default_args=["--no-sandbox"],
        )
    else:
        port = launch_vb_env(_CFG.VB_ENV_ID, _CFG.VB_API_BASE, _CFG.VB_LAUNCH_TIMEOUT)
        if not port:
            raise RuntimeError(
                f"[vbrowser] 指纹浏览器 CDP 服务不可达: {_CFG.VB_API_BASE}（已禁用原生 Playwright，不会回退）。"
                f"请先启动 VirtualBrowser/Ant-Browser 本地服务。")
        p = await async_playwright().start()
        browser = await p.chromium.connect_over_cdp(f"http://localhost:{port}")
        context = browser.contexts[0] if browser.contexts else await browser.new_context()

    page = context.pages[0] if context.pages else await context.new_page()
    await page.goto(url, wait_until="domcontentloaded")
    logger.info(f"[vbrowser] 已打开页面: {url}（用户可手动操作，关闭窗口即结束）")
    # 前台常驻：阻塞直到浏览器上下文关闭（用户手动关窗），避免进程被回收
    try:
        while True:
            if not context.pages:
                break
            await asyncio.sleep(1)
    except Exception:
        pass
    finally:
        try:
            await context.close()
        except Exception:
            pass
        try:
            await p.stop()
        except Exception:
            pass
    return True


# 全局 VB 配置对象（供 open_douyin_home 等轻量入口读取 VB_MODE/EXE 等，
# 无需每次传入）。运行期由 init_vb_config() 注入。
_CFG = None


def init_vb_config(cfg):
    """注入 VB 配置对象（含 USE_VIRTUAL_BROWSER/VB_MODE/VB_CHROME_EXE 等属性）。

    供 open_douyin_home 等不接收 cfg 参数的轻量入口复用，避免 import 循环。
    """
    global _CFG
    _CFG = cfg


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
