# -*- coding: utf-8 -*-
"""入口模块身份归一 · **覆盖全部入口**的机械门禁（审计 P0-4 / S-3 结构性解法）。

## 这一条守的是什么（真实事故族，2026-09-23）

P3-5 大组件拆分**插入了此前不存在的跨模块边界**：入口文件以 `__main__` 身份
（PyInstaller frozen）运行，同时又被同进程**按包名 import** ⇒ 同一份源码被加载
两次，`sys.modules` 两个键并存 ⇒ 模块级可变状态（`_state` / sink / 缓存）**分叉**。

| 事故 | 入口 | 反向 import | 后果 |
|---|---|---|---|
| REG-01（已修 `2ce728a`） | `daemon/browser_daemon.py` | `bcc_routes.py` `from daemon.browser_daemon import _state` | 空 `_state` → `BCC-051` 熔断、容器永不启动、日志 sink 被删致 **0 字节** |
| 同类（本门禁覆盖） | `main.py` | `api/accounts.py` `from main import app` | 第二份 app → `app.state` 无 `adm` → 「引擎实例不可用」 |
| 潜伏（P0-4，本次修） | `daemon/recv_daemon.py` | `test_send_gate_config.py` / `verify_handledispatch.py` `import daemon.recv_daemon` | 反向着读到**空** `_state`（accounts=[] port=0） |

**定式**（与 `browser_daemon.py:78`、`main.py:74`、`recv_daemon.py` 同源）：
凡「以 `__main__` 运行、又被同进程按包名 import」的入口，都必须在**任何反向 import
发生之前**把自身登记为规范模块名 —— `sys.modules.setdefault("<规范名>", sys.modules[__name__])`。

## 为什么是「覆盖全部入口的机械门禁」而不是逐个人肉修（B 报告 S-3）

逐个人肉修只能补上**已经知道**的那几个入口；下一次拆分再引入一条反向 import，
就又漏一个。本门禁改为**枚举式**：
  ① 从 **PyInstaller spec 的 `Analysis(['<entry>.py'])`** 反查「产品入口」（判据有唯一来源，
     非硬编码猜测）；
  ② 断言每个产品入口都含归一语句（**AST 级**校验，注释掉即不中 —— 修 P3-4 的「纯子串匹配」弱点）；
  ③ 断言归一语句出现在**任何模块级状态创建之前**；
  ④ 枚举全仓 `if __name__ == "__main__":` 候选 ∩ 「被按包名 import」= **双栖入口**，
     断言「双栖且未归一」的集合 ⊆ 一份**逐条写明理由**的豁免名单（新出现的=门禁变红）；
  ⑤ **行为探针**：真加载入口为 `__main__` → 再按包名 import → 断言 `_state` 是**同一对象**；
  ⑥ **负控**：造一个无归一入口 → 源码判据与行为探针都必须变红（证明它不是假门禁）。

不启应用即可跑：`cd backend && python test_entry_module_identity_guard.py`
"""

from __future__ import annotations

import ast
import glob
import os
import re
import subprocess
import sys
import tempfile
import unittest

_BACKEND = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_BACKEND)

# 产品入口的**兜底集合**（spec 解析失败时使用；解析成功时以 spec 为准）
_CORE_ENTRIES = [
    os.path.join("main.py"),
    os.path.join("daemon", "browser_daemon.py"),
    os.path.join("daemon", "recv_daemon.py"),
]
# 核心入口必须**始终**在要求集合内 —— 防「spec 扫描失败 → 空集 → 门禁假绿」
_MUST_INCLUDE = {
    "main": "main.py",
    "daemon.browser_daemon": os.path.join("daemon", "browser_daemon.py"),
    "daemon.recv_daemon": os.path.join("daemon", "recv_daemon.py"),
}

# 双栖入口的**已知豁免**（逐条写理由；不在表内且未归一的 = 新敞口 = 门禁变红）。
# 判据：`__main__` 是**开发/自检脚本**（非 PyInstaller 入口），产品内只按库名 import，
# 因此不存在「同进程既冻结运行又按包名 import」形态。
_ALLOWLIST_DUAL_HOST = {
    "dy_live.server": "开发用 __main__（硬编码 live_id 手测），产品内仅 `from dy_live.server import DouyinLive`（库用法）；无 PyInstaller spec",
    "dy_apis.douyin_api": "开发用 __main__ 调试块，产品内仅按库名 import；无 spec",
    "dy_apis.login_api": "开发用 __main__ 调试块，产品内仅按库名 import；无 spec",
    "utils.bd_ticket": "开发用 __main__ 自检块，产品内仅按库名 import；无 spec",
    "utils.sm3": "开发用 __main__ 自检块，产品内仅按库名 import；无 spec",
}

