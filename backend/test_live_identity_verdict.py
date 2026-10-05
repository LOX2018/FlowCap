# coding=utf-8
"""H-3 / ENG-018 返工回归：直播「昵称解密权」判据必须**合取两侧**。

## 用户需求（2026-09-22 交接卡台账 H-3）

> 现用 `probe_live_identity`（`profile/self`）**不应单独决定降级**；
> 应改以 `_uid_consistent_with_history`（探活 uid 与历史 conv_id 一致性）为主，
> `profile/self` 降为辅助。
> 完成标准：已知故障样本（uid ≠ conv_id）→ 判「无解密权」；
> **已知健康样本（一致）→ 判「有解密权」**（两侧都验）；
> 守卫测试绿 + 回退变红；同步改 `LIVE-035` 六段契约措辞。

## 实现取舍（必须显式记录，见 `accounts.uid_identity_verdict` docstring）

台账措辞是「以 A 为主、B 为辅助」，但 `accounts.live_session_state()` 的 docstring
自证边界：**会话活性** 与 **身份漂移** 是两类**不同**失效，结论**互不代替**。
⇒ 实现按 **合取（AND）** 落地，而不是「主/辅替代」：
   有解密权 ⟺ ① 会话被服务端承认 **且** ② 身份未漂移
   任一侧「已确认失败」→ 无解密权（False）；任一侧「取不到证据」→ None（诚实降级）。
理由：若按「替代」实现，会把「身份漂移」的账号判成有解密权 —— 正是本缺陷本体。

## 本测试守住的不变式

1. **故障侧**：uid ≠ conv_id（AUTH-050）→ 必须判 False/uid_drift（哪怕会话被承认）；
2. **健康侧**：uid 一致 + 会话被承认 → 必须判 True/ok（两侧都验）；
3. 会话侧确认被拒 → False/not_logged_in（成因可区分于身份漂移）；
4. 任一侧取不到证据 → None/unknown（**不得**降级成 False，也不得伪装成 True）；
5. `uid_probe` 的三态出口：AUTH-050 必须留下 **False**（而非 None）的结论，
   否则消费方无法把「身份漂移」与「探活失败」分开；
6. `probe_live_identity` 既有二元契约零回归（`True` → `bool(state)`）。

环境：纯单测。**不联网、不开浏览器、不读真实凭证、不写真实 DB。**
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

BACKEND = os.path.dirname(os.path.abspath(__file__))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)


class _Auth:
    """凭证替身：cookie 非空 = 「看起来有凭证」。"""

    def __init__(self, cookie_str: str = "sessionid=deadbeef; ttwid=t1; uid_tt=u1") -> None:
        self.cookie_str = cookie_str
        self.cookie = dict(p.split("=", 1) for p in cookie_str.split("; ") if "=" in p)
        self.ticket = "t"
        self.private_key = "k"
        self.account_name = "替身账号"


def _verdict(sess, ident):
    """在替身下求 `uid_identity_verdict`：会话侧与身份侧都由 mock 喂入。"""
    from auto_dm import accounts as acc

    with mock.patch.object(acc, "live_session_state", return_value=sess), \
         mock.patch("services.uid_probe.uid_verdict", return_value=ident):
        return acc.uid_identity_verdict("替身账号", _Auth(), force=True)


OK_SESS = (True, "服务端承认登录态：具备直播昵称解密权")
LOGIN_REQUIRED = (False, "服务端判为未登录（profile/self status_code=8）—— "
                         "无直播昵称解密权，弹幕昵称将被脱敏（uid=111111）。请重新扫码")
NO_COOKIE = (False, "无 cookie —— 无解密权（只能收到匿名脱敏弹幕）")
SESS_UNKNOWN = (None, "登录态探测失败：Timeout（结论未知，不据此降级）")

OK_ID = (True, "探活 uid=1 与历史 conv_id 一致")
DRIFT_ID = (False, "AUTH-050 身份漂移：探活 uid=3119773958541104 不在历史 conv_id 中")
ID_UNKNOWN = (None, "本进程尚未对该账号做过 uid 判定（取不到证据）")


class TestConjunctionTruthTable(unittest.TestCase):
    """判据本体：合取真值表（会话活性 × 身份一致性）。"""

    # ---- ① 故障侧（台账要求「已知故障样本 → 无解密权」）----

    def test_uid_drift_alone_is_no_authority(self):
        """★ 本缺陷本体：会话被承认 **但** 身份漂移 → 仍无解密权。"""
        state, reason, label, detail = _verdict(OK_SESS, DRIFT_ID)
        self.assertIs(state, False)
        self.assertEqual(reason, "uid_drift")
        self.assertIn("身份漂移", label)
        self.assertIn("AUTH-050", detail)

    def test_uid_drift_beats_healthy_session(self):
        """若按「替代」实现，这一条会假绿成 True —— 故单列一条守住它。"""
        state, reason, _, _ = _verdict(OK_SESS, DRIFT_ID)
        self.assertIsNot(state, True, "身份漂移绝不可被判为有解密权")

    def test_not_logged_in_alone_is_no_authority(self):
        state, reason, label, detail = _verdict(LOGIN_REQUIRED, OK_ID)
        self.assertIs(state, False)
        self.assertEqual(reason, "not_logged_in")
        self.assertIn("status_code=8", detail)

    def test_no_cookie_is_reported_distinctly(self):
        state, reason, _, detail = _verdict(NO_COOKIE, OK_ID)
        self.assertIs(state, False)
        self.assertEqual(reason, "no_credential")
        self.assertIn("无 cookie", detail)

    def test_both_failed_prefers_session_reason(self):
        """两侧都确认失败：会话侧先判（成因更靠前），仍是无解密权。"""
        state, reason, _, _ = _verdict(LOGIN_REQUIRED, DRIFT_ID)
        self.assertIs(state, False)
        self.assertEqual(reason, "not_logged_in")

    # ---- ② 健康侧（台账要求「已知健康样本 → 有解密权」）----

    def test_healthy_sample_has_authority(self):
        """★ 两侧都成立才判有解密权。"""
        state, reason, label, detail = _verdict(OK_SESS, OK_ID)
        self.assertIs(state, True)
        self.assertEqual(reason, "ok")
        self.assertIn("具备直播昵称解密权", label)
        self.assertIn("会话活性", detail)
        self.assertIn("身份一致性", detail)

    # ---- ③ 诚实三态：取不到证据不得降级 ----

    def test_identity_unknown_degrades_to_none(self):
        state, reason, _, detail = _verdict(OK_SESS, ID_UNKNOWN)
        self.assertIsNone(state, "身份侧无证据时不得判有解密权（诚实三态）")
        self.assertEqual(reason, "unknown")
        self.assertIn("身份侧", detail)

    def test_both_unknown_is_none(self):
        state, reason, _, _ = _verdict(SESS_UNKNOWN, ID_UNKNOWN)
        self.assertIsNone(state)
        self.assertEqual(reason, "unknown")

    def test_session_unknown_but_identity_ok_is_none(self):
        state, reason, _, _ = _verdict(SESS_UNKNOWN, OK_ID)
        self.assertIsNone(state, "会话侧无证据时不得判有解密权")
        self.assertEqual(reason, "unknown")

    def test_none_is_not_false(self):
        """None 与 False 语义相反：不得被调用方折叠（§〇·己·2）。"""
        for sess, ident in ((SESS_UNKNOWN, ID_UNKNOWN), (OK_SESS, ID_UNKNOWN)):
            state, _, _, _ = _verdict(sess, ident)
            self.assertIsNone(state)
            self.assertIsNot(state, False)


class TestUidProbeVerdictTristate(unittest.TestCase):
    """`services.uid_probe` 的三态出口：身份漂移必须留 **False**，不是 None。"""

    def setUp(self):
        from services import uid_probe as up
        self.up = up
        up.invalidate_verdict("替身账号")
        up.invalidate("替身账号")

    def tearDown(self):
        self.up.invalidate_verdict("替身账号")
        self.up.invalidate("替身账号")

    def test_missing_record_is_none(self):
        state, detail = self.up.uid_verdict("替身账号")
        self.assertIsNone(state)
        self.assertIn("取不到证据", detail)

    def test_drift_records_false_not_none(self):
        """★ 关键：AUTH-050 分支必须写 False —— 否则消费方无法区分漂移与失败。"""
        self.up._record_verdict("替身账号", False, "AUTH-050 身份漂移")
        state, detail = self.up.uid_verdict("替身账号")
        self.assertIs(state, False)
        self.assertIn("AUTH-050", detail)

    def test_ok_records_true(self):
        self.up._record_verdict("替身账号", True, "一致")
        state, _ = self.up.uid_verdict("替身账号")
        self.assertIs(state, True)

    def test_ttl_expiry_degrades_to_none(self):
        self.up._record_verdict("替身账号", False, "AUTH-050 身份漂移")
        state, detail = self.up.uid_verdict("替身账号", ttl=-1)
        self.assertIsNone(state, "过期结论必须降级为 None（取不到证据）")
        self.assertIn("过期", detail)

    def test_get_uid_writes_verdict_on_drift(self):
        """端到端（mock 网络）：get_uid 探到漂移 uid → 缓存不写、三态记 False。"""
        with mock.patch.object(self.up, "_do_probe", return_value=3119773958541104), \
             mock.patch.object(self.up, "_uid_consistent_with_history", return_value=False):
            got = self.up.get_uid("替身账号")
        self.assertIsNone(got, "漂移 uid 不得返回（也不得进缓存）")
        state, detail = self.up.uid_verdict("替身账号")
        self.assertIs(state, False)
        self.assertIn("AUTH-050", detail)

    def test_get_uid_writes_verdict_on_success(self):
        with mock.patch.object(self.up, "_do_probe", return_value=63676672247), \
             mock.patch.object(self.up, "_uid_consistent_with_history", return_value=True):
            got = self.up.get_uid("替身账号")
        self.assertEqual(got, 63676672247)
        state, _ = self.up.uid_verdict("替身账号")
        self.assertIs(state, True)

    def test_get_uid_probe_failure_is_none_not_false(self):
        with mock.patch.object(self.up, "_do_probe", return_value=None):
            got = self.up.get_uid("替身账号")
        self.assertIsNone(got)
        state, detail = self.up.uid_verdict("替身账号")
        self.assertIsNone(state, "探活失败是「取不到证据」，不得记成 False")
        self.assertIn("AUTH-051", detail)


class TestLegacyBinaryContractZeroRegression(unittest.TestCase):
    """`probe_live_identity` 既有二元契约：True → bool(state)，行为逐字不变。"""

    def test_true_maps_to_true(self):
        from auto_dm import accounts as acc
        with mock.patch.object(acc, "live_session_state",
                               return_value=(True, "服务端承认登录态：具备直播昵称解密权")):
            ok, detail = acc.probe_live_identity("替身账号", _Auth(), force=True)
        self.assertIs(ok, True)
        self.assertIn("具备直播昵称解密权", detail)

    def test_unknown_maps_to_false_with_marker(self):
        """既有契约里 None 折成 False（由 detail 的「结论未知」区分）—— 保持不变。"""
        from auto_dm import accounts as acc
        with mock.patch.object(acc, "live_session_state",
                               return_value=(None, "登录态探测失败（结论未知，不据此降级）")):
            ok, detail = acc.probe_live_identity("替身账号", _Auth(), force=True)
        self.assertIs(ok, False)
        self.assertIn("结论未知", detail)


if __name__ == "__main__":
    unittest.main(verbosity=2)
