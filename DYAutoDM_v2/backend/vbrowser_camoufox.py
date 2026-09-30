# -*- coding: utf-8 -*-
"""Camoufox 启动层（Firefox 内核，C++ 层指纹注入 —— 无 JS 注入痕迹）。

## 为什么引入（2026-09-20 用户拍板更换指纹浏览器）

抖音「安全风险…已阻止此次访问」弹窗在现行方案（ungoogled-chromium +
add_init_script 注入劫持 fetch/XHR/WebSocket）下，于**交互时刻**（扫码授权、
输手机号）被拦截。而 Camoufox 实测在同一出口、同一交互路径下**连续两轮
未弹窗**（含冷启动持久 profile）。

关键差异（取自 Camoufox 官方设计）：
> Fingerprint injection & rotation (**without JS injection!**) —— 数据拦截在
> **C++ 实现层**，无法通过 JavaScript 检查发现。

我们的旧方案正是 JS 注入改写原生对象，属"可被 JS 检查发现"的痕迹类别。

## 设计契约（必须遵守）

1. **单 profile 铁律不变**：必须传 user_data_dir（该账号固定目录），
   绝不新建临时目录（临时/全新环境本身即风控放大器）。
2. **headless 是调用方意图，不得内部改写**（沿用 §十七 铁律）。
3. **代理由配置显式决定**（沿用环境门阀：node / system / direct）。
4. 与 Chromium 路径**返回同构结果** (pw, browser, context, backend)，
   使 `vbrowser.launch_async/launch_sync` 可透明分派、调用方零改动。

## 已知差异（Firefox vs Chromium，迁移需注意）

- 私信 WS 抓取：Camoufox 下**不得**注入 JS 劫持 WebSocket；
  应改用 Playwright 原生事件（非页面注入，无痕迹）。
- 昵称抓取：Chromium 的 IndexedDB `<uid>_user` 结构在 Firefox 下需实测。
- 指纹开关：`--fingerprint-*` 命令行 → Camoufox 的 config={} / os / locale。
"""
from __future__ import annotations

import asyncio
import os
from typing import Any, Optional

from loguru import logger

# ── 媒体设备（虚拟麦克风/摄像头）—— Camoufox 原生协议（Firefox pref）──────────
#   Chromium 路径用 `vbrowser_args._FAKE_MEDIA_ARGS`（--use-fake-device-for-media-stream
#   等）；Camoufox 是 **Firefox 内核，Chromium flag 会静默失效** ⇒ 2026-09-29 迁移补齐（H-12）。
#   这些是与 Chromium 那组 **语义等价** 的 Firefox pref：
#     media.navigator.streams.fake        = 用虚拟采集源替代真实麦克风/摄像头
#     permissions.default.microphone/camera = 1（自动允许，不弹系统授权框）
#     media.navigator.permission.disabled = 自动同意权限请求
#     media.peerconnection.enabled        = 启用 WebRTC（连麦/语音必需）
#   ⚠️ **设备红线**：始终使用**虚拟**设备，绝不触碰真实麦克风/摄像头（沿用 v0.40 铁律）。
_CAMOUFOX_MEDIA_PREFS = {
    "media.navigator.streams.fake": True,
    "media.navigator.permission.disabled": True,
    "permissions.default.microphone": 1,
    "permissions.default.camera": 1,
    "media.peerconnection.enabled": True,
}


def camoufox_enabled(cfg: Any = None) -> bool:
    """是否启用 Camoufox 内核（显式配置，绝不自动探测）。

    配置优先级：cfg.DY_BROWSER_KERNEL > 环境变量 DY_BROWSER_KERNEL > 默认 chromium。
    值：'camoufox' 启用；其它（含 'chromium'）走原 Chromium 路径 —— 保留回退。
    """
    val = ""
    try:
        val = str(getattr(cfg, "DY_BROWSER_KERNEL", "") or "").strip().lower()
    except Exception:
        val = ""
    if not val:
        val = str(os.environ.get("DY_BROWSER_KERNEL", "") or "").strip().lower()
    return val == "camoufox"


