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


async def run_probe(dispatch, room_url, headless=False, interval=3.0, should_stop=None):
    """启动一个浏览器，定时扫描中控台评论区，把昵称推给 dispatch。

    should_stop: 可选 callable，返回 True 时立即停止扫描并退出（配合“停止”按钮）。

    2026-09-17 修补（OCR 审查 HIGH —— 声明却未使用的参数）：
    原签名有一个 `user_data_dir="pw_profile_probe"` 形参，但函数体**从未使用**
    它（launch_async 用的是账号级 profile）。该形参既是死代码，默认值还会
    误导读者以为"中控台采集用独立 profile"。现移除；若有调用方按位置传入，
    需改为关键字或删除（已确认仓库内无调用方依赖该形参）。
    """
    if not room_url:
        logger.warning(f"[BCC-043] " + "[web_probe] 未配置 WEB_PROBE_ROOM_URL，跳过中控台采集")
        return
    from auto_dm import config as _cfg
    from auto_dm.vbrowser import should_use_vb, launch_async

    # 禁止回退原生 Playwright：指纹内核不可用（should_use_vb / launch_async 抛错）时
    # 直接报错并跳过中控台采集，绝不降级到原生内核
    try:
        _vb, _vb_mode = should_use_vb(_cfg)
        logger.info(f"[web_probe] 使用指纹浏览器内核接管中控台采集 (mode={_vb_mode})")
        # 2026-09-13 环境门阀：中控台采集也须走账号代理（与 BCC 同环境）
        _pw, _browser, context, _backend = await launch_async(
            _vb_mode, _cfg, headless=headless, account=getattr(_cfg, "WEB_PROBE_ACCOUNT", None) or None)
    except RuntimeError as e:
        logger.error(f"[BCC-044] " + f"[web_probe] 中控台采集无法启动（已禁用原生 Playwright，不采集）：{e}")
        return
    page = context.pages[0] if context.pages else await context.new_page()
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
                        # ADR-007 / C-06（2026-09-24）：采集来源同样先沉淀
                        # （与直播共用同一张 dm_uid_sink，source="crawl"）。
                        try:
                            from services.dm_dispatch import get_dispatcher as _gd
                            _gd().uid_sink.mark_seen(
                                r.get("account") or r.get("acct") or "",
                                r.get("user_id") or r.get("uid") or "",
                                r.get("nickname") or "", "crawl",
                                r.get("comment") or "")
                        except Exception:
                            logger.debug('[SILENT-00] web_probe: mark_seen failed')
                        dispatch.submit(r)
            except Exception as e:
                logger.warning(f"[BCC-045] " + f"[web_probe] 扫描异常: {e}")
            await asyncio.sleep(interval)
    finally:
        # exe 模式浏览器由我们 launch，需关闭；cdp 模式由外部客户端管理，不关。
        # （原生 Playwright 已禁用，无“原生模式”收尾分支）
        if _backend == "exe" and _browser is not None:
            try:
                await _browser.close()
            except Exception:
                pass
