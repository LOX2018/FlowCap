# -*- coding: utf-8 -*-
"""聚焦实测：搜索条目里的「房间级权限/加密」字段实际值是什么。

上一轮 `probe_live_search_fields.py` 已证明搜索条目**确实含**这些键：
  · room_secret_chat              ← 疑「房间私密聊天」（用户说的「加密」？）
  · paid_live_data                ← 疑「付费直播」
  · paid_live_data.pay_ab_type
  · owner.mystery_man             ← 知识库已证**不可**当脱敏判据（恒为 1）
  · owner.authorization_info / adversary_authorization_info
  · room_view_stats.is_hidden
  · AnchorABMap.room_secret_chat / opt_paid_link_feature_switch / ...

本脚本把每条结果的**这些字段的实际值** dump 出来，并做跨条目对比：
  - 若某字段**全部相同** ⇒ 无区分度（不是房间级信号）
  - 若某字段**有差异** ⇒ 候选判据，需进一步验证语义

只读诊断：不写 kv、不发私信、不改文件。
"""
from __future__ import annotations

import json
import os
import sys

DESIGN_ROOT = os.path.abspath(os.environ.get("FLOWCAP_APP_ROOT") or r"C:\temp\flowcap_design")
if DESIGN_ROOT == os.path.abspath(r"C:\temp\flowcap_test"):
    sys.exit("[环境门禁] 拒绝在主分支环境运行")
os.environ["FLOWCAP_APP_ROOT"] = DESIGN_ROOT
_BACKEND = os.path.abspath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "backend"))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)
os.chdir(_BACKEND)

QUERY = os.environ.get("DY_PROBE_QUERY") or "工伤"
ACCOUNT = os.environ.get("DY_PROBE_ACCOUNT") or "小助理"

# 关注的字段路径（相对 rawdata 解析后的 dict）
WATCH = [
    "room_secret_chat",
    "paid_live_data",
    "room_view_stats",
    "owner.mystery_man",
    "owner.authorization_info",
    "owner.adversary_authorization_info",
    "AnchorABMap.room_secret_chat",
    "AnchorABMap.opt_paid_link_feature_switch",
    "AnchorABMap.live_anchor_hit_video_bid_paid",
    "AnchorABMap.optran_paid_linkmic",
    "AnchorABMap.radio_paid_linkmic",
]


def dig(d, path):
    """按 'a.b.c' 取值；任一层缺失返回 <MISSING>。"""
    cur = d
    for seg in path.split("."):
        if not isinstance(cur, dict) or seg not in cur:
            return "<MISSING>"
        cur = cur[seg]
    return cur


def main() -> int:
    from auto_dm import accounts as acc
    from dy_apis.login_api import DYLoginApi
    from dy_apis.douyin_api import DouyinAPI

    auth = DYLoginApi._load_auth_from_env(acc.env_path_of(ACCOUNT))
    res = DouyinAPI.search_live(auth, QUERY, "0", "25")
    data = (res or {}).get("data") or []
    print(f"查询={QUERY!r}  账号={ACCOUNT}  条目数={len(data)}\n")

    rows = []
    for i, item in enumerate(data):
        lv = item.get("lives") if isinstance(item, dict) else None
        target = item
        if isinstance(lv, dict):
            raw = lv.get("rawdata")
            if isinstance(raw, str) and raw.strip():
                try:
                    target = json.loads(raw)
                except Exception:
                    target = lv
            else:
                target = lv
        if not isinstance(target, dict):
            continue
        room_id = target.get("id_str") or ""
        title = str(target.get("title") or "")[:24]
        vals = {p: dig(target, p) for p in WATCH}
        rows.append((room_id, title, vals))

    # ── 逐条打印关注字段 ──────────────────────────────────────────────
    for room_id, title, vals in rows:
        print(f"--- {room_id} | {title} ---")
        for p in WATCH:
            v = vals[p]
            s = json.dumps(v, ensure_ascii=False)
            if len(s) > 200:
                s = s[:200] + "…"
            print(f"    {p} = {s}")
        print()

    # ── 跨条目对比：哪些字段有差异（= 有区分度）────────────────────────
    print("=" * 74)
    print("【跨条目区分度分析】（全部相同 = 无区分度，不是房间级信号）")
    print("=" * 74)
    for p in WATCH:
        seen = set()
        for _rid, _t, vals in rows:
            seen.add(json.dumps(vals[p], ensure_ascii=False, sort_keys=True))
        n = len(seen)
        mark = "🔴 有差异（候选判据）" if n > 1 else "⚪ 全同（无区分度）"
        print(f"  {p}: {n} 种取值  {mark}")
        if n > 1:
            for s in sorted(seen)[:6]:
                print(f"        {s[:150]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
