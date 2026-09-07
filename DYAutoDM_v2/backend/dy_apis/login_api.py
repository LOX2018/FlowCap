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


# ---------------------------------------------------------------------------
# BCC（浏览器容器）HTTP 客户端辅助
# 每账号一个常驻 BCC（dyautodm-browser-daemon），持有 profile 的唯一 Playwright
# context。调用方通过 HTTP 调 BCC 接口，不再各自 launch_persistent_context 抢锁。
# ---------------------------------------------------------------------------

def _bcc_port(account_name: str) -> int:
    """该账号的 BCC（browser_daemon）专属端口。"""
    from auto_dm import accounts as _acc
    return _acc.browser_daemon_port(account_name)


def _bcc_alive(account_name: str, timeout: float = 0.5) -> bool:
    """探测该账号的 BCC 是否在运行（端口开 + /status alive + 账号匹配）。

    2026-09-06 P1 修复（知识库 08 §24.9 事故 ④）：不只查端口 alive，
    还校验 /status 返回的 account 与请求的 account_name 一致——
    防止端口被别的账号 BCC 占用（如手动 --port 启动绕过端口哈希）
    时，把 A 账号的 cookie 当成 B 账号的刷进 .env。
    """
    from auto_dm import accounts as _acc
    port = _bcc_port(account_name)
    if not _acc._port_open(port, timeout=timeout):
        return False
    try:
        r = requests.get(f"http://127.0.0.1:{port}/status", timeout=2)
        d = r.json() or {}
        if not d.get("alive", False):
            return False
        # 账号一致性：BCC 挂的账号必须就是请求的账号
        bcc_account = str(d.get("account") or "")
        if bcc_account and bcc_account != str(account_name):
            logger.warning(
                f"[bcc-client] 端口 {port} 上运行的 BCC 属于账号「{bcc_account}」"
                f"而非「{account_name}」（端口被占用/手动启动绕过哈希），"
                f"按未运行处理，拒绝跨账号取 cookie。")
            return False
        return True
    except Exception:
        return False


def _bcc_post(account_name: str, path: str, json_body: dict = None, timeout: float = 30) -> dict:
    """调 BCC 的 POST 接口，返回 {ok, ...}。BCC 不在运行时返回 {ok:false, msg:...}。"""
    port = _bcc_port(account_name)
    try:
        r = requests.post(f"http://127.0.0.1:{port}{path}",
                          json=json_body or {}, timeout=timeout)
        return r.json() or {}
    except Exception as e:
        return {"ok": False, "msg": f"BCC 不可达: {e}"}


class RiskControlError(Exception):
    """风控/验证码拦截异常。

    抓取到的凭证被抖音风控页污染（如 s_v_web_id=verify_xxx 占位），
    或抓取过程中抖音弹出验证码/滑块验证页时抛出。

    抛出此异常代表【本次凭证不写入 .env】，并提示用户在指纹浏览器中
    手动处理验证码/滑块，期间程序持续监测验证码处理进度与凭证污染状态。
    """


