# -*- coding: utf-8 -*-
"""T5-b 门禁回归：两世界可见性探针（`services/env_baseline.py`）。

## 验什么
把「注入成功却读到空」的复发钉成机械判据：**必须同时**满足
① 页面**主世界**看得到 hook 键（注入确实执行）；② **默认（读取）世界**也能
读到同一键（跨世界可见）。二者不一致 = FAIL；缺证据 = unknown（绝不静默通过）。

## 怎么离线验证（诚实标注：真机未验证）
真实浏览器在本环境不可用（任务禁真机），故用 **TwoWorldPageStub** 桩对象
模拟「主世界 vs 默认世界」两个 JS 世界：桩以 evaluate 是否收到
`isolated_context=False` 区分世界并各自回报 hook 键可见性。
本测试**只证明判据本身能对**（正控 PASS / 负控 FAIL / 缺证据 unknown），
**不证明**真实 patchright 页面下的行为（那需真机，未做）。

隔离：与 `test_replay_gates.py` 同法——不激活 Sandbox、不碰真实数据库；
基线落盘路径用内存替身（mock kv_get/kv_set）。绝不改仓库任何数据。
"""
import os
import re
import sys
import unittest
from unittest import mock

_BACKEND = os.path.dirname(os.path.abspath(__file__))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from services import env_baseline  # noqa: E402


# ═══════════════════════════════════════════════════════════════════════════
# 离线替身：模拟「页面主世界 / 默认（读取）世界」两个 JS 世界
# ═══════════════════════════════════════════════════════════════════════════
class TwoWorldPageStub:
    """桩对象：以 `isolated_context` 区分两世界，各自回报 hook 键可见性。

    - `isolated_context=False`（或后端不支持该 kwarg 时的回退）= **主世界**
    - 默认（不传 / `isolated_context=True`）= **默认（读取）世界**
    """

    def __init__(self, *, main_visible=None, default_visible=None,
                 supports_isolated_context=True, raise_on=None):
        self.main_visible = dict(main_visible or {})
        self.default_visible = dict(default_visible or {})
        self.supports_isolated_context = supports_isolated_context
        self.raise_on = raise_on          # 'main'/'default'：模拟读取抛异常
        self.calls = []                    # 记录读取足迹（校验世界切换）

    def evaluate(self, js, *args, **kwargs):
        has_iso = "isolated_context" in kwargs
        if has_iso and not self.supports_isolated_context:
            # 原生 playwright：无该 kwarg → TypeError（触发探针回退）
            raise TypeError("evaluate() got an unexpected keyword argument "
                            "'isolated_context'")
        iso_false = has_iso and kwargs.get("isolated_context") is False
        world = "main" if iso_false else "default"
        self.calls.append(world)
        if self.raise_on == world:
            raise RuntimeError(f"boom in {world} world")
        seen = self.main_visible if world == "main" else self.default_visible
        return {"keys": {k: bool(seen.get(k)) for k in env_baseline.VISIBILITY_KEYS}}


_ALL = {k: True for k in env_baseline.VISIBILITY_KEYS}   # 两键都在
_NONE = {k: False for k in env_baseline.VISIBILITY_KEYS}


