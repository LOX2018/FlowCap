# -*- coding: utf-8 -*-
"""H-22 审计外 · ADR-018 F4 定时任务中心 风控门禁（**行为断言版** + 负控）。

## 🔴 v2 重写说明（2026-09-27 全库审计 TS-9）

**v1 的门禁 7/7 全绿，而同一份代码里同时存在两个 P1 级真缺陷** ——
这是本项目「**声明式规则 = 空架子**」教训的第 N 次复现，且这次最讽刺：
门禁自己「看起来」很认真（有负控、有注释反思），但判据**全部是源码字符串匹配**。

| v1 判据 | 为何失效（全库审计实测） |
|---|---|
| G5 `"can_send()" in s` | 通过 —— 但代码把 `can_send()` 的 **3 元组解成 2 元组**，
  实测 `ValueError: too many values to unpack (expected 2, got 3)`
  ⇒ **三重闸门 100% 恒拒**，而 G5 毫无察觉（字符串在 ≠ 逻辑对）|
| G6 `"if needs_send and REQUIRE_DELIVERY_VERIFY:" in s` | 通过 —— 它匹配的**恰是那段
  有缺陷的代码的字符串**；真实缺陷在 `_auto_dm`：用 `SubmitResult.accepted`（入池）
  冒充 `delivery_verified`，**G6 根本没检查 `_auto_dm` 的返回值** |

**核心教训**：判据必须锚定**行为**（真调用、真断言返回值），
不能锚定**源码文本的存在性** —— 字符串存在不代表逻辑正确，
更糟的是**缺陷本身往往就写在那个字符串里**。

## v2 判据（B1~B7，全部为行为断言）

| # | 判据 | 锚定的行为 | 覆盖 v1 的哪个盲区 |
|---|---|---|---|
| B1 | 两个总开关默认 False | 读**运行时值**（非源码文本） | v1 G1/G2 仅模式匹配 |
| B2 | 总开关关闭时 `start()` 被拒 | **真调** `start()` | v1 G7 仅匹配字符串 |
| B3 | `_gate_for_send` **不得因异常而拒** | **真调** `_gate_for_send` | **v1 完全盲区**（TS-2） |
| B4 | 额度闸门**真被调用且正确解包** | 注入假 quota，断言调用序列 + 无解包错 | **v1 完全盲区**（TS-2） |
| B5 | 闸门**真的会拒**（防恒 True） | 造必拒条件，断言 ok=False | v1 无此判据 |
| B6 | 入池**不得**冒充投递已验证 | 注入假 dispatcher，**真调** `_auto_dm` | **v1 完全盲区**（TS-3） |
| B7 | 新建外发任务默认休眠 | **真构造** `Task`，断言 `enabled` | **v1 完全盲区**（TS-8） |

## 负控（D-07）

`--selftest`：注入**v1 曾漏掉的**缺陷形态（元组解包错 / accepted 冒充 / 默认 True），
断言 v2 门禁**真的变红**。不变红 = 仍是空架子。

## 隔离

全部在临时 `DY_APP_ROOT` + 假对象注入下运行，**不碰真实数据根**、**不发任何网络请求**。
"""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
import threading
import time
import unittest
import atexit  # noqa: F401  # D-5b：selftest 中断兜底还原（见 selftest()）

# ── 隔离根：必须在 import 业务模块**之前**设置（app_root() 会忽略不存在的路径）
# M-28（2026-09-29）：原实现用**固定**目录 `f4_gate_v2` 且只在模块级设一次。
#   `database.get_db()` **每次调用**都按**当前** DY_APP_ROOT 做会员一致性校验，
#   故「导入期设一次」不够 —— 执行期会被别的模块盖掉 ⇒ 顺序相关假失败。
#   改为一次性临时目录，并在每个用例 setUp 里重钉。
#   标识符（父会话 grep 用）：N1_M28_PIN_ROOT
_ROOT = tempfile.mkdtemp(prefix="n1_m28_f4gate_")
os.makedirs(os.path.join(_ROOT, "members"), exist_ok=True)
os.environ["DY_APP_ROOT"] = _ROOT
# 门禁自身只做只读行为断言 ⇒ 不带外部开关（各用例内临时改运行时常量）
os.environ.pop("DY_TASK_SCHEDULER_ENABLED", None)
os.environ.pop("DY_AUTO_SEND_ENABLED", None)

_BACKEND = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _BACKEND)

# M-28 / D-3（2026-09-30）：`dd.cfg` 可能被别的测试模块（实测毒源
# `test_uid_sink_ext`：`setUpClass` 存 `cls._orig = dd.cfg`，`tearDown` 经描述符
# 协议取回成 bound method 后写回）在执行期换掉。这里在**导入期**抓一份**原始函数
# 对象**的强引用，`_n1_m28_pin_root()` 里无条件归还 ⇒ 本模块不依赖执行顺序。
from services import dm_dispatch as dd  # noqa: E402
from services.dm_dispatch import cfg as _PRISTINE_CFG  # noqa: E402

RESULTS: list = []


