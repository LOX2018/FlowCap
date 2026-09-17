"""私信会话 —— protobuf 解析工具层

## 为什么独立（2026-09-15 大单文件打散）

原 `auto_dm/conversation_capture.py`（1687 行）前段约 270 行是**与业务无关的
protobuf 解析工具**（varint 线型常量、子消息递归、字符串/数字长字段提取、
sender/时间/消息 ID/text 字段解析）。抽出后：

  · conversation_capture.py → 会话抓取与 HTTP 业务
  · im_protobuf.py         → 协议解析工具（可单测）

## 说明
**纯搬移**——所有解析逻辑逐字节不变。
"""
from __future__ import annotations

import json
import re
import struct

WT_VARINT = 0
WT_64BIT = 1
WT_LEN = 2
WT_32BIT = 5

CONV_RE = re.compile(rb"0:1:\d{5,20}:\d{5,20}")

# 富媒体文本提取器：由业务层（conversation_capture）在导入后注入 ——
# 见 set_media_text_extractor()。这样工具层**不反向依赖业务层**。
#
# 2026-09-15 修复（对照主分支 v2-refactor @af07378 确证）：
#   158b47d 把 _parse_message_text 从 conversation_capture.py 搬到本模块时，
#   漏搬了两个依赖 → 该函数对**所有**消息恒返回 None（首包「含消息的 0 个」根因）：
#     ① `json` 未导入 → json.loads 抛 NameError，被函数内 `except: continue`
#        **静默吞掉**（无任何日志），表现为「解析不出消息」而非报错；
#     ② `_extract_media_text` 留在 conversation_capture.py → 富媒体路径同样 NameError。
#   主分支两文件本在同一模块（json 在 26 行、_extract_media_text 在 402 行），
#   从不报错 —— 故此为纯搬移引入的回归。
_MEDIA_TEXT_EXTRACTOR = None


def set_media_text_extractor(fn) -> None:
    """注入富媒体文本提取器（业务层实现，工具层调用）。"""
    global _MEDIA_TEXT_EXTRACTOR
    _MEDIA_TEXT_EXTRACTOR = fn


def get_media_text_extractor():
    """取当前注入的富媒体文本提取器；未注入时按 None 处理（调用方降级）。"""
    return _MEDIA_TEXT_EXTRACTOR



# ---------------------------------------------------------------------------
# 通用 protobuf 解析（项目未定义 cmd 2043 的 pb2，用通用递归解析）
# ---------------------------------------------------------------------------
def _parse(buf):
    """解析 protobuf message -> [(field, wiretype, value)]，len 字段 value=bytes。"""
    out = []
    i, n = 0, len(buf)
    while i < n:
        tag = 0
        shift = 0
        while i < n:
            b = buf[i]
            i += 1
            tag |= (b & 0x7F) << shift
            shift += 7
            if not (b & 0x80):
                break
        if i >= n:
            break
        field = tag >> 3
        wt = tag & 0x7
        if wt == WT_VARINT:
            v = 0
            s = 0
            while i < n:
                b = buf[i]
                i += 1
                v |= (b & 0x7F) << s
                s += 7
                if not (b & 0x80):
                    break
            out.append((field, wt, v))
        elif wt == WT_64BIT:
            if i + 8 > n:
                break
            out.append((field, wt, int.from_bytes(buf[i:i + 8], "little")))
            i += 8
        elif wt == WT_LEN:
            ln = 0
            s = 0
            while i < n:
                b = buf[i]
                i += 1
                ln |= (b & 0x7F) << s
                s += 7
                if not (b & 0x80):
                    break
            if i + ln > n:
                break
            sub = buf[i:i + ln]
            i += ln
            out.append((field, wt, sub))
        elif wt == WT_32BIT:
            if i + 4 > n:
                break
            out.append((field, wt, int.from_bytes(buf[i:i + 4], "little")))
            i += 4
        else:
            break
    return out


def _find_submessages(parsed, depth=0):
    res = []
    for f, wt, v in parsed:
        if wt == WT_LEN and isinstance(v, bytes) and len(v) > 2:
            sub = _parse(v)
            if sub:
                res.append((f, v))
                res.extend(_find_submessages(sub, depth + 1))
    return res


def _extract_str(b):
    out = []
    i, n, cur = 0, len(b), bytearray()
    while i < n:
        c = b[i]
        if 0x20 <= c < 0x7F:
            cur.append(c)
            i += 1
        elif 0xC0 <= c <= 0xDF and i + 1 < n:
            cur += b[i:i + 2]
            i += 2
        elif 0xE0 <= c <= 0xEF and i + 2 < n:
            cur += b[i:i + 3]
            i += 3
        else:
            if cur:
                try:
                    s = bytes(cur).decode("utf-8")
                    if 1 <= len(s) <= 800:
                        out.append(s)
                except Exception:
                    pass
            cur = bytearray()
            i += 1
    if cur:
        try:
            s = bytes(cur).decode("utf-8")
            if 1 <= len(s) <= 800:
                out.append(s)
        except Exception:
            pass
    return out


