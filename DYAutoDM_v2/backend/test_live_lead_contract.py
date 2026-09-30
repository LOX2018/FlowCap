# -*- coding: utf-8 -*-
"""首触三段契约 + 文案来源标识 + 表格结构 · 通用门禁（2026-09-30）

架构定位（用户 2026-09-30 校正，**本文关键**）：
  本项目是**通用**采集/私信引擎，不是某个行业的专属系统。故本门禁断言的是
  **与领域无关的结构契约**，不把任何行业规则固化成机械判据：
    · 「表明身份 / 结合需求 / 留资钩子」是三段**结构**（顺序与要件），
      具体句子内容由 Agent 配置（system_prompt / fallback_pool / 知识库）决定；
    · 判定口径（什么材料是必要前置）属 **Agent 配置层**，引擎层只保证
      「不得把 Agent 未声明的条件当作结论讲出来」这一**通用**约束。
  判据来源（用户原话）：
    1. 「发送内容不需要完整的全部展示，显示前10个字就行，不要破坏表格结构」
    2. 「针对于直播间捕获的陌生客户，评论生成的文本应该是：第一表明身份；
       第二结合他的需求；第三要求留资钩子」
    3. 「发送私信的文本目前没有标识，根本不知道这个文本到底是词库，还是
       AI 生成，还是兜底文档」

运行：cd DYAutoDM_v2/backend && python -m unittest test_live_lead_contract -v
"""
from __future__ import annotations

import asyncio
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from services import ai_reply  # noqa: E402


CFG = {"max_reply_len": 120, "min_reply_len": 5, "forbidden_words": []}


class TestDomainNeutrality(unittest.TestCase):
    """G0：通用引擎**不得**内置任何领域/商家知识（本轮架构校正的核心）。"""

    # 这些词一旦出现在引擎层常量/默认模板里，即说明领域知识被硬编码（回归）
    INDUSTRY_TOKENS = ("唐律", "工伤", "内固定", "十级", "九级", "评残", "理赔顾问")

    def _engine_texts(self):
        """引擎层的**内置默认**文本（不含注释）：兜底池 / 直播兜底 / 场景契约。"""
        cfg = dict(ai_reply._DEFAULT_CONFIG)
        pool = " ".join(str(x) for x in (cfg.get("fallback_pool") or []))
        fb = ai_reply.fallback_reply(dict(cfg, fallback_pool=[]))
        live = ai_reply._LIVE_FALLBACK_EXTRA
        rules = ai_reply._LIVE_CONTACT_RULES
        return {"fallback_pool": pool, "fallback_reply": fb,
                "live_fallback": live, "live_rules": rules}

    def test_g0_default_texts_have_no_industry_vocabulary(self):
        for name, text in self._engine_texts().items():
            for tok in self.INDUSTRY_TOKENS:
                self.assertNotIn(
                    tok, text,
                    f"引擎内置文本 {name} 出现行业词「{tok}」——领域知识必须放 Agent 层: {text[:60]!r}")

    def test_g0b_merchant_placeholder_is_substituted(self):
        """G0b：默认模板用 {merchant} 占位；未替换会外发字面量（实测风险）。"""
        cfg = dict(ai_reply._DEFAULT_CONFIG)
        # 未配置商家名 ⇒ 中性称谓，且**不得残留占位符**
        fb_pool0 = ai_reply.fallback_reply(dict(cfg, fallback_pool=None))
        fb_live0 = ai_reply.live_fallback_reply(dict(cfg))
        for t in (fb_pool0, fb_live0):
            self.assertNotIn("{merchant}", t, f"占位符未替换: {t!r}")
        # 配置商家名 ⇒ 直播兜底必须出现（证明接线生效，不是摆设）
        cfg2 = dict(cfg, merchant_name="示例团队")
        self.assertIn("示例团队", ai_reply.live_fallback_reply(cfg2))
        # fallback_reply 随机选一条，不能断言 merchant 一定出现；
        # 但**末位默认**（pool=None）必须出现
        self.assertIn("示例团队", ai_reply.fallback_reply(dict(cfg2, fallback_pool=None)))
        self.assertIn("示例团队", ai_reply.build_system_prompt(cfg2, "示例提问"))

    def test_g0c_brand_uses_config_not_hardcode(self):
        """G0c：`ensure_live_brand` 只做**结构**补齐，不写死任何行业身份。"""
        s = ai_reply.ensure_live_brand("你的问题我需要看资料再答，留个手机号我发你。")
        self.assertTrue(s)
        for tok in self.INDUSTRY_TOKENS:
            self.assertNotIn(tok, s, f"结构补齐写死了行业身份「{tok}」: {s!r}")


