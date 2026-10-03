# -*- coding: utf-8 -*-
"""聊天长图渲染 + 开放 API 数据整形（2026-09-17 新增，对照上游 douyin-chat-export）。

## 两件事

1. **长图渲染**（上游 `GET .../screenshot` → PNG）：把任意消息区间渲染成一张聊天
   界面长图，可选标题栏与主题，**全程本地渲染，内容不出机器**。
   上游用「无头浏览器打开内置界面截图模式」实现；我方采用**同思路但更轻**的做法：
   生成**自包含 HTML**（内联样式 + 消息气泡），交给浏览器 `print`/截图即可，
   **不引入新的服务端依赖**（不装 png 编码库、不启无头浏览器）。

   ⚠️ 设计取舍（写明，便于后续替换）：上游用 Playwright 截图直出 PNG；
   我方先产出 HTML（PNG 需调用方用浏览器另存/打印）。若后续要「服务端直出 PNG」，
   接法是在 BCC 容器里 `page.screenshot()`（复用既有浏览器，零新依赖）——
   属下一阶段，本模块先把**内容与排版**这部分确定性工作做掉。

2. **开放 API 数据整形**：上游开放 API 暴露 `by-date` / `range` / `daily` /
   `users` 等只读视图。本模块提供与 UI 无关的**纯数据整形函数**，
   由 `api/messages.py` 的只读端点复用（便于单测）。

## 安全契约

· 长图渲染**只读**：仅 SELECT 消息 + 拼 HTML；HTML 中的文本**必须转义**
  （防注入：消息内容可能含 `<script>`，历史数据来自不可信来源）。
· 开放 API 视图**只读**；不暴露密钥类字段（extra 里的 skey 等一律不进视图）。
"""
from __future__ import annotations

import html
import json
import time
from typing import Any

from loguru import logger
# 2026-09-30：时间格式 SSOT（日期分割线 / 时分不再裸切片）
from services.message_time import mt_date, mt_time

# 主题（照上游 5 套：dark / wechat / light / warm / purple）
THEMES: dict[str, dict[str, str]] = {
    "dark":   {"bg": "#14161a", "fg": "#e9ecef", "self": "#3b6fd4", "self_fg": "#ffffff",
               "peer": "#23262c", "peer_fg": "#e9ecef", "meta": "#8b93a1"},
    "wechat": {"bg": "#ededed", "fg": "#111111", "self": "#95ec69", "self_fg": "#111111",
               "peer": "#ffffff", "peer_fg": "#111111", "meta": "#7a7a7a"},
    "light":  {"bg": "#ffffff", "fg": "#1a1a1a", "self": "#d6e4ff", "self_fg": "#10233f",
               "peer": "#f2f3f5", "peer_fg": "#1a1a1a", "meta": "#888888"},
    "warm":   {"bg": "#fbf6f0", "fg": "#3a2f28", "self": "#f3c894", "self_fg": "#3a2f28",
               "peer": "#ffffff", "peer_fg": "#3a2f28", "meta": "#a08c7a"},
    "purple": {"bg": "#171326", "fg": "#eae6ff", "self": "#6b4bd6", "self_fg": "#ffffff",
               "peer": "#241d3d", "peer_fg": "#eae6ff", "meta": "#9a92c4"},
}

MAX_SPAN = 2000          # 区间跨度上限（照上游 screenshot 的 2000 上限）
DEFAULT_WIDTH = 520
DEFAULT_SCALE = 2


def _esc(s: Any) -> str:
    """HTML 转义（**必做**：消息文本来自不可信来源）。"""
    return html.escape(str(s or ""), quote=True)


def _fmt_ts(ts: float) -> str:
    """时间戳 → 契约串 `YYYY-MM-DD HH:MM:SS`（委托 SSOT）。

    2026-09-30：旧实现用 `%Y-%m-%d %H:%M`（无秒），且 `float(ts or 0)`
    会把 ts 缺失映射成 **1970-01-01 08:00**，导出图/HTML 里出现「1970 年」
    的假分割线。现委托 `services.message_time.fmt_mt`：
    格式统一为带秒的契约串，且缺失 ⇒ 空串（零信息，不谎报）。
    """
    from services.message_time import fmt_mt
    return fmt_mt(ts)


