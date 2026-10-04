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

    title = f"川流 · 凭证失效 · {account}"
    body = (f"账号：{account}\n原因：{reason or '（未注明）'}\n"
            f"请手动更新凭证：配置中心 → 账号 → 查看/重新授权。")

    # 唯一通道：Windows Toast（PowerShell 5.1 + WinRT）。
    #
    # 2026-10-05 定稿：曾尝试改由**前端 Tauri 通知插件**以应用身份发出
    # （后端入队 → 前端轮询取走 → 插件发送），实测**链路能跑通**（boot log
    # 证实 `SENT 1 条（应用身份）`），但 Windows 上**显示身份仍是宿主**
    # —— Tauri v2 官方文档写明「Windows: Only works for installed apps.
    # Shows powershell name & icon in development.」：未走 MSI 安装的应用
    # 没有 package identity ⇒ toast 挂在宿主身份下。
    # ⇒ 权衡后保留后端直发（少一层、无轮询开销），前端链路整体移除。
    try:
        from utils.win_notify import notify_windows
        ok = notify_windows(title, body)
        if ok:
            logger.info(f"[cred-notify] 账号 {account} 凭证失效通知已发")
        else:
            logger.warning(
                f"[cred-notify] 账号 {account} 通知发送失败"
                f"（不自动开浏览器 —— 避免触发 context 重建风暴）")
        return ok
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[cred-notify] 账号 {account} 通知异常（不影响主流程）: {e}")
        return False
