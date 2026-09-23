"""BccCaptureMixin — 昵称捕获（P3-5 Step 4 提取）。

提取自 browser_daemon.py BrowserContainer，2026-09-23。

方法（均以 self 访问 BrowserContainer 实例属性）：
  capture_userinfo_map
"""

from __future__ import annotations

import asyncio
import os
import time

from loguru import logger


def _cache_unpack(cache):
    """兼容解包 (ts, data) 旧格式与 (ts, data, gen) 新格式。"""
    if cache is None:
        return 0.0, None, -1
    if len(cache) == 3:
        return cache[0], cache[1], cache[2]
    return cache[0], cache[1], -1


class BccCaptureMixin:
    """昵称捕获混入。"""

    # 从 browser_daemon 模块导入 JS 常量和人类化辅助（延迟加载避循环）
    _CAP_DOM_SWEEP_JS = None
    _CAP_USERINFO_HOOK_JS = None

    @staticmethod
    def _resolve_cap_js():
        """惰性加载 JS 常量（避循环 import）。"""
        if BccCaptureMixin._CAP_DOM_SWEEP_JS is not None:
            return
        try:
            from daemon.browser_daemon_js import CAP_DOM_SWEEP_JS
            BccCaptureMixin._CAP_DOM_SWEEP_JS = CAP_DOM_SWEEP_JS
        except ImportError:
            try:
                from browser_daemon_js import CAP_DOM_SWEEP_JS
                BccCaptureMixin._CAP_DOM_SWEEP_JS = CAP_DOM_SWEEP_JS
            except ImportError:
                BccCaptureMixin._CAP_DOM_SWEEP_JS = ""

    async def capture_userinfo_map(self, wait: int = 15,
                                   lease_id: str = "",
                                   internal: bool = False) -> dict:
        """被动 hook 截前端自己发的 im/user/info 响应（零主动请求、零风控）。

        复用本容器已持有的常驻浏览器 context/page（不另开浏览器、不抢 profile）。
        在 _lock 内执行，与 bulk_user_info/resolve_url 串行无冲突。
        返回 {sec_uid: {"nickname": str, "avatar": str, "uid": str}}。
        """
        BccCaptureMixin._resolve_cap_js()
        # ── 缓存命中检查（在 _lock 外）──
        _deadline = time.time() + (40.0 if internal else 0.0)
        while True:
            try:
                _ttl = int(os.environ.get("DY_USERINFO_CACHE_SEC", "600"))
            except Exception:
                _ttl = 600
            if _ttl > 0 and self._userinfo_cache:
                _ts, _data, _gen = _cache_unpack(self._userinfo_cache)
                if (_data and (time.time() - _ts) < _ttl
                        and _gen == self._context_generation):
                    logger.info(
                        f"[bcc] 复用昵称缓存（{len(_data)} 个，"
                        f"{time.time() - _ts:.0f}s 前采集，代次={_gen}），跳过滚动")
                    return _data
                if _gen != self._context_generation:
                    logger.warning(
                        f"[BCC-062] [bcc] 昵称缓存代次不符（缓存={_gen} "
                        f"当前={self._context_generation}，context 已重建），"
                        f"丢弃旧缓存并重新采集")
            if self._prewarm_running and time.time() < _deadline:
                await asyncio.sleep(1.0)
                continue
            break

        async def _do():
            # 导入人类化辅助（避循环 import）
            from daemon.browser_daemon import (
                _human_on, _human_click, _human_gap, _human_scroll_ratio)

            if "/chat" not in self._page.url:
                await self._page.goto(
                    "https://www.douyin.com/chat?isPopup=1",
                    wait_until="domcontentloaded", timeout=25000)
                await self._page.wait_for_timeout(2000)
            await self._page.wait_for_timeout(wait * 1000)

            _dom_seen = {}
            _clicked = set()

            async def _click_all():
                items = await self._page.query_selector_all(
                    ".conversationConversationItemwrapper")
                if _human_on():
                    import random as _r
                    try:
                        items = list(items)
                        _r.shuffle(items)
                    except Exception:
                        pass
                n_new = 0
                for idx, it in enumerate(items):
                    try:
                        key = (await it.inner_text())[:40]
                    except Exception:
                        key = f"__idx{idx}"
                    if key in _clicked:
                        continue
                    _clicked.add(key)
                    try:
                        if await _human_click(self._page, it, timeout=2000):
                            n_new += 1
                            await self._page.wait_for_timeout(
                                int(_human_gap(0.4, 0.6) * 1000))
                    except Exception:
                        pass
                return n_new

            _stall = 0
            _prev = -1
            import time as _time

            _t0 = _time.time()
            for _round in range(40):
                _n_new = await _click_all()

                _dnew = 0
                try:
                    _ditems = await self._page.evaluate(
                        BccCaptureMixin._CAP_DOM_SWEEP_JS)
                    for _dit in (_ditems or []):
                        _dn = (_dit or {}).get("nickname") or ""
                        if _dn and _dn not in _dom_seen:
                            _dom_seen[_dn] = {
                                "nickname": _dn,
                                "avatar": (_dit or {}).get("avatar") or "",
                                "desc": (_dit or {}).get("desc") or "",
                                "uid": "",
                                "sec_uid": "",
                            }
                            _dnew += 1
                except Exception as _de:
                    logger.warning(f"[BCC-055] [bcc] DOM 抓取失败: {_de}")
                _dom_total = len(_dom_seen)

                try:
                    _cur = await self._page.evaluate(
                        "() => window.__CAP_USERINFO__ "
                        "? Object.keys(window.__CAP_USERINFO__.map || {}).length : 0")
                except Exception:
                    _cur = -1
                logger.info(
                    f"[bcc] 滚动轮次 {_round + 1}: 新点击={_n_new} "
                    f"DOM累计昵称={_dom_total}(本屏+{_dnew}) "
                    f"hook={_cur} 用时={_time.time() - _t0:.1f}s")
                if _prev >= 0 and _dom_total <= _prev:
                    _stall += 1
                    if _stall >= 3:
                        logger.info(
                            f"[bcc] 昵称无新增（连续 {_stall} 轮，当前 "
                            f"DOM {_dom_total} 个），提前结束滚动"
                            f"（第 {_round} 轮，用时 {_time.time() - _t0:.1f}s）")
                        break
                else:
                    _stall = 0
                _prev = _dom_total

                moved = await self._page.evaluate(
                    "() => { const el = document.querySelector("
                    "'.conversationConversationListwrapper'); "
                    "if (!el) return false; "
                    f"const before = el.scrollTop; "
                    f"el.scrollTop = before + el.clientHeight * "
                    f"{_human_scroll_ratio():.3f}; "
                    "return el.scrollTop > before; }")
                await self._page.wait_for_timeout(1200)
                at_bottom = await self._page.evaluate(
                    "() => { const el = document.querySelector("
                    "'.conversationConversationListwrapper'); "
                    "if (!el) return true; "
                    "return el.scrollTop + el.clientHeight >= el.scrollHeight - 4; }")
                if at_bottom:
                    break
            await _click_all()
            try:
                _items = await self._page.evaluate(
                    BccCaptureMixin._CAP_DOM_SWEEP_JS)
                for _it in (_items or []):
                    _n2 = (_it or {}).get("nickname") or ""
                    if _n2 and _n2 not in _dom_seen:
                        _dom_seen[_n2] = {
                            "nickname": _n2,
                            "avatar": (_it or {}).get("avatar") or "",
                            "desc": (_it or {}).get("desc") or "",
                            "uid": "",
                            "sec_uid": "",
                        }
                logger.info(f"[bcc] DOM 末屏补充：累计昵称={len(_dom_seen)}")
            except Exception as _e:
                logger.warning(f"[BCC-055] [bcc] DOM 抓取失败: {_e}")

            cap = dict(_dom_seen)
            try:
                _hooked = await self._page.evaluate(
                    "() => window.__CAP_USERINFO__ ? window.__CAP_USERINFO__.map : {}")
                for _su, _v in (_hooked or {}).items():
                    _n3 = (_v or {}).get("nickname") or ""
                    if _n3 and _n3 not in cap:
                        _v = dict(_v)
                        _v["sec_uid"] = _su
                        cap[_n3] = _v
            except Exception as e:
                logger.warning(
                    f"[BCC-011] [bcc] 读取 hook 结果失败（DOM 兜底仍可用）: {e}")

            if cap:
                self._userinfo_cache = (time.time(), cap,
                                        getattr(self, "_context_generation", 0))
                logger.info(
                    f"[bcc] 昵称捕获完成：{len(cap)} 个，"
                    f"总耗时 {_time.time() - _t0 + wait:.1f}s（已缓存）")
            return cap

        if internal:
            return await self._exec(_do, holder="prewarm", internal=True)
        return await self._exec(_do, holder="capture_userinfo",
                                purpose="auto", prio=1, ttl=300.0,
                                lease_id=lease_id)