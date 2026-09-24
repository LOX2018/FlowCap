# -*- coding: utf-8 -*-
"""直连项目 API 取直播间原始信息（绕过引擎判定，看 room_status 真值）。

用法：py probe_room.py <live_id> [账号名]
"""
import json
import os
import sys
from pathlib import Path

DEPLOY = Path(r"C:\temp\dyautodm_design")
BACKEND = DEPLOY / "backend"
# 源码树（dy_apis 只在源码 backend 下，部署态后端是冻结 exe）
SRC = Path(r"C:\Users\LOX\Desktop\DYchajian\DYAutoDM_v2\backend")
sys.path.insert(0, str(DEPLOY))
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(SRC))
os.environ.setdefault("DY_APP_ROOT", str(DEPLOY))


def main() -> int:
    live_id = sys.argv[1] if len(sys.argv) > 1 else "38596030289"
    acc = sys.argv[2] if len(sys.argv) > 2 else "四川工伤张老师"
    from dy_apis.douyin_api import DouyinAPI
    from dy_apis.login_api import DYLoginApi

    # 定位账号 .env
    env_path = None
    try:
        from auto_dm.accounts import env_path_of
        env_path = env_path_of(acc)
    except Exception as e:  # noqa: BLE001
        print("env_path_of 失败:", e)
    if not env_path:
        base = Path(os.environ.get("DY_APP_ROOT", "")) / "accounts"
        if base.exists():
            for p in base.rglob("*.env"):
                if acc in str(p):
                    env_path = p
                    break
    print("账号:", acc)
    print("env :", env_path)
    auth = None
    if env_path:
        try:
            auth = DYLoginApi._load_auth_from_env(str(env_path))
            print("cookie 长度:", len(getattr(auth, "cookie", "") or ""))
        except Exception as e:  # noqa: BLE001
            print("载入 auth 失败:", e)

    for label, a in (("带凭证", auth), ("匿名", None)):
        print("\n=== %s 查询 live_id=%s ===" % (label, live_id))
        try:
            if a is None:
                from core.live_hook import LiveChatHook
                p = LiveChatHook.__new__(LiveChatHook)
                p.live_id = live_id
                p.auth_ = None
                info = p._anon_live_info()
            else:
                info = DouyinAPI.get_live_info(a, live_id)
            if isinstance(info, dict):
                print("  room_status =", repr(info.get("room_status")))
                print("  room_title  =", repr(info.get("room_title")))
                print("  keys        =", list(info.keys())[:20])
                for k in ("status", "live_status", "is_live", "user_count", "total_user"):
                    if k in info:
                        print("   %s = %r" % (k, info[k]))
            else:
                print("  返回类型:", type(info), repr(info)[:200])
        except Exception as e:  # noqa: BLE001
            print("  异常: %s: %s" % (type(e).__name__, e))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
