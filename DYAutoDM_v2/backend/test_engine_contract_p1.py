# -*- coding: utf-8 -*-
"""P1 批次：前端↔后端「引擎控制」契约错位 + 引擎控制不可用的守卫测试。

## 覆盖（缺陷编号 → 测试）

| # | 缺陷 | 测试类 |
|---|---|---|
| P1-1 | 4 个控制端点丢账号（前端发 JSON body，后端只读 query） | `TestEngineControlAccountContract` |
| P1-2 | `notify._execute` 直调端点漏传 `acct`（Request→acct 错位） | `TestNotifyInternalCallContract` |
| P1-3 | 多任务卡片 `/start` 不带 `live_url` → 必 400，按钮永不可用 | `TestEngineStartRequiresLiveUrl`（后端侧）+ 前端 grep 断言 |
| P1-6 | `busy_keys()` 把 `stopped` 当忙 → 双引擎停止后 `/start` 恒 409 | `TestBusyKeysTerminalStates` |
| high-1 | `/start` 锁键与实例分叉（匿名锁 + 别人实例） | `TestStartLockKeyMatchesInstance` |
| P1-7 | 合集传 `mix_id` 当 `series_id`，无 `is_serial_mix` 分流 | `TestCollectionSeriesDispatch` |
| P4 | `all_running_keys` 死代码 / 未用 import | `TestDeadCodeRemoved` |

## 运行（隔离：只跑本模块，DY_APP_ROOT 指向临时目录）

```
cd DYAutoDM_v2/backend
DY_APP_ROOT="$LOCALAPPDATA/Temp/fixB" python -m unittest test_engine_contract_p1 -v
```
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
import unittest

from fastapi import HTTPException

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import test_config_isolation as iso  # noqa: E402,F401  （强制 DY_APP_ROOT 隔离）

from models.enums import EngineState  # noqa: E402
from services.engine_registry import EngineRegistry, ANONYMOUS_KEY  # noqa: E402
from api import engine as engine_api  # noqa: E402


# ══════════════════════════════════════════════════════════════════════════
# 替身（沿用 test_engine_multi_account 的契约成员集，保持同一套语义）
# ══════════════════════════════════════════════════════════════════════════

class _FakeAdm:
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
                              EngineState.PAUSED, EngineState.STOPPING)

    async def start(self, cfg) -> None:
        self.start_calls += 1
        self.state = EngineState.STARTING
        self.live_url = getattr(cfg, "live_url", None)
        self._acct = getattr(cfg, "acct", None)

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
    def __init__(self, adm, engines=None) -> None:
        class _App:
            pass

        self.app = _App()
        self.app.state = _App()
        self.app.state.adm = adm
        if engines is not None:
            self.app.state.engines = engines


class _Cfg:
    def __init__(self, live_url: str, acct: str | None = None) -> None:
        self.live_url = live_url
        self.acct = acct

    def resolved(self):
        return self


# ══════════════════════════════════════════════════════════════════════════
# P1-1：真 app + 真 TestClient + 真会员登录 —— 前端现在发的形态必须 200
# ══════════════════════════════════════════════════════════════════════════

class TestEngineControlAccountContract(unittest.TestCase):
    """🔴 P1-1：四个控制端点的账号是 **query `?acct=`**（唯一契约）。

    用**真 app 的 TestClient**（含会员中间件）验证 —— 而不是函数直调，
    因为本次缺陷正是「HTTP 层契约（query vs body）」而不是函数签名。

    修复前实测：`body={"account":"张老师"}` → **409**「存在多个进行中的直播任务」；
                `?acct=张老师` → **200**。
    修复后：两者都 200，且 body 里的 account **被忽略**（只认 query）。
    """

    USER = "p1_contract"
    PWD = "p1-contract-pw-123"

    #: TestClient **不起 lifespan**（故意）。
    #:
    #: 为什么：本类要守的是**端点 + 会员中间件的 HTTP 契约**（query vs body），
    #: 而这层完全不依赖 lifespan 建的任何状态 —— 我们自己在 `setUp` 里注入
    #: `app.state.engines` / `app.state.adm`。
    #:
    #: 实测：`with TestClient(app)` 会跑完整 lifespan（DB 初始化 + 后台巡检/定时器），
    #: 在同一台机器上同一命令的 startup 耗时**在 0.08s ~ 33s 之间抖动**（12 次采样：
    #: 4s/37s/64s 三次连跑；直接探针 0.07s/0.08s），把单测变成不确定时长且易超时。
    #: 去掉 lifespan 后本类稳定 ~0.8s（登录 200 + 端点 200 实测）。
    #: 因此这里用**裸 TestClient**（不用 `with` / `__enter__`），并在下方断言
    #: 「会员中间件确实生效」（无 token → 401），保证测试没有因为省掉 lifespan 而失去防线。
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from main import app
        cls._TestClient = TestClient
        cls._app = app
        cls._c = TestClient(app)          # 裸构造：不发 lifespan 事件
        cls._c.post("/api/member/register",
                    json={"username": cls.USER, "password": cls.PWD})
        r = cls._c.post("/api/member/login",
                        json={"username": cls.USER, "password": cls.PWD})
        if r.status_code != 200:
            raise RuntimeError(f"会员登录失败，契约测试无法进行: {r.status_code} {r.text[:200]}")
        cls._token = r.json()["token"]

    def setUp(self):
        # 每个用例装一套干净的「两个 RUNNING 账号」
        reg = EngineRegistry(factory=_FakeAdm)
        a = reg.get("小助理"); a.state = EngineState.RUNNING; a.target_acct = "小助理"
        b = reg.get("张老师"); b.state = EngineState.RUNNING; b.target_acct = "张老师"
        self._app.state.engines = reg
        self._app.state.adm = a
        self.reg = reg

    def _H(self) -> dict:
        return {"X-Member-Token": self._token}

    def test_member_middleware_is_actually_in_effect(self):
        """自证：省掉 lifespan 之后，**会员门禁仍然在守**（否则本类的 200 是假的）。

        判据：**同一请求**在无 token 时 401、有 token 时走到业务逻辑（本例为
        `?acct=小助理` → 200）。中间件若没挂上，无 token 那条会变成 200/409 而非 401。
        没有这条断言，一个「中间件没挂上」的坏环境会让所有 200 断言静默变绿。

        ⚠️ 必须带 `?acct=`：本类 setUp 装的是**两个** RUNNING 账号，
        不带 acct 是**设计上的 409**（歧义必须显式失败），不是门禁失效。
        """
        self.assertEqual(self._c.post("/api/engine/pause").status_code, 401,
                         "无 X-Member-Token 必须 401 —— 证明会员中间件在场")
        r = self._c.post("/api/engine/pause?acct=%E5%B0%8F%E5%8A%A9%E7%90%86",
                         headers=self._H())
        self.assertEqual(r.status_code, 200, r.text)

    def test_query_acct_targets_that_account(self):
        """规范形态：`?acct=张老师` → 200 且只停张老师（不碰小助理）。"""
        r = self._c.post("/api/engine/stop?acct=%E5%BC%A0%E8%80%81%E5%B8%88", headers=self._H())
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["acct"], "张老师")
        self.assertEqual(self.reg.get_or_none("张老师").stopped, ["hard"])
        self.assertEqual(self.reg.get_or_none("小助理").stopped, [],
                         "停张老师不得动到小助理（ADR-002 §5.3 停对任务）")

    def test_body_form_does_not_take_effect(self):
        """🔴 回归门禁：body 里的 `account` **不生效**（证明契约确实只在 query）。

        本测试与 `TestFrontendContractSource.test_client_ts_uses_query_acct_not_body`
        合起来才构成完整防线：
          · 本测试（后端侧）证明 **只有 query 管用**；
          · 那条（前端侧）证明 **前端确实发 query**。
        任何一侧漂回 body，两条断言必有一条变红。

        判据取「多任务 + 无 query acct → 409」——这是「body.account 没被采纳」的
        可观测形态（若被采纳就应该 200 且停掉张老师）。
        """
        r = self._c.post("/api/engine/stop", json={"account": "张老师"}, headers=self._H())
        self.assertEqual(
            r.status_code, 409,
            "body.account 必须被忽略：多任务且无 query acct 时应 409，"
            f"实际 {r.status_code} {r.text[:160]}")
        self.assertIn("不猜测账号", r.text)
        # 两个账号都没被停（证明 body 里的账号确实没生效，不是碰巧停错）
        self.assertEqual(self.reg.get_or_none("张老师").stopped, [])
        self.assertEqual(self.reg.get_or_none("小助理").stopped, [])

    def test_all_four_control_endpoints_read_query_acct(self):
        """四个端点（stop/stop-soft/pause/resume）都按 query acct 寻址。"""
        for ep in ("pause", "resume", "stop-soft"):
            r = self._c.post(f"/api/engine/{ep}?acct=%E5%BC%A0%E8%80%81%E5%B8%88", headers=self._H())
            self.assertEqual(r.status_code, 200, f"{ep}: {r.text}")
            self.assertEqual(r.json()["acct"], "张老师", ep)
        # 张老师被操作过，小助理全程未被触碰
        self.assertEqual(self.reg.get_or_none("小助理").stopped, [])

    def test_single_account_without_acct_zero_regression(self):
        """单任务（只有一个 busy）时不给 acct 仍可用 —— 单账号零回归。"""
        self.reg.drop("小助理")
        r = self._c.post("/api/engine/pause", headers=self._H())
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.reg.get_or_none("张老师").state, EngineState.PAUSED)


# ══════════════════════════════════════════════════════════════════════════
# 前端契约（grep 断言：两侧字段名/位置一致）
# ══════════════════════════════════════════════════════════════════════════

class TestFrontendContractSource(unittest.TestCase):
    """🔴 P1-1/P1-3/P1-7：前端**源码**必须与后端契约同源。

    为什么用 grep 而不是跑 vitest：本仓前端**未配置**任何测试框架
    （package.json 无 vitest/jest），硬塞一套会引入新的构建依赖。
    这里断言的是「不能出现旧形态」这一**机械判据**，属于防复发门禁：
    一旦有人把 body 形态改回来 / 再漏传 live_url，本测试立刻变红。
    """

    _FE = os.path.join(os.path.dirname(_HERE), "frontend", "src")

    def _read(self, rel: str) -> str:
        p = os.path.join(self._FE, rel)
        self.assertTrue(os.path.isfile(p), f"前端源文件缺失: {p}")
        with open(p, encoding="utf-8") as f:
            return f.read()

    def test_client_ts_uses_query_acct_not_body(self):
        src = self._read("api/client.ts")
        # ① 必须有 query 构造器
        self.assertIn('?acct=${encodeURIComponent(a)}', src,
                      "client.ts 必须按 query 传账号（契约：?acct=）")
        # ② 四个控制端点都必须走它（恰好一次，且带 account 实参）
        for ep in ("/api/engine/stop", "/api/engine/stop-soft",
                   "/api/engine/pause", "/api/engine/resume"):
            self.assertEqual(
                src.count(f'engineControlPath("{ep}", account)'), 1,
                f"{ep} 必须调用 engineControlPath(path, account)（恰好一次）")
        # ③ 不得再出现旧形态（把 account 塞进 body）
        self.assertNotIn("body.account = account", src,
                         "client.ts 不得再构造 body.account（契约是 query ?acct=）")

    def test_engine_cards_start_passes_live_url(self):
        """🔴 P1-3：多任务卡片的「开始」必须带 live_url，否则后端恒 400。"""
        src = self._read("components/live/engine-cards.tsx")
        self.assertIn("startUrlOf", src, "engine-cards 必须解析卡片上的直播间地址")
        self.assertRegex(src, r"api\.start\(\{\s*acct:\s*item\.acct,\s*live_url:\s*startUrl\s*\}\)",
                         "start 必须同时传 acct 与 live_url（缺 live_url → 后端 400）")
        self.assertNotIn("api.start({ acct: item.acct })", src,
                         "不得保留「只传 acct」的旧写法（该写法永不可用）")

    def test_tasks_page_control_passes_account(self):
        """🔴 P1-1：任务中心控制按钮必须带 runningAcct。"""
        src = self._read("components/tasks/tasks-page.tsx")
        self.assertIn("runningAcct", src)
        for fn in ("stopEngine", "pauseEngine", "resumeEngine"):
            self.assertRegex(src, r"api\s*\n?\s*\.\s*" + fn + r"\(runningAcct \|\| undefined\)",
                             f"{fn} 必须传 runningAcct（否则多任务 409）")
        self.assertNotRegex(src, r"\.stopEngine\(\)",
                            "不得保留无参 stopEngine()")

    def test_platform_ts_maps_mix_id_to_mix_slot(self):
        """🔴 P1-7：合集必须把 id 放进 mix_id 槽（不是只放 series_id）。"""
        src = self._read("api/platform.ts")
        self.assertIn("mix_id, series_id: mix_id", src,
                      "collectionSeries 必须同时给 mix_id（正确槽）与 series_id（兼容槽）")

    def test_client_ts_has_no_stale_duplicate_is_local_api_url(self):
        """防止源码被截断式改动（本批次曾误删该函数签名，此断言把结构钉住）。"""
        src = self._read("api/client.ts")
        self.assertEqual(src.count("export function isLocalApiUrl"), 1,
                         "isLocalApiUrl 必须恰好定义一次")

    def test_client_overview_has_acct_field(self):
        src = self._read("api/client.ts")
        m = re.search(r"export interface Overview \{(.*?)\n\}", src, re.S)
        self.assertIsNotNone(m, "未找到 Overview 接口")
        self.assertRegex(m.group(1), r"\bacct\?:\s*string\b",
                         "Overview 必须补 acct 字段（任务中心取运行账号）")


# ══════════════════════════════════════════════════════════════════════════
# P1-2：notify 内部直调端点必须显式传 acct
# ══════════════════════════════════════════════════════════════════════════

class TestNotifyInternalCallContract(unittest.TestCase):
    """🔴 P1-2：`notify._execute` 是**函数直调**路径，不经过 FastAPI 依赖注入。

    实测缺陷：`stop_engine(_fake_request(adm))` 漏传 acct → `acct` 落到
    `Query("")` 的默认值（FieldInfo）→ `str()` 后当账号去查 →
    404「账号『annotation=str required=False … alias=acct』没有进行中的直播任务」。
    """

    def setUp(self):
        from api import notify
        self.notify = notify
        self._prev_reg = notify._BOUND_ENGINES
        self._prev_adm = notify._BOUND_ADM

    def tearDown(self):
        self.notify._BOUND_ENGINES = self._prev_reg
        self.notify._BOUND_ADM = self._prev_adm

    def test_stop_task_by_account_works(self):
        reg = EngineRegistry(factory=lambda: _FakeAdm())
        a = reg.get("小助理"); a.state = EngineState.RUNNING; a.target_acct = "小助理"
        b = reg.get("张老师"); b.state = EngineState.RUNNING; b.target_acct = "张老师"
        self.notify.bind_engines(reg)
        self.notify._BOUND_ADM = a
        r = asyncio.run(self.notify._execute("stop_task", {"account": "张老师"}))
        self.assertNotIn("annotation=", str(r), f"acct 被 FieldInfo 污染: {r}")
        self.assertEqual(b.stopped, ["hard"], "必须停用户指定的账号")
        self.assertEqual(a.stopped, [], "不得停到别的账号")

    def test_stop_task_no_account_single_busy(self):
        """不给账号 + 单任务 → 回落该任务（零回归），且不得报 FieldInfo。"""
        reg = EngineRegistry(factory=lambda: _FakeAdm())
        a = reg.get("小助理"); a.state = EngineState.RUNNING; a.target_acct = "小助理"
        self.notify.bind_engines(reg)
        self.notify._BOUND_ADM = a
        r = asyncio.run(self.notify._execute("stop_task", {}))
        self.assertNotIn("annotation=", str(r), f"acct 被 FieldInfo 污染: {r}")
        self.assertEqual(a.stopped, ["hard"])

    def test_stop_task_no_account_ambiguous_409_message(self):
        """不给账号 + 多任务 → 显式回话失败（不猜账号）。"""
        reg = EngineRegistry(factory=lambda: _FakeAdm())
        for n in ("小助理", "张老师"):
            e = reg.get(n); e.state = EngineState.RUNNING; e.target_acct = n
        self.notify.bind_engines(reg)
        self.notify._BOUND_ADM = reg.get("小助理")
        r = asyncio.run(self.notify._execute("stop_task", {}))
        self.assertIn("请指定账号", str(r), r)

    def test_direct_call_without_acct_does_not_use_fieldinfo(self):
        """端点被直接调用且漏传 acct 时，`_resolve_adm` 必须把 FieldInfo 归一为空。"""
        reg = EngineRegistry(factory=lambda: _FakeAdm())
        a = reg.get("小助理"); a.state = EngineState.RUNNING; a.target_acct = "小助理"
        req = _Req(a, reg)
        # 注意：这里**故意**直接调函数且不传 acct —— 模拟旧 notify 写法
        r = asyncio.run(engine_api.stop_engine(req))
        self.assertEqual(r["acct"], "小助理", f"不得把 FieldInfo 当账号: {r}")
        self.assertEqual(a.stopped, ["hard"])


# ══════════════════════════════════════════════════════════════════════════
# P1-3：/start 缺 live_url → 400（后端判据 + 前端已带 live_url）
# ══════════════════════════════════════════════════════════════════════════

class TestEngineStartRequiresLiveUrl(unittest.TestCase):
    def test_empty_live_url_is_400(self):
        adm = _FakeAdm()
        req = _Req(adm, EngineRegistry(factory=lambda: _FakeAdm()))
        with self.assertRaises(HTTPException) as cm:
            asyncio.run(engine_api.start_engine(req, _Cfg("   ", "小助理")))
        self.assertEqual(cm.exception.status_code, 400)

    def test_start_with_live_url_succeeds(self):
        """带上 live_url 就能真启动（前端修好后走的就是这条路径）。"""
        reg = EngineRegistry(factory=lambda: _FakeAdm())
        adm = reg.get("小助理")
        req = _Req(adm, reg)
        r = asyncio.run(engine_api.start_engine(
            req, _Cfg("https://live.douyin.com/123456", "小助理")))
        self.assertTrue(r["ok"])
        self.assertEqual(adm.start_calls, 1, "start 必须真被调用")
        self.assertEqual(adm.live_url, "https://live.douyin.com/123456")


# ══════════════════════════════════════════════════════════════════════════
# P1-6：busy_keys 终态判据
# ══════════════════════════════════════════════════════════════════════════

class TestBusyKeysTerminalStates(unittest.TestCase):
    def test_stopped_is_not_busy(self):
        reg = EngineRegistry(factory=lambda: _FakeAdm())
        x = reg.get("小助理"); x.state = EngineState.STOPPED
        y = reg.get("张老师"); y.state = EngineState.STOPPED
        self.assertEqual(reg.busy_keys(), [],
                         "两个 STOPPED 引擎不得算忙（否则无 acct 的 /start 恒 409）")

    def test_error_is_not_busy(self):
        # 说明：`models.enums.EngineState` 目前没有 ERROR 成员（前端 STATE_COLORS 有），
        # 但 busy_keys 的判据必须是**白名单**，任何非在跑状态（含未来的 error）都不得算忙。
        reg = EngineRegistry(factory=lambda: _FakeAdm())
        x = reg.get("小助理"); x.state = "error"
        self.assertEqual(reg.busy_keys(), [], "非在跑状态（error）不得算忙")

    def test_idle_is_not_busy(self):
        reg = EngineRegistry(factory=lambda: _FakeAdm())
        reg.get("小助理")
        self.assertEqual(reg.busy_keys(), [])

    def test_running_states_are_busy(self):
        reg = EngineRegistry(factory=lambda: _FakeAdm())
        for st in (EngineState.STARTING, EngineState.RUNNING,
                   EngineState.PAUSED, EngineState.STOPPING):
            reg.drop("小助理")
            e = reg.get("小助理"); e.state = st
            self.assertEqual(reg.busy_keys(), ["小助理"], f"{st} 应算忙")

    def test_start_without_acct_after_all_stopped(self):
        """🔴 复现原缺陷：两引擎都 STOPPED → 不带 acct 的 /start 必须能启动。"""
        reg = EngineRegistry(factory=lambda: _FakeAdm())
        reg.get("小助理").state = EngineState.STOPPED
        reg.get("张老师").state = EngineState.STOPPED
        anonymous = reg.get(ANONYMOUS_KEY)
        req = _Req(anonymous, reg)
        r = asyncio.run(engine_api.start_engine(
            req, _Cfg("https://live.douyin.com/123456", None)))
        self.assertTrue(r["ok"], f"双引擎停止后无 acct 的 /start 必须可用: {r}")
        self.assertEqual(anonymous.start_calls, 1)

    def test_stale_stopped_instances_do_not_block_new_account_start(self):
        """有历史 STOPPED 实例时，新账号 /start 仍必须成功。"""
        reg = EngineRegistry(factory=lambda: _FakeAdm())
        reg.get("小助理").state = EngineState.STOPPED
        reg.get("张老师").state = EngineState.STOPPED
        req = _Req(reg.get("小助理"), reg)
        r = asyncio.run(engine_api.start_engine(
            req, _Cfg("https://live.douyin.com/1", "新账号")))
        self.assertTrue(r["ok"], r)
        self.assertEqual(reg.get_or_none("新账号").start_calls, 1)


# ══════════════════════════════════════════════════════════════════════════
# high-1：/start 锁键必须与实例同源
# ══════════════════════════════════════════════════════════════════════════

class TestStartLockKeyMatchesInstance(unittest.TestCase):
    def test_lock_key_follows_resolved_instance(self):
        """🔴 high-1：acct 空 + 恰一个 busy（账号「小助理」）→ 锁键必须是「小助理」，
        不能是 anonymous（否则锁与实例分叉，check-then-act 竞态失守）。"""
        reg = EngineRegistry(factory=lambda: _FakeAdm())
        only = reg.get("小助理")
        only.state = EngineState.RUNNING          # 唯一 busy
        req = _Req(only, reg)
        adm = engine_api._resolve_adm(req, None)   # acct 空 → 单任务回落
        self.assertIs(adm, only, "单任务回落必须拿到那个实例本身")
        self.assertEqual(engine_api._key_of(reg, adm, None), "小助理",
                         "锁键必须跟随实例（旧写法会取 ANONYMOUS_KEY → 锁与实例分叉）")
        self.assertNotEqual(engine_api._key_of(reg, adm, None), ANONYMOUS_KEY)

    def test_key_of_prefers_instance_key_over_provided_acct(self):
        """给定实例时，锁键取实例在表中的键（同源），而不是调用方口述的 acct。"""
        reg = EngineRegistry(factory=lambda: _FakeAdm())
        inst = reg.get("张老师")
        req = _Req(inst, reg)
        adm = engine_api._resolve_adm(req, "张老师", create=False)
        self.assertIs(adm, inst)
        self.assertEqual(engine_api._key_of(reg, adm, "张老师"), "张老师")
        # 口述错账号也不会把锁打到错地方
        self.assertEqual(engine_api._key_of(reg, adm, "小助理"), "张老师")

    def test_start_lock_is_per_instance_key(self):
        """并发 /start 同一账号时，锁互斥（第二次必须 409，不得双启动）。"""
        reg = EngineRegistry(factory=lambda: _FakeAdm())
        adm = reg.get("小助理")
        req = _Req(adm, reg)

        async def _two():
            r1 = await engine_api.start_engine(req, _Cfg("https://live.douyin.com/1", "小助理"))
            with self.assertRaises(HTTPException) as cm:
                await engine_api.start_engine(req, _Cfg("https://live.douyin.com/1", "小助理"))
            return r1, cm.exception

        r1, exc = asyncio.run(_two())
        self.assertTrue(r1["ok"])
        self.assertEqual(exc.status_code, 409)
        self.assertEqual(adm.start_calls, 1, "同账号不得被启动两次")


# ══════════════════════════════════════════════════════════════════════════
# P1-7：合集分流（is_serial_mix）
# ══════════════════════════════════════════════════════════════════════════

class _FakePlatformApi:
    """替身 DouyinAPI：记录被调用的方法与参数，返回可编排的响应。"""

    douyin_url = "https://www.douyin.com"

    def __init__(self, mix_resp=None, series_resp=None):
        self.calls: list[tuple[str, str]] = []
        self.mix_resp = mix_resp if mix_resp is not None else {
            "status_code": 0, "aweme_list": [{"aweme_id": "1"}], "has_more": False}
        self.series_resp = series_resp if series_resp is not None else {
            "status_code": 5, "status_msg": "参数不合法", "aweme_list": None}

    def get_mix_aweme(self, auth, mix_id, cursor="0", count="20", **kw):
        self.calls.append(("mix", str(mix_id)))
        return self.mix_resp

    def get_series_aweme(self, auth, series_id, cursor="0", count="20", **kw):
        self.calls.append(("series", str(series_id)))
        return self.series_resp


class TestCollectionSeriesDispatch(unittest.TestCase):
    """🔴 P1-7：`/collection/series` 必须按 `is_serial_mix` 分流（普通合集用 mix_id）。

    修复前：只调 `get_series_aweme(series_id=mix_id)` → sc=5 → 前端恒空。
    """

    def _call(self, body: dict, api_fake: _FakePlatformApi):
        from api import platform as platform_api
        prev_auth, prev_api = platform_api._auth_for, platform_api._api
        platform_api._auth_for = lambda acct: object()
        platform_api._api = lambda: api_fake
        try:
            return asyncio.run(platform_api.collection_series(
                platform_api.SeriesAwemeReq(**body)))
        finally:
            platform_api._auth_for, platform_api._api = prev_auth, prev_api

    def test_normal_mix_goes_to_mix_api(self):
        """普通合集（is_serial_mix 未知）→ 先打 mix 接口，命中即止。"""
        api_fake = _FakePlatformApi()
        r = self._call({"account": "a", "series_id": "mix123"}, api_fake)
        self.assertTrue(r["ok"])
        self.assertEqual(api_fake.calls, [("mix", "mix123")],
                         "普通合集必须优先走 get_mix_aweme（参数 mix_id）")
        self.assertEqual(r["via"], "mix")
        self.assertEqual(len(r["items"]), 1)

    def test_mix_id_field_takes_priority(self):
        api_fake = _FakePlatformApi()
        r = self._call({"account": "a", "mix_id": "M9"}, api_fake)
        self.assertEqual(api_fake.calls, [("mix", "M9")])
        self.assertEqual(r["via"], "mix")

    def test_serial_mix_explicit_goes_to_series(self):
        """显式 is_serial_mix=1（短剧）→ 走 series 接口。"""
        api_fake = _FakePlatformApi(
            series_resp={"status_code": 0, "aweme_list": [{"aweme_id": "s"}], "has_more": False})
        r = self._call({"account": "a", "series_id": "S1", "is_serial_mix": 1}, api_fake)
        self.assertEqual(api_fake.calls, [("series", "S1")])
        self.assertEqual(r["via"], "series")
        self.assertEqual(len(r["items"]), 1)

    def test_fallback_when_mix_returns_sc5(self):
        """上游对 mix 接口回 sc=5（说明其实是短剧）→ 自动回退 series。"""
        api_fake = _FakePlatformApi(
            mix_resp={"status_code": 5, "status_msg": "参数不合法", "aweme_list": None},
            series_resp={"status_code": 0, "aweme_list": [{"aweme_id": "s"}], "has_more": False})
        r = self._call({"account": "a", "series_id": "X"}, api_fake)
        self.assertEqual(api_fake.calls, [("mix", "X"), ("series", "X")],
                         "sc!=0 时必须回退另一条接口")
        self.assertEqual(r["via"], "series")
        self.assertEqual(len(r["items"]), 1)

    def test_both_fail_returns_empty_with_status_code(self):
        """两条都失败 → 如实返回空 + status_code（不得假装成功）。"""
        api_fake = _FakePlatformApi(
            mix_resp={"status_code": 5, "aweme_list": None},
            series_resp={"status_code": 5, "aweme_list": None})
        r = self._call({"account": "a", "series_id": "X"}, api_fake)
        self.assertTrue(r["ok"])          # 端点本身可达
        self.assertEqual(r["items"], [])
        self.assertEqual(r["status_code"], 5)
        self.assertEqual(len(api_fake.calls), 2)

    def test_missing_id_is_400(self):
        from api import platform as platform_api
        with self.assertRaises(HTTPException) as cm:
            asyncio.run(platform_api.collection_series(
                platform_api.SeriesAwemeReq(account="a")))
        self.assertEqual(cm.exception.status_code, 400)


# ══════════════════════════════════════════════════════════════════════════
# P4：死代码 / 未用 import 的机械门禁
# ══════════════════════════════════════════════════════════════════════════

class TestDeadCodeRemoved(unittest.TestCase):
    def test_all_running_keys_removed(self):
        src = open(os.path.join(_HERE, "services", "engine_registry.py"),
                   encoding="utf-8").read()
        self.assertNotIn("all_running_keys", src,
                         "P4：无调用点的 all_running_keys 必须删除")

    def test_no_unused_module_imports(self):
        """用 AST 扫本批次 5 个后端文件，断言没有「导入但未使用」的名字。"""
        import ast
        files = [
            ("api", "engine.py"), ("api", "notify.py"),
            ("services", "engine_registry.py"), ("api", "platform.py"),
            ("dy_apis", "client_collection.py"),
        ]
        for pkg, name in files:
            path = os.path.join(_HERE, pkg, name)
            with open(path, encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=path)
            imported: dict[str, int] = {}
            for n in ast.walk(tree):
                if isinstance(n, ast.Import):
                    for a in n.names:
                        imported[(a.asname or a.name).split(".")[0]] = n.lineno
                elif isinstance(n, ast.ImportFrom):
                    if n.module == "__future__":
                        continue
                    for a in n.names:
                        if a.name != "*":
                            imported[a.asname or a.name] = n.lineno
            used: set[str] = set()
            for n in ast.walk(tree):
                if isinstance(n, ast.Name):
                    used.add(n.id)
                if isinstance(n, ast.Attribute):
                    cur = n
                    while isinstance(cur, ast.Attribute):
                        cur = cur.value
                    if isinstance(cur, ast.Name):
                        used.add(cur.id)
            unused = {k: v for k, v in imported.items() if k not in used}
            self.assertEqual(unused, {}, f"{pkg}/{name} 存在未使用 import: {unused}")

    def test_notify_execute_has_no_unused_local_request_import(self):
        src = open(os.path.join(_HERE, "api", "notify.py"), encoding="utf-8").read()
        # `_fake_request` 里的 `from fastapi import Request as _Req` 是**在用**的，
        # 只有 `_execute` 里那行裸 `from fastapi import Request`（未使用）该删。
        self.assertNotIn("    from fastapi import Request\n", src,
                         "P4：_execute 内未使用的 `from fastapi import Request` 必须删除")


if __name__ == "__main__":
    unittest.main(verbosity=2)