def _n1_m28_pin_root():
    """N1_M28_PIN_ROOT：执行期重钉本模块的一次性隔离根。

    M-28（2026-09-29）：`database.get_db()` **每次调用**都按**当前** DY_APP_ROOT
    做会员一致性校验（不一致即重连）⇒ 模块级设一次不够，执行期会被别的模块盖掉。

    2026-09-30 补（D-3）：原实现**只钉根、不归还 `dd.cfg`**，而本模块**不在**
    `test_module_order_independence._CFG_DEPENDENT` 的既有修复名单内 ⇒ 毒源
    `test_uid_sink_ext`（其 `setUpClass` 存 `cls._orig = dd.cfg`，经描述符协议
    取回成 bound method）污染后本模块照单全收，`dd.cfg("X")` 的 name 位收到
    TestCase 实例 ⇒ `KeyError: <TestCase ...>`，表现为「单跑绿、乱序红」。
    修法与既有受害者（test_dm_dispatch_config 等）**同体例**：导入期抓原始函数
    强引用，执行期无条件归还。
    """
    os.environ["DY_APP_ROOT"] = _ROOT
    try:
        import database
        database.reset_connection()
    except Exception:                                           # noqa: BLE001
        pass
    if getattr(dd, "cfg", None) is not _PRISTINE_CFG:
        dd.cfg = _PRISTINE_CFG


def check(ok: bool, gid: str, desc: str) -> None:
    RESULTS.append((bool(ok), gid, desc))
    print(f"  [{'PASS' if ok else 'FAIL'}] {gid}  {desc}")


def _reload_target() -> None:
    """强制重载被测模块 —— 清掉 Python 的模块缓存。

    🔴 为什么必须做（本门禁自检实测踩到的真 bug）：
    `--selftest` 会「篡改源码 → 跑 → 还原」。但 `task_scheduler` 一旦被 import
    就进了 `sys.modules` **缓存**，**磁盘还原后内存里的类仍是旧版** ⇒
    下一轮用例/复跑读到的是**上一轮篡改后的类** ⇒ 报出**假失败**。
    （实测：selftest 全部用例通过 + 还原成功 + 磁盘字节一致，但「还原后复跑」
      仍报 B7 红 —— 就是缓存所致。）

    判据：凡「改写源码后重跑」的测试装置，**必须显式 `importlib.reload`**，
    不能假设「文件还原了 = 行为还原了」。这与「资源释放是异步的，不能假设
    关闭成功即已释放」是同一类错误。
    """
    for name in ("services.task_scheduler",):
        if name in sys.modules:
            importlib.reload(sys.modules[name])


# ===========================================================================
# B1 · 运行时常量默认关闭
# ===========================================================================
def b1_runtime_defaults_off() -> None:
    from services import task_scheduler as ts
    ok = (ts.TASK_SCHEDULER_ENABLED is False and ts.AUTO_SEND_ENABLED is False)
    check(ok, "B1",
          f"运行时默认关闭（实测 TASK_SCHEDULER_ENABLED={ts.TASK_SCHEDULER_ENABLED!r} "
          f"AUTO_SEND_ENABLED={ts.AUTO_SEND_ENABLED!r}）")


# ===========================================================================
# B2 · 总开关关闭时 start() 拒绝（真调用，非匹配字符串）
# ===========================================================================
def b2_start_fail_closed() -> None:
    from services import task_scheduler as ts
    saved = ts.TASK_SCHEDULER_ENABLED
    try:
        ts.TASK_SCHEDULER_ENABLED = False
        r = ts.start()
        ok = (isinstance(r, dict) and r.get("ok") is False)
        check(ok, "B2", f"start() 在总开关关闭时拒绝启动（实测 {r!r}）")
    except Exception as e:                                      # noqa: BLE001
        check(False, "B2", f"start() 抛异常而非 fail-closed: {type(e).__name__}: {e}")
    finally:
        ts.TASK_SCHEDULER_ENABLED = saved


# ===========================================================================
# B3 · _gate_for_send 不得因异常而拒  ← v1 的完全盲区（TS-2）
# ===========================================================================
def b3_gate_does_not_raise() -> None:
    from services import task_scheduler as ts
    saved = ts.AUTO_SEND_ENABLED
    try:
        ts.AUTO_SEND_ENABLED = True          # 开外发 ⇒ 会走到额度闸门（缺陷点在此）
        t = ts.Task(id="b3", name="b3", kind="auto_dm_send", account="agA")
        try:
            ok, why = ts._gate_for_send(t)
        except Exception as e:                                  # noqa: BLE001
            check(False, "B3", f"_gate_for_send 直接抛异常: {type(e).__name__}: {e}")
            return
        raised = "闸门检查异常" in str(why)
        check(not raised, "B3",
              f"_gate_for_send 未因异常而拒（实测 ok={ok!r} reason={why!r}）")
    finally:
        ts.AUTO_SEND_ENABLED = saved


