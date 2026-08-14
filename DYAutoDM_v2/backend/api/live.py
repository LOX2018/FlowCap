"""直播监听路由

取代原版 WebBridge.getLiveStream / sendDanmaku / doLike 等。
关键改进：用 WebSocket 推送实时弹幕，替代 2s 轮询。
"""
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from models.live import LiveStreamResponse, DanmakuRequest, DmTemplateRequest

router = APIRouter()


@router.get("/stream")
async def get_stream() -> LiveStreamResponse:
    """直播流快照（用于初次加载）"""
    return LiveStreamResponse(alive=False)


@router.websocket("/ws")
async def live_ws(ws: WebSocket):
    """WebSocket 推送实时弹幕（替代前端 2s 轮询）

    前端连接后，后端有新弹幕就推送：
    {type: 'message', data: LiveMessage}
    {type: 'heat', data: int}
    """
    await ws.accept()
    try:
        while True:
            # TODO: 从 LiveChatHook 订阅消息推送
            msg = await ws.receive_text()
            # 处理前端发来的指令（如发弹幕）
    except WebSocketDisconnect:
        pass


@router.post("/danmaku")
async def send_danmaku(body: DanmakuRequest):
    """发送弹幕"""
    # TODO: 迁移 sendDanmaku 逻辑
    return {"ok": True, "content": body.content}


@router.post("/dm-template")
async def set_dm_template(body: DmTemplateRequest):
    """配置私信模板与发送参数"""
    # TODO: 写入 config
    return {"ok": True}
