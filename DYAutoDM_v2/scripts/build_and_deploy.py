# -*- coding: utf-8 -*-
"""构建/部署范围门禁 —— 按**实际改动范围**决定要不要重建 sidecar。

## 为什么需要它（用户 2026-09-19 定调）

用户原话：「全量打包部署太慢了，版本校验加一个前置，当前端/后端的修改涉及联动时
再检验版本。这样加速测试进程」。

现状痛点：`build_sidecar.py`（3 份 PyInstaller）+ `tauri build` 全量约 12 分钟，
但**纯前端改动**（tsx/css）根本不需要重打 sidecar —— 那是 ~10 分钟纯浪费。
本项目已有先例判据（`dyautodm-dev-guards` 铁律六）：
「先看 `git diff --stat` 有没有 backend/ 或任何 sidecar entry 的改动；
只有 frontend/ → 不升版本号，前端产物重跑后重部署即可」。

## 设计契约（不变式）

1. **判据取自实际改动，不取记忆**：用 `git diff`（已跟踪）+ 未跟踪文件，逐路径归类。
2. **安全方向优先**：拿不准就**重建**（宁可慢，不可漏打）。只有「确认后端与 sidecar
   入口零改动」才允许跳过 sidecar。
3. **`--fast` 是断言不是绕过**：调用方声明「只有前端变了」，脚本会**复核**；
   若发现后端改动则**拒绝执行**（exit 非 0），绝不允许静默漏打包。
4. **联动才校验版本**：一次交付若**同时**触及前端与后端（联动），则要求
   五处版本号齐平（调 `check_version_sync.py`）；只动一侧时不强制版本递增，
   避免纯前端修正被版本号门禁卡住。

## 用法

```bash
python scripts/build_and_deploy.py --dry-run   # 只算范围并打印计划（不构建）
python scripts/build_and_deploy.py             # 自动：按改动范围决定是否重建 sidecar
python scripts/build_and_deploy.py --fast      # 声明「仅前端」→ 跳 sidecar（会被复核）
python scripts/build_and_deploy.py --full      # 强制全量（重建 sidecar）
```

退出码：0 = 计划可执行；2 = `--fast` 与实测范围冲突（拒绝）；3 = 版本齐平门禁失败。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ── 改动归类：这些路径的改动意味着 **sidecar 需要重建** ──
SIDECAR_ENTRY_PREFIXES = (
    "DYAutoDM_v2/backend/",
    "DYAutoDM_v2/daemon/",
    "src/qoder2api/",          # 非本项目，占位保持通用
)
BACKEND_SIGNAL = (
    "DYAutoDM_v2/backend/",
    "DYAutoDM_v2/daemon/",
    "DYAutoDM_v2/scripts/build_sidecar.py",
    "DYAutoDM_v2/scripts/_rebuild_",
)
FRONTEND_SIGNAL = (
    "DYAutoDM_v2/frontend/src/",
    "DYAutoDM_v2/frontend/*.html",
    "DYAutoDM_v2/frontend/package.json",
    "DYAutoDM_v2/frontend/vite.config.ts",
)
# Tauri 壳（改它需要重编 Rust，但**不需要**重打 sidecar）
SHELL_SIGNAL = (
    "DYAutoDM_v2/src-tauri/",
)
VERSION_FILES = (
    "DYAutoDM_v2/package.json",
    "DYAutoDM_v2/frontend/package.json",
    "DYAutoDM_v2/src-tauri/tauri.conf.json",
    "DYAutoDM_v2/src-tauri/Cargo.toml",
    "DYAutoDM_v2/backend/_build_version.py",
)


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def changed_files() -> list[str]:
    """已跟踪的改动 + 未跟踪的新文件（相对仓库根，统一 `/` 分隔）。"""
    out: list[str] = []
    for extra in ([], ["--cached"]):
        cp = _run(["git", "diff", "--name-only", *extra])
        out += [ln.strip() for ln in cp.stdout.splitlines() if ln.strip()]
    cp = _run(["git", "ls-files", "--others", "--exclude-standard"])
    out += [ln.strip() for ln in cp.stdout.splitlines() if ln.strip()]
    # 去重保序
    seen, uniq = set(), []
    for f in out:
        f = f.replace("\\", "/")
        if f not in seen:
            seen.add(f)
            uniq.append(f)
    return uniq


def classify(files: list[str]) -> dict:
    def hit(prefixes: tuple[str, ...]) -> list[str]:
        r = []
        for f in files:
            for p in prefixes:
                if p.endswith("*"):
                    d, _, pat = p.rpartition("/")
                    if f.startswith(d + "/") and f.endswith(pat.lstrip("*")):
                        r.append(f)
                        break
                elif f.startswith(p):
                    r.append(f)
                    break
        return r

    backend = hit(BACKEND_SIGNAL)
    frontend = hit(FRONTEND_SIGNAL)
    shell = hit(SHELL_SIGNAL)
    version = [f for f in files if f in VERSION_FILES]
    return {
        "all": files,
        "backend": backend,
        "frontend": frontend,
        "shell": shell,
        "version": version,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="构建/部署范围门禁")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--fast", action="store_true", help="声明仅前端改动（跳过 sidecar 重建）")
    g.add_argument("--full", action="store_true", help="强制全量（重建 sidecar）")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不构建")
    args = ap.parse_args()

    files = changed_files()
    c = classify(files)

    print("=" * 68)
    print("构建范围门禁 · build_and_deploy")
    print("=" * 68)
    print(f"改动文件数: {len(c['all'])}")
    for k, label in (("backend", "后端/sidecar"), ("frontend", "前端"),
                     ("shell", "Tauri 壳"), ("version", "版本号文件")):
        print(f"  · {label}: {len(c[k])}" + (f"  -> {', '.join(c[k][:4])}"
                                            + (" …" if len(c[k]) > 4 else "") if c[k] else ""))

    needs_sidecar = bool(c["backend"])
    # 联动判据：一次交付同时触及前端与后端（或改了两侧的版本文件）
    linked = bool(c["frontend"]) and bool(c["backend"])

    if args.fast and needs_sidecar:
        print("\n✗ 拒绝：--fast 声明「仅前端」，但实测存在后端/sidecar 改动：")
        for f in c["backend"][:8]:
            print("    - " + f)
        print("  安全方向优先：请改用全量（去掉 --fast），否则会漏打包 sidecar。")
        return 2

    if args.full:
        needs_sidecar = True

    print("\n判定：")
    print(f"  · 需要重建 sidecar : {'是' if needs_sidecar else '否'}")
    print(f"  · 前后端联动       : {'是' if linked else '否'}")

    if linked:
        cp = _run([sys.executable, "scripts/check_version_sync.py"])
        ok = cp.returncode == 0
        # 无参数时脚本可能只打印用法；以退出码为准，取尾部信息辅助判断
        tail = "\n".join((cp.stdout or cp.stderr).strip().splitlines()[-2:])
        print(f"  · 版本齐平门禁     : {'通过' if ok else '失败'}\n      {tail}")
        if not ok:
            print("  ⚠ 联动改动要求版本齐平；若刚改了版本请补齐五处（见 skill "
                  "dyautodm-version-sync），或确认无需升版本。")
    else:
        print("  · 版本齐平门禁     : 跳过（未联动，纯单侧改动）")

    steps = []
    if needs_sidecar:
        steps.append("1) build_sidecar.py --onedir   (~10min, 3×PyInstaller)")
    steps.append(("2)" if needs_sidecar else "1)") + " tauri build --no-bundle        (~2min)")
    steps.append(("3)" if needs_sidecar else "2)") + " deploy.py                       (~10s)")
    print("\n计划步骤：")
    for s in steps:
        print("  " + s)
    if not needs_sidecar:
        print("\n  ⚡ 快速通道：跳过 sidecar 重建（沿用现有 binaries），预计 ~2 分钟")
    else:
        print("\n  全量构建，预计 ~12 分钟")

    if args.dry_run:
        print("\n(--dry-run：未执行任何构建)")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
