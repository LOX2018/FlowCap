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


class TestSendGateConfig(unittest.TestCase):
    def setUp(self):
        ac.reset_section("send")
        rd._send_gate_last.clear()

    # ---- 1. 零回归 ----
    def test_default_matches_pre_change(self):
        self.assertEqual(rd._cfg_min_interval(), 8.0)
        self.assertEqual(rd._cfg_max_wait(), 30.0)

    def test_fallback_constant_unchanged(self):
        self.assertEqual(rd._SEND_GATE_MIN_INTERVAL, 8.0)
        self.assertEqual(rd._SEND_GATE_MAX_WAIT, 30.0)

    # ---- 2. 热生效 ----
    def test_config_change_takes_effect_immediately(self):
        # 同一进程内改配置，无需重启
        ac.save_section("send", {"min_interval": 15.0, "max_wait": 45.0})
        self.assertEqual(rd._cfg_min_interval(), 15.0)
        self.assertEqual(rd._cfg_max_wait(), 45.0)

    def test_gate_actually_uses_new_value(self):
        """闸门真的按新间隔拦：第二次获取应被拒。

        注意 max_wait 取 schema 下限 5s（低于则被配置中心丢弃回落 30s）。
        设 min_interval=8（下限）、max_wait=5 → 第二次必在 5s 后快速失败，
        而不是等到 8s 才放行——这正是「走配置值」与「走兜底 8s」的分界。
        """
        ac.save_section("send", {"min_interval": 8.0, "max_wait": 5.0})
        self.assertEqual(rd._cfg_min_interval(), 8.0)
        self.assertEqual(rd._cfg_max_wait(), 5.0)

        ok1, _ = rd._send_gate_acquire("测试账号")
        self.assertTrue(ok1, "首次应放行")
        t0 = time.time()
        ok2, waited = rd._send_gate_acquire("测试账号")
        cost = time.time() - t0
        self.assertFalse(ok2, "间隔未到应被闸门拦住")
        # 关键：应在 max_wait(5s) 附近失败，而不是等到 min_interval(8s) 才放行
        self.assertLess(cost, 7.0, f"应在 ~5s 快速失败，实际耗时 {cost:.1f}s")
        self.assertGreaterEqual(waited, 4.5)

    # ---- 3. 边界 ----
    def test_out_of_range_keeps_default(self):
        ac.save_section("send", {"min_interval": 1.0})   # 下限 8s
        self.assertEqual(rd._cfg_min_interval(), 8.0)

    def test_fallback_when_app_config_broken(self):
        """配置中心抛异常时回落兜底常量，绝不崩。"""
        orig = ac.get
        try:
            ac.get = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
            self.assertEqual(rd._cfg_min_interval(), rd._SEND_GATE_MIN_INTERVAL)
            self.assertEqual(rd._cfg_max_wait(), rd._SEND_GATE_MAX_WAIT)
        finally:
            ac.get = orig


if __name__ == "__main__":
    unittest.main(verbosity=2)
