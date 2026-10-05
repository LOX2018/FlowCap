#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""H-25 存量修复：会话昵称/头像污染订正（「对端被写成本账号身份」）。

背景（2026-09-25 实测根因）
--------------------------
`recv_daemon._extract_peer_uid` 在 my_uid 未就绪时兜底 `return uid_b`；
而实库 conv_id 形态为 `0:1:<对端uid>:<本号uid>`（本号段在 idx=3，136/136 实测）
⇒ 兜底返回的 uid_b **恰是本号自己** ⇒ 富化用本号 uid 去 IndexedDB 取到
**本号昵称/头像**并回填 ⇒ 会话被污染。

本脚本按**权威来源**重建 peer_name/avatar：
  真对端 uid = `services.conv_identity.peer_uid(conv_id, my_uid)`
  真昵称/头像 = BCC `POST /userinfo_idb`（IndexedDB，零网络请求，不碰风控）

安全约束（遵项目铁律）
----------------------
  * **默认 dry-run**，只打印将做的变更；`--apply` 才写库。
  * 写库前**自动备份**（SQLite backup API → `<db>.bak.<ts>`）。
  * **逐条判定**，不用 LIKE 模糊删改；只改「污染态/占位态」行，真昵称永不覆盖。
  * 不新增依赖；判据**复用** `services.conv_identity` / `services.verdicts`（SSOT）。
