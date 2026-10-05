#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""H-25 存量图片消息「统一落库契约」迁移（2026-09-25）。

背景
----
`text` 曾同时承载「语义」与「缩略图字节」两种职责，导致：
  · AI prompt 被 10.3 万字符 base64 污染（≈34k tokens）
  · ChatLab 导出 / 审计视图出现无意义串码
契约（v0.45.3 起）：
  text   = 纯语义标签 `[图片]`
  extra  = {skey, origin_url, thumb, ...}  ← 缩略图字节归此

本脚本把**存量行**搬迁到该契约。搬迁是**无损**的：字节从 text 移到
extra.thumb，前端经 `/conversation` 的 `thumb_url` 派生字段继续渲染。

安全设计（遵项目铁律）
----------------------
  · 默认 **dry-run**，必须显式 `--apply` 才写库
  · 写前**自动备份**（含 -wal/-shm）
  · **逐条判定**、绝不 `LIKE` 模糊删；只做「读取→重写两个字段」
  · 幂等：已符合契约的行跳过（可重复跑）
  · 只动 `text` / `extra` 两列，其它列（含 ts / msg_id）一律不碰

用法
----
    python scripts/migrate_image_text_contract.py --account "四川工伤张老师"
    python scripts/migrate_image_text_contract.py --account "..." --apply
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_DATA_URI_RE = re.compile(r"^\[图片\]\s*(data:image/[\w.+-]+;base64,(\S+))", re.S)
# 2026-09-25：契约同样禁止 `[图片] <url>` 形态留在 text（线上实测该 URL 是
# 加密体、不可渲染，且污染 AI prompt / 导出）。故迁移范围含此形态。
_TEXT_URL_RE = re.compile(r"^\[图片\]\s*(https?://\S+)(?:\s*$|\s*\r?\n)", re.M)


def _thumb_from_text(text: str) -> str:
    """从 text 内提取**可迁入 extra.thumb 的缩略图字节**；无则返回 ''。

    ⚠️ 仅覆盖 `data:` 形态。`[图片] <url>` 形态**不迁 thumb** ——
    实测（本账号 12/12）该 URL 与 `extra.origin_url` 逐字相同，且是
    **抖音加密体**（需 skey 解密，浏览器不可直接渲染）。把它塞进
    `extra.thumb` 会让前端当成可渲染缩略图去加载（必然失败），
    且其展示早已由既有 `image_url`（后端解密）路径覆盖 ⇒ 对这类行
    只做「text 纯化」，URL 不重复搬运。
    """
    m = _DATA_URI_RE.match(text)
    if m:
        return m.group(1)
    return ""


def _needs_text_purify(text: str) -> bool:
    """该行 text 是否符合契约（纯语义标签）。"""
    t = (text or "").strip()
    if t == "[图片]":
        return False
    return bool(_DATA_URI_RE.match(t) or _TEXT_URL_RE.match(t))


def _db_path_for(account: str) -> str | None:
    """按账号名定位会员库（沿用项目 flowcap.db 布局）。"""
    try:
        from config import settings

        root = getattr(settings, "app_root", None) or os.environ.get("FLOWCAP_APP_ROOT", "")
    except Exception:
        root = os.environ.get("FLOWCAP_APP_ROOT", "")
    if not root:
        return None
    members = os.path.join(str(root), "members")
    if not os.path.isdir(members):
        return None
    for name in os.listdir(members):
        p = os.path.join(members, name, "data", "flowcap.db")
        if not os.path.isfile(p):
            continue
        try:
            c = sqlite3.connect(p)
            hit = c.execute("SELECT 1 FROM dm_conversations WHERE account=? LIMIT 1",
                            (account,)).fetchone()
            c.close()
        except Exception:
            continue
        if hit:
            return p
    return None


def _backup(db: str) -> str:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    dst = f"{db}.bak.h25mig.{stamp}"
    shutil.copy2(db, dst)
    for suf in ("-wal", "-shm"):
        s = db + suf
        if os.path.exists(s):
            shutil.copy2(s, f"{dst}{suf}")
    return dst


