# -*- coding: utf-8 -*-
"""离线回放层 · 机械自检（G1–G9）。

判据来自 `dyautodm-dev-guards` 中被反复验证过的三条铁律：
  · §三·己：`DY_APP_ROOT` 必须显式钉根，不能读错根            → G4 / G5
  · §四·丙：计数型机械判据必须真跑一次并核对数字               → G1 / G2 / G8
  · §八·坑：**未证明会拒绝的门禁 = 假门禁**（本项目多次踩）    → G3 / G4 / G9

## 两条自我保护（避免自检本身变成污染源）

1. G3/G9 在**临时目录**里伪造清单与样本，**绝不改动仓库内的真实样本**
   （原实现直接翻转真实样本字节，与其他用例并发读时会竞态）。
2. 全程不设 `DY_APP_ROOT` 以外的环境变量；沙箱退出即回收。
"""

import ast
import json
import os
import tempfile
from contextlib import contextmanager

from . import loader
from .sandbox import (DESIGN_ROOT, LEGACY_ROOT, Sandbox, _forbidden_reason,
                      _SRC_REPO)

#: 回放层绝不允许引入的模块（打网 / 开浏览器 / 起服务）
FORBIDDEN_IMPORTS = {
    "requests", "urllib", "urllib2", "urllib3", "httpx", "http",
    "socket", "aiohttp", "websocket", "websockets", "socketserver",
    "playwright", "patchright", "camoufox", "selenium",
    "uvicorn", "fastapi", "starlette",
}


def _ok(name, cond, detail=""):
    return (name, bool(cond), detail)


