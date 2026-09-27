# -*- coding: utf-8 -*-
"""污染源候选清单生成器 —— 扫描 backend/ 下所有 test_*.py 与 scripts/*.py
对 DY_APP_ROOT 的每一次**写**（赋值/setdefault/pop）并分类。

分类：
  A 模块级无条件赋值   os.environ["DY_APP_ROOT"] = X        （import 期即生效，最危险）
  B 模块级 setdefault  os.environ.setdefault(...)          （同进程内先到先得）
  C 函数/方法/setUp 内赋值
  D 只读引用 / 文档注释（不写，排除）
"""
from __future__ import annotations

import ast
import io
import json
import os
import sys

BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "backend")


def _const_str(node):
    try:
        v = ast.literal_eval(node)
    except Exception:
        return None
    return v if isinstance(v, str) else None


def _target_is_app_root(node) -> bool:
    """判断下标是否是 os.environ["DY_APP_ROOT"] / get 之一。"""
    if not isinstance(node, ast.Subscript):
        return False
    val = node.value
    if isinstance(val, ast.Attribute) and val.attr == "environ":
        return True
    if isinstance(val, ast.Name) and val.id == "os":
        return True
    return False


def _is_app_root_subscript(node) -> bool:
    if not isinstance(node, ast.Subscript):
        return False
    v = node.slice
    if isinstance(v, ast.Constant) and v.value == "DY_APP_ROOT":
        return _target_is_app_root(node)
    return False


def _is_setdefault_call(node) -> bool:
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "setdefault")


def scan_file(path: str):
    src = io.open(path, encoding="utf-8", errors="replace").read()
    try:
        tree = ast.parse(src, filename=path)
    except SyntaxError as e:
        return [], ["syntax error: %s" % e]
    lines = src.splitlines()
    hits = []

    # 建立 行 -> 所处作用域深度 + 是否在函数内 的映射
    class V(ast.NodeVisitor):
        def __init__(self):
            self.depth = 0

        def visit_FunctionDef(self, n):
            self.depth += 1
            self.generic_visit(n)
            self.depth -= 1

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_ClassDef(self, n):
            self.generic_visit(n)

    # 用 parent map 判断是否位于函数体内
    parents = {}
    for node in ast.walk(tree):
        for ch in ast.iter_child_nodes(node):
            parents[ch] = node

    def in_function(n):
        p = parents.get(n)
        while p is not None:
            if isinstance(p, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return True
            p = parents.get(p)
        return False

    for node in ast.walk(tree):
        # 1) 赋值： os.environ["DY_APP_ROOT"] = X
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if _is_app_root_subscript(tgt):
                    hits.append({
                        "line": node.lineno,
                        "cat": "C" if in_function(node) else "A",
                        "kind": "assign",
                        "expr": lines[node.lineno - 1].strip()[:160],
                        "value": _const_str(node.value),
                    })
            continue
        # 2) os.environ.setdefault("DY_APP_ROOT", X)
        if isinstance(node, ast.Call) and _is_setdefault_call(node):
            args = node.args
            if args and isinstance(args[0], ast.Constant) \
                    and args[0].value == "DY_APP_ROOT":
                hits.append({
                    "line": node.lineno,
                    "cat": "C" if in_function(node) else "B",
                    "kind": "setdefault",
                    "expr": lines[node.lineno - 1].strip()[:160],
                    "value": _const_str(args[1]) if len(args) > 1 else None,
                })
    return hits, []


def main():
    results = []
    for sub in (".", "scripts"):
        d = os.path.join(BACKEND, sub)
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if not name.endswith(".py"):
                continue
            if sub == ".":
                if not (name.startswith("test_") or name.startswith("verify_")):
                    continue
            p = os.path.join(d, name)
            hits, errs = scan_file(p)
            if hits:
                results.append({"file": os.path.relpath(p, BACKEND),
                                "hits": hits, "errors": errs})
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "pollution_candidates.json")
    with io.open(out, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    # 汇总
    na = nb = nc = 0
    design = []
    for r in results:
        for h in r["hits"]:
            if h["cat"] == "A":
                na += 1
            elif h["cat"] == "B":
                nb += 1
            else:
                nc += 1
            v = (h.get("value") or "").lower()
            if "dyautodm_design" in v or "design" in v:
                design.append((r["file"], h["line"], h["cat"], h["kind"], h["expr"]))
    print(json.dumps({"files": len(results), "catA": na, "catB": nb, "catC": nc,
                      "design_pointing": design}, ensure_ascii=False, indent=1))
    print("\n-> %s" % out)


if __name__ == "__main__":
    sys.exit(main())
