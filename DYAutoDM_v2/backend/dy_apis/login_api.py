import time
import urllib.parse

import aiohttp
import asyncio
import os
import requests
from loguru import logger

from builder.auth import DouyinAuth
from builder.header import HeaderBuilder, HeaderType
from builder.params import Params
from utils.dy_util import generate_ree_key, generate_bd_ticket_client_data
import json
from threading import Thread
import qrcode


class DYLoginApi:

    def __init__(self):
        self.base_url = "https://sso.douyin.com/"
        self.home_url = 'https://www.douyin.com/'

    # 生成初始cookies
    async def dyGenerateInitData(self, headless=True, cookie_str="",
                                  landing_url="https://www.douyin.com/chat?isPopup=1"):
        # 禁止回退原生 Playwright：统一走指纹浏览器内核（should_use_vb 不可用时直接抛错）
        from auto_dm import config as _cfg
        from auto_dm.vbrowser import should_use_vb, launch_async

        _vb, _vb_mode = should_use_vb(_cfg)
        _pw, _browser, context, _backend = await launch_async(
            _vb_mode, _cfg, headless=headless, force=True)
        try:
            if cookie_str:
                await context.add_cookies([
                    {"name": part.strip().partition("=")[0],
                     "value": part.strip().partition("=")[2],
                     "domain": ".douyin.com", "path": "/"}
                    for part in cookie_str.split(";") if part.strip()
                ])
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(self.home_url)
            await page.wait_for_load_state("load")
            # 主动打开私信落地页（默认 chat?isPopup=1），触发 security-sdk 生成【有效】web_protect。
            # 用户实测（2026-08-17）确认 chat?isPopup=1 私信页显示正常、私信功能正常。
            # 崩溃根因在“清空/占用持久化 profile”，已由 launch_async(force=True) 改用临时 profile 解决。
            try:
                await page.goto(landing_url,
                                wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(2)
            except Exception as e:
                logger.warning(f"[auth] 生成初始数据：打开私信落地页({landing_url})失败: {e}")
            keys_str = None
            web_protect_str = None
            for _ in range(6):
                await asyncio.sleep(4)
                await page.mouse.wheel(0, 600)
                keys_str = await page.evaluate('localStorage["security-sdk/s_sdk_crypt_sdk"]')
                web_protect_str = await page.evaluate('localStorage["security-sdk/s_sdk_sign_data_key/web_protect"]')
                if keys_str and web_protect_str:
                    break
            cookies = {cookie['name']: cookie['value'] for cookie in await context.cookies()}
            auth = DouyinAuth()
            auth.perepare_auth('', web_protect_str, keys_str)
            auth.cookie = cookies
            auth.cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
            return auth
        finally:
            if _backend == "exe" and _browser is not None:
                try:
                    await _browser.close()
                except Exception:
                    pass
            if _pw is not None:
                try:
                    await _pw.stop()
                except Exception:
                    pass

    # 扫码登录并抓 ticket
    async def login_grab_ticket(self, headless=False, timeout=300, user_data_dir="pw_profile_dm",
                                env_path=".env", force=False,
                                landing_url="https://www.douyin.com/chat?isPopup=1"):
        """捕获私信签名凭证。

        landing_url: 登录后主动打开私信落地页，触发 security-sdk 把 web_protect 从空壳
            升级为有效值。默认 https://www.douyin.com/chat?isPopup=1（弹窗聊天页）。
            用户实测验证（2026-08-17）：chat?isPopup=1 私信页显示正常、私信功能正常，
            是获取有效 web_protect 的正确落地页。此前因 profile 锁冲突导致浏览器崩溃
            落到 about:blank（日志 "Target page, context or browser has been closed"）
            是【清空持久化 profile / profile 被占用】所致，与主动 goto 私信页无关——
            强制重扫现改用临时 profile 后该问题已解决，可安全恢复主动打开私信落地页。
        """
        # force=True：强制重新扫码，绝不复用 profile 里的旧登录态（避免“着急捕获旧凭证”）。
        # 关键：私信凭证 = security-sdk 的 web_protect/keys。经多方验证（含记忆 53668519 #20），
        #   抖音网页版 security-sdk 在【打开过私信落地页（chat?isPopup=1 或 /message）】之后，
        #   web_protect 才会生成【有效值】，仅停留在首页时拿到的是空壳 → 私信 KICK。
        #   故登录态就绪后必须主动 goto 私信落地页触发 SDK 生成有效签名。
        # 账号隔离：env_path 推导该账号独占的 profile 目录，新增账号用全新指纹封存、不复用他人凭证。
        from auto_dm import config as _cfg
        from auto_dm.vbrowser import should_use_vb, launch_async
        from auto_dm import accounts as _accounts

        _vb, _vb_mode = should_use_vb(_cfg)
        _pw = None          # async_playwright 实例
        _browser = None
        _backend = None      # "exe" / "cdp"（指纹内核；禁止回退原生 Playwright，无 None 降级）
        context = None
        page = None
        # 账号独占 profile：exe 模式用 accounts.profile_dir_of 推导，确保每账号独立封存
        _acc_profile = _accounts.profile_dir_of(env_path)
        # 指纹内核不可用时 should_use_vb 已直接抛错，此处不会再出现“回退原生 Playwright”
        logger.info(f"[auth] 使用指纹浏览器内核接管登录会话 (mode={_vb_mode})")
        logger.info(f"[auth] 账号专属指纹 profile: {_acc_profile}")
        _pw, _browser, context, _backend = await launch_async(
            _vb_mode, _cfg, headless=headless, user_data_dir=_acc_profile, force=force)
        page = context.pages[0] if context.pages else await context.new_page()

        # ===== 以下为指纹内核共用的登录/抓签名流程 =====
        if page is None:
            raise RuntimeError("[auth] 未能获得浏览器页面，登录中止")

        async def _is_real_login(ctx):
            """判定浏览器是否处于“真实登录态”：
            仅当 cookie 中存在 sessionid / sid_tt（抖音真实登录 cookie）才认为已登录。
            注意：web_protect/keys 在首页加载时即由 security-sdk 生成（未登录也会生成空壳），
            不能据此判断登录，否则会出现“未扫码就捕获凭证”的事故。"""
            try:
                ck = {c['name']: c['value'] for c in await ctx.cookies()}
            except Exception:
                return False
            return bool(ck.get("sessionid") or ck.get("sid_tt"))

        def _web_protect_valid(s):
            """校验 web_protect 是有效 JSON 且含关键密钥字段，避免捕获空壳/占位导致私信 KICK。"""
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
            # 有效 web_protect 应含密钥/签名相关字段，空壳通常缺少这些
            blob = json.dumps(obj)
            for key in ("webcast", "sign", "key", "ticket", "token", "salt", "app_id"):
                if key in blob:
                    return True
            return False

        async def _open_message_page():
            """主动打开抖音私信落地页（默认 chat?isPopup=1，用户实测该页显示正常、私信功能正常），
            触发 security-sdk 把 web_protect 从空壳升级为有效值（仅首页拿不到有效签名）。
            """
            try:
                await page.goto(landing_url,
                                wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(2)
            except Exception as e:
                logger.warning(f"[auth] 打开私信落地页({landing_url})失败（将继续重试）: {e}")

        async def _wait_sign_and_login(ctx, deadline):
            """轮询直到：① 真实登录 cookie 出现（用户已扫码）且 ② web_protect 有效。
            二者同时满足才返回 (keys_str, web_protect_str)，否则超时 raise。

            关键时序（对齐用户实测 2026-08-17 + 记忆 53668519 #20）：
            - 浏览器先停留在抖音【首页】等待用户扫码，绝不提前跳私信页；
              否则会出现“还没打开登录窗口就跳走”的事故（用户看不到登录/扫码界面）。
            - 轮询前期先只等待真实登录态（sessionid/sid_tt 出现 = 用户已扫码）；
              未登录前只在首页轮询，不打开私信落地页。
            - 一旦检测到真实登录态，才打开私信落地页（chat?isPopup=1），让 security-sdk
              把 web_protect 从空壳升级为有效值；之后维持在该页轮询 localStorage 直至有效。
            - 崩溃根因不在 goto 私信页，而在“清空/占用持久化 profile 导致浏览器启动失败”；
              该问题已由 vbrowser.launch_async(force=True) 改用临时 profile 修复。
            """
            keys_str = web_protect_str = None
            _logged_in = False
            _msg_opened = False
            while time.time() < deadline:
                await asyncio.sleep(1)
                # 阶段一：等待用户扫码（真实登录态）。未登录前绝不开私信页，停在首页。
                if not _logged_in:
                    if await _is_real_login(ctx):
                        _logged_in = True
                        logger.info("[auth] 检测到真实登录态（已扫码），准备打开私信落地页生成有效 web_protect")
                    else:
                        continue
                # 阶段二：已登录 → 打开一次私信落地页触发 security-sdk 生成有效签名
                if not _msg_opened:
                    await _open_message_page()
                    _msg_opened = True
                try:
                    keys_str = await page.evaluate('localStorage["security-sdk/s_sdk_crypt_sdk"]')
                    web_protect_str = await page.evaluate(
                        'localStorage["security-sdk/s_sdk_sign_data_key/web_protect"]')
                except Exception:
                    keys_str = web_protect_str = None
                # 必须真实登录态（sessionid 出现）+ web_protect 有效 JSON（空壳不算），否则继续等
                if (keys_str and _web_protect_valid(web_protect_str)) and await _is_real_login(ctx):
                    logger.info("[auth] 检测到真实登录态 + 有效 web_protect（私信落地页 security-sdk 已生成）")
                    return keys_str, web_protect_str
            raise TimeoutError(
                "登录超时：未在超时时间内完成扫码并拿到【有效】web_protect+登录态，已放弃，不写入残缺凭证")

        # 用 domcontentloaded 而非 load：抖音首页有持续长连接/轮询，load 事件常延迟触发，
        # 在指纹 chromium 下更易触发 30s 超时；domcontentloaded 已足够后续脚本执行与扫码。
        await page.goto(self.home_url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(1)

        if force:
            # 强制重新扫码：绝不依赖 profile 里的旧 sessionid 判定“已登录”，直接等待本次真实扫码。
            # 若 profile 残留旧登录态导致没出现登录按钮，先尝试点“退出登录/切换账号”不可行，
            # 但抖音首页对已登录用户通常仍会展示头像而非登录按钮；此时改为等待其真实登录 cookie
            # + 有效 web_protect 即可（已登录用户打开私信对话框后 web_protect 会变有效）。
            logger.info("强制重新扫码模式：等待本次真实登录态 + 有效 web_protect（不会复用旧残缺凭证）")
            keys_str, web_protect_str = await _wait_sign_and_login(context, time.time() + timeout)
        else:
            # 非强制：仅当 profile 真实已登录（sessionid 存在）才复用，否则重新扫码
            already_login = await _is_real_login(context)
            if not already_login:
                try:
                    await page.evaluate('''() => {
                        const nodes = Array.from(document.querySelectorAll('button, span, div, a'));
                        const hit = nodes.find(n => ['登录','登 录'].includes((n.textContent||'').trim()));
                        if (hit) hit.click();
                    }''')
                except Exception:
                    pass
                logger.info("请在浏览器里扫码登录（必须先完成扫码、出现真实登录态才捕获凭证；"
                            "未完成将报错而不是写残缺凭证）")
                keys_str, web_protect_str = await _wait_sign_and_login(context, time.time() + timeout)
            else:
                logger.info("[auth] 检测到真实已登录会话（profile 含 sessionid），直接复用并抓取签名")
                try:
                    keys_str, web_protect_str = await _wait_sign_and_login(context, time.time() + 30)
                except TimeoutError:
                    logger.warning("[auth] 已登录会话签名未就绪，降级为重新扫码")
                    try:
                        await page.evaluate('''() => {
                            const nodes = Array.from(document.querySelectorAll('button, span, div, a'));
                            const hit = nodes.find(n => ['登录','登 录'].includes((n.textContent||'').trim()));
                            if (hit) hit.click();
                        }''')
                    except Exception:
                        pass
                    keys_str, web_protect_str = await _wait_sign_and_login(context, time.time() + timeout)

        if not (keys_str and _web_protect_valid(web_protect_str)):
            if _backend == "exe":
                await context.close()
            raise TimeoutError("登录超时：未抓到完整/有效 web_protect/keys，已放弃，不写入残缺凭证")

        cookies = {cookie['name']: cookie['value'] for cookie in await context.cookies()}
        # 最终守卫：必须含真实登录 cookie + 有效签名，否则绝不返回（上层不会写 .env）
        if not (cookies.get("sessionid") or cookies.get("sid_tt")) or not _web_protect_valid(web_protect_str) or not keys_str:
            if _backend == "exe":
                await context.close()
            raise RuntimeError(
                "凭证不完整（缺少真实登录 cookie 或有效 web_protect/keys），拒绝返回残缺 auth，不写 .env")
        if _backend == "exe":
            await context.close()
        auth = DouyinAuth()
        auth.perepare_auth('', web_protect_str, keys_str)
        auth.cookie = cookies
        auth.cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
        # 把原始 web_protect/keys JSON 挂到 auth 上，供 save_credential 持久化到 .env，
        # 后续复用凭证时能完整还原签名（含 ree_public_key 等派生字段），私信不再缺签名。
        auth.web_protect_str = web_protect_str
        auth.keys_str = keys_str
        return auth

    # 登录凭证写入 .env
    ENV_FILE = ".env"
    TICKET_KEYS = ("DY_TICKET", "DY_TS_SIGN", "DY_CLIENT_CERT", "DY_PRIVATE_KEY")

    @staticmethod
    def _encode_private_key(pem):
        """把 PEM 私钥里的真实换行替换成字面量 \\n，使其能安全存入单引号 .env 而不破坏 dotenv 解析。"""
        if not pem:
            return ""
        return pem.replace("\r\n", "\n").replace("\n", "\\n")

    @staticmethod
    def _decode_private_key(value):
        """读回时把 .env 中的字面量 \\n 还原为真实换行，恢复合法 PEM 格式。"""
        if not value:
            return None
        return value.replace("\\n", "\n")

    def save_credential(self, auth, env_path=None):
        env_file = env_path or self.ENV_FILE
        cookie_str = "; ".join(f"{k}={v}" for k, v in auth.cookie.items())
        values = {
            "DY_COOKIES": cookie_str,
            "DY_TICKET": auth.ticket or "",
            "DY_TS_SIGN": auth.ts_sign or "",
            "DY_CLIENT_CERT": auth.client_cert or "",
            # PEM 含换行，必须编码为字面量 \n 才能安全存入单引号 .env（否则 dotenv 解析失败）
            "DY_PRIVATE_KEY": self._encode_private_key(auth.private_key),
            # 原始 web_protect/keys（私信签名 JSON）持久化：复用凭证时完整还原签名，
            # 避免仅存四件套导致 ree_public_key 等派生字段缺失、create_conversation 缺签名。
            "DY_WEB_PROTECT": getattr(auth, "web_protect_str", "") or "",
            "DY_KEYS": getattr(auth, "keys_str", "") or "",
        }
        set_values = {k: v for k, v in values.items() if v}
        from dotenv import set_key
        for key, value in set_values.items():
            set_key(env_file, key, value, quote_mode="never")
        logger.debug(f"[auth] 凭证已写回 {env_file}（DY_PRIVATE_KEY 已编码换行）")
        return os.path.abspath(env_file)

    @staticmethod
    def _load_auth_from_env(env_path):
        """从指定 .env 读取并构造 DouyinAuth（替代 common_util.load_env 的写死路径）。"""
        from dotenv import load_dotenv
        from builder.auth import DouyinAuth
        if env_path and env_path != ".env":
            load_dotenv(env_path, override=True)
        else:
            load_dotenv(override=True)
        cookies = os.getenv("DY_COOKIES")
        web_protect = os.getenv("DY_WEB_PROTECT") or ""
        keys = os.getenv("DY_KEYS") or ""
        auth = DouyinAuth()
        # 优先用持久化的 web_protect/keys（完整还原签名，含 ree_public_key 等派生字段）；
        # 旧 .env 无这两个键时退回仅四件套。
        auth.perepare_auth(cookies, web_protect, keys)
        if not (web_protect and keys):
            auth.ticket = os.getenv("DY_TICKET") or None
            auth.ts_sign = os.getenv("DY_TS_SIGN") or None
            auth.client_cert = os.getenv("DY_CLIENT_CERT") or None
            auth.private_key = DYLoginApi._decode_private_key(os.getenv("DY_PRIVATE_KEY"))
            # 补齐 ree_public_key（perepare_auth 用 web_protect/keys 时才派生；
            # 旧 .env 无 web_protect/keys 键时手动补，避免私信签名缺字段）
            if auth.private_key:
                import base64 as _b64
                auth.ree_public_key = _b64.b64encode(auth.private_key.encode()).decode()
        # 补 cookie_str，供 IM 私有网关 with_csrf 注入 x-secsdk-csrf-token 头（抖音要求）
        if auth.cookie and not getattr(auth, "cookie_str", None):
            auth.cookie_str = "; ".join(f"{k}={v}" for k, v in auth.cookie.items())
        return auth

    async def get_login_auth(self, headless=False, env_path=".env", force=False,
                             landing_url="https://www.douyin.com/chat?isPopup=1"):
        """优先从 env_path 指定的 .env 读 ticket，没有或已失效就扫码登录后写入该 .env。

        多账号切换：env_path 指向当前选中账号的 .env，扫码凭证只写回该账号文件。

        关键修正：之前只判断 ticket 字符串“是否存在”，失效的 ticket 同样会
        被当成“已登录”直接返回，导致永远不触发扫码（用户看不到二维码）。
        现在改为：有 ticket 且用 get_my_uid 探活通过才跳过；否则强制重扫。

        force=True 时忽略现有凭证，直接重新打开浏览器扫码（GUI“重新扫码”按钮用）。
        """
        from dy_apis.douyin_api import DouyinAPI
        if not force:
            auth = self._load_auth_from_env(env_path)
            # 私信签名四件套（ticket/ts_sign/client_cert/private_key）必须齐全，否则私信接口必失败
            _has_sign = auth.ticket and auth.ts_sign and auth.client_cert and auth.private_key
            if auth.cookie and _has_sign:
                # 浅校验：有 cookie 且能拿到自己的 uid，才算有效；否则视为失效重扫
                try:
                    if DouyinAPI.get_my_uid(auth):
                        logger.info("[auth] 凭证有效，跳过扫码（私信签名来自首页 security-sdk 自动生成，无需打开私信页）")
                        return auth
                except Exception as e:
                    logger.warning(f"[auth] 已有凭证但校验失败，将重新扫码: {e}")
            elif auth.cookie and not _has_sign:
                logger.warning("[auth] 登录 cookie 存在但私信签名缺失，将重新扫码以抓取 web_protect/keys")
        else:
            logger.info("[auth] 强制重新扫码（忽略现有凭证）…")
        logger.info("[auth] 打开浏览器扫码登录…")
        # —— 扫码前快照旧凭证（磁盘上 .env 的当前值），供扫码后做“旧→新”捕获对比 ——
        from auto_dm.login_capture import snapshot_old_env, analyze_login_capture
        old_snap = snapshot_old_env(env_path)
        auth = await self.login_grab_ticket(headless=headless, env_path=env_path, force=force,
                                             landing_url=landing_url)
        # —— 写回 .env 前，先比对旧→新并生成捕获分析报告（你扫码，程序自动分析）——
        analyze_login_capture(auth, old_snap, env_path)
        logger.info(f"登录凭证已存 {self.save_credential(auth, env_path=env_path)}")
        return auth

    # 获取二维码
    def dyGenerateQRcode(self, auth) -> dict:
        api = f"get_qrcode/"
        headers = HeaderBuilder().build(HeaderType.GET)
        headers.set_referer("https://www.douyin.com/")
        params = Params()
        params.add_param("service", 'https://www.douyin.com')
        params.add_param("need_logo", 'false')
        params.add_param("need_short_url", 'false')
        params.add_param("passport_jssdk_version", "1.0.26")
        params.add_param("passport_jssdk_type", "pro")
        params.add_param("aid", '6383')
        params.add_param("language", 'zh')
        params.add_param("account_sdk_source", 'sso')
        params.add_param("account_sdk_source_info", "7e276d64776172647760466a6b66707777606b667c273f3735292772606761776c736077273f63646976602927666d776a686061776c736077273f63646976602927766d60696961776c736077273f63646976602927756970626c6b76273f302927756077686c76766c6a6b76273f5e7e276b646860273f276b6a716c636c6664716c6a6b762729277671647160273f2775776a68757127785829276c6b6b60774d606c626d71273f3431313729276c6b6b6077526c61716d273f3436363129276a707160774d606c626d71273f3430303729276a70716077526c61716d273f37303335292776716a64776260567164717076273f7e276c6b61607d60614147273f7e276c6167273f276a676f6066712729276a75606b273f2763706b66716c6a6b2729276c6b61607d60614147273f276a676f6066712729274c41474e607c57646b6260273f2763706b66716c6a6b2729276a75606b4164716467647660273f27706b6160636c6b60612729276c7656646364776c273f636469766029276d6476436071666d273f6364697660782927696a66646956716a77646260273f7e276c76567075756a77714956716a77646260273f717770602927766c7f60273f3337313c32292772776c7160273f7177706078292776716a7764626054706a7164567164717076273f7e277076646260273f343031323236292774706a7164273f34373d3d313c33313030333d29276c7655776c73647160273f6364697660787829276b6a716c636c6664716c6a6b556077686c76766c6a6b273f2761606364706971272927756077636a7768646b6660273f7e27716c68604a776c626c6b273f3432373635343636303c3131372b362927707660614f564d606475566c7f60273f3437333c373c32343529276b64736c6264716c6a6b516c686c6b62273f7e276160666a616061476a617c566c7f60273f3035333434322927606b71777c517c7560273f276b64736c6264716c6a6b2729276c6b6c716c64716a77517c7560273f276b64736c6264716c6a6b2729276b646860273f276d717175763f2a2a7272722b616a707c6c6b2b666a682a707660772a48563172496f4447444444444075684d363131466e46723748303d513636543d5170437561734f764a7c645f6667527d444866334d3536724a534363344a72316855553c315141505631507627292777606b61607747696a666e6c6b62567164717076273f276b6a6b2867696a666e6c6b62272927766077736077516c686c6b62273f276c6b6b60772971715a6462722966616b286664666d602960616260296a776c626c6b272927627069605671647771273f343d3d3d2b3029276270696041707764716c6a6b273f34362b363c3c3c3c3c3c323334303d34313778782927776074706076715a6d6a7671273f277272722b616a707c6c6b2b666a68272927776074706076715a7564716d6b646860273f272a707660772a48563172496f4447444444444075684d363131466e46723748303d513636543d5170437561734f764a7c645f6667527d444866334d3536724a534363344a72316855553c31514150563150762778")
        params.add_param("passport_ztsdk", '3.0.20')
        params.add_param("passport_verify", '1.0.17')
        params.add_param("device_platform", 'web_app')
        params.add_param("msToken", auth.cookie['msToken'])
        params.with_a_bogus()
        resp = requests.get(self.base_url + api, headers=headers.get(), cookies=auth.cookie, params=params.get(), verify=False)
        return json.loads(resp.text)


    def dyCheckQrCodeLogin(self, auth, token):
        api = 'check_qrconnect/'
        headers = HeaderBuilder().build(HeaderType.GET)
        headers.set_referer("https://www.douyin.com/")
        params = Params()
        params.add_param("service", 'https://www.douyin.com')
        params.add_param("token", token)
        params.add_param("need_logo", 'false')
        params.add_param("is_frontier", 'false')
        params.add_param("need_short_url", 'false')
        params.add_param("passport_jssdk_version", "1.0.26")
        params.add_param("passport_jssdk_type", "pro")
        params.add_param("aid", '6383')
        params.add_param("language", 'zh')
        params.add_param("account_sdk_source", 'sso')
        params.add_param("account_sdk_source_info", "7e276d64776172647760466a6b66707777606b667c273f3735292772606761776c736077273f63646976602927666d776a686061776c736077273f63646976602927766d60696961776c736077273f63646976602927756970626c6b76273f302927756077686c76766c6a6b76273f5e7e276b646860273f276b6a716c636c6664716c6a6b762729277671647160273f2775776a68757127785829276c6b6b60774d606c626d71273f3431313729276c6b6b6077526c61716d273f3436363129276a707160774d606c626d71273f3430303729276a70716077526c61716d273f37303335292776716a64776260567164717076273f7e276c6b61607d60614147273f7e276c6167273f276a676f6066712729276a75606b273f2763706b66716c6a6b2729276c6b61607d60614147273f276a676f6066712729274c41474e607c57646b6260273f2763706b66716c6a6b2729276a75606b4164716467647660273f27706b6160636c6b60612729276c7656646364776c273f636469766029276d6476436071666d273f6364697660782927696a66646956716a77646260273f7e276c76567075756a77714956716a77646260273f717770602927766c7f60273f3337313c32292772776c7160273f7177706078292776716a7764626054706a7164567164717076273f7e277076646260273f343031323236292774706a7164273f34373d3d313c33313030333d29276c7655776c73647160273f6364697660787829276b6a716c636c6664716c6a6b556077686c76766c6a6b273f2761606364706971272927756077636a7768646b6660273f7e27716c68604a776c626c6b273f3432373635343636303c3131372b362927707660614f564d606475566c7f60273f3437333c373c32343529276b64736c6264716c6a6b516c686c6b62273f7e276160666a616061476a617c566c7f60273f3035333434322927606b71777c517c7560273f276b64736c6264716c6a6b2729276c6b6c716c64716a77517c7560273f276b64736c6264716c6a6b2729276b646860273f276d717175763f2a2a7272722b616a707c6c6b2b666a682a707660772a48563172496f4447444444444075684d363131466e46723748303d513636543d5170437561734f764a7c645f6667527d444866334d3536724a534363344a72316855553c315141505631507627292777606b61607747696a666e6c6b62567164717076273f276b6a6b2867696a666e6c6b62272927766077736077516c686c6b62273f276c6b6b60772971715a6462722966616b286664666d602960616260296a776c626c6b272927627069605671647771273f343d3d3d2b3029276270696041707764716c6a6b273f34362b363c3c3c3c3c3c323334303d34313778782927776074706076715a6d6a7671273f277272722b616a707c6c6b2b666a68272927776074706076715a7564716d6b646860273f272a707660772a48563172496f4447444444444075684d363131466e46723748303d513636543d5170437561734f764a7c645f6667527d444866334d3536724a534363344a72316855553c31514150563150762778")
        params.add_param("passport_ztsdk", '3.0.20')
        params.add_param("passport_verify", '1.0.17')
        params.add_param("biz_trace_id", auth.cookie['biz_trace_id'])
        params.add_param("device_platform", 'web_app')
        params.add_param("msToken", auth.cookie['msToken'])
        params.with_a_bogus()
        resp = requests.get(self.base_url + api, headers=headers.get(), cookies=auth.cookie, params=params.get(), verify=False)
        return json.loads(resp.text)

    # 手机验证码登录
    def dyGeneratePhoneVerificationCode(self, phone_num, auth):
        api = "send_activation_code/v2/"
        headers = {
            "accept": "application/json, text/javascript",
            "accept-language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
            "cache-control": "no-cache",
            "content-type": "application/x-www-form-urlencoded",
            "origin": "https://www.douyin.com",
            "pragma": "no-cache",
            "priority": "u=1, i",
            "referer": "https://www.douyin.com/",
            "sec-ch-ua": "\"Not)A;Brand\";v=\"99\", \"Microsoft Edge\";v=\"127\", \"Chromium\";v=\"127\"",
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": "\"Windows\"",
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-site",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/117.0",
            "x-tt-passport-csrf-token": auth.cookie['passport_csrf_token'],
            "x-tt-passport-trace-id": auth.cookie['biz_trace_id']
        }
        params = Params()
        params.add_param("passport_jssdk_version", "1.0.26")
        params.add_param("passport_jssdk_type", "pro")
        params.add_param("aid", "6383")
        params.add_param("language", "zh")
        params.add_param("account_sdk_source", "sso")
        params.add_param("account_sdk_source_info", "7e276d64776172647760466a6b66707777606b667c273f3735292772606761776c736077273f63646976602927666d776a686061776c736077273f63646976602927766d60696961776c736077273f63646976602927756970626c6b76273f302927756077686c76766c6a6b76273f5e7e276b646860273f276b6a716c636c6664716c6a6b762729277671647160273f2775776a68757127785829276c6b6b60774d606c626d71273f3431313729276c6b6b6077526c61716d273f3434313129276a707160774d606c626d71273f3430303729276a70716077526c61716d273f37303335292776716a64776260567164717076273f7e276c6b61607d60614147273f7e276c6167273f276a676f6066712729276a75606b273f2763706b66716c6a6b2729276c6b61607d60614147273f276a676f6066712729274c41474e607c57646b6260273f2763706b66716c6a6b2729276a75606b4164716467647660273f27706b6160636c6b60612729276c7656646364776c273f636469766029276d6476436071666d273f6364697660782927696a66646956716a77646260273f7e276c76567075756a77714956716a77646260273f717770602927766c7f60273f363d333433292772776c7160273f7177706078292776716a7764626054706a7164567164717076273f7e277076646260273f3135323c3d292774706a7164273f34373d3d313c33313030333d29276c7655776c73647160273f6364697660787829276b6a716c636c6664716c6a6b556077686c76766c6a6b273f2761606364706971272927756077636a7768646b6660273f7e27716c68604a776c626c6b273f34323736353734333d3d3c3d302b342927707660614f564d606475566c7f60273f34313331323c37303129276b64736c6264716c6a6b516c686c6b62273f7e276160666a616061476a617c566c7f60273f323537303c362927606b71777c517c7560273f276b64736c6264716c6a6b2729276c6b6c716c64716a77517c7560273f276b64736c6264716c6a6b2729276b646860273f276d717175763f2a2a7272722b616a707c6c6b2b666a682a3a7760666a6868606b61383427292777606b61607747696a666e6c6b62567164717076273f276b6a6b2867696a666e6c6b62272927766077736077516c686c6b62273f276c6b6b60772971715a6462722966616b286664666d602960616260296a776c626c6b272927627069605671647771273f343333362b3635353535353532343037303329276270696041707764716c6a6b273f276b6a6b602778782927776074706076715a6d6a7671273f277272722b616a707c6c6b2b666a68272927776074706076715a7564716d6b646860273f272a2778")
        params.add_param("passport_ztsdk", "3.0.20")
        params.add_param("passport_verify", "1.0.17")
        params.add_param("biz_trace_id", auth.cookie['biz_trace_id'])
        params.add_param("device_platform", "web_app")
        params.add_param("msToken", auth.cookie['msToken'])
        data = generateSecretPhoneNum(phone_num)
        params.with_a_bogus(data)
        response = requests.post(self.base_url + api, headers=headers, cookies=auth.cookie, params=params.get(), data=data, verify=False)
        res_json = json.loads(response.text)
        if res_json['error_code'] == 0:
            print("无需过滑块, 验证码发送成功")
            return res_json

        firstLoginRes = json.loads(response.text)
        iframeTemplate = self.generateIframe(auth.cookie, firstLoginRes)
        print(iframeTemplate)
        input('过滑块')
        # 过验证码后
        params.add_param("fp", auth.cookie['s_v_web_id'])
        params.add_param("verifyFp", auth.cookie['s_v_web_id'])
        response = requests.post(self.base_url + api, headers=headers, cookies=auth.cookie, params=params.get(), data=data, verify=False)
        return json.loads(response.text)

    def dyPhoneVerificationCodeLogin(self, auth, phone_num, code):
        headers = {
            "accept": "application/json, text/javascript",
            "accept-language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
            "bd-ticket-guard-iteration-version": "1",
            "bd-ticket-guard-ree-public-key": generate_ree_key(auth.private_key),
            "bd-ticket-guard-version": "2",
            "bd-ticket-guard-web-version": "1",
            "cache-control": "no-cache",
            "content-type": "application/x-www-form-urlencoded",
            "origin": "https://www.douyin.com",
            "pragma": "no-cache",
            "priority": "u=1, i",
            "referer": "https://www.douyin.com/",
            "sec-ch-ua": "\"Not)A;Brand\";v=\"99\", \"Microsoft Edge\";v=\"127\", \"Chromium\";v=\"127\"",
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": "\"Windows\"",
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-site",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/117.0",
            "x-tt-passport-csrf-token": auth.cookie['passport_csrf_token'],
            "x-tt-passport-trace-id": auth.cookie['biz_trace_id']
        }
        api = "quick_login/v2/"
        params = Params()
        params.add_param("passport_jssdk_version", "1.0.26")
        params.add_param("passport_jssdk_type", "pro")
        params.add_param("aid", "6383")
        params.add_param("language", "zh")
        params.add_param("account_sdk_source", "sso")
        params.add_param("account_sdk_source_info", "7e276d64776172647760466a6b66707777606b667c273f3735292772606761776c736077273f63646976602927666d776a686061776c736077273f63646976602927766d60696961776c736077273f63646976602927756970626c6b76273f302927756077686c76766c6a6b76273f5e7e276b646860273f276b6a716c636c6664716c6a6b762729277671647160273f2775776a68757127785829276c6b6b60774d606c626d71273f3431313729276c6b6b6077526c61716d273f3434313129276a707160774d606c626d71273f3430303729276a70716077526c61716d273f37303335292776716a64776260567164717076273f7e276c6b61607d60614147273f7e276c6167273f276a676f6066712729276a75606b273f2763706b66716c6a6b2729276c6b61607d60614147273f276a676f6066712729274c41474e607c57646b6260273f2763706b66716c6a6b2729276a75606b4164716467647660273f27706b6160636c6b60612729276c7656646364776c273f636469766029276d6476436071666d273f6364697660782927696a66646956716a77646260273f7e276c76567075756a77714956716a77646260273f717770602927766c7f60273f363d333433292772776c7160273f7177706078292776716a7764626054706a7164567164717076273f7e277076646260273f3135323c3d292774706a7164273f34373d3d313c33313030333d29276c7655776c73647160273f6364697660787829276b6a716c636c6664716c6a6b556077686c76766c6a6b273f2761606364706971272927756077636a7768646b6660273f7e27716c68604a776c626c6b273f34323736353734333d3d3c3d302b342927707660614f564d606475566c7f60273f34313331323c37303129276b64736c6264716c6a6b516c686c6b62273f7e276160666a616061476a617c566c7f60273f323537303c362927606b71777c517c7560273f276b64736c6264716c6a6b2729276c6b6c716c64716a77517c7560273f276b64736c6264716c6a6b2729276b646860273f276d717175763f2a2a7272722b616a707c6c6b2b666a682a3a7760666a6868606b61383427292777606b61607747696a666e6c6b62567164717076273f276b6a6b2867696a666e6c6b62272927766077736077516c686c6b62273f276c6b6b60772971715a6462722966616b286664666d602960616260296a776c626c6b272927627069605671647771273f343333362b3635353535353532343037303329276270696041707764716c6a6b273f276b6a6b602778782927776074706076715a6d6a7671273f277272722b616a707c6c6b2b666a68272927776074706076715a7564716d6b646860273f272a2778")
        params.add_param("passport_ztsdk", "3.0.20")
        params.add_param("passport_verify", "1.0.17")
        params.add_param("biz_trace_id", auth.cookie['biz_trace_id'])
        params.add_param("device_platform", "web_app")
        params.add_param("msToken", auth.cookie['msToken'])
        params.with_a_bogus()
        data = generateSecretCode(phone_num, code)
        response = requests.post(self.base_url + api, headers=headers, cookies=auth.cookie, params=params.get(), data=data, verify=False)
        responseCookies = response.cookies.get_dict()
        # 结合到cookies中
        auth.cookie.update(responseCookies)
        return json.loads(response.text), auth

    def generateIframe(self, cookies, firstLoginRes):
        verify_center_decision_conf = json.loads(firstLoginRes['verify_center_decision_conf'])
        url = r'https://rmc.bytedance.com/verifycenter/captcha/v2?from=iframe&fp=' + cookies["s_v_web_id"] + '&env={"screen":{"w":2560,"h":1600},"browser":{"w":2560,"h":1552},"page":{"w":1166,"h":1442},"document":{"width":1166},"product_host":"www.douyin.com","vc_version":"1.0.0.100","maskTime":' + str(int(time.time()) * 1000) + ',"h5_check_version":"3.8.6"}&aid=6383&repoId=579047&scene_level=p2&app_name=抖音 Web 站&host=https://verify.zijieapi.com&lang=zh&verify_data={"code":"10000","from":"shark_admin","type":"verify","version":"1","region":"cn","subtype":"slide","ui_type":"","detail":"' + verify_center_decision_conf["detail"] + '","verify_event":"tt_sso_send_code","fp":"' + cookies["s_v_web_id"] + '","server_sdk_env":"{\\"idc\\":\\"lq\\",\\"region\\":\\"CN\\",\\"server_type\\":\\"passport\\"}","log_id":"' + verify_center_decision_conf["log_id"] + '","is_assist_mobile":false,"is_complex_sms":false,"identity_action":"","identity_scene":"","verify_scene":"passport","login_status":0,"aid":0,"mfa_decision":""}'
        url = self.quoteUrl(url)
        iframeTemplate = f'<iframe src="{url}" style="z-index: 999;border: none;display: block;visibility: visible;border-radius: 6px;overflow: hidden;position: absolute;left: 50%;top: 50%;transform: translate(-50%, -50%);width: 380px;height: 384px;"></iframe>'
        return iframeTemplate

    def quoteUrl(self, url):
        parsed = urllib.parse.urlparse(url)
        params = urllib.parse.parse_qs(parsed.query)
        new_url = ''
        for k, v in params.items():
            i = f'{k}={v[0]}&'
            if k == 'host':
                new_url += requests.utils.quote(i, safe='?=&')
            else:
                new_url += requests.utils.quote(i, safe='/?=&*')
        return parsed.scheme + '://' + parsed.netloc + parsed.path + '?' + new_url[:-1]

    def persistenceLoginInfo(self, auth):
        url = "https://www.douyin.com/passport/user/web_record_status/set/"
        api = "passport/user/web_record_status/set/"
        headers = {
            "accept": "application/json, text/javascript",
            "accept-language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
            # "bd-ticket-guard-client-data": generate_bd_ticket_client_data(api, auth.ticket, auth.ts_sign, auth.private_key),
            "bd-ticket-guard-iteration-version": "1",
            "bd-ticket-guard-ree-public-key": generate_ree_key(auth.private_key),
            "bd-ticket-guard-version": "2",
            "bd-ticket-guard-web-version": "1",
            "cache-control": "no-cache",
            "content-type": "application/x-www-form-urlencoded",
            "pragma": "no-cache",
            "priority": "u=1, i",
            "referer": "https://www.douyin.com/video/7212619184386182435",
            "sec-ch-ua": "\"Not)A;Brand\";v=\"99\", \"Microsoft Edge\";v=\"127\", \"Chromium\";v=\"127\"",
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": "\"Windows\"",
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/117.0",
            "x-tt-passport-csrf-token": "07e71018d12ee15b8a50b086cd82d021",
            "x-tt-passport-trace-id": "5714b00b"
        }
        params = Params()
        params.add_param("user_web_record_status", "1")
        params.add_param("passport_jssdk_version", "1.0.26")
        params.add_param("passport_jssdk_type", "pro")
        params.add_param("aid", "6383")
        params.add_param("language", "zh")
        params.add_param("account_sdk_source", "web")
        params.add_param("account_sdk_source_info", "7e276d64776172647760466a6b66707777606b667c273f3735292772606761776c736077273f63646976602927666d776a686061776c736077273f63646976602927766d60696961776c736077273f63646976602927756970626c6b76273f302927756077686c76766c6a6b76273f5e7e276b646860273f276b6a716c636c6664716c6a6b762729277671647160273f2775776a68757127785829276c6b6b60774d606c626d71273f3431313729276c6b6b6077526c61716d273f3434313129276a707160774d606c626d71273f3430303729276a70716077526c61716d273f37303335292776716a64776260567164717076273f7e276c6b61607d60614147273f7e276c6167273f276a676f6066712729276a75606b273f2763706b66716c6a6b2729276c6b61607d60614147273f276a676f6066712729274c41474e607c57646b6260273f2763706b66716c6a6b2729276a75606b4164716467647660273f27706b6160636c6b60612729276c7656646364776c273f636469766029276d6476436071666d273f6364697660782927696a66646956716a77646260273f7e276c76567075756a77714956716a77646260273f717770602927766c7f60273f363d333433292772776c7160273f7177706078292776716a7764626054706a7164567164717076273f7e277076646260273f3135323c3d292774706a7164273f34373d3d313c33313030333d29276c7655776c73647160273f6364697660787829276b6a716c636c6664716c6a6b556077686c76766c6a6b273f2761606364706971272927756077636a7768646b6660273f7e27716c68604a776c626c6b273f34323736353734333d3d3c3d302b342927707660614f564d606475566c7f60273f34313331323c37303129276b64736c6264716c6a6b516c686c6b62273f7e276160666a616061476a617c566c7f60273f323537303c362927606b71777c517c7560273f276b64736c6264716c6a6b2729276c6b6c716c64716a77517c7560273f276b64736c6264716c6a6b2729276b646860273f276d717175763f2a2a7272722b616a707c6c6b2b666a682a3a7760666a6868606b61383427292777606b61607747696a666e6c6b62567164717076273f276b6a6b2867696a666e6c6b62272927766077736077516c686c6b62273f276c6b6b60772971715a6462722966616b286664666d602960616260296a776c626c6b272927627069605671647771273f343333362b3635353535353532343037303329276270696041707764716c6a6b273f276b6a6b602778782927776074706076715a6d6a7671273f277272722b616a707c6c6b2b666a68272927776074706076715a7564716d6b646860273f272a2778")
        params.add_param("passport_ztsdk", "3.0.20")
        params.add_param("passport_verify", "1.0.17")
        params.add_param("biz_trace_id", auth.cookie['biz_trace_id'])
        params.add_param("device_platform", "web_app")
        params.add_param("msToken", auth.cookie['msToken'])
        params.with_a_bogus()
        response = requests.get(url, headers=headers, cookies=auth.cookie, params=params.get(), verify=False)
        auth.cookie.update(response.cookies.get_dict())
        return json.loads(response.text)


    # ==========================
    def generateQrcode(self, verify_url):
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=10,
            border=4,
        )
        qr.add_data(verify_url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        img.show()

    async def qrcodeMain(self):
        auth = await self.dyGenerateInitData()
        qrCodeDict = self.dyGenerateQRcode(auth)
        token = qrCodeDict['data']['token']
        verify_url = qrCodeDict['data']['qrcode_index_url']
        qrcode_thread = Thread(target=self.generateQrcode, args=(verify_url,))
        qrcode_thread.start()
        while True:
            checkLoginInfo = self.dyCheckQrCodeLogin(auth, token)
            print(checkLoginInfo)
            await asyncio.sleep(10)


    async def phoneMain(self):
        auth = await self.dyGenerateInitData()
        phone_num = "15251991681"
        sendCodeRes = self.dyGeneratePhoneVerificationCode(phone_num, auth)
        print(sendCodeRes)
        code = input("请输入验证码：")
        loginRes, auth = self.dyPhoneVerificationCodeLogin(auth, phone_num, code)
        print(loginRes)
        redirect_url = loginRes['redirect_url']
        print(redirect_url)
        headers = {
            "accept": "application/json, text/plain, */*",
            "accept-language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
            "cache-control": "no-cache",
            "pragma": "no-cache",
            "priority": "u=1, i",
            "referer": "https://www.douyin.com/?recommend=1",
            "sec-ch-ua": "\"Not)A;Brand\";v=\"99\", \"Microsoft Edge\";v=\"127\", \"Chromium\";v=\"127\"",
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": "\"Windows\"",
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/117.0",
        }
        response = requests.get(redirect_url, headers=headers, cookies=auth.cookie, verify=False)
        print(response.status_code)
        if response.status_code == 302:
            print(response.headers)
            location = response.headers['Location']
            response = requests.get(location, headers=headers, cookies=auth.cookie, verify=False)
            auth.cookie.update(response.cookies.get_dict())
            if response.status_code == 302:
                print(response.headers)
                location = response.headers['Location']
                response = requests.get(location, headers=headers, cookies=auth.cookie, verify=False)
                auth.cookie.update(response.cookies.get_dict())

        res = self.persistenceLoginInfo(auth)
        print(res)
        # 将cookie转为字符串
        cookie_str = ''
        for k, v in auth.cookie.items():
            cookie_str += k + '=' + v + '; '
        cookie_str = cookie_str[:-2]
        print(cookie_str)

if __name__ == '__main__':
    login_util = DYLoginApi()
    loop = asyncio.get_event_loop()
    # loop.run_until_complete(login_util.qrcodeMain())
    loop.run_until_complete(login_util.phoneMain())