# -*- coding: utf-8 -*-
"""知识维护与自动学习（v0.40，移植自 MalogBot 的知识演化体系）。

职责
----
1. **自动学习**（周期跑，全自动落库）：扫描 dm_messages 成功会话 →
   LLM 提炼「客户问法 → 可复用话术」→ 写入对话回复库（命中库）。
   安全依据：只增不删。
2. **维护扫描**（周期跑，只出报告）：三档陈旧检测
   - cold      冷数据   ：hits=0 且 存在 > 180 天
   - low_value 低价值   ：importance<0.3 且 hits=0 且 存在 > 90 天
   - duplicate 重复     ：同主题内两两余弦 >= 0.95
   **扫描绝不删数据**，只生成报告；用户在前端勾选确认后才进回收站。
3. **回收站清理**（周期跑，全自动）：超过 30 天的回收站条目真删。
4. **常驻定时器**：threading.Timer 循环，默认 84 小时一轮，
   启动后延迟 30 分钟首次执行（避开启动初始化）。

用户拍板（2026-09-11）
---------------------
- 删除一律「扫描出报告 → 人工确认 → 进回收站 → 30 天后真删」。
- 学习周期 84 小时；调度用后端常驻定时器。
"""

from __future__ import annotations

import json
import threading
import time
import traceback
from typing import Optional

from services import pro_kb
from services import reply_kb

# 周期（小时）——用户指定 84
LEARN_INTERVAL_HOURS = 84
FIRST_DELAY_SECONDS = 30 * 60          # 启动后 30 分钟首次
_HOUR = 3600.0

_lock = threading.RLock()
_timer: Optional[threading.Timer] = None
_state = {
    "enabled": False,
    "interval_hours": LEARN_INTERVAL_HOURS,
    "last_run_at": None,
    "last_run_result": None,
    "last_scan": None,
    "next_run_at": None,
    "runs": 0,
    "errors": [],
}


def _log(code: str, msg: str) -> None:
    try:
        from loguru import logger as _lg
        _lg.warning(code, msg)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 1) 自动学习（全自动）
# ---------------------------------------------------------------------------

def run_learn_job(account: str = "", limit: int = 200) -> dict:
    """扫描聊天记录 → LLM 提炼话术 → 入对话回复库。只增不删，安全。"""
    t0 = time.time()
    try:
        res = reply_kb.learn_from_history(account=account, limit=limit)
        out = {"ok": True, "job": "learn", "result": res,
               "elapsed": round(time.time() - t0, 1)}
        _log("KB-101", f"[kb_maintain] 自动学习完成: {res}")
        return out
    except Exception as e:
        _log("KB-102", f"[kb_maintain] 自动学习失败: {e}")
        return {"ok": False, "job": "learn", "error": str(e)}


# ---------------------------------------------------------------------------
# 2) 维护扫描（只报告，不删）
# ---------------------------------------------------------------------------

def run_scan_job() -> dict:
    """三档陈旧扫描，返回报告（不修改任何数据）。"""
    t0 = time.time()
    try:
        report = pro_kb.scan_stale()
        # 给扫描到的条目打 is_stale 标记（纯标记，便于界面高亮；不影响数据）
        cold_ids = [r["id"] for r in report.get("cold", [])]
        low_ids = [r["id"] for r in report.get("low_value", [])]
        if cold_ids:
            pro_kb.mark_stale(cold_ids, reason="cold")
        if low_ids:
            pro_kb.mark_stale(low_ids, reason="low_value")
        report["scanned_at"] = time.time()
        report["elapsed"] = round(time.time() - t0, 1)
        with _lock:
            _state["last_scan"] = report
        _log("KB-103", "[kb_maintain] 维护扫描完成: "
                       f"cold={len(cold_ids)} low={len(low_ids)} "
                       f"dup={len(report.get('duplicate', []))}")
        return {"ok": True, "job": "scan", "report": report}
    except Exception as e:
        _log("KB-104", f"[kb_maintain] 维护扫描失败: {e}")
        return {"ok": False, "job": "scan", "error": str(e)}


def get_last_scan() -> Optional[dict]:
    with _lock:
        return _state.get("last_scan")


# ---------------------------------------------------------------------------
# 3) 回收站清理（全自动，30 天）
# ---------------------------------------------------------------------------

def run_purge_job(days: int = pro_kb.RECYCLE_DAYS) -> dict:
    try:
        n = pro_kb.purge_expired(days=days)
        if n:
            _log("KB-105", f"[kb_maintain] 回收站清理: 真删 {n} 条(>{days}天)")
        return {"ok": True, "job": "purge", "purged": n, "days": days}
    except Exception as e:
        _log("KB-106", f"[kb_maintain] 回收站清理失败: {e}")
        return {"ok": False, "job": "purge", "error": str(e)}


# ---------------------------------------------------------------------------
# 4) 常驻定时器
# ---------------------------------------------------------------------------

