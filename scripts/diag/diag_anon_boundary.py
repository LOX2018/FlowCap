# -*- coding: utf-8 -*-
"""匿名链路边界判据（严格版）：区分「需要 cookie 存在」vs「需要登录态 cookie」。

第一轮实测发现：
  A 无凭证 GET 页面 → 200，拿到 room_id + ttwid（匿名 session 自带 ttwid/UIFID_TEMP）
  B 无凭证 POST im/fetch → 200 但 **0 字节**
  C 无凭证 WS → 握手被拒，handshake-msg = 'http: named cookie ...'

⇒ 关键问题：B/C 要的是「任意 cookie 存在」还是「有效登录态 cookie」？
   若只需 cookie 存在 → 用匿名 session 的 ttwid 就能连 → 凭证失效不该断流（用户对）
   若需登录态       → 凭证失效确实会断流（但与"昵称解密"是两件事，仍可分离降级）

本脚本用**同一个匿名 session**（携带 ttwid）重跑 B/C，给出定论。
"""
from __future__ import annotations

import gzip
import json
import os
import re
import sys
from urllib.parse import urlencode

_BACKEND = os.path.abspath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "backend"))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)
os.chdir(_BACKEND)

DESIGN_ROOT = os.path.abspath(os.environ.get("FLOWCAP_APP_ROOT") or r"C:\temp\flowcap_design")
if DESIGN_ROOT == os.path.abspath(r"C:\temp\flowcap_test"):
    sys.exit("[环境门禁] 拒绝在主分支环境运行")
os.environ["FLOWCAP_APP_ROOT"] = DESIGN_ROOT

LIVE_ID = os.environ.get("DY_PROBE_LIVE_ID") or "141042208268"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36")


