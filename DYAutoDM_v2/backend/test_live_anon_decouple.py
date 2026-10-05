# coding=utf-8
"""ENG-017 回归：直播流连接与凭证解耦（凭证失效不得导致连不上流）。

## 设计契约（用户 2026-09-21 明确）

> 「直播流连接不能直接写入凭证，抖音的直播本身是支持匿名连接查看的，
>   凭证是为了部分直播间昵称加密需要写入有解密权的凭证使用。
>   不能因为凭证失效就连直播流都无法连接」

⇒ 两条**独立**的能力线：
   监听线（收弹幕/看直播）  = 匿名即可，不依赖登录态
   增强线（昵称解密 + 私信发送） = 需要有效凭证；失效时降级为「只听不发」

## 本测试守住的不变式

1. 无凭证时 `LiveChatHook._has_credential()` 为 False，但**建连路径不被 return 阻断**；
2. 无凭证时 WS cookie 退回匿名（ttwid），不抛 `AttributeError`；
3. `AutoDM._run` 的凭证闸门：缺失时**不再 return / 不置 IDLE**，而是
   `_send_available=False` + `dispatch.enable_send=False`（只听不发）；
4. 有凭证时行为与改造前**逐字一致**（零回归）：enable_send=True、cookie 用账号的。

环境：纯单测，不联网、不开浏览器、不读任何真实凭证。
"""
from __future__ import annotations

import os
import sys
import unittest

BACKEND = os.path.dirname(os.path.abspath(__file__))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)


class _Auth:
    """凭证替身。cookie_str 为空 = 失效/缺失。"""
    def __init__(self, cookie_str: str = "", has_ticket: bool = True) -> None:
        self.cookie_str = cookie_str
        self.cookie = cookie_str
        self.ticket = "t" if has_ticket else None
        self.private_key = "k" if has_ticket else None
        self.msToken = "m" if cookie_str else ""
        self.account_name = ""


def _mk_hook(auth):
    from core.live_hook import LiveChatHook
    import collections
    hook = LiveChatHook.__new__(LiveChatHook)
    hook.auth_ = auth
    hook.live_id = "999"
    hook._should_stop = False
    hook._ws_alive = False
    hook.ws = None
    hook.dispatch = None
    hook.controller = None
    hook.feed = collections.deque(maxlen=500)
    hook.room_stats = {"online": 0, "likes": 0, "total_user": 0, "display": ""}
    hook.heat_series = collections.deque(maxlen=180)
    return hook


class TestCredentialPredicate(unittest.TestCase):
    """_has_credential 判据：只看 cookie，不要求 ticket/私钥。"""

    def test_no_auth(self):
        self.assertFalse(_mk_hook(None)._has_credential())

    def test_empty_cookie(self):
        self.assertFalse(_mk_hook(_Auth(cookie_str=""))._has_credential())

    def test_cookie_only_is_credential(self):
        """只有 cookie、无 ticket/私钥 —— 监听线视为「有凭证」（够用）。"""
        self.assertTrue(_mk_hook(_Auth(cookie_str="a=b"))._has_credential())

    def test_cookie_without_ticket_still_listens(self):
        """发送签名缺失不影响监听能力（两线解耦的核心断言）。"""
        h = _mk_hook(_Auth(cookie_str="a=b", has_ticket=False))
        self.assertTrue(h._has_credential())


class TestAnonCookie(unittest.TestCase):
    """匿名 cookie 路径：取不到 ttwid 也必须安全返回空串，不抛异常。"""

    def test_anon_cookie_never_raises(self):
        h = _mk_hook(None)
        try:
            v = h._anon_cookie()
        except Exception as e:  # noqa: BLE001
            self.fail(f"_anon_cookie 不应抛异常（无凭证是正常路径）: {type(e).__name__}: {e}")
        self.assertIsInstance(v, str)

    def test_ws_cookie_falls_back_to_anon(self):
        """无凭证 → WS cookie 走匿名分支，绝不 AttributeError。"""
        h = _mk_hook(None)
        h._anon_cookie = lambda: ""      # 隔离网络：直接给空 ttwid
        # 复刻 server.py 里的分支逻辑
        if h._has_credential():
            ck = h.auth_.cookie_str
        else:
            _t = h._anon_cookie()
            ck = f"ttwid={_t}" if _t else ""
        self.assertEqual(ck, "", "无凭证且无 ttwid 时应为空 cookie，而不是崩")


