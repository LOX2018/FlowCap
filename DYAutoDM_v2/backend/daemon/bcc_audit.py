"""BccAuditMixin — 窗口可见性 + 环境审计（P3-5 Step 5 提取）。

提取自 browser_daemon.py BrowserContainer，2026-09-23。

方法（均以 self 访问 BrowserContainer 实例属性）：
  set_visible, _wait_window_visible, _window_really_visible, env_audit_snapshot
"""

from __future__ import annotations

import asyncio
import os
import time

from loguru import logger


# 可见性切换冷却期时长（秒）
_SWITCH_COOLDOWN_SEC = 180


class BccAuditMixin:
    """窗口可见性和环境审计混入。"""

    async def set_visible(self, visible: bool, url: str = "",
                          intent: str = "verify") -> dict:
        """切换容器可见性：把无头容器重启为有头可见（或反向）。

        ## 2026-09-25 v0.44.67【观测态契约落地】—— 用户指出的语义歧义

        同一端点原本承载两种语义完全不同的调用方：
          · 引擎校验（探活/自愈，intent=verify）：临时拉起验凭证，
            验完**可以**自动转无头；
          · 用户双击（/open-browser，intent=observe）：**观测态**，
            是给人看的窗口，**禁止任何自动关闭/自动转无头**，
            只能由用户手动结束。

        项目早有该契约（api/accounts.py:632：「有头是观测态，不是运行态」），
        但**只在打开前做了门禁，打开后没有任何标记** ⇒ 自愈/探活路径
        无从区分，可能顺手把用户眼前的窗口关掉。这就是「观测窗口被杀」的
        机制性成因。本次把契约**收口到状态**：`_observe_mode`。

        intent 取值：
          "observe" = 用户观测（双击/查看登录态）→ 置 _observe_mode=True
          "verify"  = 引擎校验/自愈            → 置 _observe_mode=False
        未传时默认 "verify"（保守：不擅自把自动路径变成受保护态）。

        ## 2026-09-25 v0.44.65：切换中的**幂等去重**（消「反复激活」）

        实测现象：点一次「打开指纹浏览器」后，后台 _launch 冷启动需 1~3 分钟；
        期间前端轮询/用户再点会**再次进入本函数**，原实现不看 `_switching`
        → 又销毁一次 context、又排一次 _do_switch_background
        → 多轮完整冷启动叠加 = 用户所见「反复激活 / 反复弹窗」，
          且**每轮在抖音侧都是一次全新环境**（风控暴露面）。

        现在：切换进行中且目标态一致 → 直接返回「已在切换中」，不重复排程。

        ## 修复3 的范围说明（诚实标注，勿扩大解读）

        理想态是「不重建 context，只改窗口状态」（零冷启动）。但**实测否决**：
        `_set_window_state_sync` 走 CDP `Browser.setWindowBounds`，而 Camoufox
        是 Firefox 内核走 juggler、非 CDP —— 实机返回 False（无头下无真实窗口
        可操作）。故本次**不改**为纯窗口操作，只做去重 + 修复1/2 消除根因残留。
        待内核侧提供有头可用的窗口操作通道后再推进零重建方案。
        """
        # ── 观测态标记（2026-09-25 v0.44.67 契约收口）──────────────────
        # 请求语义落到状态：之后所有自动关闭/自动转无头路径据此判定。
        _intent = str(intent or "verify").strip().lower()
        _observe = (_intent == "observe")
        prev_observe = getattr(self, "_observe_mode", False)
        self._observe_mode = _observe
        if _observe:
            logger.info(
                f"[bcc] {self.account} 进入【观测态】(intent=observe) —— "
                f"禁止自动关闭/自动转无头，仅用户手动结束；"
                f"凭证仍会照常观测回写（不中断）")
        elif prev_observe:
            logger.info(
                f"[bcc] {self.account} 退出【观测态】(intent={_intent}) —— "
                f"恢复自动关闭/自动转无头")

        async with self._lock:
            target = not bool(visible)
            # ── 切换中去重：同目标态的直接返回，绝不重复排程 ──
            if self._switching and self._headless == target:
                logger.info(
                    f"[bcc] {self.account} 可见性切换仍在进行中"
                    f"（headless->{target}），本次请求已去重，不重复冷启动"
                    f"（已切换 {time.time() - (self._switch_started_at or time.time()):.0f}s）")
                return {"ok": True, "headless": target, "changed": False,
                        "switching": True, "deduped": True,
                        "msg": "正在切换可见性中（已去重，未重复启动）"}
            if self._headless == target and self._context is not None:
                alive = False
                try:
                    if self._context.pages:
                        _pg = self._page if (self._page is not None
                                             and not self._page.is_closed()) \
                            else self._context.pages[0]
                        await _pg.evaluate("1")
                        alive = True
                except Exception as e:
                    logger.info(
                        f"[bcc] {self.account} 页面已失效（{type(e).__name__}），"
                        f"需重建 context 唤醒窗口")
                    alive = False
                if alive:
                    if url and self._page is not None and not self._page.is_closed():
                        try:
                            await self._page.goto(
                                url, wait_until="domcontentloaded", timeout=20000)
                        except Exception as e:
                            logger.warning(f"[BCC-038] [bcc] {self.account} 导航失败: {e}")
                    return {"ok": True, "headless": target, "changed": False}
            logger.info(
                f"[bcc] {self.account} 切换浏览器可见性: "
                f"headless={self._headless} -> {target}")
            _switch_mode = str(os.environ.get(
                "DY_BCC_SWITCH_MODE", "window")).strip().lower()
            _can_window_mode = (
                _switch_mode == "window" and self._backend in ("exe", "camoufox")
                and self._context is not None and not self._headless
            )
            if _switch_mode == "window" and self._backend in ("exe", "camoufox") \
                    and self._context is not None and not _can_window_mode:
                logger.info(
                    f"[bcc] {self.account} 容器当前为纯无头，"
                    f"「只改窗口状态」无效（headless 下无真实窗口）→ 走重建 context")
            if _can_window_mode:
                try:
                    from vbrowser import _set_window_state
                    _st = "normal" if target is False else "minimized"
                    _okw = await _set_window_state(self._context, _st)
                    if _okw:
                        self._headless = target
                        self._switch_cool_until = time.time() + 5.0
                        logger.info(
                            f"[bcc] {self.account} 可见性已切换为"
                            f"{'有头可见' if visible else '最小化'}（仅改窗口状态，"
                            f"未重建 context —— 业务不中断）")
                        if url and self._page is not None and not self._page.is_closed():
                            try:
                                await self._page.goto(
                                    url, wait_until="domcontentloaded", timeout=20000)
                            except Exception as e:
                                logger.warning(
                                    f"[BCC-038] [bcc] {self.account} 导航失败: {e}")
                        return {"ok": True, "headless": target, "changed": True,
                                "switching": False, "mode": "window",
                                "msg": f"已切换为{'有头可见' if visible else '窗口最小化'}"}
                    logger.info(
                        f"[bcc] {self.account} 窗口状态设置失败，"
                        f"回退到重建 context 流程")
                except Exception as e:
                    logger.warning(
                        f"[BCC-035] [bcc] {self.account} 窗口状态切换异常"
                        f"（回退重建）: {e}")
            # 关旧 context
            try:
                if self._backend in ("exe", "camoufox") and self._context is not None:
                    if self._backend == "camoufox":
                        from vbrowser_camoufox import close_camoufox_context
                        await close_camoufox_context(self._context, getattr(self, "_profile_dir", None))
                    else:
                        await self._context.close()
                if self._pw is not None:
                    await self._pw.stop()
            except Exception:
                pass
            self._pw = None
            self._browser = None
            self._context = None
            self._page = None
            self._nav_page = None
            self._diag_page = None
            self._headless = target
            self._switching = True
            self._switch_started_at = time.time()
            asyncio.get_event_loop().create_task(
                self._do_switch_background(target, url, headless=target))
            mode_str = "有头可见" if visible else "纯无头"
            # ══════════ 2026-09-25 v0.44.67【假阳性根治】════════════════
            # 原实现把「已受理请求」冒充「已达成」：这里立即返回 ok:True +
            # "正在切换为有头可见，窗口就绪后自动完成"，而真正的重建在后台
            # _do_switch_background 里跑，**失败只写日志，前端永远收不到**。
            # 实测事故：用户看到弹窗「成功」但屏幕上没有窗口（后台重建
            # 实际以 BCC-058 失败）。
            #
            # 修法：返回语义必须自证其然 ——
            #   ok        = 请求已受理（不是"已达成"）
            #   accepted  = True  显式标记"受理态"
            #   switching = True  明确告知"仍在切换"
            #   settled   = False 可见性**尚未达成**
            #   msg       不再出现任何"已显示/已完成"字样
            # 调用方（/open-browser）须据 settled/switching 展示「进行中」，
            # 并以 /status 轮询真实结果，不得直接宣称成功。
            return {"ok": True, "accepted": True, "settled": False,
                    "headless": target, "changed": True,
                    "switching": True,
                    "observe": _observe,
                    "msg": f"已受理：正在切换为{mode_str}（后台冷启动约 1~3 分钟）"
                           f"—— 尚未完成，请以实际窗口或 /status 为准"}

    # 可见窗口出现前的轮询参数
    _VISIBLE_POLL_EVERY = 3.0
    _VISIBLE_POLL_MAX = 90.0

    async def _wait_window_visible(self, target: bool) -> bool | None:
        """有头重建后，轮询确认 OS 层出现可见窗口。"""
        deadline = time.time() + self._VISIBLE_POLL_MAX
        attempt = 0
        while True:
            attempt += 1
            try:
                vis = await self._window_really_visible()
            except Exception as e:
                logger.warning(
                    f"[bcc] {self.account} 可见窗口检测异常（不判定）: "
                    f"{type(e).__name__}: {e}")
                return None
            if vis:
                if attempt > 1:
                    logger.info(
                        f"[bcc] {self.account} 可见窗口在第 {attempt} 次轮询"
                        f"（约 {attempt * self._VISIBLE_POLL_EVERY:.0f}s）后确认")
                return True
            if time.time() >= deadline:
                return False
            await asyncio.sleep(self._VISIBLE_POLL_EVERY)

    async def _do_switch_background(self, target: bool, url: str = "",
                                    headless: bool | None = None) -> None:
        """后台执行可见性切换的 _launch 部分。"""
        try:
            await self._launch(headless=headless)
            self._switch_cool_until = time.time() + _SWITCH_COOLDOWN_SEC
            if target is False:
                _vis = await self._wait_window_visible(target)
                if _vis is True:
                    logger.info(
                        f"[bcc] {self.account} 可见性切换完成："
                        f"OS 层已确认存在可见窗口")
                elif _vis is False:
                    logger.error(
                        f"[BCC-053] [bcc] {self.account} 可见性切换后"
                        f"（最长等待 {self._VISIBLE_POLL_MAX:.0f}s）"
                        f"OS 层仍未检测到可见窗口 —— 判定切换未达成，"
                        f"状态回退为无头（请检查内核参数/是否仍为 native headless）")
                    self._headless = True
                else:
                    logger.warning(
                        f"[bcc] {self.account} 可见性切换完成，但 OS 层可见性"
                        f"**无法确认**（检测函数据取不到证据）—— 保留当前可见性意图，"
                        f"不擅自回退；请以屏幕实际窗口为准")
            logger.info(
                f"[bcc] {self.account} 可见性切换完成(headless={target})，"
                f"进入 {_SWITCH_COOLDOWN_SEC}s 切换冷却期（探活只告警不强杀）")
            if url and self._page is not None and not self._page.is_closed():
                try:
                    await self._page.goto(
                        url, wait_until="domcontentloaded", timeout=20000)
                except Exception as e:
                    logger.warning(
                        f"[BCC-039] [bcc] {self.account} 切换后导航失败: {e}")
        except Exception as e:
            logger.error(
                f"[BCC-006] [bcc] 可见性切换失败(切换为{'有头' if target else '无头'}): {e} —— "
                f"将在冷却期后由探活自愈（不自动重启，防误杀）", exc_info=True)
        finally:
            self._switching = False
            self._switch_started_at = 0.0

    async def _window_really_visible(self) -> bool:
        """实机校验：本容器的 chromium 在操作系统层面是否有可见窗口。"""
        try:
            import ctypes
            from ctypes import wintypes
        except Exception:
            return False
        pids = set()
        try:
            _b = getattr(self, "_browser", None) or getattr(self._context, "browser", None)
            if _b is not None:
                _proc = getattr(_b, "process", None) or getattr(_b, "_process", None)
                if _proc is not None and getattr(_proc, "pid", None):
                    pids.add(int(_proc.pid))
        except Exception as _e_pid:
            logger.warning(f"[bcc] 获取浏览器 PID 异常: {_e_pid}")
        _names_seen = set()
        if not pids and getattr(self, "_profile_dir", None):
            _prof = str(self._profile_dir).replace("\\", "/")
            _mem = ""
            try:
                _m = __import__("re").search(r"/members/([^/]+)/", _prof)
                if _m:
                    _mem = _m.group(1)
            except Exception:
                _mem = ""
            _stable = [s.lower() for s in (_mem, "_camoufox") if s]

            def _cmdline_hit(_c: str) -> bool:
                if not _stable:
                    return False
                _lc = str(_c or "").lower()
                return all(s in _lc for s in _stable)

            try:
                import psutil as _psu
                for _p in _psu.process_iter(["pid", "name", "cmdline"]):
                    try:
                        _names_seen.add(_p.info.get("name") or "")
                        _cl = " ".join(_p.info.get("cmdline") or [])
                        if _cmdline_hit(_cl):
                            pids.add(int(_p.info["pid"]))
                    except Exception:
                        continue
            except Exception as _e1:
                logger.debug(
                    f"[bcc] psutil PID 兜底不可用: {type(_e1).__name__}: {_e1}")
            if not pids:
                try:
                    import json as _json
                    import subprocess as _sp
                    _ps = (
                        'Get-CimInstance Win32_Process | Where-Object '
                        '{$_.CommandLine -like "*_camoufox*" -and '
                        '$_.CommandLine -notlike "*--type=*" } | '
                        'Select-Object ProcessId,Name,CommandLine | ConvertTo-Json -Compress'
                    )
                    _r = _sp.run(
                        ["powershell", "-NoProfile", "-Command", _ps],
                        capture_output=True, timeout=25)
                    _out = (_r.stdout or b"").decode("utf-8", errors="replace")
                    _d = _json.loads(_out) if _out.strip() else []
                    if isinstance(_d, dict):
                        _d = [_d]
                    for _it in _d:
                        _names_seen.add(str(_it.get("Name") or ""))
                        if not _it.get("ProcessId"):
                            continue
                        if _cmdline_hit(str(_it.get("CommandLine") or "")):
                            pids.add(int(_it["ProcessId"]))
                except Exception as _e2:
                    logger.debug(
                        f"[bcc] PowerShell PID 兜底不可用: {type(_e2).__name__}: {_e2}")
            if not pids:
                logger.warning(
                    f"[bcc] {getattr(self, 'account', '?')} 可见窗口检测：未能解析到"
                    f"本容器进程 PID（兜底未命中；进程名样本="
                    f"{sorted(n for n in _names_seen if n)[:8]}，匹配片段={_stable}）"
                    f" —— 本次判为不可见，请检查进程名/匹配片段是否漂移")
        if not pids:
            return False
        u32 = ctypes.WinDLL("user32", use_last_error=True)
        EnumWindows = u32.EnumWindows
        EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        IsWindowVisible = u32.IsWindowVisible
        GetClassNameW = u32.GetClassNameW
        GetWindowThreadProcessId = u32.GetWindowThreadProcessId
        hit = {"n": 0}

        def _cb(hwnd, lparam):
            pid = wintypes.DWORD()
            GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value in pids:
                cbuf = ctypes.create_unicode_buffer(256)
                GetClassNameW(hwnd, cbuf, 256)
                _cls = cbuf.value or ""
                _is_browser_win = ("Chrome_WidgetWin" in _cls
                                   or "MozillaWindowClass" in _cls)
                if _is_browser_win and IsWindowVisible(hwnd):
                    hit["n"] += 1
            return True

        try:
            EnumWindows(EnumWindowsProc(_cb), 0)
        except Exception:
            return False
        return hit["n"] > 0

    async def env_audit_snapshot(self, internal: bool = False) -> dict:
        """采集浏览器环境真值并对照项目档案做泄漏检测。"""
        from services.env_audit import ENV_AUDIT_JS, compare_with_profile

        async def _do():
            if self._page is None:
                raise RuntimeError("page 未就绪")
            view = await self._page.evaluate(ENV_AUDIT_JS)
            return compare_with_profile(self.account, view or {})

        return await self._exec(_do, holder="env_audit", internal=internal)