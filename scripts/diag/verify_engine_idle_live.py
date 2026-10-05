# -*- coding: utf-8 -*-
"""ENG-015 实机探针：在**真实部署环境**里驱动真实 LiveChatHook + AutoDM 状态机。

与单测的区别：本脚本跑在 `C:\\temp\\flowcap_design` 环境下、import 的是
**部署目录 sidecar 同源的源码**（backend/），用它验证两件事：

1. 握手窗口（`_ws_alive=True`、`ws=None`）内触发 on_idle —— 引擎**不得**被收尾；
2. 监听线真停止后触发 on_idle —— 引擎**应当**正常收尾（防矫枉过正）。

不发任何网络请求、不启浏览器、不碰凭证（遵守风控红线）。

用法：
    set FLOWCAP_APP_ROOT=C:\\temp\\flowcap_design
    python scripts/diag/verify_engine_idle_live.py
"""
from __future__ import annotations

import asyncio
import collections
import json
import os
import sys

BACKEND = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "backend"))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)


def main() -> int:
    root = os.path.abspath(os.environ.get("FLOWCAP_APP_ROOT") or r"C:\temp\flowcap_design")
    forbidden = os.path.abspath(r"C:\temp\flowcap_test")
    if root == forbidden:
        sys.exit("[环境门禁] FLOWCAP_APP_ROOT 指向主分支环境，拒绝运行")
    os.environ["FLOWCAP_APP_ROOT"] = root
    os.chdir(BACKEND)
    print(f"数据根(FLOWCAP_APP_ROOT) = {root}")

    from core.auto_dm import AutoDM
    from core.live_hook import LiveChatHook
    from models.enums import EngineState

    class FakeDispatch:
        """只提供收尾路径需要的接口，不发送任何私信。"""
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

    def mk_live(ws_alive: bool, ws: object | None, should_stop: bool) -> LiveChatHook:
        """真实 LiveChatHook 实例（不发起连接），只承载存活判据所需状态。"""
        live = LiveChatHook.__new__(LiveChatHook)
        live._should_stop = should_stop
        live._ws_alive = ws_alive
        live.ws = ws
        live.dispatch = None
        live.controller = None
        live.room_status = None
        live.feed = collections.deque(maxlen=500)
        live.room_stats = {"online": 0, "likes": 0, "total_user": 0, "display": ""}
        live.heat_series = collections.deque(maxlen=180)
        return live

    def mk_adm(live) -> AutoDM:
        adm = AutoDM.__new__(AutoDM)
        adm.state = EngineState.RUNNING
        adm.live = live
        adm.status_msg = ""
        adm.limit = 3
        adm.interval = 60.0
        adm.delay_range = (40, 65)
        adm.room_title = ""
        adm.sent_count = 0
        adm.live_url = ""
        adm.live_id = ""
        adm.dm_template = []
        adm._acct = None
        adm._task_history_id = None
        adm.dispatch = FakeDispatch()
        return adm

    results: dict[str, object] = {}

    # ---- ① 握手窗口（事故现场）：start_ws 已发起，ws 句柄尚未赋值 ----
    live = mk_live(ws_alive=True, ws=None, should_stop=False)
    adm = mk_adm(live)
    results["handshake_listen_active"] = adm._listen_line_active()
    results["handshake_snapshot_alive"] = adm.snapshot()["live"]["alive"]
    asyncio.run(adm._on_dispatch_idle())
    results["handshake_state_after_idle"] = adm.state.value
    results["handshake_detached"] = adm.dispatch._detached

    # ---- ② 已连上 ----
    live2 = mk_live(ws_alive=True, ws=object(), should_stop=False)
    adm2 = mk_adm(live2)
    asyncio.run(adm2._on_dispatch_idle())
    results["connected_state_after_idle"] = adm2.state.value

    # ---- ③ 重连空隙（ws 已断、未置停）仍属活跃 ----
    live3 = mk_live(ws_alive=True, ws=None, should_stop=False)
    adm3 = mk_adm(live3)
    asyncio.run(adm3._on_dispatch_idle())
    results["reconnecting_state_after_idle"] = adm3.state.value

    # ---- ④ 真停止：应正常收尾 ----
    live4 = mk_live(ws_alive=False, ws=object(), should_stop=True)
    adm4 = mk_adm(live4)
    asyncio.run(adm4._on_dispatch_idle())
    results["stopped_state_after_idle"] = adm4.state.value
    results["stopped_detached"] = adm4.dispatch._detached

    # ---- ⑤ 从未发起连接：应正常收尾 ----
    live5 = mk_live(ws_alive=False, ws=None, should_stop=False)
    adm5 = mk_adm(live5)
    asyncio.run(adm5._on_dispatch_idle())
    results["never_connected_state_after_idle"] = adm5.state.value

    print(json.dumps(results, ensure_ascii=False, indent=2))

    checks = [
        ("握手窗口内监听线判为存活", results["handshake_listen_active"] is True),
        ("握手窗口内 snapshot alive=True", results["handshake_snapshot_alive"] is True),
        ("握手窗口内不被收尾", results["handshake_state_after_idle"] == "running"),
        ("握手窗口内不解绑回调", results["handshake_detached"] is False),
        ("已连上不被收尾", results["connected_state_after_idle"] == "running"),
        ("重连空隙不被收尾", results["reconnecting_state_after_idle"] == "running"),
        ("真停止能收尾", results["stopped_state_after_idle"] == "stopped"),
        ("真停止后解绑回调", results["stopped_detached"] is True),
        ("从未连接能收尾", results["never_connected_state_after_idle"] == "stopped"),
    ]
    print()
    ok = True
    for name, passed in checks:
        print(("  ✅ " if passed else "  ❌ ") + name)
        ok = ok and passed
    print()
    print("结论：" + ("全部通过 —— 握手窗口误收尾已修复" if ok else "存在失败项"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