def run_all_jobs() -> dict:
    """一轮完整任务：学习 -> 扫描 -> 回收站清理。"""
    _log("KB-100", "[kb_maintain] 周期任务开始")
    result = {
        "started_at": time.time(),
        "learn": run_learn_job(),
        "scan": run_scan_job(),
        "purge": run_purge_job(),
    }
    result["elapsed"] = round(time.time() - result["started_at"], 1)
    with _lock:
        _state["last_run_at"] = time.time()
        _state["last_run_result"] = {
            k: (v if k != "scan" else {"ok": v.get("ok"),
                                       "scanned": (v.get("report") or {}).get("scanned"),
                                       "cold": len((v.get("report") or {}).get("cold", [])),
                                       "low": len((v.get("report") or {}).get("low_value", [])),
                                       "dup": len((v.get("report") or {}).get("duplicate", []))})
            for k, v in result.items() if k != "elapsed"
        }
        _state["runs"] = int(_state.get("runs") or 0) + 1
        _state["next_run_at"] = time.time() + _state["interval_hours"] * _HOUR
    _log("KB-107", f"[kb_maintain] 周期任务完成 elapsed={result['elapsed']}s")
    return result


def _tick(interval_hours: float) -> None:
    """定时器回调：跑任务 -> 重新排下一次。异常不打断循环。"""
    try:
        run_all_jobs()
    except Exception as e:
        with _lock:
            _state["errors"].append({"at": time.time(), "error": str(e)})
            _state["errors"] = _state["errors"][-20:]
        _log("KB-108", f"[kb_maintain] 周期任务异常: {traceback.format_exc(limit=3)}")
    finally:
        _schedule(interval_hours)


def _schedule(interval_hours: float) -> None:
    global _timer
    with _lock:
        need = float(interval_hours or LEARN_INTERVAL_HOURS) * _HOUR
        if _timer is not None:
            try:
                _timer.cancel()
            except Exception:
                pass
        _timer = threading.Timer(need, _tick, args=(interval_hours,))
        _timer.daemon = True
        _timer.start()
        _state["next_run_at"] = time.time() + need


def start_scheduler(interval_hours: float = LEARN_INTERVAL_HOURS,
                    first_delay_seconds: float = FIRST_DELAY_SECONDS) -> dict:
    """启动常驻定时器（幂等）。"""
    global _timer
    with _lock:
        if _state.get("enabled") and _timer is not None:
            return {"ok": True, "already": True, "state": get_state()}
        _state["interval_hours"] = float(interval_hours or LEARN_INTERVAL_HOURS)
    delay = max(30.0, float(first_delay_seconds or 0))
    with _lock:
        if _timer is not None:
            try:
                _timer.cancel()
            except Exception:
                pass
        _timer = threading.Timer(delay, _tick, args=(_state["interval_hours"],))
        _timer.daemon = True
        _timer.start()
        _state["enabled"] = True
        _state["next_run_at"] = time.time() + delay
    _log("KB-099", f"[kb_maintain] 定时器已启动: 每 {interval_hours}h 一轮, "
                   f"首次 {round(delay / 60)} 分钟后")
    return {"ok": True, "already": False, "state": get_state()}


def stop_scheduler() -> dict:
    global _timer
    with _lock:
        if _timer is not None:
            try:
                _timer.cancel()
            except Exception:
                pass
            _timer = None
        _state["enabled"] = False
        _state["next_run_at"] = None
    return {"ok": True, "state": get_state()}


def get_state() -> dict:
    with _lock:
        return dict(_state)


# ---------------------------------------------------------------------------
# 5) 维护执行（人工确认后）
# ---------------------------------------------------------------------------

def apply_report(items: list, action: str = "recycle") -> dict:
    """执行用户确认的维护项：条目进回收站（默认）或打标记。"""
    ids = []
    for it in items or []:
        if isinstance(it, dict):
            _id = it.get("id") or it.get("drop_id")
        else:
            _id = it
        if _id is not None:
            ids.append(_id)
    if not ids:
        return {"ok": False, "error": "未选择任何条目"}
    return pro_kb.apply_maintenance(ids, action=action)


def merge_duplicate_pairs(pairs: list) -> dict:
    """按重复报告合并：保留 keep_id，drop_id 进回收站。"""
    drop_ids = []
    for p in pairs or []:
        if isinstance(p, dict) and p.get("drop_id") is not None:
            drop_ids.append(p["drop_id"])
    if not drop_ids:
        return {"ok": False, "error": "未选择任何重复项"}
    return pro_kb.apply_maintenance(drop_ids, action="recycle")


__all__ = [
    "run_learn_job", "run_scan_job", "run_purge_job", "run_all_jobs",
    "start_scheduler", "stop_scheduler", "get_state",
    "get_last_scan", "apply_report", "merge_duplicate_pairs",
    "LEARN_INTERVAL_HOURS",
]
