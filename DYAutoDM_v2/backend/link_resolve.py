# coding=utf-8
"""链接解析：把粘贴的抖音链接统一解析成直播间号 live_id（=web_rid）。

解析优先级（主引擎 -> 备用）：
  【主引擎】reflow 两步换发（借鉴 DouyinLiveRecorder）：
      分享短链 / 直播链接 / 用户主页
        -> 跟随重定向拿 **room_id**（注意：重定向后 URL 里是 room_id）
        -> 调 webcast.amemv.com/webcast/room/reflow/info/（带 X-Bogus 签名）
        -> 取真实直播间号 **web_rid** 与主播 sec_uid
    该方式拿到的 web_rid 比直接抠 URL 片段更权威，且一并返回主播 sec_uid。
  【备用】直接抠 URL 片段 / 已登录浏览器兜底跳转（保留原逻辑，reflow 失败时用）。

> 🔴 2026-10-01 语义澄清（doc-rot 订正）：本项目共有**两套房间标识**，勿混 ——
>   · **web_rid**（URL 短号，如 `291891133640`）：`live.douyin.com/<web_rid>` 用它；
>   · **room_id**（19 位大数，如 `7691345987004009258`）：直播域 API 用它。
>   上游 DTK `urls/patterns.py` 明文：「Douyin reflow links carry a **room_id**,
>   not the web_rid that `live.douyin.com/<id>` uses」⇒ `/webcast/reflow/<id>`
>   与短链重定向后的 id 都是 **room_id**，须经 `reflow/info` 桥接换出 web_rid。
>   （原文档早前把重定向结果写作「room_id」却又当 web_rid 用，属同源混淆，已订正。）

支持输入形式：
  1) 数字 / web_rid 直播间号（直接当成 live_id）
  2) 直播页链接  https://live.douyin.com/7323xxxx?...
  3) 分享短链    https://v.douyin.com/iRxxxx/  （自动跟随重定向）
  4) 用户主页    https://www.douyin.com/user/MS4wLjAB...（需该用户正在直播）
  5) 关注页直播  https://www.douyin.com/follow/live/<web_rid>?anchor_id=...
  6) 搜索页直播卡 https://www.douyin.com/search/<kw>?...&live_web_rid=<web_rid>&type=live
  7) reflow 分享 https://webcast.amemv.com/douyin/webcast/reflow/<room_id>（经桥接换 web_rid）
"""

from utils.tls_policy import tls_verify  # noqa: E402
import re
import os
import json
import time
import threading
import requests
from urllib.parse import urlparse, parse_qs
from loguru import logger


def _ua() -> str:
    """出站 UA —— 统一走档案（内核感知）。

    ★ 2026-09-26（ADR-016 D4）：本文件原**两处写死旧版 Chrome UA**，
    与档案（内核感知，实测 Firefox 152）矛盾 ⇒ 抖音可见同一账号
    在不同链路暴露不同浏览器身份。现统一入口。
    """
    try:
        from utils.fingerprint import user_agent
        return user_agent()
    except Exception:
        # fallback：档案不可用时的保底 UA（正常路径不会走到）
        return ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