_SKIP_DIR_PARTS = {"__pycache__", "build", "dist", "node_modules", ".git", ".venv", "venv"}


# ============================================================================
# ① 发现：入口候选 / 产品入口 / 双栖入口
# ============================================================================

_MAIN_GUARD_RE = re.compile(
    r'^\s*if\s+__name__\s*==\s*[\'"]__main__[\'"]\s*:', re.M)
_SPEC_ENTRY_RE = re.compile(r"Analysis\(\s*\[\s*[\"']([^\"']+\.py)[\"']")


def _iter_py_files(base: str):
    for root, dirs, fns in os.walk(base):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIR_PARTS]
        for fn in fns:
            if fn.endswith(".py"):
                yield os.path.join(root, fn)


def _canonical(path: str, backend: str = _BACKEND) -> str:
    rel = os.path.relpath(path, backend).replace("\\", "/")
    return rel[:-3].replace("/", ".")


def has_normalization(path: str, canonical: str | None = None) -> bool:
    """源码契约（**AST 级**，非子串）：存在真正的 `sys.modules.setdefault("<canonical>", ...)` 调用。

    用 AST 而非 `in src` 是必须的：纯子串匹配会把**被注释掉**的那行也算通过
    （审计 P3-4 记录的假绿形态）。AST 只认真正的 Call 节点，注释天然被忽略。
    """
    canonical = canonical or _canonical(path)
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            tree = ast.parse(f.read())
    except (OSError, SyntaxError):
        return False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if not (isinstance(f, ast.Attribute) and f.attr == "setdefault"):
            continue
        base = f.value
        if not (isinstance(base, ast.Attribute) and base.attr == "modules"
                and isinstance(base.value, ast.Name) and base.value.id == "sys"):
            continue
        if node.args and isinstance(node.args[0], ast.Constant) \
                and node.args[0].value == canonical:
            return True
    return False


def _first_module_state_line(path: str) -> int:
    """模块级状态创建点的最小行号（用于断言「归一在状态创建之前」）。

    只认**模块顶层**（col 0）的可变容器/应用赋值，避免把函数内局部状态算进来。
    """
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            tree = ast.parse(f.read())
    except (OSError, SyntaxError):
        return 10 ** 9
    best = 10 ** 9
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                name = getattr(t, "id", None)
                if not name:
                    continue
                # 典型模块级状态：_state / app / _xxx_last / <CAPS>
                if (name in ("app",) or name.startswith("_state")
                        or name.endswith(("_last", "_cache", "_state"))):
                    best = min(best, node.lineno)
    return best


def discover_entry_candidates(backend: str = _BACKEND):
    """全仓「入口候选」= 含 `if __name__ == "__main__":` 的模块（相对路径）。"""
    out = []
    for p in _iter_py_files(backend):
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                src = f.read()
        except OSError:
            continue
        if _MAIN_GUARD_RE.search(src):
            out.append(os.path.relpath(p, backend).replace("\\", "/"))
    return sorted(out)


def discover_product_entries(backend: str = _BACKEND):
    """产品入口 = PyInstaller spec 的 `Analysis(['...entry.py'])` 目标（唯一判据来源）。

    返回 {canonical: relpath}；spec 扫描为空时回落 `_CORE_ENTRIES`（并保证核心三入口在内）。
    """
    found: dict[str, str] = {}
    specs = glob.glob(os.path.join(_ROOT, "*.spec")) + \
        glob.glob(os.path.join(_ROOT, "backend", "build", "*", "*.spec"))
    for sp in specs:
        try:
            with open(sp, "r", encoding="utf-8", errors="replace") as f:
                txt = f.read()
        except OSError:
            continue
        for m in _SPEC_ENTRY_RE.finditer(txt):
            ep = m.group(1).replace("\\", "/")
            ep = os.path.normpath(ep)
            if not os.path.isabs(ep):
                ep = os.path.join(_ROOT, ep)
            try:
                rel = os.path.relpath(ep, backend).replace("\\", "/")
            except ValueError:
                continue
            if rel.startswith(".."):
                continue
            if os.path.isfile(os.path.join(backend, rel)):
                found[_canonical(os.path.join(backend, rel), backend)] = rel
    for rel in _CORE_ENTRIES:
        if os.path.isfile(os.path.join(backend, rel)):
            found.setdefault(_canonical(os.path.join(backend, rel), backend), rel)
    return dict(sorted(found.items()))


