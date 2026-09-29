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


# ===========================================================================
# 2026-09-29：投递结果**读取侧**（「受理 ≠ 送达」的判定出口）
# ===========================================================================
# 背景（用户实测反馈 + 只读取证）：
#   前端「已发送」取自调度记录 `RecordStatus.SENT`，而它是在
#   `core/dispatch.py:_do_send` 拿到「调度器已入池（accepted=True）」时就置位
#   —— 那是**受理**（消息交给了发送链），不是**送达**（服务端确认）。
#   实测 2026-09-29：**受理条数与平台拒收回执条数不一致**——相当一部分回执是
#   「对方回复或关注你之前，只能发送一条文字消息」（平台拒收）。
#   ⇒ 界面却把**全部受理条**都显示成绿色「已发送」。
#
# 本模块**只增加一个「证据读取」出口**，不改任何写入路径/时序/限流：
#   送达证据 = 该账号该会话存在 `msg_type='7'` 的**回声帧**
#            （`recv_daemon` 收到自己发出的消息回执时会以 msg_type='7' 落库）
#            或存在 `[投递验证]` 标记行（`mark_delivery_verified` 写入）。
#   平台拒收证据 = 存在平台提示行（「对方回复或关注你之前…」系统通知）。
# ---------------------------------------------------------------------------

#: 平台对「陌生人只能发一条」的拒收回执前缀（与 message_schema 的
#: `_RE_SYSTEM_NOTICE` 同源语义；此处只做**读取侧**匹配，不重复定义业务规则）。
PLATFORM_REJECT_PREFIX = "对方回复或关注你之前"


def delivery_state_of(account: str, conv_id: str = "",
                      uid: str = "") -> str:
    """返回该目标最近一次投递的**真实结局**（只读，零副作用）。

    取值（供调用方与前端映射，语义即契约）：
      · ``"delivered"`` —— 有 Echo 帧或投递标记（服务端确认已达）
      · ``"rejected"``  —— 只有平台拒收回执（**没送达**）
      · ``""``          —— 无证据（保持既有状态，不臆断）

    匹配口径：优先 conv_id（精确），其次 uid（回声/标记可能落在
    会话 id 的任一方向上，故用 LIKE 两端匹配）。
    """
    if not account:
        return ""
    try:
        from database import get_db

        conn = get_db()
        acc = account
        if conv_id:
            # 回声帧：role=me 且 msg_type='7'（recv_daemon 的 WS 回执指纹）
            # 标记行：msg_id 以 verify: 开头（mark_delivery_verified 的命名空间）
            #
            # 🔴 2026-09-29：定位口径**复用项目 SSOT** `conv_identity.peer_uid`
            # 解析出的对端 uid，再按 uid 两端 LIKE 匹配 —— 不可用 conv_id 末段
            # 直接前缀匹配：实库两种方向（`0:1:对端:自己` 与 `0:1:自己:对端`）
            # **同时存在**，末段可能是自己，两端 LIKE 会把别的会话误判进来
            # （实机验证脚本 L1 当场抓出：99/186 条与独立真值不一致）。
            from services.conv_identity import peer_uid as _peer_uid

            _peer = ""
            try:
                _peer = str(_peer_uid(conv_id, _my_uid(acc)) or "")
            except Exception:
                _peer = ""
            if _peer:
                # 🔴 实库两种方向**同时存在**（`0:1:对端:自己` 与 `0:1:自己:对端`，
                # 实测同一对端两种形态都在），故必须同时命中两种规范形态。
                # 用 `IN (?, ?)` 精确匹配（而不是两端 `LIKE`）—— conv_id 恰好 4 段，
                # 精确串排除任何跨会话误伤。
                _my = _my_uid(acc)
                forms = []
                if _my:
                    forms = [f"0:1:{_peer}:{_my}", f"0:1:{_my}:{_peer}"]
                else:
                    forms = [conv_id, _flip_conv(conv_id)]
                ph = ",".join("?" * len(forms))
                row = conn.execute(
                    # 🔴 role='me' 必须限定：`msg_type='7'` 是**双方共用**的消息类型码，
                    # 对方发来的普通文本也是 '7'（实测把对方留言误当回声帧 ⇒ 假送达）。
                    "SELECT SUM(CASE WHEN msg_type='7' AND role='me' THEN 1 ELSE 0 END) echo,"
                    "       SUM(CASE WHEN msg_id LIKE 'verify:%' THEN 1 ELSE 0 END) marker,"
                    "       SUM(CASE WHEN text LIKE ? THEN 1 ELSE 0 END) rej "
                    f"FROM dm_messages WHERE account=? AND conv_id IN ({ph})",
                    (PLATFORM_REJECT_PREFIX + "%", acc, *forms),
                ).fetchone()
            else:
                # 对端解析不出来（my_uid 未知等）⇒ 退回精确 conv_id + 反向形态，
                # 宁可少匹配也不臆断（与 M-28「不猜」同一条纪律）。
                row = conn.execute(
                    "SELECT SUM(CASE WHEN msg_type='7' AND role='me' THEN 1 ELSE 0 END) echo,"
                    "       SUM(CASE WHEN msg_id LIKE 'verify:%' THEN 1 ELSE 0 END) marker,"
                    "       SUM(CASE WHEN text LIKE ? THEN 1 ELSE 0 END) rej "
                    "FROM dm_messages WHERE account=? AND (conv_id=? OR conv_id=?)",
                    (PLATFORM_REJECT_PREFIX + "%", acc, conv_id, _flip_conv(conv_id)),
                ).fetchone()
        elif uid:
            row = conn.execute(
                "SELECT SUM(CASE WHEN msg_type='7' AND role='me' THEN 1 ELSE 0 END) echo,"
                "       SUM(CASE WHEN msg_id LIKE 'verify:%' THEN 1 ELSE 0 END) marker,"
                "       SUM(CASE WHEN text LIKE ? THEN 1 ELSE 0 END) rej "
                "FROM dm_messages WHERE account=? AND conv_id LIKE ?",
                (PLATFORM_REJECT_PREFIX + "%", acc, f"%{uid}%"),
            ).fetchone()
        else:
            return ""
        if not row:
            return ""
        echo = int(row["echo"] or 0)
        marker = int(row["marker"] or 0)
        rej = int(row["rej"] or 0)
        if echo or marker:
            return "delivered"
        if rej:
            return "rejected"
        return ""
    except Exception as e:
        logger.debug(f"[SILENT-00] services.delivery_verify: delivery_state_of 失败: {e}")
        return ""


def _my_uid(account: str) -> str:
    """本账号 uid（复用 conv_identity.my_uid 的权威解析；取不到返回空串）。"""
    try:
        from services.conv_identity import my_uid as _mu

        return str(_mu(account) or "")
    except Exception as e:
        logger.debug(f"[SILENT-00] services.delivery_verify: my_uid 解析失败: {e}")
        return ""


def _flip_conv(conv_id: str) -> str:
    """`0:1:A:B` ↔ `0:1:B:A`（回声/标记可能以另一方向落库）。"""
    parts = (conv_id or "").split(":")
    if len(parts) == 4:
        return f"{parts[0]}:{parts[1]}:{parts[3]}:{parts[2]}"
    return conv_id