def _proxy_for_camoufox(cfg: Any, account: Optional[str]) -> Optional[dict]:
    """把项目环境门阀（node/system/direct）映射为 Camoufox 的 proxy 参数。"""
    try:
        from vbrowser import _launch_args_with_proxy
        _args, proxy_url, _pw_proxy = _launch_args_with_proxy(cfg, account=account)
    except Exception:
        proxy_url = None
    if not proxy_url:
        return None
    return {"server": proxy_url}


# ★ 2026-10-01 防 GC 强引用池（**修复「扫码/短信等待期间 context 被回收」**）
#   实测：`AsyncCamoufox`（PlaywrightContextManager）被挂到 `context.__dict__["_camoufox_ctx_mgr"]`
#   后**无强引用** ⇒ 等待用户操作（扫码最长 240s、短信最长 300s）期间被 GC 回收
#   ⇒ 触发其 `__aexit__` ⇒ context 关闭 ⇒ 后续 `context.cookies()` 抛
#   `'NoneType' object has no attribute 'send'` / `TargetClosedError`。
#   实测对照：无强引用 45s 断；有强引用 150s 稳定。
#   这里在**创建点**统一登记，确保所有路径（桥/短信/截图）一致生效。
_CAMOUFOX_KEEP: dict = {}


def _keep_camoufox_alive(context, ctx_mgr) -> None:
    """登记强引用，防止 ctx_mgr 被 GC 回收（幂等）。"""
    try:
        _CAMOUFOX_KEEP[id(context)] = {"ctx_mgr": ctx_mgr, "context": context}
    except Exception:  # noqa: BLE001
        pass


def _release_camoufox(context) -> None:
    """配对释放强引用（幂等；context 关闭后调用）。"""
    try:
        _CAMOUFOX_KEEP.pop(id(context), None)
    except Exception:  # noqa: BLE001
        pass


def _media_prefs(cfg: Any = None) -> Optional[dict]:
    """Camoufox 媒体 pref（虚拟麦克风/摄像头）。显式配置可关：DY_FAKE_MEDIA_OFF=1。

    返回 None ⇒ 不注入（保持上游默认）。
    """
    off = str(os.environ.get("DY_FAKE_MEDIA_OFF", "") or "").strip().lower() in ("1", "true", "yes", "on")
    try:
        if cfg is not None and bool(getattr(cfg, "DY_FAKE_MEDIA_OFF", False)):
            off = True
    except Exception:
        pass
    if off:
        logger.info("[camoufox] 虚拟媒体已按配置关闭（DY_FAKE_MEDIA_OFF）")
        return None
    return dict(_CAMOUFOX_MEDIA_PREFS)


def launch_camoufox_sync(*, headless: bool = False, user_data_dir: str | None = None,
                         account: str | None = None, cfg: Any = None) -> tuple:
    """同步启动 Camoufox（persistent_context）。返回 (None, browser, context, 'camoufox')。

    与 launch_sync 保持同构返回，便于上层透明替换。
    """
    if user_data_dir and not os.path.isabs(user_data_dir):
        # 方案 3：相对 profile 名 → 可写数据根（必要时从资源根首次种子化）
        try:
            from vbrowser import resolve_profile_dir as _rpd
            user_data_dir = _rpd(user_data_dir)
        except Exception:  # noqa: BLE001
            pass
    if not user_data_dir:
        raise RuntimeError(
            "[camoufox] 未指定固定 profile 目录。单 profile 铁律："
            "禁止临时目录，必须传入 accounts.profile_dir_of(env_path)")

    try:
        from camoufox.sync_api import Camoufox
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(
            f"[camoufox] 未安装或不可用（pip install camoufox[geoip]）: {e}") from e

    # Camoufox 的 profile 与 Chromium 不兼容：在同一账号目录下用独立子目录，
    # 保证「单 profile 铁律」语义一致（该账号仍只有一个浏览器环境）。
    cam_dir = os.path.join(user_data_dir, "_camoufox")
    os.makedirs(cam_dir, exist_ok=True)
    logger.info(f"[camoufox] 复用固定 profile: {cam_dir}")

    proxy = _proxy_for_camoufox(cfg, account)
    if proxy:
        logger.info(f"[camoufox] 环境门阀：代理 → {proxy.get('server')}")
    else:
        logger.info("[camoufox] 环境门阀：无代理（直连）")

    # persistent_context 用法：Camoufox(...) 直接给出 BrowserContext
    ctx_mgr = Camoufox(
        persistent_context=True,
        user_data_dir=cam_dir,
        headless=bool(headless),
        proxy=proxy,
        locale="zh-CN",
        os="windows",
        firefox_user_prefs=_media_prefs(cfg),
        i_know_what_im_doing=True,
    )
    # 交给调用方管理生命周期：这里进入并返回 context，
    # 由 vbrowser 统一封装关闭逻辑。
    context = ctx_mgr.__enter__()
    # 保存 ctx_mgr 以便正确退出（挂到 context 上，避免调用方改动）
    try:
        context.__dict__["_camoufox_ctx_mgr"] = ctx_mgr
    except Exception:
        pass
    return None, None, context, "camoufox"


