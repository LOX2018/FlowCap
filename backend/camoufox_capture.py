# -*- coding: utf-8 -*-
"""Camoufox 凭证捕获/写回（正式入口）。

## 为什么需要

Camoufox（Firefox）与 Chromium 的 profile 不兼容，走独立的 `_camoufox`
子目录；用户在该窗口内完成扫码授权后，凭证位于 Camoufox profile 内，
**不会自动写回**账号 .env —— 本模块负责这座桥。

## 设计契约

1. **只读 + 写回**：读取 browser cookies 与页面 localStorage 的 security-sdk
   两个键，组装 DouyinAuth 后交既有 `save_credential` 落盘。
   **不发起任何主动业务请求**（风控红线）。
2. **不写残缺凭证**：无 sessionid/sid_tt 或 web_protect 无效（空壳）时
   **拒绝写回**并显式报错，绝不污染 .env（沿用既有守卫语义）。
3. **复用既有真源**：perepare_auth / save_credential / _web_protect_valid
   一律用 login_api 的既有实现，不另写一份（防同源多实现漂移）。

## 用法

    from camoufox_capture import capture_from_camoufox
    ok, msg = capture_from_camoufox("账号名")
"""
from __future__ import annotations

import os
import time
from typing import Optional, Tuple

from loguru import logger

_LANDING = "https://www.douyin.com/chat?isPopup=1"


def _web_protect_valid(s) -> bool:
    """校验 web_protect 是有效 JSON 且含关键密钥字段（防空壳/占位导致私信 KICK）。

    判据与 `dy_apis/login_api.py:335` 的嵌套实现**逐条一致**——该实现是函数内
    嵌套定义，外部不可直接引用，故在此复刻；若上游判据变更，两处须同步
    （已加守卫测试 TestJ 断言二者行为一致）。
    """
    if not s or not isinstance(s, str):
        return False
    s = s.strip()
    if not (s.startswith("{") or s.startswith("[")):
        return False
    try:
        import json
        obj = json.loads(s)
    except Exception:
        return False
    blob = json.dumps(obj)
    for key in ("webcast", "sign", "key", "ticket", "token", "salt", "app_id"):
        if key in blob:
            return True
    return False


def _assert_not_in_asyncio(where: str) -> None:
    """守卫：同步 Playwright 调用不得在 asyncio 事件循环内执行。

    2026-09-20 实测事故：`launch_camoufox_async` 初版用 run_in_executor 包同步
    API → Playwright 的「Sync API inside asyncio loop」检测是**进程级**的，
    换线程无效 → BCC 拿不到 context 句柄，而浏览器进程**已经起来** →
    孤儿进程占住 `_camoufox`/parent.lock，用户双击被提示「占用」。

    本模块的函数是**同步**语义（供 CLI / 脚本调用）。若被误用在 asyncio 环境，
    这里**显式报错**而不是让它以「孤儿进程 + 占用」的形式死得不明不白。
    """
    try:
        import asyncio
        asyncio.get_running_loop()
    except RuntimeError:
        return  # 无运行中的 loop —— 正常同步场景
    raise RuntimeError(
        f"[camoufox] {where} 是同步 API，不可在 asyncio 事件循环内调用"
        "（Playwright 会拒绝并在磁盘留下孤儿浏览器进程）。"
        "asyncio 环境请改用 vbrowser_camoufox.launch_camoufox_async。")


def _env_path_of(account: str) -> Optional[str]:
    try:
        from auto_dm import accounts as _acc
        return _acc.env_path_of(account)
    except Exception:
        return None


def open_camoufox_window(account: str, url: str = _LANDING) -> Tuple[bool, str]:
    """以有头 Camoufox 打开账号的固定 profile，供用户扫码授权/观察。

    **路径契约（必须与 capture_from_camoufox 同源）**：profile 一律取
    `<账号>/.env 的目录>/profile`，Camoufox 内部再拼一层 `_camoufox`，
    即 `<账号>/profile/_camoufox`。两处若不同源，会各自生成一个 profile，
    表现为「明明授权了却读不到 sessionid」（实测踩坑，见案例归档）。
    """
    from vbrowser_camoufox import launch_camoufox_sync

    _assert_not_in_asyncio("open_camoufox_window")
    env_path = _env_path_of(account)
    if not env_path:
        return False, f"账号 {account} 的 .env 未登记"
    prof = os.path.join(os.path.dirname(env_path), "profile")
    try:
        pw, browser, ctx, backend = launch_camoufox_sync(
            headless=False, user_data_dir=prof, account=account)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=90000)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[camoufox-open] 打开 {url} 失败: {e}")
        # 不关闭：窗口留给用户（与"有头作为观测态"的契约一致）
        ctx.__dict__["_camoufox_ctx_mgr"] = ctx.__dict__.get("_camoufox_ctx_mgr")
        return True, f"Camoufox 窗口已打开（backend={backend}, profile={prof}）"
    except Exception as e:  # noqa: BLE001
        return False, f"打开窗口异常: {e}"


