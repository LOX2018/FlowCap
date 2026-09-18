# -*- coding: utf-8 -*-
"""合并转发（聊天记录卡片）解析与正文补全（2026-09-17 新增）。

对照上游 `extractor/forwarded.py` + `docs/merged-forward-records.md`。

## 设计意图与**契约冲突修正**

抖音的「合并转发」卡片内容是 `aweType=13600` 的富媒体对象：
· `title` / `list_content` / `msg_ids` → 预览与索引；
· `inline_content` → 新格式自带的**完整正文**（无需任何网络请求）；
· `upload_key_list` + `skey` → 旧格式只给索引，正文需远端补抓后解密。

⚠️ **本次修正一处既有契约冲突（Iron Law 5：契约即架构）**

我方 `conversation_capture.py` 原注释写「上游 getProfileCard 用 aweType=13600」，
据此把 `13600` 归类为**名片**。经**上游源码实测核对**该说法不成立：
· 上游前端 `getForwardInfo()`：`if (String(cj?.aweType) !== '13600') return null`
  → **13600 就是合并转发**；
· 上游 `getProfileCard()`：判据是 `cj.name && (cj.secUID || cj.sec_uid ||
  (cj.uid && cj.source === 'others_homepage'))`，**与 13600 无关**。

故本模块按上游真值把 13600 作为合并转发处理；**名片识别改为与上游同款判据**
（见 `classify_card()`），避免继续用错误类型键。

## 正文来源优先级（照上游）

1. **本地原消息**：用 `msg_ids` 去本地库查（可复用已下载媒体）；
2. **inline_content**：新格式自带完整正文，零请求；
3. **远端补抓**：仅当上面都没有，且卡片带 `upload_key_list` 时才发请求
   —— 走 BCC 页面上下文（复用登录态，**不向其它域转发 cookie**）。

## 安全约束（照上游，逐条落地）

· 资源 URL 必须 `https` 且主机匹配 `p\\d+-aweme-im-merge-share-sign\\.byteimg\\.com`
  （**白名单**，防任意域下载）；
· 单文件 ≤ 16 MiB、不跟随重定向、**下载不带 cookie**（签名 URL 自身足够）；
· skey 必须是 32/48/64 位 hex（AES-128/192/256）；
· AAD 为空；`nonce(12) + 密文与标签`，非 12 字节前缀即拒绝；
· 完整性：解密后条数 == `count`、ID 无重复且与索引集合**完全一致**，
  否则**不写入**（宁缺勿错）。
"""
from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlsplit

from loguru import logger

OBJECT_URL_PATH = "/aweme/v1/im_communication/merge_msg_card/get_object_url/"
WEB_OBJECT_URL_PATH = "/aweme/v1/web/im_communication/merge_msg_card/get_object_url/"
MAX_RESOURCE_BYTES = 16 * 1024 * 1024
MAX_MESSAGES = 1000
_UPLOAD_PREFIX = "douyin-im-merge-share/"
_SKEY_RE = re.compile(r"(?:[0-9a-fA-F]{32}|[0-9a-fA-F]{48}|[0-9a-fA-F]{64})")
_HOST_RE = re.compile(r"p[0-9]+-aweme-im-merge-share-sign\.byteimg\.com")
_MSG_ID_RE = re.compile(r"[1-9][0-9]*")
_AWE_MERGE = "13600"

# 在**已登录的抖音页面上下文**里发同域 POST（复用网页登录态；不向第三方域转发 cookie，
# 也不把 64 位 ID 经 JS Number 往返 —— body 已在 Python 侧序列化为字符串）。
WEB_FETCH_JS = """async (arg) => {
    const [path, body] = arg;       // 本项目约定：exec_js(js, [a, b]) → JS 内解构
    if (location.origin !== 'https://www.douyin.com')
        throw new Error('需要抖音网页登录上下文');
    const r = await fetch(path + '?aid=6383&device_platform=webapp', {
        method: 'POST', credentials: 'include',
        headers: {'Content-Type': 'application/json'}, body,
        signal: AbortSignal.timeout(15000),
    });
    return {status: r.status, body: await r.text()};
}"""


