# -*- coding: utf-8 -*-
"""D4 内容层回归：残句护栏 + 专业准确性护栏（2026-09-28，零网络）。

事故判据：
  · AI 对「陈旧性骨折还能认工伤吗」回「能认」—— 与专业知识相悖（陈旧性骨折
    难以证明本次因果，实务中基本不予评定）；且是 2 字残句。
负控（必须变红）：绝对化断言 / 残句 → validate_reply 返回 None（走兜底）。
正控（必须通过）：带「最终以认定结论为准」的正常分析**不得**被误伤。
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("DY_APP_ROOT", tempfile.mkdtemp(prefix="dy_aiq_"))

from services import ai_reply as A   # noqa: E402

EMPTY_CFG = {}


class TestResidualGuard(unittest.TestCase):
    """残句护栏：过短回复不得外发。"""

    def test_two_char_residual_blocked(self):
        # 实测事故原文
        self.assertIsNone(A.validate_reply("能认", EMPTY_CFG))

    def test_short_fillers_blocked(self):
        for r in ("好的", "嗯嗯", "可以", "在的"):
            self.assertIsNone(A.validate_reply(r, EMPTY_CFG), r)

    def test_normal_length_passes(self):
        r = "陈旧性骨折一般很难认定，需要先看有没有本次事故的证据。"
        self.assertEqual(A.validate_reply(r, EMPTY_CFG), r)

    def test_min_len_configurable(self):
        # 放宽到 2 时，2 字回复不再因长度被拦（正常放行）
        self.assertEqual(A.validate_reply("好的", {"min_reply_len": 2}), "好的")
        # 1 字仍被拦（< 2）
        self.assertIsNone(A.validate_reply("好", {"min_reply_len": 2}))


class TestAssertiveGuard(unittest.TestCase):
    """专业准确性护栏：绝对化断言不得外发。"""

    def test_stale_fracture_positive_claim_blocked(self):
        for r in (
            "能认",
            "能认工伤",
            "陈旧性骨折当然能认工伤",
            "陈旧性骨折是可以认定工伤的",
            "你这个能评上九级",
            "肯定是九级",
            "我确定就是十级",
        ):
            self.assertIsNone(A.validate_reply(r, EMPTY_CFG), f"应拦: {r}")

    def test_normal_analysis_not_blocked(self):
        """正控：含『以认定结论为准』的正常专业分析不得被误伤。"""
        for r in (
            "腰椎骨折多评九级，若内固定手术也够九级门槛。以劳动能力鉴定结论为准。你在哪个省？",
            "陈旧性骨折既已陈旧，说明伤后时间较久，难以证明是本次造成，实务中基本不予认定。"
            "你手里有当时的首诊记录和事故证明吗？",
            "您这种陈旧性骨折一般很难认定，需要先补齐本次事故的证据链，最终以认定结论为准。",
            "先别慌，1年内你自己就能申请工伤认定，单位不报也照样办。你伤在哪、哪个省？",
            "十级核心是这几笔：一次性伤残补助金7个月本人工资（基金出），停工留薪期工资照发（单位出）。",
            # 实测误伤原文（2026-09-28 真机）：疑问句「旧伤能不能认」被旧正则当断言。
            "先认工伤，才能评残。旧伤能不能认，看的是**证据链**，不是医院那张片子。",
            "陈旧性骨折能不能认定，得看能否证明是本次事故造成的，你有事故证明吗？",
            "能不能评上级，取决于治疗后留下多少功能障碍，最终以鉴定结论为准。",
        ):
            self.assertEqual(A.validate_reply(r, EMPTY_CFG), r, f"不应拦: {r}")


class TestTruncationResidualGuard(unittest.TestCase):
    """截断后复核（2026-09-28 实测缺陷）：长回复被 max_reply_len 截成极短首句时，
    **不得**把残句外发 —— 长度检查与「实际外发文本」解耦是原始缺陷。"""

    def test_truncation_into_residual_blocked(self):
        # 首句 2 字 + 长尾（总长 > 60）→ 截断取首句「很难」→ 必须判残句
        long_reply = "很难。" + "陈旧性骨折要证明是本次事故造成的才行，" * 4
        self.assertGreater(len(long_reply), 60, "用例需超过 max_reply_len 才会触发截断")
        self.assertIsNone(A.validate_reply(long_reply, EMPTY_CFG))

    def test_truncation_keeps_normal_first_sentence(self):
        # 首句本身够长 → 截断后正常外发（正控，防误伤）
        r = "你这种情况要看压缩程度，减少不到一半通常九级。" + "补充说明" * 12
        out = A.validate_reply(r, EMPTY_CFG)
        self.assertIsNotNone(out)
        self.assertGreaterEqual(len(out), 5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
