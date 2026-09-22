# coding=utf-8
"""判据函数（单一来源化，2026-09-22 M-10）。"""

from __future__ import annotations

from typing import Any


def is_placeholder_name(name: Any, min_len: int = 1, peer_id: Any = None) -> bool:
    """昵称是否为占位值（空、纯数字 UID、等于对端 uid）。

    收敛自 5 处不同实现（见 M-10 F7）：
      - probe._is_real_nickname
      - nickname_fallback._is_uid_placeholder
      - api/messages.py peer_name 过滤
      - recv_daemon.py SQL AND 条件

    Args:
        name: 待判定昵称。
        min_len: 有效名称最小长度（默认 1，即非空即通过）。
        peer_id: 可选对端 UID；若 name == str(peer_id) 则视为占位。

    Returns:
        True 表示 name 为占位值（应当用真实昵称替换）。
    """
    if not name:
        return True
    s = str(name).strip()
    if not s:
        return True
    if len(s) < min_len:
        return True
    if s.isdigit():
        return True
    if peer_id is not None and s == str(peer_id):
        return True
    return False


def is_uid_placeholder(uid: Any, peer_id: Any = None) -> bool:
    """UID 占位判定（纯数字且长度 ≥6，如 3887506227210423）。

    数字串长度门槛 6 排除短数字昵称（如"123"可能是真实昵称）。
    另：若 uid == peer_id 则视为占位（自身 UID 填入对端字段的污染）。

    Args:
        uid: 待判定 UID 字符串。
        peer_id: 可选对端 UID；相等则视为占位。

    Returns:
        True 表示 uid 是占位值（应尝试回填真实昵称）。
    """
    if not uid:
        return True
    s = str(uid).strip()
    if not s:
        return True
    if peer_id is not None and s == str(peer_id):
        return True
    return s.isdigit() and len(s) >= 6