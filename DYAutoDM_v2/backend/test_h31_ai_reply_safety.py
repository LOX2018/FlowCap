# -*- coding: utf-8 -*-
"""ADR-013 门禁：AI 回复安全提取 + max_tokens / context_window 显式化。

缺陷编号：AI-061（截断回复被当正常外发）· AI-062（context_window 拍脑袋定值）

## 实测依据（2026-09-26，真实模型 + 真实网关）
`deepseek-v4.1-flash` 在复杂推理题下，max_tokens=1000/2000/4000
**全部 `finish_reason=length`（被截断）**，reasoning 吃掉 365~1009 tokens。
而修复前**全仓零处处理 finish_reason** ⇒ 半截话被直接发给客户。

## 判据
  G1  `_extract_reply` 存在且为**模块级**（两处协议共用，SSOT）
  G2  截断必判失败：`finish_reason == "length"` → 返回 None
  G3  Anthropic 路径同样处理 `stop_reason == "max_tokens"`
  G4  正常响应（finish_reason=stop）**不得**被误判（防过度拦截）
  G5  max_tokens 调用点走 `_eff_max_tokens`（非硬编码 1000）
  G6  推理模型上浮生效（_is_reasoner 命中时 > base）
  G7  context_window 未配置 → 走保守下限且**告警**（不静默猜 65536）
  G8  负控：无 finish_reason 字段的响应不得崩（健壮性）
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

_BACKEND = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _BACKEND)
# A-8 / M-17 隔离根单一化：模块级 DY_APP_ROOT 必须是**一次性临时目录**。
# 先 mkdir 再赋值（vbrowser.app_root() 忽略不存在的根 → 回落仓库 data/）。
_ROOT = tempfile.mkdtemp(prefix="h31_reply_safety_")
os.makedirs(_ROOT, exist_ok=True)
os.environ["DY_APP_ROOT"] = _ROOT

from services import ai_reply as ar  # noqa: E402


class TestAdr013ReplySafety(unittest.TestCase):

    def test_g1_extract_reply_is_module_level(self):
        """G1：模块级函数（两处协议共用，非类内重复实现）。"""
        self.assertTrue(callable(getattr(ar, "_extract_reply", None)))
        src = open(os.path.join(_BACKEND, "services", "ai_reply.py"),
                   encoding="utf-8").read()
        self.assertIn("\ndef _extract_reply(", src)

    def test_g2_truncated_must_fail(self):
        """G2：finish_reason=length 必须判失败（不得外发半截话）。"""
        r = {"choices": [{
            "finish_reason": "length",
            "message": {"content": "这是一段被截断的回复……", "reasoning_content": "x" * 500},
        }]}
        self.assertIsNone(ar._extract_reply(r))

    def test_g3_anthropic_max_tokens_handled(self):
        """G3：Anthropic 路径处理 stop_reason=max_tokens。"""
        src = open(os.path.join(_BACKEND, "services", "ai_reply.py"),
                   encoding="utf-8").read()
        self.assertIn('stop_reason', src)
        self.assertIn('"max_tokens"', src)

    def test_g4_normal_response_not_blocked(self):
        """G4：正常响应不得被误判（防过度拦截 —— 过度拦截 = 全走兜底）。"""
        r = {"choices": [{
            "finish_reason": "stop",
            "message": {"content": "您好，工伤赔偿流程如下：先申请认定。",
                        "reasoning_content": ""},
        }]}
        self.assertEqual(ar._extract_reply(r),
                         "您好，工伤赔偿流程如下：先申请认定。")

    def test_g5_uses_eff_max_tokens(self):
        """G5：调用点走 _eff_max_tokens，不得再硬编码 1000。"""
        src = open(os.path.join(_BACKEND, "services", "ai_reply.py"),
                   encoding="utf-8").read()
        self.assertNotIn('"max_tokens": int(cfg.get("max_tokens", 1000))', src)
        self.assertGreaterEqual(src.count("_eff_max_tokens(cfg)"), 2)

    def test_g6_reasoner_uplift(self):
        """G6：推理模型 max_tokens 上浮生效。"""
        base = {"max_tokens": 4000, "reasoner_max_tokens_factor": 1.5}
        self.assertTrue(ar._is_reasoner("deepseek-v4.1-flash"))
        self.assertTrue(ar._is_reasoner("glm-5.2"))
        self.assertFalse(ar._is_reasoner("agnes-2.5-flash"))
        up = ar._eff_max_tokens({**base, "model": "deepseek-v4.1-flash"})
        flat = ar._eff_max_tokens({**base, "model": "agnes-2.5-flash"})
        self.assertEqual(flat, 4000)
        self.assertGreater(up, flat)

    def test_g7_context_window_unset_is_conservative(self):
        """G7：未配置 → 保守下限，不得再静默用 65536。"""
        src = open(os.path.join(_BACKEND, "services", "ai_reply.py"),
                   encoding="utf-8").read()
        self.assertNotIn("or 65536", src)
        b_unset = ar._context_budget({"context_window": 0})
        b_set = ar._context_budget({"context_window": 65536})
        self.assertLess(b_unset, b_set)          # 未配置显著更小（安全方向）
        self.assertGreaterEqual(b_unset, 1000)

    def test_g8_missing_fields_no_crash(self):
        """G8：结构缺失/畸形不得崩（健壮性负控）。"""
        for bad in ({}, {"choices": []}, {"choices": [{}]},
                    {"choices": [{"message": {}}]}, None):
            try:
                out = ar._extract_reply(bad)
            except Exception as e:                     # noqa: BLE001
                self.fail(f"畸形响应导致异常: {e}")
            self.assertIsNone(out, f"畸形响应应判失败，实际={out!r}")

    def test_g9_reasoning_leak_still_blocked(self):
        """G9：既有的思考过程泄漏检测未被本次改动破坏。"""
        r = {"choices": [{
            "finish_reason": "stop",
            "message": {"content": "1. **分析请求** 用户想了解…",
                        "reasoning_content": ""},
        }]}
        self.assertIsNone(ar._extract_reply(r))


if __name__ == "__main__":
    unittest.main(verbosity=2)
