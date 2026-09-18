# -*- coding: utf-8 -*-
"""ChatLab 导出 + 知识库桥（2026-09-17 新增，对照上游 douyin-chat-export）。

## 设计意图

上游把聊天记录导出为 **ChatLab 标准格式**（`https://github.com/hellodigua/ChatLab`），
「可直接导入做 AI 分析」。用户问「ChatLab 导出可以给知识库使用吗」—— 可以，
但正确链路是 **导出 → 导入知识库**（我方已有 `services/kb_import.py` +
`pro_kb` / `reply_kb`），**不是**去实现 ChatLab 的 Pull 服务端（那是为别的软件服务）。

本模块提供两件事（**只读 + 本地文件，不联网**）：

1. `export_chatlab()`  —— 会话 → ChatLab JSON/JSONL；
2. `export_to_kb()`    —— 会话 → 知识库条目（QA / 专业库），落我方 `reply_kb` /
   `pro_kb`，供 AI 回复引用。

## ChatLab 格式（照上游结构，字段名逐一对齐）

```json
{
  "chatlab": {"version": "0.0.2", "exportedAt": <int>, "generator": "..."},
  "meta":    {"name": "与X的对话", "platform": "douyin", "type": "private", "ownerId": "<uid>"},
  "members": [{"platformId": "<uid>", "accountName": "<昵称>"}],
  "messages": [{
      "sender": "<uid>", "accountName": "<昵称>", "timestamp": <秒>,
      "type": <int>, "content": "<text>", "platformMessageId": "<msg_id>",
      "replyToMessageId": "<msg_id>"   // 可选
  }]
}
```

`type` 取值照上游 `CHATLAB_TYPE_MAP`：
`0=TEXT, 1=IMAGE, 5=EMOJI, 24=SHARE, 26=FORWARD, 99=OTHER`
（上游把视频也归 0 并只留时长标签，本模块沿用该「面向分析」的取舍）。

JSONL 形态：首行 `{"_type":"header", ...header}`，其后每行一条消息
（与上游一致，便于流式消费）。

## 与上游的差异（有意）

| 项 | 上游 | 我方 |
|---|---|---|
| 语音转写 | 独立表 `voice_transcriptions` | 同表 `extra.transcription` |
| 引用关系 | `ref_msg` 列 | `extra.reply.ref_msg_id` |
| 分享卡 | `msg_type=4` + `content_json` | `text` 以 `[分享X] ` / `[一起看视频]` 开头 |
| 媒体 | `media_local_path` 已下载文件 | `extra.origin_url` / 解密后 `image_url` |

**只读保证**：导出路径全部 SELECT；知识库写入仅落在 `reply_kb` / `pro_kb`
（用户明确要求的「给知识库使用」），不碰 `dm_*` 表。
"""
from __future__ import annotations

import io
import json
import os
import re
import time
from pathlib import Path

from loguru import logger

CHATLAB_VERSION = "0.0.2"
GENERATOR = "DYAutoDM_v2"

# 照上游 CHATLAB_TYPE_MAP（面向分析的取舍：视频归 TEXT，时长写进正文）
_TYPE_TEXT = 0
_TYPE_IMAGE = 1
_TYPE_EMOJI = 5
_TYPE_SHARE = 24
_TYPE_FORWARD = 26
_TYPE_OTHER = 99


def _chatlab_type(msg_type: str, text: str) -> int:
    """我方 msg_type / 文本形态 → ChatLab type。"""
    mt = str(msg_type or "").lower()
    t = (text or "").strip()
    if t.startswith("[图片]") or mt in ("27", "image"):
        return _TYPE_IMAGE
    if t.startswith("[表情包]") or mt in ("5", "sticker"):
        return _TYPE_EMOJI
    if t.startswith("[分享") or t.startswith("[一起看视频]") or mt in ("8", "video", "share"):
        return _TYPE_SHARE if not t.startswith("[分享视频]") else _TYPE_SHARE
    if t.startswith("[合并转发"):
        return _TYPE_FORWARD
    if mt in ("17", "voice"):
        return _TYPE_TEXT          # 与上游一致：语音保留时长/转写文本，type=TEXT
    if mt in ("50001", "read_receipt"):
        return _TYPE_OTHER
    return _TYPE_TEXT


