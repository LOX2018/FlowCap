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
    """不再创建无 triple 别名：Tauri externalBin 要求文件名带 triple 后缀，
    无 triple 的同名 .exe 会与 triple 文件冲突导致 externalBin 嵌入失败。
    本函数保留为空操作（兼容旧调用），实际产物名已由 build_one 直接带 triple。"""
    return


def _runtime_resources() -> list[str]:
    """返回需要随 sidecar 一起 --add-data 打包的运行时资源。

    重要：PyInstaller onefile 的 --add-data 会把资源解压到 sys._MEIPASS
    临时目录，而 vbrowser.app_root() 在打包态返回 dirname(sys.executable)
    （exe 旁边）。两者不一致，--add-data 的资源按 app_root() 解析不到。

    因此 vb_chromium / vb_profile_* / pw_profile_dm / .env / logs 等
    运行时资源【不应打进 sidecar】，而应放 sidecar exe 旁边（由 Tauri
    bundle.resources 分发或 build 后手动复制）。这里返回空列表。
    """
    return []


def build_one(entry: str, name: str) -> None:
    """打包单个 sidecar，产物名直接带 target-triple 后缀（Tauri externalBin 要求）。"""
    triple = _target_triple()
    full = f"{name}-{triple}"
    print(f"\n=== 打包 {name} (-> {full}{EXT}) ===")
    BINARIES.mkdir(parents=True, exist_ok=True)
    out = BINARIES / f"{full}{EXT}"
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--name", full,
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


def main() -> None:
    build_one("main.py", "dyautodm-backend")
    build_one("daemon/browser_daemon.py", "dyautodm-browser-daemon")
    build_one("daemon/recv_daemon.py", "dyautodm-recv-daemon")
    print("\n全部打包完成，二进制位于:", BINARIES)


if __name__ == "__main__":
    main()

