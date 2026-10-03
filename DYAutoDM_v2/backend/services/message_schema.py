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

def _coerce_extra(extra) -> dict:
    """extra 归一化为 dict（ADR-012 补，2026-09-26 实测缺陷）。

    DB 读路径给的是 **JSON 字符串**；写路径给的是 dict。两者都必须支持。
    非法 JSON / 非容器类型 → 退化为空 dict（**绝不抛异常**：迁移脚本一次
    崩溃就会让整库标注中断，实测 360 行因此未被标注）。
    """
    if extra is None or extra == "":
        return {}
    if isinstance(extra, dict):
        return dict(extra)
    if isinstance(extra, (bytes, bytearray)):
        try:
            extra = bytes(extra).decode("utf-8", "replace")
        except Exception:                                   # noqa: BLE001
            return {}
    if isinstance(extra, str):
        s = extra.strip()
        if not s:
            return {}
        try:
            v = json.loads(s)
        except Exception:                                   # noqa: BLE001
            return {}
        return dict(v) if isinstance(v, dict) else {}
    try:
        return dict(extra)
    except Exception:                                       # noqa: BLE001
        return {}


class MessageRecord:
    """消息落库的**唯一**产出器（ADR-012 层 2）。

    用法（7 处写入点一律如此）：
        rec = MessageRecord.build(role=..., text=..., msg_type=..., extra={...})
        conn.execute(
            "INSERT OR IGNORE INTO dm_messages"
            "(account,conv_id,role,text,msg_type,msg_code,extra,ts,msg_id) VALUES(?,?,?,?,?,?,?,?,?)",
            (acct, cid, rec.text, ...)  # ← 用 rec.text / rec.msg_type / rec.extra
        )
    """

    __slots__ = ("text", "msg_type", "msg_code", "extra", "kind")

    def __init__(self, text: str, msg_type: str, extra: dict, kind: str,
                 msg_code: str | None = None):
        self.text = text
        self.msg_type = msg_type
        #: ★ 2026-10-03 P5：上游原始数字码，与语义名 msg_type **分列**。
        #: None = 上游只给了语义名（补拉/历史路径）⇒ 无原始码可存。
        self.msg_code = msg_code
        self.extra = extra
        self.kind = kind

    @staticmethod
    def build(*, text: str, msg_type: str | None = None,
              extra: dict | str | None = None,
              role: str | None = None) -> "MessageRecord":
        """按注册表归一化一条消息；未知类型强制安全降级。

        ⚠️ ADR-012 补（2026-09-26 实测）：`extra` 必须同时接受 **dict 与
        JSON 字符串**。DB 读路径（存量迁移 / 读侧）天然给的是字符串，
        原签名只收 dict ⇒ `dict("{"sender_sec_uid": ...}")` 直接抛
        ValueError。此处统一解析，非法 JSON 退化为空 dict（不崩）。
        """
        ex: dict = _coerce_extra(extra)
        raw_text = "" if text is None else str(text)
        mt = "" if msg_type is None else str(msg_type)

        # ★ 2026-10-03 P5：把「上游码 or 语义名」**分流**到两列。
        #   ⚠️ 必须在此**最前面**算 —— 下面每个 return 分支都要用 mt_sem/mt_code，
        #   放在 is_system_text 之后会 UnboundLocalError（实测 g2b/g9 崩）。
        #   kind_of 仍按注册表判语义（不受拆分影响）。
        mt_sem, mt_code = MessageRecord.split_type(mt)

        # 次序：平台提示文案 → 未知类型 → 已登记类型
        if is_system_text(raw_text):
            ex["kind"] = "system_notice"
            ex.setdefault("raw", raw_text)
            return MessageRecord(raw_text, mt_sem or "text", ex, "system_notice", mt_code)

        kind, known = kind_of(mt)
        if not known:
            # 🔴 前向兼容铁律：未登记 ⇒ 降级，绝不污染 text
            ex["kind"] = "unknown"
            ex.setdefault("raw", raw_text)
            label = LABEL_UNKNOWN + (mt or "")
            return MessageRecord(label, mt_sem, ex, "unknown", mt_code)

        ex["kind"] = kind
        if kind == "user_text" and is_noise_text(raw_text):
            # H-25：噪音前缀（`[投递验证]`/`[系统提示]`/`[系统消息]`/`[未知类型`/
            # `[未知媒体]`）是写侧占位/探针标记，**非会话内容**。读侧
            # `readable()` 对**有 kind** 的行只按白名单放行、不再回看前缀 ⇒
            # 必须在写侧就记 system_notice，否则有 kind 的行比存量无 kind 行更宽松
            # （存量行反而被 `is_noise_text` 拦下）。
            # 亦须与 `scripts/migrate_message_kind.py` 口径一致：后者把同一行判为
            # system_notice，且遵守「已标注即跳过」的幂等规则 ⇒ 写侧若错标为
            # user_text，迁移**永不纠正**。
            ex["kind"] = "system_notice"
            return MessageRecord(raw_text, mt_sem, ex, "system_notice", mt_code)
        if kind == "media":
            # ADR-011：缩略图字节必须在 extra.thumb，不得在 text
            if raw_text.startswith("data:image") or "base64," in raw_text[:64] \
                    or raw_text.startswith("http"):
                ex.setdefault("raw", raw_text)
                return MessageRecord(LABEL_MEDIA, mt_sem, ex, "media", mt_code)
            return MessageRecord(raw_text or LABEL_MEDIA, mt_sem, ex, "media", mt_code)

        if kind == "delivery_marker":
            ex.setdefault("raw", raw_text)

        return MessageRecord(raw_text, mt_sem, ex, kind, mt_code)

    def extra_json(self) -> str:
        return json.dumps(self.extra, ensure_ascii=False)

    @staticmethod
    def split_type(mt: str) -> tuple[str, str | None]:
        """★ 2026-10-03 P5：把「上游码 or 语义名」拆成 (语义名, 上游码)。

        ## 为什么要拆

        拆分前 `msg_type` 一列**混装两套体系**：语义名（'text'）与上游数字码
        （'7' / '27' / '50001'）。读侧判据因此分裂 —— 同一列上既有
        `== '50001'`（`api/messages.py:894`）又有 `== 'text'`（各处默认值），
        且 `api/messages._front_type` 得再做一次「数字→语义」归一才能给前端。

        ## 拆列后

        - `msg_type`：**只**存语义名（列语义单一，可直接给前端/AI 读）
        - `msg_code`：**只**存上游原始码（审计/回溯上游用，语义名时为 None）

        ## 映射口径

        语义名取自 `MSG_TYPES` 注册表登记的 kind 之外的**规范名**：
        上游码 `'7'`→`'text'`、`'27'`→`'image'`、`'8'`→`'video'`，
        其余系统类码（`'0'/'1'/'15'/'50010'`）→`'text'`（它们本来就是提示文案，
        由 `is_system_text` 分支另行处理，此处仅作兜底不崩）。
        未知值（未登记）⇒ 语义名原样透传、上游码 None（**不猜**）。
        """
        if not mt:
            return ("text", None)
        if not mt.isdigit():
            return (mt, None)          # 已是语义名
        code = mt
        # 🔴 不猜铁律：只有**已登记**的码才映射语义名；未登记的码**原样保留**
        #   （`build()` 的未知分支会加 [未知类型] 前缀并标 extra.kind='unknown'，
        #   若这里把它猜成 'text'，那条降级路径就永远走不到 ⇒ 分类失效）。
        #   ⚠️ 未登记码**仍要存入 msg_code**（那是上游原值，审计/回溯需要），
        #   只是 msg_type 不给它编语义名。
        name = {"7": "text", "27": "image", "8": "video"}.get(code)
        if name is None:
            return (code, code)       # 未登记：语义列存原值，码也留档
        return (name, code)

    def tuple(self, account: str, conv_id: str, *,
              ts: float = 0.0, msg_id: str | None = None,
              role: str = "them") -> tuple:
        """产出与 `dm_messages(...)` **9 列**同序的插入元组。

        9 列顺序：account, conv_id, role, text, msg_type, **msg_code**,
        extra, ts, msg_id
        —— 写入点一律用它，杜绝各自手写列名导致的漏字段（ADR-012 层 2）。
        ★ 2026-10-03 P5：新增 `msg_code`（上游原始码），与 msg_type 分列。

        2026-09-30（脏数据根治）：`ts` 若为 0（上游没给时间），在此**统一**
        从 `extra.created_at_us` 回补；两者都没有才落 0。
        放在唯一出口，是为了让 4 个写入点（`ts or 0` / `ts=m.get("ts") or 0`）
        一次受益，不必逐点改、也不会再漏。
        """
        return (account, conv_id, role, self.text, self.msg_type, self.msg_code,
                self.extra_json(), resolve_message_ts(ts, self.extra), msg_id)


