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
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# M-28（2026-09-29）：模块级根改为**一次性临时目录**，并在**每个用例 setUp**
# 里重新钉根 —— 原实现用*固定*目录 + 只在模块级设一次，执行期会被别的模块
# 改写 DY_APP_ROOT（`database.get_db()` 每次调用按当前根做会员一致性校验），
# 于是读到的库文件由「谁最后设根」决定 ⇒ 顺序相关假失败。
# 标识符（父会话 grep 用）：N1_M28_PIN_ROOT
_TMP = tempfile.mkdtemp(prefix="n1_m28_dispatch_")
os.makedirs(_TMP, exist_ok=True)
os.environ["DY_APP_ROOT"] = _TMP

import services.dm_dispatch as dd  # noqa: E402
from services import app_config as ac  # noqa: E402
# M-28 主毒源修复：`dd.cfg` 可能被别的测试模块（实测为 test_uid_sink_ext，
# 其 tearDown 把 dd.cfg 永久换成一个 bound method）在执行期换掉。这里在导入期
# 抓一份**原始函数对象**的强引用，setUp 里无条件归还 ⇒ 本模块不依赖执行顺序。
from services.dm_dispatch import cfg as _PRISTINE_CFG  # noqa: E402


def _n1_m28_pin_root():
    """N1_M28_PIN_ROOT：重钉 DY_APP_ROOT + 归还 dm_dispatch.cfg 原始实现。"""
    os.environ["DY_APP_ROOT"] = _TMP
    import database
    database.reset_connection()
    if getattr(dd, "cfg", None) is not _PRISTINE_CFG:
        dd.cfg = _PRISTINE_CFG

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
    # 2026-09-28：账号级分钟窗（全量发送）—— 默认 3/分钟，可设 0 关闭
    "PER_MINUTE_LIMIT": 3,
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
        # N1_M28_PIN_ROOT：每个用例重新钉根 + 归还 dd.cfg 原始实现
        _n1_m28_pin_root()
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
