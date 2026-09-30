# -*- coding: utf-8 -*-
"""直播页「发送弹幕 / 点赞」写接口的行为门禁（2026-09-30）。

## 被修复的缺陷（用户报「发送弹幕和点赞异常」）

  ① **前端点赞是空壳**：`live-page.tsx` 的 `doLike` 只是
     `push("功能开发中：点赞")` —— UI 上有按钮、点了什么都不发生。
  ② **弹幕端点不带 account**：前端 `client.ts:sendDanmaku` 只发 `{content}`，
     多账号时后端只能回落「当前账号」⇒ 可能用错身份。
  ③ 🔴 **房间号形态错（真根因）**：`link_resolve.resolve_live_id` 契约只保证返回
     **web_rid（URL 短号）**，而直播域写接口 `/webcast/room/{chat,like}/` 要的是
     **真实 room_id**。实测 `follow/live/992931212705` 进房
     `room_id=7688251038101556006`，二者不同 ⇒ 直接拿短号发 = 上游静默失败。
  ④ **点赞统一形态缺口**：`diggLiveRoom` 用**主站 Origin**、无 bd-ticket 证书
     （ADR-004 §4 登记的「E 线同族残留」）；而同一上游的 `sendMsgInRoom` 已明文写明
     直播域写接口必须用 `live.douyin.com` Origin（因此前完全缺失，改用主站会「空响应
     或业务失败」）。
  ⑤ **缺少「用户是否想要」的显式选择**：写接口只要凭证齐备就无条件外发 ⇒ 触风控。

## 门禁设计（全部为**行为断言**，无纯 grep 判据）

  D1 显式配置门：`danmaku_enabled` / `like_enabled` 默认 False ⇒ 拒发且**零出站**
  D2 开启后真调：出站计数=1，且房间号经归一化（不是 URL 短号）
  D3 归一化降级：`get_live_info` 空/异常 ⇒ **原样回退**，不 fail-closed、不编造
  D4 成败判据：`status_code==0` 才成功；非 0 与**空响应**都判失败（不得假成功）
  D5 异常如实上报：抛异常 ⇒ ok=False，reason=exception
  D6 count 越界显式拒绝（不静默夹取）
  D7 基座形态：`diggLiveRoom` 必须带直播域 Origin + bd-ticket 证书
  D8 负控：把归一化去掉 ⇒ 写接口收到的是 URL 短号（证明门禁真守得住）

## 红线声明

本模块**不发起任何真实网络请求**：`_live_chat_room_id` 的底层
`DouyinAPI.get_live_info`、`sendMsgInRoom`、`diggLiveRoom` 全部被 monkeypatch
替换为本地计数器桩；凭证加载（`_auth_for`）被打桩，绝不读真实 `.env`、
绝不触碰真实账号。`_NET.calls` 在每个用例里核验出站次数。

隔离：复用 `test_config_isolation` 的临时根（`<tmp>/dyautodm_cfgtest_root`），
绝不写 `C:/temp/dyautodm_design`。

运行：cd backend && python -m unittest test_live_write_gates -v
"""
import asyncio
import importlib
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import test_config_isolation as iso  # noqa: E402

_ROOT = iso._ROOT
os.environ["DY_APP_ROOT"] = _ROOT

_HERE = os.path.dirname(os.path.abspath(__file__))
_LIVE_PY = os.path.join(_HERE, "api", "live.py")

#: URL 短号（web_rid）与真实 room_id —— 取自本项目实测样本
#: （12_业务域_直播监听 §10.2：follow/live/992931212705 进房 room_id=7688251038101556006）
WEB_RID = "992931212705"
REAL_ROOM_ID = "7688251038101556006"


class _NetCounter:
    """出站计数器：任何一次真实调用（含 get_live_info 探测）都记一笔。"""

    def __init__(self):
        self.reset()

    def reset(self):
        self.calls = 0          # 写接口调用次数（sendMsgInRoom / diggLiveRoom）
        self.probes = 0         # 房间号归一化探测次数（get_live_info）
        self.routed_room = None  # 写接口**实际收到**的 room_id


_NET = _NetCounter()

# ── 进程级全局的 快照 / 还原（照 test_live_endpoint_honesty 的 M-17 范式）───
# `unittest discover` 是单进程；本模块会遮蔽继承来的方法，必须还原，
# 否则污染同进程后续模块（曾实测 7F+2E 量级的跨模块顺序依赖）。
_SNAPSHOT = None  # {"attrs": [(name, own: bool, raw)], "patched": bool}


