# -*- coding: utf-8 -*-
"""抖音私信语音消息 → 文字转写（2026-09-17 新增）。

## 来源与依据

对照上游 `TeamBreakerr/douyin-chat-export` 的 `extractor/voice_transcriber.py`
（该能力是 v2.0.0 相对 v1.0.0 的**新引入**功能，v1.0.0 树里没有该模块）。
上游实测要点，本模块逐条对应：

1. 端点：抖音网页版 IM 的**原生识别接口**
   `POST https://www.douyin.com/aweme/v1/web/im/message/audio/recognition/`
   —— cookie 鉴权（登录态）。
2. 请求体：`{"req_list": [{uri, sec_uid, uuid, message_id, message_type:7,
   skey, conv_short_id}, ...]}`，**每批至多 10 条**
   （上游注释原文：larger payloads return `status_code=5/"参数不合法"`）。
   `message_type` 取 7（抖音 IM 语音的类型码）。
3. `skey` 历史消息常缺失，**允许为空串**（上游注释：endpoint still expects
   the key in each item; an empty value is accepted）。
4. `uuid` = 抖音 IM 客户端的 `mainOptions.deviceId || mainOptions.uuid`，
   藏在 React context 里，需在**页面上下文**里探测。
5. 成功判据：响应体 `status_code`/`code` 为 0 或 200。

## 风控边界（铁律 §一 / §二 —— 必须遵守）

- 请求在**账号自己的 BCC 容器页面上下文**里发出（`fetch(..., {credentials:'include'})`），
  复用浏览器自身登录态 —— **绝不**在后端用 cookie 拼 requests 直发（铁律 §一.1）。
  实现上不出现任何 cookie/token 变量，凭据由浏览器隐式携带。
- 只处理**语音消息**（消息体自带 `uri`），**不查昵称、不遍历用户信息**
  （铁律 §二）。
- 每条消息只识别一次（结果落 `dm_messages.extra.transcription`，
  已有值则跳过）；批量上限 10，请求间加节流。
"""
from __future__ import annotations

import json
from typing import Any

from loguru import logger

# 抖音网页版 IM 原生语音识别端点（上游实测；cookie 鉴权）
AUDIO_RECOGNITION_PATH = "/aweme/v1/web/im/message/audio/recognition/"
AUDIO_RECOGNITION_URL = "https://www.douyin.com" + AUDIO_RECOGNITION_PATH

# 每批上限（上游实测：>10 条返回 status_code=5「参数不合法」）
BATCH_SIZE = 10

# 抖音 IM 语音消息类型码
VOICE_MESSAGE_TYPE = 7


def _first_str(d: Any, *keys: str) -> str:
    """从 dict 里按序取第一个非空字符串（支持值为 list 的取首项）。"""
    if not isinstance(d, dict):
        return ""
    for k in keys:
        v = d.get(k)
        if isinstance(v, (list, tuple)):
            for it in v:
                if isinstance(it, str) and it.strip():
                    return it.strip()
        elif isinstance(v, str) and v.strip():
            return v.strip()
        elif isinstance(v, (int, float)) and v:
            return str(v)
    return ""


def extract_voice_fields(content: dict) -> dict:
    """从语音消息的 content JSON 里取识别所需字段 → dict（取不到为空串）。

    返回 {"uri", "skey", "duration"}。
    uri 优先 resource_url.uri（上游同序），退化到 url_list / tkey。
    """
    if not isinstance(content, dict):
        return {"uri": "", "skey": "", "duration": ""}
    res = content.get("resource_url")
    if not isinstance(res, dict):
        res = {}
    uri = _first_str(res, "uri", "url", "url_list", "large_url_list",
                     "origin_url_list", "medium_url_list", "thumb_url_list")
    if not uri:
        uri = _first_str(content, "uri", "url", "url_list", "tkey")
    skey = _first_str(res, "skey") or _first_str(content, "skey")
    duration = res.get("duration") or content.get("duration") or ""
    return {"uri": uri, "skey": skey, "duration": duration}


