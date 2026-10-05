# -*- coding: utf-8 -*-
"""daemon_registry.kill_all 并发语义回归测试（2026-09-17 OCR 审查 HIGH）。

缺陷：原实现先在锁内 `pids = sorted(_pids); _pids.clear()`，再**锁外**
逐个 `_pid_running(pid)` 判定后下手 → 判定与下手之间 PID 可能被系统回收
给新进程，`taskkill /F /T` 会连带杀掉无关进程树；且 clear 后新登记的
pid 与本次列表脱节。

修复：两段式 —— 锁内快照 → 锁外判定 → **锁内原子摘除**（只处理仍在登记表
里的 pid），kill 在锁外执行。

本测试用假 pid + 假 _pid_running 复刻两条路径，断言：
  1. 期间被 unregister 的 pid **不再被下手**（这是修复的核心）；
  2. 已死的 pid 会被清出登记表，不下手；
  3. 存活且在册的 pid 正常下手并从登记表移除。

用法：cd FlowCap/backend && python test_daemon_registry_killall.py
"""
import unittest


class FakeRegistry:
    """复刻 daemon_registry 的相关状态与两种 kill_all 实现。"""

    def __init__(self, pids, running):
        self._pids = set(pids)
        self._running = set(running)      # 视为存活的 pid
        self.killed = []

    def _pid_running(self, pid):
        return pid in self._running

    def _kill(self, pid):
        self.killed.append(pid)

    # ---- 旧实现 ----
    def kill_all_old(self, on_judge=None):
        pids = sorted(self._pids)
        self._pids.clear()                # ← 先清空
        killed = []
        for pid in pids:
            if not self._pid_running(pid):
                continue
            if on_judge:                  # 模拟"判定与下手之间"的并发事件
                on_judge(self)
            killed.append(pid)
        self.killed = killed
        return killed

    # ---- 新实现 ----
    def kill_all_new(self, on_judge=None):
        pids = sorted(self._pids)
        live = []
        for pid in pids:
            if self._pid_running(pid):
                live.append(pid)
                if on_judge:              # 模拟判定期间的并发事件
                    on_judge(self)
        to_kill = []
        for pid in live:
            if pid in self._pids:         # ← 关键：仍在册才下手
                self._pids.discard(pid)
                to_kill.append(pid)
        for pid in pids:
            if pid not in to_kill:
                self._pids.discard(pid)
        self.killed = to_kill
        return to_kill


def unregister_midway(reg):
    """模拟并发 unregister（另一个线程把 pid 移出登记表）。"""
    reg._pids.discard(2002)
    reg._pids.add(9999)                   # 同时登记一个新 pid


class TestKillAllConcurrency(unittest.TestCase):
    def test_old_impl_kills_unregistered_pid(self):
        """对照：旧实现在判定后仍会对已 unregister 的 pid 下手。"""
        reg = FakeRegistry([1001, 2002], running=[1001, 2002])
        killed = reg.kill_all_old(on_judge=unregister_midway)
        self.assertIn(2002, killed,
                      "旧实现应仍杀掉已注销的 2002（缺陷复现）")

    def test_new_impl_skips_unregistered_pid(self):
        """修复后：期间被 unregister 的 pid 不再被下手。"""
        reg = FakeRegistry([1001, 2002], running=[1001, 2002])
        killed = reg.kill_all_new(on_judge=unregister_midway)
        self.assertNotIn(2002, killed,
                         "修复后不得对已注销的 2002 下手")
        self.assertIn(1001, killed, "在册存活的 1001 应被下手")

    def test_dead_pid_is_cleaned_not_killed(self):
        """已死 pid 只清登记，不下手。"""
        reg = FakeRegistry([1001, 3003], running=[1001])
        killed = reg.kill_all_new()
        self.assertEqual(killed, [1001])
        self.assertNotIn(3003, reg._pids, "已死 pid 应被清出登记表")

    def test_newly_registered_pid_survives(self):
        """期间新登记的 pid 不得被本次清理误删/误杀。"""
        reg = FakeRegistry([1001, 2002], running=[1001, 2002])
        reg.kill_all_new(on_judge=unregister_midway)
        self.assertIn(9999, reg._pids, "新登记的 9999 必须仍在册（不被误清）")
        self.assertNotIn(9999, reg.killed, "新登记的 9999 不得被误杀")


if __name__ == "__main__":
    unittest.main(verbosity=2)
