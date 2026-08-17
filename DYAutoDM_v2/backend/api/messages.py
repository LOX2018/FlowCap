"""私信会话路由

取代原版 WebBridge.getConversations / getConversation / sendDm。
转发到 recv_daemon 的 HTTP 接口（每账号专属端口）。
"""
from __future__ import annotations

import time
import urllib.parse
import urllib.request
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from auto_dm import accounts as acct_core

router = APIRouter()


class SendDmRequest(BaseModel):
    account: str
    conv_id: str
    text: str


class RequestDmBody(BaseModel):
    name: str
    comment: str = ""


def _recv_url(account: str, path: str) -> str | None:
    """构造该账号 recv_daemon 的 HTTP URL（端口稳定哈希，与守护进程一致）。"""
    port = acct_core.recv_daemon_port(account)
    return f"http://127.0.0.1:{port}{path}"


def _http_get_json(url: str, timeout: float = 5.0) -> dict:
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        import json
        return json.loads(resp.read().decode("utf-8"))


def _http_post_json(url: str, payload: dict, timeout: float = 8.0) -> dict:
    import json
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _fmt_ts(ts: float | None) -> str:
    try:
        if ts:
            return time.strftime("%H:%M:%S", time.localtime(float(ts)))
    except Exception:
        pass
    return ""


def _map_message(m: dict) -> dict:
    """把 recv_daemon 的 message 字段映射成前端期望结构。

    recv_daemon to_dict(): role/the/me、text、msg_type、ts
    前端 messages.tsx: dir(in/out)、type、text、time
    """
    role = (m.get("role") or "them")
    direction = "in" if role == "them" else "out"
    return {
        "dir": direction,
        "type": m.get("msg_type") or m.get("type") or "text",
        "text": m.get("text") or "",
        "time": _fmt_ts(m.get("ts")),
    }


def _map_conversation(c: dict) -> dict:
    return {
        "conv_id": c.get("conv_id"),
        "name": c.get("peer_name") or c.get("peer_id") or c.get("conv_id") or "会话",
        "unread": c.get("unread") or 0,
        "messages": [_map_message(m) for m in (c.get("messages") or [])],
    }


@router.get("/conversations")
async def list_conversations(account: str):
    """会话列表（转发到 recv_daemon /conversations）"""
    try:
        url = _recv_url(account, "/conversations")
        if url is None:
            return {"ok": False, "conversations": [], "error": "端口分配失败"}
        d = _http_get_json(url)
        convs = [_map_conversation(c) for c in (d.get("conversations") or [])]
        return {"ok": True, "conversations": convs}
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"ok": True, "conversations": []}
        return {"ok": False, "conversations": [], "error": f"私信守护返回 {e.code}"}
    except Exception as e:
        # 守护未启动 / 端口无响应 → 返回空列表（前端显示空态，不报错崩溃）
        return {"ok": False, "conversations": [], "error": str(e)}


@router.get("/conversation")
async def get_conversation(account: str, conv_id: str):
    """会话详情（转发到 recv_daemon /conversation，含已读标记）"""
    try:
        url = _recv_url(account, f"/conversation?conv_id={urllib.parse.quote(str(conv_id))}")
        d = _http_get_json(url)
        conv = d.get("conversation")
        if conv is None:
            return {"ok": False, "conversation": {}}
        return {"ok": True, "conversation": _map_conversation(conv)}
    except urllib.error.HTTPError as e:
        if e.code in (404,):
            return {"ok": False, "conversation": {}}
        return {"ok": False, "conversation": {}, "error": f"私信守护返回 {e.code}"}
    except Exception as e:
        return {"ok": False, "conversation": {}, "error": str(e)}


@router.post("/request")
async def request_dm(body: RequestDmBody, request: Request):
    """新建会话（手动把某昵称/评论入队，走与直播间捕获同一条延迟发送队列）

    对齐前端 messages.tsx 的 requestDm(name) 调用。
    委托 adm.dispatch.submit（与原版 web_bridge.requestDm 一致）。
    """
    adm = request.app.state.adm
    if adm is None or adm.dispatch is None:
        return {"ok": False, "msg": "引擎尚未启动（无调度中心）"}
    target = {
        "user_id": None,
        "sec_uid": None,
        "nickname": body.name,
        "comment": body.comment or "",
    }
    try:
        ok = adm.dispatch.submit(target)
    except Exception as e:
        return {"ok": False, "msg": f"入队失败: {e}"}
    if ok:
        return {"ok": True, "msg": f"已为「{body.name}」创建私信会话（进入延迟发送队列）"}
    return {"ok": False, "msg": f"「{body.name}」已在队列中或已达上限"}


@router.post("/send")
async def send_dm(body: SendDmRequest):
    """手动发送私信（转发到 recv_daemon /send）"""
    try:
        url = _recv_url(body.account, "/send")
        if url is None:
            return {"ok": False, "error": "端口分配失败"}
        d = _http_post_json(url, {
            "account": body.account,
            "conv_id": body.conv_id,
            "text": body.text,
        })
        return d
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"ok": False, "error": "账号私信守护未运行"}
        return {"ok": False, "error": f"私信守护返回 {e.code}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}