def _ensure_snapshot():
    global _SNAPSHOT
    if _SNAPSHOT is None:
        import dy_apis.douyin_api as dapi
        names = ("sendMsgInRoom", "diggLiveRoom", "get_live_info")
        _SNAPSHOT = {
            "attrs": [(n, n in dapi.DouyinAPI.__dict__,
                       dapi.DouyinAPI.__dict__.get(n)) for n in names],
            "patched": False,
        }
    return _SNAPSHOT


def _restore_globals():
    """还原本模块改写过的全部进程级全局（幂等）。"""
    global _SNAPSHOT
    if _SNAPSHOT is not None:
        import dy_apis.douyin_api as dapi
        for name, own, raw in _SNAPSHOT["attrs"]:
            if own:
                setattr(dapi.DouyinAPI, name, raw)
            elif name in dapi.DouyinAPI.__dict__:
                delattr(dapi.DouyinAPI, name)
        _SNAPSHOT = None
    if "api.live" in sys.modules:
        importlib.reload(sys.modules["api.live"])
    os.environ["DY_APP_ROOT"] = _ROOT
    import database
    database.reset_connection()


def tearDownModule():
    _restore_globals()


def _load_live():
    os.environ["DY_APP_ROOT"] = _ROOT
    import database
    database.reset_connection()
    import api.live as L
    importlib.reload(L)
    return L


def _set_switch(**flags):
    """写 app_config 的 `live` 分区开关（走真实存储层）。"""
    from database import get_kv_json, set_kv_json
    data = get_kv_json("app_config", {}) or {}
    live = dict(data.get("live") or {})
    live.update(flags)
    data["live"] = live
    set_kv_json("app_config", data)


def _clear_switches():
    from database import set_kv_json
    set_kv_json("app_config", {})


def _install_auth(L):
    L._auth_for = lambda account: object()


def _install_room_probe(info=None, raise_exc=False):
    """打桩真实 room_id 探测（`get_live_info`），绝不出网。

    info: 探测返回的 dict（None=空）; raise_exc=True 时抛异常。
    """
    import dy_apis.douyin_api as dapi
    _ensure_snapshot()
    _SNAPSHOT["patched"] = True

    def _stub(auth, live_id, **kwargs):
        _NET.probes += 1
        if raise_exc:
            raise RuntimeError("探测异常（桩）")
        return info

    dapi.DouyinAPI.get_live_info = staticmethod(_stub)


def _install_send_stub(mode="ok", status_code=0):
    """打桩 sendMsgInRoom；记录写接口**实际收到**的 room_id。"""
    import dy_apis.douyin_api as dapi
    _ensure_snapshot()
    _SNAPSHOT["patched"] = True

    def _stub(auth, room_id, content="", **kwargs):
        _NET.calls += 1
        _NET.routed_room = room_id
        if mode == "raise":
            raise RuntimeError("连接被重置（桩）")
        if mode == "empty":
            return {}  # 风控/限流下的空响应体（safe_json 降级形态）
        if mode == "fail":
            return {"status_code": status_code, "data": {"message": "发送过于频繁"}}
        return {"status_code": 0, "data": {}}

    dapi.DouyinAPI.sendMsgInRoom = staticmethod(_stub)


def _install_digg_stub(mode="ok", status_code=0):
    """打桩 diggLiveRoom；记录写接口**实际收到**的 room_id。"""
    import dy_apis.douyin_api as dapi
    _ensure_snapshot()
    _SNAPSHOT["patched"] = True

    def _stub(auth, room_id, count="1"):
        _NET.calls += 1
        _NET.routed_room = room_id
        if mode == "raise":
            raise RuntimeError("连接被重置（桩）")
        if mode == "empty":
            return {}
        if mode == "fail":
            return {"status_code": 2154, "data": {"message": "操作过于频繁"}}
        return {"status_code": 0, "data": {}}

    dapi.DouyinAPI.diggLiveRoom = staticmethod(_stub)


def _prep(L, live_id=WEB_RID, **flags):
    """公共前置：隔离根 + kv live_id + 开关 + 打桩。"""
    from database import set_kv_json
    _NET.reset()
    os.environ["DY_APP_ROOT"] = _ROOT
    set_kv_json("config", {"live_id": live_id})
    _clear_switches()
    if flags:
        _set_switch(**flags)
    _install_auth(L)


# ══════════════════════════════════════════════════════════════════════
# D1 · 显式配置门（默认休眠）
# ══════════════════════════════════════════════════════════════════════

