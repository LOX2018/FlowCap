# coding=utf-8
"""中控台浏览器 DOM 扫描采集（补充/兜底来源，非必需，已不推荐）。

【重要澄清】直播间“评论区/公屏评论”与“弹幕”在抖音后端是同一个 WebSocket 流
（WebcastChatMessage），已由 live_hook 完整接收并推给调度中心——这才是评论区的
真实、稳定来源。本模块的 DOM 扫描只是 DYchajian 旧思路的兜底，依赖中控台页面
渲染出 messageItem/chatItem 这类 DOM，且必须配置 WEB_PROBE_ROOM_URL 才会启动；
选择器常与真实 DOM 对不上，稳定性差，平时建议保持关闭（ENABLE_WEB_PROBE=False）。

注意：浏览器采集只能拿到昵称（拿不到数字 uid），因此推给 dispatch 时
只有 nickname，sender 会 fallback 到 send_by_secuid（get_user_info 查询）。
弹幕来源（live_hook）自带 uid 是主路径，本模块作补充/兜底。
"""

import asyncio
import time
from loguru import logger

from playwright.async_api import async_playwright

# ---- 内联选择器（来自 DYchajian selectors.py）----
COMMENT_ROW = [
    "[class*='messageItem']", "[class*='MessageItem']",
    "[class*='chatItem']", "[class*='ChatItem']",
    "[class*='commentItem']", "[class*='CommentItem']",
    "[class*='interactItem']", "[class*='interactionItem']",
    "[class*='chatList']", "[class*='ChatList']",
    "[class*='bullet']", "[class*='danmu']",
]
COMMENT_NICKNAME = [
    "[class*='chatItemNickName']", "[class*='ChatItemNickName']",
    "[class*='nickname']", "[class*='NickName']",
    "[class*='userName']", "[class*='UserName']", "[class*='user-name']",
]
COMMENT_TEXT = [
    "[class*='chatItemDesc']", "[class*='chatItemContent']",
    "[class*='ChatItemContent']", "[class*='commentText']",
    "[class*='CommentText']", "[class*='messageContent']", "[class*='MessageContent']",
]


def _text_of(el):
    try:
        return (el.inner_text() or "").strip()
    except Exception:
        return ""


def scan(page):
    row_selector = None
    for sel in COMMENT_ROW:
        try:
            if page.locator(sel).count() > 0:
                row_selector = sel
                break
        except Exception:
            continue
    if not row_selector:
        return []
    rows = page.locator(row_selector)
    total = min(rows.count(), 80)
    records = []
    seen = set()
    for i in range(total):
        row = rows.nth(i)
        nick = ""
        for sel in COMMENT_NICKNAME:
            try:
                ne = row.locator(sel).first
                if ne.count() > 0:
                    nick = _text_of(ne)
                    break
            except Exception:
                continue
        if not nick or nick in seen:
            continue
        seen.add(nick)
        comment = ""
        for sel in COMMENT_TEXT:
            try:
                ce = row.locator(sel).first
                if ce.count() > 0:
                    comment = _text_of(ce)
                    if comment and comment != nick:
                        break
            except Exception:
                continue
        records.append({"nickname": nick, "comment": comment, "user_id": None, "sec_uid": None})
    return records


async def run_probe(dispatch, room_url, user_data_dir="pw_profile_probe", headless=False, interval=3.0, should_stop=None):
    """启动一个浏览器，定时扫描中控台评论区，把昵称推给 dispatch。

    should_stop: 可选 callable，返回 True 时立即停止扫描并退出（配合“停止”按钮）。
    """
    if not room_url:
        logger.warning("[web_probe] 未配置 WEB_PROBE_ROOM_URL，跳过中控台采集")
        return
    from auto_dm import config as _cfg
    from auto_dm.vbrowser import should_use_vb, launch_async

    _vb, _vb_mode = should_use_vb(_cfg)
    _pw = None
    _browser = None
    _backend = None
    if _vb:
        logger.info(f"[web_probe] 使用指纹浏览器内核接管中控台采集 (mode={_vb_mode})")
        _pw, _browser, context, _backend = await launch_async(_vb_mode, _cfg, headless=headless)
        if _backend is None:
            logger.warning("[web_probe] 指纹内核启动失败，回退原生 Playwright")
            _vb = False
    if not _vb:
        _pw = await async_playwright().start()
        _browser = await _pw.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"])
        context = await _browser.new_context(
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"))
    if not _vb:
        page = await context.new_page()
    try:
        logger.info(f"[web_probe] 打开中控台 {room_url}")
        await page.goto(room_url, wait_until="domcontentloaded", timeout=60000)
        # 等待登录（简化：停留直到评论区出现）
        await asyncio.sleep(5)
        while True:
            if should_stop and should_stop():
                logger.info("[web_probe] 收到停止信号，关闭中控台采集。")
                break
            try:
                recs = scan(page)
                for r in recs:
                    if r.get("nickname"):
                        dispatch.submit(r)
            except Exception as e:
                logger.warning(f"[web_probe] 扫描异常: {e}")
            await asyncio.sleep(interval)
    finally:
        # exe 模式浏览器由我们 launch，需关闭；cdp 模式由外部客户端管理，不关；原生模式关闭
        if _backend == "exe" and _browser is not None:
            try:
                await _browser.close()
            except Exception:
                pass
        elif not _vb and _browser is not None:
            try:
                await _browser.close()
            except Exception:
                pass
