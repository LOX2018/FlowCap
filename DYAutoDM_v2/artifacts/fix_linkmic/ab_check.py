# -*- coding: utf-8 -*-
"""A/B 判型：账号级只读态（609）还是全局。

对每个账号调 POST /api/accounts/{name}/check（内部走 probe_im_write：
create_conversation(自身 uid)，**不投递任何消息**），比较写校验结论。

用法：py ab_check.py
"""
import json
import time
from pathlib import Path
from urllib.parse import quote

import requests

BASE = "http://127.0.0.1:8000"
DEPLOY = Path(r"C:\temp\dyautodm_design")
ACCS = ["尚进工伤小助理", "四川工伤张老师"]


def hdr():
    tk = json.load(open(DEPLOY / "members" / ".session.json", encoding="utf-8"))["token"]
    return {"X-Member-Token": tk, "Content-Type": "application/json"}


def main() -> int:
    H = hdr()
    for a in ACCS:
        url = BASE + "/api/accounts/" + quote(a) + "/check"
        print("=" * 62, flush=True)
        print("账号:", a, flush=True)
        t0 = time.time()
        try:
            r = requests.post(url, headers=H, json={}, timeout=180)
            print("耗时 %.1fs | HTTP %s" % (time.time() - t0, r.status_code), flush=True)
            try:
                d = r.json()
                for k in ("ok", "valid", "credential", "write", "im_write", "detail",
                          "msg", "error", "checks", "result"):
                    if k in d:
                        print(f"  {k} = {json.dumps(d[k], ensure_ascii=False)[:300]}", flush=True)
                if not any(k in d for k in ("write", "im_write", "detail")):
                    print("  原始:", json.dumps(d, ensure_ascii=False)[:700], flush=True)
            except Exception:  # noqa: BLE001
                print("  文本:", r.text[:700], flush=True)
        except Exception as e:  # noqa: BLE001
            print("异常: %s: %s" % (type(e).__name__, e), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