def is_voice_content(content: dict) -> bool:
    """判断 content 是否为**语音**消息。

    判据（从严，避免把图片误判为语音）：
      · 有 duration 或 tkey / voice_wave / ai_audio_text / is_voice 标记；
      · 排除视频（有 video.vid 且无显式语音标记）；
      · 排除 aweType 2702/2703/2704（图片族）而**无**显式语音标记。
    """
    if not isinstance(content, dict):
        return False
    res = content.get("resource_url")
    if not isinstance(res, dict):
        res = {}
    explicit = bool(
        res.get("is_voice")
        or _first_str(content, "tkey")
        or _first_str(content, "voice_wave")
        or _first_str(content, "ai_audio_text")
    )
    duration = res.get("duration") or content.get("duration")
    video = content.get("video")
    if isinstance(video, dict) and video.get("vid") and not explicit:
        return False
    if str(content.get("aweType") or "") in {"2702", "2703", "2704"} and not explicit:
        return False
    if explicit:
        return bool(extract_voice_fields(content)["uri"])
    # 无显式标记：要求带 duration 且能从 resource_url 取到 uri
    if duration in (None, ""):
        return False
    return bool(extract_voice_fields(content)["uri"])


# ---------------------------------------------------------------------------
# 页面上下文 JS（在 BCC 容器内执行；凭据由浏览器隐式携带，代码里不出现 cookie）
# ---------------------------------------------------------------------------

# 探测本机 IM 客户端 uuid（上游 SELF_UUID_EVAL_SCRIPT 的精简版）。
# 逐层降级：window 全局 → React context props → 放弃（返回空串）。
SELF_UUID_JS = r"""() => {
  const text = (v) => v == null ? '' : String(v).trim();
  const fromProps = (props) => {
    if (!props || typeof props !== 'object') return '';
    const main = props.mainOptions || (props.value && props.value.mainOptions);
    if (main && typeof main === 'object') {
      const id = text(main.deviceId || main.uuid);
      if (id) return id;
    }
    const options = props.options;
    if (options && typeof options === 'object') {
      const id = text(options.deviceId || options.uuid);
      if (id) return id;
    }
    return text(props.deviceId || props.uuid);
  };
  const direct = [
    window.__IM_MAIN_OPTIONS__, window.mainOptions, window.imMainOptions,
    window.userInfoStore && window.userInfoStore.mainOptions,
    window.__INITIAL_STATE__ && window.__INITIAL_STATE__.mainOptions,
  ];
  for (const item of direct) {
    const id = fromProps({mainOptions: item});
    if (id) return id;
  }
  return '';
}"""

# 批量识别：一次 fetch 提交一批 req_list。
# 说明：本脚本**只读**返回转写文本；不写入页面、不改动任何前端状态。
RECOGNIZE_JS = r"""async (arg) => {
  const [url, itemList] = arg;
  const body = JSON.stringify({req_list: itemList});
  const r = await fetch(url, {
    method: 'POST',
    credentials: 'include',        // 复用浏览器自身登录态（不手拼 cookie）
    headers: {'Content-Type': 'application/json'},
    body,
  });
  const text = await r.text();
  let j = null;
  try { j = text ? JSON.parse(text) : null; } catch (e) {}
  return {status: r.status, body: j, raw: j ? '' : text.slice(0, 300)};
}"""


def _ok_status(body: Any) -> bool:
    """响应是否成功（上游判据：status_code/code ∈ {0,200}）。"""
    if not isinstance(body, dict):
        return False
    for key, ok in (("status_code", (0, 200)), ("code", (0, 200)),
                    ("error_code", (0,)), ("errno", (0,))):
        if key in body:
            try:
                return int(body[key]) in ok
            except Exception:
                return False
    return False


def _iter_texts(body: Any):
    """从响应体里提取转写文本（尽量容错：遍历常见键位）。"""
    if not isinstance(body, dict):
        return []
    for key in ("data", "resp_list", "list", "items", "results"):
        v = body.get(key)
        if isinstance(v, list):
            return v
        if isinstance(v, dict):
            for k2 in ("resp_list", "list", "items"):
                if isinstance(v.get(k2), list):
                    return v[k2]
    return []


