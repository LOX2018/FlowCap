"""私信全库检索与逐日统计（2026-09-17 新增，对照上游 douyin-chat-export）。

## 设计意图

上游开放 API 提供两件我方此前没有的**只读**能力（`backend/database.search_messages`
+ `GET /api/conversations/{id}/stats/daily`）：

  · **全库/会话内检索** —— 文本、时间区间、媒体类型三个维度；
  · **逐日消息量** —— 供「日历/月份跳转」定位某天首条消息。

我方复用点：
  · 「会话内搜索 + 引用跳转」：聊天气泡里的引用块点击 → 定位到被引用消息；
  · 「日历跳转」：按日统计 → 点某天 → 跳到那天第一条。

## 与上游的差异（有意为之，不照抄）

| 项 | 上游 | 我方 |
|---|---|---|
| 消息表 | `messages.content` + `raw_data` JSON | `dm_messages.text` + `extra` JSON |
| 媒体判定 | `media_local_path` / `msg_type=3/5` | `msg_type`('27'/'8') + `extra.origin_url`/`video_*` |
| 时间区间 | `[start, end)` 半开 | **同样半开** `[start, end)`（相邻日不重叠） |
| 语音转写 | `voice_transcriptions.text_result` 独立表 | 我方存 `extra.transcription`（同表，少一次 JOIN） |

**只读保证**：全部为 `SELECT`，无任何写操作；不触网、不查用户信息（不涉昵称红线）。
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from loguru import logger

# 媒体类型判定（与 api/messages._front_type 的类型码口径一致）
#   '27' = 图片；'8' = 分享视频；视频也可能以 extra 里的 video_* 出现
_IMAGE_TYPES = ("27", "image")
_VIDEO_TYPES = ("8", "video")


def _esc_like(s: str) -> str:
    """转义 LIKE 通配符（用户输入里的 % _ \\ 不得当通配符）。"""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _media_clause(media_type: str) -> tuple[str, list]:
    """媒体类型 → SQL 片段 + 参数。

    图片：msg_type 属图片族（27/image）**且**不带视频标记；
    视频：msg_type 属视频族（8/video）**或** extra 里含 video 标记（vid/url）；
    media：两者并集。
    用 `json_extract` 取 extra，非 JSON 行由 `NULLIF/''` 兜住（不报错）。
    """
    _v_json = "json_extract(NULLIF(extra,''),'$.video')"
    _is_video = (
        # ADR-012 / R8-6（方案 B）：只认注册的 "8"。
        # 'video' 从未被任何写入点使用（防御性猜测），按单一名字原则移除。
        "msg_type = '8'"
        f" OR {_v_json} IS NOT NULL"
        " OR json_extract(NULLIF(extra,''),'$.video_url') IS NOT NULL"
    )
    # ADR-012 / R8-2（方案 B）：图片 msg_type 单一名字 = "27"。
    # 原 `IN ('27','image')` 是对「同一语义两个名字」的容忍，属补丁式写法；
    # 发送侧已统一写 "27"，历史库实测 0 条 'image'，故此处的别名一并移除。
    _is_image = f"msg_type = '27' AND NOT ({_is_video})"
    if media_type == "image":
        return f"({_is_image})", []
    if media_type == "video":
        return f"({_is_video})", []
    if media_type == "media":
        return f"(({_is_image}) OR ({_is_video}))", []
    raise ValueError("未知媒体类型")


def search_messages(account: str, query: str = "", *, conv_id: str | None = None,
                    start_time: float | None = None, end_time: float | None = None,
                    media_type: str | None = None, page: int = 1,
                    page_size: int = 50) -> dict:
    """在全库（或指定会话）内检索消息。

    参数
    ----
    account     : 账号（必填，数据按账号隔离）
    query       : 关键词（空则按时间/媒体筛选；全空则拒绝）
    conv_id     : 限定会话
    start_time  : 起始 ts（秒，含）
    end_time    : 结束 ts（秒，**不含** —— 半开区间，相邻日期不重叠）
    media_type  : image | video | media
    page/page_size : 分页

    返回 `{items, total, page, page_size}`；`items` 每项含
    `{conv_id, conv_name, msg_id, role, text, msg_type, ts, snippet, media_type}`。
    """
    from database import get_db  # 延迟导入，避免服务层反向依赖
    if not account:
        raise ValueError("account 必填")
    if not (query or "").strip() and not conv_id and start_time is None \
            and end_time is None and not media_type:
        raise ValueError("请指定搜索条件")
    if start_time is not None and end_time is not None and start_time >= end_time:
        raise ValueError("结束时间必须晚于开始时间")

    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 50), 200))

    clauses: list[str] = ["m.account = ?"]
    params: list[Any] = [account]

    q = (query or "").strip()
    if q:
        pat = "%" + _esc_like(q) + "%"
        # 检索面：正文 + 引用正文 + 语音转写 + 分享卡标题
        fields = [
            "m.text",
            "json_extract(NULLIF(m.extra,''),'$.transcription')",
            "json_extract(NULLIF(m.extra,''),'$.reply.text')",
            "json_extract(NULLIF(m.extra,''),'$.share_title')",
        ]
        clauses.append("(" + " OR ".join(
            f"{f} LIKE ? ESCAPE '\\'" for f in fields) + ")")
        params.extend([pat] * len(fields))

    if conv_id:
        clauses.append("m.conv_id = ?")
        params.append(str(conv_id))
    if start_time is not None:
        clauses.append("m.ts >= ?")
        params.append(float(start_time))
    if end_time is not None:
        clauses.append("m.ts < ?")
        params.append(float(end_time))
    if media_type:
        mc, mp = _media_clause(media_type)
        clauses.append(mc)
        params.extend(mp)

    # 与聊天页一致的噪音过滤（否则搜出来点不开：前端本就不显示这些行）
    # ⚠️ 判据语义（★ 2026-10-03 P5 修正）：**排除**「上游码=7 且 msg_id 为空」
    #   的回查帧 —— 注意是 AND（两者同时成立才排除），不是 OR。
    #   🔴 我曾误写成 `(msg_code IS NULL OR msg_code = '7')`：msg_code 为 NULL
    #   时该子句**恒真** ⇒ 全部正常消息被排除 ⇒ 搜索恒返回 0 条（实测 10 项红）。
    #   正确写法：NULL 视为「非 7」，用 COALESCE 统一兜底。
    clauses.append("(m.msg_code IS NULL OR m.msg_code <> '50001') "
                   "AND (m.msg_type IS NULL OR m.msg_type <> '50001')")
    clauses.append("NOT (COALESCE(m.msg_code, m.msg_type) = '7' "
                   "AND m.msg_id IS NULL)")
    clauses.append("m.text NOT LIKE '[未知媒体]%'")
    clauses.append("m.text <> '[分享视频]'")

    where = " AND ".join(clauses)
    conn = get_db()
    try:
        total = conn.execute(
            f"SELECT COUNT(*) AS n FROM dm_messages m WHERE {where}", tuple(params)
        ).fetchone()["n"]
        rows = conn.execute(
            "SELECT m.msg_id, m.conv_id, m.role, m.text, m.msg_type, m.ts, m.extra,"
            "       c.peer_name AS conv_name "
            "FROM dm_messages m "
            "LEFT JOIN dm_conversations c "
            "  ON c.account = m.account AND c.conv_id = m.conv_id "
            f"WHERE {where} "
            # 排序与聊天页同口径：created_at_us 优先，缺失时 ts×1e6 量级对齐
            "ORDER BY CASE WHEN json_extract(NULLIF(m.extra,''),'$.created_at_us')"
            " IS NOT NULL"
            " THEN CAST(json_extract(NULLIF(m.extra,''),'$.created_at_us') AS INTEGER)"
            " ELSE CAST(m.ts * 1000000 AS INTEGER) END ASC, m.ts ASC "
            "LIMIT ? OFFSET ?",
            tuple(params) + (page_size, (page - 1) * page_size),
        ).fetchall()
    except sqlite3.Error as e:
        logger.warning(f"[MSG-030] " + f"搜索执行失败: {type(e).__name__}: {e}")
        return {"items": [], "total": 0, "page": page, "page_size": page_size,
                "error": "query_failed"}

    items = []
    for r in rows:
        ex = {}
        try:
            ex = json.loads(r["extra"] or "{}")
        except Exception:
            ex = {}
        if not isinstance(ex, dict):
            ex = {}
        mt = str(r["msg_type"] or "")
        is_video = mt in _VIDEO_TYPES or bool(
            ex.get("video") or ex.get("video_url"))
        _mtype = "video" if is_video else ("image" if mt in _IMAGE_TYPES else "")
        text = r["text"] or ""
        items.append({
            "conv_id": r["conv_id"],
            "conv_name": r["conv_name"] or r["conv_id"],
            "msg_id": r["msg_id"],
            "role": r["role"],
            "text": text,
            "msg_type": r["msg_type"],
            "ts": r["ts"],
            "media_type": _mtype,
            "snippet": _snippet(text, q),
        })
    return {"items": items, "total": int(total or 0), "page": page,
            "page_size": page_size}


def _snippet(text: str, query: str, width: int = 60) -> str:
    """给命中位置生成上下文摘要（前端可据此高亮）。"""
    t = (text or "").strip()
    if not t:
        return ""
    if not query:
        return t[:width]
    low = t.lower()
    i = low.find(query.lower())
    if i < 0:
        return t[:width]
    lo = max(0, i - width // 3)
    hi = min(len(t), i + len(query) + width)
    return ("…" if lo > 0 else "") + t[lo:hi] + ("…" if hi < len(t) else "")


def daily_stats(account: str, conv_id: str, tz_hours: int = 8) -> dict:
    """逐日消息量统计（供日历/月份跳转定位）。

    返回 `{days: [{"date": "YYYY-MM-DD", "count": n, "first_msg_id": ..., "first_ts": ...}],
             total: n, bounds: {"min": ts, "max": ts}}`

    `tz_hours` 偏移（默认 +8 中国时区）；`first_msg_id` 供前端「跳到那天第一条」。
    """
    from database import get_db
    if not account or not conv_id:
        raise ValueError("account 与 conv_id 必填")
    try:
        tz_off = int(tz_hours)
    except Exception:
        tz_off = 8
    tz_off = max(-12, min(tz_off, 14))
    conn = get_db()
    rows = conn.execute(
        "SELECT msg_id, ts FROM dm_messages "
        "WHERE account=? AND conv_id=? AND ts > 0 "
        "  AND (msg_code IS NULL OR msg_code <> '50001') AND (msg_type IS NULL OR msg_type <> '50001') "
        "  AND text NOT LIKE '[未知媒体]%' AND text <> '[分享视频]' "
        "ORDER BY ts ASC",
        (account, str(conv_id)),
    ).fetchall()
    off = tz_off * 3600
    days: dict[str, dict] = {}
    for r in rows:
        ts = float(r["ts"] or 0)
        if ts <= 0:
            continue
        import time as _t
        key = _t.strftime("%Y-%m-%d", _t.gmtime(ts + off))
        d = days.get(key)
        if d is None:
            days[key] = {"date": key, "count": 1,
                         "first_msg_id": r["msg_id"], "first_ts": ts}
        else:
            d["count"] += 1
    out = sorted(days.values(), key=lambda x: x["date"])
    _ts = [float(r["ts"]) for r in rows if r["ts"]]
    return {
        "days": out,
        "total": len(rows),
        "bounds": {"min": min(_ts) if _ts else 0, "max": max(_ts) if _ts else 0},
        "tz": tz_off,
    }