_LIVE_RE = re.compile(r"live\.douyin\.com/([^?/\s\"']+)")
# 2026-09-22（T-01 / ENG-021）：抖音「直播页」还有一类**非 live.douyin.com 域名**的
# 分享/关注链接，形如 https://www.douyin.com/follow/live/<web_rid>?anchor_id=…。
# 其 `<web_rid>` 与 live.douyin.com/<web_rid> 同源（实测：follow/live/992931212705
# 进房 room_id=7688251038101556006，与 live.douyin.com/992931212705 逐字一致）。
# 此前未识别 ⇒ 解析失败 ⇒ 调用方退回整段 URL 当 live_id ⇒ 进房 URL 变成
# live.douyin.com/https://…（实测 404）。此正则**零网络**直接抠出 web_rid。
# ⚠️ 必须忽略 `anchor_id` 等查询参数（它**不是**直播间号）。
_LIVE_PAGE_RE = re.compile(r"douyin\.com/(?:[A-Za-z0-9_\-]+/)*live/(\d{5,})")
# 🔴 2026-10-01（用户实测报障）：**房间号出现在 query 参数里**的形态。
#   例：https://www.douyin.com/search/<关键词>?from_search=true&is_aweme_tied=1
#        &live_web_rid=291891133640&search_id=…&search_result_id=7691345930137652543&type=live
#       （抖音「搜索结果里的直播卡」分享出来的链接）
#   ⇒ 直播间号 = **`live_web_rid` 的值**（实测：用它能匿名进房，HTTP 200 / 1.1MB；
#      且该房间真实 room_id=7691345987004009258 —— 二者是**两套值**，勿混）。
# 🔴 必须**精确锚定 `live_web_rid`**，绝不能「取 URL 里第一个大数字」：
#      同一 URL 里 `search_result_id=7691345930137652543` 也是 19 位大数，
#      却**不是**直播间号（search_result_id 是搜索结果项 id）。
# 💡 开源溯源核对：上游 DTK `src/dtk/urls/patterns.py` 的 DOUYIN_ROUTES 含
#      `[?&]modal_id=` / `[?&]vid=`（视频）/ `[?&]mix_id=` / `[?&]sec_(uid|user_id)=`，
#      **不含 `live_web_rid`** ⇒ 该格式连最权威的上游库都未覆盖，属**本地新增**能力。
_LIVE_QQ_RE = re.compile(r"[?&]live_web_rid=(\d{5,})")
# 兼容拼写/大小写差异（`live_web_rid` / `liveWebRid` / `live_web_rid` 的常见变体）
_LIVE_QQ_ALT_RE = re.compile(r"[?&]live_?web_?rid=(\d{5,})", re.I)
# 🔴 2026-10-01：`/webcast/reflow/<id>`（DTK ResourceKind.LIVE_ROOM）——
#   ⚠️ 该路径里带的是 **room_id（19 位大数），不是 web_rid**（DTK 源码明文注释：
#   「Douyin reflow links carry a room_id, not the web_rid that live.douyin.com/<id> uses」）。
#   ⇒ 该形态**不能**零网络直接当 web_rid 用，必须经 room_id→web_rid 桥接。
_REFLOW_ROOM_RE = re.compile(r"/webcast/reflow/(\d{5,})")
# reflow 重定向链接里抽 sec_user_id
_SEC_UID_RE = re.compile(r"sec_user_id=([\w_\-]+)")
# reflow/info 返回 JSON 取 web_rid 的路径：data.room.owner.web_rid
_REFLOW_URL = "https://webcast.amemv.com/webcast/room/reflow/info/"

# 解析结果 TTL 缓存（key=原始输入）：短链/用户主页等网络型解析很慢(2~15s)，
# 缓存后重复点击/多页复用直接命中，秒回。
_RESOLVE_CACHE: dict[str, tuple[float, tuple]] = {}
_RESOLVE_CACHE_TTL = 300.0  # 5 分钟
_RESOLVE_LOCK = threading.Lock()


def _cache_get(raw: str):
    with _RESOLVE_LOCK:
        hit = _RESOLVE_CACHE.get(raw)
        if hit and (time.time() - hit[0]) < _RESOLVE_CACHE_TTL:
            return hit[1]
    return None


def _cache_put(raw: str, value: tuple) -> None:
    with _RESOLVE_LOCK:
        _RESOLVE_CACHE[raw] = (time.time(), value)
    # 防止缓存无限膨胀：超 200 条删最旧
    if len(_RESOLVE_CACHE) > 200:
        with _RESOLVE_LOCK:
            oldest = min(_RESOLVE_CACHE, key=lambda k: _RESOLVE_CACHE[k][0])
            _RESOLVE_CACHE.pop(oldest, None)


def _extract_live_id_from_url(url):
    """从 URL 文本里抠出直播间号（web_rid）。

    2026-10-01 补：**顺序 = 路径语义 → query 参数**（与输入解析层同序）。
      · `live.douyin.com/<id>` 的 path id 才是 web_rid；
      · `?live_web_rid=<id>`（搜索页直播卡分享）零网络可直取；
    绝不「取 URL 里第一个大数字」—— 同 URL 里 `search_result_id` 是 19 位大数
    但**不是**直播间号。
    """
    if not url:
        return None
    m = _LIVE_RE.search(url)
    if m:
        return m.group(1)
    # query 参数形态（并经重定向后仍可能保留）
    for _re_qq in (_LIVE_QQ_RE, _LIVE_QQ_ALT_RE):
        m = _re_qq.search(url)
        if m:
            return m.group(1)
    return None


