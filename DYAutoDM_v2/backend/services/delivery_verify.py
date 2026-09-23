# -*- coding: utf-8 -*-
"""投递验证标记写入工具（M-5）。

## 设计契约（2026-09-23 审计 P0-1 修复后重写）

**前置条件 P**：调用方必须持有**服务端返回的投递证据**，即
`server_message_id`（非空非 0）且 `check_code != 8610`。
参数形态二选一：
  - 推荐：传 `verdict=`（`services.send_response.delivery_verdict()` 的返回值，
    唯一解析器出口）；
  - 兼容：直接传 `server_message_id=` / `check_code=`。

**后置条件 Q**：
  - 证据成立 ⇒ 写入一行 `[投递验证]` 标记，返回 True；
  - 证据不成立（无消息号 / 8610 / HTTP 失败）⇒ **不写入**，返回 False，
    并打 `[SEND-041]` 日志说明缺什么 —— **绝不**把「接口回 ok」当投递证据
    （历史缺陷：`mark(message=='OK')` 恒成立 ⇒ 探针 `probe_send_delivery`
    只凭 marker 就报 healthy，判别力被击穿）。

**不变式 I**：
  Ⅰ1 标记行的 `msg_id` 使用 **`verify:` 独立命名空间**（`verify:<server_message_id>`），
     因此：① 与真实消息行（`msg_id=<server_message_id>`）**不冲突**（不会因
     `uniq_dmmsg` 唯一索引被 INSERT OR IGNORE 静默丢弃）；② 同一条投递重复标记
     **幂等**（不再无上限堆积 —— 旧实现 msg_id 恒 NULL，部分唯一索引管不到）。
  Ⅰ2 标记行 text 以 `[投递验证]` 开头，渲染层（`api/messages.py`）按此前缀
     **显式排除**，不会作为「我发的消息」展示给用户。
  Ⅰ3 DB 写入失败只打 warning，不阻断发送（发送本身已成功）。

设计文档：artifacts/m5_send_delivery_design.md（方案 A）
"""

from __future__ import annotations

import json
import time

from loguru import logger

MARKER_PREFIX = "[投递验证]"
_MARKER_MSG_ID_PREFIX = "verify:"


def _resolve_evidence(verdict: dict | None, server_message_id: str,
                      status_code: int, check_code: int) -> tuple[bool, str, str]:
    """归一投递证据 → (是否有直接证据, server_message_id, 原因)。唯一判定出口。"""
    if verdict is not None:
        sid = str(verdict.get("server_message_id") or "").strip()
        ok = bool(verdict.get("delivered")) and bool(sid)
        return ok, sid, str(verdict.get("reason") or "")
    sid = str(server_message_id or "").strip()
    if sid == "0":
        sid = ""
    if status_code != 0:
        return False, sid, f"status_code={status_code} != 0"
    if check_code == 8610:
        return False, sid, f"check_code={check_code}=8610（内容安全检查未通过）"
    if not sid:
        return False, sid, ("无 server_message_id —— 只有『接口返回 ok』不足以证明投递"
                            "（本判定即为此而设）")
    return True, sid, f"server_message_id={sid} check_code={check_code}"


def mark_delivery_verified(
    account: str,
    conv_id: str,
    msg_id_hint: str = "",
    status_code: int = 0,
    check_code: int = 0,
    *,
    verdict: dict | None = None,
    server_message_id: str = "",
    source: str = "",
) -> bool:
    """写入一行 `[投递验证]` 标记（**仅当持有服务端投递证据时**）。

    返回 True=已写入标记, False=证据不成立/写入失败（静默返回，不抛异常）。

    :param verdict: `services.send_response.delivery_verdict()` 的返回值（推荐路径）
    :param server_message_id: 兼容路径 —— 服务端返回的消息号
    :param source: 调用来源（dispatch/dm_dispatch/…），仅用于日志归因
    """
    ok, sid, why = _resolve_evidence(verdict, server_message_id, status_code, check_code)
    if not ok:
        logger.info(
            f"[SEND-041] [delivery-verify] 投递判定=**无直接证据**，跳过写入标记: "
            f"account={account} conv_id={conv_id[:16] if conv_id else '(空)'}… "
            f"source={source or '(未标注)'} 原因={why}"
        )
        return False

    _ts = time.time()
    text_content = f"{MARKER_PREFIX} conv_id={conv_id}"
    if msg_id_hint:
        text_content += f" hint={msg_id_hint}"
    text_content += f" msg_id={sid}"

    extra = {
        "delivery_verified": True,
        "server_message_id": sid,
        "status_code": status_code,
        "check_code": check_code,
        "source": source,
        "written_at": _ts,
        "marker": True,          # 供读侧二次判定（不依赖 text 前缀这一条判据）
    }

    try:
        from database import get_db

        conn = get_db()
        # Ⅰ1：独立 msg_id 命名空间，既避开与真实消息行的唯一索引冲突，又让重复标记幂等
        marker_msg_id = _MARKER_MSG_ID_PREFIX + sid
        conn.execute(
            "INSERT OR IGNORE INTO dm_messages("
            "account, conv_id, role, text, msg_type, extra, ts, msg_id) "
            "VALUES(?, ?, 'me', ?, 'delivery_marker', ?, ?, ?)",
            (account, conv_id, text_content,
             json.dumps(extra, ensure_ascii=False), _ts, marker_msg_id),
        )
        conn.commit()
        logger.info(
            f"[SEND-043] [delivery-verify] 已写入投递验证标记: "
            f"account={account} conv_id={conv_id[:16] if conv_id else '(空)'}… "
            f"server_message_id={sid} source={source or '(未标注)'}"
        )
        return True
    except Exception as e:
        # R3：DB 写入失败不抛异常（发送已成功，标记只是辅助证据）
        logger.warning(
            f"[SEND-044] [delivery-verify] DB 写入 [投递验证] 失败"
            f"（不阻断发送）: {e}"
        )
        return False
