# -*- coding: utf-8 -*-
"""HC-16 写接口 403 取证：**只读自证**，绝不发弹幕/点赞。

目标：区分 403 是 (a) 凭证态问题 (b) 签名/参数问题 (c) 真实风控。
做法：不调用 sendMsgInRoom（那是写接口），只做三件只读的事：
  1) 加载真实凭证，检查关键字段是否齐备（cookie / msToken / 私钥可解析）
  2) 调 _live_chat_room_id 看 web_rid → 真实 room_id 是否成功归一化
  3) 跑一次**只读**的 get_live_info（同一域名、同一签名链路），看是否也 403
     ⇒ 若只读也 403 ⇒ 是账号态/风控，不是弹幕参数问题（关键对照）
不打印任何凭证明文。
"""
import os
import sys

ROOT = r"C:\Users\LOX\Desktop\DYchajian\DYAutoDM_v2"
sys.path.insert(0, os.path.join(ROOT, "backend"))
os.environ.setdefault("DY_APP_ROOT", r"C:\temp\dyautodm_design")

WEB_RID = "218293658526"
ACCOUNT = "尚进工伤小助理"


def main():
    print("=== 1) 凭证关键字段（不明文） ===")
    auth = None
    try:
        from dy_apis.login_api import DYLoginApi
        from auto_dm import accounts as acc
        env = acc.env_path_of(ACCOUNT)
        print(f"  env_path 存在 = {bool(env)}")
        auth = DYLoginApi._load_auth_from_env(env)
    except Exception as e:
        print(f"  凭证加载失败: {type(e).__name__}: {e}")
        return
    if auth is None:
        print("  凭证为空 ⇒ 403 属凭证问题")
        return

    cookie = getattr(auth, "cookie", None) or {}
    print(f"  cookie 键数       = {len(cookie)}")
    print(f"  cookie_str 长度   = {len(getattr(auth, 'cookie_str', '') or '')}")
    print(f"  msToken 长度      = {len(getattr(auth, 'msToken', '') or '')}")
    for k in ("sessionid", "msToken", "ttwid", "UIFID", "odin_tt", "bd_ticket"):
        v = cookie.get(k) if isinstance(cookie, dict) else None
        print(f"  cookie.{k:<12} = {'有(%d字)' % len(v) if v else '❌ 无'}")
    # 私钥可解析性（写接口独有的失败点）
    try:
        from utils.dy_util import _encode_private_key  # noqa: F401
    except Exception:
        pass
    pk = getattr(auth, "private_key", None) or ""
    pk_src = ""
    for attr in ("privateKey", "private_key", "_private_key"):
        pk_src = getattr(auth, attr, None) or ""
        if pk_src:
            break
    print(f"  私钥: 有={bool(pk_src)} 含真实换行={chr(10) in str(pk_src)}")

    print()
    print("=== 2) web_rid → 真实 room_id（_live_chat_room_id） ===")
    try:
        from dy_apis.douyin_api import DouyinAPI
        real = DouyinAPI._live_chat_room_id(auth, WEB_RID)
        print(f"  web_rid  = {WEB_RID}")
        print(f"  room_id  = {real}")
        print(f"  归一化成功 = {real != WEB_RID}")
    except Exception as e:
        print(f"  失败: {type(e).__name__}: {e}")
        real = WEB_RID

    print()
    print("=== 3) 对照：只读接口 get_live_info 是否也 403 ===")
    print("    （同一账号、同一域名、同一签名链路；只读，零副作用）")
    try:
        from dy_apis.douyin_api import DouyinAPI
        info = DouyinAPI.get_live_info(auth, WEB_RID)
        if isinstance(info, dict):
            print(f"  只读成功: room_status={info.get('room_status')} "
                  f"room_id={info.get('room_id')}")
            print(f"  ⇒ 只读通 ⇒ 403 更可能是**弹幕写接口专属**（风控/参数）")
        else:
            print(f"  只读返回非 dict: {type(info)} {str(info)[:200]}")
    except Exception as e:
        print(f"  只读也失败: {type(e).__name__}: {e}")
        print(f"  ⇒ 只读也挂 ⇒ 403 是**账号态/风控**，不是弹幕参数问题")


if __name__ == "__main__":
    main()