class TestTripleContract(unittest.TestCase):
    """G3/G4：三段结构（身份 / 结合需求 / 留资钩子）在引擎层**结构成立**。"""

    def test_g3_triple_structure_is_declared_and_ordered(self):
        """G3：三段结构必须在场景契约里**显式声明且顺序固定**。"""
        r = ai_reply._LIVE_CONTACT_RULES
        i_id = r.find("表明身份")
        i_need = r.find("结合他的需求")
        i_hook = r.find("留资钩子")
        for name, idx in (("表明身份", i_id), ("结合他的需求", i_need), ("留资钩子", i_hook)):
            self.assertGreaterEqual(idx, 0, f"三段结构缺「{name}」")
        self.assertTrue(i_id < i_need < i_hook, "三段结构顺序被改动（应为 身份→需求→钩子）")
        self.assertIn("同一轮", r, "缺少「引导后同一轮必须收口到留资钩子」的约束")

    def test_g4_fallbacks_carry_hook_and_identity(self):
        """G4：兜底话术的留资钩子（结构要件）。

        直播兜底是**单条**（确定）⇒ 必须带钩子；
        主兜底是**随机池** ⇒ 不要求每条都带，但池里至少有一条带钩子（保证留资能力），
        且末位默认（pool=None）必须带钩子。
        """
        # 直播兜底（单条，确定）
        live_fb = ai_reply.live_fallback_reply(dict(ai_reply._DEFAULT_CONFIG))
        self.assertTrue(live_fb, "直播兜底为空")
        self.assertTrue(ai_reply._has_lead_ask(live_fb), f"直播兜底缺留资钩子: {live_fb!r}")
        self.assertGreaterEqual(len(live_fb), CFG["min_reply_len"])

        # 主兜底池：至少一条带钩子
        pool = list(ai_reply._DEFAULT_CONFIG.get("fallback_pool") or [])
        self.assertTrue(pool, "主兜底池为空")
        has_hook = [t for t in pool if ai_reply._has_lead_ask(str(t))]
        self.assertTrue(has_hook, f"主兜底池里没有任何一条带留资钩子: {pool}")

        # 末位默认（pool=None）必须带钩子
        fb_default = ai_reply.fallback_reply(dict(ai_reply._DEFAULT_CONFIG, fallback_pool=None))
        self.assertTrue(fb_default, "末位默认兜底为空")
        self.assertTrue(ai_reply._has_lead_ask(fb_default), f"末位默认缺留资钩子: {fb_default!r}")

    def test_g4b_basis_comes_from_agent_config_not_engine(self):
        """G4b（架构判据，2026-09-30 用户校正）：

        「什么材料是必要前置」属**领域判据** ⇒ 必须由 Agent 配置提供，
        **不得**由引擎的机械门禁固化。断言两件事：
          ① 场景契约明确把判定依据指向 Agent（system_prompt / 知识库）；
          ② 引擎层不含该领域判据的实现（无「内固定⇒等级」类正则）。
        """
        r = ai_reply._LIVE_CONTACT_RULES
        self.assertIn("以本 Agent 的 system_prompt", r,
                      "场景契约未把判定依据指向 Agent 层")
        self.assertIn("知识库", r)
        src = open(os.path.join(HERE, "services", "ai_reply.py"), encoding="utf-8").read()
        # 允许出现在注释/文档里的说明，但**不得**出现在可执行正则中
        self.assertNotIn("_RE_LIVE_FIXATION", src,
                         "引擎层又出现领域专属机械门禁（丧失通用性）")
        self.assertNotIn("内固定", "".join(
            ln for ln in src.splitlines()
            if "re.compile" in ln or ln.strip().startswith("r\"")),
            "领域词被写进了可执行正则")


