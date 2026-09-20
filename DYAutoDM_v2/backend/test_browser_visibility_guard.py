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


class TestH_NoEnvironmentFlapping(unittest.TestCase):
    """H：禁止无头↔有头横跳与有头期的重建/重扫（v0.43.99 环境跳变风暴根治）。

    背景：v0.43.98 让「切可见」= 真正重建为有头，但自愈重建仍恒按无头，
    二者叠加造成无头↔有头横跳。每次 context 重建在抖音侧都是一次「全新
    环境」访问，短间隔横跳 = 风控判环境异常的教科书特征，表现为**扫码/
    输手机号时**弹「安全风险…已阻止此次访问」（首屏渲染看不出来）。
    """

    @classmethod
    def setUpClass(cls):
        cls.src = _read(BROWSER_DAEMON)

    def test_01_selfheal_inherits_visibility(self):
        """自愈重建必须继承当前可见性意图（不再一律无头）。"""
        seg = self.src[self.src.find("context 失活自愈"):]
        seg = seg[:seg.find("async def set_visible")]
        self.assertIn("await self._launch(headless=self._headless)", seg,
                      "自愈重建必须传 headless=self._headless，否则把有头冲回无头 → 横跳")

    def test_02_headed_no_silent_rebuild(self):
        """有头观测态下探活失败不得自动重建。"""
        self.assertIn("BCC-055", self.src)
        seg = self.src[self.src.find("async def _ensure_alive("):]
        seg = seg[:seg.find("async def set_visible(")]
        self.assertIn("if not self._headless:", seg)
        self.assertIn("BCC-055", seg, "有头态必须有『不自动重建』的显式分支")
        # 该分支必须在真正重建之前 return
        i_gate = seg.find("BCC-055")
        i_rebuild = seg.find("await self._launch(headless=self._headless)")
        self.assertLess(i_gate, i_rebuild, "有头闸必须在重建之前拦截")

    def test_03_headed_no_auto_rescan(self):
        """有头观测态下不得触发自动重扫（scan_login 会重建 context）。

        判据：定位到「不触发自动重扫」这条告警，其后 6 行内必须有 continue。
        注：窗口取得太窄会漏判（探针自身的缺陷会伪造出「后端 bug」）。
        """
        i = self.src.find("**不触发自动重扫**")
        self.assertNotEqual(i, -1, "未找到有头态禁止重扫的告警文案")
        # 错误码在该文案【之前】的一行内（loguru 拼接写法），故向前取窗口
        seg = self.src[max(0, i - 300):i + 400]
        self.assertIn("BCC-057", seg)
        j = self.src.find("continue", i)
        self.assertNotEqual(j, -1, "有头态应 continue 跳过重扫")
        self.assertLess(j - i, 400, "continue 必须紧随该告警（而非别处的 continue）")

    def test_04_old_force_headless_removed(self):
        """旧的『重建一律把 _headless 置 True』必须已移除（那是横跳根源）。"""
        seg = self.src[self.src.find("async def _ensure_alive("):]
        seg = seg[:seg.find("async def set_visible(")]
        self.assertNotIn(
            'if str(os.environ.get(\n                    "DY_BCC_RESTORE_VISIBLE_ON_RELAUNCH", "")).strip() != "1":\n                self._headless = True',
            seg, "旧逻辑『重建即强制无头』仍在 → 必然造成可见性横跳")


class TestI_ProxyPathResolution(unittest.TestCase):
    """I：账号 .env 路径解析必须走唯一真源（会员空间）—— 出口 IP 分叉根因。

    背景：_env_path_of_account 曾硬拼 <root>/auto_dm/accounts/<name>/.env，
    而会员体系真实路径是 <root>/members/<id>/auto_dm/accounts/<name>/.env，
    故恒返回 None → 代理模式读不到 → 落 direct 强制直连（国内 IP），
    与用户用 Edge（跟系统代理，境外）形成出口 IP 分叉 → 抖音判异地登录
    → 弹「安全风险…已阻止此次访问」。
    """

    def test_01_delegates_to_accounts(self):
        """必须优先委托 accounts.env_path_of（唯一真源）。"""
        src = _read(os.path.join(BE, "vbrowser.py"))
        seg = src[src.find("def _env_path_of_account"):]
        seg = seg[:seg.find("def fingerprint_seed_of")]
        self.assertIn("_acc.env_path_of(account)", seg,
                      "必须委托 accounts.env_path_of，否则会员空间恒解析失败")

    def test_02_no_circular_import_break(self):
        """延后导入，避免模块级循环导入。"""
        import importlib
        try:
            m = importlib.import_module("vbrowser")
            self.assertTrue(callable(getattr(m, "_env_path_of_account", None)))
        except Exception as e:  # noqa: BLE001
            self.fail(f"vbrowser 导入失败（可能引入循环导入）: {e}")


