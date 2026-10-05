# -*- coding: utf-8 -*-
"""构建产物路径 SSOT（单一真源）—— 构建缓存已迁出源码树（2026-09-29 架构决策）。

## 为什么存在

cargo 默认把编译产物放在项目树内的 `src-tauri/target/`。实测该目录会**单调膨胀**
（`debug/incremental` 每次重编新增 ~460M 快照且旧份不回收；`_up_` 每次重建；实测
一次构建即 4.0G，历史峰值 42G）。根因 = **「部署路径/源码路径隔离」从未覆盖编译期缓存**。

本模块把「产物根在哪」收敛为**唯一解析入口**，消除此前散落在 5 个脚本里的硬编码：
  - deploy.py（主程序 exe 探测）
  - package_installer.py（wix/msi/nsis）
  - verify_build.py（exe 路径）

## 解析优先级（显式配置优先）

  1. 环境变量 `CARGO_TARGET_DIR` / `CARGO_BUILD_TARGET_DIR`（build_all.py 显式注入）
  2. `src-tauri/.cargo/config.toml` 的 `[build] target-dir`（提交态；防绕过脚本直接构建）
  3. 兜底 = 树内 `src-tauri/target`（**门禁 R10 会报红**，等于回归默认行为）

⇒ 路径字面量**只写在** `.cargo/config.toml` 一处；本模块不含任何机器绝对路径常量。

## 用法

    from build_paths import target_dir, main_exe, bundle_dir, msi_path, nsis_path
"""
from __future__ import annotations

import os
import re
from pathlib import Path

# scripts/build_paths.py → DYAutoDM_v2/
ROOT = Path(__file__).resolve().parents[1]
TAURI_DIR = ROOT / "src-tauri"
_CARGO_CFG = TAURI_DIR / ".cargo" / "config.toml"
_CARGO_CFG_LEGACY = TAURI_DIR / ".cargo" / "config"   # 旧式无扩展名
_FALLBACK_NAME = "target"                              # cargo 默认（门禁会拦）


def _cargo_config_target_dir() -> str | None:
    """从 src-tauri/.cargo/config.toml 读 [build] target-dir（最小解析，不引第三方依赖）。"""
    for cfg in (_CARGO_CFG, _CARGO_CFG_LEGACY):
        if not cfg.is_file():
            continue
        try:
            txt = cfg.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        in_build = False
        for raw in txt.splitlines():
            s = raw.strip()
            if not s or s.startswith("#"):
                continue
            if s.startswith("["):
                in_build = s == "[build]"
                continue
            if in_build:
                m = re.match(r'target-dir\s*=\s*["\']([^"\']+)["\']', s)
                if m:
                    return m.group(1)
    return None


def target_dir() -> Path:
    """构建产物根（cargo target-dir 的等价物）。"""
    for env in ("CARGO_TARGET_DIR", "CARGO_BUILD_TARGET_DIR"):
        v = os.environ.get(env)
        if v:
            return Path(v)
    cfg = _cargo_config_target_dir()
    if cfg:
        p = Path(cfg)
        if not p.is_absolute():
            p = (TAURI_DIR / p).resolve()
        return p
    return TAURI_DIR / _FALLBACK_NAME


def is_defaulted() -> bool:
    """True = 回落到了树内默认 target（门禁 R10 的判据输入）。"""
    return target_dir() == (TAURI_DIR / _FALLBACK_NAME).resolve()


def exe_path(kind: str) -> Path:
    """指定构建类型（debug/release）的主程序产物路径。"""
    return target_dir() / kind / "dyautodm-v2.exe"


def main_exe(kind: str = "auto") -> Path | None:
    """主程序产物：auto = debug/release 取较新者（与旧 deploy.py 语义一致）。

    返回 None 表示两者都不存在（调用方按自己的退出码处理）。
    """
    if kind in ("debug", "release"):
        p = exe_path(kind)
        return p if p.is_file() else None
    cands = [p for p in (exe_path("release"), exe_path("debug")) if p.is_file()]
    if not cands:
        return None
    return max(cands, key=lambda p: p.stat().st_mtime)


def bundle_dir() -> Path:
    return target_dir() / "release" / "bundle"


def wix_dir() -> Path:
    return target_dir() / "release" / "wix"


def msi_path(version: str) -> Path:
    # 2026-10-02：产品定名「川流」（tauri.conf.json productName）⇒ 安装包名随之改。
    return bundle_dir() / "msi" / f"川流_{version}_x64_en-US.msi"


def nsis_path(version: str) -> Path:
    return bundle_dir() / "nsis" / f"川流_{version}_x64-setup.exe"


if __name__ == "__main__":           # 便于人工核对
    print(f"target_dir()   = {target_dir()}")
    print(f"is_defaulted() = {is_defaulted()}")
    print(f"main_exe(auto) = {main_exe()}")
