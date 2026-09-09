"""B4：三个全局开关（enable_danmaku/enable_console/enable_send）接线验证。

目标：
1. 零回归：未写入配置中心时，_flag() 回落到 settings 实例，行为与接线前一致
2. 读优先：配置中心有值 → 读配置中心（覆盖 settings）
3. 写双向：save_config 写 settings 的同时写入配置中心
"""

import os
import sys
import tempfile
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_tmp = os.path.join(tempfile.gettempdir(), "dyautodm_b4_test")
os.makedirs(_tmp, exist_ok=True)
os.environ["DY_APP_ROOT"] = _tmp

from services import app_config as ac  # noqa: E402
import api.tasks as T  # noqa: E402


def make_settings(**kw):
    s = types.SimpleNamespace()
    s.enable_danmaku = kw.get("enable_danmaku", True)
    s.enable_console = kw.get("enable_console", True)
    s.enable_send = kw.get("enable_send", True)
    return s


class TestFlagFallback(unittest.TestCase):
    """零回归：未配置时回落 settings。"""

    def setUp(self):
        ac.reset_section("task")

    def tearDown(self):
        ac.reset_section("task")

    def test_fallback_true(self):
        s = make_settings()
        self.assertTrue(T._flag("enable_danmaku", s))
        self.assertTrue(T._flag("enable_console", s))
        self.assertTrue(T._flag("enable_send", s))

    def test_fallback_false(self):
        s = make_settings(enable_danmaku=False, enable_console=False,
                          enable_send=False)
        self.assertFalse(T._flag("enable_danmaku", s))
        self.assertFalse(T._flag("enable_console", s))
        self.assertFalse(T._flag("enable_send", s))


class TestFlagPriority(unittest.TestCase):
    """读优先：配置中心有值则覆盖 settings。"""

    def setUp(self):
        ac.reset_section("task")

    def tearDown(self):
        ac.reset_section("task")

    def test_config_wins(self):
        s = make_settings(enable_send=True)   # settings 说 True
        ac.save_section("task", {"enable_send": False})  # 配置中心说 False
        self.assertFalse(T._flag("enable_send", s))

    def test_all_three(self):
        s = make_settings()
        ac.save_section("task", {
            "enable_danmaku": False,
            "enable_console": False,
            "enable_send": False,
        })
        self.assertFalse(T._flag("enable_danmaku", s))
        self.assertFalse(T._flag("enable_console", s))
        self.assertFalse(T._flag("enable_send", s))


class TestSchemaExists(unittest.TestCase):
    """三个开关必须在 schema 里注册（否则设置页不显示）。"""

    def test_in_schema(self):
        fields = ac.SECTIONS["task"]["fields"]
        for k in ("enable_danmaku", "enable_console", "enable_send"):
            self.assertIn(k, fields, f"{k} 未在 schema 注册")
            self.assertEqual(fields[k]["type"], "bool")

    def test_defaults_true(self):
        fields = ac.SECTIONS["task"]["fields"]
        for k in ("enable_danmaku", "enable_console", "enable_send"):
            self.assertIs(fields[k]["default"], True, f"{k} 默认应为 True")

    def test_hot_apply(self):
        """三个开关都是热生效（不该要求重启）。"""
        fields = ac.SECTIONS["task"]["fields"]
        for k in ("enable_danmaku", "enable_console", "enable_send"):
            self.assertEqual(fields[k].get("apply"), "hot", f"{k} 应为 hot")


if __name__ == "__main__":
    unittest.main(verbosity=0)
