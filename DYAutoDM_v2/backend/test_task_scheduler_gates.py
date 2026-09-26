# -*- coding: utf-8 -*-
"""H-22 审计外 · ADR-018 F4 定时任务中心 门禁（防回归 + 负控）。

## 为什么单独建这个文件

F4（定时自动发私信）是本项目**迄今风控敞口最大**的功能。
用户 2026-09-27 拍板（ADR-018 D1）：**默认休眠 enabled=False**。

「默认关闭」这种约束**极易在后续迭代中被无意打开** ——
一次随手改默认值、一次调试时 export 环境变量，都会让自动外发悄悄上线。
故必须把它固化成**会真变红**的门禁（铁律：声明式规则 = 空架子）。

## 判据（逐条对应 ADR-018）

| # | 判据 | 为什么 |
|---|---|---|
| G1 | 源码默认值 `TASK_SCHEDULER_ENABLED` **必须**默认 False | D1 默认休眠 |
| G2 | 源码默认值 `AUTO_SEND_ENABLED` **必须**默认 False | D1（外发独立可控） |
| G3 | 外发类任务执行前**必须**过 `_gate_for_send` | D4 三重闸门 |
| G4 | `_gate_for_send` 必须含**时段闸门**调用 | D4 闸门③ |
| G5 | `_gate_for_send` 必须含**额度闸门**调用（can_send / can_stranger_first） | D4 闸门①②，且复用既有不自造 |
| G6 | 外发结果**必须**校验 `delivery_verified`（无回执不认成功） | M-5 + 用户铁律 |
| G7 | `start()` 在总开关关闭时**必须拒绝**（fail-closed） | D1 |

## 负控（D-07：验收判据必须自证「失败态会变红」）

`--selftest` 模式：注入「把默认 False 改成 True」等违规形态，
断言 G1/G2/G3/G6 精确变红。不变红 = 门禁形同虚设。
"""

from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))          # backend/
MOD = os.path.join(ROOT, "services", "task_scheduler.py")

RESULTS: list = []


def check(ok: bool, gid: str, desc: str) -> None:
    RESULTS.append((bool(ok), gid, desc))
    print(f"  [{'PASS' if ok else 'FAIL'}] {gid}  {desc}")


def _src() -> str:
    with open(MOD, encoding="utf-8", errors="replace") as f:
        return f.read()


def g1_scheduler_default_off() -> None:
    s = _src()
    m = re.search(r'TASK_SCHEDULER_ENABLED\s*=\s*_env_bool\(\s*"DY_TASK_SCHEDULER_ENABLED"\s*,\s*(\w+)\s*\)', s)
    ok = bool(m) and m.group(1) == "False"
    check(ok, "G1",
          f"TASK_SCHEDULER_ENABLED 默认关闭（实测默认参数 = {m.group(1) if m else '未匹配'}）")


def g2_auto_send_default_off() -> None:
    s = _src()
    m = re.search(r'AUTO_SEND_ENABLED\s*=\s*_env_bool\(\s*"DY_AUTO_SEND_ENABLED"\s*,\s*(\w+)\s*\)', s)
    ok = bool(m) and m.group(1) == "False"
    check(ok, "G2",
          f"AUTO_SEND_ENABLED 默认关闭（实测默认参数 = {m.group(1) if m else '未匹配'}）")


def g3_gate_before_send() -> None:
    s = _src()
    ok = ("_gate_for_send(task)" in s and "if needs_send:" in s)
    check(ok, "G3", "外发类任务执行前调用 _gate_for_send（ADR-018 D4）")


def g4_hour_gate() -> None:
    s = _src()
    ok = "within_active_hours()" in s
    check(ok, "G4", "闸门含时段检查 within_active_hours()")


def g5_quota_gate() -> None:
    s = _src()
    ok = ("can_send()" in s and "can_stranger_first()" in s)
    check(ok, "G5", "闸门复用既有额度仲裁 can_send/can_stranger_first（不自造）")


