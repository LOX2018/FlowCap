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
# 2026-09-09 安全边界（用户明确要求）：【禁止任何真实外接采集设备】
#   本项目任何环节、任何模块都不得使用真实麦克风 / 摄像头。
#   键盘、鼠标、网卡等输入/网络设备不受影响。
#
# 实现方式：在启动参数层强制注入 Chromium 虚拟设备 + 自动授权：
#   --use-fake-device-for-media-stream  用虚拟采集源替代真实麦克风/摄像头
#   --use-fake-ui-for-media-stream      自动同意权限请求（不弹系统授权框）
#   --allow-file-access-from-files      配合虚拟设备的本地回环
#   --mute-audio                        不占用/不输出真实音频设备
#   --deny-permission-prompts           不弹权限框（配合上面的自动授权）
# 这四条保证即使页面调用 getUserMedia，拿到的也是 Fake Audio/Video Input，
# 绝不会触碰物理设备。
_FAKE_MEDIA_ARGS = [
    "--use-fake-device-for-media-stream",
    "--allow-file-access-from-files",
    "--mute-audio",
    "--deny-permission-prompts",
]

_CHROME_ARGS = [
    "--disable-gpu",
    "--disable-dev-shm-usage",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-background-networking",
    "--disable-extensions",
    "--disable-sync",
] + _FAKE_MEDIA_ARGS

# 2026-09-09 变更（用户要求）：【废弃】「真有头 + 窗口移出屏幕」伪装模式。
#
# 原设计（2026-09-06）：BCC「无头」改用真有头窗口移到屏幕外（-32000,-32000），
#   理由是抖音风控能识别 Chromium headless，需保持有头特征。
#
# 废弃原因：
#   1) 副作用严重 —— 持久化 profile 会把屏外坐标写进 Preferences，导致后续
#      可见启动（扫码登录 / 查看模式）窗口恢复在桌面外，用户无法扫码。
#      为兜底不得不额外维护 _ensure_window_visible() 归位逻辑。
#   2) 实测证伪必要性 —— 2026-09-08 双账号实测：纯 native 无头下
#      小助理 ws 发送成功、张老师 wp 发送成功，无头完全可用（见下 native 分支注释）。
#   3) 用户明确要求删除全部移屏外设定。
#
# 现在 headless 一律走纯 Playwright headless（native），不再有任何屏外窗口。
# _HEADLESS_DISGUISE_ARGS 保留为空列表仅为兼容旧引用，调用方加它是无操作。
_HEADLESS_DISGUISE_ARGS: list = []

# 窗口归位判定（2026-09-06 二次修正，实机数据）：
# 伪装模式写 --window-position=-32000，但 Chromium 会把窗口钳制到虚拟桌面
# 边界，实测 profile 里残留的 window_placement.left = -26214（四川工伤张老师
# 实测）/ -1268（尚进工伤小助理，窗口主体在屏外仅边角在屏内）。旧阈值
# 30000 > 26214，把屏外遗留误判成「多显示器正常负坐标」直接跳过归位
# → 可见启动窗口仍落在桌面外。
# 新判据 = EnumDisplayMonitors 拿真实全部显示器工作区 +「与任一工作区
# 可见相交 ≥200px」。跨屏窗口只要主体落在任何一块真实屏幕上都放行；
# -26214 / -1268 两类伪装遗留与所有真实屏幕相交均 <200px，正确归位。
import ctypes as _ctypes
from ctypes import wintypes as _wintypes


def _enum_workareas():
    """枚举全部显示器工作区。失败退回主屏假设 (0,0)-(1536,864)。"""
    try:
        user32 = _ctypes.windll.user32
        out = []

        def _cb(hMonitor, hdc, lprc, lParam):
            rc = lprc.contents
            out.append({"left": rc.left, "top": rc.top,
                        "right": rc.right, "bottom": rc.bottom})
            return True

        proc = _ctypes.WINFUNCTYPE(
            _ctypes.c_int, _ctypes.c_void_p, _ctypes.c_void_p,
            _ctypes.POINTER(_wintypes.RECT), _ctypes.c_double)
        if user32.EnumDisplayMonitors(0, 0, proc(_cb), 0) and out:
            return out
    except Exception:
        pass
    return [{"left": 0, "top": 0, "right": 1536, "bottom": 864}]