async def launch_camoufox_async(*, headless: bool = False,
                                user_data_dir: str | None = None,
                                account: str | None = None,
                                cfg: Any = None) -> tuple:
    """异步启动 Camoufox（persistent_context）。返回 (None, None, context, 'camoufox')。

    ## 为什么必须用 AsyncCamoufox 而不是「线程池包 sync」（2026-09-20 实测）

    初版实现是 `run_in_executor(None, launch_camoufox_sync)` —— **必然失败**：
    Playwright 的「Sync API 检测」是**进程级**的（driver 连接时会校验调用线程
    之外是否存在运行中的 asyncio loop），换线程不解决。实测报错原文：

        It looks like you are using Playwright Sync API inside the asyncio loop.

    正确做法：改用 `camoufox.async_api.AsyncCamoufox`——它基于
    `playwright.async_api`，与 BCC（FastAPI/asyncio）同构。
    """
    if user_data_dir and not os.path.isabs(user_data_dir):
        # 方案 3：相对 profile 名 → 可写数据根（必要时从资源根首次种子化）
        try:
            from vbrowser import resolve_profile_dir as _rpd
            user_data_dir = _rpd(user_data_dir)
        except Exception:  # noqa: BLE001
            pass
    if not user_data_dir:
        raise RuntimeError(
            "[camoufox] 未指定固定 profile 目录。单 profile 铁律："
            "禁止临时目录，必须传入 accounts.profile_dir_of(env_path)")

    try:
        from camoufox.async_api import AsyncCamoufox
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(
            f"[camoufox] 未安装或不可用（pip install camoufox[geoip]）: {e}") from e

    # Camoufox 的 profile 与 Chromium 不兼容：同一账号目录下用独立子目录，
    # 保证「单 profile 铁律」语义一致（路径契约见 camoufox_capture 同名说明）。
    cam_dir = os.path.join(user_data_dir, "_camoufox")
    os.makedirs(cam_dir, exist_ok=True)
    logger.info(f"[camoufox] 复用固定 profile: {cam_dir}")

    proxy = _proxy_for_camoufox(cfg, account)
    if proxy:
        logger.info(f"[camoufox] 环境门阀：代理 → {proxy.get('server')}")
    else:
        logger.info("[camoufox] 环境门阀：无代理（直连）")

    ctx_mgr = AsyncCamoufox(
        persistent_context=True,
        user_data_dir=cam_dir,
        headless=bool(headless),
        proxy=proxy,
        locale="zh-CN",
        os="windows",
        firefox_user_prefs=_media_prefs(cfg),
        i_know_what_im_doing=True,
    )
    context = await ctx_mgr.__aenter__()
    _keep_camoufox_alive(context, ctx_mgr)   # ★ 防 GC：创建即登记强引用
    try:
        context.__dict__["_camoufox_ctx_mgr"] = ctx_mgr
        context.__dict__["_camoufox_is_async"] = True
    except Exception:
        pass
    return None, None, context, "camoufox"


