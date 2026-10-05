# -*- coding: utf-8 -*-
"""D-12 直播 AI 回复「发送功能性障碍」门禁（2026-09-30）。

覆盖用户报障的四个症状 + 顺带抓出的契约漂移，逐条给出**可判定不变量**与**负控**：

  G1 投递证据时间锚     —— 历史回声帧不得把本次记录判成「已送达」
  G2 意向门匹配 SSOT    —— 空词表不拦；命中放行；未命中拦截
  G3 意向门零回归       —— 未配置 ⇒ 不构造判定（None），调度器不过滤
  G4 意向门落 reason    —— 跳过必须带可读原因（不得静默）
  G5 失败保留试发文案   —— attempted_content 在失败时仍有值
  G6 状态档优先级       —— 失败优先于历史送达证据（文本契约 + 负控）
  G7 错误计数同源       —— 前端「错误」与表格 danger 行同源（文本契约 + 负控）

设计纪律（M-22 / 测试隔离）：本模块 setUpModule 把 DY_APP_ROOT 钉到独立临时目录，
绝不让测试写到任何真实数据根。
"""
from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
import unittest

BE = os.path.dirname(os.path.abspath(__file__))
if BE not in sys.path:
    sys.path.insert(0, BE)

_ROOT = None
_FE = os.path.join(os.path.dirname(BE), "frontend", "src", "components", "live")


def setUpModule():
    global _ROOT
    # 🔴 2026-10-04 必修：旧实现只设 `DY_APP_ROOT` env，但隔离的真正入口是
    # `member_ctx.db_path()` —— 它由 `_resolve_member_id()` 从**盘上会话文件**解出
    # 真实会员 ID，完全不看 DY_APP_ROOT ⇒ 下面的 `DELETE FROM dm_messages`
    # 打的是**生产会员库**（仅按 account 过滤，仍可能删到真实消息）。
    # 用 isolate() 直接钉死 db_path，不再依赖 env。
    from test_isolation import isolate
    _ROOT, _ = isolate("d12_gate")  # 用 isolate 实际使用的根，保持 tearDown 一致


def tearDownModule():
    if _ROOT and os.path.isdir(_ROOT):
        shutil.rmtree(_ROOT, ignore_errors=True)


def _reset_db():
    try:
        import database
        database.reset_connection()
        database.get_db()
    except Exception:
        pass


class T1DeliveryTimeAnchor(unittest.TestCase):
    """G1：投递证据必须带时间锚 —— 否则历史回声帧污染全部记录。"""

    ACC = "门禁测试账号"

    def setUp(self):
        _reset_db()
        import database
        conn = database.get_db()
        conn.execute("DELETE FROM dm_messages WHERE account=?", (self.ACC,))
        conn.commit()

    def _seed(self):
        """构造：uid 历史有回声帧(ts=100)；本次发送(ts=210)之后只有平台拒收(ts=250)。"""
        import database
        conn = database.get_db()
        uid = "90000001234"
        cid = f"0:1:{uid}:888888"
        conn.execute(
            "INSERT INTO dm_messages(account,conv_id,role,text,msg_type,extra,ts,msg_id)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (self.ACC, cid, "me", "历史回声", "7", "{}", 100.0, "old_echo"))
        conn.execute(
            "INSERT INTO dm_messages(account,conv_id,role,text,msg_type,extra,ts,msg_id)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (self.ACC, cid, "them", "对方回复或关注你之前，只能发送一条文字消息", "text",
             "{}", 250.0, None))
        conn.commit()
        return uid

    def test_time_anchor_excludes_history(self):
        from services.delivery_verify import delivery_state_of
        uid = self._seed()
        # 带锚（本次发送于 ts=210）：历史回声(100)必须被排除 ⇒ 只剩本次之后的平台拒收(250)
        got = delivery_state_of(self.ACC, uid=uid, after_ts=210.0)
        self.assertEqual(got, "rejected",
                         "时间锚未生效：历史回声帧仍被判成 delivered（正是用户报的「已送达却无文案」）")

    def test_negative_control_without_anchor_is_contaminated(self):
        """负控：anchor=0 就是修复前的行为 ⇒ 必然被历史证据污染成 delivered。"""
        from services.delivery_verify import delivery_state_of
        uid = self._seed()
        got = delivery_state_of(self.ACC, uid=uid)      # 不传 after_ts = 旧行为
        self.assertEqual(got, "delivered",
                         "负控失效：不传 after_ts 时本应复现旧的污染行为；"
                         "若这里不是 delivered，说明夹具没构造出污染条件，G1 断言无判别力")

    def test_evidence_after_anchor_counts(self):
        """正控：本次发送之后到达的回声帧必须被采信。"""
        import database
        from services.delivery_verify import delivery_state_of
        uid = self._seed()
        conn = database.get_db()
        conn.execute(
            "INSERT INTO dm_messages(account,conv_id,role,text,msg_type,extra,ts,msg_id)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (self.ACC, f"0:1:{uid}:888888", "me", "本次回声", "7", "{}", 300.0,
             "new_echo"))
        conn.commit()
        self.assertEqual(delivery_state_of(self.ACC, uid=uid, after_ts=210.0),
                         "delivered")


