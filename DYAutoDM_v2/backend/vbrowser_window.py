"""指纹浏览器 —— 窗口可见性 / 状态管理

## 为什么独立（2026-09-15 大单文件打散）

原 `vbrowser.py`（1369 行）含约 221 行**窗口几何与状态工具**（枚举工作区、
判断窗口是否落在真实屏幕内、防止窗口跑到屏幕外、最小化/状态切换，含 async
与 sync 两套）。抽出后 vbrowser.py 聚焦浏览器启动与上下文管理。

## 说明
**纯搬移**——几何判据（_VISIBLE_MIN_OVERLAP=200）、同步/异步镜像实现逐字节不变。
"""
from __future__ import annotations

from loguru import logger

# 2026-09-17 修补（OCR 审查 CRITICAL）：本模块由 vbrowser.py 机械拆分而来，
# 但拆分时**丢失了 ctypes 导入**，导致 `_enum_workareas()` 首次调用即抛
# `NameError: name '_ctypes' is not defined`（Windows 下窗口几何判据全失效）。
# 与 vbrowser_args.py:68-69 保持同一写法；非 Windows 平台下置 None 并走兜底。
try:
    import ctypes as _ctypes
    from ctypes import wintypes as _wintypes
except Exception:  # pragma: no cover - 非 Windows
    _ctypes = None
    _wintypes = None


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
        logger.warning(f"[BCC-035] " + f"[vbrowser] 窗口归位检查失败（不阻塞启动）: {e}")


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
        logger.warning(f"[BCC-036] " + f"[vbrowser] 窗口归位检查失败（不阻塞启动）: {e}")

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

async def _set_window_state(context, state: str) -> bool:
    """把窗口设为 normal / minimized（异步版）。返回是否成功。

    2026-09-13 新增：容器常驻「真有头+最小化」后，切换可见性**不再重建 context**，
    只需改窗口状态 —— 这同时解决三件事：
      · 消除 context 重建带来的 3~6s 抖动与重启 churn（用户所见"闪退"）；
      · 昵称/头像捕获链路全程不中断（不再出现切换期间 im/user/info 断档）；
      · 登录态与环境零跳变（抖音看不到环境突变 → 不触发 step-up）。
    """
    try:
        pages = context.pages
        if not pages:
            return False
        session = await context.new_cdp_session(pages[0])
        try:
            info = await session.send("Browser.getWindowForTarget")
            wid = info.get("windowId")
            await session.send("Browser.setWindowBounds",
                               {"windowId": wid, "bounds": {"windowState": state}})
            return True
        finally:
            try:
                await session.detach()
            except Exception:
                pass
    except Exception as e:
        logger.debug(f"[vbrowser] 设置窗口状态({state})失败: {e}")
        return False


def _set_window_state_sync(context, state: str) -> bool:
    """同步版：把窗口设为 normal / minimized。"""
    try:
        pages = context.pages
        if not pages:
            return False
        session = context.new_cdp_session(pages[0])
        try:
            info = session.send("Browser.getWindowForTarget")
            wid = info.get("windowId")
            session.send("Browser.setWindowBounds",
                         {"windowId": wid, "bounds": {"windowState": state}})
            return True
        finally:
            try:
                session.detach()
            except Exception:
                pass
    except Exception as e:
        logger.debug(f"[vbrowser] 设置窗口状态({state})失败: {e}")
        return False


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
            except Exception as e:
                # 🔴 2026-10-01 修复：静默兜底改为可观测日志
                logger.debug(f"[vbrowser] session.detach 失败: {e}")
    except Exception as e:
        logger.warning(f"[BCC-035] " + f"[vbrowser] 窗口最小化失败（不阻塞启动）: {e}")


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
        logger.warning(f"[BCC-035] " + f"[vbrowser] 窗口最小化失败（不阻塞启动）: {e}")


