# coding=utf-8
"""补全私信签名所需的 auth（对齐基座 cv-cat/DouYin_Spider 的做法）。

基座的正确流程（dy_apis/login_api.py）：
  1) 优先从 .env 读取 DY_TICKET / DY_TS_SIGN / DY_CLIENT_CERT / DY_PRIVATE_KEY；
     若齐全则直接复用，无需任何浏览器操作（首次扫码之后全自动）。
  2) 缺失时由 DYLoginApi.login_grab_ticket 打开浏览器扫码登录，
     从 localStorage 的 security-sdk/s_sdk_crypt_sdk 与
     security-sdk/s_sdk_sign_data_key/web_protect 抓取签名原始串，
     经 DouyinAuth.perepare_auth('', web_protect_str, keys_str) 双层解析后回填，
     再由 save_credential 把 4 个签名变量 + cookie 写回 .env。

本模块只做一件事：把上面这套封装成同步的 enrich_auth(auth, ...) 供 run/gui 调用，
不再自行模糊匹配 localStorage（之前那套会导致 ticket 始终为 None）。
"""

import os
import asyncio
from loguru import logger


def enrich_auth(auth, cookies_dy="", headless=False,
                user_data_dir="pw_profile_dm", env_path=".env", force=False):
    """补全 auth 的私信签名字段。

    直接委托基座 DYLoginApi.get_login_auth：
      - env_path 指向的 .env 已有 DY_TICKET/DY_PRIVATE_KEY 时，直接返回（不扫码、不开浏览器）；
      - 缺失/失效时扫码登录并把凭证写回该 .env，之后长期复用。

    env_path: 要读写的 .env 绝对/相对路径（多账号切换时指向对应账号的 .env）。

    返回 (auth, cookie_str)。cookie_str 非空表示本次新抓到并落盘。
    """
    try:
        from dy_apis.login_api import DYLoginApi
    except Exception as e:
        logger.warning(f"[auth] 无法导入基座 DYLoginApi，跳过签名补全: {e}")
        logger.warning("[auth] 请先安装依赖：pip install aiohttp（基座 login_api 需要）")
        return auth, cookies_dy

    try:
        from dotenv import load_dotenv
        # 强制用指定 .env 绝对路径加载（override=True），确保 get_login_auth 内部的
        # common_util.load_env() 能读到 DY_COOKIES（否则 trans_cookies(None) 会崩）。
        if not os.path.isabs(env_path):
            from auto_dm.vbrowser import app_root
            env_path = os.path.join(app_root(), env_path)
        load_dotenv(env_path, override=True)
    except Exception as e:
        logger.warning(f"[auth] 加载 {env_path} 失败: {e}")

    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = None

    try:
        if loop is not None and loop.is_running():
            # 极端情况下已有事件循环在跑，用新线程跑异步
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                auth = ex.submit(
                    lambda: asyncio.run(
                        DYLoginApi().get_login_auth(
                            headless=headless, env_path=env_path, force=force))
                ).result()
        else:
            auth = asyncio.run(
                DYLoginApi().get_login_auth(
                    headless=headless, env_path=env_path, force=force))
    except Exception as e:
        logger.warning(f"[auth] 获取登录凭证失败: {e}")
        return auth, cookies_dy

    cookie_str = "; ".join(f"{k}={v}" for k, v in (auth.cookie or {}).items())
    # 修复：基座 get_login_auth 不设置 auth.uid，而 create_conversation 依赖
    # auth.get_uid() 返回数字 uid；get_my_uid 依赖不稳定的 s_v_web_id 字段，
    # 抓到的 cookie 里往往没有 -> 返回 None -> int(None) 崩。
    # 这里直接从 cookie 的数字字段提取并固定 auth.uid，彻底绕开该问题。
    ensure_uid(auth)
    return auth, cookie_str


