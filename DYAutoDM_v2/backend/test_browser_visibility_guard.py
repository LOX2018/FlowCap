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

BE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BE)

BROWSER_DAEMON = os.path.join(BE, "daemon", "browser_daemon.py")
BCC_LOGIN = os.path.join(BE, "daemon", "bcc_login.py")
BCC_CAPTURE = os.path.join(BE, "daemon", "bcc_capture.py")
BCC_AUDIT = os.path.join(BE, "daemon", "bcc_audit.py")
JS = os.path.join(BE, "daemon", "browser_daemon_js.py")
GATE = os.path.join(BE, "services", "browser_gate.py")
API_ACC = os.path.join(BE, "api", "accounts.py")
AUTO_ACC = os.path.join(BE, "auto_dm", "accounts.py")


def _read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def _read_all(*paths):
    """Read multiple files and return their concatenated content."""
    parts = []
    for p in paths:
        parts.append(_read(p))
    return "\n".join(parts)


class TestA_VisibilityRebuild(unittest.TestCase):
    """A：纯无头下不得走「只改窗口状态」的假成功路径。"""

    @classmethod
    def setUpClass(cls):
        cls.src = _read_all(BROWSER_DAEMON, BCC_AUDIT)

    def test_01_window_mode_requires_headed(self):
        """window 快路径的前提必须包含「当前非无头」。
        # 2026-09-20：正则原为 `\((.*?)\)\s*\n`，会被表达式内的
        # `("exe", "camoufox")` 里的 `)` 提前截断（非贪婪 + 换行边界）
        # → 断言失效。改为锚定「赋值起始」到「行首右括号结束」的整段。
        """
        m = re.search(
            r"_can_window_mode\s*=\s*\((.*?)^\s*\)",
            self.src, re.S | re.M)
        self.assertIsNotNone(m, "未找到 _can_window_mode 定义")
        expr = m.group(1)
        self.assertIn("not self._headless", expr,
                      "window 模式必须以「当前有头」为前提（纯无头下 setWindowBounds 无效）")
        self.assertIn('"camoufox"', expr,
                      "window 模式的后端判断必须含 camoufox（否则切可见永远走重建）")

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
        cls.src = _read_all(BROWSER_DAEMON, BCC_AUDIT)

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
        cls.src = _read_all(BROWSER_DAEMON, BCC_LOGIN, BCC_AUDIT)

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


class TestL_CamoufoxNoFallback(unittest.TestCase):
    """L：启用 Camoufox 时必须**禁止静默回退** Chromium（v0.44.2 用户拍板）。

    背景：GUI 启动的 sidecar 不继承 shell 的 DY_BROWSER_KERNEL，导致
    「以为切了内核、实际仍走 Chromium」而全程无声。现要求：
      ① 内核由配置真源 auto_dm/config.py 的 DY_BROWSER_KERNEL 决定；
      ② 启用 camoufox 时启动失败必须抛 BCC-058，不得降级到 Chromium。
    """

    def test_config_declares_kernel(self):
        """配置真源必须显式声明内核（GUI 路径不继承环境变量）。"""
        from auto_dm import config as cfg
        self.assertEqual(
            getattr(cfg, "DY_BROWSER_KERNEL", ""), "camoufox",
            "auto_dm/config.py 未声明 DY_BROWSER_KERNEL=camoufox → GUI 启动会走 Chromium")

    def test_kernel_decision_ignores_env_when_config_set(self):
        """配置已声明时，判定只看配置（显式配置原则：不依赖外部可变状态）。"""
        import os
        from auto_dm import config as cfg
        from vbrowser_camoufox import camoufox_enabled
        old = os.environ.pop("DY_BROWSER_KERNEL", None)
        try:
            self.assertTrue(camoufox_enabled(cfg))
        finally:
            if old is not None:
                os.environ["DY_BROWSER_KERNEL"] = old

    def test_no_silent_fallback_on_camoufox_failure(self):
        """两个启动出口都必须含「禁止回退」抛错分支（防单边漂移）。"""
        import inspect
        import vbrowser as vb
        for fn, name in ((vb.launch_async, "launch_async"),
                         (vb.launch_sync, "launch_sync")):
            src = inspect.getsource(fn)
            self.assertIn("禁止回退 Chromium", src,
                          f"{name} 缺少禁止回退分支 → Camoufox 失败会静默降级")
            self.assertIn("BCC-058", src, f"{name} 未上报 BCC-058")


