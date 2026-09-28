# -*- coding: utf-8 -*-
"""发送侧回归：结构化失败类型 + 冷静期判定 + 分钟级限流（2026-09-28）。

设计契约：
  A. 失败类型是**枚举**（services.send_response.KIND_*），全链路透传；
     频控/风控类失败必须触发冷静期（改前只认关键字，KICK 类漏判）。
  B. 账号级**每分钟全量发送**上限（含 AI/直播首发）；手动发送豁免但计入。

判据（负控必须变红）：
  1. failure_kind：KICK/INVALID_REQUEST → risk_control；频繁/频控 → rate_limited；
     8610 → safety_blocked；10502 → under_review；互关 → need_follow。
  2. kind_is_cooldown：仅 rate_limited / risk_control 为 True。
  3. AccountQuota.can_send(per_minute=3)：第 4 条被拒、无 per_minute 时不拒。
  4. on_result(ok=False, kind=risk_control) → cooldown_until>0（强制冷静）；
     旧路径 on_result(ok=False, detail="KICK") 也必须冷静（兼容兜底）。
"""
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("DY_APP_ROOT", tempfile.mkdtemp(prefix="dy_sendcheck_"))

from services import send_response as SR          # noqa: E402
from services import dm_dispatch as DD            # noqa: E402


class TestFailureKind(unittest.TestCase):
    def test_risk_control_kinds(self):
        # 负控核心：改前 KICK/风控文案不含关键字 ⇒ 不冷静。现在必须归到 risk_control
        self.assertEqual(SR.failure_kind({"decision": "KICK"}), SR.KIND_RISK_CONTROL)
        self.assertEqual(SR.failure_kind({"message": "INVALID_REQUEST"}), SR.KIND_RISK_CONTROL)
        self.assertEqual(SR.failure_kind({"error_desc": "账号安全风险"}), SR.KIND_RISK_CONTROL)

    def test_rate_limited_kinds(self):
        self.assertEqual(SR.failure_kind({"message": "FREQUENT"}), SR.KIND_RATE_LIMITED)
        self.assertEqual(SR.failure_kind({"message": "TOO_FREQUENT"}), SR.KIND_RATE_LIMITED)
        self.assertEqual(SR.failure_kind({"error_desc": "发送过于频繁"}), SR.KIND_RATE_LIMITED)

    def test_verdict_driven(self):
        # 8610 → safety_blocked（不冷静，非频控）
        v = {"state": "blocked", "check_code": 8610, "delivered": False,
             "server_message_id": ""}
        self.assertEqual(SR.failure_kind(None, v), SR.KIND_SAFETY_BLOCKED)
        # 10502 → under_review
        v2 = {"state": "review", "check_code": 10502, "delivered": False}
        self.assertEqual(SR.failure_kind(None, v2), SR.KIND_UNDER_REVIEW)
        # delivered
        v3 = {"state": "delivered", "delivered": True, "server_message_id": "123"}
        self.assertEqual(SR.failure_kind(None, v3), SR.KIND_DELIVERED)

    def test_other_kinds(self):
        self.assertEqual(SR.failure_kind({"message": "NEED_FOLLOW"}), SR.KIND_NEED_FOLLOW)
        self.assertEqual(SR.failure_kind({"message": "PRIVACY"}), SR.KIND_PRIVACY)
        self.assertEqual(SR.failure_kind({"message": "USER_NOT_EXIST"}), SR.KIND_USER_GONE)
        self.assertEqual(SR.failure_kind({}, None, http_ok=False), SR.KIND_HTTP_ERROR)

    def test_cooldown_set(self):
        self.assertTrue(SR.kind_is_cooldown(SR.KIND_RATE_LIMITED))
        self.assertTrue(SR.kind_is_cooldown(SR.KIND_RISK_CONTROL))
        self.assertFalse(SR.kind_is_cooldown(SR.KIND_SAFETY_BLOCKED))
        self.assertFalse(SR.kind_is_cooldown(SR.KIND_UNDER_REVIEW))
        self.assertFalse(SR.kind_is_cooldown(SR.KIND_DELIVERED))


class TestMinuteWindow(unittest.TestCase):
    def test_per_minute_blocks_4th(self):
        q = DD.AccountQuota("acc_min")
        for i in range(3):
            ok, why, w = q.can_send(0.0, 3)
            self.assertTrue(ok, f"第 {i+1} 条应放行: {why}")
            q.note_sent()
        ok, why, w = q.can_send(0.0, 3)
        self.assertFalse(ok, "第 4 条必须被分钟窗拦下")
        self.assertGreaterEqual(w, 0.0)

    def test_no_limit_zero_regression(self):
        q = DD.AccountQuota("acc_zero")
        for i in range(10):
            ok, why, w = q.can_send(0.0, 0)   # 0 = 不启用
            self.assertTrue(ok, f"per_minute=0 不应拦: {why}")
            q.note_sent()

    def test_cooldown_blocks_all(self):
        q = DD.AccountQuota("acc_cool")
        q.cooldown_until = time.time() + 300
        ok, why, w = q.can_send(0.0, 0)
        self.assertFalse(ok)
        self.assertIn("冷静期", why)


class TestCooldownTrigger(unittest.TestCase):
    def test_kind_triggers_cooldown(self):
        q = DD.AccountQuota("acc_kind")
        q.on_result(False, "", SR.KIND_RISK_CONTROL)
        self.assertGreater(q.cooldown_until, time.time(),
                           "kind=risk_control 必须触发冷静期")
        # 注意：cooldown 分支内部会调 _weight_unlocked() → _decay_freq()，
        # 对 freq_hits_f 做一次轻微衰减，故断言 > 0.5（而非 >= 1.0）。
        self.assertGreater(q.freq_hits_f, 0.5)

    def test_legacy_kick_detail_triggers(self):
        # 兼容兜底：无 kind、detail 含 KICK 也要冷静（改前会漏）
        q = DD.AccountQuota("acc_legacy")
        q.on_result(False, "抖音拒绝发送：KICK（风控/限流）")
        self.assertGreater(q.cooldown_until, time.time())

    def test_safety_blocked_does_not_cool(self):
        q = DD.AccountQuota("acc_safe")
        q.on_result(False, "", SR.KIND_SAFETY_BLOCKED)
        self.assertEqual(q.cooldown_until, 0.0, "内容安全拦截不应触发冷静期")


class TestManualExemption(unittest.TestCase):
    """手动发送豁免：源码契约（source=='manual' 时不传 min_interval/per_minute）。"""

    def test_source_manual_recognized(self):
        # 判据：_PRIO_BY_SOURCE 认识 manual，且优先级最高
        self.assertEqual(DD._PRIO_BY_SOURCE.get("manual"), DD.PRIO_MANUAL)
        self.assertLess(DD.PRIO_MANUAL, DD.PRIO_AI)


if __name__ == "__main__":
    unittest.main(verbosity=2)
