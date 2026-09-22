# coding=utf-8
"""浏览器租约（Lease）—— BCC 资源所有权单一事实源。

**为什么需要**（2026-09-13 调度器体系 S2）：
  browser_gate.py 的独占分支读一个/status从不返回的字段 → 静默假成功。
  租约取代「让出 profile」：BCC 始终是 profile 的唯一所有者，
  业务操作向 BCC 申请租约，TTL 到期自动释放。

**关键设计决策**：
1. 模块级可变 dict + threading.RLock（不是 asyncio.Lock）：
   keepalive 跑在子线程，HTTP 端点在 FastAPI 事件循环，两者都读写 _lease，
   必须用跨线程锁。RLock 可重入（_lease_acquire 内调 _lease_current）。
2. 惰性 TTL（无后台定时器）：任何读取都先检查 expires_at。
   持有者崩溃/忘记 release 时，到期自动释放，不永久独占。
3. 不做真抢占：浏览器操作不可中断。用「P2 限时 + 快速失败」解决霸占。

**使用者**：
  daemon/browser_daemon.py 的 _exec() / scan_login / 保活心跳。
  HTTP 端点 /lease /lease/renew /lease/release。
  _scan_exclusive 也由本模块管理（与租约共用同一把锁）。
"""

from __future__ import annotations

import threading
import time
from typing import Any

from loguru import logger


# 优先级与 TTL 硬上限
# 2026-09-14 v0.43.11：P0 300 -> 600s。实机实测「更新会话」整轮可达 302s+
# （滚动 10+ 轮 × 每轮 30~40s），放宽到 600s 覆盖真实耗时。
LEASE_PRIO_TTL_LIMIT = {0: 600.0, 1: 180.0, 2: 30.0}
LEASE_PRIO_NAME = {0: "用户显式", 1: "业务自动", 2: "后台保活"}

# 2026-09-17 修补（OCR 审查 HIGH）：租约与 _scan_exclusive 的互斥锁。
# 背景：`_lease` / `_scan_exclusive` 是模块级可变 dict，读写出现在两条
# 不同线程上：HTTP 端点（FastAPI 主事件循环）与 run_keepalive
# （threading.Thread）。原先所有读写完全无锁。
# 用 threading.RLock（不是 asyncio.Lock）：因为 keepalive 跑在子线程，
# 且 _lease_acquire 内部会再调 _lease_current（可重入），RLock 最合适。
_lease_lock = threading.RLock()

_scan_exclusive: dict[str, Any] = {"holder": None}  # None=空闲；否则是独占操作名

_lease: dict[str, Any] = {
    "holder": None, "purpose": None, "prio": None, "lease_id": None,
    "acquired_at": 0.0, "ttl": 0.0, "expires_at": 0.0, "renew_count": 0,
}


# ============================================================================
# 独占标志（兼容 _scan_exclusive 旧检查）
# ============================================================================




class ContainerBusy(Exception):
    """Container is busy with an exclusive operation (scan_login / context rebuild).

    Caught by global exception handler (browser_daemon.py) to return
    HTTP 200 {ok:false, busy:"scan_login"} instead of queueing.
    Coordinated with _scan_exclusive + lease system.
    """
    def __init__(self, holder: str = "scan_login"):
        self.holder = holder
        super().__init__(f"ContainerBusy: {holder}")



def _is_busy() -> bool:
    """检查是否有独占操作在运行（适配旧调用点）。"""
    with _lease_lock:
        return _scan_exclusive["holder"] is not None


def _scan_exclusive_set(holder: str | None) -> None:
    """写入独占标志（None=空闲）。与租约共用同一把锁，保证状态一致。"""
    with _lease_lock:
        _scan_exclusive["holder"] = holder


def _scan_exclusive_get() -> str | None:
    """原子读取独占标志（None=空闲）。避免「判空 + 取值」两次读的 TOCTOU。"""
    with _lease_lock:
        return _scan_exclusive["holder"]


# ============================================================================
# 租约核心函数
# ============================================================================


def _lease_reset() -> None:
    """重置租约为空闲状态。"""
    with _lease_lock:
        _lease.update(holder=None, purpose=None, prio=None, lease_id=None,
                      acquired_at=0.0, ttl=0.0, expires_at=0.0, renew_count=0)


def _lease_current() -> dict[str, Any] | None:
    """返回当前有效租约 dict；已过期则惰性释放并返回 None。

    **惰性判定**（无需后台定时器）：任何读取都先检查 expires_at——
    这样持有者崩溃/忘记 release 时，TTL 到期即自动释放，不会永久独占。
    """
    if not _lease["holder"]:
        return None
    if time.time() > _lease["expires_at"]:
        logger.warning(f"[BCC-046] [lease] {_lease['holder']}（prio={_lease['prio']} "
            f"{LEASE_PRIO_NAME.get(_lease['prio'], '?')}）租约超时 "
            f"{_lease['ttl']:.0f}s 未释放，强制回收"
            f"（持有者可能崩溃或忘记 release）")
        _lease_reset()
        return None
    return _lease


def _lease_status() -> dict[str, Any]:
    """对外可读的租约状态（空闲时 holder=None）。"""
    cur = _lease_current()
    if not cur:
        return {"holder": None, "purpose": None, "prio": None,
                "lease_id": None, "expires_at": 0.0, "remaining": 0.0,
                "renew_count": 0}
    return {
        "holder": cur["holder"], "purpose": cur["purpose"],
        "prio": cur["prio"], "lease_id": cur["lease_id"],
        "expires_at": cur["expires_at"],
        "remaining": max(0.0, round(cur["expires_at"] - time.time(), 1)),
        "renew_count": cur["renew_count"],
    }


