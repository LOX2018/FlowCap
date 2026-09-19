# -*- coding: utf-8 -*-
"""v0.43.98 浏览器可用性与双实例根治 —— 守卫测试（静态断言，绝不启第二个 BCC）。

覆盖：
  A. set_visible 在纯无头下必须走真实重建（不再被 window 快路径截断）
  B. 切换完成后以 OS 层可见窗口为判据（诚实降级，不谎报）
  C. profile 所有权互斥：按账号维度、可重入、并发串行
  D. 重捕/扫码整段被所有权锁包裹（含风控态不回拉 BCC 的例外）
  E. open-browser 边界契约：凭证有效+任务在跑 → 拒绝；失效 → 暂停并放行
  F. 注入脚本保留被包装函数的可观测身份（fetch.name / WS 静态常量）
  G. 新增错误码均有 design 契约
"""
import ast
import os
import re
import sys
import threading
import time
import unittest

BE = r"C:\Users\LOX\Desktop\DYchajian\DYAutoDM_v2\backend"
sys.path.insert(0, BE)

BROWSER_DAEMON = os.path.join(BE, "daemon", "browser_daemon.py")
JS = os.path.join(BE, "daemon", "browser_daemon_js.py")
GATE = os.path.join(BE, "services", "browser_gate.py")
API_ACC = os.path.join(BE, "api", "accounts.py")
AUTO_ACC = os.path.join(BE, "auto_dm", "accounts.py")


def _read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


class TestA_VisibilityRebuild(unittest.TestCase):
    """A：纯无头下不得走「只改窗口状态」的假成功路径。"""

    @classmethod
    def setUpClass(cls):
        cls.src = _read(BROWSER_DAEMON)

    def test_01_window_mode_requires_headed(self):
        """window 快路径的前提必须包含「当前非无头」。"""
        m = re.search(r"_can_window_mode\s*=\s*\((.*?)\)\s*\n", self.src, re.S)
        self.assertIsNotNone(m, "未找到 _can_window_mode 定义")
        expr = m.group(1)
        self.assertIn("not self._headless", expr,
                      "window 模式必须以「当前有头」为前提（纯无头下 setWindowBounds 无效）")

    def test_02_switch_passes_target_headless(self):
        """切可见时后台重建必须显式传目标可见性。"""
        self.assertIn("_do_switch_background(target, url, headless=target)", self.src,
                      "重建必须传入目标可见性，否则一律按无头重建 → 永远出不了窗口")

    def test_03_launch_accepts_headless(self):
        self.assertIn("async def _launch(self, headless: bool | None = None)", self.src)


class TestB_HonestDegradation(unittest.TestCase):
    """B：以 OS 层真实可见窗口为完成判据。"""

    @classmethod
    def setUpClass(cls):
        cls.src = _read(BROWSER_DAEMON)

    def test_01_has_os_window_check(self):
        self.assertIn("async def _window_really_visible(self)", self.src)

    def test_02_uses_enumwindows(self):
        self.assertIn("EnumWindows", self.src)
        self.assertIn("IsWindowVisible", self.src)

    def test_03_reports_failure_not_silent(self):
        """未见可见窗口必须报错码并回退状态，而不是静默成功。"""
        self.assertIn("BCC-053", self.src)
        # 回退为无头
        self.assertIn("self._headless = True", self.src)


class TestC_ProfileOwnership(unittest.TestCase):
    """C：所有权锁按账号维度、可重入、并发串行。"""

    def test_01_lock_per_account(self):
        from services.browser_gate import profile_owner_lock
        a, b = profile_owner_lock("acct-A"), profile_owner_lock("acct-B")
        self.assertIsNot(a, b, "不同账号必须互不阻塞（不牺牲并发）")
        self.assertIs(a, profile_owner_lock("acct-A"))

    def test_02_reentrant(self):
        from services.browser_gate import profile_owner_lock
        lk = profile_owner_lock("acct-reentrant")
        with lk:
            self.assertTrue(lk.acquire(timeout=2))
            lk.release()

    def test_03_serializes(self):
        """同一账号的两个操作必须串行（重叠量必须为 0）。"""
        from services.browser_gate import ProfileOwnership
        overlap = {"n": 0}
        inside = {"n": 0}
        errs = []

        def worker(name):
            try:
                with ProfileOwnership("acct-serial", name, timeout=10):
                    inside["n"] += 1
                    time.sleep(0.4)
                    if inside["n"] > 1:
                        overlap["n"] += 1
                    inside["n"] -= 1
            except Exception as e:  # noqa: BLE001
                errs.append(e)

        t1 = threading.Thread(target=worker, args=("op1",))
        t2 = threading.Thread(target=worker, args=("op2",))
        t1.start(); t2.start(); t1.join(); t2.join()
        self.assertEqual(errs, [], f"锁使用异常: {errs}")
        self.assertEqual(overlap["n"], 0, "同一账号的独占操作发生了重叠 → 双实例风险未根治")

    def test_04_owner_tracked(self):
        from services.browser_gate import ProfileOwnership, profile_owner_of
        with ProfileOwnership("acct-owner", "unit-test", timeout=5):
            self.assertEqual(profile_owner_of("acct-owner"), "unit-test")
        self.assertEqual(profile_owner_of("acct-owner"), "")


