#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""统一部署脚本 —— 根治「假版本」（文件名与内容版本不一致）。

## 为什么需要（用户 2026-09-13 质问）

用户原话：「你在构建的版本号和部署的版本号是同一个版本吗？会不会出现
         调用版本号不一致的问题？还是说你版本号这个地方没有进行配置化处理？」

事实核查结论：**存在假版本** ——
    主程序 exe 的 Windows 资源版本 = 0.43.0（构建于 19:34）
    而部署文件名却叫 DYAutoDM_v2_0.43.1.exe（版本号 19:51 才改成 0.43.1）
根因：exe 内嵌的前端 bundle 与版本号都在**构建期固化**，改了版本号不重新构建，
      产物内容仍是旧版；而部署时按「我以为的版本」命名 → 名字与内容不符。

## 本脚本的规则（把「版本一致」变成机械可验证，而不是靠人记）

1. 期望版本 = `src-tauri/tauri.conf.json` 的 version（项目唯一真源）
2. 主程序 exe 的 **Windows 资源版本必须 == 期望版本**，否则**拒绝部署**并
   提示「请执行完整重建」（这就在部署关口挡住了假版本）
3. 部署文件名一律用**产物的真实版本**（读 exe 资源），不用调用方口述的版本
4. 三份 sidecar：构建产物 md5 必须 == 部署产物 md5（逐一致）
5. sidecar 的编译期版本常量 `_build_version.py` 必须 == 期望版本
6. 部署完成后打印验收命令（curl /api/version 应返回期望版本）

用法：
    python scripts/deploy.py [--app-root C:\\temp\\dyautodm_test] [--dry-run]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TRIPLE = "x86_64-pc-windows-msvc"
SIDECARS = [
    "dyautodm-backend",
    "dyautodm-browser-daemon",
    "dyautodm-recv-daemon",
]


def log(msg: str) -> None:
    print(msg, flush=True)


def _md5(p: Path) -> str:
    h = hashlib.md5()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def expected_version() -> str:
    conf = ROOT / "src-tauri" / "tauri.conf.json"
    with open(conf, encoding="utf-8") as f:
        return str((json.load(f) or {}).get("version") or "")


def exe_file_version(p: Path) -> str:
    """读 exe 的 Windows 资源版本（这是产物自述版本，不是文件名）。"""
    if not p.is_file():
        return ""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"(Get-Item '{p}').VersionInfo.FileVersion"],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=30)
        return (out.stdout or "").strip()
    except Exception:
        return ""


def build_version_const() -> str:
    """读 sidecar 的编译期版本常量（构建时由 build_sidecar.py 写入）。"""
    fp = ROOT / "backend" / "_build_version.py"
    if not fp.is_file():
        return ""
    for line in fp.read_text(encoding="utf-8").splitlines():
        if line.startswith("BUILD_VERSION"):
            return line.split("=", 1)[1].strip().strip('"')
    return ""