def ensure_uid(auth):
    """从 cookie 提取并设置 auth.uid（数字 uid），避免 get_my_uid 不稳定导致崩溃。"""
    if getattr(auth, "uid", None):
        return auth.uid
    ck = getattr(auth, "cookie", None) or {}
    # 优先：uid_tt / sid_tt 是抖音登录态里的账号标识
    # 旧版是 base64（MS4wLjAB...），新版是 32 位 hex 串（uid 的十六进制），
    # 需注意 sid_ucp_v1 可能为风控验证页占位值（如 "1.0.0-..." / "verify_..."）。
    for key in ("uid_tt", "sid_tt", "uid_tt_ss"):
        val = ck.get(key)
        if not val:
            continue
        sval = str(val).strip()
        # 排除风控验证页占位（verify_ 开头 / 纯 "1" / 1.0.0- 前缀）
        if sval.startswith("verify_") or sval in ("1", "0"):
            continue
        _uid = None
        try:
            _uid = int(sval)  # 纯数字 uid
        except Exception:
            pass
        if _uid is None:
            try:
                _uid = int(sval, 16)  # 32 位 hex 串 -> 十进制 uid（新版 cookie）
            except Exception:
                pass
        if _uid and _uid > 1:
            auth.uid = _uid
            logger.info(f"[auth] 从 cookie 字段 {key} 解析到 uid={auth.uid}")
            return auth.uid
    # 兜底：基座 get_my_uid（依赖 s_v_web_id，可能返回 None）
    try:
        from dy_apis.douyin_api import DouyinAPI
        uid = DouyinAPI.get_my_uid(auth)
        if uid:
            auth.uid = int(uid)
            logger.info(f"[auth] 从 get_my_uid 解析到 uid={auth.uid}")
            return auth.uid
    except Exception as e:
        logger.debug(f"[auth] get_my_uid 失败: {e}")
    logger.warning("[auth] 未能解析自身 uid（create_conversation 将失败，"
                   "请确认登录 cookie 含 uid_tt/sid_tt）")
    return None


def save_cookie_to_env(cookie_str, env_path=".env"):
    """把 cookie 写回 .env（DY_COOKIES=...）。enrich_auth 已通过基座写全凭证，
    此函数仅作补充，一般无需调用。"""
    if not cookie_str:
        return
    lines = []
    found = False
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    for i, line in enumerate(lines):
        if line.strip().startswith("DY_COOKIES"):
            lines[i] = f"DY_COOKIES='{cookie_str}'\n"
            found = True
            break
    if not found:
        lines.append(f"DY_COOKIES='{cookie_str}'\n")
    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(lines)
    logger.info(f"[auth] 已将 cookie 写入 {env_path}")


def get_current_auth(user_data_dir="pw_profile_dm", headless=False):
    """构造并返回当前账号（accounts 选中）的已登录 auth。

    供需要「当前登录态」的入口（如 web_bridge 的搜索/点赞/收藏等）复用，
    与 run 启动私信走同一套登录逻辑，避免重复实现。
    返回 (auth, cookie_str)；若当前账号无 cookie 且无法登录则返回 (None, None)。
    """
    try:
        from dotenv import load_dotenv
        from builder.auth import DouyinAuth
        from auto_dm import accounts
    except Exception as e:
        logger.warning(f"[auth] 导入依赖失败: {e}")
        return None, None

    env_path = accounts.current_env_path()
    cookies = ""
    if env_path and os.path.exists(env_path):
        load_dotenv(env_path, override=True)
        cookies = os.getenv("DY_COOKIES", "") or ""
    auth = DouyinAuth()
    if cookies:
        auth.perepare_auth(cookies, "", "")
    auth, cookie_str = enrich_auth(
        auth, cookies_dy=cookies, headless=headless,
        user_data_dir=user_data_dir, env_path=env_path or ".env", force=bool(not cookies))
    ensure_uid(auth)
    if not getattr(auth, "cookie", None):
        logger.warning("[auth] 当前账号无可用的登录态，请在「账号管理」完成登录。")
        return None, None
    return auth, cookie_str
