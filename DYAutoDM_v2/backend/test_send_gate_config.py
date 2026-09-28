"""发送闸门参数接线验证（B1）。

关键点：闸门参数从**模块级常量**改为**每次调用读配置中心**，
从而把「需重启 recv_daemon」降级为热生效。

验证目标：
1. 未配置时行为与接线前一致（默认 8s / 30s）——零回归
2. 改配置后立即生效（同一进程内，不重启）
3. 配置中心异常时回落模块级兜底，绝不崩

不启应用即可跑：
  cd backend && python test_send_gate_config.py
"""

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_TMP = os.path.join(os.environ.get("TEMP", "/tmp"), "dyautodm_gate_test")
os.makedirs(_TMP, exist_ok=True)
os.environ["DY_APP_ROOT"] = _TMP

import daemon.recv_daemon as rd  # noqa: E402
from services import app_config as ac  # noqa: E402

_orig_per_minute = rd._cfg_per_minute   # 供边界用例临时打桩/还原


class TestSendGateConfig(unittest.TestCase):
    def setUp(self):
        ac.reset_section("send")
        rd._send_gate_last.clear()
        rd._send_gate_minute.clear()

    # ---- 1. 零回归 ----
    def test_default_matches_pre_change(self):
        # 2026-09-17 修补（OCR 审查 CRITICAL）：兜底闸门在 v0.43.40 已
        # **有意放宽**为 `max(2.0, 配置值 * 0.5)`（专防绕过调度器的直发，
        # 见 recv_daemon.py 闸门注释）。原断言写 8.0 未考虑 0.5 折扣 → 必失败。
        self.assertEqual(rd._cfg_min_interval(), 4.0)   # 8.0 * 0.5
        self.assertEqual(rd._cfg_max_wait(), 30.0)

    def test_fallback_constant_unchanged(self):
        # 常量真名是 _FALLBACK_*（_SEND_GATE_* 不存在，原写法必抛 AttributeError）
        self.assertEqual(rd._FALLBACK_MIN_INTERVAL, 8.0)
        self.assertEqual(rd._FALLBACK_MAX_WAIT, 30.0)

    # ---- 2. 热生效 ----
    def test_config_change_takes_effect_immediately(self):
        # 同一进程内改配置，无需重启；注意闸门值 = 配置值 * 0.5（下限 2s）
        ac.save_section("send", {"min_interval": 15.0, "max_wait": 45.0})
        self.assertEqual(rd._cfg_min_interval(), 7.5)    # 15.0 * 0.5
        self.assertEqual(rd._cfg_max_wait(), 45.0)

    def test_gate_actually_uses_new_value(self):
        """闸门真的按新间隔拦：第二次获取应被拒。

        闸门值 = max(2.0, 配置值 * 0.5)。设 min_interval=8 → 闸门 4s；
        max_wait=5 → 第二次在 4s 后即可放行（间隔先于 deadline 到期）。
        """
        ac.save_section("send", {"min_interval": 8.0, "max_wait": 5.0})
        self.assertEqual(rd._cfg_min_interval(), 4.0)    # 8.0 * 0.5
        self.assertEqual(rd._cfg_max_wait(), 5.0)

        ok1, _, _r1 = rd._send_gate_acquire("测试账号")
        self.assertTrue(ok1, "首次应放行")
        self.assertEqual(_r1, "")
        t0 = time.time()
        ok2, waited, _r2 = rd._send_gate_acquire("测试账号")
        cost = time.time() - t0
        # 闸门间隔 4s < max_wait 5s → 等待 4s 后放行（而非失败）
        self.assertTrue(ok2, "闸门间隔(4s)短于 max_wait(5s)，第二次应等待后放行")
        self.assertGreaterEqual(cost, 3.5, f"应等待约 4s，实际 {cost:.1f}s")

    # ---- 4. 分钟窗（2026-09-28 ADR-021 残留补齐） ----
    def test_minute_limit_blocks_after_n(self):
        """每分钟上限 3：第 4 次获取必须被拒，且原因码 = minute_limit。"""
        ac.save_section("send", {"per_minute_limit": 3, "min_interval": 8.0,
                                 "max_wait": 5.0})
        for i in range(3):
            ok, _, _r = rd._send_gate_acquire("acc_min")
            self.assertTrue(ok, f"第 {i+1} 条应放行")
        ok, wait, reason = rd._send_gate_acquire("acc_min")
        self.assertFalse(ok, "第 4 条必须被分钟窗拦下")
        self.assertEqual(reason, "minute_limit")
        self.assertGreater(wait, 0.0)

    def test_minute_limit_zero_disabled(self):
        """per_minute_limit=0 → 不启用分钟窗（零回归）。"""
        ac.save_section("send", {"per_minute_limit": 0, "min_interval": 8.0,
                                 "max_wait": 5.0})
        for i in range(10):
            ok, _, _r = rd._send_gate_acquire("acc_zero")
            self.assertTrue(ok, f"per_minute=0 时第 {i+1} 条不应被拦")

    def test_gate_error_is_attributable(self):
        """失败返回必须带 error_kind=rate_limited + reason_code（可归因）。"""
        ac.save_section("send", {"per_minute_limit": 1, "min_interval": 8.0,
                                 "max_wait": 5.0})
        rd._send_gate_acquire("acc_attr")
        d = rd._gate_error("acc_attr", "minute_limit")
        self.assertFalse(d["ok"])
        self.assertEqual(d["error_kind"], "rate_limited")
        self.assertEqual(d["reason_code"], "minute_limit")
        self.assertIn("每分钟", d["msg"])

    def test_fractional_per_minute_floor_one(self):
        """边界：per_minute=0.5（>0 但 <1）不得被 int() 成 0 而恒拦一切。

        离线路径直接调 `_send_gate_acquire` 验证：前 1 条放行、第 2 条被拦。
        """
        rd._cfg_per_minute = lambda: 0.5
        try:
            ok1, _, _r = rd._send_gate_acquire("acc_frac")
            self.assertTrue(ok1, "分数上限应至少放行 1 条")
            ok2, _, reason = rd._send_gate_acquire("acc_frac")
            self.assertFalse(ok2)
            self.assertEqual(reason, "minute_limit")
        finally:
            rd._cfg_per_minute = _orig_per_minute

    # ---- 5. 手动豁免（2026-09-28 用户拍板：由配置控制，不默认定死） ----
    def test_manual_exempt_default_allows_beyond_minute(self):
        """默认「手动豁免」：source=manual 连发超过上限**不被拦**；自动源被拦。"""
        ac.save_section("send", {"per_minute_limit": 1, "min_interval": 8.0,
                                 "max_wait": 5.0})
        # 手动：连发 5 条都应放行（豁免分钟窗）
        for i in range(5):
            ok, _, r = rd._send_gate_acquire("acc_manual", "manual")
            self.assertTrue(ok, f"手动第 {i+1} 条应被豁免放行（原因 {r}）")
        # 同一账号的自动源：分钟窗已被手动的 5 条占满 ⇒ 立即被拦
        ok, _, reason = rd._send_gate_acquire("acc_manual", "")
        self.assertFalse(ok, "自动源应被分钟窗拦下（水位被手动抬高）")
        self.assertEqual(reason, "minute_limit")

    def test_manual_exempt_configurable_off(self):
        """关闭「手动豁免」→ 手动发送同样受分钟窗约束（由配置控制，非定死）。"""
        ac.save_section("send", {"per_minute_limit": 1, "min_interval": 8.0,
                                 "max_wait": 5.0,
                                 "per_minute_manual_exempt": False})
        ok1, _, _ = rd._send_gate_acquire("acc_off", "manual")
        self.assertTrue(ok1)
        ok2, _, reason = rd._send_gate_acquire("acc_off", "manual")
        self.assertFalse(ok2, "关闭豁免后手动第 2 条应被拦")
        self.assertEqual(reason, "minute_limit")

    def test_source_is_manual_helper(self):
        self.assertTrue(rd._source_is_manual("manual"))
        self.assertTrue(rd._source_is_manual(" Manual "))
        self.assertFalse(rd._source_is_manual(""))
        self.assertFalse(rd._source_is_manual("dispatch"))

    # ---- 3. 边界 ----
    def test_out_of_range_keeps_default(self):
        # min_interval=1.0 被 schema 下限(8)拒绝 → 回落默认 8 → 闸门 4.0
        ac.save_section("send", {"min_interval": 1.0})
        self.assertEqual(rd._cfg_min_interval(), 4.0)

    def test_fallback_when_app_config_broken(self):
        """配置中心抛异常时回落兜底常量，绝不崩。

        注意：_cfg_min_interval 对兜底值同样打 5 折（下限 2s），
        故断言的是折扣后的值，而非裸常量。
        """
        orig = ac.get
        try:
            ac.get = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
            self.assertEqual(rd._cfg_min_interval(),
                             max(2.0, rd._FALLBACK_MIN_INTERVAL * 0.5))
            self.assertEqual(rd._cfg_max_wait(), rd._FALLBACK_MAX_WAIT)
        finally:
            ac.get = orig


if __name__ == "__main__":
    unittest.main(verbosity=2)
