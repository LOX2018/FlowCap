"""直播监听路由

取代原版 WebBridge.getLiveStream / sendDanmaku / doLike 等。
关键改进：用 WebSocket 推送实时弹幕，替代 2s 轮询。
"""
from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from loguru import logger
from models.live import LiveStreamResponse, LiveMessage, DanmakuRequest, DmTemplateRequest
from config import settings

router = APIRouter()


class ResolveRequest(BaseModel):
    url: str


@router.get("/stream")
async def get_stream(request: Request) -> LiveStreamResponse:
    """直播流快照（用于初次加载）。

    从引擎持有 AutoDM.live (LiveChatHook) 读取实时直播流数据：
    room_status（room_title/主播昵称/room_id）、feed（实时弹幕流）、heat_curve、
    online_count / likes、当前监听状态。引擎未运行或无 live 时返回 alive=False。
    """
    adm = getattr(request.app.state, "adm", None)
    live = getattr(adm, "live", None) if adm else None
    if live is None:
        return LiveStreamResponse(alive=False)

    room = getattr(live, "room_status", None) or {}
    room_info = room.get("room_info") if isinstance(room, dict) else {}
    room_stats = getattr(live, "room_stats", None) or {}

    running = bool(getattr(live, "running", False))

    # 弹幕流：feed_snapshot 返回 [{type,nickname,content,ts,epoch},...]，映射成 LiveMessage
    feed_items = live.feed_snapshot(limit=50) if hasattr(live, "feed_snapshot") else []
    messages = [
        LiveMessage(
            uid=str(x.get("uid", "") or ""),
            nickname=str(x.get("nickname", "") or ""),
            content=str(x.get("content", "") or ""),
            ts=int(x.get("epoch", 0) or 0),
        )
        for x in feed_items
    ]

    # 热度曲线：heat_snapshot 返回 [[epoch, online, likes],...]，取 online 作为热度点
    heat = live.heat_snapshot() if hasattr(live, "heat_snapshot") else []
    heat_curve = [int(h[1]) for h in heat if isinstance(h, (list, tuple)) and len(h) > 1]

    return LiveStreamResponse(
        alive=running,
        room_id=getattr(adm, "live_id", None) or str(room_info.get("room_id") or ""),
        online_count=int(room_stats.get("online", 0) or 0),
        messages=messages,
        heat_curve=heat_curve,
        likes=int(room_stats.get("likes", 0) or 0),
        listening=running,
        roomTitle=room_info.get("title") or room_info.get("room_title")
        or room.get("title") or "",
        liveUrl=f"https://live.douyin.com/{getattr(adm, 'live_id', '') or ''}",
    )


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
