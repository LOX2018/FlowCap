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


def _parse_message_text(b):
    """从消息对象的 bytes 里提取聊天文本。返回 (text, aweType, createdAt) 或 None。"""
    strs = _extract_str(b)
    for s in strs:
        if '"text"' not in s:
            continue
        j = _strip_pb_prefix(s)
        if not j:
            continue
        try:
            obj = json.loads(j)
        except Exception:
            continue
        text = obj.get("text")
        if not text:
            continue
        return (text, obj.get("aweType"), obj.get("createdAt") or 0)
    return None


# ---------------------------------------------------------------------------
# 首包精确解析（V23 实证：field 6 = conversation 数组）
# ---------------------------------------------------------------------------
def parse_init_protobuf(raw, my_uid):
    """解析 get_message_by_init 首包，返回会话列表（含消息）。

    返回 [{conversation_id, peer_uid, sec_uid, messages:[{role,text,ts}]}]
    - conversation 数组 = 顶层 field 6
    - 每条 message 内嵌 conv_id 链接键（0:1:uid:uid）
    - 消息体 JSON 嵌套在 len 字段，剥 pb 前缀后 json.loads 取 text
    """
    if not raw:
        return []
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
            role = "them" if txt[1] == 700 else "me"
            messages.append({
                "role": role,
                "text": txt[0],
                "ts": (txt[2] / 1000.0) if txt[2] else time.time(),
            })
        result.append({
            "conversation_id": cid,
            "peer_uid": peer_uid,
            "sec_uid": sec_uid,
            "messages": messages,
        })
    logger.info(f"[capture] 首包解析出 {len(result)} 个会话，"
                f"含消息的 {sum(1 for r in result if r['messages'])} 个")
    return result


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

def capture_userinfo_via_browser(name, wait=15):
    """经 BCC 被动 hook 截前端自发 im/user/info 响应（08 文档验证 44/44）。

    BCC 启动后自动导航到 chat 页 + 平滑滚动触发全部 im/user/info。
    超时 120s（滚动 40 轮约需 66s）。
    """
    import requests
    from auto_dm import accounts as acc
    try:
        bport = acc.browser_daemon_port(name)
    except Exception:
        return {}
    url = f"http://127.0.0.1:{bport}/capture_userinfo"
    try:
        r = requests.post(url, json={"wait": 60}, timeout=120)
        if r.status_code == 200:
            data = r.json().get("data") or {}
            logger.info(f"[capture] 经 BCC 截到昵称数: {len(data)}")
            return data
    except Exception as e:
        logger.warning(f"[capture] 调 BCC /capture_userinfo 失败: {e}")
    return {}


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
        DYLoginApi.refresh_cookie_from_profile(auth, env_path)
        my_uid = str(auth.get_uid())
    except Exception as e:
        logger.warning(f"[capture][{name}] 加载凭证失败: {e}")
        return (0, 0)

    # 1) 首包
    try:
        raw = DouyinAPI.get_message_by_init(auth)
        convs = parse_init_protobuf(raw, my_uid)
    except Exception as e:
        logger.warning(f"[capture][{name}] 首包解析失败: {e}")
        return (0, 0)
    if not convs:
        return (0, 0)
    # 缓存首包解析结果（供 capture_userinfo_via_browser 取 peer_uid 桥接）
    _last_parsed_convs[name] = convs

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
            # upsert 会话骨架
            conn.execute(
                "INSERT OR IGNORE INTO dm_conversations("
                "account,conv_id,peer_id,peer_name,short_id,last_ts,unread,avatar) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (name, cid, peer_uid, nickname or peer_uid, None, 0, 0, avatar or None),
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
                    conn.execute(
                        "INSERT OR IGNORE INTO dm_messages("
                        "account,conv_id,role,text,msg_type,extra,ts) VALUES(?,?,?,?,?,?,?)",
                        (name, cid, m["role"], m["text"], "text", "{}", m["ts"]),
                    )
                    n_msg += 1
                except Exception:
                    pass
            n_conv += 1
        conn.commit()
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