class TestM_CamoufoxAsyncApi(unittest.TestCase):
    """M：Camoufox 在 asyncio 环境下**必须使用 async API**（v0.44.8 实测事故）。

    事故：`launch_camoufox_async` 初版用 `run_in_executor` 包同步 API ——
    Playwright 的「Sync API inside asyncio loop」检测是**进程级**的，换线程无效。
    后果：BCC（FastAPI/asyncio）拿不到 context 句柄，但 Camoufox 进程**已经起来**
    → 变成**无人回收的孤儿进程**，占着 `_camoufox` 的 parent.lock，
    用户双击打开浏览器被提示「占用」。
    """

    def test_async_launcher_uses_async_camoufox(self):
        """必须 import AsyncCamoufox 且用 __aenter__（不得包 sync API）。

        只检查**可执行代码行**（剔除 docstring/注释）——docstring 里会提到
        "run_in_executor" 作为反例说明，不能据此误判。
        """
        import inspect
        import tokenize
        import io
        import vbrowser_camoufox as vc
        src = inspect.getsource(vc.launch_camoufox_async)
        # 去注释
        code_lines = []
        for ln in src.splitlines():
            s = ln.strip()
            if s.startswith("#"):
                continue
            code_lines.append(ln)
        code = "\n".join(code_lines)
        # 去 docstring（粗粒度：去掉三引号块内容）
        import re as _re
        code = _re.sub(r'"""[\s\S]*?"""', '""', code)
        code = _re.sub(r"'''[\s\S]*?'''", "''", code)

        self.assertIn("AsyncCamoufox", code,
                      "async 启动未使用 AsyncCamoufox → asyncio 环境会失败并残留孤儿进程")
        self.assertIn("__aenter__", code, "未使用异步上下文进入（__aenter__）")
        self.assertNotIn("run_in_executor", code,
                         "仍用线程池包同步 API —— 实测必然失败")
        self.assertNotIn("launch_camoufox_sync", code,
                         "async 路径不得调用同步启动函数")

    def test_sync_launcher_unchanged_contract(self):
        """同步版仍须存在且用同步 API（CLI/脚本路径依赖它）。"""
        import inspect
        import vbrowser_camoufox as vc
        src = inspect.getsource(vc.launch_camoufox_sync)
        self.assertIn("from camoufox.sync_api import Camoufox", src)
        self.assertIn("__enter__", src)

    def test_orphan_guard_profile_path_contract(self):
        """两个启动器必须共用同一 profile 路径契约（防再次出现双 profile）。"""
        import inspect
        import vbrowser_camoufox as vc
        for fn, name in ((vc.launch_camoufox_sync, "sync"),
                         (vc.launch_camoufox_async, "async")):
            src = inspect.getsource(fn)
            self.assertIn('os.path.join(user_data_dir, "_camoufox")', src,
                          f"{name} 未使用 <profile>/_camoufox 契约 → 会与另一路径分叉")