def norm(v: str) -> str:
    """归一化版本：tauri 写 0.43.1，Windows 资源可能是 0.43.1.0。

    ⚠️ 2026-09-14 修复（v0.43.10 部署文件名被截成 0.43.1）：
    原实现用 `rstrip(".0")` —— **按字符集**删除尾部字符，会把 legit 的
    尾零一起吃掉：`"0.43.10".rstrip(".0")` → `"0.43.1"`（末尾 0 被当填充删了），
    于是部署文件名/比对都错成 0.43.1，与真实版本 0.43.10 不符。
    正确做法：只剥掉**完整的 `.0` 四段后缀**（0.43.1.0 → 0.43.1），
    绝不按字符集裁剪。
    """
    _s = (v or "").strip()
    if not _s:
        return _s
    # 仅在形如 X.Y.Z.0（四段且末段为 0）时剥掉最后一段
    _parts = _s.split(".")
    if len(_parts) == 4 and _parts[3] == "0":
        _s = ".".join(_parts[:3])
    return _s


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--app-root", default=os.environ.get("DY_APP_ROOT")
                    or r"C:\temp\dyautodm_test")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    app_root = Path(args.app_root)

    exp = expected_version()
    log("=" * 74)
    log("[部署] 期望版本（tauri.conf.json 真源） = %s" % exp)
    log("[部署] 应用根目录 = %s" % app_root)
    log("=" * 74)

    # ---- 校验 1：主程序 exe 资源版本 ----
    exe_src = ROOT / "src-tauri" / "target" / "release" / "dyautodm-v2.exe"
    real = exe_file_version(exe_src)
    log("\n[校验1] 主程序 exe 真实资源版本 = %s" % (real or "(读不到)"))
    if not exe_src.is_file():
        log("  ❌ 未找到构建产物：%s" % exe_src)
        return 2
    if norm(real) != norm(exp):
        log("  ❌ 假版本拦截：exe 资源版本(%s) ≠ 期望版本(%s)" % (real, exp))
        log("     exe 内嵌前端 bundle 与版本号都在构建期固化，改版本号【必须完整重建】：")
        log('       export PATH="/c/Users/LOX/.rustup/toolchains/stable-x86_64-pc-windows-msvc/bin:$PATH"')
        log("       npx tauri build --no-bundle")
        return 3
    log("  ✅ 一致（部署文件名将用产物真实版本，而非人工指定）")

    # ---- 校验 2：sidecar 编译期版本常量 ----
    bc = build_version_const()
    log("\n[校验2] sidecar 编译期版本常量 = %s" % (bc or "(缺失)"))
    if norm(bc) != norm(exp):
        log("  ❌ 不一致：需重新打包 sidecar（scripts/build_sidecar.py）")
        return 4
    log("  ✅ 一致")

    # ---- 校验 3：三份 sidecar 构建产物存在 ----
    log("\n[校验3] sidecar 构建产物")
    bins = ROOT / "src-tauri" / "binaries"
    for name in SIDECARS:
        full = f"{name}-{TRIPLE}"
        d = bins / full
        f = d / f"{full}.exe"
        if not f.is_file():
            log("  ❌ 缺失：%s" % f)
            return 5
        log("  ✅ %-46s %s" % (full[:46], _md5(f)[:12]))

    if args.dry_run:
        log("\n[dry-run] 校验全部通过，未执行部署")
        return 0

    # ---- 部署 ----
    log("\n[部署] 开始")
    if not app_root.is_dir():
        log("  ❌ 应用根目录不存在：%s" % app_root)
        return 6

    # 主程序：用【产物真实版本】命名
    dst_exe = app_root / f"DYAutoDM_v2_{norm(real)}.exe"
    shutil.copy2(exe_src, dst_exe)
    log("  ✅ 主程序 → %s (md5=%s)" % (dst_exe.name, _md5(dst_exe)[:12]))
    # 清理同名旧版本
    for p in app_root.glob("DYAutoDM_v2_*.exe"):
        if p.name != dst_exe.name:
            try:
                p.unlink()
                log("     🗑 移除旧版本 %s" % p.name)
            except Exception as e:
                log("     ⚠️ 移除 %s 失败：%s" % (p.name, str(e)[:60]))

    # sidecar：md5 必须一致
    ok_all = True
    for name in SIDECARS:
        full = f"{name}-{TRIPLE}"
        src = bins / full
        dst = app_root / full
        if dst.exists():
            shutil.rmtree(dst, ignore_errors=True)
        shutil.copytree(src, dst)
        a, b = _md5(src / f"{full}.exe"), _md5(dst / f"{full}.exe")
        same = a == b
        ok_all = ok_all and same
        log("  %s %-46s md5=%s" % ("✅" if same else "❌", full[:46], b[:12]))

    log("\n" + "=" * 74)
    if ok_all:
        log("[部署] 完成。验收命令（启动应用后执行）：")
        log('  curl -s http://127.0.0.1:8000/api/version')
        log('  → 期望返回 {"backend":"%s", ...}' % exp)
    else:
        log("[部署] ⚠️ 存在 md5 不一致的 sidecar，请检查是否被进程占用")
    log("=" * 74)
    return 0 if ok_all else 7


if __name__ == "__main__":
    sys.exit(main())
