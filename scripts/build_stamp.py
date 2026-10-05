# -*- coding: utf-8 -*-
"""构建来源戳（build provenance stamp）—— 让「产物是否陈旧」可机械判定。

## 为什么需要它（与版本号的分工，务必分清）

| 机制 | 证明什么 | 不能证明什么 |
|---|---|---|
| `check_version_sync.py`（版本号） | 前后端**声明**的版本一致 | 产物是**这份代码**构建的 |
| `build_stamp.py`（来源戳，本模块） | 产物是**这份源码**构建的 | 版本号是否该升 |

两个用**同版本号、不同代码**分别构建的产物，版本门禁会**放行** ——
它只证明「声明的版本号一致」，不证明「sidecar 里装的是你最新的后端代码」。
本模块补上后者：对产物的**真实输入源码**算 sha256。

## 判据

```
产物构建时记录的戳  ==  当前源码算出的戳   ⇒ 产物就是这份源码构建的（非陈旧）
                       不等                ⇒ 陈旧，拒绝 --skip-* / 拒绝部署
```

## 为什么锚在「源码」而不是「产物字节」

PyInstaller / tauri 产物字节**不可复现**（内嵌时间戳、绝对路径、随机化），
直接哈希产物字节无意义。但源码是产物的**唯一业务输入**，哈希源码足以判定
「是否这份代码」。

⚠️ 这条**依赖**「构建是源码的纯函数」这一前提。若构建掺入了未纳管的输入
（随机 / 时间 / 网络），该判据会失真。故 `SIDECAR_SOURCES` / `RUST_SOURCES`
必须覆盖**所有会影响产物行为**的源码位置 —— 改动纳入范围时同步更新本模块。

## 行尾归一化（必须）

本仓 `core.autocrlf=true` 且存在**混合行尾**文件（实测 `patch` 工具会把
整文件重写为 CRLF）。若不做归一化，一次纯行尾变更就会让戳变化，制造假红。
故读取时统一 `\\r\\n` / `\\r` → `\\n`，再哈希。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # FlowCap/

# ── 哪些源码进 sidecar 的戳 ──
#   与 build_and_deploy.py 的 BACKEND_SIGNAL 对齐（同一批「改了就要重打 sidecar」）。
SIDECAR_GLOBS = [
    ("backend", "**/*.py"),
    ("daemon", "**/*.py"),
    ("scripts", "build_sidecar.py"),
]

# ── 哪些源码进 exe（Rust）的戳 ──
#   与 build_and_deploy.py 的 FRONTEND_SIGNAL + SHELL_SIGNAL 对齐。
#   注：hash `frontend/src`（源）而非 `frontend/dist`（产物）——dist 是 src 的
#   派生，且 tauri 的 beforeBuildCommand 每次都会重建它。
RUST_GLOBS = [
    ("frontend", "src/**/*"),
    ("frontend", "*.html"),
    ("frontend", "package.json"),
    ("frontend", "vite.config.ts"),
    ("src-tauri", "src/**/*"),
    ("src-tauri", "tauri.conf.json"),
]

# 生成物 / 缓存 / 非产物输入：**必须排除**。
#   `_build_version.py` 由 build_sidecar.py 生成，且本模块会把 SOURCE_STAMP
#   写进去 ⇒ 若纳入哈希就是**自指**（戳变了导致文件变了导致戳又变），
#   故硬排除。版本号另有 check_version_sync.py 守护，不靠本模块。
#   `test_*.py` / `_*.py`（一次性脚本）**不被应用 import ⇒ 不进 sidecar 的
#   import 图**（已实测：`grep -rn "import _x"` 无命中）。纳入它们会让
#   「改一个测试 / 一次性脚本」把 sidecar 判成陈旧 ⇒ 假红，故排除。
EXCLUDE_NAMES = {"_build_version.py", "_build_stamp.py"}
EXCLUDE_GLOBS = ["test_*.py", "_*.py", "conftest.py"]
EXCLUDE_DIRS = {"__pycache__", "build", ".pytest_cache", "node_modules", ".git",
                ".ruff_cache", ".mypy_cache", "dist"}


def _norm_bytes(b: bytes) -> bytes:
    """行尾归一化：CRLF / CR → LF。避免纯行尾变更制造假红。"""
    return b.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _collect(root: Path, globs: list[tuple[str, str]]) -> list[Path]:
    import fnmatch
    out: list[Path] = []
    for sub, pat in globs:
        base = root / sub
        if not base.is_dir():
            continue
        for p in base.glob(pat):
            if not p.is_file():
                continue
            rel_parts = p.relative_to(root).parts
            if any(part in EXCLUDE_DIRS for part in rel_parts):
                continue
            if p.name in EXCLUDE_NAMES:
                continue
            if any(fnmatch.fnmatch(p.name, g) for g in EXCLUDE_GLOBS):
                continue
            out.append(p)
    # 去重 + 按相对路径排序（保证与文件系统遍历顺序无关）
    uniq = sorted({p.resolve() for p in out}, key=lambda x: x.relative_to(root).as_posix())
    return uniq


def compute(root: Path | None = None, kind: str = "sidecar") -> tuple[str, int]:
    """算来源戳。返回 (sha256 十六进制, 参与文件数)。

    `kind`: "sidecar" | "rust"。
    """
    root = root or ROOT
    globs = SIDECAR_GLOBS if kind == "sidecar" else RUST_GLOBS
    files = _collect(root, globs)
    h = hashlib.sha256()
    for p in files:
        rel = p.relative_to(root).as_posix()
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(_norm_bytes(p.read_bytes()))
        h.update(b"\0")
    return h.hexdigest(), len(files)


# ── 戳的记录文件（gitignored；与 artifacts/.last_built_sha 同族）──
STAMP_FILE = ROOT / "artifacts" / ".build_stamp.json"


def read_record(path: Path | None = None) -> dict:
    p = path or STAMP_FILE
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_record(kind: str, stamp: str, files: int, version: str = "",
                 path: Path | None = None) -> None:
    """记下本次构建的戳。**只在构建成功后调用**（由构建脚本负责时机）。"""
    p = path or STAMP_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    data = read_record(p)
    data[kind] = {"stamp": stamp, "files": files, "version": version}
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _md5(p: Path) -> str:
    h = hashlib.md5()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def record_build(kind: str, artifacts: list[Path], version: str = "",
                 root: Path | None = None, path: Path | None = None) -> dict:
    """构建**成功后**调用：记下源码戳 + 产物 md5。

    `artifacts`：本次构建产出的可执行文件路径（sidecar = 3 个 exe；
    rust = 主程序 exe）。记 md5 是为了闭合「**磁盘上的产物 == 那次构建的产物**」
    这一环 —— 只记源码戳的话，产物被人替换/回滚后门禁仍会放行。
    """
    root = root or ROOT
    stamp, n = compute(root, kind)
    art: dict[str, str] = {}
    for a in artifacts:
        try:
            if a.is_file():
                art[a.name] = _md5(a)
        except Exception:
            pass
    rec = {"stamp": stamp, "files": n, "version": version, "artifacts": art}
    p = path or STAMP_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    data = read_record(p)
    data[kind] = rec
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return rec


def check_artifacts(kind: str, artifacts: list[Path],
                    record_path: Path | None = None) -> dict:
    """校验「磁盘上的产物」是否就是那次构建的产物（md5 比对）。"""
    rec = read_record(record_path).get(kind) or {}
    recorded = rec.get("artifacts") or {}
    if not recorded:
        return {"ok": False, "reason": f"无 {kind} 产物 md5 记录（尚未构建过）",
                "missing": [], "changed": []}
    missing, changed = [], []
    for a in artifacts:
        if not a.is_file():
            missing.append(a.name)
            continue
        want = recorded.get(a.name)
        if want is None:
            missing.append(a.name)
            continue
        if _md5(a) != want:
            changed.append(a.name)
    if missing or changed:
        return {"ok": False, "missing": missing, "changed": changed,
                "reason": (f"{kind} 产物与构建记录不符"
                           + (f"；缺失 {missing}" if missing else "")
                           + (f"；内容变化 {changed}" if changed else ""))}
    return {"ok": True, "missing": [], "changed": [], "reason": "产物 md5 一致"}


def verify(kind: str, artifacts: list[Path], root: Path | None = None,
           record_path: Path | None = None) -> dict:
    """**组合校验**：源码戳 + 产物 md5 **两者都要**过。

    🔴 为什么必须两者都要（实测发现的漏洞）：
    只验源码戳时，一条**伪造的**记录（只写 stamp、不写产物 md5）就能让门禁放行 ——
    实测：手工写一条「stamp = 当前源码」的记录，`--skip-sidecar` 立刻判「跳过安全」，
    而磁盘上的 sidecar 二进制其实是**改动前**构建的（源码已改、产物没重建）。
    这就是典型的「门禁看起来在工作、实际放行了陈旧产物」。

    加上产物 md5 后：伪造者必须同时伪造 md5，而 md5 来自**真实产物文件** ——
    等于「你得先把产物做出来」。这才是「非陈旧」的实质证明。
    """
    rs = check(kind, root=root, record_path=record_path)
    if not rs["ok"]:
        return {**rs, "artifacts_ok": None}
    ra = check_artifacts(kind, artifacts, record_path=record_path)
    if not ra["ok"]:
        return {"ok": False, "current": rs["current"], "recorded": rs["recorded"],
                "files": rs["files"], "artifacts_ok": False,
                "reason": (f"{kind} 源码戳一致，但{ra['reason']}"
                           " —— 产物与构建记录不符（可能被替换/回滚，或记录是伪造的）")}
    return {"ok": True, "current": rs["current"], "recorded": rs["recorded"],
            "files": rs["files"], "artifacts_ok": True, "reason": "源码戳与产物 md5 均一致"}


def check(kind: str, root: Path | None = None, record_path: Path | None = None) -> dict:
    """比对「记录的戳」vs「当前源码的戳」。

    返回 dict：`{ok, current, recorded, files, reason}`
      ok=True  ⇒ 产物就是这份源码构建的（可安全跳过重建）
      ok=False ⇒ 陈旧（或从未构建过），reason 说明原因
    """
    cur, n = compute(root, kind)
    rec = read_record(record_path).get(kind) or {}
    got = rec.get("stamp") or ""
    if not got:
        return {"ok": False, "current": cur, "recorded": "",
                "files": n, "reason": f"无 {kind} 构建记录（尚未构建过）"}
    if got != cur:
        return {"ok": False, "current": cur, "recorded": got,
                "files": n,
                "reason": (f"{kind} 源码自上次构建后已改动"
                           f"（记录 {got[:12]} ≠ 当前 {cur[:12]}）")}
    return {"ok": True, "current": cur, "recorded": got, "files": n, "reason": "一致"}


if __name__ == "__main__":  # 便于手工核对
    import sys
    _kind = sys.argv[1] if len(sys.argv) > 1 else "sidecar"
    _cur, _n = compute(kind=_kind)
    print(f"[{_kind}] 当前源码戳 = {_cur}（{_n} 个文件）")
    _r = read_record().get(_kind) or {}
    print(f"[{_kind}] 构建记录   = {_r.get('stamp', '(无)')}")
    _c = check(_kind)
    print(f"[{_kind}] 判定       = {'✅ 一致' if _c['ok'] else '❌ ' + _c['reason']}")
