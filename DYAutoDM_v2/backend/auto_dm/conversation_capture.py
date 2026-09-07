# coding=utf-8
"""私信列表及会话详情捕获（前移捕获方案，对应工作记忆/08）。

设计（实机 V6~V23 验证）：
- 昵称/头像来自抖音前端自己发的 `POST /aweme/v1/web/im/user/info/`，响应自带 sec_uid
  （{nickname, sec_uid, avatar_small, avatar_thumb}），无需数组顺序桥接。
- 聊天内容来自首包 `get_message_by_init`（cmd 2043）protobuf：
  * 顶层 field 6 = conversation 数组（每个子对象内嵌 conv_id + 子消息）
  * 每条 message 对象自身内嵌 conv_id 链接键（不靠邻近匹配即可 100% 配对）
  * 消息体是 JSON 字符串嵌套在 protobuf len 字段，形如
    `GBE{"aweType":700,"text":"你好"}J`（前缀是 protobuf tag 残影，JSON 部分可直接 json.loads）
  * 无密码学加密，protobuf 解析 + JSON 提取即可

流程（account 级、一次性，不在私信页运行时批量查）：
1. 后端调 get_message_by_init 拿首包字节（imapi 私有网关，项目已有，零新增网页 REST 请求）
2. 无头指纹浏览器打开 chat，hook 截 im/user/info 响应 → {sec_uid: {nickname, avatar}}
3. 首包 protobuf 精确解析 → {conv_id, peer_uid, sec_uid, messages:[]}
4. 按 peer_uid ↔ im/user/info 的 uid 关联昵称/头像（首包 sec_uid 仅作 fallback）→ 写 dm_conversations + dm_messages

接入点：
- auto_dm/accounts.py verify_account(dm_loopback=True) 凭证有效后调用 capture_all
- daemon/recv_daemon.py 守护启动顺带补一次
"""
import os
import re
import json
import time
import threading
import traceback

from loguru import logger

# protobuf wire types
WT_VARINT = 0
WT_64BIT = 1
WT_LEN = 2
WT_32BIT = 5

CONV_RE = re.compile(rb"0:1:\d{5,20}:\d{5,20}")


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
        text = _extract_media_text(obj)
        if not text:
            continue
        return (text, obj.get("aweType"), obj.get("createdAt") or 0)
    return None


# ---------------------------------------------------------------------------
# 首包精确解析（V23 实证：field 6 = conversation 数组）
# ---------------------------------------------------------------------------
def _inline_max_kb() -> int:
    """内联 base64 的体积上限（KB）。超过才走图床。

    2026-08-31 实测：私信图片平均仅 2.9KB，60 张合计 121KB，
    全部内联只让数据库增加 13%。而图床渲染每张要多花 0.35~2.5s 下载。
    故默认给一个较宽松的 32KB 阈值 —— 绝大多数图片都会内联，
    只有异常大的图才上图床。设为 0 表示**永远内联**（图床仅作兜底）。
    """
    try:
        from config import settings

        v = getattr(settings, "image_inline_max_kb", None)
        if v is not None:
            return int(v)
    except Exception:
        pass
    import os

    try:
        return int(os.environ.get("IMAGE_INLINE_MAX_KB", "32"))
    except Exception:
        return 32


def _norm_inline_pic(s: str) -> str:
    """把 inline_pic 的 base64 规范化（去空白 + 补 padding）。

    实测：inline_pic 值内含 \\r\\n 换行，需先去掉再解码；
    base64 长度非 4 倍数时要补 '='。
    """
    if not s:
        return ""
    import re as _re
    body = _re.sub(r"\s+", "", s)
    if len(body) < 100:
        return ""
    return body + "=" * ((-len(body)) % 4)


def _extract_image_secret(obj: dict):
    """提取图片消息的解密要素：(skey, origin_url) —— 2026-09-01 实机证真。

    来源（开源项目 douyin-chat-export 标注 + 本机实测确认）：
        消息 protobuf field 8 = content_json（明文 JSON）
            └─ resource_url.skey               = 64 hex = 32 字节 AES-256 密钥
            └─ resource_url.origin_url_list[0] = 加密原图 URL

    抖音 IM 图片是 **AES-256-GCM 加密** 的标准格式（此前 §二十七 误判为
    「私有加密不可得」，实测推翻，见 08 §三十五）：
        key = bytes.fromhex(skey)
        iv  = 密文前 12 字节
        plain = AESGCM(key).decrypt(iv, cipher[12:], None)

    返回 (skey, origin_url)；无则 (None, None)。
    注意：URL 里的 & 在 JSON 中被转义为 \\u0026，需还原。
    """
    res = obj.get("resource_url")
    if not isinstance(res, dict):
        return None, None
    skey = res.get("skey")
    if not isinstance(skey, str) or not skey:
        return None, None
    origin = ""
    for k in ("origin_url_list", "large_url_list", "medium_url_list", "thumb_url_list"):
        lst = res.get(k)
        if isinstance(lst, list) and lst and isinstance(lst[0], str):
            origin = lst[0]
            break
    if not origin:
        return None, None
    # JSON 内 & 被转义成 \u0026，必须还原，否则带签名参数的 URL 会失效
    origin = origin.replace("\\u0026", "&")
    return skey, origin


