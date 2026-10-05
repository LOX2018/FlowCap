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
sys.path.insert(0, str(Path(__file__).resolve().parent))  # scripts/
from build_paths import target_dir  # noqa: E402
REPO = ROOT.parent                                   # 仓库根
TAURI_DIR = ROOT / "src-tauri"
PY314 = r"C:\Users\LOX\AppData\Local\Programs\Python\Python314\python.exe"
CARGO_BIN = r"C:\Users\LOX\.rustup\toolchains\stable-x86_64-pc-windows-msvc\bin"


def _env() -> dict:
    e = dict(os.environ)
    e["PATH"] = CARGO_BIN + os.pathsep + e.get("PATH", "")
    # 2026-09-29 用户拍板：构建缓存**显式**重定向出源码树（源码树零构建产物）。
    # 与 src-tauri/.cargo/config.toml 的 target-dir 同源（经 build_paths 解析，
    # 路径字面量只在 config.toml 一处）。实测 target/ 会单调膨胀（incremental
    # 每次重编新增 ~460M 且旧份不回收；一次构建即 4.0G、历史峰值 42G）。
    e["CARGO_TARGET_DIR"] = str(target_dir())
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
    ap.add_argument("--allow-stale", action="store_true",
                    help="显式放行陈旧的 --skip-*（认账：沿用与当前源码不符的产物）")
    ap.add_argument("--prune-cache", action="store_true",
                    help="构建+部署完成后回收构建缓存（cargo clean；默认关闭，"
                         "保留增量编译加速）")
    args = ap.parse_args()

    kind = "release" if args.release else "debug"
    rust_flag = [] if args.release else ["--debug"]

    # ★ 2026-10-04：`--skip-*` 从「声明」升级为「**必须用来源戳自证**」。
    #
    #   为什么：`--skip-sidecar` 原先只凭调用方一句声明就跳过 —— 若本次实际改了
    #   `backend/`，产物就是**陈旧的**，而版本号门禁**拦不住**（它只比版本号，
    #   不比代码）。现在改为：跳过前先算「当前源码戳」，与上次构建记录比对；
    #   不一致 ⇒ **拒绝跳过**（要么去掉 --skip-*，要么显式 --allow-stale 认账）。
    #
    #   这正好回答「不校对版本号，如何确认非陈旧」：版本号证明**声明一致**，
    #   来源戳证明**是这份代码**。两者分工不同，不是替代关系。
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import build_stamp as _bs
    except Exception as _e:  # noqa: BLE001
        _bs = None
        print(f"⚠️ 无法加载 build_stamp（跳过陈旧性校验）: {_e}")

    # 定位 sidecar 产物（与 build_sidecar.py 的命名规则一致：<name>-<triple>.exe）
    BINARIES = ROOT / "src-tauri" / "binaries"
    _EXT = ".exe" if os.name == "nt" else ""

    def _triple_of() -> str:
        import platform
        m = platform.machine().lower()
        arch = {"amd64": "x86_64", "x86_64": "x86_64", "arm64": "aarch64"}.get(m, m)
        if os.name == "nt":
            return f"{arch}-pc-windows-msvc"
        if sys.platform == "darwin":
            return f"{arch}-apple-darwin"
        return f"{arch}-unknown-linux-gnu"

    def _stale_guard(kind_name: str) -> bool:
        """返回 True = 允许跳过；False = 应拒绝（陈旧 / 产物不符 / 无记录）。

        🔴 组合校验（源码戳 **且** 产物 md5）：只验源码戳时，一条伪造记录
        （只写 stamp）就能骗过门禁（实测踩到）。见 build_stamp.verify 的说明。
        """
        if _bs is None:
            print(f"  ⚠️ {kind_name}：无法校验来源戳，按**安全方向**视为需重建")
            return False
        try:
            from build_paths import exe_path as _exe_path
            if kind_name == "sidecar":
                _triple = _triple_of()
                _arts = [BINARIES / f"{n}-{_triple}{_EXT}"
                         for n in ("dyautodm-backend", "dyautodm-browser-daemon",
                                   "dyautodm-recv-daemon")]
            else:
                _arts = [_exe_path("release" if args.release else "debug")]
        except Exception as _e:  # noqa: BLE001
            print(f"  ⚠️ {kind_name}：无法定位产物（{_e}），视为需重建")
            return False
        r = _bs.verify(kind_name, _arts)
        if r["ok"]:
            print(f"  ✅ {kind_name} 可安全跳过（{r['files']} 文件，"
                  f"源码戳 {r['current'][:12]}… + 产物 md5 一致）")
            return True
        print(f"  ❌ {kind_name} 不可跳过：{r['reason']}")
        return False

    print("=" * 72)
    print(f"  构建类型：{'RELEASE 正式版' if args.release else 'DEBUG 测试版（非正式发布）'}")
    print(f"  sidecar ：{'跳过' if args.skip_sidecar else '并行重建 3 份'}")
    print(f"  Rust    ：{'跳过' if args.skip_rust else ('tauri build --debug（快速）' if not args.release else 'tauri build（release）')}")
    print(f"  部署到  ：{'(跳过)' if args.no_deploy else args.app_root}")
    print(f"  产物根  ：{target_dir()}（源码树外）")
    print("=" * 72)

    if args.dry_run:
        print("\n(--dry-run：未执行任何构建)")
        return 0

    # 陈旧性门禁：拒绝「跳过一个其实已经变了的部分」
    if args.skip_sidecar and not _stale_guard("sidecar") and not args.allow_stale:
        print("\n❌ 拒绝 --skip-sidecar：sidecar 源码已改动或从无构建记录。")
        print("   正解：去掉 --skip-sidecar 重建（~3.5 分钟），")
        print("   或若确实要沿用现有产物，显式加 --allow-stale 认账。")
        return 2
    if args.skip_rust and not _stale_guard("rust") and not args.allow_stale:
        print("\n❌ 拒绝 --skip-rust：前端/Rust 源码已改动或从无构建记录。")
        print("   正解：去掉 --skip-rust 重建（~1.5 分钟），或显式加 --allow-stale 认账。")
        return 2

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
        # ★ 2026-10-04：tauri 成功后记录 rust 侧来源戳 + 主程序 md5（供 --skip-rust 判定）。
        try:
            from build_paths import exe_path as _exe_path
            _art = _exe_path("release" if args.release else "debug")
            _ver = ""
            try:
                import re as _re
                _pkg = (ROOT / "frontend" / "package.json").read_text(encoding="utf-8")
                _ver = (_re.search(r'"version"\s*:\s*"([^"]+)"', _pkg) or [None, ""])[1]
            except Exception:
                pass
            _rec = _bs.record_build("rust", [_art], version=_ver)
            print(f"[戳] 已记录 rust 来源戳 = {_rec['stamp'][:16]}…"
                  f"（{_rec['files']} 个文件）")
        except Exception as _e:  # noqa: BLE001
            print(f"[戳] rust 记录失败（下次将判『无构建记录』）: {_e}")
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

    # 4) 可选：回收构建缓存（默认关闭；必须在部署之后执行）
    if args.prune_cache:
        rc = _run(["cargo", "clean", "--manifest-path", str(TAURI_DIR / "Cargo.toml")],
                  ROOT, None, "cargo clean（--prune-cache）")
        if rc != 0:
            print(f"  ⚠️ cargo clean 返回 {rc}（不影响已完成的构建/部署）")
        else:
            print(f"  ✅ 已回收构建缓存：{target_dir()}")

    print("\n" + "=" * 72)
    print(f"  ✅ 完成（{kind}）")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
