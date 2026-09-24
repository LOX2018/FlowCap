# -*- coding: utf-8 -*-
"""H-13 全链路·真实 AI 版（段③ 必验）。

与 h13_chain 的差别：**收尾不调 stop**（避免 stop/start 竞态把新任务硬停）。
链路：① 抓取 → ② 一沉 → ③ 聚合上下文 + **真实 AI 生成** → ④ 二沉 → ⑤ 调度 → ⑥ 发送+回查

用法：py h13_ai.py <直播间URL> [账号] [秒数] [max_target]
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
LOG = DEPLOY / "logs"
POOL = [
    {"text": "你好，我是唐律助理，可以留个联系~方式，唐律下播帮你分析", "enabled": True},
    {"text": "工友你好", "enabled": True},
    {"text": "我看下你的病例", "enabled": True},
]


def hdr():
    tk = json.load(open(DEPLOY / "members" / ".session.json", encoding="utf-8"))["token"]
    return {"X-Member-Token": tk, "Content-Type": "application/json"}


def ro():
    return sqlite3.connect("file:" + str(DB).replace("\\", "/") + "?mode=ro", uri=True)


def snap():
    c = ro()
    o = {}
    o["sink_max"] = c.execute("SELECT COALESCE(MAX(first_seen_ts),0) FROM dm_uid_sink").fetchone()[0]
    o["sink_n"] = c.execute("SELECT count(*) FROM dm_uid_sink").fetchone()[0]
    o["sink_live"] = c.execute("SELECT count(*) FROM dm_uid_sink WHERE source='live'").fetchone()[0]
    o["cross_n"] = c.execute("SELECT count(*) FROM dm_cross_sink").fetchone()[0]
    o["cross_max"] = c.execute("SELECT COALESCE(MAX(sent_ts),0) FROM dm_cross_sink").fetchone()[0]
    o["me_max"] = c.execute("SELECT COALESCE(MAX(ts),0) FROM dm_messages WHERE role='me'").fetchone()[0]
    o["me_n"] = c.execute("SELECT count(*) FROM dm_messages WHERE role='me'").fetchone()[0]
    c.close()
    return o


def log_since(keys, n=600):
    try:
        f = sorted(LOG.glob("run_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)[0]
        return [x for x in f.read_text(encoding="utf-8", errors="ignore").splitlines()[-n:]
                if any(k in x for k in keys)]
    except Exception:  # noqa: BLE001
        return []


def main() -> int:
    room = sys.argv[1] if len(sys.argv) > 1 else "https://live.douyin.com/38596030289"
    acc = sys.argv[2] if len(sys.argv) > 2 else "四川工伤张老师"
    dur = int(sys.argv[3]) if len(sys.argv) > 3 else 900
    mt = int(sys.argv[4]) if len(sys.argv) > 4 else 3
    H = hdr()
    b = snap()
    print("=== 基线 ===", flush=True)
    for k, v in b.items():
        print(f"  {k} = {v}", flush=True)

    print(f"=== 启动引擎 acct={acc} max_target={mt}（AI 已绑定 · 聚合窗口=300）===", flush=True)
    try:
        print(requests.post(BASE + "/api/engine/start", headers=H,
                            json={"acct": acc, "live_url": room, "max_target": mt,
                                  "interval": 25, "delay": "10,20", "dm_pool": POOL},
                            timeout=60).text[:220], flush=True)
    except Exception as e:  # noqa: BLE001
        print("启动异常:", e, flush=True)
        return 1

    t0 = time.time()
    seen = set()
    last = ""
    ai_seen = []
    while time.time() - t0 < dur:
        try:
            st = requests.get(BASE + "/api/tasks/current", headers=H, timeout=20).json()
            lv = st.get("live") or {}
            cn = st.get("counts") or {}
            line = ("[%4ds] %s | cap=%s sent=%s q=%s | online=%s ai=%s"
                    % (int(time.time() - t0), st.get("engine_state"), cn.get("captured"),
                       cn.get("sent"), cn.get("queue"), lv.get("online"), lv.get("dm_running")))
            if line != last:
                print(line, flush=True)
                last = line
            for ln in log_since(("[弹幕]", "[agg-sink]", "[live-ai]", "已生成文案")):
                if ln not in seen:
                    seen.add(ln)
                    if "[agg-sink]" in ln or "[live-ai]" in ln or "已生成文案" in ln:
                        ai_seen.append(ln)
                        print("   ③> " + ln[-104:], flush=True)
            if (cn.get("sent") or 0) >= mt:
                print(">>> 达成 max_target", flush=True)
                break
        except Exception as e:  # noqa: BLE001
            print("轮询异常:", e, flush=True)
        time.sleep(15)

    print("\n=== 六段收尾取证 ===", flush=True)
    print("[段① 弹幕（真机）]", flush=True)
    for x in log_since(("[弹幕]",))[-6:]:
        print("   ", x[-104:], flush=True)
    print("[段② 一沉 dm_uid_sink 本轮新行]", flush=True)
    c = ro()
    for r in c.execute("SELECT account,peer_uid,substr(nickname,1,12),source,keyword_score,"
                       "substr(aggregate_text,1,40) FROM dm_uid_sink WHERE first_seen_ts>? "
                       "ORDER BY first_seen_ts", (b["sink_max"],)):
        print("   ", r, flush=True)
    c.close()
    print("[段③ 上下文注入 + AI 生成]", flush=True)
    for x in log_since(("[agg-sink]", "[live-ai]", "已生成文案"), 600)[-8:]:
        print("   ", x[-112:], flush=True)
    print("[段④ 二沉 dm_cross_sink 本轮新行]", flush=True)
    c = ro()
    for r in c.execute("SELECT peer_uid,account_sent,source,sent_ts FROM dm_cross_sink "
                       "WHERE sent_ts>? ORDER BY sent_ts", (b["cross_max"],)):
        print("   ", r, flush=True)
    c.close()
    print("[段⑤ 调度]", flush=True)
    for x in log_since(("[dm-dispatch] 入池", "[调度]"), 600)[-8:]:
        print("   ", x[-104:], flush=True)
    print("[段⑥ 发送+回查 role=me 本轮新行]", flush=True)
    c = ro()
    for r in c.execute("SELECT msg_type,ts,substr(text,1,40),conv_id FROM dm_messages "
                       "WHERE role='me' AND ts>? ORDER BY ts", (b["me_max"],)):
        print("   ", time.strftime("%H:%M:%S", time.localtime(r[1])), "|", r[0], "|", r[2],
              "|", r[3][-16:], flush=True)
    c.close()
    s = snap()
    print("=== 快照对比 ===", flush=True)
    for k in b:
        print(f"  {k}: {b[k]} -> {s.get(k)}", flush=True)
    print("=== 完 %s（引擎保持运行，需人工/下一任务停）===" % time.strftime("%H:%M:%S"), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
