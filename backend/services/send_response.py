# -*- coding: utf-8 -*-
"""私信投递响应的**唯一解析器**（Canonical Contract Law：同一事实只允许一个实现）。

## 为什么有这个模块（2026-09-23 审计 P0-1）

`probe_send_delivery` 的设计契约（`02_效果定义与探针.md` §2.2 / C-04）要求
「**禁止用『接口返回 200』『UI 弹窗』当投递判据**」。但生产链路把
`resp_json.get('message') == 'OK'` 直接当成投递成功 —— 而本项目源码里
`dy_apis/client_im.py` 自己就有注释：

    发送被静默拦截（返回 OK 但未实际投递，疑似内容违规/被截断）

即 **`message == 'OK'` 不足以证明投递**。真正的投递证据在响应体的
`body.field6 → field100` 里（`server_message_id` / `status` / `check_code`），
而 `static/Response_pb2.py` 的 `ResponseBody` **没有定义 field 100**
（已实测：oneof 只有 500/609/610）⇒ 必须走**宽容字节解析**。

本模块把 `scripts/diag/verify_text_send_delivery.py` 里已验证的解析逻辑
**收敛为唯一实现**，生产链路（`dy_apis/client_im.py`）与诊断脚本共同引用，
避免同一协议两处各写一遍（Duplicate Implementation 是下轮审查必提项）。

## 判定口径（与上游 zhinjs/douyin-im `isMessageDelivered()` 对齐）

- `server_message_id` **非空且非 0** —— 投递的**直接证据**（唯一硬判据）；
- `check_code == 8610` —— 内容安全检查未通过 ⇒ **明确未投递**；
- `check_code == 8101` —— 服务端标记已投递；
- `check_code == 10502` —— 已提交、审核中（不构成「已投递」）。
"""

from __future__ import annotations

# ── check_code 语义（上游判据，勿凭印象改） ──
CHECK_CODE_DELIVERED = 8101       # 服务端标记已投递
CHECK_CODE_UNDER_REVIEW = 10502   # 已提交但审核中
CHECK_CODE_SAFETY_BLOCKED = 8610  # 内容安全检查未通过（未投递）

# ===========================================================================
# 发送失败的结构化类型（Canonical Contract Law：全链路唯一枚举）
# ---------------------------------------------------------------------------
# 为什么需要它（2026-09-28 架构审计）：
#   改前「失败原因」是一条**人类可读字符串**，从 client_im 一路透传到
#   recv_daemon（JSON 的 error 字段）再到 dm_dispatch，后者靠**关键字子串
#   匹配**（FREQUENT/RATE/TOO_/... ）反推是否命中频控 —— 只要文案改一个字，
#   判定就静默失效（契约漂移）。实测断点：抖音风控返回 `{"decision":"KICK"}` 时，
#   client_im 生成的文案是「抖音拒绝发送：KICK（风控/限流，建议冷却后重试）」，
#   其中没有一个是关键字表里的词 ⇒ **最该冷静的一类失败反而不冷静**。
#
# 修法：把「失败类型」提升为**枚举**，在**唯一解析器**（本模块）定一次，
#   全链路透传 `error_kind`；字符串仅作人类可读补充。消费方（配额/冷静期）
#   只认枚举，不再猜文案。
# 设计契约：kinds 一经消费即不得改名（改名 = 破坏性变更，须走 ADR）。
# ===========================================================================
KIND_DELIVERED = "delivered"            # 已投递（有 server_message_id）
KIND_SAFETY_BLOCKED = "safety_blocked"  # 内容安全未通过（8610）
KIND_UNDER_REVIEW = "under_review"      # 已提交审核中（10502）
KIND_RATE_LIMITED = "rate_limited"      # 发送频繁 / 频控 → **触发冷静期**
KIND_RISK_CONTROL = "risk_control"      # 风控拦截 KICK/INVALID_REQUEST → **触发冷静期**
KIND_NEED_FOLLOW = "need_follow"        # 需互关后才能私信
KIND_PRIVACY = "privacy"                # 对方隐私/权限限制
KIND_USER_GONE = "user_gone"            # 对方不存在/已注销
KIND_DENIED = "denied"                  # 被拒绝 / 拉黑 / BLOCK
KIND_HTTP_ERROR = "http_error"          # HTTP 层非 200
KIND_PARSE_ERROR = "parse_error"        # 响应无法解析
KIND_UNKNOWN = "unknown"                # 其余

