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

# ── 隔离根：必须在 import 业务模块**之前**设置（app_root() 会忽略不存在的路径）
_ROOT = os.path.join(tempfile.gettempdir(), "f4_gate_v2")
os.makedirs(os.path.join(_ROOT, "members"), exist_ok=True)
os.environ["DY_APP_ROOT"] = _ROOT
# 门禁自身只做只读行为断言 ⇒ 不带外部开关（各用例内临时改运行时常量）
os.environ.pop("DY_TASK_SCHEDULER_ENABLED", None)
os.environ.pop("DY_AUTO_SEND_ENABLED", None)

_BACKEND = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _BACKEND)

RESULTS: list = []


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
    try:
        ts.AUTO_SEND_ENABLED = True
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


CHECKS = [
    ("B1", b1_runtime_defaults_off),
    ("B2", b2_start_fail_closed),
    ("B3", b3_gate_does_not_raise),
    ("B4", b4_quota_gate_really_called),
    ("B5", b5_gate_really_refuses),
    ("B6", b6_auto_dm_delivery_semantics),
    ("B7", b7_new_send_task_defaults_off),
]


def run() -> int:
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
    """
    print("=" * 72)
    print("自检：注入 v1 漏掉的缺陷形态，验证 v2 门禁真的报红（D-07）")
    print("=" * 72)
    MOD = os.path.join(_BACKEND, "services", "task_scheduler.py")
    orig = open(MOD, encoding="utf-8", newline="").read()
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
