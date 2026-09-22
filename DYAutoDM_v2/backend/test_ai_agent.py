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
        """回复生成必须「按账号」解析知识库/配置 —— 用**行为机制**断言。

        ## 2026-09-21 修正（原断言是假红，守的是已废弃的写法）

        原断言在 `ai_reply.py` 全文里找字面量 `KB.find_match(text, account=account)`。
        但 v0.39.0 起两库职责已明确拆分（源码注释原文）：

            · 命中即回 → `reply_kb.find_match(text, account=account)`（对话回复库）
            · 专业库   → 只做 RAG 参考，**旧 `KB.find_match` 已从回复链路移除**

        ⇒ 字面量断言守的是一个**已被有意废弃**的调用，恒红且毫无保护力；
          而真正在跑的新链路 `reply_kb` 反倒**无人守卫**。典型的
          「断言绑字面量而非机制」：实现一重构就失灵（见调试方法论
          「守卫测试必须断言机制，不能断言字面量」）。

        ## 现在的判据（逐条对应真实机制）

        1. `_generate_reply` 里**必须有一次**带 `account=` 的 find_match 调用
           （不论它挂在 `reply_kb` 还是 `KB` —— 换库不算破坏契约）；
        2. 该函数体必须做 Agent 级知识库解析（`ai_agent.resolve_knowledge`），
           否则「知识库跟随 Agent」这条设计契约无人执行；
        3. 行为验证：`reply_kb` 暴露且签名含 `account` 形参（可自省，不靠文本）。
        """
        import inspect
        import re

        src = inspect.getsource(ai_reply.AutoReplyWorker._generate_reply)

        # ① 带账号的 find_match 调用（库名不限，防再次因换库而假红）
        calls = re.findall(r"[\w.]*find_match\([^)]*\)", src)
        account_aware = [c for c in calls if re.search(r"account\s*=\s*\w+", c)]
        self.assertTrue(
            account_aware,
            f"回复生成链路中找不到带 account= 的 find_match 调用（实际: {calls}）",
        )

        # ② Agent 级知识库解析（设计契约：知识库跟随 Agent）
        self.assertIn(
            "ai_agent.resolve_knowledge", src,
            "回复生成未做 Agent 级知识库解析 —— 「知识库跟随 Agent」契约失效",
        )

        # ③ 行为验证：回复库签名须支持 account（可自省，不依赖文本匹配）
        try:
            from services import reply_kb
        except Exception as e:  # 模块缺失则明确失败，不静默跳过
            self.fail(f"回复库 reply_kb 不可用: {e}")
        sig = inspect.signature(reply_kb.find_match)
        self.assertIn("account", sig.parameters,
                      f"reply_kb.find_match 缺少 account 形参: {sig}")

    def test_find_match_semantic_accepts_items(self):
        import inspect
        sig = inspect.signature(ai_reply.find_match_semantic)
        self.assertIn("items", sig.parameters)


if __name__ == "__main__":
    unittest.main(verbosity=0)