def _extract_room_id_from_html(html_text):
    """从直播间/用户主页 HTML 里抠 roomId（与 DouyinAPI.get_live_info 同源正则）。"""
    if not html_text:
        return None
    try:
        m = re.search(r'\\"roomId\\":\\"(\d+)\\"', html_text)
        if m:
            return m.group(1)
    except Exception:
        pass
    return None


def _follow_redirects(url, cookies=None, timeout=15):
    """跟随重定向拿到最终 URL（处理 v.douyin.com 短链）。带登录态 cookie 时
    抖音才会把短链重定向到含 sec_user_id 的 reflow 链接，否则容易被风控页拦截。"""
    headers = {
        # ★ ADR-016 D4：统一走档案（内核感知），禁止写死
        "User-Agent": _ua(),
        "Referer": "https://www.douyin.com/",
    }
    # 非合法 http(s) 链接（如 GUI 把直播间号与链接字段拼接出的畸形串）直接返回，不请求
    if not url or not url.lower().startswith(("http://", "https://")):
        return url
    try:
        resp = requests.get(url, allow_redirects=True, timeout=timeout,
                            headers=headers, cookies=cookies, verify=tls_verify())
        return resp.url
    except Exception as e:
        logger.warning(f"[LIVE-009] " + f"[resolve] 跟随重定向失败: {e}")
        return url


def _extract_reflow_room_id(url):
    """从 `/webcast/reflow/<id>` 抽 **room_id**（⚠️ 是 room_id，**不是** web_rid）。

    2026-10-01（任务 1）：权威依据双源一致 ——
      · 上游 DTK `urls/patterns.py` 明文注释：「Douyin reflow links carry a
        **room_id**, not the web_rid that live.douyin.com/<id> uses」；
      · 上游 DouyinLiveRecorder `room.py:61-66`：`room_id = redirect_url.split('?')[0]
        .rsplit('/', maxsplit=1)[1]`，再经 `reflow/info` 换出 web_rid。
    ⇒ 该 id **不能**直接当 web_rid 用（本项目实测两套值是不同数字），
      必须交 `_reflow_resolve` 桥接。
    """
    m = _REFLOW_ROOM_RE.search(url or "")
    return m.group(1) if m else None


def _build_reflow_params(room_id, sec_user_id, ms_token=""):
    """拼 reflow/info 的 query 参数（与 DouyinLiveRecorder 对齐）。"""
    return {
        "verifyFp": "verify_lk07kv74_QZYCUApD_xhiB_405x_Ax51_GYO9bUIyZQVf",
        "type_id": "0",
        "live_id": "1",
        "room_id": room_id,
        "sec_user_id": sec_user_id,
        "app_id": "1128",
        "msToken": ms_token,
    }


def _xbogus_sign(query_string, ua):
    """对 reflow query 生成 X-Bogus 签名（复用基座 xbogus_pure）。

    注意：xbogus_pure.sign(stub_hex, payload) 内部用 bytes.fromhex(stub_hex)，
    要求 stub_hex 是 query 的十六进制表示，而非裸 query 字符串。
    """
    from utils.dy_util import _xb_sign
    stub_hex = query_string.encode("utf-8").hex()
    try:
        return _xb_sign().sign(stub_hex, ua)
    except Exception as e:
        logger.warning(f"[LIVE-010] " + f"[resolve] X-Bogus 签名失败: {e}")
        return ""


