# -*- coding: utf-8 -*-
"""开启聚合窗口（段③ 上下文注入的第二道门），走应用自身 API。

用法：
    py enable_agg_window.py           # 设 uid_sink_window_seconds=300
    py enable_agg_window.py --revert  # 回 0
"""
import json
import sys
from pathlib import Path

import requests

BASE = "http://127.0.0.1:8000"
DEPLOY = Path(r"C:\temp\dyautodm_design")
BAK = Path(r"C:\Users\LOX\Desktop\DYchajian\DYAutoDM_v2\artifacts\fix_linkmic\_agg_window_backup.json")
KEY = "uid_sink_window_seconds"


def hdr():
    tk = json.load(open(DEPLOY / "members" / ".session.json", encoding="utf-8"))["token"]
    return {"X-Member-Token": tk, "Content-Type": "application/json"}


def cur() -> dict:
    r = requests.get(BASE + "/api/settings", headers=hdr(), timeout=20)
    d = r.json()
    return (d.get("config") or d).get("send", {}) if isinstance(d.get("config") or d, dict) else {}


def main() -> int:
    H = hdr()
    before = cur().get(KEY)
    print("改前 %s = %r" % (KEY, before))
    if "--revert" in sys.argv:
        if BAK.exists() and json.loads(BAK.read_text(encoding="utf-8")).get(KEY) is not None:
            val = json.loads(BAK.read_text(encoding="utf-8"))[KEY]
        else:
            val = 0
    else:
        BAK.parent.mkdir(parents=True, exist_ok=True)
        BAK.write_text(json.dumps({KEY: before,
                                   "ts": __import__("time").strftime("%F %T")},
                                  ensure_ascii=False), encoding="utf-8")
        print("已备份 ->", BAK)
        val = 300

    r = requests.post(BASE + "/api/settings", headers=H,
                      json={"sections": {"send": {KEY: val}}}, timeout=40)
    print("POST /api/settings HTTP", r.status_code, "->", r.text[:300])
    after = cur().get(KEY)
    print("改后 %s = %r" % (KEY, after))
    print("restart_required 见上面响应（hot 类应无需重启）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
