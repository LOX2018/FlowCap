# -*- coding: utf-8 -*-
"""H-22 审计 P4 修复的机械门禁（防复发）—— 2026-09-26。

## 两条判据
  G1  backend/auto_dm/login_api_vendor.py ：`_import_upstream` 必须带**命名空间碰撞
      前置检查** —— 当 `sys.modules` 里已有**项目** `dy_apis` 时**显式抛错（AUTH-073）**，
      **绝不**返回一个缺 `bootstrap_auth/get_qrcode/check_qrcode` 的对象（静默半坏）。
  G2  backend/mcp/registry.py ：`set_active_scope` 对「**显式提供**但解析为空」的取值
      （如 `' '` / `','` / `['','  ']`）必须**抛 ValueError**，绝不静默回落 `("full",)`
      —— 后者使 ADR-010 S5「绝不静默回落全量」形同虚设。

判据分**源码级**（防形态回退）与**行为级**（真跑）两层，各配负控。
"""
from __future__ import annotations

import os
import re
import sys
import textwrap
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


def _read(rel: str) -> str:
    with open(os.path.join(_HERE, rel), encoding="utf-8") as f:
        return f.read()


def _func_src(src: str, name: str) -> str | None:
    import ast
    tree = ast.parse(src)
    lines = src.splitlines(keepends=True)

    def _find(node):
        for n in node.body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
                return n
            if isinstance(n, ast.ClassDef):
                r = _find(n)
                if r:
                    return r
        return None

    n = _find(tree)
    return None if n is None else textwrap.dedent(
        "".join(lines[n.lineno - 1:n.end_lineno]))


# ═════════════════════ 判据 ═════════════════════

def check_g1(source: str) -> tuple[bool, str]:
    """判据：`_import_upstream` 必须**先判后插** —— 碰撞判定在 `_ensure_path()` 之前，
    且覆盖「已加载」与「可解析到」两种情形；同时 `_collision_error` 必须带 AUTH-073。"""
    fn = _func_src(source, "_import_upstream")
    if not fn:
        return False, "未找到 _import_upstream"
    for need in ("_SHARED_TOP_PKGS", "_loaded_origin", "_resolvable_origin"):
        if need not in fn:
            return False, f"缺少 {need}（未同时覆盖已加载/可解析两情形）"
    i_ensure = fn.find("\n    _ensure_path()")     # 锚实际调用行，避开 docstring 提及
    i_loaded = fn.find("_loaded_bad = [")
    i_resolved = fn.find("_resolved_bad = [")
    if i_ensure < 0 or i_loaded < 0 or i_resolved < 0:
        return False, "缺少 _ensure_path / _loaded_bad / _resolved_bad"
    if not (i_loaded < i_ensure and i_resolved < i_ensure):
        return False, "碰撞判定晚于 _ensure_path（会先遮蔽项目包再判定 —— 污染宿主）"
    if "AUTH-073" not in source:
        return False, "缺少 AUTH-073 错误码"
    if "raise _collision_error(" not in fn:
        return False, "未显式失败（仍是静默半坏）"
    if "def _collision_error" not in source:
        return False, "缺少 _collision_error 定义"
    return True, "先判后插 + 双情形覆盖 + AUTH-073 显式失败"


def check_g2(source: str) -> tuple[bool, str]:
    fn = _func_src(source, "set_active_scope")
    if not fn:
        return False, "未找到 set_active_scope"
    if re.search(r"if not items:\s*\n\s*items\s*=\s*\(\s*[\"']full[\"']\s*,?\s*\)", fn):
        return False, "仍在空解析时静默回落 full"
    if "raise ValueError" not in fn:
        return False, "空解析未抛 ValueError"
    if "解析后为空" not in fn:
        return False, "未给出「解析后为空」的明确报错"
    return True, "显式空项抛 ValueError；None/'' 仍走未提供分支"


# ═════════════════════ 源码级正向 ═════════════════════

class TestP4SourceGates(unittest.TestCase):
    def test_g1_vendor_collision_guard(self):
        ok, msg = check_g1(_read(os.path.join("auto_dm", "login_api_vendor.py")))
        self.assertTrue(ok, f"G1 失败: {msg}")

    def test_g2_scope_empty_rejected(self):
        ok, msg = check_g2(_read(os.path.join("mcp", "registry.py")))
        self.assertTrue(ok, f"G2 失败: {msg}")


# ═════════════════════ 行为级正向 ═════════════════════

class TestP4Behaviour(unittest.TestCase):
    def test_g2_behaviour(self):
        """真跑：显式空项拒绝 / None·'' 仍默认 full / 拒绝后状态不变。"""
        from mcp import registry as R
        for v in (" ", ",", "  ,  ", ["", "  "], (" ",), {"  "}):
            with self.assertRaises(ValueError, msg=f"{v!r} 应拒绝"):
                R.set_active_scope(v)
        for v in (None, ""):
            self.assertEqual(R.set_active_scope(v), ("full",),
                             f"{v!r} 应仍走默认")
        self.assertEqual(R.set_active_scope("full,debug"), ("full", "debug"))
        R.set_active_scope("debug")
        before = R._ACTIVE_SCOPES
        with self.assertRaises(ValueError):
            R.set_active_scope(",")
        self.assertEqual(R._ACTIVE_SCOPES, before, "拒绝后作用域不应被改变")

    def test_g1_behaviour_collision_explicit_fail(self):
        """真跑：项目 dy_apis 占用时，bootstrap 必须显式抛 AUTH-073，而非 AttributeError。"""
        import dy_apis  # noqa: F401  ← 模拟应用进程：项目包已加载
        from auto_dm import login_api_vendor as V
        self.assertTrue(V.vendor_available(), "前提：vendor 目录应就位")
        with self.assertRaises(RuntimeError) as ctx:
            V.VendorLoginApi().bootstrap()
        self.assertIn("AUTH-073", str(ctx.exception))


# ═════════════════════ 负控 ═════════════════════

_G1_OLD = textwrap.dedent('''
def _import_upstream():
    """惰性导入上游 DYLoginApi。"""
    _ensure_path()
    try:
        from dy_apis.login_api import DYLoginApi
    except ImportError as e:
        raise
    return DYLoginApi
''')

_G2_OLD = textwrap.dedent('''
def set_active_scope(scope) -> tuple:
    global _ACTIVE_SCOPES
    if scope is None or scope == "":
        items: tuple = ("full",)
    else:
        items = tuple(x.strip().lower()
                      for x in str(scope).split(",") if x.strip())
    if not items:
        items = ("full",)
    bad = [x for x in items if x not in SCOPES]
    if bad:
        raise ValueError(f"未知 scope: {bad!r}")
    _ACTIVE_SCOPES = items
    return items
''')


class TestP4NegativeControls(unittest.TestCase):
    def test_g1_detects_silent_halfbroken(self):
        ok, msg = check_g1(_G1_OLD)
        self.assertFalse(ok, f"G1 负控失效: {msg}")

    def test_g2_detects_silent_full_fallback(self):
        ok, msg = check_g2(_G2_OLD)
        self.assertFalse(ok, f"G2 负控失效: {msg}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
