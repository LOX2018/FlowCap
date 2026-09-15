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
from .im_protobuf import (
    WT_VARINT, WT_64BIT, WT_LEN, WT_32BIT, CONV_RE,
    _parse, _find_submessages, _extract_str, _strip_pb_prefix,
    _parse_message_sender, _parse_message_create_time, _parse_message_id,
    _extract_long_str, _parse_message_text,
)

def _cfg(section: str, key: str):
    """读统一配置中心（services.app_config）；不可用返回 None。

    2026-09-08 接线：替换散落的 os.environ.get 读取点，
    未命中（返回 None）时由调用方的 `or <默认值>` 兜底 —— 行为与接线前一致。
    """
    try:
        from services.app_config import get

        return get(section, key)
    except Exception:
        return None


def _cfg_bool(section: str, key: str, env: str = "",
              default: bool = False) -> bool:
    """读布尔配置（2026-09-15 修复「类型契约破裂」）。

    契约：schema 里 `"type": "bool"` 的字段，`app_config.get()` 返回
    **Python bool**（见 app_config_schema.py / app_config._coerce）。

    旧写法 `str(_cfg(...)) == "1"` 对 bool 恒为 False
    （`str(True) == 'True' != '1'`）→ 「长会话历史补全(cmd301)」整块被静默跳过，
    库里永远只有首包自带的每会话 ~20 条。本函数统一处理 bool / int / str 三类
    来源；配置中心不可用时回落环境变量；都没有则用 default。
    """
    v = _cfg(section, key)
    if v is not None:
        if isinstance(v, bool):
            return v
        return str(v).strip().lower() in ("1", "true", "yes", "on")
    ev = os.environ.get(env) if env else None
    if ev not in (None, ""):
        return ev.strip().lower() in ("1", "true", "yes", "on")
    return default


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
        return int(_cfg("capture", "image_inline_max_kb") or 32)
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


# 2026-09-15 修复（对照主分支 v2-refactor 确证）：
#   协议工具层 im_protobuf 被单独抽出后，_parse_message_text 里仍引用
#   **本模块**的 _extract_media_text —— 跨模块引用未接线 → 富媒体消息恒被丢弃。
#   正解：工具层不反向依赖业务层，由业务层**注入**提取器（依赖倒置）。
#   这一步必须发生在任何解析调用之前（模块导入即完成）。
try:
    from .im_protobuf import set_media_text_extractor as _set_media_ex
    _set_media_ex(_extract_media_text)
