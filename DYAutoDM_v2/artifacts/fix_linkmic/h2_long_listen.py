# -*- coding: utf-8 -*-
"""H-2 真实业务场景·长监听：真实在播直播间 → 真实观众弹幕 → 自动随机发私信。

严格按真实业务链路：**不再定向选人**，而是让引擎监听真实弹幕，
捕获到真实观众发言后，按策略词库随机抽一条自动私信该观众。

- 窗口：默认 900s（延长等待，直到拿到真实结果）
- 目标：max_target=3（随机发放 3 条）
- 取证：counts.captured/sent 变化 + 日志 [弹幕]/[私信] 行 + DB role=me 落库

用法：
    py h2_long_listen.py          # 900s
    py h2_long_listen.py 1500     # 自定义秒数
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
ROOM = "https://live.douyin.com/992931212705"
ACC = "尚进工伤小助理"
POOL = [
    {"text": "你好，我是唐律助理，可以留个联系~方式，唐律下播帮你分析", "enabled": True},
    {"text": "工友你好", "enabled": True},
    {"text": "我看下你的病例", "enabled": True},
]


def hdr():
    tk = json.load(open(DEPLOY / "members" / ".session.json", encoding="utf-8"))["token"]
    return {"X-Member-Token": tk, "Content-Type": "application/json"}


def me_rows(n=8):
    try:
        c = sqlite3.connect("file:" + str(DB).replace("\\", "/") + "?mode=ro", uri=True)
        r = c.execute("SELECT substr(text,1,28),msg_type,ts,conv_id FROM dm_messages "
                      "WHERE account=? AND role='me' ORDER BY rowid DESC LIMIT ?",
                      (ACC, n)).fetchall()
        c.close()
        return r
    except Exception as e:  # noqa: BLE001
        return [("ERR", str(e), 0, "")]


def tail_lines():
    try:
        logs = sorted(LOG.glob("run_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not logs:
            return []
        return logs[0].read_text(encoding="utf-8", errors="ignore").splitlines()[-150:]
    except Exception:  # noqa: BLE001
        return []


def main() -> int:
    dur = int(sys.argv[1]) if len(sys.argv) > 1 else 900
    H = hdr()
    print("=== 启动引擎（真实弹幕监听 · max_target=3 · 随机发放）===", flush=True)
    body = {"acct": ACC, "live_url": ROOM, "max_target": 3,
            "interval": 20, "delay": "5,15", "dm_pool": POOL}
    try:
        print(requests.post(BASE + "/api/engine/start", headers=H, json=body,
                            timeout=40).text[:220], flush=True)
    except Exception as e:  # noqa: BLE001
        print("启动异常:", e, flush=True)
        return 1

    t0 = time.time()
    seen = set()
    last = ""
    while time.time() - t0 < dur:
        try:
            st = requests.get(BASE + "/api/tasks/current", headers=H, timeout=15).json()
            cn = st.get("counts") or {}
            lv = st.get("live") or {}
            line = ("[%4ds] %s | cap=%s sent=%s q=%s | online=%s alive=%s dm=%s"
                    % (int(time.time() - t0), st.get("engine_state"), cn.get("captured"),
                       cn.get("sent"), cn.get("queue"), lv.get("online"), lv.get("alive"),
                       lv.get("dm_running")))
            if line != last:
                print(line, flush=True)
                last = line
            for ln in tail_lines():
                if "[弹幕]" in ln and ln not in seen:
                    seen.add(ln)
                    print("   弹幕> " + ln[-88:], flush=True)
            if (cn.get("sent") or 0) >= 3:
                print(">>> 已达成 3 条「真实弹幕触发」发送", flush=True)
                break
        except Exception as e:  # noqa: BLE001
            print("轮询异常:", e, flush=True)
        time.sleep(15)

    print("=== 日志取证（[弹幕]/[私信]/收尾）===", flush=True)
    for ln in tail_lines():
        if any(k in ln for k in ("[弹幕]", "[私信]", "收尾", "LIVE-022")):
            print("  ", ln[-110:], flush=True)
    print("=== DB role=me 取证 ===", flush=True)
    for x in me_rows(8):
        print("  ", x, flush=True)
    try:
        print("停止:", requests.post(BASE + "/api/engine/stop", headers=H,
                                    json={"hard": False}, timeout=30).text[:150], flush=True)
    except Exception:  # noqa: BLE001
        pass
    print("=== 完 %s ===" % time.strftime("%H:%M:%S"), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