# ===========================================================================
# B4 · 额度闸门真被调用 + 正确解包  ← v1 的完全盲区（TS-2）
# ===========================================================================
def b4_quota_gate_really_called() -> None:
    """注入**记录调用**的假 quota。

    「代码里出现了 `can_send()` 这个字符串」与「调用它之后能把返回值正确解包」
    是两件事 —— v1 只验前者，于是 3 元组解成 2 元组（必抛）完全在视野外。
    """
    from services import task_scheduler as ts
    from services import dm_dispatch

    calls: list = []

    class _FakeQuota:
        def can_send(self, min_interval: float = 0.0):
            calls.append("can_send")
            # ★ 真实签名返回 3 元组：(ok, reason, 还需等待秒数) —— dm_dispatch.py:394
            return True, "ok", 0.0

        def can_stranger_first(self):
            calls.append("can_stranger_first")
            # ★ 真实签名返回 2 元组：(ok, reason) —— dm_dispatch.py:371
            return True, "ok"

    class _FakeDisp:
        def quota_of(self, account: str):
            return _FakeQuota()

    saved_send, saved_get = ts.AUTO_SEND_ENABLED, dm_dispatch.get_dispatcher
    # 🔴 2026-09-28 夜修补：B4 原本**不锁定时段**，于是只有在 09:00~21:00 之间
    #    跑才会真正走到额度闸门；否则被时段闸门提前挡掉（calls=[]）⇒ B4 **恒红**。
    #    这是「判据依赖墙上时钟」的典型假信号：**白天绿、夜里红**，
    #    与被测代码是否正确无关。故在此显式放开允许时段（用例结束还原），
    #    让 B4 **任何时刻**都能真正命中它声称要测的额度闸门。
    saved_hours = ts.ACTIVE_HOURS
    try:
        ts.AUTO_SEND_ENABLED = True
        ts.ACTIVE_HOURS = (0, 24)        # ★ 全时段放行 ⇒ 必走到额度闸门
        dm_dispatch.get_dispatcher = lambda: _FakeDisp()
        t = ts.Task(id="b4", name="b4", kind="auto_dm_send", account="agA")

        err = ""
        try:
            ok, why = ts._gate_for_send(t)
        except Exception as e:                                  # noqa: BLE001
            ok, why, err = False, "", f"{type(e).__name__}: {e}"

        called_both = ("can_send" in calls and "can_stranger_first" in calls)
        no_unpack_err = ("unpack" not in str(why).lower() and not err)
        passed = bool(ok) and called_both and no_unpack_err
        check(passed, "B4",
              f"额度闸门真被调用且解包正确（calls={calls} ok={ok!r} "
              f"err={err or why!r}）")
    finally:
        ts.AUTO_SEND_ENABLED = saved_send
        ts.ACTIVE_HOURS = saved_hours
        dm_dispatch.get_dispatcher = saved_get


# ===========================================================================
# B5 · 闸门真的会拒（防「恒放行」）
# ===========================================================================
def b5_gate_really_refuses() -> None:
    from services import task_scheduler as ts
    saved_send, saved_sched, saved_hours = (
        ts.AUTO_SEND_ENABLED, ts.TASK_SCHEDULER_ENABLED, ts.ACTIVE_HOURS)
    try:
        t = ts.Task(id="b5", name="b5", kind="auto_dm_send", account="agA")

        ts.AUTO_SEND_ENABLED = False                    # 条件① 外发总开关关
        ok1, why1 = ts._gate_for_send(t)

        ts.AUTO_SEND_ENABLED = True                     # 条件② 非允许时段
        ts.ACTIVE_HOURS = (25, 26)                      # 小时 0~23，绝不可能命中
        ok2, why2 = ts._gate_for_send(t)

        passed = (ok1 is False) and (ok2 is False)
        check(passed, "B5",
              f"闸门真的会拒（总开关关→{ok1!r} / 非允许时段→{ok2!r}）")
    finally:
        ts.AUTO_SEND_ENABLED, ts.TASK_SCHEDULER_ENABLED, ts.ACTIVE_HOURS = (
            saved_send, saved_sched, saved_hours)


# ===========================================================================
# B6 · 入池不得冒充投递已验证  ← v1 的完全盲区（TS-3）
# ===========================================================================
def b6_auto_dm_delivery_semantics() -> None:
    """注入「已入池但无投递证据」的假 dispatcher，**真调** `_auto_dm`。

    契约（M-5 + 用户铁律「无回执不认成功」）：
      · 入池（`SubmitResult.accepted`）≠ 投递成功；
      · **调度器拿不到服务端消息号** ⇒ 不可能持有投递证据；
      · 因此返回值语义必须是三态：
          True  —— 仅当持有服务端证据（本层**不可能**出现）
          False —— 明确知道失败
          None  —— **未验证**（本层的诚实值）
    核心断言：`accepted=True` 时 `delivery_verified` **不得为 True**。
    """
    from services import task_scheduler as ts
    from services import dm_dispatch

    class _FakeRes:
        """刻意用**真实** SubmitResult 的字段名（accepted/task_id/error/queue_size）。

        历史缺陷即：代码去读 `.ok` / `.reason` —— 两个字段都**不存在**，
        于是 `ok` 落到 `accepted`（入池当成功）、`detail` 落到空串。
        """
        accepted = True
        task_id = "tk_fake"
        error = ""
        queue_size = 1

    class _FakeDisp:
        def submit_by_uid(self, account, uid, text, source="dispatch", priority=None):
            return _FakeRes()

    saved_get = dm_dispatch.get_dispatcher
    try:
        dm_dispatch.get_dispatcher = lambda: _FakeDisp()
        ts.register_builtin_handlers()
        fn = ts._handlers.get("auto_dm_send")
        if fn is None:
            check(False, "B6", "auto_dm_send 无注册执行体 ⇒ 接线缺失（无法验证投递语义）")
            return
        t = ts.Task(id="b6", name="b6", kind="auto_dm_send", account="agA",
                    params={"uid": "123", "text": "hi"})
        r = fn(t)
        dv = r.get("delivery_verified")
        passed = (dv is not True)
        check(passed, "B6",
              f"入池未被冒充为投递已验证（实测 delivery_verified={dv!r} "
              f"ok={r.get('ok')!r}）")
    finally:
        dm_dispatch.get_dispatcher = saved_get


