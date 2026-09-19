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

import os
from typing import Any, Optional

from loguru import logger


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


def launch_camoufox_sync(*, headless: bool = False, user_data_dir: str | None = None,
                         account: str | None = None, cfg: Any = None) -> tuple:
    """同步启动 Camoufox（persistent_context）。返回 (None, browser, context, 'camoufox')。

    与 launch_sync 保持同构返回，便于上层透明替换。
    """
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
    """异步启动 Camoufox。Playwright 的 Camoufox 同步 API 在子线程执行，
    避免阻塞事件循环（本项目 async 路径均在事件循环内）。"""
    import asyncio
    return await asyncio.get_event_loop().run_in_executor(
        None,
        lambda: launch_camoufox_sync(
            headless=headless, user_data_dir=user_data_dir,
            account=account, cfg=cfg),
    )
