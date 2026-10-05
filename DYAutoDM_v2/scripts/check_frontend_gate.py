#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""前端门禁管线（独立职责域）—— 类型检查 + 构建产物体积。

职责域（与 check_iron_rules.py 分离）：
  check_iron_rules.py 管数据契约 / 环境 / 凭证 / 文案
  本脚本          管前端构建（tsc -b → vite build → chunk 体积）

设计原则：
  1. 单一入口，一条命令搞定前端全部门禁
  2. fail-closed：任何阶段失败即阻断
  3. 自包含：不依赖 check_iron_rules.py 的内部逻辑
  4. 可单独运行，也可由 pre-commit hook 调用

阶段：
  ① tsc -b --force      → 类型检查（~10s）
  ② vite build          → 确保 dist 是源码的新鲜构建（~5s）
  ③ chunk 体积扫描     → 非白名单域 > 50KB 阻断（~1s）

用法：
  python scripts/check_frontend_gate.py            # 完整门禁（阻断）
  python scripts/check_frontend_gate.py --dry-run  # 只跑 ①②，不阻断 ③
  python scripts/check_frontend_gate.py --quiet    # 只输出结论行

退出码：
  0 = 全部通过
  1 = 有阻断（类型错误 / 构建失败 / 超限）
  2 = 环境异常（node_modules 缺失等）
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

# ── 阈值 ──────────────────────────────────────────────────────────────────
CHUNK_MAX_B = 51_200  # 50KB

# 存量超限域白名单（已确认的拆页目标，暂不阻断）
# 格式：{"domain_name": current_size_bytes}
# 新增超限域或白名单外超限 ⇒ 阻断
WHITELIST = {
    "accounts":  64_345,  # dist/assets/accounts-page-*.js
    "live":      71_133,  # dist/assets/live-page-*.js
    "messages":  52_828,  # dist/assets/messages-page-*.js
    "platform":  56_669,  # dist/assets/platform-page-*.js
    "settings": 126_435,  # dist/assets/settings-page-*.js
}

# 白名单域在门禁中的显示名
WHITELIST_LABELS = {
    "accounts": "accounts",
    "live": "live",
    "messages": "messages",
    "platform": "platform",
    "settings": "settings",
}

# ── 仓库根定位 ──────────────────────────────────────────────────────────
def _repo_root() -> Path:
    """从脚本位置往上找仓库根（含 frontend/package.json 的目录）。"""
    here = Path(__file__).resolve().parent
    p = here
    for _ in range(5):
        if (p / "frontend" / "package.json").exists():
            return p
        p = p.parent
    return here.parent  # 回退：scripts/ 的上一级


REPO = _repo_root()
FE = REPO / "frontend"


# ── 阶段 ①：tsc -b 类型检查 ───────────────────────────────────────────
def stage_tsc() -> tuple[bool, str]:
    """返回 (pass, detail)。"""
    # Windows: npx/npm 是 .cmd，需 shell=True 才能解析
    shell = os.name == "nt"
    cmd = "npx tsc -b --force" if shell else ["npx", "tsc", "-b", "--force"]
    try:
        r = subprocess.run(
            cmd, cwd=str(FE), capture_output=True, text=True, timeout=60,
            shell=shell,
        )
        if r.returncode == 0:
            return True, "tsc -b 通过"
        # 提取错误行（前 5 条）
        lines = [l for l in r.stdout.splitlines() if "error TS" in l]
        detail = lines[:5]
        if detail:
            err_lines = "\n".join(f"    {l.strip()}" for l in detail)
            return False, f"tsc -b 失败 ({len(lines)} 个类型错误)\n{err_lines}"
        return False, f"tsc -b 失败 (exit {r.returncode})"
    except subprocess.TimeoutExpired:
        return False, "tsc -b 超时 (>60s)"
    except FileNotFoundError:
        return False, "npx 不可用（node_modules 缺失？）"