except Exception:  # noqa: BLE001  —— 注入失败不得阻断导入；报错见 PB-001
    try:
        from loguru import logger as _lg
        import traceback as _tb
        _lg.warning("PB-002", f"[capture] 注入富媒体提取器失败: {_tb.format_exc()[:300]}")
    except Exception:
        pass


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
            # 本账号 UID 必然出现在【每一个】conv_id 中（自己与所有人聊天）
            _cand = [(u, c) for u, c in _cnt.items() if c >= _n * 0.9]
            _common = max(_cand, key=lambda x: x[1])[0] if _cand else None
            if _common:
                if _mu in ("", "None", "none", "0"):
                    # 原场景：my_uid 失效（None/空）→ 直接自愈
                    logger.warning("CAP-003",
                        f"[capture] my_uid 无效({my_uid!r})，从 {_n} 个 conv_id "
                        f"自愈推断本账号 UID={_common}（出现 {_cnt[_common]} 次）")
                    my_uid = _common
                elif _mu != _common:
                    # 2026-09-13 新增场景（用户实测：更新会话后 66 个会话的
                    # peer_id 全是本号）：my_uid 【有值但不对】——抖音 web 侧
                    # query/user 的 user_uid 与 imapi 会话体系绑定的 uid 并非
                    # 永远同一个值（技能 §9.8 已记录该轮换现象，实测出现
                    # 2609… 与 316… 两个值）。conv_id 里的本号段才是与私信体系
                    # 一致的身份，必须以此为准，否则
                    #   peer_uid = uid_b if uid_a == my_uid else uid_a
                    # 因 uid_a(本号) != my_uid(错值) 恒取 uid_a → 对端全变成自己。
                    logger.warning("CAP-003",
                        f"[capture] my_uid({_mu}) 与 {_n} 个 conv_id 的共同项"
                        f"({_common}，出现 {_cnt[_common]} 次) 不一致 —— "
                        f"以 conv_id 为准（web user_uid 与 imapi 会话 uid 可能轮换）")
                    my_uid = _common
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

    # 2026-09-15 修复（实测定位）：
    # 同一 conv_id 可能同时命中两个不同层级的对象 ——
    #   ① 「容器包装对象」：repeated 消息集合（field1 有 N 个），**无 field4**；
    #   ② 「会话元数据对象」：带 field4（short_id=field4.5 / total_msgs=field4.2）。
    # 原实现按出现顺序用 seen_cid 去重 → 容器对象排在前面时先占位，
    # 该会话落库 short_id=None → 被长会话补全 `if c.get("short_id")` 永久跳过。
    # 实测（2026-09-15）：承载**全部图片消息**的那个会话正是因此补不出历史。
    # 修复：按 cid 归并，**优先保留带 field4 的元数据对象**。
    def _has_short_id(buf):
        for _f, _wt, _v in _parse(buf):
            if _f == 4 and _wt == WT_LEN and isinstance(_v, bytes):
                for _f2, _wt2, _v2 in _parse(_v):
                    if _f2 == 5 and _wt2 in (WT_VARINT, WT_64BIT):
                        return True
        return False

    _best_conv: dict[str, bytes] = {}
    for _cb in conv_objs:
        _cid0 = None
        for _s in _extract_str(_cb):
            _m = CONV_RE.search(_s.encode("utf-8", "replace"))
            if _m:
                _cid0 = _m.group().decode()
                break
        if not _cid0:
            continue
        _cur = _best_conv.get(_cid0)
        if _cur is None or (not _has_short_id(_cur) and _has_short_id(_cb)):
            _best_conv[_cid0] = _cb
    if _best_conv:
        conv_objs = list(_best_conv.values())

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
            # 2026-09-13 修复「聊天图片不解码，只显示『前往抖音查看』」：
            # 首包路径此前只取文本 txt[0]，**从不提取图片解密要素**，
            # 导致图片消息落库 extra={}（实测 3 条 [图片] 消息 extra 全空）
            # → 后端 origin_image_resolver 拿不到 skey/origin_url 无法解密
            # → 前端只能降级显示「前往抖音查看」。cmd 301 路径早有该提取
            # （_parse_message_text 返回 skey/origin_url），首包路径漏了，此处补齐。
            _skey, _origin = None, None
            try:
                for _f3, _wt3, _v3 in _parse(sb2):
                    if _f3 == 8 and _wt3 == WT_LEN and isinstance(_v3, bytes):
                        try:
                            _obj3 = json.loads(_v3.decode("utf-8", "ignore"))
                        except Exception:
                            _obj3 = None
                        if isinstance(_obj3, dict):
                            _skey, _origin = _extract_image_secret(_obj3)
                        break
            except Exception:
                _skey, _origin = None, None
            messages.append({
                "role": role,
                "text": txt[0],
                "ts": _msg_ts if _msg_ts else time.time(),
                "msg_id": _parse_message_id(sb2),
                "skey": _skey,
                "origin_url": _origin,
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


def fetch_conversation_history(auth, cid, short_id, count=50, timeout=20,
                               max_pages=40):
    """拉指定会话的**完整**历史消息（cmd 301，自动翻页）。

    2026-09-15 修复（实机逆向 + 验证）：旧实现**只发 1 次请求**（cursor=0），
    当会话条数 > count 时会被**静默截断**为「最新的 count 条」，无任何告警。
    现按官方分页协议翻页取全量：
      · 每页响应 [5] = 分页对象，[5][2]=总数、[5][3]=下一页游标（取更早消息）
      · 循环直到：无游标 / 游标不再前进 / 累计已达总数 / 达 max_pages 上限
    实机验证（count=10 连续翻 5 页）：累计去重 45 条 == 会话总数 45 ✅

    max_pages 兜底防死循环（40 页 × count≥50 = ≥2000 条，足够覆盖私信场景）。

    返回 [{role,text,ts,msg_id,skey,origin_url}]，按 ts 升序，去重（按 msg_id）。
    """
    try:
        import requests
        from builder.proto import ProtoBuilder
        from builder.header import HeaderBuilder, HeaderType
        from dy_apis.douyin_api import DouyinAPI

        my_uid = str(auth.get_uid()).strip()
        url = "https://imapi.douyin.com/v1/message/get_by_conversation"
        headers = HeaderBuilder().build(HeaderType.PROTOBUF)
        headers.set_header("referer", "https://www.douyin.com/")
        page_count = max(int(count or 50), 1)

        merged = []
        seen_ids = set()
        cursor = 0
        total = None
        for page_no in range(max(1, int(max_pages))):
            request = ProtoBuilder.build_normal_request(auth, 301)
            body_bytes = request.SerializeToString()
            inner = _build_301_body(cid, short_id, cursor=cursor, count=page_count)
            tag = _pb_varint((301 << 3) | 2)          # field 301, wiretype 2
            payload = tag + _pb_varint(len(inner)) + inner
            # 追加到 Request 的 field 8 (body): tag = (8<<3)|2 = 66 = 0x42
            body_bytes = body_bytes + b"\x42" + _pb_varint(len(payload)) + payload

            resp = requests.post(
                url, headers=headers.get(), cookies=auth.cookie,
                data=body_bytes, verify=False, timeout=timeout,
            )
            if resp.status_code != 200 or len(resp.content) < 100:
                logger.warning("CAP-004", f"[capture][301] HTTP {resp.status_code} "
                               f"len={len(resp.content)} cid={cid} page={page_no}")
                break

            msgs, page = _extract_301_page(resp.content)
            if total is None:
                try:
                    total = int(page.get("2")) if page.get("2") is not None else None
                except Exception:
                    total = None
            batch = _parse_301_messages(msgs, my_uid)
            added = 0
            for it in batch:
                mid = it.get("msg_id")
                key = mid if mid else f"{it['ts']}|{it['role']}|{it['text'][:32]}"
                if key in seen_ids:
                    continue
                seen_ids.add(key)
                merged.append(it)
                added += 1

            nxt = page.get("3")
            try:
                nxt = int(nxt) if nxt is not None else 0
            except Exception:
                nxt = 0
            # 终止条件
            if not msgs:
                break
            if nxt in (0, None) or nxt == cursor:
                break
            if total is not None and len(merged) >= total:
                break
            cursor = nxt

        if len(merged) > 1:
            merged.sort(key=lambda x: x.get("ts") or 0)
        if total is not None and len(merged) < total:
            logger.warning("CAP-013",
                           f"[capture][301] 会话 {cid} 仅取到 {len(merged)}/{total} 条"
                           f"（可能触达 max_pages={max_pages} 上限）")
        return merged
    except Exception as e:
        logger.warning("CAP-005", f"[capture][301] 拉取失败 cid={cid}: {e}")
        return []


def _extract_301_page(raw):
    """从 cmd301 响应里取出 (消息数组, 分页信息)。

    2026-09-15 逆向确证分页协议（实机 count=10 连续翻页累计到全量 45/45）：
      response['6']['301']['1'] = 本页消息数组
      response['6']['301']['5'] = 分页对象 {
          1 = 本页起始序号(倒序计数), 2 = 会话总条数,
          3 = **下一页游标**(cursor，取更早的消息；无更多页时缺省/0),
          4 = 本页结束 ts, 5 = short_id, 6 = conv_id }
    旧实现只发 1 次请求（cursor=0）→ 会话条数 > count 时**静默截断**为最新 count 条。
    """
    import blackboxprotobuf
    parsed, _ = blackboxprotobuf.decode_message(raw)
    inner = parsed["6"]["301"]
    msgs = inner.get("1", [])
    if isinstance(msgs, dict):
        msgs = [msgs]
    page = inner.get("5") or {}
    if not isinstance(page, dict):
        page = {}
    return msgs, page


def _parse_301_messages(msgs, my_uid):
    """把一批 301 消息条目解析为统一 dict（供分页循环复用）。"""
    out = []
    for m in msgs:
        if not isinstance(m, dict):
            continue
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
                if not text:
                    text = _extract_media_text(obj)
                skey, origin_url = _extract_image_secret(obj)
        except Exception:
            text = s if s else None
        if not text:
            # 2026-09-15：content 为空 ≠ 无内容 —— 实测这类条目是抖音的**系统通知**
            # （content={} 但 field9 带 a:biz=aweme_im_consecutive_chat_notice /
            #   s:biz_aid / notice_type=introduction 等标识）。
            # 旧实现直接 continue 丢弃 → 45 条被静默砍成 43 条。
            # 现改为保留为占位文本（保真：条数与序号不再缺失）。
            text = _system_notice_text(m)
        if not text:
            continue
        ts = m.get("10", 0) or 0
        sender = str(m.get("7", "")).strip()
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


def _system_notice_text(m):
    """识别系统通知类消息并返回可读占位文本；非系统通知返回 None。

    实测结构：`content` 为 `{}`，而 `field 9` 是 [{1:'k', 2:'v'}] 的键值表，
    常见 `a:biz = aweme_im_consecutive_chat_notice`（久未联系/连续聊天提示）。
    """
    try:
        f9 = m.get("9")
        if not f9:
            return None
        if isinstance(f9, dict):
            f9 = [f9]
        kv = {}
        for item in f9:
            if not isinstance(item, dict):
                continue
            k = item.get("1")
            v = item.get("2")
            if isinstance(k, bytes):
                k = k.decode("utf-8", "ignore")
            if isinstance(v, bytes):
                v = v.decode("utf-8", "ignore")
            if k:
                kv[str(k)] = str(v) if v is not None else ""
        biz = kv.get("a:biz") or ""
        if "consecutive_chat_notice" in biz:
            # 说明：抖音会为同一事件写**多条** content={} 的系统条目（实测同一会话
            # 有 2 条，msg_id 不同、文本相同）。占位文本须带上 msg_id 尾号以便区分，
            # 否则前端/统计会误当成重复条目。
            tail = str(m.get("3") or "")[-6:]
            return f"[系统提示] 对方已久未回复，此为连续聊天提醒（#{tail}）"
        if biz:
            tail = str(m.get("3") or "")[-6:]
            return f"[系统消息] {biz}（#{tail}）"
        return None
    except Exception:
        return None


def parse_conversation_301(raw, cid, my_uid):
    """解析 get_by_conversation(cmd 301) 响应 -> [{role,text,ts,msg_id,skey,origin_url}]

    2026-09-15：解析逻辑抽到 `_parse_301_messages`，供分页循环复用（本函数保持
    单页语义不变，避免破坏既有调用方/测试）。
    """
    try:
        msgs, _page = _extract_301_page(raw)
    except Exception as e:
        logger.warning("CAP-006", f"[capture][301] 解析失败: {e}")
        return []
    return _parse_301_messages(msgs, my_uid)


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

def capture_userinfo_via_browser(name, wait=15, max_age=None, lease_id=""):
    """经 BCC 截昵称/头像（DOM 直读为主，hook 兜底）。

    BCC 启动后自动导航到 chat 页 + 平滑滚动 + 逐屏 DOM 抓取。

    lease_id（2026-09-14 v0.43.11）：**跨调用窗口租约**。调用方
    （capture_all）已从 gate 取得租约，此处透传给 BCC `/capture_userinfo`，
    BCC 的 _exec 据此判定「重入」并复用该租约。不传则 BCC 按新请求处理，
    会被调用方自己持有的租约拒绝（实测 BCC-047 → BCC-030 → 昵称 0 个）。

    timeout 提到 240s：BCC 浏览器**冷启动**首次调用实测 152.7s
    （含页面导航与首屏渲染），180s 会在冷启动场景超时。

    2026-09-01 新增**结果缓存**（08 §三十七，修复 220s 慢）：
      BCC 启动后 _prewarm 线程已在后台跑过一次完整捕获（日志
      「昵称缓存预热完成：N 个」）。这里按账号缓存最近一次成功结果，
      默认 10 分钟内直接复用，让「更新会话」几乎零等待。
      可用 DY_USERINFO_CACHE_SEC 调整（0 = 关闭缓存）。
    """
    import time as _t

    # ── 缓存命中检查 ──
    try:
        _ttl = (int(_cfg("capture", "userinfo_cache_sec") or 600)
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

    # ── ① 首选：IndexedDB 用户信息（2026-09-15 实机落地，替代失败的「文本桥」）──
    # 抖音把前端取到的用户信息落在 IndexedDB `<uid>_user`.user，
    # 记录 value.uid 是**数字**，与首包 peer_uid 同体系 → 直接相等比对。
    # 实测：首包 peer_uid ∩ IDB uid = **44/44 = 100%**（对比：文本桥 1/11、
    # 位置对齐错配 80 条）。纯读页面自有存储，**零网络请求**。
    # 转换：{uid: {...}} → {sec_uid|uid: {...}}，下游 uid 索引同时识别两者。
    try:
        _r0 = requests.post(f"http://127.0.0.1:{bport}/userinfo_idb",
                            json={"wait": 5, "lease_id": lease_id or ""},
                            timeout=180)
        if _r0.status_code == 200:
            _j0 = _r0.json() or {}
            _users = _j0.get("users") or {}
            if _j0.get("ok") and _users:
                # 以 **uid 为键**（下游 _userinfo_by_uid 直接命中），
                # 同时把 sec_uid 塞进 value 供按 sec_uid 的旧路径兜底。
                _out = {}
                for _uid, _v in _users.items():
                    if not _uid:
                        continue
                    _out[str(_uid)] = {
                        "uid": str(_uid),
                        "nickname": _v.get("nickname") or "",
                        "avatar": _v.get("avatar") or "",
                        "sec_uid": _v.get("sec_uid") or "",
                        "avatar_uri": _v.get("avatar_uri") or "",
                        "unique_id": _v.get("unique_id") or "",
                    }
                logger.info(
                    f"[capture] 经 IndexedDB 取到用户信息: {len(_out)} 条"
                    f"（库 {_j0.get('db')}；零网络请求）")
                _userinfo_cache[name] = (_t.time(), _out)
                return _out
            logger.warning(
                "CAP-014",
                f"[capture] /userinfo_idb 无数据（total={_j0.get('total')}），"
                f"降级 DOM 抓取")
        else:
            logger.warning("CAP-014", f"[capture] /userinfo_idb HTTP {_r0.status_code}，降级 DOM")
    except Exception as _e0:  # noqa: BLE001
        logger.warning("CAP-014", f"[capture] /userinfo_idb 调用失败（降级 DOM）: {_e0}")

    # ── ② 兜底：DOM 直读（原路径，覆盖 IDB 尚未写入的会话）──
    url = f"http://127.0.0.1:{bport}/capture_userinfo"
    try:
        # wait: BCC 在 chat 页平滑滚动 + DOM 抓取的秒数。
        # 2026-09-14 v0.43.11：BCC 侧若发现预热仍在进行，会先等预热（最多
        # 40s）再走缓存命中，故此处给足客户端超时。
        r = requests.post(url, json={"wait": 15, "lease_id": lease_id or ""},
                          timeout=600)
        if r.status_code == 200:
            _j = r.json() or {}
            data = _j.get("data") or {}
            if not _j.get("ok"):
                logger.warning(
                    "CAP-007",
                    f"[capture] BCC /capture_userinfo 返回失败: {_j.get('msg')}")
            else:
                logger.info(f"[capture] 经 BCC 截到昵称数: {len(data)}")
            if data:
                _userinfo_cache[name] = (_t.time(), data)
            return data
    except Exception as e:
        logger.warning("CAP-007", f"[capture] 调 BCC /capture_userinfo 失败: {e}")
    return {}


# 昵称缓存：{账号: (采集时间戳, {sec_uid: {...}})}
# 配合 BCC 的 _prewarm，避免每次「更新会话」都重跑 176s 的滚动捕获。
_userinfo_cache: dict[str, tuple] = {}

# 2026-09-14 v0.43.11：跨调用窗口租约登记表 {账号: lease_id}。
# 「更新会话全程」由 api/messages.refresh_conversations 在 finally 里
# `release_active_lease(account)` 释放（设计文档 §3.6 的最终落地）。
_ACTIVE_LEASE: dict[str, str] = {}


def release_active_lease(account: str) -> None:
    """释放该账号由 capture_all 取得的跨调用窗口租约（幂等，绝不再抛）。

    必须放在调用方 finally 里 —— 否则只能等 BCC 侧 TTL（P0 上限 300s）回收，
    期间该账号的浏览器对所有其他业务不可用。
    """
    _lid = _ACTIVE_LEASE.pop(account, "")
    if not _lid:
        return
    try:
        from services.browser_gate import release_lease
        r = release_lease(account, _lid, holder="capture_all")
        logger.info(f"[capture][{account}] 已释放跨调用窗口租约 "
                    f"({_lid[:8]}…) ok={r.get('ok')}")
    except Exception as _e:
        logger.warning("CAP-017",
                       f"[capture][{account}] 释放租约失败（等 TTL 回收）: {_e}")


# 模块级缓存：首包解析结果（供 capture_userinfo_via_browser 取 peer_uid）
_last_parsed_convs: dict[str, list] = {}


# ---------------------------------------------------------------------------
# 组合：首包解析 + 浏览器昵称 + 写库
# ---------------------------------------------------------------------------
def capture_all(name, with_browser=True):
    """前移捕获：首包解析会话+消息，浏览器截昵称头像，写 dm_conversations + dm_messages。
    返回 (n_conv, n_msg) 写库数量。

    2026-09-14 v0.43.11：本函数是「更新会话全程」＝**跨调用窗口租约**的持有者
    （`调度器租约设计细节.md` §3.6）。租约从 ensure_browser 取得、lease_id 透传到
    BCC `/cookie` 与 `/capture_userinfo`，函数返回前一定 release。
    """
    from auto_dm import accounts as acc
    from dy_apis.login_api import DYLoginApi
    from dy_apis.douyin_api import DouyinAPI
    from database import get_db

    _lease_id = ""
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
            # 2026-09-13 统一入口收敛（用户要求：所有启动路径走同一入口，杜绝环境分叉）：
            # 原实现 allow_launch=True —— BCC 不在线时【兜底直开一个浏览器】，
            # 于是可能「BCC 容器 + 这个临时浏览器」同时持有同一 profile → 抢锁
            # → 环境跳变 → 风控（实测日志 19:44:50「强制重扫模式：复用固定 profile」
            # 即 BCC 离线时另起的实例，同时 19:46:41 又见 profile 锁 10s 未释放）。
            # 现：交给统一门禁 ensure_browser —— 复用 BCC；离线则先拉起 BCC 再复用；
            # 拿不到就显式失败（调用方提示用户），【绝不静默开第二个浏览器】。
            #
            # 🔴 2026-09-14 v0.43.11 跨调用窗口租约接线（本函数 = 「更新会话全程」）：
            #   `调度器租约设计细节.md` §3.6 早已规划「更新会话全程」属跨调用窗口，
            #   须「显式 POST /lease + release，_exec 检测到已持有则复用（同 lease_id）」。
            #   但此前只 acquire、**lease_id 从未向下透传，也从未 release** ——
            #   于是本函数第 1135 行自己拿到 gate 租约后，5 秒后自己发起的
            #   /capture_userinfo 请求被判为「并发冲突」而被同一把租约拒绝：
            #     BCC-047 → BCC-030 → 昵称 0 个 → peer_name 回填裸 UID → 前端只显示数字。
            #   修法：租约 lift 到整个 capture_all 生命周期，lease_id 透传到捕获调用，
            #   finally 里 release。用途改 PURPOSE_USER —— 这是**用户点按钮**触发的
            #   路径（prio=0 / ttl≤300s），按既有铁律「用户显式动作豁免后台守卫」。
            try:
                from services.browser_gate import (
                    ensure_browser, refresh_cookie_via_owner, release_lease,
                    PURPOSE_USER)
                _g = ensure_browser(name, purpose=PURPOSE_USER,
                                    holder="capture_all", ttl=300.0)
                if not _g.get("ok"):
                    logger.warning("CAP-016",
                        f"[capture][{name}] 浏览器统一入口未就绪：{_g.get('msg')}"
                        f"（不新开独立浏览器，将只用 .env 凭证跑 HTTP 首包）")
                else:
                    # 跨调用窗口租约：贯穿整个「更新会话」，由调用方 finally 释放
                    _lease_id = _g.get("lease_id") or ""
                    _ACTIVE_LEASE[name] = _lease_id
                    refresh_cookie_via_owner(name, auth, env_path,
                                             lease_id=_lease_id)
            except Exception as _e:
                logger.warning("CAP-016",
                    f"[capture][{name}] 统一入口调用异常（沿用 .env 凭证）: {_e}")
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
        logger.warning("CAP-008", f"[capture][{name}] 加载凭证失败: {e}")
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
        logger.warning("CAP-009", f"[capture][{name}] 首包解析失败: {e}")
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
        if _cfg_bool("capture", "history_full",
                     env="DY_HISTORY_FULL", default=True):
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

            _FORCE = _cfg_bool("capture", "history_force",
                               env="DY_HISTORY_FORCE", default=False)

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
                if _cfg_bool("capture", "history_skip_paged",
                             env="DY_HISTORY_SKIP_PAGED", default=False):
                    need = [c for c in convs
                            if c.get("short_id")
                            and _db_counts.get(str(c.get("conversation_id")), 0)
                                < max(len(c.get("messages", [])), 1)]
                else:
                    need = [c for c in convs if c.get("short_id")]
            max_n = int(_cfg("capture", "history_max") or 45)
            sleep_s = float(_cfg("capture", "history_sleep") or 1.5)
            # 2026-08-31：串行补全实测太慢（20 个会话 × (请求~10s + 间隔1.5s)
            # ≈ 230s，加上首包和浏览器启动总计 365s，用户明确抱怨）。
            # 改为**可配置并发**：DY_HISTORY_WORKERS（默认 4）。
            # 保守取值的原因：本项目曾因短时高频调用抖音接口触发风控
            # （首包返回 50 字节空响应、cookie 失效），故不做激进并发。
            # 想更快可调到 6~8，但需承担风控风险。
            workers = max(1, min(8, int(_cfg("capture", "history_workers") or 4)))
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
                        # 2026-09-15：fetch_conversation_history 已内建游标翻页，
                        # 单页 count 固定即可（它会自动拉到会话全量）。
                        _h = fetch_conversation_history(
                            auth, _c["conversation_id"], _c["short_id"],
                            count=50,
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
                                logger.warning("CAP-010", 
                                    f"[capture][{name}] 301 补全失败 {peer_uid}:"
                                    f" {err}")
                            else:
                                logger.info(
                                    f"[capture][{name}] 301 补全 {peer_uid}: {n} 条")
    except Exception as e:
        logger.warning("CAP-011", f"[capture][{name}] 长会话补全失败（降级仅首包）: {e}")

    # 2) 浏览器昵称（可选，单 profile 独占）
    userinfo = {}
    if with_browser:
        try:
            userinfo = capture_userinfo_via_browser(name, lease_id=_lease_id)
        except Exception as e:
            logger.warning("CAP-012", f"[capture][{name}] 浏览器昵称捕获失败（降级仅首包）: {e}")

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
        # 2026-09-14 v0.43.9：BCC 改走 DOM 抓取后，键是**昵称**（DOM 无 uid）。
        # 原逻辑只认 uid → DOM 结果会被全部丢弃（昵称仍全空）。
        # 这里同时建「昵称索引」，供 uid 匹配不到时兜底。
        _userinfo_by_nick = {}
        for _k, _v in userinfo.items():
            _u = _v.get("uid")
            if _u:
                _userinfo_by_uid[str(_u)] = _v
            _nk = (_v.get("nickname") or "").strip()
            if _nk:
                _userinfo_by_nick[_nk] = _v
            elif _k:
                # 键本身即昵称（DOM 路径）
                _userinfo_by_nick[str(_k).strip()] = _v
        _nick_order = list(_userinfo_by_nick.keys())

        # ══════════════════════════════════════════════════════════════════
        # 2026-09-14 v0.43.11 方案 A：**文本桥**（DOM 昵称 → peer_uid 精确关联）
        #
        # 为什么需要：抖音改版后昵称只在**会话列表 DOM**（无 uid 属性），
        # 而 peer_uid 只在**首包 protobuf**。两端没有共同 id（实机确证：
        # DOM 项仅 data-e2e="conversation-item" + title + img，点击会话
        # 也不改 URL —— 位置/顺序对齐会在懒加载+置顶错位，绝不可用）。
        #
        # 但两端有一个**共同的自然键：最后一条消息的正文**。
        #   · 首包每个会话自带 messages（权威、带 uid 归属）
        #   · DOM 每项的 desc 就是「该会话最后一条消息」的预览文本
        # 于是：用首包消息文本建索引 → 与 DOM desc 精确匹配 → 命中即得 uid。
        #
        # ⚠️ 只在能唯一命中时建立映射；命中多义（同文本属于多个会话，如群发
        #    欢迎语）一律**放弃**（宁可不显示昵称，绝不张冠李戴）。
        # ══════════════════════════════════════════════════════════════════
        _text_to_uids: dict[str, set] = {}
        for _cc_conv in convs:
            _pu = _cc_conv.get("peer_uid")
            if not _pu:
                continue
            for _m in (_cc_conv.get("messages") or []):
                _tx = (_m.get("text") or "").replace("\n", " ").strip()
                if len(_tx) < 4:
                    continue
                _text_to_uids.setdefault(_tx[:80], set()).add(str(_pu))
        _bridged = 0
        _ambig = 0
        for _nk, _vv in list(_userinfo_by_nick.items()):
            _desc = (_vv.get("desc") or "").replace("\n", " ").strip()
            if not _desc or len(_desc) < 4:
                continue
            # 双向包含匹配：DOM desc 可能被截断/加省略号，首包文本是全文
            _cands = _text_to_uids.get(_desc[:80]) or set()
            if not _cands:
                for _tk, _tus in _text_to_uids.items():
                    if _tk[:24] and (_tk[:24] in _desc or _desc[:24] in _tk):
                        _cands = set(_cands) | _tus
            if len(_cands) == 1:
                _uid_hit = next(iter(_cands))
                if _uid_hit not in _userinfo_by_uid:
                    _userinfo_by_uid[_uid_hit] = {
                        "nickname": _nk,
                        "avatar": _vv.get("avatar") or "",
                        "uid": _uid_hit,
                    }
                    _bridged += 1
            elif len(_cands) > 1:
                _ambig += 1
        if _bridged or _ambig:
            logger.info(
                f"[capture][{name}] 文本桥（方案A）：唯一关联昵称 {_bridged} 个，"
                f"歧义放弃 {_ambig} 个，未匹配 "
                f"{len(_userinfo_by_nick) - _bridged - _ambig} 个")
        if _userinfo_by_nick and not _userinfo_by_uid:
            logger.info(
                f"[capture][{name}] 昵称索引已建立（{len(_userinfo_by_nick)} 个，"
                f"DOM 路径无 uid，将按昵称兜底关联）")
        _by_uid = 0
        _by_sec = 0
        # 2026-09-13 第三道防线：写库前的「本号 UID」同样以 conv_id 共同项为准。
        # 原实现在 L1283 直接用 _myuid，而该名字在部分路径并未定义（dir() 检查
        # 出现在更靠后的 L1377），一旦取值失败 _real 恒为 None → 订正失效、
        # peer_id 保持错误值。此处独立算一遍，不依赖任何外部变量。
        _auth_uid = None
        try:
            from collections import Counter as _C2
            _cc = _C2()
            _nn = 0
            for _c in convs:
                _pp = str(_c.get("conversation_id") or "").split(":")
                if len(_pp) == 4:
                    _cc[_pp[2]] += 1
                    _cc[_pp[3]] += 1
                    _nn += 1
            if _nn:
                _top = [(u, k) for u, k in _cc.items() if k >= _nn * 0.9]
                if _top:
                    _auth_uid = max(_top, key=lambda x: x[1])[0]
        except Exception:
            _auth_uid = None
        if not _auth_uid:
            try:
                _auth_uid = str(locals().get("_myuid") or "") or None
            except Exception:
                _auth_uid = None
        if _auth_uid:
            logger.info(
                f"[capture][{name}] 本号 UID（conv_id 共同项）= {_auth_uid}")
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
                _real = (_b if _a == str(_auth_uid)
                         else (_a if _b == str(_auth_uid) else None))
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
            # 2026-09-14（本分支 design/better-douyin）：`short_id` 列改为**写入 sec_uid**。
            #   原实现硬编码 None → 该列恒为 NULL，导致「主动批量查用户」没有关联键
            #   （`/aweme/v1/web/im/user/info/` 实测只认 `sec_user_ids`，见
            #    docs/reverse_interface_spec.md §三）。而 `parse_init_protobuf`
            #   其实**早已解析出 sec_uid**，只是从未落库 —— 本次补上最后一公里。
            conn.execute(
                "INSERT OR IGNORE INTO dm_conversations("
                "account,conv_id,peer_id,peer_name,short_id,last_ts,unread,avatar) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (name, cid, peer_uid, nickname or peer_uid,
                 c.get("sec_uid") or None, 0, 0, avatar or None),
            )
            # 存量记录补写 sec_uid（原为 NULL 的行，本次起可回填）
            if c.get("sec_uid"):
                conn.execute(
                    "UPDATE dm_conversations SET short_id=? "
                    "WHERE account=? AND conv_id=? "
                    "AND (short_id IS NULL OR short_id='')",
                    (c["sec_uid"], name, cid),
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
            # 2026-09-13：本号 uid 必须用 conv_id 共同项（_auth_uid），不能用
            # auth.get_uid() —— 后者是 web query/user 的 user_uid，与 imapi
            # 会话体系的 uid 可能不同值（§9.8 轮换现象）。用错值会导致
            # WHERE peer_id=? 匹配不到任何行 → 订正静默失效（正是本次
            # 「更新会话后全是本号 UID」未被自动修正的原因）。
            _myuid_fix = str(_auth_uid or "")
            if not _myuid_fix:
                raise RuntimeError("本号 uid 无法确定，跳过存量订正")
            # 同时覆盖「peer_id == 本号」与「peer_id 为空」两类污染
            _dirty = conn.execute(
                "SELECT conv_id, peer_id, peer_name FROM dm_conversations "
                "WHERE account=? AND (peer_id=? OR peer_id IS NULL "
                "OR peer_id='' OR peer_id=peer_name)",
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
                _ui = _userinfo_by_uid.get(_real) or {}
                # 🔴 2026-09-14 v0.43.11 修复「张冠李戴」：此处原用 `convs.index(_c)`。
                #   `_c` 是**上面 `for _c in convs:` 循环结束后泄漏的变量**，恒等于
                #   convs 的**最后一个元素** → `_idx` 恒为 len-1 → 所有 _dirty 行都取
                #   `_nick_order[43]` 同一个昵称。实机后果：80 条会话的 peer_name
                #   全被写成同一个「傲雪」（比原来的裸 UID 更糟，属数据污染）。
                #   修法：**不再用位置猜测**（DOM 无 uid，顺序软对齐不可靠）——
                #   uid 无匹配时保留真实 UID 占位，绝不写入可能错误的昵称。
                _newname = _ui.get("nickname") or _real
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
            logger.warning("CAP-013", f"[capture][{name}] 存量污染订正失败: {e}")
        # 兜底：recv_daemon 可能已写入 capture_all 首包未解析到的会话（WS 增量等，
        # 其 peer_id 是对端 UID 但 peer_name 仍是占位/自己）。用已截获的 BCC 昵称补全库内
        # 所有「peer_name 为空/等于 peer_id（数字 UID）/等于自己 UID」的会话，
        # 保证展示层无裸 UID、也不出现「自己」的冗余显示。
        try:
            _myuid = str(_auth_uid or "")   # 2026-09-13：同上，用 conv_id 共同项
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
            logger.warning("CAP-014", f"[capture][{name}] 兜底补全失败: {e}")
    except Exception as e:
        logger.warning("CAP-015", f"[capture][{name}] 写库失败: {e}")
        traceback.print_exc()
    logger.info(f"[capture][{name}] 写库完成：会话 {n_conv}（含消息 {n_msg}），"
                f"昵称命中 uid关联={_by_uid} sec_uid关联={_by_sec} "
                f"未命中={len(convs) - _by_uid - _by_sec}/{len(convs)}")
    return (n_conv, n_msg)
