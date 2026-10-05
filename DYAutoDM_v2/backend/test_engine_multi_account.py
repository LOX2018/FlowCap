# -*- coding: utf-8 -*-
"""多账号直播并发（ADR-002）回归守卫。

## 覆盖的验收判据（ADR-002 §6，逐条对应）

| # | 判据 | 本文件对应测试 |
|---|---|---|
| 1 | 两账号**同时 RUNNING**、各自实例互不影响 | `TestRegistryIsolation` |
| 2 | 同账号重复启动 → **409**（不再是静默 `ok:true`） | `TestStartConflict409` |
| 3 | 「未指定 acct 且有多个任务」→ **409**（不猜账号） | `TestAmbiguousAccount409` |
| 4 | 单账号/匿名行为**零回归** | `TestSingleAccountZeroRegression` |
| 5 | 收尾逐账号 shutdown（不丢实例） | `TestShutdownAll` |

## 设计要点（为什么这样测）

用**替身引擎**（`_FakeAdm`）而不是真 `AutoDM`：本文件要守的是**编排与端点契约**
（谁拿到哪个实例、冲突怎么报），不是引擎内部行为；真 `AutoDM` 会拉 settings/BCC 依赖，
把这类测试变成「跑得动才怪」。替身实现了契约里被用到的**全部**成员
（`state`/`is_running`/`pause`/`resume`/`stop`/`shutdown`/`start`/`live_url`），
因此端点代码路径与真机一致。

运行：cd backend && python -m unittest test_engine_multi_account -v
"""
from __future__ import annotations

import asyncio
import os
import sys
import unittest

from fastapi import HTTPException

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import test_config_isolation as iso  # noqa: E402,F401

from models.enums import EngineState  # noqa: E402
from services.engine_registry import EngineRegistry, ANONYMOUS_KEY  # noqa: E402
from api import engine as engine_api  # noqa: E402


class _FakeAdm:
    """替身引擎：契约成员齐全，状态可被测试直接摆布。"""

    def __init__(self, acct: str = "") -> None:
        self.state = EngineState.IDLE
        self.live_url = None
        self.live_id = None
        self.sent_count = 0
        self.status_msg = ""
        self.target_acct = acct
        self._acct = acct
        self.start_calls = 0
        self.stopped: list[str] = []
        self.shutdown_called = False

    @property
    def is_running(self) -> bool:
        return self.state in (EngineState.STARTING, EngineState.RUNNING,
                              EngineState.PAUSED)

    async def start(self, cfg) -> None:
        self.start_calls += 1
        self.state = EngineState.STARTING
        self.live_url = getattr(cfg, "live_url", None)

    async def pause(self) -> None:
        self.state = EngineState.PAUSED

    async def resume(self) -> None:
        self.state = EngineState.RUNNING

    async def stop(self, hard: bool = False) -> None:
        self.stopped.append("hard" if hard else "soft")
        self.state = EngineState.STOPPED

    async def shutdown(self) -> None:
        self.shutdown_called = True


class _Req:
    """最小 Request 替身：只需 `app.state`。"""

    def __init__(self, adm, engines=None) -> None:
        class _App:
            pass

        self.app = _App()
        self.app.state = _App()
        self.app.state.adm = adm
        if engines is not None:
            self.app.state.engines = engines


class _Cfg:
    """TaskConfig 替身（只带端点用到的字段）。"""

    def __init__(self, live_url: str, acct: str | None = None) -> None:
        self.live_url = live_url
        self.acct = acct

    def resolved(self):
        return self


class TestRegistryIsolation(unittest.TestCase):
    """判据 1：(A) 两账号各自房间 → 两个独立实例、互不影响。"""

    def test_distinct_accounts_get_distinct_engines(self):
        reg = EngineRegistry(factory=_FakeAdm)
        a = reg.get("小助理")
        b = reg.get("张老师")
        self.assertIsNot(a, b, "不同账号必须拿到不同实例（并发的前提）")
        self.assertEqual(reg.keys(), ["小助理", "张老师"])

    def test_same_account_reuses_one_engine(self):
        reg = EngineRegistry(factory=_FakeAdm)
        self.assertIs(reg.get("小助理"), reg.get("小助理"),
                      "同账号必须复用同一实例（否则同账号会跑出两个任务）")

    def test_anonymous_key_is_stable(self):
        reg = EngineRegistry(factory=_FakeAdm)
        self.assertIs(reg.get(None), reg.get(""))
        self.assertIs(reg.get(None), reg.get("   "))
        self.assertEqual(reg.keys(), [ANONYMOUS_KEY])

    def test_both_can_be_running_simultaneously(self):
        """两账号同时 RUNNING —— 旧实现下第二个会被单例吞掉（实测 already:true）。"""
        reg = EngineRegistry(factory=_FakeAdm)
        a, b = reg.get("小助理"), reg.get("张老师")
        asyncio.run(a.start(_Cfg("https://live.douyin.com/A")))
        asyncio.run(b.start(_Cfg("https://live.douyin.com/B")))
        self.assertEqual(a.state, EngineState.STARTING)
        self.assertEqual(b.state, EngineState.STARTING)
        self.assertEqual(a.live_url, "https://live.douyin.com/A")
        self.assertEqual(b.live_url, "https://live.douyin.com/B")

    def test_busy_keys_reflects_state(self):
        reg = EngineRegistry(factory=_FakeAdm)
        idle = reg.get("小助理")
        busy = reg.get("张老师")
        busy.state = EngineState.RUNNING
        self.assertEqual(reg.busy_keys(), ["张老师"])
        idle.state = EngineState.RUNNING
        self.assertEqual(sorted(reg.busy_keys()), sorted(["小助理", "张老师"]))


