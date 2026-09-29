# -*- coding: utf-8 -*-
"""E-1 最小复现/回归：熔断跳闸后 scan_fail_count 是否归零（行为级判据）。

被测文件 bcc_login.py 的 run_keepalive 里有 4 个熔断跳闸点（写 breaker_until）：
  583（uid 漂移后自动刷新连续失败） / 633（页面重激活连续失败）
  685（已确认身份漂移→立即熔断）   / 701（通用：探活+scan_login 连续失败）

判据（不读私有局部变量，全靠时钟推进 + 观察 BCC-024 跳闸时刻）：
  · 每次跳闸 ⇒ scan_fail_count 归零 ⇒ 30 分钟期满后必须**再连续 2 次失败**才再跳。
  · 期望：4 个场景的第 2 次跳闸都落在 tick4（旧逻辑落在 tick3）。

用法：py -3.14 repro_e1_breaker_reset.py <backend_dir> <bcc_login.py 路径>
"""
from __future__ import annotations

import importlib.util
import os
import sys
import types

from loguru import logger

BACKEND = sys.argv[1]
TARGET = sys.argv[2]
sys.path.insert(0, BACKEND)

# 隔离：services.app_config / env_baseline 全部桩化，绝不触真配置/真数据根/网络
import services  # noqa: E402

_stub_appcfg = types.ModuleType("services.app_config")
_stub_appcfg.get = lambda *a, **k: None
sys.modules["services.app_config"] = _stub_appcfg

_stub_env = types.ModuleType("services.env_baseline")
_stub_env.compare_baseline = lambda *a, **k: {"drift": False, "checked": False}
_stub_env.record_baseline = lambda *a, **k: None
sys.modules["services.env_baseline"] = _stub_env

os.environ["DY_CRED_REFRESH_MODE"] = "popup"  # 跳过观测态静默回写，直入重扫/熔断分支

_spec = importlib.util.spec_from_file_location("bcc_login_under_test", TARGET)
mod = importlib.util.module_from_spec(_spec)
sys.modules["bcc_login_under_test"] = mod
_spec.loader.exec_module(mod)


class FakeClock:
    def __init__(self, t=1000.0):
        self.t = t

    def time(self):
        return self.t

    def sleep(self, s):
        self.t += s


class StepEvent:
    """wait() 每拍返回 False（放行一整轮心跳），第 ticks+1 拍返回 True 结束。"""

    def __init__(self, ticks, clock, schedule):
        self.ticks = ticks
        self.clock = clock
        self.schedule = schedule or {}
        self.n = 0

    def is_set(self):
        return False

    def wait(self, interval):
        self.n += 1
        if self.n > self.ticks:
            return True
        adv = self.schedule.get(self.n, 0)
        if adv:
            self.clock.t += adv
        return False


class StubFuture:
    def __init__(self, result):
        self._r = result

    def result(self, timeout=None):
        return self._r


class LoopStub:
    def is_closed(self):
        return False


class Host(mod.BccLoginMixin):
    def __init__(self, uid_getter, scan_results, page_state):
        self.account = "E1_TEST"
        self._switching = False
        self._switch_cool_until = 0.0
        self._headless = True            # 非「有头观测态」→ 允许自动重扫
        self._loop = LoopStub()
        self._last_uid = "uidA"
        self._last_env_audit_at = 1e18    # 关掉周期审计
        self._uid_getter = uid_getter
        self._scan_results = list(scan_results)
        self._page_state = dict(page_state)
        self.scan_calls = 0

    def _load_uid_from_env(self):
        return self._uid_getter()

    def _page_login_state_sync(self):
        return dict(self._page_state)

    def scan_login(self, force=False, timeout=300):
        self.scan_calls += 1
        if self._scan_results:
            return self._scan_results.pop(0)
        return {"ok": False, "uid": None}


def run(uid_getter, scan_results, page_state=None, schedule=None, ticks=4):
    clock = FakeClock(1000.0)
    mod.time = clock  # 只替换被测模块命名空间里的 time
    mod.asyncio = types.SimpleNamespace(
        run_coroutine_threadsafe=lambda x, loop: StubFuture(x))
    host = Host(uid_getter, scan_results, page_state or {"rel": True, "conv": True})
    ev = StepEvent(ticks, clock, schedule if schedule is not None else {3: 1801})
    trips = []
    lines = []

    def sink(m):
        if "BCC-024" in m.record["message"]:
            trips.append(ev.n)
            lines.append(m.record["line"])

    hid = logger.add(sink, level="ERROR")
    try:
        host.run_keepalive(ev, interval=1)
    finally:
        logger.remove(hid)
    return trips, host.scan_calls, lines


def main():
    fails = [{"ok": False, "uid": None}] * 6

    # 场景A：701 通用跳闸（探活取不到 uid ⇒ 646 else 分支）
    a_uid = lambda: None                                            # noqa: E731
    a = run(a_uid, fails)

    # 场景B：685 漂移立即熔断（tick1 纯失败 count=1 → tick2 漂移）
    b_uid = lambda: None                                            # noqa: E731
    b = run(b_uid, [{"ok": False, "uid": None},
                    {"ok": False, "uid": "X", "drift": True}] + fails)

    # 场景C：633 页面重激活连续失败（uid 正常 + 页面 rel=True）
    c_uid = lambda: "uidA"                                          # noqa: E731
    c = run(c_uid, fails)

    # 场景D：583 uid 漂移后刷新连续失败（uid 每拍都变）
    _seq = iter(["uidB", "uidC", "uidD", "uidE", "uidF", "uidG"])
    d_uid = lambda: next(_seq, "uidZ")                              # noqa: E731
    d = run(d_uid, fails)

    print("SCEN_A_701_TRIPS:" + repr(a[0]) + " SCANS:" + str(a[1]) + " LINES:" + repr(a[2]))
    print("SCEN_B_685_TRIPS:" + repr(b[0]) + " SCANS:" + str(b[1]) + " LINES:" + repr(b[2]))
    print("SCEN_C_633_TRIPS:" + repr(c[0]) + " SCANS:" + str(c[1]) + " LINES:" + repr(c[2]))
    print("SCEN_D_583_TRIPS:" + repr(d[0]) + " SCANS:" + str(d[1]) + " LINES:" + repr(d[2]))
    all_trips = [a[0], b[0], c[0], d[0]]
    ok = all(t == [2, 4] for t in all_trips)
    print("VERDICT:" + ("RESET_OK" if ok else "RESET_MISSING"))


if __name__ == "__main__":
    main()
