# -*- coding: utf-8 -*-
"""基座(DY_Spider_base)私信链路直测脚本——完全脱离 V1/V2 封装，直接用基座原始 API。

账号名硬编码（UTF-8 文件由 Python 读取，不受 PowerShell 命令行中文编码影响）。
不导入 UTF-16 损坏的 dy_apis/login_api.py，直接用 builder.auth.DouyinAuth + dy_apis.douyin_api.DouyinAPI
（两者 UTF-8 正常）。从 .env 加载凭证（复刻 _load_auth_from_env 的核心：perepare_auth + 四件套兜底），
做完整链路：get_my_uid -> create_conversation -> send_msg -> get_conversation_list。
"""
import os
import sys
import json
import base64

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

def _load_auth(env_path):
    """从 .env 加载凭证，复刻基座 _load_auth_from_env 的核心逻辑（不 import login_api）。"""
    from builder.auth import DouyinAuth
    # 解析 .env（支持 UTF-8/UTF-8-BOM）
    content = open(env_path, encoding='utf-8-sig', errors='replace').read()
    kv = {}
    for line in content.splitlines():
        if '=' in line and not line.strip().startswith('#'):
            k, v = line.split('=', 1)
            kv[k.strip()] = v
    auth = DouyinAuth()
    cookies = kv.get('DY_COOKIES', '')
    web_protect = kv.get('DY_WEB_PROTECT', '')
    keys = kv.get('DY_KEYS', '')
    auth.perepare_auth(cookies, web_protect, keys)
    # 兜底四件套（无 web_protect/keys 时）
    if not (web_protect and keys):
        auth.ticket = kv.get('DY_TICKET') or None
        auth.ts_sign = kv.get('DY_TS_SIGN') or None
        auth.client_cert = kv.get('DY_CLIENT_CERT') or None
        pk = kv.get('DY_PRIVATE_KEY')
        if pk:
            auth.private_key = pk.replace('\\n', '\n')
        if auth.private_key:
            auth.ree_public_key = base64.b64encode(auth.private_key.encode()).decode()
    if auth.cookie and not getattr(auth, 'cookie_str', None):
        auth.cookie_str = "; ".join(f"{k}={v}" for k, v in auth.cookie.items())
    return auth, kv

def main():
    acct = "测试小助理"
    env_path = os.path.join(r"C:\temp\dyautodm_test\auto_dm\accounts", acct, ".env")
    print(f"=== base DM chain test acct={acct!r}")
    if not os.path.exists(env_path):
        print(f"!! .env not found: {env_path}")
        return

    from dy_apis.douyin_api import DouyinAPI

    # 1) load credential
    try:
        auth, kv = _load_auth(env_path)
    except Exception as e:
        print(f"!! load credential failed: {e}")
        return
    cookie = getattr(auth, "cookie", {}) or {}
    print(f"[1] load OK | cookie_fields={len(cookie)}")
    for k in ("s_v_web_id", "sid_ucp_v1", "sessionid", "sid_tt", "uid_tt", "msToken"):
        print(f"    cookie[{k}] = {(cookie.get(k) or '')[:60]}")
    print(f"    ticket={bool(auth.ticket)} ts_sign={bool(auth.ts_sign)} "
          f"client_cert={bool(auth.client_cert)} private_key={bool(auth.private_key)} "
          f"ree_public_key={bool(getattr(auth,'ree_public_key',None))}")

    # 2) get_my_uid
    try:
        uid = DouyinAPI.get_my_uid(auth)
        print(f"[2] get_my_uid OK uid={uid}")
    except Exception as e:
        print(f"!! get_my_uid failed: {e}")
        return
    if not uid:
        print("!! no uid, abort")
        return

    # 3) create_conversation to self (loopback)
    conv_id = short_id = ticket = None
    try:
        conv_id, short_id, ticket = DouyinAPI.create_conversation(auth, uid)
        print(f"[3] create_conversation OK conv_id={conv_id} short_id={short_id} ticket_present={bool(ticket)}")
    except Exception as e:
        print(f"!! create_conversation failed: {e}")

    # 4) send_msg to self (loopback)
    if conv_id:
        try:
            ok = DouyinAPI.send_msg(auth, conv_id, short_id, ticket, "base-loopback-ok")
            print(f"[4] send_msg OK={ok}")
        except Exception as e:
            print(f"!! send_msg exception: {e}")

    # 5) get_conversation_list
    try:
        convs = DouyinAPI.get_conversation_list(auth, uid, 0)
        print(f"[5] get_conversation_list OK={convs}")
    except Exception as e:
        print(f"!! get_conversation_list exception: {e}")

    print("=== base DM chain test DONE ===")

if __name__ == "__main__":
    main()
