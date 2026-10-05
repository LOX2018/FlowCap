# -*- coding: utf-8 -*-
"""凭证外发审计 —— 敏感数据（登录凭证 / browser profile）是否离开受信边界。

## 为什么存在（2026-09-29 实测，ADR-030 / ADR-031）

设计符合性审计回答「实现**是否符合**设计」；当**设计假设本身错**（从未成文写
「凭证不得外发」）时，它结构性看不见 —— 实测：3 个含真实抖音登录 cookie
（sessionid / sid_guard / sid_tt / uid_tt）的 browser profile 被 `bundle.resources`
打进 MSI 分发物，且因被 `.gitignore` 忽略而逃过所有「以文件/源码为抓手」的扫描。
⇒ 本维度回答**另一个问题**：**这份数据会不会离开受信边界？**

## 判据（两类，分级不同）

| 类 | 判据 | 分级 |
|---|---|---|
| **A DISTRIBUTED** | 含凭证的路径出现在**分发路径**里（tauri `bundle.resources`/`externalBin`；或仓库内任何上传/分发命令 `scp`/`rsync`/`ossutil`/`aws s3`/`gh release`… 携带含凭证路径） | **阻断**（这是正在泄露） |
| **B STRAY** | 源码树内散落**凭证类文件/目录**（profile 目录 / `members/` / 账号 `.env`·`.enc` / `cookies.sqlite`·`Cookies`·`cert9.db`·`key4.db`·`Login Data`） | **警告**（应清理，尚未外发） |

## 排除项（防误报）

`.example` / `.template` 样例、`node_modules` / `.git` / 构建产物 / `_ext_repos` 参照库。

## 用法

    python scripts/check_credential_exposure.py          # 人类可读
    python scripts/check_credential_exposure.py --json    # 机器可读

退出码：0 = 无 DISTRIBUTED（STRAY 只警告）；1 = 有 DISTRIBUTED。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # FlowCap/
CONF = os.path.join(ROOT, "src-tauri", "tauri.conf.json")

# 凭证类**目录**名（出现在源码树 → STRAY；出现在分发路径 → DISTRIBUTED）
CRED_DIRS = ("vb_profile_default", "vb_profile_dm", "pw_profile_dm",
             "members", "profiles")
# 凭证类**目录**前缀（如 vb_profile_* / pw_profile_* / chromium_profile*）
CRED_DIR_PREFIXES = ("vb_profile_", "pw_profile_", "chromium_profile")
# 凭证类**文件**名
CRED_FILES = ("cookies.sqlite", "Cookies", "cert9.db", "key4.db",
              "logins.db", "Login Data", "Local State")
CRED_FILE_EXTS = (".enc",)
# .env 类文件：**按内容**判定 —— 含下列凭证类键才计为凭证文件（纯注释/纯非凭证键不计）
_ENV_CRED_KEY = re.compile(
    r"(?im)^\s*(?:export\s+)?[A-Z0-9_]*"
    r"(COOKIE|TICKET|SECRET|TOKEN|API_KEY|APIKEY|PRIVATE_KEY|PASSWORD|PASSWD|SKEY|CREDENTIAL)"
    r"[A-Z0-9_]*\s*=")

# 扫描时跳过的目录（构建产物 / 依赖 / 参照库 / VCS）
SKIP_DIRS = {"node_modules", ".git", "target", "dist", "build", "__pycache__",
             "_ext_repos", "vendor", ".venv", "venv", "appinternals",
             "_internal", "binaries"}

# 分发/上传类命令模式（携带的路径若含凭证名即 DISTRIBUTED）
_UPLOAD_PAT = re.compile(
    r"(?i)\b(scp|rsync|ossutil|ossutil64|aws\s+s3\s+(cp|sync|mv)|azcopy|"
    r"gh\s+release\s+(create|upload)|coscmd|rclone\s+(copy|sync))\b")


def _is_sample(name: str) -> bool:
    low = name.lower()
    return low.endswith((".example", ".template", ".sample", ".dist")) or \
        ".example." in low or ".template." in low


def _cred_kind(name: str) -> str | None:
    """返回 'dir' / 'file' / None（按**名字**判定；.env 需再按内容复核）。"""
    if name in CRED_DIRS or any(name.startswith(p) for p in CRED_DIR_PREFIXES):
        return "dir"
    if name in CRED_FILES or name.endswith(CRED_FILE_EXTS):
        return "file"
    return None


def _leaks_env_file(path: str) -> bool:
    """`.env*` 文件是否含凭证类键（按内容判定，避免把纯注释/非凭证配置误报）。"""
    try:
        txt = open(path, encoding="utf-8", errors="replace").read()
    except Exception:  # noqa: BLE001
        return False
    return bool(_ENV_CRED_KEY.search(txt))


def check_distributed(root: str = ROOT) -> list[dict]:
    """A：分发路径引用含凭证的目录/文件。"""
    out = []
    conf_path = os.path.join(root, "src-tauri", "tauri.conf.json")
    if os.path.isfile(conf_path):
        try:
            conf = json.load(open(conf_path, encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            out.append({"kind": "DISTRIBUTED", "src": "tauri.conf.json",
                        "detail": f"无法解析 tauri.conf.json: {e}"})
            conf = None
        if conf:
            bundle = (conf.get("bundle") or {})
            for field in ("resources", "externalBin"):
                val = bundle.get(field)
                keys = val.keys() if isinstance(val, dict) else (val or [])
                for k in keys:
                    base = os.path.basename(str(k).rstrip("/\\"))
                    if _cred_kind(base) == "dir" or _cred_kind(base) == "file":
                        out.append({"kind": "DISTRIBUTED",
                                    "src": f"tauri.conf.json:bundle.{field}",
                                    "detail": f"分发路径引用含凭证的条目: {k}"})
    # 仓库内上传/分发命令
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if not fn.endswith((".py", ".sh", ".ps1", ".bat", ".cmd", ".yml", ".yaml")):
                continue
            p = os.path.join(dirpath, fn)
            try:
                txt = open(p, encoding="utf-8", errors="replace").read()
            except Exception:  # noqa: BLE001
                continue
            for i, line in enumerate(txt.splitlines(), 1):
                if line.lstrip().startswith("#"):
                    continue
                if _UPLOAD_PAT.search(line) and any(
                        n in line for n in CRED_DIRS + CRED_FILES):
                    out.append({"kind": "DISTRIBUTED",
                                "src": f"{os.path.relpath(p, root)}:{i}",
                                "detail": f"分发命令携带含凭证路径: {line.strip()[:120]}"})
    return out


def check_stray(root: str = ROOT) -> list[dict]:
    """B：源码树内散落的凭证类文件/目录（gitignored 或未跟踪）。"""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root)
        # 剪枝：跳过构建产物/依赖；命中凭证目录则记录并**不深入**其内部
        keep = []
        for d in dirnames:
            if d in SKIP_DIRS:
                continue
            if _cred_kind(d) == "dir" and not _is_sample(d):
                out.append({"kind": "STRAY", "path": os.path.join(rel_dir, d).replace("\\", "/"),
                            "detail": "凭证类目录散落源码树（应只存在于数据根）"})
                continue          # 不深入
            keep.append(d)
        dirnames[:] = keep
        for fn in filenames:
            if _is_sample(fn):
                continue
            full = os.path.join(dirpath, fn)
            if fn == ".env" or fn.startswith(".env."):
                if _leaks_env_file(full):
                    out.append({"kind": "STRAY",
                                "path": os.path.join(rel_dir, fn).replace("\\", "/"),
                                "detail": "明文 .env 含凭证类键散落源码树"})
                continue
            if _cred_kind(fn) == "file":
                out.append({"kind": "STRAY", "path": os.path.join(rel_dir, fn).replace("\\", "/"),
                            "detail": "凭证类文件散落源码树"})
    return out


def run(as_json: bool = False, root: str = ROOT) -> int:
    dist = check_distributed(root)
    stray = check_stray(root)
    if as_json:
        print(json.dumps({"distributed": dist, "stray": stray}, ensure_ascii=False, indent=2))
        return 1 if dist else 0
    print("=" * 72)
    print("凭证外发审计 check_credential_exposure")
    print("=" * 72)
    print(f"  [{'FAIL' if dist else 'PASS'}] A DISTRIBUTED  分发路径携带凭证（{len(dist)} 处）")
    for d in dist:
        print(f"         ⛔ {d['src']}: {d['detail']}")
    print(f"  [{'WARN' if stray else 'PASS'}] B STRAY        源码树散落凭证类文件/目录（{len(stray)} 处）")
    for s in stray[:20]:
        print(f"         ⚠️  {s['path']}")
    if len(stray) > 20:
        print(f"         … 另有 {len(stray) - 20} 处")
    print("-" * 72)
    if dist:
        print("⛔ 有凭证进入分发路径 —— 必须先修（这是正在泄露）")
        return 1
    if stray:
        print("⚠️ 无外发（A 通过），但源码树有凭证类残留（B 警告）—— 应清理/迁往数据根")
    else:
        print("✅ 无凭证外发、源码树无凭证残留")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    return run(as_json=args.json)


if __name__ == "__main__":
    sys.exit(main())
