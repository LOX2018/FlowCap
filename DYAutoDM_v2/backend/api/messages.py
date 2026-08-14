"""私信会话路由

取代原版 WebBridge.getConversations / getConversation / sendDm。
转发到 recv_daemon 的 HTTP 接口。
"""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter()


class SendDmRequest(BaseModel):
    account: str
    conv_id: str
    text: str


class RequestDmBody(BaseModel):
    name: str
    comment: str = ""


@router.get("/conversations")
async def list_conversations(account: str):
    """会话列表（转发到 recv_daemon /conversations）"""
    # TODO: 调 recv_daemon HTTP
    return {"ok": True, "conversations": []}


@router.get("/conversation")
async def get_conversation(account: str, conv_id: str):
    """会话详情"""
    return {"ok": True, "conversation": {}}


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
    """手动发送私信"""
    # TODO: 调 recv_daemon /send
    return {"ok": True}
