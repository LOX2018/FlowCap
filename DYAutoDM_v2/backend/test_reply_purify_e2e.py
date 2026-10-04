# -*- coding: utf-8 -*-
"""端到端：learn_from_history 是否真的走「向量提纯 → 候选」（2026-09-28）。

判据：真实调用链（DM 库 → 抽对 → 提纯）在注入替身下**产出通用候选并暂存**，
且**不直接写正式库**；向量不可用时**不落任何条目**（不降级照搬）。
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 🔴 2026-10-04 必修：setUp 里有 `DELETE FROM dm_messages`（**无 WHERE，清全表**）。
# 旧写法只设 `DY_APP_ROOT` env，而 `vbrowser.app_root()` 在目录不存在/被外部预设时
# 会静默忽略并回退 ⇒ 该 DELETE 可能清掉**生产库**的 303 条消息。
from test_isolation import isolate   # noqa: E402  必须在 import database 之前
isolate("reply_purify_e2e")

import database                      # noqa: E402
from services import reply_kb, reply_purify as P   # noqa: E402


def _fake_embed(texts):
    out = []
    for t in texts:
        v = [0.0, 0.0, 0.0]
        if "赔" in t or "补偿" in t or "伤残" in t:
            v = [1.0, 0.0, 0.0]
        elif "鉴定" in t or "认定" in t:
            v = [0.0, 1.0, 0.0]
        out.append(v)
    return out, "fake"


def _fake_llm(prompt):
    if "赔" in prompt:
        return '{"question":"工伤赔偿怎么算","answer":"先做工伤认定与劳动能力鉴定，再按伤残等级核算一次性伤残补助金与停工留薪期工资。"}'
    return "{}"


class TestLearnE2E(unittest.TestCase):
    def setUp(self):
        # 干净库 + 干净候选/正式库
        P._kv_set(P._KV_CAND, [])
        reply_kb.save_items([])
        conn = database.get_db()
        conn.execute("DELETE FROM dm_messages")
        conn.commit()

    def _seed(self):
        conn = database.get_db()
        rows = [
            # 会话1：通用问法 A
            ("A", "c1", "them", "工伤怎么赔"),
            ("A", "c1", "me", "十级伤残 7 个月本人工资"),
            # 会话3：同簇问法 B（**不同会话**才算通用 —— 2026-09-28 修复点）
            ("A", "c3", "them", "工伤赔偿怎么算"),
            ("A", "c3", "me", "按伤残等级核算一次性伤残补助金"),
            # 会话2：单例（应弃）
            ("A", "c2", "them", "你好"),
            ("A", "c2", "me", "你好，请问有什么可以帮您"),
        ]
        for a, c, r, t in rows:
            conn.execute(
                "INSERT INTO dm_messages(account,conv_id,role,text,msg_type,ts) "
                "VALUES(?,?,?,?,?,?)", (a, c, r, t, "text", 1.0))
        conn.commit()

    def test_learn_stages_candidates_not_formal(self):
        self._seed()
        # 注入确定性替身
        orig_e, orig_l = P._default_embed, P._default_llm
        P._default_embed, P._default_llm = _fake_embed, _fake_llm
        try:
            r = reply_kb.learn_from_history(account="A")
        finally:
            P._default_embed, P._default_llm = orig_e, orig_l
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["added"], 0, "不得直接写正式库")
        self.assertGreaterEqual(r.get("staged", 0), 1, r)
        self.assertEqual(len(reply_kb.list_items()), 0, "正式库应保持为空")
        cands = P.list_candidates()
        self.assertGreaterEqual(len(cands), 1)
        self.assertIn("工伤", cands[0]["answer"])

    def test_learn_embed_down_no_write(self):
        self._seed()
        orig_e, orig_l = P._default_embed, P._default_llm
        P._default_embed, P._default_llm = (lambda ts: (None, None)), _fake_llm
        try:
            r = reply_kb.learn_from_history(account="A")
        finally:
            P._default_embed, P._default_llm = orig_e, orig_l
        self.assertFalse(r["ok"], r)
        self.assertEqual(r["reason"], "embedding_unavailable")
        self.assertEqual(len(reply_kb.list_items()), 0)
        self.assertEqual(len(P.list_candidates()), 0)

    def test_confirm_promotes_to_formal(self):
        P.stage_candidates([{"question": "工伤赔偿怎么算",
                             "answer": "按等级核算一次性伤残补助金"}], account="A")
        ids = [c["id"] for c in P.list_candidates()]
        out = P.confirm_candidates(ids=ids)
        self.assertEqual(out["added"], 1)
        self.assertEqual(len(reply_kb.list_items()), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
