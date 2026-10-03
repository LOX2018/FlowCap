# -*- coding: utf-8 -*-
"""M-17 门禁：测试模块之间必须**顺序无关**（`unittest discover` 单进程共存）。

## 被守的缺陷（2026-09-28 定位并修复，v0.45.86）

`unittest discover` 把全部 `test_*.py` 导入**同一解释器**，而 `database` 是
进程级单例。历史上两类写法制造进程级**全局分裂**，表现为「单跑绿、全量红」：

  ① **重复模块对象**：`del sys.modules["database"/"services.*"]`（或 `.pop`）
     后重导入 ⇒ 同一逻辑模块出现**两个对象** —— 先前导入者持旧对象、
     `sys.modules` 放新对象 ⇒ 两边连的**不是同一个 SQLite 文件**。
     实测证据：全量 collection 后 `ai_agent.database is sys.modules["database"]`
     → `False`。由此产生 **12 项**假失败（`test_upstream_write_align_t1_t2` 9、
     `test_ai_agent` 2、`test_p2_live_guards` 1）。
     毒源（导入期调用者最致命）：`test_model_hub_v2._reimport()` 在**模块级**执行。

  ② **共享可调用属性被改写而不还原**：`test_live_endpoint_honesty` 把
     `DouyinAPI.sendMsgInRoom` 换成计数桩后**从不还原** ⇒ 后续
     `test_upstream_write_align_t1_t2._capture()` 的
     `fn.__globals__ is module.__dict__` 断言炸 7F+2E。

## 判据

  O1 **行为（主判据）**：把「毒源 × 受害模块」放进**一个子进程**跑，
     顺序 A 与顺序 B 都必须 0 失败。子进程隔离保证不受本进程已导入状态影响。
  O2 **源码哨兵**：`backend/test_*.py` 内**不得**卸载 `sys.modules` 里的
     `database` / `services.*`；其它模块的卸载必须登记在 `_ALLOWED_UNLOADS`
     并逐条写明理由（照 G6 `_ALLOWED_ORPHANS` 的纪律：**不得**为「让门禁变绿」而加项）。
     用 AST 而非正则 —— 注释/文档串里出现 `sys.modules.pop` 字样**不算**违规
     （本文件自己不因此误报）。
  O3 **负控（D-07）**：
     · O2：临时目录注入一个含 `del sys.modules["database"]` 的模块 ⇒ 必报红；
       不含 ⇒ 复绿。
     · O1：合成一对最小模块（A 在用例里污染共享属性、B 断言原值）⇒
       **污染源在前**的顺序必须失败，反序必须通过 —— 证明本判据真能抓到顺序依赖。

跑法：cd backend && python -m unittest test_m17_order_independence -v
"""
import ast
import os
import subprocess
import sys
import tempfile
import unittest
import warnings

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_BACKEND = os.path.dirname(os.path.abspath(__file__))

# O2 豁免表：**仅**允许「卸载非 database/services.* 的模块」；每条必须写明理由。
# 判定时另有一条硬规则：**字面量**键为 "database" 或以 "services." 开头 ⇒
# 即使文件在此表中也照样报红（豁免面不得覆盖致命模式）。
_ALLOWED_UNLOADS = {
    "test_upstream_p4.py": (
        "T1/T2 上游对齐用例：为让 `api.messages` 重新绑定被打桩的 "
        "`database.get_db`，临时 pop `api.messages` 并在 tearDown 逐键还原原对象"
        "（M-12，2026-09-23）。只卸载 `api.messages`，不触碰 database/services.*。"
    ),
    "test_replay_conversation_read.py": (
        "仅在「api.messages 本就是本模块引入」时，把它移回**未导入态**（收尾还原，"
        "见文件内 2026-09-23 注释）。只卸载 `api.messages`，不触碰 database/services.*。"
    ),
    "test_login_channel_config.py": (
        "G-P8 行为级用例的 fixture 收尾：`_rpa_scan_login` 需要 `auto_dm.login_remote` "
        "**已导入**才能验证通道路由，故先注入桩模块、跑完在 `finally` 里 pop 掉"
        "（若它本非本模块引入则还原原对象）。只卸载 `auto_dm.login_remote`，"
        "不触碰 `database` / `services.*`（M-17 关心的正是那两者的进程级单例）。"
        "⚠️ 登记纪律：只因**确属合理的收尾还原**才登记，不是为让门禁变绿"
        "（照 G6 `_ALLOWED_ORPHANS` 同一条纪律）。"
    ),
}