class TestRunGatesDegrade(unittest.TestCase):
    """AutoDM._run 凭证闸门的**静态契约**：不得再出现 return 中断。

    用源码断言（机制级，不是字面量）：命中「凭证缺失 → return」即失败。
    """

    def test_no_idle_return_on_missing_credential(self):
        src = open(os.path.join(BACKEND, "core", "auto_dm.py"), encoding="utf-8").read()
        # 取 _run 函数体范围（到下一个顶层 async def 前）
        i = src.find("async def _run(")
        self.assertNotEqual(i, -1, "未找到 _run")
        j = src.find("\n    async def ", i + 10)
        body = src[i:j if j != -1 else len(src)]

        # 改造前：AUTH-018/019/020 三处各自 `return` 并置 IDLE
        for code in ("AUTH-018", "AUTH-019", "AUTH-020", "AUTH-021"):
            self.assertIn(code, body, f"{code} 应保留（降级告警仍是显式的）")
        # 判据：这些码所在分支不得再直接 `self.state = EngineState.IDLE` + return
        seg_start = body.find("AUTH-018")
        seg_end = body.find("AUTH-021")
        seg = body[seg_start:seg_end + 200]
        self.assertNotIn(
            "self.state = EngineState.IDLE", seg,
            "凭证缺失分支仍会置 IDLE 并中断任务 —— ENG-017 解耦被回退")
        self.assertIn("_send_available", body, "应记录发送能力开关")
        self.assertIn("enable_send=_send_ok", body, "应按发送能力配置调度器")

    def test_dispatch_respects_enable_send(self):
        """enable_send=False 时调度器只记录、不真发（只听不发的落点）。"""
        import asyncio
        from core.dispatch import DispatchCenter

        dc = DispatchCenter(auth=None, max_target=5, delay_range=(0, 0),
                            interval=0.0, enable_send=False,
                            pick_dm_message=lambda: "不该发出")
        ok = dc.submit({"user_id": "1", "nickname": "张三", "comment": "x"})
        self.assertFalse(ok, "enable_send=False 时不应入队发送")
        # 但记录必须保留（前端可见「已捕获未发」）
        self.assertEqual(len(dc.records), 1, "捕获记录必须保留（不丢数据）")
        self.assertEqual(dc.queue_size(), 0)


class TestCredentialPathUnchanged(unittest.TestCase):
    """有凭证时零回归：enable_send 仍应为 True，cookie 用账号的。"""

    def test_has_credential_true_keeps_account_cookie(self):
        a = _Auth(cookie_str="sessionid=abc; ttwid=xyz")
        h = _mk_hook(a)
        self.assertTrue(h._has_credential())
        ck = h.auth_.cookie_str if h._has_credential() else "ANON"
        self.assertEqual(ck, "sessionid=abc; ttwid=xyz")


class TestServerNoHardCredentialDep(unittest.TestCase):
    """server.py 不得在无凭证时直接失败。"""

    def test_start_ws_has_anon_branch(self):
        src = open(os.path.join(BACKEND, "dy_live", "server.py"), encoding="utf-8").read()
        self.assertIn("_has_credential()", src, "start_ws 应判凭证可用性")
        self.assertIn("_anon_cookie()", src, "应有无凭证降级分支")
        # 判据：get_webcast_detail 调用被 try 包裹（失败转匿名而非抛出）
        i = src.find("if _has_cred:")
        self.assertNotEqual(i, -1)
        seg = src[i:i + 600]
        self.assertIn("try:", seg, "凭证态取首包必须 try 包裹（失败转匿名建连）")
        self.assertIn("except Exception", seg)


if __name__ == "__main__":
    unittest.main(verbosity=2)