class T2IntentScopeSSOT(unittest.TestCase):
    """G2：意向门匹配的唯一实现（引擎不含行业规则）。"""

    def test_empty_words_never_blocks(self):
        from services.ai_reply import intent_in_scope
        self.assertTrue(intent_in_scope("随便什么内容", []))
        # 空文本 + 非空词表 = 未命中 ⇒ 应拦（不是「不拦」）
        self.assertFalse(intent_in_scope("", ["工伤"]))

    def test_hit_passes_miss_blocks(self):
        from services.ai_reply import intent_in_scope
        self.assertTrue(intent_in_scope("我在工地受了工伤骨折", ["工伤", "骨折"]))
        self.assertFalse(intent_in_scope("主播晚上好", ["工伤", "骨折"]))

    def test_negative_control_empty_must_not_block(self):
        """负控：若把空词表判成「未命中」⇒ 未配置 = 全拦 ⇒ 与 G3 零回归契约相反。"""
        from services.ai_reply import intent_in_scope
        self.assertNotEqual(intent_in_scope("任何内容", []), False,
                            "空词表被当成「未命中」：未配置就会拦掉全部弹幕（零回归被破坏）")

    def test_words_normalisation(self):
        from services.ai_reply import intent_scope_words
        self.assertEqual(intent_scope_words({"intent_scope_enabled": False,
                                             "intent_scope": ["工伤"]}), [])
        self.assertEqual(intent_scope_words({"intent_scope_enabled": True,
                                             "intent_scope": ["工伤", " ", "骨折"]}),
                         ["工伤", "骨折"])
        self.assertEqual(intent_scope_words({"intent_scope_enabled": True,
                                            "intent_scope": []}), [])


class T3IntentGateDispatch(unittest.TestCase):
    """G3/G4：调度器侧 —— 未配置零回归；配置未命中即跳过且落 reason。"""

    class _Auth:
        account_name = ""       # 置空 ⇒ 走非路由分支，便于打桩

    def _dc(self):
        from core.dispatch import DispatchCenter
        return DispatchCenter(auth=self._Auth(), max_target=3, enable_send=True,
                              pick_dm_message=lambda: "词库文案")

    def test_not_configured_is_zero_regression(self):
        dc = self._dc()
        self.assertIsNone(dc.intent_verdict, "未配置意向门时必须是 None（零回归）")
        self.assertTrue(dc.submit({"user_id": "1", "nickname": "甲"}))

    def test_negative_control_default_none(self):
        """负控：若默认构造了一个判定（即使空词表），这里会不是 None。"""
        from core.dispatch import DispatchCenter
        import inspect
        sig = inspect.signature(DispatchCenter.__init__)
        self.assertIn("intent_verdict", sig.parameters)
        self.assertIsNone(sig.parameters["intent_verdict"].default,
                          "intent_verdict 默认值必须为 None（显式配置才启用）")

    def test_miss_skips_with_reason(self):
        from models.enums import RecordStatus
        dc = self._dc()
        dc.intent_verdict = lambda t: (False, "非意向内容：未命中 Agent 意向范围，未发送")
        ok = dc.submit({"user_id": "2", "nickname": "乙", "comment": "主播好"})
        self.assertFalse(ok, "意向门未命中却仍然入池")
        rec = dc.records.get("2")
        self.assertIsNotNone(rec)
        self.assertEqual(rec.status, RecordStatus.SKIPPED)
        self.assertTrue(str(rec.reason or "").strip(),
                        "跳过必须落可读 reason（否则就是「发送失败却无缘由」的同族缺陷）")

    def test_hit_passes(self):
        dc = self._dc()
        dc.intent_verdict = lambda t: (True, "")
        self.assertTrue(dc.submit({"user_id": "3", "nickname": "丙", "comment": "工伤几级"}))

    def test_gate_exception_fails_open(self):
        """判定异常必须放行（不得因门坏掉而静默不发）。"""
        dc = self._dc()

        def _boom(_t):
            raise RuntimeError("boom")

        dc.intent_verdict = _boom
        self.assertTrue(dc.submit({"user_id": "4", "nickname": "丁"}))


