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
PURPOSE_AUTO = "auto"            # 自动/后台路径：更新会话、凭证刷新、采集
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
        out["exclusive"] = j.get("exclusive")
        out["version"] = j.get("version")
        out["account"] = j.get("account") or account
        out["msg"] = "ok"
    except Exception as e:  # 端口在但不响应 等
        out["msg"] = f"查询 BCC 状态失败: {str(e)[:90]}"
    return out


def ensure_browser(account: str, purpose: str = PURPOSE_AUTO,
                   wait: bool = True) -> dict:
    """统一入口：确保该账号有**且仅有一个**浏览器所有者。

    返回 {ok, owner, port, state, msg}。永不新开独立浏览器实例。

    - purpose=AUTO      : 复用 BCC；离线则先拉起 BCC 再复用
    - purpose=EXCLUSIVE : 要求独占（扫码/读 profile）；仍由 BCC 进程让出 profile
    """
    a = _acc()
    st = bcc_state(account)

    if not st["online"]:
        # 自动拉起 BCC（唯一入口 ensure_bcc，带冷静期/防重复保护）
        logger.info(f"[gate][{account}] BCC 未运行，委托调度器拉起"
                    f"（purpose={purpose}）")
        try:
            from auto_dm.daemon_launcher import ensure_daemons_for
            r = ensure_daemons_for(account, wait=wait)
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

    # 已在线 → 复用（不新开）
    if purpose == PURPOSE_AUTO:
        logger.info(f"[gate][{account}] 复用 BCC 容器 (port={st['port']}, "
                    f"alive={st['alive']}, exclusive={st['exclusive']})")
    else:
        if not st["exclusive"]:
            logger.info(f"[gate][{account}] 独占用途 {purpose}：需先令 BCC 让出 profile")
    return {"ok": True, "owner": "bcc", "port": st["port"],
            "state": st, "msg": "ok"}


def refresh_cookie_via_owner(account: str, auth: Any, env_path: Optional[str]) -> bool:
    """自动路径刷新凭证：**只走 BCC /cookie**，绝不直开浏览器。

    返回是否成功拿到实时 cookie。失败时调用方应沿用 .env 凭证（不阻断主流程），
    但**不得**转而开浏览器。
    """
    st = bcc_state(account)
    if not st["online"]:
        logger.info(f"[gate][{account}] BCC 不在线，沿用 .env 凭证"
                    f"（不直开浏览器：环境一致性优先）")
        return False
    try:
        from dy_apis.login_api import DYLoginApi
        return bool(DYLoginApi.refresh_cookie_from_profile(
            auth, env_path, allow_launch=False))  # 关键：永远 False
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
