"""应用配置 —— 域 schema 定义（纯数据）

## 为什么独立（2026-09-15 大单文件打散）

原 `services/app_config.py`（851 行）其中 566 行是 **SECTIONS** 字典
（每个配置域：字段名/类型/默认值/apply 模式/说明）。按「数据与逻辑分离」抽出。

## 说明
**纯搬移**——SECTIONS 字典逐字段、逐注释不变。
"""
from __future__ import annotations

from typing import Any

SECTIONS: dict[str, dict[str, Any]] = {
    # ===== 通用 / 启动 =====
    "general": {
        "label": "通用 / 启动",
        "fields": {
            "force_rescan": {
                "label": "启动前强制重新扫码",
                "type": "bool", "default": False, "env": None,
                "apply": "hot",
                "hint": "勾选则每次启动自动私信都强制重扫，忽略磁盘凭证",
            },
            "bcc_on_start": {
                "label": "启动时拉起 BCC 浏览器容器",
                "type": "bool", "default": False, "env": "DY_BCC_ON_START",
                "apply": "restart_backend",
                "hint": "BCC 已改懒加载，按需拉起；开启会增加启动耗时与风控暴露",
            },
            "bcc_headless_mode": {
                "label": "BCC 无头模式",
                "type": "select", "default": "native", "env": "DY_BCC_HEADLESS_MODE",
                "options": ["native"],
                "apply": "restart_backend",
                "hint": "仅 native（纯无头）；disguise（移屏外）已于 2026-09-09 废弃移除",
            },
            "cred_refresh_mode": {
                "label": "凭证更新方式",
                "type": "select", "default": "observe", "env": "DY_CRED_REFRESH_MODE",
                "options": [
                    {"value": "observe", "label": "观测态静默更新（推荐）"},
                    {"value": "popup", "label": "弹窗激活更新（需人工点一下）"},
                    {"value": "both", "label": "两者兼容（观测优先，失效再弹窗）"},
                ],
                "apply": "restart_daemon",
                "hint": ("observe=观测态静默更新（保活心跳读实时 cookie 写回 .env，"
                         "不弹窗、零打扰，推荐）；popup=仅弹窗激活更新"
                         "（发现登录态待激活时弹指纹浏览器请用户点一下，"
                         "适合习惯人工确认的账号）；both=先观测态、"
                         "观察到登录态失效再弹窗"),
            },
            "auto_capture_on_start": {
                "label": "启动时自动捕获会话",
                "type": "bool", "default": False, "env": "DY_AUTO_CAPTURE_ON_START",
                "apply": "restart_backend",
                "hint": "默认关闭（每次开软件抓包等于白白暴露）；私信页应纯读库",
            },
        },
    },

    # ===== 直播监听 =====
    "live": {
        "label": "直播监听",
        "fields": {
            "live_url": {
                "label": "直播间链接",
                "type": "str", "default": "", "env": None,
                "apply": "hot", "hint": "https://live.douyin.com/<房间号>",
            },
            "max_target": {
                "label": "每场私信上限",
                "type": "int", "default": 3, "min": 1, "max": 200, "env": None,
                "apply": "hot", "hint": "达到后停止发送",
            },
            "interval": {
                "label": "私信间隔（秒）",
                "type": "float", "default": 60.0, "min": 1, "max": 3600, "env": None,
                "apply": "hot", "hint": "两条私信之间的最小间隔",
            },
            "delay_min": {
                "label": "延迟抖动下限（秒）",
                "type": "int", "default": 40, "min": 0, "max": 600, "env": None,
                "apply": "hot", "hint": "模拟真人「看到弹幕→过一会再私信」",
            },
            "delay_max": {
                "label": "延迟抖动上限（秒）",
                "type": "int", "default": 65, "min": 0, "max": 900, "env": None,
                "apply": "hot", "hint": "需 >= 下限",
            },
            "live_poll_interval": {
                "label": "未开播轮询间隔（秒）",
                "type": "int", "default": 30, "min": 5, "max": 600, "env": None,
                "apply": "hot", "hint": "开播前每 N 秒复查一次房间状态",
            },
            "ws_heartbeat_interval": {
                "label": "WS 心跳间隔（秒）",
                "type": "int", "default": 300, "min": 30, "max": 3600, "env": None,
                "apply": "hot", "hint": "监听中定期探活，登录态失效自动触发重扫",
            },
            "enable_danmaku": {
                "label": "接收弹幕", "type": "bool", "default": True, "env": None,
                "apply": "hot", "hint": "启动监听时自动接收弹幕评论",
            },
            "enable_console": {
                "label": "控制台输出", "type": "bool", "default": True, "env": None,
                "apply": "hot", "hint": "运行日志输出到控制台",
            },
            "enable_send": {
                "label": "启用发送", "type": "bool", "default": True, "env": None,
                "apply": "hot", "hint": "关闭则只采集不发送（调试用）",
            },
        },
    },

    # ===== 私信发送与风控（敏感，UI 需下限保护）=====
    "send": {
        "label": "私信发送与风控",
        "fields": {
            "min_interval": {
                "label": "发送闸门最小间隔（秒）",
                "type": "float", "default": 8.0, "min": 8, "max": 300,
                "env": "DY_SEND_MIN_INTERVAL",
                "apply": "restart_daemon", "risk": True,
                "hint": "per-account 令牌闸门；低于 8s 显著增加风控风险",
            },
            "max_wait": {
                "label": "闸门排队等待上限（秒）",
                "type": "float", "default": 30.0, "min": 5, "max": 300,
                "env": "DY_SEND_MAX_WAIT",
                "apply": "restart_daemon", "risk": True,
                "hint": "超时快速失败 rate_limited，不静默堆积",
            },
            "stranger_per_minute": {
                "label": "陌生人首发 每分钟上限",
                "type": "int", "default": 2, "min": 0, "max": 60,
                "env": "DY_STRANGER_PER_MINUTE",
                "apply": "hot", "risk": True, "hint": "陌生人是风控最高频面",
            },
            "stranger_per_day": {
                "label": "陌生人首发 每日上限",
                "type": "int", "default": 30, "min": 0, "max": 2000,
                "env": "DY_STRANGER_PER_DAY",
                "apply": "hot", "risk": True, "hint": "0 = 不限制",
            },
            "cooldown_freq": {
                "label": "频控命中冷静期（秒）",
                "type": "float", "default": 600.0, "min": 0, "max": 86400,
                "env": "DY_DM_COOLDOWN_FREQ",
                "apply": "hot", "risk": True, "hint": "被频控后暂停发送时长",
            },
            "cooldown_max": {
                "label": "冷静期上限（秒）",
                "type": "float", "default": 3600.0, "min": 0, "max": 604800,
                "env": "DY_DM_COOLDOWN_MAX",
                "apply": "hot", "risk": True, "hint": "连续被频控时冷静期封顶",
            },
            "weight_recover_halflife": {
                "label": "权重恢复半衰期（秒）",
                "type": "float", "default": 21600.0, "min": 60, "max": 604800,
                "env": "DY_WEIGHT_RECOVER_HALFLIFE",
                "apply": "hot", "risk": True, "hint": "默认 6 小时；保证被降权后能恢复",
            },
            "weight_forgive_after": {
                "label": "权重原谅期（秒）",
                "type": "float", "default": 86400.0, "min": 60, "max": 2592000,
                "env": "DY_WEIGHT_FORGIVE_AFTER",
                "apply": "hot", "risk": True, "hint": "默认 24 小时",
            },
            "dedup_window": {
                "label": "去重窗口（秒）",
                "type": "float", "default": 5.0, "min": 0, "max": 3600,
                "env": "DY_DM_DEDUP_WINDOW", "apply": "hot", "risk": True,
                "hint": "同会话同文本在此窗口内视为重复",
            },
            "queue_max": {
                "label": "队列上限",
                "type": "int", "default": 200, "min": 1, "max": 10000,
                "env": "DY_DM_QUEUE_MAX", "apply": "hot", "risk": True,
                "hint": "超出拒绝入队",
            },
            "pool_strict": {
                "label": "会话整理严格模式",
                "type": "bool", "default": True, "env": "DY_DM_POOL_STRICT",
                "apply": "hot", "risk": True,
                "hint": "无法从 conv_id 解析真实对端时直接拒绝（防发给自）",
            },
            "uid_sink_cooldown": {
                "label": "UID 沉淀冷却（秒）",
                "type": "float", "default": 604800.0, "min": 0, "max": 31536000,
                "env": "DY_UID_SINK_COOLDOWN", "apply": "hot", "risk": True,
                "hint": "默认 7 天：同 UID 冷却期内不重复发",
            },
            "uid_sink_strict": {
                "label": "UID 沉淀严格模式",
                "type": "bool", "default": True, "env": "DY_UID_SINK_STRICT",
                "apply": "hot", "risk": True, "hint": "关则仅提示不拦截",
            },
        },
    },

    # ===== 通知（IM 通知指令解析用模型）=====
    # 2026-09-09：通知页原自带 llm 配置（notify_config.json），与全局模型
    # 割裂。现收敛到此处，由设置页统一管理（用户要求）。
    "notify": {
        "label": "通知与指令",
        "fields": {
            "llm_base_url": {
                "label": "通知解析 API 地址",
                "type": "str", "default": "http://127.0.0.1:31415/v1",
                "env": None, "apply": "hot",
                "hint": "IM 通知指令解析用的模型地址；留空则回落到 AI 全局配置",
            },
            "llm_model": {
                "label": "通知解析模型",
                "type": "str", "default": "glm-5.2",
                "env": None, "apply": "hot",
                "hint": "用于把自然语言指令转成结构化操作",
            },
            "llm_api_key": {
                "label": "通知解析 API Key",
                "type": "str", "default": "",
                "env": None, "apply": "hot",
                "hint": "留空则回落到 AI 全局配置的 api_key",
            },
            "llm_enabled": {
                "label": "启用模型指令解析",
                "type": "bool", "default": False,
                "env": None, "apply": "hot",
                "hint": "关闭则只用规则解析（零模型调用）",
            },
        },
    },

    # ===== 捕获与存储 =====
    "capture": {
        "label": "捕获与存储",
        "fields": {
            "history_max": {
                "label": "历史补全会话数（每轮）",
                "type": "int", "default": 45, "min": 0, "max": 500,
                "env": "DY_HISTORY_MAX", "apply": "hot",
                "hint": "**每轮**补全的会话数上限；单次更新会话可跑多轮（见下）",
            },
            "history_max_rounds": {
                "label": "单次最多轮数（分批）",
                "type": "int", "default": 3, "min": 1, "max": 10,
                "env": "DY_HISTORY_MAX_ROUNDS", "apply": "hot",
                "hint": "单次「更新会话」最多跑几轮；总上限 = 每轮数 × 本值（默认 45×3=135）",
            },
            "history_batch_gap": {
                "label": "轮间隔（秒）",
                "type": "float", "default": 60.0, "min": 0, "max": 3600,
                "env": "DY_HISTORY_BATCH_GAP", "apply": "hot",
                "hint": "分批轮与轮之间的错峰等待，比批间隔更长以规避风控",
            },
            "history_sleep": {
                "label": "补全间隔（秒）",
                "type": "float", "default": 1.5, "min": 0, "max": 30,
                "env": "DY_HISTORY_SLEEP", "apply": "hot", "hint": "每次请求间隔",
            },
            "history_workers": {
                "label": "补全并发数",
                "type": "int", "default": 4, "min": 1, "max": 8,
                "env": "DY_HISTORY_WORKERS", "apply": "hot", "hint": "上限 8",
            },
            "history_full": {
                "label": "启用历史补全",
                "type": "bool", "default": True, "env": "DY_HISTORY_FULL",
                "apply": "hot", "hint": "首包只给每会话最后 ~20 条，长会话需补全",
            },
            "history_skip_paged": {
                "label": "跳过翻页补全",
                "type": "bool", "default": False, "env": "DY_HISTORY_SKIP_PAGED",
                "apply": "hot", "hint": "调试用",
            },
            "userinfo_cache_sec": {
                "label": "昵称缓存 TTL（秒）",
                "type": "int", "default": 600, "min": 0, "max": 86400,
                "env": "DY_USERINFO_CACHE_SEC", "apply": "restart_daemon",
                "hint": "调小 = 更多浏览器读取，增加暴露",
            },
            "uid_probe_ttl_ok": {
                "label": "UID 探活缓存 TTL（秒）",
                "type": "float", "default": 300.0, "min": 0, "max": 86400,
                "env": "DY_UID_PROBE_TTL_OK", "apply": "restart_daemon",
                "hint": "调小 = 更多 query/user 请求，增加风控面",
            },
            "uid_probe_ttl_fail": {
                "label": "探活失败退避（秒）",
                "type": "float", "default": 60.0, "min": 0, "max": 3600,
                "env": "DY_UID_PROBE_TTL_FAIL", "apply": "restart_daemon",
                "hint": "失败后此期间内不重试",
            },
            "uid_probe_lock_wait": {
                "label": "探活锁等待上限（秒）",
                "type": "float", "default": 10.0, "min": 1, "max": 120,
                "env": "DY_UID_PROBE_LOCK_WAIT", "apply": "restart_daemon",
                "hint": "超时退化为读缓存，绝不无限等待",
            },
            "image_inline_max_kb": {
                "label": "图片内联阈值（KB）",
                "type": "int", "default": 32, "min": 0, "max": 10240,
                "env": "IMAGE_INLINE_MAX_KB", "apply": "hot",
                "hint": "≤此值内联 base64（实测图片平均 2.9KB）；0=永远内联",
            },
            "origin_image_ttl_days": {
                "label": "原图保留天数",
                "type": "int", "default": 30, "min": 1, "max": 365,
                "env": "ORIGIN_IMAGE_TTL_DAYS", "apply": "hot", "hint": "TTL 回收",
            },
            "origin_image_max_mb": {
                "label": "原图目录上限（MB）",
                "type": "int", "default": 2048, "min": 64, "max": 102400,
                "env": "ORIGIN_IMAGE_MAX_MB", "apply": "hot", "hint": "超限按 mtime 清理",
            },
            "image_force_hosted": {
                "label": "强制上图床",
                "type": "bool", "default": False, "env": "IMAGE_FORCE_HOSTED",
                "apply": "hot", "hint": "默认本地托管（图床更慢且公开可访问）",
            },
            "image_host_backend": {
                "label": "图床后端",
                "type": "select", "default": "tucdn", "env": "IMAGE_HOST_BACKEND",
                "options": ["tucdn", "imgbb"], "apply": "hot",
                "hint": "tucdn 上传 0.83s / 下载 0.35s，比 imgbb 快 6.4x",
            },
        },
    },

    # ===== 自动化频率控制（★ 本分支 design/better-douyin 新增）=====
    # 照源项目 better-douyin 的 `auto_*` 模型（实测提取自 douyin-dl 二进制，
    # 见 docs/reverse_interface_spec.md §三）：源项目**没有全局发送闸门**，
    # 风控靠「按动作限速 + 关键词准入 + 互动门槛 + 单轮上限」四件套。
    # 本分支按用户「全解除」授权，补上这套模型（与既有 send 闸门并存，可各自关闭）。
    "automation": {
        "label": "自动化频率控制（照源项目）",
        "fields": {
            # 2026-09-17 修补（OCR 审查 HIGH —— 字典重复键静默覆盖）：
            # 本 dict 原先**重复定义**了下列 10 个键，Python 字面量 last-wins，
            # 前者被静默丢弃（无任何告警）：
            #   max_actions_per_run / send_delay_ms / scan_interval_seconds /
            #   require_context / min_digg_count / min_comment_count /
            #   min_play_count / match_keywords / exclude_keywords / use_global_gate
            # 下方（# ══ 以下照源项目 … 权威契约 ══ 之后）那一组才是与
            # `automation_engine.py` 的 clamp 实测一致的权威值，故删除本处的
            # 重复定义。保留此项以防将来再被无意复制回来：
            #   如需改「扫描间隔」，改 516 行附近那一处（权威组）。
            "monitor_interval_minutes": {
                "label": "监控目标检查间隔（分钟）",
                "type": "int", "default": 60, "min": 10, "max": 1440,
                "env": "DY_CREATOR_MONITOR_INTERVAL_MIN",
                "apply": "hot",
                "hint": "源项目 `user_interval_seconds`（前端约束 10~1440，默认 60）",
            },
            "max_new_per_check": {
                "label": "单次检查最多处理新条目",
                "type": "int", "default": 10, "min": 1, "max": 200,
                "env": "DY_MONITOR_MAX_NEW_PER_CHECK",
                "apply": "hot",
                "hint": "源项目 `max_new_downloads_per_check`（本体用于下载，此处用于处理）",
            },
            # 2026-09-17：min_digg_count / min_comment_count / min_play_count /
            # match_keywords / exclude_keywords / use_global_gate 的**前一份
            # 重复定义已删除**（见本 dict 开头说明）——权威定义在下方
            # 「照源项目 ai-automation.ts 权威契约」分组内。
            # ══ 以下照源项目 frontend/src/lib/ai-automation.ts 的权威契约 ══
            # （字段名/默认值/clamp 范围逐项对齐源项目）
            "monitor_notices": {
                "label": "监控·通知", "type": "bool", "default": False,
                "env": "DY_AUTO_MONITOR_NOTICES", "apply": "hot",
                "hint": "源项目 auto_monitor_notices：新粉丝/评论/赞通知",
            },
            "monitor_friends": {
                "label": "监控·好友动态", "type": "bool", "default": False,
                "env": "DY_AUTO_MONITOR_FRIENDS", "apply": "hot",
                "hint": "源项目 auto_monitor_friends",
            },
            "monitor_comments": {
                "label": "监控·评论", "type": "bool", "default": False,
                "env": "DY_AUTO_MONITOR_COMMENTS", "apply": "hot",
                "hint": "源项目 auto_monitor_comments：自己作品下的新评论",
            },
            "monitor_feed": {
                "label": "监控·推荐流", "type": "bool", "default": False,
                "env": "DY_AUTO_MONITOR_FEED", "apply": "hot",
                "hint": "源项目 auto_monitor_feed",
            },
            "follow_back_on_new_follower": {
                "label": "新粉丝自动回关", "type": "bool", "default": False,
                "env": "DY_AUTO_FOLLOW_BACK_ON_NEW_FOLLOWER", "apply": "hot", "risk": True,
                "hint": "源项目 auto_follow_back_on_new_follower",
            },
            "match_keywords": {
                "label": "通用·包含词", "type": "str", "default": "",
                "env": "DY_AUTO_MATCH_KEYWORDS", "apply": "hot",
                "hint": "源项目 auto_match_keywords（逗号/空格分隔；空=不过滤）",
            },
            "exclude_keywords": {
                "label": "通用·排除词", "type": "str", "default": "",
                "env": "DY_AUTO_EXCLUDE_KEYWORDS", "apply": "hot",
                "hint": "源项目 auto_exclude_keywords",
            },
            "private_match_keywords": {
                "label": "私信·包含词", "type": "str", "default": "",
                "env": "DY_AUTO_PRIVATE_MATCH_KEYWORDS", "apply": "hot",
                "hint": "源项目 auto_private_match_keywords（空则回落通用词）",
            },
            "private_exclude_keywords": {
                "label": "私信·排除词", "type": "str", "default": "",
                "env": "DY_AUTO_PRIVATE_EXCLUDE_KEYWORDS", "apply": "hot",
                "hint": "源项目 auto_private_exclude_keywords（空则回落通用词）",
            },
            "comment_match_keywords": {
                "label": "评论·包含词", "type": "str", "default": "",
                "env": "DY_AUTO_COMMENT_MATCH_KEYWORDS", "apply": "hot",
                "hint": "源项目 auto_comment_match_keywords（空则回落通用词）",
            },
            "comment_exclude_keywords": {
                "label": "评论·排除词", "type": "str", "default": "",
                "env": "DY_AUTO_COMMENT_EXCLUDE_KEYWORDS", "apply": "hot",
                "hint": "源项目 auto_comment_exclude_keywords（空则回落通用词）",
            },
            "like_match_keywords": {
                "label": "点赞·包含词", "type": "str", "default": "",
                "env": "DY_AUTO_LIKE_MATCH_KEYWORDS", "apply": "hot",
                "hint": "源项目 auto_like_match_keywords（空则回落通用词）",
            },
            "like_exclude_keywords": {
                "label": "点赞·排除词", "type": "str", "default": "",
                "env": "DY_AUTO_LIKE_EXCLUDE_KEYWORDS", "apply": "hot",
                "hint": "源项目 auto_like_exclude_keywords（空则回落通用词）",
            },
            "collect_match_keywords": {
                "label": "收藏·包含词", "type": "str", "default": "",
                "env": "DY_AUTO_COLLECT_MATCH_KEYWORDS", "apply": "hot",
                "hint": "源项目 auto_collect_match_keywords（空则回落通用词）",
            },
            "collect_exclude_keywords": {
                "label": "收藏·排除词", "type": "str", "default": "",
                "env": "DY_AUTO_COLLECT_EXCLUDE_KEYWORDS", "apply": "hot",
                "hint": "源项目 auto_collect_exclude_keywords（空则回落通用词）",
            },
            "min_digg_count": {
                "label": "门槛·最少点赞", "type": "int", "default": 0, "min": 0, "max": 100000000,
                "env": "DY_AUTO_MIN_DIGG_COUNT", "apply": "hot",
                "hint": "源项目 auto_min_digg_count",
            },
            "min_comment_count": {
                "label": "门槛·最少评论", "type": "int", "default": 0, "min": 0, "max": 100000000,
                "env": "DY_AUTO_MIN_COMMENT_COUNT", "apply": "hot",
                "hint": "源项目 auto_min_comment_count",
            },
            "min_play_count": {
                "label": "门槛·最少播放", "type": "int", "default": 0, "min": 0, "max": 1000000000,
                "env": "DY_AUTO_MIN_PLAY_COUNT", "apply": "hot",
                "hint": "源项目 auto_min_play_count",
            },
            "scan_interval_seconds": {
                "label": "扫描间隔（秒）", "type": "int", "default": 30, "min": 10, "max": 300,
                "env": "DY_AUTO_SCAN_INTERVAL_SECONDS", "apply": "hot", "risk": True,
                "hint": "源项目 auto_scan_interval_seconds（clamp 10~300）",
            },
            "max_actions_per_run": {
                "label": "单轮最大动作数", "type": "int", "default": 5, "min": 1, "max": 50,
                "env": "DY_AUTO_MAX_ACTIONS_PER_RUN", "apply": "hot", "risk": True,
                "hint": "源项目 auto_max_actions_per_run（clamp 1~50）",
            },
            "send_delay_ms": {
                "label": "动作间隔（毫秒）", "type": "int", "default": 0, "min": 0, "max": 10000,
                "env": "DY_AUTO_SEND_DELAY_MS", "apply": "hot", "risk": True,
                "hint": "源项目 getAiAutoSendDelayMs（clamp 0~10000）",
            },
            "return_shared_media": {
                "label": "回流·共享媒体", "type": "bool", "default": False,
                "env": "DY_AUTO_RETURN_SHARED_MEDIA", "apply": "hot",
                "hint": "源项目 auto_return_shared_media",
            },
            "return_shared_allow_images": {
                "label": "回流·允许图片", "type": "bool", "default": True,
                "env": "DY_AUTO_RETURN_SHARED_ALLOW_IMAGES", "apply": "hot",
                "hint": "源项目 auto_return_shared_allow_images",
            },
            "return_shared_allow_videos": {
                "label": "回流·允许视频", "type": "bool", "default": True,
                "env": "DY_AUTO_RETURN_SHARED_ALLOW_VIDEOS", "apply": "hot",
                "hint": "源项目 auto_return_shared_allow_videos",
            },
            "return_shared_max_size_mb": {
                "label": "回流·单文件上限(MB)", "type": "int", "default": 20, "min": 1, "max": 200,
                "env": "DY_AUTO_RETURN_SHARED_MAX_SIZE_MB", "apply": "hot",
                "hint": "源项目 auto_return_shared_max_size_mb（clamp 1~200）",
            },
            "return_shared_max_media_count": {
                "label": "回流·最大媒体数", "type": "int", "default": 9, "min": 1, "max": 20,
                "env": "DY_AUTO_RETURN_SHARED_MAX_MEDIA_COUNT", "apply": "hot",
                "hint": "源项目 auto_return_shared_max_media_count（clamp 1~20）",
            },
            "auto_like": {
                "label": "动作·点赞", "type": "bool", "default": False,
                "env": "DY_AUTO_LIKE", "apply": "hot", "risk": True,
                "hint": "源项目 auto_like",
            },
            "auto_collect": {
                "label": "动作·收藏", "type": "bool", "default": False,
                "env": "DY_AUTO_COLLECT", "apply": "hot", "risk": True,
                "hint": "源项目 auto_collect",
            },
            "auto_comment": {
                "label": "动作·评论", "type": "bool", "default": False,
                "env": "DY_AUTO_COMMENT", "apply": "hot", "risk": True,
                "hint": "本项目扩展（源项目仅评论建议，无自动评论位）",
            },
            "auto_private": {
                "label": "动作·私信", "type": "bool", "default": False,
                "env": "DY_AUTO_PRIVATE", "apply": "hot", "risk": True,
                "hint": "本项目扩展",
            },
            "auto_follow": {
                "label": "动作·关注", "type": "bool", "default": False,
                "env": "DY_AUTO_FOLLOW", "apply": "hot", "risk": True,
                "hint": "本项目扩展（源项目仅有回关布尔位）",
            },
            "require_context": {
                "label": "需要上下文才动作", "type": "bool", "default": True,
                "env": "DY_AUTO_REQUIRE_CONTEXT", "apply": "hot",
                "hint": "本项目保留（源项目无此项）",
            },
            "use_global_gate": {
                "label": "同时使用全局发送闸门", "type": "bool", "default": True,
                "env": "DY_AUTO_USE_GLOBAL_GATE", "apply": "hot",
                "hint": "本项目保留（源项目无闸门）",
            },
        },
    },
}


SCHEMA_VERSION = 1