def resolve_message_ts(ts, extra) -> float:
    """解析出一条消息可用的落库时间戳；**取不到就返回 0**（零信息，不谎报）。

    ## 为什么需要（2026-09-30 缺陷根治）
    写入侧有 4 处形如 `ts=m.get("ts") or 0` / `ts=ts or 0` 的落库调用。
    当上游没给 `ts` 时，落库的 ts 就是 **0** ⇒ 后端 `fmt_mt(0)` 返回空串
    ⇒ 该条消息在**聊天记录里没有任何时间**（这就是用户看到的「无意义时间脏数据」的源头）。

    而 extra 里其实**常常带着真实的服务端时间**：`created_at_us`
    （protobuf f4，服务端单调微秒序号）。它此前只被用于排序，没有被用来补 ts。

    ## 判据（按可信度降级，绝不编造）
    1. `ts` 有效（>0 且非 NaN）⇒ 直接用；
    2. 否则取 `extra["created_at_us"]`（**真实服务端数据**），微秒 → 秒；
    3. 两者都没有 ⇒ **返回 0**（保持"无时间"），**不用 `time.time()` 兜底**
       —— 那会把"我不知道"伪装成一个看似合理的具体值，脏数据从此无法被发现。
    """
    try:
        v = float(ts)
        if v and v == v:            # 非 0 且非 NaN
            return v
    except (TypeError, ValueError):
        pass

    ex = extra
    if isinstance(ex, str):
        try:
            ex = json.loads(ex)
        except Exception:                                   # noqa: BLE001
            ex = None
    if isinstance(ex, dict):
        raw = ex.get("created_at_us")
        if raw:
            try:
                us = float(raw)
                if us > 0:
                    return us / 1_000_000.0
            except (TypeError, ValueError):
                pass
    return 0.0


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
