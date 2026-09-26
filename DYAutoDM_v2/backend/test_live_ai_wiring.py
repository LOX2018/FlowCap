# -*- coding: utf-8 -*-
"""直播私信文案 AI 接线 + dm_pool 契约放宽 —— 防回归门禁（v0.44.14）。

为什么需要这个文件（每条都对应一次真实缺陷）：
  1. **dm_pool 422**：直播页把所选策略（`[{text,enabled}]`）整条 cfg 原样 POST
     到 `/api/engine/start`，而 `TaskConfig.dm_pool` 声明为 `list[str]` →
     Pydantic 在 `resolved()` **之前**就抛 422（`Input should be a valid string`）
     → 「开始自动私信」永远不生效。缺陷长期隐藏，因为：
       - 前端 TS 类型（`live-shared.TaskConfig.dm_pool?: string[]`）**也是错的**，
         两边错得一致 ⇒ 类型检查全绿；
       - 后端单测从未用**对象形态**的 dm_pool 打过 `/api/engine/start`。
      ⇒ 本测试同时锁死「后端声明」与「前端类型」两处，堵住「同错同绿」。
  2. **AI 接线缺失**：直播私信文案此前只走词库随机（`pick_dm_message`），
     AI 生成回调全程没有接线点，而用户已拍板「直播/采集两场景的私信统一走
     调度器，文案 AI 生成优先、词库回落」。
      ⇒ 本测试锁死「接线判定」与「回落语义」。
  3. **错误码必须带设计契约**：新增报错码没有 `design` 不允许提交。

设计约束（本测试绝不触碰任何真实资源）：
  - 不启引擎、不启浏览器、不连网络、不写 DB；
  - `DispatchCenter._do_send` 的发送出口被 monkeypatch 成 stub；
  - `AutoDM` 用 `__new__` 构造（不跑 `__init__`，不拉 auth/浏览器）。
"""
import os
import sys
import unittest

_BACKEND = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__))))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)
_ROOT = os.path.abspath(os.path.join(_BACKEND, ".."))

_OBJ_POOL = [
    {"text": "你好，我是唐律助理，可以留个联系~方式，唐律下播帮你分析", "enabled": True},
    {"text": "工友你好", "enabled": False},
    {"text": "我看下你的病例", "enabled": True},
]


class TestDmPoolContract(unittest.TestCase):
    """1) 契约层：词库两形态都要吃得下，且不得静默抹掉 enabled。"""

    def test_object_pool_accepted(self):
        """对象形态（直播间配置主形态）不得 422 —— 原缺陷载荷。"""
        from models.task import TaskConfig

        c = TaskConfig(live_url="992931212705", max_target=100, interval=60.0,
                       delay="50,120", dm_pool=_OBJ_POOL, acct="测试账号")
        r = c.resolved()
        self.assertEqual(len(r.dm_pool), 3)
        self.assertTrue(all(isinstance(t, dict) for t in r.dm_pool),
                        "对象形态应在 resolved() 后仍保留对象（携带 enabled）")

    def test_object_pool_keeps_enabled_false(self):
        """enabled=False 不得被静默降级成 True（会重发已停用文案）。"""
        from models.task import TaskConfig

        r = TaskConfig(live_url="x", dm_pool=_OBJ_POOL).resolved()
        self.assertIs(r.dm_pool[1].get("enabled"), False)

    def test_string_pool_zero_regression(self):
        """旧 list[str] 形态零回归。"""
        from models.task import TaskConfig

        r = TaskConfig(live_url="x", dm_pool=["A", "B"]).resolved()
        self.assertEqual(r.dm_pool, ["A", "B"])

    def test_frontend_type_is_not_narrower_than_backend(self):
        """前后端类型必须同时正确 —— 禁止「同错同绿」。"""
        fe = open(os.path.join(_ROOT, "frontend", "src", "components", "live",
                               "live-shared.tsx"), encoding="utf-8").read()
        self.assertIn("dm_pool?: (string | {", fe,
                      "前端 TaskConfig.dm_pool 仍写死 string[] → 与后端对象形态不匹配")


