"""直播监听路由

取代原版 WebBridge.getLiveStream / sendDanmaku / doLike 等。
关键改进：用 WebSocket 推送实时弹幕，替代 2s 轮询。
"""
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from loguru import logger
from models.live import LiveStreamResponse, DanmakuRequest, DmTemplateRequest
from config import settings

router = APIRouter()


class ResolveRequest(BaseModel):
    url: str


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


@router.post("/resolve")
async def resolve_live(body: ResolveRequest):
    """解析直播间链接/房间号，写回配置（迁移自原版 WebBridge.resolveLive）。

    1) 调用 link_resolve.resolve_live_id 解析出 web_rid（真实直播间号）；
    2) 写入运行时 settings（live_url 字段，供 engine.start 等后续读取）；
    3) 落盘 data/config.json（等价原版 _write_config_file 写回 LIVE_ID/LIVE_URL）。
    """
    from link_resolve import resolve_live_id
    import json
    from pathlib import Path

    raw = (body.url or "").strip()
    if not raw:
        return {"ok": False, "error": "链接为空"}
    try:
        live_id, _src = resolve_live_id(raw)
    except Exception as e:
        return {"ok": False, "error": f"解析失败: {e}"}
    if not live_id:
        return {"ok": False, "error": "未能从链接中解析出直播间号"}

    # 运行时生效：写入 settings 单例（原版 C.LIVE_ID / C.LIVE_URL）
    settings.live_url = raw
    # 落盘：等价原版 _write_config_file({"LIVE_ID", "LIVE_URL"})
    try:
        cfg_path = settings.data_dir / "config.json"
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        data = {}
        if cfg_path.exists():
            try:
                data = json.loads(cfg_path.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        data["live_url"] = raw
        data["live_id"] = live_id
        cfg_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as e:
        logger.warning(f"[resolve] 配置落盘失败（不影响本次解析）: {e}")

    return {"ok": True, "liveId": live_id, "liveUrl": raw}
