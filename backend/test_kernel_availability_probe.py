# -*- coding: utf-8 -*-
r"""内核可用性探针守卫（防「内核缺失无先兆、直到 BCC 起不来」回归）。

## 守的是什么（真实事故，2026-09-24）

Camoufox 内核缓存被上游 `pkgman` 自毁清空后，**除「BCC 起不来」外没有任何先兆**，
且以 3s 一轮重建风暴表现。`kernel_availability` 探针把缺失提前到巡检里报出来。

## 判据（正控 + 两条负控 + 零风控边界）

| # | 场景 | 期望 | 性质 |
|---|---|---|---|
| 1 | 内核可用 | `healthy` + 版本号在证据里 | 正控 |
| 2 | 内核缺失（`installed_verstr` 抛 `CamoufoxNotInstalled`） | `failed`，且理由含修复指引 | **负控**（这条就是事故形态） |
| 3 | 未启用 Camoufox（Chromium 分支） | `unknown`（**不得**报 `failed`） | **负控**（防误报） |
| 4 | 零风控边界 | 探针函数体内无任何网络调用 | 结构判据 |

## 为什么用 monkeypatch 而不用真环境

真环境是否装了内核随机器而异 —— 测「防御逻辑」必须能自证，不能依赖外部真值。
（同一教训见 `session-quality-guard` Pitfalls：别把某模型的当前取值写死进测试。）
"""

from __future__ import annotations

import inspect
import os
import sys
import unittest

_BACKEND = os.path.dirname(os.path.abspath(__file__))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from services import probe as P  # noqa: E402


class TestKernelAvailabilityProbe(unittest.TestCase):

    def setUp(self):
        import camoufox.pkgman as pk
        import vbrowser_camoufox as vbc
        self._pk, self._vbc = pk, vbc
        self._orig_verstr = pk.installed_verstr
        self._orig_enabled = vbc.camoufox_enabled

    def tearDown(self):
        self._pk.installed_verstr = self._orig_verstr
        self._vbc.camoufox_enabled = self._orig_enabled

    # ── 正控 ──
    def test_healthy_when_kernel_available(self):
        self._vbc.camoufox_enabled = lambda cfg=None: True
        self._pk.installed_verstr = lambda: "152.0.4-beta.30"
        res = P.run_probe("kernel_availability", "T")
        self.assertEqual(res["state"], "healthy", res.get("reasons"))
        self.assertEqual(res["confidence"], "A")
        self.assertTrue(any("152.0.4-beta.30" in e for e in res["evidence"]),
                        f"版本号未出现在证据里: {res['evidence']}")

    # ── 负控 1：事故形态 ──
    def test_failed_when_kernel_missing(self):
        from camoufox.exceptions import CamoufoxNotInstalled
        self._vbc.camoufox_enabled = lambda cfg=None: True

        def _boom():
            raise CamoufoxNotInstalled(
                "official/stable is not installed. Please run `camoufox fetch` to install.")
        self._pk.installed_verstr = _boom
        res = P.run_probe("kernel_availability", "T")
        self.assertEqual(res["state"], "failed",
                         f"内核缺失必须报 failed（否则探针无意义）: {res}")
        self.assertEqual(res["coverage"], 0.0)
        joined = " ".join(res.get("reasons", []) + res.get("evidence", []))
        self.assertIn("BCC-070", joined, "理由应点名 BCC-070（与运行期错误码对齐）")
        self.assertIn("camoufox fetch", joined, "理由应给可操作修复指引")

    # ── 负控 2：防误报 ──
    def test_unknown_when_camoufox_not_enabled(self):
        self._vbc.camoufox_enabled = lambda cfg=None: False
        res = P.run_probe("kernel_availability", "T")
        self.assertEqual(res["state"], "unknown",
                         f"未启用 Camoufox 不得报 failed（那是 Chromium 分支，非缺陷）: {res}")

    # ── 结构判据：零风控边界 ──
    def test_probe_has_no_network_calls(self):
        src = inspect.getsource(P.probe_kernel_availability)
        forbidden = ("requests.", "urllib", "http://", "https://", "socket.",
                     "aiohttp", "httpx")
        hits = [f for f in forbidden if f in src]
        self.assertEqual(hits, [],
                         f"探针违反零风控边界（模块级硬约束：只读本地事实）: {hits}")

    def test_registered_as_prep_capability(self):
        """内核项必须在注册表 + 前置项集合里，但**不得**混入 CAPABILITY_ORDER。

        判据来源：`test_capability_probe.test_registry_and_order` 把
        CAPABILITY_ORDER 钉在「02_效果定义与探针.md §2 的六大业务域」上 ——
        内核不是业务域，属于**前置项**（缺失会拖垮全部业务域）。
        """
        self.assertIn("kernel_availability", P.REGISTRY)
        self.assertIn("kernel_availability", P.PREP_CAPABILITIES,
                      "内核项应作为前置项并入巡检默认集合")
        self.assertNotIn("kernel_availability", P.CAPABILITY_ORDER,
                         "CAPABILITY_ORDER 是冻结的业务域契约，不得为其新增成员")
        self.assertEqual(P.CAPABILITY_ORDER,
                         ["conversation_capture", "send_delivery", "credential_identity",
                          "live_danmaku", "ai_lead_capture", "message_integrity"],
                         "六大业务域顺序被改动（协作契约破坏）")

    def test_patrol_default_includes_kernel(self):
        """巡检默认集合必须含内核项 —— 否则「悄悄坏了」仍无先兆。"""
        default_caps = list(P.PREP_CAPABILITIES) + list(P.CAPABILITY_ORDER)
        self.assertIn("kernel_availability", default_caps)


if __name__ == "__main__":
    unittest.main(verbosity=2)