def plan(rows) -> list[dict]:
    """逐条判定：返回需要迁移的行及其目标形态。纯函数，便于单测。

    两类目标形态：
      · `[图片] data:image/...`  → text 纯化 + 字节迁入 extra.thumb（无损）
      · `[图片] <url>`          → 仅 text 纯化（URL 已由 extra.origin_url 覆盖）
    """
    todo = []
    for r in rows:
        text = r["text"] or ""
        if not _needs_text_purify(text):
            continue
        thumb = _thumb_from_text(text)
        try:
            ex = json.loads(r["extra"] or "{}")
            if not isinstance(ex, dict):
                ex = {}
        except Exception:
            ex = {}
        if thumb and ex.get("thumb") == thumb:
            continue                      # 幂等：已迁移
        new_ex = dict(ex)
        if thumb:
            new_ex["thumb"] = thumb
        todo.append({
            "id": r["id"], "msg_id": r["msg_id"],
            "old_extra": r["extra"],
            "new_extra": json.dumps(new_ex, ensure_ascii=False),
            "kind": "data-uri" if thumb else "url-only",
        })
    return todo


def main() -> int:
    ap = argparse.ArgumentParser(description="H-25 存量图片契约迁移")
    ap.add_argument("--account", required=True, help="账号名（如「四川工伤张老师」）")
    ap.add_argument("--db", default="", help="显式指定库路径（默认自动定位）")
    ap.add_argument("--apply", action="store_true", help="真正写库（默认 dry-run）")
    args = ap.parse_args()

    db = args.db or _db_path_for(args.account)
    if not db or not os.path.isfile(db):
        print(f"[ERR] 未找到账号 {args.account!r} 的库；请用 --db 显式指定")
        return 2
    print(f"[db] {db}")
    print(f"[mode] {'APPLY（写库）' if args.apply else 'DRY-RUN（只读）'}")

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, msg_id, text, extra FROM dm_messages "
        "WHERE text LIKE '[图片] data:image%' OR text LIKE '[图片] http%' "
        "ORDER BY id").fetchall()
    print(f"[scan] text 内含 base64/URL 的图片行：{len(rows)}")
    todo = plan(rows)
    k1 = sum(1 for t in todo if t["kind"] == "data-uri")
    k2 = len(todo) - k1
    print(f"[plan] 需迁移：{len(todo)}（字节迁移 {k1} 条 / 仅 text 纯化 {k2} 条）")

    if not todo:
        print("[done] 无需迁移")
        conn.close()
        return 0

    for t in todo[:5]:
        extra = ""
        if t["kind"] == "data-uri":
            extra = f" → extra.thumb 长度={len(json.loads(t['new_extra'])['thumb'])}"
        print(f"   · id={t['id']} msg_id={t['msg_id']} [{t['kind']}]{extra}")
    if len(todo) > 5:
        print(f"   · … 另有 {len(todo) - 5} 条")

    if not args.apply:
        print("\n[dry-run] 未写库。确认无误后加 --apply 执行。")
        conn.close()
        return 0

    bak = _backup(db)
    print(f"[backup] {bak}")

    n = 0
    for t in todo:
        # 逐条：仅重写 text / extra 两列（text → 语义标签）
        cur = conn.execute(
            "UPDATE dm_messages SET text=?, extra=? WHERE id=? "
            "  AND (text LIKE '[图片] data:image%' OR text LIKE '[图片] http%')",
            ("[图片]", t["new_extra"], t["id"]))
        n += cur.rowcount or 0
    conn.commit()

    # 迁移后核验：不应再有 text 型 base64 / URL
    left_b64 = conn.execute(
        "SELECT COUNT(*) n FROM dm_messages WHERE text LIKE '[图片] data:image%'").fetchone()["n"]
    left_url = conn.execute(
        "SELECT COUNT(*) n FROM dm_messages WHERE text LIKE '[图片] http%'").fetchone()["n"]
    print(f"[apply] 迁移 {n} 行；残留 text 型 base64 = {left_b64}、URL = {left_url}（期望 0 / 0）")
    conn.close()
    return 0 if (left_b64 == 0 and left_url == 0) else 1


if __name__ == "__main__":
    raise SystemExit(main())
