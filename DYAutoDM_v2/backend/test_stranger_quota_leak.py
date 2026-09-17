# -*- coding: utf-8 -*-
"""配额泄漏回归测试（2026-09-17 OCR 审查 HIGH）。

缺陷：`submit` / `submit_by_uid` 原先在**队列容量检查之前**就调用
`note_stranger_sent()` 预占额度，而队列满时直接 return **不归还** →
每次被拒都白吃一个「陌生人首发」额度（2/分钟、30/天），批量场景会把
额度快速耗空，之后真实可发的目标被 `can_stranger_first` 误拒。

本测试用最小桩（不导入 dm_dispatch 的 HTTP 依赖）复刻两条提交路径的
**顺序语义**，对比旧/新实现在「队列满」时的额度消耗：

  旧：容量检查前预占 → 队列满被拒，额度 -1（泄漏）
  新：容量检查后预占 → 队列满被拒，额度 0（正确）

用法：cd DYAutoDM_v2/backend && python test_stranger_quota_leak.py
"""
import unittest


class FakeQuota:
    """AccountQuota 的最小语义桩：分钟/日窗 + 预占 + 归还 + 上限判定。"""

    def __init__(self, per_minute=2, per_day=30):
        self.minute = []
        self.day = []
        self.per_minute = per_minute
        self.per_day = per_day

    def can_stranger_first(self):
        if len(self.minute) >= self.per_minute:
            return False, f"每分钟上限 {self.per_minute}"
        if len(self.day) >= self.per_day:
            return False, f"每天上限 {self.per_day}"
        return True, ""

    def note_stranger_sent(self):
        self.minute.append(1)
        self.day.append(1)

    def refund_stranger(self):
        if self.minute:
            self.minute.pop()
        if self.day:
            self.day.pop()

    @property
    def used(self):
        return len(self.day)


def submit_old(quota, queue_size, queue_max):
    """旧实现顺序：先预占额度，后检查队列容量。"""
    is_stranger = True
    if is_stranger:
        ok, _ = quota.can_stranger_first()
        if not ok:
            return False
        quota.note_stranger_sent()          # ← 预占（在容量检查之前）
    if queue_size >= queue_max:
        return False                        # ← 队列满：直接返回，不归还
    return True


def submit_new(quota, queue_size, queue_max):
    """新实现顺序：先检查队列容量，通过后才预占额度。"""
    is_stranger = True
    if queue_size >= queue_max:
        return False                        # ← 容量不足：此时还没预占，无泄漏
    if is_stranger:
        ok, _ = quota.can_stranger_first()
        if not ok:
            return False
        quota.note_stranger_sent()
    return True


def hammer(submit_fn, attempts=10, queue_max=200, per_minute=2):
    """模拟「队列一直满」时反复提交：容量恒满 → 每次都该被拒且不耗额度。"""
    q = FakeQuota(per_minute=per_minute)
    accepted = 0
    for _ in range(attempts):
        if submit_fn(q, queue_size=queue_max, queue_max=queue_max):
            accepted += 1
    return q.used, accepted


class TestStrangerQuotaLeak(unittest.TestCase):
    def test_old_impl_leaks_quota(self):
        """旧实现：队列满时反复提交会耗空额度（复现缺陷）。"""
        used, accepted = hammer(submit_old)
        self.assertEqual(accepted, 0, "队列满时不应接受任何任务")
        self.assertGreater(used, 0,
                           "旧实现应出现配额泄漏（被拒仍扣额度）")
        self.assertEqual(used, 2,
                         "旧实现会耗到分钟上限 2（后续被 can_stranger_first 拒）")

    def test_new_impl_no_leak(self):
        """新实现：队列满时反复提交，零额度消耗。"""
        used, accepted = hammer(submit_new)
        self.assertEqual(accepted, 0, "队列满时不应接受任何任务")
        self.assertEqual(used, 0,
                         "新实现不得泄漏任何额度（容量检查在预占之前）")

    def test_normal_path_still_reserves(self):
        """回归保护：容量充足时，新实现仍**会**预占额度（不能把闸门改废）。"""
        q = FakeQuota(per_minute=2)
        self.assertTrue(submit_new(q, queue_size=0, queue_max=200))
        self.assertEqual(q.used, 1, "正常入池应预占 1 个额度")

    def test_quota_limit_still_enforced(self):
        """回归保护：额度耗尽后仍须拒绝。"""
        q = FakeQuota(per_minute=2)
        # 先占满（容量充足）
        for _ in range(3):
            submit_new(q, queue_size=0, queue_max=200)
        # 第 3 次应因额度不足被拒 → used 停在 2
        self.assertEqual(q.used, 2, "额度上限仍然生效")

    def test_refund_on_send_failure(self):
        """回归保护：发送失败归还额度（既有 _send_one 语义）。"""
        q = FakeQuota(per_minute=2)
        submit_new(q, queue_size=0, queue_max=200)
        self.assertEqual(q.used, 1)
        q.refund_stranger()
        self.assertEqual(q.used, 0, "归还后额度应恢复")


if __name__ == "__main__":
    unittest.main(verbosity=2)