_VISIBLE_MIN_OVERLAP = 200  # 窗口须至少 200px 落在某块真实屏幕工作区内


def _window_visible_on_any_monitor(left, top, width, height, workareas=None):
    """窗口是否与任一真实显示器工作区可见相交（≥200px 双向）。"""
    was = workareas if workareas is not None else _enum_workareas()
    for wa in was:
        ox = min(left + width, wa["right"]) - max(left, wa["left"])
        oy = min(top + height, wa["bottom"]) - max(top, wa["top"])
        if ox >= _VISIBLE_MIN_OVERLAP and oy >= _VISIBLE_MIN_OVERLAP:
            return True
    return False


async def _ensure_window_visible(context):
    """把屏外遗留窗口（伪装模式 -32000/-26214 写进 profile）拉回屏幕内。

    2026-09-06 二次修正：改用「与任一真实显示器工作区可见相交」判据
    （_window_visible_on_any_monitor），旧版仅比较 left/top 是否超过
    30000，被 Chromium 钳制后的 -26214 漏判。maximized/minimized 状态
    需先置 normal 才能改坐标。失败只告警不阻塞。
    """
    try:
        pages = context.pages
        if not pages:
            return
        session = await context.new_cdp_session(pages[0])
        try:
            info = await session.send("Browser.getWindowForTarget")
            wid = info.get("windowId")
            b = info.get("bounds") or {}
            left, top = int(b.get("left", 0)), int(b.get("top", 0))
            width = int(b.get("width", 1440))
            height = int(b.get("height", 900))
            state = b.get("windowState", "normal")
            if _window_visible_on_any_monitor(left, top, width, height):
                return  # 窗口主体可见（含多显示器任意屏），不动
            bounds = {"left": 80, "top": 80, "width": max(width, 1000),
                      "height": max(height, 700)}
            if state in ("minimized", "maximized", "fullscreen"):
                bounds["windowState"] = "normal"
            await session.send("Browser.setWindowBounds", {"windowId": wid, "bounds": bounds})
            logger.info(
                f"[vbrowser] 窗口从屏外/不可见位置 (left={left}, top={top}, "
                f"state={state}) 归位到 (80,80)（伪装模式 profile 残留，保证扫码/查看可见）")
        finally:
            try:
                await session.detach()
            except Exception:
                pass
    except Exception as e:
        logger.warning("BCC-035", f"[vbrowser] 窗口归位检查失败（不阻塞启动）: {e}")


def _ensure_window_visible_sync(context):
    """_ensure_window_visible 的同步版（launch_sync 用）。"""
    try:
        pages = context.pages
        if not pages:
            return
        session = context.new_cdp_session(pages[0])
        try:
            info = session.send("Browser.getWindowForTarget")
            wid = info.get("windowId")
            b = info.get("bounds") or {}
            left, top = int(b.get("left", 0)), int(b.get("top", 0))
            width = int(b.get("width", 1440))
            height = int(b.get("height", 900))
            state = b.get("windowState", "normal")
            if _window_visible_on_any_monitor(left, top, width, height):
                return
            bounds = {"left": 80, "top": 80, "width": max(width, 1000),
                      "height": max(height, 700)}
            if state in ("minimized", "maximized", "fullscreen"):
                bounds["windowState"] = "normal"
            session.send("Browser.setWindowBounds", {"windowId": wid, "bounds": bounds})
            logger.info(
                f"[vbrowser] 窗口从屏外/不可见位置 (left={left}, top={top}, "
                f"state={state}) 归位到 (80,80)（伪装模式 profile 残留，保证扫码/查看可见）")
        finally:
            try:
                session.detach()
            except Exception:
                pass
    except Exception as e:
        logger.warning("BCC-036", f"[vbrowser] 窗口归位检查失败（不阻塞启动）: {e}")