class TestD_RecapScanWrapped(unittest.TestCase):
    """D：重捕/扫码整段必须被所有权锁包裹。"""

    @classmethod
    def setUpClass(cls):
        cls.api = _read(API_ACC)
        cls.auto = _read(AUTO_ACC)

    def test_01_scan_wrapped(self):
        seg = self.api[self.api.find("def _do_scan("):]
        seg = seg[:seg.find("def _do_open_browser(")]
        self.assertIn("ProfileOwnership", seg)
        self.assertIn('"scan_login"', seg)

    def test_02_auto_recapture_wrapped(self):
        seg = self.auto[self.auto.find("def _do_auto_recapture("):]
        seg = seg[:seg.find("def recapture_from_profile(")]
        self.assertIn("ProfileOwnership", seg)

    def test_03_recapture_from_profile_wrapped(self):
        seg = self.auto[self.auto.find("def _do_recapture_from_profile("):]
        self.assertIn("ProfileOwnership", seg)

    def test_04_risk_control_skips_bcc_pullback(self):
        """风控污染时浏览器保持打开 → 必须跳过拉回 BCC（否则立刻双实例）。"""
        seg = self.auto[self.auto.find("def _do_recapture_from_profile("):]
        self.assertIn("_keep_browser_open", seg)
        self.assertIn("if not st.get(\"_keep_browser_open\")", seg)

    def test_05_lock_released_in_finally(self):
        """锁必须在 finally 释放，異常時不得泄漏。"""
        for src, name in ((self.api, "api/accounts.py"), (self.auto, "auto_dm/accounts.py")):
            self.assertIn("_own.__exit__(None, None, None)", src, f"{name} 未在 finally 释放所有权锁")


class TestE_OpenBrowserGate(unittest.TestCase):
    """E：open-browser 的边界契约（凭证有效禁有头 / 失效暂停并引导）。"""

    @classmethod
    def setUpClass(cls):
        cls.src = _read(API_ACC)

    def test_01_gate_present(self):
        seg = self.src[self.src.find("async def open_fingerprint_browser("):]
        seg = seg[:seg.find("bport = acct_core.browser_daemon_port(name)")]
        self.assertIn("verify_account", seg, "必须用真实校验判凭证，不能用端口推断")
        self.assertIn("_wp_level == \"ok\"", seg)

    def test_02_blocks_when_valid_and_running(self):
        seg = self.src[self.src.find("async def open_fingerprint_browser("):]
        self.assertIn("已阻止打开有头浏览器", seg)

    def test_03_pauses_when_invalid(self):
        seg = self.src[self.src.find("async def open_fingerprint_browser("):]
        self.assertIn("ACC-025", seg)
        self.assertIn("adm.pause()", seg)

    def test_04_uses_real_engine_api(self):
        """不得引用不存在的 API（如 stop_engine）。

        注：只检查本次改动的 open-browser 段 —— 全文件 grep 会被其它无关
        代码/注释误伤（探针自身的缺陷会伪造出「后端 bug」）。
        """
        seg = self.src[self.src.find("async def open_fingerprint_browser("):]
        seg = seg[:seg.find("bport = acct_core.browser_daemon_port(name)")]
        self.assertNotIn("stop_engine", seg,
                         "AutoDM 没有 stop_engine；真实 API 是 pause()/stop()（async）")
        # 判据：必须引用 app.state 上的 AutoDM 单例（而不是凭空造一个 adm）
        self.assertIn("req.app.state", seg)
        self.assertIn('"adm"', seg)

    def test_05_takes_request(self):
        self.assertIn("async def open_fingerprint_browser(name: str, req: Request)",
                      self.src)


class TestF_InjectionHygiene(unittest.TestCase):
    """F：注入脚本保留被包装函数的可观测身份。"""

    @classmethod
    def setUpClass(cls):
        cls.js = _read(JS)

    def test_01_fetch_name_preserved(self):
        self.assertIn("'name'", self.js)
        self.assertIn("'fetch'", self.js)

    def test_02_ws_statics_preserved(self):
        for k in ("CONNECTING", "OPEN", "CLOSING", "CLOSED"):
            self.assertIn(k, self.js, f"WebSocket 静态常量 {k} 未保留")

    def test_03_ws_name_preserved(self):
        self.assertIn("_hide(WrappedWS, 'WebSocket', 1)", self.js)


class TestG_ErrorCodes(unittest.TestCase):
    """G：新增错误码必须有 design 契约且编号不重。"""

    def test_01_new_codes_have_design(self):
        from errcode_data import CODE_DESIGN, ERRCODES
        for code in ("BCC-053", "BCC-054", "ACC-022", "ACC-023",
                     "ACC-024", "ACC-025", "ACC-026"):
            self.assertIn(code, ERRCODES, f"{code} 未登记 meaning")
            d = CODE_DESIGN.get(code) or {}
            self.assertTrue(d.get("design"), f"{code} 缺 design 契约")
            for f in ("contract", "deviation", "chain", "root", "verify"):
                self.assertTrue(d.get(f), f"{code} 缺 {f}")

    def test_02_no_gaps_overall(self):
        from errcode_data import CODE_DESIGN
        missing = [k for k, v in CODE_DESIGN.items() if not (v or {}).get("design")]
        self.assertEqual(missing, [], f"存在无 design 的错误码: {missing}")


class TestZ_Syntax(unittest.TestCase):
    def test_all_files_parse(self):
        for f in (BROWSER_DAEMON, JS, GATE, API_ACC, AUTO_ACC):
            with open(f, encoding="utf-8") as fh:
                try:
                    ast.parse(fh.read())
                except SyntaxError as e:
                    self.fail(f"{f} 语法错误: {e}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
