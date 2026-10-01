# -*- coding: utf-8 -*-
"""HC-16 / H-32 真机前置：解析用户给出的直播间 https://live.douyin.com/218293658526?type=live

只读，零写入，零出站写操作。三步：
  1) 匿名解析 room_id / live_id（复用 link_resolve.resolve_live_id + _extract_reflow_room_id）
  2) 匿名取房间统计（link_resolve.fetch_room_stats）→ 看 like_count 是否有真值
  3) 匿名看是否在播（room/web/enter 的 room.status）
不打印任何凭证。
"""
import os
import sys

ROOT = r"C:\Users\LOX\Desktop\DYchajian\DYAutoDM_v2"
sys.path.insert(0, os.path.join(ROOT, "backend"))
os.environ.setdefault("DY_APP_ROOT", r"C:\temp\dyautodm_design")

URL = "https://live.douyin.com/218293658526?type=live"
WEB_RID = "218293658526"

import requests  # noqa: E402
from builder.header import HeaderBuilder  # noqa: E402
from utils.dy_util import tls_verify  # noqa: E402


def step1_resolve():
    print("=== 1) 解析 room_id / live_id ===")
    try:
        import link_resolve as lr
    except Exception as e:
        print(f"  import link_resolve 失败: {type(e).__name__}: {e}")
        return None, None
    # a) 从 URL 提取 web_rid → reflow room_id
    rid = None
    try:
        rid = lr._extract_reflow_room_id(URL)
        print(f"  _extract_reflow_room_id(URL) = {rid!r}")
    except Exception as e:
        print(f"  _extract_reflow_room_id 异常: {type(e).__name__}: {e}")
    # b) 完整解析（可能触发网络/浏览器，加保护）
    try:
        out = lr.resolve_live_id(URL)
        print(f"  resolve_live_id(URL) = {out!r}")
    except Exception as e:
        print(f"  resolve_live_id 异常（可接受，非致命）: {type(e).__name__}: {e}")
    return rid, None


def step2_stats(rid):
    print("=== 2) 匿名取房间统计（红心真值来源） ===")
    if not rid:
        print("  跳过：无 rid")
        return
    try:
        import link_resolve as lr
        st = lr.fetch_room_stats(rid)
        if not st:
            print("  fetch_room_stats 返回空 ⇒ 该房间当前可能未开播或端点拒答")
            return
        for k in ("like_count", "total_user", "user_count", "status", "title"):
            if k in st:
                print(f"  {k} = {st[k]}")
        print(f"  完整字段: {sorted(st.keys())}")
    except Exception as e:
        print(f"  fetch_room_stats 异常: {type(e).__name__}: {e}")


def step3_live_status():
    print("=== 3) 匿名查在播状态（room/web/enter） ===")
    try:
        h = HeaderBuilder().build("GET")
        h.set_referer(f"https://live.douyin.com/{WEB_RID}")
        params = {
            "aid": "6383", "app_name": "douyin_web", "live_id": "1",
            "device_platform": "web", "language": "zh-CN", "enter_from": "link_share",
            "cookie_enabled": "true", "web_rid": WEB_RID, "os_name": "Windows",
            "os_version": "10",
        }
        r = requests.get("https://live.douyin.com/webcast/room/web/enter/",
                         headers=h.get(), params=params, timeout=20,
                         verify=tls_verify())
        print(f"  HTTP={r.status_code} bytes={len(r.content):,}")
        try:
            j = r.json()
            print(f"  status_code = {j.get('status_code')}")
            rooms = ((j.get("data") or {}).get("data") or [])
            if rooms and isinstance(rooms[0], dict):
                d = rooms[0]
                print(f"  room.status = {d.get('status')}  (2=在播, 4=未开播/回放)")
                print(f"  room.title  = {str(d.get('title'))[:60]!r}")
                print(f"  room.id_str = {d.get('id_str')}")
                print(f"  like_count  = {d.get('like_count')}")
                print(f"  user_count  = {((d.get('stats') or {}).get('total_user'))}")
                owner = d.get("owner") or {}
                print(f"  owner.nick  = {owner.get('nickname')}")
            else:
                print(f"  data.data 为空 ⇒ {str(j)[:300]}")
        except Exception as e:
            print(f"  非 JSON: {type(e).__name__}  前 300B: {r.text[:300]!r}")
    except Exception as e:
        print(f"  请求异常: {type(e).__name__}: {e}")


if __name__ == "__main__":
    print(f"目标直播间 = {URL}")
    rid, _ = step1_resolve()
    step2_stats(rid)
    step3_live_status()
    print("=== 结论 ===")
    print("若 step3 的 room.status == 2 ⇒ 在播，可进入真机验证配置阶段。")
