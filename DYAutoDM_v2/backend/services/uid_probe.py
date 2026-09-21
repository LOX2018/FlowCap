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

    uid = get_uid("<账号名>")          # 缓存优先，可能返回 None
    uid = get_uid("<账号名>", force=True)  # 强制真探活（慎用）
    refresh_now("<账号名>")            # 异步预热，不阻塞
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
# ⚠️ 以下常量仅作**兜底**（配置中心不可用时保持接线前行为，勿改默认值）。
# 运行时实际值走 `services.app_config` —— 原名已改为 _FALLBACK_<原名>，
# 由下方 __getattr__ 对外提供（与 dm_dispatch.py 同款接线手法）。
# 缓存有效期：成功 300s（与风控安全频次一致，每账号每 5 分钟至多 1 次）
_FALLBACK_UID_TTL_OK = float(os.environ.get("DY_UID_PROBE_TTL_OK", "300"))
# 失败后的退避期：60s 内不再重试（防凭证失效时疯狂打网）
_FALLBACK_UID_TTL_FAIL = float(os.environ.get("DY_UID_PROBE_TTL_FAIL", "60"))
# 同账号并发探活的加锁等待上限（超时则本线程自己去打网，不无限等）
_FALLBACK_LOCK_WAIT = float(os.environ.get("DY_UID_PROBE_LOCK_WAIT", "10"))

# ---------------------------------------------------------------------------
# 强探活（force）调用方白名单 —— 风控频次唯一后门，必须显式登记（2026-09-21）
# ---------------------------------------------------------------------------
# 背景：`force=True` 能穿透 300s TTL，是本模块里**唯一**能造成探活频次失控的
# 入口。原文只说「仅凭证落盘门禁可用」，但签名上任何调用方都能传 —— 属于
# 「约束靠约定、不靠强制」（skill §〇·丙 三根因之一），一旦有调用方写错，
# 单账号探活频次就会脱离 300s 门控，形成规律性风控信号。
#
# 现改为**显式登记制**：调用方必须出现在白名单里，否则 force 被静默降级为
# 普通（走 TTL）并告警。降级而非抛错，保证任何路径都不会因本守卫而中断。
_FORCE_CALLERS = {
    # 凭证落盘门禁（browser_daemon 内在 .env 写入前必须拿鲜值）。
    # 实测函数名：daemon/browser_daemon.py 的 _page_login_state_sync
    # （绝不能吃缓存，否则把陈旧登录态当有效写入 —— BCC-015/BCC-016 成因）。
    "_page_login_state_sync",
    # 重扫/重捕后的复验（camoufox_capture）。
    # 实测函数名：camoufox_capture.py 的 verify_credentials。
    "verify_credentials",
    # accounts.py 内的凭证落盘校验 worker（闭包，栈帧名 worker）。
    "worker",
    # 判别工具（诊断用，人工触发，非常驻路径）。
    "diag",
}


def _caller_tag() -> str:
    """从调用栈推断调用方标签（白名单匹配用）。

    取栈上第一个**不在本模块内**的函数的限定名末段。失败返回 ""（= 不在
    白名单 → force 会被降级），这是**安全侧降级**：宁可多打一次 TTL 门控，
    也不要放行未登记的高频探活。
    """
    try:
        import inspect
        frame = inspect.currentframe()
        # 跳过本模块内的帧（_caller_tag / get_uid / _do_probe …）
        while frame is not None:
            mod = frame.f_globals.get("__name__", "")
            if mod != __name__ and not mod.startswith("services.uid_probe"):
                return frame.f_code.co_name
            frame = frame.f_back
        return ""
    except Exception:
        return ""


def _force_allowed() -> bool:
    """当前调用方是否被允许强探活。"""
    return (_caller_tag() or "") in _FORCE_CALLERS


_LAZY_MAP = {
    "UID_TTL_OK": ("capture", "uid_probe_ttl_ok"),
    "UID_TTL_FAIL": ("capture", "uid_probe_ttl_fail"),
    "LOCK_WAIT": ("capture", "uid_probe_lock_wait"),
}