def _reflow_resolve(room_id, sec_user_id, auth=None, ua=None):
    """调 webcast.amemv.com reflow/info，返回 (web_rid, anchor_sec_uid) 或 (None, None)。

    ★ 2026-09-26（ADR-016 D4 / H-22 审计 idx13 订正）：`ua` 默认值原为硬编码
    `"Mozilla/5.0"`，而本文件既定的统一方向是「出站 UA 一律走档案 `_ua()`
    （内核感知）」。该 ua **同时进入 X-Bogus 签名 payload 与请求头**，默认值
    硬编码 ⇒ 本函数对外仍暴露与档案不一致的浏览器身份，恰是 ADR-016 要根除的
    「同一账号不同链路不同身份」。唯一调用点亦未覆盖该默认值。
    现改为 `None` 再回落到 `_ua()`：显式传入仍可覆盖，缺省则与全文件一致。
    """
    ua = ua or _ua()
    params = _build_reflow_params(room_id, sec_user_id,
                                  ms_token=getattr(auth, "msToken", "") or "" if auth else "")
    query = "&".join(f"{k}={v}" for k, v in params.items())
    try:
        xb = _xbogus_sign(query, ua)
    except Exception as e:
        logger.warning(f"[LIVE-011] " + f"[resolve] X-Bogus 签名失败: {e}")
        xb = ""
    url = f"{_REFLOW_URL}?{query}&X-Bogus={xb}"
    headers = {
        "User-Agent": ua,
        "Referer": "https://live.douyin.com/",
        "Accept": "application/json",
    }
    cookies = getattr(auth, "cookie", None) if auth else None
    try:
        resp = requests.get(url, headers=headers, cookies=cookies,
                            timeout=15, verify=tls_verify())
        data = resp.json()
    except Exception as e:
        logger.warning(f"[LIVE-012] " + f"[resolve] reflow 请求失败: {e}")
        return None, None
    # 🔴 2026-10-01（任务 1 实测驱动）：**先判业务码，再判结构**。
    #   历史形态：直接取 `data["data"]["room"]`，业务失败（无 `room` 键）时抛
    #   `KeyError: 'room'` 并被下面的 except 记成「可能未开播/接口变更」——
    #   实测真因是 `status_code=101 / "invalid session"`（**账号会话失效**），
    #   与「未开播」完全两回事。误报会把排查引到错误方向（A/B 判型才发现）。
    #   现有两种账号实测对照：张老师 status_code=0 → web_rid 正确换出；
    #   尚进 status_code=101 → 会话失效。故此处**如实区分**两者。
    _sc = data.get("status_code") if isinstance(data, dict) else None
    if _sc not in (0, None):
        _msg = ""
        _d = data.get("data") if isinstance(data.get("data"), dict) else {}
        _msg = str(_d.get("message") or data.get("status_message") or "")
        if _sc in (101, 10001, 10002) or "session" in _msg.lower():
            logger.warning(f"[LIVE-013b] [resolve] reflow 被拒：**账号会话失效**"
                           f"（status_code={_sc} msg={_msg!r}）—— 请重新扫码/换账号，"
                           f"不是「未开播」")
        else:
            logger.warning(f"[LIVE-013b] [resolve] reflow 业务失败"
                           f"（status_code={_sc} msg={_msg!r}）")
        return None, None
    try:
        room = data["data"]["room"]
        web_rid = room["owner"]["web_rid"]
        anchor_sec_uid = room["owner"].get("sec_uid") or sec_user_id
        return web_rid, anchor_sec_uid
    except Exception as e:
        logger.warning(f"[LIVE-013] " + f"[resolve] reflow 响应解析失败（可能未开播/结构变更）: {e}")
        return None, None


