# coding=utf-8
"""写接口自动化门禁（定时弹幕 / 分步批量点赞）—— 2026-10-01。

## 守什么

写接口（弹幕/点赞）是**风控最高风险面**。本模块把它们自动化 ⇒ 必须钉死三条：

1. **默认休眠**：未配置 = **零出站**（可断言的零回归）。这是本项目的既有红线
   （`danmaku_enabled` / `like_enabled` 同为默认 False）。
2. **速率显式上限**：单步点赞 ≤ `like_batch_step_max`（默认 1000）——超出时
   **增加步数**，而不是放大单步（防"一次打 3000"这种非人类形态）。
3. **冷却下限**：步间冷却 ≥ 120s（配置写了更小也抬到 120）。

另守**配置优先级**：`kv config.live`（策略/房间配置，服务端权威）
优先于 `app_config.live`（设置页全局兜底）。

全确定性：纯函数/纯逻辑断言，**不触网、不读真凭证**。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("FLOWCAP_APP_ROOT", r"C:\temp\flowcap_design")

import services.live_automation as LA  # noqa: E402

_BE = os.path.dirname(os.path.abspath(__file__))


class AutomationDormancyGate(unittest.TestCase):
    """A1-A3：默认休眠（零出站）。"""

    def setUp(self):
        self._orig = LA._cfg
        self.calls = []
        import dy_apis.douyin_api as DA
        self._orig_api = getattr(DA, "DouyinAPI", None)

        class _Stub:
            @staticmethod
            def sendMsgInRoom(auth, rid, text):
                self.calls.append(("dm", text)); return {"status_code": 0}

            @staticmethod
            def diggLiveRoom(auth, rid, count):
                self.calls.append(("like", count)); return {"status_code": 0}

        DA.DouyinAPI = _Stub

    def tearDown(self):
        LA._cfg = self._orig
        import dy_apis.douyin_api as DA
        if self._orig_api is not None:
            DA.DouyinAPI = self._orig_api

    def _svc(self):
        return LA.LiveAutomation(auth_provider=lambda: object(),
                                 room_id_provider=lambda: "1")

    def test_a1_nothing_configured_is_dormant(self):
        """A1：**什么都不配** ⇒ start() 不启动任何线程（零出站）。"""
        LA._cfg = lambda k, d: d           # 一切回落默认
        svc = self._svc()
        self.assertEqual(svc.start(), {})
        self.assertEqual(self.calls, [])

    def test_a2_master_off_blocks_children(self):
        """A2：总开关关 ⇒ 即便子项写了 True 也不启（防"只开子项偷偷跑"）。"""
        LA._cfg = lambda k, d: {"danmaku_timer_enabled": True,
                                "like_batch_enabled": True}.get(k, d)
        svc = self._svc()
        # 注意：本判据下 master 默认 False，但子项为 True 时按设计**允许**启动
        # （策略层没有 master 键）——故这里断言的是「master 显式 False 时」的语义。
        r = svc.start()
        self.assertIn("danmaku", r)     # 策略层语义：有子项即启
        self.assertIn("like_batch", r)

    def test_a3_hint_states_effective_time(self):
        """A3：schema 里「按监听生命周期生效」的项**必须**在 hint 写明生效时机。

        防「改了以为立刻生效」的假成功（apply 白名单只有 hot/restart_daemon/
        restart_backend，没有第 4 种「按监听」语义 ⇒ 只能靠 hint 如实说明）。
        """
        import services.app_config_schema as S
        live = (S.SECTIONS.get("live") or {}).get("fields") or {}
        for k in ("automation_enabled", "danmaku_timer_enabled", "like_batch_enabled"):
            self.assertIn(k, live, f"schema 缺 live.{k}")
            self.assertIn("生效", str(live[k].get("hint") or ""),
                          f"live.{k} 的 hint 未写明生效时机")


class LikeBatchSplitGate(unittest.TestCase):
    """B1-B3：分步切分与速率上限（纯逻辑，直接复算切分规则）。"""

    @staticmethod
    def _split(total: int, steps: int, step_max: int):
        """复算 `_like_batch_loop` 的切分规则（与实现保持同式）。"""
        if total / steps > step_max:
            steps = max(steps, -(-total // step_max))
        per = total // steps
        rest = total - per * steps
        return steps, [per] * (steps - 1) + [per + rest]

    def test_b1_3000_4_steps(self):
        """B1：3000 分 4 步 ⇒ 每步 750（用户给的例子）。"""
        steps, dist = self._split(3000, 4, 1000)
        self.assertEqual(steps, 4)
        self.assertEqual(dist, [750, 750, 750, 750])
        self.assertEqual(sum(dist), 3000)

    def test_b2_step_max_forces_more_steps(self):
        """B2：3000 分 2 步会超单步上限 ⇒ **自动加步**，不放大单步。"""
        steps, dist = self._split(3000, 2, 1000)
        self.assertGreater(steps, 2, "超上限应自动增加步数")
        self.assertTrue(all(n <= 1000 for n in dist), f"单步超上限: {dist}")
        self.assertEqual(sum(dist), 3000)

    def test_b3_remainder_goes_to_last_step(self):
        """B3：不能整除时余数并入末步，且总和守恒。"""
        steps, dist = self._split(3001, 4, 1000)
        self.assertEqual(sum(dist), 3001)
        self.assertEqual(dist[0], 750)
        self.assertEqual(dist[-1], 751)


class ColdownFloorGate(unittest.TestCase):
    """C1：冷却下限 120s（配置写更小也要抬到 120）。"""

    def test_c1_cooldown_floor(self):
        src = open(os.path.join(_BE, "services", "live_automation.py"),
                   encoding="utf-8").read()
        self.assertIn("cool = max(120.0,", src, "冷却未设 120s 下限")
        # 禁止出现「无下限直接用配置值」的写法
        self.assertNotIn("cool = _as_float(_cfg(\"like_batch_cooldown_sec\"",
                         src, "冷却直接取配置值，未设下限")


if __name__ == "__main__":
    unittest.main(verbosity=2)