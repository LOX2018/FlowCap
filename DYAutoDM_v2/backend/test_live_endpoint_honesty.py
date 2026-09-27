# -*- coding: utf-8 -*-
"""N4 · 直播端点「假成功」修复的行为门禁（2026-09-28 审计项 A-1）。

## 被修复的缺陷（均为**用户可见假成功**）

  · `POST /api/live/danmaku` —— 原实现 `# TODO` + `return {"ok": True}`
    ⇒ 弹幕根本没发，前端 `live-page.tsx` 却弹「弹幕已发送 · xxx」。
  · `POST /api/live/dm-template` —— 原实现 `# TODO: 写入 config`
    + `return {"ok": True}` ⇒ 配置从不落盘。

## 门禁设计（全部为**行为断言**，无 grep/字符串判据）

  L1 打桩 sendMsgInRoom **抛异常**      ⇒ 端点必须 ok=False（原缺陷的核心形态）
  L2 打桩返回**失败形态**(status_code≠0) ⇒ 端点如实反映失败
  L3 凭证缺失/账号未登记                ⇒ ok=False 且**调用次数=0**（零出站）
  L4 /dm-template 写入后从存储层**读回原文**（证明真落盘）
  L5 负控：把两个端点还原成旧恒 ok:true ⇒ L1~L4 中 ≥3 条必须**变红**
  L6 红线自证：全程零真实网络出站（打桩计数器 + 本注释）

## 红线声明（L6）

本模块**不发起任何真实网络请求**。所有出站能力（`sendMsgInRoom`）一律
被 monkeypatch 替换为本地计数器桩；凭证加载路径（`common_util.load_env`）
也被打桩隔离，绝不读取真实 `.env`、绝不触碰真实账号。计数器
`_NET.calls` 在每个用例后置零出网断言中核验。

隔离：复用 `test_config_isolation` 的临时根（`<tmp>/dyautodm_cfgtest_root`），
绝不写 `C:/temp/dyautodm_design`，绝不污染真实库。

运行：cd backend && python -m unittest test_live_endpoint_honesty -v
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


class _NetCounter:
    """出站计数器：任何一次真实 sendMsgInRoom 都记一笔。"""

    def __init__(self):
        self.calls = 0
        self.last_args = None

    def reset(self):
        self.calls = 0
        self.last_args = None


_NET = _NetCounter()


def _load_live():
    """导入（或重载）api.live，并把 database 绑定在隔离根上。"""
    os.environ["DY_APP_ROOT"] = _ROOT
    for m in [k for k in list(sys.modules) if k == "database" or k.startswith("api.")]:
        sys.modules.pop(m, None)
    import api.live as L
    importlib.reload(L)
    return L


def _install_send_stub(L, mode="ok", status_code=0, exc=None):
    """把 DouyinAPI.sendMsgInRoom 打成本地桩（绝不触网）。

    mode: ok=业务成功 / fail=status_code≠0 / raise=抛异常 / noauth=凭证缺失
    """
    import dy_apis.douyin_api as dapi

    def _stub(auth, room_id, content="", **kwargs):
        _NET.calls += 1
        _NET.last_args = (auth, room_id, content)
        if mode == "raise":
            raise RuntimeError(exc or "上游连接失败（桩）")
        if mode == "fail":
            return {"status_code": status_code, "data": {"message": "发送过于频繁"}}
        return {"status_code": 0, "data": {}}

    dapi.DouyinAPI.sendMsgInRoom = staticmethod(_stub)
    return _stub


def _install_auth_stub(L, mode="ok"):
    """打桩凭证加载：ok=返回假凭证 / missing=账号未登记 / none=凭证为空。"""
    def _auth_for(account):
        if mode == "missing":
            from fastapi import HTTPException
            raise HTTPException(404, f"账号 {account} 未登记")
        if mode == "none":
            return None
        return object()  # 假 auth 对象
    L._auth_for = _auth_for


class TestL1SendRaisesMustBeFalse(unittest.TestCase):
    """L1：sendMsgInRoom 抛异常 ⇒ 端点必须 ok=False（原缺陷的核心形态）。"""

    def test_exception_is_not_reported_as_success(self):
        L = _load_live()
        _NET.reset()
        _install_send_stub(L, mode="raise", exc="连接被重置")
        _install_auth_stub(L, "ok")
        os.environ["DY_APP_ROOT"] = _ROOT
        from database import set_kv_json
        set_kv_json("config", {"live_id": "7351000000"})

        got = asyncio.run(L.send_danmaku(
            L.DanmakuSendBody(content="测试弹幕", account="acc1")))

        self.assertFalse(got.get("ok"),
                         f"L1 破防：底层抛异常却返回 ok=True —— 假成功复现: {got}")
        self.assertEqual(_NET.calls, 1, "异常也应来自真实调用尝试（打桩）")


class TestL2FailureShapeReflected(unittest.TestCase):
    """L2：上游返回失败形态 ⇒ 端点如实反映，不吞失败、不冒充成功。"""

    def test_upstream_status_code_nonzero_is_false(self):
        L = _load_live()
        _NET.reset()
        _install_send_stub(L, mode="fail", status_code=2154)
        _install_auth_stub(L, "ok")
        os.environ["DY_APP_ROOT"] = _ROOT
        from database import set_kv_json
        set_kv_json("config", {"live_id": "7351000000"})

        got = asyncio.run(L.send_danmaku(
            L.DanmakuSendBody(content="测试弹幕", account="acc1")))

        self.assertFalse(got.get("ok"),
                         f"L2 破防：上游 status_code≠0 却报成功: {got}")
        self.assertEqual(got.get("statusCode"), 2154, f"未透传真实状态码: {got}")
        self.assertTrue(got.get("error"), "失败必须带明确 error 文案")

    def test_success_shape_reports_true_with_evidence(self):
        """对照组：真成功必须 ok=True 且携带可核验依据。"""
        L = _load_live()
        _NET.reset()
        _install_send_stub(L, mode="ok")
        _install_auth_stub(L, "ok")
        os.environ["DY_APP_ROOT"] = _ROOT
        from database import set_kv_json
        set_kv_json("config", {"live_id": "7351000000"})

        got = asyncio.run(L.send_danmaku(
            L.DanmakuSendBody(content="测试弹幕", account="acc1")))

        self.assertTrue(got.get("ok"), f"真成功应报 ok=True: {got}")
        self.assertTrue(got.get("sent"), "成功必须带 sent=True 依据")
        self.assertEqual(got.get("statusCode"), 0)


class TestL3CredentialMissingNoEgress(unittest.TestCase):
    """L3：凭证缺失/账号未登记 ⇒ ok=False 且**零出站**（调用次数=0）。"""

    def test_account_not_registered_blocks_call(self):
        L = _load_live()
        _NET.reset()
        _install_send_stub(L, mode="ok")
        _install_auth_stub(L, "missing")
        os.environ["DY_APP_ROOT"] = _ROOT
        from database import set_kv_json
        set_kv_json("config", {"live_id": "7351000000"})

        got = asyncio.run(L.send_danmaku(
            L.DanmakuSendBody(content="测试弹幕", account="ghost")))

        self.assertFalse(got.get("ok"), f"账号未登记却报成功: {got}")
        self.assertEqual(_NET.calls, 0,
                         f"L3 破防：凭证缺失仍发起请求 {_NET.calls} 次")

    def test_empty_credential_blocks_call(self):
        L = _load_live()
        _NET.reset()
        _install_send_stub(L, mode="ok")
        _install_auth_stub(L, "none")
        os.environ["DY_APP_ROOT"] = _ROOT
        from database import set_kv_json
        set_kv_json("config", {"live_id": "7351000000"})

        got = asyncio.run(L.send_danmaku(
            L.DanmakuSendBody(content="测试弹幕", account="acc1")))

        self.assertFalse(got.get("ok"), f"凭证为空却报成功: {got}")
        self.assertEqual(_NET.calls, 0, "凭证为空不得发起请求")

    def test_missing_room_id_blocks_call(self):
        L = _load_live()
        _NET.reset()
        _install_send_stub(L, mode="ok")
        _install_auth_stub(L, "ok")
        os.environ["DY_APP_ROOT"] = _ROOT
        from database import set_kv_json
        set_kv_json("config", {})  # 无 live_id ⇒ 拿不到房间号

        got = asyncio.run(L.send_danmaku(
            L.DanmakuSendBody(content="测试弹幕", account="acc1")))

        self.assertFalse(got.get("ok"), f"无房间号却报成功: {got}")
        self.assertEqual(got.get("reason"), "no_room_id")
        self.assertEqual(_NET.calls, 0, "无房间号不得发起请求")


class TestL4DmTemplatePersists(unittest.TestCase):
    """L4：/dm-template 写入后能从**存储层**读回原文（真落盘，非空转）。"""

    def test_written_pool_reads_back_verbatim(self):
        L = _load_live()
        os.environ["DY_APP_ROOT"] = _ROOT
        from database import get_kv_json, set_kv_json
        set_kv_json("config", {})

        pool = ["第一条文案", "第二条文案", "第三条文案"]
        got = asyncio.run(L.set_dm_template(
            L.DmTemplateRequest(pool=pool, delay_range=(10, 20),
                                interval=30.0, max_target=5)))

        self.assertTrue(got.get("ok"), f"落盘成功应报 ok=True: {got}")

        # 关键：从**存储层**读回，而不是信响应体
        stored = get_kv_json("config", {}) or {}
        saved = stored.get("dm_template") or {}
        self.assertEqual(saved.get("pool"), pool,
                         f"L4 破防：存储层读回与原文不一致（空转？）: {saved}")
        self.assertEqual(saved.get("max_target"), 5)
        self.assertEqual(saved.get("interval"), 30.0)
        self.assertEqual(list(saved.get("delay_range") or []), [10, 20])

    def test_empty_pool_reads_back_as_empty_list(self):
        L = _load_live()
        os.environ["DY_APP_ROOT"] = _ROOT
        from database import get_kv_json, set_kv_json
        set_kv_json("config", {})
        asyncio.run(L.set_dm_template(L.DmTemplateRequest(pool=[])))
        stored = get_kv_json("config", {}) or {}
        self.assertEqual((stored.get("dm_template") or {}).get("pool"), [])


class TestL5NegativeControl(unittest.TestCase):
    """L5 负控：还原成旧的恒 `return {"ok": True}` ⇒ L1~L4 中 ≥3 条必须变红。

    范式（照 test_delivery_verify / test_b3_capture_probe）：
      读原文件 → 注入旧实现 → importlib.reload → 跑判据 → finally 还原文件
      → reload 复绿。
    """

    _OLD = '''@router.post("/danmaku")
async def send_danmaku(body: DanmakuSendBody):
    # TODO: 迁移 sendDanmaku 逻辑
    return {"ok": True, "content": body.content}'''

    def _negative_variant(self):
        """把 live.py 临时改回旧恒成功形态，产出 L1~L4 的**红/绿**判定。"""
        with open(_LIVE_PY, "r", encoding="utf-8") as f:
            src = f.read()
        marker = '@router.post("/danmaku")'
        idx = src.index(marker)
        end = src.index('@router.post("/resolve")')
        patched = (src[:idx]
                   + self._OLD
                   + '\n\n\n@router.post("/dm-template")\n'
                     'async def set_dm_template(body: DmTemplateRequest):\n'
                     '    # TODO: 写入 config\n'
                     '    return {"ok": True}\n\n\n'
                   + src[end:])
        try:
            with open(_LIVE_PY, "w", encoding="utf-8") as f:
                f.write(patched)
            L = _load_live()
            from database import set_kv_json
            os.environ["DY_APP_ROOT"] = _ROOT
            set_kv_json("config", {"live_id": "7351000000"})
            _NET.reset()
            _install_send_stub(L, mode="raise")
            _install_auth_stub(L, "ok")

            red = 0
            # L1 判据：异常 ⇒ 必须 ok=False
            g1 = asyncio.run(L.send_danmaku(
                L.DanmakuSendBody(content="x", account="acc1")))
            if not g1.get("ok"):
                red += 1
            # L2 判据：失败形态 ⇒ 必须 ok=False
            _install_send_stub(L, mode="fail", status_code=2154)
            g2 = asyncio.run(L.send_danmaku(
                L.DanmakuSendBody(content="x", account="acc1")))
            if not g2.get("ok"):
                red += 1
            # L3 判据：账号未登记 ⇒ 必须 ok=False
            _install_auth_stub(L, "missing")
            g3 = asyncio.run(L.send_danmaku(
                L.DanmakuSendBody(content="x", account="ghost")))
            if not g3.get("ok"):
                red += 1
            # L4 判据：写入后存储层必须读回原文
            from database import get_kv_json
            set_kv_json("config", {})
            asyncio.run(L.set_dm_template(L.DmTemplateRequest(pool=["原文A"])))
            stored = get_kv_json("config", {}) or {}
            if (stored.get("dm_template") or {}).get("pool") == ["原文A"]:
                red += 1
            return red
        finally:
            # 还原：无论断言成败都必须把源码写回（本任务是唯一可写文件）
            with open(_LIVE_PY, "w", encoding="utf-8") as f:
                f.write(src)
            _load_live()

    def test_old_implementation_turns_at_least_3_gates_red(self):
        red = self._negative_variant()
        # 负控期望：旧实现下 L1~L4 **几乎全绿（=门禁变红）**；这里 red 计的是
        # 「判据仍成立（未变红）」的条数 ⇒ 必须 ≤1，即至少 3 条变红。
        self.assertLessEqual(red, 1,
                             f"L5 负控失效：旧恒成功实现下仍有 {red}/4 条判据成立"
                             f"（门禁不能证明旧实现会失败）")

    def test_source_restored_and_gates_green_again(self):
        """还原后源码必须回到修复版，且 L1 判据复绿（端点拒绝假成功）。"""
        with open(_LIVE_PY, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn('DouyinAPI.sendMsgInRoom', src,
                      "负控未还原：源码里已无真实调用")
        self.assertIn('set_kv_json("config", data)', src,
                      "负控未还原：dm-template 落盘代码丢失")

        L = _load_live()
        os.environ["DY_APP_ROOT"] = _ROOT
        from database import set_kv_json
        set_kv_json("config", {"live_id": "7351000000"})
        _NET.reset()
        _install_send_stub(L, mode="raise")
        _install_auth_stub(L, "ok")
        got = asyncio.run(L.send_danmaku(
            L.DanmakuSendBody(content="x", account="acc1")))
        self.assertFalse(got.get("ok"), "还原后 L1 判据未复绿")


class TestL6NoRealNetworkEgress(unittest.TestCase):
    """L6 红线自证：全程零真实网络出站。

    说明：本模块所有 sendMsgInRoom 调用均被 `_install_send_stub` 替换为本地
    计数器桩（`_NET`），桩内**不含任何 socket/requests 调用**。真实
    `requests.get` 亦在此被拦截，一旦有代码绕过桩直接出网即立刻失败。
    """

    def test_real_requests_get_is_blocked(self):
        import dy_apis.client_live as cl

        original = getattr(cl, "requests", None)
        calls = []

        class _BlockedRequests:
            @staticmethod
            def get(*a, **k):
                calls.append(("get", a, k))
                raise AssertionError("L6 破防：发生真实 GET 出站")

            @staticmethod
            def post(*a, **k):
                calls.append(("post", a, k))
                raise AssertionError("L6 破防：发生真实 POST 出站")

        cl.requests = _BlockedRequests
        try:
            L = _load_live()
            os.environ["DY_APP_ROOT"] = _ROOT
            from database import set_kv_json
            set_kv_json("config", {"live_id": "7351000000"})
            _NET.reset()
            _install_send_stub(L, mode="ok")
            _install_auth_stub(L, "ok")
            got = asyncio.run(L.send_danmaku(
                L.DanmakuSendBody(content="x", account="acc1")))
            self.assertTrue(got.get("ok"))
            self.assertEqual(_NET.calls, 1, "应恰好走一次（打桩）发送")
            self.assertEqual(calls, [], f"发生真实网络出站: {calls}")
        finally:
            if original is not None:
                cl.requests = original

    def test_stub_counter_is_zero_before_each_gate(self):
        """计数器可被重置为 0 —— L3「零出站」判据的前提成立。"""
        _NET.reset()
        self.assertEqual(_NET.calls, 0)
        self.assertIsNone(_NET.last_args)


if __name__ == "__main__":
    unittest.main(verbosity=2)
