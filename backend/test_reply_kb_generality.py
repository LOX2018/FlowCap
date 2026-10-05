# -*- coding: utf-8 -*-
"""D5 内容层回归（2026-09-28，零网络）：命中库门槛 / 语义开关隔离 / 审计可逆。

覆盖三个真实缺陷（真机取证）：
  ① 命中库「命中即回」**绕过出口护栏** ⇒ 库内断言原件直发（如「就是9级水平」）。
  ② 命中库语义级沿用知识库的宽松阈值 0.40 ⇒ **张冠李戴**直答客户。
  ③ auto 存量 81 条中 52 条为 占位碎片/个案/断言 ⇒ 需**可逆**降级而非直删。
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["FLOWCAP_APP_ROOT"] = tempfile.mkdtemp(prefix="dy_d5_")

from services import reply_kb as R            # noqa: E402
from services import reply_purify as P        # noqa: E402


class TestEntryUsable(unittest.TestCase):
    """匹配侧门槛：个案化/寒暄/断言 auto 条目必须失效（人工条目不受限）。"""

    def test_manual_always_usable(self):
        self.assertTrue(R._entry_usable(
            {"source": "manual", "question": "湖北黄冈", "answer": "好的"}))
        self.assertTrue(R._entry_usable(
            {"source": "manual", "question": "x", "answer": "就是9级"}))

    def test_auto_case_specific_blocked(self):
        # 数字/地名/伤情 = 个案，不宜作通用话术（真机样例）
        for it in (
            {"source": "auto", "question": "我只有初次的髌骨撕脱骨折的诊断证明可以评十级吧",
             "answer": "目前伤情只有十级"},
            {"source": "auto", "question": "湖北黄冈", "answer": "好的"},
            {"source": "auto", "question": "江苏盐城", "answer": "好的"},
        ):
            self.assertFalse(R._entry_usable(it), it["question"])

    def test_auto_level_assertion_blocked(self):
        # 断言（无豁免语）⇒ 失效：命中库绕过出口护栏，必须在此拦
        self.assertFalse(R._entry_usable(
            {"source": "auto", "question": "能确定上九级吗？", "answer": "你的伤情就是9级水平的"}))
        self.assertFalse(R._entry_usable(
            {"source": "auto", "question": "我这个多少级", "answer": "肯定是十级"}))
        # 口径式断言（真机库内「目测伤9级」4 条；答句加长时不得漏网）
        self.assertFalse(R._entry_usable(
            {"source": "auto", "question": "我腰椎做手术了，目测能定几级", "answer": "目测伤9级"}))
        self.assertFalse(R._entry_usable(
            {"source": "auto", "question": "帮我看下", "answer": "根据描述目测大概能到九级的水平，你先准备材料"}))
        # 带豁免语 ⇒ 不算断言（正控，防误伤）
        self.assertTrue(R._entry_usable(
            {"source": "auto", "question": "我这个多少级", "answer": "大致九级，最终以鉴定结论为准"}))

    def test_placeholder_still_blocked(self):
        # 原长度/占位门槛不得被新逻辑破坏
        self.assertFalse(R._entry_usable(
            {"source": "auto", "question": "你好", "answer": "可以的"}))
        self.assertFalse(R._entry_usable(
            {"source": "auto", "question": "在吗", "answer": "嗯嗯"}))


class TestSemSwitchIsolation(unittest.TestCase):
    """命中库语义级必须与知识库 RAG 语义级**隔离**（独立开关 + 严格阈值）。"""

    BASE = {"sem_enabled": True, "sem_base_url": "http://x/v1"}

    def test_off_by_default(self):
        # 只开总开关、未开命中库专用开关 ⇒ 命中库语义级不得运行
        self.assertFalse(R._sem_ready(self.BASE))

    def test_on_when_both_switches(self):
        cfg = dict(self.BASE, reply_sem_enabled=True)
        self.assertTrue(R._sem_ready(cfg))

    def test_sem_disabled_globally(self):
        self.assertFalse(R._sem_ready({"sem_enabled": False,
                                       "reply_sem_enabled": True,
                                       "sem_base_url": "http://x/v1"}))


class TestAuditReversible(unittest.TestCase):
    """存量审计：脏 auto 移入候选区 —— 可逆（候选区可回滚），人工条目不动。"""

    def setUp(self):
        with R._lock:
            from services.kv_store import kv_set
            kv_set(R._KV_REPLY_KB, [
                {"id": 1, "question": "湖北黄冈", "answer": "好的",
                 "source": "auto", "enabled": True, "hits": 0},
                {"id": 2, "question": "工伤怎么认定需要什么材料", "answer": "先固定证据，30日内单位报，单位不报你1年内可自行申请。",
                 "source": "auto", "enabled": True, "hits": 0},
                {"id": 3, "question": "能确定九级吗", "answer": "你的伤情就是9级水平的",
                 "source": "auto", "enabled": True, "hits": 0},
                {"id": 4, "question": "人工条目", "answer": "人工内容在这里不动它",
                 "source": "manual", "enabled": True, "hits": 0},
            ])
            kv_set(P._KV_CAND, [])

    def test_audit_moves_only_dirty_auto(self):
        r = P.audit_legacy_auto()
        self.assertEqual(r["scanned"], 4)
        self.assertEqual(r["moved"], 2)      # #1 个案/碎片 + #3 等级断言
        self.assertEqual(r["kept"], 2)       # #2 合格 auto + #4 人工
        rest = R.list_items()
        self.assertEqual([it["id"] for it in rest], [2, 4])  # 合格 auto 与人工都留下
        self.assertEqual(len(P.list_candidates()), 2)        # 移入候选区（可逆）

    def test_audit_idempotent_second_run(self):
        P.audit_legacy_auto()
        r2 = P.audit_legacy_auto()
        self.assertEqual(r2["moved"], 0)     # 二次运行不再改动（幂等）


if __name__ == "__main__":
    unittest.main(verbosity=2)
