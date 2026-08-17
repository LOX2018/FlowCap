# -*- coding: utf-8 -*-
"""纯基座(DY_Spider_base)扫码 + 私信链路测试——完全隔离 V1/V2。

流程：
1) 用基座 accounts.add_account 在基座自己的 auto_dm/accounts/<账号>/.env 建独立账号
   （全新空 .env + 独立 profile，不碰 C:\\temp\\dyautodm_test 与 V1/V2 任何现有凭证）。
2) 用基座 get_login_auth(headless=False, force=True) 打开指纹浏览器，用户单独扫码。
3) 扫码后凭证写回基座自己的 .env。
4) 立即用基座 DouyinAPI 做完整私信链路回环：get_my_uid -> create_conversation -> send_msg
   -> get_conversation_list，确认干净凭证能真实发送私信。
运行（真实 python，无命令行中文参数）：
    python _base_scan_test.py
"""
import os
import sys
import asyncio
import json

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

# 基座独立测试账号（完全隔离 V1/V2）
ACCT = "基座测试"


async def scan_and_save():
    from auto_dm import accounts
    from dy_apis.login_api import DYLoginApi

    # 1) 建独立账号（全新空 .env + 独立 profile，隔离）
    env_path = accounts.add_account(ACCT)
    print(f"[1] 独立账号已建 env_path={env_path}")

    # 2) 扫码（force=True 强制，打开指纹浏览器；用户单独扫码）
    api = DYLoginApi()
    auth = await api.get_login_auth(headless=False, env_path=env_path, force=True)
    cookie = getattr(auth, "cookie", {}) or {}
    print(f"[2] 扫码完成 cookie_fields={len(cookie)}")
    for k in ("s_v_web_id", "sid_ucp_v1", "sessionid", "sid_tt", "uid_tt"):
        print(f"    cookie[{k}] = {(cookie.get(k) or '')[:60]}")
    print(f"    ticket={bool(auth.ticket)} ts_sign={bool(auth.ts_sign)} "
          f"client_cert={bool(auth.client_cert)} private_key={bool(auth.private_key)} "
          f"ree_public_key={bool(getattr(auth,'ree_public_key',None))}")
    wp = getattr(auth, "web_protect_str", "")
    print(f"    web_protect len={len(wp or '')} | keys len={len(getattr(auth,'keys_str','') or '')}")

    # 3) 凭证已由 get_login_auth 内部 save_credential 写回 env_path
    print(f"[3] 凭证已写回 {env_path}")

    # 4) 回环链路测试
    from dy_apis.douyin_api import DouyinAPI
    uid = DouyinAPI.get_my_uid(auth)
    print(f"[4] get_my_uid uid={uid}")
    if not uid:
        print("!! no uid after scan, chain test aborted")
        return auth
    conv_id = short_id = ticket = None
    try:
        conv_id, short_id, ticket = DouyinAPI.create_conversation(auth, uid)
        print(f"[4] create_conversation OK conv_id={conv_id} short_id={short_id} ticket_present={bool(ticket)}")
    except Exception as e:
        print(f"!! create_conversation failed: {e}")
    if conv_id:
        try:
            ok = DouyinAPI.send_msg(auth, conv_id, short_id, ticket, "base-scan-loopback-ok")
            print(f"[4] send_msg OK={ok}")
        except Exception as e:
            print(f"!! send_msg exception: {e}")
    # get_conversation_list 依赖 blackboxprotobuf（与 protobuf 7.x 冲突，非私信发送必需），
    # 扫码/建会话/发消息已覆盖核心链路，此处不再调用。
    return auth


def main():
    print("=== 纯基座扫码+私信链路测试（隔离 V1/V2）===")
    auth = asyncio.run(scan_and_save())
    print("=== 完成 ===")


if __name__ == "__main__":
    main()
