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
sys.path.insert(0, str(Path(__file__).resolve().parent))  # scripts/ -> build_paths (SSOT)
from build_paths import target_dir, exe_path, wix_dir, msi_path, nsis_path  # noqa: E402
TRIPLE = "x86_64-pc-windows-msvc"
SIDECARS = [
    "dyautodm-backend",
    "dyautodm-browser-daemon",
    "dyautodm-recv-daemon",
]

# ── 2026-09-15 分支环境隔离（铁律：隔离须固化为代码，不靠记忆）────────────
# 本分支 design/better-douyin 的独立部署环境；主分支环境 C:\temp\dyautodm_test
# 由主分支使用，本分支**绝不写入**（历史上曾误用主分支环境致污染）。
DEFAULT_APP_ROOT = r"C:\temp\dyautodm_design"
FORBIDDEN_ROOTS = (r"C:\temp\dyautodm_test",)


def branch_guard(app_root: Path, allow_foreign: bool = False) -> bool:
    """拒绝把本分支产物部署进主分支环境（除非显式放行）。"""
    norm = str(app_root).replace("/", "\\").rstrip("\\").lower()
    for bad in FORBIDDEN_ROOTS:
        if norm == bad.replace("/", "\\").rstrip("\\").lower():
            if allow_foreign:
                log("  ⚠️ --allow-foreign-root 已显式放行主分支环境：%s" % app_root)
                return True
            log("\n[隔离门禁] ❌ 拒绝部署：目标指向主分支环境 %s" % app_root)
            log("  本分支（design/better-douyin）环境 = %s" % DEFAULT_APP_ROOT)
            log("  如确要部署到该目录，请显式加 --allow-foreign-root。")
            return False
    return True


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


def build_kind_const() -> str:
    """读 sidecar 的构建类型（debug/release；缺省按 debug —— 用户铁律）。"""
    fp = ROOT / "backend" / "_build_version.py"
    if not fp.is_file():
        return "debug"
    for line in fp.read_text(encoding="utf-8").splitlines():
        if line.startswith("BUILD_KIND"):
            v = line.split("=", 1)[1].split("#")[0].strip().strip('"').strip("'")
            return v if v in ("debug", "release") else "debug"
    return "debug"


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


def _dir_size_mb(p) -> str:
    """目录体积展示（仅用于日志，失败不抛）。"""
    try:
        from pathlib import Path as _P
        tot = sum(f.stat().st_size for f in _P(p).rglob("*") if f.is_file())
        return "%.1f MB" % (tot / 1048576)
    except Exception:
        return "?"


