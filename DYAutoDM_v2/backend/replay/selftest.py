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

#: 打网模块的顶层名 —— 用于「函数级网络调用点」扫描的别名解析。
NETWORK_MODULES = {"requests", "urllib", "urllib2", "urllib3", "httpx",
                   "http", "socket", "aiohttp", "aiohttp.client"}

#: 网络调用方法名（仅当调用链根名来自 NETWORK_MODULES 别名时才算命中）。
NETWORK_METHODS = {"get", "post", "put", "patch", "delete", "head", "request",
                   "urlopen", "urlretrieve", "connect", "create_connection",
                   "send", "sendall"}

#: ⚠️ P3-5（2026-09-23 假绿修复）：真实回放路径**执行到**的入口。
#:
#: 旧门禁只 AST 扫 `replay/` 目录 —— 而 replay 包自身本就不 import 网络
#: （恒真陈述），真正被回放用例 import 并**执行**的是产品模块
#: （`auto_dm.conversation_capture` 用 requests、`api.messages` 用 urllib）。
#: 只扫 replay/ 会让门禁与本层真实运行路径无关。
#:
#: 现以「(模块, 入口函数)」为粒度扫描**回放实际调用的函数**：
#:   · 这些函数体内不得出现任何网络调用点（严格，必须为空）；
#:   · 模块其余函数（如 fetch_conversation_history）回放**不执行**，其网络
#:     调用点登记在 `REPLAY_UNEXECUTED_NET_ALLOWLIST` 中为已知基线，不误报。
#: `_scan_replay_scope_coverage()` 再把「回放用例 import 的产品模块」与声明
#: 清单对账，防止有人新增 import 却忘了扩扫描范围（范围不可静默缩小）。
REPLAY_EXERCISED_ENTRYPOINTS = [
    ("auto_dm.conversation_capture", "parse_init_protobuf"),
    ("auto_dm.conversation_capture", "_extract_301_page"),
    ("auto_dm.conversation_capture", "_parse_301_messages"),
    ("api.messages", "list_conversations"),
]

#: 回放用例 import 的产品模块（用于范围对账；须 ⊆ 上面入口所属模块）。
REPLAY_EXERCISED_MODULES = {
    "auto_dm.conversation_capture",
    "auto_dm.im_protobuf",
    "api.messages",
}

#: 范围对账时忽略的框架/标准库/第三方顶层名（非本仓产品模块）。
REPLAY_SCOPE_IGNORE_TOP = {
    "replay", "unittest", "os", "sys", "tempfile", "sqlite3", "json",
    "asyncio", "shutil", "collections", "__future__", "ast", "hashlib",
    "importlib", "inspect", "time", "re", "pathlib", "typing", "datetime",
    "patchright", "playwright", "camoufox", "selenium", "loguru", "fastapi",
    "pydantic", "pydantic_settings", "requests", "urllib", "httpx",
}

#: T5 相关的产品模块（非网络扫描对象，但属回放层门禁范围，显式登记）。
#: 用于 `_scan_replay_scope_coverage()` 对账时不误报。
REPLAY_AUX_MODULES = {
    "services.env_audit",   # T5-b 两世界可见性探针宿主
    "daemon.browser_daemon",  # T5-a init script 清理宿主
    # ★ 2026-10-03 P5：夹具结构对齐。`test_replay_conversation_read` 加载
    #   **冻结快照**（sha256 校验，旧结构无 msg_code）后跑一次幂等迁移，
    #   与生产启动路径一致 —— 不可改夹具（破坏冻结样本真实性），
    #   也不可回退读侧 SQL（会丢存量兼容兜底）。
    #   迁移是纯 DDL/DML，**无网络**；本模块由
    #   `test_replay_gates.TestAuxModulesHaveNoNetwork` **主动扫过**
    #   （AUX 集合本身只参与范围对账、不自动扫网络，故必须显式扫）。
    "database",
    # ⚠️ 对账粒度是**前两段**（`_scan_replay_scope_coverage` 取
    #   `nm.split('.')[:2]`）。`database` 是单段模块（backend/database.py），
    #   `from database import _migrate_schema` 被解析成
    #   `database._migrate_schema`，只登记 `database` 匹配不上 ⇒ 并列登记两段形式。
    "database._migrate_schema",
}