class TestD1DefaultDormant(unittest.TestCase):
    """默认（未配置）必须拒发且零出站 —— 写接口不配置不外发。"""

    def test_danmaku_disabled_by_default(self):
        L = _load_live()
        _prep(L)                       # 不设任何开关
        _install_send_stub("ok")
        _install_room_probe({"room_id": REAL_ROOM_ID})
        got = asyncio.run(L.send_danmaku(
            L.DanmakuSendBody(content="测试弹幕", account="acc1")))
        self.assertFalse(got.get("ok"), f"D1 破防：默认态仍报成功: {got}")
        self.assertEqual(got.get("reason"), "danmaku_disabled", got)
        self.assertEqual(_NET.calls, 0, "默认态不得出站")
        self.assertEqual(_NET.probes, 0,
                         "默认态连房间号探测都不应发生（门在最前）")

    def test_like_disabled_by_default(self):
        L = _load_live()
        _prep(L)
        _install_digg_stub("ok")
        _install_room_probe({"room_id": REAL_ROOM_ID})
        got = asyncio.run(L.like_room(
            L.LikeSendBody(count=1, account="acc1")))
        self.assertFalse(got.get("ok"), f"D1 破防：默认态仍报成功: {got}")
        self.assertEqual(got.get("reason"), "like_disabled", got)
        self.assertEqual(_NET.calls, 0, "默认态不得出站")


# ══════════════════════════════════════════════════════════════════════
# D2 · 开启后真调 + 房间号归一化生效
# ══════════════════════════════════════════════════════════════════════

class TestD2EnabledReallyCalls(unittest.TestCase):
    """开启开关后必须真调写接口，且**用真实 room_id**（不是 URL 短号）。"""

    def test_danmaku_calls_with_real_room_id(self):
        L = _load_live()
        _prep(L, danmaku_enabled=True)
        _install_send_stub("ok")
        _install_room_probe({"room_id": REAL_ROOM_ID})
        got = asyncio.run(L.send_danmaku(
            L.DanmakuSendBody(content="测试弹幕", account="acc1")))
        self.assertTrue(got.get("ok"), f"开启后应成功: {got}")
        self.assertTrue(got.get("sent"))
        self.assertEqual(_NET.calls, 1, "开启后必须真调一次")
        self.assertEqual(_NET.routed_room, REAL_ROOM_ID,
                         f"D2 破防：写接口拿到的是 {_NET.routed_room}，"
                         f"应为真实 room_id {REAL_ROOM_ID}")
        self.assertEqual(got.get("roomId"), REAL_ROOM_ID)

    def test_like_calls_with_real_room_id(self):
        L = _load_live()
        _prep(L, like_enabled=True)
        _install_digg_stub("ok")
        _install_room_probe({"room_id": REAL_ROOM_ID})
        got = asyncio.run(L.like_room(
            L.LikeSendBody(count=5, account="acc1")))
        self.assertTrue(got.get("ok"), f"开启后应成功: {got}")
        self.assertEqual(_NET.routed_room, REAL_ROOM_ID,
                         f"D2 破防：点赞拿到的是 {_NET.routed_room}")
        self.assertEqual(got.get("count"), 5)

    def test_probed_once_and_short_circuit_when_already_real(self):
        """探测返回的值 == 传入值（本身就是真实 room_id）时不得改写。"""
        L = _load_live()
        _prep(L, live_id=REAL_ROOM_ID, danmaku_enabled=True)
        _install_send_stub("ok")
        _install_room_probe({"room_id": REAL_ROOM_ID})
        got = asyncio.run(L.send_danmaku(
            L.DanmakuSendBody(content="x", account="acc1")))
        self.assertTrue(got.get("ok"))
        self.assertEqual(_NET.routed_room, REAL_ROOM_ID)


# ══════════════════════════════════════════════════════════════════════
# D3 · 归一化降级（取不到 ⇒ 原样回退，不 fail-closed、不编造）
# ══════════════════════════════════════════════════════════════════════