def _lease_acquire(holder: str, purpose: str = "auto", prio: int = 2,
                   ttl: float = 0.0, lease_id: str = "") -> dict[str, Any]:
    """申请租约。

    返回 {ok, lease_id, expires_at, waited, renew} 或
         {ok: False, busy, busy_prio, retry_after, reason}

    规则：
      · 同 lease_id 重入 → 复用（renew，不新建）
      · 同 holder 且未持 id → 视为重入，复用现有租约
      · 已被他人持有 → 拒绝（不抢占），给出 retry_after
      · ttl 超该 prio 上限 → 拒绝（防"续租绕过上限"）
    """
    prio = int(prio) if prio is not None else 2
    if prio not in LEASE_PRIO_TTL_LIMIT:
        prio = 2
    limit = LEASE_PRIO_TTL_LIMIT[prio]
    if not ttl or ttl <= 0:
        ttl = limit
    if ttl > limit:
        logger.warning(f"[BCC-048] [lease] {holder} 申请 ttl={ttl:.0f}s 超过 prio={prio}"
            f"（{LEASE_PRIO_NAME[prio]}）上限 {limit:.0f}s，已按上限授予")
        ttl = limit

    # 整个「读-判-写」必须是原子的，否则并发申请会双写覆盖。
    with _lease_lock:
        cur = _lease_current()
        if cur is not None:
            # 重入：同一 lease_id，或同一 holder（调用链内层再取）
            if (lease_id and cur["lease_id"] == lease_id) or \
               (not lease_id and cur["holder"] == holder):
                return {"ok": True, "lease_id": cur["lease_id"],
                        "expires_at": cur["expires_at"], "waited": 0.0,
                        "renew": True}
            retry = max(0.5, round(cur["expires_at"] - time.time(), 1))
            logger.debug(f"[BCC-047] [lease] {holder}(prio={prio}) 被拒：当前 {cur['holder']}"
                f"(prio={cur['prio']}) 持有，剩余 {retry}s")
            return {"ok": False, "busy": cur["holder"], "busy_prio": cur["prio"],
                    "retry_after": retry, "reason": "busy"}

        import uuid as _uuid
        _lease.update(
            holder=holder, purpose=purpose, prio=prio,
            lease_id=lease_id or _uuid.uuid4().hex[:12],
            acquired_at=time.time(), ttl=ttl,
            expires_at=time.time() + ttl, renew_count=0)
        logger.debug(
            f"[lease] {holder} 获得租约（prio={prio} {LEASE_PRIO_NAME[prio]}, "
            f"ttl={ttl:.0f}s, id={_lease['lease_id']}）")
        return {"ok": True, "lease_id": _lease["lease_id"],
                "expires_at": _lease["expires_at"], "waited": 0.0, "renew": False}


def _lease_renew(lease_id: str, ttl: float = 0.0) -> dict[str, Any]:
    """续租。累计时长不得超过该 prio 上限（防绕过 TTL 上限）。"""
    with _lease_lock:
        cur = _lease_current()
        if not cur or (lease_id and cur["lease_id"] != lease_id):
            return {"ok": False, "reason": "not_holder"}
        prio = cur["prio"]
        limit = LEASE_PRIO_TTL_LIMIT.get(prio, 30.0)
        new_ttl = ttl if (ttl and ttl > 0) else limit
        total = new_ttl * (cur["renew_count"] + 1)
        if total > limit:
            logger.warning(f"[BCC-048] [lease] {cur['holder']} renew 被拒：累计 {total:.0f}s 超 "
                f"prio={prio} 上限 {limit:.0f}s（防续租绕过 TTL 上限）")
            return {"ok": False, "reason": "ttl_exceeds_limit",
                    "limit": limit, "total": total}
        cur["renew_count"] += 1
        cur["ttl"] = new_ttl
        cur["expires_at"] = time.time() + new_ttl
        logger.debug(f"[lease] {cur['holder']} 续租 {new_ttl:.0f}s"
                     f"（第 {cur['renew_count']} 次）")
        return {"ok": True, "expires_at": cur["expires_at"],
                "renew_count": cur["renew_count"]}


def _lease_release(lease_id: str, holder: str = "") -> dict[str, Any]:
    """释放租约（必须 id 匹配，防误释放他人租约）。"""
    with _lease_lock:
        cur = _lease_current()
        if not cur:
            return {"ok": True, "msg": "本就空闲"}
        if lease_id and cur["lease_id"] != lease_id:
            logger.debug(f"[BCC-049] [lease] release 被拒：id 不匹配"
                         f"（当前 {cur['lease_id']}，请求 {lease_id}）")
            return {"ok": False, "reason": "not_holder"}
        if not lease_id and holder and cur["holder"] != holder:
            return {"ok": False, "reason": "not_holder"}
        _h = cur["holder"]
        _lease_reset()
        logger.debug(f"[lease] {_h} 释放租约")
        return {"ok": True}


def _lease_owned_by(holder: str) -> bool:
    """检查当前租约是否由指定 holder 持有。"""
    with _lease_lock:
        cur = _lease_current()
        return bool(cur and cur["holder"] == holder)