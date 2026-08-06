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
            env_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))), env_path)
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
    return auth, cookie_str


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
