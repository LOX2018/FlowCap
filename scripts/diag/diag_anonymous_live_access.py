# -*- coding: utf-8 -*-
"""实测：直播流连接是否真的可以匿名（不依赖账号凭证）。

用户论断：抖音直播本身支持匿名连接查看；凭证是为了**部分直播间昵称加密**
需要写入有解密权的凭证。因此「凭证失效」不应导致连直播流都连不上。

本脚本用三组对照实测（零账号凭证、零浏览器）：
  A. 无凭证 GET 直播间页面 → 能否拿到 room_id / ttwid / status
  B. 无凭证 POST /webcast/im/fetch/ → 能否拿到首包 protobuf
  C. 无凭证 WS 握手 → 能否连上并收到帧

判定：
  A/B/C 全通 → 匿名链路成立，「凭证失效⇒连不上流」是**过度耦合**，须解耦；
  A 通 B/C 不通 → 匿名只能到页面层，首包/WS 需要某种凭证（进一步区分是哪一类）。
"""
from __future__ import annotations

import gzip
import json
import os
import re
import sys
import time
from urllib.parse import urlencode

DESIGN_ROOT = os.path.abspath(os.environ.get("FLOWCAP_APP_ROOT") or r"C:\temp\flowcap_design")
if DESIGN_ROOT == os.path.abspath(r"C:\temp\flowcap_test"):
    sys.exit("[环境门禁] 拒绝在主分支环境运行")
os.environ["FLOWCAP_APP_ROOT"] = DESIGN_ROOT
# backend 必须最早就进 sys.path（builder / static / utils 都在这里）
# ⚠️ 不能用 DESIGN_ROOT 推导（它是 C:\temp\flowcap_design，与环境无关），
#    必须基于脚本自身位置 → <repo>/FlowCap/scripts/diag/ → ../../backend
_BACKEND = os.path.abspath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "backend"))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)
os.chdir(_BACKEND)

LIVE_ID = os.environ.get("DY_PROBE_LIVE_ID") or "141042208268"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36")


