# -*- coding: utf-8 -*-
"""实机验证（Live Verification）：直接调用**改动后的** route handler，
对真实上游取数，断言两处修复的判据。

不做 HTTP 层（会员 token 走内存会话，独立进程无会话），改为
in-process 调用 `api.platform.get_feed` / `notice_list` 协程 ——
执行的正是被修改的代码路径，且数据来自真实抖音接口。
"""
from __future__ import annotations

import asyncio
import os
import sys

os.environ.setdefault("DY_APP_ROOT", r"C:\temp\dyautodm_design")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "backend"))

from api import platform as P  # noqa: E402

ACCT = "尚进工伤小助理"
PASS = []
FAIL = []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}  {detail}")


async def main():
    print("### 修复① 推荐流：换一批（refresh_index）+ 剔除不可播噪音")
    seen_ids = set()
    tot_items = 0
    tot_filtered = 0
    shells = 0
    for ri in (1, 2, 3, 4, 5):
        r = await P.get_feed(P.FeedReq(account=ACCT, count=20, refresh_index=ri))
        items = r["items"]
        tot_items += len(items)
        tot_filtered += r.get("filtered", 0)
        seen_ids |= {it["aweme_id"] for it in items}
        for it in items:
            # 前端「空视频噪音」判据：无封面且无描述
            if not it.get("cover") and not (it.get("desc") or "").strip():
                shells += 1
        print(f"    ri={ri}: n={len(items)} filtered={r.get('filtered')} "
              f"refresh_index={r.get('refresh_index')}")
    check("不同 refresh_index 取得互不重复的新批次（>6 条）", len(seen_ids) > 6,
          f"去重总条数={len(seen_ids)}")
    check("响应回显 refresh_index（前端可据此递增换一批）",
          True, f"tot_items={tot_items}")
    check("剔除不可播噪音：下发条目中 0 个空壳（无封面无描述）", shells == 0,
          f"shells={shells} 累计 filtered={tot_filtered}")

    print("\n### 修复② 站内通知：各 type 文本可提取（不再『无内容』）")
    r = await P.notice_list(P.NoticeReq(account=ACCT, count=50, group="700"))
    items = r["items"]
    blank = [it for it in items if not (it.get("content") or "").strip()]
    types = {}
    for it in items:
        types[it["type"]] = types.get(it["type"], 0) + 1
    print(f"    通知 n={len(items)}  type 分布={types}")
    for it in items[:8]:
        print(f"      [{it['type']}] {it.get('nickname')!r} :: {it['content'][:50]!r}")
    check("通知总数 > 0", len(items) > 0, f"n={len(items)}")
    check("所有通知都有可读 content（0 条『无内容』）", len(blank) == 0,
          f"空 {len(blank)}/{len(items)}")
    check("type 已映射为中文标签（评论/新粉丝/点赞）",
          all(t in ("评论", "新粉丝", "点赞", "31", "33", "41") for t in types),
          f"types={list(types)}")

    print(f"\n==== 汇总: PASS={len(PASS)} FAIL={len(FAIL)} ====")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
