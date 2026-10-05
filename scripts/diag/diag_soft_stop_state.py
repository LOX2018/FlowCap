# -*- coding: utf-8 -*-
"""软停止卡死 —— 实测埋点定论（不打补丁，只观测）。

复现 11:20:49 那次软停止的完整状态机流转，每一步 dump 真实字段值：
  - stop() 各分支的判定值
  - _run finally 落入哪个分支
  - 是否有人创建 _wait_dispatch_done
  - _listen_line_active() 的真实返回值
  - _on_dispatch_idle 是否被触发、是否推进状态
"""
from __future__ import annotations

import asyncio
import collections
import os
import sys

BACKEND = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "backend"))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)


async def main() -> int:
    from core.auto_dm import AutoDM
    from core.dispatch import DispatchCenter
    from core.live_hook import LiveChatHook
    from models.enums import EngineState

    log: list[str] = []

    def dump(tag: str, adm) -> None:
        live = adm.live
        line = (f"{tag:28s} state={adm.state.value:9s} msg={adm.status_msg!r} "
                f"q={adm.dispatch.queue_size() if adm.dispatch else '-'} "
                f"pending={len(adm.dispatch.pending) if adm.dispatch else '-'} "
                f"ws_alive={getattr(live, '_ws_alive', None)} "
                f"ws={getattr(live, 'ws', None) is not None} "
                f"should_stop={getattr(live, '_should_stop', None)} "
                f"line_active={adm._listen_line_active() if live else None}")
        print(line)
        log.append(line)

    # ---- 构造真实对象 ----
    class _Auth:
        account_name = ""

    adm = AutoDM.__new__(AutoDM)
    adm.state = EngineState.RUNNING
    adm.status_msg = "监听中 999"
    adm.limit = 100
    adm.interval = 0.0
    adm.delay_range = (0, 0)
    adm.room_title = ""
    adm.sent_count = 0
    adm.live_url = "999"
    adm.live_id = "999"
    adm.dm_template = []
    adm._acct = None
    adm._task_history_id = None
    # stop() 会调 _stop_event.set() / self._task —— 替身须补齐
    adm._stop_event = asyncio.Event()
    adm._task = None

    dc = DispatchCenter(auth=_Auth(), max_target=100, delay_range=(50, 50),
                        interval=0.0, pick_dm_message=lambda: "文案")
    dc.enable_send = True          # 真入队（复现「胡仕军」在队列中的状态）
    adm.dispatch = dc

    live = LiveChatHook.__new__(LiveChatHook)
    live._should_stop = False
    live._ws_alive = True          # 假设 WS 当时是连着的
    live.ws = object()             # 有句柄
    live.dispatch = dc
    live.controller = adm
    live.feed = collections.deque(maxlen=500)
    live.room_stats = {"online": 0, "likes": 0, "total_user": 0, "display": ""}
    live.heat_series = collections.deque(maxlen=180)
    live._hb_thread = None
    live._hb_stop = __import__("threading").Event()
    adm.live = live

    dump("① 初始（RUNNING）", adm)

    await dc.start()
    # 模拟「胡仕军」已入队但尚未发送
    dc.submit({"user_id": "123", "nickname": "胡仕军", "comment": "x"})
    dump("② 捕获一条（未发）", adm)

    # ---- 调真实的 stop(hard=False) ----
    print("\n--- 调用 adm.stop(hard=False) ---")
    await adm.stop(hard=False)
    dump("③ stop() 返回后", adm)

    # 给后台任务一点时间（_run finally / _wait_dispatch_done / on_idle）
    # 观察 1~60s：看 50s（delay 窗口）过去后 pending 是否被清理、状态是否推进
    for i in range(24):
        await asyncio.sleep(2.5)
        dump(f"④ +{(i+1)*2.5:.1f}s", adm)

    print("\n=== 判定 ===")
    final = adm.state.value
    if final == "stopped":
        print("✅ 软停止最终落到 stopped（未复现卡死）")
    else:
        print(f"❌ 软停止后卡在 {final}（复现 11:20:49 的现象）")
        print("   期望：stopped（队列已空，应正常收尾）")

    dc._stopped = True
    return 0 if final == "stopped" else 1


if __name__ == "__main__":
    root = os.path.abspath(os.environ.get("FLOWCAP_APP_ROOT") or r"C:\temp\flowcap_design")
    if root == os.path.abspath(r"C:\temp\flowcap_test"):
        sys.exit("[环境门禁] 拒绝在主分支环境运行")
    os.environ["FLOWCAP_APP_ROOT"] = root
    os.chdir(BACKEND)
    print(f"数据根 = {root}\n")
    raise SystemExit(asyncio.run(main()))
