# -*- coding: utf-8 -*-
"""ADR-010 机械门禁：debug MCP 工具族 + scope 隔离。

覆盖判据（ADR-010 §6）：
- V1 scope=debug 恰 7 个、零泄漏主动查询工具
- V2 未知 scope 拒绝（绝不静默回落）
- V3 依赖序上游优先定位首个失败环节
- V4 数据不足不冒充 healthy
- V7 默认面（full）与改造前一致（不含 debug_*）

原则：**不触网、不触浏览器、不写库**。探针与账号解析一律替换为假实现。
"""
from __future__ import annotations

import unittest

from mcp import registry as R
from mcp import tools_debug as T
from mcp.tools import register_all

# 既有平台读取类工具（本分支「全解除」后的真实工具面，默认面应含之）
_PLATFORM_TOOLS = ("search_user", "user_info_batch", "recommend_feed",
                   "user_info", "search_work", "notice_list")


class _ScopeCase(unittest.TestCase):
    """每个用例结束都把作用域复位，避免相互污染。"""

    def setUp(self):
        register_all()
        R.set_active_scope("full")

    def tearDown(self):
        R.set_active_scope("full")


class TestScopeIsolation(_ScopeCase):

    def test_v7_default_full_unchanged(self):
        """默认面 = 既有全量：含平台工具，且**不含**任何 debug_*。"""
        names = [t.name for t in R.all_tools()]
        self.assertFalse([n for n in names if n.startswith("debug_")],
                         "默认 full 面不该出现 debug_* 工具")
        for t in _PLATFORM_TOOLS:
            self.assertIn(t, names, f"既有工具 {t} 不应从默认面消失")
        self.assertEqual(22, len(names), "默认面应仍是既有 22 个（零回归）")

    def test_v1_debug_scope_isolates(self):
        """debug 面恰 7 个，且物理拿不到主动查询平台工具。"""
        R.set_active_scope("debug")
        names = sorted(t.name for t in R.all_tools())
        self.assertEqual(7, len(names), f"debug 面应为 7 个，实得 {names}")
        self.assertTrue(all(n.startswith("debug_") for n in names))
        for t in _PLATFORM_TOOLS:
            self.assertNotIn(t, names, f"🔴 隔离失效：debug 面泄漏 {t}")

    def test_multi_scope_union(self):
        """full,debug = 并集（供本机调试一次看全，不影响默认）。"""
        base = {t.name for t in R.all_tools()}
        R.set_active_scope("debug")
        dbg = {t.name for t in R.all_tools()}
        R.set_active_scope("full,debug")
        both = {t.name for t in R.all_tools()}
        self.assertEqual(base | dbg, both)

    def test_v2_unknown_scope_rejected(self):
        """未知 scope 必须抛错，且**不得**回落全量（S5）。"""
        R.set_active_scope("debug")
        before = {t.name for t in R.all_tools()}
        with self.assertRaises(ValueError):
            R.set_active_scope("bogus")
        after = {t.name for t in R.all_tools()}
        self.assertEqual(before, after, "拒绝后作用域不应被改变")

    def test_v2b_empty_scope_rejected(self):
        with self.assertRaises(ValueError):
            R.set_active_scope("full,,nope")

    def test_call_rejects_out_of_scope_tool(self):
        """跨作用域**调用**也必须被拦（不只看清单）。"""
        R.set_active_scope("debug")
        with self.assertRaises(R.McpError) as ctx:
            R.call("overview", {})          # 既有只读工具，不在 debug 面
        self.assertEqual("MCP-004", ctx.exception.code)