#: 🔴 上者中**回放期间真的会被执行**的顶层模块（必须证明无网络调用）。
#:
#: `REPLAY_AUX_MODULES` 只是「范围对账时不误报」的登记集合，**不含**网络检查
#: 语义 —— 既有成员（`daemon.browser_daemon` 等）本就含网络调用。
#: 只有本集合里的模块，其网络调用才会让「零网络回放」名存实亡，故单独列出。
#:
#: ★ 2026-10-03：`database` —— P5 拆列后 `test_replay_conversation_read`
#:   在 setUp 里跑 `_migrate_schema` 对齐冻结夹具结构。
AUX_EXECUTED_IN_REPLAY_TOP = {"database"}

#: 已知基线：这些「回放不执行」的函数含网络调用，登记以免误报。
#: 新增未登记网络调用点（任何函数）→ G7b2 变红。
REPLAY_UNEXECUTED_NET_ALLOWLIST = {
    ("auto_dm.conversation_capture", "fetch_conversation_history"),
    ("auto_dm.conversation_capture", "capture_userinfo_via_browser"),
    ("api.messages", "_http_get_json"),
    ("api.messages", "_http_post_json"),
}


def _alias_map(tree):
    """收集 AST 内所有网络模块的本地别名（含函数内 import）。"""
    aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                top = a.name.split(".")[0]
                if top in NETWORK_MODULES:
                    aliases[a.asname or top] = top
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.split(".")[0] in NETWORK_MODULES:
                for a in node.names:
                    aliases[a.asname or a.name] = node.module.split(".")[0]
    return aliases


def _dotted(func_node):
    parts = []
    n = func_node
    while isinstance(n, ast.Attribute):
        parts.append(n.attr)
        n = n.value
    if isinstance(n, ast.Name):
        parts.append(n.id)
    return ".".join(reversed(parts))


def scan_network_calls(module_name: str, funcs=None, path=None, source=None):
    """扫描模块内（或指定函数内）的网络调用点，返回 [(函数名, 调用链)].

    P3-5：以「调用链根名是否为网络模块别名」判定 —— 避免 `x.get()` 这类
    字典访问被误判。`funcs=None` 扫全模块；给定则只扫这些函数体。

    `path` / `source` 可显式指定被扫源码（供「注入网络调用 → 门禁必须变红」
    的反证测试使用；不给则 import 真实模块）。
    """
    import importlib
    import inspect as _inspect
    if source is None:
        if path is not None:
            with open(path, encoding="utf-8") as _f:
                source = _f.read()
        else:
            mod = importlib.import_module(module_name)
            source = _inspect.getsource(mod)
            path = getattr(mod, "__file__", module_name)
    tree = ast.parse(source, filename=path or module_name)
    aliases = _alias_map(tree)

    def _hits_in(nodes, fname):
        out = []
        for st in nodes:
            for node in ast.walk(st):
                if not isinstance(node, ast.Call):
                    continue
                dotted = _dotted(node.func)
                root = dotted.split(".")[0]
                attr = dotted.split(".")[-1]
                if root in aliases and attr in NETWORK_METHODS:
                    out.append((fname, dotted))
                # 裸调用 urlopen(...)（from urllib.request import urlopen）
                elif not isinstance(node.func, ast.Attribute) \
                        and dotted in ("urlopen", "urlretrieve"):
                    out.append((fname, dotted))
        return out

    out = []
    if funcs is None:
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out += _hits_in([node], node.name)
    else:
        wanted = set(funcs)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                    and node.name in wanted:
                out += _hits_in([node], node.name)
    return out