# ═══════════════════════════════════════════════════════════════════════════
# 1) 探针正控 / 负控（离线替身）
# ═══════════════════════════════════════════════════════════════════════════
class TestTwoWorldProbeOffline(unittest.TestCase):

    def test_positive_control_both_worlds_visible_pass(self):
        """正控：两世界都看得到 → PASS。"""
        page = TwoWorldPageStub(main_visible=_ALL, default_visible=_ALL)
        r = env_baseline.probe_two_world_visibility(page)
        self.assertEqual(r["status"], env_baseline.VIS_STATUS_PASS)
        self.assertTrue(r["ok"])
        self.assertEqual(r["leaks"], [])
        # 主世界读取确实走了 isolated_context=False
        self.assertEqual(page.calls, ["main", "default"])

    def test_negative_control_main_only_is_fail(self):
        """负控（核心）：主世界可见、默认世界读不到 = 「注入成功却读到空」→ FAIL。"""
        page = TwoWorldPageStub(main_visible=_ALL, default_visible=_NONE)
        r = env_baseline.probe_two_world_visibility(page)
        self.assertEqual(r["status"], env_baseline.VIS_STATUS_FAIL,
                         "跨世界不可见却判了通过（门禁假绿）")
        self.assertFalse(r["ok"])
        self.assertTrue(any(x["severity"] == "fatal" for x in r["leaks"]),
                        "缺 fatal 泄漏项 —— 无法驱动告警")
        self.assertEqual(set(r["main_visible"]), set(env_baseline.VISIBILITY_KEYS))
        self.assertEqual(r["default_visible"], [])

    def test_negative_control_nothing_visible_is_fail(self):
        """负控：两世界都看不到（注入未执行）→ FAIL（不是 unknown）。"""
        page = TwoWorldPageStub(main_visible=_NONE, default_visible=_NONE)
        r = env_baseline.probe_two_world_visibility(page)
        self.assertEqual(r["status"], env_baseline.VIS_STATUS_FAIL)

    def test_negative_control_default_only_is_fail(self):
        """负控：默认世界可见、主世界不可见（反向不一致）→ FAIL。"""
        page = TwoWorldPageStub(main_visible=_NONE, default_visible=_ALL)
        r = env_baseline.probe_two_world_visibility(page)
        self.assertEqual(r["status"], env_baseline.VIS_STATUS_FAIL)

    def test_single_world_backend_falls_back_and_passes(self):
        """原生 playwright（无 isolated_context）回退：单世界，键在即 PASS。"""
        page = TwoWorldPageStub(main_visible=_ALL, default_visible=_ALL,
                                supports_isolated_context=False)
        r = env_baseline.probe_two_world_visibility(page)
        self.assertEqual(r["status"], env_baseline.VIS_STATUS_PASS)
        self.assertIn("fallback", r.get("main_read_mode", ""))
        # 两次都落回默认世界（无主世界概念）
        self.assertEqual(page.calls, ["default", "default"])


# ═══════════════════════════════════════════════════════════════════════════
# 2) 三态：缺证据一律 unknown（绝不静默通过）
# ═══════════════════════════════════════════════════════════════════════════
class TestUnknownNeverPasses(unittest.TestCase):

    def test_no_page_is_unknown(self):
        r = env_baseline.probe_two_world_visibility(None)
        self.assertEqual(r["status"], env_baseline.VIS_STATUS_UNKNOWN)
        self.assertFalse(r["ok"], "无页面竟判通过 —— 假绿")
        self.assertIn(r["reason"], ("not_executed", "no_browser", "offline"))

    def test_object_without_evaluate_is_unknown(self):
        r = env_baseline.probe_two_world_visibility(object())
        self.assertEqual(r["status"], env_baseline.VIS_STATUS_UNKNOWN)
        self.assertFalse(r["ok"])

    def test_not_applicable_is_unknown(self):
        page = TwoWorldPageStub(main_visible=_ALL, default_visible=_ALL)
        r = env_baseline.probe_two_world_visibility(page, applicable=False)
        self.assertEqual(r["status"], env_baseline.VIS_STATUS_UNKNOWN)
        self.assertEqual(r["reason"], "not_applicable")
        self.assertFalse(r["ok"])

    def test_read_exception_is_unknown(self):
        page = TwoWorldPageStub(main_visible=_ALL, default_visible=_ALL,
                                raise_on="main")
        r = env_baseline.probe_two_world_visibility(page)
        self.assertEqual(r["status"], env_baseline.VIS_STATUS_UNKNOWN)
        self.assertFalse(r["ok"])

    def test_bad_probe_output_is_unknown(self):
        class BadPage:
            def evaluate(self, js, *a, **k):
                return "not-a-dict"
        r = env_baseline.probe_two_world_visibility(BadPage())
        self.assertEqual(r["status"], env_baseline.VIS_STATUS_UNKNOWN)
        self.assertEqual(r["reason"], "bad_probe_output")


# ═══════════════════════════════════════════════════════════════════════════
# 3) 三态契约：checked 标志 + 分类函数
# ═══════════════════════════════════════════════════════════════════════════
class TestThreeStateContract(unittest.TestCase):

    def test_classify_statuses(self):
        ok = env_baseline.classify_two_world_visibility(_ALL, _ALL)
        self.assertEqual(ok["status"], env_baseline.VIS_STATUS_PASS)
        bad = env_baseline.classify_two_world_visibility(_ALL, _NONE)
        self.assertEqual(bad["status"], env_baseline.VIS_STATUS_FAIL)
        unknown = env_baseline.classify_two_world_visibility(executed=False)
        self.assertEqual(unknown["status"], env_baseline.VIS_STATUS_UNKNOWN)

    def test_unknown_is_not_checked_not_ok(self):
        u = env_baseline.classify_two_world_visibility(executed=False)
        self.assertFalse(u["checked"], "unknown 不得标记为已检查")
        self.assertFalse(u["ok"], "unknown 不得为 ok")
        p = env_baseline.classify_two_world_visibility(_ALL, _ALL)
        self.assertTrue(p["checked"])
        f = env_baseline.classify_two_world_visibility(_ALL, _NONE)
        self.assertTrue(f["checked"], "fail 是已判定的结果（checked=True）")

    def test_probe_keys_match_module_constant(self):
        self.assertEqual(
            set(env_baseline.VISIBILITY_KEYS),
            {"__CAP_USERINFO__", "__CAP_WP_MESSAGE__"})
        for k in env_baseline.VISIBILITY_KEYS:
            self.assertIn(k, env_baseline.VISIBILITY_PROBE_JS)


