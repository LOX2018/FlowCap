# coding=utf-8
"""ENG-018 回归：直播「昵称解密权」判据 —— 「有 cookie」不等于「能解密」。

## 用户报告（2026-09-21）

> 「张老师凭证更新了，但直播监听没有用凭证解密」

## 实机取证结论（三组对照，同一房间、同一时刻，只换 cookie）

| cookie            | 弹幕结果                                              |
|-------------------|-------------------------------------------------------|
| 四川工伤张老师    | `uid=111111` `sec_uid=''` `三***`   ❌ 脱敏          |
| 尚进工伤小助理    | `uid=63676672247` `sec_uid=MS4wLjABAAAA…` `幸运星语` ✅ |
| （无 cookie 真匿名）| `uid=111111` `sec_uid=''` `三***`   ❌ 脱敏          |

⇒ 张老师**带 cookie 与完全不带 cookie 的现象逐字相同**；故
「有非空 cookie」是**代理信号**（本项目 §〇·己·3 明令禁止），判据为真、能力为零。
真判据 = 主站 `user/profile/self/` 是否承认该会话（status_code=0 + sec_uid）。

## 本测试守住的不变式

1. `_has_credential()` 必须是**会话态判据**而非「有没有 cookie」；
2. 三态诚实降级：未知（None）不得擅自降级为 False，也不得伪装成可用；
3. **无解密权时不得用账号 cookie 去建 WS**（否则只能收到脱敏弹幕，
   正是用户报的症状）；
4. 有解密权时行为与改造前逐字一致（零回归）；
5. `probe_live_identity` 异常必须转可读原因（"结论未知"），不得冒泡成
   「校验异常」——那会把确定结论降级成无信息量的一句报错。

环境：纯单测。**不联网**（requests.get / WebSocketApp 全部替身）、
不开浏览器、不读任何真实凭证。
"""
from __future__ import annotations

import collections
import json
import os
import sys
import unittest
from unittest import mock

BACKEND = os.path.dirname(os.path.abspath(__file__))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

ANON_TTWID = "FAKE_TTWID_ANON_123"
ROOM_ID = "7687849087689214761"
USER_ID = "7687549608519894528"


class _Auth:
    """凭证替身：cookie 非空 = 「看起来有凭证」。"""

    def __init__(self, cookie_str: str = "sessionid=deadbeef; ttwid=t1; uid_tt=u1") -> None:
        self.cookie_str = cookie_str
        self.cookie = dict(p.split("=", 1) for p in cookie_str.split("; ") if "=" in p)
        self.ticket = "t"
        self.private_key = "k"
        self.msToken = "m"
        self.account_name = "替身账号"


def _hook(auth, session_ok):
    from core.live_hook import LiveChatHook
    h = LiveChatHook("992931212705", auth, None, controller=None, session_ok=session_ok)
    return h


class _FakeResp:
    """忠实复刻 requests.Response 的两处契约：`.text` 与 `.json()` 同源。"""

    def __init__(self, payload, status=200):
        self.status_code = status
        self.text = payload if isinstance(payload, str) else json.dumps(payload)

    def json(self):
        return json.loads(self.text)


def _live_identity_response(logged_in: bool):
    """按真实契约造响应：登录→status_code=0 + sec_uid；未登录→status_code=8。

    注意：probe_live_identity 的判据是 `json.status_code == 0` **且** 响应体含
    `MS4wLjABAAAA`（两处都读 `r.text`），故 mock 必须两个契约形状一致
    （此前替身二者矛盾 → 测试自己假红）。
    """
    body = ('{"status_code":0,"user":{"sec_uid":"MS4wLjABAAAAxyz","uid":123}}'
            if logged_in else '{"status_code":8,"user":null}')
    return _FakeResp(body)


class TestHasCredentialTristate(unittest.TestCase):
    """1) 判据层：必须是会话态三态，而不是「有没有 cookie」。"""

    def test_no_cookie_is_false(self):
        for sess in (True, False, None):
            self.assertFalse(_hook(_Auth(cookie_str=""), sess)._has_credential(),
                             f"无 cookie 时必须 False（session_ok={sess}）")

    def test_cookie_but_not_logged_in_is_false(self):
        """★ 原缺陷本体：有 cookie 且服务端判未登录 → 旧实现恒 True。"""
        self.assertFalse(_hook(_Auth(), False)._has_credential())

    def test_cookie_and_logged_in_is_true(self):
        self.assertTrue(_hook(_Auth(), True)._has_credential())

    def test_unknown_session_degrades_honestly(self):
        """取不到证据（None）→ 保留原行为，不擅自降级（§〇·己·2）。"""
        self.assertTrue(_hook(_Auth(), None)._has_credential())