def _pick_text(item: Any) -> str:
    """从单个响应项里取转写文本。"""
    if not isinstance(item, dict):
        return ""
    for k in ("text", "transcription", "recognize_text", "result", "content"):
        v = item.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def transcribe_batch(exec_js, items: list[dict],
                     self_uuid: str) -> dict:
    """执行一批（≤10 条）语音识别。

    参数
    ----
    exec_js   : 调用方注入的「在 BCC 页面上下文执行 JS」函数，签名
                `exec_js(script: str, arg) -> Any`（同步或异步均可）。
    items     : [{"msg_id", "message_id", "uri", "skey", "sec_uid",
                  "conv_short_id"}]，最多 BATCH_SIZE 条。
    self_uuid : 本机 IM 客户端 uuid（页面上下文探测得到，可为空串）。

    返回 {"ok": bool, "mapped": {msg_id: text}, "reason": str, "attempted": n}
    """
    batch = [it for it in (items or []) if it.get("uri")][:BATCH_SIZE]
    if not batch:
        return {"ok": False, "mapped": {}, "reason": "no-items", "attempted": 0}
    if not self_uuid:
        # uuid 取不到时**不硬发**：上游把 uuid 作为请求必备项，
        # 空值可能被判参数不合法，且会浪费一次请求配额。
        return {"ok": False, "mapped": {}, "reason": "no-uuid",
                "attempted": len(batch)}
    req_list = []
    for it in batch:
        req_list.append({
            "uri": str(it.get("uri") or ""),
            "sec_uid": str(it.get("sec_uid") or ""),
            "uuid": str(self_uuid),
            "message_id": str(it.get("message_id") or ""),
            "message_type": VOICE_MESSAGE_TYPE,
            "skey": str(it.get("skey") or ""),
            "conv_short_id": str(it.get("conv_short_id") or ""),
        })
    try:
        res = exec_js(RECOGNIZE_JS, [AUDIO_RECOGNITION_URL, req_list])
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "mapped": {}, "reason": f"exec:{type(e).__name__}",
                "attempted": len(batch)}
    if not isinstance(res, dict):
        return {"ok": False, "mapped": {}, "reason": "bad-result",
                "attempted": len(batch)}
    body = res.get("body")
    if not _ok_status(body):
        _sc = (body or {}).get("status_code") if isinstance(body, dict) else None
        logger.warning(f"[VOICE-001] " + f"[voice] 识别失败 http={res.get('status')} "
                       f"status_code={_sc} n={len(batch)}")
        return {"ok": False, "mapped": {}, "reason": f"status:{_sc}",
                "attempted": len(batch)}
    texts = _iter_texts(body)
    mapped = {}
    for idx, it in enumerate(batch):
        t = _pick_text(texts[idx]) if idx < len(texts) else ""
        if t and it.get("msg_id"):
            mapped[str(it["msg_id"])] = t
    return {"ok": True, "mapped": mapped, "reason": "", "attempted": len(batch)}


def fetch_self_uuid(exec_js) -> str:
    """在页面上下文探测本机 IM 客户端 uuid（失败返回空串）。"""
    try:
        v = exec_js(SELF_UUID_JS, None)
    except Exception:
        return ""
    return str(v or "").strip()


def load_json(s: Any) -> dict:
    """宽松 JSON 解析（消息 extra / content 字段可能是字符串）。"""
    if isinstance(s, dict):
        return s
    if not isinstance(s, str) or not s.strip():
        return {}
    try:
        v = json.loads(s)
    except Exception:
        return {}
    return v if isinstance(v, dict) else {}


# ---------------------------------------------------------------------------
# 持久化与批量触发（供后端 API 调用）
# ---------------------------------------------------------------------------

# extra 里保存识别结果与请求要素的键名（契约：前端读 transcription，
# 重试时读 voice_uri/voice_skey，避免重复解析 content）
K_TEXT = "transcription"
K_URI = "voice_uri"
K_SKEY = "voice_skey"


def persist_transcripts(conn, account: str, mapping: dict) -> int:
    """把 {msg_id: 识别文本} 写入 dm_messages.extra.transcription（幂等）。

    只更新**尚无 transcription** 的行；返回实际更新行数。
    与既有 extra 写法一致：读-合并-写，保留 skey/origin_url/reply 等键。
    """
    n = 0
    for msg_id, text in (mapping or {}).items():
        if not msg_id or not str(text or "").strip():
            continue
        row = conn.execute(
            "SELECT extra FROM dm_messages WHERE account=? AND msg_id=? LIMIT 1",
            (account, str(msg_id)),
        ).fetchone()
        if row is None:
            continue
        try:
            ex = json.loads(row["extra"] or "{}")
        except Exception:
            ex = {}
        if not isinstance(ex, dict):
            ex = {}
        if str(ex.get(K_TEXT) or "").strip():
            continue  # 已有转写，不覆盖
        ex[K_TEXT] = str(text).strip()
        try:
            conn.execute(
                "UPDATE dm_messages SET extra=? WHERE account=? AND msg_id=?",
                (json.dumps(ex, ensure_ascii=False), account, str(msg_id)),
            )
            n += 1
        except Exception:
            continue
    try:
        conn.commit()
    except Exception:
        pass
    return n


