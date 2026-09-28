# coding=utf-8
"""周期性日志节流原语（2026-09-28 新增）。

## 为什么需要（同一故障的第三次复发）

本项目已连续三次踩「周期性路径无节流 ⇒ 日志无界增长」：

| 版本 | 位置 | 症状 |
|---|---|---|
| v0.45.82 | 前端 `frontend_boot.log` | 31MB / 28 万行噪声 |
| v0.45.83 | 前端 overview 轮询 | 同类漏网，补修 |
| **本轮** | **后端 BCC** | `BCC-080` 单日 **1195 条**（每 3s 一条） |

前两次的修复都落在**各自那一条通道**上（写入端轮转 / 单通道去重），
**没有产生可复用原语** ⇒ 第三次必然复发。本模块即为该原语的落点。

## 设计契约

1. **只节流「同一件事的重复陈述」，不吞掉首次告警** —— 首次必记，
   静默期内的重复只在**末尾汇总一次**（带计数），保证「发生过」这条
   事实不丢失。这与「静默失败」相反：静默是无声丢弃，本原语是可回捞的压缩。
2. **key 维度由调用方给定**：同一 key 才合并。默认 key 含 code + 账号，
   避免不同账号/不同错误码互相遮蔽。
3. **零新增风控面**：纯内存计数，无网络、无文件 IO（除日志本身）。
4. **线程安全**：后端存在 FastAPI 事件循环与 `run_keepalive` 子线程
   并发写日志的场景（与 `bcc_lease` 同款约束），故用 `threading.RLock`。
5. **可测试**：`reset()` 供测试清理全局态；`snapshot()` 供观测。

用法
----
    from utils.log_throttle import should_log

    if should_log("BCC-080", account, interval=30.0):
        logger.warning(f"[BCC-080] ...")   # 首次或静默期已过 ⇒ 真记

静默期内的调用返回 False（不记），但在静默期结束后的**第一次**调用
返回 True，由调用方补记一条汇总（见 `pending_count`）。
"""

from __future__ import annotations

import threading
import time
from typing import Dict, Optional, Tuple

# {key: (first_ts, last_ts, suppressed_count)}
_state: Dict[str, Tuple[float, float, int]] = {}
_lock = threading.RLock()

# 默认静默期（秒）。调用方应当显式传 interval —— 本值只是兜底，
# 保证「忘记传参」时仍有节流效果，而不是无限刷屏。
DEFAULT_INTERVAL = 30.0


def _key(code: str, subject: str = "") -> str:
    return f"{code}|{subject or ''}"


def should_log(code: str, subject: str = "",
               interval: Optional[float] = None) -> bool:
    """本次是否应当真正落日志。True ⇒ 记；False ⇒ 静默期内，不记。

    code    错误码或告警标识（如 "BCC-080"）
    subject 主体（通常是账号名）；同一 code 不同主体**分别**计数
    interval 静默期秒数；None ⇒ DEFAULT_INTERVAL
    """
    iv = DEFAULT_INTERVAL if interval is None else float(interval)
    k = _key(code, subject)
    now = time.time()
    with _lock:
        hit = _state.get(k)
        if hit is None:
            _state[k] = (now, now, 0)
            return True
        _first, _last, _n = hit
        if (now - _last) >= iv:
            # 静默期已过 ⇒ 放行一次（调用方补记汇总后应 reset 计数）
            _state[k] = (_first, now, 0)
            return True
        _state[k] = (_first, _last, _n + 1)
        return False


def pending_count(code: str, subject: str = "") -> int:
    """当前静默期内被抑制的条数（供汇总文案使用）。"""
    k = _key(code, subject)
    with _lock:
        hit = _state.get(k)
        return int(hit[2]) if hit else 0


def reset(code: str = "", subject: str = "") -> None:
    """清理计数（汇总已记出 / 状态变迁 / 测试清理时用）。"""
    with _lock:
        if not code:
            _state.clear()
            return
        _state.pop(_key(code, subject), None)


def snapshot() -> Dict[str, Dict[str, float]]:
    """观测用：当前节流状态快照。"""
    now = time.time()
    with _lock:
        return {
            k: {"first_age_sec": round(now - v[0], 1),
                "last_age_sec": round(now - v[1], 1),
                "suppressed": int(v[2])}
            for k, v in _state.items()
        }
