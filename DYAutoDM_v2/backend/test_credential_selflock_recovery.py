# -*- coding: utf-8 -*-
"""凭证自锁恢复的守卫测试（2026-09-21）。

覆盖的机制（不是字面量）：
  1. `live_session_state` 三态：True / False / None（探测失败不得折成 False）
  2. `probe_live_identity` 兼容二元契约（None 折成 False，行为与改造前一致）
  3. `refresh_cookie_to_env` 写盘门禁：
       - 会话明确被拒（False）→ **不写** .env，返回 retry_now=True
       - 取不到证据（None）→ **保守放行**（写），不制造死锁
       - 被承认（True）→ 正常写
  4. 现有门禁 1.5（身份一致性）仍生效

跑法：python test_credential_selflock_recovery.py
（也可 python -m unittest test_credential_selflock_recovery）
"""
import asyncio
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

# A-8 / M-17 隔离根单一化：模块级 DY_APP_ROOT 必须是**一次性临时目录**，禁止回落
# 源码树/仓库父目录（旧值 = backend/../..）。先 mkdir 再赋值（vbrowser.app_root()
# 忽略不存在的根 → 回落仓库 data/）。范式见 test_uid_sink_ext.py:17-39。
_ROOT = tempfile.mkdtemp(prefix="cred_selflock_")
os.makedirs(_ROOT, exist_ok=True)
os.environ["DY_APP_ROOT"] = _ROOT
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


# --------------------------------------------------------------------------
# 1) 三态探针
# --------------------------------------------------------------------------
class TestLiveSessionStateTriState(unittest.TestCase):
    def setUp(self):
        from auto_dm import accounts as A
        self.A = A

    class _Auth:
        cookie = {"sessionid": "sess-X"}

    def _with_raw(self, state, detail="d"):
        return mock.patch.object(self.A, "_live_session_probe_raw",
                                 return_value=(state, detail))

    def test_true(self):
        self.A.invalidate_live_identity_cache("t1")
        with self._with_raw(True):
            st, _ = self.A.live_session_state("t1", auth=self._Auth())
        self.assertIs(st, True)
        with self._with_raw(True):
            ok, _ = self.A.probe_live_identity("t1", auth=self._Auth())
        self.assertTrue(ok)

    def test_false_is_reported_not_collapsed(self):
        self.A.invalidate_live_identity_cache("t2")
        with self._with_raw(False):
            st, _ = self.A.live_session_state("t2", auth=self._Auth())
        self.assertIs(st, False)
        with self._with_raw(False):
            ok, _ = self.A.probe_live_identity("t2", auth=self._Auth())
        self.assertFalse(ok)

    def test_probe_failure_is_none_not_false(self):
        """核心不变式：「探测不到」≠「不存在」。"""
        self.A.invalidate_live_identity_cache("t3")
        with mock.patch.object(self.A, "_live_session_probe_raw",
                               side_effect=RuntimeError("net down")):
            st, detail = self.A.live_session_state("t3", auth=self._Auth())
        self.assertIsNone(st, "探测失败必须是 None（未知），不能折成 False")
        self.assertIn("未知", detail)
        # 兼容二元入口仍把 None 折成 False（不由它区分）
        with mock.patch.object(self.A, "_live_session_probe_raw",
                               side_effect=RuntimeError("net down")):
            ok, _ = self.A.probe_live_identity("t3", auth=self._Auth())
        self.assertFalse(ok)

    def test_no_cookie_is_false(self):
        class _Auth:
            cookie = None
        self.A.invalidate_live_identity_cache("t4")
        st, _ = self.A.live_session_state("t4", auth=_Auth())
        self.assertIs(st, False)


