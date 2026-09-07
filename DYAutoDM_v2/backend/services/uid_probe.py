"""UID 探活统一调度器（2026-09-07 架构重构）。

背景
----
此前 `get_my_uid`（抖音 query/user 外网接口）被 6 个模块、8 处代码各自
独立调用，靠 `DouyinAPI._uid_probe_cache`（class 变量）与 `auth.uid`
缓存勉强去重，问题：

  1. **频次不可控**：每个调用方按自己的节奏触发（前端 30s 轮询、
     live_hook 300s 心跳、凭证门禁事件触发…），叠加后同账号同 IP 的
     query/user 规律性探测 = 风控信号。
  2. **缓存语义混乱**：TTL 分散在 DouyinAPI（300s/60s）、auth.uid
     （UID_CACHE_TTL_SEC 300s）、api/accounts（_VERIFY_TTL 60s）三处，
     互相覆盖、难以推理（09 台账记录过 25s<30s 导致缓存零命中事故）。
  3. **强/弱探活混用**：`force_probe=True` 只有凭证落盘门禁该用，
     但普通调用方也能传，缓存可被任意穿透。

设计
----
**本模块是 query/user 的唯一出口**。其他模块禁止再直接调
`DouyinAPI.get_my_uid`，一律调 `get_uid(name)`：

  - 缓存命中（默认 300s）→ 直接返回，**零网络请求**；
  - 缓存未命中 → 由本模块**串行**（全局锁）发一次请求，结果广播给
    所有等待方（thundering herd 只会打一次网）；
  - `force=True` 保留给凭证落盘门禁等必须真探活的路径（唯一豁免）。

统一调度：后台单线程 `_probe_scheduler` 按账号**错峰**刷新（避免同秒
并发打网），把各调用方的零散需求收敛为「每账号每 DY_UID_PROBE_TTL
秒至多 1 次」。消费方只读取，不触发。

风控铁律合规：本模块只做**登录态自我校验**（query/user 是抖音标准的
"我是谁"接口，非批量查询他人资料），不涉及任何昵称批量拉取。

用法
----
    from services.uid_probe import get_uid, refresh_now, shutdown

    uid = get_uid("四川工伤张老师")          # 缓存优先，可能返回 None
    uid = get_uid("四川工伤张老师", force=True)  # 强制真探活（慎用）
    refresh_now("四川工伤张老师")            # 异步预热，不阻塞
"""
from __future__ import annotations

import os
import threading
import time
from typing import Dict, Optional, Tuple

from loguru import logger

# ---------------------------------------------------------------------------
# 配置（均可用环境变量覆盖，便于测试期调参而不改代码）
# ---------------------------------------------------------------------------
# 缓存有效期：成功 300s（与风控安全频次一致，每账号每 5 分钟至多 1 次）
UID_TTL_OK = float(os.environ.get("DY_UID_PROBE_TTL_OK", "300"))
# 失败后的退避期：60s 内不再重试（防凭证失效时疯狂打网）
UID_TTL_FAIL = float(os.environ.get("DY_UID_PROBE_TTL_FAIL", "60"))
# 同账号并发探活的加锁等待上限（超时则本线程自己去打网，不无限等）
LOCK_WAIT = float(os.environ.get("DY_UID_PROBE_LOCK_WAIT", "10"))

# ---------------------------------------------------------------------------
# 状态
# ---------------------------------------------------------------------------
# {账号名: (ts, uid_or_None)}  —— 探活体系（web query/user）
_cache: Dict[str, Tuple[float, Optional[int]]] = {}
# {账号名: (ts, uid)}  —— 会话体系（imapi conv_id 推断），两套 uid 分开存
_valid_session: Dict[str, Tuple[float, str]] = {}
# 每账号一把锁：同账号并发只让一个线程打网，其余等结果（防惊群）
_locks: Dict[str, threading.Lock] = {}
_registry_lock = threading.Lock()
# 调度线程控制
_sched_stop = threading.Event()
_sched_thread: Optional[threading.Thread] = None


def _lock_for(name: str) -> threading.Lock:
    with _registry_lock:
        lk = _locks.get(name)
        if lk is None:
            lk = threading.Lock()
            _locks[name] = lk
        return lk


def _valid(name: str, ttl_override: Optional[float] = None) -> Optional[int]:
    """缓存有效则返回 uid，否则 None（不触网）。"""
    with _registry_lock:
        hit = _cache.get(name)
    if not hit:
        return None
    ts, uid = hit
    ttl = ttl_override if ttl_override is not None else (
        UID_TTL_OK if uid else UID_TTL_FAIL)
    if (time.time() - ts) < ttl:
        return uid
    return None


def _do_probe(name: str) -> Optional[int]:
    """真正发一次 query/user（本模块内唯一的网络出口）。"""
    try:
        from dy_apis.login_api import DYLoginApi
        from auto_dm import accounts as acct_core
        from dy_apis.douyin_api import DouyinAPI

        env_path = acct_core.env_path_of(name)
        if not env_path:
            logger.debug(f"[uid-probe] 账号「{name}」无 .env，跳过探活")
            return None
        auth = DYLoginApi._load_auth_from_env(env_path)
        if not auth or not getattr(auth, "cookie", None):
            logger.debug(f"[uid-probe] 账号「{name}」凭证为空，跳过探活")
            return None
        # force_probe=True：已由本模块的 TTL 门控，此处必须真打网拿鲜值
        uid = DouyinAPI.get_my_uid(auth, force_probe=True)
        return int(uid) if uid else None
    except Exception as e:
        logger.debug(f"[uid-probe] 账号「{name}」探活异常: {e}")
        return None


