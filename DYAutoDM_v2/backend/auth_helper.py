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
                user_data_dir="pw_profile_dm", env_path=".env", force=False,
                landing_url="https://www.douyin.com/chat?isPopup=1"):
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
        logger.warning(f"[AUTH-001] " + f"[auth] 无法导入基座 DYLoginApi，跳过签名补全: {e}")
        logger.warning(f"[AUTH-002] " + "[auth] 请先安装依赖：pip install aiohttp（基座 login_api 需要）")
        return auth, cookies_dy

    # 🔴 2026-09-21：凭证永久加密（明文 .env 已废弃）——
    # 不再 load_dotenv 注入 os.environ（那是明文时代的做法，会跨账号污染）；
    # 凭证一律经 member_ctx 按 env_path 精确解密读取。
    if not os.path.isabs(env_path):
        from auto_dm.vbrowser import app_root
        env_path = os.path.join(app_root(), env_path)
    env_path = os.path.abspath(env_path)
    try:
        from services import member_ctx
        if not member_ctx.env_exists(env_path):
            raise FileNotFoundError(f"凭证不存在（明文 .env 已废弃）: {env_path}")
    except FileNotFoundError:
        raise
    except Exception as e:
        logger.warning(f"[AUTH-003] " + f"[auth] 加载 {env_path} 失败: {e}")

    # 解析账号名（env_path -> account name），用于调 BCC
    account_name = None
    if env_path:
        base = os.path.basename(os.path.dirname(os.path.abspath(env_path)))
        if base and base != "accounts":
            account_name = base

    # 优先走 BCC /scan_login（常驻浏览器容器，不抢锁）。
    # 仅在确实需要扫码时调用（force=True 或 .env 缺四件套），避免无谓重启容器
    # context——BCC scan_login 会先关闭自身 context 让 DYLoginApi 独占扫码再重开。
    _needs_scan = bool(force)
    # P3：凭证收敛——静态字段检查委托 verify_credential(lightweight=True)
    if not _needs_scan:
        from auto_dm.accounts import verify_credential as _vc
        _vcr = _vc(account_name or "", lightweight=True)
        if not _vcr["ok"]:
            _needs_scan = True
    if account_name and _needs_scan:
        try:
            from dy_apis.login_api import _bcc_alive, _bcc_post
            if _bcc_alive(account_name):
                r = _bcc_post(account_name, "/scan_login",
                              {"force": bool(force), "timeout": 300}, timeout=300)
                if r.get("ok"):
                    try:
                        _auth = DYLoginApi._load_auth_from_env(env_path)
                    except Exception:
                        _auth = None
                    if _auth and getattr(_auth, "cookie", None):
                        _cks = "; ".join(f"{k}={v}" for k, v in (_auth.cookie or {}).items())
                        ensure_uid(_auth)
                        logger.info("[auth] BCC /scan_login 完成，已从 .env 重载凭证")
                        return _auth, _cks
                    logger.warning(f"[AUTH-004] " + "[auth] BCC /scan_login 返回 ok 但 .env 无 cookie，退回直开浏览器")
                else:
                    logger.warning(f"[AUTH-005] " + f"[auth] BCC /scan_login 返回失败: {r.get('msg', '')}，退回直开浏览器")
        except Exception as e:
            logger.warning(f"[AUTH-006] " + f"[auth] BCC /scan_login 异常，退回直开浏览器: {e}")

    # 后备：直开 Playwright（DYLoginApi.get_login_auth，BCC 未运行/失败时）
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
                            headless=headless, env_path=env_path, force=force,
                            landing_url=landing_url))
                ).result()
        else:
            auth = asyncio.run(
                DYLoginApi().get_login_auth(
                    headless=headless, env_path=env_path, force=force,
                    landing_url=landing_url))
    except Exception as e:
        # 风控/验证码拦截异常：向上透传，让调用方（auto_recapture / scan）能明确提示用户
        # 在指纹浏览器中手动处理验证码，而非被静默吞掉、导致带着污染凭证写回 .env。
        from dy_apis.login_api import RiskControlError
        if isinstance(e, RiskControlError) or (getattr(e, "__cause__", None)
                                               and isinstance(e.__cause__, RiskControlError)):
            raise
        logger.warning(f"[AUTH-007] " + f"[auth] 获取登录凭证失败: {e}")
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
        # 【修复 2026-08-17 16:55】仅当 uid_tt 是【纯十进制数字】且不超过 int64 范围
        # （< 10**19）才直接用作 uid。抖音新版 uid_tt 是 32 位 hex 串（如
        # 75d0502a...），int(sval,16) 转十进制会得到 39 位超 int64 的假值，用它调
        # create_conversation/send_msg 会报 "Value out of range" 且不是真实 uid。
        # 真实十进制 uid 的唯一可靠来源是网络接口 query/user（get_my_uid）。
        _uid = None
        if sval.isdigit():
            try:
                _uid = int(sval)
                if _uid >= 10 ** 19:  # 超 int64 范围，非真实 uid
                    _uid = None
            except (ValueError, TypeError):
                _uid = None
        if _uid and _uid > 1:
            auth.uid = _uid
            logger.info(f"[auth] 从 cookie 字段 {key} 解析到 uid={auth.uid}")
            return auth.uid
    # 兜底：get_my_uid（已修复：走网络接口 query/user 拿真实十进制 uid，
    # 即使 s_v_web_id=verify_ 开头也返回真实 uid）
    try:
        from dy_apis.douyin_api import DouyinAPI
        uid = DouyinAPI.get_my_uid(auth)
        if uid:
            auth.uid = int(uid)
            logger.info(f"[auth] 从 get_my_uid 解析到 uid={auth.uid}")
            return auth.uid
    except Exception as e:
        logger.debug(f"[auth] get_my_uid 失败: {e}")
    logger.warning(f"[AUTH-008] " + "[auth] 未能解析自身 uid（create_conversation 将失败，"
                   "请确认登录 cookie 含 uid_tt/sid_tt）")
    return None


