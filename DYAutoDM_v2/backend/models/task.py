"""任务/发送记录相关协议"""
from pydantic import BaseModel
from .enums import RecordStatus


class SendRecord(BaseModel):
    """单条私信发送记录

    迁移自原版 DispatchCenter.records，
    关键变化：status 用枚举而非中文字符串，原因放 reason 字段
    """

    key: str  # 去重键（uid / sec_uid / nickname 之一）
    uid: str
    nickname: str
    sec_uid: str | None = None
    status: RecordStatus
    reason: str | None = None  # 失败原因（status=fail 时）
    captured_at: float  # 捕获时间戳
    send_at: float | None = None  # 计划发送时间
    sent_at: float | None = None  # 实际发送时间
    content: str | None = None
    comment: str = ""  # 原始评论内容（展示用）


class TaskConfig(BaseModel):
    live_url: str
    max_target: int = 3
    keywords: list[str] = []
    dm_pool: list[str] = []
    delay_range: tuple[int, int] = (40, 65)
    interval: float = 60.0


class TaskListResponse(BaseModel):
    config: TaskConfig
    records: list[SendRecord]
    sent: int
    captured: int
