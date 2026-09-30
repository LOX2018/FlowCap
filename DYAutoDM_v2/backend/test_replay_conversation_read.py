# -*- coding: utf-8 -*-
"""回放驱动测试：私信**会话读取路径**（`api.messages.list_conversations`）。

## 为什么值得建

这条路径是「私信页纯读 SQLite」的权威实现，也是事故高发区（`dyautodm-dev-guards` §九·乙）：
  · 排序字段不可信 → 180 个空会话霸占列表顶部（用户点到的永远是空会话）
  · `last_ts` 大量并列 → 顺序每次轮询都变（列表「乱跳」+ 选中态错位）
  · 字段形状与前端假设不符 → 白屏 / 空下拉
此前这些**只能靠真机**发现。现跑在脱敏 DB 夹具上：零 BCC、零网络、秒级、可并发。

## 判据口径（不硬编码数字）

「应有多少条」一律**由夹具上的直接 SQL 现算**，再与产品函数输出比对 ——
两条独立代码路径（SQL ↔ service 层）互证，而不是与一个写死的数字比对。

## 敏感性

`TestSensitivity` 把某空会话的 `last_ts` 抬到极大，断言它**仍不排到有消息会话之前** ——
证明排序判据不是恒真装饰（该排序规则是为修此事故而加的）。
"""

import asyncio
import os
import shutil
import sqlite3
import sys
import unittest

_BACKEND = os.path.dirname(os.path.abspath(__file__))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

import tempfile  # noqa: E402

# M-28（2026-09-29）：原实现 `os.environ.setdefault(...)` + **固定**目录 ——
#   setdefault 在进程里已有 DY_APP_ROOT 时是**空操作**，本模块就跑在别人的根上。
#   改为**无条件赋值**一次性临时目录，并在每个用例 setUp 里重钉。
#   （本模块真正的 DB 是 setUpModule 里装入的夹具文件 `_dbfile`，根只用于
#     `database.get_db()` 的一致性校验基线 —— 每次调用都读**当前**根，故必须
#     执行期钉住，否则顺序相关。）标识符（父会话 grep 用）：N1_M28_PIN_ROOT
_FALLBACK_ROOT = tempfile.mkdtemp(prefix="n1_m28_replay_")
os.makedirs(_FALLBACK_ROOT, exist_ok=True)
os.environ["DY_APP_ROOT"] = _FALLBACK_ROOT

from replay import loader                        # noqa: E402

FIXTURE = "dm_read_fixture"

def _n1_m28_pin_root():
    """N1_M28_PIN_ROOT：执行期把 DY_APP_ROOT 重钉回本模块的一次性临时根。"""
    os.environ["DY_APP_ROOT"] = _FALLBACK_ROOT
_ACCOUNTS = ("acct_A", "acct_B")

_dbfile = None
_saved_get_db = None
_tmpdir = None
_was_imported = False
_entry = None

# ⚠️ 三条硬约束（都是实测踩出来的，勿动）：
#  ① **绝不**调用 `Sandbox.activate()` —— 它改写进程级 `DY_APP_ROOT`，而 discover 把所有
#     模块导入同一进程，后续模块按新根算路径即串库。
#  ② **绝不**改 `database._conn` —— 它是**全局**，即便用完还原也改变了该全局的「历史」，
#     实测与 `test_upstream_p4.TestConversationSeq` 双向互染。
#  ③ **必须还原 `api.messages` 的 import 状态** —— 见下。
#
# ★ 真因（2026-09-22 实测定位）：
#   `api/messages.py:32` 是**模块级** `from database import get_db`，绑定在首次 import 时冻结。
#   `test_upstream_p4.TestConversationSeq` 的隔离手法是「patch `database.get_db` 后**首次**
#   import api.messages」，靠「我是第一个 import 者」让它捕获替身。
#   ⇒ 一旦本模块抢先 import 了 api.messages，p4 的 patch 永远失效（两者互染，两个顺序都红）。
#   故本模块收尾时若「api.messages 原本不在 sys.modules」，必须把它移除，恢复到未 import 状态。
#   （该缺陷属 p4 自身的顺序依赖，已登记待修；此处只做「不殃及他人」的最小隔离。）