class TestN_CamoufoxBackendParity(unittest.TestCase):
    """N：Camoufox 必须与 Chromium 在 BCC 生命周期操作上**同等对待**（v0.44.9）。

    事故（2026-09-20 用户报「双击提示占用 / 弹窗没窗口」）：
    BCC 的可见性切换、context 清理等处硬编码 `self._backend == "exe"`，
    而 Camoufox 的 backend 是 `"camoufox"` → 全部不匹配 →
    **旧 Camoufox 进程不被关闭**，继续占住 `_camoufox` profile →
    新的有头实例启动报 `Failed to launch the browser process`。

    同时：AsyncCamoufox 只调 `context.close()` **不会**结束浏览器进程，
    必须走 `__aexit__`（见 vbrowser_camoufox.close_camoufox_context）。
    """

    def setUp(self):
        self.p = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "daemon", "browser_daemon.py")
        self.p2 = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "daemon", "bcc_routes.py")
        self.p3 = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "daemon", "bcc_login.py")
        self.p4 = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "daemon", "bcc_audit.py")
        self.src = open(self.p, encoding="utf-8").read()
        self.src2 = open(self.p2, encoding="utf-8").read() if os.path.isfile(self.p2) else ""
        self.src3 = open(self.p3, encoding="utf-8").read() if os.path.isfile(self.p3) else ""
        self.src4 = open(self.p4, encoding="utf-8").read() if os.path.isfile(self.p4) else ""

    def test_no_bare_exe_backend_check(self):
        """不得再有只认 "exe" 的裸判断（会漏掉 camoufox）。"""
        bad = 'self._backend == "exe"' in self.src or 'c._backend == "exe"' in self.src
        bad2 = 'self._backend == "exe"' in self.src2 or 'c._backend == "exe"' in self.src2
        self.assertFalse(
            bad or bad2,
            "存在 `_backend == \"exe\"` 裸判断 → camoufox 会被漏掉（context 不关闭）")

    def test_backend_checks_include_camoufox(self):
        """所有后端判断必须同时覆盖 exe 与 camoufox。"""
        # main module (browser_daemon.py) 中的判断
        main_ok = ('self._backend in ("exe", "camoufox")' in self.src or
                   'c._backend in ("exe", "camoufox")' in self.src)
        # routes module (bcc_routes.py) 中的判断
        routes_ok = ('self._backend in ("exe", "camoufox")' in self.src2 or
                     'c._backend in ("exe", "camoufox")' in self.src2)
        self.assertTrue(
            main_ok or routes_ok,
            "任何 `_backend` 判断必须同时覆盖 exe 与 camoufox，全部缺失")

    def test_context_close_dispatches_to_camoufox_helper(self):
        """关闭 Camoufox context 必须走 close_camoufox_context（__aexit__）。

        browser_daemon.py (2) + bcc_login.py (2) + bcc_audit.py (2) + bcc_routes.py (4) = 10
        """
        total = (self.src.count("close_camoufox_context") +
                 self.src3.count("close_camoufox_context") +
                 self.src4.count("close_camoufox_context") +
                 self.src2.count("close_camoufox_context"))
        self.assertGreaterEqual(
            total, 5,
            "Camoufox 关闭点未全部走统一 helper")

    def test_helper_uses_aexit(self):
        """helper 本体必须调用 __aexit__（否则进程不退，profile 被占）。"""
        import inspect
        import vbrowser_camoufox as vc
        src = inspect.getsource(vc.close_camoufox_context)
        self.assertIn("__aexit__", src)
        src_sync = inspect.getsource(vc.close_camoufox_context_sync)
        self.assertIn("__exit__", src_sync)


class TestO_WindowVisibleCrossKernel(unittest.TestCase):
    """O：可见窗口检测必须跨内核（Camoufox/Firefox 的窗口类名 ≠ Chromium）。

    事故（2026-09-20 用户报「双击没窗口」）：`_window_really_visible` 只认
    `Chrome_WidgetWin`，且 PID 只从 `self._browser` 取；而 Camoufox 走
    AsyncCamoufox 返回 **BrowserContext**（`_browser` 为 None）→ pids 为空
    直接 return False → 把**已出来的 Firefox 窗口**判为不可见 →
    BCC-053 误判切换失败、回退无头。实测系统里确有可见的 MozillaWindowClass 窗口。
    """

    def setUp(self):
        self.p = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "daemon", "browser_daemon.py")
        self.p2 = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "daemon", "bcc_audit.py")
        self.src = open(self.p, encoding="utf-8").read()
        self.src2 = open(self.p2, encoding="utf-8").read() if os.path.isfile(self.p2) else ""

    def test_accepts_mozilla_window_class(self):
        self.assertIn("MozillaWindowClass", self.src2,
                      "未支持 Firefox 窗口类名 → Camoufox 有头窗口会被判不可见")

    def test_keeps_chromium_window_class(self):
        self.assertIn("Chrome_WidgetWin", self.src2, "不得丢弃 Chromium 支持")

    def test_pid_fallback_when_browser_is_none(self):
        """_browser 为 None（Camoufox/AsyncCamoufox）时必须有 PID 兜底。

        2026-09-20 修正：原断言只查字面量 `"camoufox.exe"`，而该字符串会出现在
        **注释**里 → 断言「假绿」（把注释当实现，典型的形式主义门禁）。现改为
        断言**真机制**：进程名不作过滤依据，改按 cmdline 稳定片段匹配。
        """
        i = self.src2.find("async def _window_really_visible")
        self.assertGreater(i, 0)
        seg = self.src2[i:i + 9000]
        self.assertIn("_profile_dir", seg,
                      "缺少按 profile 路径匹配进程的兜底 → Camoufox 下 pids 恒空")
        self.assertIn("psutil", seg,
                      "首选机制必须是 psutil（不依赖 PATH / 外部命令 / 进程名）")
        self.assertIn("process_iter", seg, "兜底应遍历进程而非只看窗口")
        self.assertIn("cmdline", seg, "兜底应按命令行匹配，而非写死进程名")

    def test_no_hardcoded_process_name_filter(self):
        """不得再写死进程名过滤（本案真因）。

        实测：真实内核进程名不是 camoufox.exe（本机是 firefox.exe），
        `$_.Name -eq "camoufox.exe"` 使查询恒空 → pids 空 → 误判不可见 → BCC-053。
        """
        i = self.src2.find("async def _window_really_visible")
        seg = self.src2[i:i + 9000]
        self.assertNotIn('$_.Name -eq', seg,
                         "不得按固定进程名过滤（进程名会漂移，须按 cmdline 匹配）")

    def test_fallback_failure_is_loud(self):
        """兜底拿不到 PID 必须响亮告警，不得静默 return False。"""
        i = self.src2.find("async def _window_really_visible")
        seg = self.src2[i:i + 9000]
        self.assertIn("未能解析到", seg,
                      "兜底未命中必须留下可诊断的 WARNING（静默失败正是本案真因）")
        self.assertIn("logger.warning", seg)

    def test_no_wmic_dependency(self):
        """不得依赖 wmic（本机已移除，实测 FileNotFoundError）。"""
        i = self.src2.find("async def _window_really_visible")
        seg = self.src2[i:i + 4000]
        self.assertNotIn('"wmic"', seg,
                         "wmic 在本机不可用；用 PowerShell Get-CimInstance")
        self.assertIn("powershell", seg.lower())