class TestDispatchContentSource(unittest.TestCase):
    """2) 调度层：文案来源 AI 优先 / 词库回落（发送出口已 stub）。"""

    def setUp(self):
        from core import dispatch as _d

        self._d = _d
        self.sent = []
        self._orig = _d.send_target_async

        async def _stub(auth, target, content):
            self.sent.append(content)
            return True, "stub"

        _d.send_target_async = _stub

    def tearDown(self):
        self._d.send_target_async = self._orig

    def _mk(self):
        class _Auth:
            account_name = ""      # 空 → 不进入 dm_dispatch 真闸门

        return self._d.DispatchCenter(
            auth=_Auth(), max_target=5, delay_range=(0, 0), interval=0.0,
            pick_dm_message=lambda: "词库文案")

    def _run(self, dc, key, gen):
        import asyncio

        dc.gen_dm_message = gen
        target = {"user_id": key, "nickname": "测试"}
        # _do_send 要求 records 里已存在该 key（正常前置，由 submit/_ensure_record 建立）
        dc._ensure_record(key, target)
        asyncio.run(dc._do_send(key, target))

    def test_ai_content_preferred(self):
        async def _gen(t):
            return "AI 生成的开场白"

        dc = self._mk()
        self._run(dc, "k1", _gen)
        self.assertEqual(self.sent[-1], "AI 生成的开场白")

    def test_empty_ai_falls_back_to_pool(self):
        async def _gen(t):
            return ""

        dc = self._mk()
        self._run(dc, "k2", _gen)
        self.assertEqual(self.sent[-1], "词库文案")

    def test_ai_exception_falls_back_and_send_continues(self):
        async def _gen(t):
            raise RuntimeError("模拟 AI 故障")

        dc = self._mk()
        self._run(dc, "k3", _gen)
        self.assertEqual(self.sent[-1], "词库文案",
                         "AI 异常必须回落词库，绝不能中断本次发送")

    def test_sync_callback_supported(self):
        dc = self._mk()
        self._run(dc, "k4", lambda t: "同步回调文案")
        self.assertEqual(self.sent[-1], "同步回调文案")

    def test_no_wiring_keeps_old_behavior(self):
        dc = self._mk()
        self._run(dc, "k5", None)
        self.assertEqual(self.sent[-1], "词库文案", "未接线必须与改造前逐字一致")

    def test_runtime_hot_update_supports_ai(self):
        dc = self._mk()

        async def _gen(t):
            return "x"

        applied = dc.apply_runtime(gen_dm_message=_gen)
        self.assertIn("ai_gen", applied)
        self.assertIs(dc.gen_dm_message, _gen)


