# -*- coding: utf-8 -*-
"""留资处置护栏 · 门禁（2026-09-29）

判据来源：用户实测反馈（会话「668」截图 + 原话）
  「要知道私信的目的就是留资，这个对方的聊天内容如果是有明确的问题则先针对问题
   回复体现专业性同时引导留资，但如果是这种对话对象，他的聊天内容没有明确目标的
   人群直接引导留资就行，别说什么之后再联系，抖音是快平台，客户前一秒还在你这
   下一秒就去别人那了，根本没有沉淀的必要。」

**契约（2026-09-29 定稿，两轮实机验证后收敛）**：
  · 判据 = **回复不含「索要联系方式」即未推进留资**（与用户第 3 条机械规则字面一致）；
  · 处置顺序 = 定向重试 → 保留专业回答+末尾追加索要 → 整句替换为引导留资话术；
  · **降级路径（兜底池）同受约束** —— 模型挂了也不能白放走线索。

运行：cd DYAutoDM_v2/backend && python -m unittest test_lead_disposition_guard -v
"""
from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from services import ai_reply as A  # noqa: E402


class TestLeadDispositionGuard(unittest.TestCase):
    CFG = dict(A._DEFAULT_CONFIG)

    # ── G1 正控：截图原文（放走线索）必须判「未推进」 ────────────────────
    STALLED = [
        # 截图原文（会话 668）
        "好，问问进度。认定书下来第一时间通知我，我帮你算清赔偿清单。",
        "我等你人社局问完的消息，认定书到手我就帮你算清单。",
        "没认定就没法评残，也拿不到基金那部分钱",
        "我看下你的病例",
        # 同类「以后再说」式收尾
        "好的，等你的认定结果。",
        "了解了。",
        "那你先去人社局问问，有消息再说。",
        "嗯嗯，好的。",
    ]

    def test_g1_real_stalled_replies_detected(self):
        for s in self.STALLED:
            self.assertTrue(A._is_lead_stalled(s), f"应判未推进却放过: {s!r}")

    # ── G2 负控：**含索要**的回复不得判沉降 ─────────────────────────────
    ASKING = [
        "留个手机号，我按当地标准把清单算好发你。",
        "你联系方式多少？我给你算个准数。",
        "方便留个电话，我算好发你。",
        "要判断等级得看治疗后遗留的功能障碍程度，不是按伤情名字对号入座。"
        "你把受伤部位、诊断结论发我，我帮你初步看下。方便留个手机号，"
        "我按你当地标准算份清单发你。",
    ]

    def test_g2_asking_replies_not_stalled(self):
        for s in self.ASKING:
            self.assertFalse(A._is_lead_stalled(s), f"误伤（含索要却判沉降）: {s!r}")

    # ── G3 追加索要：**必须保留专业回答** ───────────────────────────────
    def test_g3_append_keeps_professional_part(self):
        pro = "要判断等级得看治疗后遗留的功能障碍程度，不是按伤情名字对号入座。"
        got = A.append_lead_ask(pro, self.CFG)
        self.assertIsNotNone(got, "正常专业回答应可追加索要")
        self.assertIn(pro.rstrip("。"), got, "专业结论被丢掉（违反保专业性优先）")
        self.assertTrue(A._has_lead_ask(got), "追加后必须含索要")

    def test_g3b_append_refuses_deferring(self):
        """含「放走语」时不得追加（否则「等你消息…方便留个手机号」自相矛盾）。"""
        for s in ("等你消息，认定书下来告诉我。", "回头再说吧。", "稍后再联系你。"):
            self.assertIsNone(A.append_lead_ask(s, self.CFG), f"应拒绝追加: {s!r}")

    # ── G4 放走语判据必须锚「人称」：陈述流程不得误判 ─────────────────────
    def test_g4_deferring_judgement(self):
        for s in ("我等你人社局问完的消息。", "认定书下来第一时间通知我。",
                  "那你先去问问，有消息再说。", "稍后再联系你。"):
            self.assertTrue(A._is_deferring(s), f"应判放走: {s!r}")
        for s in ("单位申报后接下来等认定结果，一般 60 天内出。",
                  "等鉴定结论出来才能定级。", "治疗终结后再申请劳动能力鉴定。"):
            self.assertFalse(A._is_deferring(s), f"误判为放走（陈述流程）: {s!r}")

    # ── G5 引导留资话术 ────────────────────────────────────────────────
    def test_g5_lead_fallback_asks(self):
        fb = A.lead_fallback_reply(self.CFG)
        self.assertTrue(fb and len(fb) >= self.CFG["min_reply_len"], fb)
        self.assertTrue(A._has_lead_ask(fb), f"引导留资话术必须含索要: {fb!r}")
        self.assertFalse(A._is_lead_stalled(fb))
        self.assertNotIn("**", fb)

    # ── G6 接线：留资铁律必须真的进了 system prompt ─────────────────────
    def test_g6_rules_wired_into_prompt(self):
        prompt = A.build_system_prompt(dict(A._DEFAULT_CONFIG), "测试")
        self.assertIn("留资铁律", prompt)
        self.assertIn("没有", prompt)
        self.assertIn(A._LEAD_DISPOSITION_RULES.strip()[:20], prompt)

    # ── G7 端到端：桩模型吐「放走句」→ 出口必须带索要 ────────────────────
    def test_g7_stalled_reply_becomes_asking_end_to_end(self):
        import tempfile
        from pathlib import Path

        root = Path(tempfile.mkdtemp(prefix="lead_guard_"))
        (root / "data").mkdir(parents=True, exist_ok=True)
        os.environ["DY_APP_ROOT"] = str(root)

        bad = "好，问问进度。认定书下来第一时间通知我，我帮你算清赔偿清单。"
        orig = A.AIClient

        class _Stub:
            def __init__(self, cfg):
                pass

            def chat_failover(self, *a, **kw):
                return bad

        A.AIClient = _Stub
        try:
            worker = A.AutoReplyWorker.__new__(A.AutoReplyWorker)
            worker.status = dict(getattr(A.AutoReplyWorker, "status", {}) or {})
            cfg = dict(A._DEFAULT_CONFIG)
            cfg.update({"enabled": True, "strict_level": "rag",
                        "sem_enabled": False, "base_url": "http://127.0.0.1:1/v1"})
            reply, source = A.AutoReplyWorker._generate_reply(
                worker, cfg, "好", "验证账号", "0:1:1:2", 1)
        finally:
            A.AIClient = orig
        self.assertTrue(reply, f"应给出含索要的回复，实际 {reply!r}")
        self.assertTrue(A._has_lead_ask(reply), f"出口仍不含索要: {reply!r}")
        self.assertNotEqual(reply, bad, "不得把放走句原样外发")

    # ── G8 降级路径：主兜底池不含索要 ⇒ 必须换成引导留资 ─────────────────
    def test_g8_fallback_pool_guard(self):
        cfg = dict(A._DEFAULT_CONFIG)
        fb = A.fallback_reply(cfg)
        self.assertTrue(A._is_lead_stalled(fb),
                        f"前提假设失效：主兜底池已含索要？{fb!r}")
        self.assertTrue(A._has_lead_ask(A.lead_fallback_reply(cfg)),
                        "引导留资话术必须含索要")

    # ── G9 已留过联系方式 ⇒ 不再替换 ──────────────────────────────────
    def test_g9_contact_shortcircuits(self):
        self.assertTrue(A._contains_personal_contact("我的号是13812345678"))
        self.assertFalse(A._contains_personal_contact("好"))

    # ── G10 命中库最短路必须过同一护栏（用户第一轮点名的存量条目） ────────
    def test_g10_reply_kb_shortcut_is_guarded(self):
        """判据：命中库直回此前**完全绕过**留资护栏；存量里就有
        `没有保守治疗 → 有等级，工伤10级`（用户第一次反馈点名的正是这条）。
        契约：命中库话术若不推进留资 ⇒ 必须补索要 / 换引导留资。
        """
        hit = "有等级，工伤10级"          # 命中库存量原文（src=auto）
        self.assertTrue(A._is_lead_stalled(hit), "该存量话术应判未推进留资")
        fixed = A.append_lead_ask(hit, self.CFG)
        self.assertIsNotNone(fixed)
        self.assertIn("10级", fixed, "不得丢掉库内专业内容")
        self.assertTrue(A._has_lead_ask(fixed), "补齐后必须含索要")
        # 接线断言（防止有人把命中库分支改回 direct return）
        src = open(os.path.join(HERE, "services", "ai_reply.py"), encoding="utf-8").read()
        seg = src.split("hit = reply_kb.find_match(text, account=account)")[-1][:900]
        self.assertIn("_is_lead_stalled(_hit)", seg, "命中库分支未接留资判据")

    # ── G11 直播命中库同样过场景护栏（防止等级断言原件直发） ──────────────
    def test_g11_live_reply_kb_passes_scene_guard(self):
        hit = "有等级，工伤10级"
        self.assertIsNotNone(A._live_guard_violation(hit),
                             "直播命中库的等级断言必须被场景护栏拦住")
        src = open(os.path.join(HERE, "services", "ai_reply.py"), encoding="utf-8").read()
        seg = src.split("hit = reply_kb.find_match(text_in, account=account)")[-1][:600]
        self.assertIn("_live_guard_violation(_hit)", seg, "直播命中库分支未接场景护栏")

    # ── G12 用户点名样本端到端（留存「用户反馈原文」作正控） ──────────────
    def test_g12_user_reported_samples(self):
        """用户两次反馈点名的原始样本，全部必须被治理。"""
        # 第一轮（直播首触）：问等级答赔偿 / 直接给等级
        for s in ("南通十级 7 个月本人工资，保守治疗一般定十级。留个联系方式。",
                  "单根肋骨骨折十级有依据：GB/T 16180",
                  "有等级，工伤10级"):
            if A._live_guard_violation(s) is None and not A._is_lead_stalled(s):
                self.fail(f"样本既未被场景护栏拦、也未判未推进: {s!r}")
        # 第二轮（会话内留资）：把线索放走
        for s in ("好，问问进度。认定书下来第一时间通知我，我帮你算清赔偿清单。",
                  "我等你人社局问完的消息，认定书到手我就帮你算清单。"):
            self.assertTrue(A._is_lead_stalled(s) or A._is_deferring(s),
                            f"放走句未被识别: {s!r}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
