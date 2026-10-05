# -*- coding: utf-8 -*-
"""软停止卡死诊断：软停止后 dispatch.pending 是否真的清空。

判据（从日志推断的两条互斥假设）：
  H1：pending 残留 → `wait_done()` 的 `while ... or self.pending` 永真 → 卡 STOPPING
  H2：pending 已空，但 `_wait_dispatch_done` 从未被调用/被取消 → 无人推进状态

本脚本不发网络、不启浏览器，纯状态机推演。
"""
from __future__ import annotations

import asyncio
import os
import sys

BACKEND = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "backend"))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)


async def scenario() -> None:
    from core.dispatch import DispatchCenter

    class _Auth:
        account_name = ""   # 空 → 不进入 dm_dispatch 分支，走 send_target_async

    dc = DispatchCenter(auth=_Auth(), max_target=100, delay_range=(0, 0),
                        interval=0.0, pick_dm_message=lambda: "文案")
    await dc.start()

    # 捕获一个目标（模拟「胡仕军」入队）
    t = {"user_id": "123", "nickname": "胡仕军", "comment": "x"}
    dc.submit(t)
    print(f"入队后: queue={dc.queue_size()} pending={list(dc.pending)}")

    # 模拟软停止（此时那条记录可能还在队列里没被消费）
    dc.stop_soft()
    print(f"软停止后: queue={dc.queue_size()} pending={list(dc.pending)}")

    # 关键：wait_done 会不会返回？
    try:
        await asyncio.wait_for(dc.wait_done(), timeout=3.0)
        print("wait_done: 返回了 ✅（3s 内）")
    except asyncio.TimeoutError:
        print(f"wait_done: **3s 超时未返回** ❌  queue={dc.queue_size()} pending={list(dc.pending)}")

    dc._stopped = True
    await asyncio.sleep(0.2)


async def scenario2() -> None:
    """对照：软停止时队列已空、pending 已空 —— wait_done 应立刻返回。"""
    from core.dispatch import DispatchCenter

    class _Auth:
        account_name = ""

    dc = DispatchCenter(auth=_Auth(), max_target=100, delay_range=(0, 0),
                        interval=0.0, pick_dm_message=lambda: "文案")
    dc.stop_soft()
    print(f"\n[对照] 空队列软停止: queue={dc.queue_size()} pending={list(dc.pending)}")
    try:
        await asyncio.wait_for(dc.wait_done(), timeout=2.0)
        print("wait_done: 立刻返回 ✅")
    except asyncio.TimeoutError:
        print("wait_done: 超时 ❌")


def main() -> int:
    root = os.path.abspath(os.environ.get("FLOWCAP_APP_ROOT") or r"C:\temp\flowcap_design")
    if root == os.path.abspath(r"C:\temp\flowcap_test"):
        sys.exit("[环境门禁] 拒绝在主分支环境运行")
    os.environ["FLOWCAP_APP_ROOT"] = root
    os.chdir(BACKEND)
    print(f"数据根 = {root}\n")
    asyncio.run(scenario())
    asyncio.run(scenario2())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