# ── 阶段 ①·乙：vitest 单元测试 ───────────────────────────────────────────
def stage_test() -> tuple[bool, str]:
    """跑前端单元测试（拆分安全网）。

    无测试文件时视为「不适用」而非失败 —— 避免在建网过渡期误阻提交，
    但一旦有测试就必须全绿。
    """
    has_tests = sorted((FE / "src").rglob("*.test.ts")) or sorted(
        (FE / "src").rglob("*.test.tsx")
    )
    if not has_tests:
        return True, "无测试文件（跳过）"

    shell = os.name == "nt"
    cmd = "npx vitest run" if shell else ["npx", "vitest", "run"]
    try:
        r = subprocess.run(
            cmd, cwd=str(FE), capture_output=True, text=True, timeout=300,
            shell=shell,
        )
        if r.returncode == 0:
            # 提取 pass 计数
            for l in r.stdout.splitlines():
                if "Tests" in l and "passed" in l:
                    return True, f"vitest 通过 ({l.strip()})"
            return True, "vitest 通过"
        fail_lines = [
            l for l in r.stdout.splitlines()
            if "✗" in l or "×" in l or "FAIL" in l
        ][:5]
        detail = "\n".join(f"    {l.strip()}" for l in fail_lines)
        return False, f"vitest 失败 (exit {r.returncode})\n{detail}"
    except subprocess.TimeoutExpired:
        return False, "vitest 超时 (>300s)"
    except FileNotFoundError:
        return False, "npx 不可用（node_modules 缺失？）"


# ── 阶段 ②：vite build ────────────────────────────────────────────────
def stage_vite_build() -> tuple[bool, str]:
    """返回 (pass, detail)。"""
    # Windows: npx/npm 是 .cmd，需 shell=True 才能解析
    shell = os.name == "nt"
    cmd = "npx vite build" if shell else ["npx", "vite", "build"]
    try:
        r = subprocess.run(
            cmd, cwd=str(FE), capture_output=True, text=True, timeout=60,
            shell=shell,
        )
        if r.returncode == 0:
            # 提取构建耗时
            for l in r.stdout.splitlines():
                if "built in" in l:
                    return True, f"vite build 通过 ({l.strip()})"
            return True, "vite build 通过"
        stderr_lines = r.stderr.strip().splitlines()[-5:] if r.stderr.strip() else []
        detail = "\n".join(f"    {l}" for l in stderr_lines)
        return False, f"vite build 失败 (exit {r.returncode})\n{detail}"
    except subprocess.TimeoutExpired:
        return False, "vite build 超时 (>60s)"
    except FileNotFoundError:
        return False, "npx 不可用（node_modules 缺失？）"


# ── 阶段 ③：chunk 体积扫描 ─────────────────────────────────────────────
def stage_chunk_scan() -> tuple[bool, str]:
    """扫描 dist/assets/*.js，检查非白名单域超限。"""
    dist = FE / "dist" / "assets"
    if not dist.is_dir():
        return False, "dist/assets/ 不存在（dist 陈旧或从未构建）"

    js_files = sorted(dist.glob("*.js"))
    if not js_files:
        return False, "dist/assets/ 内无 .js 文件（构建异常）"

    # 解析每个 chunk 的 domain 名和大小
    domain_sizes: dict[str, int] = {}
    for f in js_files:
        name = f.stem  # 例：accounts-page-CCoQUabJ
        # 提取 domain（accounts-page-* → accounts）
        domain = None
        for dl in WHITELIST_LABELS:
            if name.startswith(dl):
                domain = dl
                break
        if domain is None:
            # 非白名单域：按 <domain>-page 前缀匹配
            # 或提取 -page- 前的部分
            parts = name.split("-page-")
            if len(parts) == 2:
                domain = parts[0]
        if domain is None:
            # 非 page chunk（如 vendor-react, index 等），跳过
            continue

        size = f.stat().st_size
        domain_sizes[domain] = max(domain_sizes.get(domain, 0), size)

    # 分类：白名单域 / 超限域 / 合规域
    over_limit = []  # 非白名单超限
    whitelisted = []  # 白名单内（报告但不阻断）
    compliant = []  # 合规

    for dom, size in sorted(domain_sizes.items()):
        if dom in WHITELIST:
            wl_size = WHITELIST[dom]
            ratio = size / wl_size * 100 if wl_size else 0
            if size > CHUNK_MAX_B:
                whitelisted.append(f"{dom}={size:,}B ({ratio:.0f}% of baseline)")
            else:
                compliant.append(f"{dom}={size:,}B")
        else:
            if size > CHUNK_MAX_B:
                over_limit.append(f"{dom}={size:,}B")
            else:
                compliant.append(f"{dom}={size:,}B")

    # 构建报告
    parts = []
    if whitelisted:
        parts.append(f"  ⚠ 白名单内 ({len(whitelisted)}): " + ", ".join(whitelisted))
    if compliant:
        parts.append(f"  ✓ 合规 ({len(compliant)}): " + ", ".join(compliant))
    if over_limit:
        parts.append(f"  ✗ 超限 ({len(over_limit)}): " + ", ".join(over_limit))

    detail = "\n".join(parts) if parts else "  无 chunk 数据"

    if over_limit:
        return False, f"R17-FAIL {len(over_limit)} 域超限\n{detail}"
    return True, f"R17-PASS\n{detail}"


