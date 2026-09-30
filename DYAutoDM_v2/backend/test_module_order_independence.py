# -*- coding: utf-8 -*-
"""M-28 门禁：测试模块必须**模块顺序无关**（乱序重排后仍 0 failures / 0 errors）。

## 被守的缺陷（M-28，2026-09-29 实测定位）

`unittest discover` 默认按**文件名排序**加载 —— 这个排序恰好掩盖了顺序依赖。
把收集到的模块 suite 按 `random.seed(N)` 打乱**模块顺序**（模块内用例顺序保持）
再跑，就暴露出「单跑全绿、乱序红」。本机复现（50 个争根模块，593 用例）：

    seed=7    ran=593 failures=1 errors=8
    seed=1    ran=593 failures=1 errors=8
    seed=42   ran=593 failures=7 errors=1
    seed=2024 ran=593 failures=5 errors=9

失败集中在 4 个模块，全部是**跨模块污染**，断言本身没错：

  ① **`dd.cfg` 被永久换成 bound method**（7 项，主毒源）
     毒源 `test_uid_sink_ext.T3UidSinkExtTest`：
       setUpClass  `cls._orig = dd.cfg`   ← 把**函数**存成**类属性**
       tearDown    `dd.cfg = self._orig`  ← 经**描述符协议**取回 ⇒ bound method
     于是「还原」实际把 `dd.cfg` 换成 `bound method cfg of <TestCase>`，之后任何
     `dd.cfg("X")` 的 name 位置收到的是 TestCase 实例 ⇒
     `KeyError: <test_uid_sink_ext.T3UidSinkExtTest testMethod=test_zero_regression_defaults_off>`
     受害者：test_dm_dispatch_config 5 项、test_send_pacing_and_kind 2 项。

  ② **`DY_APP_ROOT` 只在**导入期**设一次**（2 项）
     `test_config_tag` 用 `os.environ["DY_APP_ROOT"] = <固定目录>`、
     `test_send_pacing_and_kind` 用 `os.environ.setdefault(...)`。
     而 `database.get_db()` **每次调用**都按**当前** DY_APP_ROOT 做会员一致性
     校验（不一致即重连）⇒ 导入期设的根在执行期会被别的模块盖掉 ⇒ 读到别人的库。
     受害者：test_config_tag.test_cfg_with_account
     （`TypeError: cfg() got multiple values for argument 'account'`）。

## 修法（最小改动、不碰 services 本体）

  · 受害模块在**导入期**抓一份原始 `cfg` 函数强引用 `_PRISTINE_CFG`
    （此刻必为原始实现 —— 所有 import 都发生在任何用例执行之前）；
  · 每个用例 `setUp` 里调 `_n1_m28_pin_root()`：**重钉** DY_APP_ROOT
    （一次性 `mkdtemp`）+ 无条件归还 `dd.cfg`。

标识符（父会话 grep 用）：`N1_M28_PIN_ROOT` / `_n1_m28_pin_root` / `_PRISTINE_CFG`

## 判据

  R1 **乱序行为判据（主）**：对 `_CHAIN` 里的模块，用 `SEEDS` 中每个 seed 打乱
     模块顺序，在**独立子进程**里按该顺序真跑，断言 failures=0 且 errors=0。
  R2 **钉根范式哨兵**：`_TARGETS`（= 本次修复的 7 个模块）源码必须出现
     `N1_M28_PIN_ROOT`，且**不得**再用 `os.environ.setdefault("DY_APP_ROOT", ...)`
     （setdefault 在已有根时是**空操作** = 假隔离）。
     注：哨兵作用域**仅限** `_TARGETS` —— 邻居模块只是「争根陪跑」，不强制。
  R3 **cfg 归还哨兵（机制级）**：把 `dd.cfg` 人为污染后，调任一受害模块的
     `_n1_m28_pin_root()`，必须把**原始函数**还回去 —— 直接守 ① 号根因的
     不变量，而不是等它炸出 KeyError。
  R4 **负控（D-07）**：
     · R1：合成一对最小模块（A 在用例里改写共享 `dd.cfg` 且不还原，B 断言它
       仍是原始函数）⇒ **污染源在前**必须失败、反序必须通过 —— 证明 R1 判据
       真能抓到顺序依赖，不是恒绿。
     · R2：临时目录注入缺标识符 / 用 setdefault 的模块 ⇒ 哨兵必报红；撤除 ⇒ 复绿。

跑法：cd backend && python -m unittest test_module_order_independence -v
"""
from __future__ import annotations