def _safe_name(s: str) -> str:
    """文件名安全化（照上游：剔除 Windows 非法字符 + 保留名）。"""
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", str(s or "")).strip(" .")
    if s.upper().split(".")[0] in {
        "CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4", "COM5",
        "COM6", "COM7", "COM8", "COM9", "LPT1", "LPT2", "LPT3", "LPT4", "LPT5",
        "LPT6", "LPT7", "LPT8", "LPT9",
    }:
        s = "_" + s
    return s or "conversation"


def build_filename(conv_name: str, fmt: str, ts: int | None = None) -> str:
    """`<会话名>_<YYYYMMDDHHMMSS>_export.<ext>`（照上游命名约定）。"""
    ts = ts or int(time.time())
    ext = "json" if fmt == "json" else "jsonl"
    stamp = time.strftime("%Y%m%d%H%M%S", time.localtime(ts))
    return f"{_safe_name(conv_name)}_{stamp}_export.{ext}"


def _load_msgs(conn, account: str, conv_id: str) -> tuple[dict, list]:
    """取会话元信息 + 按稳定顺序取消息（与聊天页同排序口径）。"""
    row = conn.execute(
        "SELECT conv_id, peer_id, peer_name, conv_type FROM dm_conversations "
        "WHERE account=? AND conv_id=?", (account, str(conv_id))).fetchone()
    if row is None:
        raise ValueError("会话不存在")
    msgs = conn.execute(
        "SELECT msg_id, role, text, msg_type, extra, ts FROM dm_messages "
        "WHERE account=? AND conv_id=? AND msg_type <> '50001' "
        "  AND text <> '[分享视频]' "
        "ORDER BY CASE WHEN json_extract(NULLIF(extra,''),'$.created_at_us') IS NOT NULL"
        " THEN CAST(json_extract(NULLIF(extra,''),'$.created_at_us') AS INTEGER)"
        " ELSE CAST(ts*1000000 AS INTEGER) END ASC, ts ASC",
        (account, str(conv_id))).fetchall()
    return {"conv_id": row["conv_id"], "peer_id": row["peer_id"],
            "peer_name": row["peer_name"]}, list(msgs)


def default_export_dir(sub: str = "chatlab") -> Path:
    """默认导出目录：`<app_root>/exports/<sub>`（2026-09-17 乙方案）。

    桌面应用没有「服务端路径」语义 —— 用户点导出应直接拿到文件，
    故 `dest_dir` 留空时落到此默认目录，再由下载端点交给前端。
    """
    try:
        import vbrowser
        root = Path(vbrowser.app_root())
    except Exception:
        root = Path(os.environ.get("DY_APP_ROOT") or os.getcwd())
    d = root / "exports" / sub
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe_export_file(filename: str, sub: str = "chatlab") -> Path | None:
    """把文件名解析为导出目录内的安全路径；不存在/越界返回 None。

    ⚠️ **不能**用 ASCII 白名单：导出文件名含**中文昵称**
    （`build_filename` 用会话名命名，如 `四川工伤-张老师_..._export.jsonl`），
    白名单会把它一律拒绝 → 下载恒 404（实测踩中）。
    故改为「拒绝路径分隔与上跳 + **以 resolve 后的目录包含性为准**（权威判据）」。
    """
    name = (filename or "").strip()
    if not name or name in (".", ".."):
        return None
    # 显式拒绝路径分隔符与 NUL（跨平台：/ 与 \ 都拦）
    if any(c in name for c in ("/", "\\", "\x00")):
        return None
    base = default_export_dir(sub).resolve()
    p = (base / name).resolve()
    try:
        p.relative_to(base)          # ★ 权威判据：必须仍在导出目录内
    except ValueError:
        return None
    return p if p.is_file() else None


