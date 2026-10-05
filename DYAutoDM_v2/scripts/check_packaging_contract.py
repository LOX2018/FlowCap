# -*- coding: utf-8 -*-
"""打包契约门禁（安装包）—— 把「正确的打包方式」做成机械判据，不靠记忆。

## 为什么存在（2026-09-29 实测事故，v0.45.97）

首次打正式 MSI 后，跨设备安装**必然卡门禁**（前端停在「正在唤醒后端引擎」，后端从不监听 8000）。
三条根因**都只在「真打包 + 真安装」时暴露**，源码态/部署态全部正常：

  ① **`bundle.externalBin` 只带 exe 单文件**，PyInstaller onedir 的 contents 目录
     （`python314.dll` + 全部 pyd/dll）不进安装包 ⇒ sidecar 启动即
     `[PYI-xxxx:ERROR] Failed to load Python DLL`（exit 127）。
  ② **WiX 会把以 `_` 开头的目录名规范化**（实测 `_internal` → `internal`）⇒ 即便带上，
     目录名不符，PyInstaller 启动器仍找不到 ⇒ 同样崩。**内容目录名必须不带下划线。**
  ③ **资源映射 target 为空串会把目录内容平铺到安装根**（`python314.dll` 落根、
     profile 内容散落）⇒ 必须映射到**同名子目录**。
  ④ **Tauri 的 MSI(WiX) 无 per-user 选项 ⇒ 恒 `ALLUSERS=1` 装 `C:\\Program Files`（只读）**，
     而应用把随附资源与运行期数据共用一个根 ⇒ `[DB-005] 拒绝访问 ...data`（表面能连、实际不落库）。
     正解：`app_root()`（可写数据根）与 `resource_root()`（只读安装根）**分离**，
     运行期 profile 首次从资源根**种子化**到数据根。

本门禁把这些约束**机械断言**在源文件上（不依赖构建产物），任何一条被改回去即 FAIL。
另有 `scripts/package_installer.py` 负责「按正确方式出包 + 出包后自证布局」。

用法：
    python scripts/check_packaging_contract.py          # 人类可读
    python scripts/check_packaging_contract.py --json    # 机器可读

退出码：0 = 全通过；1 = 有未通过项。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # DYAutoDM_v2/
RESULTS: list[dict] = []


def _ok(cid: str, name: str, detail: str = "") -> None:
    RESULTS.append({"id": cid, "name": name, "ok": True, "detail": detail})


def _fail(cid: str, name: str, detail: str = "") -> None:
    RESULTS.append({"id": cid, "name": name, "ok": False, "detail": detail})


def _read(rel: str) -> str:
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def _load_json(rel: str) -> dict:
    return json.loads(_read(rel))


# ── P1 内容目录名不得以下划线开头（WiX 会剥下划线）────────────────────────
def check_contents_dir_name() -> None:
    src = _read(os.path.join("scripts", "build_sidecar.py"))
    m = re.search(r'CONTENTS_DIR\s*=\s*"([^"]+)"', src)
    if not m:
        _fail("P1", "sidecar contents 目录名（常量）",
              'scripts/build_sidecar.py 未定义 CONTENTS_DIR 常量')
        return
    name = m.group(1)
    if name.startswith("_"):
        _fail("P1", "sidecar contents 目录名不得以 _ 开头",
              f'CONTENTS_DIR="{name}" —— WiX(MSI) 会剥掉前导下划线，'
              f'安装后目录名不符 ⇒ sidecar 加载 pythonXX.dll 失败')
        return
    # 必须真的用于 PyInstaller
    if f'"--contents-directory", "{name}"' not in src and \
       f"'--contents-directory', '{name}'" not in src:
        _fail("P1", "sidecar contents 目录名已接线",
              f'CONTENTS_DIR="{name}" 未传给 PyInstaller --contents-directory')
        return
    _ok("P1", f'sidecar contents 目录名 = "{name}"（无下划线，且已接线）')


# ── P2 tauri.conf.json：内容目录 + profile 的映射 ──────────────────────────
def check_bundle_resources() -> None:
    cfg = _load_json(os.path.join("src-tauri", "tauri.conf.json"))
    bundle = cfg.get("bundle") or {}
    res = bundle.get("resources")
    targets = bundle.get("targets") or []

    if not targets:
        _fail("P2", "bundle.targets 非空", "targets 为空 ⇒ 不会产出任何安装包")
    else:
        _ok("P2", f'bundle.targets = {targets}')

    if not isinstance(res, dict):
        _fail("P3", "bundle.resources 为 map 形式（可指定落地路径）",
              "resources 不是 map ⇒ 无法指定子目录，目录内容会被平铺到安装根")
        return

    # 3.1 contents 目录映射到同名子目录（且源来自 binaries/）
    src = _read(os.path.join("scripts", "build_sidecar.py"))
    m = re.search(r'CONTENTS_DIR\s*=\s*"([^"]+)"', src)
    cname = m.group(1) if m else "appinternals"
    src_key = f"binaries/{cname}"
    tgt = res.get(src_key)
    if tgt != f"{cname}/":
        _fail("P3", f"contents 目录映射到子目录 {cname}/",
              f'resources[{src_key!r}] = {tgt!r} —— 应为 "{cname}/"；'
              f'为空串或缺失 ⇒ 内容平铺到安装根 ⇒ sidecar 找不到 {cname}/pythonXX.dll')
    else:
        _ok("P3", f'contents 映射 {src_key} → {tgt}')

    # 3.2 🔴 不得随包分发任何 browser profile（L-16，2026-09-29 拍板）
    # 理由：profile 目录含**真实抖音登录凭证**（sessionid/sid_guard/sid_tt/uid_tt）。
    # 打包进 MSI = 把账号登录态随分发包外发（凭证泄露）。首次运行由浏览器自行
    # 生成空 profile（resolve_profile_dir 无 seed 源时返回 app_root 下路径），
    # 登录由用户现场扫码完成。
    profiles = ["vb_profile_default", "vb_profile_dm", "pw_profile_dm"]
    leaked = []
    for p in profiles:
        for k in (f"../{p}", p, f"{p}/"):
            if k in res:
                leaked.append(k)
    if leaked:
        _fail("P4", "不得随包分发 browser profile（含登录凭证）",
              f"bundle.resources 仍含 {leaked} —— profile 含真实登录 cookie，"
              f"随包外发 = 凭证泄露；应从 resources 移除")
    else:
        _ok("P4", "无随包 profile（不泄露登录凭证）")

    # 3.3 不得残留指向已废弃 _internal 的映射（该目录不再产出 ⇒ 构建直接报错）
    stale = [k for k in res if k.endswith("_internal")]
    if stale:
        _fail("P5", "无指向已废弃 _internal 的资源映射",
              f"仍存在 {stale} —— PyInstaller 已改产 {cname}/，该路径不存在 ⇒ "
              f"`resource path ... doesn't exist` 构建失败")
    else:
        _ok("P5", "无残留 _internal 资源映射")


# ── P6 数据根/资源根分离（可写性）─────────────────────────────────────────
def check_root_split() -> None:
    src = _read(os.path.join("backend", "vbrowser.py"))
    if "def resource_root(" not in src:
        _fail("P6", "vbrowser.resource_root() 存在", "未定义 resource_root()")
    else:
        _ok("P6", "vbrowser.resource_root() 存在")

    if "def resolve_profile_dir(" not in src:
        _fail("P7", "vbrowser.resolve_profile_dir() 存在", "未定义 resolve_profile_dir()")
    else:
        _ok("P7", "vbrowser.resolve_profile_dir() 存在")

    # app_root() frozen 分支**不得**返回安装目录（否则只读安装位置下 DB 写不进去）。
    # 逐行收敛冻结分支（从 `frozen` 判定行起，遇到下一个 def/空行段即止）。
    lines = src.splitlines()
    fseg_lines: list[str] = []
    grp = False
    for i, ln in enumerate(lines):
        if 'def app_root(' in ln:
            grp = True
            continue
        if grp:
            if ln.startswith('def ') or ln.startswith('@'):
                break
            fseg_lines.append(ln)
            if 'def ' in ln and 'app_root' not in ln and ln.startswith('    def '):
                break
    fseg = "\n".join(fseg_lines)
    # 只取 frozen 判定之后的段
    if 'frozen' in fseg:
        fseg = fseg[fseg.index('frozen'):]
    if "_install_dir()" in fseg or "os.path.dirname(exe_dir)" in fseg:
        _fail("P8", "冻结态 app_root() 与安装目录解耦",
              "app_root() 的 frozen 分支引用了安装目录/exe_dir ⇒ MSI 装 Program Files（只读）后 "
              "data/ 会 [DB-005] 拒绝访问")
    elif "_default_data_root()" in fseg or "LOCALAPPDATA" in fseg:
        _ok("P8", "冻结态 app_root() 指向用户可写数据根")
    else:
        _fail("P8", "冻结态 app_root() 指向用户可写数据根",
              "无法确认 frozen 分支指向可写目录（未见 _default_data_root/LOCALAPPDATA）")

    # 兼容 shim 必须重导出（打包态 main → api.accounts → auto_dm.accounts 走这条路径）
    shim = _read(os.path.join("backend", "auto_dm", "vbrowser.py"))
    miss = [n for n in ("resource_root", "resolve_profile_dir") if n not in shim]
    if miss:
        _fail("P9", "auto_dm/vbrowser.py 重导出新契约", f"缺 {miss}")
    else:
        _ok("P9", "auto_dm/vbrowser.py 已重导出 resource_root / resolve_profile_dir")


def main() -> int:
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    check_contents_dir_name()
    check_bundle_resources()
    check_root_split()

    passed = [r for r in RESULTS if r["ok"]]
    failed = [r for r in RESULTS if not r["ok"]]

    if args.json:
        print(json.dumps({"results": RESULTS, "passed": len(passed),
                          "failed": len(failed)}, ensure_ascii=False, indent=2))
    else:
        print("=" * 72)
        print("打包契约门禁 check_packaging_contract")
        print("=" * 72)
        for r in RESULTS:
            tag = "[PASS]" if r["ok"] else "[FAIL]"
            print(f"  {tag} {r['id']:<3} {r['name']}"
                  + (f"\n         {r['detail']}" if (r["detail"] and not r["ok"]) else ""))
        print("-" * 72)
        print(f"  合计: {len(RESULTS)} 项，通过 {len(passed)}，未通过 {len(failed)}")
        if failed:
            print("\n⛔ 未通过项（安装包会坏，必须先修）：")
            for r in failed:
                print(f"  - {r['id']}: {r['name']} —— {r['detail']}")
        else:
            print("\n✅ 打包契约全部满足")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
