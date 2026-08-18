"""直播监听相关协议"""
from pydantic import BaseModel, Field


class LiveMessage(BaseModel):
    """单条弹幕消息"""

    uid: str = ""  # 弹幕可能无 uid，给默认值
    nickname: str = ""
    content: str = ""
    ts: int = 0  # 服务器时间戳（秒）


class LiveStreamResponse(BaseModel):
    """直播流状态（替代原版 getLiveStream 2s 轮询）"""

    alive: bool
    room_id: str | None = None
    online_count: int = 0
    messages: list[LiveMessage] = Field(default_factory=list)
    heat_curve: list[int] = Field(default_factory=list)
    likes: int = 0
    listening: bool = False
    roomTitle: str = ""
    liveUrl: str = ""

    # V2 任务容器：切页后回读引擎真实状态（修复「页面显示等待启动」）
    engineState: str = "idle"  # idle/starting/running/paused/stopping/stopped
    statusMsg: str = ""  # 引擎状态文案（启动中/等待开播/监听中/…）
    dmRunning: bool = False  # 私信引擎是否运行（running/paused/starting/stopping）
    dmPaused: bool = False  # 私信引擎是否暂停


class DanmakuRequest(BaseModel):
    content: str


class DmTemplateRequest(BaseModel):
    pool: list[str]
    delay_range: tuple[int, int] = (40, 65)
    interval: float = 60.0
    max_target: int = 3