def _cookie_is_polluted(cookies):
    """检测 cookie 是否被抖音风控验证页污染。

    返回 (is_polluted, polluted_fields: list[str])。

    【判定废弃 2026-08-17 16:36 基座直测结论】不再基于 cookie 格式判污染：
      - 基座全新扫码实测：抖音新版的 s_v_web_id 正常值就是 'verify_xxx' 开头、
        uid_tt 是 32 位 hex 字符串、sid_ucp_v1 以 '1.0.0-' 开头——这些都是【正常
        登录态】，不是污染。此前『verify_ 前缀』『非 19 位纯数字』『1.0.0- 前缀』
        的判定都会被抖音新版格式误伤，导致拒绝写盘。
      - 基座实测：即便 s_v_web_id=verify_ 开头，query/user 网络接口仍返回真实十进制
        uid（3887506227210423），create_conversation 仍成功——凭证完全有效。
    真实的风控验证页已由上层『_risk_hit 页面实时监测』精确捕捉（URL/内容含
    verifycenter/captcha/滑块/安全验证 等即判定并保持浏览器打开提示用户处理）。
    因此本函数不再做任何基于 cookie 格式的污染判定，一律放行；污染由页面监测兜底。
    """
    return False, []


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
            # 【关键】私信落地页（默认 chat?isPopup=1）必须在【新标签页】打开，绝不在首页
            # 标签页 goto 刷新。用户实测确认：chat?isPopup=1 私信页在原生打开时一切正常
            # （拉取聊天对象/历史对话/收发均正常），首页标签页保留干净登录态不被打扰。
            # 打开私信落地页触发 security-sdk 生成【有效】web_protect。
            msg_page = None
            try:
                msg_page = await context.new_page()
                await msg_page.goto(landing_url,
                                    wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(2)
            except Exception as e:
                logger.warning(f"[auth] 生成初始数据：新开标签页打开私信落地页({landing_url})失败: {e}")
            # 凭证读取切到私信新标签页（干净登录态上下文）；失败时退回首页标签页
            _read_page = msg_page if (msg_page is not None and not msg_page.is_closed()) else page
            keys_str = None
            web_protect_str = None
            for _ in range(6):
                await asyncio.sleep(4)
                await _read_page.mouse.wheel(0, 600)
                keys_str = await _read_page.evaluate('localStorage["security-sdk/s_sdk_crypt_sdk"]')
                web_protect_str = await _read_page.evaluate('localStorage["security-sdk/s_sdk_sign_data_key/web_protect"]')
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
            强制重扫现统一复用该账号固定 profile（单 profile 铁律：禁止临时目录，
            临时 profile 会被抖音识别为新设备触发风控），且重扫前先停该账号凭证守护
            释放 profile 锁，故可安全恢复主动打开私信落地页。
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
        # 账号名由 env_path 推导（accounts/<name>/.env -> <name>），供账号级 DY_PROXY 代理注入
        import os as _os
        _acc_name = _os.path.basename(_os.path.dirname(_os.path.abspath(env_path))) if env_path else None
        # 指纹内核不可用时 should_use_vb 已直接抛错，此处不会再出现“回退原生 Playwright”
        logger.info(f"[auth] 使用指纹浏览器内核接管登录会话 (mode={_vb_mode})")
        logger.info(f"[auth] 账号专属指纹 profile: {_acc_profile}")
        _pw, _browser, context, _backend = await launch_async(
            _vb_mode, _cfg, headless=headless, user_data_dir=_acc_profile, force=force,
            account=_acc_name)
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

        msg_page = None

        async def _open_message_page():
            """在【新标签页】打开抖音私信落地页（默认 chat?isPopup=1，用户实测该页显示正常、
            私信功能正常），触发 security-sdk 把 web_protect 从空壳升级为有效值（仅首页拿不到
            有效签名）。绝不在原首页标签页 goto 刷新——首页保留干净登录态不被打扰。
            """
            nonlocal msg_page
            try:
                msg_page = await context.new_page()
                await msg_page.goto(landing_url,
                                    wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(2)
            except Exception as e:
                logger.warning(f"[auth] 新开标签页打开私信落地页({landing_url})失败（将继续重试）: {e}")

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

            【风控/验证码处理策略 2026-08-17 修订】
            检测到抖音弹出验证码/滑块/风控验证页时，【绝不关闭浏览器、绝不立即 raise】，
            而是 logger.warning 提示用户在【保持打开的指纹浏览器】中手动完成验证码，
            本循环继续轮询：用户完成验证码后页面会离开风控页 → 自动恢复抓取凭证流程。
            仅当超时仍处于风控页才 raise RiskControlError（此时浏览器仍不关闭，留给用户处理）。
            """
            keys_str = web_protect_str = None
            _logged_in = False
            _msg_opened = False
            _risk_notified = False  # 风控提示只打一次，避免刷屏
            while time.time() < deadline:
                await asyncio.sleep(1)
                # 监测对象：优先用已打开的私信新标签页（msg_page），未打开时用首页 page。
                _mon_page = msg_page if (msg_page is not None and not msg_page.is_closed()) else page
                # 风控/验证码实时监测：抓取过程中若抖音弹出验证码/滑块/风控验证页，
                # 【不关闭浏览器、不立即 raise】，提示用户在保持打开的指纹浏览器中手动处理，
                # 本循环继续轮询；用户完成验证码后页面离开风控页 → 自动恢复抓取。
                try:
                    _page_url = await _mon_page.evaluate("location.href")
                    _page_html = await _mon_page.evaluate("document.documentElement.outerHTML.slice(0, 4000)")
                except Exception:
                    _page_url = ""
                    _page_html = ""
                _risk_hit = any(k in (_page_url + _page_html) for k in (
                    "verify.zijieapi.com", "verifycenter", "captcha", "滑块", "安全验证",
                    "异常请求", "验证中心", "人机验证", "账号存在风险"))
                if _risk_hit:
                    if not _risk_notified:
                        logger.warning(
                            f"[风控] 检测到验证码/风控验证页（{_page_url}）。"
                            f"【指纹浏览器保持打开】，请在其中手动完成验证码/滑块验证，"
                            f"完成后程序将自动继续抓取凭证；无需重新扫码。")
                        _risk_notified = True
                    # 风控期间不抓凭证，继续轮询等待用户处理完成
                    continue
                else:
                    # 风控页已离开（用户处理完成），重置提示标记，恢复抓取
                    if _risk_notified:
                        logger.info("[风控] 验证码/风控页已离开，恢复抓取凭证流程")
                        _risk_notified = False
                # 阶段一：等待用户扫码（真实登录态）。未登录前绝不开私信页，停在首页。
                if not _logged_in:
                    if await _is_real_login(context):
                        _logged_in = True
                        logger.info("[auth] 检测到真实登录态（已扫码），准备新开标签页打开私信落地页生成有效 web_protect")
                    else:
                        continue
                # 阶段二：已登录 → 新开标签页打开私信落地页触发 security-sdk 生成有效签名
                if not _msg_opened:
                    await _open_message_page()
                    _msg_opened = True
                try:
                    keys_str = await _mon_page.evaluate('localStorage["security-sdk/s_sdk_crypt_sdk"]')
                    web_protect_str = await _mon_page.evaluate(
                        'localStorage["security-sdk/s_sdk_sign_data_key/web_protect"]')
                except Exception:
                    keys_str = web_protect_str = None
                # 必须真实登录态（sessionid 出现）+ web_protect 有效 JSON（空壳不算），否则继续等
                if (keys_str and _web_protect_valid(web_protect_str)) and await _is_real_login(context):
                    logger.info("[auth] 检测到真实登录态 + 有效 web_protect（私信落地页 security-sdk 已生成）")
                    return keys_str, web_protect_str
            # 超时仍未拿到有效凭证：若当前正处于风控页，raise RiskControlError（浏览器不关闭，留给用户处理）
            if _risk_notified:
                raise RiskControlError(
                    "登录超时且仍处于抖音验证码/风控验证页。【指纹浏览器保持打开】，"
                    "请在其中手动完成验证码（滑块/短信验证），处理完成后重新点击「重新获取凭证」。"
                    "本次不写入被污染的凭证。")
            raise TimeoutError(
                "登录超时：未在超时时间内完成扫码并拿到【有效】web_protect+登录态，已放弃，不写入残缺凭证")

        # 用 domcontentloaded 而非 load：抖音首页有持续长连接/轮询，load 事件常延迟触发，
        # 在指纹 chromium 下更易触发 30s 超时；domcontentloaded 已足够后续脚本执行与扫码。
        # 【关键】goto 首页必须带异常处理 + 重试：临时全新 profile 首次启动时抖音首页
        # 加载很慢/可能重定向，30s 超时后 Playwright 抛 TimeoutError；若不加 try/except，
        # 异常冒泡到 enrich_auth 的 except 返回，但浏览器窗口已弹出且停在 about:blank
        # （launch_persistent_context 初始页就是 about:blank，goto 未成功则不导航）。
        # 这里重试一次 + 失败时明确报错，避免残留一个“白屏 about:blank”窗口让用户困惑。
        _home_loaded = False
        for _attempt in range(2):
            try:
                await page.goto(self.home_url, wait_until="domcontentloaded", timeout=30000)
                _home_loaded = True
                break
            except Exception as _e:
                logger.warning(f"[auth] 打开抖音首页失败(第{_attempt+1}次): {_e}")
                await asyncio.sleep(2)
        if not _home_loaded:
            if _backend == "exe":
                try:
                    await context.close()
                except Exception:
                    pass
            raise RuntimeError(
                "打开抖音首页失败（可能因临时全新环境加载超时/被抖音风控拦截）。"
                "请稍后重试「重新获取凭证」，或确认网络正常后再试。")
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
            # 【不关闭浏览器】用户可能在处理验证码/等待页面恢复，保持打开让用户可继续操作
            raise TimeoutError("登录超时：未抓到完整/有效 web_protect/keys，已放弃，不写入残缺凭证")

        cookies = {cookie['name']: cookie['value'] for cookie in await context.cookies()}
        # 最终守卫：必须含真实登录 cookie + 有效签名，否则绝不返回（上层不会写 .env）
        if not (cookies.get("sessionid") or cookies.get("sid_tt")) or not _web_protect_valid(web_protect_str) or not keys_str:
            # 【不关闭浏览器】凭证不完整可能源于用户仍在处理验证码，保持打开
            raise RuntimeError(
                "凭证不完整（缺少真实登录 cookie 或有效 web_protect/keys），拒绝返回残缺 auth，不写 .env")
        # 风控污染守卫：即便 web_protect/cookie 看似齐全，只要关键身份 cookie 被抖音
        # 风控验证页污染（s_v_web_id=verify_xxx 等占位），也绝不写入 .env，
        # 否则会带着污染 cookie 写回，导致后续弹幕昵称被加密 / sec_uid 丢失。
        _polluted, _fields = _cookie_is_polluted(cookies)
        if _polluted:
            # 【不关闭浏览器】保持指纹浏览器打开，提示用户手动处理验证码/滑块后重试
            raise RiskControlError(
                f"抓取到的凭证被抖音风控验证页污染（字段={'/'.join(_fields)}），"
                f"已【拒绝写入】.env。【指纹浏览器保持打开】，请在其中手动处理验证码/滑块，"
                f"处理完成后重新点击「重新获取凭证」。")
        # 【不立即关闭浏览器】成功抓到凭证后保持指纹浏览器打开，让用户确认登录态/处理可能的二次验证。
        # 浏览器由用户手动关闭，或由后续「关闭指纹浏览器」按钮/enrich_auth 上层关闭。
        logger.info("[auth] 凭证抓取完成，指纹浏览器保持打开（用户可手动关闭或继续操作页面）")
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
        """从指定 .env 读取并构造 DouyinAuth（替代 common_util.load_env 的写死路径）。

        P3（2026-09-06 第五轮治理，09 台账 5.2B3）：改用 dotenv_values 纯文件读，
        **不再把凭证写进进程级 os.environ**——backend 单进程多账号场景下，
        旧实现 load_dotenv(override=True) 会让后加载的账号环境变量覆盖先加载的，
        各处 os.getenv 拿到串号凭证。现按 env_path 精确读取；
        文件缺某键时回退 os.getenv（兼容扫码流程先写环境的旧路径）。
        """
        from dotenv import load_dotenv, dotenv_values
        from builder.auth import DouyinAuth
        if env_path and env_path != ".env":
            vals = dotenv_values(env_path) if os.path.exists(env_path) else {}
            # 兼容：仅当文件不存在时才退回环境变量（存在但不完整以文件为准，
            # 避免陈旧环境值覆盖刚扫码的新凭证）
            if not os.path.exists(env_path):
                load_dotenv(env_path, override=True)
                vals = {}
        else:
            load_dotenv(override=True)
            vals = {}

        def _val(key):
            v = vals.get(key)
            if v is None or v == "":
                v = os.getenv(key)
            return v

        cookies = _val("DY_COOKIES")
        web_protect = _val("DY_WEB_PROTECT") or ""
        keys = _val("DY_KEYS") or ""
        auth = DouyinAuth()
        # 优先用持久化的 web_protect/keys（完整还原签名，含 ree_public_key 等派生字段）；
        # 旧 .env 无这两个键时退回仅四件套。
        auth.perepare_auth(cookies, web_protect, keys)
        if not (web_protect and keys):
            auth.ticket = _val("DY_TICKET") or None
            auth.ts_sign = _val("DY_TS_SIGN") or None
            auth.client_cert = _val("DY_CLIENT_CERT") or None
            auth.private_key = DYLoginApi._decode_private_key(_val("DY_PRIVATE_KEY"))
            # 补齐 ree_public_key（perepare_auth 用 web_protect/keys 时才派生；
            # 旧 .env 无 web_protect/keys 键时手动补，避免私信签名缺字段）
            if auth.private_key:
                import base64 as _b64
                auth.ree_public_key = _b64.b64encode(auth.private_key.encode()).decode()
        # 补 cookie_str，供 IM 私有网关 with_csrf 注入 x-secsdk-csrf-token 头（抖音要求）
        if auth.cookie and not getattr(auth, "cookie_str", None):
            auth.cookie_str = "; ".join(f"{k}={v}" for k, v in auth.cookie.items())
        return auth

    @staticmethod
    def refresh_cookie_from_profile(auth, env_path=None, allow_launch=False):
        """刷新 auth 的 cookie 为浏览器 profile 里的实时值（写回 .env）。

        优先通过 BCC HTTP /cookie 接口（常驻浏览器容器，不抢锁）；
        2026-09-06 全局治理（BCC 快闪根治）：**默认绝不兜底直开 Playwright**。
        原实现在 BCC 未运行时 fallback 到 launch_sync 直开浏览器——而 WS 每次
        重连（recv_daemon:434 _build_auth）、启动补捕获（capture_all:1027）
        都会调本函数 → 失败 → 3~5s 重试 → 再开浏览器 = 无限快闪循环
        （2026-09-06 20:36 实测 154 次 launch）。
        现语义：
          allow_launch=False（默认）—— BCC 不在线直接返回 False（沿用 .env 凭证），
            绝不为"刷新"开浏览器。高频路径（WS建连/API拉取/发送）全部安全。
          allow_launch=True —— 仅用户显式动作（更新会话按钮/手动校验）允许
            兜底开浏览器（旧行为）。
        """
        import os
        # 解析账号名（env_path -> account name）
        account_name = None
        if env_path:
            base = os.path.basename(os.path.dirname(os.path.abspath(env_path)))
            if base and base != "accounts":
                account_name = base
        # 优先走 BCC
        if account_name and _bcc_alive(account_name):
            r = _bcc_post(account_name, "/cookie", timeout=15)
            if r.get("ok"):
                cks_str = r.get("cookies") or ""
                if not cks_str:
                    # BCC 返回的 cookie 列表
                    cks = r.get("cookie_dict") or {}
                    if cks:
                        cks_str = "; ".join(f"{k}={v}" for k, v in cks.items())
                if cks_str:
                    auth.cookie = dict(p.split("=", 1) for p in cks_str.split("; ") if "=" in p)
                    auth.cookie_str = cks_str
                    logger.info(f"[auth] BCC /cookie 刷新成功（{len(auth.cookie)} 项）")
                    return True
            logger.warning(f"[auth] BCC /cookie 返回失败: {r.get('msg', '')}，退回直开浏览器")

        # 后备：直开 Playwright —— 2026-09-06 起【仅 allow_launch=True】才走。
        # （BCC 未运行时默认直接沿用 .env 凭证，绝不为刷新开浏览器，见函数 docstring）
        if not allow_launch:
            logger.debug("[auth] profile 刷新 cookie：BCC 不在线且 allow_launch=False，沿用 .env 凭证")
            return False
        try:
            from auto_dm import accounts as _acc
            from auto_dm.vbrowser import should_use_vb, launch_sync
            from auto_dm import config as _cfg
            if not env_path or not os.path.exists(env_path):
                logger.info(f"[auth] profile 刷新 cookie：env 不存在跳过 env_path={env_path}")
                return False
            profile = _acc.profile_dir_of(env_path)
            if not profile or not os.path.isdir(profile):
                logger.info(f"[auth] profile 刷新 cookie：profile 不存在跳过 profile={profile}")
                return False
            _vb, _vb_mode = should_use_vb(_cfg)
            _pw, _browser, context, _backend = launch_sync(_vb_mode, _cfg, headless=True, user_data_dir=profile, account=account_name)
            try:
                page = context.pages[0] if context.pages else context.new_page()
                try:
                    page.goto("https://www.douyin.com/chat", wait_until="domcontentloaded", timeout=20000)
                    page.wait_for_timeout(2500)
                except Exception:
                    logger.warning("[auth] profile 刷新 cookie：打开 chat 页失败，退回原凭证")
                    return False
                cks = {}
                for c in context.cookies():
                    cks[c["name"]] = c["value"]
                if cks.get("sessionid") or cks.get("sid_tt"):
                    auth.cookie = cks
                    auth.cookie_str = "; ".join(f"{k}={v}" for k, v in cks.items())
                    try:
                        DYLoginApi().save_credential(auth, env_path)
                    except Exception:
                        pass
                    logger.info(f"[auth] profile 刷新 cookie 成功（{len(cks)} 项，已写回 .env）")
                    return True
                logger.warning("[auth] profile 刷新 cookie：profile 内无登录态（无 sessionid）")
                return False
            finally:
                try:
                    if _backend == "exe":
                        context.close()
                    _pw.stop()
                except Exception:
                    pass
        except Exception as e:
            logger.info(f"[auth] profile 刷新 cookie 跳过（{e}）")
            return False

    @staticmethod
    def bulk_user_info_via_browser(env_path, sec_uids):
        """在真实浏览器上下文里批量查用户昵称/头像（对齐 douyin.com/chat 实机行为）。

        背景：纯 requests 调 get_user_info（profile API）会被拒绝（status=2）或限频，
        而浏览器页面内 fetch 带真实签名+指纹+实时cookie，稳定返回昵称/头像。
        CDP 实测：页面内 fetch `/aweme/v1/web/im/user/info/` POST `sec_user_ids=[...]`
        返回 `data: [{nickname, avatar_small...}, ...]`，一次可查多个用户。

        注意：本函数是「主动 POST sec_user_ids 批量查」旧路径，无头下抖音会降级返回空，
        故需有头浏览器（与 BCC 常驻无头无关）。
        新版前移捕获改用 browser_daemon.capture_userinfo_map「被动 hook 截前端自发
        im/user/info」——该路径无头可行（实机 44/44），BCC 常驻已默认 headless=True。
        返回 {sec_uid: {"nickname": str, "avatar": str}}，失败/受限的 sec_uid 不包含。

        优先通过 BCC HTTP /user_info 接口（常驻浏览器容器，不抢锁）；
        BCC 未运行时退回直开 Playwright（旧路径，可能抢锁但保证功能可用）。
        """
        import os
        # 解析账号名（env_path -> account name）
        account_name = None
        if env_path:
            base = os.path.basename(os.path.dirname(os.path.abspath(env_path)))
            if base and base != "accounts":
                account_name = base
        # 优先走 BCC（常驻浏览器容器，不抢锁）
        if account_name and sec_uids and _bcc_alive(account_name):
            r = _bcc_post(account_name, "/user_info",
                          {"sec_uids": list(sec_uids)}, timeout=30)
            if r.get("ok"):
                data = r.get("data") or {}
                # BCC 已用真实浏览器查过：data 为空说明这些 sec_uid 无效/受限，
                # 直接返回空（不再退回直开浏览器，避免白白触发 Playwright 崩溃）
                logger.info(f"[auth] BCC /user_info 批量查昵称："
                            f"{len(data)}/{len(sec_uids)} 个成功")
                return data
            logger.warning(f"[auth] BCC /user_info 返回失败: "
                           f"{r.get('msg', '')}，退回直开浏览器")
        # 后备：直开 Playwright（BCC 未运行时）
        from playwright.sync_api import sync_playwright
        from auto_dm import accounts as _acc
        from auto_dm.vbrowser import should_use_vb, launch_sync
        from auto_dm import config as _cfg
        out = {}
        if not sec_uids or not env_path or not os.path.exists(env_path):
            return out
        profile = _acc.profile_dir_of(env_path)
        if not profile or not os.path.isdir(profile):
            return out
        _vb, _vb_mode = should_use_vb(_cfg)
        _pw = None
        _browser = None
        context = None
        try:
            _pw, _browser, context, _backend = launch_sync(_vb_mode, _cfg, headless=False, user_data_dir=profile)
            page = context.pages[0] if context.pages else context.new_page()
            try:
                page.goto("https://www.douyin.com/chat", wait_until="domcontentloaded", timeout=25000)
                page.wait_for_timeout(2500)
            except Exception:
                logger.warning("[auth] 批量查昵称：打开 chat 页失败")
                return out
            if not page.url.startswith("https://www.douyin.com"):
                logger.warning("[auth] 批量查昵称：未落在 douyin.com 域，跳过")
                return out
            api_url = "/aweme/v1/web/im/user/info/?device_platform=webapp&aid=6383&channel=channel_pc_web&pc_client_type=1&update_version_code=170400&version_code=170400&version_name=17.4.0&cookie_enabled=true&browser_language=zh-CN&browser_platform=Win32&browser_name=Mozilla&browser_version=5.0&browser_online=true&os_name=Windows&os_version=10&platform=PC&downlink=10&effective_type=4g&round_trip_time=100"
            # 分批（每批 6 个），浏览器单次 fetch 批量查询
            import json as _json
            import urllib.parse as _up
            batch = 6
            for i in range(0, len(sec_uids), batch):
                chunk = sec_uids[i:i + batch]
                # form-urlencoded: sec_user_ids=["...","..."]（与浏览器一致）
                _body_q = "sec_user_ids=" + _up.quote(_json.dumps(chunk))
                js = f"""
                (async () => {{
                  const r = await fetch({_json.dumps(api_url)}, {{
                    method: 'POST',
                    headers: {{'content-type': 'application/x-www-form-urlencoded; charset=UTF-8'}},
                    body: {_json.dumps(_body_q)},
                    credentials: 'include'
                  }});
                  return await r.json();
                }})()
                """
                try:
                    result = page.evaluate(js)
                except Exception as e:
                    logger.warning(f"[auth] 批量查昵称 evaluate 失败: {e}")
                    continue
                items = (result or {}).get("data") or []
                for u in items:
                    sec = u.get("sec_uid") or ""
                    if not sec:
                        continue
                    avt = (u.get("avatar_small") or {}).get("url_list") or []
                    out[sec] = {
                        "nickname": u.get("nickname") or "",
                        "avatar": avt[0] if avt else "",
                    }
                page.wait_for_timeout(300)
            logger.info(f"[auth] 浏览器批量查昵称：{len(out)}/{len(sec_uids)} 个成功")
            return out
        except Exception as e:
            logger.warning(f"[auth] 浏览器批量查昵称失败: {e}")
            return out
        finally:
            try:
                if _backend == "exe" and context is not None:
                    context.close()
                if _pw is not None:
                    _pw.stop()
            except Exception:
                pass

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
        try:
            auth = await self.login_grab_ticket(headless=headless, env_path=env_path, force=force,
                                                 landing_url=landing_url)
        except RiskControlError as _rc:
            # 风控/验证码拦截：本次凭证被污染或抓取中触发风控页，绝不写 .env。
            # 提示用户在指纹浏览器中手动处理验证码，期间继续监测验证码处理进度与污染状态。
            logger.warning(f"[风控] {_rc}（指纹浏览器保持打开，请在其中手动处理验证码/滑块）")
            raise
        # —— 写回 .env 前，先比对旧→新并生成捕获分析报告（你扫码，程序自动分析）——
        analyze_login_capture(auth, old_snap, env_path)
        logger.info(f"登录凭证已存 {self.save_credential(auth, env_path=env_path)}")
        return auth

    async def read_auth_from_profile(self, env_path=".env",
                                       landing_url="https://www.douyin.com/chat?isPopup=1",
                                       timeout=300):
        """从【该账号已登录的持久化 profile】直接读取有效凭证（不强制重新扫码）。

        与 login_grab_ticket(force=True) 的区别：
          - 本方法 force=False：复用账号独占 profile（accounts.profile_dir_of 推导）里
            已有的登录态，绝不清空、绝不临时化。若 profile 仍是干净已登录态，
            打开首页后直接进入私信落地页抓取有效 web_protect/keys 即可，【不弹扫码】。
          - 仅在 profile 自身也已失效（无 sessionid、且无法在超时内拿到有效签名）时，
            才降级为等待本次真实扫码（等价于 force=True 的重扫路径）。

        风控/污染策略（与 login_grab_ticket 一致并强化）：
          - 抓取/读取过程中若抖音弹出验证码/滑块/风控验证页，【绝不关闭浏览器、
            绝不立即 raise】，提示用户在保持打开的指纹浏览器中手动处理，本循环继续轮询；
            用户完成验证码后页面离开风控页 → 自动恢复抓取。
          - 即便 web_protect/cookie 看似齐全，只要关键身份 cookie 被风控验证页污染
            （s_v_web_id=verify_xxx 等占位），也绝不返回残缺 auth，而是 raise RiskControlError
            （浏览器仍不关闭，提示用户手动处理验证码/滑块）。
          - 未读取到正确凭证前，浏览器【始终保持打开】，直到超时仍在风控页才 raise。

        返回完整的 DouyinAuth（含 cookie / ticket / web_protect 等）。调用方（accounts
        层 recapture_from_profile）负责在确认凭证 clean 后写回 .env。本方法本身不写盘，
        以便把“读取”与“落盘”两件事解耦，避免污染凭证误写回。
        """
        from auto_dm import config as _cfg
        from auto_dm.vbrowser import should_use_vb, launch_async
        from auto_dm import accounts as _accounts

        _vb, _vb_mode = should_use_vb(_cfg)
        _pw = None
        _browser = None
        _backend = None
        context = None
        page = None
        _acc_profile = _accounts.profile_dir_of(env_path)
        # 账号名由 env_path 推导，供账号级 DY_PROXY 代理注入
        import os as _os
        _acc_name = _os.path.basename(_os.path.dirname(_os.path.abspath(env_path))) if env_path else None
        logger.info(f"[auth] 从持久化 profile 读取凭证 (mode={_vb_mode}, profile={_acc_profile})")
        _pw, _browser, context, _backend = await launch_async(
            _vb_mode, _cfg, headless=False, user_data_dir=_acc_profile, force=False,
            account=_acc_name)
        page = context.pages[0] if context.pages else await context.new_page()

        if page is None:
            raise RuntimeError("[auth] 未能获得浏览器页面，读取凭证中止")

        async def _is_real_login(ctx):
            try:
                ck = {c['name']: c['value'] for c in await ctx.cookies()}
            except Exception:
                return False
            return bool(ck.get("sessionid") or ck.get("sid_tt"))

        def _web_protect_valid(s):
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

        msg_page = None

        async def _open_message_page():
            # 【关键】必须在【新标签页】打开私信落地页，绝不在原首页标签页 goto 刷新——
            # 用户实测确认：chat?isPopup=1 私信页在原生打开时一切正常（拉取聊天对象/
            # 历史对话/收发均正常），首页标签页保留干净登录态不被打扰，两者互不干扰。
            nonlocal msg_page
            try:
                msg_page = await context.new_page()
                await msg_page.goto(landing_url, wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(3)
            except Exception as e:
                logger.warning(f"[auth] 新开标签页打开私信落地页({landing_url})失败（将继续重试）: {e}")

        # 首页加载（带重试一次），失败则明确报错（浏览器保持打开）
        _home_loaded = False
        for _attempt in range(2):
            try:
                await page.goto(self.home_url, wait_until="domcontentloaded", timeout=30000)
                _home_loaded = True
                break
            except Exception as _e:
                logger.warning(f"[auth] 打开抖音首页失败(第{_attempt+1}次): {_e}")
                await asyncio.sleep(2)
        if not _home_loaded:
            if _backend == "exe":
                try:
                    await context.close()
                except Exception:
                    pass
            raise RuntimeError(
                "打开抖音首页失败（可能因网络异常/被抖音风控拦截）。"
                "请确认网络正常后重试「引擎校验」/「重新获取凭证」。")

        # 长超时轮询：优先复用已登录态，仅在 profile 也失效时才等待本次扫码；
        # 期间持续监测验证码/风控页，保持浏览器打开直到拿到 clean 凭证或超时。
        keys_str = web_protect_str = None
        _logged_in = False
        _msg_opened = False
        _risk_notified = False
        deadline = time.time() + timeout
        while time.time() < deadline:
            await asyncio.sleep(1)
            # 监测对象：优先用已打开的私信页标签页（msg_page），未打开时用首页 page。
            # 私信页是新标签页、干净登录态上下文，凭证应从这里读取；首页标签页保持不动。
            _mon_page = msg_page if (msg_page is not None and not msg_page.is_closed()) else page
            # 风控/验证码实时监测：抓取/读取过程中若抖音弹出验证码/滑块/风控验证页，
            # 【不关闭浏览器、不立即 raise】，提示用户在保持打开的指纹浏览器中手动处理，
            # 本循环继续轮询；用户完成验证码后页面离开风控页 → 自动恢复读取凭证流程。
            try:
                _page_url = await _mon_page.evaluate("location.href")
                _page_html = await _mon_page.evaluate("document.documentElement.outerHTML.slice(0, 4000)")
            except Exception:
                _page_url = ""
                _page_html = ""
            _risk_hit = any(k in (_page_url + _page_html) for k in (
                "verify.zijieapi.com", "verifycenter", "captcha", "滑块", "安全验证",
                "异常请求", "验证中心", "人机验证", "账号存在风险"))
            if _risk_hit:
                if not _risk_notified:
                    logger.warning(
                        f"[风控] 检测到验证码/风控验证页（{_page_url}）。【指纹浏览器保持打开】，"
                        f"请在其中手动完成验证码/滑块验证，完成后程序将自动继续读取凭证；"
                        f"在您验证通过、页面离开风控页之前，本程序不会关闭浏览器、也不会写入被污染的凭证。")
                    _risk_notified = True
                continue
            else:
                if _risk_notified:
                    logger.info("[风控] 验证码/风控页已离开，恢复读取凭证流程")
                    _risk_notified = False

            if not _logged_in:
                if await _is_real_login(context):
                    _logged_in = True
                    logger.info("[auth] 检测到真实登录态（profile 已登录或用户已扫码），准备新开标签页打开私信落地页读取有效 web_protect")
                else:
                    # profile 无登录态：提示用户扫码（降级为等待本次扫码）
                    if not _risk_notified:
                        logger.info("[auth] 持久化 profile 未检测到登录态，请在打开的指纹浏览器中完成扫码")
                    continue
            if not _msg_opened:
                await _open_message_page()
                _msg_opened = True
            try:
                keys_str = await _mon_page.evaluate('localStorage["security-sdk/s_sdk_crypt_sdk"]')
                web_protect_str = await _mon_page.evaluate(
                    'localStorage["security-sdk/s_sdk_sign_data_key/web_protect"]')
            except Exception:
                keys_str = web_protect_str = None
            if (keys_str and _web_protect_valid(web_protect_str)) and await _is_real_login(context):
                logger.info("[auth] 从新开标签页私信落地页读取到真实登录态 + 有效 web_protect")
                break
        else:
            # while 正常结束（超时）：若当前处于风控页，raise（浏览器不关闭，留给用户处理）
            if _risk_notified:
                raise RiskControlError(
                    "读取超时且仍处于抖音验证码/风控验证页。【指纹浏览器保持打开】，"
                    "请在其中手动完成验证码（滑块/短信验证），处理完成后重新点击「引擎校验」。"
                    "本次不写入被污染的凭证。")
            raise TimeoutError(
                "读取超时：未在超时时间内从 profile 拿到【有效】web_protect+登录态，已放弃，不写入残缺凭证")

        cookies = {cookie['name']: cookie['value'] for cookie in await context.cookies()}
        if not (cookies.get("sessionid") or cookies.get("sid_tt")) or not _web_protect_valid(web_protect_str) or not keys_str:
            raise RuntimeError(
                "凭证不完整（缺少真实登录 cookie 或有效 web_protect/keys），拒绝返回残缺 auth，不写 .env")
        # 风控污染守卫：即便看似齐全，只要关键身份 cookie 被风控验证页污染，也绝不返回
        _polluted, _fields = _cookie_is_polluted(cookies)
        if _polluted:
            raise RiskControlError(
                f"读取到的凭证被抖音风控验证页污染（字段={'/'.join(_fields)}），"
                f"已【拒绝返回】。【指纹浏览器保持打开】，请在其中手动处理验证码/滑块，"
                f"处理完成后重新点击「引擎校验」。")
        logger.info("[auth] 从 profile 读取到有效凭证，指纹浏览器保持打开（等待调用方写盘/用户确认）")
        auth = DouyinAuth()
        auth.perepare_auth('', web_protect_str, keys_str)
        auth.cookie = cookies
        auth.cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
        auth.web_protect_str = web_protect_str
        auth.keys_str = keys_str
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