class TestStartConflict409(unittest.TestCase):
    """判据 2：同账号重复启动必须**显式 409**（旧实现静默 ok:true = 假成功）。"""

    def _run(self, coro):
        return asyncio.run(coro)

    def test_second_start_same_account_raises_409(self):
        adm = _FakeAdm("小助理")
        reg = EngineRegistry(factory=_FakeAdm)
        reg._engines["小助理"] = adm
        req = _Req(adm, reg)

        r = self._run(engine_api.start_engine(req, _Cfg("https://live.douyin.com/A", "小助理")))
        self.assertTrue(r["ok"])
        self.assertEqual(r["acct"], "小助理")
        # 状态已推进到 STARTING → 再来一次必须 409
        adm.state = EngineState.RUNNING
        with self.assertRaises(HTTPException) as cm:
            self._run(engine_api.start_engine(req, _Cfg("https://live.douyin.com/A", "小助理")))
        self.assertEqual(cm.exception.status_code, 409)
        self.assertIn("已在监听", str(cm.exception.detail))

    def test_other_account_not_blocked_by_running_one(self):
        """🔴 旧缺陷的核心：A 在跑**不得**阻止 B 启动（旧实现返回 already:true）。"""
        reg = EngineRegistry(factory=lambda: _FakeAdm())
        a = reg.get("小助理")
        a.state = EngineState.RUNNING
        req = _Req(a, reg)
        r = self._run(engine_api.start_engine(req, _Cfg("https://live.douyin.com/B", "张老师")))
        self.assertTrue(r["ok"])
        self.assertEqual(r["acct"], "张老师")
        b = reg.get_or_none("张老师")
        self.assertIsNotNone(b, "B 必须真的被建起来（不得被 A 的运行态吞掉）")
        self.assertEqual(b.start_calls, 1, "B 的 start 必须真被调用一次")

    def test_start_does_not_create_instance_via_peek_path(self):
        """start 之后 app.state.adm 兼容别名应指向「刚启动的那个实例」。"""
        reg = EngineRegistry(factory=lambda: _FakeAdm())
        base = reg.get(ANONYMOUS_KEY)
        req = _Req(base, reg)
        self._run(engine_api.start_engine(req, _Cfg("https://live.douyin.com/A", "小助理")))
        self.assertIs(req.app.state.adm, reg.get_or_none("小助理"))


class TestAmbiguousAccount409(unittest.TestCase):
    """判据 3：未指定 acct 且有多个任务 → 409（不猜账号）。"""

    def _two_busy(self):
        reg = EngineRegistry(factory=lambda: _FakeAdm())
        a, b = reg.get("小助理"), reg.get("张老师")
        a.state = EngineState.RUNNING
        b.state = EngineState.RUNNING
        req = _Req(a, reg)
        return reg, req

    def test_control_without_acct_is_ambiguous(self):
        reg, req = self._two_busy()
        for fn in (engine_api.pause_engine, engine_api.resume_engine,
                   engine_api.stop_engine, engine_api.stop_soft):
            with self.assertRaises(HTTPException) as cm:
                asyncio.run(fn(req, acct=""))
            self.assertEqual(cm.exception.status_code, 409, fn.__name__)
            self.assertIn("不猜测账号", str(cm.exception.detail))

    def test_control_with_acct_targets_that_account(self):
        reg, req = self._two_busy()
        asyncio.run(engine_api.stop_engine(req, acct="张老师"))
        self.assertEqual(reg.get_or_none("张老师").stopped, ["hard"])
        self.assertEqual(reg.get_or_none("小助理").stopped, [],
                         "停 B 不得动到 A（旧单例实现必然停错）")

    def test_control_unknown_account_is_404_not_fallback(self):
        """🔴 未知账号**不得**回落到 app.state.adm（那会停掉别人的任务）。"""
        reg, req = self._two_busy()
        with self.assertRaises(HTTPException) as cm:
            asyncio.run(engine_api.stop_engine(req, acct="不存在的账号"))
        self.assertEqual(cm.exception.status_code, 404)
        self.assertEqual(reg.get_or_none("小助理").stopped, [])
        self.assertEqual(reg.get_or_none("张老师").stopped, [])

    def test_control_without_acct_single_busy_works(self):
        """单任务时不给 acct 仍可用（单账号零回归）。"""
        reg = EngineRegistry(factory=lambda: _FakeAdm())
        only = reg.get("小助理")
        only.state = EngineState.RUNNING
        req = _Req(only, reg)
        asyncio.run(engine_api.pause_engine(req, acct=""))
        self.assertEqual(only.state, EngineState.PAUSED)