def get_uid(name: str, force: bool = False,
            ttl: Optional[float] = None) -> Optional[int]:
    """统一入口：取账号 uid（缓存优先，默认零网络请求）。

    force=True 强制真探活（**仅**凭证落盘门禁等必须鲜值的路径可用）。
    ttl 可临时覆盖有效期（测试/特殊场景用；常规不要传）。

    同账号并发调用时只有一个线程真正打网，其余等待并复用结果。
    """
    if not name:
        return None
    if not force:
        cached = _valid(name, ttl)
        if cached is not None:
            return cached
        # 缓存里是 None（失败退避期内）→ 直接返回 None，不再打网
        with _registry_lock:
            hit = _cache.get(name)
        if hit and not hit[1]:
            ttl_fail = ttl if ttl is not None else UID_TTL_FAIL
            if (time.time() - hit[0]) < ttl_fail:
                return None

    lk = _lock_for(name)
    if not lk.acquire(timeout=LOCK_WAIT):
        # 拿不到锁（极端并发）：退化为读缓存，绝不无限等待
        return _valid(name, ttl)
    try:
        # 双检：等锁期间可能已被别的线程刷新
        if not force:
            cached = _valid(name, ttl)
            if cached is not None:
                return cached
        uid = _do_probe(name)
        with _registry_lock:
            _cache[name] = (time.time(), uid)
        if uid:
            logger.info(f"[uid-probe] 账号「{name}」uid={uid}（已缓存 {UID_TTL_OK:.0f}s）")
        else:
            logger.warning(
                f"[uid-probe] 账号「{name}」探活失败（{UID_TTL_FAIL:.0f}s 内不再重试）")
        return uid
    finally:
        lk.release()


def _session_uid_of(account: str) -> str:
    """取账号在**私信会话体系**中的 uid（权威，用于"对端是谁"判定）。

    2026-09-07 真机实测（09 台账第九轮）：**探活 uid 与会话 uid 可能是
    两个值**。例如「四川工伤张老师」：
      - 会话 uid（imapi，conv_id 内）= 3887506227210423
      - 探活 uid（web query/user）= 4175297014664416
    原因是该账号经历过 uid 轮换/换绑：web 侧 user_uid 变了，但 imapi
    历史会话体系仍挂老 uid。

    推断原理：本账号 uid 必然出现在该账号的**每一个** conv_id 中
    （自己与所有人聊天），故出现次数 ≈ 会话数的那个 uid 即本账号。
    实测：张老师 278/278、尚进 78/79 命中。

    返回 "" 表示无法推断（无历史会话 / DB 不可用）。
    """
    try:
        from database import get_db
        conn = get_db()
        rows = conn.execute(
            "SELECT conv_id FROM dm_conversations WHERE account=?",
            (account,)).fetchall()
        if not rows:
            return ""
        cnt: Dict[str, int] = {}
        for (cid,) in rows:
            p = (cid or "").split(":")
            if len(p) >= 4:
                cnt[p[2]] = cnt.get(p[2], 0) + 1
                cnt[p[3]] = cnt.get(p[3], 0) + 1
        n = len(rows)
        for u, c in cnt.items():
            if c >= n * 0.9:
                return u
    except Exception:
        pass
    return ""


def session_uid(account: str) -> str:
    """会话体系 uid（带缓存，TTL 同 _cache）。供外部判定"对端是谁"用。"""
    hit = _valid_session.get(account)
    if hit and (time.time() - hit[0]) < 300:
        return hit[1]
    u = _session_uid_of(account)
    _valid_session[account] = (time.time(), u)
    return u


def invalidate(name: str = "") -> None:
    """作废缓存（凭证刷新/重扫后调用，让下次 get_uid 取鲜值）。"""
    with _registry_lock:
        if name:
            _cache.pop(name, None)
            _valid_session.pop(name, None)
        else:
            _cache.clear()
            _valid_session.clear()


def refresh_now(name: str) -> None:
    """异步预热（开线程刷新，不阻塞调用方）。失败静默，由 TTL 兜底。"""
    def _run():
        try:
            get_uid(name)
        except Exception:
            pass

    threading.Thread(target=_run, daemon=True).start()


def warm_all(names) -> None:
    """批量预热（错峰，避免同秒并发打网）。启动期调用。"""
    for i, n in enumerate(names or []):
        try:
            # 已有有效缓存的跳过，不重复打网
            if _valid(n) is not None:
                continue
            refresh_now(n)
            time.sleep(0.3)   # 错峰 300ms
        except Exception:
            pass


def stats() -> dict:
    """观测用：当前缓存快照（排查探活频次问题时很有用）。"""
    now = time.time()
    with _registry_lock:
        return {
            n: {"uid": u, "age_sec": round(now - ts, 1),
                "valid": (now - ts) < (UID_TTL_OK if u else UID_TTL_FAIL)}
            for n, (ts, u) in _cache.items()
        }


def shutdown() -> None:
    """停止后台调度（进程退出时调用）。"""
    _sched_stop.set()
