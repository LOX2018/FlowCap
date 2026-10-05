# -*- coding: utf-8 -*-
"""直播首触话术修复 · 门禁（2026-09-29）

判据来源：用户实测反馈三条
  1. 「对于弹幕的内容不要直接说有等级/没等级，直接让发病例，先引导对话，
     不要直接给答案」
  2. 「部分弹幕已经说了骨折，生成回复还让别人说伤情」
     → 引导语必须**锚定观众已说的部位**，不能笼统再问一遍
  3. 「直播首触不再查命中库，改为 AI 生成 + 引导型兜底」

本门禁的全部断言**取自真实故障样本**（截图里的原文），不写理想样例 ——
「只写理想样例的正控挡不住误伤」（llm-feature-debugging Pitfalls）。

运行：cd FlowCap/backend && python -m unittest test_live_contact_guard -v
"""
from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from services import ai_reply  # noqa: E402


class TestLiveContactGuard(unittest.TestCase):
    """G1~G5：给出判据的正控/负控。"""

    CFG = {"max_reply_len": 60, "min_reply_len": 5, "forbidden_words": []}

    # ── 正控（真实故障样本 → 必须被拦）─────────────────────────────────
    REAL_BAD = [
        "能评。压缩小于1/2是九级。安徽有标准。留个联系方式，我给你算赔偿清单。",
        "南通十级 7 个月本人工资，保守治疗一般定十级。留个联系方式。",
        "轻微骨裂大概率评不上等级，除非影响手指功能",
        "腰1骨折是常见工伤，一般保守治疗无功能障碍多为十级。",
        "单根肋骨骨折十级有依据",                       # 出自截图第 2 行
        "保守治疗一般定十级",                            # 出自截图第 9 行
        "有等级，工伤10级",                              # 命中库原件（用户点名那类）
        "韧带损伤本身，大概率评不上十级",
        "胸11压缩性骨折保守治疗，能赔 12 万",           # 金额承诺
    ]

    def test_g1_real_bad_samples_are_blocked(self):
        """G1 正控：真实故障样本必须全部命中护栏（live_guard=True）。"""
        for s in self.REAL_BAD:
            self.assertIsNone(
                ai_reply.validate_reply(s, self.CFG, live_guard=True),
                f"应被拦下却放行: {s!r}",
            )

    # ── 负控（正确输出 → 不得误伤）───────────────────────────────────
    GOOD = [
        "能不能评级要看诊断报告上的描述和有没有做内固定，"
        "你先说下受伤部位，我帮你对一下。",
        "你这个伤情具体几级要以鉴定结论为准，"
        "先跟我说下在哪个省受的伤？",                     # 「几级」出现在引用句里但非断言
        "请把你的病例或诊断报告发我看看，我帮你对一下能不能评。",
        "得看恢复情况，你是什么时候受的伤？",
    ]

    def test_g2_good_replies_survive(self):
        """G2 负控：正常引导型回复**不得**被场景护栏误伤。"""
        for s in self.GOOD:
            got = ai_reply.validate_reply(s, self.CFG, live_guard=True)
            self.assertIsNotNone(got, f"正常回复被误拦: {s!r}")

    def test_g3_private_chat_unaffected(self):
        """G3 隔离：**会话内私信**（live_guard=False）不受场景护栏影响。

        Agent prompt 本身授权会话内「给等级判断」，若这条断言变红，
        说明我把场景护栏扩到了不该管的范围（破坏既有行为）。
        """
        s = "单根肋骨骨折十级有依据：GB/T 16180「身体各部位骨折愈合后"
        self.assertIsNotNone(
            ai_reply.validate_reply(s, self.CFG),
            "会话内回复不应被直播场景护栏影响",
        )

    def test_g4_markdown_stars_stripped(self):
        """G4：`**` 强调符必须被剔除（私信是纯文本，实测截图见原样星号）。"""
        got = ai_reply.validate_reply(
            "轻微骨裂**大概率评不上等级**，除非影响手指功能", self.CFG)
        self.assertIsNotNone(got)
        self.assertNotIn("**", got)

    def test_g5_live_fallback_is_guidance_not_verdict(self):
        """G5：直播兜底必须是**引导型**，且自身不得触发场景护栏。"""
        fb = ai_reply.live_fallback_reply(self.CFG)
        self.assertTrue(fb and len(fb) >= self.CFG["min_reply_len"])
        self.assertIsNone(
            ai_reply._live_guard_violation(fb),
            f"兜底话术自己就违反场景规则: {fb!r}",
        )

    def test_g6_hit_kb_gate_default_off(self):
        """G6：命中库开关**默认关闭**（用户拍板「默认停用」）。

        负控：把构造出的 cfg 打开时，generate_dm_for_live 必须真的去查命中库
        —— 证明开关是**接线**的，不是摆设。
        """
        self.assertIs(
            ai_reply._DEFAULT_CONFIG.get("live_reply_kb_enabled"), False,
            "live_reply_kb_enabled 默认必须为 False",
        )
        # 静态接线判据：源码中查询入口必须在开关分支内
        src = open(os.path.join(HERE, "services", "ai_reply.py"),
                   encoding="utf-8").read()
        seg = src.split("def generate_dm_for_live")[-1]
        self.assertIn('if cfg.get("live_reply_kb_enabled"):', seg,
                      "命中库查询未被开关包裹")
        self.assertIn("live_guard=True", seg,
                      "直播出口未启用场景护栏")


if __name__ == "__main__":
    unittest.main(verbosity=2)