def _extract_media_text(obj: dict):
    """从富媒体消息体里提取可读文本 + 媒体 URL。

    抖音图片/表情/语音/视频卡片等消息**没有 text 字段**，只有资源 URL。
    原解析只取 text，这类消息会被整条丢弃（前端看不到或只剩「图片」二字）。

    2026-08-30 实测重大修正（双 URL 方案）：
      图片消息体里有两个独立来源，必须都取：
        1) inline_pic   —— 内嵌在消息里的 **标准 WebP 缩略图 base64**，
                           零请求、零风控，实测 27/35 条可用，平均 3.5KB。
                           这是「默认显示缩略图」的数据源。
        2) resource_url —— 远程原图/大图 URL，**实测为抖音私有加密格式**
                           （熵 7.999/8.0，256 字节值均匀分布，无图片魔数），
                           浏览器无法直接解码，仅作「点击查看原图」的跳转引用。
      远端 URL 四组（thumb/medium/large/origin）实测**全部加密**，无一可内嵌。

    输出格式（前端按行拆分）：
        "[图片] data:image/webp;base64,<inline_pic>"
        或（无 inline_pic 时）
        "[图片] <thumb_url>"
        第二行（可选）："[原图] <origin_url>"

    返回 None 表示确实无法解析（调用方会 continue 丢弃）。
    """
    # 图片：优先 inline_pic 缩略图，其次远程 URL
    res = obj.get("resource_url")
    if isinstance(res, dict):
        # 2) 远程 URL（原图引用 + 无 inline_pic 时的降级缩略图）
        def _first(*keys):
            for k in keys:
                lst = res.get(k)
                if isinstance(lst, list) and lst:
                    return lst[0]
            return ""

        origin = _first("origin_url_list", "large_url_list", "url_list")
        thumb = _first("thumb_url_list", "medium_url_list")
        # 1) 内嵌缩略图（最高优先级：可直接渲染）
        inline = _norm_inline_pic(obj.get("inline_pic") or "")
        if inline:
            # 2026-08-31 实测修正：**小图直接内联 base64，不上图床**。
            # 图片平均仅 2.9KB（60 张合计 121KB），内联让数据库 +13%，
            # 却换来零网络请求、瞬时渲染；而图床每张要多花 0.35~2.5s 下载，
            # 且境外图床还会超时 —— 为省 3KB 付出百毫秒延迟是**本末倒置**。
            # 只有超过阈值的大图才走图床，避免单条消息过大拖慢列表查询。
            thumb_src = f"data:image/webp;base64,{inline}"
            max_kb = _inline_max_kb()
            size_kb = len(inline) * 3 // 4 // 1024  # base64 → 原始字节估算
            if max_kb and size_kb > max_kb:
                # 大图：上传图床换短链接；失败则仍内联（保证可渲染）
                try:
                    from auto_dm import image_host

                    hosted = image_host.upload_base64(inline)
                    if hosted:
                        thumb_src = hosted
                except Exception:
                    pass
            body = f"[图片] {thumb_src}"
            if origin:
                body += f"\n[原图] {origin}"
            return body
        # 无 inline_pic：用远程缩略图 URL 兜底（前端会降级为可点击链接）
        if thumb:
            body = f"[图片] {thumb}"
            if origin:
                body += f"\n[原图] {origin}"
            return body
        if origin:
            return f"[图片] {origin}"
    # 表情包：url.url_list[0]
    urlobj = obj.get("url")
    if isinstance(urlobj, dict):
        lst = urlobj.get("url_list")
        if isinstance(lst, list) and lst:
            return f"[表情包] {lst[0]}"
    # 视频分享
    if obj.get("itemId"):
        return f"[分享视频] 视频ID {obj.get('itemId')}"
    # 2026-08-31 实测：打招呼卡片 / 推荐表情包不是聊天内容，
    # 形如 {"hint_text":"我们已互相关注，可以开始聊天了","stickers":[...]}
    # 或 {"hello_text":"打个招呼吧","stickers":[...]}。
    # 这些是**系统提示**，之前被兜底成 "[未知媒体] {...JSON...}" 写进库，
    # 前端显示成一长串乱码 JSON（用户反馈的"污染信息"之一）。
    # 这里转成系统提示文案；无文案则丢弃。
    if "hint_text" in obj or "hello_text" in obj or "joker_stickers" in obj:
        hint = (obj.get("hint_text") or obj.get("hello_text") or "").strip()
        if hint:
            return f"[系统提示] {hint}"
        return None
    # 纯表情包推荐（无文案）也不是消息内容
    if "stickers" in obj and not obj.get("text"):
        return None
    # 兜底：空对象（如 {}）不代表真实媒体，返回 None 让调用方丢弃，
    # 避免把 "[未知媒体] {}" 这类噪音写进聊天记录（实测出现过）。
    if not obj:
        return None
    # 其他未知媒体：把 JSON 截短存下来，至少不丢消息
    try:
        import json as _json
        return "[未知媒体] " + _json.dumps(obj, ensure_ascii=False)[:200]
    except Exception:
        return None