def _scan_replay_scope_coverage():
    """范围对账：回放用例 import 的**本仓产品模块**必须全在声明清单内。

    只关心「本仓一等包」（backend/<top>.py 或 backend/<top>/ 真实存在）——
    标准库/第三方（patchright/fastapi/subprocess/...）与 `replay` 自身忽略。
    目的：新增**产品依赖**时扫描范围不得静默缩水（新增即变红）。
    """
    pkg_dir = os.path.dirname(os.path.abspath(__file__))
    tests_dir = os.path.dirname(pkg_dir)
    backend = tests_dir
    known = set(REPLAY_EXERCISED_MODULES) | set(REPLAY_AUX_MODULES)

    def _is_first_party(top: str) -> bool:
        if top in REPLAY_SCOPE_IGNORE_TOP:
            return False
        return (os.path.isfile(os.path.join(backend, top + ".py"))
                or os.path.isdir(os.path.join(backend, top)))

    unknown = set()
    for fn in sorted(os.listdir(tests_dir)):
        if not (fn.startswith("test_replay_") and fn.endswith(".py")):
            continue
        with open(os.path.join(tests_dir, fn), encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=fn)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                cands = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                # `from services import env_audit` → services.env_audit
                cands = [f"{node.module}.{a.name}" for a in node.names] \
                    or [node.module]
            else:
                continue
            for nm in cands:
                top = nm.split(".")[0]
                if not _is_first_party(top):
                    continue
                if nm in known:
                    continue
                two = ".".join(nm.split(".")[:2])
                if two in known:
                    continue
                unknown.add(two)
    return sorted(unknown), sorted(known)


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

    # ---- G7 回放层零打网/浏览器 import（replay 包自身）----
    hits = _scan_forbidden_imports(pkg_dir)
    rows.append(_ok("G7 replay 包零打网/浏览器 import", not hits, f"{hits}"))

    # ---- G7b 真实回放路径零网络调用（P3-5：扫描范围覆盖被执行的产品模块）----
    try:
        bad = []
        all_hits = []
        for mod_name, fn in REPLAY_EXERCISED_ENTRYPOINTS:
            h = scan_network_calls(mod_name, funcs=[fn])
            all_hits += h
            bad += h
        # 「回放不执行」的函数含网络调用：登记为基线，但**未登记的**必须变红
        unregistered = []
        for mod_name in sorted({m for m, _ in REPLAY_UNEXECUTED_NET_ALLOWLIST} |
                               {m for m, _ in REPLAY_EXERCISED_ENTRYPOINTS}):
            for fname, dotted in scan_network_calls(mod_name):
                if (mod_name, fname) in REPLAY_UNEXECUTED_NET_ALLOWLIST:
                    continue
                if (mod_name, fname) in REPLAY_EXERCISED_ENTRYPOINTS:
                    continue  # 已在上面严格判过
                # 既非执行入口、又非登记基线 ⇒ 新增未登记网络点
                unregistered.append((mod_name, fname, dotted))
        rows.append(_ok(
            "G7b 执行入口零网络调用（conversation_capture/api.messages）",
            not bad, f"命中 {bad}" if bad else "4 个入口均无网络调用"))
        rows.append(_ok(
            "G7b2 无未登记网络调用点（防扫描范围缩水/新增打网）",
            not unregistered, f"{unregistered}" if unregistered else "无"))
    except Exception as e:  # noqa: BLE001
        rows.append(_ok("G7b 执行入口零网络调用（conversation_capture/api.messages）",
                        False, f"{type(e).__name__}: {e}"))

    # ---- G7c 范围对账：回放用例 import 的产品模块必须全在声明清单内 ----
    try:
        missing, imported = _scan_replay_scope_coverage()
        rows.append(_ok("G7c 扫描范围覆盖回放用例 import 的产品模块",
                        not missing,
                        f"未纳入：{missing}（已纳入 {imported}）" if missing
                        else f"{imported}"))
    except Exception as e:  # noqa: BLE001
        rows.append(_ok("G7c 扫描范围覆盖回放用例 import 的产品模块", False,
                        f"{type(e).__name__}: {e}"))

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
