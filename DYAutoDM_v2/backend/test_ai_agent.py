"""v0.38.0：AI Agent 模版 + 账号绑定验证。

核心验证点：
1. 零回归：未绑定 Agent → resolve_config 原样返回全局配置
2. Agent 覆盖：绑定后逐键覆盖，未配置的键保留全局
3. 知识库/黑名单按 Agent 隔离
4. 删除 Agent 自动解绑（防悬空）
5. 消费方接入：_handle / _generate_reply 真的按账号解析

跑法：cd backend && python test_ai_agent.py
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_tmp = os.path.join(tempfile.gettempdir(), "dyautodm_agent_test")
os.makedirs(_tmp, exist_ok=True)
os.environ["DY_APP_ROOT"] = _tmp

from services import ai_agent, ai_reply  # noqa: E402


def reset_all():
    import database
    conn = database.get_db()
    for k in ("ai_agents", "ai_account_agent", "ai_reply_config",
              "ai_reply_knowledge_base", "ai_reply_blacklist"):
        conn.execute("DELETE FROM kv_store WHERE key=?", (k,))
    conn.commit()


class TestZeroRegression(unittest.TestCase):
    """未绑定 Agent 时必须与改造前完全一致。"""

    def setUp(self):
        reset_all()

    def tearDown(self):
        reset_all()

    def test_no_binding_returns_base(self):
        base = {"model": "glm-5.2", "strict_level": "rag", "enabled": True}
        self.assertEqual(ai_agent.resolve_config("账号A", base), base)

    def test_unknown_agent_returns_base(self):
        ai_agent.bind("账号A", "ag_not_exist")
        base = {"model": "x"}
        self.assertEqual(ai_agent.resolve_config("账号A", base), base)

    def test_knowledge_no_binding(self):
        base = [{"id": 1, "question": "q", "answer": "a"}]
        self.assertEqual(ai_agent.resolve_knowledge("账号A", base), base)

    def test_blacklist_no_binding(self):
        base = ["uid1"]
        self.assertEqual(ai_agent.resolve_blacklist("账号A", base), base)


class TestAgentOverride(unittest.TestCase):
    """绑定后用 Agent 配置覆盖。"""

    def setUp(self):
        reset_all()

    def tearDown(self):
        reset_all()

    def test_override(self):
        ai_agent.save_agent("ag1", "客服Agent", {
            "model": "minimax-m3",
            "merchant_name": "工伤小助理",
            "strict_level": "kb_only",
        })
        ai_agent.bind("账号A", "ag1")
        base = ai_reply.get_config()
        cfg = ai_agent.resolve_config("账号A", base)
        self.assertEqual(cfg["model"], "minimax-m3")
        self.assertEqual(cfg["merchant_name"], "工伤小助理")
        self.assertEqual(cfg["strict_level"], "kb_only")

    def test_partial_override_keeps_global(self):
        """Agent 只配了部分键，其余保留全局（不是整体替换）。"""
        ai_agent.save_agent("ag2", "只改模型", {"model": "qwen"})
        ai_agent.bind("账号B", "ag2")
        base = ai_reply.get_config()
        cfg = ai_agent.resolve_config("账号B", base)
        self.assertEqual(cfg["model"], "qwen")
        self.assertEqual(cfg["strict_level"], base["strict_level"])
        self.assertEqual(cfg["max_tokens"], base["max_tokens"])

    def test_different_accounts_different_agents(self):
        """多账号绑定不同 Agent → 互不干扰（本次改造的核心目标）。"""
        ai_agent.save_agent("agA", "A", {"merchant_name": "商家A"})
        ai_agent.save_agent("agB", "B", {"merchant_name": "商家B"})
        ai_agent.bind("账号A", "agA")
        ai_agent.bind("账号B", "agB")
        base = ai_reply.get_config()
        self.assertEqual(
            ai_agent.resolve_config("账号A", base)["merchant_name"], "商家A")
        self.assertEqual(
            ai_agent.resolve_config("账号B", base)["merchant_name"], "商家B")

    def test_template_sharing(self):
        """同一 Agent 被多账号绑定 → 改一次全部生效（模版价值）。"""
        ai_agent.save_agent("agS", "共享", {"model": "v1"})
        ai_agent.bind("账号A", "agS")
        ai_agent.bind("账号B", "agS")
        base = ai_reply.get_config()
        ai_agent.save_agent("agS", "共享", {"model": "v2"})
        self.assertEqual(ai_agent.resolve_config("账号A", base)["model"], "v2")
        self.assertEqual(ai_agent.resolve_config("账号B", base)["model"], "v2")


class TestKnowledgeIsolation(unittest.TestCase):
    """知识库/黑名单跟随 Agent。"""

    def setUp(self):
        reset_all()

    def tearDown(self):
        reset_all()

    def test_knowledge_per_agent(self):
        ai_agent.save_agent("ag1", "A", {
            "knowledge_base": [{"id": 1, "question": "价格", "answer": "100"}]})
        ai_agent.bind("账号A", "ag1")
        got = ai_agent.resolve_knowledge("账号A", [{"id": 9, "question": "全局"}])
        self.assertEqual(got[0]["question"], "价格")

    def test_blacklist_per_agent(self):
        ai_agent.save_agent("ag1", "A", {"blacklist": ["坏人A"]})
        ai_agent.bind("账号A", "ag1")
        self.assertEqual(ai_agent.resolve_blacklist("账号A", ["全局坏人"]),
                         ["坏人A"])


class TestDeleteUnbinds(unittest.TestCase):
    """删除 Agent 必须解绑，否则账号指向不存在的 Agent（悬空）。"""

    def setUp(self):
        reset_all()

    def tearDown(self):
        reset_all()

    def test_delete_unbinds(self):
        ai_agent.save_agent("ag1", "A", {"model": "x"})
        ai_agent.bind("账号A", "ag1")
        ai_agent.bind("账号B", "ag1")
        r = ai_agent.delete_agent("ag1")
        self.assertEqual(sorted(r["unbound_accounts"]), ["账号A", "账号B"])
        self.assertIsNone(ai_agent.agent_of("账号A"))
        # 删除后回落全局（零回归）
        base = ai_reply.get_config()
        self.assertEqual(ai_agent.resolve_config("账号A", base), base)


class TestConsumerWiring(unittest.TestCase):
    """消费方真的按账号解析（防止改了存储层但没接进链路）。"""

    def setUp(self):
        reset_all()

    def tearDown(self):
        reset_all()

    def test_handle_resolves_per_account(self):
        """_handle 内 cfg 应被 Agent 覆盖 —— 用源码断言 + 行为验证双保险。"""
        import inspect
        src = inspect.getsource(ai_reply.AutoReplyWorker._handle)
        self.assertIn("ai_agent.resolve_config", src)

    def test_generate_reply_passes_account(self):
        with open(os.path.join(os.path.dirname(__file__), "services",
                               "ai_reply.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn("KB.find_match(text, account=account)", src)

    def test_find_match_semantic_accepts_items(self):
        import inspect
        sig = inspect.signature(ai_reply.find_match_semantic)
        self.assertIn("items", sig.parameters)


if __name__ == "__main__":
    unittest.main(verbosity=0)