class TestSingleAccountZeroRegression(unittest.TestCase):
    """判据 4：单账号/匿名单实例行为与改造前一致。"""

    def test_anonymous_start_uses_fixed_key(self):
        reg = EngineRegistry(factory=lambda: _FakeAdm())
        base = reg.get(ANONYMOUS_KEY)
        req = _Req(base, reg)
        r = asyncio.run(engine_api.start_engine(req, _Cfg("https://live.douyin.com/A", None)))
        self.assertTrue(r["ok"])
        self.assertEqual(r["acct"], "", "匿名任务不回传 acct")
        self.assertEqual(reg.keys(), [ANONYMOUS_KEY])

    def test_legacy_single_instance_without_registry(self):
        """未挂 registry（旧调用方/测试替身）→ 退回 app.state.adm，行为不变。"""
        adm = _FakeAdm("小助理")
        adm.state = EngineState.RUNNING
        req = _Req(adm, engines=None)
        r = asyncio.run(engine_api.pause_engine(req, acct=""))
        self.assertEqual(adm.state, EngineState.PAUSED)
        self.assertEqual(r["state"], "paused")

    def test_empty_live_url_still_400(self):
        adm = _FakeAdm()
        req = _Req(adm, EngineRegistry(factory=lambda: _FakeAdm()))
        with self.assertRaises(HTTPException) as cm:
            asyncio.run(engine_api.start_engine(req, _Cfg("   ", "小助理")))
        self.assertEqual(cm.exception.status_code, 400)

    def test_accounts_endpoint_lists_started_only(self):
        reg = EngineRegistry(factory=lambda: _FakeAdm())
        a = reg.get("小助理")
        a.state = EngineState.RUNNING
        req = _Req(a, reg)
        items = asyncio.run(engine_api.list_engine_accounts(req))["items"]
        self.assertEqual([i["acct"] for i in items], ["小助理"])

    def test_accounts_endpoint_does_not_create_instances(self):
        reg = EngineRegistry(factory=lambda: _FakeAdm())
        req = _Req(_FakeAdm(), reg)
        asyncio.run(engine_api.list_engine_accounts(req))
        self.assertEqual(reg.keys(), [], "查询不得把实例「建」出来")


class TestShutdownAll(unittest.TestCase):
    """判据 5：收尾逐账号 shutdown（不丢实例、异常不吞）。"""

    def test_shutdown_all_touches_every_instance(self):
        reg = EngineRegistry(factory=lambda: _FakeAdm())
        engs = [reg.get(n) for n in ("小助理", "张老师", None)]
        res = asyncio.run(reg.shutdown_all())
        self.assertEqual(sorted(res.keys()), sorted(["小助理", "张老师", ANONYMOUS_KEY]))
        for e in engs:
            self.assertTrue(e.shutdown_called, "有实例没被 shutdown")

    def test_shutdown_all_reports_failures(self):
        class _Bad(_FakeAdm):
            async def shutdown(self) -> None:
                raise RuntimeError("boom")

        reg = EngineRegistry(factory=_Bad)
        reg.get("小助理")
        res = asyncio.run(reg.shutdown_all())
        self.assertIn("err", res["小助理"], "shutdown 异常必须如实上报，不得静默吞")


class TestNotifyAccountResolution(unittest.TestCase):
    """通知链路（外部指令）必须按账号解析；歧义显式失败。"""

    def setUp(self):
        from api import notify
        self.notify = notify
        self.reg = EngineRegistry(factory=lambda: _FakeAdm())
        notify.bind_engines(self.reg)
        self._prev = notify._BOUND_ENGINES

    def tearDown(self):
        self.notify._BOUND_ENGINES = self._prev

    def test_explicit_account_resolves(self):
        a = self.reg.get("小助理")
        self.assertIs(self.notify.resolve_notify_adm("小助理"), a)

    def test_ambiguous_raises(self):
        self.reg.get("小助理").state = EngineState.RUNNING
        self.reg.get("张老师").state = EngineState.RUNNING
        with self.assertRaises(RuntimeError) as cm:
            self.notify.resolve_notify_adm(None)
        self.assertIn("不猜测", str(cm.exception))

    def test_unknown_account_raises(self):
        with self.assertRaises(RuntimeError):
            self.notify.resolve_notify_adm("查无此账号")

    def test_single_busy_resolves_without_acct(self):
        a = self.reg.get("小助理")
        a.state = EngineState.RUNNING
        self.assertIs(self.notify.resolve_notify_adm(None), a)


if __name__ == "__main__":
    unittest.main(verbosity=2)
