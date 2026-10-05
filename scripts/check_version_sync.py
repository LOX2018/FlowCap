# -*- coding: utf-8 -*-
"""版本一致性门禁（2026-09-16 v0.43.40）。

为什么需要（真实事故）
----------------------
v0.43.37→0.43.40 期间只升了「根 package.json / tauri.conf / Cargo.toml /
_build_version.py」四处，**漏了 `frontend/package.json`**——
而它才是 **vite `__APP_VERSION__` 的真实来源**（vite.config.ts 里
`import pkg from "./package.json"`）。后果：

    前端 0.43.37 ≠ 后端 0.43.40 → 版本门禁拒绝**所有** /api 请求
    → 界面 "Failed to fetch"，且日志刷 `前后端版本不一致`。

本脚本把"六处版本必须齐平"固化为可执行门禁，改版本后跑一次即可，
**不再依赖记忆**。

L-10 补记（2026-09-23，验证轮）
-------------------------------
第六处 `src-tauri/Cargo.lock`（`[[package]] name = "flowcap"` 段的 version）
由 `48ae837`（v0.44.44）加入 TARGETS，**故「门禁未覆盖 Cargo.lock」的审计结论
在本版 HEAD（0.44.53）上已过期**。本轮补做当时缺失的**破坏性验证**：

    break :  0.44.53 -> 0.99.99  ⇒ 门禁 ✗（`版本不一致: ['0.44.53','0.99.99']`，exit 1）
    restore:  备份还原            ⇒ 门禁 ✓（`六处版本齐平: 0.44.53`，exit 0）

⇒ 第六处**是活的检查项**（不是只打印不判定）。已顺带把总结行的
「六处」改为按 TARGETS 长度动态生成，避免以后增删目标时文案说谎。

另注（F-5 盲区为何在此**不需要**新条目）：`backend/main.py` 的
`FastAPI(version=...)` 过去写死 `"0.43.83"`（漂移 10 个小版本且门禁不管）。
本轮 F-5 已把它改为引用 `APP_VERSION`（= `_build_version.py` 的编译期常量，
**正是本门禁已检查的第 4 处**）⇒ 该盲区**结构性**关闭，无需重复登记。

用法：
    python scripts/check_version_sync.py            # 校验，不一致 exit 1
    python scripts/check_version_sync.py 0.43.40    # 校验并断言等于指定版本
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 分位数（patch，第三位）上限 —— flowcap-version-sync skill，2026-10-01 用户设定。
# 规则：分位数 = 50 时再 +0.01 → 次版本号 +1，分位数从 01 起。
# patch > 50 = 应进位而未进位 = 违规。
# H-33 根因：此上限此前只在 skill 正文里、没有机械载体 ⇒ 已复发两次
#（0.45.152 → 0.46.60，两次都超限却无人拦）。本常量是它的 instrument 层。
PATCH_LIMIT = 50


def _check_patch_limit(version: str) -> list[str]:
    """分位数 ≤ PATCH_LIMIT，超过即应进位（H-33 根治，2026-10-04）。

    返回违规消息列表（空 = 合规）。纯函数，便于负控验证。
    """
    bad: list[str] = []
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)$", version)
    if not m:
        bad.append(f"版本号格式非 major.minor.patch: {version}")
        return bad
    major, minor, patch = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if patch > PATCH_LIMIT:
        bad.append(
            f"分位数 {patch} 超过上限 {PATCH_LIMIT}（应进位到 "
            f"{major}.{minor + 1}.01，flowcap-version-sync 规则）"
        )
    return bad


# (文件, 提取方式, 说明)  —— 「说明」标注该文件在版本链路中的角色
TARGETS = [
    ("frontend/package.json", "json", "vite __APP_VERSION__ 的真实来源（前端门禁上报值）"),
    ("src-tauri/tauri.conf.json", "json", "Tauri 应用版本（桌面端 exe）"),
    ("src-tauri/Cargo.toml", "cargo", "Rust crate 版本"),
    ("backend/_build_version.py", "py", "sidecar 内嵌版本（后端 /api/version）"),
    ("package.json", "json", "根 package.json（非前端源，但保持齐平避免混淆）"),
    ("src-tauri/Cargo.lock", "cargo_lock", "Rust Cargo.lock（flowcap 段，构建时自动改写）"),
]


def _extract(path: Path, kind: str) -> str:
    txt = path.read_text(encoding="utf-8")
    if kind == "json":
        return str((json.loads(txt) or {}).get("version") or "")
    if kind == "cargo":
        m = re.search(r'^version\s*=\s*"([^"]+)"', txt, re.M)
        return m.group(1) if m else ""
    if kind == "cargo_lock":
        m = re.search(r'name = "flowcap"\nversion = "([^"]+)"', txt)
        return m.group(1) if m else ""
    if kind == "py":
        m = re.search(r'BUILD_VERSION\s*=\s*"([^"]+)"', txt)
        return m.group(1) if m else ""
    return ""


def main() -> int:
    want = sys.argv[1].strip() if len(sys.argv) > 1 else ""
    found: dict[str, str] = {}
    bad: list[str] = []

    print(f"root = {ROOT}")
    for rel, kind, role in TARGETS:
        p = ROOT / rel
        if not p.exists():
            bad.append(f"缺失文件: {rel}")
            print(f"  [MISS] {rel:34s} 文件不存在")
            continue
        v = _extract(p, kind)
        found[rel] = v
        mark = "OK  "
        if not v:
            mark = "ERR "
            bad.append(f"{rel}: 提取不到 version")
        print(f"  [{mark}] {rel:34s} = {v:10s}  # {role}")

    vals = {v for v in found.values() if v}
    # 2026-09-17 修补（OCR 审查 HIGH）：原实现只判 `len(vals) > 1` 与
    # `vals != {want}` —— 当**五处全部提取不到 version**（文件缺失/字段被
    # 改名）时 vals 为空集，两个条件都不成立 → 直接落到
    # `sorted(vals)[0]` 抛 **IndexError 崩溃**，而不是给出可读的门禁失败。
    # 现显式处理空集：既然前面已把"提取不到"记进 bad，这里只需安全返回。
    if not vals:
        print()
        print("✗ 版本门禁未通过：")
        for b in bad or ["五处均未提取到 version（缺少目标文件或字段被改名）"]:
            print("   -", b)
        return 1
    if len(vals) > 1:
        bad.append(f"版本不一致: {sorted(vals)}")
    if want and vals != {want}:
        bad.append(f"期望 {want}，实际 {sorted(vals)}")

    print()
    if bad:
        print("✗ 版本门禁未通过：")
        for b in bad:
            print("   -", b)
        return 1
    print(f"✓ {len(TARGETS)} 处版本齐平: {sorted(vals)[0]}")
    # 分位数上限（H-33 根治，2026-10-04）：六处齐平后检查 patch ≤ 50。
    # 放在齐平检查**之后**：不一致时先报不一致（更优先的问题）。
    patch_bad = _check_patch_limit(sorted(vals)[0])
    if patch_bad:
        print()
        print("✗ 版本门禁未通过：")
        for b in patch_bad:
            print("   -", b)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