"""
from __future__ import annotations

import argparse
import datetime
import os
import sqlite3
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(_BACKEND))


def _db_file(conn: sqlite3.Connection) -> str:
    for _seq, _name, f in conn.execute("PRAGMA database_list"):
        if _name == "main" and f:
            return f
    return ""


def _fetch_idb(account: str, timeout: int = 120) -> dict:
    """经 BCC 读 IndexedDB 用户表 → {uid: {nickname, avatar}}。零网络请求。"""
    import requests

    from auto_dm import accounts as acc
    port = acc.browser_daemon_port(account)
    r = requests.post(f"http://127.0.0.1:{port}/userinfo_idb",
                      json={}, timeout=timeout)
    j = (r.json() or {}) if r.status_code == 200 else {}
    users = j.get("users") or {}
    out: dict[str, dict] = {}
    for _k, _v in users.items():
        uid = str((_v or {}).get("uid") or _k)
        if uid:
            out[uid] = _v or {}
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="H-25 会话昵称/头像污染存量修复")
    ap.add_argument("--account", required=True, help="账号名，如 四川工伤张老师")
    ap.add_argument("--apply", action="store_true",
                    help="实际写库（默认 dry-run 只打印）")
    ap.add_argument("--no-backup", action="store_true", help="跳过备份（不推荐）")
    args = ap.parse_args()

    from database import get_db
    from services.conv_identity import my_uid as _my_uid, peer_uid as _peer_uid

    account = args.account
    conn = get_db()
    db_path = _db_file(conn)
    print(f"库文件: {db_path}")
    print(f"账号  : {account}")
    print(f"模式  : {'APPLY（写库）' if args.apply else 'DRY-RUN（只打印）'}")
    print("-" * 72)

    my = str(_my_uid(account) or "")
    print(f"本号 uid（conv_identity 会话池推断）: {my or '(未推断出)'}")
    if not my:
        print("❌ 无法推断本号 uid —— 会话数据不足或过脏，中止（不猜）。")
        return 2

    idb = _fetch_idb(account)
    print(f"IndexedDB 用户条目: {len(idb)}")
    self_nick = str((idb.get(my) or {}).get("nickname") or "").strip()
    self_avatar = str((idb.get(my) or {}).get("avatar") or "").strip()
    print(f"本号昵称(IDB): {self_nick!r}")
    if not idb:
        print("❌ IndexedDB 为空（BCC 未运行/页面未就绪）—— 中止，先保 BCC 在线。")
        return 2

    rows = conn.execute(
        "SELECT id, conv_id, peer_id, peer_name, avatar "
        "FROM dm_conversations WHERE account=?", (account,)).fetchall()

    # 每会话目标值：真对端 uid → IDB 真昵称/头像
    plan: list[tuple] = []          # (row_id, cid, old_name, new_name, old_av, new_av, why)
    skipped: dict[str, int] = {}
    for r in rows:
        cid = r["conv_id"]
        rid = r["id"] if "id" in r.keys() else None
        old_name = (r["peer_name"] or "").strip()
        old_av = (r["avatar"] or "").strip()
        real_peer = _peer_uid(cid, my)
        if not real_peer or str(real_peer) == my:
            skipped["对端uid无法解析或==本号"] = skipped.get("对端uid无法解析或==本号", 0) + 1
            continue
        info = idb.get(str(real_peer)) or {}
        new_name = str(info.get("nickname") or "").strip()
        new_av = str(info.get("avatar") or "").strip()
        # 是否污染/占位态（可覆盖）——真昵称不覆盖
        polluted = (
            (not old_name)
            or (self_nick and old_name == self_nick)
            or old_name == my
            or old_name == str(r["peer_id"] or "")
        )
        av_polluted = (not old_av) or (self_avatar and old_av == self_avatar)
        # 2026-09-25 H-25：本号身份冒充（本号昵称/头像）是**主动误导**，比裸 uid 更坏。
        # 若拿不到真值，必须「退化订正」为裸 peer_id（项目既定回退），绝不保留本号身份。
        self_masq = ((self_nick and old_name == self_nick)
                     or (self_avatar and old_av == self_avatar))
        if not new_name:
            if self_masq:
                # ★ 2026-09-26（H-22 审计 idx24 订正）：退化分支原本**无条件**把
                # new_av 置 `""`，而写库 L180-186 的 WHERE 只约束 peer_name 形态、
                # **不约束 avatar** ⇒ 当「只有昵称属本号身份」而 avatar 是**合法对端
                # 头像**（av_polluted=False）时，会把该有效头像一并擦成 NULL ——
                # 项目为此专门算出的 `av_polluted` 判据在此被弃用，属判据不一致。
                # 现按 av_polluted 保护：仅当头像确属污染（空/本号头像）才清空，
                # 否则原样保留对端头像。
                _na = "" if av_polluted else old_av
                plan.append((rid, cid, old_name, str(r["peer_id"] or ""),
                             old_av, _na, "本号身份冒充且无真值→退化裸uid"))
            else:
                skipped["IDB无该对端昵称"] = skipped.get("IDB无该对端昵称", 0) + 1
            continue
        if not polluted and not av_polluted:
            skipped["真昵称（不覆盖）"] = skipped.get("真昵称（不覆盖）", 0) + 1
            continue
        if old_name == new_name and (not av_polluted or old_av == new_av):
            skipped["已是正确值"] = skipped.get("已是正确值", 0) + 1
            continue
        why = []
        if self_nick and old_name == self_nick:
            why.append("昵称=本号昵称(污染)")
        elif not old_name:
            why.append("昵称为空")
        elif old_name == my:
            why.append("昵称=本号uid")
        elif old_name == str(r["peer_id"] or ""):
            why.append("昵称=裸peer_id")
        if av_polluted and old_av != new_av:
            why.append("头像=本号头像" if old_av else "头像为空")
        plan.append((rid, cid, old_name, new_name, old_av, new_av, " + ".join(why)))

    print(f"\n计划修复: {len(plan)} 条 / 会话总数 {len(rows)}")
    print("跳过分类:", skipped)
    print("-" * 72)
    for rid, cid, on, nn, oa, na, why in plan[:15]:
        print(f"  #{rid} {cid}")
        print(f"      昵称 {on!r} → {nn!r}")
        if oa != na:
            print(f"      头像 {oa[:45]!r}… → {na[:45]!r}…")
        print(f"      判据: {why}")
    if len(plan) > 15:
        print(f"  …（其余 {len(plan) - 15} 条略）")

    if not args.apply:
        print("\n[DRY-RUN] 未写库。加 --apply 执行。")
        return 0

    # 备份
    if not args.no_backup and db_path:
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        bak = f"{db_path}.bak.h25.{ts}"
        b = sqlite3.connect(bak)
        with b:
            conn.backup(b)
        b.close()
        print(f"\n✅ 已备份: {bak}")

    done = 0
    for rid, cid, on, nn, oa, na, why in plan:
        cur = conn.execute(
            "UPDATE dm_conversations SET peer_name=?, avatar=? "
            "WHERE account=? AND conv_id=? "
            "AND (peer_name IS NULL OR peer_name='' OR peer_name=peer_id "
            "     OR peer_name=? OR peer_name=?)",
            (nn, na or None, account, cid, my, self_nick),
        )
        done += (cur.rowcount or 0)
    conn.commit()
    print(f"✅ 已修复 {done} 条（计划 {len(plan)}）")

    # 复验
    left = conn.execute(
        "SELECT COUNT(*) FROM dm_conversations WHERE account=? AND peer_name=?",
        (account, self_nick)).fetchone()[0] if self_nick else -1
    print(f"复验：仍为「本号昵称」的会话 = {left}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
