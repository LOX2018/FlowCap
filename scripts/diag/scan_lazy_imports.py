# -*- coding: utf-8 -*-
"""扫描 backend 全仓「函数体内延迟 import」的本地模块，与 build_sidecar 的
hidden-import 清单做差集 —— 差集非空 = 打包后极可能 ModuleNotFoundError。

## 为什么需要（v0.43.91 事故固化）

`services/dm_dispatch.py` 是**只在函数体内**被 import 的（`core/dispatch.py:350`
的 `from services.dm_dispatch import get_dispatcher`）。它未被
`scripts/build_sidecar.py` 声明为 hidden-import → PyInstaller 打包后
backend 侧 `No module named 'services.dm_dispatch'` → 直播/采集私信**一条都发不出**
（日志只有一条 `SEND-037 dm_dispatch 接入失败，已放弃发送`），而其余功能全正常
—— 症状极隐蔽，且**只有真跑打包产物才暴露**（源码态 import 一切正常）。

判据：**函数体内的 import 不保证被打包收录**。凡「本地包」模块属此类，
必须在 build_sidecar.py 显式声明。本脚本把它变成机器可检查的差集。

候选模块必须**在磁盘上真实存在**（`<pkg>/<name>.py` 或 `<pkg>/<name>/__init__.py`），
从而不把函数名/类名误判成模块。

用法:
    python scripts/diag/scan_lazy_imports.py            # 打印差集
    python scripts/diag/scan_lazy_imports.py --check    # 差集非空则 exit 1（守卫）
"""
from __future__ import annotations

import argparse
import ast
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
BACKEND = os.path.join(ROOT, "backend")
BUILD_SIDECAR = os.path.join(ROOT, "scripts", "build_sidecar.py")

LOCAL_PKGS = ("services", "daemon", "api", "core", "notify", "dy_apis",
              "models", "utils", "mcp")


def _is_local(mod: str) -> bool:
    return bool(mod) and (mod.split(".")[0] in LOCAL_PKGS)


def _exists_as_module(mod: str) -> bool:
    """backend/<mod>.py 或 backend/<mod>/__init__.py 是否存在。"""
    rel = mod.replace(".", os.sep)
    return (os.path.isfile(os.path.join(BACKEND, rel + ".py"))
            or os.path.isfile(os.path.join(BACKEND, rel, "__init__.py")))


def _collect(tree: ast.AST, lazy: bool) -> set[str]:
    """收集 import 目标（只保留磁盘上真实存在的本地模块）。"""
    out: set[str] = set()

    def note(node: ast.AST) -> None:
        if isinstance(node, ast.Import):
            cands = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:            # 相对导入跳过（包内必被打包）
                return
            if not node.module:
                return
            cands = [node.module]
            # from pkg import mod → 可能导入的是子模块（仅当文件存在时计入）
            cands += [f"{node.module}.{a.name}" for a in node.names]
        else:
            return
        for c in cands:
            if _is_local(c) and _exists_as_module(c):
                out.add(c)

    if lazy:
        for fn in ast.walk(tree):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                for node in ast.walk(fn):
                    if isinstance(node, (ast.Import, ast.ImportFrom)):
                        note(node)
    else:
        for node in tree.body:
            note(node)
            # try/except 包裹的顶层 import（容错导入）同样算静态可见
            if isinstance(node, ast.Try):
                for sub in ast.walk(node):
                    if isinstance(sub, (ast.Import, ast.ImportFrom)):
                        note(sub)
    return out


def _is_app_module(filename: str) -> bool:
    """该文件是否会进入 sidecar 产物。

    PyInstaller 从入口脚本（main.py / daemon/*.py）出发做可达性分析，
    **只打包能追到的模块**。以下两类永不进产物，它们里面的 import
    不能作为「已被静态分析收录」的证据（否则差集恒为空 → 守卫失效，
    实测：dm_dispatch 曾被 test_dm_dispatch_config.py 的顶层 import 掩盖）：
      - `test_*.py`（单元测试）
      - `_*.py`（一次性探针 / 诊断脚本）
    """
    return not (filename.startswith("test_") or filename.startswith("_"))


def _module_name(path: str) -> str | None:
    """backend 下文件路径 → 模块名（非应用模块返回 None）。"""
    rel = os.path.relpath(path, BACKEND).replace(os.sep, "/")
    if not rel.endswith(".py") or not _is_app_module(os.path.basename(path)):
        return None
    rel = rel[:-3]
    if rel.endswith("/__init__"):
        rel = rel[: -len("/__init__")]
    return rel.replace("/", ".")


def _collect_all(tree: ast.AST) -> tuple[set[str], set[str]]:
    """返回 (顶层导入, 全部导入) —— 都用文件存在性过滤。"""
    top = _collect(tree, lazy=False)
    lazy = _collect(tree, lazy=True)
    return top, (top | lazy)


ENTRIES = {
    "main": "main.py",
    "daemon.browser_daemon": "daemon/browser_daemon.py",
    "daemon.recv_daemon": "daemon/recv_daemon.py",
}


