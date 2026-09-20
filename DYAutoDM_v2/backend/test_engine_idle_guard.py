# coding=utf-8
"""ENG-015 回归：WS 握手窗口内不得误收尾引擎（前端进程控件失效案）。

## 事故回放（实机日志 src-tauri/logs/run_20260921_010907.log）

    01:21:41  已在直播，直接开始监听                  ← 进入 RUNNING，开始建 WS
    01:21:41  [引擎] 私信收尾 …（状态=finished）        ← ★ 误收尾
    01:21:42  [live-ws] 连接已建立                    ← WS 一秒后才连上
    01:22:07  [调度] 已捕获「u点高冷」…                ← 弹幕照常进来
    01:23:55  [live-ai] 已生成文案 …                  ← AI 照常生成

用户看到的现象：直播流与 AI 生成都正常，但直播监听页的引擎状态徽章与
「暂停 / 继续 / 停止监听」控件全部失效 —— 因为 `engineState` 被写回
`stopped`，前端 3s 轮询读到的永远是「等待启动」。

## 根因（两处连坐）

1. `DispatchCenter._loop` 的 `on_idle` 触发后自行 `self.on_idle = None`
   —— 把「只触发一次」这一**上层状态机语义**硬编码进调度器。而队列空在
   **启动握手窗口内是必然的正常中间态**，这次唯一的触发机会被握手窗口吃掉。
2. `AutoDM._on_dispatch_idle` 的存活判据是 `self.live.ws is not None`，
   而 `ws` 是 `run_forever()` 内才赋值的，**握手窗口内恒为 None** →
   把正在启动的监听判成「已死」→ 落 STOPPED。

## 本测试守住的不变式

- 监听线已发起连接、尚未连上时（握手窗口）：`on_idle` 触发**不得**收尾；
- 监听线确实停止后队列发空：应当正常收尾（不能矫枉过正把真停止也卡住）；
- 收尾判据在 `snapshot()` / `/api/live/stream` / `_on_dispatch_idle` 三处同源。

环境：纯单测，不启进程、不碰浏览器、不发任何网络请求（遵守风控红线）。
"""
from __future__ import annotations

import asyncio
import os
import sys
import unittest

BACKEND = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from core.auto_dm import AutoDM  # noqa: E402
from models.enums import EngineState  # noqa: E402
from dy_live.server import DouyinLive  # noqa: E402


class _FakeLive(DouyinLive):
    """最小 LiveChatHook 替身：只承载存活判据需要的字段，不发任何网络请求。"""

    def __init__(self, ws_alive: bool = False, ws: object | None = None,
                 should_stop: bool = False) -> None:
        super().__init__("0", None)
        self._ws_alive = ws_alive
        self.ws = ws
        self._should_stop = should_stop

    def feed_snapshot(self, limit: int = 50):
        return []

    def heat_snapshot(self):
        return []


class _FakeDispatch:
    """最小调度中心替身：只暴露 `_listen_line_active` / 收尾路径需要的接口。"""

    def __init__(self) -> None:
        self.on_idle = None
        self._detached = False

    def detach_on_idle(self) -> None:
        self._detached = True
        self.on_idle = None

    def records_list(self):
        return []

    def queue_size(self) -> int:
        return 0


def _mk_adm(live: _FakeLive | None, state: EngineState = EngineState.RUNNING) -> AutoDM:
    adm = AutoDM.__new__(AutoDM)          # 绕过 __init__（它会读 settings/建子系统）
    adm.state = state
    adm.live = live
    adm.status_msg = ""
    adm.limit = 3
    adm.interval = 60.0
    adm.delay_range = (40, 65)
    adm.room_title = ""
    adm.sent_count = 0
    adm.dispatch = _FakeDispatch()
    adm._task_history_id = None           # 让 _finish_history_task 安全 no-op
    # snapshot() 会读这些字段（__init__ 里由 settings 填充，此处手工对齐）
    adm.live_url = ""
    adm.live_id = ""
    adm.dm_template = []
    adm._acct = None
    return adm