def main() -> int:
    import requests

    out: dict[str, object] = {}
    sess = requests.Session()

    # ---- 建立匿名 session（拿 ttwid）----
    r = sess.get(f"https://live.douyin.com/{LIVE_ID}",
                 headers={"User-Agent": UA}, timeout=20)
    ck = sess.cookies.get_dict()
    room_id = None
    m = re.search(r'"roomId"\s*:\s*"(\d+)"', r.text)
    if m:
        room_id = m.group(1)
    uid_m = re.search(r'"user_unique_id"\s*:\s*"(\d+)"', r.text)
    user_id = uid_m.group(1) if uid_m else None
    print(f"匿名 session: {list(ck.keys())}  room_id={room_id} user_id={user_id}")
    out["anon_has_ttwid"] = bool(ck.get("ttwid"))
    out["anon_has_sessionid"] = bool(ck.get("sessionid"))

    # ---- B. 带匿名 cookie 的 im/fetch ----
    print("\n【B】带匿名 session cookie 的 POST /webcast/im/fetch/")
    params = {
        "app_name": "douyin_web", "version_code": "180800",
        "webcast_sdk_version": "1.0.15", "update_version_code": "1.0.15",
        "compress": "gzip", "device_platform": "web", "cookie_enabled": "true",
        "screen_width": "1707", "screen_height": "960",
        "browser_language": "zh-CN", "browser_platform": "Win32",
        "browser_name": "Mozilla", "browser_online": "true",
        "tz_name": "Etc/GMT-8", "host": "https://live.douyin.com",
        "aid": "6383", "live_id": "1", "did_rule": "3", "endpoint": "live_pc",
        "support_wrds": "1", "user_unique_id": str(user_id or ""),
        "im_path": "/webcast/im/fetch/", "identity": "audience",
        "need_persist_msg_count": "15", "insert_task_id": "", "live_reason": "",
        "room_id": str(room_id), "heartbeatDuration": "0",
    }
    try:
        rr = sess.post(f"https://live.douyin.com/webcast/im/fetch/?{urlencode(params)}",
                       headers={"User-Agent": UA, "Referer": f"https://live.douyin.com/{LIVE_ID}",
                                "Origin": "https://live.douyin.com",
                                "Content-Type": "application/x-www-form-urlencoded"},
                       timeout=20)
        n = len(rr.content)
        print(f"  HTTP {rr.status_code}, {n} bytes")
        looks_json = rr.content[:1] in (b"{", b"[")
        if looks_json:
            print(f"  响应: {rr.content[:300].decode('utf-8','replace')}")
        out["B_with_anon_cookie"] = bool(n > 100 and not looks_json)
    except Exception as e:
        out["B_with_anon_cookie"] = False
        print(f"  ❌ {type(e).__name__}: {e}")

    # ---- C. 带匿名 cookie 的 WS 握手 ----
    print("\n【C】带匿名 session cookie 的 WS 握手")
    try:
        import websocket
        from builder.header import HeaderBuilder
        from builder.params import Params
        from utils.dy_util import generate_signature

        p = Params()
        (p.add_param("app_name", "douyin_web").add_param("version_code", "180800")
          .add_param("webcast_sdk_version", "1.0.15").add_param("update_version_code", "1.0.15")
          .add_param("compress", "gzip").add_param("device_platform", "web")
          .add_param("cookie_enabled", "true")
          .add_param("screen_width", "1707").add_param("screen_height", "960")
          .add_param("browser_language", "zh-CN").add_param("browser_platform", "Win32")
          .add_param("browser_name", "Mozilla")
          .add_param("browser_version", HeaderBuilder.ua.split("Mozilla/")[-1])
          .add_param("browser_online", "true").add_param("tz_name", "Etc/GMT-8")
          .add_param("cursor", "").add_param("internal_ext", "")
          .add_param("host", "https://live.douyin.com").add_param("aid", "6383")
          .add_param("live_id", "1").add_param("did_rule", "3").add_param("endpoint", "live_pc")
          .add_param("support_wrds", "1").add_param("user_unique_id", str(user_id or ""))
          .add_param("im_path", "/webcast/im/fetch/").add_param("identity", "audience")
          .add_param("need_persist_msg_count", "15").add_param("insert_task_id", "")
          .add_param("live_reason", "").add_param("room_id", str(room_id))
          .add_param("heartbeatDuration", "0")
          .add_param("signature", generate_signature(str(room_id), str(user_id))))
        wss = f"wss://webcast100-ws-web-hl.douyin.com/webcast/im/push/v2/?{urlencode(p.get())}"
        # 关键：把匿名 session 的 cookie 串带上（ttwid 等），无 sessionid
        cookie_str = "; ".join(f"{k}={v}" for k, v in ck.items())
        print(f"  携带 cookie: {list(ck.keys())}")
        ws = websocket.create_connection(
            wss, timeout=15, origin="https://live.douyin.com", cookie=cookie_str,
            header={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9",
                    "Cache-Control": "no-cache", "Pragma": "no-cache"})
        print("  WS 握手成功（匿名 ttwid，无 sessionid）")
        ws.settimeout(10)
        got = 0
        for _ in range(4):
            try:
                mm = ws.recv()
            except Exception:
                continue
            if mm:
                got += 1
        try:
            ws.close()
        except Exception:
            pass
        print(f"  收到帧数 = {got}")
        out["C_with_anon_cookie"] = True
    except Exception as e:
        out["C_with_anon_cookie"] = False
        print(f"  ❌ {type(e).__name__}: {str(e)[:200]}")

    print("\n" + "=" * 74)
    print("【判定】")
    b = out.get("B_with_anon_cookie")
    c = out.get("C_with_anon_cookie")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    print()
    if c:
        print("✅ 结论：直播流连接只需『cookie 存在』（匿名 ttwid 即可），")
        print("   不要求有效登录态 —— 凭证失效不应导致断流（与用户判断一致）。")
    elif b:
        print("⚠ 首包可匿名取，但 WS 仍拒 —— 需继续定位 WS 的具体要求。")
    else:
        print("❌ 首包与 WS 都需要更强凭证 —— 需进一步区分是 ttwid 还是签名。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