# 命中即应「强制冷静」的类型（配额/闸门的唯一判据，勿再叠加字符串匹配）
COOLDOWN_KINDS = frozenset({KIND_RATE_LIMITED, KIND_RISK_CONTROL})

# ── 关键字表（仅用于把**人类可读文案/响应体**归一到枚举，勿在别处重复）──
_KW_RATE = ("FREQUENT", "RATE", "TOO_", "LIMIT", "SPAM", "FREQUENCY",
            "频繁", "频控", "限流", "太快")
_KW_RISK = ("KICK", "INVALID_REQUEST", "RISK", "风控", "安全风险")
_KW_FOLLOW = ("MUTUAL", "FOLLOW_EACH", "NEED_FOLLOW", "INTERACT", "互关", "关注")
_KW_PRIVACY = ("PRIVILEGE", "PERMISSION", "PRIVACY", "隐私", "权限")
_KW_GONE = ("NOT_FOUND", "NOT FOUND", "USER_NOT_EXIST", "不存在", "注销")
_KW_DENIED = ("DENY", "FORBIDDEN", "BLOCK", "拒绝", "拉黑")


def failure_kind(resp_json: dict | None = None, verdict: dict | None = None,
                 http_ok: bool = True) -> str:
    """把一次发送失败**归一**到 KIND_* 枚举（唯一判定出口）。

    证据优先级：verdict（结构化，来自 delivery_verdict）> resp_json（原始体）。
    顺序敏感：先命中具体场景（风控/频控），再兜底。
    """
    if not http_ok:
        return KIND_HTTP_ERROR
    v = verdict or {}
    st = str(v.get("state") or "")
    if st == "delivered":
        return KIND_DELIVERED
    if st == "blocked":
        # blocked 目前只来自 8610 或 HTTP 失败
        if v.get("check_code") == CHECK_CODE_SAFETY_BLOCKED:
            return KIND_SAFETY_BLOCKED
        return KIND_HTTP_ERROR
    if st == "review":
        return KIND_UNDER_REVIEW
    j = resp_json or {}
    raw_msg = str(j.get("message") or "").upper()
    decision = str(j.get("decision") or "").upper()
    combined = " ".join([raw_msg, decision,
                         str(j.get("error_desc") or ""),
                         str(j.get("status_code") or "")]).upper()
    code = v.get("check_code")
    if code == CHECK_CODE_SAFETY_BLOCKED:
        return KIND_SAFETY_BLOCKED
    if code == CHECK_CODE_UNDER_REVIEW:
        return KIND_UNDER_REVIEW
    # 顺序：风控 > 频控 > 互关 > 隐私 > 不存在 > 被拒
    if any(k in combined for k in _KW_RISK):
        return KIND_RISK_CONTROL
    if any(k in combined for k in _KW_RATE):
        return KIND_RATE_LIMITED
    if any(k in combined for k in _KW_FOLLOW):
        return KIND_NEED_FOLLOW
    if any(k in combined for k in _KW_PRIVACY):
        return KIND_PRIVACY
    if any(k in combined for k in _KW_GONE):
        return KIND_USER_GONE
    if any(k in combined for k in _KW_DENIED):
        return KIND_DENIED
    if v.get("server_message_id"):
        return KIND_DELIVERED
    return KIND_UNKNOWN


def kind_is_cooldown(kind: str) -> bool:
    """该失败类型是否应触发冷静期（消费方唯一判据）。"""
    return kind in COOLDOWN_KINDS


def _fields(raw: bytes) -> dict:
    """宽容 protobuf 字段遍历（不依赖 .proto 定义，未定义字段也能读出来）。"""
    out: dict = {}
    i, n = 0, len(raw)
    while i < n:
        j, key, shift = i, 0, 0
        while j < n:
            b = raw[j]
            key |= (b & 0x7F) << shift
            j += 1
            if not (b & 0x80):
                break
            shift += 7
        if j > i + 10:
            break
        f_no, wt = key >> 3, key & 7
        if wt == 0:
            val, shift = 0, 0
            while j < n:
                b = raw[j]
                val |= (b & 0x7F) << shift
                j += 1
                if not (b & 0x80):
                    break
                shift += 7
            out.setdefault(f_no, []).append(val)
            i = j
        elif wt == 2:
            ln, shift = 0, 0
            while j < n:
                b = raw[j]
                ln |= (b & 0x7F) << shift
                j += 1
                if not (b & 0x80):
                    break
                shift += 7
            out.setdefault(f_no, []).append(raw[j:j + ln])
            i = j + ln
        elif wt == 5:
            out.setdefault(f_no, []).append(int.from_bytes(raw[j:j + 4], "little"))
            i = j + 4
        elif wt == 1:
            out.setdefault(f_no, []).append(int.from_bytes(raw[j:j + 8], "little"))
            i = j + 8
        else:
            break
    return out