# ===========================================================================
# B7 · 新建外发任务默认休眠（D1 的**逐任务**语义）  ← v1 的完全盲区（TS-8）
# ===========================================================================
def b7_new_send_task_defaults_off() -> None:
    from services import task_scheduler as ts
    t = ts.Task(id="b7", name="b7", kind="auto_dm_send", account="agA")
    check(t.enabled is False, "B7",
          f"新建 auto_dm_send 任务默认 enabled=False（实测 enabled={t.enabled!r}）")


# ===========================================================================
# B8~B10 · 并发与记账语义（2026-09-28 夜修 · TS-4 / TS-5 / MC-2）
#
# 三条判据**全部是行为断言**：真起线程、真调用、真读任务对象状态。
# 之所以必须这么写：本模块上一代门禁（v1）7/7 全绿却漏掉两个 P1 级真缺陷，
# 就是因为判据锚在**源码字符串**上。字符串存在 ≠ 逻辑正确。
# ===========================================================================

def _reset_scheduler_state() -> None:
    """把模块级调度状态清回干净态（每个并发用例前必做）。

    `_tasks` / `_handlers` / `_running` 是**模块级**的，用例之间会互相污染；
    不清就会出现「上一个用例留下 _running=True ⇒ 本用例第一次调用被拒」
    这类**假失败**（与「文件还原了但 sys.modules 还是旧的」是同一类错误）。
    """
    from services import task_scheduler as ts
    with ts._lock:
        ts._tasks.clear()
        ts._handlers.clear()
        ts._running = False
        ts._state["enabled"] = False
        ts._state["errors"] = []
    if ts._timer is not None:
        try:
            ts._timer.cancel()
        except Exception:                                  # noqa: BLE001
            pass
        ts._timer = None


def b8_reentrancy_fail_closed() -> None:
    """TS-4 / TS-5：真并发下第二个调用者必须被 **fail-closed** 拒绝。

    做法：`run_task_now` 在**另一个线程里**执行一个「可控时长」的任务
    （用 Event 卡住 handler），主线程**同时**再调一次同一个任务。
    断言：
      · 第二次调用返回 ok=False（被拒，不是静默执行）；
      · handler **只被真正执行 1 次**（计数器证明，不是靠看代码）。
    """
    from services import task_scheduler as ts

    _reset_scheduler_state()
    saved_sched = ts.TASK_SCHEDULER_ENABLED
    entered = threading.Event()          # handler 已进入
    release = threading.Event()          # 放行 handler
    hits = []                            # ★ 真实执行次数计数器

    def _slow_handler(task):
        hits.append(1)
        entered.set()
        release.wait(5.0)                # 卡住 ⇒ 制造真实的重入窗口
        return {"ok": True, "dummy": True}

    try:
        ts.TASK_SCHEDULER_ENABLED = True     # 否则 _run_one 直接 skipped
        t = ts.Task(id="b8t", name="b8", kind="keyword_process",
                    interval=60.0, enabled=True)
        ts.add_task(t)
        ts.register_handler("keyword_process", _slow_handler)

        # 线程①：占住执行权（会在 handler 里卡住）
        box = {}

        def _first():
            box["r1"] = ts.run_task_now("b8t")

        th = threading.Thread(target=_first, daemon=True)
        th.start()
        if not entered.wait(5.0):
            check(False, "B8", "handler 未进入 ⇒ 并发窗口未成立，判据无效")
            release.set()
            th.join(5.0)
            return

        # 线程②（主线程）：在①仍在执行时**真并发**发起第二次
        r2 = ts.run_task_now("b8t")
        release.set()
        th.join(5.0)
        r1 = box.get("r1")

        rejected = isinstance(r2, dict) and r2.get("ok") is False
        # 计数证明：只允许真正执行 1 次
        only_once = (len(hits) == 1)
        first_ok = isinstance(r1, dict) and r1.get("ok") is True
        passed = rejected and only_once and first_ok
        check(passed, "B8",
              f"重入被 fail-closed 拒绝且只真执行 1 次"
              f"（第二次 ok={r2.get('ok') if isinstance(r2, dict) else r2!r} "
              f"handler 命中 {len(hits)} 次 第一次 ok="
              f"{r1.get('ok') if isinstance(r1, dict) else r1!r}）")
    finally:
        release.set()
        ts.TASK_SCHEDULER_ENABLED = saved_sched
        _reset_scheduler_state()