class TestContentSourceTraceability(unittest.TestCase):
    """G5/G6：文案来源（AI / 词库 / 原文）在取值点被记录并下发。"""

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

    def _mk(self, pick=lambda: "词库文案"):
        class _Auth:
            account_name = ""

        return self._d.DispatchCenter(
            auth=_Auth(), max_target=5, delay_range=(0, 0), interval=0.0,
            pick_dm_message=pick)

    def _run(self, dc, key, gen):
        dc.gen_dm_message = gen
        target = {"user_id": key, "nickname": "测试", "comment": "弹幕原文"}
        dc._ensure_record(key, target)
        asyncio.run(dc._do_send(key, target))
        return dc.records[key]

    def test_g5_source_recorded_per_branch(self):
        async def _gen_ai(t):
            return "AI 生成的文案"

        async def _gen_empty(t):
            return ""

        async def _gen_boom(t):
            raise RuntimeError("模拟 AI 故障")

        rec = self._run(self._mk(), "k1", _gen_ai)
        self.assertEqual((rec.content, rec.content_source), ("AI 生成的文案", "AI"))

        rec = self._run(self._mk(), "k2", _gen_empty)
        self.assertEqual((rec.content, rec.content_source), ("词库文案", "词库"))

        rec = self._run(self._mk(), "k3", _gen_boom)
        self.assertEqual((rec.content, rec.content_source), ("词库文案", "词库"))

    def test_g5b_fallback_to_comment_marked_yuanwen(self):
        dc = self._mk(pick=None)
        dc.gen_dm_message = None
        target = {"user_id": "k9", "nickname": "测试", "comment": "弹幕原文"}
        dc._ensure_record("k9", target)
        asyncio.run(dc._do_send("k9", target))
        rec = dc.records["k9"]
        self.assertEqual((rec.content, rec.content_source), ("弹幕原文", "原文"))

    def test_g6_source_is_emitted_to_api(self):
        src = open(os.path.join(HERE, "api", "tasks.py"), encoding="utf-8").read()
        self.assertIn('"content_source": d.get("content_source")', src,
                      "api/tasks._records_from_adm 未下发 content_source")
        self.assertIn("content_source", src, "导出未带来源列")

    def test_g6b_model_field_exists(self):
        from models.task import SendRecord
        self.assertIn("content_source", SendRecord.model_fields)


class TestTableRenderingContract(unittest.TestCase):
    """G7/G8：前端表格重设计的静态契约（前 10 字 / 不破坏结构 / 单一真源）。"""

    _FE = os.path.join(os.path.dirname(HERE), "frontend", "src", "components", "live")

    def _read(self, name):
        with open(os.path.join(self._FE, name), encoding="utf-8") as f:
            return f.read()

    def test_g7_preview_chars_is_ten_and_shared(self):
        shared = self._read("live-shared.tsx")
        self.assertIn("export const DM_PREVIEW_CHARS = 10;", shared)

    def test_g7b_both_views_use_shared_helpers(self):
        for f in ("live-page.tsx", "LiveReviewMode.tsx"):
            s = self._read(f)
            for helper in ("dmTitle", "sourceMetaOf", "DM_PREVIEW_CHARS"):
                self.assertIn(helper, s, f"{f} 未使用共享 {helper}")
            self.assertNotIn("const SOURCE_META: Record<string", s,
                             f"{f} 内联了第二份来源映射（契约漂移）")

    def test_g8_table_structure_preserved(self):
        s = self._read("live-page.tsx")
        for col in ("发送时间", "发言人", "评论内容", "私信状态", "私信文案", "私信时间"):
            self.assertIn(f">{col}</Th>", s, f"列「{col}」缺失或改名")
        self.assertEqual(s.count("colSpan={6}"), 2, "空态行 colSpan 必须仍是 6（列数未变）")
        self.assertIn("title={dmTitle(r)}", s)


if __name__ == "__main__":
    unittest.main(verbosity=2)
