"""H-9 (A) 真机验收 v2 — 修复 field name（TaskConfig 规范字段是 acct，不是 account）"""
from __future__ import annotations
import json
import os
import sys
import time
import urllib.request
import urllib.error

B = "http://127.0.0.1:8000"
VERSION = "0.44.40"


def auth_hdr():
    p = r"C:\temp\dyautodm_design\members\.session.json"
    if not os.path.exists(p):
        raise SystemExit(f"missing {p}")
    tok = json.load(open(p, encoding="utf-8"))["token"]
    return {"X-Member-Token": tok, "Content-Type": "application/json"}


def req(method, path, body=None, acct_query=None):
    url = B + path
    if acct_query is not None:
        url = url + f"?acct={urllib.parse.quote(acct_query)}" if "acct=" not in url else url
    elif acct_query == "":
        url = url + "?acct="
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method, headers=auth_hdr())
    if data:
        r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=15) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8") if e.fp else ""
        try:
            return e.code, json.loads(raw) if raw else {"raw": raw}
        except json.JSONDecodeError:
            return e.code, {"raw": raw}


def get(url):
    r = urllib.request.Request(B + url, headers=auth_hdr())
    with urllib.request.urlopen(r, timeout=15) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


def start(label, acct, live_url, expect_status=200):
    print(f"\n▶ start {label}")
    body = {"acct": acct, "live_url": live_url}
    s, d = req("POST", "/api/engine/start", body)
    ok = s == expect_status
    print(f"  status={s} expected={expect_status} {'✅' if ok else '❌'}")
    print(f"  acct={d.get('acct', '<missing>')} state={d.get('state', '<missing>')} detail={d}")
    return s, d


def stop_by_acct(acct, expect_status=200):
    """stop 走 query param（endpoints 设计为 acct: str = Query('')）"""
    print(f"\n▶ stop acct={acct!r} (query)")
    s, d = req("POST", f"/api/engine/stop?acct={urllib.parse.quote(acct)}")
    ok = s == expect_status
    print(f"  status={s} expected={expect_status} {'✅' if ok else '❌'}")
    print(f"  detail={d}")
    return s, d


def main():
    import urllib.parse  # noqa: F811
    print("=" * 64)
    print(f"H-9 (A) 多账号并发真机验收 — v{VERSION}")
    print("=" * 64)

    # 版本检查
    s, d = get("/api/version")
    print(f"version: {d.get('backend')} {'✅' if d.get('backend') == VERSION else '❌'}")

    # 清现存
    for _ in range(3):
        _, d = get("/api/engine/accounts")
        accts = [i.get("acct", "") for i in d.get("items", []) if i.get("acct")]
        if not accts:
            break
        for a in accts:
            req("POST", f"/api/engine/stop?acct={urllib.parse.quote(a)}")
        time.sleep(1)

    print("\n=== 双账号同时 start（各指同一房间号）===")
    ROOM = "https://live.douyin.com/992931212705"
    s1, d1 = start("小助理 (acct=A)", "尚进工伤小助理", ROOM)
    s2, d2 = start("张老师 (acct=B)", "四川工伤张老师", ROOM)
    if s1 != 200 or s2 != 200:
        print("❌ 启不动，直接退出")
        return

    # 轮询
    print("\n=== 轮询 /api/engine/accounts ===")
    last = {}
    for i in range(8):
        time.sleep(2)
        _, d = get("/api/engine/accounts")
        items = d.get("items", [])
        running = {i.get("acct"): i.get("state") for i in items if i.get("acct")}
        print(f"  t={i*2:2d}s items={len(items)} running={running}")
        last = running
        if len(running) >= 2 and all(v in ("running", "paused") for v in running.values()):
            break
    tick_keys = {k: v for k, v in last.items()}
    both = {"尚进工伤小助理", "四川工伤张老师"}
    ok = both.issubset(tick_keys.keys()) and all(v in ("running", "paused") for v in tick_keys.values() if v)
    print(f"\n  [双账号 RUNNING] got={tick_keys} expected-keys={both} {'✅' if ok else '❌'}")

    # 重复 start 期望 409
    print("\n=== 对小助理再次 start → 期望 409 ===")
    s3, d3 = start("小助理 repeat", "尚进工伤小助理", ROOM, expect_status=409)

    # 未知账号 stop 期望 404
    print("\n=== /stop 未知账号 → 期望 404 ===")
    s4, d4 = stop_by_acct("不存在的账号", expect_status=404)

    # 多账号歧义：停小助理，再不带 acct 的 /pause → 期望 409
    print("\n=== 停小助理后 /pause 不带 acct → 期望 409 ===")
    stop_by_acct("尚进工伤小助理")
    time.sleep(1)
    s5, d5 = req("POST", "/api/engine/pause")
    print(f"  status={s5} detail={d5}")
    print(f"  [409 歧义] {'✅' if s5 == 409 else '❌'}")

    # 收尾
    print("\n=== 收尾：停张老师 ===")
    stop_by_acct("四川工伤张老师")

    print("\n" + "=" * 64)
    print("判定（✅=通过）:")
    print(f"  双账号同时 RUNNING: {'✅' if ok else '❌'}")
    print(f"  重复 start → 409:  {'✅' if s3 == 409 else '❌'}")
    print(f"  未知账号 stop → 404: {'✅' if s4 == 404 else '❌'}")
    print(f"  多任务歧义 → 409:  {'✅' if s5 == 409 else '❌'}")
    print("=" * 64)


if __name__ == "__main__":
    main()