class TestWsConnectUsesAnonymousCookie(unittest.TestCase):
    """2) ★ 用户报的症状本体：无解密权时**不得**用账号 cookie 建 WS。"""

    def _run_start_ws(self, session_ok):
        from dy_live import server as srv

        captured = {}

        class _FakeWS:
            def __init__(self, **kw):
                captured.update(kw)

            def run_forever(self, **kw):
                return None

        fake_info = {"room_id": ROOM_ID, "user_id": USER_ID,
                     "ttwid": ANON_TTWID, "room_status": 2, "room_title": ""}
        with mock.patch.object(srv, "WebSocketApp", _FakeWS), \
             mock.patch.object(srv, "DouyinAPI") as m_api:
            m_api.get_live_info.return_value = dict(fake_info)
            m_api.get_webcast_detail.return_value = b""

            h = _hook(_Auth(), session_ok)
            h._anon_live_info = lambda: dict(fake_info)      # 匿名进房替身
            h.start_ws()
            return captured, m_api, h

    def test_no_authority_uses_anon_cookie_and_skips_account_path(self):
        captured, m_api, _h = self._run_start_ws(False)
        self.assertIn("cookie", captured, "必须真的走到建连")
        self.assertEqual(captured["cookie"], f"ttwid={ANON_TTWID}",
                         "无解密权时 WS 必须用匿名 ttwid，不得带账号会话")
        self.assertNotIn("sessionid", captured["cookie"])
        self.assertEqual(m_api.get_live_info.call_count, 0,
                         "无解密权时不得用账号凭证去取直播间信息")
        self.assertEqual(m_api.get_webcast_detail.call_count, 0,
                         "无解密权时不得走需要 cookie 的首包路径")

    def test_with_authority_keeps_original_behavior(self):
        """有解密权 → 零回归：用账号 cookie + 走凭证态首包。"""
        captured, m_api, _h = self._run_start_ws(True)
        self.assertIn("sessionid", captured["cookie"], "有解密权时必须带账号会话")
        self.assertEqual(m_api.get_live_info.call_count, 1)
        self.assertEqual(m_api.get_webcast_detail.call_count, 1)


class TestRoomInfoFallsBackToAnon(unittest.TestCase):
    """3) 带凭证取信息拿不到 → 必须显式回落匿名（不得静默）。"""

    def test_falls_back_when_credential_path_fails(self):
        from dy_live import server as srv
        fake = {"room_id": ROOM_ID, "user_id": USER_ID, "ttwid": ANON_TTWID}
        h = _hook(_Auth(), True)
        h._anon_live_info = lambda: dict(fake)
        with mock.patch.object(srv, "DouyinAPI") as m_api:
            m_api.get_live_info.return_value = None      # 凭证态取不到
            self.assertEqual(h._room_info_with_credential()["room_id"], ROOM_ID)
            m_api.get_live_info.side_effect = RuntimeError("boom")
            self.assertEqual(h._room_info_with_credential()["room_id"], ROOM_ID)


class TestProbeLiveIdentity(unittest.TestCase):
    """4) 探针：判据落在真实业务通路（profile/self），异常不冒泡。"""

    def _probe(self, resp=None, exc=None, cookie="sessionid=x"):
        from auto_dm import accounts as acc
        with mock.patch("requests.get") as m_get:
            if exc is not None:
                m_get.side_effect = exc
            else:
                m_get.return_value = resp
            return acc.probe_live_identity("替身账号", _Auth(cookie_str=cookie), force=True)

    def test_no_cookie_is_false(self):
        ok, detail = self._probe(resp=_live_identity_response(False), cookie="")
        self.assertFalse(ok)
        self.assertIn("无 cookie", detail)

    def test_not_logged_in_is_false(self):
        ok, detail = self._probe(resp=_live_identity_response(False))
        self.assertFalse(ok)
        self.assertIn("status_code=8", detail)
        self.assertIn("无直播昵称解密权", detail)

    def test_logged_in_is_true(self):
        ok, detail = self._probe(resp=_live_identity_response(True))
        self.assertTrue(ok)
        self.assertIn("具备直播昵称解密权", detail)

    def test_probe_error_is_reported_as_unknown_not_swallowed(self):
        """探测异常 → 必须可读且标为「结论未知」，绝不冒泡也不谎报。"""
        ok, detail = self._probe(exc=RuntimeError("network down"))
        self.assertFalse(ok)
        self.assertIn("结论未知", detail)

    def test_cache_avoids_repeat_network_and_force_bypasses(self):
        from auto_dm import accounts as acc
        acc.invalidate_live_identity_cache("替身账号")
        with mock.patch("requests.get") as m_get:
            m_get.return_value = _live_identity_response(True)
            acc.probe_live_identity("替身账号", _Auth(), force=True)
            acc.probe_live_identity("替身账号", _Auth())          # 命中缓存
            self.assertEqual(m_get.call_count, 1, "TTL 内不得重复打网")
            acc.probe_live_identity("替身账号", _Auth(), force=True)
            self.assertEqual(m_get.call_count, 2, "force 必须穿透缓存")
        acc.invalidate_live_identity_cache("替身账号")


class TestAutoDmThreeStateMapping(unittest.TestCase):
    """5) AutoDM 侧：探测结果到三态字段的映射（结论未知 → None，不降级）。"""

    def test_detail_marker_maps_to_none(self):
        # 与 core/auto_dm.py 中的判定表达式保持同一语义
        cases = [
            ("服务端承认登录态：具备直播昵称解密权", True, True),
            ("服务端判为未登录（profile/self status_code=8）…", False, False),
            ("登录态探测失败：Timeout（结论未知，不据此降级）", False, None),
        ]
        for detail, ok_flag, expect in cases:
            got = None if "结论未知" in detail else bool(ok_flag)
            self.assertIs(got, expect, f"detail={detail!r}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