import os
import random
import re
import subprocess
import sys
import tempfile
import unittest

_BACKEND = os.path.dirname(os.path.abspath(__file__))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

# ── R2 哨兵作用域：本次 N1 修复的 7 个模块（**只**管这些）
_TARGETS = (
    "test_dm_dispatch_config",
    "test_config_tag",
    "test_send_pacing_and_kind",
    "test_replay_conversation_read",
    "test_task_scheduler_gates",
    "test_m20_search_transport",
    "test_abogus_host_guards",
)

# ── R1 乱序链：7 个目标 + 毒源 + 一批争根邻居
#    不加毒源/邻居就无法暴露「dd.cfg 被换掉」和「执行期根被盖掉」两类问题。
_CHAIN = (
    "test_dm_dispatch_config",
    "test_config_tag",
    "test_send_pacing_and_kind",
    "test_uid_sink_ext",
    "test_task_scheduler_gates",
    "test_replay_conversation_read",
    "test_m20_search_transport",
    "test_abogus_host_guards",
    "test_config_isolation",
    "test_app_config",
    "test_m2_secsdk_send_side",
    "test_cross_account_sink",
    "test_features_wiring",
    "test_settings_api",
    # 2026-09-30（审计整改）：补入实际争根模块 —— 它用了 N1_M28_PIN_ROOT 却不在链内，
    # 是既有覆盖缺口（新增争根模块必须登记，否则乱序面覆盖不到它）。
    "test_lead_disposition_guard",
)

# 至少 3 组 seed，且含台账定位用的 seed=7
SEEDS = (7, 42, 2024)

PIN_MARKER = "N1_M28_PIN_ROOT"


def shuffled(modules, seed: int) -> list:
    """按 seed 打乱**模块顺序**（模块内用例顺序由 unittest 自身保持）。"""
    order = list(modules)
    random.Random(seed).shuffle(order)
    return order