# ---------- 代理支持（2026-09-06 借鉴 OpenBrowser per-env proxy 设计）----------
#
# 每账号可在自己的 .env 里配置 DY_PROXY（如 DY_PROXY=http://user:pass@host:port），
# vbrowser 启动该账号浏览器时自动注入 --proxy-server，并按需启用 WebRTC 防泄漏。
# 未配置 DY_PROXY 时行为与之前完全一致（直连本机出口）。
#
# 配置格式（urlparse 解析）：
#   http://host:port                          HTTP 代理
#   http://user:pass@host:port                HTTP 代理 + 认证（Playwright 自动处理凭据弹窗）
#   socks5://host:port                        SOCKS5 代理（不支持用户名密码认证，Chromium 限制）
#
# WebRTC 防泄漏（--enforce-webrtc-ip-handling-policy + --force-webrtc-ip-handling-policy）：
#   有代理时默认关闭 WebRTC 的非代理 UDP（default_public_interface_only 级策略），
#   防止 STUN 探测经本机网卡暴露真实 IP（即使流量已走代理）。未配代理时不注入。

_PROXY_BUILTIN_BYPASS = "localhost;127.0.0.1;<local>"



async def _minimize_window(context):
    """异步版：最小化 BCC 窗口到任务栏。"""
    try:
        pages = context.pages
        if not pages:
            return
        session = await context.new_cdp_session(pages[0])
        try:
            info = await session.send("Browser.getWindowForTarget")
            wid = info.get("windowId")
            await session.send("Browser.setWindowBounds",
                               {"windowId": wid, "bounds": {"windowState": "minimized"}})
            logger.info("[vbrowser] 窗口已最小化到任务栏（有头特征保留，风控对齐）")
        finally:
            try: await session.detach()
            except Exception: pass
    except Exception as e:
        logger.warning("BCC-035", f"[vbrowser] 窗口最小化失败（不阻塞启动）: {e}")


def _minimize_window_sync(context):
    """同步版：最小化 BCC 窗口到任务栏（launch_sync 用）。"""
    try:
        pages = context.pages
        if not pages:
            return
        session = context.new_cdp_session(pages[0])
        try:
            info = session.send("Browser.getWindowForTarget")
            wid = info.get("windowId")
            session.send("Browser.setWindowBounds",
                         {"windowId": wid, "bounds": {"windowState": "minimized"}})
            logger.info("[vbrowser] 窗口已最小化到任务栏（有头特征保留，风控对齐）")
        finally:
            try: session.detach()
            except Exception: pass
    except Exception as e:
        logger.warning("BCC-035", f"[vbrowser] 窗口最小化失败（不阻塞启动）: {e}")