def find_blocking_processes(app_root: "Path") -> list:
    """列出**占用部署目录**的进程（Windows）。

    2026-09-22 加固：旧逻辑先 `rmtree(dst_i, ignore_errors=True)` 再 `copytree`
    —— 应用若在运行（exe / 共享 _internal 被占用），rmtree 会**静默失败或删一半**，
    紧接着 copytree 必抛 FileExistsError，部署目录便停在「半新半旧」
    （实测：_internal 7004→73 个文件、主 exe 新旧并存）。根因是**缺少前置占用检查**。
    返回 [] 表示未检出占用；psutil 缺失时降级为宽松放行（调用处打印降级提示）。
    """
    out: list = []
    try:
        import psutil  # type: ignore
    except Exception:
        return out
    try:
        target = str(Path(app_root).resolve()).lower()
    except Exception:
        target = str(app_root).lower()
    for proc in psutil.process_iter(["pid", "name", "exe"]):
        try:
            exe = (proc.info.get("exe") or "")
            if exe and str(Path(exe).resolve()).lower().startswith(target):
                out.append((proc.info.get("pid"), proc.info.get("name"), exe))
        except Exception:
            continue
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    # 2026-09-15 隔离门禁（用户铁律：分支环境隔离须固化为代码，不靠记忆）。
    # 默认值按**当前分支**给出，并**拒绝写入非本分支环境**（见下 branch_guard）。
    # 显式配置原则：仍可用 --app-root / DY_APP_ROOT 覆盖，不自动探测本机状态。
    ap.add_argument("--app-root", default=os.environ.get("DY_APP_ROOT")
                    or DEFAULT_APP_ROOT)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--allow-foreign-root", action="store_true",
                    help="显式放行非本分支默认环境（一般不要用；防止误部署到主分支环境）")
    args = ap.parse_args()
    app_root = Path(args.app_root)

    if not branch_guard(app_root, allow_foreign=args.allow_foreign_root):
        return 9

    exp = expected_version()
    log("=" * 74)
    log("[部署] 期望版本（tauri.conf.json 真源） = %s" % exp)
    log("[部署] 应用根目录 = %s" % app_root)
    log("=" * 74)

    # ---- 校验 1：主程序 exe 资源版本 ----
    # 主程序产物路径：auto 探测 debug / release，取**较新**者。
    # 2026-09-20：引入 `tauri build --debug`（快速测试构建，Rust 阶段 ~1.5min
    # 对比 release 的 ~7min，原因是 [profile.release] 开了 lto+codegen-units=1）
    # → 产物落在 target/debug/ 而非 target/release/。
    # 判据用 mtime 而非「优先 debug」：避免 debug 残留旧产物盖过新 release。
    # 2026-09-29：产物根经 build_paths 解析（构建缓存已迁出源码树）。
    _rel = exe_path("release")
    _dbg = exe_path("debug")
    _cands = [p for p in (_rel, _dbg) if p.is_file()]
    if not _cands:
        log("  ❌ 未找到主程序产物（debug/release 均不存在）")
        log(f"     尝试过: {_rel}")
        log(f"              {_dbg}")
        return 7
    exe_src = max(_cands, key=lambda p: p.stat().st_mtime)
    log("  主程序产物: %s (%s)" % (
        exe_src.parent.name, "release" if exe_src is _rel else "debug"))
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
    # 2026-09-14 性能主线：产物有两种形态，都要支持
    #   A) 共享形态（新，默认）：binaries/_internal + binaries/<full>.exe 平铺
    #   B) 传统形态（旧）：binaries/<full>/ （目录内含 exe + _internal）
    log("\n[校验3] sidecar 构建产物")
    bins = ROOT / "src-tauri" / "binaries"
    # 2026-09-29：contents 目录名优先新值 appinternals（避免 WiX 剥下划线），
    # 兼容旧产物 _internal。
    _contents = "appinternals" if (bins / "appinternals").is_dir() else "_internal"
    shared_internal = (bins / _contents).is_dir()
    layout = "共享 _internal（三 exe 平铺）" if shared_internal else "传统（每份独立目录）"
    log("  形态：%s" % layout)
    for name in SIDECARS:
        full = f"{name}-{TRIPLE}"
        cands = [bins / f"{full}.exe", bins / full / f"{full}.exe"]
        f = next((c for c in cands if c.is_file()), None)
        if f is None:
            log("  ❌ 缺失：%s（尝试 %s）" % (full, " / ".join(str(c) for c in cands)))
            return 5
        # 2026-09-15 平铺/子目录 双份并存且不一致 → 拒绝（防部署到旧产物）。
        # 背景：`build_one(onedir)` 单独调用时只产出 <full>/ 子目录，平铺 <full>.exe
        #       仍是**上一次**的旧物；deploy 又优先取平铺 → 会部署旧二进制，
        #       且 md5 校验因「源==源」而照样通过（实测连验 3 次都跑旧 exe）。
        flat, sub = bins / f"{full}.exe", bins / full / f"{full}.exe"
        if flat.is_file() and sub.is_file() and _md5(flat) != _md5(sub):
            log("  ❌ 产物自相矛盾：平铺与子目录 exe 不一致")
            log("     平铺  %s %s" % (_md5(flat)[:12], flat))
            log("     子目录 %s %s" % (_md5(sub)[:12], sub))
            log("     → 请执行完整 `python scripts/build_sidecar.py --onedir`（含 dedupe）重建")
            return 8
        log("  ✅ %-46s %s" % (full[:46], _md5(f)[:12]))

    # ---- 占用预检（2026-09-22 加固：必须在**任何写操作之前**）----
    blockers = find_blocking_processes(app_root)
    if blockers:
        log("\n[占用检测] ❌ 部署目录被进程占用，拒绝部署（避免半新半旧）：")
        for pid, name, exe in blockers:
            log("    PID %-7s %-40s %s" % (pid, (name or "")[:40], exe))
        log("  → 请先优雅停止应用（各守护 POST /quit + 关主窗口）再重跑本脚本。")
        return 9
    log("\n[占用检测] ✅ 无进程占用部署目录")

    if args.dry_run:
        log("\n[dry-run] 校验全部通过，未执行部署")
        return 0

    # ---- 部署 ----
    log("\n[部署] 开始")
    if not app_root.is_dir():
        log("  ❌ 应用根目录不存在：%s" % app_root)
        return 6

    # 主程序：用【产物真实版本 + 构建类型】命名
    #   2026-09-20 用户铁律（选 A 方案）：默认 debug 版；debug 产物名带 -debug 后缀，
    #   正式版不带。这样「一眼可辨」，杜绝把 debug 包当正式包发出去。
    _kind = build_kind_const()
    _suffix = "-debug" if _kind == "debug" else ""
    dst_exe = app_root / f"DYAutoDM_v2_{norm(real)}{_suffix}.exe"
    shutil.copy2(exe_src, dst_exe)
    log("  ✅ 主程序 → %s (md5=%s) [%s]" % (dst_exe.name, _md5(dst_exe)[:12], _kind))
    # 清理旧版本：**只清理同类型**（debug 不删 release，反之亦然），
    # 避免交叉删除把另一种构建误删。
    is_debug_target = _kind == "debug"
    for p in app_root.glob("DYAutoDM_v2_*.exe"):
        if p.name == dst_exe.name:
            continue
        p_is_debug = p.stem.endswith("-debug")
        if p_is_debug != is_debug_target:
            continue  # 另一种构建类型，保留
        try:
            p.unlink()
            log("     🗑 移除同类旧版本 %s" % p.name)
        except Exception as e:
            log("     ⚠️ 移除 %s 失败：%s" % (p.name, str(e)[:60]))

    # sidecar：md5 必须一致
    # 2026-09-14：共享形态（_internal 一份 + 三 exe 平铺）与传统形态都要支持。
    ok_all = True
    if shared_internal:
        # 共享依赖：只拷一次（contents 目录名 = appinternals / 兼容 _internal）
        src_i = bins / _contents
        dst_i = app_root / _contents
        if dst_i.exists():
            # 2026-09-22 加固：**不得 ignore_errors**（旧写法会静默删一半后仍继续，
            # 制造「半新半旧」）。删除失败即报错并拒绝继续。
            try:
                shutil.rmtree(dst_i)
            except Exception as e:  # noqa: BLE001
                log("  ❌ 未能清空旧 _internal（%s）—— 部署目录可能被占用，已中止部署"
                    % str(e)[:100])
                return 9
            if dst_i.exists():
                log("  ❌ 旧 _internal 删除后仍存在—— 已中止部署")
                return 9
        shutil.copytree(src_i, dst_i, dirs_exist_ok=True)
        log("  ✅ %-46s %s" % ("_internal（共享依赖，唯一一份）", _dir_size_mb(dst_i)))
    for name in SIDECARS:
        full = f"{name}-{TRIPLE}"
        # 源：共享形态在 binaries 根；传统形态在 binaries/<full>/
        src_exe = bins / f"{full}.exe"
        if not src_exe.is_file():
            src_exe = bins / full / f"{full}.exe"
        if shared_internal:
            dst_exe = app_root / f"{full}.exe"
            # 2026-09-17 修补（真实故障 —— 旧子目录遮蔽新产物，桌面端跑旧版）：
            # `sidecar.rs::resolve_sidecar` 的候选顺序是
            #   ① `<root>/<full>/<full>.exe`（目录形态，**优先**）
            #   ② `<root>/<full>.exe`（平铺）
            # 而共享形态下这里**只覆盖平铺 exe**，从不清理可能存在的
            # `<root>/<full>/` 子目录 —— 历史"传统形态"部署留下的旧目录
            # （每份约 280MB，含自己的 `_internal`）会**接管解析**，
            # 于是应用启动的仍是旧版本（实测：部署 0.43.71 平铺新产物，
            # 桌面端 `/api/version` 报 0.43.42 —— 启动的是 02:43 的旧子目录）。
            # 这正是本脚本"根治假版本"契约要防的情形，故在此显式清除。
            _stale = app_root / full
            if _stale.is_dir():
                # ⚠️ 不能 ignore_errors=True —— 实测曾出现"内容删净但目录残留"
                # 时被**静默吞掉**，导致遮蔽依旧存在却毫无提示。删完必须复核。
                _err = None
                try:
                    shutil.rmtree(_stale)
                except Exception as e:  # noqa: BLE001
                    _err = e
                if _stale.exists():
                    log("     ❌ 未能移除遮蔽目录 %s（%s）—— 该子目录优先于平铺 exe，"
                        "会导致启动旧版本！请手动删除后重跑。" % (full, _err))
                    ok_all = False
                else:
                    log("     🗑 移除遮蔽新版本的旧 sidecar 目录 %s" % full)
            shutil.copy2(src_exe, dst_exe)
        else:
            dst_dir = app_root / full
            if dst_dir.exists():
                shutil.rmtree(dst_dir, ignore_errors=True)
            shutil.copytree(bins / full, dst_dir)
            dst_exe = dst_dir / f"{full}.exe"
        same = _md5(src_exe) == _md5(dst_exe)
        ok_all = ok_all and same
        log("  %s %-46s md5=%s" % ("✅" if same else "❌", full[:46], _md5(dst_exe)[:12]))

    # ── 部署副本「自描述数据根」+ 启动器（2026-09-29 用户拍板方案②）────────────
    # 背景：副本被「双击 exe」启动时没有 DY_APP_ROOT ⇒ vbrowser.app_root() 回落到
    #   %LOCALAPPDATA%\DYAutoDM（那个空根）⇒ 会员注册表为空 ⇒ 登录报**误导性**的
    #   「用户名或口令错误」（实测口令哈希完全匹配）。见 backend/vbrowser.py::_deploy_declared_root。
    # 处置：① 写 exe 同目录的声明文件（frozen 启动时优先读它 ⇒ 双击即用）；
    #       ② 写启动器（显式注入 DY_APP_ROOT，开关可复现）。
    if ok_all:
        try:
            _marker = app_root / "dyautodm_app_root.txt"
            _marker.write_text(str(app_root.resolve()) + "\n", encoding="utf-8")
            log("  ✅ 数据根声明 → %s（双击 exe 即用本环境）" % _marker.name)
            _launcher = app_root / "启动.cmd"
            _exe_name = dst_exe.name
            _launcher.write_text(
                (chr(13) + chr(10)).join([
                    "@echo off",
                    "rem DYAutoDM 测试副本启动器（由 deploy.py 生成）——显式注入本环境数据根",
                    "rem 以免未设 DY_APP_ROOT 时落到 LOCALAPPDATA 下的空根",
                    "chcp 65001 >nul",
                    'set "DY_APP_ROOT=%~dp0."',
                    f'start "" "%~dp0{_exe_name}"',
                    "",
                ]),
                encoding="utf-8",
            )
            log("  ✅ 启动器 → %s（双击即用 · 数据根已显式注入）" % _launcher.name)
        except Exception as e:  # noqa: BLE001
            log("  ⚠️ 写数据根声明/启动器失败（不影响主部署，但双击 exe 需自备 DY_APP_ROOT）: %s"
                % str(e)[:80])

    log("\n" + "=" * 74)
    if ok_all:
        log("[部署] 完成。启动方式（任选其一）：")
        log("  · 双击同目录的「启动.cmd」（推荐 —— 已显式注入本环境数据根）")
        log("  · 或直接双击 %s（同目录的 dyautodm_app_root.txt 会声明数据根）" % dst_exe.name)
        log("验收命令（启动应用后执行）：")
        log('  curl -s http://127.0.0.1:8000/api/version')
        log('  → 期望返回 {"backend":"%s", ...}' % exp)
    else:
        log("[部署] ⚠️ 存在 md5 不一致的 sidecar，请检查是否被进程占用")
    log("=" * 74)
    return 0 if ok_all else 7


if __name__ == "__main__":
    sys.exit(main())