def b9_last_run_at_is_finish_time() -> None:
    """MC-2：`last_run_at` 必须记「**执行完成**时刻」，不是开始时刻。

    🔴 **判据走 `_tick` 而非 `run_task_now`** —— 因为缺陷真正的**重灾区**在
    `_tick`：它写的是**本轮扫描开始时刻** `now`。`run_task_now` 旧形态用的
    是 `_run_one` 之后的 `time.time()`，反而**接近**完成时刻（影响小得多）。
    若判据只测 `run_task_now`，就会「测了个噪声、漏了病灶」。

    缺陷后果：耗时 D > interval 的任务被记成「刚开始就算跑完」⇒
    下轮 `now - last_run_at >= interval` 立刻成立 ⇒ 长任务被连续重复调度。
    断言：
      · last_run_at - 扫描开始时刻 >= 任务耗时量级（证明记的是**结束**时刻）；
      · 紧接的下轮 due 判定**不成立**（不会因此提前到期）。
    """
    from services import task_scheduler as ts

    _reset_scheduler_state()
    saved_sched = ts.TASK_SCHEDULER_ENABLED
    D = 1.2                              # 任务耗时（秒），远大于下方 interval

    def _slow_handler(task):
        time.sleep(D)
        return {"ok": True, "dummy": True}

    try:
        ts.TASK_SCHEDULER_ENABLED = True
        # interval 远小于耗时 D：旧形态（记开始时刻）必然误判下轮已到期
        t = ts.Task(id="b9t", name="b9", kind="keyword_process",
                    interval=0.3, enabled=True)
        ts.add_task(t)
        ts.register_handler("keyword_process", _slow_handler)

        started = time.time()
        ts._tick()                        # ★ 走真实调度路径（last_run_at=0 ⇒ 必到期）
        finished = time.time()

        gap = t.last_run_at - started
        # 判据①：记的是完成时刻 ⇒ 与开始时刻的差 >= 任务耗时
        #   （旧形态记循环开始的 `now` ⇒ gap ≈ 0 ⇒ 必然 < D）
        is_finish_time = gap >= D * 0.9
        # 判据②：完成时刻记账 ⇒ 下轮不会立刻被判为到期
        #   模拟下轮扫描时刻 = 刚跑完的时刻：距 last_run_at 应 < interval
        due_now = (finished - t.last_run_at) >= t.interval
        # 判据③：任务确实被跑了（防止「因 _running 残留而跳过」造成假绿）
        really_ran = (t.run_count == 1)
        passed = bool(is_finish_time) and (not due_now) and really_ran
        check(passed, "B9",
              f"_tick 的 last_run_at 记完成时刻（gap={gap:.2f}s ≥ 耗时 {D}s ⇒ "
              f"{is_finish_time}；紧接下轮 due={due_now} 应为 False；"
              f"run_count={t.run_count}）")
    finally:
        ts.TASK_SCHEDULER_ENABLED = saved_sched
        _reset_scheduler_state()


def b10_state_update_no_race() -> None:
    """TS-5：并发 N 次下，状态更新不得丢计数。

    锁的正确性**只能靠并发压出来**：单线程跑 N 次无论加不加锁都是 N。
    断言（在「允许执行」与「拒绝」都是 fail-closed 的前提下）：
      · run_count 增量 == 真正被允许执行的次数（handler 命中次数）；
      · fail_count 与 run_count 自洽（被执行过的失败任务才计 fail）；
      · 并发结束后 `_running` 必须回到 False（不得残留 ⇒ 否则调度永久假忙）。
    """
    from services import task_scheduler as ts

    _reset_scheduler_state()
    saved_sched = ts.TASK_SCHEDULER_ENABLED
    N = 12
    hits = []                            # handler 进入时刻列表
    spans = []                           # handler 离开时刻列表
    gate = threading.Barrier(N)          # ★ 让 N 个线程尽量同时发起

    def _handler(task):
        hits.append(time.time())                 # ★ 记录进入时刻
        time.sleep(0.05)                         # 拉长窗口，放大竞态
        spans.append(time.time())                # ★ 记录离开时刻
        return {"ok": True, "dummy": True}

    try:
        ts.TASK_SCHEDULER_ENABLED = True
        t = ts.Task(id="b10t", name="b10", kind="keyword_process",
                    interval=60.0, enabled=True)
        ts.add_task(t)
        ts.register_handler("keyword_process", _handler)

        results = [None] * N

        def _worker(i):
            gate.wait(5.0)
            results[i] = ts.run_task_now("b10t")

        ths = [threading.Thread(target=_worker, args=(i,), daemon=True)
               for i in range(N)]
        for x in ths:
            x.start()
        for x in ths:
            x.join(10.0)

        allowed = len(hits)
        rejected_n = sum(
            1 for r in results
            if isinstance(r, dict) and r.get("ok") is False)
        # 判据①：记账次数 == 真正执行次数（无丢失、无重复记账）
        consistent = (t.run_count == allowed)
        # 判据②：每次调用非此即彼 —— 要么真执行，要么被明确拒绝
        accounted = (allowed + rejected_n == N)
        # 判据③：并发后标志复位（残留 True ⇒ 调度器永久假忙，后续全被拒）
        flag_clear = (ts._running is False)
        # 判据④：全部返回 ok=True ⇒ 不该有 fail_count
        no_false_fail = (t.fail_count == 0)
        # 判据⑤ ★**互斥性**：任意两次执行区间不得**重叠**。
        #   这是「锁真的生效」的**强判据** —— 仅比对计数不够：
        #   旧形态（无锁）下 run_count 也可能恰好等于执行次数
        #   （GIL 让 `+= 1` 在这类短临界区里未必丢），计数判据会**漏报**；
        #   但无锁时两个线程会**同时待在 handler 里** ⇒ 区间必然重叠。
        iv = sorted(zip(hits, spans))
        overlap = any(iv[i][1] > iv[i + 1][0] + 1e-9 for i in range(len(iv) - 1))
        passed = (consistent and accounted and flag_clear
                  and no_false_fail and allowed >= 1 and not overlap)
        check(passed, "B10",
              f"并发 {N} 次状态更新无竞态（真执行 {allowed} + 被拒 {rejected_n} "
              f"= {allowed + rejected_n}/{N}；run_count={t.run_count} "
              f"fail_count={t.fail_count} _running={ts._running!r} "
              f"区间重叠={overlap}）")
    finally:
        ts.TASK_SCHEDULER_ENABLED = saved_sched
        _reset_scheduler_state()


