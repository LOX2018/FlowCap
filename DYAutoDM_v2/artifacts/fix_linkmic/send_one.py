# -*- coding: utf-8 -*-
"""单条真实发送 + 完整返回取证（不做任何预演）。

用法：
    py send_one.py            # 发到首个真实观众
    py send_one.py <conv_id>  # 指定会话
"""
import json
import sqlite3
import sys
import time
from pathlib import Path

import requests

BASE = "http://127.0.0.1:8000"
DEPLOY = Path(r"C:\temp\dyautodm_design")
DB = DEPLOY / "members" / "m17db0f8209156f26" / "data" / "dyautodm.db"
ACC = "尚进工伤小助理"
TEXT = "你好，我是唐律助理，可以留个联系~方式，唐律下播帮你分析"


def hdr():
    tk = json.load(open(DEPLOY / "members" / ".session.json", encoding="utf-8"))["token"]
    return {"X-Member-Token": tk, "Content-Type": "application/json"}


def me_recent(n=4):
    c = sqlite3.connect("file:" + str(DB).replace("\\", "/") + "?mode=ro", uri=True)
    r = c.execute("SELECT msg_type,ts,substr(text,1,30),conv_id FROM dm_messages "
                  "WHERE account=? AND role='me' ORDER BY rowid DESC LIMIT ?", (ACC, n)).fetchall()
    c.close()
    return r


def main() -> int:
    conv = sys.argv[1] if len(sys.argv) > 1 else None
    if not conv:
        c = sqlite3.connect("file:" + str(DB).replace("\\", "/") + "?mode=ro", uri=True)
        row = c.execute("SELECT conv_id,peer_name FROM dm_conversations WHERE account=? "
                        "AND peer_name IS NOT NULL AND peer_name<>'' "
                        "AND conv_id NOT LIKE '%:316276709526638' ORDER BY last_ts DESC LIMIT 1",
                        (ACC,)).fetchone()
        c.close()
        conv, name = row[0], row[1]
    else:
        name = "?"
    print("目标会话:", conv, "| 观众:", name, flush=True)
    print("文案:", TEXT, flush=True)
    print("=== 发送前 role=me 最近 2 条 ===", flush=True)
    for x in me_recent(2):
        print("  ", x, flush=True)

    t0 = time.time()
    try:
        r = requests.post(BASE + "/api/messages/send", headers=hdr(),
                          json={"account": ACC, "conv_id": conv, "text": TEXT}, timeout=300)
        print("耗时: %.1fs" % (time.time() - t0), flush=True)
        print("HTTP:", r.status_code, flush=True)
        print("真实返回:", r.text[:900], flush=True)
    except Exception as e:  # noqa: BLE001
        print("异常: %s: %s" % (type(e).__name__, e), flush=True)

    time.sleep(5)
    print("=== 发送后 role=me 最近 4 条（真实落库凭证）===", flush=True)
    for x in me_recent(4):
        ts = x[1]
        print("   ", time.strftime("%m-%d %H:%M:%S", time.localtime(ts)) if ts else "?", "|", x[0],
              "|", x[2], "|", x[3][-20:], flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