def discover_dual_host(backend: str = _BACKEND):
    """双栖入口 = 入口候选 ∩ 「被别处按包名 import」（canonical 名出现在 import 目标里）。"""
    _IMPORT_RE = re.compile(
        r"^\s*(?:from\s+([\w\.]+)\s+import|import\s+([\w\.]+))", re.M)

    imports: dict[str, set[str]] = {}
    for p in _iter_py_files(backend):
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                src = f.read()
        except OSError:
            continue
        for m in _IMPORT_RE.finditer(src):
            dotted = m.group(1) or m.group(2)
            imports.setdefault(dotted, set()).add(p)

    out = {}
    for rel in discover_entry_candidates(backend):
        c = _canonical(os.path.join(backend, rel), backend)
        importers = set()
        for dotted, fs in imports.items():
            if dotted == c or dotted.startswith(c + "."):
                importers |= {os.path.relpath(x, backend).replace("\\", "/") for x in fs}
        importers.discard(rel)
        if importers:
            out[c] = (rel, sorted(importers))
    return dict(sorted(out.items()))


# ============================================================================
# 行为探针
# ============================================================================

_PROBE_RECV = r'''
import os, sys, importlib.util, traceback
BACKEND = r"{backend}"
ENTRY = r"{entry}"
RESULT = r"{result}"
out = open(RESULT, "w", encoding="utf-8")
def emit(s):
    out.write(s + "\n"); out.flush()
sys.path.insert(0, BACKEND)
os.environ.setdefault("DY_APP_ROOT", os.path.join(os.environ["LOCALAPPDATA"], "Temp", "fixF"))
# recv_daemon.main() 会把 stdout/stderr 重定向到日志文件，故判据一律写 RESULT 文件。
sys.argv = ["recv_daemon_guard.exe", "--accounts", "GUARD_PROBE", "--port", "19996"]
import uvicorn
uvicorn.run = lambda *a, **k: None
try:
    spec = importlib.util.spec_from_file_location("__main__", ENTRY)
    m = importlib.util.module_from_spec(spec)
    sys.modules["__main__"] = m
    spec.loader.exec_module(m)          # 末尾 if __name__=="__main__": main() 会跑（已 stub uvicorn）
    m._state["accounts"] = ["GUARD_PROBE_ACCOUNT"]
    m._state["port"] = 19996
    import daemon.recv_daemon as rd     # 反向 import（test_send_gate_config.py 同形）
    emit("RESULT:" + ("SAME" if rd._state is m._state else "DIFF"))
    emit("ACCT:" + repr(rd._state.get("accounts")))
    emit("SAME_OBJ:" + str(rd is m))
except Exception:
    emit("PROBE_EXC:")
    emit(traceback.format_exc())
finally:
    out.close()
'''

_DUMMY_UNNORM = 'import sys\n_state = {"n": 0}\n\ndef main():\n    pass\n\nif __name__ == "__main__":\n    main()\n'
_DUMMY_NORM = ('import sys\nsys.modules.setdefault("dummy_entry", sys.modules[__name__])\n'
               '_state = {"n": 0}\n\ndef main():\n    pass\n\n'
               'if __name__ == "__main__":\n    main()\n')

_PROBE_DUMMY = r'''
import os, sys, importlib.util
TMP = r"{tmp}"
sys.path.insert(0, TMP)
spec = importlib.util.spec_from_file_location("__main__", os.path.join(TMP, "dummy_entry.py"))
m = importlib.util.module_from_spec(spec)
sys.modules["__main__"] = m
spec.loader.exec_module(m)
m._state["n"] = 42
import dummy_entry as d2
print("RESULT:" + ("SAME" if d2._state is m._state else "DIFF"))
print("N:" + str(d2._state.get("n")))
'''


def _run(code: str, timeout: int = 120):
    env = dict(os.environ)
    env.setdefault("DY_APP_ROOT", os.path.join(os.environ.get("LOCALAPPDATA", tempfile.gettempdir()), "Temp", "fixF"))
    return subprocess.run([sys.executable, "-c", code], capture_output=True,
                          text=True, cwd=_BACKEND, env=env, timeout=timeout)


