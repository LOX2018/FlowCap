# -*- coding: utf-8 -*-
"""绑定张老师 → 工伤赔偿留资助手，并启用该 Agent（走应用自身 API）。

- 先备份原值（可回滚）
- 用 /api/ai/bind + /api/ai/agents（= UI 的走法），非直写 KV
- 验证：GET /api/ai/bind、GET /api/ai/live_dm_state

用法：py bind_agent.py [--revert]
"""
import json
import sqlite3
import sys
from pathlib import Path

import requests

BASE = "http://127.0.0.1:8000"
DEPLOY = Path(r"C:\temp\dyautodm_design")
DB = DEPLOY / "members" / "m17db0f8209156f26" / "data" / "dyautodm.db"
ACC = "四川工伤张老师"
AID = "ag_88387f9ee4e34dc1"
BAK = Path(r"C:\Users\LOX\Desktop\DYchajian\DYAutoDM_v2\artifacts\fix_linkmic\_agent_binding_backup.json")


def hdr():
    tk = json.load(open(DEPLOY / "members" / ".session.json", encoding="utf-8"))["token"]
    return {"X-Member-Token": tk, "Content-Type": "application/json"}


def kv(key):
    c = sqlite3.connect("file:" + str(DB).replace("\\", "/") + "?mode=ro", uri=True)
    v = c.execute("SELECT value FROM kv_store WHERE key=?", (key,)).fetchone()
    c.close()
    return json.loads(v[0]) if v else None


def main() -> int:
    H = hdr()
    binds = kv("ai_account_agent") or {}
    agents = kv("ai_agents") or {}
    cur = agents.get(AID) or {}

    if "--revert" in sys.argv:
        if not BAK.exists():
            print("无备份，无法回滚")
            return 1
        b = json.loads(BAK.read_text(encoding="utf-8"))
        print("回滚绑定 ->", b["bindings"])
        print("回滚 agent.config.enabled ->", b["agent_enabled"])
        requests.post(BASE + "/api/ai/bind", headers=H,
                      json={"account": ACC, "agent_id": b["bindings"].get(ACC, "")}, timeout=30)
        cfg = dict(cur.get("config") or {})
        cfg["enabled"] = b["agent_enabled"]
        requests.post(BASE + "/api/ai/agents", headers=H,
                      json={"id": AID, "name": cur.get("name", ""), "config": cfg}, timeout=30)
        print("已回滚")
        return 0

    # ① 备份
    BAK.parent.mkdir(parents=True, exist_ok=True)
    BAK.write_text(json.dumps({
        "ts": __import__("time").strftime("%Y-%m-%d %H:%M:%S"),
        "bindings": binds,
        "agent_enabled": (cur.get("config") or {}).get("enabled"),
        "agent_config": cur.get("config"),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print("已备份 ->", BAK)
    print("  改前绑定 =", binds)
    print("  改前 agent.enabled =", (cur.get("config") or {}).get("enabled"))

    # ② 绑定
    r = requests.post(BASE + "/api/ai/bind", headers=H,
                      json={"account": ACC, "agent_id": AID}, timeout=40)
    print("\n[绑定] HTTP", r.status_code, "->", r.text[:300])

    # ③ 启用 Agent（保留其余键，仅 enabled=True）
    cfg = dict(cur.get("config") or {})
    cfg["enabled"] = True
    r2 = requests.post(BASE + "/api/ai/agents", headers=H,
                       json={"id": AID, "name": cur.get("name") or "工伤赔偿留资助手（唐律）",
                             "config": cfg}, timeout=40)
    print("[启用] HTTP", r2.status_code, "->", r2.text[:300])

    # ④ 验证
    print("\n=== 验证 ===")
    rb = requests.get(BASE + "/api/ai/bind", headers=H, timeout=20)
    print("GET /api/ai/bind :", rb.text[:300])
    print("KV 绑定(读回)    :", kv("ai_account_agent"))
    print("KV agent.enabled :", (kv("ai_agents") or {}).get(AID, {}).get("config", {}).get("enabled"))
    for params in ({"acct": ACC}, {"account": ACC}):
        rl = requests.get(BASE + "/api/ai/live_dm_state", headers=H, params=params, timeout=20)
        if rl.status_code == 200:
            print("live_dm_state(%s):" % list(params)[0], rl.text[:400])
            break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