def _run_unittest(modules, cwd, extra_pythonpath=None, timeout=900):
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    if extra_pythonpath:
        env["PYTHONPATH"] = os.pathsep.join(
            [extra_pythonpath]
            + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    return subprocess.run(
        [sys.executable, "-m", "unittest"] + list(modules),
        cwd=cwd, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout)


def _counts(text: str):
    """从 unittest 输出解析 (failures, errors)；解析不出来返回 None（不得算通过）。"""
    m = re.search(r"FAILED \(failures=(\d+), errors=(\d+)\)", text or "")
    if m:
        return int(m.group(1)), int(m.group(2))
    if re.search(r"^OK$", text or "", re.M):
        return 0, 0
    return None


# ────────────────────────────────────────────────── R2 哨兵内核（负控可复用）
def scan_pin_violations(root: str, targets=_TARGETS):
    """返回 [(filename, reason)]：目标模块缺钉根标识符 / 仍用 setdefault 假隔离。"""
    out = []
    sd = re.compile(r"""os\.environ\.setdefault\(\s*["']DY_APP_ROOT["']""")
    for name in targets:
        path = os.path.join(root, name + ".py")
        if not os.path.exists(path):
            continue
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        if PIN_MARKER not in src:
            out.append((name, f"缺钉根标识符 {PIN_MARKER}（仍是「只在模块级设一次根」形态）"))
        if sd.search(src):
            out.append((name, "仍用 os.environ.setdefault('DY_APP_ROOT', ...) —— 已有根时是空操作（假隔离）"))
    return out


class TestR1ShuffledOrderIsGreen(unittest.TestCase):
    """R1 主判据：多组 seed 打乱模块顺序后，子进程真跑必须 0 failures / 0 errors。"""

    def test_each_seed_runs_clean(self):
        for seed in SEEDS:
            order = shuffled(_CHAIN, seed)
            with self.subTest(seed=seed):
                r = _run_unittest(order, _BACKEND)
                got = _counts((r.stderr or "") + (r.stdout or ""))
                self.assertIsNotNone(
                    got,
                    f"seed={seed} 无法解析 unittest 结果（判据失效，不得算通过）\n"
                    f"returncode={r.returncode}\n{(r.stderr or '')[-2000:]}")
                self.assertEqual(
                    got, (0, 0),
                    f"M-28 破防：seed={seed} 顺序下 failures={got[0]} errors={got[1]}\n"
                    f"模块顺序={order}\n{(r.stderr or '')[-2500:]}")

    def test_seed_actually_changes_order(self):
        """元判据：不同 seed 必须给出**不同**顺序，否则 R1 退化成重复跑同一顺序。"""
        orders = {tuple(shuffled(_CHAIN, s)) for s in SEEDS}
        self.assertGreater(
            len(orders), 1,
            f"各 seed 打乱出的模块顺序完全相同 —— R1 退化为单次执行: {orders}")


class TestR2PinParadigmSentinel(unittest.TestCase):
    """R2：源码哨兵 —— 必须钉根，且不得再靠 setdefault 假隔离。"""

    def test_targets_pin_root(self):
        bad = scan_pin_violations(_BACKEND)
        self.assertEqual(bad, [],
                         "M-28 破防：目标模块未采用钉根范式 —— "
                         + "；".join(f"{n}: {r}" for n, r in bad))

    def test_negative_control_defects_turn_red(self):
        """R4-b 负控：注入缺标识符 / setdefault 的模块 ⇒ 哨兵必报红。"""
        with tempfile.TemporaryDirectory(prefix="m28_r2_") as d:
            good = os.path.join(d, "test_good.py")
            with open(good, "w", encoding="utf-8") as f:
                f.write("# N1_M28_PIN_ROOT\nx = 1\n")
            self.assertEqual(scan_pin_violations(d, targets=("test_good",)), [],
                             "合格样本不应报红")

            badf = os.path.join(d, "test_bad.py")
            with open(badf, "w", encoding="utf-8") as f:
                f.write("import os\nos.environ['DY_APP_ROOT'] = '/tmp/x'\n")
            found = scan_pin_violations(d, targets=("test_bad",))
            self.assertEqual(len(found), 1, f"未抓到缺钉根标识符的模块: {found}")
            self.assertIn(PIN_MARKER, found[0][1])

            with open(badf, "w", encoding="utf-8") as f:
                f.write("# N1_M28_PIN_ROOT\nimport os\n"
                        "os.environ.setdefault('DY_APP_ROOT', '/tmp/x')\n")
            found2 = scan_pin_violations(d, targets=("test_bad",))
            self.assertEqual(len(found2), 1, f"未抓到 setdefault 假隔离: {found2}")
            self.assertIn("setdefault", found2[0][1])


class TestR3CfgIdentityRestored(unittest.TestCase):
    """R3：机制级判据 —— 受害模块的钉根函数必须能把 `dd.cfg` 还成**原始函数**。

    直接守 ① 号根因的不变量，而不是等它炸出 `KeyError: <TestCase ...>`。
    """

    # 只检查**声明了 _PRISTINE_CFG** 的模块（= 确实依赖 dd.cfg 的受害模块）；
    # 其余模块只钉根、不碰 dd.cfg，不应被本判据要求。
    # 2026-09-30（D-3）：+ `test_task_scheduler_gates` —— 其 B3 真调 `_gate_for_send`
    #   会走到 `dd.cfg(...)`，被毒源污染时以 `KeyError: <TestCase ...>` 假失败。
    _CFG_DEPENDENT = ("test_dm_dispatch_config", "test_config_tag",
                      "test_send_pacing_and_kind",
                      "test_task_scheduler_gates")

    def test_pin_root_restores_pristine_cfg(self):
        from services import dm_dispatch as dd
        from services.dm_dispatch import cfg as pristine

        for name in self._CFG_DEPENDENT:
            mod = __import__(name)
            fn = getattr(mod, "_n1_m28_pin_root", None)
            self.assertTrue(callable(fn), f"{name} 缺 _n1_m28_pin_root()")
            with self.subTest(module=name):
                dd.cfg = (lambda n, account="": None)   # 人为污染
                fn()                                    # 应无条件归还
                self.assertIs(dd.cfg, pristine,
                              f"{name} 的 _n1_m28_pin_root() 未归还 dd.cfg 原始实现")

    def test_cfg_dependent_modules_declare_pristine(self):
        """反哨兵：声明表必须**恰好等于**真正持有 _PRISTINE_CFG 的模块集合。

        防止「僵尸声明」（表里写了但代码里没有）与「漏登记」（代码里有但表没写）。
        """
        declared = set()
        for name in _TARGETS:
            path = os.path.join(_BACKEND, name + ".py")
            with open(path, "r", encoding="utf-8") as f:
                if "_PRISTINE_CFG" in f.read():
                    declared.add(name)
        self.assertEqual(declared, set(self._CFG_DEPENDENT),
                         "R3 声明表与源码实际持有 _PRISTINE_CFG 的模块集合不一致")


class TestR4NegativeControlOrderDependence(unittest.TestCase):
    """R4-a 负控：证明 R1 判据不是恒绿 —— 真有顺序依赖时必须报红。"""

    def test_injected_order_dependence_turns_red(self):
        with tempfile.TemporaryDirectory(prefix="m28_r4_") as d:
            with open(os.path.join(d, "m28_poison_a.py"), "w", encoding="utf-8") as f:
                f.write(
                    '"""合成污染源：在用例里改写共享的 dd.cfg 且不还原。"""\n'
                    "import unittest\n\n\n"
                    "class TestPoison(unittest.TestCase):\n"
                    "    def test_replace_shared_cfg(self):\n"
                    "        from services import dm_dispatch as dd\n"
                    "        dd.cfg = lambda name, account='': 999\n"
                    "        self.assertTrue(True)\n")
            with open(os.path.join(d, "m28_victim_b.py"), "w", encoding="utf-8") as f:
                f.write(
                    '"""合成受害模块：断言 dd.cfg 仍是原始函数（未钉根 ⇒ 会被污染）。"""\n'
                    "import unittest\n\n\n"
                    "class TestVictim(unittest.TestCase):\n"
                    "    def test_cfg_is_pristine(self):\n"
                    "        from services import dm_dispatch as dd\n"
                    "        self.assertEqual(dd.cfg.__name__, 'cfg',\n"
                    "                         'dd.cfg 被换成替身且未还原')\n")

            pp = os.pathsep.join([d, _BACKEND])
            poison_first = _run_unittest(
                ["m28_poison_a", "m28_victim_b"], d, extra_pythonpath=pp, timeout=300)
            self.assertNotEqual(
                poison_first.returncode, 0,
                "R1 判据失效：污染源在前的顺序竟未失败 —— 本门禁抓不到顺序依赖")

            victim_first = _run_unittest(
                ["m28_victim_b", "m28_poison_a"], d, extra_pythonpath=pp, timeout=300)
            self.assertEqual(
                victim_first.returncode, 0,
                f"R4 基准异常：反序本应通过\n{(victim_first.stderr or '')[-1500:]}")


class TestR5NoDescriptorRoundTrip(unittest.TestCase):
    """R5：禁止「模块函数存进类属性、再经 self./cls. 取回赋给模块」这一毒源范式。

    直接守 M-28 ① 号根因的**形态**（不是等它炸出 KeyError）：把函数存成类属性后，
    经**描述符协议**取回会得到 bound method ⇒「还原」实际把模块属性永久换成
    `bound method <fn> of <TestCase>`，污染后续所有消费者。

    判据：`_CHAIN` 模块源码中不得出现「`<mod>.<attr> = self.<x>` / `= cls.<x>`」形态。
           正确写法是存进**非描述符容器**（模块级变量 / list 盒 / staticmethod），
           例如 `dd.cfg = _orig_cfg`（本仓毒源已按此修复，见 test_uid_sink_ext）。
    """

    # 模块属性 ← 类/实例属性（描述符往返）。这是 M-28 ① 号根因的**唯一必要形态**。
    #   ⚠️ 只有**类属性**存储（`cls._x = <函数>`）才会触发描述符绑定：
    #      实例属性（`self._x = <函数>`）取回时是原值，不绑定 ⇒ 不构成毒源。
    #      故判据 = 「先有 `cls.<n> =` 存储，再有 `Mod.attr = self./cls.<n>` 取回」。
    _CLS_STORE = re.compile(r"^\s*cls\.(\w+)\s*=", re.M)
    _ROUNDTRIP = re.compile(
        r"^\s*\w+\.\w+\s*=\s*(?:self|cls)\.(\w+)\s*(?:#.*)?$", re.M)

    def _scan(self, root, names):
        out = []
        for name in names:
            path = os.path.join(root, name + ".py")
            if not os.path.exists(path):
                continue
            with open(path, "r", encoding="utf-8") as f:
                src = f.read()
            class_attrs = set(self._CLS_STORE.findall(src))
            if not class_attrs:
                continue
            for i, line in enumerate(src.splitlines(), 1):
                m = self._ROUNDTRIP.match(line)
                if m and m.group(1) in class_attrs:
                    out.append((name, i, line.strip()))
        return out

    def test_chain_modules_have_no_descriptor_roundtrip(self):
        bad = self._scan(_BACKEND, _CHAIN)
        self.assertEqual(
            bad, [],
            "M-28 毒源形态复发（模块属性经 self./cls. 取回 = 描述符往返）：\n"
            + "\n".join(f"  {n}:{i}  {ln}" for n, i, ln in bad))

    def test_negative_control_descriptor_roundtrip_turns_red(self):
        """R5 负控：注入毒源形态 ⇒ 必红；正确形态（模块级变量容器）⇒ 绿。"""
        with tempfile.TemporaryDirectory(prefix="m28_r5_") as d:
            bad = os.path.join(d, "test_poison.py")
            with open(bad, "w", encoding="utf-8") as f:
                f.write(
                    "class T:\n"
                    "    @classmethod\n"
                    "    def setUpClass(cls):\n"
                    "        cls._orig = dd.cfg\n"
                    "    def tearDown(self):\n"
                    "        dd.cfg = self._orig\n")
            found = self._scan(d, ("test_poison",))
            self.assertEqual(len(found), 1, f"未抓到描述符往返毒源: {found}")
            self.assertEqual(found[0][1], 6, f"应精确报在 tearDown 行: {found}")

            with open(bad, "w", encoding="utf-8") as f:
                f.write(
                    "_orig_cfg = dd.cfg\n\n\n"
                    "class T:\n"
                    "    def tearDown(self):\n"
                    "        dd.cfg = _orig_cfg\n")
            self.assertEqual(self._scan(d, ("test_poison",)), [],
                             "正确形态（模块级变量容器）不应报红")


if __name__ == "__main__":
    unittest.main(verbosity=2)
