"""B4：三个全局开关（enable_danmaku/enable_console/enable_send）接线验证。

2026-09-09 定调（用户决策）：**settings 优先**，配置中心只作镜像同步，不反向覆盖。

  读：一律以 settings 实例为准（保留接线前行为，零回归）
  写：save_config 双写，把值镜像进 app_config 的 live 分区，供设置页展示

修正历史：
  - 初版把分区名写成 "task"（实际不存在）→ 改 "live"，并加存在性断言防回归
  - 初版让配置中心优先 → 因 get() reset 后返回默认 True，settings 的 False
    永不可达 → 按用户决策改为 settings 优先
  - 初版无 DB 隔离 → 各测试文件互相污染，单独跑全绿、整体跑失败
"""

import types
import unittest

import test_config_isolation as iso

ac = iso.fresh()
import api.tasks as T  # noqa: E402  （须在 fresh() 之后导入）

SEC = "live"  # 三个开关注册在 live 分区
KEYS = ("enable_danmaku", "enable_console", "enable_send")


def make_settings(**kw):
    s = types.SimpleNamespace()
    for k in KEYS:
        setattr(s, k, kw.get(k, True))
    return s


class TestSectionExists(unittest.TestCase):
    """防呆：分区名与字段名必须真实存在于 schema。"""

    def test_section_exists(self):
        self.assertIn(SEC, ac.SECTIONS, f"分区 {SEC} 不存在")

    def test_fields_exist(self):
        for k in KEYS:
            self.assertIn(k, ac.SECTIONS[SEC]["fields"], f"{k} 未注册")

    def test_no_task_section(self):
        """task 分区不存在（曾误写，留断言防回归）。"""
        self.assertNotIn("task", ac.SECTIONS)


class TestSettingsWins(unittest.TestCase):
    """核心语义：settings 优先，配置中心不反向覆盖。"""

    def setUp(self):
        ac.reset_section(SEC)

    def tearDown(self):
        ac.reset_section(SEC)

    def test_settings_true(self):
        s = make_settings()
        for k in KEYS:
            self.assertTrue(T._flag(k, s))

    def test_settings_false(self):
        """settings=False 必须读出 False —— 这正是旧实现会失败的用例。"""
        s = make_settings(enable_danmaku=False, enable_console=False,
                          enable_send=False)
        for k in KEYS:
            self.assertFalse(T._flag(k, s), f"{k} 应回落 settings 的 False")

    def test_config_center_does_not_override(self):
        """配置中心写 True，settings 写 False → 仍应是 False。"""
        s = make_settings(enable_send=False)
        ac.save_section(SEC, {"enable_send": True})
        self.assertFalse(T._flag("enable_send", s))

    def test_mixed(self):
        s = make_settings(enable_danmaku=False, enable_send=True)
        self.assertFalse(T._flag("enable_danmaku", s))
        self.assertTrue(T._flag("enable_send", s))


class TestMirrorSync(unittest.TestCase):
    """写侧双写：settings 的值应镜像进配置中心。"""

    def setUp(self):
        ac.reset_section(SEC)

    def tearDown(self):
        ac.reset_section(SEC)

    def test_save_mirrors_to_config_center(self):
        """模拟 save_config 的镜像写入。"""
        vals = {"enable_danmaku": False, "enable_console": True,
                "enable_send": False}
        ac.save_section(SEC, vals)
        for k, v in vals.items():
            self.assertEqual(ac.get(SEC, k), v, f"{k} 未镜像")


class TestSchemaMeta(unittest.TestCase):
    """三个开关必须在 schema 里正确注册（否则设置页不显示）。"""

    def test_type_bool(self):
        for k in KEYS:
            self.assertEqual(ac.SECTIONS[SEC]["fields"][k]["type"], "bool")

    def test_defaults_true(self):
        for k in KEYS:
            self.assertIs(ac.SECTIONS[SEC]["fields"][k]["default"], True)

    def test_hot_apply(self):
        for k in KEYS:
            self.assertEqual(ac.SECTIONS[SEC]["fields"][k].get("apply"), "hot")


if __name__ == "__main__":
    unittest.main(verbosity=0)