def _reap_camoufox_processes(user_data_dir: str | None = None,
                             grace_sec: float = 3.0,
                             force_sec: float = 12.0) -> int:
    """强制清扫仍占用该 profile 的 Camoufox 进程（残留根因修复，2026-09-25）。

    ## 为什么需要本函数（实机实测，非推测）

    实测（drain.py，生产路径 launch_camoufox_sync）：
        before_alive = 0       启动前干净
        launched     = 2       启动后 2 个进程
        调 close_camoufox_context() 后等 60s → 仍是 2（**未退净**）

    即：Camoufox 的 ctx_mgr.__aexit__() **不能保证**浏览器进程退出。
    而上层 _wait_profile_released() 只等 10s 就放行 → 新实例撞上旧进程
    → `Failed to launch the browser process`（BCC-058）。这就是
    「先开 A 失败、紧接着开 B 也失败、过一会儿又正常」的时序竞态真因。

    策略（优雅优先，逐级升级）：
      1) 先给 grace_sec 秒让进程自行退出（terminate）；
      2) 仍在则 kill；
      3) 返回被清扫的进程数。

    只杀**命令行命中本 profile 路径**的进程（含 -contentproc 子进程），
    绝不按进程名盲杀 —— 否则会误伤其它账号/用户手动打开的窗口。
    """
    if not user_data_dir:
        return 0
    # ── 安全边界（2026-09-25 补，实测教训）──────────────────────────────
    # 曾因 needle 过短/过通用（探针里 _profile_dir="GUARD"），命中了
    # 命令行含 "guard" 的**无关进程**（含测试宿主自身）→ 误杀。
    # 判据：profile 路径必须是绝对路径且长度足够，否则宁可不扫。
    # 「不能可靠识别目标」时拒绝行动，是本函数的第一 invariant。
    _ud_norm = str(user_data_dir).replace("\\", "/")
    if len(_ud_norm) < 20 or (":" not in _ud_norm and not _ud_norm.startswith("/")):
        logger.warning(
            f"[camoufox] profile 路径过短或非绝对路径，拒绝清扫（防误杀无关进程）: "
            f"{user_data_dir!r}")
        return 0
    try:
        import psutil
    except Exception:
        logger.debug("[camoufox] psutil 不可用，跳过残留清扫")
        return 0
    needle = _ud_norm.lower()
    _self_pid = os.getpid()

    def _victims():
        out = []
        try:
            for _p in psutil.process_iter(["pid", "name", "cmdline"]):
                try:
                    # 绝不杀自己（误杀宿主 = 比残留严重一个量级）
                    if int(_p.info.get("pid") or 0) == _self_pid:
                        continue
                    # ── 2026-09-28 修（DSSCC-BCC-005，实测自伤）──────────────
                    # 原判据**只看命令行是否含 profile 路径**，与本函数文档
                    # 「只杀命中该 profile 的 camoufox/firefox 进程」不符：
                    # 任何**命令行里恰好写了这个路径**的进程都会被列入待杀。
                    # 实测后果：诊断脚本 / shell 命令中带上该路径（极常见）⇒
                    # **调用者自身连同其 shell 一起被杀**（终端 exit 15，
                    # 无任何错误输出，排查成本极高）。
                    # 现补进程名前置过滤，与 `count_profile_processes` 同一判据。
                    _nm = (_p.info.get("name") or "").lower()
                    if "camoufox" not in _nm and "firefox" not in _nm:
                        continue
                    _cl = " ".join(_p.info.get("cmdline") or [])
                    if needle in _cl.replace("\\", "/").lower():
                        out.append(_p)
                except Exception:
                    continue
        except Exception:
            return []
        return out

    try:
        pending = _victims()
        if not pending:
            return 0
        logger.warning(
            f"[BCC-074] [camoufox] 关闭后仍残留 {len(pending)} 个进程占用 profile，"
            f"开始清扫（grace={grace_sec}s）: {needle[-60:]}")
        for p in pending:
            try:
                p.terminate()
            except Exception:
                pass
        psutil.wait_procs(pending, timeout=grace_sec)
        still = [p for p in _victims()]
        if still:
            for p in still:
                try:
                    p.kill()
                except Exception:
                    pass
            psutil.wait_procs(still, timeout=force_sec)
        left = len(_victims())
        if left:
            logger.error(
                f"[BCC-075] [camoufox] 清扫后仍有 {left} 个进程占用 profile"
                f"（可能被系统保护，需人工处理）: {needle[-60:]}")
        else:
            logger.info("[camoufox] profile 占用进程已清扫干净，可安全重建 context")
        return len(pending) - left
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[camoufox] 残留清扫异常（不阻塞关闭流程）: {e}")
        return 0


