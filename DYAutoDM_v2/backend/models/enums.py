"""状态枚举（协议层核心）

这是解决"中文字符串协议脆弱"问题的关键文件。

原版问题：
- 后端 records.status 用中文字符串（'已发送'/'已捕获'/'发送失败'）
- 后端 _do_send 失败时 status 是 f'发送失败({reason})'
- 前端用 r.status === '发送失败' 永远不匹配，落到 'un'

新版方案：
- 所有状态用枚举（小写英文），原因放单独字段
- 前端通过 OpenAPI 生成的 TS 枚举强类型比较
"""
from enum import Enum


class EngineState(str, Enum):
    """引擎整体状态（取代原版 5 个标志位 _running/listen_active/hard_stopped/no_new/paused）"""

    IDLE = "idle"  # 未启动
    STARTING = "starting"  # 启动中（建 auth / 探活 / 等开播）
    RUNNING = "running"  # 监听 + 发送都进行
    PAUSED = "paused"  # 暂停发送（监听仍在）
    STOPPING = "stopping"  # 已停止监听，存量队列在发
    STOPPED = "stopped"  # 完全停止


class RecordStatus(str, Enum):
    """单条私信记录的状态（取代中文 '已发送'/'已捕获'/'发送失败'）"""

    CAPTURED = "captured"  # 已捕获，等待发送
    SENT = "sent"  # 已发送
    FAIL = "fail"  # 发送失败（看 reason 字段）
    SKIPPED = "skipped"  # 跳过（去重 / 上限）


class AccountRole(str, Enum):
    """账号角色"""

    WATCH = "watch"  # 监测账号（看直播收弹幕）
    SEND = "send"  # 发送账号（发私信）
    BOTH = "both"  # 既是监测又是发送


class AccountStatus(str, Enum):
    """账号登录/凭证状态"""

    LOGGED_IN = "logged_in"
    LOGGED_OUT = "logged_out"
    SCANNING = "scanning"  # 扫码中
    EXPIRED = "expired"  # 凭证过期


class DaemonKind(str, Enum):
    """守护进程类型"""

    BROWSER = "browser"  # 凭证守护
    RECV = "recv"  # 私信接收守护
