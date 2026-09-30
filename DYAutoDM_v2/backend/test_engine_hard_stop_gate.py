# coding=utf-8
"""M-29 离线契约门禁：硬停止（清队列）vs 软停止（存量发完）的语义差别。

## 为什么要有这份门禁

端点层已验证（`api/engine.py` `/stop` → hard=True、`/stop-soft` → hard=False），
但**真正决定用户体验的是调度层**：

    DispatchCenter.stop_hard()  ── 清空队列 + 清空 pending，一条不发
    DispatchCenter.stop_soft()  ── 保留队列，存量发完，只是不再收新目标

这两态此前**没有机械判据** —— 只有真机点按钮、看日志 `[SEND-002]` 才能判定。
本文件把这项判定离线化，真机只留最后一跳（见报告第 4 节）。

## 本文件守住的不变式

| 编号 | 不变式 |
|------|--------|
| H1 | `stop_hard()` 后 `queue_size()==0` 且 `pending=={}`（立刻清空） |
| H2 | `stop_hard()` 后 `submit()` 一律被忽略（队列不再增长） |
| S1 | `stop_soft()` 后 `queue_size()>0` 且 `pending` 保留（存量不动） |
| S2 | `stop_soft()` 后 `submit()` 同样被忽略（不收新目标） |
| S3 | **决定性判据**：软停止 `wait_done()` 后**存量全部发出**；硬停止后**发出 0 条** |

## 负控（判别力证明）

判据被抽成模块级函数 `check_H1 / check_H2 / check_S1 / check_S2 / check_S3_*`，
**正例与负控共用同一份判据**：

- 正例：`check_XXX(self, dc)` 不抛异常 ⇒ 通过；
- 负控：先用内存子类注入缺陷，再 `with self.assertRaises(AssertionError): await check_XXX(...)`
  —— 断言「这份判据**真的会红**」。若哪天判据退化成恒真，负控自己先失败。

| 负控 | 注入的缺陷 | 应当变红的判据 |
|------|-----------|---------------|
| X1 | `stop_hard` 漏掉清空循环 | H1 |
| X2 | `stop_soft` 也清空队列 | S1 / S3-软 |
| X3 | `submit` 不看停止闸门 | H2 / S2 |
| X0 | 真实实现（对照） | 必须全绿 |

🔴 风控红线：全程内存对象 + 打桩回调，**不发任何真实私信、不联网、不碰抖音**。
`_do_send` 被整体替换成只计数的桩，绝不触达 `core/sender.send_target_async`，
且 `auth.account_name` 置空以杜绝 `_do_send` 走 `dm_dispatch` 路由。
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
import unittest

BACKEND = os.path.dirname(os.path.abspath(__file__))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from core.dispatch import DispatchCenter  # noqa: E402

try:  # pragma: no cover - `_QueueItem` 属模块内部类型，取不到则退化成元组
    from core.dispatch import _QueueItem as _QI  # type: ignore

    def _qi(send_at: float, key: str, target: dict):
        return _QI(send_at=send_at, key=key, target=target)
except Exception:  # pragma: no cover
    def _qi(send_at: float, key: str, target: dict):
        return (send_at, key, target)


# --------------------------------------------------------------------------
# 替身 / 打桩
# --------------------------------------------------------------------------
class _Auth:
    """凭证替身：只提供 account_name，避免 `_do_send` 走 dm_dispatch 路由。

    注：`_do_send` 被本测试整体替换为桩，auth 全程不会被真正使用；
    这里给空 account_name 是双保险（即使桩失效也不会撞到真实发送通道）。
    """

    account_name = ""
    cookie_str = ""
    cookie = ""
    ticket = None
    private_key = None


class _SendSpy:
    """发送打桩：替换 `DispatchCenter._do_send`，只记录「实际发出」的条数。

    这是 S3 决定性判据的核心 —— 不看状态字段，看**真的走了几次发送动作**。
    """

    def __init__(self) -> None:
        self.sent_keys: list[str] = []

    def install(self, dc: DispatchCenter) -> None:
        async def _fake_do_send(key: str, target: dict) -> None:
            self.sent_keys.append(key)
            # 对齐真实 _do_send 的成功收尾语义：成功即出池，否则 wait_done 永不结束
            dc.pending.pop(key, None)
            dc.count += 1

        dc._do_send = _fake_do_send  # type: ignore[method-assign]

    @property
    def n(self) -> int:
        return len(self.sent_keys)


def _mk_dc(n_targets: int = 3, n_pending_only: int = 2,
           cls=DispatchCenter) -> DispatchCenter:
    """构造一个**已装入存量**的调度中心（不启动 `_loop`，纯状态构造 + 直填队列）。

    直填 `_queue` / `pending` 而不是走 `submit()`，是因为 submit 的延迟是随机的，
    而我们要的是**确定的初始存量**。
    """
    dc = cls(auth=_Auth(), max_target=999, delay_range=(0, 0),
             interval=0.0, enable_send=True)
    for i in range(n_targets):
        key = f"u{i}"
        target = {"user_id": key, "nickname": f"用户{i}", "comment": f"弹幕{i}"}
        dc.pending[key] = target
        dc._ensure_record(key, target)
        dc._queue.put_nowait(_qi(time.time(), key, target))
    # 只在 pending、尚未入队的目标（延迟窗口内未排期）
    for j in range(n_pending_only):
        key = f"p{j}"
        target = {"user_id": key, "nickname": f"待排{j}"}
        dc.pending[key] = target
        dc._ensure_record(key, target)
    return dc


async def _drain(dc: DispatchCenter, spy: _SendSpy) -> None:
    """把队列抽干：每条都走 `dc._do_send`（已被替换为桩），模拟消费循环。

    不起真实 `_loop`：它含 `asyncio.sleep(interval)` 与随机延迟，既慢又不稳，
    且我们要测的是「停止语义对队列/pending 的作用」，不是 `_loop` 的调度时序。
    """
    while not dc._queue.empty():
        try:
            item = dc._queue.get_nowait()
        except asyncio.QueueEmpty:
            break
        key = getattr(item, "key", None) or item[1]
        await dc._do_send(key, item.target if hasattr(item, "target") else {})
    # 队列抽干后清掉仅 pending 的目标（真实 _loop 在软停止时会丢弃它们），
    # 再等 wait_done 收敛，避免死等。
    dc.pending.clear()
    await dc.wait_done()


# --------------------------------------------------------------------------
# 判据（正例与负控**共用**，保证负控真的在测同一条事实）
# --------------------------------------------------------------------------
def check_H1(tc: unittest.TestCase, dc: DispatchCenter) -> None:
    """H1：硬停止后队列归零、pending 清空。"""
    tc.assertEqual(dc.queue_size(), 0, "H1：硬停止后队列必须归零")
    tc.assertEqual(dc.pending, {}, "H1：硬停止后 pending 必须清空")
    tc.assertTrue(dc._clear_queue, "H1：_clear_queue 必须置位")


def check_H2(tc: unittest.TestCase, dc: DispatchCenter) -> None:
    """H2：硬停止后 submit 一律被忽略（队列不再增长）。"""
    before = dc.queue_size()
    ok = dc.submit({"user_id": "new1", "nickname": "新目标1"})
    tc.assertFalse(ok, "H2：硬停止后 submit 必须返回 False")
    tc.assertEqual(dc.queue_size(), before, "H2：硬停止后队列不得再增长")
    tc.assertIn("new1", dc.records, "H2：被拒的目标仍要有捕获记录（不丢数据）")


def check_S1(tc: unittest.TestCase, dc: DispatchCenter) -> None:
    """S1：软停止后存量保留（队列不动、pending 不清）。"""
    tc.assertGreater(dc.queue_size(), 0, "S1：软停止后存量必须保留")
    tc.assertNotEqual(dc.pending, {}, "S1：软停止不得清空 pending")
    tc.assertFalse(dc._accept_new, "S1：软停止后不再接收新目标")
    tc.assertFalse(dc._clear_queue, "S1：软停止绝不能置 _clear_queue")


def check_S2(tc: unittest.TestCase, dc: DispatchCenter) -> None:
    """S2：软停止后 submit 同样被忽略。"""
    before_q, before_p = dc.queue_size(), dict(dc.pending)
    ok = dc.submit({"user_id": "new1", "nickname": "新目标1"})
    tc.assertFalse(ok, "S2：软停止后 submit 必须返回 False")
    tc.assertEqual(dc.queue_size(), before_q, "S2：不得把新目标塞进队列")
    tc.assertEqual(dc.pending, before_p, "S2：pending 不得因新目标变化")
    tc.assertIn("new1", dc.records, "S2：被拒的目标仍要有捕获记录")


async def check_S3_soft(tc: unittest.TestCase, dc: DispatchCenter,
                        expected: int) -> None:
    """S3-软：软停止后跑完 wait_done，存量必须**全部发出**。

    ⚠️ `expected` **必须在调用 stop_soft() 之前**取（`_mk_dc` 的入队数）。
    若在这里现读 `queue_size()`，缺陷变体「软停止顺手清了队列」会让期望值
    退化成 0，判据变成 0==0 恒真 —— 这正是负控 X2 一开始没抓住它的原因。
    """
    spy = _SendSpy()
    spy.install(dc)
    await _drain(dc, spy)
    tc.assertEqual(spy.n, expected,
                   f"S3-软：软停止后存量必须全部发出（发了 {spy.n} / 存量 {expected}）")


async def check_S3_hard(tc: unittest.TestCase, dc: DispatchCenter) -> None:
    """S3-硬：硬停止后跑完 wait_done，必须**一条都不发**。"""
    spy = _SendSpy()
    spy.install(dc)
    await _drain(dc, spy)
    tc.assertEqual(spy.n, 0, f"S3-硬：硬停止后一条都不许发（实际发了 {spy.n} 条）")


# --------------------------------------------------------------------------
# H1 / H2 —— 硬停止：立刻清空，之后不收
# --------------------------------------------------------------------------
class TestHardStopClearsQueue(unittest.IsolatedAsyncioTestCase):
    """硬停止 = 「立刻停」：队列清空、pending 清空、后续 submit 全忽略。"""

    async def test_H1_hard_stop_clears_queue_and_pending(self):
        dc = _mk_dc(n_targets=3, n_pending_only=2)
        self.assertGreaterEqual(dc.queue_size(), 3, "前置：队列里得有存量")
        self.assertGreaterEqual(len(dc.pending), 5, "前置：pending 也得有存量")
        dc.stop_hard()
        check_H1(self, dc)

    async def test_H2_submit_after_hard_stop_is_ignored(self):
        dc = _mk_dc(n_targets=3, n_pending_only=2)
        dc.stop_hard()
        check_H2(self, dc)
        # 再补一次提交，确认不是「只拒第一条」
        dc.submit({"user_id": "new2", "nickname": "新目标2"})
        self.assertEqual(dc.queue_size(), 0, "H2：连续提交也不得让队列增长")


# --------------------------------------------------------------------------
# S1 / S2 —— 软停止：保留存量，但不再收新目标
# --------------------------------------------------------------------------
class TestSoftStopKeepsQueue(unittest.IsolatedAsyncioTestCase):
    """软停止 = 「存量发完」：队列不动、pending 保留，只是不再接收新目标。"""

    async def test_S1_soft_stop_keeps_queue_and_pending(self):
        dc = _mk_dc(n_targets=3, n_pending_only=2)
        before_q, before_p = dc.queue_size(), dict(dc.pending)
        dc.stop_soft()
        check_S1(self, dc)
        self.assertEqual(dc.queue_size(), before_q, "S1：队列长度不得被软停止改变")
        self.assertEqual(dc.pending, before_p, "S1：pending 内容不得被软停止改变")

    async def test_S2_submit_after_soft_stop_is_ignored(self):
        dc = _mk_dc(n_targets=3, n_pending_only=2)
        dc.stop_soft()
        check_S2(self, dc)


# --------------------------------------------------------------------------
# S3 —— 决定性判据：软停发完 N 条，硬停发 0 条
# --------------------------------------------------------------------------
class TestTwoStatesDivergeOnWaitDone(unittest.IsolatedAsyncioTestCase):
    """两态差别的**决定性判据**：同一批存量，wait_done() 后发出数分别是 N 和 0。"""

    async def test_S3_soft_stop_drains_all_backlog(self):
        """软停止：存量全部发出（发出数 == 入队数 3）。"""
        dc = _mk_dc(n_targets=3, n_pending_only=2)
        dc.stop_soft()
        await check_S3_soft(self, dc, expected=3)

    async def test_S3_hard_stop_sends_nothing(self):
        """硬停止：一条都不发（发出数 == 0）。"""
        dc = _mk_dc(n_targets=3, n_pending_only=2)
        dc.stop_hard()
        await check_S3_hard(self, dc)

    async def test_S3_the_two_states_are_opposite(self):
        """同一批存量走两态，发出数必须是 3 vs 0 —— 一句话锁死语义差别。"""
        soft = _mk_dc(n_targets=3, n_pending_only=2)
        hard = _mk_dc(n_targets=3, n_pending_only=2)
        spy_s, spy_h = _SendSpy(), _SendSpy()
        spy_s.install(soft)
        spy_h.install(hard)

        soft.stop_soft()
        hard.stop_hard()
        await _drain(soft, spy_s)
        await _drain(hard, spy_h)

        self.assertEqual(spy_s.n, 3, "软停止：存量 3 条应全部发出")
        self.assertEqual(spy_h.n, 0, "硬停止：应一条不发")
        self.assertNotEqual(spy_s.n, spy_h.n,
                            "两态必须可区分，否则这份门禁没有判别力")


# --------------------------------------------------------------------------
# 缺陷变体（仅存在于内存，用于负控；不改动任何生产源码）
# --------------------------------------------------------------------------
class _BrokenHardStop(DispatchCenter):
    """缺陷变体 X1：`stop_hard` 不清队列（漏掉清空循环 + pending.clear）。"""

    def stop_hard(self) -> None:
        self._clear_queue = True
        self._accept_new = False
        self._stopped = True
        self._stop_signal.set()
        # ★ 故意漏掉：while not self._queue.empty(): get_nowait() / pending.clear()


class _BrokenSoftStop(DispatchCenter):
    """缺陷变体 X2：`stop_soft` 也把队列清了（退化成硬停止）。"""

    def stop_soft(self) -> None:
        self._accept_new = False
        self._stop_signal.set()
        self._clear_queue = True          # ★ 缺陷
        while not self._queue.empty():    # ★ 缺陷
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        self.pending.clear()              # ★ 缺陷


class _BrokenSubmit(DispatchCenter):
    """缺陷变体 X3：`submit` 不再看停止闸门（停止后照样收新目标）。"""

    def submit(self, target: dict) -> bool:  # type: ignore[override]
        if not target.get("capture_ts"):
            target["capture_ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
        key = self._dedup_key(target)
        if not key:
            return False
        self._ensure_record(key, target)
        # ★ 故意漏掉 `if self._clear_queue or not self._accept_new: return False`
        self.pending[key] = target
        self._queue.put_nowait(_qi(time.time(), key, target))
        return True


# --------------------------------------------------------------------------
# 负控：注入缺陷后，同一份判据必须真的抛 AssertionError（= 门禁会红）
# --------------------------------------------------------------------------
class TestNegativeControls(unittest.IsolatedAsyncioTestCase):
    """负控：证明上面的判据**真的会红**，不是恒真的摆设。

    每条负控都在内存里构造缺陷变体，断言「判据抛 AssertionError」。
    若哪天判据退化成恒真（正例永远绿），这些负控会自己先失败 —— 那才是门禁失效。
    """

    async def test_X1_broken_hard_stop_turns_H1_red(self):
        """X1：清掉 stop_hard 的清空循环 → H1 判据必须红。"""
        dc = _mk_dc(n_targets=3, n_pending_only=2, cls=_BrokenHardStop)
        dc.stop_hard()
        with self.assertRaises(AssertionError) as cm:
            check_H1(self, dc)
        self.assertIn("H1", str(cm.exception))
        # 附带确认缺陷形态：队列确实没被清
        self.assertGreater(dc.queue_size(), 0)
        self.assertNotEqual(dc.pending, {})

    async def test_X2_broken_soft_stop_turns_S1_and_S3_red(self):
        """X2：让 stop_soft 也清空 → S1 与 S3-软 判据必须红。"""
        dc1 = _mk_dc(n_targets=3, n_pending_only=2, cls=_BrokenSoftStop)
        dc1.stop_soft()
        with self.assertRaises(AssertionError) as cm:
            check_S1(self, dc1)
        self.assertIn("S1", str(cm.exception))

        dc2 = _mk_dc(n_targets=3, n_pending_only=2, cls=_BrokenSoftStop)
        dc2.stop_soft()
        with self.assertRaises(AssertionError) as cm:
            await check_S3_soft(self, dc2, expected=3)
        self.assertIn("S3-软", str(cm.exception))

    async def test_X3_broken_submit_turns_H2_and_S2_red(self):
        """X3：submit 不看停止闸门 → H2 / S2 判据必须红。"""
        dc_h = _mk_dc(n_targets=2, n_pending_only=0, cls=_BrokenSubmit)
        dc_h.stop_hard()
        with self.assertRaises(AssertionError) as cm:
            check_H2(self, dc_h)
        self.assertIn("H2", str(cm.exception))

        dc_s = _mk_dc(n_targets=2, n_pending_only=0, cls=_BrokenSubmit)
        dc_s.stop_soft()
        with self.assertRaises(AssertionError) as cm:
            check_S2(self, dc_s)
        self.assertIn("S2", str(cm.exception))

    async def test_X0_real_implementation_passes_the_same_checks(self):
        """对照：真实实现在**同一组判据**下必须全绿（与 X1/X2/X3 形成反证）。"""
        dc1 = _mk_dc(n_targets=3, n_pending_only=2)
        dc1.stop_hard()
        check_H1(self, dc1)
        check_H2(self, dc1)

        dc2 = _mk_dc(n_targets=3, n_pending_only=2)
        dc2.stop_soft()
        check_S1(self, dc2)
        check_S2(self, dc2)

        dc3 = _mk_dc(n_targets=3, n_pending_only=2)
        dc3.stop_soft()
        await check_S3_soft(self, dc3, expected=3)

        dc4 = _mk_dc(n_targets=3, n_pending_only=2)
        dc4.stop_hard()
        await check_S3_hard(self, dc4)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
