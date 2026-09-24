# -*- coding: utf-8 -*-
"""真实业务场景发送 3 条（可指定账号）——真实观众 + 真实业务文案 + 真实发送。

用法：
    py send_real_account.py <账号名>         # 发 3 条并全程取证
    py send_real_account.py <账号名> 1       # 只发 1 条
"""
import json
import random
import sqlite3
import sys
import time
from pathlib import Path

import requests

BASE = "http://127.0.0.1:8000"
DEPLOY = Path(r"C:\temp\dyautodm_design")
DB = DEPLOY / "members" / "m17db0f8209156f26" / "data" / "dyautodm.db"
POOL = [
    "你好，我是唐律助理，可以留个联系~方式，唐律下播帮你分析",
    "工友你好",
    "我看下你的病例",
]
SKIP = ("抖音团队", "抖音", "系统", "客服", "官方")


def hdr():
    tk = json.load(open(DEPLOY / "members" / ".session.json", encoding="utf-8"))["token"]
    return {"X-Member-Token": tk, "Content-Type": "application/json"}


def ro():
    return sqlite3.connect("file:" + str(DB).replace("\\", "/") + "?mode=ro", uri=True)


def audience(acc: str) -> list:
    c = ro()
    rows = c.execute(
        "SELECT conv_id, peer_name, peer_id FROM dm_conversations WHERE account=? "
        "AND peer_name IS NOT NULL AND peer_name<>'' ORDER BY last_ts DESC", (acc,)).fetchall()
    c.close()
    return [(a, b, d) for a, b, d in rows
            if not any(k in (b or "") for k in SKIP) and a.rsplit(":", 1)[-1] != a.split(":")[2]]


def me_rows(acc: str, n=5) -> list:
    c = ro()
    r = c.execute("SELECT msg_type,ts,substr(text,1,28),conv_id FROM dm_messages "
                  "WHERE account=? AND role='me' ORDER BY rowid DESC LIMIT ?", (acc, n)).fetchall()
    c.close()
    return r


def main() -> int:
    acc = sys.argv[1] if len(sys.argv) > 1 else "四川工伤张老师"
    n_send = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    aud = audience(acc)
    print(f"账号 {acc} 的真实观众候选数: {len(aud)}", flush=True)
    if not aud:
        print("该账号无带昵称的真实观众会话，终止", flush=True)
        return 1
    random.seed()
    picks = random.sample(aud, min(n_send, len(aud)))
    for cid, nm, pid in picks:
        print("  选中:", nm, "|", cid, flush=True)

    print("\n=== 发送前 role=me 最近 2 条 ===", flush=True)
    for x in me_rows(acc, 2):
        print("  ", x, flush=True)

    ok_n = 0
    for i, (cid, nm, pid) in enumerate(picks, 1):
        text = random.choice(POOL)
        print(f"\n=== 第 {i}/{len(picks)} 条 → {nm} ===", flush=True)
        print("文案:", text, flush=True)
        t0 = time.time()
        try:
            r = requests.post(BASE + "/api/messages/send", headers=hdr(),
                              json={"account": acc, "conv_id": cid, "text": text}, timeout=300)
            print("耗时 %.1fs | HTTP %s" % (time.time() - t0, r.status_code), flush=True)
            print("真实返回:", r.text[:600], flush=True)
            if '"ok":true' in r.text.replace(" ", ""):
                ok_n += 1
        except Exception as e:  # noqa: BLE001
            print("异常: %s: %s" % (type(e).__name__, e), flush=True)
        time.sleep(random.uniform(10, 18))

    time.sleep(4)
    print("\n=== 发送后 role=me 最近 6 条（真实落库凭证 / 含 [投递验证] 标记）===", flush=True)
    for x in me_rows(acc, 6):
        ts = x[1]
        print("   ", time.strftime("%H:%M:%S", time.localtime(ts)) if ts else "?", "|", x[0],
              "|", x[2], "|", x[3][-20:], flush=True)
    print(f"\n成功条数: {ok_n}/{len(picks)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
