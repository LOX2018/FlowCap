# -*- coding: utf-8 -*-
"""ENG-020 守卫测试：凭证回写不得依赖「页面证据」，且页面探针必须三态。

被固化的不变式（回退任一条 → 测试必须变红）：
  I1. `_page_login_state_sync()` 取不到证据时返回 `{"unknown": True}`，
      **不得**返回 `{}`（否则「超时」被当成「页面失效」）。
  I2. run_keepalive 在「uid 有效」分支内**无条件**回写 `.env`：
      即便页面探针报「失效」，`refresh_cookie_to_env` 也必须被调用。
      （ENG-020 根因：回写被错绑到页面探针结果上 ⇒ 探针超时 → 回写停摆 → 坏会话固化）
  I3. 页面探针「明确失效」时仍保留重激活告警（BCC-022），但**不再阻断回写**。
  I4. 观测动作不得改写被观测页面：`exec_js` / `capture_wp_messages`
      源码中不得再出现 `self._page.goto(`（必须走 `_ensure_nav_tab`）。
  I5. `main.py` 的 WP 接收循环覆盖**全部**账号（不再只取 `[0]`）。

跑法：python test_live_cred_writeback_independence.py
"""
import asyncio
import os
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("DY_APP_ROOT", os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")))

_HERE = os.path.dirname(os.path.abspath(__file__))


class TestPageProbeTriState(unittest.TestCase):
    """I1：页面探针三态。"""

    def _mk(self, fut_result=None, exc=None):
        from daemon.browser_daemon import BrowserContainer
        obj = BrowserContainer.__new__(BrowserContainer)
        obj.account = "t"

        async def _exec_js(*a, **k):
            if exc:
                raise exc
            return fut_result

        obj.exec_js = _exec_js
        # 生产环境 self._loop 始终是运行中的主 loop；测试须等价提供，
        # 否则 run_coroutine_threadsafe 分支不可达（会误判为「探针无结论」）。
        loop = asyncio.new_event_loop()
        obj._loop = loop
        t = threading.Thread(target=loop.run_forever, daemon=True)
        t.start()
        self.addCleanup(lambda: (loop.call_soon_threadsafe(loop.stop),
                                 t.join(timeout=3), loop.close()))
        return obj

    def test_probe_no_evidence_returns_unknown_not_empty(self):
        obj = self._mk(fut_result=None)
        st = obj._page_login_state_sync()
        self.assertTrue(st.get("unknown"),
                        "探针无结论必须返回 {'unknown': True}，不得返回 {}（会把超时当页面失效）")

    def test_probe_timeout_returns_unknown(self):
        obj = self._mk(exc=TimeoutError("hung"))
        st = obj._page_login_state_sync()
        self.assertTrue(st.get("unknown"))

    def test_probe_exception_returns_unknown_not_empty(self):
        """覆盖**外层 except 分支**（真实超时/投递失败走这里）。

        必须 mock 掉 run_coroutine_threadsafe 使投递本身抛错 ——
        否则异常会被 `_probe()` 内部吞掉、改走 got 分支，测不到兜底逻辑。
        """
        obj = self._mk(fut_result={"conv": 1, "rel": False})
        with mock.patch("asyncio.run_coroutine_threadsafe",
                        side_effect=RuntimeError("loop down")):
            st = obj._page_login_state_sync()
        self.assertTrue(st.get("unknown"),
                        "探针抛异常必须返回 {'unknown': True}，不得返回 {}（会把异常当页面失效）")

    def test_probe_ok_dict_passthrough(self):
        obj = self._mk(fut_result={"conv": 14, "rel": False, "url": "/chat"})
        st = obj._page_login_state_sync()
        self.assertEqual(st.get("conv"), 14)
        self.assertFalse(st.get("unknown"))


class TestWritebackIndependence(unittest.TestCase):
    """I2 / I3：回写与页面证据解耦。"""

    def _drive_one_round(self, page_state):
        """驱动 run_keepalive 一轮：uid 有效 + 指定页面探针结论。"""
        from daemon.browser_daemon import BrowserContainer
        obj = BrowserContainer.__new__(BrowserContainer)
        obj.account = "自锁测试账号"
        obj._switching = False
        obj._switch_cool_until = 0.0
        obj._last_env_audit_at = 9e18
        obj._headless = True
        obj._last_uid = None
        obj._headless = True

        calls = {"sync": 0}

        async def _fake_refresh(lease_id="", internal=False):
            calls["sync"] += 1
            return {"ok": True, "uid": "x"}

        obj.refresh_cookie_to_env = _fake_refresh
        obj._load_uid_from_env = lambda: 3887506227210423    # uid 有效
        obj._page_login_state_sync = lambda: page_state

        loop = asyncio.new_event_loop()
        obj._loop = loop
        t = threading.Thread(target=loop.run_forever, daemon=True)
        t.start()
        stop_ev = threading.Event()

        def _stop_soon():
            import time as _t
            _t.sleep(0.5)
            stop_ev.set()

        threading.Thread(target=_stop_soon, daemon=True).start()
        obj.run_keepalive(stop_ev, interval=0.05)

        loop.call_soon_threadsafe(loop.stop)
        t.join(timeout=3)
        loop.close()
        return calls

    def test_writeback_runs_even_when_page_probe_says_failed(self):
        """★ 核心不变式：页面探针报失效，回写仍必须发生。"""
        calls = self._drive_one_round({"conv": None, "rel": None})
        self.assertGreaterEqual(
            calls["sync"], 1,
            "页面级探针失效不得阻断凭证回写（ENG-020 根因：回写被错绑到页面证据）")

    def test_writeback_runs_when_page_probe_unknown(self):
        calls = self._drive_one_round({"unknown": True})
        self.assertGreaterEqual(calls["sync"], 1, "探针未知态下回写同样必须发生")

    def test_writeback_runs_when_page_ok(self):
        calls = self._drive_one_round({"conv": 21, "rel": False})
        self.assertGreaterEqual(calls["sync"], 1)


class TestNoSideEffectsOnObserve(unittest.TestCase):
    """I4：观测动作不得把主 page 导航走。"""

    def _src(self, rel):
        return open(os.path.join(_HERE, rel), encoding="utf-8").read()

    def test_exec_js_does_not_goto_main_page(self):
        src = self._src("daemon/browser_daemon.py")
        i = src.find("async def exec_js(")
        j = src.find("async def wp_send_text(")
        seg = src[i:j]
        self.assertNotIn(
            "self._page.goto(", seg,
            "exec_js 不得再用主 page goto（会把页面导航走并挂满 25s 超时，ENG-020）")
        self.assertIn("_ensure_nav_tab", seg, "exec_js 必须改用独立导航 tab")

    def test_capture_wp_messages_does_not_goto_main_page(self):
        src = self._src("daemon/browser_daemon.py")
        i = src.find("async def capture_wp_messages(")
        j = src.find("async def resolve_url(")
        seg = src[i:j]
        self.assertNotIn(
            "self._page.goto(", seg,
            "capture_wp_messages 不得再用主 page goto（会与页面级探针互相踩）")
        self.assertIn("_ensure_nav_tab", seg)


class TestWpRecvCoversAllAccounts(unittest.TestCase):
    """I5：WP 接收循环覆盖全部账号（原实现只驱动 list_accounts()[0]）。"""

    def test_main_loops_over_all_accounts(self):
        src = open(os.path.join(_HERE, "main.py"), encoding="utf-8").read()
        i = src.find("WP 通道私信接收循环")
        j = src.find("AI 获客自动回复", i)
        seg = src[i:j]
        self.assertNotIn(
            "run_wp_recv_loop(_wp_names[0])", seg,
            "wp_recv 不得只驱动第一个账号（Camoufox 已是每账号独立容器，ENG-020）")
        self.assertIn("for _nm in _wp_names", seg,
                      "wp_recv 必须遍历全部账号各起一个循环")


class TestSyncThrottleNotAdvancedOnTransientFailure(unittest.TestCase):
    """I6：回写节流只在「已定论」后推进 —— 租约争用不得让自愈静默等满 30 分钟。"""

    def _src(self):
        return open(os.path.join(_HERE, "daemon/browser_daemon.py"),
                    encoding="utf-8").read()

    def test_throttle_stamp_not_set_before_attempt(self):
        src = self._src()
        i = src.find("def _sync_env(")
        j = src.find("while not stop_ev.is_set():", i)
        seg = src[i:j]
        self.assertNotIn(
            "last_cookie_sync = time.time()\r\n            if not self._loop:",
            seg,
            "节流时间戳不得在尝试前推进（撞锁会白白多等一个周期，ENG-020）")
        self.assertIn("finally:", seg, "必须有 finally 收口节流推进逻辑")
        self.assertIn("if tried and (succeeded or _sync_sec <= interval):", seg,
                      "节流只应在「真正尝试过且已定论」时推进")


class TestErrcodeContract(unittest.TestCase):
    def test_bcc_073_registered(self):
        import errcode_data as E
        self.assertIn("BCC-073", E.ERRCODES)


if __name__ == "__main__":
    unittest.main(verbosity=2)