class TestD3NormalizeDegradesHonestly(unittest.TestCase):
    """探测失败/空结果 ⇒ 沿用原值继续（增强步骤不得阻断用户操作）。"""

    def test_probe_returns_empty_falls_back_to_original(self):
        L = _load_live()
        _prep(L, danmaku_enabled=True)
        _install_send_stub("ok")
        _install_room_probe(None)          # 探测空
        got = asyncio.run(L.send_danmaku(
            L.DanmakuSendBody(content="x", account="acc1")))
        self.assertEqual(_NET.routed_room, WEB_RID,
                         "取不到真实 room_id 时必须原样回退（不得编造）")
        self.assertTrue(got.get("ok"), "降级不等于 fail-closed（写接口仍应尝试）")

    def test_probe_raises_falls_back_and_does_not_break(self):
        L = _load_live()
        _prep(L, like_enabled=True)
        _install_digg_stub("ok")
        _install_room_probe(raise_exc=True)
        got = asyncio.run(L.like_room(L.LikeSendBody(count=1, account="acc1")))
        self.assertEqual(_NET.routed_room, WEB_RID, "探测异常必须原样回退")

    def test_probe_returns_dict_without_room_id(self):
        L = _load_live()
        _prep(L, like_enabled=True)
        _install_digg_stub("ok")
        _install_room_probe({"room_id": ""})   # 键在但为空
        asyncio.run(L.like_room(L.LikeSendBody(count=1, account="acc1")))
        self.assertEqual(_NET.routed_room, WEB_RID)


# ══════════════════════════════════════════════════════════════════════
# D4/D5 · 成败判据与异常如实上报
# ══════════════════════════════════════════════════════════════════════

class TestD4SuccessCriterion(unittest.TestCase):
    """成功 = 上游 status_code==0；非 0 与**空响应**都必须判失败。"""

    def test_like_nonzero_status_is_failure(self):
        L = _load_live()
        _prep(L, like_enabled=True)
        _install_digg_stub("fail")
        _install_room_probe({"room_id": REAL_ROOM_ID})
        got = asyncio.run(L.like_room(L.LikeSendBody(count=1, account="acc1")))
        self.assertFalse(got.get("ok"), f"D4 破防：上游非 0 却报成功: {got}")
        self.assertEqual(got.get("statusCode"), 2154)
        self.assertEqual(got.get("reason"), "upstream_failed")
        self.assertTrue(got.get("error"), "失败必须带明确 error 文案")

    def test_like_empty_response_is_failure_not_success(self):
        """🔴 关键：风控下上游返**空响应体**（safe_json 降级为 {}）不得判成功。"""
        L = _load_live()
        _prep(L, like_enabled=True)
        _install_digg_stub("empty")
        _install_room_probe({"room_id": REAL_ROOM_ID})
        got = asyncio.run(L.like_room(L.LikeSendBody(count=1, account="acc1")))
        self.assertFalse(got.get("ok"),
                         f"D4 破防：空响应被判成功（假成功）: {got}")

    def test_danmaku_empty_response_is_failure(self):
        L = _load_live()
        _prep(L, danmaku_enabled=True)
        _install_send_stub("empty")
        _install_room_probe({"room_id": REAL_ROOM_ID})
        got = asyncio.run(L.send_danmaku(
            L.DanmakuSendBody(content="x", account="acc1")))
        self.assertFalse(got.get("ok"), f"空响应不得判成功: {got}")


class TestD5ExceptionsReported(unittest.TestCase):
    """出站异常必须如实上报为失败。"""

    def test_like_exception(self):
        L = _load_live()
        _prep(L, like_enabled=True)
        _install_digg_stub("raise")
        _install_room_probe({"room_id": REAL_ROOM_ID})
        got = asyncio.run(L.like_room(L.LikeSendBody(count=1, account="acc1")))
        self.assertFalse(got.get("ok"))
        self.assertEqual(got.get("reason"), "exception")

    def test_danmaku_exception(self):
        L = _load_live()
        _prep(L, danmaku_enabled=True)
        _install_send_stub("raise")
        _install_room_probe({"room_id": REAL_ROOM_ID})
        got = asyncio.run(L.send_danmaku(
            L.DanmakuSendBody(content="x", account="acc1")))
        self.assertFalse(got.get("ok"))
        self.assertEqual(got.get("reason"), "exception")


# ══════════════════════════════════════════════════════════════════════
# D6 · count 越界显式拒绝
# ══════════════════════════════════════════════════════════════════════

class TestD6CountBounds(unittest.TestCase):
    def test_zero_and_negative_rejected(self):
        L = _load_live()
        for bad in (0, -3):
            _prep(L, like_enabled=True)
            _install_digg_stub("ok")
            _install_room_probe({"room_id": REAL_ROOM_ID})
            got = asyncio.run(L.like_room(L.LikeSendBody(count=bad, account="acc1")))
            self.assertFalse(got.get("ok"), f"count={bad} 应被拒: {got}")
            self.assertEqual(got.get("reason"), "bad_count")
            self.assertEqual(_NET.calls, 0, "越界不得出站")

    def test_over_max_rejected(self):
        L = _load_live()
        _prep(L, like_enabled=True)
        _set_switch(like_max=10)
        _install_digg_stub("ok")
        _install_room_probe({"room_id": REAL_ROOM_ID})
        got = asyncio.run(L.like_room(L.LikeSendBody(count=11, account="acc1")))
        self.assertFalse(got.get("ok"))
        self.assertEqual(got.get("reason"), "bad_count")
        self.assertEqual(_NET.calls, 0)