def _is_sys_modules(node) -> bool:
    return (isinstance(node, ast.Attribute) and node.attr == "modules"
            and isinstance(node.value, ast.Name) and node.value.id == "sys")


def _iter_unloads(tree):
    """产出 (lineno, literal_key_or_None) —— 每次 `sys.modules` 卸载/删除。"""
    for node in ast.walk(tree):
        if isinstance(node, ast.Delete):
            for t in node.targets:
                if isinstance(t, ast.Subscript) and _is_sys_modules(t.value):
                    key = t.slice.value if isinstance(t.slice, ast.Constant) else None
                    yield node.lineno, (key if isinstance(key, str) else None)
        elif isinstance(node, ast.Call):
            f = node.func
            if (isinstance(f, ast.Attribute) and f.attr == "pop"
                    and _is_sys_modules(f.value)):
                key = None
                if node.args and isinstance(node.args[0], ast.Constant) \
                        and isinstance(node.args[0].value, str):
                    key = node.args[0].value
                yield node.lineno, key


def _is_fatal_key(key) -> bool:
    return bool(key) and (key == "database" or key.startswith("services."))


def scan_module_unload_violations(root: str):
    """扫描 root 下 `test_*.py`，返回 [(filename, lineno, reason)]。

    违规两类：
      · FATAL —— 字面量卸载 `database` / `services.*`（任何文件都不许）
      · UNAPPROVED —— 其它卸载但文件未登记进 `_ALLOWED_UNLOADS`
    """
    out = []
    for name in sorted(os.listdir(root)):
        if not (name.startswith("test_") and name.endswith(".py")):
            continue
        path = os.path.join(root, name)
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        try:
            with warnings.catch_warnings():
                # 只做语法树解析，不必被别的模块的历史 escape 警告刷屏
                warnings.simplefilter("ignore")
                tree = ast.parse(src)
        except SyntaxError:
            continue
        for lineno, key in _iter_unloads(tree):
            if _is_fatal_key(key):
                out.append((name, lineno,
                            f"卸载 sys.modules[{key!r}] —— 制造重复模块对象（M-17 致命模式）"))
            elif name not in _ALLOWED_UNLOADS:
                out.append((name, lineno,
                            "卸载 sys.modules 但文件未登记进 _ALLOWED_UNLOADS"))
    return out


class TestO2NoModuleUnloadHygiene(unittest.TestCase):
    """O2：源码哨兵 —— 不得卸载 database/services.*，其余卸载须登记理由。"""

    def test_no_fatal_or_unapproved_unloads(self):
        bad = scan_module_unload_violations(_BACKEND)
        self.assertEqual(
            bad, [],
            "M-17 破防：测试源码里存在拆散进程级模块的写法 —— "
            + "；".join(f"{n}:{ln} {r}" for n, ln, r in bad))

    def test_allowlist_entries_are_real_and_justified(self):
        """豁免面**恰好等于**已登记集合：不许有僵尸豁免项。"""
        for name, reason in _ALLOWED_UNLOADS.items():
            path = os.path.join(_BACKEND, name)
            self.assertTrue(os.path.exists(path), f"豁免表里的文件不存在: {name}")
            with open(path, "r", encoding="utf-8") as f:
                tree = ast.parse(f.read())
            unloads = list(_iter_unloads(tree))
            self.assertTrue(
                unloads, f"豁免项已失效（{name} 已无任何 sys.modules 卸载）—— 应删除该豁免")
            self.assertTrue(reason.strip(), f"豁免 {name} 缺理由")

    def test_negative_control_injected_unload_turns_red(self):
        """O3-a 负控：注入致命卸载 ⇒ 扫描必报红；撤除 ⇒ 复绿。"""
        with tempfile.TemporaryDirectory(prefix="m17_o2_") as d:
            self.assertEqual(scan_module_unload_violations(d), [],
                             "空目录不应有违规")
            bad_file = os.path.join(d, "test_injected_split.py")
            with open(bad_file, "w", encoding="utf-8") as f:
                f.write('import sys\n'
                        'sys.modules.pop("database", None)\n')
            found = scan_module_unload_violations(d)
            self.assertEqual(len(found), 1, f"未抓到注入的致命卸载: {found}")
            self.assertIn("database", found[0][2])
            os.remove(bad_file)
            self.assertEqual(scan_module_unload_violations(d), [],
                             "撤除注入后必须复绿")