def cfg(name: str):
    """取运行时配置值（配置中心优先，失败回落兜底常量）。"""
    if name not in _LAZY_MAP:
        raise KeyError(name)
    sec, key = _LAZY_MAP[name]
    try:
        from services.app_config import get

        v = get(sec, key)
        if v is not None:
            return v
    except Exception:
        pass
    return globals()["_FALLBACK_" + name]


def __getattr__(name: str):
    """PEP 562：原名已改名，既有调用点自动走配置中心。"""
    if name in _LAZY_MAP:
        return cfg(name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

# ---------------------------------------------------------------------------
# 状态
# ---------------------------------------------------------------------------
# {账号名: (ts, uid_or_None)}  —— 探活体系（web query/user）
# 2026-09-16 v0.43.40：会话体系 uid（session_uid）的缓存已统一到
# services.conv_identity（TTL 60s），本模块不再自持 _valid_session。
_cache: Dict[str, Tuple[float, Optional[int]]] = {}
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
        cfg("UID_TTL_OK") if uid else cfg("UID_TTL_FAIL"))
    if (time.time() - ts) < ttl:
        return uid
    return None


def _uid_consistent_with_history(account: str, uid) -> bool:
    """探活 uid 是否与该账号历史会话一致（防陈旧/错误 uid 被缓存）。

    判据：该 uid 必须出现在该账号**至少一条**历史 conv_id 中。
    无历史会话（新账号）时返回 True（无从比对，不冤枉）。
    DB 不可用时也返回 True（降级放行，避免误伤）。

    实测依据：正确 uid 在该账号**全部**历史会话中出现（如 278/278 条）；
    陈旧/错误 uid 出现 0 次。
    """
    try:
        from database import get_db
        conn = get_db()
        rows = conn.execute(
            "SELECT conv_id FROM dm_conversations WHERE account=? LIMIT 500",
            (account,)).fetchall()
        if not rows:
            return True
        s = str(uid)
        for (cid,) in rows:
            if s in (cid or "").split(":"):
                return True
        return False
    except Exception:
        return True


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

    ⚠️ 2026-09-21：force 已改为**调用方白名单制**（见 `_FORCE_CALLERS`）。
    未登记的调用方传 force=True 会被**降级**为走 TTL 的普通探活并告警，
    保证探活频次不会因某个调用方写错而脱离 300s 门控（频次=风控面）。

    同账号并发调用时只有一个线程真正打网，其余等待并复用结果。
    """
    if not name:
        return None
    if force and not _force_allowed():
        # 未登记调用方 → 降级（安全侧），不静默穿透缓存
        logger.warning(
            f"[AUTH-052] [uid-probe] 调用方「{_caller_tag() or '?'}」不在强探活"
            f"白名单内，force=True 已降级为普通探活（走 {cfg('UID_TTL_OK'):.0f}s "
            f"TTL）—— 如需真探活请登记到 services.uid_probe._FORCE_CALLERS")
        force = False
    if not force:
        cached = _valid(name, ttl)
        if cached is not None:
            return cached
        # 缓存里是 None（失败退避期内）→ 直接返回 None，不再打网
        with _registry_lock:
            hit = _cache.get(name)
        if hit and not hit[1]:
            ttl_fail = ttl if ttl is not None else cfg("UID_TTL_FAIL")
            if (time.time() - hit[0]) < ttl_fail:
                return None

    lk = _lock_for(name)
    if not lk.acquire(timeout=cfg("LOCK_WAIT")):
        # 拿不到锁（极端并发）：退化为读缓存，绝不无限等待
        return _valid(name, ttl)
    try:
        # 双检：等锁期间可能已被别的线程刷新
        if not force:
            cached = _valid(name, ttl)
            if cached is not None:
                return cached
        uid = _do_probe(name)
        # 2026-09-07 事实更正 + 加固：**不存在"两套 uid"**，探活 uid 必须
        # 与该账号历史会话一致。实测事故：某账号真实 uid 在全部会话中出现
        # （278/278 + 抖音 query/user 双重确认），但日志里长期出现一个**陈旧值**，
        # 被本缓存按 300s TTL 反复复用，导致上游误判"uid 漂移"、账号校验误报。
        # 加固：探活成功后与历史会话交叉验证 —— 若该 uid 从未出现在该账号
        # conv_id 中，视为**陈旧/不可信**，不写入缓存、不返回（返回 None 让
        # 调用方走兜底），并告警。这样陈旧值不会污染后续 300s。
        if uid and not _uid_consistent_with_history(name, uid):
            logger.warning(f"[AUTH-050] " + f"[uid-probe] 账号「{name}」探活 uid={uid} 与该账号历史会话"
                f"不一致 —— 判为陈旧/不可信，不缓存（真实 uid 以 conv_id 为准）")
            return None
        with _registry_lock:
            _cache[name] = (time.time(), uid)
        if uid:
            logger.info(f"[uid-probe] 账号「{name}」uid={uid}（已缓存 {cfg('UID_TTL_OK'):.0f}s）")
        else:
            logger.warning(f"[AUTH-051] " + f"[uid-probe] 账号「{name}」探活失败（{cfg('UID_TTL_FAIL'):.0f}s 内不再重试）")
        return uid
    finally:
        lk.release()


def _session_uid_of(account: str) -> str:
    """取账号在**私信会话体系**中的 uid（权威，用于"对端是谁"判定）。

    2026-09-07 真机实测（09 台账第九轮）：**探活 uid 与会话 uid 可能是
    两个值** —— 会话 uid（imapi，conv_id 内）与探活 uid（web query/user）
    不一致。原因是该账号经历过 uid 轮换/换绑：web 侧 user_uid 变了，
    但 imapi 历史会话体系仍挂老 uid。
    （注：2026-09-16 起本函数已收敛到 services.conv_identity.my_uid，
    不再各自维护一份实现。）

    推断原理：本账号 uid 必然出现在该账号的**每一个** conv_id 中
    （自己与所有人聊天），故出现次数 ≈ 会话数的那个 uid 即本账号。

    ⚠️ 2026-09-16 v0.43.39：**实现已收敛到 services.conv_identity**——
    此处只做委托，不再自维护一份（改前本函数与 dm_dispatch.ConvPool、
    recv_daemon 等共 7 处同款实现，算法略有差异，属隐性漂移面）。
    注意本函数**不做覆盖度以外的位置稳定性校验**的历史行为已统一，
    由 conv_identity 统一保证（覆盖度 ≥90% 且位置固定）。

    返回 "" 表示无法推断（无历史会话 / DB 不可用）。
    """
    try:
        from services.conv_identity import my_uid
        return my_uid(account) or ""
    except Exception:
        return ""


def session_uid(account: str) -> str:
    """会话体系 uid（供外部判定"对端是谁"用）。

    2026-09-16 v0.43.40：**缓存已统一**——改前本函数自维护一份 300s TTL
    的 `_valid_session` 缓存，与 `conv_identity.my_uid` 的 60s 缓存**功能
    重叠、TTL 不同**（同一事实两个新鲜度）。现直接委托 conv_identity，
    由它统一保证 60s TTL（本推断零网络，更短 TTL 无成本且换号自愈更快）。
    """
    try:
        from services.conv_identity import my_uid
        return my_uid(account) or ""
    except Exception:
        return ""


def invalidate(name: str = "") -> None:
    """作废缓存（凭证刷新/重扫后调用，让下次 get_uid 取鲜值）。

    2026-09-16 v0.43.40：**联动清理** conv_identity 的 my_uid 缓存 ——
    session_uid 已委托给它，若只清本模块的 _cache，会出现
    「uid 已刷新但 session_uid 仍返回旧值」的不一致窗口。
    """
    with _registry_lock:
        if name:
            _cache.pop(name, None)
        else:
            _cache.clear()
    try:
        from services.conv_identity import clear_cache as _ci_clear
        _ci_clear(name or "")
    except Exception:
        pass


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
                "valid": (now - ts) < (cfg("UID_TTL_OK") if u else cfg("UID_TTL_FAIL"))}
            for n, (ts, u) in _cache.items()
        }


def shutdown() -> None:
    """停止后台调度（进程退出时调用）。"""
    _sched_stop.set()
