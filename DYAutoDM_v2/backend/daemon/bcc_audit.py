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

    async def set_visible(self, visible: bool, url: str = "") -> dict:
        """切换容器可见性：把无头容器重启为有头可见（或反向）。"""
        async with self._lock:
            target = not bool(visible)
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
                        await close_camoufox_context(self._context)
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
            return {"ok": True, "headless": target, "changed": True,
                    "switching": True,
                    "msg": f"正在切换为{mode_str}，"
                           f"窗口就绪后自动完成（冷启动约 1~3 分钟）"}

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