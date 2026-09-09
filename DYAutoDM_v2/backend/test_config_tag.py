"""v0.38.2：配置标签验证。

核心原则（用户明确）：**标签是指引，参数仍由 app_config（原单位）按 scope 隔离存储**，
标签自身不持有任何参数副本。

验证点：
1. 标签只存元数据 —— get_tag() 不含 config 字段
2. 参数落在 app_config 的 scope kv，不在标签里
3. 绑定标签后读到标签值；未绑定读到全局（零回归）
4. 删标签 → 解绑账号 + 清掉 scope 参数（无孤儿数据）
5. dm_dispatch.cfg(account=) 按标签取值
6. general 分区不受标签影响（非业务）

跑法：cd backend && python test_config_tag.py
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_tmp = os.path.join(tempfile.gettempdir(), "dyautodm_tag_test")
os.makedirs(_tmp, exist_ok=True)
os.environ["DY_APP_ROOT"] = _tmp

from services import app_config as ac, config_tag  # noqa: E402


def reset_all():
    import database
    conn = database.get_db()
    conn.execute("DELETE FROM kv_store")
    conn.commit()


class TestTagIsOnlyMetadata(unittest.TestCase):
    """标签只存元数据，不存参数副本（用户明确要求）。"""

    def setUp(self):
        reset_all()

    def tearDown(self):
        reset_all()

    def test_tag_has_no_config(self):
        t = config_tag.save_tag("", "高频标签")
        self.assertNotIn("config", t, "标签不应持有 config 字段")

    def test_params_stored_in_app_config(self):
        """参数应落在 app_config 的 scope kv，不是标签里。"""
        t = config_tag.save_tag("", "高频标签")
        tid = t["id"]
        ac.save_section("send", {"stranger_per_minute": 5}, scope=tid)

        # 标签元数据里没有参数
        self.assertNotIn("config", config_tag.get_tag(tid))
        # 参数确实在 app_config 的 scope 里
        stored = ac._load(ac.scope_key(tid))
        self.assertEqual(stored["send"]["stranger_per_minute"], 5)


class TestResolve(unittest.TestCase):
    """绑定/未绑定的解析行为。"""

    def setUp(self):
        reset_all()

    def tearDown(self):
        reset_all()

    def test_unbound_is_global(self):
        """未绑定标签 → 读全局（零回归）。"""
        ac.save_section("send", {"stranger_per_minute": 12})
        base = ac.get_section("send")
        self.assertEqual(config_tag.resolve("账号A", "send", base), base)
        self.assertEqual(base["stranger_per_minute"], 12.0)

    def test_bound_overrides(self):
        """绑定标签后读标签值。"""
        t = config_tag.save_tag("", "保守标签")
        ac.save_section("send", {"stranger_per_minute": 60}, scope=t["id"])
        config_tag.bind("账号A", t["id"])

        base = ac.get_section("send")          # 全局
        got = config_tag.resolve("账号A", "send", base)
        self.assertEqual(got["stranger_per_minute"], 60.0)

    def test_partial_override_keeps_global(self):
        """标签只配部分键，其余保留全局。"""
        ac.save_section("send", {"min_interval": 12.0, "max_wait": 30.0})
        t = config_tag.save_tag("", "只改间隔")
        ac.save_section("send", {"stranger_per_minute": 45}, scope=t["id"])
        config_tag.bind("账号B", t["id"])

        got = config_tag.resolve("账号B", "send", ac.get_section("send"))
        self.assertEqual(got["stranger_per_minute"], 45)
        self.assertEqual(got["max_wait"], 30.0)   # 保留全局

    def test_two_accounts_different_tags(self):
        """多账号不同标签互不干扰（本次改造核心目标）。"""
        t1 = config_tag.save_tag("", "激进")
        t2 = config_tag.save_tag("", "保守")
        ac.save_section("send", {"stranger_per_minute": 8}, scope=t1["id"])
        ac.save_section("send", {"stranger_per_minute": 60}, scope=t2["id"])
        config_tag.bind("账号A", t1["id"])
        config_tag.bind("账号B", t2["id"])

        base = ac.get_section("send")
        self.assertEqual(
            config_tag.resolve("账号A", "send", base)["stranger_per_minute"], 8.0)
        self.assertEqual(
            config_tag.resolve("账号B", "send", base)["stranger_per_minute"], 60.0)

    def test_general_not_managed(self):
        """general（前端/系统行为）不受标签影响。"""
        t = config_tag.save_tag("", "标签")
        config_tag.bind("账号A", t["id"])
        base = {"force_rescan": True}
        self.assertEqual(config_tag.resolve("账号A", "general", base), base)


class TestDeleteCleansUp(unittest.TestCase):
    """删标签必须解绑 + 清参数，不留孤儿。"""

    def setUp(self):
        reset_all()

    def tearDown(self):
        reset_all()

    def test_delete_unbinds_and_drops_params(self):
        t = config_tag.save_tag("", "临时")
        ac.save_section("send", {"stranger_per_minute": 45}, scope=t["id"])
        config_tag.bind("账号A", t["id"])

        r = config_tag.delete_tag(t["id"])
        self.assertEqual(r["unbound_accounts"], ["账号A"])
        self.assertIsNone(config_tag.tag_of("账号A"))

        # scope 参数已清理
        self.assertEqual(ac._load(ac.scope_key(t["id"])), {})
        # 回落到全局（零回归）
        ac.save_section("send", {"stranger_per_minute": 12})
        base = ac.get_section("send")
        self.assertEqual(config_tag.resolve("账号A", "send", base), base)


class TestDispatchWiring(unittest.TestCase):
    """dm_dispatch.cfg(account=) 按标签取值。"""

    def setUp(self):
        reset_all()

    def tearDown(self):
        reset_all()

    def test_cfg_with_account(self):
        import services.dm_dispatch as dd
        ac.save_section("send", {"stranger_per_minute": 20})   # 全局
        t = config_tag.save_tag("", "慢速")
        ac.save_section("send", {"stranger_per_minute": 50}, scope=t["id"])
        config_tag.bind("账号A", t["id"])

        self.assertEqual(dd.cfg("STRANGER_PER_MINUTE", account="账号A"), 50)
        self.assertEqual(dd.cfg("STRANGER_PER_MINUTE"), 20.0)              # 无账号=全局
        self.assertEqual(dd.cfg("STRANGER_PER_MINUTE", account="未绑定B"), 20.0)


if __name__ == "__main__":
    unittest.main(verbosity=0)
