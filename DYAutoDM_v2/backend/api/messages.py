"""私信会话路由

取代原版 WebBridge.getConversations / getConversation / sendDm。
转发到 recv_daemon 的 HTTP 接口。
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()


class SendDmRequest(BaseModel):
    account: str
    conv_id: str
    text: str


@router.get("/conversations")
async def list_conversations(account: str):
    """会话列表（转发到 recv_daemon /conversations）"""
    # TODO: 调 recv_daemon HTTP
    return {"ok": True, "conversations": []}


@router.get("/conversation")
async def get_conversation(account: str, conv_id: str):
    """会话详情"""
    return {"ok": True, "conversation": {}}


@router.post("/send")
async def send_dm(body: SendDmRequest):
    """手动发送私信"""
    # TODO: 调 recv_daemon /send
    return {"ok": True}
