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


class TestOcrFixBatchAA(unittest.TestCase):
    """OCR 审查两条修复门禁（2026-09-29）：A-1 上限生效 / A-2 不丢专业正文。

    每条带**负控**：这里断言的是「修复后」行为；负控在报告里以
    「把守卫改回旧实现即可复现失败」的方式给出（A-1 的负控同时内嵌为
    `test_a1_negative_control_old_path_still_asks`，可直接跑红）。
    """

    CFG = dict(A._DEFAULT_CONFIG)

    # ── 共用：把测试隔离到独立 DY_APP_ROOT（绝不动真实库）────────────────
    def _isolate(self, prefix: str):
        import tempfile
        from pathlib import Path

        import database
        prev = os.environ.get("DY_APP_ROOT")
        root = Path(tempfile.mkdtemp(prefix=prefix))
        (root / "data").mkdir(parents=True, exist_ok=True)
        os.environ["DY_APP_ROOT"] = str(root)
        # 关键：清掉可能已指向真实库的全局连接，否则隔离根不生效。
        database.reset_connection()

        def _cleanup():
            if prev is None:
                os.environ.pop("DY_APP_ROOT", None)
            else:
                os.environ["DY_APP_ROOT"] = prev
            database.reset_connection()

        self.addCleanup(_cleanup)
        return root

    # ═══════════════════════════════════════════════════════════════════
    # A-1（OCR[22] · HIGH）：`max_lead_ask` 上限真正生效
    # ═══════════════════════════════════════════════════════════════════
    def test_a1_cap_binds_no_growth_after_3_rounds(self):
        """连续 3+ 轮后：`_asks` 不再增长，且第 3 轮起回复不含 `_LEAD_ASK_TAIL`。"""
        self._isolate("lead_cap_a1_")
        # 桩模型固定吐「专业但无索要」句 ⇒ 护栏必须介入；重试返回同句 ⇒ 走追加。
        pro = "要判断等级得看治疗后遗留的功能障碍程度，不是按伤情名字对号入座。"
        orig = A.AIClient

        class _Stub:
            def __init__(self, cfg):
                pass

            def chat_failover(self, *a, **kw):
                return pro

        A.AIClient = _Stub
        worker = A.AutoReplyWorker.__new__(A.AutoReplyWorker)
        worker.status = dict(getattr(A.AutoReplyWorker, "status", {}) or {})
        cfg = dict(A._DEFAULT_CONFIG)
        cfg.update({"enabled": True, "strict_level": "rag",
                    "sem_enabled": False, "base_url": "http://127.0.0.1:1/v1"})
        acct, conv = "a1acct", "0:9:9:9"
        counts, replies = [], []
        try:
            for i in range(4):
                reply, _src = A.AutoReplyWorker._generate_reply(
                    worker, cfg, "好", acct, conv, i + 1)
                counts.append(A._lead_ask_count(acct, conv))
                replies.append(reply or "")
        finally:
            A.AIClient = orig

        max_ask = int(cfg.get("max_lead_ask", 2))
        self.assertEqual(counts[:max_ask], [1, 2],
                         f"前 {max_ask} 轮应逐轮计次，实际 {counts}")
        self.assertEqual(counts[max_ask:], [max_ask, max_ask],
                         f"达上限后 _asks 必须不再增长，实际 {counts}")
        # 前 2 轮：追加了索要
        for i in range(max_ask):
            self.assertIn(A._LEAD_ASK_TAIL, replies[i],
                          f"第 {i+1} 轮应追加索要，实际 {replies[i]!r}")
        # 第 3 轮起：不再追加索要（只保留专业回答）
        for i in range(max_ask, 4):
            self.assertNotIn(A._LEAD_ASK_TAIL, replies[i],
                             f"第 {i+1} 轮不得再索要，实际 {replies[i]!r}")
            self.assertFalse(A._has_lead_ask(replies[i]),
                             f"第 {i+1} 轮不应含索要，实际 {replies[i]!r}")
            self.assertIn("功能障碍", replies[i], "达上限后仍须保留专业回答")

    def test_a1_deferring_at_cap_becomes_no_ask_guidance(self):
        """达上限且原句含放走语 ⇒ 改发**不含索要**的专业引导（不再放走、不再计次）。"""
        self._isolate("lead_cap_a1b_")
        bad_defer = "好，问问进度，认定书下来第一时间通知我，我帮你算清单。"
        orig = A.AIClient

        class _Stub:
            def __init__(self, cfg):
                pass

            def chat_failover(self, *a, **kw):
                return bad_defer

        A.AIClient = _Stub
        worker = A.AutoReplyWorker.__new__(A.AutoReplyWorker)
        worker.status = dict(getattr(A.AutoReplyWorker, "status", {}) or {})
        cfg = dict(A._DEFAULT_CONFIG)
        cfg.update({"enabled": True, "strict_level": "rag", "sem_enabled": False,
                    "base_url": "http://127.0.0.1:1/v1",
                    "max_lead_ask": 0})   # 直接处于「已达上限」态
        acct, conv = "a1b", "0:8:8:8"
        try:
            reply, _src = A.AutoReplyWorker._generate_reply(
                worker, cfg, "好", acct, conv, 1)
        finally:
            A.AIClient = orig
        self.assertTrue(reply)
        self.assertNotEqual(reply, bad_defer, "放走句不得原样外发")
        self.assertTrue(A._is_deferring(bad_defer) and not A._is_deferring(reply),
                        f"出口应为不含放走语的引导，实际 {reply!r}")
        self.assertFalse(A._has_lead_ask(reply),
                         f"max_lead_ask=0 时绝不能再索要，实际 {reply!r}")
        self.assertEqual(A._lead_ask_count(acct, conv), 0,
                         "达上限时不得再计次（不 bump）")

    def test_a1_negative_control_old_else_branch_always_asks(self):
        """负控（离线复刻）：旧 `else` 分支无条件追加并 bump ⇒ 第 3 轮仍索要。

        这是把 A-1 修好的分支**改回旧实现**时会得到的输出，用来说明
        「修复前」确实每轮都继续索要（与 ADR-027 D5 矛盾）。
        """
        old_appended = A.append_lead_ask(
            "要判断等级得看治疗后遗留的功能障碍程度，不是按伤情名字对号入座。",
            self.CFG)
        self.assertIsNotNone(old_appended)
        self.assertIn(A._LEAD_ASK_TAIL, old_appended,
                      "旧 else 分支的产物必然含索要 —— 与修复后第 3 轮相反")

    # ═══════════════════════════════════════════════════════════════════
    # A-2（OCR[9] · MED）：超长时不得丢弃专业正文
    # ═══════════════════════════════════════════════════════════════════
    def test_a2_long_professional_body_preserved_after_guard(self):
        pro = ("工伤认定后能否评级，要看治疗后遗留的功能障碍程度：手指末节缺失、"
               "脊柱压缩超过三分之一、关节功能活动明显受限这类才够得上伤残等级；"
               "单纯骨折保守治疗且功能恢复良好的，通常评不上等级，"
               "具体以劳动能力鉴定委员会的结论为准。")
        self.assertGreater(len(pro) + 1 + len(A._LEAD_ASK_TAIL),
                           A._LEAD_ASK_HARD_CAP,
                           "前置假设失效：样本没超过硬上限，测不到截断分支")
        got = A.append_lead_ask(pro, self.CFG)
        self.assertIsNotNone(
            got, "超长专业回复不得整句丢弃（旧实现返回 None ⇒ 正文全丢）")
        self.assertLessEqual(len(got), A._LEAD_ASK_HARD_CAP)
        self.assertIn("功能障碍", got, "丢了专业正文关键片段")
        self.assertTrue(A._has_lead_ask(got), "截断后仍须含索要")
        self.assertNotIn("，，", got)

    # ═══════════════════════════════════════════════════════════════════
    # A-3（OCR[10] · MED）：共享 KV 计数必须原子（并发不丢计数）
    # ═══════════════════════════════════════════════════════════════════
    def test_a3_concurrent_bump_never_loses_counts(self):
        """多线程对照：旧读-改-写丢计数（负控）→ 新逻辑（持 `_LEAD_ASK_LOCK`）0 丢失。"""
        import json
        import threading
        import time

        key = A._KV_ASK_COUNT
        store = {}
        store_lock = threading.Lock()

        # 用线程安全的进程内 store 替换 KV，并在「读之后」插入固定延时，
        # 把旧逻辑的读-改-写窗口放大到必然交错（否则竞态是概率性的、测不稳）。
        def fake_get(k, default=None):
            with store_lock:
                v = json.loads(json.dumps(store.get(k, default)))
            time.sleep(0.002)          # 关键：读与写之间让出
            return v

        def fake_set(k, value):
            with store_lock:
                store[k] = json.loads(json.dumps(value))

        orig_get, orig_set = A._kv_get, A._kv_set
        A._kv_get, A._kv_set = fake_get, fake_set
        n = 24
        try:
            # ---- 负控：旧逻辑（无锁读-改-写）必然丢计数 ----
            def old_bump():
                d = A._kv_get(key, {}) or {}
                d["a3:conv"] = int(d.get("a3:conv", 0) or 0) + 1
                A._kv_set(key, d)

            b1 = threading.Barrier(n)
            ths = [threading.Thread(target=lambda: (b1.wait(), old_bump()))
                   for _ in range(n)]
            for t in ths:
                t.start()
            for t in ths:
                t.join()
            old_final = A._lead_ask_count("a3", "conv")
            self.assertLess(old_final, n,
                            f"负控前提失效：旧逻辑竟未丢计数（{old_final}/{n}）")

            # ---- 正控：新逻辑（持锁）24 次并发 ⇒ 恰好 24，0 丢失 ----
            store.clear()
            b2 = threading.Barrier(n)
            ths = [threading.Thread(
                target=lambda: (b2.wait(), A._bump_lead_ask("a3", "conv")))
                for _ in range(n)]
            for t in ths:
                t.start()
            for t in ths:
                t.join()
            new_final = A._lead_ask_count("a3", "conv")
            self.assertEqual(new_final, n,
                             f"新逻辑丢计数：{new_final}/{n}")
        finally:
            A._kv_get, A._kv_set = orig_get, orig_set

    def test_a3_source_uses_lock(self):
        """静态接线：`_bump_lead_ask`/`_lead_ask_count` 必须持同一把锁。"""
        src = open(os.path.join(HERE, "services", "ai_reply.py"),
                   encoding="utf-8").read()
        seg = src.split("def _lead_ask_count")[-1].split("def lead_fallback_reply")[0]
        self.assertIn("_LEAD_ASK_LOCK", seg, "计数读写未接同步锁")
        self.assertEqual(seg.count("with _LEAD_ASK_LOCK:"), 2,
                         "_lead_ask_count / _bump_lead_ask 应各持一次锁")

    # ═══════════════════════════════════════════════════════════════════
    # A-4（OCR[8] · MED）：直播兜底的「回退主池」分支必须可达
    # ═══════════════════════════════════════════════════════════════════
    def test_a4_live_fallback_extra_empty_falls_back_to_main_pool(self):
        cfg = dict(A._DEFAULT_CONFIG)
        cfg["live_fallback_extra"] = ""       # 额外池不可用
        got = A.live_fallback_reply(cfg)
        self.assertTrue(got, "额外池为空时必须回退主兜底池（非死代码）")
        self.assertIn(got, list(cfg.get("fallback_pool") or []),
                      "回退应落到主兜底池（docstring 承诺的语义）")

    def test_a4_default_still_uses_guidance_extra(self):
        self.assertEqual(
            A.live_fallback_reply(dict(A._DEFAULT_CONFIG)).strip(),
            A._LIVE_FALLBACK_EXTRA.strip(),
            "默认配置必须仍取纯引导话术（零回归）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