def fetch_range(account: str, conv_id: str, start_seq: int | None = None,
                end_seq: int | None = None, *, limit: int = MAX_SPAN,
                db_path: str | None = None) -> dict:
    """按**消息序号区间**取消息（照上游 `by_seq` 语义）。

    我方 `dm_messages` 没有 seq 列，故用「按稳定顺序的行号」当 seq
    （排序口径与聊天页一致：created_at_us 优先，缺失按 ts×1e6 量级对齐）。
    返回 `{conv, messages[], seq_range}`；`messages[i]` 带 `seq` 字段。
    """
    # 2026-09-18 审查修复（LOW→实修）：`db_path` 参数此前被线程化到本函数却**从不使用**，
    # 调用方传具体库路径时被静默忽略（误导）。现真正生效：显式给出路径则用独立连接，
    # 否则沿既有单例（保持默认行为逐字不变）。
    if db_path:
        import sqlite3 as _sq3
        conn = _sq3.connect(str(db_path))
        conn.row_factory = _sq3.Row
    else:
        from database import get_db
        conn = get_db()
    limit = max(1, min(int(limit or MAX_SPAN), MAX_SPAN))
    row = conn.execute(
        "SELECT conv_id, peer_id, peer_name FROM dm_conversations "
        "WHERE account=? AND conv_id=?", (account, str(conv_id))).fetchone()
    if row is None:
        raise ValueError("会话不存在")
    rows = conn.execute(
        "SELECT rowid AS rid, msg_id, role, text, msg_type, extra, ts FROM dm_messages "
        "WHERE account=? AND conv_id=? AND (msg_code IS NULL OR msg_code <> '50001') AND (msg_type IS NULL OR msg_type <> '50001') "
        # 2026-09-18（E1/E2）：过滤口径与 `/conversations/{id}` 详情端点**完全一致** ——
        # 否则详情下发的 seq 与渲染端点的 seq 会错位（选区导出错条）。
        # 附带修正：此前导出长图会把系统引导噪音也画进去。
        "  AND NOT (msg_type = '7' AND msg_id IS NULL) "
        "  AND text NOT LIKE '%对方回复你或互关之前%' "
        "  AND text NOT LIKE '%请礼貌发言%' "
        "  AND text NOT LIKE '%自觉遵守%' "
        "  AND text <> '[分享视频]' AND text NOT LIKE '[未知媒体]%' "
        "  AND text NOT LIKE 'https://www.iesdouyin.com/share/%' "
        "ORDER BY CASE WHEN json_extract(NULLIF(extra,''),'$.created_at_us') IS NOT NULL"
        " THEN CAST(json_extract(NULLIF(extra,''),'$.created_at_us') AS INTEGER)"
        " ELSE CAST(ts*1000000 AS INTEGER) END ASC, ts ASC",
        (account, str(conv_id))).fetchall()

    msgs: list[dict] = []
    for i, r in enumerate(rows):
        ex = {}
        try:
            ex = json.loads(r["extra"] or "{}")
        except Exception:
            ex = {}
        if not isinstance(ex, dict):
            ex = {}
        msgs.append({
            "seq": i + 1,
            "msg_id": r["msg_id"],
            "role": r["role"],
            "dir": "out" if r["role"] == "me" else "in",
            "text": r["text"] or "",
            "msg_type": r["msg_type"],
            "ts": float(r["ts"] or 0),
            "time": _fmt_ts(r["ts"]),
            "transcription": str(ex.get("transcription") or "") or None,
            "recalled": bool(int(ex.get("is_recalled") or 0)),
        })
    lo = int(start_seq) if start_seq else 1
    hi = int(end_seq) if end_seq else (len(msgs) or 1)
    # DbC 前置条件：区间必须有序。若不校验，start>end 会**静默返回空区间**
    # （画出一张只有标题的空图），调用方无从分辨「区间为空」与「参数写反」。
    if lo > hi:
        raise ValueError(f"start_seq({lo}) 不能大于 end_seq({hi})")
    picked = [m for m in msgs if lo <= m["seq"] <= hi][:limit]
    return {"conv": {"conv_id": row["conv_id"], "name":
                     row["peer_name"] or row["peer_id"] or row["conv_id"]},
            "messages": picked,
            "seq_range": {"start": lo, "end": hi, "total": len(msgs)}}


def by_date(account: str, conv_id: str, date: str, tz_hours: int = 8,
            db_path: str | None = None) -> dict:
    """某自然日的全部消息（照上游 `by-date?date=YYYY-MM-DD&tz=8`）。"""
    import datetime as _dt
    try:
        d0 = _dt.datetime.strptime(date, "%Y-%m-%d")
    except Exception:
        raise ValueError("date 需为 YYYY-MM-DD")
    off = max(-12, min(int(tz_hours or 8), 14)) * 3600
    start = d0.timestamp() - off
    end = start + 86400
    r = fetch_range(account, conv_id, db_path=db_path)
    # 2026-09-18 审查修复（MEDIUM）：`fetch_range` 默认 `limit=MAX_SPAN(2000)`，
    # 会把**前 2000 条**切出来再按时间过滤 → 消息数 >2000 的会话「按日期渲染」
    # **静默缺数据**（缺哪一天取决于 seq 顺序，用户无从察觉）。
    # 该场景在当前架构下无正确解（seq 必须基于全量计算），故按 DbC：宁显式失败。
    total = int((r.get("seq_range") or {}).get("total") or 0)
    if total > MAX_SPAN:
        raise ValueError(
            f"会话消息数 {total} 超过单次渲染上限 {MAX_SPAN}，按日期渲染会缺失数据；"
            f"请改用选区/区间渲染（start_seq/end_seq）")
    return {**r, "date": date,
            "messages": [m for m in r["messages"] if start <= m["ts"] < end]}


