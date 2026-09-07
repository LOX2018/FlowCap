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
    # 2026-09-05：wp_recv 是 main.py 里 asyncio 动态导入的模块
    # （from daemon.wp_recv import run_wp_recv_loop 写在 lifespan 内部），
    # PyInstaller 静态分析扫不到，必须显式 hidden-import，
    # 否则打包后 WP 通道启动失败（ModuleNotFoundError）。
    if entry == "main.py":
        cmd += ["--hidden-import", "daemon.wp_recv"]
        # 2026-09-07：AI 知识库文件导入用了 UploadFile/Form，python-multipart
        # 是隐式依赖（PyInstaller 扫不到），缺失时 backend 启动即崩
        # （RuntimeError: Form data requires "python-multipart"）。
        cmd += ["--hidden-import", "multipart"]
        cmd += ["--hidden-import", "python_multipart"]
    cmd += [str(BACKEND / entry)]
    print(" ".join(cmd))
    subprocess.check_call(cmd, cwd=str(BACKEND))
    print(f"产出: {out}")


def inject_test_whitelist() -> None:
    """【调试版专用】把测试账号白名单注入 services/dm_dispatch.py。

    仅在显式传 `--debug-whitelist` 时执行。产出的二进制**禁止对外发布**：
    它限制私信只能发给白名单账号，是测试期防误发真人的护栏。

    正式版：**不调用本函数** → 模块内 _TEST_WHITELIST 保持空壳、
    TEST_WHITELIST_ON 恒 False → 白名单逻辑物理不执行。
    """
    import re
    from pathlib import Path

    # 两个测试账号的**私信会话 uid**（不是探活 uid！）。
    # 实测：四川工伤张老师「探活 uid=4175297014664416」与「会话 uid=
    # 3887506227210423」**不一致**（两套 uid 体系，日志持续报 uid 漂移）。
    # 白名单比对的是**发送目标的 peer_uid**（来自 conv_id，属会话体系），
    # 故此处必须用会话 uid，用探活 uid 会误拒（2026-09-07 真机踩坑）。
    WL = {
        "尚进工伤小助理": "3887506227210423",   # 张老师作为对端的会话 uid
        "四川工伤张老师": "316276709526638",   # 尚进作为对端的会话 uid
    }
    target = Path(__file__).resolve().parent.parent / "backend" / "services" / "dm_dispatch.py"
    src = target.read_text(encoding="utf-8")
    start = "# ---DM_TEST_WHITELIST_INJECT_START---"
    end = "# ---DM_TEST_WHITELIST_INJECT_END---"
    if start not in src or end not in src:
        print("[warn] 未找到注入标记，跳过白名单注入")
        return
    body = (
        f'_TEST_WHITELIST = {{\n'
        f'    "{WL and list(WL)[0]}": {{"{WL[list(WL)[1]]}"}},\n'
        f'    "{list(WL)[1]}": {{"{WL[list(WL)[0]]}"}},\n'
        f'}}\n'
        f'TEST_WHITELIST_ON = True\n'
    )
    new = re.sub(re.escape(start) + r".*?" + re.escape(end),
                 start + "\n" + body + end, src, flags=re.S)
    target.write_text(new, encoding="utf-8")
    print(f"[debug] 已注入测试白名单（仅调试版）: {WL}")


def restore_whitelist() -> None:
    """打包后把注入还原（避免污染工作区源码 → 正式版不含白名单）。"""
    import subprocess
    try:
        subprocess.run(["git", "checkout", "--",
                        "backend/services/dm_dispatch.py"],
                       cwd=str(Path(__file__).resolve().parent.parent),
                       capture_output=True)
        print("[debug] 已还原 dm_dispatch.py（工作区恢复为正式版空壳）")
    except Exception as e:
        print(f"[warn] 还原失败（请手动 git checkout）: {e}")


def main() -> None:
    import sys
    debug_wl = "--debug-whitelist" in sys.argv
    if debug_wl:
        inject_test_whitelist()
    try:
        build_one("main.py", "dyautodm-backend")
        build_one("daemon/browser_daemon.py", "dyautodm-browser-daemon")
        build_one("daemon/recv_daemon.py", "dyautodm-recv-daemon")
        print("\n全部打包完成，二进制位于:", BINARIES)
        if debug_wl:
            print("[警告] 本次为**调试版**构建（含测试白名单限制），"
                  "禁止对外发布！")
    finally:
        if debug_wl:
            restore_whitelist()


if __name__ == "__main__":
    main()