class TestJ_CamoufoxSwitch(unittest.TestCase):
    """J：内核可切换（Chromium / Camoufox）—— 换指纹浏览器的接入契约。

    背景：抖音在 Chromium + JS 注入方案下于**交互时刻**（扫码/输手机号）
    弹「安全风险…已阻止此次访问」。Camoufox 实测同出口、同交互路径连续两轮
    未弹窗（其指纹注入在 C++ 层，无 JS 注入痕迹）。故接入 Camoufox，
    但必须**可配置回退**，避免把路走死。
    """

    def test_01_dispatch_in_launch_async(self):
        src = _read(os.path.join(BE, "vbrowser.py"))
        seg = src[src.find("async def launch_async"):]
        seg = seg[:seg.find("def launch_sync")]
        self.assertIn("camoufox_enabled(cfg)", seg)
        self.assertIn("launch_camoufox_async", seg)

    def test_02_dispatch_in_launch_sync(self):
        src = _read(os.path.join(BE, "vbrowser.py"))
        seg = src[src.find("def launch_sync"):]
        self.assertIn("camoufox_enabled(cfg)", seg)
        self.assertIn("launch_camoufox_sync", seg)

    def test_03_opt_in_by_config_only(self):
        """必须显式配置才启用（默认 Chromium），绝不自动探测。"""
        try:
            from vbrowser_camoufox import camoufox_enabled
        except Exception as e:  # noqa: BLE001
            self.skipTest(f"camoufox 未安装: {e}")

        class _C:
            DY_BROWSER_KERNEL = ""

        self.assertFalse(camoufox_enabled(_C()), "缺省必须走 Chromium（保留回退）")
        _C.DY_BROWSER_KERNEL = "camoufox"
        self.assertTrue(camoufox_enabled(_C()))
        _C.DY_BROWSER_KERNEL = "chromium"
        self.assertFalse(camoufox_enabled(_C()))

    def test_04_no_js_injection_under_camoufox(self):
        """Camoufox 模式下禁止 JS 注入（注入会抵消其反检测优势）。"""
        src = _read(BROWSER_DAEMON)
        seg = src[src.find("Camoufox 模式禁止 JS 注入"):]
        seg = seg[:seg.find("直接打开 chat 页")]
        self.assertIn('self._backend == "camoufox"', seg)
        self.assertIn("跳过 JS 注入", seg)
        # 注入必须被条件包住，不能在条件外无条件执行
        self.assertLess(seg.find("add_init_script"), seg.find("else:") + 400)

    def test_05_failure_falls_back(self):
        """Camoufox 启动失败必须回退 Chromium 并记 BCC-058。"""
        src = _read(os.path.join(BE, "vbrowser.py"))
        self.assertIn("BCC-058", src)


class TestK_CamoufoxCaptureContract(unittest.TestCase):
    """K：Camoufox 凭证捕获的路径契约与判据一致性（防两处 profile 分叉）。

    实测踩坑：`open_camoufox_window` 传 `<账号>/profile`，而 `capture_from_camoufox`
    若传 `<账号>`（env 的 dirname），Camoufox 各自再拼 `_camoufox`，会生成
    **两个 profile** —— 用户在 A 授权，脚本从 B 读 → 误报"未检测到登录态"。
    """

    def setUp(self):
        import camoufox_capture as cc
        self.cc = cc

    def test_both_paths_use_same_profile_dir(self):
        """两个入口的 profile 目录必须完全同源（本测试是防分叉的机械门禁）。"""
        import inspect
        src_open = inspect.getsource(self.cc.open_camoufox_window)
        src_cap = inspect.getsource(self.cc.capture_from_camoufox)
        for src, name in ((src_open, "open"), (src_cap, "capture")):
            self.assertIn(
                'os.path.join(os.path.dirname(env_path), "profile")', src,
                f"{name} 未使用 <账号>/profile 路径契约 → 会与另一入口分叉")

    def test_web_protect_valid_rejects_shell(self):
        """空壳/占位 web_protect 必须拒绝（否则写回残缺凭证 → 私信 KICK）。"""
        v = self.cc._web_protect_valid
        for bad in (None, "", "   ", "not-json", '{"a":1}', "[]", '{"x":"y"}'):
            self.assertFalse(v(bad), f"空壳未被拒绝: {bad!r}")
        for good in ('{"ticket":"abc"}', '{"data":{"key":"k"}}',
                     '{"app_id":"1","salt":"s"}'):
            self.assertTrue(v(good), f"有效凭证被误拒: {good!r}")

    def test_web_protect_judgement_matches_upstream(self):
        """本地复刻判据必须与 login_api 嵌套实现行为一致（防上游漂移）。"""
        import re
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "dy_apis", "login_api.py")
        src = open(p, encoding="utf-8").read()
        ns = {}
        exec(re.search(r"def _web_protect_valid\(s\):.*?\n            return False\n",
                       src, re.S).group(0), ns)
        upstream = ns["_web_protect_valid"]
        mine = self.cc._web_protect_valid
        cases = [None, "", "  ", "junk", "{}", '{"a":1}', '{"ticket":"t"}',
                 '{"sign":"s"}', '{"data":{"salt":"x"}}', '[]', '["sign"]']
        for c in cases:
            self.assertEqual(upstream(c), mine(c),
                             f"判据不一致（输入={c!r}）→ 上游已漂移，须同步")

    def test_capture_without_login_refuses_write(self):
        """无登录态时必须拒绝写回，且给出可操作提示（绝不污染 .env）。"""
        import inspect
        src = inspect.getsource(self.cc.capture_from_camoufox)
        self.assertIn("未检测到登录态", src)
        self.assertIn("不写残缺凭证", src)


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