def parse_init_protobuf(raw, my_uid):
    """解析 get_message_by_init 首包，返回会话列表（含消息）。

    返回 [{conversation_id, peer_uid, sec_uid, messages:[{role,text,ts}]}]
    - conversation 数组 = 顶层 field 6
    - 每条 message 内嵌 conv_id 链接键（0:1:uid:uid）
    - 消息体 JSON 嵌套在 len 字段，剥 pb 前缀后 json.loads 取 text
    """
    if not raw:
        return []
    # 2026-09-07 根因修复（D 方案）：my_uid 失效导致 peer_uid 恒取 uid_a。
    # 实测：auth.get_uid() 偶发返回空/None（凭证刷新时序），my_uid 变成 ""
    # 或 "None"，使下方 564 行 `uid_b if uid_a == my_uid else uid_a` 恒走
    # else 分支取 uid_a —— 而抖音 conv_id 里 uid_a 常是本账号，结果 32/77
    # 个会话的 peer_uid 被写成自己，peer_name 全填成"尚进工伤小助理"。
    # 自愈：conv_id 形如 0:1:uidA:uidB，本账号 UID 必然出现在【每一个】
    # conv_id 中（自己与所有人聊天），故出现次数 == 会话数 的 UID 即本账号。
    # 仅当传入 my_uid 无效时才覆盖，不干扰正常路径。
    _mu = str(my_uid or "").strip()
    if _mu in ("", "None", "none", "0"):
        try:
            import re as _re
            from collections import Counter as _Counter
            _probe = _re.findall(rb"0:1:(\d{6,20}):(\d{6,20})", raw)
            if _probe:
                _cnt = _Counter()
                for _a, _b in _probe:
                    _cnt[_a.decode()] += 1
                    _cnt[_b.decode()] += 1
                _n = len(_probe)
                # 本账号出现次数应接近会话数（每个 conv_id 至少含一次）
                _cand = [(u, c) for u, c in _cnt.items() if c >= _n * 0.9]
                if _cand:
                    _mu = max(_cand, key=lambda x: x[1])[0]
                    logger.warning(
                        f"[capture] my_uid 无效({my_uid!r})，从 {_n} 个 conv_id "
                        f"自愈推断本账号 UID={_mu}（出现 {_cand[0][1]} 次）")
                    my_uid = _mu
        except Exception:
            pass
    top = _parse(raw)
    # 顶层 conversation 数组 = field 6 的响应体（cmd 2043 包裹）内的 repeated 元素。
    # 结构：top.field6(bytes) -> parse -> field(2043)(bytes) -> parse -> 多个 field(1, WT_LEN) 每个 = 一个 conversation。
    # 不要递归 _find_submessages 全部层（深层 message 内嵌 conv_id 会污染统计）。
    conv_objs = []
    for f, wt, v in top:
        if f == 6 and wt == WT_LEN and isinstance(v, bytes):
            lvl1 = _parse(v)
            if not lvl1:
                continue
            for f1, wt1, v1 in lvl1:
                if wt1 == WT_LEN and isinstance(v1, bytes):
                    sub = _parse(v1)
                    if sub:
                        # v1 本身可能就是 conversation 字节，或其内部 field(1) 元素才是
                        if CONV_RE.search(v1):
                            conv_objs.append(v1)
                        for f2, wt2, v2 in sub:
                            if wt2 == WT_LEN and isinstance(v2, bytes) and CONV_RE.search(v2):
                                if v2 != v1:
                                    conv_objs.append(v2)
    if not conv_objs:
        all_subs = _find_submessages(top)
        conv_objs = [sb for sf, sb in all_subs if CONV_RE.search(sb)]
    if not conv_objs:
        return _fallback_regex(raw, my_uid)

    # 全局统计 sec_uid 频率：自己的 sec_uid 在每个会话都出现（收发双方），
    # 频率远高于对端；部分会话还会粘连后续字节形成“超长污染串”（全局高频），
    # 也需排除。取“非自己且低频”的串作为对端 sec_uid。
    from collections import Counter
    _global_sec = Counter()
    for cb in conv_objs:
        for s in _extract_str(cb):
            # 精确匹配 sec_uid：MS4wLjABAAAA + 36~44 个 base64url 字符
            # 负向前瞻 (?![\w_\-]) 防止吞入后续粘连字节
            for m in re.finditer(r"MS4wLjABAAAA[\w_\-]{36,44}(?![\w_\-])", s):
                su = m.group()
                _global_sec[su] += 1
    _own_sec = _global_sec.most_common(1)[0][0] if _global_sec else None
    # 异常高频串（自己除外）：频率超过会话数 1/3 的视为污染/系统串，排除
    _anomaly = {s for s, n in _global_sec.items()
                if s != _own_sec and n > max(3, len(conv_objs) // 3)}

    result = []
    seen_cid = set()
    for cbuf in conv_objs:
        cid = None
        for s in _extract_str(cbuf):
            m = CONV_RE.search(s.encode("utf-8", "replace"))
            if m:
                cid = m.group().decode()
                break
        if not cid or cid in seen_cid:
            continue
        seen_cid.add(cid)
        parts = cid.split(":")
        peer_uid = None
        if len(parts) == 4:
            uid_a, uid_b = parts[2], parts[3]
            if uid_a == uid_b:
                continue
            peer_uid = uid_b if uid_a == my_uid else uid_a
            # 第二道防线（2026-09-07）：my_uid 失效时会恒取 uid_a，导致
            # peer_uid 变成自己 → 昵称被回填成本账号。此处显式拦截：
            # 若取出的 peer_uid 恰等于 my_uid（两侧都不是自己 -> 数据异常），
            # 说明 my_uid 不可信，peer_uid 置 None 让其降级为裸 UID，
            # 绝不用"自己"冒充对端。
            if my_uid and peer_uid == my_uid:
                peer_uid = None
        # sec_uid：取“非自己、非异常高频、长度合理”的（低频对端串）
        sec_uid = None
        for s in _extract_str(cbuf):
            for m in re.finditer(r"MS4wLjABAAAA[\w_\-]{36,44}(?![\w_\-])", s):
                su = m.group()
                if su != _own_sec and su not in _anomaly:
                    sec_uid = su
                    break
            if sec_uid:
                break
        # 消息：在该 conversation 子对象内部，递归找「自身含 conv_id 且含 text」的子对象
        cparsed = _parse(cbuf)
        subs2 = _find_submessages(cparsed)
        messages = []
        seen_msg = set()
        for sf2, sb2 in subs2:
            mcid = None
            for s in _extract_str(sb2):
                mm = CONV_RE.search(s.encode("utf-8", "replace"))
                if mm:
                    mcid = mm.group().decode()
                    break
            if mcid != cid:
                continue
            txt = _parse_message_text(sb2)
            if txt is None:
                continue
            key = (mcid, txt[0][:50])
            if key in seen_msg:
                continue
            seen_msg.add(key)
            # 方向：用消息自带的 sender UID（field 7）判断，不用 aweType。
            # 首包里 sender 为空时(系统/自动生成的欢迎语等),默认按"我发"处理
            # 逻辑:在对端会话里,对端发的消息 100% 带 sender UID;空 sender 极可能是
            # 本系统/自动回复产生,归 me 比归 them 更合理
            sender = _parse_message_sender(sb2)
            if not sender:
                role = "me"
            elif sender == str(my_uid):
                role = "me"
            else:
                role = "them"
            # 时间：优先 message 对象 field 10（create_time 毫秒，2026-09-06
            # 黑盒实证：首包每条 message 的 field10=毫秒时间戳，与 DB 历史
            # 时间完全吻合）；缺失时回退 content JSON 的 createdAt，再退入库时间。
            _msg_ts = _parse_message_create_time(sb2)
            if not _msg_ts:
                _msg_ts = (txt[2] / 1000.0) if txt[2] else 0
            messages.append({
                "role": role,
                "text": txt[0],
                "ts": _msg_ts if _msg_ts else time.time(),
                "msg_id": _parse_message_id(sb2),
            })
        # 会话属性（field 4）：含总消息数(field 2) / short_id(field 5)，
        # 用于长会话历史补全（cmd 301）。cbuf 是原始 bytes，须从 cparsed 取。
        short_id = None
        total_msgs = None
        for _f, _wt, _v in cparsed:
            if _f == 4 and _wt == WT_LEN and isinstance(_v, bytes):
                for _f2, _wt2, _v2 in _parse(_v):
                    if _wt2 not in (WT_VARINT, WT_64BIT):
                        continue
                    if _f2 == 5:
                        short_id = _v2
                    elif _f2 == 2:
                        total_msgs = _v2
                break

        result.append({
            "conversation_id": cid,
            "peer_uid": peer_uid,
            "sec_uid": sec_uid,
            "messages": messages,
            "short_id": short_id,
            "total_msgs": total_msgs,
        })
    logger.info(f"[capture] 首包解析出 {len(result)} 个会话，"
                f"含消息的 {sum(1 for r in result if r['messages'])} 个")
    return result


# ---------------------------------------------------------------------------
# 长会话历史补全（cmd 301 get_by_conversation）
# ---------------------------------------------------------------------------
# 首包(2043) 固定只返回每个会话「最后 ~20 条」消息，长会话历史必须靠
# get_by_conversation(cmd 301) 补齐。
# 实测（2026-08-29）：「转角遇到」会话总 41 条，首包只给 20 条，301 一次补齐 41 条。
#
# 请求构造：复用项目 ProtoBuilder.build_normal_request(auth, 301)，
# 手工追加 field 301 的 body（项目 .proto 未定义 cmd 301 的 oneof）。
# body 字段（实测）：
#   1 = conversation_id (string)
#   2 = conversation_type (int) = 1
#   3 = conversation_short_id (int64)  ← 从首包 .4.5 取得
#   4 = direction (int) = 1
#   5 = cursor (int64) = 0
#   6 = count (int) = 50
#
# 风控边界：这是「聊天记录」接口，不是昵称接口；单次请求（非批量），
# 昵称仍且仅由 BCC 被动截获 im/user/info 提供，本函数不查昵称。
# ---------------------------------------------------------------------------
def _pb_varint(n):
    """protobuf varint 编码"""
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            break
    return bytes(out)


def _build_301_body(cid, short_id, cursor=0, count=50, direction=1):
    """构造 cmd 301 的 inner body（字段见上方注释）"""
    inner = bytearray()
    cid_b = cid.encode()
    inner += b"\x0a" + _pb_varint(len(cid_b)) + cid_b   # 1: conversation_id
    inner += b"\x10" + _pb_varint(1)                     # 2: type
    inner += b"\x18" + _pb_varint(int(short_id))         # 3: short_id
    inner += b"\x20" + _pb_varint(direction)             # 4: direction
    inner += b"\x28" + _pb_varint(int(cursor))           # 5: cursor
    inner += b"\x30" + _pb_varint(count)                 # 6: count
    return bytes(inner)


def fetch_conversation_history(auth, cid, short_id, count=50, timeout=20):
    """拉指定会话的完整历史消息（cmd 301）。

    返回 [{role,text,ts,msg_id}] 或 []（失败时）。
    """
    try:
        import requests
        from builder.proto import ProtoBuilder
        from builder.header import HeaderBuilder, HeaderType
        from dy_apis.douyin_api import DouyinAPI

        my_uid = str(auth.get_uid()).strip()
        request = ProtoBuilder.build_normal_request(auth, 301)
        body_bytes = request.SerializeToString()
        inner = _build_301_body(cid, short_id, cursor=0, count=count)
        tag = _pb_varint((301 << 3) | 2)          # field 301, wiretype 2
        payload = tag + _pb_varint(len(inner)) + inner
        # 追加到 Request 的 field 8 (body): tag = (8<<3)|2 = 66 = 0x42
        body_bytes = body_bytes + b"\x42" + _pb_varint(len(payload)) + payload

        url = "https://imapi.douyin.com/v1/message/get_by_conversation"
        headers = HeaderBuilder().build(HeaderType.PROTOBUF)
        headers.set_header("referer", "https://www.douyin.com/")
        resp = requests.post(
            url, headers=headers.get(), cookies=auth.cookie,
            data=body_bytes, verify=False, timeout=timeout,
        )
        if resp.status_code != 200 or len(resp.content) < 100:
            logger.warning(f"[capture][301] HTTP {resp.status_code} "
                           f"len={len(resp.content)} cid={cid}")
            return []
        return parse_conversation_301(resp.content, cid, my_uid)
    except Exception as e:
        logger.warning(f"[capture][301] 拉取失败 cid={cid}: {e}")
        return []


def parse_conversation_301(raw, cid, my_uid):
    """解析 get_by_conversation(cmd 301) 响应 -> [{role,text,ts,msg_id,skey,origin_url}]

    结构：parsed['6']['301']['1'] = 消息数组
      每条: 1=conversation_id 3=msg_id 7=sender 8=content 10=create_time

    2026-09-01 新增 skey/origin_url（08 §三十五 实机证真）：
      图片消息的 content_json.resource_url.skey 是 AES-256-GCM 密钥，
      此前被丢弃导致原图无法解密。现在一并返回，由调用方落库到 extra。

    2026-09-04 实测纠错：
      曾误以为 cmd 301 field 7 语义与首包相反(方向全反)，实机验证推翻了该假设 ——
      反转后「能看我病历吗 老师」变成 me(实为对方发)完全错误。
      实锤：cmd 301 的 field 7 与首包(2043)一致 = 发送方 sender_uid，
      sender == my_uid → me 的原始逻辑正确，不得反转。
    """
    out = []
    try:
        import blackboxprotobuf
        parsed, _ = blackboxprotobuf.decode_message(raw)
        inner = parsed["6"]["301"]
        msgs = inner.get("1", [])
        if isinstance(msgs, dict):
            msgs = [msgs]
    except Exception as e:
        logger.warning(f"[capture][301] 解析失败: {e}")
        return out

    for m in msgs:
        if not isinstance(m, dict):
            continue
        # content -> text
        text = None
        skey = None
        origin_url = None
        raw_content = m.get("8", b"")
        s = raw_content
        if isinstance(s, bytes):
            try:
                s = s.decode("utf-8", "ignore")
            except Exception:
                s = ""
        s = str(s)
        try:
            import json as _json
            obj = _json.loads(s)
            if isinstance(obj, dict):
                text = obj.get("text") or obj.get("tips")
                # 2026-08-29 修复：图片/表情/语音等富媒体消息没有 text 字段，
                # 只有 resource_url / url，原逻辑会 continue 丢弃整条消息，
                # 导致前端只能看到「图片」二字或完全看不到该条。
                # 现按类型提取媒体 URL，统一存成 "[图片] <url>" 形态，
                # 前端据此直接渲染缩略图预览。
                if not text:
                    text = _extract_media_text(obj)
                # 2026-09-01：图片解密要素（AES-256-GCM），此前恒被丢弃
                skey, origin_url = _extract_image_secret(obj)
        except Exception:
            text = s if s else None
        if not text:
            continue
        ts = m.get("10", 0) or 0
        sender = str(m.get("7", "")).strip()
        # 方向：sender 为空时默认 me(自动欢迎语等),sender == my_uid → me,否则 them
        if not sender:
            role = "me"
        elif sender == str(my_uid):
            role = "me"
        else:
            role = "them"
        out.append({
            "role": role,
            "text": text,
            "ts": (int(ts) / 1000.0) if ts else time.time(),
            "msg_id": str(m.get("3")) if m.get("3") else None,
            "skey": skey,
            "origin_url": origin_url,
        })
    return out


def _split_repeated(buf):
    """把一个 repeated message 字段的 bytes 拆成每个子 message 的 bytes 列表。
    遍历顶层 tag，遇到 WT_LEN 且内部可解析为 message 的，切分出来。"""
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
        if wt == WT_LEN:
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
            out.append(buf[i:i + ln])
            i += ln
        elif wt == WT_VARINT:
            while i < n and buf[i] & 0x80:
                i += 1
            if i < n:
                i += 1
        elif wt == WT_64BIT:
            i += 8
        elif wt == WT_32BIT:
            i += 4
        else:
            break
    return out


def _fallback_regex(raw, my_uid):
    """退化正则法（仅在 field 6 未命中时用），对齐旧 parse_init_conversations 行为。"""
    decoded = raw.decode("utf-8", errors="replace")
    seen = set()
    result = []
    for m in re.finditer(r"0:1:\d{5,20}:\d{5,20}", decoded):
        cid = m.group()
        if cid in seen:
            continue
        seen.add(cid)
        parts = cid.split(":")
        uid_a, uid_b = parts[2], parts[3]
        if uid_a == uid_b:
            continue
        peer_uid = uid_b if uid_a == my_uid else uid_a
        # 同主解析路径的第二道防线：peer_uid 等于自己说明 my_uid 不可信，
        # 降级为 None（裸 UID），绝不用"自己"冒充对端。
        if my_uid and peer_uid == my_uid:
            peer_uid = None
        result.append({
            "conversation_id": cid,
            "peer_uid": peer_uid,
            "sec_uid": None,
            "messages": [],
        })
    return result


# ---------------------------------------------------------------------------
# 浏览器捕获 im/user/info 响应（无头指纹浏览器 + hook 截接口）
# ---------------------------------------------------------------------------
HOOK_JS = r"""
() => {
  window.__CAP_USERINFO__ = {map: {}};
  const T = window.__CAP_USERINFO__;
  const origFetch = window.fetch;
  window.fetch = function(...args){
    const url = (args[0] && (typeof args[0]==='string'?args[0]:args[0].url)) || '';
    const rec = {url: url.slice(0,200)};
    T._last = rec;
    return origFetch.apply(this, args).then(async (resp) => {
      try {
        if(/im\/user\/info/.test(url)){
          const txt = await resp.clone().text();
          try {
            const obj = JSON.parse(txt);
            for(const u of (obj.data||[])){
              const su = u.sec_uid || (u.sec_user_id);
              if(su){
                T.map[su] = {
                  nickname: u.nickname || '',
                  avatar: (u.avatar_thumb && u.avatar_thumb.url_list && u.avatar_thumb.url_list[0])
                           || (u.avatar_small && u.avatar_small.url_list && u.avatar_small.url_list[0]) || ''
                };
              }
            }
          } catch(e){}
        }
      } catch(e){}
      return resp;
    });
  };
  const origXHR = window.XMLHttpRequest;
  window.XMLHttpRequest = function(){
    const x = new origXHR();
    const o = x.open; x.open = function(m,u,...r){ x.__u=u; x.__m=m; return o.call(x,m,u,...r); };
    const ob = x.send; x.send = function(d){ return ob.call(x,d); };
    x.addEventListener('load', function(){
      try {
        if(x.__u && /im\/user\/info/.test(x.__u)){
          const obj = JSON.parse(x.responseText||'{}');
          for(const u of (obj.data||[])){
            const su = u.sec_uid || u.sec_user_id;
            if(su){ window.__CAP_USERINFO__.map[su] = {nickname:u.nickname||'', avatar:(u.avatar_thumb&&u.avatar_thumb.url_list&&u.avatar_thumb.url_list[0])||''}; }
          }
        }
      } catch(e){}
    });
    return x;
  };
  return 'captured';
}
"""

def capture_userinfo_via_browser(name, wait=15, max_age=None):
    """经 BCC 被动 hook 截前端自发 im/user/info 响应（08 文档验证 44/44）。

    BCC 启动后自动导航到 chat 页 + 平滑滚动触发全部 im/user/info。

    2026-08-31 实测（scripts/probe_bcc_speed.py，222 个会话的真机账号）：
      wait=15 -> 耗时 23.4s，截获 **278** 个昵称
      wait=30 -> 耗时 38.4s，截获 278 个（与 15 相同，多等无收益）
      wait=90 -> 90s 等待 + 滚动点击，必然超过调用方 180s 超时
    **此前 wait=90 是超时根因**：昵称全部降级为 UID（222/222 全是
    peer_name=peer_id）。改为 15 后单次调用约 23s。

    timeout 提到 240s：BCC 浏览器**冷启动**首次调用实测 152.7s
    （含页面导航与首屏渲染），180s 会在冷启动场景超时。

    2026-09-01 新增**结果缓存**（08 §三十七，修复 220s 慢）：
      BCC 启动后 _prewarm 线程已在后台跑过一次完整捕获（日志
      「昵称缓存预热完成：277 个」）。但 capture_all 每次都重新调 BCC
      再跑一遍 176s 的滚动 —— 预热白做了。
      这里按账号缓存最近一次成功结果，默认 10 分钟内直接复用，
      让「更新会话」在预热已完成的常见情况下几乎零等待。
      可用 DY_USERINFO_CACHE_SEC 调整（0 = 关闭缓存）。
    """
    import time as _t

    # ── 缓存命中检查 ──
    try:
        _ttl = (int(os.environ.get("DY_USERINFO_CACHE_SEC", "600"))
                if max_age is None else int(max_age))
    except Exception:
        _ttl = 600
    if _ttl > 0:
        _hit = _userinfo_cache.get(name)
        if _hit and (_t.time() - _hit[0]) < _ttl and _hit[1]:
            logger.info(
                f"[capture] 复用昵称缓存（{len(_hit[1])} 个，"
                f"{_t.time() - _hit[0]:.0f}s 前采集），跳过 BCC 滚动")
            return _hit[1]

    import requests
    from auto_dm import accounts as acc
    try:
        bport = acc.browser_daemon_port(name)
    except Exception:
        return {}
    url = f"http://127.0.0.1:{bport}/capture_userinfo"
    try:
        # wait: BCC 在 chat 页平滑滚动触发 im/user/info 的秒数。
        # 实测 15s 与 30s 截获量相同（278 个），取 15s。
        r = requests.post(url, json={"wait": 15}, timeout=240)
        if r.status_code == 200:
            data = r.json().get("data") or {}
            logger.info(f"[capture] 经 BCC 截到昵称数: {len(data)}")
            if data:
                _userinfo_cache[name] = (_t.time(), data)
            return data
    except Exception as e:
        logger.warning(f"[capture] 调 BCC /capture_userinfo 失败: {e}")
    return {}


# 昵称缓存：{账号: (采集时间戳, {sec_uid: {...}})}
# 配合 BCC 的 _prewarm，避免每次「更新会话」都重跑 176s 的滚动捕获。
_userinfo_cache: dict[str, tuple] = {}


# 模块级缓存：首包解析结果（供 capture_userinfo_via_browser 取 peer_uid）
_last_parsed_convs: dict[str, list] = {}


# ---------------------------------------------------------------------------
# 组合：首包解析 + 浏览器昵称 + 写库
# ---------------------------------------------------------------------------
def capture_all(name, with_browser=True):
    """前移捕获：首包解析会话+消息，浏览器截昵称头像，写 dm_conversations + dm_messages。
    返回 (n_conv, n_msg) 写库数量。
    """
    from auto_dm import accounts as acc
    from dy_apis.login_api import DYLoginApi
    from dy_apis.douyin_api import DouyinAPI
    from database import get_db

    env_path = acc.env_path_of(name)
    try:
        auth = DYLoginApi._load_auth_from_env(env_path)
        # 2026-09-06 全局治理（BCC 快闪根治）：
        # refresh_cookie_from_profile 在 BCC 不在线时会走"后备路径"
        # 直开 Playwright chromium（login_api.py:562 launch_sync）——
        # with_browser=False 的语义是"启动补捕获不碰浏览器"，但原来这行
        # 无条件执行，导致 recv_daemon startup 每账号反复拉浏览器（快闪）。
        # 修复：with_browser=False 时跳过 profile 刷新，直接用 .env 凭证。
        # 首包 get_message_by_init 是 HTTP API，不依赖浏览器 cookie 新鲜度；
        # BCC 在线时仍走 /cookie 刷新（不抢锁、不开新浏览器）。
        if with_browser:
            # 用户显式动作（更新会话按钮）→ 允许 BCC 不在线时兜底开浏览器
            DYLoginApi.refresh_cookie_from_profile(auth, env_path, allow_launch=True)
        else:
            # BCC 在线时仍走 /cookie 刷新（不抢锁、不开新浏览器）；
            # BCC 不在线就绝不为启动补捕获开浏览器。
            try:
                from dy_apis.login_api import _bcc_alive as _alive
                if _alive(name):
                    DYLoginApi.refresh_cookie_from_profile(auth, env_path)
            except Exception:
                pass
        my_uid = str(auth.get_uid())
    except Exception as e:
        logger.warning(f"[capture][{name}] 加载凭证失败: {e}")
        return (0, 0)

    # 1) 首包
    import time as _tm

    _t_start = _tm.time()
    try:
        raw = DouyinAPI.get_message_by_init(auth)
        _t_raw = _tm.time()
        convs = parse_init_protobuf(raw, my_uid)
        _t_parse = _tm.time()
        logger.info(
            f"[capture][{name}] 首包 拉取={_t_raw - _t_start:.1f}s "
            f"解析={_t_parse - _t_raw:.1f}s "
            f"（{len(raw):,}B -> {len(convs)} 会话）")
    except Exception as e:
        logger.warning(f"[capture][{name}] 首包解析失败: {e}")
        return (0, 0)
    if not convs:
        return (0, 0)
    # 缓存首包解析结果（供 capture_userinfo_via_browser 取 peer_uid 桥接）
    _last_parsed_convs[name] = convs

    # 1.5) 长会话历史补全（cmd 301）
    # 首包固定只给每会话最后 ~20 条；total_msgs > 已解析条数 时用 301 补齐。
    #
    # 风控保护（重要）：补全要对每个会话各发 1 次请求，频次必须受限：
    #   - DY_HISTORY_FULL=0 可整体关闭（默认 1 开启）
    #   - DY_HISTORY_MAX   单次最多补全会话数（默认 20）
    #   - DY_HISTORY_SLEEP 每次请求间隔秒（默认 1.5s）
    # 且这是「聊天记录」接口，不是昵称接口；昵称仍且仅由 BCC 被动截获提供。
    try:
        import os as _os
        import time as _time
        if _os.environ.get("DY_HISTORY_FULL", "1") == "1":
            # 2026-09-01 优化：跳过已掌握会话。
            #
            # 之前用首包 total_msgs 字段，实测**不可靠**（首包 field 4.2
            # 与消息内嵌结构混淆，会把 total_msgs 取成 6 而非真实 50）。
            # 现用「库内条数」+「首包解析条数」综合判断：
            #   - 已掌握 = 库内条数 ≥ max(首包解析条数, 1)
            #   - 首包给约 20 条消息；库内补全后 1 页 = 50 条
            #   - 长会话（>50）库内未到首包数 = 显然未补全
            #   - 短会话（1~20）库内=首包 = 已掌握
            # 不再用 total_msgs 字段，彻底绕开解析 bug。
            _db_counts = {}
            try:
                from database import get_db as _getdb
                _c2 = _getdb()
                for _r in _c2.execute(
                    "SELECT conv_id, COUNT(*) n FROM dm_messages "
                    "WHERE account=? GROUP BY conv_id", (name,)
                ):
                    _db_counts[str(_r["conv_id"])] = int(_r["n"] or 0)
            except Exception:
                _db_counts = {}

            _FORCE = _os.environ.get("DY_HISTORY_FORCE", "0") == "1"

            def _have(_c):
                """该会话已掌握消息条数：取 max(库内, 首包解析)"""
                return max(len(_c.get("messages", [])),
                           _db_counts.get(str(_c.get("conversation_id")), 0))

            if _FORCE:
                need = [c for c in convs if c.get("short_id")]
            else:
                # DY_HISTORY_SKIP_PAGED=1 才启用「跳过已补全会话」逻辑。
                # **默认关闭**（= 0）—— 维持旧行为「20 个会话全补」，
                # 避免在没测透前对真实账号造成新消息漏补。
                # 启用条件：
                #   - 长会话（>50 条）：库内 0 补；库内 50（已 1 页）跳
                #   - 短会话（<=20）：库内 0 补；库内 =首包 跳
                if _os.environ.get("DY_HISTORY_SKIP_PAGED", "0") == "1":
                    need = [c for c in convs
                            if c.get("short_id")
                            and _db_counts.get(str(c.get("conversation_id")), 0)
                                < max(len(c.get("messages", [])), 1)]
                else:
                    need = [c for c in convs if c.get("short_id")]
            max_n = int(_os.environ.get("DY_HISTORY_MAX", "45"))
            sleep_s = float(_os.environ.get("DY_HISTORY_SLEEP", "1.5"))
            # 2026-08-31：串行补全实测太慢（20 个会话 × (请求~10s + 间隔1.5s)
            # ≈ 230s，加上首包和浏览器启动总计 365s，用户明确抱怨）。
            # 改为**可配置并发**：DY_HISTORY_WORKERS（默认 4）。
            # 保守取值的原因：本项目曾因短时高频调用抖音接口触发风控
            # （首包返回 50 字节空响应、cookie 失效），故不做激进并发。
            # 想更快可调到 6~8，但需承担风控风险。
            workers = max(1, min(8, int(_os.environ.get("DY_HISTORY_WORKERS", "4"))))
            need = need[:max_n]
            if not need:
                # 全部会话的历史都已掌握（库内条数 >= total_msgs），
                # 无需发任何 301 请求 —— 这是重复点「更新会话」的常态。
                logger.info(
                    f"[capture][{name}] 长会话补全：{len(convs)} 个会话历史均完整"
                    f"（库内已有 >= total_msgs），跳过 301 请求")
            if need:
                logger.info(f"[capture][{name}] 长会话补全：{len(need)} 个会话"
                            f"（并发 {workers}，间隔 {sleep_s}s）")
                from concurrent.futures import ThreadPoolExecutor

                def _fill(_c):
                    try:
                        _h = fetch_conversation_history(
                            auth, _c["conversation_id"], _c["short_id"],
                            count=max(50, int(_c.get("total_msgs") or 50)),
                        )
                        if _h:
                            _c["messages"] = _h
                            return (_c.get("peer_uid"), len(_h), None)
                        return (_c.get("peer_uid"), 0, "空响应")
                    except Exception as _e:  # noqa: BLE001
                        return (_c.get("peer_uid"), 0, str(_e)[:60])

                # 按批次错峰：每批 workers 个并发，批间等 sleep_s。
                # 这样并发真正生效（不像逐个 sleep 那样退化成串行）。
                with ThreadPoolExecutor(max_workers=workers) as ex:
                    for b in range(0, len(need), workers):
                        if b:
                            _time.sleep(sleep_s)
                        batch = need[b:b + workers]
                        for peer_uid, n, err in ex.map(_fill, batch):
                            if err:
                                logger.warning(
                                    f"[capture][{name}] 301 补全失败 {peer_uid}:"
                                    f" {err}")
                            else:
                                logger.info(
                                    f"[capture][{name}] 301 补全 {peer_uid}: {n} 条")
    except Exception as e:
        logger.warning(f"[capture][{name}] 长会话补全失败（降级仅首包）: {e}")

    # 2) 浏览器昵称（可选，单 profile 独占）
    userinfo = {}
    if with_browser:
        try:
            userinfo = capture_userinfo_via_browser(name)
        except Exception as e:
            logger.warning(f"[capture][{name}] 浏览器昵称捕获失败（降级仅首包）: {e}")

    # 3) 写库
    n_conv = 0
    n_msg = 0
    _t_write0 = _tm.time()   # 写库开始（配合 _t_start 统计各阶段耗时）
    conn = get_db()
    try:
        # 关联键修正（对应工作记忆/08 §十~§十一）：
        # 首包 conv_id 形如 0:1:uid:uid，排除自身即 peer_uid（100% 可靠）；
        # im/user/info 同时返回该用户 uid → 二者数字对应，为可靠桥接。
        # 首包 sec_uid 覆盖率仅 27%、与 im/user/info 重叠仅 5%，仅作 fallback。
        _userinfo_by_uid = {}
        for _su, _v in userinfo.items():
            _u = _v.get("uid")
            if _u:
                _userinfo_by_uid[str(_u)] = _v
        _by_uid = 0
        _by_sec = 0
        for c in convs:
            cid = c["conversation_id"]
            peer_uid = c["peer_uid"]
            sec_uid = c.get("sec_uid")
            info = _userinfo_by_uid.get(str(peer_uid)) if peer_uid else None
            if info:
                _by_uid += 1
            else:
                info = userinfo.get(sec_uid) or {} if sec_uid else {}
                if info:
                    _by_sec += 1
            nickname = info.get("nickname") or ""
            avatar = info.get("avatar") or ""
            # 2026-09-07 存量修复（D 方案）：历史污染会话 peer_id 被写成
            # my_uid、peer_name 被写成"本账号昵称"。此处用 conv_id 重新解析
            # 真实对端 UID 并修正；若解析失败（peer_uid=None）则把错误的
            # 本账号昵称降级为裸 UID，让下次 BCC 截获到真实昵称时再回填。
            _cid_parts = cid.split(":")
            if len(_cid_parts) == 4:
                _a, _b = _cid_parts[2], _cid_parts[3]
                _real = _b if _a == str(_myuid) else (_a if _b == str(_myuid) else None)
                if _real and str(peer_uid) != _real:
                    peer_uid = _real
                    # peer 变了，之前按错误 peer_uid 查到的昵称不可信
                    _info2 = _userinfo_by_uid.get(str(peer_uid))
                    nickname = (_info2 or {}).get("nickname") or ""
                    avatar = (_info2 or {}).get("avatar") or ""
            if not peer_uid:
                # 无法判定对端：绝不写"自己"，昵称降级为对端 UID 占位
                nickname = ""
            # upsert 会话骨架
            conn.execute(
                "INSERT OR IGNORE INTO dm_conversations("
                "account,conv_id,peer_id,peer_name,short_id,last_ts,unread,avatar) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (name, cid, peer_uid, nickname or peer_uid, None, 0, 0, avatar or None),
            )
            # 存量污染订正：已存在但 peer_id 是自己的记录 → 改回真实对端 UID
            if peer_uid:
                conn.execute(
                    "UPDATE dm_conversations SET peer_id=? "
                    "WHERE account=? AND conv_id=? AND peer_id<>?",
                    (peer_uid, name, cid, peer_uid),
                )
            if nickname or avatar:
                # capture_all 是昵称/头像的权威写入方（08 方案经 BCC 截获）。
                # 无条件覆盖：recv_daemon 可能已先写入数字 UID/自身 UID 占位，
                # 此处用真实昵称替换，避免展示层看到数字 UID。
                conn.execute(
                    "UPDATE dm_conversations SET peer_name=?,avatar=? "
                    "WHERE account=? AND conv_id=?",
                    (nickname or peer_uid, avatar or None, name, cid),
                )
            # 写消息
            for m in c["messages"]:
                try:
                    # 2026-09-01：图片消息的解密要素写入 extra（08 §三十五）。
                    # 此前恒写 "{}"，skey 被丢弃 → 原图无法解密。
                    # extra = {"skey":..., "origin_url":...}；非图片消息仍为 {}。
                    _extra = "{}"
                    _sk = m.get("skey")
                    _ou = m.get("origin_url")
                    if _sk and _ou:
                        try:
                            import json as _json
                            _extra = _json.dumps(
                                {"skey": _sk, "origin_url": _ou}, ensure_ascii=False)
                        except Exception:
                            _extra = "{}"
                    if _extra != "{}":
                        # 带 skey 的图片消息：先尝试 UPDATE 补写，无影响行再 INSERT。
                        #
                        # 为何不用 INSERT OR IGNORE / ON CONFLICT：
                        #  - 历史图片行大多已存在（extra 是旧的 "{}"），INSERT OR IGNORE
                        #    会被唯一索引静默跳过 → 旧行永远拿不到 skey；
                        #  - uniq_dmmsg 是**部分索引**（WHERE msg_id IS NOT NULL），
                        #    ON CONFLICT(account,conv_id,msg_id) 匹配不上，报
                        #    "does not match any PRIMARY KEY or UNIQUE constraint"。
                        # 故用「UPDATE 优先 + 幂等」写法，且只补写尚未有 skey 的行
                        # （extra NOT LIKE '%skey%'），重复跑不会覆盖已有正确值。
                        _cur = conn.execute(
                            "UPDATE dm_messages SET extra=? "
                            "WHERE account=? AND conv_id=? AND msg_id=? "
                            "  AND (extra IS NULL OR extra='' "
                            "       OR extra NOT LIKE '%skey%')",
                            (_extra, name, cid, m.get("msg_id")),
                        )
                        if (_cur.rowcount or 0) == 0:
                            conn.execute(
                                "INSERT OR IGNORE INTO dm_messages("
                                "account,conv_id,role,text,msg_type,extra,ts,msg_id) "
                                "VALUES(?,?,?,?,?,?,?,?)",
                                (name, cid, m["role"], m["text"], "text", _extra,
                                 m["ts"], m.get("msg_id")),
                            )
                    else:
                        conn.execute(
                            "INSERT OR IGNORE INTO dm_messages("
                            "account,conv_id,role,text,msg_type,extra,ts,msg_id) "
                            "VALUES(?,?,?,?,?,?,?,?)",
                            (name, cid, m["role"], m["text"], "text", _extra,
                             m["ts"], m.get("msg_id")),
                        )
                    n_msg += 1
                except Exception:
                    pass
            n_conv += 1
        conn.commit()
        # 2026-09-07 存量污染一次性订正（D 方案）：
        # 历史库里 peer_id 被写成 my_uid、peer_name 被写成"本账号昵称"的会话
        # （实测尚进工伤小助理 32/77 条）。上面循环只订正本次首包命中的会话，
        # 这里扫全表把漏网的也修好：用 conv_id 解析真实对端 → 改写 peer_id，
        # 并把错误的本账号昵称降级为裸 UID（下次 BCC 截到真实昵称会回填）。
        try:
            _myuid_fix = str(auth.get_uid()) if "auth" in dir() else ""
            _dirty = conn.execute(
                "SELECT conv_id, peer_id, peer_name FROM dm_conversations "
                "WHERE account=? AND peer_id=?",
                (name, _myuid_fix),
            ).fetchall()
            _fixed = 0
            for _row in _dirty:
                _cid = _row["conv_id"]
                _p = _cid.split(":")
                if len(_p) != 4:
                    continue
                _ua, _ub = _p[2], _p[3]
                _real = _ub if _ua == str(_myuid_fix) else (
                    _ua if _ub == str(_myuid_fix) else None)
                if not _real:
                    continue
                _newname = (_userinfo_by_uid.get(_real) or {}).get("nickname") or _real
                conn.execute(
                    "UPDATE dm_conversations SET peer_id=?, peer_name=? "
                    "WHERE account=? AND conv_id=?",
                    (_real, _newname, name, _cid),
                )
                _fixed += 1
            if _fixed:
                conn.commit()
                logger.info(
                    f"[capture][{name}] 存量订正 {_fixed} 条 "
                    f"peer_id=自己的污染会话（peer_id 已改回真实对端）")
        except Exception as e:
            logger.warning(f"[capture][{name}] 存量污染订正失败: {e}")
        # 兜底：recv_daemon 可能已写入 capture_all 首包未解析到的会话（WS 增量等，
        # 其 peer_id 是对端 UID 但 peer_name 仍是占位/自己）。用已截获的 BCC 昵称补全库内
        # 所有「peer_name 为空/等于 peer_id（数字 UID）/等于自己 UID」的会话，
        # 保证展示层无裸 UID、也不出现「自己」的冗余显示。
        try:
            _myuid = str(auth.get_uid()) if "auth" in dir() else ""
            _backfill = conn.execute(
                "SELECT conv_id, peer_id, peer_name FROM dm_conversations "
                "WHERE account=? AND (peer_name IS NULL OR peer_name='' "
                "OR peer_name=peer_id OR peer_name=?)",
                (name, _myuid),
            ).fetchall()
            _bf = 0
            for _row in _backfill:
                _cid, _pid, _existing = _row["conv_id"], _row["peer_id"], _row["peer_name"]
                _info = _userinfo_by_uid.get(str(_pid)) if _pid else None
                if _info:
                    # BCC 截到该 peer_id 的昵称 → 补全
                    conn.execute(
                        "UPDATE dm_conversations SET peer_name=?, avatar=? "
                        "WHERE account=? AND conv_id=?",
                        (_info.get("nickname") or _pid, _info.get("avatar") or None,
                         name, _cid),
                    )
                    _bf += 1
                elif _existing == _myuid and _pid:
                    # peer_name 是自己（污染值）→ 降级为对端 UID，避免展示「自己」
                    conn.execute(
                        "UPDATE dm_conversations SET peer_name=? "
                        "WHERE account=? AND conv_id=?",
                        (_pid, name, _cid),
                    )
                    _bf += 1
            if _bf:
                conn.commit()
                logger.info(f"[capture][{name}] 兜底补全 {_bf} 个库内会话昵称/降级")
        except Exception as e:
            logger.warning(f"[capture][{name}] 兜底补全失败: {e}")
    except Exception as e:
        logger.warning(f"[capture][{name}] 写库失败: {e}")
        traceback.print_exc()
    logger.info(f"[capture][{name}] 写库完成：会话 {n_conv}（含消息 {n_msg}），"
                f"昵称命中 uid关联={_by_uid} sec_uid关联={_by_sec} "
                f"未命中={len(convs) - _by_uid - _by_sec}/{len(convs)}")
    return (n_conv, n_msg)
