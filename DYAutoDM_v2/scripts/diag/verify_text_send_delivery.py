# -*- coding: utf-8 -*-
"""私信文本发送投递验证（以**接口响应**为准，不以日志/落库为准）。

## 为什么存在
历史上多次把「日志写『私信发送成功』」当成投递成功，实际对方收不到。
本脚本只信服务端回包，并把判定口径与 GitHub 上游一致：

- `zhinjs/douyin-im` `isMessageDelivered()`：
  statusCode==0 && server_message_id 非空非 0 && check_code != 8610
- 其日志语义：8101=已投递 / 10502=审核中 / 8610=安全检查未通过(未投递)

## 判定流程
① create_conversation 取会话 → ② POST /v1/message/send →
③ 解析 body.100（server_message_id / status / check_code / check_message）→
④ cmd 301 回读该会话，核对 server_message_id 是否真的出现。

③④同时成立才算「已投递」。

## 安全
- 只发**文本**；默认要求目标会话是**已互关**的历史会话（--peer 必填，无默认值）。
- 环境门禁：DY_APP_ROOT=C:\\temp\\dyautodm_test（主分支）时拒绝运行。
- 不打印 cookie / ticket / 私钥。

## 用法
    python scripts/diag/verify_text_send_delivery.py --account <名> --peer <uid> [--text <内容>]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid

DESIGN_ROOT = os.path.abspath(os.environ.get("DY_APP_ROOT") or r"C:\temp\dyautodm_design")
_FORBIDDEN = {os.path.abspath(r"C:\temp\dyautodm_test")}
BACKEND = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "backend"))


def _bootstrap() -> str:
    if DESIGN_ROOT in _FORBIDDEN:
        sys.exit("[环境门禁] DY_APP_ROOT 指向主分支环境(%s)，拒绝运行" % DESIGN_ROOT)
    os.environ["DY_APP_ROOT"] = DESIGN_ROOT
    sys.path.insert(0, BACKEND)
    os.chdir(BACKEND)
    with open(os.path.join(DESIGN_ROOT, "members", ".session.json"), encoding="utf-8") as f:
        sess = json.load(f)
    os.environ["DY_MEMBER"] = sess["member_id"]
    os.environ["DY_MEMBER_KEY"] = sess["master_key"]
    return os.path.join(DESIGN_ROOT, "members", sess["member_id"], "auto_dm", "accounts")


def _fields(raw: bytes) -> dict:
    """（已收敛）宽容 protobuf 字段遍历 —— 唯一实现在 services/send_response.py。

    保留本函数据名以免破坏外部引用；实际委托给唯一实现（SSOT）。
    """
    from services.send_response import _fields as _f  # noqa: PLC0415
    return _f(raw)


def parse_send_response(raw: bytes) -> dict:
    """（已收敛）解析 message/send 响应 —— 唯一实现在 services/send_response.py。

    2026-09-23 审计 P0-1：同一协议此前在本脚本与生产链路各写一遍；
    现生产链路（dy_apis/client_im.py）与本脚本**共用**同一实现，
    避免判定口径漂移（Duplicate Implementation）。
    """
    from services.send_response import parse_send_response as _p  # noqa: PLC0415
    return _p(raw)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", required=True)
    ap.add_argument("--peer", required=True, help="对端 uid（必填；建议用已互关的历史会话）")
    ap.add_argument("--text", default=None)
    args = ap.parse_args(argv)

    acc_root = _bootstrap()
    from dy_apis.login_api import DYLoginApi
    from dy_apis.douyin_api import DouyinAPI
    import requests
    from builder.header import HeaderBuilder, HeaderType
    from builder.proto import ProtoBuilder
    from utils.dy_util import generate_a_bogus, splice_url
    from utils.tls_policy import tls_verify

    auth = DYLoginApi._load_auth_from_env(os.path.join(acc_root, args.account, ".env"))
    my_uid = int(DouyinAPI.get_my_uid(auth, force_probe=True))
    peer = int(args.peer)
    text = args.text or f"[投递验证]{uuid.uuid4().hex[:6]}"
    print(f"账号={args.account} 自身uid={my_uid} 目标uid={peer}")
    print(f"文案={text!r}")

    conv_id, short_id, ticket = DouyinAPI.create_conversation(auth, peer)
    print(f"① create_conversation OK: {conv_id}")

    itok, idev = DouyinAPI.get_identity_security_token(auth)
    cmid = str(uuid.uuid4())
    req = ProtoBuilder.build_send_message_request(
        auth, conv_id, short_id, ticket, text,
        identity_security_token=itok, identity_security_device_id=idev,
        client_message_id=cmid)
    # 打印实际发出的 content（验证 aweType 信封真的编进去了）
    print(f"   实际发出 content = {req.body.send_message_body.content[:120]!r}")
    print(f"   message_type = {req.body.send_message_body.message_type}")

    headers = HeaderBuilder().build(HeaderType.PROTOBUF)
    headers.set_header("referer", "https://www.douyin.com/")
    headers.with_bd("/v1/message/send", auth)
    params = {"msToken": getattr(auth, "msToken", "") or ""}
    params["a_bogus"] = generate_a_bogus(splice_url(params))
    webid = (auth.cookie or {}).get("s_v_web_id", "")
    params["verifyFp"] = webid
    params["fp"] = webid
    resp = requests.post("https://imapi.douyin.com/v1/message/send", params=params,
                         headers=headers.get(), verify=tls_verify(), cookies=auth.cookie,
                         data=req.SerializeToString())
    print(f"② send HTTP {resp.status_code} bytes={len(resp.content)}")
    parsed = parse_send_response(resp.content)
    print("   响应解析 =", json.dumps(parsed, ensure_ascii=False))

    b = parsed.get("body100") or {}
    sid = str(b.get("server_message_id") or "")
    code = b.get("check_code")
    if not sid or sid == "0":
        print("❌ 未投递：服务端未返回 server_message_id")
        return 2
    print(f"③ server_message_id={sid} status={b.get('status')} check_code={code}")
    if code == 8610:
        print("❌ 内容安全检查未通过（8610），消息未投递")
        return 3
    if code == 10502:
        print("⚠️ 已提交但审核中（10502），对方未必立即可见")
    elif code == 8101:
        print("✅ 服务端标记已投递（8101）")

    from auto_dm.conversation_capture import fetch_conversation_history
    hist = fetch_conversation_history(auth, conv_id, short_id, count=20, max_pages=1)
    hit = [m for m in hist if str(m.get("msg_id")) == sid]
    print(f"④ cmd301 回读 {len(hist)} 条；命中 {sid}: {len(hit)} 条")
    if hit:
        print("✅ 端到端已投递（服务端生成消息号且可回读）")
        return 0
    print("⚠️ 有消息号但 301 回读未见（索引延迟或陌生人拦截）")
    for m in hist[-3:]:
        print("   最近:", str(m.get("msg_id")), m.get("role"), repr(str(m.get("text"))[:40]))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
