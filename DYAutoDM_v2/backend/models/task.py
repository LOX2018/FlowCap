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
    # 后端规范字段名
    live_url: str | None = None
    max_target: int = 3
    keywords: list[str] = []
    dm_pool: list[str] = []
    delay_range: list[int] = [40, 65]
    interval: float = 60.0
    force_rescan: bool = False

    # 前端别名兼容（live.tsx 发送 liveUrl/maxTarget/dmPool/delay/forceRescan）
    # 使用字段别名 + 宽松校验，避免 pydantic 422
    model_config = {"populate_by_name": True, "extra": "ignore"}

    # 别名映射：前端传 liveUrl -> live_url，maxTarget -> max_target
    liveUrl: str | None = None
    maxTarget: int | None = None
    dmPool: list = []
    delay: str | None = None  # 形如 "50,120" 或 "60"
    forceRescan: bool | None = None

    def resolved(self) -> "TaskConfig":
        """把前端别名归一到规范字段。

        - live_url/liveUrl 二选一
        - max_target/maxTarget 二选一
        - dm_pool/dmPool 二选一
        - delay_range 优先；否则由 delay 字符串("50,120"/"60")解析
        - force_rescan 优先；否则取 forceRescan
        """
        live_url = self.live_url or self.liveUrl or ""
        max_target = self.max_target if self.maxTarget is None else self.maxTarget
        dm_pool = self.dm_pool or self.dmPool or []
        force_rescan = self.force_rescan if self.forceRescan is None else self.forceRescan

        delay_range = self.delay_range
        if (not delay_range or delay_range == [40, 65]) and self.delay:
            delay_range = _parse_delay(self.delay)

        return TaskConfig(
            live_url=live_url,
            max_target=int(max_target or 3),
            keywords=self.keywords,
            dm_pool=[t if isinstance(t, str) else t.get("text", "") for t in dm_pool],
            delay_range=delay_range,
            interval=self.interval,
            force_rescan=bool(force_rescan),
        )


def _parse_delay(raw: str) -> list[int]:
    """解析延迟抖动字符串：'50,120'/'50-120'/'50~120' -> [50,120]；'60' -> [60,60]"""
    import re

    s = raw.strip()
    if not s:
        return [40, 65]
    m = re.split(r"[,~\-\s]+", s)
    if len(m) >= 2:
        try:
            lo, hi = int(m[0]), int(m[1])
            if lo > hi:
                lo, hi = hi, lo
            return [lo, hi]
        except ValueError:
            pass
    try:
        v = int(s)
        return [v, v]
    except ValueError:
        return [40, 65]


class TaskListResponse(BaseModel):
    config: TaskConfig
    records: list[SendRecord]
    sent: int
    captured: int
