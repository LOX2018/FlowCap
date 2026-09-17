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
    # 2026-09-08：失败原因结构化分类（前端弹窗区分调度堵塞/凭证失效/风控等）
    fail_kind: str | None = None      # credential/risk/ratelimit/blocked/param/network/other
    fail_label: str | None = None     # 中文类名，如「凭证失效」
    fail_advice: str | None = None    # 可操作建议（弹窗展示）
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
    delay_range: list[int] | None = None
    interval: float = 60.0
    force_rescan: bool = False
    enable_danmaku: bool = True
    enable_console: bool = True
    enable_send: bool = True
    # 指定私信/监测使用的账号名（直播监听页选择的账号）；None 时用当前账号 current_env_path()
    acct: str | None = None

    # 前端别名兼容 + 扩展开关（tasks.tsx 发送 enableDanmaku/enableConsole/enableSend）
    # 使用字段别名 + 宽松校验，避免 pydantic 422
    model_config = {"populate_by_name": True, "extra": "ignore"}

    # 别名映射：前端传 liveUrl -> live_url，maxTarget -> max_target
    liveUrl: str | None = None
    maxTarget: int | None = None
    dmPool: list = []
    delay: str | None = None  # 形如 "50,120" 或 "60"
    forceRescan: bool | None = None
    enableDanmaku: bool | None = None
    enableConsole: bool | None = None
    enableSend: bool | None = None

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
        enable_danmaku = self.enable_danmaku if self.enableDanmaku is None else self.enableDanmaku
        enable_console = self.enable_console if self.enableConsole is None else self.enableConsole
        enable_send = self.enable_send if self.enableSend is None else self.enableSend

        delay_range = self.delay_range
        # 2026-09-17 修补（OCR 审查 HIGH —— 用默认值当"未设置"哨兵）：
        # 原为 `if (not delay_range or delay_range == [40, 65]) and self.delay:`。
        # `[40, 65]` 恰是字段的**类默认值**，于是「用户显式传 [40,65]」与
        # 「用户没传」无法区分（一旦默认值调整即成真 bug）。
        # 现将字段默认值改为 **None** 作显式哨兵：只有"未提供"才回落 `delay` 别名。
        # 语义保持：`delay_range` 优先，其次 `delay`，最后由消费方兜底 [40,65]
        # （core/auto_dm.py 多处 `or [40, 65]`）。
        if (not delay_range) and self.delay:
            delay_range = _parse_delay(self.delay)
        if not delay_range:
            delay_range = [40, 65]      # 两者都未提供时的既定默认（与旧行为一致）

        return TaskConfig(
            live_url=live_url,
            max_target=int(max_target or 3),
            keywords=self.keywords,
            dm_pool=[t if isinstance(t, str) else t.get("text", "") for t in dm_pool],
            delay_range=delay_range,
            interval=self.interval,
            force_rescan=bool(force_rescan),
            enable_danmaku=bool(enable_danmaku),
            enable_console=bool(enable_console),
            enable_send=bool(enable_send),
            acct=self.acct,
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
