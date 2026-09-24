# -*- coding: utf-8 -*-
"""H-2 真实发送取证：用小助理账号向【自身会话】发真实私信（零风控目标）。

用法：
    py send_real_three.py scan          # 只读：表结构 + 该会话现状
    py send_real_three.py send <序号>   # 发第 <序号> 条并取证
"""
import json
import sqlite3
import sys
import time
from pathlib import Path

import requests

BASE = "http://127.0.0.1:8000"
ACC = "尚进工伤小助理"
CONV = "0:1:316276709526638:316276709526638"  # 自身会话（零风控）
DEPLOY = Path(r"C:\temp\dyautodm_design")
DB = DEPLOY / "members" / "m17db0f8209156f26" / "data" / "dyautodm.db"
TEXTS = [
    "[投递验证] H2-REAL-1 真实发送端到端",
    "[投递验证] H2-REAL-2 落库与skey核对",
    "[投递验证] H2-REAL-3 链路稳定性复核",
]


def token() -> str:
    return json.load(open(DEPLOY / "members" / ".session.json", encoding="utf-8"))["token"]


def conn():
    return sqlite3.connect("file:" + str(DB).replace("\\", "/") + "?mode=ro", uri=True)


def cols() -> list:
    c = conn()
    r = [x[1] for x in c.execute("PRAGMA table_info(dm_messages)")]
    c.close()
    return r


def recent(n=4) -> list:
    c = conn()
    cs = cols()
    sel = "rowid,text,msg_type,ts,account,role"
    if "skey" in cs:
        sel += ",skey"
    r = c.execute(f"SELECT {sel} FROM dm_messages WHERE conv_id=? ORDER BY rowid DESC LIMIT ?",
                  (CONV, n)).fetchall()
    c.close()
    return r


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "scan"
    if mode == "scan":
        print("dm_messages 列:", cols())
        print("自身会话最近记录:")
        for x in recent(6):
            print("  ", x)
        return 0
    idx = int(sys.argv[2])
    text = TEXTS[idx - 1]
    print(f"--- 发送第 {idx} 条 ---")
    print("发送前最近 2 条:", recent(2))
    print("文本:", text)
    t0 = time.time()
    try:
        r = requests.post(BASE + "/api/messages/send",
                          headers={"X-Member-Token": token(),
                                   "Content-Type": "application/json"},
                          json={"account": ACC, "conv_id": CONV, "text": text},
                          timeout=180)
        print("耗时: %.1fs" % (time.time() - t0))
        print("HTTP:", r.status_code)
        print("返回:", r.text[:800])
    except Exception as e:  # noqa: BLE001
        print("异常: %s: %s" % (type(e).__name__, e))
    time.sleep(4)
    print("发送后最近 3 条:", recent(3))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