def _strip_pb_prefix(s):
    """剥掉 protobuf tag 残影（如 'GBE' / 'BF' / 'B{'），保留 JSON 部分。
    抖音消息形如 GBE{"aweType":700,"text":"你好"}J，前缀后缀是 protobuf field tag 字节。"""
    # 找第一个 { 之前的可见 ASCII 前缀去掉；找最后一个 } 之后的后缀去掉
    start = s.find("{")
    end = s.rfind("}")
    if start < 0 or end < 0 or end <= start:
        return None
    return s[start:end + 1]


def _parse_message_sender(b):
    """从消息对象 bytes 里提取发送方 UID（protobuf field 7，varint）。

    实测（2026-08-29）：首包(2043)与历史(301)的消息对象里，field 7 = sender UID。
    - sender == my_uid -> 我发
    - sender == peer_uid -> 对方发

    注意：不可用 aweType/content 类型推断方向 —— 那是消息类型，与发送方无关
    （历史 bug：role = "them" if aweType == 700 else "me"，导致方向全反）。
    """
    try:
        for f, wt, v in _parse(b):
            if f == 7 and wt == WT_VARINT:
                return str(v)
    except Exception:
        pass
    return None


def _parse_message_create_time(b):
    """从消息对象 bytes 里提取创建时间（protobuf field 10，毫秒时间戳）。

    2026-09-06 黑盒实证（知识库 08 §24.9 分割线事故）：首包(2043)每条
    message 对象的 field 10 = create_time 毫秒，与 dm_messages 历史时间
    完全吻合。此前 ts 全部 fallback 到入库 time.time()，导致首包批量
    解析的会话全部挤在同一天，前端日期分割线只剩一条。
    返回秒级 float（毫秒/1000），缺失返回 0。
    """
    try:
        for f, wt, v in _parse(b):
            if f == 10 and wt == WT_VARINT:
                # 毫秒时间戳范围校验（2020-2030），防误配其它字段
                if 1577836800000 <= v <= 1893456000000:
                    return v / 1000.0
                # 秒级时间戳兜底
                if 1577836800 <= v <= 1893456000:
                    return float(v)
    except Exception:
        pass
    return 0


def _parse_message_id(b):
    """从消息对象 bytes 里提取消息唯一 ID（protobuf field 3，varint）。

    用于落库去重（唯一索引 uniq_dmmsg），杜绝 capture_all 重复运行导致重复写入
    （历史 bug：dm_messages 无唯一约束，INSERT OR IGNORE 形同虚设，
      实测「转角遇到」会话真实 ~19 条被写成 104 条）。
    """
    try:
        for f, wt, v in _parse(b):
            if f == 3 and wt == WT_VARINT:
                return str(v)
    except Exception:
        pass
    return None


def _parse_message_sec_uid(b):
    """从消息对象 bytes 里提取发送者 sec_uid（protobuf field 14，len/UTF-8）。

    2026-09-17 新增（对照上游 TeamBreakerr/douyin-chat-export v1.0.0/v2.0.0
    同一实现，其注释原文：「Field 14: 发送者 sec_uid。群聊补全昵称/头像的
    唯一线索 —— IM 用户信息接口只认 sec_uid，不认 f7 的数字 uid」）。

    设计边界（不得越界）：
      · 本函数**只做解析**，不发起任何请求、不查昵称 ——
        昵称红线仍归 BCC 被动截获（铁律 §二），此处仅把**消息自带**的
        sec_uid 提取出来，供：(a) 语音识别请求体回填、(b) 前端展示/跳转。
      · 返回值形如 "MS4wLjABAAAA..."；缺失返回 None。

    len 线上是 UTF-8 串；若解出的串不符合 sec_uid 形态（长度/前缀），
    返回 None 而不是把噪声当 sec_uid（避免脏值扩散）。
    """
    try:
        for f, wt, v in _parse(b):
            if f == 14 and wt == WT_LEN and isinstance(v, bytes):
                try:
                    s = v.decode("utf-8", "ignore").strip()
                except Exception:
                    return None
                if 8 <= len(s) <= 256 and s.startswith("MS4wLjAB"):
                    return s
                return None
    except Exception:
        pass
    return None