# --------------------------------------------------------------------------
# 2) 写盘门禁
# --------------------------------------------------------------------------
def _mk_container(sess_state, uid_consistent=True):
    """构造一个不启浏览器的 BrowserContainer 替身，绑定真实 refresh_cookie_to_env。"""
    from daemon.browser_daemon import BrowserContainer

    obj = BrowserContainer.__new__(BrowserContainer)
    obj.account = "自锁测试账号"
    obj._last_refresh = 0.0
    obj._last_uid = None
    obj._uid_at_last_env_write = None

    async def _get_cookies(lease_id="", internal=False):
        return {"sessionid": "sess-A", "sid_tt": "sess-A", "uid_tt": "u"}

    async def _read_page_sign():
        return {}

    async def _refresh_page_for_session():
        return False       # 替身不刷新页面 → 复验仍失败

    obj.get_cookies = _get_cookies
    obj._read_page_sign = _read_page_sign
    obj._refresh_page_for_session = _refresh_page_for_session
    return obj


class TestRefreshCookieGate(unittest.TestCase):
    def setUp(self):
        from dy_apis import login_api as L
        self.L = L
        self.writes = []

        class _Auth:
            cookie = {"sessionid": "sess-A"}
            cookie_str = "sessionid=sess-A"
            uid = 111
            ticket = "t"
            ts_sign = "s"
            client_cert = "c"
            private_key = "k"
            web_protect_str = "w"
            keys_str = "k"
            _uid_cached_at = None
            uid_tt = "u"

        self._auth = _Auth()

    def _patch_common(self, sess_state, uid_consistent=True):
        patches = [
            mock.patch.object(self.L.DYLoginApi, "_load_auth_from_env",
                              staticmethod(lambda p: self._auth)),
            mock.patch.object(self.L.DYLoginApi, "save_credential",
                              side_effect=lambda a, env_path=None: self.writes.append(env_path)),
            mock.patch("auto_dm.accounts.env_path_of",
                       lambda name: r"C:\tmp\x\.env"),
            mock.patch("auto_dm.accounts.live_session_state",
                       return_value=(sess_state, "detail")),
            mock.patch("services.uid_probe._uid_consistent_with_history",
                       return_value=uid_consistent),
        ]
        return patches

    def _run(self, obj):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(obj.refresh_cookie_to_env(internal=True))
        finally:
            loop.close()

    def test_session_rejected_blocks_write_and_asks_retry(self):
        """会话明确被拒 → 不写 .env，且要求立即重试（自锁修复的核心）。"""
        obj = _mk_container(False)
        for p in self._patch_common(False):
            p.start(); self.addCleanup(p.stop)
        with mock.patch("dy_apis.douyin_api.DouyinAPI.get_my_uid",
                        staticmethod(lambda a, **k: 3887506227210423)):
            r = self._run(obj)
        self.assertFalse(r.get("ok"))
        self.assertEqual(self.writes, [], "会话未被承认时绝不允许写 .env")
        self.assertTrue(r.get("retry_now"), "必须要求立即重试，否则自锁永久化")

    def test_unknown_session_state_passes_conservatively(self):
        """取不到证据（None）→ 保守放行，不得制造与门禁 1.5 同型的死锁。"""
        obj = _mk_container(None)
        for p in self._patch_common(None):
            p.start(); self.addCleanup(p.stop)
        with mock.patch("dy_apis.douyin_api.DouyinAPI.get_my_uid",
                        staticmethod(lambda a, **k: 3887506227210423)):
            r = self._run(obj)
        self.assertTrue(r.get("ok"), "未知态应放行（诚实降级），否则网络抖动会误拦")
        self.assertEqual(len(self.writes), 1)

    def test_acknowledged_session_writes(self):
        obj = _mk_container(True)
        for p in self._patch_common(True):
            p.start(); self.addCleanup(p.stop)
        with mock.patch("dy_apis.douyin_api.DouyinAPI.get_my_uid",
                        staticmethod(lambda a, **k: 3887506227210423)):
            r = self._run(obj)
        self.assertTrue(r.get("ok"))
        self.assertEqual(len(self.writes), 1)

    def test_gate_15_still_blocks_ghost_uid(self):
        """既有门禁 1.5（幽灵 uid）不得被本次改动绕过。"""
        obj = _mk_container(True)
        for p in self._patch_common(True, uid_consistent=False):
            p.start(); self.addCleanup(p.stop)
        with mock.patch("dy_apis.douyin_api.DouyinAPI.get_my_uid",
                        staticmethod(lambda a, **k: 9999999999999)):
            r = self._run(obj)
        self.assertFalse(r.get("ok"))
        self.assertEqual(self.writes, [], "幽灵 uid 必须仍被拦下")


