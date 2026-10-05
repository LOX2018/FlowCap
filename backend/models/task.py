"""任务/发送记录相关协议"""
from typing import Any, List, Union

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
    # 2026-09-30（用户实测「发送失败，既没有显示失败缘由」）：**尝试发送的文案**。
    # content 在失败时被置 None（历史语义：失败=没有内容），于是表格里失败行永远
    # 只剩一个红标签、看不到到底试发了什么。本字段独立保留「当时要发的那句话」，
    # 与 content 的成败语义解耦 —— 不改 content 的任何既有消费点。
    attempted_content: str | None = None
    # 2026-09-30：文案**实际来源**（用户实测反馈：界面上无法辨别这条私信
    # 到底是词库、AI 生成，还是兜底文档）。取值由 core/dispatch._do_send 在
    # **取值点**写入（不是事后推断），故与实际外发内容一一对应：
    #   "AI"   —— gen_dm_message 回调产出（经 ai_reply 出口护栏）
    #   "词库" —— pick_dm_message 从 dm_pool 抽取
    #   "原文" —— 两个来源都空，回退弹幕原文（M-9 兜底）
    #    None  —— 未发送（content 同为空）
    content_source: str | None = None
    comment: str = ""  # 原始评论内容（展示用）


class TaskConfig(BaseModel):
    # 后端规范字段名
    live_url: str | None = None
    max_target: int = 3
    keywords: list[str] = []
    # 2026-09-20：契约放宽 —— 词库两种形态都要吃得下：
    #   `list[str]`（旧）与 `[{text,enabled}]`（live_room_configs / RoomConfigPage 的
    #   **主形态**）。此前声明为 list[str]，而直播页把所选策略整条 cfg 原样传入
    #   （live-page.tsx 的 `dm_pool: selCfg.dm_pool`）→ Pydantic 在 resolved() 之前
    #   就抛 422（实机：「Input should be a valid string」×N）→ /api/engine/start 永不生效。
    #   消费侧（core/auto_dm._normalize_dm_pool / api/live_config._RuntimeCfg）本就按
    #   对象归一，故放宽声明即与真实契约对齐，不改任何业务语义。
    dm_pool: List[Union[str, dict]] = []
    delay_range: list[int] | None = None
    interval: float = 60.0
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
    enableDanmaku: bool | None = None
    enableConsole: bool | None = None
    enableSend: bool | None = None

    def resolved(self) -> "TaskConfig":
        """把前端别名归一到规范字段。

        - live_url/liveUrl 二选一
        - max_target/maxTarget 二选一
        - dm_pool/dmPool 二选一
        - delay_range 优先；否则由 delay 字符串("50,120"/"60")解析
        - 其余字段按别名归一
        """
        live_url = self.live_url or self.liveUrl or ""
        max_target = self.max_target if self.maxTarget is None else self.maxTarget
        dm_pool = self.dm_pool or self.dmPool or []
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
            # 2026-09-20：对象形态保留 `enabled` 位（旧写法只取 text，会把「停用」
            # 的文案重新变成启用 —— 与 api/tasks.save_config 的既有修补同一条原则：
            # 不做「写回时抹掉启用标记」的静默降级。消费侧 _normalize_dm_pool 对
            # dict 直接读 enabled，对 str 回落 dm_template 的既有启用位。
            dm_pool=[
                ({"text": str(t.get("text", "") or ""),
                  "enabled": bool(t.get("enabled", True))}
                 if isinstance(t, dict) else str(t))
                for t in dm_pool
                if isinstance(t, dict) or str(t or "").strip()
            ],
            delay_range=delay_range,
            interval=self.interval,
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