def export_chatlab(account: str, conv_id: str, dest_dir: str, *,
                   fmt: str = "jsonl", db_path: str | None = None) -> dict:
    """导出单个会话为 ChatLab 格式（JSON 或 JSONL）。**只读**。

    `dest_dir` 留空 → 落到 `default_export_dir()`（桌面默认导出目录）。
    返回 `{ok, path, filename, format, messages, members}`。
    """
    from database import get_db
    if fmt not in ("json", "jsonl"):
        raise ValueError("fmt 只能是 json 或 jsonl")
    conn = get_db()
    conv, msgs = _load_msgs(conn, account, conv_id)
    name = conv["peer_name"] or conv["peer_id"] or conv["conv_id"]
    # 2026-09-17（群聊支持）：会话类型照上游 exporter ——
    #   `"type": "group" if row["conv_type"] == 2 else "private"`，
    #   且群聊额外给出 meta.groupId（上游同款字段）。
    # 2026-09-18：收敛到 conv_identity.conv_type（唯一实现，勿再内联重写）
    from services.conv_identity import conv_type as _conv_type
    _ct = _conv_type(conv_id)
    try:
        _ct = int(conv["conv_type"] or _ct)
    except Exception:
        pass
    is_group = _ct == 2

    # 成员表：me 用 self 占位 uid（我方无 owner uid 的可读名时用「我」）
    members_map: dict[str, str] = {}
    for m in msgs:
        uid = "me" if m["role"] == "me" else str(conv["peer_id"] or "peer")
        members_map.setdefault(uid, "我" if uid == "me" else str(name))
    members = [{"platformId": u, "accountName": n} for u, n in members_map.items()]

    out_msgs = []
    for m in msgs:
        ex = {}
        try:
            ex = json.loads(m["extra"] or "{}")
        except Exception:
            ex = {}
        if not isinstance(ex, dict):
            ex = {}
        text = m["text"] or ""
        # 语音：附转写（我方在同表 extra.transcription）
        trans = str(ex.get("transcription") or "").strip()
        if trans and not text.startswith("[语音]"):
            text = f"{text}\n[转写] {trans}"
        elif trans and text.startswith("[语音]"):
            text = f"[语音]\n[转写] {trans}"
        uid = "me" if m["role"] == "me" else str(conv["peer_id"] or "peer")
        item = {
            "sender": uid,
            "accountName": "我" if uid == "me" else str(name),
            "timestamp": float(m["ts"] or 0),
            "type": _chatlab_type(m["msg_type"], text),
            "content": text,
            "platformMessageId": m["msg_id"],
        }
        rep = ex.get("reply") if isinstance(ex.get("reply"), dict) else None
        if rep and rep.get("ref_msg_id"):
            item["replyToMessageId"] = str(rep["ref_msg_id"])
        out_msgs.append(item)

    header = {
        "chatlab": {"version": CHATLAB_VERSION, "exportedAt": int(time.time()),
                    "generator": GENERATOR},
        "meta": {"name": (f"群聊 {name}" if is_group else f"与{name}的对话"),
                 "platform": "douyin",
                 "type": "group" if is_group else "private",
                 "ownerId": "me"},
    }
    if is_group:
        # 上游同款：群聊额外标注群 ID
        header["meta"]["groupId"] = str(conv_id)
    d = Path(dest_dir) if (dest_dir or "").strip() else default_export_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = d / build_filename(str(name), fmt)
    if fmt == "json":
        with io.open(path, "w", encoding="utf-8") as f:
            json.dump({**header, "members": members, "messages": out_msgs},
                      f, ensure_ascii=False, indent=1)
    else:
        with io.open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"_type": "header", **header},
                               ensure_ascii=False) + "\n")
            for item in out_msgs:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
    logger.info(f"[CLE-001] " + f"ChatLab 导出: {path.name} 消息 {len(out_msgs)} 条")
    return {"ok": True, "path": str(path), "filename": path.name, "format": fmt,
            "messages": len(out_msgs), "members": len(members)}


# ---------------------------------------------------------------------------
# 知识库桥：会话 → 知识库条目
# ---------------------------------------------------------------------------

# 问句特征（用于把「对方提问 + 我方作答」配对成 QA）
_ASK_RE = re.compile(r"[?？]$|^(请问|怎么|如何|多少|能不能|可以|是否|为什么|什么|哪)")


