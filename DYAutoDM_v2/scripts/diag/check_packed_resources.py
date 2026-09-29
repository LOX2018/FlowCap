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

## contents 目录名（2026-09-29 更新）

contents 依赖目录名已由 `_internal` 改为 **`appinternals`**（原因：WiX 会把以 `_`
开头的目录名规范化、剥掉下划线，而 PyInstaller 启动器只认原名 ⇒ MSI 装出来崩）。
本自检**自动探测** `appinternals` 与旧名 `_internal`，不再硬编码单一名字
（历史缺陷：硬编码 `_internal` ⇒ 每轮构建都误报「未找到 _internal 目录」）。

## 用法

    python scripts/diag/check_packed_resources.py <packed_dir> [模块名...]

    packed_dir: 打包产物目录（内含 contents 依赖目录），如 `src-tauri/binaries`
    模块名:     可选，默认检查 Camoufox 依赖链

退出码：0=全齐；1=有缺失（并打印清单）。
"""
from __future__ import annotations

import os
import sys

DEFAULT_MODULES = (
    "camoufox", "camoufox.sync_api", "camoufox.async_api", "camoufox.utils",
    "browserforge", "apify_fingerprint_datapoints",
    "playwright.sync_api", "patchright.sync_api",
    "orjson", "maxminddb", "geoip2", "screeninfo", "ua_parser",
)

#: contents 依赖目录候选名（新名优先；旧名兼容）
_CONTENTS_NAMES = ("appinternals", "_internal")


def _find_contents_dir(packed_dir: str) -> str | None:
    """在 packed_dir 下探测 contents 依赖目录（appinternals 或 _internal）。"""
    for name in _CONTENTS_NAMES:
        p = os.path.join(packed_dir, name)
        if os.path.isdir(p):
            return p
    if os.path.basename(os.path.normpath(packed_dir)) in _CONTENTS_NAMES:
        return packed_dir
    return None


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
    internal = _find_contents_dir(packed_dir)
    if not internal:
        return False, {"error":
                       f"未找到 contents 依赖目录（{' / '.join(_CONTENTS_NAMES)}）: {packed_dir}"}
    res = collect_resources(modules)
    missing: dict[str, list[str]] = {}
    for pkg, files in res.items():
        miss = [f for f in files
                if not os.path.exists(os.path.join(internal, pkg, f))]
        if miss:
            missing[pkg] = miss
    return (not missing), {"checked": res, "missing": missing, "contents": internal}


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
    print(f"contents 依赖目录: {info.get('contents')}")
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