_POISON_SET = [
    "test_live_endpoint_honesty",
    "test_upstream_write_align_t1_t2",
    "test_ai_agent",
    "test_p2_live_guards",
    "test_model_hub_v2",
    "test_model_hub_key_masking",
    "test_reply_kb_generality",
    "test_config_isolation",
    # ⚠️ 2026-10-04（审计 H-38）实测：本集**不能**用来覆盖 H-38 的 P8 顺序依赖。
    #   原因：本类的 `_run_unittest` 走 `python -m unittest`，而
    #   `test_login_channel_config` 是 **pytest 风格**（`@pytest.fixture`，无 TestCase）
    #   ⇒ unittest **收集不到**它（实测 `Ran 0 tests`）。若把它加进来会制造
    #   「已覆盖」的**假象**（子进程 0 收集也算 returncode 0 ⇒ 永远绿）。
    #   H-38 的回归保护改在 `test_login_channel_config.py` 内以**自污染测试**实现
    #   （`test_p10_...`：先真导入 login_remote 制造包属性污染，再跑路由断言）。
]


def _run_unittest(modules, cwd, extra_pythonpath=None):
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    if extra_pythonpath:
        env["PYTHONPATH"] = os.pathsep.join(
            [extra_pythonpath] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    return subprocess.run(
        [sys.executable, "-m", "unittest"] + list(modules),
        cwd=cwd, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=600)


class TestO1OrderIndependenceBehavioral(unittest.TestCase):
    """O1：主判据 —— 「毒源 × 受害模块」正反两种顺序都必须绿。"""

    def test_poison_set_green_in_both_orders(self):
        forward = _run_unittest(_POISON_SET, _BACKEND)
        self.assertEqual(
            forward.returncode, 0,
            f"M-17 破防：顺序 A 有失败\n{(forward.stderr or '')[-2500:]}")
        backward = _run_unittest(list(reversed(_POISON_SET)), _BACKEND)
        self.assertEqual(
            backward.returncode, 0,
            f"M-17 破防：顺序 B 有失败\n{(backward.stderr or '')[-2500:]}")

    def test_negative_control_synthetic_order_dependence_is_detected(self):
        """O3-b 负控：合成本对模块 —— 污染源在前必失败、反序必通过。"""
        with tempfile.TemporaryDirectory(prefix="m17_o1_") as d:
            with open(os.path.join(d, "m17_poison_a.py"), "w", encoding="utf-8") as f:
                f.write(
                    '"""合成污染源：在用例里改写跨模块共享属性且不还原。"""\n'
                    "import unittest\n\n\n"
                    "def _stub(auth, room_id, content='', **kw):\n"
                    "    return {'status_code': 0, 'data': {}}\n\n\n"
                    "class TestPoison(unittest.TestCase):\n"
                    "    def test_patch_shared_attr(self):\n"
                    "        import dy_apis.douyin_api as dapi\n"
                    "        dapi.DouyinAPI.sendMsgInRoom = staticmethod(_stub)\n"
                    "        self.assertTrue(True)\n")
            with open(os.path.join(d, "m17_victim_b.py"), "w", encoding="utf-8") as f:
                f.write(
                    '"""合成受害模块：断言共享能力仍属于其宿主模块。"""\n'
                    "import unittest\n\n\n"
                    "class TestVictim(unittest.TestCase):\n"
                    "    def test_still_hosted_by_client_live(self):\n"
                    "        import dy_apis.client_live as cl\n"
                    "        from dy_apis.douyin_api import DouyinAPI\n"
                    "        fn = DouyinAPI.sendMsgInRoom\n"
                    "        self.assertIs(getattr(fn, '__globals__', None), cl.__dict__,\n"
                    "                      'sendMsgInRoom 被换成桩且未还原')\n")

            pp = os.pathsep.join([d, _BACKEND])
            poison_first = _run_unittest(
                ["m17_poison_a", "m17_victim_b"], d, extra_pythonpath=pp)
            self.assertNotEqual(
                poison_first.returncode, 0,
                "O1 负控失效：污染源在前的顺序竟未失败，说明本判据抓不到顺序依赖")

            victim_first = _run_unittest(
                ["m17_victim_b", "m17_poison_a"], d, extra_pythonpath=pp)
            self.assertEqual(
                victim_first.returncode, 0,
                f"O1 负控基准异常：反序本应通过\n{(victim_first.stderr or '')[-1500:]}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
