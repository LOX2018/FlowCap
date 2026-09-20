# -*- coding: utf-8 -*-
"""一键构建 + 部署（含构建类型选择与耗时优化）。

## 背景（2026-09-20 用户拍板）

痛点：全量构建 ~12 分钟，其中 tauri 的 Rust 编译占 ~7 分钟。排查发现
`src-tauri/Cargo.toml` 的 `[profile.release]` 为追求**产物体积**开了：
    codegen-units = 1   → 12 核只跑 1 个 rustc（代码生成无法并行）
    lto = true          → 链接时把全部依赖 crate 拉平重新优化
    opt-level = "s"
即：**产物最小 = 编译最慢**，这是同一枚硬币的两面。

## 本脚本的策略

| 构建类型 | Rust profile | 预估耗时 | 用途 |
|---|---|---|---|
| **debug（默认）** | dev（opt-level=0, 无 LTO, 多 codegen-units） | Rust 阶段 ~1.5min | 测试（用户铁律：默认 debug） |
| release | [profile.release]（保持原样，不降级） | Rust 阶段 ~7min | 正式发布 |

**正式发布路径完全不变** —— 通过 `--release` 显式选择，仍走原优化配置。

## 用法

    python scripts/build_all.py                # 默认 debug：并行 sidecar + tauri --debug + 部署
    python scripts/build_all.py --release      # 正式版（原 release profile）
    python scripts/build_all.py --skip-rust    # 仅 Python/前端改动：跳过 sidecar 重建与 Rust
    python scripts/build_all.py --dry-run      # 只打印计划

## 退出码

0 成功；非 0 = 首个失败步骤的退出码（短路，不掩盖）。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]          # DYAutoDM_v2/
REPO = ROOT.parent                                   # 仓库根
TAURI_DIR = ROOT / "src-tauri"
PY314 = r"C:\Users\LOX\AppData\Local\Programs\Python\Python314\python.exe"
CARGO_BIN = r"C:\Users\LOX\.rustup\toolchains\stable-x86_64-pc-windows-msvc\bin"


def _env() -> dict:
    e = dict(os.environ)
    e["PATH"] = CARGO_BIN + os.pathsep + e.get("PATH", "")
    return e


def _run(cmd: list[str], cwd: Path, log_path: Path | None = None,
         label: str = "") -> int:
    print(f"\n{'='*72}\n▶ {label or ' '.join(cmd)}\n{'='*72}")
    if log_path:
        with open(log_path, "w", encoding="utf-8", errors="replace") as f:
            p = subprocess.run(cmd, cwd=str(cwd), env=_env(),
                               stdout=f, stderr=subprocess.STDOUT)
        tail = ""
        try:
            tail = "\n".join(
                open(log_path, encoding="utf-8", errors="replace")
                .read().splitlines()[-6:])
        except Exception:
            pass
        print(f"  日志: {log_path}")
        if tail.strip():
            print("  尾部输出:")
            for ln in tail.splitlines():
                print("    " + ln.strip())
        return p.returncode
    p = subprocess.run(cmd, cwd=str(cwd), env=_env())
    return p.returncode


def _run_shell(cmd: str, cwd: Path, log_path: Path | None = None,
               label: str = "") -> int:
    """跑一条 shell 命令（Windows 上用 shell=True，让 npx.cmd 等可解析）。"""
    print(f"\n{'='*72}\n▶ {label or cmd}\n{'='*72}")
    if log_path:
        with open(log_path, "w", encoding="utf-8", errors="replace") as f:
            p = subprocess.run(cmd, cwd=str(cwd), env=_env(), shell=True,
                               stdout=f, stderr=subprocess.STDOUT)
        tail = ""
        try:
            tail = "\n".join(
                open(log_path, encoding="utf-8", errors="replace")
                .read().splitlines()[-6:])
        except Exception:
            pass
        print(f"  日志: {log_path}")
        if tail.strip():
            print("  尾部输出:")
            for ln in tail.splitlines():
                print("    " + ln.strip())
        return p.returncode
    p = subprocess.run(cmd, cwd=str(cwd), env=_env(), shell=True)
    return p.returncode


def main() -> int:
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--release", action="store_true",
                    help="构建正式版（使用 [profile.release]；默认 debug）")
    ap.add_argument("--skip-sidecar", action="store_true", help="跳过 sidecar 重建")
    ap.add_argument("--skip-rust", action="store_true", help="跳过 tauri/Rust 构建")
    ap.add_argument("--no-deploy", action="store_true", help="只构建不部署")
    ap.add_argument("--app-root", default=r"C:\temp\dyautodm_design",
                    help="部署目标目录")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划")
    args = ap.parse_args()

    kind = "release" if args.release else "debug"
    rust_flag = [] if args.release else ["--debug"]

    print("=" * 72)
    print(f"  构建类型：{'RELEASE 正式版' if args.release else 'DEBUG 测试版（非正式发布）'}")
    print(f"  sidecar ：{'跳过' if args.skip_sidecar else '并行重建 3 份'}")
    print(f"  Rust    ：{'跳过' if args.skip_rust else ('tauri build --debug（快速）' if not args.release else 'tauri build（release）')}")
    print(f"  部署到  ：{'(跳过)' if args.no_deploy else args.app_root}")
    print("=" * 72)

    if args.dry_run:
        print("\n(--dry-run：未执行任何构建)")
        return 0

    # 1) sidecar（内部已并行；默认 debug）
    if not args.skip_sidecar:
        cmd = [PY314, str(ROOT / "scripts" / "build_sidecar.py"), "--onedir"]
        if args.release:
            cmd.append("--release")
        rc = _run(cmd, ROOT, ROOT.parent / "_build_sidecar.log", "并行重建 sidecar")
        if rc != 0:
            print(f"\n❌ sidecar 构建失败（exit {rc}）")
            return rc
    else:
        print("\n[跳过] sidecar 重建（沿用现有 binaries）")

    # 2) tauri
    if not args.skip_rust:
        # ⚠️ Windows 上 npx 是可执行脚本（npx.cmd），subprocess 不带 shell
        # 直接找 "npx" 会 FileNotFoundError [WinError 2]（实测踩坑：
        # build_all 在 tauri 阶段直接崩，看似"卡住"）。
        _tau_cmd = "npx tauri build --no-bundle" + (" --debug" if not args.release else "")
        rc = _run_shell(_tau_cmd, TAURI_DIR, ROOT.parent / "_build_tauri.log",
                        f"tauri build {'--debug' if not args.release else '(release)'}")
        if rc != 0:
            print(f"\n❌ tauri 构建失败（exit {rc}）")
            return rc
    else:
        print("\n[跳过] tauri/Rust 构建（沿用现有主程序）")

    # 3) 部署
    if not args.no_deploy:
        rc = _run([PY314, str(ROOT / "scripts" / "deploy.py"),
                   "--app-root", args.app_root],
                  ROOT, ROOT.parent / "_deploy.log", f"部署到 {args.app_root}")
        if rc != 0:
            print(f"\n❌ 部署失败（exit {rc}）")
            return rc

    print("\n" + "=" * 72)
    print(f"  ✅ 完成（{kind}）")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