# ═══════════════════════════════════════════════════════════════════════════
# 4) 基线落盘（内存替身，零 DB 副作用）：unknown 不落、pass/fail 可落
# ═══════════════════════════════════════════════════════════════════════════
class _MemKV(dict):
    def get(self, k, d=None):
        return dict.get(self, k, d)


class TestVisibilityBaselinePersistence(unittest.TestCase):

    def setUp(self):
        self.store = _MemKV()
        self._p1 = mock.patch.object(env_baseline, "kv_get",
                                     side_effect=lambda k, d=None: self.store.get(k, d))
        self._p2 = mock.patch.object(env_baseline, "kv_set",
                                     side_effect=lambda k, v: self.store.__setitem__(k, v))
        self._p1.start()
        self._p2.start()

    def tearDown(self):
        self._p1.stop()
        self._p2.stop()

    def test_pass_is_recorded_and_readable(self):
        page = TwoWorldPageStub(main_visible=_ALL, default_visible=_ALL)
        res = env_baseline.visibility_gate("acct1", page)
        self.assertEqual(res["status"], env_baseline.VIS_STATUS_PASS)
        rec = env_baseline.get_visibility_baseline("acct1")
        self.assertIsNotNone(rec)
        self.assertEqual(rec["status"], env_baseline.VIS_STATUS_PASS)
        self.assertEqual(env_baseline.latest_visibility("acct1")["status"],
                         env_baseline.VIS_STATUS_PASS)
        # 分键：不污染出口 IP 基线键
        self.assertIn(env_baseline._vis_key("acct1"), self.store)
        self.assertNotIn(env_baseline._key("acct1"), self.store)

    def test_fail_is_recorded(self):
        page = TwoWorldPageStub(main_visible=_ALL, default_visible=_NONE)
        res = env_baseline.visibility_gate("acct2", page)
        self.assertEqual(res["status"], env_baseline.VIS_STATUS_FAIL)
        self.assertEqual(env_baseline.latest_visibility("acct2")["status"],
                         env_baseline.VIS_STATUS_FAIL)

    def test_unknown_is_never_persisted(self):
        """无数据不得落盘（否则会被当通过）。"""
        res = env_baseline.visibility_gate("acct3", None)
        self.assertEqual(res["status"], env_baseline.VIS_STATUS_UNKNOWN)
        self.assertIsNone(env_baseline.get_visibility_baseline("acct3"),
                          "unknown 被落盘 —— 无数据会被误当结论")
        self.assertTrue(env_baseline._vis_key("acct3") not in self.store)

    def test_latest_visibility_no_baseline_is_unknown(self):
        r = env_baseline.latest_visibility("never_seen")
        self.assertEqual(r["status"], env_baseline.VIS_STATUS_UNKNOWN)
        self.assertFalse(r["ok"])


# ═══════════════════════════════════════════════════════════════════════════
# 5) 接线缺口（latent，诚实标注）：探针键须覆盖真实 hook 脚本写的键
# ═══════════════════════════════════════════════════════════════════════════
class TestProbeCoversHookKeys(unittest.TestCase):
    """防空转：探针若与真实 hook 写的键脱节，门禁恒绿而事故照复发。"""

    @classmethod
    def setUpClass(cls):
        cls.js_path = os.path.join(_BACKEND, "daemon", "browser_daemon_js.py")
        with open(cls.js_path, encoding="utf-8") as _f:
            cls.js_src = _f.read()

    def test_probe_keys_cover_all_hook_window_keys(self):
        hook_keys = set(re.findall(r"window\.(__CAP_[A-Z_]+__)", self.js_src))
        self.assertTrue(hook_keys, "未在 hook 脚本里找到 CAP_* window 键（判据失效）")
        uncovered = hook_keys - set(env_baseline.VISIBILITY_KEYS)
        self.assertEqual(uncovered, set(),
                         f"hook 写的键未被探针覆盖（门禁会漏）: {uncovered}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
