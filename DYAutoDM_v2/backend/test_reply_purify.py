# -*- coding: utf-8 -*-
"""向量提纯回归（2026-09-28）—— 确定性替身，零网络。

设计契约：
  学习必须「聚类 → 通用性门槛 → 剔个案 → 抽象 → 专业实质门槛 → 去重」，
  **不可退化为照搬原文**；向量不可用时按契约**不产出**（不是照搬）。

负控（必须变红）：
  · 只出现一次的问法（单成员簇）→ 不进候选；
  · 全是该客户个例的簇 → 整簇丢弃；
  · LLM 失败 → 该簇跳过（绝不写原样截断）；
  · 回复无工伤专业实质 → 丢弃；
  · embedding 不可用 → ok=False 且 0 候选。
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("DY_APP_ROOT", tempfile.mkdtemp(prefix="dy_purify_"))

from services import reply_purify as P   # noqa: E402


# ── 确定性替身 ──────────────────────────────────────────────
_VEC = {
    "赔": [1.0, 0.0, 0.0],
    "补偿": [1.0, 0.0, 0.0],
    "等级": [0.0, 1.0, 0.0],
    "你好": [0.0, 0.0, 1.0],
}


def fake_embed(texts):
    out = []
    for t in texts:
        v = [0.0, 0.0, 0.0]
        for k, vec in _VEC.items():
            if k in t:
                v = vec
                break
        out.append(v)
    return out, "fake-model"


def fake_llm_factory(mapping):
    def _llm(prompt):
        for k, resp in mapping.items():
            if k in prompt:
                return resp
        return None
    return _llm


class TestJudges(unittest.TestCase):
    def test_case_specific(self):
        self.assertTrue(P.is_case_specific("湖北黄冈冻库受伤能赔多少"))   # 地名+伤情
        self.assertTrue(P.is_case_specific("做了骨水泥手术能平几级"))     # 数字+手术
        self.assertTrue(P.is_case_specific("x" * 40))                    # 超长
        self.assertFalse(P.is_case_specific("工伤一般怎么赔"))
        self.assertFalse(P.is_case_specific("赔偿包括哪些项目"))

    def test_professional_substance(self):
        self.assertTrue(P.has_professional_substance("十级伤残为 7 个月本人工资"))
        self.assertTrue(P.has_professional_substance("可以申请工伤认定，注意 1 年时效"))
        self.assertFalse(P.has_professional_substance("可以的"))
        self.assertFalse(P.has_professional_substance("嗯嗯好的呀"))


class TestPurifyPipeline(unittest.TestCase):
    def test_singletons_and_case_dropped(self):
        """两个「赔」簇成员 + 两个单例 → 只应产出 1 条（簇），单例全弃。"""
        pairs = [
            ("工伤怎么赔", "十级伤残按 7 个月本人工资赔，另外还有停工留薪期工资。"),
            ("受伤了赔偿怎么算", "先做工伤认定与劳动能力鉴定，再按等级核算一次性伤残补助金。"),
            ("你好", "你好，请问有什么可以帮您"),
            ("在吗", "在的"),
        ]
        llm = fake_llm_factory({"赔": '{"question":"工伤怎么赔偿","answer":"需先认定工伤并做劳动能力鉴定，按伤残等级赔偿，含一次性伤残补助金与停工留薪期工资。"}'})
        r = P.purify(pairs, embed_fn=fake_embed, llm_fn=llm)
        self.assertTrue(r["ok"])
        self.assertEqual(len(r["candidates"]), 1, r["stats"])
        self.assertEqual(r["candidates"][0]["cluster_size"], 2)

    def test_case_only_cluster_dropped(self):
        """簇内全是含个案信息的问法 → 整簇丢弃（不产生通用条目）。"""
        pairs = [
            ("湖北黄冈冻库受伤2019年能赔多少", "按等级核算。"),
            ("江苏盐城2020年冻库受伤赔偿标准", "需鉴定后核算。"),
        ]
        r = P.purify(pairs, embed_fn=fake_embed, llm_fn=fake_llm_factory({}))
        self.assertTrue(r["ok"])
        self.assertEqual(len(r["candidates"]), 0)
        self.assertGreaterEqual(r["stats"]["dropped_case_only"], 1)

    def test_llm_fail_skips_no_copy(self):
        """LLM 不可用 → 该簇跳过，**绝不照搬原文**入库。"""
        pairs = [("工伤怎么赔", "十级 7 个月本人工资"),
                 ("赔偿怎么算", "按等级核算一次性伤残补助金")]
        r = P.purify(pairs, embed_fn=fake_embed, llm_fn=lambda p: None)
        self.assertTrue(r["ok"])
        self.assertEqual(len(r["candidates"]), 0)
        self.assertGreaterEqual(r["stats"]["dropped_llm_fail"], 1)

    def test_no_substance_dropped(self):
        """LLM 回归了无专业实质的答复（共情/占位）→ 丢弃。"""
        pairs = [("工伤怎么赔", "十级 7 个月"),
                 ("赔偿算多少", "按等级算")]
        llm = fake_llm_factory({"赔": '{"question":"工伤赔偿","answer":"理解你的心情，别着急"}'})
        r = P.purify(pairs, embed_fn=fake_embed, llm_fn=llm)
        self.assertEqual(len(r["candidates"]), 0)
        self.assertGreaterEqual(r["stats"]["dropped_no_substance"], 1)

    def test_embed_unavailable_no_degrade(self):
        """向量不可用 → ok=False、0 候选（契约：不降级照搬）。"""
        pairs = [("工伤怎么赔", "十级 7 个月"), ("赔偿多少", "按等级")]
        r = P.purify(pairs, embed_fn=lambda ts: (None, None), llm_fn=fake_llm_factory({}))
        self.assertFalse(r["ok"])
        self.assertEqual(r["candidates"], [])
        self.assertEqual(r["error"], "embedding_unavailable")

    def test_existing_dedup(self):
        """与库内既有问法高度相似 → 去重丢弃。"""
        pairs = [("工伤怎么赔", "十级 7 个月本人工资"),
                 ("受伤怎么赔偿", "按等级核算伤残补助金")]
        llm = fake_llm_factory({"赔": '{"question":"工伤怎么赔偿","answer":"先认定工伤再按等级赔偿。"}'})
        r = P.purify(pairs, existing_questions=["工伤怎么赔偿啊"],
                     embed_fn=fake_embed, llm_fn=llm)
        self.assertEqual(len(r["candidates"]), 0)
        self.assertGreaterEqual(r["stats"]["dropped_dedup"], 1)


class TestLifecycle(unittest.TestCase):
    def setUp(self):
        # 自清理：候选区 + 正式库都清空（避免复用 DY_APP_ROOT 时的跨轮污染）
        P._kv_set(P._KV_CAND, [])
        from services import reply_kb
        reply_kb.save_items([])

    def test_stage_confirm_reject(self):
        n = P.stage_candidates([
            {"question": "q1", "answer": "a1", "cluster_size": 2, "evidence": ["e"]},
            {"question": "q2", "answer": "a2", "cluster_size": 2},
        ], account="t")
        self.assertEqual(n, 2)
        self.assertEqual(len(P.list_candidates()), 2)
        # 幂等：重复 stage 同问法不重复加
        self.assertEqual(P.stage_candidates([{"question": "q1", "answer": "a1"}]), 0)
        ids = [c["id"] for c in P.list_candidates()]
        out = P.confirm_candidates(ids=ids[:1])
        self.assertEqual(out["added"], 1)
        self.assertEqual(out["remaining"], 1)
        self.assertEqual(P.reject_candidates([ids[1]]), 1)
        self.assertEqual(len(P.list_candidates()), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