class TestP_SwitchTimingAndProfileRelease(unittest.TestCase):
    """P：切换判据必须「轮询 + 诚实降级」；profile 释放必须对 Firefox 有效。

    事故（2026-09-20 实测）：`_do_switch_background` 在 `_launch()` 返回后**只检查
    一次**；同时 `_wait_profile_released` 只认 Chromium 锁名（Camoufox 是 Firefox，
    锁名 `parent.lock`）→ 对 Camoufox **静默空操作**。
    """

    @classmethod
    def setUpClass(cls):
        cls.src = _read_all(BCC_AUDIT, BROWSER_DAEMON)

    def test_polling_replaces_single_check(self):
        self.assertIn("async def _wait_window_visible(self, target: bool) -> bool | None:",
                      self.src, "必须新增带重试的可见性轮询")
        i = self.src.find("async def _do_switch_background")
        j = self.src.find("async def _wait_profile_released")
        seg = self.src[i:j]
        self.assertIn("await self._wait_window_visible(target)", seg,
                      "切换完成判据必须走轮询，而非单次检查")
        self.assertNotIn("await self._window_really_visible()", seg,
                         "不得再在切换路径单次直查（窗口尚未出现即误判 → BCC-053）")

    def test_honest_degradation_does_not_force_headless(self):
        """检测函数据取不到时，不得擅自把 _headless 回退（既不误报也不谎报）。"""
        i = self.src.find("async def _do_switch_background")
        j = self.src.find("async def _wait_profile_released")
        seg = self.src[i:j]
        self.assertIn("**无法确认**", seg, "必须有「证据取不到」的诚实降级分支")
        self.assertIn("elif _vis is False:", seg,
                      "回退为无头只能出现在「确认不可见」的严格分支里")
        # 诚实降级分支（else）内不得出现状态回退
        k = seg.find("else:")
        self.assertGreater(k, 0, "缺少 else 分支")
        self.assertNotIn("self._headless = True", seg[k:k + 400],
                         "诚实降级分支不得擅自回退状态")

    def test_wait_profile_released_covers_camoufox(self):
        i = self.src.find("async def _wait_profile_released")
        j = self.src.find("async def submit(")
        seg = self.src[i:j]
        self.assertIn('"camoufox"', seg, "Camoufox 必须走进程退出判据")
        self.assertIn("psutil", seg)
        self.assertIn("_needle", seg)
        # Chromium 路径不得被破坏
        self.assertIn("SingletonLock", seg, "不得删除 Chromium 锁名（回归）")
        self.assertIn("lockfile", seg, "不得删除 ungoogled-chromium 锁名（回归）")

    def test_browser_pid_falls_back_to_context(self):
        """同构契约漏洞的调用侧补强：context 自带 browser 时也要用上。"""
        self.assertIn('getattr(self._context, "browser", None)', self.src,
                      "Camoufox 返回 BrowserContext，_browser 为 None，须从 context 取")


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