# --------------------------------------------------------------------------
# 3) 固化的契约（防后人撤回判据边界）
# --------------------------------------------------------------------------
class TestDesignInvariants(unittest.TestCase):
    def test_probe_is_distinct_from_identity_check(self):
        """会话活性(profile/self) 与 身份一致性(query/user) 必须是两个独立判据。"""
        from auto_dm import accounts as A
        self.assertTrue(hasattr(A, "live_session_state"))
        self.assertTrue(hasattr(A, "_live_session_probe_raw"))
        self.assertTrue(hasattr(A, "probe_live_identity"))
        # 会话活性不得复用身份一致性实现
        self.assertIsNot(getattr(A, "live_session_state", None),
                         getattr(A, "_uid_consistent_with_history", None))

    def test_new_errcodes_have_design_contract(self):
        import errcode_data as E
        for code in ("AUTH-053", "BCC-071", "BCC-072"):
            self.assertIn(code, E.ERRCODES, f"{code} 未登记 ERRCODES")
            self.assertIn(code, E.CODE_DESIGN, f"{code} 缺六段设计契约")
            self.assertTrue(E.CODE_DESIGN[code].get("design"))
            self.assertTrue(E.CODE_DESIGN[code].get("verify"))


# --------------------------------------------------------------------------
# 3) 保活「自锁恢复」另一半：探活异常但页面登录态正常 → 必须回写 .env
# --------------------------------------------------------------------------
class TestKeepaliveSelflockRecovery(unittest.TestCase):
    """行为级：驱动 run_keepalive 一轮，验证坏 .env（uid 为空）时仍会回写。"""

    def test_probe_fail_but_page_logged_in_triggers_writeback(self):
        import threading
        from daemon.browser_daemon import BrowserContainer

        obj = BrowserContainer.__new__(BrowserContainer)
        obj.account = "自锁测试账号"
        obj._switching = False
        obj._switch_cool_until = 0.0
        obj._loop = None                  # 让 _sync_env 走「无 loop」前先被替换
        obj._last_env_audit_at = 9e18     # 跳过环境审计
        obj._headless = True
        obj._last_uid = None

        called = {"sync": 0}

        # 用真实 _sync_env 闭包不可直接调用 ⇒ 改为驱动 run_keepalive，
        # 但把 refresh_cookie_to_env 打桩统计调用次数。
        async def _fake_refresh(lease_id="", internal=False):
            called["sync"] += 1
            return {"ok": True, "uid": "x"}

        obj.refresh_cookie_to_env = _fake_refresh
        obj._load_uid_from_env = lambda: None        # ★ 模拟坏 .env（探活拿不到 uid）
        obj._page_login_state_sync = lambda: {"conv": 14, "rel": False}  # ★ 页面登录态正常

        # refresh_cookie_to_env 需要 loop：给一个真实 loop 并常驻
        loop = asyncio.new_event_loop()
        obj._loop = loop
        t = threading.Thread(target=loop.run_forever, daemon=True)
        t.start()

        stop_ev = threading.Event()

        def _stop_soon():
            import time as _t
            _t.sleep(0.4)
            stop_ev.set()

        threading.Thread(target=_stop_soon, daemon=True).start()
        # interval=0.05 → 第一轮等待后进入循环体
        obj.run_keepalive(stop_ev, interval=0.05)

        loop.call_soon_threadsafe(loop.stop)
        t.join(timeout=3)
        loop.close()

        self.assertGreaterEqual(called["sync"], 1,
            "探活异常但页面登录态正常时，必须回写 .env（自锁恢复）；"
            "否则坏快照被永久固化")


if __name__ == "__main__":
    unittest.main(verbosity=2)