def _graph() -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """(模块 → 顶层导入, 模块 → 全部导入)。"""
    top_g: dict[str, set[str]] = {}
    all_g: dict[str, set[str]] = {}
    for dirpath, dirnames, filenames in os.walk(BACKEND):
        dirnames[:] = [d for d in dirnames
                       if d not in ("__pycache__", "build", "dist")]
        for fn in filenames:
            path = os.path.join(dirpath, fn)
            mod = _module_name(path)
            if not mod:
                continue
            try:
                with open(path, encoding="utf-8", errors="replace") as f:
                    tree = ast.parse(f.read())
            except (SyntaxError, OSError):
                continue
            t, a = _collect_all(tree)
            top_g[mod] = t
            all_g[mod] = a
    return top_g, all_g


def _reach(graph: dict[str, set[str]], start: str) -> set[str]:
    seen: set[str] = set()
    stack = [start]
    while stack:
        m = stack.pop()
        for nxt in graph.get(m, ()):  # noqa: B905
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return seen


def per_entry_gaps() -> dict[str, list[str]]:
    """每个入口：可达但**静态追不到**的本地模块（即必须 hidden-import 的）。"""
    top_g, all_g = _graph()
    out: dict[str, list[str]] = {}
    for entry, rel in ENTRIES.items():
        if entry not in top_g:
            continue
        static = _reach(top_g, entry)      # PyInstaller 静态可达
        runtime = _reach(all_g, entry)     # 运行期真正会用到的
        out[entry] = sorted(runtime - static)
    return out


def scan() -> tuple[set[str], set[str], set[str]]:
    top: set[str] = set()
    lazy: set[str] = set()
    for dirpath, dirnames, filenames in os.walk(BACKEND):
        dirnames[:] = [d for d in dirnames
                       if d not in ("__pycache__", "build", "dist")]
        for fn in filenames:
            if not fn.endswith(".py") or not _is_app_module(fn):
                continue
            try:
                with open(os.path.join(dirpath, fn),
                          encoding="utf-8", errors="replace") as f:
                    tree = ast.parse(f.read())
            except (SyntaxError, OSError):
                continue
            top |= _collect(tree, lazy=False)
            lazy |= _collect(tree, lazy=True)
    return top, lazy, (lazy - top)


def declared() -> set[str]:
    """build_sidecar.py 里声明的 --hidden-import 模块名。"""
    try:
        with open(BUILD_SIDECAR, encoding="utf-8", errors="replace") as f:
            src = f.read()
    except OSError:
        return set()
    names = set(re.findall(r'"--hidden-import",\s*"([\w.\-]+)"', src))
    for grp in re.findall(r"for _m in \((.*?)\):", src, re.S):
        names |= set(re.findall(r'"([\w.\-]+)"', grp))
    return names


def _artifact_missing(modules: list[str] | None = None) -> list[str] | None:
    """用产物归档判定「真缺失」——**权威判据**。

    静态差集（scan）有**大量假阳性**：实测 PyInstaller 能扫到绝大多数函数体
    import（22 个静态差集里只有 3 个真缺），故静态结果只能当**候选**；
    是否真缺必须以产物 PYZ 归档为准（`list_archive_modules`）。
    返回 None 表示产物/读取器不可用（此时不做结论）。
    """
    import glob
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "_lam", os.path.join(HERE, "list_archive_modules.py"))
        lam = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        spec.loader.exec_module(lam)  # type: ignore[union-attr]
    except Exception:
        return None

    exe = None
    for pat in ("src-tauri/binaries/flowcap-backend-*.exe",
                "src-tauri/binaries/**/flowcap-backend-*.exe"):
        hits = glob.glob(os.path.join(ROOT, pat), recursive=True)
        if hits:
            exe = hits[0]
            break
    if not exe:
        return None
    try:
        names = {n.replace(".", "_") for n in lam.list_pyz_names(exe)}
    except Exception:
        return None
    cands = modules if modules is not None else sorted(scan()[2])
    return [m for m in cands if m.replace(".", "_") not in names]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="差集非空则返回 1（供 CI / 守卫测试调用）")
    ap.add_argument("--against-artifact", action="store_true",
                    help="以 src-tauri/binaries 的 backend 产物为准，只报真缺失")
    args = ap.parse_args()

    top, lazy, diff = scan()
    dec = declared()
    missing = sorted(m for m in diff if m not in dec)

    if args.against_artifact:
        real = _artifact_missing(sorted(diff))
        if real is None:
            print("产物或读取器不可用，无法做权威判定（退化为静态候选）")
        else:
            print(f"以产物归档为准，真缺失 {len(real)} 个：")
            for m in real:
                print("   ❌", m)
            if not real:
                print("   ✅ 无缺失")
            return 1 if (real and args.check) else 0

    print(f"顶层可见的本地模块          : {len(top)}")
    print(f"函数体延迟导入的本地模块    : {len(lazy)}")
    print(f"仅延迟导入（顶层不可见）    : {len(diff)}")
    print(f"build_sidecar 已声明        : {len(dec)}")
    print("")
    if missing:
        print(f"⚠️ 未声明 hidden-import 的**候选** {len(missing)} 个")
        print("   （注意：多数在产物里其实存在 —— 以 --against-artifact 为准）")
        for m in missing:
            print("   -", m)
    else:
        print("✅ 仅延迟导入的模块已全部在 build_sidecar.py 声明")
    return 1 if (missing and args.check) else 0


if __name__ == "__main__":
    sys.exit(main())
