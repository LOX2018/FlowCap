# -*- coding: utf-8 -*-
"""内容页播放链路实机验证（v0.44.31，部署态）。

用法（需应用已启动，后端 127.0.0.1:8000）：
    py314 scripts/diag/verify_content_playback.py [账号名]

判据（全部为可判定项）：
  1) POST /api/platform/favorite           → 200 且 items 非空（前端列表数据源）
  2) POST /api/platform/media/stream-ticket → 200 且 stream_url 非空
     ⚠️ raw 必须传「列表项对象或其 media 子对象」，否则后端无 media 数据可取址
        （实测：不传 raw → 200 但 url 为空 → 播不了）
  3) GET  /api/platform/media/stream       → 206 + Content-Range + video/mp4
     同 Range 连打：首次 X-Stream-Cache=MISS，后续=HIT，且字节 md5 一致
  4) 载荷首字节为 MP4 签名（b"\\x00\\x00\\x00..ftyp"）
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8000"
APP_ROOT = Path(os.environ.get("FLOWCAP_APP_ROOT", r"C:\temp\flowcap_design"))


def _token() -> str:
    p = APP_ROOT / "members" / ".session.json"
    return json.loads(p.read_text(encoding="utf-8"))["token"]


def call(path: str, data=None, hdr=None, method=None):
    req = urllib.request.Request(BASE + path, method=method or ("POST" if data is not None else "GET"))
    req.add_header("X-Member-Token", _token())
    body = None
    if data is not None:
        req.add_header("Content-Type", "application/json")
        body = json.dumps(data).encode("utf-8")
    for k, v in (hdr or {}).items():
        req.add_header(k, str(v))
    try:
        r = urllib.request.urlopen(req, body, timeout=90)
        return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read()
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in e.headers.items()}, e.read(500)


def main() -> int:
    acc = sys.argv[1] if len(sys.argv) > 1 else "尚进工伤小助理"
    print("=" * 64)
    print(f"内容页播放链路验证 · 账号={acc} · {BASE}")

    print("\n[1] /api/platform/favorite")
    s, h, b = call("/api/platform/favorite", {"account": acc, "num": 5})
    items = json.loads(b.decode("utf-8", "replace")).get("items", []) if s == 200 else []
    print(f"    HTTP {s} | items={len(items)}")
    if not items:
        print("    ❌ 无收藏作品，无法继续（先用有收藏的账号）")
        return 1

    it = items[0]
    print(f"    首作品: {it.get('aweme_id')} | {str(it.get('desc',''))[:36]}")
    media = it.get("media") or {}

    print("\n[2] /api/platform/media/stream-ticket（raw = 前端实际回传的 it.media）")
    s, h, b = call("/api/platform/media/stream-ticket",
                   {"account": acc, "aweme_id": it["aweme_id"], "quality": "origin", "raw": media})
    d = json.loads(b.decode("utf-8", "replace")) if b else {}
    print(f"    HTTP {s} | resolved_via={d.get('resolved_via')} | qualities={d.get('qualities')}")
    if s != 200 or not d.get("stream_url"):
        print(f"    ❌ 取址失败：{str(d)[:160]}")
        return 2

    print("\n[3] /api/platform/media/stream（同 Range 连打 3 次）")
    rng = "bytes=0-1048575"
    res = []
    for i in (1, 2, 3):
        s, h, b = call(d["stream_url"], hdr={"Range": rng})
        res.append((h.get("x-stream-cache"), s, len(b), hashlib.md5(b).hexdigest()[:12], h.get("content-range"), b[:8]))
        print(f"    第{i}次: HTTP {s} | cache={res[-1][0]} | {res[-1][4]} | bytes={res[-1][2]} | md5={res[-1][3]}")

    ok_range = all(r[1] == 206 for r in res)
    ok_cache = res[0][0] == "MISS" and all(r[0] == "HIT" for r in res[1:])
    ok_same = len({r[3] for r in res}) == 1
    ok_mp4 = b"ftyp" in res[0][5]
    print("\n判据:")
    print(f"    Range/206      : {'✅' if ok_range else '❌'}")
    print(f"    缓存 MISS→HIT  : {'✅' if ok_cache else '❌'}")
    print(f"    字节一致       : {'✅' if ok_same else '❌'}")
    print(f"    MP4 载荷签名   : {'✅' if ok_mp4 else '❌'} ({res[0][5]!r})")
    ok = ok_range and ok_cache and ok_same and ok_mp4
    print(f"\n最终: {'✅ 内容页播放链路全通过' if ok else '❌ 有判据未通过'}")
    return 0 if ok else 3


if __name__ == "__main__":
    raise SystemExit(main())
