"""BccLoginMixin — 登录/凭证管理（P3-5 Step 3 提取）。

提取自 browser_daemon.py BrowserContainer，2026-09-23。

方法清单（均以 self 访问 BrowserContainer 实例属性）：
  scan_login, refresh_cookie_to_env, _read_page_sign, run_keepalive,
  _refresh_page_for_session, _page_login_state_sync, _load_uid_from_env
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from typing import Any

from loguru import logger

from daemon.bcc_lease import (
    _lease_status,
    _scan_exclusive_set,
    LEASE_PRIO_TTL_LIMIT,
)


class BccLoginMixin:
    """登录/凭证管理混入。"""

    async def _read_page_sign(self) -> dict:
        """从当前页面读取 security-sdk 的最新签名数据（web_protect / keys）。

        返回 {"web_protect": str, "keys": str}；任一缺失返回 {}（视为失败）。
        在 _lock 内执行（浏览器串行铁律）。
        """
        async def _do():
            page = self._page
            if page is None or page.is_closed():
                return {}
            keys_str = None
            wp_str = None
            for _ in range(4):
                try:
                    keys_str = await page.evaluate(
                        'localStorage["security-sdk/s_sdk_crypt_sdk"]')
                    wp_str = await page.evaluate(
                        'localStorage["security-sdk/s_sdk_sign_data_key/web_protect"]')
                except Exception:
                    return {}
                if keys_str and wp_str:
                    break
                try:
                    await page.mouse.wheel(0, 600)
                except Exception:
                    pass
                await asyncio.sleep(1.5)
            if not (keys_str and wp_str):
                return {}
            return {"web_protect": wp_str, "keys": keys_str}

        return await self._exec(_do)

    async def refresh_cookie_to_env(self, lease_id: str = "",
                                    internal: bool = False) -> dict:
        """读实时 cookie，写回 .env。返回 {ok, cookie_count, sessionid?}。"""
        from auto_dm import accounts as _acc
        from dy_apis.login_api import DYLoginApi
        from dy_apis.douyin_api import DouyinAPI
        env_path = _acc.env_path_of(self.account)
        if not env_path:
            return {"ok": False, "msg": "账号 .env 未登记"}
        auth = DYLoginApi._load_auth_from_env(env_path)
        cks = await self.get_cookies(lease_id=lease_id, internal=internal)
        if not (cks.get("sessionid") or cks.get("sid_tt")):
            return {"ok": False, "msg": "profile 内无登录态"}

        # ---- P0 门禁 1：新 cookie 必须能探活出 uid ----
        probe_auth = DYLoginApi._load_auth_from_env(env_path)
        probe_auth.cookie = cks
        probe_auth.cookie_str = "; ".join(f"{k}={v}" for k, v in cks.items())
        probe_auth.uid = None
        now_ts = time.time()
        # 2026-09-23（审计 P4 死代码）：原为
        #   getattr(BrowserContainer_instance_hack if False else type(self), "_uid_probe_cache", None)
        # 引用**不存在**的名 `BrowserContainer_instance_hack`，仅靠 `if False` 短路才不 NameError；
        # 下一行才是正解。死分支会误导读者以为这是有效属性，故删除、只保留真正那一行。
        cls = type(self)
        cached = getattr(cls, "_uid_probe_cache", None)
        new_uid = None
        if cached and now_ts - cached[0] < 60:
            new_uid = cached[1]
        else:
            try:
                new_uid = DouyinAPI.get_my_uid(probe_auth, force_probe=True)
            except Exception as _uid_e:
                logger.warning(f"[BCC-015] [bcc] 探活 uid 异常: {_uid_e}")
                new_uid = None
            cls._uid_probe_cache = (now_ts, new_uid)
        if not new_uid:
            logger.warning(
                f"[BCC-015] [bcc] 拒绝写入 .env：新 cookie 探活失败（无 uid），"
                f"保留既有凭证。疑似 profile 登录态失效，请重新扫码。")
            return {"ok": False, "msg": "新 cookie 探活失败（登录态无效），已保留原凭证"}

        # ---- P0 门禁 1.5：探活 uid 必须与该账号历史会话一致 ----
        try:
            from services.uid_probe import _uid_consistent_with_history as _uid_ok
            if not _uid_ok(self.account, new_uid):
                logger.error(
                    f"[BCC-016] [bcc] 拒绝写入 .env：探活 uid={new_uid} "
                    f"与该账号「{self.account}」"
                    f"历史会话不一致（幽灵 uid，0 命中）。profile 登录态疑似"
                    f"失效/残留他人凭证，请重新扫码登录本账号。")
                return {"ok": False,
                        "msg": f"探活 uid={new_uid} 与历史会话不一致，"
                               f"疑似残留凭证，已拒绝写入（请重新扫码）"}
        except Exception:
            pass

        # ---- P0 门禁 1.6：会话活性必须被服务端承认 ----
        _sess_ok = True
        _sess_detail = ""
        try:
            from auto_dm.accounts import live_session_state as _lss
            _sess_state, _sess_detail = _lss(self.account, auth=probe_auth,
                                             force=True)
            if _sess_state is False:
                _sess_ok = False
        except Exception as _e_ss:
            _sess_state, _sess_detail = None, f"会话活性探测异常（保守放行）: {_e_ss}"
        if not _sess_ok:
            logger.warning(
                f"[BCC-071] [bcc] 新 cookie 的会话未被服务端承认"
                f"（{_sess_detail}）—— 先就地刷新页面再复验，避免把过期会话固化进 .env。")
            _refreshed = False
            try:
                _refreshed = await self._refresh_page_for_session()
            except Exception as _e_rf:
                logger.debug(f"[BCC-071] 页面刷新异常（继续复验）: {_e_rf}")
            if _refreshed:
                try:
                    cks2 = await self.get_cookies(lease_id=lease_id, internal=internal)
                    if cks2.get("sessionid") or cks2.get("sid_tt"):
                        probe_auth.cookie = cks2
                        probe_auth.cookie_str = "; ".join(
                            f"{k}={v}" for k, v in cks2.items())
                        probe_auth.uid = None
                        _st2, _dt2 = _lss(self.account, auth=probe_auth, force=True)
                        if _st2 is True:
                            cks = cks2
                            _sess_ok, new_uid = True, new_uid
                            logger.info(
                                f"[BCC-071] [bcc] 就地刷新后会话已被服务端承认，"
                                f"继续写回 .env（{_dt2}）")
                        else:
                            _sess_detail = _dt2 or _sess_detail
                except Exception as _e_rc:
                    logger.debug(f"[BCC-071] 刷新后复验异常: {_e_rc}")
            if not _sess_ok:
                logger.warning(
                    f"[BCC-071] [bcc] 就地刷新后仍未获承认，本轮放弃写入 .env"
                    f"（保留既有凭证）。keepalive 将立即重试。")
                return {"ok": False, "uid": str(new_uid), "session_ok": False,
                        "retry_now": True,
                        "msg": f"会话未被服务端承认，本轮未写入（{_sess_detail}）"}

        # ---- P0 门禁 2：uid 与既有值一致性 ----
        old_uid = getattr(self, "_uid_at_last_env_write", None)
        if old_uid and str(old_uid) != str(new_uid):
            logger.error(
                f"[BCC-017] [bcc] 拒绝写入 .env：uid 漂移！old={old_uid} new={new_uid}。"
                f"疑似账号身份被轮换/替换，保留既有凭证并告警。")
            return {"ok": False,
                    "msg": f"uid 漂移({old_uid}→{new_uid})，已保留原凭证，请重新扫码确认"}

        # ---- 签名必须随 cookie 一起刷新 ----
        try:
            _fresh = await self._read_page_sign()
            if _fresh:
                auth.web_protect_str = _fresh.get("web_protect") or auth.web_protect_str
                auth.keys_str = _fresh.get("keys") or getattr(auth, "keys_str", "")
                try:
                    auth.perepare_auth("", auth.web_protect_str, auth.keys_str)
                except Exception:
                    pass
                _has_sdk = bool(getattr(auth, "ticket", None) or
                                getattr(auth, "ts_sign", None))
                logger.info(
                    f"[bcc] 观测态写回：已同步页面最新签名"
                    f"（web_protect={'有' if auth.web_protect_str else '无'}, "
                    f"keys={'有' if auth.keys_str else '无'}, "
                    f"ticket={'有' if _has_sdk else '无'}）")
            else:
                logger.debug("[bcc] 观测态写回：未读到页面签名，沿用既有签名")
        except Exception as e:
            logger.debug(f"[bcc] 观测态写回：读取页面签名失败（沿用既有）: {e}")

        auth.cookie = cks
        auth.cookie_str = "; ".join(f"{k}={v}" for k, v in cks.items())
        _write_ok = True
        _write_err = ""
        try:
            DYLoginApi().save_credential(auth, env_path)
        except Exception as e:
            _write_ok = False
            _write_err = f"{type(e).__name__}: {e}"
            logger.warning(f"[BCC-018] [bcc] 写回 .env 失败: {e}")
        if not _write_ok:
            return {"ok": False, "cookie_count": len(cks), "uid": str(new_uid),
                    "msg": f"凭证写盘失败，.env 未更新（基线未推进）: {_write_err}",
                    "write_failed": True}
        self._last_refresh = time.time()
        self._last_uid = new_uid
        self._uid_at_last_env_write = new_uid
        return {"ok": True, "cookie_count": len(cks), "uid": str(new_uid),
                "sessionid": cks.get("sessionid", "")[:12],
                "cookies": "; ".join(f"{k}={v}" for k, v in cks.items()),
                "cookie_dict": cks}

    async def scan_login(self, force: bool = False, timeout: int = 300) -> dict:
        """扫码登录/刷新凭证。"""
        from dy_apis.login_api import DYLoginApi
        from auto_dm import accounts as _acc
        env_path = _acc.env_path_of(self.account)
        if not env_path:
            return {"ok": False, "msg": "账号 .env 未登记"}

        async def _do():
            # 2026-09-23（审计 P0-3）：本处原**再**取一次租约：
            #     _sl_lease = _lease_acquire("scan_login", "exclusive", 0, ttl=…)
            # 但外层 `self._exec(_do)` 已经以 `holder="bcc-internal"`(prio=2,ttl=30)
            # 持有租约，而 `_lease_acquire` 对「不同 holder」一律拒绝（不做真抢占）
            # ⇒ 实测返回 `{'ok': False, 'busy': 'bcc-internal'}` ⇒ `_sl_lid=None`
            # ⇒ **scan_login 的 P0 独占租约从未建立**（与 2ce728a 同源：拆分引入的
            # 跨模块边界事故）。
            #
            # 修法（三者择一）：把两个租约**串成同一件事** —— 不再内层重取，改为由
            # 外层 `_exec` **以 P0（prio=0，用户显式，ttl=LEASE_PRIO_TTL_LIMIT[0]）
            # 取唯一的那把租约**，`_do` 在其保护下执行。为什么这满足原意图：
            #   · 「P0 独占」的语义取自 prio=0 的 TTL 上限（600s，覆盖扫码整轮），
            #     外层按该 prio 持租后，低优先级调用方（业务 prio=1 / 内部 prio=2）
            #     对同一把租约同样被拒，独占语义不变；
            #   · `_lease_acquire` 对「同 holder 重入」会复用，故内层再取本就多余，
            #     删除它只是去掉冗余，**不是**「去掉租约」（否则才是把 P0 独占变成无租约）。
            _scan_exclusive_set("scan_login")
            try:
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
                api = DYLoginApi()
                auth = await api.get_login_auth(
                    headless=False, env_path=env_path, force=force,
                    landing_url="https://www.douyin.com/chat?isPopup=1")
                ok = False
                _uid = None
                if auth and getattr(auth, "cookie", None):
                    try:
                        from services.uid_probe import (
                            get_uid as _uid_get,
                            _uid_consistent_with_history as _uid_ok)
                        _uid = _uid_get(self.account, force=True)
                        if _uid and _uid_ok(self.account, _uid):
                            ok = True
                        else:
                            logger.warning(
                                f"[BCC-036] [bcc] 刷新后凭证仍未通过校验"
                                f"（uid={_uid}，与历史会话不一致或探活为空）"
                                f"—— 判定为仍需人工扫码，不再视为成功")
                    except Exception as e:
                        logger.warning(
                            f"[BCC-036] [bcc] 刷新后凭证校验异常，判为未成功: {e}")

                if ok:
                    try:
                        from services.env_baseline import record_baseline as _rec_base
                        _b = _rec_base(self.account, source="scan_login")
                        if _b:
                            logger.info(
                                f"[bcc] 环境基线已记录: ip={_b['ip']} mode={_b['mode']} country={_b['country']}")
                    except Exception as _e:
                        logger.debug(f"[bcc] 环境基线记录失败（不影响登录）: {_e}")
                else:
                    logger.debug(
                        "[bcc] 凭证未通过校验，跳过环境基线记录（防把失效环境当基线）")
                await self._launch()
                return {"ok": ok, "uid": _uid}
            finally:
                _scan_exclusive_set(None)

        # 2026-09-23（审计 P0-3）：`_do` 不再内层重取租约 —— 由本调用**以 P0 取
        # 唯一那把租约**（prio=0 用户显式，ttl=600s 覆盖扫码整轮），租约在 _do
        # 全周期内有效，_exec 在 finally 统一释放。holder="scan_login" 与日志/语义一致。
        return await self._exec(
            _do, holder="scan_login", purpose="exclusive", prio=0,
            ttl=LEASE_PRIO_TTL_LIMIT[0])

    def _load_uid_from_env(self) -> Any:
        """从 .env 读当前 uid（保活用）。"""
        from auto_dm import accounts as _acc
        from dy_apis.login_api import DYLoginApi
        from dy_apis.douyin_api import DouyinAPI
        env_path = _acc.env_path_of(self.account)
        if not env_path:
            return None
        try:
            auth = DYLoginApi._load_auth_from_env(env_path)
            if auth and auth.cookie:
                try:
                    from services.uid_probe import get_uid as _uid_get
                    _u = _uid_get(self.account)
                    if _u is not None:
                        return _u
                except Exception:
                    pass
                try:
                    from services.uid_probe import (
                        _uid_consistent_with_history as _uid_ok)
                    _fallback_uid = DouyinAPI.get_my_uid(auth)
                    if _fallback_uid and _uid_ok(self.account, _fallback_uid):
                        return _fallback_uid
                    if _fallback_uid:
                        logger.warning(
                            f"[BCC-014] [bcc] 账号「{self.account}」裸探活 uid={_fallback_uid} "
                            f"与历史会话不一致，判为不可信（拒绝返回）")
                    return None
                except Exception:
                    return DouyinAPI.get_my_uid(auth)
        except Exception:
            pass
        return None

    def _page_login_state_sync(self) -> dict:
        """页面级登录态检查（keepalive 用，同步包装 async exec_js）。"""
        import asyncio as _aio

        async def _probe():
            try:
                res = await self.exec_js(
                    "() => ({"
                    " conv: document.querySelectorAll('.conversationConversationItemwrapper').length,"
                    " rel: /一键登录|扫码登录|二维码失效/.test(document.body.innerText || ''),"
                    " url: location.href.slice(0, 60)})", timeout=15)
                if isinstance(res, dict):
                    return res
            except Exception as e:
                logger.debug(f"[bcc] 页面登录态探测异常: {e}")
            return None

        try:
            loop = self._loop or asyncio.get_event_loop()
            fut = _aio.run_coroutine_threadsafe(_probe(), loop) \
                if loop.is_running() else _aio.ensure_future(_probe())
            got = fut.result(timeout=25)
            if isinstance(got, dict):
                return got
            logger.debug(
                f"[BCC-073] [bcc] 页面级探针无结论（返回 {type(got).__name__}）—— 记为未知")
            return {"unknown": True}
        except Exception as e:
            logger.debug(
                f"[BCC-073] [bcc] 页面级探针取不到证据（{type(e).__name__}）—— 记为未知，"
                f"不得据以判定页面失效，也不影响凭证回写")
            return {"unknown": True}

    async def _refresh_page_for_session(self) -> bool:
        """就地让页面重载一次，促使 passport 用当前 profile 的会话换发新 cookie。"""
        async def _do() -> bool:
            page = self._page
            if page is None or page.is_closed():
                return False
            try:
                if not getattr(self, "_headless", True):
                    logger.debug("[bcc] 观测态（有头）跳过会话刷新，避免打断用户")
                    return False
                _is_real = None
                try:
                    from dy_apis.login_api import DYLoginApi as _LA
                    _is_real = _LA._is_real_login
                    if not await _is_real(self._context):
                        return False
                except Exception:
                    _is_real = None
                await page.reload(wait_until="domcontentloaded", timeout=20000)
                for _ in range(8):
                    await page.wait_for_timeout(1000)
                    if _is_real is None:
                        break
                    try:
                        if await _is_real(self._context):
                            break
                    except Exception:
                        break
                return True
            except Exception as e:
                logger.debug(f"[bcc] 会话刷新失败（不阻塞）: {e}")
                return False
        return await self._exec(_do, internal=True)

    def run_keepalive(self, stop_ev: threading.Event, interval: int = 300) -> None:
        """每 interval 秒探活一次；uid 探活失败或漂移时触发刷新/告警。"""
        # 凭证更新方式
        try:
            from services import app_config
            v = app_config.get("general", "cred_refresh_mode", None)
            cred_mode = str(v) if v else (os.environ.get("DY_CRED_REFRESH_MODE") or "observe").strip() or "observe"  # noqa: E501
        except Exception:
            cred_mode = (os.environ.get("DY_CRED_REFRESH_MODE") or "observe").strip() or "observe"
        logger.info(
            f"[bcc] 保活心跳启动，间隔 {interval}s，凭证更新方式={cred_mode}"
            f"（{'观测态静默/弹窗激活/两者兼容' if cred_mode=='both' else cred_mode}）")
        scan_fail_count = 0
        SCAN_BREAKER_LIMIT = 2
        SCAN_BACKOFF_SEC = 1800
        breaker_until = 0.0
        last_cookie_sync = 0.0

        def _sync_env(uid_for_log=None) -> bool:
            nonlocal last_cookie_sync
            if cred_mode == "popup":
                logger.debug("[bcc] 凭证更新方式=popup，跳过观测态静默回写"
                             "（等待用户弹窗激活）")
                return False
            try:
                _sync_sec = int(os.environ.get("DY_BCC_COOKIE_SYNC_SEC", "1800"))
            except Exception:
                _sync_sec = 1800
            if _sync_sec <= 0 or (time.time() - last_cookie_sync) < _sync_sec:
                return False
            if not self._loop:
                return False
            tried = False
            succeeded = False
            try:
                r = {}
                for _attempt in range(2):
                    tried = True
                    fut = asyncio.run_coroutine_threadsafe(
                        self.refresh_cookie_to_env(internal=True), self._loop)
                    r = fut.result(timeout=90) or {}
                    if r.get("ok"):
                        logger.info(
                            f"[bcc] 保活回写：已将 profile 新鲜凭证同步至账号 .env"
                            f"（uid={uid_for_log}）"
                            + ("（第 2 次重试成功）" if _attempt else ""))
                        succeeded = True
                        return True
                    if r.get("retry_now"):
                        logger.warning(
                            f"[bcc] 保活回写会话未获承认，立即重试"
                            f"（第 {_attempt + 1} 次）: {r.get('msg')}")
                        continue
                    logger.debug(f"[bcc] 保活回写跳过：{r.get('msg', '无更新')}")
                    succeeded = True
                    return False
                logger.error(
                    "[BCC-072] [bcc] 保活回写连续两次会话未获承认"
                    " —— .env 保留既有凭证；疑似 profile 登录态需人工重新登录"
                    "（不自动重扫，防风控）。")
                succeeded = True
            except Exception as e:
                logger.debug(f"[bcc] 保活回写失败（不影响运行）: {e}")
            finally:
                if tried and (succeeded or _sync_sec <= interval):
                    last_cookie_sync = time.time()
                elif tried:
                    logger.debug("[bcc] 保活回写本轮未定论（疑似租约争用），"
                                 "不推进节流时间戳，下轮继续重试")
            return False

        while not stop_ev.is_set():
            if stop_ev.wait(interval):
                break
            now = time.time()
            in_breaker = now < breaker_until
            if self._switching or now < self._switch_cool_until:
                remain = int(self._switch_cool_until - now)
                phase = "切换中(_launch后台)" if self._switching else "切换冷却期"
                logger.debug(f"[bcc] {phase}({max(remain,0)}s)，跳过探活（防误杀新 context）")
                continue

            # 环境基线比对
            try:
                from services.env_baseline import compare_baseline as _env_cmp
                _ec = _env_cmp(self.account)
                if _ec.get("drift"):
                    logger.error(
                        f"[BCC-063] [bcc] 出口环境漂移！{_ec.get('reason')}。"
                        f"登录环境与运行环境不一致（知识库 9.30 铁律），若出现「安全风险"
                        f"阻止访问」/ 强制下线 / INVALID_REQUEST，先重扫建立新基线，"
                        f"不要先改发送/风控代码。")
                elif _ec.get("checked"):
                    logger.debug(f"[bcc] 环境基线比对一致: ip={_ec.get('current_ip')}")
            except Exception as _e:
                logger.debug(f"[bcc] 环境基线比对异常（不影响保活）: {_e}")

            # 环境泄漏监测
            try:
                _now2 = time.time()
                if _now2 - getattr(self, "_last_env_audit_at", 0) >= 3600:
                    self._last_env_audit_at = _now2
                    if self._loop and not self._loop.is_closed():
                        _fut = asyncio.run_coroutine_threadsafe(
                            self.env_audit_snapshot(internal=True), self._loop)
                        try:
                            _er = _fut.result(timeout=60) or {}
                        except Exception as _ee:
                            _er = {"ok": False, "error": str(_ee), "leaks": []}
                        for _lk in (_er.get("leaks") or []):
                            _sev = _lk.get("severity")
                            if _sev == "fatal":
                                logger.error(
                                    f"[BCC-064] [bcc] 环境泄漏(fatal): "
                                    f"{_lk.get('code')}: {_lk.get('detail')}")
                            elif _sev == "warn":
                                logger.warning(
                                    f"[BCC-064] [bcc] 环境泄漏(warn): "
                                    f"{_lk.get('code')}: {_lk.get('detail')}")
                        if not (_er.get("leaks") or []) and _er.get("ok"):
                            logger.debug("[bcc] 环境泄漏监测通过（本轮无 leaks）")
            except Exception as _e2:
                logger.debug(f"[bcc] 环境泄漏监测异常（不影响保活）: {_e2}")

            try:
                uid = self._load_uid_from_env()
                prev_uid = getattr(self, "_last_uid", None)
                if uid and prev_uid and str(uid) != str(prev_uid):
                    logger.error(
                        f"[BCC-019] [bcc] uid 漂移！old={prev_uid} new={uid}，"
                        f"凭证身份存疑，触发自动刷新…")
                    self._last_uid = uid
                    if self._loop and not in_breaker:
                        fut = asyncio.run_coroutine_threadsafe(
                            self.scan_login(force=False), self._loop)
                        try:
                            _r = fut.result(timeout=120) or {}
                            if _r.get("ok"):
                                scan_fail_count = 0
                            else:
                                scan_fail_count += 1
                                logger.warning(
                                    f"[BCC-020] [bcc] uid 漂移后自动刷新未通过校验"
                                    f"（连续 {scan_fail_count} 次）: {_r.get('uid')}")
                        except Exception as e:
                            logger.warning(
                                f"[BCC-020] [bcc] uid 漂移后自动刷新失败: {e}")
                            scan_fail_count += 1
                        if scan_fail_count >= SCAN_BREAKER_LIMIT:
                            breaker_until = time.time() + SCAN_BACKOFF_SEC
                            logger.error(
                                f"[BCC-024] [bcc] 凭证刷新连续未通过 {scan_fail_count} 次，"
                                f"熔断 {SCAN_BACKOFF_SEC // 60} 分钟（自动救不回，"
                                f"请在指纹浏览器重新扫码）")
                elif uid:
                    _sync_env(uid)
                    page_state = self._page_login_state_sync()
                    if page_state.get("unknown"):
                        logger.debug(
                            "[BCC-073] [bcc] 页面级登录态结论未知（探针无证据）"
                            " —— 按保守处理：不判页面失效、不触发重激活；"
                            "凭证回写已独立执行")
                    elif page_state.get("rel") or not page_state.get("conv"):
                        if in_breaker:
                            remain = int(breaker_until - now)
                            logger.warning(
                                f"[BCC-021] [bcc] 页面仍需重激活（conv={page_state.get('conv')}），"
                                f"scan_login 已熔断（连续失败 {scan_fail_count} 次），"
                                f"{remain // 60} 分钟内不再自动重启浏览器，"
                                f"请在指纹浏览器完成扫码登录")
                            continue
                        logger.warning(
                            f"[BCC-022] [bcc] 页面操作级登录态失效"
                            f"（conv={page_state.get('conv')} "
                            f"rel={page_state.get('rel')}），uid={uid} "
                            f"凭证仍有效（回写已独立执行），"
                            f"受影响的是 WP 发送/昵称抓取等**页面操作**；"
                            f"凭证更新方式={cred_mode}"
                            + ("（observe：不弹窗，等用户自行激活）"
                               if cred_mode == "observe" else "，触发 scan_login…"))
                        if cred_mode == "observe":
                            continue
                        if self._loop:
                            fut = asyncio.run_coroutine_threadsafe(
                                self.scan_login(force=False), self._loop)
                            try:
                                _r = fut.result(timeout=120) or {}
                                if _r.get("ok"):
                                    scan_fail_count = 0
                                else:
                                    scan_fail_count += 1
                                    logger.warning(
                                        f"[BCC-023] [bcc] 页面重激活未通过校验"
                                        f"（连续 {scan_fail_count} 次）: {_r.get('uid')}")
                            except Exception as e:
                                logger.warning(
                                    f"[BCC-023] [bcc] 页面重激活失败: {e}")
                                scan_fail_count += 1
                            if scan_fail_count >= SCAN_BREAKER_LIMIT:
                                breaker_until = time.time() + SCAN_BACKOFF_SEC
                                logger.error(
                                    f"[BCC-024] [bcc] scan_login 连续失败 {scan_fail_count} 次，"
                                    f"熔断 {SCAN_BACKOFF_SEC // 60} 分钟。"
                                    f"session 疑似服务端已失效，自动登录救不回，"
                                    f"请在指纹浏览器重新扫码；期间仅告警不重启浏览器")
                    else:
                        self._last_uid = uid
                        scan_fail_count = 0
                        logger.debug(
                            f"[bcc] 登录态正常(uid={uid}, "
                            f"conv={page_state.get('conv')})")
                else:
                    logger.warning("[BCC-025] [bcc] 登录态失效，自动刷新凭证…")
                    try:
                        if _sync_env(None):
                            scan_fail_count = 0
                            logger.info(
                                "[bcc] 探活异常但 profile 会话可用 —— 已用 profile "
                                "实时态刷新 .env（自锁恢复），本轮不触发重扫。")
                            continue
                        logger.warning(
                            "[bcc] 探活异常且会话未被服务端承认 —— 转入重扫/熔断处置。")
                    except Exception as _e_rec:
                        logger.debug(f"[bcc] 自锁恢复分支异常（继续原路径）: {_e_rec}")
                    if not self._headless:
                        logger.warning(
                            f"[BCC-057] [bcc] {self.account} 处于【有头观测态】，"
                            f"**不触发自动重扫** —— 重扫会重建 context，销毁用户正在"
                            f"操作的窗口并在抖音侧记一次新环境访问（正是扫码/输手机号"
                            f"时被拦的成因）。请在打开的窗口中完成登录。")
                        continue
                    if in_breaker:
                        remain = int(breaker_until - now)
                        logger.warning(
                            f"[BCC-021] [bcc] 探活仍拿不到有效 uid，scan_login 已熔断"
                            f"（连续失败 {scan_fail_count} 次），{remain // 60} 分钟内"
                            f"不再自动重启浏览器，请在指纹浏览器完成扫码登录")
                        continue
                    if self._loop:
                        fut = asyncio.run_coroutine_threadsafe(
                            self.scan_login(force=False), self._loop)
                        try:
                            _r = fut.result(timeout=120) or {}
                            scan_fail_count += 1
                            if not _r.get("ok"):
                                logger.warning(
                                    f"[BCC-026] [bcc] 自动刷新凭证未通过校验"
                                    f"（连续 {scan_fail_count} 次，uid={_r.get('uid')}）")
                        except Exception as e:
                            scan_fail_count += 1
                            logger.warning(
                                f"[BCC-026] [bcc] 自动刷新凭证失败: {e}")
                        if scan_fail_count >= SCAN_BREAKER_LIMIT:
                            breaker_until = time.time() + SCAN_BACKOFF_SEC
                            logger.error(
                                f"[BCC-024] [bcc] 探活/scan_login 连续失败 {scan_fail_count} 次，"
                                f"熔断 {SCAN_BACKOFF_SEC // 60} 分钟（防浏览器频繁重启"
                                f"引发风控）。session 疑似服务端已失效，自动登录救不回，"
                                f"请在指纹浏览器重新扫码；期间仅告警不重启浏览器")
            except Exception as e:
                logger.warning(f"[BCC-027] [bcc] 探活异常: {e}")
        logger.info("[bcc] 保活心跳退出")