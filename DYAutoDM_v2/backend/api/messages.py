"""私信会话路由

取代原版 WebBridge.getConversations / getConversation / sendDm。
转发到 recv_daemon 的 HTTP 接口（每账号专属端口）。
"""
from __future__ import annotations

import time
import urllib.parse
import urllib.request
import json
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from loguru import logger

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
    """会话列表（转发到 recv_daemon /conversations）

    私信守护(recv_daemon)未运行时返回 recvDaemonDown=True + 空列表，
    不再向外抛 urlopen/WinError 10061 —— 前端据此提示「守护未运行」并自动拉起，
    而不是报一屏连接失败弹窗。
    """
    logger.info(f"[私信拉取] 开始拉取账号「{account}」的会话列表")
    try:
        url = _recv_url(account, "/conversations")
        logger.info(f"[私信拉取] 账号「{account}」→ recv_daemon URL: {url}")
        if url is None:
            logger.warning(f"[私信拉取] 账号「{account}」端口分配失败")
            return {"ok": False, "conversations": [], "error": "端口分配失败"}
        # 先探测守护端口；未开直接返回空（别让前端看到原始连接错误）
        port = acct_core.recv_daemon_port(account)
        if not acct_core._port_open(port, timeout=0.3):
            logger.warning(f"[私信拉取] 账号「{account}」私信守护未运行(port={port})，返回空列表")
            return {"ok": True, "conversations": [], "recvDaemonDown": True}
        d = _http_get_json(url)
        logger.info(f"[私信拉取] 账号「{account}」recv_daemon 完整原始响应: {json.dumps(d, ensure_ascii=False)}")
        raw_convs = d.get("conversations") or []
        logger.info(f"[私信拉取] 账号「{account}」recv_daemon 返回 {len(raw_convs)} 个原始会话")
        for i, rc in enumerate(raw_convs):
            logger.info(f"[私信拉取]   原始会话#{i}: conv_id={rc.get('conv_id')}, peer_name={rc.get('peer_name')}, peer_id={rc.get('peer_id')}, messages={len(rc.get('messages') or [])} 条")
            logger.info(f"[私信拉取]   原始会话#{i} 完整数据: {json.dumps(rc, ensure_ascii=False)}")
        convs = [_map_conversation(c) for c in raw_convs]
        logger.info(f"[私信拉取] 账号「{account}」映射后会话: {json.dumps(convs, ensure_ascii=False)}")
        return {"ok": True, "conversations": convs}
    except urllib.error.HTTPError as e:
        if e.code == 404:
            logger.warning(f"[私信拉取] 账号「{account}」recv_daemon 返回 404（守护未运行）")
            return {"ok": True, "conversations": [], "recvDaemonDown": True}
        logger.error(f"[私信拉取] 账号「{account}」HTTP 错误 {e.code}")
        return {"ok": False, "conversations": [], "error": f"私信守护返回 {e.code}"}
    except (ConnectionRefusedError, TimeoutError) as e:
        logger.warning(f"[私信拉取] 账号「{account}」守护端口不可达: {e}")
        return {"ok": True, "conversations": [], "recvDaemonDown": True}
    except Exception as e:
        logger.warning(f"[私信拉取] 账号「{account}」异常: {e}（守护可能未启动）")
        return {"ok": False, "conversations": [], "error": str(e)}


@router.get("/conversation")
async def get_conversation(account: str, conv_id: str):
    """会话详情（转发到 recv_daemon /conversation，含已读标记）"""
    logger.info(f"[私信拉取] 拉取账号「{account}」会话详情 conv_id={conv_id}")
    try:
        url = _recv_url(account, f"/conversation?conv_id={urllib.parse.quote(str(conv_id))}")
        d = _http_get_json(url)
        conv = d.get("conversation")
        if conv is None:
            logger.warning(f"[私信拉取] 账号「{account}」会话 {conv_id} 未找到")
            return {"ok": False, "conversation": {}}
        mapped = _map_conversation(conv)
        logger.info(f"[私信拉取] 账号「{account}」会话 {conv_id} 详情: name={mapped.get('name')}, messages={len(mapped.get('messages') or [])} 条")
        return {"ok": True, "conversation": mapped}
    except urllib.error.HTTPError as e:
        if e.code in (404,):
            return {"ok": False, "conversation": {}, "recvDaemonDown": True}
        return {"ok": False, "conversation": {}, "error": f"私信守护返回 {e.code}"}
    except (ConnectionRefusedError, TimeoutError) as e:
        return {"ok": False, "conversation": {}, "recvDaemonDown": True}
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
