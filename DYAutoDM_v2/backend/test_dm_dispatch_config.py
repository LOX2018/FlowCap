"""dm_dispatch 风控参数接线验证（B2）。

这些是**风控核心参数**，接线失败的代价是限流/封号，故测试目标：
1. 零回归：默认值与接线前逐字一致（含类型）
2. 热生效：改配置后 cfg() 与直接读常量名都拿到新值
3. 回落：配置中心异常时用兜底常量，绝不崩
4. 覆盖完整：16 个参数一个都不能漏接线

跑法：cd backend && python test_dm_dispatch_config.py
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_TMP = os.path.join(os.environ.get("TEMP", "/tmp"), "dyautodm_dispatch_test")
os.makedirs(_TMP, exist_ok=True)
os.environ["DY_APP_ROOT"] = _TMP

import services.dm_dispatch as dd  # noqa: E402
from services import app_config as ac  # noqa: E402

# 接线前实测基线（2026-09-08，见提交记录）
# 2026-09-24 扩展：+5 项（ADR-007 / C-06 沉淀池增强）。
#   ⚠️ 本表是**硬编码副本**（M-10 同类债）：新增参数必须手动登记，否则
#      `test_all_params_wired` 会红 —— 这正是它存在的意义（强制显式接线）。
BASELINE = {
    "QUEUE_MAX": 200,
    "POOL_STRICT": True,
    "DEDUP_WINDOW": 5.0,
    "STRANGER_PER_MINUTE": 2,
    "STRANGER_PER_DAY": 30,
    "COOLDOWN_ON_FREQUENT": 600.0,
    "COOLDOWN_MAX": 3600.0,
    "WEIGHT_RECOVER_HALFLIFE": 21600.0,
    "WEIGHT_FORGIVE_AFTER": 86400.0,
    "UID_SINK_COOLDOWN": 604800.0,
    "UID_SINK_STRICT": True,
    # ADR-007 / C-06（2026-09-24）—— 窗口/阈值默认 0 = 能力休眠，零回归
    "UID_SINK_WINDOW": 0.0,
    "HIGH_VALUE_WINDOW": 60.0,
    "HIGH_VALUE_THRESHOLD": 0,
    "HIGH_VALUE_LLM": False,
    "AGGREGATE_MAX_CHARS": 2000,
}


class TestDmDispatchConfig(unittest.TestCase):
    def setUp(self):
        ac.reset_section("send")

    # ---- 1. 零回归 ----
    def test_all_defaults_identical_to_baseline(self):
        for name, exp in BASELINE.items():
            got = getattr(dd, name)
            self.assertEqual(got, exp, f"{name} 默认值变了")
            self.assertIsInstance(got, type(exp), f"{name} 类型变了")

    def test_fallback_constants_exist(self):
        """兜底常量必须存在（配置中心不可用时依赖它）。"""
        for name in BASELINE:
            self.assertIn("_FALLBACK_" + name, vars(dd), f"{name} 缺兜底常量")

    def test_all_params_wired(self):
        """11 个参数必须全部登记进 _LAZY_MAP，漏一个 = 该参数永远用旧值。"""
        self.assertEqual(set(dd._LAZY_MAP), set(BASELINE),
                         "接线清单与基线不一致")

    # ---- 2. 热生效 ----
    def test_cfg_reads_config_center(self):
        ac.save_section("send", {"stranger_per_day": 77})
        self.assertEqual(dd.cfg("STRANGER_PER_DAY"), 77)
        # 直接读常量名也应拿到新值（__getattr__ 生效）
        self.assertEqual(dd.STRANGER_PER_DAY, 77)

    def test_bool_and_float_types_preserved(self):
        ac.save_section("send", {"pool_strict": False, "dedup_window": 12.5})
        self.assertIs(dd.cfg("POOL_STRICT"), False)
        self.assertEqual(dd.cfg("DEDUP_WINDOW"), 12.5)

    # ---- 3. 回落 ----
    def test_fallback_when_config_center_broken(self):
        orig = ac.get
        try:
            ac.get = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
            for name, exp in BASELINE.items():
                self.assertEqual(dd.cfg(name), exp, f"{name} 回落失败")
        finally:
            ac.get = orig

    # ---- 4. 边界 ----
    def test_out_of_range_keeps_default(self):
        # stranger_per_minute 上限 60
        ac.save_section("send", {"stranger_per_minute": 9999})
        self.assertEqual(dd.cfg("STRANGER_PER_MINUTE"), 2)

    def test_unknown_name_raises(self):
        with self.assertRaises(KeyError):
            dd.cfg("NO_SUCH_PARAM")
        with self.assertRaises(AttributeError):
            _ = dd.NO_SUCH_PARAM


if __name__ == "__main__":
    unittest.main(verbosity=2)