def messages_for_view(account: str, conv_id: str, start_seq: int | None = None,
                      end_seq: int | None = None, *, self_uid: str = "",
                      db_path: str | None = None) -> dict:
    """**开放 API 视图**：不含 extra 敏感键（skey/origin_url 等一律剔除）。

    与 `fetch_range` 的差别：更窄的字段集，专供外部程序消费。
    """
    r = fetch_range(account, conv_id, start_seq, end_seq, db_path=db_path)
    out = []
    for m in r["messages"]:
        out.append({
            "seq": m["seq"],
            "msg_id": m["msg_id"],
            "from": "self" if (self_uid and m["role"] == "me") else m["role"],
            "dir": m["dir"],
            "type": m["msg_type"],
            "text": m["text"],
            "timestamp": m["ts"],
            "time": m["time"],
            "recalled": m["recalled"],
        })
    return {"conv": r["conv"], "messages": out,
            "seq_range": r["seq_range"]}


def render_html(account: str, conv_id: str, start_seq: int | None = None,
                end_seq: int | None = None, *, theme: str = "dark",
                title: str = "", subtitle: str = "", self_uid: str = "",
                width: int = DEFAULT_WIDTH, scale: float = DEFAULT_SCALE,
                db_path: str | None = None) -> str:
    """渲染自包含 HTML 长图（内联样式，可直接浏览器打印/截图）。

    **转义已强制**（`_esc`），消息里的 `<script>` 不会被执行。
    """
    th = THEMES.get(str(theme or "dark")) or THEMES["dark"]
    # ⚠️ 必须用 fetch_range（含 transcription/recalled），**不能**用
    #    messages_for_view —— 后者是「开放 API 视图」，刻意剥掉了这些字段。
    r = fetch_range(account, conv_id, start_seq, end_seq, db_path=db_path)
    w = max(240, min(int(width or DEFAULT_WIDTH), 1600))
    sc = max(1.0, min(float(scale or DEFAULT_SCALE), 4.0))
    conv_name = _esc(r["conv"]["name"])
    head = ""
    if title or subtitle:
        head = (
            f'<div class="hdr"><div class="t">{_esc(title)}</div>'
            + (f'<div class="s">{_esc(subtitle)}</div>' if subtitle else "")
            + "</div>"
        )
    bubbles = []
    last_date = ""
    for m in r["messages"]:
        # 2026-09-30：走 SSOT 判据（不合契约 ⇒ 空 ⇒ 不插分割线）。
        day = mt_date(m["time"])
        if day and day != last_date:
            last_date = day
            bubbles.append(f'<div class="day"><span>{_esc(day)}</span></div>')
        out = m["dir"] == "out"
        body = _esc(m["text"])
        if m["recalled"]:
            body = '<i class="rc">消息已撤回</i>'
        elif m["transcription"]:
            body = (f'{body}<div class="tr">{_esc(m["transcription"])}</div>')
        bubbles.append(
            f'<div class="row {"out" if out else "in"}">'
            f'<div class="bub">'
            f'<div class="txt">{body}</div>'
            f'<div class="ts">{_esc(mt_time(m["time"]))}</div>'
            f'</div></div>'
        )
    css = f"""
*{{box-sizing:border-box}}
body{{margin:0;background:{th['bg']};color:{th['fg']};
  font:14px/1.55 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif;
  width:{w}px;transform:scale({sc});transform-origin:top left}}
.hdr{{padding:14px 16px;border-bottom:1px solid rgba(128,128,128,.25)}}
.hdr .t{{font-size:15px;font-weight:600}}
.hdr .s{{font-size:12px;color:{th['meta']};margin-top:2px}}
.wrap{{padding:14px}}
.day{{display:flex;justify-content:center;margin:10px 0}}
.day span{{font-size:11px;color:{th['meta']};background:rgba(128,128,128,.14);
  padding:1px 8px;border-radius:9px}}
.row{{display:flex;margin:7px 0}}
.row.out{{justify-content:flex-end}}
.bub{{max-width:74%;border-radius:10px;padding:7px 10px;position:relative}}
.row.in .bub{{background:{th['peer']};color:{th['peer_fg']}}}
.row.out .bub{{background:{th['self']};color:{th['self_fg']}}}
.txt{{white-space:pre-wrap;word-break:break-word}}
.tr{{margin-top:5px;padding-top:4px;border-top:1px solid rgba(128,128,128,.35);
  font-size:12px;opacity:.9}}
.rc{{opacity:.7}}
.ts{{font-size:10px;opacity:.62;margin-top:3px;text-align:right}}
"""
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        f"<title>{conv_name}</title><style>{css}</style></head><body>"
        f"{head}<div class=\"wrap\">{''.join(bubbles)}</div></body></html>"
    )


def export_stats(daily: dict) -> dict:
    """逐日消息量的汇总视图（照上游 `stats/daily` 的对外形态）。"""
    days = daily.get("days") or []
    return {
        "total": daily.get("total", 0),
        "days": days,
        "bounds": daily.get("bounds") or {"min": 0, "max": 0},
        "active_days": len(days),
        "peak": max((d.get("count", 0) for d in days), default=0),
    }