def g6_delivery_verify() -> None:
    """G6 必须锚定**实际生效的判定语句**，不能只匹配常量名。

    教训（本次实测踩到）：初版判据用 `"REQUIRE_DELIVERY_VERIFY" in s` ——
    常量声明与 import 仍在，注入违规（把 `if needs_send and REQUIRE_DELIVERY_VERIFY:`
    改成 `if False:`）后**字符串依然命中** ⇒ G6 不变红 = 门禁形同虚设。
    判据必须锚定**被执行的分支条件本身**。
    """
    s = _src()
    ok = ("if needs_send and REQUIRE_DELIVERY_VERIFY:" in s
          and "delivery_verified" in s
          and "无投递验证回执" in s)
    check(ok, "G6", "外发结果校验 delivery_verified 的判定分支真实存在（无回执不认成功）")


def g7_start_fail_closed() -> None:
    s = _src()
    ok = "if not TASK_SCHEDULER_ENABLED:" in s and "拒绝启动" in s
    check(ok, "G7", "start() 在总开关关闭时拒绝启动（fail-closed）")


CHECKS = [
    ("G1", g1_scheduler_default_off),
    ("G2", g2_auto_send_default_off),
    ("G3", g3_gate_before_send),
    ("G4", g4_hour_gate),
    ("G5", g5_quota_gate),
    ("G6", g6_delivery_verify),
    ("G7", g7_start_fail_closed),
]


def run() -> int:
    print("=" * 70)
    print("ADR-018 F4 定时任务中心 · 风控门禁（防自动外发悄悄上线）")
    print("=" * 70)
    print(f"  目标模块: {MOD}")
    print("-" * 70)
    for gid, fn in CHECKS:
        try:
            fn()
        except Exception as e:                              # noqa: BLE001
            check(False, gid, f"{fn.__name__} 执行异常: {type(e).__name__}: {e}")
    failed = [r for r in RESULTS if not r[0]]
    print("-" * 70)
    print(f"  合计 {len(RESULTS)} 项，通过 {len(RESULTS) - len(failed)}，未通过 {len(failed)}")
    if failed:
        for _, gid, d in failed:
            print(f"  ⛔ {gid}: {d}")
        return 1
    print("  ✓ 全部通过")
    return 0


def selftest() -> int:
    """D-07 负控：注入违规形态，断言门禁**真的会变红**。"""
    print("=" * 70)
    print("自检：注入违规形态，验证门禁真的会报红（D-07）")
    print("=" * 70)
    orig = _src()
    # 违规形态：把两个默认 False 改成 True（模拟「随手打开自动外发」）
    evil = orig.replace('_env_bool("DY_TASK_SCHEDULER_ENABLED", False)',
                        '_env_bool("DY_TASK_SCHEDULER_ENABLED", True)')
    evil = evil.replace('_env_bool("DY_AUTO_SEND_ENABLED", False)',
                        '_env_bool("DY_AUTO_SEND_ENABLED", True)')
    # 违规形态 2：删掉投递验证（模拟「无回执也认成功」）
    evil2 = evil.replace("if needs_send and REQUIRE_DELIVERY_VERIFY:",
                         "if False:")
    expect_red = {"G1", "G2", "G6"}

    got_red = set()
    try:
        with open(MOD, "w", encoding="utf-8") as f:
            f.write(evil2)
        global RESULTS
        RESULTS = []
        for gid, fn in CHECKS:
            try:
                fn()
            except Exception:                                # noqa: BLE001
                RESULTS.append((False, gid, "异常"))
        got_red = {gid for ok, gid, _ in RESULTS if not ok}
    finally:
        with open(MOD, "w", encoding="utf-8") as f:
            f.write(orig)

    missing = expect_red - got_red
    print("-" * 70)
    print(f"  期望报红: {sorted(expect_red)}")
    print(f"  实际报红: {sorted(got_red)}")
    if missing:
        print(f"\n✗ 自检失败：以下判据在违规形态下没有变红 = 形同虚设: {sorted(missing)}")
        return 1
    print("\n✓ 自检通过：违规形态下门禁确实报红（非空架子）")
    # 还原后复跑，确认真的是还原
    RESULTS = []
    rc = run()
    print("\n  还原后复跑 exit =", rc)
    return 0 if rc == 0 else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(selftest())
    raise SystemExit(run())
