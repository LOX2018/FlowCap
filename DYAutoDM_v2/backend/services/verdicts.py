# coding=utf-8
"""判据函数（单一来源化，2026-09-22 M-10；2026-09-23 P2-4 收敛为单一实现）。

## P2-4 修了什么（实测复现）

2026-09-23 审计实测：同一概念「这个值是不是占位（不是真实昵称/uid）」存在
**两条互相矛盾的判据** ——

    is_placeholder_name('12345')  -> True
    is_uid_placeholder('12345')   -> False

根因：两条判据各写各的阈值（前者「纯数字即占位」无长度门槛，后者「纯数字且
长度 ≥6」）。⇒ 现只有**一条** `is_placeholder` 实现，阈值 `UID_MIN_DIGITS`
来自 `kernel/truth.py`（真 SSOT）。`is_placeholder_name` / `is_uid_placeholder`
两个既有函数名保留为**同义薄封装**（调用方无需改动），但判定逻辑逐字相同、
不再可能分叉。

## 语义（唯一判据 `is_placeholder`）

给定一个「昵称」或「uid」值，判它是否为占位（不可当真名用）：
  - 空 / 全空白            → True
  - 纯数字且长度 ≥ UID_MIN_DIGITS（6） → True（真实 uid 形态）
  - == peer_id（对端 uid）  → True（自身 uid 填进对端字段的污染）
  - 其余                    → False（含短纯数字昵称 '12345'，用户可这样取名）
"""
from __future__ import annotations

from typing import Any

from kernel.truth import UID_MIN_DIGITS


def _norm(v: Any) -> str:
    """判据输入归一化：None/数字/字符串统一成 strip 后的字符串。"""
    if v is None:
        return ""
    return str(v).strip()


def is_placeholder(value: Any, peer_id: Any = None,
                   min_digits: int = UID_MIN_DIGITS,
                   min_len: int = 1) -> bool:
    """**唯一占位判据**（其余函数都是它的薄封装，不得再各写一套）。

    Args:
        value: 待判定值（昵称或 uid）。
        peer_id: 可选对端 UID；若 value == str(peer_id) 则视为占位。
        min_digits: 纯数字视为占位的长度门槛（默认取自 `kernel.truth`）。
        min_len: 有效值最小长度（默认 1，即非空即过）。

    Returns:
        True 表示该值是占位（应当用真实昵称替换 / 应尝试回填）。
    """
    s = _norm(value)
    if not s:
        return True
    if len(s) < min_len:
        return True
    if s.isdigit() and len(s) >= min_digits:
        return True
    if peer_id is not None and s == _norm(peer_id):
        return True
    return False


def is_placeholder_name(name: Any, min_len: int = 1, peer_id: Any = None) -> bool:
    """昵称占位判定（薄封装 → `is_placeholder`；签名/对外行为保持不变）。

    收敛自 5 处不同实现（见 M-10 F7）：
      - probe._is_real_nickname
      - nickname_fallback._is_uid_placeholder
      - api/messages.py peer_name 过滤
      - recv_daemon.py（昵称占位判据第 5 处内联，**尚未接线**，父会话负责）
    """
    return is_placeholder(name, peer_id=peer_id, min_len=min_len)


def is_uid_placeholder(uid: Any, peer_id: Any = None) -> bool:
    """UID 占位判定（薄封装 → `is_placeholder`，与上者**同一套**阈值）。

    修前此函数对 '12345' 返回 False，而同概念的 `is_placeholder_name` 返回
    True（P2-4 的矛盾本体）。现两者对任意输入都返回相同结果。
    """
    return is_placeholder(uid, peer_id=peer_id)