class TestDependencyOrder(_ScopeCase):
    """V3：上游优先 —— 这是 ADR-010 的核心价值，必须有负控。"""

    def _patch_probe(self, states: dict):
        from services import probe as P
        orig = P.run_probe

        def fake(cap, acct):
            return {"capability": cap, "account": acct,
                    "state": states.get(cap, "healthy"),
                    "evidence": [f"fake:{cap}"], "confidence": "A",
                    "reasons": [], "coverage": None}
        P.run_probe = fake
        self.addCleanup(lambda: setattr(P, "run_probe", orig))

    def test_first_bad_is_upstream_credential_not_capture(self):
        """凭证与捕获同时失败 → 必须报**凭证**（上游），不得报捕获。"""
        self._patch_probe({"credential_identity": "failed",
                           "conversation_capture": "failed"})
        r = T.debug_why("acct")
        self.assertEqual("failed", r["state"])
        self.assertIsNotNone(r["first_bad"])
        self.assertEqual("credential_identity", r["first_bad"]["capability"])
        self.assertIn("kernel_availability", r["first_bad"]["upstream_ok"])
        self.assertNotIn("conversation_capture", r["first_bad"]["upstream_ok"])

    def test_capture_reported_when_credential_healthy(self):
        self._patch_probe({"conversation_capture": "failed"})
        r = T.debug_why("acct")
        self.assertEqual("conversation_capture", r["first_bad"]["capability"])

    def test_kernel_wins_over_everything(self):
        """前置项（内核）最优先 —— 它垮则全链必死。"""
        self._patch_probe({"kernel_availability": "failed",
                           "credential_identity": "failed",
                           "conversation_capture": "failed"})
        r = T.debug_why("acct")
        self.assertEqual("kernel_availability", r["first_bad"]["capability"])
        self.assertEqual([], r["first_bad"]["upstream_ok"])

    def test_all_healthy(self):
        self._patch_probe({})
        r = T.debug_why("acct")
        self.assertEqual("healthy", r["state"])
        self.assertIsNone(r["first_bad"])
        self.assertTrue(r["evidence"])

    def test_symptom_does_not_change_ordering(self):
        """症状只是线索，不得影响排序（防「按症状猜环节」复发）。"""
        self._patch_probe({"credential_identity": "failed"})
        a = T.debug_why("acct", symptom="发送失败")
        b = T.debug_why("acct", symptom="")
        self.assertEqual(a["first_bad"]["capability"], b["first_bad"]["capability"])


class TestInvariants(_ScopeCase):

    def test_v4_no_evidence_never_healthy(self):
        """I1：无证据不得报 healthy。"""
        out = T._base("healthy", evidence=[], confidence="A", reasons=[])
        self.assertEqual("unknown", out["state"])
        self.assertTrue(any("I1" in r for r in out["reasons"]))

    def test_unknown_state_not_masked(self):
        """I2：探针报 unknown 时，debug_why 不得降级成 healthy。"""
        self._patch_probe_unknown("message_integrity")
        r = T.debug_why("acct")
        self.assertNotEqual("healthy", r["state"])
        self.assertEqual("message_integrity", r["first_bad"]["capability"])
        self.assertEqual("unknown", r["first_bad"]["state"])

    def _patch_probe_unknown(self, cap_name: str):
        from services import probe as P
        orig = P.run_probe

        def fake(cap, acct):
            st = "unknown" if cap == cap_name else "healthy"
            return {"capability": cap, "account": acct, "state": st,
                    "evidence": [], "confidence": "C", "reasons": ["数据不足"]}
        P.run_probe = fake
        self.addCleanup(lambda: setattr(P, "run_probe", orig))

    def test_data_health_unknown_when_account_absent(self):
        """V4：无该账号数据 → unknown（不冒充 healthy）。"""
        r = T.debug_data_health("__不存在的账号__")
        self.assertEqual("unknown", r["state"])

    def test_log_digest_missing_dir_is_unknown(self):
        """日志目录不可读 → unknown，且**不抛异常**。"""
        from services import probe as P
        orig = P._logs_dir
        P._logs_dir = lambda: __import__("pathlib").Path("__no_such_dir__")
        self.addCleanup(lambda: setattr(P, "_logs_dir", orig))
        r = T.debug_log_digest(since_min=1)
        self.assertEqual("unknown", r["state"])

    def test_explain_empty_code(self):
        r = T.debug_explain("")
        self.assertEqual("unknown", r["state"])

    def test_log_digest_clamps_params(self):
        """参数越界必须被夹紧，不得原样透传（README 风格护栏）。"""
        r = T.debug_log_digest(since_min=10 ** 9, limit=10 ** 9)
        self.assertLessEqual(r["since_min"], 10080)


class TestVersionSources(_ScopeCase):

    def test_six_sources_aligned(self):
        """6 处版本源应可读且齐平（源码树可用时）。"""
        vs = T._version_sources()
        self.assertEqual(6, len(vs), "版本源应恰为 check_version_sync 的 6 处")
        avail = [v for v in vs if v.get("present")]
        if not avail:
            self.skipTest("非源码树环境（打包态）⇒ 版本源不可读属预期")
        versions = {v["version"] for v in avail}
        self.assertEqual(1, len(versions),
                         f"版本源不齐平：{[(v['source'], v['version']) for v in vs]}")

    def test_layers_derived_not_hardcoded(self):
        """D6：环节表必须从 probe.py 的 REGISTRY 派生，不得硬编码另一份。"""
        from services import probe as P
        caps = [c for _, c in T._layers()]
        self.assertEqual(set(P.REGISTRY.keys()), set(caps),
                         "层级表与 probe.REGISTRY 必须一一对应（防条文漂移）")
        # 上游优先：支撑链必须排在业务链之前
        self.assertLess(caps.index("credential_identity"),
                        caps.index("conversation_capture"),
                        "凭证（支撑链）必须排在捕获之前 —— 否则误判复发")


if __name__ == "__main__":
    unittest.main(verbosity=2)