def setUpModule():
    global _dbfile, _saved_get_db, _tmpdir, _was_imported, _entry
    # P3-1：夹具缺失 = hard fail（不再 SkipTest 静默通过）
    _entry = loader.require_fixture(FIXTURE)
    _tmpdir = tempfile.mkdtemp(prefix="dybc_convread_")
    _dbfile = os.path.join(_tmpdir, "replay_fixture.db")
    # P3-2：必须走 load_fixture（sha256 冻结校验）——不得用 shutil.copyfile
    #       绕过门禁（实测截断样本经 copyfile 零告警通过；load_fixture 会 raise）。
    with open(_dbfile, "wb") as _f:
        _f.write(loader.load_fixture(FIXTURE))

    _was_imported = "api.messages" in sys.modules
    import api.messages as M                     # noqa: E402
    _saved_get_db = M.get_db
    con = sqlite3.connect(_dbfile)
    con.row_factory = sqlite3.Row
    M.get_db = lambda: con

    got = con.execute("PRAGMA database_list").fetchone()[2]
    if os.path.normcase(os.path.abspath(got)) != os.path.normcase(os.path.abspath(_dbfile)):
        M.get_db = _saved_get_db
        raise RuntimeError(f"无法接管 api.messages.get_db（实际指向 {got}）")


def tearDownModule():
    import api.messages as M                     # noqa: E402
    if _saved_get_db is not None:
        M.get_db = _saved_get_db
    # ③ 还原 import 状态：若 api.messages 是本模块引入的，移除之，
    #    让后续模块（如 test_upstream_p4）能按自己的隔离手法重新 import。
    if not _was_imported:
        sys.modules.pop("api.messages", None)
    if _tmpdir:
        shutil.rmtree(_tmpdir, ignore_errors=True)


def _sql(query, args=()):
    con = sqlite3.connect(f"file:{_dbfile}?mode=ro", uri=True)
    try:
        return con.execute(query, args).fetchall()
    finally:
        con.close()


def _call(account):
    from api.messages import list_conversations
    return asyncio.run(list_conversations(account))


class TestFixtureIsSanitized(unittest.TestCase):
    def setUp(self):
        # N1_M28_PIN_ROOT：每个用例重钉隔离根（不依赖模块执行顺序）
        _n1_m28_pin_root()
    """脱敏自证 —— 夹具本身不得含真实标识（机械判据）。"""

    def test_no_urls_outside_placeholder(self):
        rows = _sql("SELECT conv_id, peer_id, peer_name, avatar FROM dm_conversations")
        bad = [r for r in rows if r[3] and "example.invalid" not in str(r[3])]
        self.assertEqual(bad, [], f"发现非占位头像 URL（可能泄漏真实 CDN）：{bad[:2]}")

    def test_message_text_opaque(self):
        rows = _sql("SELECT DISTINCT text FROM dm_messages")
        texts = {r[0] for r in rows}
        self.assertEqual(texts, {"[脱敏]"}, f"消息正文未脱敏：{sorted(texts)[:3]}")

    def test_kv_store_empty(self):
        self.assertEqual(_sql("SELECT COUNT(*) FROM kv_store")[0][0], 0,
                         "kv_store 含配置/探针读数，必须整表清空")

    def test_accounts_mapped(self):
        accts = {r[0] for r in _sql("SELECT DISTINCT account FROM dm_conversations")}
        self.assertEqual(accts, set(_ACCOUNTS), f"账号未脱敏：{accts}")

    def test_no_short_id_holds_sec_uid(self):
        """实测契约违规：`dm_conversations.short_id` 列里曾存 sec_uid。

        本夹具保留该脏值形态（回归真实数据），但断言它**不含 token 前缀**已脱敏。
        """
        rows = _sql("SELECT short_id FROM dm_conversations WHERE short_id IS NOT NULL")
        leak = [r[0] for r in rows if str(r[0]).startswith("MS4wLjAB")
                and "token" not in str(r[0])]
        # 脱敏后 token 形值形如 MS4wLjAB<sha1前24位>，允许存在；断言长度受控
        bad = [s for s in leak if len(s) > 40]
        self.assertEqual(bad, [], f"疑似未脱敏的长 token：{bad[:2]}")


