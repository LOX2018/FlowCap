# -*- coding: utf-8 -*-
"""并发压力验证：租约无锁竞态修复（2026-09-17 OCR HIGH）。

验证目标
--------
原实现 `_lease_acquire` 是「读 _lease_current() → 判 cur is None → _lease.update()」
三步非原子，多线程并发申请时可**同时通过判空检查** → 双写覆盖 →
后写者 lease_id 生效，先写者的 id 从此无法 release（返回 not_holder）。

本脚本用**同一份判定逻辑**做对照实验（不导入 browser_daemon，避免其重依赖）：
  A. 旧实现（无锁）     —— 期望复现「多个线程同时拿到租约」的竞争
  B. 新实现（RLock）    —— 期望任意时刻**最多一个**线程持有

通过标准：A 组能观测到竞争（证明缺陷真实），B 组零竞争（证明修复有效）。

用法：cd DYAutoDM_v2/backend && python daemon/_verify_lease_lock.py
"""
import sys
import threading
import time

# --------------------------------------------------------------------------
# A 组：旧实现（无锁，逐字复刻原三步逻辑）
# --------------------------------------------------------------------------
_old_lock = {"holder": None, "lease_id": None, "expires_at": 0.0}
_old_lock_meta = {}  # 不加任何锁


def old_acquire(holder: str):
    """复刻原实现：读 → 判 → 写，三步之间无互斥。"""
    cur = None
    if _old_lock["holder"] and time.time() <= _old_lock["expires_at"]:
        cur = dict(_old_lock)
    if cur is not None:
        return {"ok": False, "busy": cur["holder"]}
    # ▼ 关键：此处让出，放大并发窗口（原实现里这之间是 logger/uuid 调用）
    time.sleep(0.0005)
    _old_lock.update(holder=holder, lease_id=holder,
                     expires_at=time.time() + 60)
    return {"ok": True, "lease_id": holder}


# --------------------------------------------------------------------------
# B 组：新实现（RLock 包住整个读-判-写）
# --------------------------------------------------------------------------
_new_lock = threading.RLock()
_new_state = {"holder": None, "lease_id": None, "expires_at": 0.0}


def new_acquire(holder: str):
    """复刻修补后的实现：整个「读-判-写」在临界区内。"""
    with _new_lock:
        cur = None
        if _new_state["holder"] and time.time() <= _new_state["expires_at"]:
            cur = dict(_new_state)
        if cur is not None:
            return {"ok": False, "busy": cur["holder"]}
        time.sleep(0.0005)  # 同样的放大窗口，锁内执行 → 无竞争
        _new_state.update(holder=holder, lease_id=holder,
                          expires_at=time.time() + 60)
        return {"ok": True, "lease_id": holder}


def run(acquire_fn, n_threads=32) -> tuple:
    """并发申请，返回 (成功数, 成功者列表)。每轮前重置状态。"""
    if acquire_fn is old_acquire:
        _old_lock.update(holder=None, lease_id=None, expires_at=0.0)
    else:
        with _new_lock:
            _new_state.update(holder=None, lease_id=None, expires_at=0.0)
    ok_holders, lock = [], threading.Lock()

    def worker(i):
        r = acquire_fn(f"t{i}")
        if r.get("ok"):
            with lock:
                ok_holders.append(r["lease_id"])

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    return len(ok_holders), ok_holders


def main() -> int:
    print("=" * 62)
    print("A 组：旧实现（无锁）—— 期望复现竞争")
    print("=" * 62)
    old_bad = 0
    for i in range(30):
        n, holders = run(old_acquire)
        if n > 1:
            old_bad += 1
            if old_bad <= 3:
                print(f"  第{i+1}轮：{n} 个线程同时拿到租约 {holders[:4]}"
                      f"{' ...' if n > 4 else ''}  ← 竞态复现")
    print(f"  → 30 轮中有 {old_bad} 轮出现「多人同时持有」（应 >0，证明缺陷真实）")

    print()
    print("=" * 62)
    print("B 组：新实现（RLock 临界区）—— 期望零竞争")
    print("=" * 62)
    new_bad = 0
    for i in range(30):
        n, holders = run(new_acquire)
        if n > 1:
            new_bad += 1
            print(f"  第{i+1}轮：{n} 个线程同时拿到租约 {holders}  ← 修复无效！")
    print(f"  → 30 轮中有 {new_bad} 轮出现「多人同时持有」（应 ==0）")

    print()
    ok = (old_bad > 0) and (new_bad == 0)
    if ok:
        print("结论：✓ 缺陷真实（旧）且修复有效（新）")
    else:
        print("结论：✗ 未达预期（旧未复现 或 新仍竞争）")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