class TestHandshakeWindowNoPrematureFinish(unittest.TestCase):
    """核心不变式：握手窗口内（_ws_alive=True，ws 仍为 None）不得收尾。"""

    def test_handshake_window_not_finished(self):
        # 事故现场：监听线已进入连接流程，但 ws 句柄还没赋值
        live = _FakeLive(ws_alive=True, ws=None, should_stop=False)
        adm = _mk_adm(live, EngineState.RUNNING)

        asyncio.run(adm._on_dispatch_idle())

        self.assertEqual(adm.state, EngineState.RUNNING,
                         "握手窗口内不得把引擎收尾成 STOPPED（ENG-015 根因）")
        self.assertFalse(adm.dispatch._detached,
                         "未收尾时不得解绑 on_idle（否则真收尾时无回调可用）")

    def test_ws_connected_not_finished(self):
        live = _FakeLive(ws_alive=True, ws=object(), should_stop=False)
        adm = _mk_adm(live, EngineState.RUNNING)
        asyncio.run(adm._on_dispatch_idle())
        self.assertEqual(adm.state, EngineState.RUNNING)

    def test_reconnecting_gap_not_finished(self):
        """重连空隙（ws 已断、_should_stop 未置）仍属活跃生命周期。"""
        live = _FakeLive(ws_alive=True, ws=None, should_stop=False)
        adm = _mk_adm(live, EngineState.RUNNING)
        asyncio.run(adm._on_dispatch_idle())
        self.assertEqual(adm.state, EngineState.RUNNING)


class TestRealStopStillFinishes(unittest.TestCase):
    """反向不变式：真停止后必须仍能收尾（不能矫枉过正把真停止也卡住）。"""

    def test_stopped_line_finishes(self):
        live = _FakeLive(ws_alive=False, ws=object(), should_stop=True)
        adm = _mk_adm(live, EngineState.RUNNING)
        asyncio.run(adm._on_dispatch_idle())
        self.assertEqual(adm.state, EngineState.STOPPED)
        self.assertTrue(adm.dispatch._detached,
                        "收尾后必须解绑 on_idle（ENG-015：调度器不再自行解绑）")

    def test_no_live_object_finishes(self):
        adm = _mk_adm(None, EngineState.RUNNING)
        asyncio.run(adm._on_dispatch_idle())
        self.assertEqual(adm.state, EngineState.STOPPED)

    def test_never_connected_finishes(self):
        """从未发起连接（_ws_alive=False 且 ws=None）→ 确属已死，应收尾。"""
        live = _FakeLive(ws_alive=False, ws=None, should_stop=False)
        adm = _mk_adm(live, EngineState.RUNNING)
        asyncio.run(adm._on_dispatch_idle())
        self.assertEqual(adm.state, EngineState.STOPPED)


class TestAliveCriterionShared(unittest.TestCase):
    """收尾判据与前端数据源（`snapshot`）必须同源，否则两边给出相反答案。"""

    def test_snapshot_alive_matches_listen_line(self):
        live = _FakeLive(ws_alive=True, ws=None, should_stop=False)
        adm = _mk_adm(live, EngineState.RUNNING)
        snap = adm.snapshot()
        self.assertTrue(snap["live"]["alive"],
                        "握手窗口内 snapshot 不得报 alive=False（前端会显示等待启动）")
        self.assertTrue(snap["live"]["listening"])

    def test_snapshot_dead_when_stopped(self):
        live = _FakeLive(ws_alive=False, ws=object(), should_stop=True)
        adm = _mk_adm(live, EngineState.STOPPED)
        self.assertFalse(adm.snapshot()["live"]["alive"])


class TestDispatchKeepsCallback(unittest.TestCase):
    """调度器不得自行解绑 on_idle（只通知事实，解绑权在上层）。"""

    def test_loop_does_not_self_detach(self):
        from core import dispatch as _d

        dc = _d.DispatchCenter(auth=None, max_target=1, delay_range=(0, 0), interval=0.0)
        calls: list[int] = []

        async def _on_idle():
            calls.append(1)

        dc.on_idle = _on_idle

        async def _scenario():
            task = asyncio.create_task(dc._loop())
            await asyncio.sleep(2.6)      # 跨过至少两次 1s 的队列空超时
            dc._stopped = True
            await asyncio.sleep(0.1)
            task.cancel()

        asyncio.run(_scenario())
        self.assertGreaterEqual(len(calls), 2,
                                "调度器自行解绑了 on_idle（ENG-015 缺陷复发）："
                                "握手窗口会吃掉唯一一次触发机会")
        self.assertIsNotNone(dc.on_idle, "解绑权在 AutoDM，调度器不得自行置 None")

    def test_detach_on_idle_exists(self):
        from core import dispatch as _d

        dc = _d.DispatchCenter(auth=None, max_target=1, delay_range=(0, 0), interval=0.0)
        dc.on_idle = lambda: None
        dc.detach_on_idle()
        self.assertIsNone(dc.on_idle)


if __name__ == "__main__":
    unittest.main(verbosity=2)
