# -*- coding: utf-8 -*-
"""H-13 直播全链路端到端（6 段）真机验证。

链路：① 抓取弹幕 → ② 一沉(dm_uid_sink) → ③ 上下文注入 → ④ 二沉(dm_cross_sink)
      → ⑤ 调度(submit_by_uid dispatch) → ⑥ 发送+回查(DB 双记录)

用法：
    py h13_chain.py <直播间URL> [账号] [秒数] [max_target]
默认：账号=四川工伤张老师（可写）；秒数=900；max_target=3
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
    """六段基线/现状快照。"""
    c = ro()
    out = {}
    try:
        out["sink_max"] = c.execute("SELECT COALESCE(MAX(first_seen_ts),0) FROM dm_uid_sink").fetchone()[0]
        out["sink_n"] = c.execute("SELECT count(*) FROM dm_uid_sink").fetchone()[0]
        out["sink_live"] = c.execute("SELECT count(*) FROM dm_uid_sink WHERE source='live'").fetchone()[0]
        out["cross_max"] = c.execute("SELECT COALESCE(MAX(sent_ts),0) FROM dm_cross_sink").fetchone()[0]
        out["cross_n"] = c.execute("SELECT count(*) FROM dm_cross_sink").fetchone()[0]
        out["me_max"] = c.execute("SELECT COALESCE(MAX(ts),0) FROM dm_messages WHERE role='me'").fetchone()[0]
        out["me_n"] = c.execute("SELECT count(*) FROM dm_messages WHERE role='me'").fetchone()[0]
    except Exception as e:  # noqa: BLE001
        out["err"] = str(e)
    c.close()
    return out


def sink_rows(since: float):
    c = ro()
    r = c.execute("SELECT account,peer_uid,nickname,source,first_seen_ts,keyword_score,"
                  "is_high_value,window_end_ts,substr(aggregate_text,1,40) FROM dm_uid_sink "
                  "WHERE first_seen_ts>? ORDER BY first_seen_ts", (since,)).fetchall()
    c.close()
    return r


def cross_rows(since: float):
    c = ro()
    r = c.execute("SELECT peer_uid,account_sent,nickname,source,sent_ts,cool_until FROM dm_cross_sink "
                  "WHERE sent_ts>? ORDER BY sent_ts", (since,)).fetchall()
    c.close()
    return r


def me_rows(since: float):
    c = ro()
    r = c.execute("SELECT msg_type,ts,substr(text,1,34),conv_id FROM dm_messages "
                  "WHERE role='me' AND ts>? ORDER BY ts", (since,)).fetchall()
    c.close()
    return r


def log_tail(n=90):
    try:
        logs = sorted(LOG.glob("run_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
        return logs[0].read_text(encoding="utf-8", errors="ignore").splitlines()[-n:] if logs else []
    except Exception:  # noqa: BLE001
        return []


def main() -> int:
    room = sys.argv[1] if len(sys.argv) > 1 else "https://live.douyin.com/38596030289"
    acc = sys.argv[2] if len(sys.argv) > 2 else "四川工伤张老师"
    dur = int(sys.argv[3]) if len(sys.argv) > 3 else 900
    mt = int(sys.argv[4]) if len(sys.argv) > 4 else 3
    H = hdr()
    b = snap()
    print("=== 六段基线 ===", flush=True)
    for k, v in b.items():
        print(f"  {k} = {v}", flush=True)

    print(f"=== 启动引擎：acct={acc} room={room} max_target={mt} ===", flush=True)
    try:
        print(requests.post(BASE + "/api/engine/start", headers=H,
                            json={"acct": acc, "live_url": room, "max_target": mt,
                                  "interval": 20, "delay": "5,15", "dm_pool": POOL},
                            timeout=60).text[:250], flush=True)
    except Exception as e:  # noqa: BLE001
        print("启动异常:", e, flush=True)
        return 1

    t0 = time.time()
    seen = set()
    last = ""
    while time.time() - t0 < dur:
        try:
            st = requests.get(BASE + "/api/tasks/current", headers=H, timeout=20).json()
            lv = st.get("live") or {}
            cn = st.get("counts") or {}
            line = ("[%4ds] %s | cap=%s sent=%s q=%s | online=%s alive=%s dm=%s | %s"
                    % (int(time.time() - t0), st.get("engine_state"), cn.get("captured"),
                       cn.get("sent"), cn.get("queue"), lv.get("online"), lv.get("alive"),
                       lv.get("dm_running"), str(st.get("status_msg"))[:22]))
            if line != last:
                print(line, flush=True)
                last = line
            for ln in log_tail():
                if "[弹幕]" in ln and ln not in seen:
                    seen.add(ln)
                    print("   ①弹幕> " + ln[-92:], flush=True)
            s = snap()
            if s.get("sink_max", 0) > b.get("sink_max", 0) and "sink_reported" not in seen:
                seen.add("sink_reported")
                print("   ②一沉> dm_uid_sink 出现新行（见收尾详表）", flush=True)
            if (cn.get("sent") or 0) >= mt:
                print(">>> 达成 max_target，提前收尾", flush=True)
                break
        except Exception as e:  # noqa: BLE001
            print("轮询异常:", e, flush=True)
        time.sleep(15)

    print("\n=== 六段收尾取证 ===", flush=True)
    print("[段①] 本轮弹幕行（真机日志）:", flush=True)
    for ln in log_tail(400):
        if "[弹幕]" in ln or "[直播间状态]" in ln or "live-ws" in ln:
            print("   ", ln[-110:], flush=True)
    print("[段②] dm_uid_sink 本轮新行:", flush=True)
    for r in sink_rows(b.get("sink_max", 0)):
        print("   ", r, flush=True)
    print("[段③] 上下文注入（日志 generate/聚合 相关）:", flush=True)
    for ln in log_tail(400):
        if any(k in ln for k in ("聚合", "aggregate", "AI", "生成", "[私信]", "source=")):
            print("   ", ln[-110:], flush=True)
    print("[段④] dm_cross_sink 本轮新行:", flush=True)
    for r in cross_rows(b.get("cross_max", 0)):
        print("   ", r, flush=True)
    print("[段⑤] 调度（日志 dispatch/submit）:", flush=True)
    for ln in log_tail(400):
        if any(k in ln for k in ("调度", "dispatch", "入队", "发送中", "submit")):
            print("   ", ln[-110:], flush=True)
    print("[段⑥] dm_messages role=me 本轮新行:", flush=True)
    for r in me_rows(b.get("me_max", 0)):
        print("   ", r, flush=True)
    s = snap()
    print("=== 收尾快照 vs 基线 ===", flush=True)
    for k in b:
        print(f"  {k}: {b[k]} -> {s.get(k)}", flush=True)
    try:
        print("停止:", requests.post(BASE + "/api/engine/stop", headers=H,
                                    json={"hard": False}, timeout=40).text[:150], flush=True)
    except Exception:  # noqa: BLE001
        pass
    print("=== 完 %s ===" % time.strftime("%H:%M:%S"), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