def parse_proxy_env(env_path):
    """读账号 .env 的 DY_PROXY 值，校验格式，返回 (proxy_url or None, err or None)。

    铁律：只读这一行，绝不解析/回传 .env 里其他凭证字段（DY_COOKIES 等）。
    格式非法时返回 (None, 原因)——调用方记 warning 后按无代理继续（不阻断启动），
    与"代理配置错误不应导致账号完全不可用"的容错策略一致。
    """
    try:
        if not env_path:
            return None, None  # 无 .env 不算错（静默无代理）
        # 会员体系（v0.37.0）：会员空间内 .env 加密存 <path>.enc，
        # 经 member_ctx.parse_env_dict 解密读；外部路径沿用逐行读。
        from urllib.parse import urlparse
        val = None
        _dec = None
        try:
            from services import member_ctx
            if member_ctx.is_member_env(env_path):
                _dec = member_ctx.parse_env_dict(env_path)
        except Exception:
            _dec = None
        if _dec is not None:
            val = (_dec.get("DY_PROXY") or "").strip().strip('"').strip("'") or None
        else:
            if not os.path.isfile(env_path):
                return None, None
            with open(env_path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("DY_PROXY="):
                        val = line.split("=", 1)[1].strip().strip('"').strip("'")
                        break
        if not val:
            return None, None
        u = urlparse(val)
        if u.scheme not in ("http", "https", "socks5", "socks4") or not u.hostname or not u.port:
            return None, f"DY_PROXY 格式非法: {val}（应为 http[s]://[user:pass@]host:port 或 socks5://host:port）"
        return val, None
    except Exception as e:
        return None, f"DY_PROXY 读取失败: {e}"


def _proxy_launch_args(proxy_url):
    """WebRTC 防泄漏启动参数（--proxy-server 本身由 Playwright proxy= 参数注入，勿重复）。"""
    return [
        # WebRTC 防泄漏：限制非代理 UDP，防 STUN 经本机网卡暴露真实 IP
        "--enforce-webrtc-ip-handling-policy",
        "--force-webrtc-ip-handling-policy=default_public_interface_only",
    ]


def _playwright_proxy_param(proxy_url):
    """把 DY_PROXY URL 转成 Playwright launch(proxy=) 参数（自动处理 407 认证）。

    返回 dict 或 None。SOCKS 代理不支持用户名密码认证（Chromium 限制），凭据被忽略。
    """
    from urllib.parse import urlparse
    if not proxy_url:
        return None
    u = urlparse(proxy_url)
    scheme = u.scheme or "http"
    server = f"{scheme}://{u.hostname}:{u.port}"
    d = {"server": server, "bypass": "localhost,127.0.0.1"}
    if scheme.startswith("http") and u.username:
        d["username"] = u.username
        d["password"] = u.password or ""
    return d


def _proxy_credentials(proxy_url):
    """从代理 URL 提取 (username, password)；无认证返回 None。仅 http(s) 代理支持。"""
    from urllib.parse import urlparse
    u = urlparse(proxy_url)
    if u.username:
        return u.username, u.password or ""
    return None


# 出口 IP 校验接口（借鉴 OpenBrowser egress check）。依次尝试，谁先返回 200 用谁。
# ipify 稳定无风控；抖音自回显仅作兜底（走的是同一浏览器网络栈，失败不影响主链路）。
_EGRESS_IP_ENDPOINTS = [
    ("https://api.ipify.org?format=json", lambda d: (d or {}).get("ip")),
    ("https://httpbin.org/ip", lambda d: (d or {}).get("origin")),
]


async def check_egress_ip(context, proxy_url, timeout_ms=15000):
    """在已启动的指纹浏览器 context 里开临时页校验出口 IP（借鉴 OpenBrowser egress check）。

    返回 {"ok": bool, "egress_ip": str|None, "expected_ip": str|None, "reason": str}。
    仅校验并记日志，绝不抛异常阻断启动——代理检测失败不应让账号不可用。
    预期 IP 解析：代理 URL 的 hostname 本身是 IP 时直接比对；是域名时不做 DNS 解析
    （backend 无代理解析能力，解析结果不可信），只记录"域名型代理"跳过精确比对。
    """
    import re as _re
    from urllib.parse import urlparse

    out = {"ok": False, "egress_ip": None, "expected_ip": None, "reason": ""}
    try:
        expected = None
        host = urlparse(proxy_url).hostname or ""
        if _re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", host):
            expected = host
        out["expected_ip"] = expected

        page = await context.new_page()
        ip = None
        try:
            for url, extractor in _EGRESS_IP_ENDPOINTS:
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                    data = await page.evaluate("() => { try { return JSON.parse(document.body.innerText) } catch { return null } }")
                    ip = extractor(data)
                    if ip:
                        break
                except Exception:
                    continue
        finally:
            try:
                await page.close()
            except Exception:
                pass

        out["egress_ip"] = ip
        if not ip:
            out["reason"] = "出口 IP 探测接口全部超时/失败（不影响浏览器使用）"
        elif expected and ip != expected:
            out["reason"] = f"出口 IP({ip}) 与代理 IP({expected}) 不一致——代理可能未生效或为转发型代理"
        elif expected:
            out["ok"] = True
            out["reason"] = "出口 IP 与代理 IP 一致"
        else:
            # 域名型代理：无法在本地比对，探到出口 IP 即视为代理生效（直连时该接口也可达，此项仅供参考）
            out["ok"] = True
            out["reason"] = f"域名型代理，出口 IP={ip}（无法精确比对，仅供参考）"
    except Exception as e:
        out["reason"] = f"出口 IP 校验异常: {e}"
    return out


def _env_path_of_account(account):
    """由账号名找 .env 路径（避免 vbrowser 反向 import accounts 造成循环导入）。"""
    try:
        root = app_root()
        cand = os.path.join(root, "auto_dm", "accounts", account, ".env")
        if os.path.isfile(cand):
            return cand
    except Exception:
        pass
    return None


def _launch_args_with_proxy(cfg, account=None):
    """合并 _CHROME_ARGS + 账号级 WebRTC 防泄漏参数，并解析代理 URL。

    返回 (args, proxy_url, pw_proxy)：
      args     —— 传给 launch_persistent_context 的 args；
      proxy_url —— 账号 DY_PROXY 原始值（None=无代理）；
      pw_proxy —— Playwright proxy= 参数 dict（None=无代理）。
    account 传入时读该账号 .env 的 DY_PROXY；未传时看全局 cfg.DY_PROXY 兜底。
    """
    args = list(_CHROME_ARGS)
    proxy_url = None
    if account:
        env_path = _env_path_of_account(account)
        if env_path:
            proxy_url, err = parse_proxy_env(env_path)
            if err:
                logger.warning("BCC-037", f"[vbrowser] 账号 {account} {err}（按无代理继续）")
    else:
        # 兼容：调用方未传 account 时看全局 cfg.DY_PROXY（可全局兜底配置）
        proxy_url = (getattr(cfg, "DY_PROXY", "") or "").strip() or None
    # 2026-09-06 全局治理（C：系统代理死端口防护）：
    # Windows 注册表系统代理（v2rayN 等写入的 ProxyEnable=1）会被 Chromium
    # 自动跟随。若该端口已死（代理核心没跑），浏览器所有请求连接拒绝——
    # BCC「快闪重启循环」的诱因之一。账号没配 DY_PROXY 且系统代理端口
    # 探测不通时，对该进程加 --no-proxy-server 直连（不动系统注册表，
    # 不影响其它软件；系统代理活着则尊重，不干预）。
    if not proxy_url:
        # 2026-09-12 根治（用户确认：账号代理必须按 IP 隔离生效）：
        # 账号没配 DY_PROXY 时，【无论系统代理死活】都强制 --no-proxy-server 直连。
        # 否则指纹浏览器会默认跟随系统代理（ProxyEnable=1 → 如 satelite 的
        # 10808 国外节点），抖音检测到代理特征+出口IP与账号环境不符 → 弹
        # 「安全风险阻止访问」；且国内账号误走国外节点违反 IP 隔离要求。
        # 配了 DY_PROXY 的账号走下面的账号代理分支（国内/国外按需）。
        args.append("--no-proxy-server")
        logger.info(
            "[vbrowser] 账号未配 DY_PROXY，强制 --no-proxy-server 直连"
            "（防误走系统代理/satelite，避免代理特征泄露）")
    pw_proxy = None
    if proxy_url:
        args += _proxy_launch_args(proxy_url)
        pw_proxy = _playwright_proxy_param(proxy_url)
        logger.info(f"[vbrowser] 已启用账号代理: {_mask_proxy(proxy_url)}")
    return args, proxy_url, pw_proxy


def _dead_system_proxy_arg():
    """检测 Windows 系统代理是否指向死端口。死 → 返回 '--no-proxy-server'，否则 None。

    2026-09-06 全局治理（C）：只读注册表 + TCP 探测，绝不动系统设置。
    - ProxyEnable=0 / 非本机代理 / 端口活着 → None（尊重现状）
    - 127.0.0.1 代理端口连不通（代理核心未运行）→ '--no-proxy-server'
    非 Windows 或读取失败一律返回 None（不干预）。
    """
    import sys as _sys
    if _sys.platform != "win32":
        return None
    try:
        import winreg  # noqa: S404 仅读
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings")
        enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
        if not enabled:
            return None
        server, _ = winreg.QueryValueEx(key, "ProxyServer")
        winreg.CloseKey(key)
        # ProxyServer 形如 "127.0.0.1:10808" 或 "http=...;https=..."（取整体/常见形态）
        host_port = server.split(";")[0].strip()
        if "=" in host_port:  # 分协议形态 "http=127.0.0.1:10808"
            host_port = host_port.split("=", 1)[1].strip()
        host, _, port_s = host_port.rpartition(":")
        if not host or not port_s.isdigit():
            return None
        if host not in ("127.0.0.1", "localhost"):
            return None  # 远程代理无法本机判定，尊重现状
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            if s.connect_ex((host, int(port_s))) == 0:
                return None  # 端口活着，系统代理有效
        return "--no-proxy-server"
    except Exception:
        return None


def _mask_proxy(proxy_url):
    """日志用代理地址脱敏：隐藏用户名密码。"""
    from urllib.parse import urlparse, urlunparse
    try:
        u = urlparse(proxy_url)
        if u.username or u.password:
            netloc = f"***:***@{u.hostname}:{u.port}"
            return urlunparse((u.scheme, netloc, u.path, u.params, u.query, u.fragment))
        return proxy_url
    except Exception:
        return "***"


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
            logger.warning("BCC-039", f"[vbrowser] DY_APP_ROOT 指向的目录不存在，忽略: {_ov}")
        except Exception:
            pass
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        # 应用根解析：Tauri sidecar 常被放在 <root>/binaries/ 子目录（或类似 bin/），
        # 而随附资源（vb_chromium / vb_profile_* / .env / logs）在 <root> 下。
        # 若 exe 父目录名为 binaries/bin，则上溯一级作为应用根。
        # 2026-09-08 onedir：exe 在 <root>/binaries/<full>/ 下（目录名含 triple，
        # 非 binaries/bin）→ 须上溯两级；先探测父目录是否 binaries/bin。
        parent = os.path.dirname(exe_dir)
        if os.path.basename(exe_dir).lower() in ("binaries", "bin"):
            root_candidates = [parent, exe_dir]
        elif os.path.basename(parent).lower() in ("binaries", "bin"):
            # onedir：exe_dir=<root>/binaries/<full>/，parent=<root>/binaries/
            root_candidates = [os.path.dirname(parent), exe_dir, parent]
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
        # 再退：上溯到 binaries/bin 的上一级（onydri/onefile 通用，无 vb_chromium 时）
        if os.path.basename(parent).lower() in ("binaries", "bin"):
            return os.path.dirname(parent)
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
        logger.warning("BCC-040", f"[vbrowser] 调用启动 API 失败（服务未启动？）: {e}")
        return None
    if not data.get("success"):
        logger.warning("BCC-041", f"[vbrowser] 启动环境失败: {data}")
        return None
    port = (data.get("data") or {}).get("debuggingPort")
    if not port:
        logger.warning("BCC-042", f"[vbrowser] 响应缺少 debuggingPort: {data}")
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

async def launch_async(mode, cfg, headless=False, user_data_dir=None, force=False, account=None):
    """异步启动指纹内核，返回 (playwright, browser, context, backend)。

    backend 用于调用方决定收尾时是否关闭 context：
      - "exe" 模式：context 由我们 launch 出来，结束时需关闭（与原生 Playwright 一致）；
      - "cdp" 模式：context 由外部客户端管理，不应主动关闭。

    account 传入账号名时，读该账号 .env 的 DY_PROXY 注入代理 + WebRTC 防泄漏
    （借鉴 OpenBrowser per-env proxy）；未配置则与原行为完全一致。

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
        launch_args, _proxy_url, pw_proxy = _launch_args_with_proxy(cfg, account=account)
        # 2026-09-13 风控根治（§24.10 复发实证）：无头请求一律转为「真有头+窗口最小化」。
        #   - 有头特征与扫码/查看模式完全一致（同 profile 同环境，杜绝环境跳变触发
        #     step-up 降级 → 登录态被强制下线）；
        #   - 最小化而非移屏外（-32000 屏外坐标会写进 profile 污染后续可见启动，
        #     09-09 已废弃）；
        #   - 最小化不改变 JS 可检测特征（screen/window 尺寸正常），风控视角=有头。
        _disguise = False
        _minimize = False
        if headless:
            headless = False
            _minimize = True
            logger.info("[vbrowser] 无头请求已转为 真有头+窗口最小化（风控对齐："
                        "同 profile 同环境，杜绝环境跳变）")
        p = await async_playwright().start()
        context = await p.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            executable_path=exe,
            headless=headless,  # disguise 模式已置 False；native 保持 True
            args=launch_args,
            proxy=pw_proxy,
            # Playwright 在 Windows headed 模式下会强制注入 --no-sandbox，
            # 触发指纹内核“不受支持的命令行标记”警告。显式剔除该默认参数。
            ignore_default_args=["--no-sandbox"],
        )
        # 2026-09-06：真可见启动（扫码/登录等）必须把窗口归位屏幕内
        # ——持久化 profile 可能残留伪装模式的 -32000 屏外位置，二维码会落在桌面外。
        # 伪装启动（_disguise=True）恰恰要留在屏外，绝不能归位。
        if not headless and not _disguise:
            await _ensure_window_visible(context)
        if _minimize:
            await _minimize_window(context)
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


def launch_sync(mode, cfg, headless=False, user_data_dir=None, account=None):
    """同步版启动指纹内核，返回 (playwright, browser, context, backend)。

    account 参数语义同 launch_async（读账号 .env DY_PROXY 注入代理）。
    """
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
        launch_args, _proxy_url, pw_proxy = _launch_args_with_proxy(cfg, account=account)
        # 2026-09-13 风控根治（同 launch_async）：无头请求一律转为「真有头+窗口最小化」。
        _disguise = False
        _minimize = False
        if headless:
            headless = False
            _minimize = True
            logger.info("[vbrowser] 无头请求已转为 真有头+窗口最小化（风控对齐："
                        "同 profile 同环境，杜绝环境跳变）")
        p = sync_playwright().start()
        context = p.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            executable_path=exe,
            headless=headless,  # native：True 纯无头
            args=launch_args,
            proxy=pw_proxy,
            # Playwright 在 Windows headed 模式下会强制注入 --no-sandbox，
            # 触发指纹内核“不受支持的命令行标记”警告。显式剔除该默认参数。
            ignore_default_args=["--no-sandbox"],
        )
        # 2026-09-06：同步可见启动同样归位屏外遗留窗口；伪装启动不归位。
        if not _disguise:
            _ensure_window_visible_sync(context)
        if _minimize:
            _minimize_window_sync(context)
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


async def open_douyin_home(profile_dir, headless=False, url="https://www.douyin.com/", account=None):
    """单纯拉起指纹浏览器并打开指定页面（默认抖音主页）。

    account 传入账号名时读该账号 .env DY_PROXY 注入代理（查看模式同样走代理，
    与守护/重扫保持同一出口环境，避免同账号不同出口触发风控）。

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
        launch_args, _proxy_url, pw_proxy = _launch_args_with_proxy(_CFG, account=account)
        p = await async_playwright().start()
        context = await p.chromium.launch_persistent_context(
            user_data_dir=profile_dir,
            executable_path=exe,
            headless=headless,
            args=launch_args,
            proxy=pw_proxy,
            # Playwright 在 Windows headed 模式下会强制注入 --no-sandbox，
            # 触发指纹内核“不受支持的命令行标记”警告。显式剔除该默认参数。
            ignore_default_args=["--no-sandbox"],
        )
        # 2026-09-06：查看模式恒可见，必须把伪装模式残留的屏外窗口归位，
        # 否则用户双击打开的浏览器窗口落在桌面外、无法操作。
        await _ensure_window_visible(context)
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
