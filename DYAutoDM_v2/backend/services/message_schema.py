# -*- coding: utf-8 -*-
"""消息落库 Schema SSOT（ADR-012）—— 类型注册表 + 单一写入出口。

设计意图（Design Intent）
------------------------
`dm_messages` 曾有 **7 处各自手写列名**的写入点，导致新类型数据「来了就往 text 塞」，
只能靠读侧黑名单事后补救（H-25 连续两轮）。本模块把规则前移到**写入前**：

  1. **类型注册表**：每个 msg_type / 文案模板必须显式登记，否则视为 unknown。
  2. **单一写入出口** `MessageRecord.build()`：所有写入点必须经它产出
     `(text, msg_type, extra)` 三元组。
  3. **未知类型安全降级**：未登记的类型，`text` 只写语义标签，原始载荷存
     `extra.raw` —— **污染被拦在写侧，而不是靠读侧黑名单兜**。

契约（ADR-011 扩展）
--------------------
  text         纯语义（用户对话文本 或 语义标签）；不得含 base64 / URL / 原始 JSON / 平台提示原文
  msg_type     类型码；必须在 MSG_TYPES 内
  extra.kind   语义分类（读侧**白名单**放行依据）
  extra.raw    未知类型的原始载荷（降级存放，供日后补入注册表时回溯）

前向兼容铁律
------------
> 任何未被注册表承认的数据，必须走安全降级，绝不允许以「疑似用户消息」的形态写进 text。
"""
from __future__ import annotations

import json
import re

# ---------------------------------------------------------------- 类型注册表

#: 已登记的 msg_type → (语义分类 kind, 归属字段说明)
#: kind 取值：user_text / media / system_notice / delivery_marker / unknown
MSG_TYPES: dict[str, tuple[str, str]] = {
    # —— 用户可见对话 ——
    "text": ("user_text", "纯文本消息"),
    "7": ("user_text", "我方发出的文本（回查帧）"),
    # —— 媒体（载荷走 extra，见 ADR-011）——
    "27": ("media", "图片/表情包；缩略图走 extra.thumb"),
    # 视频：WS 侧 t==8。此前只在查询侧被猜成 'video'（无任何写入点）
    # ⇒ 按方案 B 只认注册的 "8"，不存在的别名 'video' 从查询里移除。
    "8": ("media", "视频/分享视频；载荷走 extra.video_*"),
    # ⚠️ 发送侧历史别名 "image"：用户 2026-09-26 决策走**方案 B** ——
    # 调用点统一为 "27"，Registry **不收别名**（避免同一语义两个名字合法化）。
    # 若遇历史库中残留 msg_type='image' 的行，走迁移脚本归一，不在此登记。
    # —— 平台提示（非会话内容）——
    "15": ("system_notice", "平台系统提示"),
    "50010": ("system_notice", "平台系统提示（陌生人限制类）"),
    "1": ("system_notice", "平台提示/受限态"),
    "0": ("system_notice", "平台提示（未知细分）"),
    # —— 内部标记 ——
    "delivery_marker": ("delivery_marker", "投递验证标记，非会话内容"),
}

#: 读侧白名单：只有这些 kind 允许进 AI prompt / 前端会话流。
#: 未知类型天然被挡在外面 ⇒ 无需事后补黑名单。
READABLE_KINDS = frozenset({"user_text", "media"})

#: 语义标签（text 纯化后的占位）
LABEL_MEDIA = "[图片]"
LABEL_UNKNOWN = "[未知类型]"
LABEL_SYSTEM = "[系统提示]"

#: 平台提示文案模板（实测样本，2026-09-25：本账号 183 条，全 role='me'）。
#: 特征：固定模板 + `{{0}}` 占位符，由平台在发送时返回，**不是用户对话内容**。
#: ⚠️ 靠样本归纳；抖音新增模板会被降级为 unknown（危害从「污染」降为「不认识」）。
SYSTEM_TEXT_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"^对方回复或关注你之前[，,].*只能发送一条文字消息"),
    re.compile(r"^对方回复你或互关之前[，,].*可发送一条文字消息"),
    re.compile(r"^\{\{0\}\}$"),                     # 裸占位符
)

#: 已知噪音前缀（读侧兼容存量，写侧已不再产出）
NOISE_PREFIXES: tuple[str, ...] = (
    "[投递验证]", "[系统提示]", "[系统消息]", "[未知类型", "[未知媒体]",
)


def extra_get_kind(extra: str | dict | None) -> str:
    """从 extra 取语义分类；无则回落到 msg_type 推断。"""
    if isinstance(extra, dict):
        k = extra.get("kind")
        if k:
            return str(k)
    elif isinstance(extra, str) and extra.strip():
        try:
            obj = json.loads(extra)
            if isinstance(obj, dict) and obj.get("kind"):
                return str(obj["kind"])
        except Exception:
            pass
    return ""


