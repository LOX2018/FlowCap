# -*- coding: utf-8 -*-
"""HC-16 / H-32 真机前置：探测用户给出的直播间 https://live.douyin.com/218293658526?type=live

按项目既有纪律，**全部复用既有实现，不自造**：
  · 开播判定 → core.auto_dm.check_room_live（ENG-023：**只取匿名结果**，
    实测带凭证在降权账号下会误判 room_status='4'）
  · room_id  → link_resolve.resolve_live_id
  · 真实点赞 → link_resolve.fetch_room_stats（reflow/info 匿名，HC-16 ③的权威来源）

只读、零写入、零出站写操作。不打印任何凭证。
"""
import os
import sys

ROOT = r"C:\Users\LOX\Desktop\DYchajian\DYAutoDM_v2"
sys.path.insert(0, os.path.join(ROOT, "backend"))
os.environ.setdefault("DY_APP_ROOT", r"C:\temp\dyautodm_design")

URL = "https://live.douyin.com/218293658526?type=live"


def main():
    print(f"目标直播间 = {URL}")
    print()

    # ---- 1) 解析 room_id（既有实现） ----
    print("=== 1) 解析 room_id ===")
    room_id = None
    try:
        import link_resolve as lr
        out = lr.resolve_live_id(URL)
        print(f"  resolve_live_id -> {out!r}")
        if isinstance(out, (tuple, list)) and out:
            room_id = out[0]
        elif isinstance(out, str):
            room_id = out
    except Exception as e:
        print(f"  解析异常: {type(e).__name__}: {e}")
    print(f"  room_id = {room_id!r}")
    print()

    # ---- 2) 匿名判在播（既有实现 check_room_live，ENG-023 纪律） ----
    print("=== 2) 匿名判在播（check_room_live，auth=None） ===")
    is_live = None
    try:
        from core.auto_dm import check_room_live
        is_live, status, title, info = check_room_live(None, str(room_id or ""))
        print(f"  is_live     = {is_live}")
        print(f"  room_status = {status}   (2=直播中)")
        print(f"  room_title  = {title!r}")
        if isinstance(info, dict):
            for k in ("room_status", "room_title", "nickname", "user_count",
                      "like_count", "total_user"):
                if k in info:
                    print(f"  info.{k} = {info[k]!r}")
    except Exception as e:
        print(f"  check_room_live 异常: {type(e).__name__}: {e}")
    print()

    # ---- 3) 真实点赞数（reflow/info 匿名 —— HC-16 ③ 权威来源） ----
    print("=== 3) 真实点赞数（fetch_room_stats 匿名） ===")
    if room_id:
        try:
            import link_resolve as lr
            st = lr.fetch_room_stats(str(room_id))
            print(f"  fetch_room_stats -> {st!r}")
            if not st:
                print("  空 ⇒ 未开播或端点拒答（函数语义：失败返回 {}，不清零）")
        except Exception as e:
            print(f"  异常: {type(e).__name__}: {e}")
    else:
        print("  跳过：无 room_id")
    print()

    print("=== 结论 ===")
    if is_live is True:
        print("✅ 该房间**正在直播** ⇒ H-32 前置满足，可进入真机配置阶段。")
    elif is_live is False:
        print("❌ 该房间当前**未开播/已下播** ⇒ H-32 前置不满足。")
    else:
        print("⚠️ 在播状态未能判定 ⇒ 需人工在浏览器确认。")


if __name__ == "__main__":
    main()
