# coding=utf-8
"""会员体系冒烟测试：注册/登录/数据隔离/.env加密封装（纯逻辑，零网络零浏览器）。

## 为什么重写（2026-09-22，核验报告 §5.5）

原实现是**模块级脚本式断言**（`import` 时执行，139 行顶层语句）。后果：

1. 它在**模块顶层**做「注册会员 A → 切到会员 B → 用 B 的主密钥上下文解密 A 的密文」，
   `services/member_ctx.py` 抛 `InvalidSignature` ⇒ **模块导入即崩**。
2. `unittest discover` 只记 1 条 `_FailedTest` 占位 → 内部断言**从未执行**，
   却让全量回归长期背着一个「1 个错误」的读数，掩盖真正的新增回归。
3. 知识库两处记载互相矛盾（`12_业务域_直播监听.md` 记「存量失败」✅、
   `15_业务域_会员与配置.md` 记「19/19 通过」❌）—— 后者是**假绿**。

⇒ 改为标准 `unittest.TestCase`：每个用例**自建设置/拆卸**（独立沙箱 + 独立会员），
失败只影响本用例，且能真正被回归套件计入（不再是占位 `_FailedTest`）。

## 不变式（与原文逐条对应）

- 注册/登录/口令校验；注册表不落主密钥与口令明文；
- 会员数据空间隔离（账号列表、DB、`.env.enc`）；
- 会员 B 读不出会员 A 的凭证（跨会话密钥）——**原实现崩在这里**，现为正常可跑的断言；
- 明文 `.env` 不落盘，密文不含凭证明文；
- `login_api` 兼容层能解密读到 cookie；
- 改口令后主密钥不变、新口令可登录。

⚠️ 隔离口径：沙箱用 `FLOWCAP_APP_ROOT` 指向临时目录；**不触碰真实 `members/` 与账号数据**。
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

BACKEND = os.path.dirname(os.path.abspath(__file__))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)


class MemberSmokeBase(unittest.TestCase):
    """每用例一个隔离沙箱（`FLOWCAP_APP_ROOT` 指向临时目录）。"""

    @classmethod
    def setUpClass(cls):
        cls._prev_root = os.environ.get("FLOWCAP_APP_ROOT")

    @classmethod
    def tearDownClass(cls):
        if cls._prev_root is None:
            os.environ.pop("FLOWCAP_APP_ROOT", None)
        else:
            os.environ["FLOWCAP_APP_ROOT"] = cls._prev_root

    def setUp(self):
        self.sandbox = tempfile.mkdtemp(prefix="dy_member_smoke_")
        os.environ["FLOWCAP_APP_ROOT"] = self.sandbox
        self._reset()

    def tearDown(self):
        self._reset()
        shutil.rmtree(self.sandbox, ignore_errors=True)

    @staticmethod
    def _reset():
        """清掉会员会话与 DB 连接，保证用例间零串扰。"""
        try:
            from services import member_ctx
            member_ctx.clear_current()
        except Exception:  # noqa: BLE001
            pass
        try:
            import database
            database.reset_connection()
        except Exception:  # noqa: BLE001
            pass

    # ---- 公共夹具 ----

    def _register(self, name="测试会员A", password="pass123"):
        from services import member_store
        r = member_store.register_member(name, password)
        self.assertTrue(r.get("ok"), f"注册应成功: {r}")
        return r["member_id"]

    def _login(self, mid, name="测试会员A", password="pass123"):
        """切到该会员上下文（set_current + 清 DB 连接），返回登录结果。"""
        from services import member_store, member_ctx
        r = member_store.authenticate(name, password)
        self.assertTrue(r.get("ok"), f"登录应成功: {r}")
        member_ctx.set_current(mid, name, r["master_key"], "tok")
        try:
            import database
            database.reset_connection()
        except Exception:  # noqa: BLE001
            pass
        return r


class TestMemberRegistration(MemberSmokeBase):
    """1) 注册 / 登录 / 口令校验；注册表不落明文主密钥与口令。"""

    def test_register_then_duplicate_rejected(self):
        from services import member_store
        self._register()
        r2 = member_store.register_member("测试会员A", "other666")
        self.assertFalse(r2.get("ok"), "重名注册必须被拒")

    def test_wrong_password_rejected_and_correct_accepted(self):
        from services import member_store
        self._register()
        self.assertFalse(member_store.authenticate("测试会员A", "wrongpw").get("ok"))
        r4 = member_store.authenticate("测试会员A", "pass123")
        self.assertTrue(r4.get("ok"))
        self.assertTrue(r4.get("master_key"))

    def test_registry_has_no_plaintext_secrets(self):
        from services import member_store
        self._register()
        r = member_store.authenticate("测试会员A", "pass123")
        raw = open(member_store.registry_path(), encoding="utf-8").read()
        self.assertNotIn(r["master_key"], raw, "注册表不得出现主密钥明文")
        self.assertNotIn("pass123", raw, "注册表不得出现口令明文")


class TestMemberSpaceIsolation(MemberSmokeBase):
    """2) 会员数据空间隔离：账号列表 / .env.enc / DB。"""

    def _seed_a(self):
        """建会员 A，写入一份加密凭证 + 一个账号索引条目。返回 (mid_a, env_a)。"""
        mid_a = self._register("测试会员A", "pass123")
        self._login(mid_a, "测试会员A", "pass123")
        from auto_dm import accounts as acc
        from services import member_ctx as mctx
        env_a = (acc.env_path_of("小号甲")
                 or os.path.join(acc._accounts_dir(), "小号甲", ".env"))
        os.makedirs(os.path.dirname(env_a), exist_ok=True)
        mctx.write_env_file(env_a, {"DY_COOKIES": "sessionid=A_COOKIE_AAA; uid_tt=x",
                                    "DY_TICKET": "TICKET_A"})
        idx = acc._load_index()
        idx.setdefault("accounts", {})["小号甲"] = "小号甲/.env"
        idx["current"] = "小号甲"
        acc._save_index(idx)
        return mid_a, env_a

    def test_env_is_encrypted_and_plaintext_absent(self):
        _mid_a, env_a = self._seed_a()
        self.assertFalse(os.path.exists(env_a), "明文 .env 不得落盘")
        self.assertTrue(os.path.exists(env_a + ".enc"), "必须存在 .enc 密文")
        enc_raw = open(env_a + ".enc", encoding="ascii").read()
        self.assertNotIn("A_COOKIE_AAA", enc_raw, "密文不得含 cookie 明文")
        self.assertNotIn("TICKET_A", enc_raw, "密文不得含 ticket 明文")

    def test_plaintext_readback_in_own_context(self):
        self._seed_a()
        from services import member_ctx as mctx
        env_a = (__import__("auto_dm.accounts", fromlist=["x"]).env_path_of("小号甲"))
        vals = mctx.parse_env_dict(env_a)
        self.assertEqual(vals.get("DY_COOKIES"), "sessionid=A_COOKIE_AAA; uid_tt=x")
        self.assertEqual(vals.get("DY_TICKET"), "TICKET_A")

    def test_account_list_isolated_between_members(self):
        self._seed_a()
        from auto_dm import accounts as acc
        from services import member_store, member_ctx
        self.assertEqual([n for n, _ in acc.list_accounts()], ["小号甲"])

        member_ctx.clear_current()
        r_b = member_store.register_member("测试会员B", "pass456")
        member_ctx.set_current(r_b["member_id"], "测试会员B", r_b["master_key"], "tok_b")
        self.assertEqual([n for n, _ in acc.list_accounts()], [],
                         "会员 B 的账号列表必须为空（与 A 隔离）")

    def test_other_member_cannot_decrypt(self):
        """★ 原实现崩在这里（模块顶层跨会话解密）；现在它是一条正常可跑的断言。"""
        _mid_a, env_a = self._seed_a()
        from services import member_store
        from services import member_ctx as mctx
        mctx.clear_current()
        r_b = member_store.register_member("测试会员B", "pass456")
        mctx.set_current(r_b["member_id"], "测试会员B", r_b["master_key"], "tok_b")
        try:
            vals_b = mctx.parse_env_dict(env_a)
        except Exception:  # noqa: BLE001
            vals_b = {}          # 抛异常同样算「读不出」，属合格隔离
        self.assertIsNone(vals_b.get("DY_COOKIES"), f"会员B 不得读出A的凭证: {vals_b}")

    def test_db_isolated_between_members(self):
        mid_a, _env_a = self._seed_a()
        from services import member_store, member_ctx
        import database

        conn = database.get_db()
        dbfile = str(conn.execute("PRAGMA database_list").fetchone()[2]).lower()
        self.assertIn(member_store.member_db_path(mid_a).lower(), dbfile,
                      "会员A DB 必须落在会员A空间")
        conn.execute("CREATE TABLE IF NOT EXISTS _smoke (k TEXT)")
        conn.commit()
        conn.execute("INSERT INTO _smoke VALUES ('hello_a')")
        conn.commit()

        member_ctx.clear_current()
        r_b = member_store.register_member("测试会员B", "pass456")
        member_ctx.set_current(r_b["member_id"], "测试会员B", r_b["master_key"], "tok_b")
        database.reset_connection()
        conn_b = database.get_db()
        tabs = [r[0] for r in conn_b.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")]
        self.assertNotIn("_smoke", tabs, f"会员B DB 不得含会员A的数据表: {tabs}")


class TestLoginApiCompatLayer(MemberSmokeBase):
    """3) `login_api` 兼容层能解密读到 cookie。"""

    def test_load_auth_from_env_reads_cookie(self):
        mid_a = self._register("测试会员A", "pass123")
        self._login(mid_a, "测试会员A", "pass123")
        from auto_dm import accounts as acc
        from services import member_ctx as mctx
        env_a = (acc.env_path_of("小号甲")
                 or os.path.join(acc._accounts_dir(), "小号甲", ".env"))
        os.makedirs(os.path.dirname(env_a), exist_ok=True)
        mctx.write_env_file(env_a, {"DY_COOKIES": "sessionid=A_COOKIE_AAA; uid_tt=x",
                                    "DY_TICKET": "TICKET_A"})
        from dy_apis.login_api import DYLoginApi
        auth = DYLoginApi._load_auth_from_env(env_a)
        self.assertIn("A_COOKIE_AAA", (auth.cookie_str or ""))


class TestPasswordChange(MemberSmokeBase):
    """4) 改口令：主密钥不变、新口令可登录。"""

    def test_change_password_keeps_master_key(self):
        from services import member_store
        mid_a = self._register("测试会员A", "pass123")
        master_a = member_store.authenticate("测试会员A", "pass123")["master_key"]
        rc = member_store.change_password(mid_a, "pass123", "newpass789")
        self.assertTrue(rc.get("ok"), f"改口令应成功: {rc}")
        r_re = member_store.authenticate("测试会员A", "newpass789")
        self.assertTrue(r_re.get("ok"))
        self.assertEqual(r_re["master_key"], master_a, "改口令不得更换主密钥")


if __name__ == "__main__":
    unittest.main(verbosity=2)