# --------------------------------------------------------------------------- #
# 判定
# --------------------------------------------------------------------------- #
def is_merge_forward(content: Any) -> bool:
    """是否为合并转发卡片（上游口径：aweType == '13600'）。"""
    c = _as_obj(content)
    return str(c.get("aweType") or "") == _AWE_MERGE


def is_profile_card(content: Any) -> bool:
    """是否为用户名片（与上游 `getProfileCard()` **同款判据**）。

    上游：`cj.name && (cj.secUID || cj.sec_uid || (cj.uid && cj.source ===
    'others_homepage'))`——不再用 aweType 判名片（那是错误的类型键）。
    """
    c = _as_obj(content)
    if not c.get("name"):
        return False
    return bool(c.get("secUID") or c.get("sec_uid")
                or (c.get("uid") and c.get("source") == "others_homepage"))


def classify_card(content: Any) -> str:
    """卡片类型判定（合并转发优先，其次名片）。返回 "" 表示不是卡片。"""
    if is_merge_forward(content):
        return "merge_forward"
    if is_profile_card(content):
        return "profile_card"
    return ""


def _as_obj(v: Any) -> dict:
    if isinstance(v, dict):
        return v
    if isinstance(v, str) and v.strip().startswith("{"):
        try:
            d = json.loads(v)
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}
    return {}


def _as_list(v: Any) -> list:
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except Exception:
            raise ValueError("合并记录列表格式错误")
    if not isinstance(v, list):
        raise ValueError("合并记录列表格式错误")
    return v


def validate_skey(skey: Any) -> str:
    """校验 hex 密钥（32/48/64 位 → AES-128/192/256）。"""
    if not isinstance(skey, str) or not _SKEY_RE.fullmatch(skey):
        raise ValueError("合并记录密钥格式错误")
    return skey


def check_resource_url(url: Any) -> str:
    """资源下载 URL 白名单校验（https + 指定 CDN 主机 + 无凭证 + 默认端口）。"""
    if not isinstance(url, str):
        raise ValueError("合并记录资源链接无效")
    p = urlsplit(url)
    if (p.scheme != "https" or p.username or p.password
            or p.port not in (None, 443)
            or not _HOST_RE.fullmatch(p.hostname or "")):
        raise ValueError("合并记录资源链接不是已验证的下载域名")
    return url


def _message_id(message: dict) -> str:
    v = message.get("server_message_id")
    if isinstance(v, bool) or not _MSG_ID_RE.fullmatch(str(v)):
        raise ValueError("合并记录消息 ID 无效")
    return str(v)


# --------------------------------------------------------------------------- #
# 解密
# --------------------------------------------------------------------------- #
def decode_resource(data: bytes, skey: str) -> list[dict]:
    """解密一段合并记录资源 → 正文消息列表（`nonce(12) + AES-GCM`，无 AAD）。"""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if not isinstance(data, (bytes, bytearray)) or not 28 <= len(data) <= MAX_RESOURCE_BYTES:
        raise ValueError("合并记录资源长度无效")
    key = bytes.fromhex(validate_skey(skey))
    try:
        raw = AESGCM(key).decrypt(bytes(data[:12]), bytes(data[12:]), None)
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"合并记录解密失败: {type(e).__name__}")
    try:
        payload = json.loads(raw)
    except Exception:
        raise ValueError("合并记录正文不是合法 JSON")
    messages = payload.get("Messages") if isinstance(payload, dict) else None
    if not isinstance(messages, list) or not 1 <= len(messages) <= MAX_MESSAGES:
        raise ValueError("合并记录资源缺少 Messages 正文")
    ids: set[str] = set()
    for m in messages:
        if not isinstance(m, dict):
            raise ValueError("合并记录正文格式错误")
        sid = _message_id(m)
        if sid in ids:
            raise ValueError("合并记录资源包含重复消息")
        ids.add(sid)
        content = m.get("content")
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except Exception:
                raise ValueError("合并记录消息正文缺失")
        if not isinstance(content, dict) or not content:
            raise ValueError("合并记录消息正文缺失")
    return messages


