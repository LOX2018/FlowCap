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