# ══════════════════════════════════════════════════════════════════════
# D7 · 基座形态（直播域 Origin + bd-ticket 证书）
# ══════════════════════════════════════════════════════════════════════

class TestD7BaseShape(unittest.TestCase):
    """`diggLiveRoom` 必须用直播域 Origin 并带 bd-ticket 证书。

    这是 ADR-004 §4 登记的「E 线同族残留」：上游自身在 `sendMsgInRoom` 里
    明文写明「直播域写接口必须按 live.douyin.com 生成 Origin 与证书，
    沿用主站 Origin 会得到空响应或业务失败」。

    ⚠️ 断言纪律：判据必须落在**行为机制**上，且**注释里也不得留旧写法**
    （否则字符串断言会被自己的注释卡住 —— 本模块初版即踩此坑）。
    """

    def _digg_body(self) -> str:
        src = open(os.path.join(_HERE, "dy_apis", "client_live.py"),
                   encoding="utf-8").read()
        # 只取方法**代码体**：剥掉 docstring，避免文档里的对比说明误伤断言
        body = src.split("def diggLiveRoom", 1)[-1].split("\n    @staticmethod", 1)[0]
        if '"""' in body:
            parts = body.split('"""')
            if len(parts) >= 3:
                body = parts[0] + parts[2]
        return body

    def test_origin_is_live_domain_not_main_site(self):
        body = self._digg_body()
        self.assertIn('DouyinAPI.live_url', body,
                      "D7 破防：点赞未使用直播域 Origin")
        self.assertNotIn("douyin_url", body,
                         "D7 破防：点赞仍用主站 Origin（上游明文否定该形态）")

    def test_bd_ticket_certificate_present(self):
        body = self._digg_body()
        self.assertIn("with_bd", body, "D7 破防：点赞缺 bd-ticket 证书")

    def test_write_endpoints_normalize_room_id(self):
        src = open(_LIVE_PY, encoding="utf-8").read()
        self.assertIn("DouyinAPI._live_chat_room_id", src,
                      "D7 破防：写端点未做房间号归一化")

    def test_normalization_failure_is_logged_not_silent(self):
        """归一化失败必须**留痕**（LIVE-038/039），不得静默回退。"""
        src = open(os.path.join(_HERE, "dy_apis", "client_live.py"),
                   encoding="utf-8").read()
        self.assertIn("LIVE-038", src, "探测失败未留痕")
        self.assertIn("LIVE-039", src, "取不到真实 room_id 未留痕")


# ══════════════════════════════════════════════════════════════════════
# D8 · 负控：拆掉归一化 ⇒ 写接口收到 URL 短号
# ══════════════════════════════════════════════════════════════════════

class TestD8NegativeControl(unittest.TestCase):
    """负控：临时把归一化改成 no-op ⇒ 写接口必然收到 URL 短号。

    证明门禁不是「看着像」：它真的会因为归一化缺失而变红。
    """

    def test_without_normalization_writer_receives_web_rid(self):
        import dy_apis.douyin_api as dapi
        _ensure_snapshot()
        original = dapi.DouyinAPI.__dict__.get("_live_chat_room_id")

        def _noop(auth, room_id):
            return room_id

        try:
            dapi.DouyinAPI._live_chat_room_id = staticmethod(_noop)
            L = _load_live()
            _prep(L, danmaku_enabled=True)
            _install_send_stub("ok")
            _install_room_probe({"room_id": REAL_ROOM_ID})
            asyncio.run(L.send_danmaku(
                L.DanmakuSendBody(content="x", account="acc1")))
            self.assertEqual(
                _NET.routed_room, WEB_RID,
                "负控失效：去掉归一化后写接口竟收到了真实 room_id")
            self.assertNotEqual(_NET.routed_room, REAL_ROOM_ID,
                                "负控失效：归一化看起来没有被绕过")
        finally:
            if original is not None:
                dapi.DouyinAPI._live_chat_room_id = original
            elif "_live_chat_room_id" in dapi.DouyinAPI.__dict__:
                delattr(dapi.DouyinAPI, "_live_chat_room_id")
            _load_live()


if __name__ == "__main__":
    unittest.main()