# --------------------------------------------------------------------------- #
# 索引 / 完整性
# --------------------------------------------------------------------------- #
def expected_ids(content: Any) -> list[str]:
    """卡片索引里的 msg_id 顺序（`msg_ids`）。"""
    c = _as_obj(content)
    out = []
    for d in _as_list(c.get("msg_ids", []) or []):
        if isinstance(d, dict) and d.get("msg_id") is not None:
            out.append(str(d["msg_id"]))
        elif d is not None:
            out.append(str(d))
    return out


def upload_keys(content: Any) -> list[tuple[str, int]]:
    """`upload_key_list` → [(key, count)]（校验前缀/去重/条数范围）。"""
    c = _as_obj(content)
    ups = _as_list(c.get("upload_key_list", []) or [])
    if len(ups) > MAX_MESSAGES:
        raise ValueError("合并记录资源数量过多")
    out: list[tuple[str, int]] = []
    seen: set[str] = set()
    for u in ups:
        if not isinstance(u, dict):
            raise ValueError("合并记录资源索引无效")
        uri, cnt = u.get("key"), u.get("count")
        if (not isinstance(uri, str) or not uri.startswith(_UPLOAD_PREFIX)
                or uri in seen or type(cnt) is not int or not 1 <= cnt <= MAX_MESSAGES):
            raise ValueError("合并记录资源索引无效")
        seen.add(uri)
        out.append((uri, cnt))
    return out


def complete(content: Any, bodies: Any) -> bool:
    """bodies 是否已是**完整且一致**的正文（避免用摘要在外充完整）。"""
    if not isinstance(bodies, list):
        return False
    try:
        exp = set(expected_ids(content))
        got = {_message_id(b) for b in bodies}
        return bool(exp) and exp == got
    except (ValueError, KeyError, TypeError, AttributeError):
        return False


# --------------------------------------------------------------------------- #
# 补抓（经调用方注入的传输：页面上下文 fetch + 白名单下载）
# --------------------------------------------------------------------------- #
async def fetch_uploaded_bodies(content: Any, request_url, download, *,
                                access_chain: list | None = None) -> list[dict]:
    """按 `upload_key_list` 逐片取回并解密，**全部校验通过后才返回**。

    `request_url(path, body) -> dict`：调用方在**已登录的抖音页面上下文**里
    发同域 POST（须自行保证 origin 为 douyin.com，且不向第三方域转发 cookie）。
    `download(url, max_bytes) -> bytes`：须自行流式限长、不跟随重定向、不带 cookie。
    """
    c = _as_obj(content)
    ups = _as_list(c.get("upload_key_list", []) or [])
    if not ups:
        return []
    pairs = upload_keys(content)
    skey = validate_skey(c.get("skey"))
    expected = expected_ids(content)
    if not expected or len(expected) > MAX_MESSAGES or len(set(expected)) != len(expected):
        raise ValueError("合并记录消息索引无效")

    got: dict[str, dict] = {}
    for uri, count in pairs:
        body: dict[str, Any] = {"uri": uri}
        if access_chain is not None:
            body["access_chain"] = access_chain
        resp = await request_url(OBJECT_URL_PATH, body)
        if not isinstance(resp, dict):
            raise ValueError("合并记录资源接口未返回成功状态")
        # 网页端成功响应**没有** status_code（实测），Android 端为 0；两者都接受，
        # 但显式非零状态码必须拒绝（上游注释：do not mask explicit nonzero）。
        sc = resp.get("status_code")
        if sc is not None and sc != 0:
            raise ValueError(f"合并记录资源接口未返回成功状态 (status_code={sc})")
        url = check_resource_url(resp.get("url"))
        part = decode_resource(await download(url, MAX_RESOURCE_BYTES), skey)
        if len(part) != count:
            raise ValueError("合并记录资源条数不符")
        for m in part:
            sid = _message_id(m)
            if sid not in expected or sid in got:
                raise ValueError("合并记录资源与消息索引不符")
            got[sid] = m
    if set(got) != set(expected):
        raise ValueError("合并记录资源正文不完整")
    return [got[sid] for sid in expected]


