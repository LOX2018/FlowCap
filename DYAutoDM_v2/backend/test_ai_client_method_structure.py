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
import tempfile
import unittest

_BACKEND = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _BACKEND)
# A-8 / M-17 隔离根单一化：模块级 DY_APP_ROOT 必须是**一次性临时目录**，禁止回落
# 真实 design 数据根或源码树（旧值 r"C:\temp\dyautodm_design" 靠 discover 导入顺序
# 侥幸避免污染）。先 mkdir 再赋值 —— vbrowser.app_root() 对不存在的根会忽略并回落
# 仓库相对路径 data/（污染工作树）。范式见 test_uid_sink_ext.py:17-39。
_ROOT = tempfile.mkdtemp(prefix="ai_client_ms_")
os.makedirs(_ROOT, exist_ok=True)
os.environ["DY_APP_ROOT"] = _ROOT

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


def _first_arg_is_self(fn) -> bool:
    """Python 3.8+：位置参数（含 posonly）首个是否为 `self`。"""
    a = fn.args
    if a.posonlyargs:
        return a.posonlyargs[0].arg == "self"
    if a.args:
        return a.args[0].arg == "self"
    return False


def _scan_orphan_methods(root: str) -> list[tuple[str, str, str, int]]:
    """全仓扫描「孤儿类方法」：**模块级函数**的**直接子节点**里的首参 self def。

    只用**直接子节点**（不进嵌套 class/函数），故：
      ✅ 抓 AI-063 形态（类方法被解析为模块级函数的嵌套函数）
      ⚪ 不抓 `def outer(): class Fake: def m(self)` —— 那是合法局部类
    返回 (相对路径, 外层函数名, 内层函数名, 行号)。
    """
    import pathlib
    hits: list[tuple[str, str, str, int]] = []
    for p in pathlib.Path(root).rglob("*.py"):
        if "__pycache__" in str(p):
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        for n in tree.body:                      # 仅顶层节点
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for x in n.body:                 # 仅直接子节点
                    if isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                            and _first_arg_is_self(x):
                        rel = p.relative_to(root).as_posix()
                        hits.append((rel, n.name, x.name, x.lineno))
    return hits


#: 白名单：(相对路径, 外层函数, 内层函数, 行号) —— 有两种合法用途需 `self` 形参：
#: ① monkeypatch 替身（首个形参按被替身的原签名即 `self`）；
#: ② TestCase 方法工厂（产出物被 `setattr(TestCase, name, fn)` 装成测试方法）。
#: **行号仅作文档**（匹配只用前三元组）；每条必须写明理由；不得为「让门禁变绿」而加项
#: （同 .known-gaps.json 纪律）。新增条目必须能被 test_g6b 的负控覆盖。
_ALLOWED_ORPHANS = [
    ("test_upstream_p3.py", "_rendered_texts", "spy", 334),
    # ↑ `ImageDraw.ImageDraw.text` 的 monkeypatch 替身，第一个形参按原签名即 `self`。
    ("test_task_scheduler_gates.py", "_make_test", "_t", 699),
    # ↑ TestCase 方法工厂：`_t` 经 `setattr(GateTestCase, "test_<gid>", …)` 装成
    #   测试方法（test_task_scheduler_gates.py:705），首参必须是 `self`（unittest 契约）。
    #   非 AI-063 型误嵌：它**本来就不属于任何类**，故不存在「类方法静默丢失」。
]


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

    # ── G6/G7：全仓泛化 —— 防「孤儿类方法」同类缺陷在任何文件复发 ──────────
    # 判据：**模块级函数**的**直接子节点**若是「首参为 self」的 def，则该 def
    # 几乎必然是**误嵌的类方法**。
    # 合法用途只有两种（其余一律报红）：
    #   ① monkeypatch 替身 —— 第一个形参按被替身的原签名即 `self`；
    #   ② TestCase 方法工厂 —— 产出的函数要被 `setattr(TestCase, name, fn)`
    #      装成测试方法，首参必须是 `self`（unittest 契约）。
    # 两者都需先例可查，故设白名单；**不得为「让门禁变绿」而加项**
    # （同 .known-gaps.json 纪律）。
    # 与 AI-063 同型：语法合法、无报错、类方法静默丢失。
    def test_g6_no_orphan_class_methods_repo_wide(self):
        """全仓扫描：不得存在「模块级函数直接内嵌首参 self 的 def」。"""
        offenders = _scan_orphan_methods(_BACKEND)
        allow = {(p, outer, inner) for p, outer, inner, _ in _ALLOWED_ORPHANS}
        new = [o for o in offenders if o[:3] not in allow]
        self.assertFalse(
            new,
            "发现孤儿类方法（疑误嵌进模块级函数，AI-063 同型缺陷）：\n  "
            + "\n  ".join(f"{p}:{ln} {outer}() 内嵌 {inner}()" for p, outer, inner, ln in new))

    def test_g6b_whitelist_does_not_blind_the_scan(self):
        """G6b 负控：白名单只豁免被点名的那一条，同文件其它孤儿仍须报红。

        防「为修一条红而把整类 scan 关掉」——直接对检测器喂合成源码，
        断言 ①白名单命中的形态被豁免、②**同一文件内**新增的孤儿仍被抓到。
        """
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "daemon"), exist_ok=True)
            with open(os.path.join(d, "test_task_scheduler_gates.py"), "w",
                      encoding="utf-8") as f:
                # ① 白名单内的工厂形态（应被豁免）
                f.write("def _make_test(gid, fn):\n"
                        "    def _t(self):\n"
                        "        return gid\n"
                        "    return _t\n"
                        "\n"
                        # ② 同文件内**新**孤儿（须报红）
                        "def _another_factory():\n"
                        "    def _sneaky(self):\n"
                        "        return 1\n"
                        "    return _sneaky\n")
            hits = _scan_orphan_methods(d)
        triples = {h[:3] for h in hits}
        self.assertIn(("test_task_scheduler_gates.py", "_make_test", "_t"), triples,
                      "白名单目标形态未被检测器抓到 ⇒ 白名单条目已失效（应删除或更新）")
        self.assertIn(("test_task_scheduler_gates.py", "_another_factory", "_sneaky"),
                      triples, "同文件新增孤儿未被抓到 ⇒ 扫描被白名单弄瞎了")
        allow = {(p, outer, inner) for p, outer, inner, _ in _ALLOWED_ORPHANS}
        remaining = sorted(t for t in triples if t not in allow)
        self.assertEqual(remaining,
                         [("test_task_scheduler_gates.py", "_another_factory", "_sneaky")],
                         "白名单豁免面超出预期")

    def test_g7_orphan_detector_catches_ai063_form(self):
        """G7 负控：注入 AI-063 旧形态 → 检测器必须命中（证非空转）。"""
        src = ("class AIClient:\n"
               "    def chat(self):\n"
               "        return reply\n"
               "\n"
               "def _extract_reply(x):\n"
               "    return x\n"
               "\n"
               "    def _chat_openai(self, cfg, messages):\n"
               "        return None\n")
        tree = ast.parse(src)
        found = []
        for n in tree.body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for x in n.body:
                    if isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                            and _first_arg_is_self(x):
                        found.append((n.name, x.name))
        self.assertIn(("_extract_reply", "_chat_openai"), found)


if __name__ == "__main__":
    unittest.main(verbosity=2)