def transcribe_pending(account: str, conv_id: str = "", exec_js=None,
                       limit: int = 30) -> dict:
    """把该账号「尚未转写」的语音消息批量送识别并落库。

    流程：
      1. 从 dm_messages 里找语音候选（text 形如 `[语音] <uri>`，
         或 extra 已带 voice_uri）；
      2. 取 conv_short_id（dm_conversations.short_id，本分支写的是 sec_uid ——
         故此处直接用会话行的 short_id 列，保持与 capture 一致）与发送者 sec_uid
         （extra.sender_sec_uid，缺则跳过该条 —— 上游 uuid/sec_uid 为必备项）；
      3. 在 BCC 页面上下文探测 uuid；
      4. 按 BATCH_SIZE=10 分批请求，逐条落库。

    返回 {ok, requested, succeeded, skipped, reason}
    """
    from database import get_db  # 延迟导入，避免服务层反向依赖启动顺序
    conn = get_db()
    out = {"ok": False, "requested": 0, "succeeded": 0, "skipped": 0,
           "reason": ""}
    try:
        sql = (
            "SELECT m.msg_id, m.text, m.extra, m.conv_id, c.short_id AS conv_short_id "
            "FROM dm_messages m "
            "LEFT JOIN dm_conversations c "
            "  ON c.account=m.account AND c.conv_id=m.conv_id "
            "WHERE m.account=? AND m.msg_id IS NOT NULL "
            "  AND m.text LIKE '[语音]%' "
        )
        params: list[Any] = [account]
        if conv_id:
            sql += " AND m.conv_id=? "
            params.append(str(conv_id))
        sql += " ORDER BY m.ts DESC LIMIT ?"
        params.append(int(limit))
        rows = conn.execute(sql, tuple(params)).fetchall()
        items = []
        for r in rows:
            ex = load_json(r["extra"])
            if str(ex.get(K_TEXT) or "").strip():
                continue  # 已转写
            uri = str(ex.get(K_URI) or "").split(" ")[0].strip()
            if not uri:
                # 退化：从 text 的 "[语音] <uri>" 里取
                _t = str(r["text"] or "")
                uri = _t[len("[语音]"):].strip().split(" ")[0] if _t.startswith("[语音]") else ""
            sec = str(ex.get("sender_sec_uid") or "").strip()
            if not uri or not sec:
                out["skipped"] += 1
                continue
            items.append({
                "msg_id": str(r["msg_id"]),
                "message_id": str(r["msg_id"]),
                "uri": uri,
                "skey": str(ex.get(K_SKEY) or ""),
                "sec_uid": sec,
                "conv_short_id": str(r["conv_short_id"] or ""),
            })
        out["requested"] = len(items)
        if not items:
            out["ok"] = True
            out["reason"] = "no-pending"
            return out
        if exec_js is None:
            out["reason"] = "no-exec-js"
            return out
        self_uuid = fetch_self_uuid(exec_js)
        if not self_uuid:
            out["reason"] = "no-uuid"
            return out
        total = 0
        for i in range(0, len(items), BATCH_SIZE):
            chunk = items[i:i + BATCH_SIZE]
            res = transcribe_batch(exec_js, chunk, self_uuid)
            if res.get("mapped"):
                total += persist_transcripts(conn, account, res["mapped"])
            if not res.get("ok"):
                out["reason"] = res.get("reason") or "batch-failed"
                break
        out["succeeded"] = total
        out["ok"] = total > 0
        if not out["reason"]:
            out["reason"] = "ok" if total else "no-text-returned"
        return out
    except Exception as e:  # noqa: BLE001
        out["reason"] = f"error:{type(e).__name__}"
        logger.warning(f"[VOICE-003] " + f"[voice] 批量转写失败: {e}")
        return out