def kind_of(msg_type: str | None) -> tuple[str, bool]:
    """查注册表 → (kind, known)。未登记 ⇒ ('unknown', False)。"""
    mt = "" if msg_type is None else str(msg_type)
    hit = MSG_TYPES.get(mt)
    if hit:
        return hit[0], True
    return "unknown", False


def is_system_text(text: str) -> bool:
    """平台提示文案判定（写侧 + 迁移复用同一判据，杜绝双写）。"""
    t = (text or "").strip()
    if not t:
        return False
    return any(p.search(t) for p in SYSTEM_TEXT_PATTERNS)


def is_noise_text(text: str) -> bool:
    """已知噪音前缀判定（读侧存量兼容）。"""
    t = (text or "").strip()
    return bool(t) and t.startswith(NOISE_PREFIXES)


# ---------------------------------------------------------------- 单一写入出口

class MessageRecord:
    """消息落库的**唯一**产出器（ADR-012 层 2）。

    用法（7 处写入点一律如此）：
        rec = MessageRecord.build(role=..., text=..., msg_type=..., extra={...})
        conn.execute(
            "INSERT OR IGNORE INTO dm_messages"
            "(account,conv_id,role,text,msg_type,extra,ts,msg_id) VALUES(?,?,?,?,?,?,?,?)",
            (acct, cid, rec.text, ...)  # ← 用 rec.text / rec.msg_type / rec.extra
        )
    """

    __slots__ = ("text", "msg_type", "extra", "kind")

    def __init__(self, text: str, msg_type: str, extra: dict, kind: str):
        self.text = text
        self.msg_type = msg_type
        self.extra = extra
        self.kind = kind

    @staticmethod
    def build(*, text: str, msg_type: str | None = None,
              extra: dict | None = None, role: str | None = None) -> "MessageRecord":
        """按注册表归一化一条消息；未知类型强制安全降级。"""
        ex: dict = dict(extra or {})
        raw_text = "" if text is None else str(text)
        mt = "" if msg_type is None else str(msg_type)

        # 次序：平台提示文案 → 未知类型 → 已登记类型
        if is_system_text(raw_text):
            ex["kind"] = "system_notice"
            ex.setdefault("raw", raw_text)
            return MessageRecord(raw_text, mt or "15", ex, "system_notice")

        kind, known = kind_of(mt)
        if not known:
            # 🔴 前向兼容铁律：未登记 ⇒ 降级，绝不污染 text
            ex["kind"] = "unknown"
            ex.setdefault("raw", raw_text)
            label = LABEL_UNKNOWN + (mt or "")
            return MessageRecord(label, mt, ex, "unknown")

        ex["kind"] = kind
        if kind == "media":
            # ADR-011：缩略图字节必须在 extra.thumb，不得在 text
            if raw_text.startswith("data:image") or "base64," in raw_text[:64] \
                    or raw_text.startswith("http"):
                ex.setdefault("raw", raw_text)
                return MessageRecord(LABEL_MEDIA, mt, ex, "media")
            return MessageRecord(raw_text or LABEL_MEDIA, mt, ex, "media")

        if kind == "delivery_marker":
            ex.setdefault("raw", raw_text)

        return MessageRecord(raw_text, mt, ex, kind)

    def extra_json(self) -> str:
        return json.dumps(self.extra, ensure_ascii=False)

    def tuple(self, account: str, conv_id: str, *,
              ts: float = 0.0, msg_id: str | None = None,
              role: str = "them") -> tuple:
        """产出与 `dm_messages(...)` 8 列**同序**的插入元组。

        8 列顺序：account, conv_id, role, text, msg_type, extra, ts, msg_id
        —— 写入点一律用它，杜绝各自手写列名导致的漏字段（ADR-012 层 2）。
        """
        return (account, conv_id, role, self.text, self.msg_type,
                self.extra_json(), ts, msg_id)


def readable(text: str, msg_type: str | None, extra: str | dict | None) -> bool:
    """读侧统一口径：是否允许进 AI prompt / 前端会话流（**白名单**）。

    判定次序：
      1. extra.kind 存在 ⇒ 以它为准（白名单 = READABLE_KINDS）
      2. 无 kind（存量行）⇒ 回落 msg_type 注册表 + 噪音前缀/系统文案排除
    """
    k = extra_get_kind(extra)
    if k:
        return k in READABLE_KINDS
    kind, known = kind_of(msg_type)
    if not known:
        return False
    if kind not in READABLE_KINDS:
        return False
    t = (text or "").strip()
    if is_noise_text(t) or is_system_text(t):
        return False
    return True