# ── 主流程 ──────────────────────────────────────────────────────────────
def run(args: argparse.Namespace) -> int:
    results = []  # (stage_name, pass, detail)

    # 前置检查
    if not FE.is_dir():
        print(f"[FAIL] frontend/ 目录不存在: {FE}", file=sys.stderr)
        return 2
    if not (FE / "node_modules").is_dir():
        print(f"[FAIL] node_modules 缺失，请先运行 npm install", file=sys.stderr)
        return 2

    # 阶段 ① tsc
    ok, detail = stage_tsc()
    results.append(("tsc", ok, detail))
    if args.quiet:
        status = "PASS" if ok else "FAIL"
        print(f"[{status}] tsc -b")
    if not ok and not args.dry_run:
        print(f"\n[ABORT] tsc -b 失败，跳过后续阶段")
        print(f"  {detail}")
        return 1

    # 阶段 ①·乙 vitest（拆分安全网）
    ok, detail = stage_test()
    results.append(("test", ok, detail))
    if args.quiet:
        status = "PASS" if ok else "FAIL"
        print(f"[{status}] vitest")
    if not ok and not args.dry_run:
        print(f"\n[ABORT] vitest 失败，跳过后续阶段")
        print(f"  {detail}")
        return 1

    # 阶段 ② vite build
    ok, detail = stage_vite_build()
    results.append(("vite", ok, detail))
    if args.quiet:
        status = "PASS" if ok else "FAIL"
        print(f"[{status}] vite build")
    if not ok and not args.dry_run:
        print(f"\n[ABORT] vite build 失败，跳过后续阶段")
        print(f"  {detail}")
        return 1

    # 阶段 ③ chunk 扫描
    ok, detail = stage_chunk_scan()
    results.append(("chunks", ok, detail))

    if args.quiet:
        status = "PASS" if ok else "FAIL"
        print(f"[{status}] chunk scan ({CHUNK_MAX_B:,} B)")
    else:
        print(f"  {detail}")

    # 总结
    if not args.quiet:
        all_ok = all(r[1] for r in results)
        n_pass = sum(1 for r in results if r[1])
        n_fail = sum(1 for r in results if not r[1])
        if all_ok:
            print(f"\n✓ 前端门禁通过 ({n_pass}/{len(results)} 阶段)")
        else:
            print(f"\n✗ 前端门禁失败 ({n_pass}/{len(results)} 通过, {n_fail}/{len(results)} 失败)")

    if args.dry_run:
        if not args.quiet:
            print(f"\n[DRY-RUN] 仅执行了阶段 ①②，未阻断阶段 ③")
        return 0

    if not any(r[1] for r in results if r[0] == "chunks"):
        return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="前端门禁管线")
    ap.add_argument("--dry-run", action="store_true",
                    help="只跑 tsc + vite build，不阻断 chunk 超限")
    ap.add_argument("--quiet", action="store_true",
                    help="只输出结论行，不输出详情")
    args = ap.parse_args()
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