def resolve_via_reflow(raw, auth=None):
    """【主引擎】分享短链/直播链接/用户主页 -> 真实 web_rid + 主播 sec_uid。

    返回 (web_rid, anchor_sec_uid, source_url)；全部失败时返回 (None, None, raw)。
    """
    raw = (raw or "").strip()
    if not raw:
        return None, None, raw

    cookies = getattr(auth, "cookie", None) if auth else None

    # 先把各种链接统一成最终跳转 URL，从中抽 room_id + sec_user_id
    source = raw
    if not re.fullmatch(r"[A-Za-z0-9_]+", raw):  # 非纯 id 才需要重定向
        source = _follow_redirects(raw, cookies=cookies)

    room_id = _extract_live_id_from_url(source)
    sec_user_id = None
    m = _SEC_UID_RE.search(source)
    if m:
        sec_user_id = m.group(1)

    # 用户主页 https://www.douyin.com/user/<sec_uid> 形态：直接拿 sec_user_id
    if not sec_user_id:
        um = re.search(r"douyin\.com/user/([^?/\s]+)", source)
        if um:
            sec_user_id = um.group(1)

    # —— 缺项补全：reflow 需要 room_id + sec_user_id 同时存在 ——
    # 情形A：有 room_id 缺 sec_user_id（典型：v.douyin.com 短链已跳到 live.douyin.com/<web_rid>）
    if room_id and not sec_user_id and auth is not None:
        try:
            info = __import__("dy_apis.douyin_api", fromlist=["DouyinAPI"]).DouyinAPI \
                .get_live_info(auth, room_id)
            if info and info.get("sec_uid"):
                sec_user_id = info["sec_uid"]
                logger.info(f"[resolve] reflow 主引擎：用 get_live_info 补全 sec_uid={sec_user_id}")
        except Exception as e:
            logger.warning(f"[LIVE-014] " + f"[resolve] get_live_info 补全 sec_uid 失败: {e}")

    # 情形B：有 sec_user_id 缺 room_id（典型：用户主页，正在直播）
    if sec_user_id and not room_id and auth is not None:
        # 请求主页 HTML，抽 roomId（与 get_live_info 同源正则）
        try:
            resp = requests.get(
                f"https://www.douyin.com/user/{sec_user_id}",
                headers={"User-Agent": _ua(),
                         "Referer": "https://www.douyin.com/"},
                cookies=cookies, timeout=15, verify=tls_verify())
            room_id = _extract_room_id_from_html(resp.text)
            if room_id:
                logger.info(f"[resolve] reflow 主引擎：从主页 HTML 补全 room_id={room_id}")
        except Exception as e:
            logger.warning(f"[LIVE-015] " + f"[resolve] 主页 HTML 补全 room_id 失败: {e}")

    # 只要拿到 room_id（即真实直播间号 web_rid），主引擎即可成功返回直播间号；
    # 主播 sec_uid 能补全最好（用于私信/主页），补不到也不影响进入直播间。
    if room_id:
        web_rid = room_id  # live.douyin.com/<id> 的 id 本身就是 web_rid
        anchor_sec_uid = sec_user_id
        # 若 room_id + sec_user_id 都齐，再走 reflow 拿权威 web_rid（更稳，且校验房间存在）
        if sec_user_id:
            r_web_rid, r_sec = _reflow_resolve(room_id, sec_user_id, auth=auth)
            if r_web_rid:
                web_rid, anchor_sec_uid = r_web_rid, (r_sec or sec_user_id)
                logger.info(f"[resolve] reflow 主引擎成功 web_rid={web_rid} anchor_sec_uid={anchor_sec_uid}")
            else:
                logger.info(f"[resolve] reflow 校验未返回（可能未开播），直接用 web_rid={web_rid} 进入")
        else:
            logger.info(f"[resolve] reflow 主引擎：仅拿到 web_rid={web_rid}（无 sec_user_id，可正常进直播间）")
        return web_rid, anchor_sec_uid, source

    logger.info("[resolve] reflow 主引擎：无法提取 room_id，转备用")
    return None, None, source