def _scan_forbidden_imports(pkg_dir):
    """AST 扫描给定目录下的 .py，返回 [(文件, 模块名)]。"""
    hits = []
    for fn in sorted(os.listdir(pkg_dir)):
        if not fn.endswith(".py"):
            continue
        path = os.path.join(pkg_dir, fn)
        with open(path, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    top = a.name.split(".")[0]
                    if top in FORBIDDEN_IMPORTS:
                        hits.append((fn, top))
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    top = node.module.split(".")[0]
                    if top in FORBIDDEN_IMPORTS:
                        hits.append((fn, top))
    return hits


@contextmanager
def _temp_fixture_store():
    """把 loader 临时重定向到一个假 fixture store（用完还原）。"""
    tmp = tempfile.mkdtemp(prefix="dybc_fakestore_")
    saved = (loader._FIX_DIR, loader._MANIFEST)
    loader._FIX_DIR = tmp
    loader._MANIFEST = os.path.join(tmp, "manifest.json")
    try:
        yield tmp
    finally:
        loader._FIX_DIR, loader._MANIFEST = saved
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def run():
    rows = []
    pkg_dir = os.path.dirname(os.path.abspath(__file__))

    # ---- G1 清单可读且有样本（SSOT）----
    names = loader.list_fixtures()
    rows.append(_ok("G1 清单可读且有样本", len(names) >= 1, f"{names}"))

    # ---- G2 真实样本 sha256/size 校验通过（只读）----
    if names:
        nm = names[0]
        try:
            blob = loader.load_fixture(nm)
            ent = loader.describe(nm)
            rows.append(_ok("G2 样本加载且 sha256/size 校验通过",
                            loader.sha256_of(blob) == ent["sha256"]
                            and len(blob) == ent["size"],
                            f"{nm} {len(blob)}B"))
        except Exception as e:  # noqa: BLE001
            rows.append(_ok("G2 样本加载且 sha256/size 校验通过", False,
                            f"{type(e).__name__}: {e}"))

    # ---- G3 篡改门禁必须拒绝（在假 store 上做，不碰真样本）----
    try:
        with _temp_fixture_store() as tmp:
            rel = "fake/fake.bin"
            p = os.path.join(tmp, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as f:
                f.write(b"REAL-CONTENT")          # 真实字节
            loader.write_manifest({"fake": {
                "file": rel,
                "sha256": loader.sha256_of(b"DIFFERENT"),  # 清单登记别的 sha
                "size": 12,
                "desc": "自检用假样本", "provenance": "selftest",
            }})
            caught = None
            try:
                loader.load_fixture("fake")
            except loader.FixtureTampered as e:
                caught = e
            rows.append(_ok("G3 清单 sha 与样本不符 → FixtureTampered 拒绝",
                            caught is not None,
                            type(caught).__name__ if caught else "未拒绝（假门禁）"))
    except Exception as e:  # noqa: BLE001
        rows.append(_ok("G3 清单 sha 与样本不符 → FixtureTampered 拒绝", False,
                        f"{type(e).__name__}: {e}"))

    # ---- G4 沙箱拒绝真实数据根 / 源码树（且放行临时目录）----
    cases = {
        "DESIGN_ROOT": DESIGN_ROOT,
        "DESIGN_ROOT 子路径": os.path.join(DESIGN_ROOT, "logs"),
        "废弃主分支根": LEGACY_ROOT,
        "源码树": os.path.join(_SRC_REPO, "DYAutoDM_v2", "backend"),
    }
    bad = [k for k, p in cases.items() if not _forbidden_reason(p)]
    rows.append(_ok("G4 沙箱拒绝真实数据根/源码树（4 项）", not bad,
                    "未拒绝：" + ",".join(bad) if bad else "4/4 均被拒"))
    rows.append(_ok("G4b 临时目录被放行（门禁非无脑全拒）",
                    _forbidden_reason(tempfile.gettempdir()) == "",
                    tempfile.gettempdir()))

    # ---- G5 沙箱钉根 + 回读自证；退出后回收 + env 还原 ----
    try:
        before = os.environ.get("DY_APP_ROOT")
        with Sandbox("selftest") as sb:
            env_root = os.path.abspath(os.environ["DY_APP_ROOT"])
            same = env_root.lower() == os.path.abspath(sb.root).lower()
            is_tmp = env_root.lower().startswith(
                os.path.abspath(tempfile.gettempdir()).lower())
            not_design = not env_root.lower().startswith(DESIGN_ROOT.lower())
            rows.append(_ok("G5 沙箱把 DY_APP_ROOT 钉到独立临时根",
                            same and is_tmp and not_design, env_root))
        rows.append(_ok("G5b 退出后根目录已回收且 env 已还原",
                        (not os.path.exists(env_root))
                        and os.environ.get("DY_APP_ROOT") == before))
    except Exception as e:  # noqa: BLE001
        rows.append(_ok("G5 沙箱把 DY_APP_ROOT 钉到独立临时根", False, str(e)))

    # ---- G6 materialize 落盘 + 拒绝 .. 逃逸 ----
    try:
        with Sandbox("selftest2") as sb:
            p = sb.materialize({"a/b.bin": b"\x01\x02"})
            with open(p["a/b.bin"], "rb") as f:
                wrote = f.read() == b"\x01\x02"
            escaped = False
            try:
                sb.materialize({"../evil.bin": b"x"})
            except ValueError:
                escaped = True
            rows.append(_ok("G6 materialize 落盘 + 拒绝 .. 逃逸", wrote and escaped))
    except Exception as e:  # noqa: BLE001
        rows.append(_ok("G6 materialize 落盘 + 拒绝 .. 逃逸", False,
                        f"{type(e).__name__}: {e}"))

    # ---- G7 回放层零打网/浏览器 import ----
    hits = _scan_forbidden_imports(pkg_dir)
    rows.append(_ok("G7 replay 包零打网/浏览器 import", not hits, f"{hits}"))

    # ---- G8 可重复性：两个独立沙箱读数逐字节一致 ----
    if names:
        try:
            vals = []
            for i in range(2):
                with Sandbox(f"rep{i}") as sb:
                    blob = loader.load_fixture(names[0])
                    sb.materialize({f"x{i}.bin": blob})
                    vals.append((len(blob), loader.sha256_of(blob)))
            rows.append(_ok("G8 两次独立沙箱读数逐字节一致", vals[0] == vals[1],
                            f"{vals[0][0]}B"))
        except Exception as e:  # noqa: BLE001
            rows.append(_ok("G8 两次独立沙箱读数逐字节一致", False,
                            f"{type(e).__name__}: {e}"))

    # ---- G9 未知样本名 → FixtureMissing（不静默返回空）----
    try:
        with _temp_fixture_store():
            loader.write_manifest({})
            got = None
            try:
                loader.load_fixture("__no_such_fixture__")
            except loader.FixtureMissing as e:
                got = e
            rows.append(_ok("G9 未知样本名 → FixtureMissing", got is not None,
                            "未报错（静默返回空）" if got is None else ""))
    except Exception as e:  # noqa: BLE001
        rows.append(_ok("G9 未知样本名 → FixtureMissing", False,
                        f"{type(e).__name__}: {e}"))

    return rows


if __name__ == "__main__":
    rows = run()
    for n, good, d in rows:
        print(f"  [{'PASS' if good else 'FAIL'}] {n}" + (f"  — {d}" if d else ""))
    bad = [r for r in rows if not r[1]]
    print(f"\n{len(rows) - len(bad)}/{len(rows)} PASS")
    raise SystemExit(1 if bad else 0)