def save_cookie_to_env(cookie_str, env_path=".env"):
    """把 cookie 写回凭证（**只写加密** <env_path>.enc）。

    🔴 2026-09-21：明文 .env 已废弃 —— 统一走 member_ctx.write_env_file。
    """
    if not cookie_str:
        return
    from services import member_ctx
    member_ctx.write_env_file(env_path, {"DY_COOKIES": cookie_str}, merge=True)
    logger.info(f"[auth] 已将 cookie 加密写入 {env_path}.enc")


def get_current_auth(user_data_dir="pw_profile_dm", headless=False):
    """构造并返回当前账号（accounts 选中）的已登录 auth。

    供需要「当前登录态」的入口（如 web_bridge 的搜索/点赞/收藏等）复用，
    与 run 启动私信走同一套登录逻辑，避免重复实现。
    返回 (auth, cookie_str)；若当前账号无 cookie 且无法登录则返回 (None, None)。
    """
    try:
        from builder.auth import DouyinAuth
        from auto_dm import accounts
    except Exception as e:
        logger.warning(f"[AUTH-009] " + f"[auth] 导入依赖失败: {e}")
        return None, None

    env_path = accounts.current_env_path()
    cookies = ""
    # 🔴 2026-09-21：凭证永久加密（明文 .env 已废弃）—— 存在性只认 .enc
    if env_path:
        from services import member_ctx
        if member_ctx.env_exists(env_path):
            cookies = member_ctx.parse_env_dict(env_path).get("DY_COOKIES") or ""
    auth = DouyinAuth()
    if cookies:
        auth.perepare_auth(cookies, "", "")
    auth, cookie_str = enrich_auth(
        auth, cookies_dy=cookies, headless=headless,
        user_data_dir=user_data_dir, env_path=env_path or ".env", force=bool(not cookies))
    ensure_uid(auth)
    if not getattr(auth, "cookie", None):
        logger.warning(f"[AUTH-010] " + "[auth] 当前账号无可用的登录态，请在「账号管理」完成登录。")
        return None, None
    return auth, cookie_str