class TestConversationReadContract(unittest.TestCase):
    def setUp(self):
        # N1_M28_PIN_ROOT：每个用例重钉隔离根（不依赖模块执行顺序）
        _n1_m28_pin_root()
    """产品函数 vs 夹具 SQL：两条路径互证。"""

    def test_counts_match_sql(self):
        for acct in _ACCOUNTS:
            with self.subTest(account=acct):
                n_sql = _sql("SELECT COUNT(*) FROM dm_conversations WHERE account=?",
                             (acct,))[0][0]
                res = _call(acct)
                self.assertTrue(res.get("ok"))
                self.assertEqual(len(res.get("conversations") or []), n_sql,
                                 "接口返回条数与库内行数不一致（静默丢/多行）")

    def test_response_shape(self):
        res = _call(_ACCOUNTS[0])
        self.assertIn("ok", res)
        self.assertIn("conversations", res)
        item = res["conversations"][0]
        for k in ("conv_id", "name", "peer_id", "peer_name", "unread",
                  "avatar", "conv_type", "is_group", "messages"):
            self.assertIn(k, item, f"响应缺少字段 {k}（契约形状漂移）")
        self.assertRegex(str(item["conv_id"]), r"^\d+:\d+:\d+:\d+$",
                         "conv_id 形状不符（0:1:uid:uid）")

    def test_no_bare_uid_as_name(self):
        """展示层不得出现裸数字 UID 作为昵称（§七 的兜底目标）。"""
        for acct in _ACCOUNTS:
            res = _call(acct)
            bad = [c for c in res["conversations"]
                   if str(c.get("name") or c.get("peer_name") or "").isdigit()]
            self.assertEqual(bad, [], f"{acct}: 出现裸 UID 昵称 {len(bad)} 条")

    def test_name_has_no_newline(self):
        """昵称不得混入时间（08 记载：title 形如 `昵称\\n时间`，须 split）。"""
        for acct in _ACCOUNTS:
            res = _call(acct)
            bad = [c for c in res["conversations"] if "\n" in str(c.get("name") or "")]
            self.assertEqual(bad, [], f"{acct}: 昵称含换行 {len(bad)} 条")

    def test_conversations_with_msgs_before_empty(self):
        """排序事故形态：空会话（last_ts 被刷大）不得霸占列表顶部。"""
        for acct in _ACCOUNTS:
            with self.subTest(account=acct):
                with_msgs = {r[0] for r in _sql(
                    "SELECT DISTINCT conv_id FROM dm_messages "
                    "WHERE account=? AND msg_type<>'50001'", (acct,))}
                res = _call(acct)
                convs = res["conversations"]
                flags = [c["conv_id"] in with_msgs for c in convs]
                # 一旦出现「空会话」，其后不得再有「有消息会话」
                first_empty = next((i for i, f in enumerate(flags) if not f), None)
                if first_empty is not None:
                    tail = flags[first_empty:]
                    self.assertTrue(all(not f for f in tail),
                                    "空会话之后又出现有消息会话 ⇒ 排序规则回归")

    def test_order_stable_across_calls(self):
        """轮询顺序必须确定（防列表「乱跳」）。"""
        for acct in _ACCOUNTS:
            a = [c["conv_id"] for c in _call(acct)["conversations"]]
            b = [c["conv_id"] for c in _call(acct)["conversations"]]
            self.assertEqual(a, b, f"{acct}: 两次调用顺序不一致")

    def test_unknown_account_is_safe(self):
        """未知账号不得抛异常（守护状态分支的边界）。"""
        res = _call("no_such_account_xyz")
        self.assertTrue(res.get("ok"))
        self.assertEqual(res.get("conversations"), [])