class T4FailedAttemptKeepsText(unittest.TestCase):
    """G5：失败时仍保留「尝试发送的文案」，前端才看得到失败上下文。"""

    class _Auth:
        account_name = ""

    def test_failed_send_keeps_attempted_content(self):
        import core.dispatch as dp
        from models.enums import RecordStatus

        async def _fake_send(_auth, _target, _content):
            return False, "测试失败原因"

        orig = dp.send_target_async
        dp.send_target_async = _fake_send
        try:
            dc = dp.DispatchCenter(auth=self._Auth(), max_target=1, enable_send=True,
                                   pick_dm_message=lambda: "要发的文案")
            target = {"user_id": "5", "nickname": "戊"}
            dc.submit(target)
            key = dc._dedup_key(target)
            asyncio.run(dc._do_send(key, target))
            rec = dc.records[key]
            self.assertEqual(rec.status, RecordStatus.FAIL)
            self.assertIsNone(rec.content, "失败时 content 应保持 None（既有语义不变）")
            self.assertEqual(rec.attempted_content, "要发的文案",
                             "失败时未保留尝试文案 ⇒ 表格里失败行只剩一个红标签（用户报障）")
        finally:
            dp.send_target_async = orig

    def test_negative_control_field_exists(self):
        from models.task import SendRecord
        self.assertIn("attempted_content", SendRecord.model_fields,
                      "SendRecord 缺 attempted_content ⇒ 前端拿不到试发文案")


def _read(p):
    with open(p, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


class T5FrontendTextContracts(unittest.TestCase):
    """G6/G7：前端两条呈现契约（源码级判据 + 负控）。

    前端无 vitest 资产，故沿用本项目既有范式（如 test_h20）以源码字符串为判据；
    每条都带「改坏即红」的负控，避免恒绿装饰。
    """

    def test_status_priority_fail_before_delivered(self):
        src = _read(os.path.join(_FE, "live-shared.tsx"))
        i_fail = src.find('if (r.dmStatus === "fail")')
        i_deliv = src.find('if (r.deliveryState === "delivered")')
        self.assertGreater(i_fail, 0, "displayStatus 缺少 fail 档判定")
        self.assertGreater(i_deliv, 0, "displayStatus 缺少 delivered 档判定")
        self.assertLess(i_fail, i_deliv,
                        "delivered 必须排在 fail 之后，否则失败行会被历史证据渲染成绿色「已送达」")

    def test_negative_control_swapped_order_is_detected(self):
        """负控：把两行对调（= 旧缺陷形态），本判据必须判红。"""
        src = _read(os.path.join(_FE, "live-shared.tsx"))
        i_fail = src.find('if (r.dmStatus === "fail")')
        i_deliv = src.find('if (r.deliveryState === "delivered")')
        # 构造旧顺序的等价物：位置互换
        mutated_fail, mutated_deliv = i_deliv, i_fail
        self.assertFalse(mutated_fail < mutated_deliv,
                         "负控失效：互换位置后判据仍为真 ⇒ 本断言没有判别力")

    def test_error_count_same_source_as_table(self):
        src = _read(os.path.join(_FE, "live-page.tsx"))
        self.assertIn('displayStatus(r)[1] === "danger"', src,
                      "「错误」计数未与表格 danger 行同源（用户报「失败没进错误统计」）")
        # 不得再用 AI worker 的 errors 当作本卡错误数
        i_counts = src.find("const aiCounts")
        self.assertGreater(i_counts, 0)
        tail = src[i_counts:i_counts + 1200]
        self.assertNotIn("aiSt.errors", tail,
                         "AI 计数行仍取 aiSt.errors（AI 会话 worker 口径），与直播发送链不通")

    def test_negative_control_ai_errors_is_flagged(self):
        """负控：把 aiSt.errors 写回计数行，本判据必须能发现。"""
        src = _read(os.path.join(_FE, "live-page.tsx"))
        i_counts = src.find("const aiCounts")
        tail = src[i_counts:i_counts + 1200]
        mutated = tail.replace("{errorCount}", "{aiSt.errors ?? 0}")
        self.assertNotEqual(mutated, tail, "负控夹具未生效：换不回旧写法")
        self.assertIn("aiSt.errors", mutated)

    def test_table_layout_fixed_both_views(self):
        """G-表格：两个视图都必须 table-fixed（用户报「表格突然变宽」）。"""
        for fn in ("live-page.tsx", "LiveReviewMode.tsx"):
            src = _read(os.path.join(_FE, fn))
            self.assertIn("table-fixed", src, f"{fn} 未使用 table-fixed ⇒ 列宽随内容漂移")


if __name__ == "__main__":
    unittest.main(verbosity=2)