CHECKS = [
    ("B1", b1_runtime_defaults_off),
    ("B2", b2_start_fail_closed),
    ("B3", b3_gate_does_not_raise),
    ("B4", b4_quota_gate_really_called),
    ("B5", b5_gate_really_refuses),
    ("B6", b6_auto_dm_delivery_semantics),
    ("B7", b7_new_send_task_defaults_off),
    ("B8", b8_reentrancy_fail_closed),
    ("B9", b9_last_run_at_is_finish_time),
    ("B10", b10_state_update_no_race),
]


def run() -> int:
    RESULTS.clear()                 # ★ 幂等：重复调用不累计上一轮结果
    print("=" * 72)
    print("ADR-018 F4 定时任务中心 · 风控门禁 v2（**行为断言**，非源码文本匹配）")
    print("=" * 72)
    print(f"  隔离根: {_ROOT}")
    print("-" * 72)
    for gid, fn in CHECKS:
        try:
            fn()
        except Exception as e:                                  # noqa: BLE001
            check(False, gid, f"{fn.__name__} 执行异常: {type(e).__name__}: {e}")
    failed = [r for r in RESULTS if not r[0]]
    print("-" * 72)
    print(f"  合计 {len(RESULTS)} 项，通过 {len(RESULTS) - len(failed)}，未通过 {len(failed)}")
    if failed:
        for _, gid, d in failed:
            print(f"  ⛔ {gid}: {d}")
        return 1
    print("  ✓ 全部通过")
    return 0


