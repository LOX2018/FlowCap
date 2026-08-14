"""
PyInstaller 打包脚本：把 backend 打包成 Tauri sidecar 二进制

产出三个二进制（放到 src-tauri/binaries/）：
- dyautodm-backend.exe           (FastAPI 主后端)
- dyautodm-browser-daemon.exe    (凭证守护)
- dyautodm-recv-daemon.exe       (私信接收守护)

Tauri 2 的 externalBin 要求文件名带 target-triple 后缀，例如
dyautodm-backend-x86_64-pc-windows-msvc.exe。本脚本在打包后自动为每个
二进制创建带后缀的硬链接（同盘符不占额外空间），链接失败则退化为复制。
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
BINARIES = ROOT / "src-tauri" / "binaries"

EXT = ".exe" if platform.system() == "Windows" else ""


def _target_triple() -> str:
    """返回当前平台的 Rust target triple（与 Tauri externalBin 命名一致）。"""
    sys_name = platform.system()
    machine = platform.machine().lower()
    if sys_name == "Windows":
        arch = "aarch64" if "arm" in machine or "aarch" in machine else "x86_64"
        return f"{arch}-pc-windows-msvc"
    if sys_name == "Darwin":
        arch = "aarch64" if machine == "arm64" else "x86_64"
        return f"{arch}-apple-darwin"
    arch = "aarch64" if "arm" in machine or "aarch" in machine else "x86_64"
    return f"{arch}-unknown-linux-gnu"


def _link_target_triple(src: Path) -> None:
    """为 src 创建带 target-triple 后缀的硬链接（失败则复制）。"""
    triple = _target_triple()
    dst = src.with_name(f"{src.stem}-{triple}{src.suffix}")
    if dst.exists():
        dst.unlink()
    try:
        os.link(src, dst)  # 硬链接，同盘符不占额外空间
        print(f"硬链接: {dst.name} -> {src.name}")
    except OSError:
        shutil.copy2(src, dst)  # 跨盘符或权限不足时退化为复制
        print(f"复制: {dst.name} (硬链接失败，退化为复制)")


def _runtime_resources() -> list[str]:
    """返回需要随 sidecar 一起打包的运行时资源（源码态项目根下的目录/文件）。

    这些资源（指纹内核、浏览器 profile、.env、logs）按 app_root() 解析，
    打包态 app_root() = exe 所在目录，因此必须用 --add-data 把它们收集进去，
    否则 sidecar 运行时会因找不到 vb_chromium 而 RuntimeError（禁止回退原生 Playwright）。
    """
    items = [
        "vb_chromium",
        "vb_profile_default",
        "vb_profile_dm",
        "pw_profile_dm",
        ".env",
        "logs",
    ]
    args: list[str] = []
    sep = ";" if platform.system() == "Windows" else ":"
    for it in items:
        src = ROOT / it
        if src.exists():
            # DEST 用 "." —— PyInstaller 会把这些目录/文件解压到 exe 所在目录（即 app_root() 打包态）
            args += ["--add-data", f"{src}{sep}."]
        else:
            print(f"[warn] 运行时资源不存在，跳过: {src}")
    return args


def build_one(entry: str, name: str) -> None:
    """打包单个 sidecar 并创建 target-triple 后缀链接"""
    print(f"\n=== 打包 {name} ===")
    BINARIES.mkdir(parents=True, exist_ok=True)
    out = BINARIES / f"{name}{EXT}"
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--name", name,
        "--distpath", str(BINARIES),
        "--workpath", str(BACKEND / "build" / name),
        "--specpath", str(BACKEND / "build" / name),
        "--clean", "--noconfirm",
    ]
    cmd += _runtime_resources()
    cmd += [str(BACKEND / entry)]
    print(" ".join(cmd))
    subprocess.check_call(cmd, cwd=str(BACKEND))
    print(f"产出: {out}")
    _link_target_triple(out)


def main() -> None:
    build_one("main.py", "dyautodm-backend")
    build_one("daemon/browser_daemon.py", "dyautodm-browser-daemon")
    build_one("daemon/recv_daemon.py", "dyautodm-recv-daemon")
    print("\n全部打包完成，二进制位于:", BINARIES)


if __name__ == "__main__":
    main()

