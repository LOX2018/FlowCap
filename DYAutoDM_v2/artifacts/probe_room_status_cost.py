# -*- coding: utf-8 -*-
"""匿名 vs 凭证：三种"取房间状态"方式的**体积/可用性**对照（2026-09-30）。

回答的问题：批量轮询多个直播间开播状态时，匿名路径的成本与可行性。
不打印任何凭证；只打印 HTTP 状态、字节数、解析出的 status_code。
"""
import os
import sys
import time

ROOT = r"C:\Users\LOX\Desktop\DYchajian\DYAutoDM_v2"
sys.path.insert(0, os.path.join(ROOT, "backend"))
os.environ.setdefault("DY_APP_ROOT", r"C:\temp\dyautodm_design")

import requests  # noqa: E402
from builder.header import HeaderBuilder  # noqa: E402
from utils.dy_util import tls_verify  # noqa: E402

WEB_RID = "992931212705"   # 用户日志里的在播房间
URL_LIVE = f"https://live.douyin.com/{WEB_RID}"


def probe_page_anon():
    """当前实现：匿名 GET 直播间 HTML 页，正则取 room_status。"""
    t0 = time.time()
    r = requests.get(URL_LIVE, headers={
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "accept-language": "zh-CN,zh;q=0.9",
        "referer": "https://live.douyin.com/?from_nav=1",
        "user-agent": HeaderBuilder.ua,
    }, timeout=20, verify=tls_verify())
    dt = time.time() - t0
    txt = r.text
    import re
    m = re.search(r'status\\":(\d+)', txt)
    st = m.group(1) if m else "?"
    print(f"[A] 匿名 HTML 页        HTTP={r.status_code} bytes={len(txt):>9,} "
          f"status={st} {dt*1000:.0f}ms")
    return len(txt)


def probe_api_anon():
    """上游轻量接口 /webcast/room/web/enter/（匿名）。"""
    t0 = time.time()
    h = HeaderBuilder().build("GET")
    h.set_referer(f"https://live.douyin.com/{WEB_RID}")
    params = {
        "aid": "6383", "app_name": "douyin_web", "live_id": "1",
        "device_platform": "web", "language": "zh-CN", "enter_from": "link_share",
        "cookie_enabled": "true", "web_rid": WEB_RID, "os_name": "Windows",
        "os_version": "10",
    }
    r = requests.get("https://live.douyin.com/webcast/room/web/enter/",
                     headers=h.get(), params=params, timeout=20, verify=tls_verify())
    dt = time.time() - t0
    body = r.content
    st = None
    try:
        j = r.json()
        st = j.get("status_code")
        rooms = ((j.get("data") or {}).get("data") or [])
        live = None
        if rooms:
            live = (rooms[0].get("status") if isinstance(rooms[0], dict) else None)
        print(f"[B] 匿名 API room/enter  HTTP={r.status_code} bytes={len(body):>9,} "
              f"status_code={st} room.status={live} {dt*1000:.0f}ms")
    except Exception as e:
        print(f"[B] 匿名 API room/enter  HTTP={r.status_code} bytes={len(body):>9,} "
              f"非JSON({type(e).__name__}) {dt*1000:.0f}ms")
    return len(body)


def probe_cred_page():
    """对照：带真凭证 GET 同一页（体积应为同一量级）。"""
    try:
        from dy_apis.login_api import DYLoginApi
        from auto_dm import accounts as acc
        env = acc.env_path_of("尚进工伤小助理")
        if not env:
            print("[C] 凭证页对照          跳过（账号未登记）")
            return None
        auth = DYLoginApi._load_auth_from_env(env)
        t0 = time.time()
        r = requests.get(URL_LIVE, headers={
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "referer": "https://live.douyin.com/?from_nav=1",
            "user-agent": HeaderBuilder.ua,
        }, cookies=auth.cookie, timeout=20, verify=tls_verify())
        dt = time.time() - t0
        print(f"[C] 凭证 HTML 页(对照)  HTTP={r.status_code} bytes={len(r.content):>9,} {dt*1000:.0f}ms")
        return len(r.content)
    except Exception as e:
        print(f"[C] 凭证页对照          异常/跳过: {type(e).__name__}: {e}")
        return None


if __name__ == "__main__":
    print(f"房间 web_rid = {WEB_RID}")
    print("--- 取「是否开播」的三种方式对照 ---")
    a = probe_page_anon()
    b = probe_api_anon()
    c = probe_cred_page()
    print("---")
    if a and b:
        print(f"轻量接口相对 HTML 页体积比 = {b/a*100:.2f}%  "
              f"（{b:,}B vs {a:,}B）")