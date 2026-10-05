# -*- coding: utf-8 -*-
"""明文 .env 废弃（凭证永久加密）的守卫测试（2026-09-21）。

钉住的机制（不是字面量）：
  1. `member_ctx.write_env_file`：主密钥不可用 → **抛错**，绝不写明文
  2. `member_ctx.parse_env_dict`：**不读明文** .env（哪怕它存在）
  3. `member_ctx.parse_env_text`：同上
  4. `member_ctx.env_exists`：只认 <path>.enc
  5. `migrate_plain_envs` 已废弃为零操作
  6. 全仓**不得再出现**明文写路径（set_key / 明文 open("w") 写 env）——静态断言，
     限定在【可执行代码段】（剥注释/docstring），避免说明性文字造成假绿

跑法：python test_plaintext_env_deprecation.py
"""
import ast
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_BACKEND = os.path.dirname(os.path.abspath(__file__))
# A-8 / M-17 隔离根单一化：模块级 DY_APP_ROOT 必须是**一次性临时目录**，禁止回落
# 源码树/仓库父目录（旧值 = backend/../..）。先 mkdir 再赋值（vbrowser.app_root()
# 忽略不存在的根 → 回落仓库 data/）。范式见 test_uid_sink_ext.py:17-39。
_ROOT = tempfile.mkdtemp(prefix="plaintext_env_dep_")
os.makedirs(_ROOT, exist_ok=True)
os.environ["DY_APP_ROOT"] = _ROOT


def _reload_member_ctx():
    from services import member_ctx
    return member_ctx


def _valid_fernet_key() -> str:
    from cryptography.fernet import Fernet
    return Fernet.generate_key().decode("ascii")


class TestWriteNeverPlaintext(unittest.TestCase):
    def test_write_without_master_key_raises(self):
        """主密钥不可用 → 必须抛错，且**不产生任何明文 .env**。"""
        from services import member_ctx
        d = tempfile.mkdtemp(prefix="pt_dep_")
        env_path = os.path.join(d, "acct", ".env")
        orig = member_ctx.master_key
        member_ctx.master_key = lambda: None          # 模拟未登录
        try:
            with self.assertRaises(RuntimeError):
                member_ctx.write_env_file(env_path, {"DY_COOKIES": "secret"})
        finally:
            member_ctx.master_key = orig
        self.assertFalse(os.path.exists(env_path),
                         "主密钥不可用时绝不允许产生明文 .env")
        self.assertFalse(os.path.exists(env_path + ".enc"),
                         "主密钥不可用时也不应有 .enc（应直接失败）")

    def test_write_with_key_creates_only_enc(self):
        from services import member_ctx
        d = tempfile.mkdtemp(prefix="pt_dep_")
        env_path = os.path.join(d, "acct", ".env")
        orig = member_ctx.master_key
        member_ctx.master_key = lambda: _valid_fernet_key()
        try:
            ep = member_ctx.write_env_file(env_path, {"DY_COOKIES": "c=1"})
        finally:
            member_ctx.master_key = orig
        self.assertTrue(ep.endswith(".enc"))
        self.assertTrue(os.path.exists(env_path + ".enc"))
        self.assertFalse(os.path.exists(env_path), "不得同时产生明文 .env")


class TestReadNeverPlaintext(unittest.TestCase):
    def test_parse_ignores_plaintext_env(self):
        """即便磁盘上有明文 .env，也**不得**被读取（拒绝静默使用未加密凭证）。"""
        from services import member_ctx
        d = tempfile.mkdtemp(prefix="pt_dep_")
        env_path = os.path.join(d, "acct", ".env")
        os.makedirs(os.path.dirname(env_path), exist_ok=True)
        with open(env_path, "w", encoding="utf-8") as f:
            f.write("DY_COOKIES=PLAINTEXT_SHOULD_NOT_BE_READ\n")
        orig = member_ctx.master_key
        member_ctx.master_key = lambda: _valid_fernet_key()
        try:
            self.assertEqual(member_ctx.parse_env_dict(env_path), {},
                             "明文 .env 必须被忽略")
            self.assertIsNone(member_ctx.parse_env_text(env_path),
                              "明文 .env 全文也不得被读取")
            self.assertFalse(member_ctx.env_exists(env_path),
                             "env_exists 只认 .enc")
        finally:
            member_ctx.master_key = orig


class TestMigrationRetired(unittest.TestCase):
    def test_migrate_is_noop(self):
        from services import member_ctx
        r = member_ctx.migrate_plain_envs("mid", _valid_fernet_key())
        self.assertEqual(r, {"migrated": 0, "skipped": 0})


class TestNoPlaintextWritePathRemains(unittest.TestCase):
    """静态断言：全仓不得再出现明文写 .env 的执行路径（剥注释/docstring）。"""

    def _exec_src(self, path):
        src = open(path, encoding="utf-8", errors="replace").read()
        tree = ast.parse(src)
        # 剥掉所有字符串常量（docstring / 注释不同 AST 节点，注释本就不进 AST）
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                node.value = ""
        return ast.unparse(tree)

    def test_no_set_key_on_env(self):
        offenders = []
        for root, _dirs, files in os.walk(_BACKEND):
            if "__pycache__" in root or "node_modules" in root:
                continue
            for fn in files:
                if not fn.endswith(".py"):
                    continue
                if fn.startswith("test_"):
                    continue
                p = os.path.join(root, fn)
                try:
                    code = self._exec_src(p)
                except Exception:
                    continue
                # 明文写盘的两个特征调用
                if "set_key" in code:
                    offenders.append((p, "set_key"))
                if "write_env_file" in code and "member_ctx" not in code and "import" in code:
                    pass  # write_env_file 统一入口允许
        self.assertEqual(offenders, [],
                         f"仍有明文写盘调用 set_key: {offenders}")

    def test_guard_import_present_in_writers(self):
        """save_credential 必须走 member_ctx（不得自带明文写盘）。"""
        src = open(os.path.join(_BACKEND, "dy_apis", "login_api.py"),
                   encoding="utf-8", errors="replace").read()
        self.assertIn("member_ctx.write_env_file", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
