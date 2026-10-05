"""业务事件 → 通知中枢的桥接层。

为什么要有这一层
----------------
业务代码（core/sender.py、auto_dm/accounts.py、core/auto_dm.py）不应该
直接 import notifier —— 否则通知模块一旦出异常会反噬主流程，且业务与
通知强耦合不好测。这里提供**零副作用**的 emit 封装：
  - 通知未启用 → 直接返回（几乎零开销）
  - 任何异常全吞，绝不影响业务
  - 业务侧只描述「发生了什么」，不关心推到哪个渠道

三类事件（对应用户需求）：
  1. 凭证失效提醒  cred_expired
  2. 私信情况汇报  dm_report（成功/失败汇总）
  3. 任务监控      task_started / task_finished / task_failed
  4. 风控告警      risk_alert（KICK 等）
"""

from __future__ import annotations

from typing import Any

from loguru import logger

# 事件类型常量
CRED_EXPIRED = "cred_expired"
CRED_RECOVERED = "cred_recovered"
DM_SENT = "dm_sent"
DM_FAILED = "dm_failed"
TASK_STARTED = "task_started"
TASK_FINISHED = "task_finished"
TASK_FAILED = "task_failed"
RISK_ALERT = "risk_alert"


def _notifier():
    """惰性取全局 notifier（避免 import 期副作用）。"""
    try:
        from notify import notifier

        return notifier
    except Exception:  # noqa: BLE001
        return None


def emit(
    event_type: str,
    title: str,
    body: str,
    *,
    level: str = "info",
    dedup_key: str = "",
    throttle_sec: int = 0,
) -> None:
    """统一事件出口。**永不抛异常**（通知失败绝不能影响业务）。

    throttle_sec: 同 dedup_key 在该秒数内只推一次（防重试风暴刷屏）。
    """
    n = _notifier()
    if n is None:
        return
    try:
        n.emit(
            event_type,
            title,
            body,
            level=level,
            dedup_key=dedup_key or f"{event_type}:{title}",
            throttle_sec=throttle_sec,
        )
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[notify-bridge] 事件 {event_type} 发送失败（已忽略）: {e}")


# ---------------- 具体事件便捷方法 ----------------

def cred_expired(account: str, reason: str, *, auto_fixing: bool = False) -> None:
    """凭证失效 —— 最高优先级告警（critical，无视渠道 min_level）。

    throttle 10 分钟：凭证失效时发送链路会反复重试，不节流会把 IM 刷爆。
    """
    tail = "\n已自动唤醒指纹浏览器重新捕获，请在浏览器中完成授权。" if auto_fixing else ""
    emit(
        CRED_EXPIRED,
        f"凭证失效·{account}",
        f"账号：{account}\n原因：{reason}{tail}\n请及时重新扫码授权，否则私信任务将中断。",
        level="critical",
        dedup_key=f"cred:{account}",
        throttle_sec=600,
    )


def cred_recovered(account: str) -> None:
    emit(
        CRED_RECOVERED,
        f"凭证恢复·{account}",
        f"账号：{account} 凭证已恢复有效，私信任务可继续。",
        level="info",
        dedup_key=f"cred_ok:{account}",
        throttle_sec=300,
    )


def dm_sent(account: str, target: str, text_preview: str = "") -> None:
    """私信发送成功汇报。

    单条成功不推（太吵），由任务维度的汇总汇报负责；这里只做 debug 记录。
    保留接口是为了将来「重要客户单独通知」的需求。
    """
    logger.debug(f"[notify-bridge] dm_sent {account} -> {target}: {text_preview[:20]}")


def dm_failed(account: str, reason: str, target: str = "") -> None:
    """私信发送失败 —— 区分「凭证类」与「普通失败」。

    凭证类由 cred_expired 单独告警（critical），这里只报普通失败，
    避免同一件事推两条。
    """
    emit(
        DM_FAILED,
        f"私信发送失败·{account}",
        f"账号：{account}\n目标：{target or '(未知)'}\n原因：{reason}",
        level="warn",
        dedup_key=f"dmfail:{account}:{reason[:30]}",
        throttle_sec=300,
    )


def task_started(task_info: str = "") -> None:
    emit(TASK_STARTED, "任务已启动", task_info or "自动私信任务开始运行。",
         level="info", throttle_sec=60)


def task_finished(summary: dict[str, Any] | None = None) -> None:
    """任务结束汇报 —— 汇总成功/失败数（用户要的「任务监控」核心）。"""
    s = summary or {}
    sent = s.get("sent", 0)
    failed = s.get("failed", 0)
    total = s.get("total", sent + failed)
    ok_rate = f"{(sent / total * 100):.0f}%" if total else "-"
    body = (
        f"成功：{sent} 条\n失败：{failed} 条\n合计：{total} 条\n成功率：{ok_rate}"
    )
    if s.get("reason"):
        body += f"\n结束原因：{s['reason']}"
    emit(TASK_FINISHED, "任务结束", body, level="info", throttle_sec=60)


def task_failed(reason: str) -> None:
    emit(TASK_FAILED, "任务异常终止", f"原因：{reason}", level="critical",
         dedup_key=f"taskfail:{reason[:40]}", throttle_sec=120)


def risk_alert(account: str, detail: str) -> None:
    """风控告警（KICK / 频控等）—— critical。"""
    emit(RISK_ALERT, f"风控告警·{account}", f"账号：{account}\n{detail}",
         level="critical", dedup_key=f"risk:{account}:{detail[:30]}",
         throttle_sec=300)
