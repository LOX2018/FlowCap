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
        # 2026-10-03（用户定调「取消默认休眠的 env 机制，改用配置中心控制」）：
        # 新增 batch_* 三项，把「批量采集总开关 / 并发上限 / 速率上限」从
        # 环境变量搬进配置中心。**契约随之扩展** —— 本门禁的用意是
        # 「字段集漂移必变红」，不是「字段集永不可变」；故此处显式登记新增项，
        # 仍保持「多一个少一个都红」的自证强度。
        fields = set((ac.SECTIONS[self.SEC].get("fields") or {}).keys())
        self.assertEqual(fields, {
            "connection_mode", "anonymous_max_rooms", "rotation_strategy",
            "desensitized_strategy", "sink_global_scope",
            "sink_cooldown_days", "sink_permanent",
            # ↓ 2026-10-03 新增（批量采集配置面）
            "batch_enabled", "batch_max_concurrent", "batch_rate_limit_per_min",
        })

    def test_batch_defaults_are_conservative(self):
        """批量三项默认值必须是**保守值**（关 + 小并发 + 低速率）。

        这三项直接决定风控敞口：默认放开等于批量自动私信默认启用 ——
        违反项目红线（用户 2026-09-27 定调「默认休眠，你要用再开」）。

        ⚠️ 判据取 **schema 里的 default**，**不取 `ac.get()`**：
        后者读的是 kv，别的测试（或用户）把值改成 True 后本用例就红 ——
        实测「单跑绿、全量红」的顺序相关假失败（本项目测试隔离老问题）。
        「用户在 UI 存了什么」不是契约，**「出厂默认是什么」才是**。
        """
        fields = ac.SECTIONS[self.SEC]["fields"]
        self.assertIs(fields["batch_enabled"]["default"], False,
                      "批量采集出厂默认必须关闭（fail-closed）")
        self.assertEqual(fields["batch_max_concurrent"]["default"], 3)
        self.assertEqual(fields["batch_rate_limit_per_min"]["default"], 60)
        # 边界约束也锁死（防止有人把上限放到 10 / 600 之外）
        self.assertEqual(fields["batch_max_concurrent"]["max"], 10)
        self.assertEqual(fields["batch_rate_limit_per_min"]["max"], 600)

    def test_batch_enabled_is_not_tag_managed(self):
        """🔴 批量三项**不得**按标签 scope 取值（它们是功能门，不是策略）。

        理由：标签是「给账号/房间分发送参数」的机制。若把功能门纳入标签域，
        会出现「标签 A 开、标签 B 关」的功能级分裂状态 —— 无法解释也无法排查。

        判据：取 `batch_*` 三个 `ac.get(...)` 调用的**实参**，断言没有 `scope`。
        """
        import re
        from services import live_batch
        with open(live_batch.__file__, encoding="utf-8") as f:
            src = f.read()
        for key in ("batch_enabled", "batch_max_concurrent", "batch_rate_limit_per_min"):
            # 形如 ac.get(_SECTION, "batch_enabled", False) 的调用片段
            m = re.search(rf'ac\.get\(\s*_SECTION\s*,\s*"{key}"[^)]*\)', src)
            self.assertIsNotNone(m, f"未找到 {key} 的 ac.get 调用（契约漂移）")
            self.assertNotIn("scope", m.group(0),
                             f"{key} 传了 scope ⇒ 功能门被纳入标签域，会出现按标签分裂")

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
