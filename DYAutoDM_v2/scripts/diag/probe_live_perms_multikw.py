# -*- coding: utf-8 -*-
"""跨关键词实测：权限字段是否真的恒定（排除「同质样本」干扰）。

上一轮只用「工伤」一个关键词（14 条全是法律类直播间），
`room_secret_chat` / `paid_live_data` / `room_view_stats.is_hidden` 全同
可能只是**样本同质**，不能推出「字段恒定」。

本轮用**多个异构关键词**（法律 / 游戏 / 带货 / 颜值 / 知识 等），
逐关键词统计关注字段的取值分布。若跨异构关键词仍恒定 ⇒ 该字段无区分度，
不能作「房间是否加密」的判据；若出现差异 ⇒ 需进一步验证语义。

只读诊断：不写 kv、不发私信、不改文件。
"""
from __future__ import annotations

import json
import os
import sys

DESIGN_ROOT = os.path.abspath(os.environ.get("DY_APP_ROOT") or r"C:\temp\dyautodm_design")
if DESIGN_ROOT == os.path.abspath(r"C:\temp\dyautodm_test"):
    sys.exit("[环境门禁] 拒绝在主分支环境运行")
os.environ["DY_APP_ROOT"] = DESIGN_ROOT
_BACKEND = os.path.abspath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "backend"))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)
os.chdir(_BACKEND)

ACCOUNT = os.environ.get("DY_PROBE_ACCOUNT") or "小助理"
QUERIES = (os.environ.get("DY_PROBE_QUERIES")
           or "工伤,游戏,带货,美女,律师,健身,美食,编程").split(",")

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
    "AnchorABMap.radio_paid_linkmic",
]


def dig(d, path):
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

    # 全局分布：{字段: {取值: 出现次数}}
    dist: dict[str, dict[str, int]] = {p: {} for p in WATCH}
    total_rooms = 0

    for q in QUERIES:
        q = q.strip()
        if not q:
            continue
        try:
            res = DouyinAPI.search_live(auth, q, "0", "25")
        except Exception as e:
            print(f"[{q}] 搜索异常: {e}")
            continue
        data = (res or {}).get("data") or []
        n_ok = 0
        for item in data:
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
            n_ok += 1
            total_rooms += 1
            for p in WATCH:
                v = json.dumps(dig(target, p), ensure_ascii=False, sort_keys=True)
                dist[p][v] = dist[p].get(v, 0) + 1
        print(f"[{q}] 条目 {len(data)} → 可解析 {n_ok}")

    print(f"\n总房间数 = {total_rooms}\n")
    print("=" * 74)
    print("【跨关键词字段分布】（多关键词异构样本）")
    print("=" * 74)
    for p in WATCH:
        vals = dist[p]
        n = len(vals)
        mark = "🔴 有差异（候选判据）" if n > 1 else "⚪ 恒定（无区分度）"
        print(f"\n  {p}: {n} 种取值  {mark}")
        for v, c in sorted(vals.items(), key=lambda kv: -kv[1])[:8]:
            s = v[:110] + ("…" if len(v) > 110 else "")
            print(f"        ×{c:<4} {s}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
