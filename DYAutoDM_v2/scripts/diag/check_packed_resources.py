# -*- coding: utf-8 -*-
"""打包产物资源自检：一次性列出所有「源码态有、打包态缺」的运行时资源。

## 为什么需要（2026-09-20 事故复盘）

Camoufox 接入过程中，连续 6 个「只在打包态暴露、源码态全绿」的缺陷被逐个
试错发现，每次都要重打 15 分钟 → 白等 1 小时。根因是**缺乏机械自检**：
人工遍历依赖链必然漏掉「传递依赖」（如 language_tags 是 ua_parser 的运行时依赖，
但不在 ua_parser 包目录内）。

## 判据（机械、可复现）

对「源码态能 import 成功的每个模块」，取其包目录下的**全部非 .py 文件**，
逐个检查是否存在于打包产物的对应位置。缺失即为打包漏漏项。

## 用法

    python scripts/diag/check_packed_resources.py <packed_dir> [模块名...]

    packed_dir: 打包产物目录（含 _internal/ 的目录），如
                C:\\temp\\dyautodm_design
    模块名:     可选，默认检查 Camoufox 依赖链

退出码：0=全齐；1=有缺失（并打印清单）。
"""
from __future__ import annotations

import importlib
import importlib.util
import os
import sys

DEFAULT_MODULES = (
    "camoufox", "camoufox.sync_api", "camoufox.async_api", "camoufox.utils",
    "browserforge", "apify_fingerprint_datapoints",
    "playwright.sync_api", "patchright.sync_api",
    "orjson", "maxminddb", "geoip2", "screeninfo", "ua_parser",
)


def _pkg_root(mod: str) -> str | None:
    """返回模块所在包的顶层目录（site-packages/<pkg>）。"""
    try:
        import importlib.util as u
        spec = u.find_spec(mod)
    except Exception:
        return None
    if not spec or not spec.origin or spec.origin in ("built-in", "frozen"):
        return None
    p = os.path.dirname(spec.origin)
    # 上溯到顶层包目录（含 __init__.py 的最外层）
    while os.path.basename(os.path.dirname(p)) not in ("site-packages", "dist-packages", ""):
        parent = os.path.dirname(p)
        if not os.path.isfile(os.path.join(parent, "__init__.py")):
            break
        p = parent
    return p


def collect_resources(modules) -> dict[str, list[str]]:
    """{包名: [相对路径...]} —— 各包内的非 .py 资源。"""
    out: dict[str, list[str]] = {}
    for mod in modules:
        root = _pkg_root(mod)
        if not root or not os.path.isdir(root):
            continue
        pkg = os.path.basename(root)
        rel: list[str] = []
        for r, _dirs, files in os.walk(root):
            for f in files:
                if f.endswith((".py", ".pyc", ".pyi")):
                    continue
                rel.append(os.path.relpath(os.path.join(r, f), root))
        if rel:
            out[pkg] = sorted(rel)
    return out


def check(packed_dir: str, modules=DEFAULT_MODULES) -> tuple[bool, dict]:
    internal = os.path.join(packed_dir, "_internal")
    if not os.path.isdir(internal):
        # 也支持传统布局：<packed_dir>/<pkg>/ 或直接 _internal 在 packed_dir 下
        if os.path.isdir(os.path.join(packed_dir, "_internal")):
            internal = os.path.join(packed_dir, "_internal")
        else:
            return False, {"error": f"未找到 _internal 目录: {internal}"}
    res = collect_resources(modules)
    missing: dict[str, list[str]] = {}
    for pkg, files in res.items():
        miss = [f for f in files
                if not os.path.exists(os.path.join(internal, pkg, f))]
        if miss:
            missing[pkg] = miss
    return (not missing), {"checked": res, "missing": missing}


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    packed = sys.argv[1]
    mods = tuple(sys.argv[2:]) or DEFAULT_MODULES
    ok, info = check(packed, mods)
    if "error" in info:
        print("❌", info["error"])
        return 1
    checked = info["checked"]
    missing = info["missing"]
    print(f"打包目录: {packed}")
    print(f"检查包数: {len(checked)}（资源文件共 {sum(len(v) for v in checked.values())} 个）")
    print()
    if ok:
        print("✅ 全部运行时资源齐备")
        return 0
    print(f"❌ 缺失 {sum(len(v) for v in missing.values())} 个资源，涉及 {len(missing)} 个包：")
    for pkg, files in sorted(missing.items()):
        print(f"\n  [{pkg}] 缺 {len(files)} 个，例：")
        for f in files[:5]:
            print(f"      {f}")
        if len(files) > 5:
            print(f"      ... 另有 {len(files)-5} 个")
    print("\n修复：在 build_sidecar.py 的 collect-all 列表加入上述包名。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
