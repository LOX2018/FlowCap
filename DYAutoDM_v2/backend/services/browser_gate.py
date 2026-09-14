# -*- coding: utf-8 -*-
"""浏览器统一入口（单例门禁）—— 所有浏览器启动路径的唯一决策点。

## 为什么需要（2026-09-13 用户要求）

用户原话：「浏览器守护不能通过调度器统一管理吗，必须手动处理？」
        「所有启动路径统一走同一入口（杜绝环境分叉）」

结构性问题：项目曾有 **12 处** 可独立拉起浏览器的调用点
（login_api 8 处 / link_resolve / web_probe / api.accounts / browser_daemon），
外加 capture_all 的 `allow_launch=True` 后门。同一账号可能出现
「BCC 容器 + 另一个临时浏览器」同时持有同一 profile → 抢锁 → 环境跳变 → 风控。

## 铁律

1. **一个账号同一时刻只有一个浏览器所有者**，由调度器统一管理。
2. **自动路径**（更新会话 / 凭证刷新 / 采集）一律**复用 BCC 容器**：
     - BCC 在线 → 走 `/cookie` 读实时凭证（不抢锁、不开新浏览器）
     - BCC 离线 → 委托 `ensure_daemons_for` **拉起 BCC**，再复用
   **绝不「兜底直开浏览器」**。
3. **独占路径**（扫码/重扫/从 profile 读凭证）必须先让 BCC 让出 profile
   （`/exclusive`），仍是同一个 BCC 进程持有目录，不新开独立实例。
4. **无法满足时显式失败**并给出可操作提示，绝不静默降级成第二个浏览器。
"""
from __future__ import annotations

import os
from typing import Any, Optional

from loguru import logger

# 用途分类
PURPOSE_AUTO = "auto"            # 纯自动/后台路径（启动期预对齐等）
PURPOSE_USER = "user"            # **用户显式操作**（点「更新会话」/「启动守护」/
                                 # 「打开浏览器」）—— 一律豁免启动冷静期
PURPOSE_EXCLUSIVE = "exclusive"  # 用户显式独占：扫码登录、重扫、读 profile 凭证


class BrowserUnavailable(RuntimeError):
    """无法提供浏览器/凭证时的显式失败（调用方应据此提示用户）。"""


def _acc() -> Any:
    from auto_dm import accounts as _a
    return _a


def bcc_state(account: str) -> dict:
    """查询该账号 BCC 容器的真实状态（一次 HTTP，零副作用）。

    返回 {online, alive, exclusive, port, account, version, msg}
    - online   : 端口可连
    - alive    : 容器上下文可用（非切换中/非独占让出中）
    - exclusive: 当前独占持有者名（None 表示未独占）——独占期间 profile 已被让出，
                 此时允许其它路径短暂接手，不算「环境分叉」
    """
    out = {"online": False, "alive": False, "exclusive": None,
           "port": None, "account": account, "version": None, "msg": ""}
    try:
        a = _acc()
        port = a.browser_daemon_port(account)
        out["port"] = port
        if not a._port_open(port, timeout=0.3):
            out["msg"] = "BCC 端口未开"
            return out
        out["online"] = True
        import json as _json
        import urllib.request as _ur
        rq = _ur.Request(f"http://127.0.0.1:{port}/status", method="GET")
        with _ur.urlopen(rq, timeout=5) as r:
            j = _json.loads(r.read().decode("utf-8", "replace"))
        out["alive"] = bool(j.get("alive"))
        # 2026-09-13 S2：真值源。此前只读 j.get("exclusive")，而 /status
        # 从不返回该字段 → 恒为 None → 独占分支永远走假成功路径。
        out["lease"] = j.get("lease") or {}
        out["exclusive"] = (j.get("lease") or {}).get("holder") \
            or j.get("exclusive")
        out["version"] = j.get("version")
        out["account"] = j.get("account") or account
        out["msg"] = "ok"
    except Exception as e:  # 端口在但不响应 等
        out["msg"] = f"查询 BCC 状态失败: {str(e)[:90]}"
    return out


# 优先级映射（与 BCC 侧 LEASE_PRIO_TTL_LIMIT 一致：0=用户显式 1=业务自动 2=后台保活）
_PRIO_BY_PURPOSE = {
    PURPOSE_AUTO: 1,         # 纯后台路径 → 业务自动
    PURPOSE_USER: 0,         # 用户点了按钮 → 用户显式（最高）
    PURPOSE_EXCLUSIVE: 0,    # 扫码/读 profile → 用户显式
}

# 冷静期豁免：用户显式操作一律放行。
#
# 为什么（2026-09-13 实测事故）：启动冷静期（DY_BCC_LAZY_DELAY）本意是防
# 「启动期自动路径乱拉 BCC」，但用户点「更新会话」被拦后：既没有 BCC 可用，
# 采集还继续空跑 → 用户看到「连 BCC 都没唤醒」。
# 注：BCC 现已改为随启动拉起（DY_BCC_ON_START 默认 1），冷静期默认 0；
# 此表仍保留——用户显式操作永远不该被任何节流机制拦住。
_SKIP_COOLDOWN_BY_PURPOSE = {
    PURPOSE_AUTO: False,      # 纯后台：仍受冷静期保护（若显式开启）
    PURPOSE_USER: True,       # 用户显式：豁免
    PURPOSE_EXCLUSIVE: True,  # 扫码等：豁免
}
_TTL_LIMIT = {0: 300.0, 1: 180.0, 2: 30.0}