async def close_camoufox_context(context, user_data_dir: str | None = None) -> None:
    _release_camoufox(context)   # ★ 配对释放：摘掉强引用（在关闭前，顺序无关紧要）
    """统一关闭 Camoufox context（含其 AsyncCamoufox 上下文管理器）。

    ## 为什么必须单独一个函数（2026-09-20 实测事故）

    `AsyncCamoufox` 是 PlaywrightContextManager：只调 `context.close()` **不会**
    真正结束它拉起的浏览器进程/驱动，必须走 `ctx_mgr.__aexit__()`。
    漏掉的后果（实测）：可见性切换时旧进程仍占 `_camoufox` profile →
    新有头实例启动报 `Failed to launch the browser process` →
    用户看到「弹窗没有浏览器窗口」。

    ## 兼容同步/异步两种来源

    - async 启动（BCC 路径）：context.__dict__ 里有 `_camoufox_ctx_mgr`，
      且带 `_camoufox_is_async=True` → 用 await __aexit__。
    - sync 启动（CLI/camoufox_capture 路径）：ctx_mgr 只有 __exit__，
      直接调同步退出（此时不处于事件循环）。

    ## 2026-09-25 补：关闭后必须清扫残留进程

    实测 __aexit__ 后进程 60s 仍不退净（见 _reap_camoufox_processes 文档）。
    原实现在各分支 `return` **提前返回**，清扫会被跳过 —— 现改为统一走
    finally 清扫。user_data_dir 缺省时从 context 反推，调用方可显式传入。
    """
    if context is None:
        return
    mgr = getattr(context, "__dict__", {}).get("_camoufox_ctx_mgr")
    is_async = bool(getattr(context, "__dict__", {}).get("_camoufox_is_async"))
    # 清扫目标：仅用显式传入的 profile 目录（从 context 无法可靠反推，
    # 宁可不扫也不能扫错 —— 按路径精确匹配是安全边界）。
    _ud = user_data_dir
    try:
        if mgr is not None:
            if is_async or hasattr(mgr, "__aexit__"):
                aexit = getattr(mgr, "__aexit__", None)
                if aexit is not None:
                    await aexit(None, None, None)
                else:
                    await context.close()
            elif hasattr(mgr, "__exit__"):
                mgr.__exit__(None, None, None)
            else:
                await context.close()
        else:
            # 兜底：普通 context
            await context.close()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[camoufox] 关闭 context 异常（忽略以继续清理）: {e}")
    finally:
        # 关闭动作无论成败、无论走哪个分支，都必须清扫残留（根因修复）
        if _ud:
            # P2-④（H-22 审计 idx21 · 实测事件循环停摆 3.14s）：
            # `_reap_camoufox_processes` 是**同步阻塞**函数（psutil.wait_procs
            # 默认 3s + 12s）⇒ 直接在 async 的 finally 里调用会**卡死整个事件循环**
            # （FastAPI/daemon 全停）。5 个生产调用点都 await 本协程 ⇒ 必现。
            # 故放线程池执行，保持「清理完成再返回」的语义但不阻塞事件循环。
            await asyncio.to_thread(_reap_camoufox_processes, _ud)


def close_camoufox_context_sync(context, user_data_dir: str | None = None) -> None:
    """同步版关闭（供 CLI/脚本路径使用，不得在 asyncio 循环内调用）。

    2026-09-25：与异步版同构，关闭后统一清扫残留进程（见
    _reap_camoufox_processes 文档）。
    """
    if context is None:
        return
    mgr = getattr(context, "__dict__", {}).get("_camoufox_ctx_mgr")
    _ud = user_data_dir
    try:
        if mgr is not None and hasattr(mgr, "__exit__"):
            mgr.__exit__(None, None, None)
        else:
            context.close()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[camoufox] 同步关闭 context 异常（忽略）: {e}")
    finally:
        if _ud:
            _reap_camoufox_processes(_ud)
