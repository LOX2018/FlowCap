# -*- coding: utf-8 -*-
"""ADR-012 存量迁移：给既有消息补 `extra.kind` 语义标注（+ 未知类型降级）。

纪律（沿用 ADR-011 `migrate_image_text_contract.py`）：
  · 默认 **dry-run**，`--apply` 才写库
  · 写前**自动备份**（含 `-wal` / `-shm`）
  · **逐条判定**，只重写 `extra` 一列（不动 text，避免破坏既有渲染契约）
  · **幂等**：已有 `extra.kind` 的行跳过
  · **禁 LIKE 模糊删**

批次：
  A  平台提示文案（实测 183 条，两种模板）→ kind=system_notice
  B  未登记 msg_type（0/1/15/50010/…）→ 按注册表归类；无匹配 → unknown + raw
  C  `[投递验证]` → kind=delivery_marker
  D  其余有 text 的常规消息 → kind=user_text（显式登记，供读侧白名单）

用法：
    DY_APP_ROOT=C:/temp/dyautodm_design python scripts/migrate_message_kind.py --account "四川工伤张老师"
    ... 同上 ... --apply
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.join(os.path.dirname(_HERE), "backend")
sys.path.insert(0, _BACKEND)
from services.message_schema import (  # noqa: E402
    MessageRecord, extra_get_kind, is_system_text, is_noise_text, kind_of,
)


def _resolve_db(account: str) -> str:
    """定位账号所在 member 库（顺序无关：环境变量 ∪ 项目设计根）。"""
    roots = []
    ev = os.environ.get("DY_APP_ROOT", "")
    if ev:
        roots.append(ev)
    for cand in (r"C:\temp\dyautodm_design", r"C:\temp\dyautodm_test"):
        if cand not in roots:
            roots.append(cand)
    for root in roots:
        mdir = os.path.join(root, "members")
        if not os.path.isdir(mdir):
            continue
        for name in os.listdir(mdir):
            p = os.path.join(mdir, name, "data", "dyautodm.db")
            if not os.path.isfile(p):
                continue
            try:
                c = sqlite3.connect(p)
                hit = c.execute(
                    "SELECT 1 FROM dm_conversations WHERE account=? LIMIT 1",
                    (account,)).fetchone()
                c.close()
            except Exception:
                continue
            if hit:
                return p
    return ""


def plan(rows) -> list[dict]:
    """逐条判定 → 需要补标注的行。纯函数，便于单测。"""
    todo = []
    for r in rows:
        text = r["text"] or ""
        mt = r["msg_type"] or ""
        raw = r["extra"] or ""
        if extra_get_kind(raw):          # 幂等：已标注跳过
            continue
        try:
            ex = json.loads(raw) if isinstance(raw, str) and raw.strip() else {}
            if not isinstance(ex, dict):
                ex = {}
        except Exception:
            ex = {}
        # 复用写侧同一判据（杜绝双写）
        rec = MessageRecord.build(text=text, msg_type=mt, extra=ex)
        if rec.kind == "user_text" and (is_noise_text(text) or is_system_text(text)):
            # 存量：text 保留原值（前端可能展示），仅标注 kind
            rec.kind = "system_notice"
            rec.extra["kind"] = "system_notice"
        ex2 = dict(ex)
        ex2["kind"] = rec.kind
        todo.append({"id": r["id"], "kind": rec.kind,
                     "extra": json.dumps(ex2, ensure_ascii=False)})
    return todo


def _backup(db: str) -> str:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    dst = f"{db}.bak.h25k.{stamp}"
    shutil.copy2(db, dst)
    for suf in ("-wal", "-shm"):
        s = db + suf
        if os.path.isfile(s):
            shutil.copy2(s, f"{dst}{suf}")
    return dst


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", required=True)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    db = _resolve_db(args.account)
    if not db:
        print("[err] 未定位到账号库")
        return 2
    print(f"[db] {db}")

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, text, msg_type, extra FROM dm_messages ORDER BY id").fetchall()
    print(f"[scan] 消息行：{len(rows)}")

    todo = plan(rows)
    from collections import Counter
    cnt = Counter(t["kind"] for t in todo)
    print(f"[plan] 需补标注：{len(todo)}  分布：{dict(cnt)}")

    if not args.apply:
        print("[dry-run] 未写库（加 --apply 执行）")
        conn.close()
        return 0

    bak = _backup(db)
    print(f"[backup] {bak}")

    n = 0
    for t in todo:
        # 逐条：只重写 extra 一列
        cur = conn.execute(
            "UPDATE dm_messages SET extra=? WHERE id=? "
            "  AND (extra IS NULL OR extra='' OR extra NOT LIKE '%\"kind\"%')",
            (t["extra"], t["id"]))
        n += cur.rowcount or 0
    conn.commit()

    left = conn.execute(
        "SELECT COUNT(*) n FROM dm_messages WHERE extra IS NOT NULL "
        "AND extra<>'' AND extra NOT LIKE '%\"kind\"%'").fetchone()["n"]
    conn.close()
    print(f"[apply] 已标注 {n} 行；未标注残留 {left}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