class TestManifestExpectedIsLive(unittest.TestCase):
    def setUp(self):
        # N1_M28_PIN_ROOT：每个用例重钉隔离根（不依赖模块执行顺序）
        _n1_m28_pin_root()
    """P3-9：manifest 的 `expected` 必须被**消费者**读取并与现算真值互证。

    原缺陷：`expected` 是死元数据（唯一消费者用 SQL 现算期望，从不读 manifest）
    ⇒ 两份判据各自演化，manifest 里写的数字谁也没验过。现断言两路一致。
    """

    def test_manifest_expected_matches_sql_truth(self):
        expected = (_entry or {}).get("expected") or {}
        self.assertTrue(expected, "manifest 缺 expected 块（判据无来源）")
        truth = {
            "accounts": _sql("SELECT COUNT(DISTINCT account) FROM dm_conversations")[0][0],
            "conversations": _sql("SELECT COUNT(*) FROM dm_conversations")[0][0],
            "messages": _sql("SELECT COUNT(*) FROM dm_messages")[0][0],
            "kv_store_rows": _sql("SELECT COUNT(*) FROM kv_store")[0][0],
            "roles_me": _sql("SELECT COUNT(*) FROM dm_messages WHERE role='me'")[0][0],
            "roles_them": _sql("SELECT COUNT(*) FROM dm_messages WHERE role='them'")[0][0],
            "convs_with_msgs": _sql(
                "SELECT COUNT(DISTINCT conv_id) FROM dm_messages "
                "WHERE msg_type<>'50001'")[0][0],
            "empty_convs": _sql(
                "SELECT COUNT(*) FROM dm_conversations c WHERE c.conv_id NOT IN "
                "(SELECT DISTINCT conv_id FROM dm_messages WHERE msg_type<>'50001')")[0][0],
        }
        for k, want in expected.items():
            if k not in truth:
                continue
            with self.subTest(metric=k):
                self.assertEqual(
                    truth[k], want,
                    f"manifest.expected[{k}]={want} 与夹具现算真值 {truth[k]} 不符 "
                    f"⇒ expected 已与夹具漂移（重录夹具必须同步 expected）")


class TestSensitivity(unittest.TestCase):
    def setUp(self):
        # N1_M28_PIN_ROOT：每个用例重钉隔离根（不依赖模块执行顺序）
        _n1_m28_pin_root()
    """证明排序判据敏感：抬高空会话的 last_ts 后，它仍不得排到有消息会话之前。"""

    def test_inflated_empty_conversation_stays_behind(self):
        acct = _ACCOUNTS[0]
        with_msgs = {r[0] for r in _sql(
            "SELECT DISTINCT conv_id FROM dm_messages WHERE account=? AND msg_type<>'50001'",
            (acct,))}
        empties = [r[0] for r in _sql(
            "SELECT conv_id FROM dm_conversations WHERE account=?", (acct,))
            if r[0] not in with_msgs]
        if not empties or not with_msgs:
            self.skipTest("夹具中缺少可比较的空/非空会话")

        target = empties[0]
        con = sqlite3.connect(_dbfile)
        try:
            row = con.execute(
                "SELECT last_ts FROM dm_conversations WHERE conv_id=?", (target,)).fetchone()
            orig_ts = row[0] if row else 0.0
            con.execute("UPDATE dm_conversations SET last_ts=? WHERE conv_id=?",
                        (9.9e9, target))
            con.commit()
        finally:
            con.close()
        try:
            flags = [c["conv_id"] in with_msgs for c in _call(acct)["conversations"]]
            first_empty = next((i for i, f in enumerate(flags) if not f), None)
            self.assertIsNotNone(first_empty, "夹具应含空会话")
            self.assertTrue(all(not f for f in flags[first_empty:]),
                            "被抬到极大 last_ts 的空会话越过了有消息会话 ⇒ 排序判据不敏感")
        finally:
            # 精确还原（不能写 0.0 —— 那会永久改掉夹具副本，影响同模块后续用例）
            con = sqlite3.connect(_dbfile)
            try:
                con.execute("UPDATE dm_conversations SET last_ts=? WHERE conv_id=?",
                            (orig_ts, target))
                con.commit()
            finally:
                con.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
