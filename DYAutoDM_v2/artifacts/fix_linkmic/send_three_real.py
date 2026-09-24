# -*- coding: utf-8 -*-
"""真实业务场景定向发送 3 条：用小助理账号 → 真实观众会话，随机抽业务文案。

- 目标选择：小助理 `dm_conversations` 中的**真实观众**（排除自身会话/系统会话），随机 3 个
- 文案：策略「常用」词库（唐律助理业务文案），逐条随机抽
- 取证：发送返回 + DB `role='me'` 落库（新 ts）+ 日志

用法：
    py send_three_real.py pick     # 只读：预演随机选中的 3 个目标
    py send_three_real.py send     # 真发 3 条并取证
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
ACC = "尚进工伤小助理"
SELF_UID = "316276709526638"
SELF_CONV = f"0:1:{SELF_UID}:{SELF_UID}"
POOL = [
    "你好，我是唐律助理，可以留个联系~方式，唐律下播帮你分析",
    "工友你好",
    "我看下你的病例",
]
SKIP_NAME = ("抖音团队", "抖音", "系统", "客服", "官方")


def hdr():
    tk = json.load(open(DEPLOY / "members" / ".session.json", encoding="utf-8"))["token"]
    return {"X-Member-Token": tk, "Content-Type": "application/json"}


def con_ro():
    return sqlite3.connect("file:" + str(DB).replace("\\", "/") + "?mode=ro", uri=True)


def candidates() -> list:
    c = con_ro()
    rows = c.execute(
        "SELECT conv_id, peer_name, peer_id FROM dm_conversations "
        "WHERE account=? AND peer_name IS NOT NULL AND peer_name<>'' "
        "AND conv_id<>? ORDER BY last_ts DESC", (ACC, SELF_CONV)).fetchall()
    c.close()
    out = []
    for cid, nm, pid in rows:
        if any(k in (nm or "") for k in SKIP_NAME):
            continue
        if cid.endswith(":" + SELF_UID):
            continue
        out.append((cid, nm, pid))
    return out


def me_rows(n=6):
    c = con_ro()
    r = c.execute("SELECT substr(text,1,30),msg_type,ts,conv_id FROM dm_messages "
                  "WHERE account=? AND role='me' ORDER BY rowid DESC LIMIT ?", (ACC, n)).fetchall()
    c.close()
    return r


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "pick"
    cand = candidates()
    print("候选真实观众数:", len(cand), flush=True)
    random.seed()  # 真随机
    picks = random.sample(cand, min(3, len(cand)))
    print("随机选中 3 个目标：", flush=True)
    for cid, nm, pid in picks:
        print("   -", nm, "|", cid, flush=True)
    if mode != "send":
        print("（预演模式，未发送）", flush=True)
        return 0

    print("\n=== 发送前 role=me 最近 2 条 ===", flush=True)
    for x in me_rows(2):
        print("  ", x, flush=True)

    sent = []
    for i, (cid, nm, pid) in enumerate(picks, 1):
        text = random.choice(POOL)
        print(f"\n=== 第 {i}/3 条 → {nm} ===", flush=True)
        print("文案:", text, flush=True)
        t0 = time.time()
        try:
            r = requests.post(BASE + "/api/messages/send", headers=hdr(),
                              json={"account": ACC, "conv_id": cid, "text": text}, timeout=180)
            print("耗时 %.1fs | HTTP %s" % (time.time() - t0, r.status_code), flush=True)
            print("返回:", r.text[:600], flush=True)
            sent.append((nm, cid, r.status_code, text))
        except Exception as e:  # noqa: BLE001
            print("异常: %s: %s" % (type(e).__name__, e), flush=True)
        time.sleep(random.uniform(12, 20))  # 随机间隔（真人节奏）

    print("\n=== 发送后 role=me 最近 6 条（真实发送凭证）===", flush=True)
    time.sleep(4)
    for x in me_rows(6):
        ts = x[2]
        print("   ", time.strftime("%H:%M:%S", time.localtime(ts)) if ts else "?", "|", x[0],
              "|", x[3], flush=True)
    print("\n汇总:", flush=True)
    for s in sent:
        print("   ", s, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