def selftest() -> int:
    """D-07 负控：注入 **v1 曾漏掉的**缺陷形态，断言 v2 门禁真的报红。

    每个用例：篡改源码 → 跑门禁 → 断言目标判据变红 → **无条件还原**。
    若锚点已不存在（说明该项已按 A 方案修好），该用例**跳过**并明确标注
    —— 跳过是诚实的（不假装测过），且不会造成假绿。

    D-5b（2026-09-30）中断兜底：原实现只在 `finally` 里还原；**中断**
    （超时 / 强杀 / Ctrl-C / 父进程被杀）会让源码**永久停在注入态**。
    实测踩到：`task_scheduler.py` 一度同时带 `enabled: bool = True` 与
    「注入缺陷（负控）：完全去掉重入保护」注释，进而让 B7/B8/B10 长期假失败
    （并使后续 selftest 以污染文件为 `orig` ⇒ **自我延续**）。
    故此处三重加固：
      ① 运行前校验 `MOD` 工作区==HEAD（否则拒绝执行，避免把污染当基线）；
      ② `atexit` + SIGINT/SIGTERM 处理，任何退出路径都还原；
      ③ 收尾断言源码已回到基线（不静默放过）。
    """
    print("=" * 72)
    print("自检：注入 v1 漏掉的缺陷形态，验证 v2 门禁真的报红（D-07）")
    print("=" * 72)
    MOD = os.path.join(_BACKEND, "services", "task_scheduler.py")
    # ① 基线校验：工作区必须==HEAD，否则拒绝（防「以污染态为基线」自我延续）
    try:
        import subprocess
        rel = os.path.relpath(MOD, _BACKEND).replace(os.sep, "/")
        # 仓库根：从 backend 目录**自身**开始上溯到含 .git 的目录
        #   （勿先 dirname 三层再上溯 —— 会把起点抬到仓库外，实测 git 不可用）
        _r = _BACKEND
        while _r and not os.path.isdir(os.path.join(_r, ".git")):
            _nxt = os.path.dirname(_r)
            if _nxt == _r:
                break
            _r = _nxt
        head = subprocess.run(
            ["git", "-C", _r, "show", f"HEAD:DYAutoDM_v2/backend/{rel}"],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=30)
        if head.returncode == 0:
            cur = open(MOD, encoding="utf-8", newline="").read()
            if cur.replace("\r\n", "\n") != (head.stdout or "").replace("\r\n", "\n"):
                print("  ❌ 拒绝执行：services/task_scheduler.py 工作区 ≠ HEAD")
                print("     （疑似上次注入未还原；先 git checkout 再跑负控）")
                return 1
        else:
            print("  ⚠️ 基线校验跳过（git 不可用），仍执行并在收尾复核还原")
    except Exception as e:                                      # noqa: BLE001
        print(f"  ⚠️ 基线校验异常（跳过）: {type(e).__name__}: {e}")
    orig = open(MOD, encoding="utf-8", newline="").read()

    # ② 任何退出路径都还原
    _restored = {"done": False}

    def _restore() -> None:
        if _restored["done"]:
            return
        try:
            with open(MOD, "w", encoding="utf-8", newline="") as f:
                f.write(orig)
            _restored["done"] = True
        except Exception:                                       # noqa: BLE001
            pass

    atexit.register(_restore)
    _prev_int = None
    try:
        import signal

        def _on_sig(signum, frame):                             # noqa: ANN001
            _restore()
            # 交回默认行为并重发，保持「被中断」的语义
            signal.signal(signum, signal.SIG_DFL)
            os.kill(os.getpid(), signum)

        _prev_int = signal.signal(signal.SIGINT, _on_sig)
        try:
            signal.signal(signal.SIGTERM, _on_sig)
        except Exception:                                       # noqa: BLE001
            pass
    except Exception:                                           # noqa: BLE001
        pass
    # ★ 行尾自适应：被改文件是 CRLF ⇒ 只写 `\n` 的锚点会「命中 0 次」而静默跳过
    #   （负控跳过 = 该判据没有覆盖，比报错更危险）
    _NL = "\r\n" if "\r\n" in orig else "\n"

    def _fix(s_: str) -> str:
        return s_.replace("\n", _NL) if _NL == "\r\n" else s_
    # ⚠️ 锚点必须**指向当前代码形态** —— 修复后旧锚点消失会导致用例被跳过，
    #    而「跳过」= 该判据**没有负控覆盖**（首版实测踩到：B3/B4/B6 全跳过）。
    #    每修一次实现，负控锚点必须同步换成「当前形态改坏」的写法。
    cases = [
        # B3：让**真实调用路径抛异常**（等价于历史 TS-2 的形态）——
        #     把 3 元组的 can_send 当 2 元组解包 ⇒ ValueError ⇒ 被 except 吞成「闸门检查异常」
        ("B3/B4", "把 can_send() 当 2 元组解包（历史 TS-2 形态）",
         "        _cs = quota.can_send()",
         "        _cs = quota.can_send()\n        _a, _b = quota.can_send()   # 注入缺陷：2 元组解包必抛",
         {"B3", "B4"}),
        ("B3/B4b", "把额度闸门整体短路（恒返回 True ⇒ 防恒放行）",
         "        _cs = quota.can_send()",
         "        _cs = (True, \"\", 0.0)   # 注入缺陷：跳过真实闸门",
         {"B4"}),
        ("B6", "把投递语义改回「入池冒充投递已验证」",
         '"delivery_verified": None,   # ★ 未验证：证据由持有服务端响应的一侧写入',
         '"delivery_verified": True,   # 注入缺陷：入池冒充已验证',
         {"B6"}),
        ("B7", "把 Task 默认改回 enabled=True",
         "enabled: bool = False",
         "enabled: bool = True", {"B7"}),
        # ── B8/B10 负控：把 run_task_now 改回「零重入保护」的历史形态（TS-5/TS-4）
        ("B8/B10", "run_task_now 去掉重入保护（历史 TS-4/TS-5 形态）",
         "    with _lock:\n"
         "        if _running:\n"
         "            # fail-closed：不排队、不等待、不重入 —— 明确告知调用方「没跑」\n"
         "            return {\"ok\": False, \"rejected\": True, \"busy\": True,\n"
         "                    \"error\": f\"任务调度器正忙（已有任务在执行中），\"\n"
         "                             f\"已拒绝本次执行: {task_id}\"}\n"
         "        _running = True",
         "    # 注入缺陷（负控）：完全去掉重入保护 ⇒ 并发可重入",
         {"B8", "B10"}),
        # ── B10 负控：判重入与置位**不在同一临界区**（TOCTOU）
        ("B10b", "重入判定与置位分离（TOCTOU：锁内查、锁外设）",
         "    with _lock:\n"
         "        if _running:\n"
         "            # fail-closed：不排队、不等待、不重入 —— 明确告知调用方「没跑」\n"
         "            return {\"ok\": False, \"rejected\": True, \"busy\": True,\n"
         "                    \"error\": f\"任务调度器正忙（已有任务在执行中），\"\n"
         "                             f\"已拒绝本次执行: {task_id}\"}\n"
         "        _running = True",
         "    if not _running:\n"
         "        time.sleep(0.05)      # 注入缺陷：锁外判定 + 延迟置位 = TOCTOU\n"
         "    _running = True",
         {"B8", "B10"}),
        # ── B9 负控：把 _tick 的 last_run_at 改回「扫描开始时刻 now」（MC-2 原形态）
        ("B9", "_tick 的 last_run_at 改回开始时刻 now（历史 MC-2 形态）",
         "                finished = time.time()\n"
         "                with _lock:\n"
         "                    t.last_run_at = finished",
         "                with _lock:\n"
         "                    t.last_run_at = now   # 注入缺陷：记开始时刻",
         {"B9"}),
    ]
    all_ok = True
    for tag, desc, old, new, expect_red in cases:
        old, new = _fix(old), _fix(new)
        if orig.count(old) != 1:
            print(f"  ⏭ [{tag}] 跳过（锚点命中 {orig.count(old)} 次，该项可能已按 A 修复）: {desc}")
            continue
        print(f"  ▶ [{tag}] {desc}")
        try:
            with open(MOD, "w", encoding="utf-8", newline="") as f:
                f.write(orig.replace(old, new, 1))
            _reload_target()          # ★ 清缓存：让本轮读到**篡改后**的代码
            global RESULTS
            RESULTS = []
            for gid, fn in CHECKS:
                try:
                    fn()
                except Exception:                              # noqa: BLE001
                    RESULTS.append((False, gid, "异常"))
            got_red = {gid for ok, gid, _ in RESULTS if not ok}
            hit = got_red & expect_red
            if hit:
                print(f"    ✅ 如期报红: {sorted(hit)}")
            else:
                print("    ❌ 未报红 ⇒ 门禁未覆盖该形态！")
                all_ok = False
        finally:
            with open(MOD, "w", encoding="utf-8", newline="") as f:
                f.write(orig)
            _reload_target()          # ★ 清缓存：让下一轮读到**已还原**的代码

    print("-" * 72)
    # ③ 收尾复核：任何路径都必须已还原（中断兜底之外的最后一道）
    try:
        _final = open(MOD, encoding="utf-8", newline="").read()
        _same = (_final.replace("\r\n", "\n")
                 == orig.replace("\r\n", "\n"))
    except Exception as e:                                      # noqa: BLE001
        _final, _same = "", False
        print(f"  ❌ 收尾复核读文件失败: {type(e).__name__}: {e}")
    if not _same:
        print("  ❌ 收尾复核失败：services/task_scheduler.py 未还原到基线")
        print("     ⇒ 已强制还原，请复查 git diff（不得把注入态提交）")
        _restore()
        all_ok = False
    # 恢复原 SIGINT 处理（不留全局副作用）
    if _prev_int is not None:
        try:
            import signal
            signal.signal(signal.SIGINT, _prev_int)
        except Exception:                                       # noqa: BLE001
            pass
    RESULTS = []
    _reload_target()                  # ★ 复跑前再清一次，确保读磁盘真态
    rc = run()
    print(f"\n  还原后复跑 exit = {rc}")
    if not all_ok:
        print("  ✗ 自检失败：存在门禁未覆盖的缺陷形态 ⇒ 需收紧判据")
        return 1
    print("  ✓ 自检通过")
    return 0 if rc == 0 else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(selftest())
    raise SystemExit(run())


