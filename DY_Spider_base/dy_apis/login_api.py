import time
import urllib.parse

import aiohttp
import asyncio
import os
from playwright.async_api import async_playwright
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
    async def dyGenerateInitData(self, headless=True, cookie_str=""):
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=headless,
                args=[
                    '--disable-blink-features=AutomationControlled',
                ],
            )
            context = await browser.new_context()
            if cookie_str:
                await context.add_cookies([
                    {"name": part.strip().partition("=")[0],
                     "value": part.strip().partition("=")[2],
                     "domain": ".douyin.com", "path": "/"}
                    for part in cookie_str.split(";") if part.strip()
                ])
            page = await context.new_page()
            await page.goto(self.home_url)
            await page.wait_for_load_state("load")
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
            await browser.close()
            auth = DouyinAuth()
            auth.perepare_auth('', web_protect_str, keys_str)
            auth.cookie = cookies
            auth.cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
            return auth

    # 扫码登录并抓 ticket
    async def login_grab_ticket(self, headless=False, timeout=300, user_data_dir="pw_profile_dm"):
        # 用持久化 profile：已登录则免扫码直接复用；过期才弹码。
        # 关键：监测账号 WS 必须用“真实浏览器会话”建，否则抖音返回 uid=111111 + 昵称加密。
        # 前提（用户确认）：仅当浏览器打开后显示“已登录”时复用 profile 才有效；
        # 若显示未登录，必须等用户真正扫码完成、且 web_protect/keys 抓全后才落盘，
        # 绝不在凭证残缺时写 .env（否则后续弹幕加密 + 私信 INVALID_REQUEST）。
        async with async_playwright() as p:
            context = await p.chromium.launch_persistent_context(
                user_data_dir=user_data_dir,
                headless=headless,
                args=['--disable-blink-features=AutomationControlled'],
            )
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(self.home_url)
            await page.wait_for_load_state("load")
            await asyncio.sleep(2)
            # 若 profile 已带登录态（已登录），跳过扫码直接抓签名
            already_login = False
            try:
                login_btn = await page.evaluate('''() => {
                    const nodes = Array.from(document.querySelectorAll('button, span, div, a'));
                    const hit = nodes.find(n => ['登录','登 录'].includes((n.textContent||'').trim()));
                    return !!hit;
                }''')
                already_login = not login_btn
            except Exception:
                already_login = False
            keys_str = None
            web_protect_str = None
            if not already_login:
                await page.evaluate('''() => {
                    const nodes = Array.from(document.querySelectorAll('button, span, div, a'));
                    const hit = nodes.find(n => ['登录','登 录'].includes((n.textContent||'').trim()));
                    if (hit) hit.click();
                }''')
                logger.info("请在浏览器里扫码登录（超时前请完成扫码，未完成将报错而不是写残缺凭证）")
                deadline = time.time() + timeout
                while time.time() < deadline:
                    await asyncio.sleep(2)
                    try:
                        keys_str = await page.evaluate('localStorage["security-sdk/s_sdk_crypt_sdk"]')
                        web_protect_str = await page.evaluate('localStorage["security-sdk/s_sdk_sign_data_key/web_protect"]')
                    except Exception:
                        continue
                    if keys_str and web_protect_str:
                        break
            else:
                logger.info("[auth] 检测到已登录会话（复用 profile），无需重新扫码")
                deadline = time.time() + timeout
                while time.time() < deadline:
                    await asyncio.sleep(2)
                    try:
                        keys_str = await page.evaluate('localStorage["security-sdk/s_sdk_crypt_sdk"]')
                        web_protect_str = await page.evaluate('localStorage["security-sdk/s_sdk_sign_data_key/web_protect"]')
                    except Exception:
                        continue
                    if keys_str and web_protect_str:
                        break
            if not (keys_str and web_protect_str):
                await context.close()
                raise TimeoutError("登录超时：未抓到完整 web_protect/keys，已放弃，不写入残缺凭证")
            # 私信签名（web_protect）需由抖音 security-sdk 在“私信界面”被触发时，才针对私信场景生成。
            # 仅首页登录抓到的通用 web_protect 调用 imapi 私信接口会被服务端判 INVALID_REQUEST/KICK。
            # 故登录后显式打开私信页，停留让 SDK 重新生成对应场景的 web_protect/keys 后读回覆盖。
            # 注意：此逻辑对应中午 11:12 私信成功发送版本，删除会导致私信再次 KICK，切勿移除。
            try:
                web_protect_str, keys_str = await self._trigger_dm_signature(page, web_protect_str, keys_str)
            except Exception as e:
                logger.warning(f"[auth] 打开私信页触发签名失败（将沿用登录态签名）: {e}")
            cookies = {cookie['name']: cookie['value'] for cookie in await context.cookies()}
            # 最终守卫：任一关键字段缺失都视为残缺，绝不返回（上层 get_login_auth 不会写 .env）
            if not cookies or not web_protect_str or not keys_str:
                await context.close()
                raise RuntimeError(
                    "凭证不完整（cookie/web_protect/keys 任一缺失），拒绝返回残缺 auth，不写 .env")
            await context.close()
            auth = DouyinAuth()
            auth.perepare_auth('', web_protect_str, keys_str)
            auth.cookie = cookies
            auth.cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
            return auth

    async def _trigger_dm_signature(self, page, web_protect_str, keys_str):
        """打开私信页，让 security-sdk 重新生成“私信场景”的 web_protect/keys 并读回覆盖。

        关键事实（来自工作记忆续5 / 中午 11:12 私信成功发送版本）：
        私信签名需由抖音 security-sdk 在“私信界面”被触发时才针对私信场景生成；
        仅首页登录抓到的通用 web_protect 调用 imapi 私信接口会被服务端判
        INVALID_REQUEST / KICK。故登录后（或复用旧凭证时）都必须显式重抓一次。
        """
        logger.info("[auth] 正在打开私信页以触发私信签名生成…")
        wp_before = web_protect_str
        await page.goto("https://www.douyin.com/message", wait_until="domcontentloaded", timeout=30000)
        for _ in range(8):
            await asyncio.sleep(3)
            try:
                wp = await page.evaluate('localStorage["security-sdk/s_sdk_sign_data_key/web_protect"]')
                ks = await page.evaluate('localStorage["security-sdk/s_sdk_crypt_sdk"]')
            except Exception:
                continue
            if wp and ks:
                web_protect_str, keys_str = wp, ks
                break
        if web_protect_str != wp_before:
            logger.info("[auth] 私信签名已刷新（场景化 web_protect 已更新）")
        else:
            logger.info("[auth] 私信签名已就绪（与登录态一致，无需刷新）")
        return web_protect_str, keys_str

    async def _refresh_dm_signature_via_profile(self, auth, env_path, headless=False,
                                                user_data_dir="pw_profile_dm"):
        """凭证有效跳过扫码时，用持久化 profile 免扫码打开私信页刷新私信场景签名。

        对齐中午 11:12 私信成功版本：私信签名必须针对私信场景生成，而仅靠首页通用
        web_protect 必被 imapi 判 KICK。即便 cookie 探活通过、签名四件套齐全，也必须
        重新触发一次私信页签名并写回 .env，否则复用旧签名时私信仍失败。
        由于会话仍有效（已登录态），打开浏览器不会弹码，用户无感。
        """
        try:
            async with async_playwright() as p:
                context = await p.chromium.launch_persistent_context(
                    user_data_dir=user_data_dir,
                    headless=headless,
                    args=['--disable-blink-features=AutomationControlled'],
                )
                page = context.pages[0] if context.pages else await context.new_page()
                web_protect_str = getattr(auth, "ticket", None) and None  # 占位，下面重抓
                keys_str = None
                web_protect_str, keys_str = await self._trigger_dm_signature(page, "", "")
                cookies = {cookie['name']: cookie['value'] for cookie in await context.cookies()}
                await context.close()
            if web_protect_str and keys_str:
                # 把私信场景签名重新解析进 auth 并写回 .env，覆盖旧的通用签名
                auth.perepare_auth(auth.cookie_str or "", web_protect_str, keys_str)
                if cookies:
                    auth.cookie = cookies
                    auth.cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
                self.save_credential(auth, env_path=env_path)
                logger.info("[auth] 私信场景签名已刷新并写回 .env")
            else:
                logger.warning("[auth] 私信页未抓到 web_protect/keys，沿用原签名（私信仍可能 KICK）")
        except Exception as e:
            logger.warning(f"[auth] 免扫码刷新私信签名失败，将沿用原签名: {e}")
        return auth

    # 登录凭证写入 .env
    ENV_FILE = ".env"
    TICKET_KEYS = ("DY_TICKET", "DY_TS_SIGN", "DY_CLIENT_CERT", "DY_PRIVATE_KEY")

    def save_credential(self, auth, env_path=None):
        env_file = env_path or self.ENV_FILE
        cookie_str = "; ".join(f"{k}={v}" for k, v in auth.cookie.items())
        values = {
            "DY_COOKIES": cookie_str,
            "DY_TICKET": auth.ticket or "",
            "DY_TS_SIGN": auth.ts_sign or "",
            "DY_CLIENT_CERT": auth.client_cert or "",
            "DY_PRIVATE_KEY": auth.private_key or "",
        }
        set_values = {k: v for k, v in values.items() if v}
        from dotenv import set_key
        for key, value in set_values.items():
            set_key(env_file, key, value)
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
        auth = DouyinAuth()
        auth.perepare_auth(cookies, "", "")
        auth.ticket = os.getenv("DY_TICKET") or None
        auth.ts_sign = os.getenv("DY_TS_SIGN") or None
        auth.client_cert = os.getenv("DY_CLIENT_CERT") or None
        auth.private_key = os.getenv("DY_PRIVATE_KEY") or None
        return auth

    async def get_login_auth(self, headless=False, env_path=".env", force=False):
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
                        logger.info("[auth] 凭证有效，跳过扫码；但需刷新私信场景签名（对齐 11:12 成功版本）")
                        # 复用旧凭证时也必须重新触发“私信页签名”，否则 imapi 私信接口会 KICK。
                        # 因会话仍有效，用持久化 profile 免扫码打开私信页即可，不会弹码。
                        auth = await self._refresh_dm_signature_via_profile(auth, env_path, headless)
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
        auth = await self.login_grab_ticket(headless=headless)
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