class TestAutoDMWiringDecision(unittest.TestCase):
    """3) 判定层：是否接线由 Agent 绑定 + scopes + enabled + 档位共同决定。"""

    def setUp(self):
        from services import ai_agent, ai_reply

        self._adm = __import__("core.auto_dm", fromlist=["AutoDM"])
        self._agent = ai_agent
        self._air = ai_reply
        self._o = (ai_agent.agent_of, ai_agent.resolve_config,
                   ai_reply.get_config)

    def tearDown(self):
        (self._agent.agent_of, self._agent.resolve_config,
         self._air.get_config) = self._o

    def _make(self, account, aid, extra):
        self._air.get_config = lambda: {"enabled": True, "strict_level": "rag"}
        self._agent.agent_of = lambda a: aid
        self._agent.resolve_config = lambda a, base: {**base, **(extra or {})}
        m = self._adm.AutoDM.__new__(self._adm.AutoDM)   # 不跑 __init__
        m.target_acct = account
        m._acct = None
        return m._make_gen_dm_message()

    def test_unbound_agent_not_wired_no_agent(self):
        """H-16（2026-09-26 用户口径变更，**旧契约已被显式推翻**）：
        账号未绑定 Agent 属**错误状态**，不得静默回落全局配置接线。
        即使全局配置 scopes 含 live，也必须返回 None（回落词库），
        并由 evaluate_live_ai 给出 reason_code=no_agent。

        注：`resolve_config` 自身的「未绑定→零回归」契约**不变**（见 test_ai_agent
        TestZeroRegression）；否决只在「AI 接线判定」这一层收口。"""
        self.assertIsNone(self._make("账号A", None, {"scopes": ["live"]}))

    def test_unbound_agent_without_live_scope_not_wired(self):
        self.assertIsNone(self._make("账号A", None, {"scopes": ["dm"]}))

    def test_global_without_scopes_not_wired(self):
        """防「默认开」漂移：全局配置没有 scopes 键时不得接线。"""
        self.assertIsNone(self._make("账号A", None, {}))

    def test_bound_but_scope_without_live_not_wired(self):
        """绑定只决定「用哪个 Agent」；作用域仍由该 Agent 的 scopes 决定。"""
        self.assertIsNone(self._make("账号A", "ag1", {"scopes": ["dm"]}))

    def test_live_scope_and_enabled_is_wired(self):
        self.assertTrue(callable(self._make(
            "账号A", "ag1",
            {"scopes": ["live"], "enabled": True, "strict_level": "rag"})))

    def test_disabled_not_wired(self):
        self.assertIsNone(self._make(
            "账号A", "ag1", {"scopes": ["live"], "enabled": False}))

    def test_kb_only_not_wired(self):
        """kb_only = 用户显式选择「AI 完全不参与」，必须尊重。"""
        self.assertIsNone(self._make(
            "账号A", "ag1",
            {"scopes": ["live"], "enabled": True, "strict_level": "kb_only"}))

    def test_no_account_context_not_wired(self):
        self.assertIsNone(self._make("", "ag1",
                                     {"scopes": ["live"], "enabled": True}))

    def test_decision_error_is_silent_fallback(self):
        """判定环节异常必须静默回落（不抛出），否则会打断发送链路。"""
        self._agent.agent_of = lambda a: (_ for _ in ()).throw(RuntimeError("kv down"))
        m = self._adm.AutoDM.__new__(self._adm.AutoDM)
        m.target_acct, m._acct = "账号A", None
        self.assertIsNone(m._make_gen_dm_message())


class TestErrCodeContracts(unittest.TestCase):
    """4) 新增错误码必须带设计契约（铁律）。"""

    def test_new_codes_have_design_and_contract(self):
        from errcode import lookup

        for code in ("SEND-038", "SEND-039", "SEND-040"):
            info = lookup(code)
            self.assertIsNotNone(info, f"{code} 未注册")
            self.assertTrue(info.get("design"), f"{code} 缺 design（设计契约）")
            self.assertTrue(info.get("contract"), f"{code} 缺 contract（不变式）")


class TestSourceContract(unittest.TestCase):
    """5) 源码契约：接线点唯一、生成入口唯一。"""

    def test_single_generation_entry(self):
        src = open(os.path.join(_BACKEND, "services", "ai_reply.py"),
                   encoding="utf-8").read()
        self.assertEqual(src.count("def generate_dm_for_live("), 1,
                         "直播文案生成必须只有一个入口（禁止 live/crawl 各写一份）")

    def test_dispatch_is_coroutine_aware(self):
        src = open(os.path.join(_BACKEND, "core", "dispatch.py"),
                   encoding="utf-8").read()
        self.assertIn("inspect.isawaitable", src,
                      "async 生成回调必须被 await（否则 content 会变成协程对象字符串）")

    def test_auto_dm_has_wiring_builder(self):
        src = open(os.path.join(_BACKEND, "core", "auto_dm.py"),
                   encoding="utf-8").read()
        self.assertIn("def _make_gen_dm_message", src)
        self.assertEqual(src.count("gen_dm_message=self.gen_dm_message"), 2,
                         "构造与热更两条路径都要挂上 AI 回调")


if __name__ == "__main__":
    unittest.main(verbosity=2)