def _s(p: dict, no: int):
    v = (p.get(no) or [None])[0]
    return v.decode("utf-8", "replace") if isinstance(v, bytes) else v


def parse_send_response(raw: bytes) -> dict:
    """解析 message/send 响应（顶层 body field 6 → 100）。

    上游 `SendMessageResponseBody` 字段表：
    1 server_message_id / 3 status / 4 client_message_id / 5 check_code / 6 check_message
    """
    top = _fields(raw or b"")
    res = {"cmd": _s(top, 1) if 1 in top else (top.get(1) or [None])[0],
           "error_desc": _s(top, 3), "message": _s(top, 4), "body100": None}
    body = (top.get(6) or [None])[0]
    if not body or not isinstance(body, (bytes, bytearray)):
        return res
    payload = (_fields(body).get(100) or [None])[0]
    if payload is None or not isinstance(payload, (bytes, bytearray)):
        return res
    p = _fields(payload)
    res["body100"] = {"server_message_id": (p.get(1) or [None])[0],
                      "status": (p.get(3) or [None])[0],
                      "client_message_id": _s(p, 4),
                      "check_code": (p.get(5) or [None])[0],
                      "check_message": _s(p, 6)}
    return res


def delivery_verdict(raw: bytes, http_ok: bool = True) -> dict:
    """把响应原始字节**归一**成投递判定事实（唯一出口）。

    返回契约（调用方**只准**读这些键，勿再自行解析）：

        {
          "http_ok": bool,            # HTTP 层是否 200
          "delivered": bool,          # 是否有「已投递」直接证据
          "state": "delivered" | "blocked" | "review" | "unknown",
          "server_message_id": str,   # 空 = 无证据
          "status": int|None,
          "check_code": int|None,
          "reason": str,              # 人类可读
        }

    ``state`` 语义：
      - ``delivered``  服务端给出消息号且未命中安全检查 ⇒ 可写投递验证标记
      - ``blocked``    check_code==8610（或 HTTP 失败/明确失败响应）⇒ **禁止**当成功
      - ``review``     check_code==10502（审核中）⇒ 有消息号但未确认可见
      - ``unknown``    其余（含「只回 OK、无消息号」——历史上被误当成功的那一类）
    """
    parsed = parse_send_response(raw)
    b = parsed.get("body100") or {}
    sid = str(b.get("server_message_id") or "").strip()
    if sid == "0":
        sid = ""
    code = b.get("check_code")
    code_i = int(code) if isinstance(code, (int, float)) else None
    status = b.get("status")
    status_i = int(status) if isinstance(status, (int, float)) else None

    def _out(state: str, delivered: bool, reason: str) -> dict:
        return {"http_ok": bool(http_ok), "delivered": bool(delivered), "state": state,
                "server_message_id": sid, "status": status_i, "check_code": code_i,
                "reason": reason,
                # 2026-09-28：结构化失败类型（消费方只认它，不再猜文案）
                "error_kind": (KIND_DELIVERED if delivered else
                               KIND_SAFETY_BLOCKED if state == "blocked" and code_i == CHECK_CODE_SAFETY_BLOCKED
                               else KIND_UNDER_REVIEW if state == "review"
                               else KIND_HTTP_ERROR if not http_ok
                               else KIND_UNKNOWN)}

    if not http_ok:
        return _out("blocked", False, "HTTP 非 200，无投递证据")
    if code_i == CHECK_CODE_SAFETY_BLOCKED:
        return _out("blocked", False,
                    f"内容安全检查未通过（check_code={CHECK_CODE_SAFETY_BLOCKED}），消息未投递")
    if not sid:
        return _out("unknown", False,
                    "服务端未返回 server_message_id —— 无投递直接证据"
                    "（历史上『只回 OK』这一类被误当成功，正是本判定要拦的）")
    if code_i == CHECK_CODE_UNDER_REVIEW:
        return _out("review", False,
                    f"已提交但审核中（check_code={CHECK_CODE_UNDER_REVIEW}），对方未必立即可见")
    if code_i == CHECK_CODE_DELIVERED:
        return _out("delivered", True, f"服务端标记已投递（{CHECK_CODE_DELIVERED}）")
    return _out("delivered", True,
                f"服务端返回消息号 {sid}（check_code={code_i}），且未命中安全检查拦截")