# --------------------------------------------------------------------------- #
# 渲染
# --------------------------------------------------------------------------- #
def _body_text(msg: dict) -> str:
    """把正文单条的 content 渲染成一行可读文本（尽力而为，不抛错）。"""
    c = _as_obj(msg.get("content"))
    if not c:
        return "[未知内容]"
    t = c.get("text")
    if isinstance(t, str) and t.strip():
        return t.strip()
    awe = str(c.get("aweType") or "")
    if awe == "700":
        return "[表情]"
    if c.get("url_list") or c.get("uri") and str(c.get("uri")).startswith("http"):
        return "[媒体]"
    if is_profile_card(c):
        return f"[用户名片] {c.get('name')}"
    if c.get("title"):
        return f"[分享] {c['title']}"
    return f"[内容 aweType={awe or '?'}]"


def render_text(content: Any, bodies: list[dict] | None = None,
                local_map: dict[str, dict] | None = None,
                limit: int = MAX_MESSAGES) -> str:
    """渲染合并转发正文文本（**本地原消息优先**，其次 bodies）。

    返回形如：
        [聊天记录] 出差报销汇总（3 条）
        · 张三：发票已交
        · 李四：明天补
    """
    c = _as_obj(content)
    title = str(c.get("title") or "聊天记录").strip()
    ids = expected_ids(c)
    # 2026-09-18 审查清理：此处原有 dead local `text = ""`（全文未被读取）
    if complete(c, c.get("inline_content")):
        bodies = c["inline_content"]
    elif complete(c, bodies):
        pass
    else:
        bodies = None
    if bodies is None and local_map:
        picked = [local_map[i] for i in ids if i in local_map]
        if len(picked) == len(ids) and ids:
            bodies = [{"server_message_id": i, "content": local_map[i]}
                      for i in ids]
    n = len(bodies) if bodies else len(ids)
    head = f"[聊天记录] {title}（{n} 条）"
    if not bodies:
        # 正文缺失：**如实标注**，不用摘要冒充完整内容
        prev = c.get("list_content")
        lines = []
        if isinstance(prev, list):
            for p in prev[:5]:
                o = _as_obj(p)
                who = str(o.get("nick_name") or o.get("nickname") or "")
                tx = str(o.get("text") or "")
                if tx:
                    lines.append(f"· {who}：{tx}".rstrip("："))
        detail = ("\n" + "\n".join(lines)) if lines else ""
        return f"{head}\n[正文未获取，仅摘要]{detail}"
    out = [head]
    for m in bodies[:limit]:
        o = _as_obj(m)
        who = str(o.get("sender_name") or o.get("nick_name")
                  or (o.get("sender") or {}).get("nickname") if isinstance(
                      o.get("sender"), dict) else o.get("sender_name") or "") or "未知"
        out.append(f"· {who}：{_body_text(m)}")
    return "\n".join(out)


def status(content: Any, bodies: Any = None) -> dict[str, Any]:
    """卡片状态摘要（供端点/前端判断是否需要补抓）。"""
    c = _as_obj(content)
    ids = expected_ids(c)
    has_inline = complete(c, c.get("inline_content"))
    has_bodies = complete(c, bodies)
    try:
        keys = upload_keys(c)
    except ValueError:
        keys = []
    return {"is_merge_forward": is_merge_forward(c), "title": str(c.get("title") or ""),
            "count": len(ids), "ids": ids,
            "has_inline": has_inline, "has_bodies": has_bodies,
            "can_fetch": bool(keys) and bool(c.get("skey")),
            "upload_parts": len(keys),
            "list_preview": c.get("list_content") if isinstance(
                c.get("list_content"), list) else []}
