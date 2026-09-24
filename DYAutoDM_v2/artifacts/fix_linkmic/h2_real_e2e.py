# -*- coding: utf-8 -*-
"""H-2 真实业务场景端到端：真实直播间 → 真实观众弹幕 → 自动随机发放 3 条私信。

- 目标：**真实业务场景**（非自发自收）：引擎监听真实在播直播间，
  捕获真实观众弹幕，按策略词库随机抽一条自动私信该观众。
- 窗口：延长抓取（默认 360s），确保拿到真实结果。
- 取证：/api/tasks/current 轮询 + DB role=me 落库 + 日志 [弹幕]/[私信] 行。

用法：
    py h2_real_e2e.py            # 默认 360s
    py h2_real_e2e.py 600        # 自定义秒数
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
ROOM = "https://live.douyin.com/992931212705"
ACC = "尚进工伤小助理"
# 真实业务词库（策略「常用」），引擎会随机抽 enabled 的一条
POOL = [
    {"text": "你好，我是唐律助理，可以留个联系~方式，唐律下播帮你分析", "enabled": True},
    {"text": "工友你好", "enabled": True},
    {"text": "我看下你的病例", "enabled": True},
]
LOG = DEPLOY / "logs"


def hdr():
    tk = json.load(open(DEPLOY / "members" / ".session.json", encoding="utf-8"))["token"]
    return {"X-Member-Token": tk, "Content-Type": "application/json"}


def me_rows(n=6):
    """本账号 role=me 最近记录（真实发送凭证）。"""
    try:
        c = sqlite3.connect("file:" + str(DB).replace("\\", "/") + "?mode=ro", uri=True)
        r = c.execute(
            "SELECT substr(text,1,34),msg_type,ts,conv_id FROM dm_messages "
            "WHERE account=? AND role='me' ORDER BY rowid DESC LIMIT ?", (ACC, n)).fetchall()
        c.close()
        return r
    except Exception as e:  # noqa: BLE001
        return [("ERR", str(e), 0, "")]


def new_log_lines(since_ts: float):
    """读最新 run 日志里 [弹幕]/[私信]/[live-ws] 行。"""
    try:
        logs = sorted(LOG.glob("run_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not logs:
            return []
        txt = logs[0].read_text(encoding="utf-8", errors="ignore").splitlines()
        keys = ("[弹幕]", "[私信]", "[live-ws]", "收尾")
        return [ln for ln in txt[-400:] if any(k in ln for k in keys)][-14:]
    except Exception:  # noqa: BLE001
        return []


def main() -> int:
    dur = int(sys.argv[1]) if len(sys.argv) > 1 else 360
    H = hdr()
    print("=== 0) 解析直播间 ===", flush=True)
    try:
        r = requests.get(BASE + "/api/live/resolve", params={"url": ROOM}, headers=H, timeout=30)
        print("resolve:", r.text[:260], flush=True)
    except Exception as e:  # noqa: BLE001
        print("resolve 异常:", e, flush=True)

    print("=== 1) 启动引擎（max_target=3 · 真实词库 · 随机发放）===", flush=True)
    body = {"acct": ACC, "live_url": ROOM, "max_target": 3,
            "interval": 20, "delay": "5,15", "dm_pool": POOL}
    try:
        r = requests.post(BASE + "/api/engine/start", headers=H, json=body, timeout=40)
        print("start:", r.text[:300], flush=True)
    except Exception as e:  # noqa: BLE001
        print("start 异常:", e, flush=True)
        return 1

    t0 = time.time()
    last = ""
    while time.time() - t0 < dur:
        try:
            st = requests.get(BASE + "/api/tasks/current", headers=H, timeout=15).json()
            lv = st.get("live") or {}
            cn = st.get("counts") or {}
            line = ("[%4ds] %s | msg=%s | alive=%s online=%s title=%s | sent=%s cap=%s q=%s"
                    % (int(time.time() - t0), st.get("engine_state"), st.get("status_msg"),
                       lv.get("alive"), lv.get("online"), str(lv.get("room_title"))[:18],
                       cn.get("sent"), cn.get("captured"), cn.get("queue")))
            if line != last:
                print(line, flush=True)
                last = line
            if (cn.get("sent") or 0) >= 3:
                print(">>> 已达成 3 条真实发送，提前收尾", flush=True)
                break
        except Exception as e:  # noqa: BLE001
            print("轮询异常:", e, flush=True)
        time.sleep(12)

    print("=== 2) 日志取证 ===", flush=True)
    for ln in new_log_lines(t0):
        print("  ", ln, flush=True)
    print("=== 3) DB role=me 落库取证（真实发送凭证）===", flush=True)
    for x in me_rows(8):
        print("  ", x, flush=True)
    print("=== 完 %s ===" % time.strftime("%H:%M:%S"), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