# ===========================================================================
# unittest 桥接 —— 让「门禁脚本」能被标准 unittest 直接跑
#
# 为什么需要：本文件原本是**脚本**（`__main__` 直接 run()），
# `python -m unittest test_task_scheduler_gates` 会 **Ran 0 tests**（空跑全绿）——
# 这是本项目最危险的一类「假绿」：**跑了、没报错、但其实一条都没测**。
# 故在此把每条判据桥成一个 TestCase：
#   · 每条判据单独成一个 test（失败可定位到 B 号）；
#   · 新增 `test_selftest` —— 跑**负控并断言其真的报红**（元门禁）。
# ===========================================================================
class GateTestCase(unittest.TestCase):
    """把每个 B 判据桥成一个 unittest 用例。"""

    def setUp(self):
        # N1_M28_PIN_ROOT：执行期重钉根 —— 本门禁所有判据都读运行时状态，
        # 若根被别的模块改写，读到的配置/库就属于别人 ⇒ 顺序相关假失败。
        _n1_m28_pin_root()

    def _run_one_gate(self, gid: str, fn) -> None:
        RESULTS.clear()
        try:
            fn()
        except Exception as e:                                  # noqa: BLE001
            check(False, gid, f"{fn.__name__} 执行异常: {type(e).__name__}: {e}")
        recs = [r for r in RESULTS if r[1] == gid]
        # 判据没产出任何结果 = 没测到（不得算通过）
        self.assertTrue(recs, f"{gid} 未产出任何判据结果（门禁未执行）")
        for ok, _gid, desc in recs:
            self.assertTrue(ok, f"{gid} 未通过: {desc}")


def _make_test(gid: str, fn):
    def _t(self):
        self._run_one_gate(gid, fn)
    _t.__name__ = f"test_{gid}"
    _t.__doc__ = (fn.__doc__ or "").strip().split("\n")[0] or gid
    return _t


for _gid, _fn in CHECKS:
    setattr(GateTestCase, f"test_{_gid}", _make_test(_gid, _fn))


class SelfTestNegativeControl(unittest.TestCase):
    """元门禁：负控装置本身必须有效（注入缺陷 ⇒ 判据真的报红）。"""

    def test_selftest(self):
        rc = selftest()
        self.assertEqual(rc, 0, "负控自检未通过：存在门禁未覆盖的缺陷形态")
