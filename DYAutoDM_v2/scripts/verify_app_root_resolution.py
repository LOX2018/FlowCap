# -*- coding: utf-8 -*-
"""机械验证：数据根解析顺序（frozen 部署副本「自描述数据根」）。

对应事故（2026-09-29）：部署副本被「双击 exe」启动、无 DY_APP_ROOT
⇒ 解析到空的 %LOCALAPPDATA%\\DYAutoDM ⇒ 会员注册表为空 ⇒ 登录报**误导性**的
「用户名或口令错误」（实测口令哈希完全匹配）。

判据（正控 + 负控，缺一不可 —— D-07「失败态会变红」）：
  P1 frozen + 声明文件（指向**已存在**目录）→ 解析到声明目录（**修复生效**）
  P2 frozen + DY_APP_ROOT 同时存在        → 环境变量优先（显式配置 > 声明）
  N1 frozen + **无**声明文件（= 修复前行为）→ 解析到默认根 %LOCALAPPDATA%\\DYAutoDM
                                            （这条**复现原缺陷**；gate 必须能看见它）
  N2 frozen + 声明指向**不存在**目录      → 回退默认根（不静默迁根）
  C1 负控：把 `_deploy_declared_root` 打桩成返回 None ⇒ P1 **必然变红**
           （证明本 gate 判据跟着修复走，不是恒绿）

跑法：python scripts/verify_app_root_resolution.py
退出码：0 = 全通过；1 = 有断言失败
"""
import importlib.util
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VBROWSER = os.path.join(ROOT, "backend", "vbrowser.py")
MARKER = "dyautodm_app_root.txt"

PASS = FAIL = 0
FAILED = []
OLD_FROZEN = getattr(sys, "frozen", None)
OLD_EXE = sys.executable


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        FAILED.append(name)
        print(f"  FAIL {name}  {detail}")


def load_vbrowser():
    spec = importlib.util.spec_from_file_location("vbrowser_under_test", VBROWSER)
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, os.path.join(ROOT, "backend"))
    spec.loader.exec_module(mod)
    return mod


def scenario(vb, *, frozen, exe_dir, env_root, marker_txt):
    """设置进程态后调用 app_root()，返回解析结果。"""
    if frozen:
        sys.frozen = True
    else:
        if OLD_FROZEN is None:
            try:
                del sys.frozen
            except AttributeError:
                pass
        else:
            sys.frozen = OLD_FROZEN
    sys.executable = os.path.join(exe_dir, "DYAutoDM_v2_x-debug.exe")
    if env_root is None:
        os.environ.pop("DY_APP_ROOT", None)
    else:
        os.environ["DY_APP_ROOT"] = env_root
    mp = os.path.join(exe_dir, MARKER)
    if marker_txt is None:
        if os.path.exists(mp):
            os.remove(mp)
    else:
        with open(mp, "w", encoding="utf-8") as f:
            f.write(marker_txt)
    return vb.app_root()


def norm(p):
    return os.path.normcase(os.path.abspath(p))


def main():
    print("=" * 70)
    print("数据根解析 · 机械验证（部署副本自描述）")
    print("=" * 70)
    vb = load_vbrowser()
    default_root = vb._default_data_root()

    real_dir = tempfile.mkdtemp(prefix="dyroot_real_")     # 「已存在」的声明目录
    exe_dir = tempfile.mkdtemp(prefix="dyroot_exe_")       # 伪装成 exe 同目录

    # ── P1：frozen + 声明（存在）→ 声明目录 ──────────────────────────────
    got = scenario(vb, frozen=True, exe_dir=exe_dir, env_root=None, marker_txt=real_dir)
    check("P1 frozen+声明(存在) → 解析到声明目录（修复生效）",
          norm(got) == norm(real_dir), f"got={got!r} want={real_dir!r}")

    # ── P2：frozen + DY_APP_ROOT 同时存在 → 环境变量优先 ─────────────────
    envdir = tempfile.mkdtemp(prefix="dyroot_env_")
    got = scenario(vb, frozen=True, exe_dir=exe_dir, env_root=envdir, marker_txt=real_dir)
    check("P2 环境变量优先于声明（显式配置 > 副本声明）",
          norm(got) == norm(envdir), f"got={got!r} want={envdir!r}")

    # ── N1：frozen + 无声明（= 修复前行为）→ 默认根（复现原缺陷）─────────
    got = scenario(vb, frozen=True, exe_dir=exe_dir, env_root=None, marker_txt=None)
    check("N1 frozen+无声明 → 落到默认根 %LOCALAPPDATA%\\DYAutoDM（复现原缺陷）",
          norm(got) == norm(default_root), f"got={got!r} want={default_root!r}")

    # ── N2：frozen + 声明指向不存在目录 → 回退默认（不静默迁根）──────────
    got = scenario(vb, frozen=True, exe_dir=exe_dir, env_root=None,
                   marker_txt=os.path.join(exe_dir, "no_such_dir_xyz"))
    check("N2 声明失真（目录不存在）→ 回退默认根，不静默迁根",
          norm(got) == norm(default_root), f"got={got!r} want={default_root!r}")

    # ── C1 负控：打桩掉修复 → P1 必然变红（判据跟着修复走，非恒绿）───────
    _saved = vb._deploy_declared_root
    vb._deploy_declared_root = lambda: None
    got = scenario(vb, frozen=True, exe_dir=exe_dir, env_root=None, marker_txt=real_dir)
    vb._deploy_declared_root = _saved
    check("C1 负控：禁用修复后 P1 场景变红（解析回默认根）",
          norm(got) == norm(default_root),
          f"got={got!r}（负控未生效 = 判据可能与修复无关）")

    # ── 还原进程态 ──────────────────────────────────────────────────────
    sys.executable = OLD_EXE
    os.environ.pop("DY_APP_ROOT", None)
    if OLD_FROZEN is None:
        try:
            del sys.frozen
        except AttributeError:
            pass
    else:
        sys.frozen = OLD_FROZEN

    print("-" * 70)
    print(f"PASS={PASS} FAIL={FAIL}")
    if FAILED:
        print("失败项：")
        for x in FAILED:
            print("  -", x)
        return 1
    print("✓ 数据根解析符合预期（正控/负控均通过）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