def _probe_dummy(normalized: bool) -> str:
    with tempfile.TemporaryDirectory(prefix="entryguard_") as td:
        with open(os.path.join(td, "dummy_entry.py"), "w", encoding="utf-8") as f:
            f.write(_DUMMY_NORM if normalized else _DUMMY_UNNORM)
        proc = _run(_PROBE_DUMMY.format(tmp=td))
    out = (proc.stdout or "") + (proc.stderr or "")
    m = re.search(r"RESULT:(\w+)", out)
    return m.group(1) if m else f"NOPROBE({out[-400:]})"


# ============================================================================
# 测试
# ============================================================================

class TestEntryModuleIdentityGuard(unittest.TestCase):
    """覆盖全部入口的身份归一机械门禁。"""

    @classmethod
    def setUpClass(cls):
        cls.entries = discover_entry_candidates()
        cls.product = discover_product_entries()
        cls.dual = discover_dual_host()

    # ---- ① 产品入口都必须有归一 ----
    def test_all_product_entries_declare_normalization(self):
        self.assertTrue(self.product, "未发现任何产品入口（spec 解析失败且兜底为空）")
        # 核心三入口必须在要求集合内（防扫描失败 → 空集假绿）
        for c, rel in _MUST_INCLUDE.items():
            self.assertIn(c, self.product,
                          f"核心入口 {rel} 未进入要求集合（发现={list(self.product)}）")
        missing = [rel for c, rel in self.product.items()
                   if not has_normalization(os.path.join(_BACKEND, rel), c)]
        self.assertEqual(
            missing, [],
            "以下产品入口缺少模块身份归一 —— 凡「以 __main__ 运行、又被同进程按包名 "
            "import」的入口都必须 `sys.modules.setdefault(\"<规范名>\", sys.modules[__name__])`："
            f"{missing}")

    # ---- ② 归一必须在模块级状态创建之前 ----
    def test_normalization_precedes_module_state(self):
        bad = []
        for c, rel in self.product.items():
            p = os.path.join(_BACKEND, rel)
            src_line = self._setdefault_line(p, c)
            state_line = _first_module_state_line(p)
            if src_line is None or src_line >= state_line:
                bad.append(f"{rel}(setdefault@{src_line} vs state@{state_line})")
        self.assertEqual(bad, [],
                         "归一语句必须出现在任何模块级状态创建**之前**（否则第二份模块"
                         f"仍会先分叉/覆盖）：{bad}")

    @staticmethod
    def _setdefault_line(path: str, canonical: str):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                tree = ast.parse(f.read())
        except (OSError, SyntaxError):
            return None
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                if (isinstance(fn, ast.Attribute) and fn.attr == "setdefault"
                        and node.args and isinstance(node.args[0], ast.Constant)
                        and node.args[0].value == canonical):
                    return node.lineno
        return None

    # ---- ③ 覆盖：双栖入口 ⊆ 产品入口 ∪ 已豁免 ----
    def test_no_unexplained_dually_hosted_entry(self):
        unknown = []
        for c, (rel, importers) in self.dual.items():
            if c in self.product and has_normalization(os.path.join(_BACKEND, rel), c):
                continue
            if c in _ALLOWLIST_DUAL_HOST:
                continue
            unknown.append(f"{rel} ← {importers[:3]}")
        self.assertEqual(
            unknown, [],
            "出现**新的**「双栖入口且未归一」——这正是 REG-01 / P0-4 的敞口形态。"
            "请给该入口加 `sys.modules.setdefault(...)` 归一，或在本门禁的 "
            "_ALLOWLIST_DUAL_HOST 中**逐条写明理由**：\n  " + "\n  ".join(unknown))

    def test_dual_host_discovery_is_not_empty(self):
        """正控边界：双栖发现器必须真能发现已知双栖入口（防发现逻辑退化 → 空集假绿）。"""
        self.assertIn("daemon.recv_daemon", self.dual,
                      f"双栖发现器漏掉了 recv_daemon（发现={list(self.dual)}）")
        self.assertIn("main", self.dual)
        self.assertIn("daemon.browser_daemon", self.dual)

    # ---- ④ 行为探针：recv_daemon 的 __main__ 与包名同模块 ----
    def test_recv_daemon_main_and_package_are_same_module(self):
        with tempfile.TemporaryDirectory(prefix="entryguard_") as td:
            result = os.path.join(td, "res.txt")
            proc = _run(_PROBE_RECV.format(
                backend=_BACKEND,
                entry=os.path.join(_BACKEND, "daemon", "recv_daemon.py"),
                result=result))
            content = ""
            if os.path.isfile(result):
                with open(result, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
            blob = content + "\n" + (proc.stdout or "") + (proc.stderr or "")
            self.assertIn("RESULT:", blob,
                          f"行为探针未产出判据\n{blob[-1500:]}")
            self.assertIn(
                "RESULT:SAME", blob,
                "recv_daemon 模块身份分叉：`import daemon.recv_daemon` 拿到了第二份模块实例 "
                "—— 反向着会读到**空** `_state`（accounts=[] port=0）。"
                f"\n实测输出：{blob[-800:]}")
            self.assertIn("SAME_OBJ:True", blob)

    # ---- ⑤ 负控：无归一入口 → 源码判据与行为探针都必须变红 ----
    def test_negative_control_unnormalized_entry_fails(self):
        """负控（真实缺陷向量）：造一个含 `__main__` 但**无归一**的入口。

        ① 源码判据必须判「无归一」；
        ② 行为探针必须报 DIFF（证明「未归一 ⇒ 状态必然分叉」这一机理真的被探针抓到）。
        另附正控：加了归一语句的同形入口必须为 SAME（证明探针**不是**恒红装饰）。
        """
        with tempfile.TemporaryDirectory(prefix="entryguard_") as td:
            un = os.path.join(td, "dummy_entry.py")
            with open(un, "w", encoding="utf-8") as f:
                f.write(_DUMMY_UNNORM)
            self.assertFalse(
                has_normalization(un, "dummy_entry"),
                "源码判据把「无归一的入口」误判为有归一（假门禁）。")
            # 注释掉同一行也必须判「无归一」（AST 判据，修 P3-4 子串假绿）
            commented = os.path.join(td, "commented.py")
            with open(commented, "w", encoding="utf-8") as f:
                f.write('# sys.modules.setdefault("commented", sys.modules[__name__])\n'
                        'if __name__ == "__main__":\n    pass\n')
            self.assertFalse(
                has_normalization(commented, "commented"),
                "源码判据把**被注释掉**的归一语句也算通过（P3-4 同款子串假绿）。")

        self.assertEqual(_probe_dummy(normalized=False), "DIFF",
                         "行为探针对「无归一入口」没有报 DIFF —— 探针抓不到分叉机理（假门禁）。")
        self.assertEqual(_probe_dummy(normalized=True), "SAME",
                         "行为探针对「已归一入口」也报非 SAME —— 探针恒红，正控失败。")

    def test_negative_control_recv_daemon_without_normalization_would_fork(self):
        """负控（真实文件形态）：把 recv_daemon 的 setdefault 行注掉 → 还原事故形态。
        不真改源文件：拷一份到 tmp、注释该行后跑行为探针，必须 DIFF。"""
        src_path = os.path.join(_BACKEND, "daemon", "recv_daemon.py")
        with open(src_path, "r", encoding="utf-8") as f:
            src = f.read()
        mutated = re.sub(r'(?m)^(\s*)sys\.modules\.setdefault\("daemon\.recv_daemon".*$',
                         r'\1# sys.modules.setdefault("daemon.recv_daemon", sys.modules[__name__])  # NEGCTRL',
                         src, count=1)
        self.assertNotEqual(src, mutated, "未能在源码中定位 recv_daemon 的归一语句")
        with tempfile.TemporaryDirectory(prefix="entryguard_") as td:
            entry = os.path.join(td, "recv_daemon_neg.py")
            with open(entry, "w", encoding="utf-8") as f:
                f.write(mutated)
            result = os.path.join(td, "res.txt")
            proc = _run(_PROBE_RECV.format(backend=_BACKEND, entry=entry, result=result))
            content = ""
            if os.path.isfile(result):
                with open(result, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
            blob = content + "\n" + (proc.stdout or "") + (proc.stderr or "")
            self.assertIn("RESULT:DIFF", blob,
                          "注掉归一语后探针未变红 —— 该门禁无法证明会拒绝事故形态。"
                          f"\n{blob[-800:]}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