def main() -> int:
    import requests

    result: dict[str, object] = {}
    print(f"直播间 = {LIVE_ID}\n（全程不带任何账号 cookie / 凭证）\n")

    # ---------- A. 无凭证 GET 直播间页面 ----------
    print("=" * 74)
    print("【A】无凭证 GET https://live.douyin.com/<id>")
    sess = requests.Session()
    try:
        r = sess.get(f"https://live.douyin.com/{LIVE_ID}",
                     headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"},
                     timeout=20)
        print(f"  HTTP {r.status_code}, {len(r.content)} bytes")
        ttwid = sess.cookies.get_dict().get("ttwid")
        print(f"  响应 Set-Cookie ttwid = {'有(len=%d)' % len(ttwid) if ttwid else '无'}")
        m = re.search(r'"roomId"\s*:\s*"(\d+)"', r.text) or \
            re.search(r'roomId["\\:\s]+(\d{15,})', r.text)
        status = re.search(r'"status"\s*:\s*(\d+)', r.text)
        user_id = re.search(r'"user_unique_id"\s*:\s*"(\d+)"', r.text) or \
            re.search(r'"user_id"\s*:\s*"(\d+)"', r.text)
        result["A_page"] = bool(r.status_code == 200)
        result["A_room_id"] = m.group(1) if m else None
        result["A_ttwid"] = ttwid
        result["A_status"] = status.group(1) if status else None
        result["A_user_id"] = user_id.group(1) if user_id else None
        print(f"  room_id={result['A_room_id']} status={result['A_status']} "
              f"user_id={result['A_user_id']}")
    except Exception as e:
        result["A_page"] = False
        print(f"  ❌ {type(e).__name__}: {e}")

    # ---------- B. 无凭证 /webcast/im/fetch/ ----------
    print()
    print("=" * 74)
    print("【B】无凭证 POST /webcast/im/fetch/（首包 protobuf）")
    try:
        room_id = result.get("A_room_id")
        user_id = result.get("A_user_id")
        if not room_id:
            print("  跳过（A 未拿到 room_id）")
            result["B_first_packet"] = False
        else:
            params = {
                "app_name": "douyin_web", "version_code": "180800",
                "webcast_sdk_version": "1.0.15", "update_version_code": "1.0.15",
                "compress": "gzip", "device_platform": "web", "cookie_enabled": "true",
                "screen_width": "1707", "screen_height": "960",
                "browser_language": "zh-CN", "browser_platform": "Win32",
                "browser_name": "Mozilla", "browser_online": "true",
                "tz_name": "Etc/GMT-8", "host": "https://live.douyin.com",
                "aid": "6383", "live_id": "1", "did_rule": "3",
                "endpoint": "live_pc", "support_wrds": "1",
                "user_unique_id": str(user_id or ""), "im_path": "/webcast/im/fetch/",
                "identity": "audience", "need_persist_msg_count": "15",
                "insert_task_id": "", "live_reason": "", "room_id": str(room_id),
                "heartbeatDuration": "0",
            }
            url = f"https://live.douyin.com/webcast/im/fetch/?{urlencode(params)}"
            rr = sess.post(url, headers={
                "User-Agent": UA, "Referer": f"https://live.douyin.com/{LIVE_ID}",
                "Origin": "https://live.douyin.com",
                "Content-Type": "application/x-www-form-urlencoded",
            }, timeout=20)
            n = len(rr.content)
            print(f"  HTTP {rr.status_code}, {n} bytes")
            looks_json = rr.content[:1] in (b"{", b"[")
            print(f"  疑似 JSON 错误体 = {looks_json}")
            if rr.content[:1] == b"{":
                print(f"  响应: {rr.content[:220].decode('utf-8', 'replace')}")
            result["B_first_packet"] = bool(rr.status_code == 200 and n > 100 and not looks_json)
    except Exception as e:
        result["B_first_packet"] = False
        print(f"  ❌ {type(e).__name__}: {e}")

    # ---------- C. 无凭证 WS 握手 ----------
    print()
    print("=" * 74)
    print("【C】无凭证 WS 握手 wss://webcast100-ws-web-hl.douyin.com/webcast/im/push/v2/")
    try:
        import websocket
        from builder.header import HeaderBuilder
        from builder.params import Params
        from utils.dy_util import generate_signature
        import static.Live_pb2 as Live_pb2

        room_id = result.get("A_room_id")
        user_id = result.get("A_user_id")
        p = Params()
        (p.add_param("app_name", "douyin_web")
          .add_param("version_code", "180800")
          .add_param("webcast_sdk_version", "1.0.15")
          .add_param("update_version_code", "1.0.15")
          .add_param("compress", "gzip")
          .add_param("device_platform", "web")
          .add_param("cookie_enabled", "true")
          .add_param("screen_width", "1707").add_param("screen_height", "960")
          .add_param("browser_language", "zh-CN").add_param("browser_platform", "Win32")
          .add_param("browser_name", "Mozilla")
          .add_param("browser_version", HeaderBuilder.ua.split("Mozilla/")[-1])
          .add_param("browser_online", "true").add_param("tz_name", "Etc/GMT-8")
          .add_param("cursor", "").add_param("internal_ext", "")
          .add_param("host", "https://live.douyin.com")
          .add_param("aid", "6383").add_param("live_id", "1").add_param("did_rule", "3")
          .add_param("endpoint", "live_pc").add_param("support_wrds", "1")
          .add_param("user_unique_id", str(user_id or ""))
          .add_param("im_path", "/webcast/im/fetch/").add_param("identity", "audience")
          .add_param("need_persist_msg_count", "15")
          .add_param("insert_task_id", "").add_param("live_reason", "")
          .add_param("room_id", str(room_id)).add_param("heartbeatDuration", "0")
          .add_param("signature", generate_signature(str(room_id), str(user_id))))
        wss = f"wss://webcast100-ws-web-hl.douyin.com/webcast/im/push/v2/?{urlencode(p.get())}"
        # 关键：cookie 传空串 = 完全匿名
        ws = websocket.create_connection(
            wss, timeout=15, origin="https://live.douyin.com", cookie="",
            header={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9",
                    "Cache-Control": "no-cache", "Pragma": "no-cache"})
        print("  WS 握手成功（匿名）")
        ws.settimeout(8)
        got = 0
        for _ in range(3):
            try:
                m = ws.recv()
            except Exception:
                continue
            if m:
                got += 1
        ws.close()
        print(f"  收到帧数 = {got}")
        result["C_ws"] = True
        result["C_frames"] = got
    except Exception as e:
        result["C_ws"] = False
        print(f"  ❌ {type(e).__name__}: {str(e)[:160]}")

    # ---------- 判定 ----------
    print()
    print("=" * 74)
    print("【判定】")
    print(json.dumps({k: v for k, v in result.items() if not k.startswith("A_ttwid")},
                     ensure_ascii=False, indent=2))
    a, b, c = result.get("A_page"), result.get("B_first_packet"), result.get("C_ws")
    print()
    if a and b and c:
        print("✅ 匿名链路成立（页面 / 首包 / WS 全通，零凭证）")
        print("   ⇒ 「凭证失效 ⇒ 连直播流都连不上」属**过度耦合**，必须解耦。")
    elif a and not (b and c):
        print("⚠ 匿名可达页面层，但首包/WS 未通过 —— 需进一步定位是哪一层需要凭证。")
    else:
        print("❌ 匿名页面层也不通 —— 需复核判据本身（探针可能有问题）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