def _parse_message_created_at_us(b):
    """从消息对象 bytes 里提取**服务端单调递增**序号（protobuf field 4）。

    2026-09-17 新增（对照上游 douyin-chat-export；其 README 头号特性原文：
    「**精确排序** — 用服务端 `created_at_us` 单调递增序号排序，消息顺序不乱」，
    web_scraper.py 注释亦写明「order 用于排序：created_at_us 是单调递增的，
    用作排序键」，并按 `order_high = created_at_us >> 32` /
    `order_low = created_at_us & 0xFFFFFFFF` 拆成两列存放）。

    为什么需要它：本模块此前用 field 10（毫秒 create_time）当排序键，
    **同秒消息会并列** → 顺序不稳定（首包/301 两路合并时尤甚）。
    field 4 是服务端生成的单调序号，同秒内也能定序。

    返回 int（原值，微秒量级；缺失返回 None，调用方降级到 field 10）。
    """
    try:
        for f, wt, v in _parse(b):
            if f == 4 and wt == WT_VARINT:
                # 量级校验：2020-2030 的微秒时间戳（1.577e15 ~ 1.893e15）。
                # 上游把它当「序号」用，但实测其取值落在微秒区间，故按区间兜底，
                # 只排除明显不含时间的噪声值。
                if 1_500_000_000_000_000 <= v <= 2_000_000_000_000_000:
                    return int(v)
                return None
    except Exception:
        pass
    return None


def _parse_message_reply(b):
    """解析「引用回复」（protobuf field 18）→ dict 或 None。

    2026-09-17 新增（对照上游 douyin-chat-export 的 `parseMessage`：
      「Field 18: 引用/回复消息。结构: f1=被引用消息 server_id,
        f2=JSON(content, nickname, refmsg_sec_uid, refmsg_content)」）。

    返回：
      {"ref_msg_id": <被引用消息 server_id 字符串>,
       "text":       <被引用正文，优先 refmsg_content/data.text>,
       "nickname":   <被引用消息发送者昵称，可能为空>,
       "sec_uid":    <被引用消息发送者 sec_uid，可能为空>}
    解析失败返回 None（**不抛异常**，不阻断主消息解析）。
    """
    try:
        for f, wt, v in _parse(b):
            if f != 18 or wt != WT_LEN or not isinstance(v, bytes):
                continue
            ref_id = None
            payload = None
            for _f, _wt, _v in _parse(v):
                if _f == 1:
                    # 上游 `parseProto` 把 varint 存成字符串 → f1 是 **varint**
                    # （被引用消息 server_id）。此处对 len 线型也容错（历史形态），
                    # 两种编码都归一成十进制字符串。
                    if _wt == WT_VARINT:
                        ref_id = str(_v)
                    elif _wt == WT_LEN and isinstance(_v, bytes):
                        ref_id = _v.decode("utf-8", "ignore").strip() or None
                elif _f == 2 and _wt == WT_LEN and isinstance(_v, bytes):
                    payload = _v
            if not isinstance(payload, bytes):
                return None
            try:
                obj = json.loads(_strip_pb_prefix(payload.decode("utf-8", "ignore")) or "{}")
            except Exception:
                return None
            if not isinstance(obj, dict):
                return None
            # 正文：上游按 refmsg_content 优先；本模块另兼容 data.text 形态。
            text = obj.get("refmsg_content") or obj.get("content") or ""
            if not text:
                _data = obj.get("data")
                if isinstance(_data, dict):
                    text = _data.get("text") or _data.get("content") or ""
            _rid = str(ref_id) if ref_id not in (None, "", 0) else ""
            _text = str(text or "").strip()
            if not _rid and not _text:
                return None
            return {
                "ref_msg_id": _rid,
                "text": _text,
                "nickname": str(obj.get("nickname") or "").strip(),
                "sec_uid": str(obj.get("refmsg_sec_uid") or "").strip(),
            }
    except Exception:
        pass
    return None


def _parse_message_flags(b):
    """提取消息对象的状态标志（protobuf）：f11 = is_recalled、f12 = visible。

    2026-09-17 新增（对照上游 douyin-chat-export 的 `parseMessage`：
      「else if (fn===11) r.is_recalled=Number(v); else if (fn===12) r.visible=Number(v);」
    其撤回判据原文即 `if (cj?.is_recalled) return true`）。

    为什么需要它：我方此前**只靠正文占位串**（`Recall Content Hided`）识别撤回 ——
    那是抖音把消息体替换后的产物，属「猜文案」；f11 才是服务端给的**字段级判据**
    （上游 `is_recalled` ⇒ 真值即已撤回）。同理 f12=visible=0 表示对端不可见。

    返回 `(is_recalled, visible)`：各自为 int 或 None（字段缺失）。
    解析失败返回 `(None, None)`，绝不抛异常。
    """
    rec = None
    vis = None
    try:
        for f, wt, v in _parse(b):
            if wt != WT_VARINT:
                continue
            if f == 11:
                rec = int(v)
            elif f == 12:
                vis = int(v)
    except Exception:
        pass
    return rec, vis


