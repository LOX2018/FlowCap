"""凭证失效通知（Windows 系统通知 + 节流）。

## 为什么独立成模块

凭证失效是**最高优先级事件**，但既有告警只走 IM 渠道（notify/events.py）——
用户没接手机时完全无感知。用户明确要求改用 Windows 通知（2026-10-05）。

同时：**通知必须节流**。凭证失效时发送链路会反复重试（sender 每发一次失败
就触发一次），不节流会把通知栏刷爆 —— 与「context 重建风暴」同类的资源浪费。

## 🔴 只读铁律

本模块**只通知，不处理**：
  · 绝不启动浏览器（那是不可逆的风控动作，每次重建都记一次新环境）；
  · 绝不改凭证；
  · 处理权**完全交给用户** —— 用户在有头观测窗口里自行完成授权。
"""
from __future__ import annotations

import os
import time
from threading import Lock

from loguru import logger

# 节流窗口（秒）：同一账号同一原因在此窗口内只通知一次。
# 取 600s（与 notify/events.py 的 cred_expired 节流一致），避免双通道各自为政。
THROTTLE_SEC = float(os.environ.get("DY_CRED_NOTIFY_THROTTLE", "600") or 600)

_lock = Lock()
_last: dict[tuple[str, str], float] = {}

# ── 待前台消费的通知队列 ──────────────────────────────────────────────
# 为什么用队列而不是直接发：后端是 **Python sidecar**，没有窗口、也没有
# 已注册的 AppUserModelID ⇒ 它发的 Toast 只能借用别的 AppID（表现为「终端」）。
# 应用身份的通知必须由 **Tauri 前端** 用 tauri-plugin-notification 发出
# （插件已在 lib.rs:205 注册、capabilities/default.json 已声明权限）。
# 故后端只负责**入队**，前端轮询拉走后以应用身份展示。
#
# 🔴 有界队列：不设上限会在前端长期不消费时无限增长（内存泄漏）。
_MAX_PENDING = 200
_pending: list[dict] = []
# 累计被取走条数（可观测性：证明前端真的在消费，而非队列从未有人取）
_drained_total = 0


def _enqueue(account: str, title: str, body: str, reason: str = "") -> None:
    """入队一条待前台消费的通知（有界，超上限丢弃最旧的）。"""
    global _pending
    item = {
        "id": f"{account}:{int(time.time() * 1000)}",
        "account": account,
        "title": title,
        "body": body,
        "reason": reason or "",
        "ts": time.time(),
    }
    with _lock:
        _pending.append(item)
        if len(_pending) > _MAX_PENDING:
            _dropped = _pending[:-_MAX_PENDING]
            del _pending[:-_MAX_PENDING]
            logger.warning(
                f"[cred-notify] 通知队列超上限 {_MAX_PENDING}，"
                f"丢弃最旧 {len(_dropped)} 条（前端长期未消费？）")


def drain_pending() -> list[dict]:
    """取走全部待消费通知（**取走即清空**，前端轮询用）。

    🔴 必须「取走即清空」：若只读取不清，前端每次轮询都会重复弹出同一条，
    用户会被同一条通知反复打扰（与「不刷爆通知栏」的设计目标相反）。
    """
    global _pending, _drained_total
    with _lock:
        out, _pending = _pending, []
        if out:
            _drained_total += len(out)
            # 可观测性：取走必须留痕，否则「前端到底消费没消费」无从判定
            # （项目铁律：静默路径必须可计数）。
            logger.info(
                f"[cred-notify] 队列被取走 {len(out)} 条"
                f"（累计 {_drained_total} 条）；最近一条："
                f"{out[-1].get('title')} / 账号 {out[-1].get('account')}")
    return out


def drained_total() -> int:
    """已被前端取走的通知总数（可观测性判据：证明前端真的在消费）。"""
    with _lock:
        return _drained_total


def _cred_expire_action() -> str:
    """读配置中心 general.cred_expire_action（热生效）。

    取值 notify（默认·推荐）/ auto（保留旧的自动开浏览器行为）。
    """
    try:
        from services import app_config
        v = app_config.get("general", "cred_expire_action", None)
        v = str(v).strip().lower() if v else ""
    except Exception:  # noqa: BLE001
        v = ""
    if not v:
        v = (os.environ.get("DY_CRED_EXPIRE_ACTION") or "").strip().lower()
    return v or "notify"


def should_auto_open_browser() -> bool:
    """凭证失效时是否**自动**拉起有头浏览器。

    默认 False（= 只通知，不开浏览器）—— 自动开浏览器是不可逆的风控动作。
    """
    return _cred_expire_action() == "auto"


def notify_cred_expired(account: str, reason: str = "", *,
                        force: bool = False) -> bool:
    """发一条「凭证失效」Windows 系统通知（带节流）。

    :param account: 账号名
    :param reason: 失效原因（进正文，帮用户判断）
    :param force: 跳过节流（仅限用户手动触发的测试，自动链路**禁用**）
    :return: 是否成功发出（被节流返回 False，但**不重复发**本身就是正确行为）

    ⚠️ Windows Toast 的固有局限：`Show()` 对未注册 AppID **静默丢弃且返回
    成功**，故本函数返回 True **不能证明用户真的看见了弹窗**（实测负控：
    坏 AppID 也返回 True）。视觉确认必须由人完成 —— 这是诚实标注，不是缺陷。
    """
    key = (account or "(未知账号)", (reason or "")[:60])
    now = time.time()
    with _lock:
        if not force:
            prev = _last.get(key, 0.0)
            if now - prev < THROTTLE_SEC:
                logger.debug(
                    f"[cred-notify] 账号 {account} 通知节流中"
                    f"（{int(THROTTLE_SEC - (now - prev))}s 内已发过），跳过")
                return False
            _last[key] = now

    title = f"凭证失效 · {account}"
    body = (f"账号：{account}\n原因：{reason or '（未注明）'}\n"
            f"请手动更新凭证：配置中心 → 账号 → 查看/重新授权。")

    # ① 主通道：入队给前端（由 Tauri 通知插件以**应用身份**发出）
    _enqueue(account, title, body, reason)

    # ② 兜底：Windows Toast（当前走终端 AppID，身份不精确但可达）
    try:
        from utils.win_notify import notify_windows
        ok = notify_windows(f"川流 · {title}", body)
        if ok:
            logger.info(f"[cred-notify] 账号 {account} 已入队 + 兜底 Toast 已发")
        else:
            logger.warning(
                f"[cred-notify] 账号 {account} 已入队；兜底 Toast 发送失败"
                f"（不自动开浏览器 —— 避免触发 context 重建风暴）")
        return ok
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[cred-notify] 账号 {account} 通知异常（不影响主流程）: {e}")
        return False
