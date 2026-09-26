# -*- coding: utf-8 -*-
"""门禁：AIClient 的类方法集合完整性 + 不得被误嵌进模块级函数。

缺陷编号：AI-063（结构性回归 —— 类方法被解析为模块级函数内的嵌套函数 ⇒ 类方法静默丢失）

## 实测依据（2026-09-26，实机复现）
提交 `7c5a378`（ADR-013，v0.45.7）为新增 `_is_reasoner` / `_eff_max_tokens` /
`_extract_reply` 三个**模块级**函数，把它们插在 `class AIClient` 的 `chat()` 方法
之后、其余 6 个方法之前。Python 按**缩进**解析：跟随在模块级 `def` 之后、
缩进 4 空格的 `def` 会被解析成该模块级函数的**嵌套函数**，而不是类方法。

后果（实测）：`AIClient` 只剩 `__init__`/`chat`；`chat()` 内
`self._chat_openai(...)` 抛 `AttributeError: 'AIClient' object has no attribute
'_chat_openai'` ⇒ **AI 回复 / 图片描述 / 连接测试全链断裂**。
实测：修复前 `chat()` 返回 None；修复后返回真实文本。

## 判据（AST 结构断言，非 grep —— 缩进语义只能靠 AST 判定）
  G1  `AIClient` 具备全部 8 个方法（含 6 个曾丢失者）
  G2  上述 6 个方法均为**类方法**（在 AIClient 的 AST body 内），不是模块级嵌套函数
  G3  负控：任一模块级函数**不得**把类方法吞成自己的嵌套 def
      （注入式复现旧形态 → 本断言必须变红）
  G4  `_is_reasoner` / `_eff_max_tokens` / `_extract_reply` 仍是**模块级**函数
  G5  运行时 `hasattr` 自证（AST 与 import 结果一致）
"""
from __future__ import annotations

import ast
import os
import sys
import unittest

_BACKEND = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _BACKEND)
os.environ.setdefault("DY_APP_ROOT", r"C:\temp\dyautodm_design")

_SRC = os.path.join(_BACKEND, "services", "ai_reply.py")

#: AIClient 必须拥有的方法全集（含 2026-09-26 回归中丢失的 6 个）
_REQUIRED_METHODS = {
    "__init__", "chat",
    "_chat_openai", "_chat_anthropic", "chat_failover",
    "describe_image", "describe_image_failover", "test_connection",
}
#: 必须保持模块级的函数（ADR-013 引入的三个）
_MODULE_LEVEL_FUNCS = {"_is_reasoner", "_eff_max_tokens", "_extract_reply"}


def _parse(path: str = _SRC) -> ast.Module:
    with open(path, encoding="utf-8") as f:
        return ast.parse(f.read(), filename=path)


def _class_methods(tree: ast.Module, cls_name: str) -> set[str]:
    for n in tree.body:
        if isinstance(n, ast.ClassDef) and n.name == cls_name:
            return {x.name for x in n.body
                    if isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef))}
    return set()


def _nested_defs_under_module_funcs(tree: ast.Module) -> dict[str, list[str]]:
    """模块级函数体内出现嵌套 def 的映射（应恒为空——类方法只能在类内）。

    ⚠️ 这正是旧形态的特征：类方法被解析为某模块级函数的嵌套函数。
    """
    out: dict[str, list[str]] = {}
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            inner = [x.name for x in ast.walk(n)
                     if isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef))
                     and x is not n]
            if inner:
                out[n.name] = inner
    return out


class TestAiClientMethodStructure(unittest.TestCase):

    def test_g1_g2_aiclient_has_all_methods_as_class_methods(self):
        """G1/G2：6 个曾丢失的方法必须是 AIClient 的**类方法**。"""
        tree = _parse()
        methods = _class_methods(tree, "AIClient")
        missing = _REQUIRED_METHODS - methods
        self.assertFalse(missing, f"AIClient 缺少方法（被误嵌/丢失）: {sorted(missing)}")

    def test_g3_no_class_method_swallowed_by_module_func(self):
        """G3：任一模块级函数**不得**把类方法吞成其嵌套 def。

        负控覆盖：若有人再把模块级 def 插进类方法中间，本断言变红。
        """
        tree = _parse()
        nested = _nested_defs_under_module_funcs(tree)
        # 允许模块级函数内有闭包，但**绝不允许**出现这 6 个类方法名
        offenders = {fn: [m for m in inner if m in _REQUIRED_METHODS]
                     for fn, inner in nested.items()}
        offenders = {k: v for k, v in offenders.items() if v}
        self.assertFalse(
            offenders,
            f"类方法被误嵌为模块级函数的嵌套函数（结构性回归）：{offenders}")

    def test_g3b_negative_control_would_catch_regression(self):
        """G3b 负控自证：构造旧形态源码 → 断言必须变红（证明门禁非空转）。"""
        src = (
            "class AIClient:\n"
            "    def chat(self):\n"
            "        return reply\n"
            "\n"
            "def _extract_reply(x):\n"
            "    return x\n"
            "\n"
            "    def _chat_openai(self, cfg, messages):\n"
            "        return None\n"
        )
        tree = ast.parse(src)
        # 旧形态下 _chat_openai 不在 AIClient 内 → G1/G2 判据应命中
        self.assertNotIn("_chat_openai", _class_methods(tree, "AIClient"))
        nested = _nested_defs_under_module_funcs(tree)
        self.assertIn("_extract_reply", nested)
        self.assertIn("_chat_openai", nested["_extract_reply"])

    def test_g4_adr013_funcs_remain_module_level(self):
        """G4：ADR-013 的 3 个函数仍是模块级（不被塞回类内）。"""
        tree = _parse()
        top = {n.name for n in tree.body
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        missing = _MODULE_LEVEL_FUNCS - top
        self.assertFalse(missing, f"以下函数不再是模块级: {sorted(missing)}")

    def test_g5_runtime_hasattr_selfproof(self):
        """G5：运行时 hasattr 自证（AST 与 import 结果一致）。"""
        from services.ai_reply import AIClient
        missing = [m for m in _REQUIRED_METHODS if not hasattr(AIClient, m)]
        self.assertFalse(missing, f"运行时 AIClient 缺方法: {missing}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
