"""app_config 统一配置中心单元测试。

跑法（用探针 Python，backend 为 cwd）：
  cd DYAutoDM_v2/backend
  "C:/Users/LOX/AppData/Local/Programs/Python/Python314/python.exe" test_app_config.py

注：本机未装 pytest，故用 stdlib unittest（同样可 `pytest test_app_config.py` 跑）。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 用临时 DB，避免污染真实数据
_TMP = os.path.join(os.environ.get("TEMP", "/tmp"), "dyautodm_appcfg_test")
os.makedirs(_TMP, exist_ok=True)
os.environ["DY_APP_ROOT"] = _TMP

import services.app_config as ac  # noqa: E402


class TestAppConfig(unittest.TestCase):
    def setUp(self):
        for s in ac.SECTIONS:
            ac.reset_section(s)

    # ---- 默认值 ----
    def test_default_when_empty(self):
        self.assertEqual(ac.get("send", "min_interval"), 8.0)

    # ---- 存取 ----
    def test_save_then_get(self):
        ac.save_section("send", {"min_interval": 12.0})
        self.assertEqual(ac.get("send", "min_interval"), 12.0)

    # ---- 优先级 ----
    def test_env_overrides_default(self):
        os.environ["DY_SEND_MIN_INTERVAL"] = "20"
        try:
            self.assertEqual(ac.get("send", "min_interval"), 20.0)
        finally:
            os.environ.pop("DY_SEND_MIN_INTERVAL", None)

    def test_config_overrides_env(self):
        os.environ["DY_SEND_MIN_INTERVAL"] = "20"
        try:
            ac.save_section("send", {"min_interval": 15.0})
            self.assertEqual(ac.get("send", "min_interval"), 15.0)
        finally:
            os.environ.pop("DY_SEND_MIN_INTERVAL", None)

    # ---- schema ----
    def test_schema_exposes_apply_mode(self):
        self.assertEqual(
            ac.schema()["send"]["fields"]["min_interval"]["apply"], "restart_daemon")

    def test_schema_exposes_risk_flag(self):
        self.assertTrue(ac.schema()["send"]["fields"]["stranger_per_day"]["risk"])
        self.assertFalse(ac.schema()["live"]["fields"]["max_target"].get("risk", False))

    # ---- 边界保护 ----
    def test_below_min_rejected(self):
        # min_interval 下限 8s（风控敏感，不允许调更低）
        ac.save_section("send", {"min_interval": 1.0})
        self.assertEqual(ac.get("send", "min_interval"), 8.0)

    def test_out_of_range_env_ignored(self):
        os.environ["DY_SEND_MIN_INTERVAL"] = "not-a-number"
        try:
            self.assertEqual(ac.get("send", "min_interval"), 8.0)
        finally:
            os.environ.pop("DY_SEND_MIN_INTERVAL", None)

    def test_select_invalid_option_rejected(self):
        ac.save_section("general", {"bcc_headless_mode": "evil"})
        self.assertEqual(ac.get("general", "bcc_headless_mode"), "native")

    # ---- 未知字段 ----
    def test_unknown_field_ignored(self):
        ac.save_section("send", {"__hack": 1})
        self.assertIsNone(ac.get("send", "__hack"))

    def test_unknown_section_empty(self):
        self.assertEqual(ac.get_section("nope"), {})

    # ---- apply 分类 ----
    def test_apply_modes(self):
        self.assertEqual(
            ac.apply_modes_of("send", ["min_interval", "stranger_per_day"]), ["daemon"])
        self.assertEqual(
            sorted(ac.apply_modes_of("general", ["bcc_on_start", "auto_capture_on_start"])),
            ["backend"])

    # ---- reset ----
    def test_reset_restores_default(self):
        ac.save_section("live", {"max_target": 99})
        self.assertEqual(ac.get("live", "max_target"), 99)
        ac.reset_section("live")
        self.assertEqual(ac.get("live", "max_target"), 3)

    # ---- 全量 schema 自检 ----
    def test_all_sections_have_label_and_fields(self):
        for name, sec in ac.SECTIONS.items():
            self.assertTrue(sec.get("label"), f"{name} 缺 label")
            fields = sec.get("fields") or {}
            self.assertTrue(fields, f"{name} 无字段")
            for k, m in fields.items():
                self.assertIn(m.get("type"), ("int", "float", "bool", "str", "select"),
                              f"{name}.{k} type 非法")
                self.assertIn(m.get("apply", "hot"),
                              ("hot", "restart_daemon", "restart_backend"),
                              f"{name}.{k} apply 非法")
                self.assertIn("default", m, f"{name}.{k} 缺 default")

    def test_defaults_pass_their_own_range(self):
        """schema 里写的默认值必须能通过自身范围校验，否则配置中心永远读不到它。"""
        for name, sec in ac.SECTIONS.items():
            for k, m in (sec.get("fields") or {}).items():
                v = ac._coerce(m.get("default"), m.get("type", "str"), m)
                self.assertIsNotNone(v, f"{name}.{k} 默认值 {m.get('default')!r} 未通过校验")


class TestLiveOrchestrationSchema(unittest.TestCase):
    """ADR-002 §5.4 策略中心契约守卫（v0.44.41）。

    D-07 自证：删除 live_orchestration 分区、改任一默认值或选项集 → 本类必变红。
    """

    SEC = "live_orchestration"

    def test_section_registered(self):
        self.assertIn(self.SEC, ac.SECTIONS)
        self.assertTrue(ac.SECTIONS[self.SEC].get("label"))

    def test_field_set_is_exactly_the_contract(self):
        fields = set((ac.SECTIONS[self.SEC].get("fields") or {}).keys())
        self.assertEqual(fields, {
            "connection_mode", "anonymous_max_rooms", "rotation_strategy",
            "desensitized_strategy", "sink_global_scope",
            "sink_cooldown_days", "sink_permanent",
        })

    def test_defaults_match_adr(self):
        self.assertEqual(ac.get(self.SEC, "connection_mode"), "credential")
        self.assertEqual(ac.get(self.SEC, "anonymous_max_rooms"), 4)
        self.assertEqual(ac.get(self.SEC, "rotation_strategy"), "per_target")
        self.assertEqual(ac.get(self.SEC, "desensitized_strategy"), "skip")
        self.assertIs(ac.get(self.SEC, "sink_global_scope"), True)
        self.assertEqual(ac.get(self.SEC, "sink_cooldown_days"), 90.0)
        self.assertIs(ac.get(self.SEC, "sink_permanent"), False)

    def test_select_options_complete(self):
        f = ac.SECTIONS[self.SEC]["fields"]

        def vals(k):
            return [o["value"] if isinstance(o, dict) else o
                    for o in (f[k].get("options") or [])]

        self.assertEqual(vals("connection_mode"), ["credential", "anonymous"])
        self.assertEqual(vals("rotation_strategy"),
                         ["per_target", "per_time_window", "per_room"])
        self.assertEqual(vals("desensitized_strategy"),
                         ["skip", "observe_only", "prompt", "reduce_anonymous"])

    def test_roundtrip_save_and_reset(self):
        ac.save_section(self.SEC, {"anonymous_max_rooms": 3,
                                   "sink_cooldown_days": 30.0,
                                   "sink_permanent": True})
        self.assertEqual(ac.get(self.SEC, "anonymous_max_rooms"), 3)
        self.assertEqual(ac.get(self.SEC, "sink_cooldown_days"), 30.0)
        self.assertIs(ac.get(self.SEC, "sink_permanent"), True)
        ac.reset_section(self.SEC)
        self.assertEqual(ac.get(self.SEC, "anonymous_max_rooms"), 4)

    def test_out_of_range_rejected(self):
        ac.save_section(self.SEC, {"anonymous_max_rooms": 99})
        self.assertEqual(ac.get(self.SEC, "anonymous_max_rooms"), 4)
        ac.save_section(self.SEC, {"sink_cooldown_days": 99999.0})
        self.assertEqual(ac.get(self.SEC, "sink_cooldown_days"), 90.0)

    def test_invalid_option_rejected(self):
        ac.save_section(self.SEC, {"desensitized_strategy": "evil"})
        self.assertEqual(ac.get(self.SEC, "desensitized_strategy"), "skip")

    def test_all_fields_are_hot_apply(self):
        for k, m in ac.SECTIONS[self.SEC]["fields"].items():
            self.assertEqual(m.get("apply", "hot"), "hot", f"{k} 应为 hot")

if __name__ == "__main__":
    unittest.main(verbosity=2)
