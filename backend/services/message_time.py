# -*- coding: utf-8 -*-
"""消息时间格式化 SSOT（2026-09-30 建立）。

## 为什么需要（真实缺陷）
同一件事（把消息时间戳变成可展示的时间串）在后端有三份各自的实现，
且**格式互不相同**：

| 位置 | 旧格式 | 缺陷 |
|---|---|---|
| `api/messages._fmt_ts` | `%Y-%m-%d %H:%M:%S` | ts 缺失返回 `""`（正确） |
| `services/chat_render._fmt_ts` | `%Y-%m-%d %H:%M` | ts 缺失返回 **`1970-01-01 08:00`** |
| `services/chat_render_png` | 复用 chat_render 的 | 同上 |

缺陷后果（导出链路，实测）：
- `ts=0`（写入侧 `ts or 0` 的产物）被格式化成 **1970-01-01**，
  导出图/HTML 里出现一个「1970 年」的日期分割线。
- 与前端那条缺陷（前端 `nowHM()` 兜底产出无日期的 `01:01`）是**同族**：
  都是「ts 缺失时不返回零信息，而是返回一个看起来合理但错误的具体值」。

## 契约（唯一真源，前后端共用同一形状）
- `MT_FORMAT = "YYYY-MM-DD HH:MM:SS"`（定长 19）
- **ts 缺失 / 为 0 / 非法 ⇒ 返回空串**，绝不返回 1970-01-01 之类的具体值。
  （返回具体值 = 谎报，会让脏数据永远无法被发现。）

## 用法
    from services.message_time import fmt_mt, mt_date, mt_time
    fmt_mt(ts)      -> "2026-09-16 10:40:00" 或 ""
    mt_date(s)      -> "2026-09-16" 或 ""
    mt_time(s)      -> "10:40" 或 ""
"""
from __future__ import annotations

import time

__all__ = ["MT_LEN", "fmt_mt", "mt_date", "mt_time", "is_valid_mt"]

#: 契约串定长（"YYYY-MM-DD HH:MM:SS"）
MT_LEN = 19

_FORMAT = "%Y-%m-%d %H:%M:%S"


def fmt_mt(ts) -> str:
    """时间戳 → 契约串；缺失/为 0/非法 ⇒ 空串（零信息，不谎报）。"""
    try:
        v = float(ts)
    except (TypeError, ValueError):
        return ""
    if not v or v != v:  # 0 / NaN
        return ""
    try:
        return time.strftime(_FORMAT, time.localtime(v))
    except (OverflowError, OSError, ValueError):
        return ""


def is_valid_mt(mt: str | None) -> bool:
    """契约形状校验（与前端 `message-shared.isValidMt` 同判据）。"""
    if not mt or len(mt) != MT_LEN:
        return False
    return (mt[4] == "-" and mt[7] == "-" and mt[10] == " "
            and mt[13] == ":" and mt[16] == ":")


def mt_date(mt: str | None) -> str:
    """日期部分；不合契约 ⇒ 空串（调用方据此不插分割线）。"""
    return mt[:10] if is_valid_mt(mt) else ""


def mt_time(mt: str | None) -> str:
    """时分部分；不合契约 ⇒ 空串（不显示假时间）。"""
    return mt[11:16] if is_valid_mt(mt) else ""