def _lease_http(port: int, path: str, payload: dict, timeout: float = 8.0) -> dict:
    """调 BCC 租约端点（零副作用失败：异常一律转为 {ok:False, msg}）。"""
    import json as _json
    import urllib.request as _ur
    try:
        rq = _ur.Request(
            f"http://127.0.0.1:{port}{path}",
            data=_json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        with _ur.urlopen(rq, timeout=timeout) as r:
            return _json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:
        return {"ok": False, "msg": f"租约请求失败({path}): {str(e)[:80]}"}


def acquire_lease(account: str, holder: str, purpose: str = PURPOSE_AUTO,
                  prio: Optional[int] = None, ttl: float = 0.0) -> dict:
    """向该账号 BCC 申请浏览器租约 —— **业务侧取用浏览器的唯一正路**。

    返回 {ok, lease_id, expires_at, busy, retry_after, msg}。
    调用方拿到 lease_id 后应在 BCC 请求里带上（BCC 据此判定"确实持有"），
    用完调 release_lease(account, lease_id)。
    """
    st = bcc_state(account)
    if not st.get("online"):
        return {"ok": False, "msg": st.get("msg") or "BCC 未在线"}
    _prio = _PRIO_BY_PURPOSE.get(purpose, 1) if prio is None else int(prio)
    _ttl = ttl or _TTL_LIMIT.get(_prio, 30.0)
    return _lease_http(st["port"], "/lease",
                       {"holder": holder, "purpose": purpose,
                        "prio": _prio, "ttl": _ttl})


def release_lease(account: str, lease_id: str, holder: str = "") -> dict:
    """释放租约（务必放在 finally，否则要等 TTL 到期才回收）。"""
    if not lease_id:
        return {"ok": True, "msg": "无租约需释放"}
    st = bcc_state(account)
    if not st.get("online"):
        return {"ok": False, "msg": "BCC 不在线，租约将随进程退出/TTL 回收"}
    return _lease_http(st["port"], "/lease/release",
                       {"lease_id": lease_id, "holder": holder})


def lease_status(account: str) -> dict:
    """只读查询该账号 BCC 的租约状态（谁在用、还剩多久）。"""
    st = bcc_state(account)
    return st.get("state", {}).get("lease") if "state" in st else st.get("lease")


def ensure_browser(account: str, purpose: str = PURPOSE_AUTO,
                   wait: bool = True, holder: str = "",
                   prio: "int|None" = None, ttl: float = 0.0,
                   skip_cooldown: "bool|None" = None) -> dict:
    """统一入口：确保该账号有**且仅有一个**浏览器所有者。

    返回 {ok, owner, port, state, lease_id, msg}。永不新开独立浏览器实例。
    拿到 lease_id 即持租约 —— **用完必须 release_lease()**（或等 TTL）。

    - purpose=AUTO      : 复用 BCC；离线则先拉起 BCC 再复用
    - purpose=EXCLUSIVE : 要求独占（扫码/读 profile）；仍由 BCC 进程让出 profile
    """
    # 2026-09-14 v0.43.8：用户主动停止 → 不拉起，直接返回未就绪。
    try:
        from auto_dm.accounts import bcc_user_stopped as _st
        if _st():
            return {"ok": False, "port": None, "holder": None,
                    "msg": "BCC 已被用户停止（自动拉起已禁用）"}
    except Exception:
        pass
    
    a = _acc()
    st = bcc_state(account)

    if not st["online"]:
        # 自动拉起 BCC（唯一入口 ensure_bcc，带冷静期/防重复保护）
        # 用户显式操作豁免启动冷静期（见 _SKIP_COOLDOWN_BY_PURPOSE）。
        _sk = (skip_cooldown if skip_cooldown is not None
               else _SKIP_COOLDOWN_BY_PURPOSE.get(purpose, False))
        logger.info(f"[gate][{account}] BCC 未运行，委托调度器拉起"
                    f"（purpose={purpose}, skip_cooldown={_sk}）")
        try:
            from auto_dm.daemon_launcher import ensure_daemons_for
            r = ensure_daemons_for(account, wait=wait, skip_cooldown=_sk)
            ok = bool(r.get("browser"))
        except Exception as e:
            logger.warning("BCC-040",
                           f"[gate][{account}] 调度器拉起 BCC 失败: {e}")
            ok = False
        st = bcc_state(account)
        if not ok or not st["online"]:
            msg = (f"BCC 浏览器守护未能就绪（{st.get('msg') or '端口未开'}）。"
                   f"请先在账号管理页启动该账号的浏览器守护，或检查守护二进制是否存在。")
            logger.warning("BCC-041", f"[gate][{account}] {msg}")
            return {"ok": False, "owner": None, "port": st.get("port"),
                    "state": st, "msg": msg}

    # 已在线 → **真取租约**（2026-09-13 S2：消除此前的静默空壳）
    #
    # 原实现的问题：EXCLUSIVE 分支只打一行日志就 return ok=True，
    # 因为它读的 st["exclusive"] 来自 /status，而该字段此前从不返回。
    # 调用方以为拿到独占，实际什么都没发生 —— 比报错更糟（完全无感）。
    _prio = _PRIO_BY_PURPOSE.get(purpose, 1)
    _ttl = _TTL_LIMIT.get(_prio, 30.0)
    _lr = _lease_http(st["port"], "/lease",
                      {"holder": holder or f"gate:{purpose}",
                       "purpose": purpose, "prio": _prio, "ttl": _ttl})
    if not _lr.get("ok"):
        _busy = _lr.get("busy")
        _retry = _lr.get("retry_after")
        logger.info(f"[gate][{account}] 浏览器被 {_busy} 占用（约 {_retry}s），"
                    f"purpose={purpose} —— 显式失败，不再起第二个实例")
        return {"ok": False, "owner": "bcc", "port": st["port"],
                "state": st, "busy": _busy, "retry_after": _retry,
                "msg": f"浏览器正被 {_busy} 使用，约 {_retry}s 后可用"}
    logger.info(f"[gate][{account}] 取得租约 (port={st['port']}, "
                f"purpose={purpose}, prio={_prio}, "
                f"lease_id={_lr.get('lease_id')})")
    return {"ok": True, "owner": "bcc", "port": st["port"],
            "state": st, "lease_id": _lr.get("lease_id"),
            "expires_at": _lr.get("expires_at"), "msg": "ok"}


def refresh_cookie_via_owner(account: str, auth: Any, env_path: Optional[str],
                             lease_id: str = "") -> bool:
    """自动路径刷新凭证：**只走 BCC /cookie**，绝不直开浏览器。

    返回是否成功拿到实时 cookie。失败时调用方应沿用 .env 凭证（不阻断主流程），
    但**不得**转而开浏览器。

    lease_id（2026-09-14 v0.43.11）：跨调用窗口租约透传。调用方（capture_all）
    已持有 gate 租约，BCC /cookie 内部的 get_cookies 走 _exec，若不带上同一
    lease_id 会被判为并发冲突 → AUTH-036「容器正被 gate:auto 独占」。
    """
    st = bcc_state(account)
    if not st["online"]:
        logger.info(f"[gate][{account}] BCC 不在线，沿用 .env 凭证"
                    f"（不直开浏览器：环境一致性优先）")
        return False
    try:
        from dy_apis.login_api import DYLoginApi
        return bool(DYLoginApi.refresh_cookie_from_profile(
            auth, env_path, allow_launch=False,  # 关键：永远 False
            lease_id=lease_id))
    except Exception as e:
        logger.debug(f"[gate][{account}] BCC /cookie 刷新异常（沿用 .env）: {e}")
        return False


# ---------------------------------------------------------------------------
# 直开审计：任何绕过本门禁的独立启动都会被记录（用于收敛残余路径）
# ---------------------------------------------------------------------------
_AUDIT: list = []


def _is_bcc_owner() -> bool:
    """当前进程是否就是 BCC 守护自身。

    BCC 启动自己持有的容器时也会走 vbrowser.launch_*，若不豁免会自己告警自己。
    判据：环境变量 DY_BROWSER_DAEMON=1（BCC 入口设置）+ 进程内标记双保险。
    """
    if os.environ.get("DY_BROWSER_DAEMON") == "1":
        return True
    try:
        import sys as _s
        return "browser_daemon" in (_s.argv[0] or "")
    except Exception:
        return False


def audit_standalone_launch(account: Optional[str], caller: str,
                            allowed: bool = False) -> None:
    """记录一次「独立启动浏览器」——用于发现绕过统一入口的残余路径。

    判定为「环境分叉」需同时满足：
      ① 该账号有 BCC 在跑 ② BCC 未处于独占让出态 ③ 非 BCC 自身启动 ④ 未显式豁免
    此时打 ERROR（BCC-042），但不阻断（先观测，收敛完残余路径后再收紧）。
    """
    try:
        if not account:
            return
        if allowed or os.environ.get("DY_ALLOW_STANDALONE_LAUNCH") == "1":
            return
        if _is_bcc_owner():
            return   # BCC 自己启动容器，是合法所有者
        st = bcc_state(account)
        if st["online"] and not st["exclusive"]:
            _AUDIT.append({"account": account, "caller": caller,
                           "bcc_port": st["port"], "bcc_alive": st["alive"]})
            logger.error(
                "BCC-042",
                f"[gate] 环境分叉告警：{caller} 为账号「{account}」独立启动了浏览器，"
                f"但该账号的 BCC 容器已在运行 (port={st['port']}) —— "
                f"同一 profile 被两个所有者持有会触发风控。"
                f"请改走 services.browser_gate.ensure_browser()。")
    except Exception:
        pass


def audit_report() -> list:
    """返回本进程内记录的直开事件（排查用）。"""
    return list(_AUDIT)