def capture_from_camoufox(account: str, wait_sec: int = 12) -> Tuple[bool, str]:
    """从 Camoufox 已授权 profile 抓取凭证并写回账号 .env。

    返回 (ok, msg)。ok=True 仅表示**已成功写盘**；凭证有效性由调用方
    用 `services.uid_probe.get_uid(force=True)` 复验（本函数不代劳，
    保持"读取/写盘"与"有效性判定"两件事解耦）。
    """
    from vbrowser_camoufox import launch_camoufox_sync
    from dy_apis.login_api import DYLoginApi

    _assert_not_in_asyncio("capture_from_camoufox")
    env_path = _env_path_of(account)
    if not env_path:
        return False, f"账号 {account} 的 .env 未登记"

    # 单一 profile 铁律：路径必须与「扫码窗口」完全一致，否则会打开另一个
    # 空壳 profile（实测踩坑：账号/ 与 账号/profile/ 会各自生成一个 _camoufox，
    # 前者无登录态 → 明明已授权却读不到 sessionid）。
    # 扫码窗口用的是 <账号>/profile/（见 open_camoufox_window），此处必须同源。
    prof = os.path.join(os.path.dirname(env_path), "profile")
    logger.info(f"[camoufox-capture] 账号 {account} 读取凭证（profile={prof}）")

    ctx = None
    mgr = None
    try:
        pw, browser, ctx, backend = launch_camoufox_sync(
            headless=True, user_data_dir=prof, account=account)
        mgr = ctx.__dict__.get("_camoufox_ctx_mgr")
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        # 打开私信落地页：security-sdk 需在该页把 web_protect 从空壳升级为有效值
        try:
            page.goto(_LANDING, wait_until="domcontentloaded", timeout=90000)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[camoufox-capture] 打开私信落地页失败（继续尝试读取）: {e}")
        time.sleep(max(3, int(wait_sec)))

        cookies = {c["name"]: c["value"] for c in ctx.cookies()}
        if not (cookies.get("sessionid") or cookies.get("sid_tt")):
            return False, ("未检测到登录态（无 sessionid/sid_tt）—— "
                           "请在 Camoufox 窗口中完成扫码授权后重试")

        keys_str = page.evaluate('localStorage["security-sdk/s_sdk_crypt_sdk"]')
        wp_str = page.evaluate(
            'localStorage["security-sdk/s_sdk_sign_data_key/web_protect"]')
        if not keys_str:
            return False, "未读到 security-sdk keys（localStorage 键缺失）"

        api = DYLoginApi()
        if not _web_protect_valid(wp_str):
            return False, ("web_protect 无效或为空壳（security-sdk 未生成有效签名）"
                           "—— 请确认已打开过私信落地页；本次不写残缺凭证")

        auth = api._load_auth_from_env(env_path)
        auth.cookie = cookies
        # 重派生 ticket/ts_sign（与 Chromium 路径同一实现，防同源多实现漂移）
        auth.perepare_auth("", wp_str, keys_str)
        api.save_credential(auth, env_path=env_path)
        logger.success(
            f"[camoufox-capture] 账号 {account} 凭证已写回（"
            f"ticket={(getattr(auth, 'ticket', '') or '')[:18]}…, "
            f"ts_sign={(getattr(auth, 'ts_sign', '') or '')[:18]}…）")
        return True, "凭证已从 Camoufox profile 读取并写回 .env"
    except Exception as e:  # noqa: BLE001
        logger.error(f"[camoufox-capture] 账号 {account} 捕获异常: {e}")
        return False, f"捕获异常: {e}"
    finally:
        try:
            if mgr is not None:
                mgr.__exit__(None, None, None)
            elif ctx is not None:
                ctx.close()
        except Exception:
            pass


def verify_credentials(account: str) -> Tuple[bool, str]:
    """复验凭证有效性（真打网）：探活 uid 且必须与该账号历史 conv_id 一致。

    判据来源：铁律「UID 漂移 = 凭证失效」。仅"写回了 .env"不算成功。
    """
    try:
        from services import uid_probe
        uid = uid_probe.get_uid(account, force=True)
        if not uid:
            return False, "探活未取得 uid（凭证可能无效）"
        ok = uid_probe._uid_consistent_with_history(account, uid)
        if not ok:
            return False, f"探活 uid={uid} 与历史会话不一致（UID 漂移 = 凭证失效）"
        return True, f"凭证有效（uid={uid}，与历史会话一致）"
    except Exception as e:  # noqa: BLE001
        return False, f"复验异常: {e}"