def _extract_long_str(b, min_len=200, max_len=200000):
    """提取**长**字符串（用于图片等富媒体 JSON）。

    `_extract_str` 有 `1 <= len(s) <= 800` 的硬上限（原为过滤短噪音设计），
    但图片消息的 content JSON 实测长达 8KB+（含 inline_pic base64），
    被该上限整条丢弃 —— 这是「图片消息解析后 0 条」的真因。

    本函数只放宽长度，其余提取规则与 `_extract_str` 一致，
    且单独提供，避免影响依赖 `_extract_str` 短串语义的其他逻辑。
    """
    out = []
    i, n, cur = 0, len(b), bytearray()
    while i < n:
        c = b[i]
        if 0x20 <= c < 0x7F:
            cur.append(c)
            i += 1
        elif 0xC0 <= c <= 0xDF and i + 1 < n:
            cur += b[i:i + 2]
            i += 2
        elif 0xE0 <= c <= 0xEF and i + 2 < n:
            cur += b[i:i + 3]
            i += 3
        else:
            if cur:
                try:
                    s = bytes(cur).decode("utf-8")
                    if min_len <= len(s) <= max_len:
                        out.append(s)
                except Exception:
                    pass
            cur = bytearray()
            i += 1
    if cur:
        try:
            s = bytes(cur).decode("utf-8")
            if min_len <= len(s) <= max_len:
                out.append(s)
        except Exception:
            pass
    return out


def _parse_message_text(b):
    """从消息对象的 bytes 里提取聊天文本。返回 (text, aweType, createdAt) 或 None。

    2026-08-30 两处修复（实测定位，图片消息此前解析后为 0 条）：
      1. 原实现要求字符串必须含 `"text"` 且 obj["text"] 非空，否则 continue
         —— 图片/表情/语音等富媒体消息**没有 text 字段**，在第一关就被丢弃。
      2. `_extract_str` 有 `len <= 800` 硬上限，而图片消息 content JSON
         实测 8KB+（含 inline_pic base64），被整条过滤掉。
    现改为：先用短串路径，失败后用长串路径 `_extract_long_str` 重试。

    2026-09-15 修复（本模块被单独抽出后的回归，见文件头注释）：
      · 富媒体提取器改为经 `set_media_text_extractor()` 注入后取用；
      · `strs` 路径内的 `except: continue` **保留**（单个串解析失败属正常），
        但记录首次异常，避免「静默恒返 None」再次无迹可查（下次 5 秒定位）。
    """
    # 先走原有短串路径（保持文本消息行为完全不变）
    strs = _extract_str(b)
    for s in strs:
        if '"text"' in s:
            j = _strip_pb_prefix(s)
            if not j:
                continue
            try:
                obj = json.loads(j)
            except Exception:
                continue
            if not isinstance(obj, dict):
                continue
            text = obj.get("text") or obj.get("tips")
            if not text:
                continue
            return (text, obj.get("aweType"), obj.get("createdAt") or 0)

    # 短串没拿到文本 -> 可能是富媒体（JSON 超 800 字符被过滤），走长串路径
    for s in _extract_long_str(b):
        if '"resource_url"' not in s and '"inline_pic"' not in s:
            continue
        j = _strip_pb_prefix(s)
        if not j:
            continue
        try:
            obj = json.loads(j)
        except Exception:
            continue
        if not isinstance(obj, dict):
            continue
        # 富媒体提取器由业务层注入（工具层不反向依赖业务层）。
        _ex = _MEDIA_TEXT_EXTRACTOR
        if _ex is None:
            # 未注入：不静默丢数据 —— 记录一次，便于快速定位装配遗漏。
            _warn_once("media_text_extractor 未注入，富媒体消息被跳过")
            continue
        try:
            text = _ex(obj)
        except Exception as _e:  # noqa: BLE001
            _warn_once(f"富媒体提取失败: {type(_e).__name__}: {_e}")
            continue
        if not text:
            continue
        return (text, obj.get("aweType"), obj.get("createdAt") or 0)
    return None


_WARNED: set[str] = set()


def _warn_once(msg: str) -> None:
    """同一类问题只记一次（避免逐消息刷屏，但**绝不静默**）。"""
    if msg in _WARNED:
        return
    _WARNED.add(msg)
    try:
        from loguru import logger
        logger.warning(f"[PB-001] " + f"[im_protobuf] {msg}")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 首包精确解析（V23 实证：field 6 = conversation 数组）
# ---------------------------------------------------------------------------


