# -*- coding: utf-8 -*-
"""投递验证标记写入工具（M-5）。

在私信成功投递后，向 dm_messages 插入一行 `[投递验证]` 记录，
供探针 probe_send_delivery 读取（text LIKE '%投递验证%'）。

设计文档：artifacts/m5_send_delivery_design.md（方案 A）
"""

import json
import time

from loguru import logger


def mark_delivery_verified(
    account: str,
    conv_id: str,
    msg_id_hint: str = "",
    status_code: int = 0,
    check_code: int = 0,
) -> bool:
    """在 dm_messages 写入一行 [投递验证] 记录。

    仅当 status_code == 0 且 status_code/check_code 通过判定阈值时才写入。
    DB 写入失败只打 warning，不阻塞调用方。

    返回 True=已写入标记, False=条件不满足/写入失败（静默返回）。
    """
    # ---- 判定口径 ----
    if status_code != 0:
        logger.info(
            f"[SEND-041] [delivery-verify] 投递判定: account={account} "
            f"conv_id={conv_id[:16] if conv_id else '(空)'}… "
            f"status_code={status_code} check_code={check_code} "
            f"→ status_code != 0，跳过写入投递验证标记"
        )
        return False
    if check_code == 8610:
        logger.info(
            f"[SEND-042] [delivery-verify] 投递判定: account={account} "
            f"conv_id={conv_id[:16] if conv_id else '(空)'}… "
            f"status_code={status_code} check_code={check_code}=8610 "
            f"→ 安全检查未通过，跳过写入投递验证标记"
        )
        return False

    # ---- 构建验证行 ----
    _ts = time.time()
    text_content = f"[投递验证] conv_id={conv_id}"
    if msg_id_hint:
        text_content += f" msg_id={msg_id_hint}"

    extra = {
        "delivery_verified": True,
        "status_code": status_code,
        "check_code": check_code,
        "written_at": _ts,
    }

    try:
        from database import get_db

        conn = get_db()
        conn.execute(
            "INSERT OR IGNORE INTO dm_messages("
            "account, conv_id, role, text, extra, ts) "
            "VALUES(?, ?, 'me', ?, ?, ?)",
            (account, conv_id, text_content, json.dumps(extra, ensure_ascii=False), _ts),
        )
        conn.commit()
        logger.info(
            f"[SEND-043] [delivery-verify] 已写入投递验证标记: "
            f"account={account} conv_id={conv_id[:16] if conv_id else '(空)'}… "
            f"status_code={status_code} check_code={check_code}"
        )
        return True
    except Exception as e:
        # R3：DB 写入失败不抛异常（发送已成功，标记只是辅助证据）
        logger.warning(
            f"[SEND-044] [delivery-verify] DB 写入 [投递验证] 失败"
            f"（不阻断发送）: {e}"
        )
        return False