def _browser_resolve(url, headless=False, account_name=None):
    """用已登录浏览器打开链接，等其跳转到直播间页，再抠 live_id。

    适用于用户主页等需要登录态 + JS 跳转才能到达直播间的场景（备用）。
    只使用 VirtualBrowser 指纹内核——禁止回退原生 Playwright，指纹不可用时
    报错并放弃浏览器解析（上层会给出“无法解析直播间号”的明确提示）。

    优先通过 BCC HTTP /resolve_url 接口（常驻浏览器容器，不抢锁）；
    BCC 未运行时退回直开 Playwright（旧路径，可能抢锁但保证功能可用）。
    """
    # 优先走 BCC（常驻浏览器容器，不抢锁）
    if account_name:
        try:
            from dy_apis.login_api import _bcc_alive, _bcc_post
            if _bcc_alive(account_name):
                r = _bcc_post(account_name, "/resolve_url", {"url": url}, timeout=30)
                lid = r.get("live_id")
                if lid:
                    logger.info(f"[resolve] BCC /resolve_url 成功 live_id={lid}")
                    return lid, r.get("final_url")
                logger.warning(f"[LIVE-016] " + f"[resolve] BCC /resolve_url 返回失败: {r.get('msg', '')}，退回直开浏览器")
        except Exception as e:
            logger.warning(f"[LIVE-017] " + f"[resolve] BCC /resolve_url 异常，退回直开浏览器: {e}")

    from auto_dm import config as _cfg
    from auto_dm.vbrowser import should_use_vb, launch_sync

    # 2026-09-29（L-16）：单 profile 铁律 —— profile 必须由账号推导
    # （accounts.profile_dir_of(env_path)），**不得**回落到已废弃的字面量。
    # 实测原实现漏传 user_data_dir，launch_sync 会 fail-loud 抛错 ⇒ 该兜底
    # 路径实际不可用（真到需要时才发现）。此处按铁律补上推导。
    _prof = None
    if account_name:
        try:
            from auto_dm import accounts as _acc
            _prof = _acc.profile_dir_of(_acc.env_path_of(account_name))
        except Exception as _e:  # noqa: BLE001
            logger.warning(f"[LIVE-021] [resolve] 推导账号 profile 失败({account_name}): {_e}")
    if not _prof:
        logger.error("[LIVE-021] [resolve] 无账号名，无法推导固定 profile —— "
                     "浏览器兜底跳过（单 profile 铁律禁止临时目录）")
        return None, None

    final_url = None
    live_id = None
    try:
        _vb, _vb_mode = should_use_vb(_cfg)
        logger.info(f"[resolve] 使用指纹浏览器内核解析跳转 (mode={_vb_mode})")
        _pw, _browser, context, _backend = launch_sync(
            _vb_mode, _cfg, headless=headless, user_data_dir=_prof, account=account_name)
    except RuntimeError as e:
        logger.error(f"[LIVE-018] " + f"[resolve] 浏览器解析不可用（已禁用原生 Playwright，跳过浏览器解析）：{e}")
        return None, None
    page = context.new_page()
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        # 等待可能的跳转（用户主页 -> 直播间）
        for _ in range(20):
            page.wait_for_timeout(1000)
            u = page.url
            if "live.douyin.com" in u and "/user/" not in u:
                live_id = _extract_live_id_from_url(u)
                if live_id:
                    final_url = u
                    break
        # 若停在用户主页，尝试找“正在直播”的入口链接
        if not live_id:
            u = page.url
            m = re.search(r"douyin\.com/user/([^?/\s]+)", u)
            if m:
                sec_uid = m.group(1)
                # 在页面里找去直播间的链接
                links = page.eval_on_selector_all(
                    "a[href*='live.douyin.com']",
                    "els => els.map(e => e.href)")
                for link in (links or []):
                    lid = _extract_live_id_from_url(link)
                    if lid:
                        live_id = lid
                        final_url = link
                        break
                if not live_id:
                    logger.warning(f"[LIVE-019] " + f"[resolve] 用户 {sec_uid} 当前未在直播或无法解析房间")
    except Exception as e:
        logger.warning(f"[LIVE-020] " + f"[resolve] 浏览器解析失败: {e}")
    finally:
        # exe 模式 context 由我们 launch，需关闭；cdp 模式由外部客户端管理，不关。
        # （原生 Playwright 已禁用，无“原生模式”收尾分支）
        if _backend == "exe":
            try:
                context.close()
            except Exception:
                pass
    return live_id, final_url