def extract_qa_pairs(msgs: list, *, max_pairs: int = 200,
                     min_answer_len: int = 2) -> list[dict]:
    """把「对方提问 → 我方紧邻作答」的相邻消息配对成 QA 候选。

    规则（保守，宁缺勿错）：
      · 问句：`role=them` 且命中提问特征，或纯文本以「？」结尾；
      · 答案：其后**最近一条** `role=me` 的纯文本消息（跳过我方富媒体/系统提示）；
      · 答案长度 ≥ `min_answer_len`，且不含媒体/系统占位前缀。
    """
    out: list[dict] = []
    n = len(msgs)
    for i, m in enumerate(msgs):
        if m["role"] != "them":
            continue
        q = (m["text"] or "").strip()
        if not q or q.startswith(("[图片]", "[表情包]", "[分享", "[语音]", "[未知媒体]", "[系统提示]")):
            continue
        if not (_ASK_RE.search(q) or len(q) <= 24):
            # 短句也当提问（私信里常见「工伤咋赔」这类无问号短问）
            continue
        # 找后继我方文本
        ans = ""
        for j in range(i + 1, min(i + 6, n)):
            mj = msgs[j]
            if mj["role"] != "me":
                continue
            t = (mj["text"] or "").strip()
            if not t or t.startswith(("[图片]", "[表情包]", "[分享", "[语音]",
                                      "[未知媒体]", "[系统提示]")):
                continue
            ans = t
            break
        if len(ans) < min_answer_len:
            continue
        out.append({"question": q, "answer": ans,
                    "source_msg_id": m["msg_id"], "ts": m["ts"]})
        if len(out) >= max_pairs:
            break
    return out


def export_to_kb(account: str, conv_id: str = "", *, target: str = "reply",
                 max_items: int = 50, db_path: str | None = None) -> dict:
    """把会话中的 QA 对导成知识库条目。

    target="reply" → `services.reply_kb`（命中即回，零 token）
    target="pro"   → `services.pro_kb`（RAG 参考资料）

    **写入范围**：仅 `reply_kb` / `pro_kb`；不碰 `dm_*` 聊天表。
    返回 `{ok, added, skipped, target}`。
    """
    from database import get_db
    if target not in ("reply", "pro"):
        raise ValueError("target 只能是 reply 或 pro")
    conn = get_db()
    if conv_id:
        _conv, msgs = _load_msgs(conn, account, conv_id)
    else:
        rows = conn.execute(
            "SELECT msg_id, role, text, msg_type, extra, ts FROM dm_messages "
            "WHERE account=? AND msg_type <> '50001' ORDER BY ts ASC",
            (account,)).fetchall()
        msgs = list(rows)
    pairs = extract_qa_pairs(msgs)[:max_items]

    added = 0
    skipped = 0
    if target == "reply":
        from services import reply_kb
        existing = {(str(it.get("question") or "").strip())
                    for it in (reply_kb.list_items() or [])}
        for p in pairs:
            if p["question"] in existing:
                skipped += 1
                continue
            try:
                reply_kb.add_item(p["question"], p["answer"], source="chatlab")
                existing.add(p["question"])
                added += 1
            except Exception as e:  # noqa: BLE001
                logger.debug(f"[CLE-002] reply_kb 写入失败: {e}")
                skipped += 1
    else:
        from services import pro_kb
        existing = {(str(it.get("topic") or "").strip())
                    for it in (pro_kb.list_items(include_deleted=True) or [])}
        for p in pairs:
            topic = p["question"][:40]
            if topic in existing:
                skipped += 1
                continue
            try:
                pro_kb.add_item(topic=topic, category="聊天记录导入",
                                content=f"问：{p['question']}\n答：{p['answer']}")
                existing.add(topic)
                added += 1
            except Exception as e:  # noqa: BLE001
                logger.debug(f"[CLE-003] pro_kb 写入失败: {e}")
                skipped += 1
    logger.info(f"[CLE-004] " + f"知识库导入({target}): 新增 {added} 跳过 {skipped}"
                f"（候选 {len(pairs)}）")
    return {"ok": True, "added": added, "skipped": skipped, "target": target,
            "candidates": len(pairs)}
