# -*- coding: utf-8 -*-
"""把专业知识库种子（knowledge/pro_kb_seed/*.json）经**运行中后端 API**写入 pro_kb。

为什么走 API 而不直写库：
  - 生产库被运行中的后端进程持有（SQLite WAL）。并发直写属于多写者，
    违反项目并发铁律；API 是**进程内唯一写者**，天然串行。
  - 顺带复用后端既有去重/规范化逻辑（add_item）。

用法：
  py -3.14 scripts/load_pro_kb_seed.py                 # 默认加载 knowledge/pro_kb_seed/*.json
  py -3.14 scripts/load_pro_kb_seed.py path/to.json    # 指定单个种子
  py -3.14 scripts/load_pro_kb_seed.py --base http://127.0.0.1:8000
  py -3.14 scripts/load_pro_kb_seed.py --token <MEMBER_TOKEN>

令牌来源：环境变量 DY_MEMBER_TOKEN，或自动探测常见会员 .session.json。
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # FlowCap/
DEFAULT_GLOB = str(ROOT / "knowledge" / "pro_kb_seed" / "*.json")

SESSION_CANDIDATES = [
    r"C:\temp\flowcap_design\members\.session.json",
    r"C:\temp\flowcap_test\members\.session.json",
]


def find_token() -> str:
    tok = os.environ.get("DY_MEMBER_TOKEN", "").strip()
    if tok:
        return tok
    for p in SESSION_CANDIDATES:
        try:
            with open(p, encoding="utf-8") as f:
                t = (json.load(f) or {}).get("token") or ""
            if t:
                return t
        except Exception:
            continue
    return ""


def post(base: str, token: str, payload: dict) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{base}/api/ai/prokb", data=data, method="POST",
        headers={"Content-Type": "application/json", "X-Member-Token": token})
    return json.loads(urllib.request.urlopen(req, timeout=30).read().decode("utf-8"))


def get_items(base: str, token: str) -> list:
    req = urllib.request.Request(f"{base}/api/ai/prokb",
                                 headers={"X-Member-Token": token})
    d = json.loads(urllib.request.urlopen(req, timeout=20).read().decode("utf-8"))
    return d.get("items", [])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*", default=None)
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--token", default="")
    args = ap.parse_args()

    token = args.token.strip() or find_token()
    if not token:
        print("ERROR: 找不到会员令牌（设 DY_MEMBER_TOKEN 或 --token）。", file=sys.stderr)
        return 2

    files = args.files or sorted(glob.glob(DEFAULT_GLOB))
    if not files:
        print(f"ERROR: 没有种子文件（{DEFAULT_GLOB}）。", file=sys.stderr)
        return 2

    print(f"后端 {args.base} | 种子 {len(files)} 个")
    added = merged = 0
    for fp in files:
        with open(fp, encoding="utf-8") as f:
            seed = json.load(f)
        items = seed.get("items") or []
        print(f"\n[{Path(fp).name}] {len(items)} 条")
        for it in items:
            try:
                r = post(args.base, token, {
                    "topic": it.get("topic", ""),
                    "category": it.get("category", ""),
                    "content": it.get("content", ""),
                    "summary": it.get("summary", ""),
                })
            except Exception as e:  # noqa: BLE001
                print(f"  EXC {it.get('category')}: {repr(e)[:160]}")
                continue
            if not r.get("ok"):
                print(f"  FAIL {it.get('category')}: {r.get('error')}")
                continue
            info = r.get("item") or {}
            if info.get("deduped"):
                merged += 1
                print(f"  合并(去重) {info.get('category')} -> id={info.get('id')}")
            else:
                added += 1
                print(f"  新增 {info.get('category')} -> id={info.get('id')}")

    cur = get_items(args.base, token)
    print(f"\n回读 pro_kb = {len(cur)} 条 | 本次新增 {added}，去重合并 {merged}")
    tgt = "GB/T 16180"
    have = [it for it in cur if "16180" in ((it.get("topic") or "")
                                            + (it.get("content") or ""))]
    print(f"含 {tgt} 的条目 = {len(have)}")
    for it in have:
        print(f"  id={it.get('id')} | {it.get('category')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