def resolve_live_id(raw, headless=False, auth=None, account_name=None):
    """解析粘贴文本为 (live_id, source_url)。失败抛 ValueError。

    解析顺序：
      1) 纯数字/web_rid 直接用；
      2) 【主引擎】reflow 两步换发（带 X-Bogus）；
      3) 【备用】直接抠 URL 片段；
      4) 【备用】已登录浏览器兜底跳转。
    返回的 live_id 即为 web_rid（真实直播间号）。
    """
    raw = (raw or "").strip()
    if not raw:
        raise ValueError("链接为空")

    # 快速路径（直播流地址解析层优化，对齐 utlived-app 的本地 URL 抠取法）：
    # 纯 web_rid / 已是 live.douyin.com/<id> 直接返回，不做任何网络请求，零延迟。
    if re.fullmatch(r"[A-Za-z0-9_]+", raw):
        _cache_put(raw, (raw, raw))
        return raw, raw
    m = _LIVE_RE.search(raw)
    if m:
        _cache_put(raw, (m.group(1), raw))
        return m.group(1), raw
    # 直播页链接（www.douyin.com/follow/live/<web_rid> 等）零网络快速路径（T-01 / ENG-021）
    m = _LIVE_PAGE_RE.search(raw)
    if m:
        _cache_put(raw, (m.group(1), raw))
        return m.group(1), raw
    # 🔴 2026-10-01（用户实测报障）：房间号在 **query 参数 `live_web_rid`** 里的形态。
    # 例：搜索页分享的直播卡 https://www.douyin.com/search/<kw>?...&live_web_rid=291891133640&type=live
    # 零网络直接抠出（无需重定向/浏览器），与 _LIVE_PAGE_RE 同为「输入解析」层。
    for _re_qq in (_LIVE_QQ_RE, _LIVE_QQ_ALT_RE):
        m = _re_qq.search(raw)
        if m:
            _cache_put(raw, (m.group(1), raw))
            logger.info(f"[resolve] query 参数 live_web_rid 命中 web_rid={m.group(1)}")
            return m.group(1), raw

    # TTL 缓存命中（短链/用户主页的慢解析结果，秒回）
    cached = _cache_get(raw)
    if cached:
        return cached

    # 情形2：【主引擎】reflow 两步换发
    web_rid, anchor_sec_uid, source = resolve_via_reflow(raw, auth=auth)
    if web_rid:
        # 把主播 sec_uid 一并返回备用（调用方可从 source 之外的通道取，这里以日志提示）
        if anchor_sec_uid:
            logger.info(f"[resolve] 主播 sec_uid={anchor_sec_uid}（可直接用于私信/主页）")
        _cache_put(raw, (web_rid, source))
        return web_rid, source

    # 情形2·乙（2026-10-01 任务 1）：**reflow / 短链形态**经 `reflow/info` 桥接。
    #   背景（权威双源）：`/webcast/reflow/<id>` 带的是 **room_id**（DTK 明文注释 +
    #   DouyinLiveRecorder `room.py:61-66`），与 `live.douyin.com/<web_rid>` 是两套值
    #   ⇒ 不能直取；而 `v.douyin.com` / `v.amemv.com` / `iesdouyin.com/share/...` 既是
    #   官方常见分享形态、又**只能**靠重定向到达。
    #   原行为：这三类走「备用」→ 浏览器兜底 → 而浏览器兜底要求账号名推导 profile，
    #   解析端点若未传账号 ⇒ 必然失败（实测报 `LIVE-021 无账号名… 浏览器兜底跳过`）。
    #   ⇒ 本分支把它们路由到**已有**的 reflow 桥接（无需 GUI、无需账号即可拿到 web_rid）。
    _short = re.search(r"(?:v\.douyin\.com|v\.amemv\.com|iesdouyin\.com)/", raw)
    if _short:
        rid, sec = resolve_via_reflow(_follow_redirects(raw, auth=auth), auth=auth)
        if rid:
            _cache_put(raw, (rid, raw))
            logger.info(f"[resolve] 短链/reflow 桥接成功 web_rid={rid}"
                        f"{'（sec_uid=' + sec + '）' if sec else ''}")
            return rid, raw
    # 情形2·丙：输入本身即 `/webcast/reflow/<room_id>`
    _rf_room = _extract_reflow_room_id(raw)
    if _rf_room and auth is not None:
        rid, _sec = _reflow_resolve(_rf_room, "", auth=auth)
        if rid:
            _cache_put(raw, (rid, raw))
            logger.info(f"[resolve] reflow room_id={_rf_room} → web_rid={rid}")
            return rid, raw

    # 情形3/4：【备用】直接抠 URL 片段 / 浏览器兜底（保留原逻辑）
    live_id = _extract_live_id_from_url(raw)
    source = raw
    if not live_id:
        final = _follow_redirects(raw)
        source = final
        live_id = _extract_live_id_from_url(final)

    if not live_id:
        logger.info("[resolve] 尝试用浏览器解析（用户主页/需登录态）...")
        live_id, source = _browser_resolve(raw, headless=headless,
                                           account_name=account_name)

    if not live_id:
        raise ValueError(
            "无法从链接解析出直播间号。请确认：\n"
            " - 粘贴的是直播页链接 / 分享短链 / 用户主页；\n"
            " - 若为用户主页，该用户需正在直播；\n"
            " - 首次使用请先启动一次完成浏览器登录。")
    _cache_put(raw, (live_id, source))
    logger.info(f"[resolve] 备用解析成功 live_id={live_id} 来源={source}")
    return live_id, source
