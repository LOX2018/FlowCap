# -*- coding: utf-8 -*-
"""正式安装包出包入口（MSI/NSIS）—— 把「正确的打包方式」固化成一条命令 + 出包自证。

## 为什么需要它（2026-09-29 事故）

`build_all.py` 走的是 `tauri build --no-bundle`（只出前后端，**不出安装包**），
所以「安装包路径」此前**没有任何脚本承载**，只能手敲命令 —— 于是三条只在
「真打包 + 真安装」时暴露的缺陷（externalBin 不带 contents 目录 / 目录平铺 /
MSI 装只读 Program Files）全部潜伏到用户装机才爆发。

本脚本把正确方式固化为：**契约门禁 → sidecar → 安装包 → 出包自证**，一条命令跑完。

## 用法

    python scripts/package_installer.py                 # 默认 msi
    python scripts/package_installer.py --targets msi,nsis
    python scripts/package_installer.py --skip-sidecar  # 沿用现有 binaries
    python scripts/package_installer.py --no-verify     # 跳过出包后自证

## 出包后自证（fail-closed：验不过即非零退出）

  ① 安装包存在且 ProductVersion == 源码版本；
  ② WiX 源（`target/release/wix/**/main.wxs`）中 contents 目录与三个 profile 均为
     **安装根下的具名子目录**（不是 `_up_`，不是平铺）——这是 sidecar 能启动、
     profile 能被 `resource_root()` 找到的机械前提；
  ③ 打印 sha256 / 大小 / 路径，便于归档与用户核对。

## 已知环境事实（固化，不靠记忆）

  - `tauri build` 只认 `bundle.targets`（命令行无 --bundles）⇒ 本脚本会**临时改写**
    `tauri.conf.json` 的 targets 为请求值，跑完**恢复原值**（try/finally）。
  - Rust 需在 PATH：`%USERPROFILE%\\.rustup\\toolchains\\stable-x86_64-pc-windows-msvc\\bin`。
  - WiX 会为**数千个 profile 文件**做 ICE 校验，耗时可达 5~15 分钟（属正常，勿中断）。
    本脚本不设超时（交由调用方/CI 的超时控制）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]            # DYAutoDM_v2/
sys.path.insert(0, str(Path(__file__).resolve().parent))  # scripts/ -> build_paths (SSOT)
from build_paths import target_dir, exe_path, wix_dir, msi_path, nsis_path  # noqa: E402
TAURI_DIR = ROOT / "src-tauri"
CONF = TAURI_DIR / "tauri.conf.json"
PY314 = r"C:\Users\LOX\AppData\Local\Programs\Python\Python314\python.exe"
CARGO_BIN = r"C:\Users\LOX\.rustup\toolchains\stable-x86_64-pc-windows-msvc\bin"
TRIPLE = "x86_64-pc-windows-msvc"


def _log(msg: str) -> None:
    print(msg, flush=True)


def _env() -> dict:
    e = dict(os.environ)
    e["PATH"] = CARGO_BIN + os.pathsep + e.get("PATH", "")
    return e


def _run(cmd, cwd, label, log_path=None) -> int:
    _log(f"\n{'='*72}\n▶ {label}\n{'='*72}")
    if log_path:
        with open(log_path, "w", encoding="utf-8", errors="replace") as f:
            p = subprocess.run(cmd, cwd=str(cwd), env=_env(), shell=isinstance(cmd, str),
                               stdout=f, stderr=subprocess.STDOUT)
        try:
            tail = "\n".join(open(log_path, encoding="utf-8", errors="replace")
                             .read().splitlines()[-6:])
            if tail.strip():
                _log("  尾部输出:")
                for ln in tail.splitlines():
                    _log("    " + ln.strip())
        except Exception:
            pass
        return p.returncode
    return subprocess.run(cmd, cwd=str(cwd), env=_env(),
                          shell=isinstance(cmd, str)).returncode


def _ver() -> str:
    return json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["version"]


def _contents_dir_name() -> str:
    src = (ROOT / "scripts" / "build_sidecar.py").read_text(encoding="utf-8")
    m = re.search(r'CONTENTS_DIR\s*=\s*"([^"]+)"', src)
    return m.group(1) if m else "appinternals"


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


# ── 出包后自证 ────────────────────────────────────────────────────────────
def _msi_product_version(msi: Path) -> str | None:
    """用 MSI 数据库读 ProductVersion（COM，逐行取值以免解析异常）。"""
    ps = (
        "$ErrorActionPreference='Stop';"
        f"$db=(New-Object -ComObject WindowsInstaller.Installer).GetType().InvokeMember("
        f"'OpenDatabase','InvokeMethod',$null,(New-Object -ComObject WindowsInstaller.Installer),"
        f"@('{msi}',0));"
        "$v=$db.GetType().InvokeMember('OpenView','InvokeMethod',$null,$db,"
        "@(\"SELECT `Value` FROM `Property` WHERE `Property`='ProductVersion'\"));"
        "$v.GetType().InvokeMember('Execute','InvokeMethod',$null,$v,$null);"
        "$r=$v.GetType().InvokeMember('Fetch','InvokeMethod',$null,$v,$null);"
        "if($r){$r.GetType().InvokeMember('StringData','GetProperty',$null,$r,1)}"
    )
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                             capture_output=True, text=True, timeout=60)
        v = (out.stdout or "").strip()
        return v or None
    except Exception:
        return None


def _verify(msi: Path, expect_ver: str) -> int:
    _log(f"\n{'='*72}\n▶ 出包后自证\n{'='*72}")
    failures = []

    if not msi.is_file():
        failures.append(f"安装包不存在: {msi}")
    else:
        size = msi.stat().st_size
        _log(f"  产物: {msi}")
        _log(f"  大小: {size:,} B")
        _log(f"  sha256: {_sha256(msi)}")
        pv = _msi_product_version(msi)
        if pv == expect_ver:
            _log(f"  ✅ ProductVersion = {pv}")
        else:
            failures.append(f"ProductVersion 不符：MSI={pv!r} 期望={expect_ver!r}")

    # WiX 源布局自证（sidecar 能启动的机械前提）
    wix_dirs = sorted(wix_dir().glob("**/main.wxs"))
    if not wix_dirs:
        _log("  ⚠️ 未找到 main.wxs（若 targets 不含 msi 属正常）")
    else:
        w = wix_dirs[-1].read_text(encoding="utf-8", errors="replace")
        cname = _contents_dir_name()
        ne = len(re.findall(r'Name="[^"]*"', w))
        checks = {
            "contents 目录落为具名子目录": f'Name="{cname}"' in w,
            "无 $RESOURCE/_up_ 目录（profile 平铺回落）": 'Name="_up_"' not in w,
            # L-16（2026-09-29）：profile 含真实登录凭证，**不得**随包分发。
            "无随包 profile（不泄露登录凭证）":
                not any(f'Name="{p}"' in w for p in
                        ("vb_profile_default", "vb_profile_dm", "pw_profile_dm")),
        }
        for name, ok in checks.items():
            _log(f"  {'✅' if ok else '❌'} {name}")
            if not ok:
                failures.append(f"WiX 布局自证失败：{name}（main.wxs={wix_dirs[-1]}）")

    if failures:
        _log("\n⛔ 自证未通过：")
        for f in failures:
            _log(f"  - {f}")
        return 1
    _log("\n✅ 自证通过")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--targets", default="msi",
                    help="安装包类型，逗号分隔（msi / nsis；默认 msi）")
    ap.add_argument("--skip-sidecar", action="store_true", help="跳过 sidecar 重建")
    ap.add_argument("--no-verify", action="store_true", help="跳过出包后自证")
    args = ap.parse_args()

    targets = [t.strip() for t in args.targets.split(",") if t.strip()]
    if not targets:
        _log("❌ --targets 为空")
        return 2
    ver = _ver()
    cname = _contents_dir_name()

    _log("=" * 72)
    _log(f"  正式安装包出包 · v{ver} · targets={targets}")
    _log(f"  sidecar ：{'跳过' if args.skip_sidecar else '重建 3 份（contents=' + cname + '）'}")
    _log("=" * 72)

    # 0) 契约门禁（fail fast，免得白等十几分钟）
    rc = _run([PY314, str(ROOT / "scripts" / "check_packaging_contract.py")],
              ROOT, "打包契约门禁 check_packaging_contract")
    if rc != 0:
        _log("\n❌ 打包契约门禁未通过 —— 先修契约再出包（详见上方 FAIL 项）")
        return rc

    # 1) sidecar（onedir + release；contents 目录名由 build_sidecar 的 CONTENTS_DIR 决定）
    if not args.skip_sidecar:
        rc = _run([PY314, str(ROOT / "scripts" / "build_sidecar.py"),
                   "--onedir", "--release"],
                  ROOT, "重建 sidecar（onedir/release）",
                  ROOT.parent / "_package_sidecar.log")
        if rc != 0:
            _log(f"\n❌ sidecar 构建失败（exit {rc}）")
            return rc

    # 2) 临时改写 bundle.targets → 跑 tauri build → 恢复（try/finally）
    orig = CONF.read_text(encoding="utf-8")
    conf = json.loads(orig)
    conf.setdefault("bundle", {})["targets"] = targets
    CONF.write_text(json.dumps(conf, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        rc = _run("npx tauri build", TAURI_DIR,
                  f"tauri build（bundles={'+'.join(targets)}）",
                  ROOT.parent / "_package_tauri.log")
    finally:
        CONF.write_text(orig, encoding="utf-8")
        _log("  [已恢复 tauri.conf.json 原 targets]")
    if rc != 0:
        _log(f"\n❌ tauri build 失败（exit {rc}）")
        return rc

    if args.no_verify:
        return 0

    # 3) 出包后自证
    if "msi" in targets:
        msi = msi_path(ver)
        return _verify(msi, ver)
    if "nsis" in targets:
        exe = nsis_path(ver)
        _log(f"\n产物: {exe}  存在={exe.is_file()}  "
             f"大小={exe.stat().st_size if exe.is_file() else 0:,} B")
        return 0 if exe.is_file() else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
