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
            # P3 策略中心：timeout 默认值（热生效，消费端按需读 app_config.get("general", "timeout_xxx")）
            # 🔴 2026-09-23 修补（P2-9，实测复现）：三个 timeout_* 原声明 `apply:"hot"`
            #    却**缺 min/max** —— 而 app_config._coerce 对 int/float 只在声明了
            #    min/max 时才做范围校验 ⇒ 这三项可被写成任意值（含 0、负数、1e9），
            #    「范围保护」形同虚设。现补齐边界（SSOT 一致：无论消费方是否自 clamp，
            #    schema 声明都必须有边界）。取值依据 = 语义下限防止「立即超时/永不超时」：
            #      · BCC HTTP：0.5s 起（低于此连本机容器都来不及应答），120s 封顶；
            #      · 端口快速探活：0.05s 起（本机 socket 探测），10s 封顶；
            #      · 通用 HTTP：1s 起，300s 封顶。
            "timeout_bcc_http": {
                "label": "BCC HTTP 请求超时（秒）",
                "type": "float", "default": 15.0, "min": 0.5, "max": 120.0,
                "env": "DY_TIMEOUT_BCC_HTTP",
                "apply": "hot",
                "hint": "BCC 容器 HTTP 接口调用超时，覆盖约 18 处 hardcoded timeout=15",
            },
            "timeout_fast_probe": {
                "label": "端口快速探活超时（秒）",
                "type": "float", "default": 0.3, "min": 0.05, "max": 10.0,
                "env": "DY_TIMEOUT_FAST_PROBE",
                "apply": "hot",
                "hint": "socket 端口是否已开的快速检测超时，覆盖约 13 处 hardcoded timeout=0.3",
            },
            "timeout_http_req": {
                "label": "通用 HTTP 请求超时（秒）",
                "type": "float", "default": 30.0, "min": 1.0, "max": 300.0,
                "env": "DY_TIMEOUT_HTTP_REQ",
                "apply": "hot",
                "hint": "后端对外 HTTP API 调用的通用超时，覆盖约 23 处 hardcoded timeout=30",
            },
        },
    },

    # ===== 私信 / 昵称兜底 =====
    "dm": {
        "label": "私信 / 昵称兜底",
        "fields": {
            "nickname_fallback_enabled": {
                "label": "启用昵称兜底查询（默认关闭）",
                "type": "bool", "default": False, "env": None,
                "apply": "hot",
                "hint": ("**默认关闭**。开启后，仅对「库里没有昵称」的会话做低频兜底："
                         "走账号自己的浏览器页面上下文请求 im/user/info（复用登录态，"
                         "后端不直发 cookie），并受下面的间隔/单次/每日上限三重约束。"
                         "常规昵称来源仍是 BCC 被动截获，本项只是兜底。"),
            },
            "nickname_fallback_min_interval_sec": {
                "label": "兜底最小间隔（秒）",
                "type": "int", "default": 600, "env": None,
                "apply": "hot",
                "hint": "两次兜底调用之间的最小间隔，下限 60 秒；默认 600 秒（10 分钟）",
            },
            "nickname_fallback_max_per_run": {
                "label": "单次最多查询用户数",
                "type": "int", "default": 10, "env": None,
                "apply": "hot", "hint": "单次调用最多查询的用户数（上限 20，与接口批次一致）",
            },
            "nickname_fallback_daily_cap": {
                "label": "每日最多查询用户数",
                "type": "int", "default": 50, "env": None,
                "apply": "hot", "hint": "当日累计查询上限，达到后当日不再查询",
            },
            # ── 对话回复库「向量提纯」参数（2026-09-28）──
            "learn_min_cluster": {
                "label": "学习·通用性门槛（最少问法数）",
                "type": "int", "default": 2, "min": 1, "max": 20, "env": None,
                "apply": "hot",
                "hint": ("同一类问题至少被 N 条不同问法命中，才沉淀为通用条目；"
                         "1 = 不设门槛（不推荐，会重新引入个案照搬）"),
            },
            "learn_min_sources": {
                "label": "学习·通用性门槛（最少不同客户数）",
                "type": "int", "default": 2, "min": 1, "max": 50, "env": None,
                "apply": "hot",
                "hint": ("**真实通用判据**：同一类问题必须来自 ≥N 个不同会话/客户才算通用；"
                         "同一客户把同句问 N 遍不算（防「刷屏凑门槛」）"),
            },
            "learn_sim_threshold": {
                "label": "学习·聚类相似度阈值",
                "type": "float", "default": 0.80, "min": 0.5, "max": 0.99, "env": None,
                "apply": "hot",
                "hint": "问法向量余弦 ≥ 该值即视为同一问题；越高越严格（簇越碎）",
            },
            "learn_max_case_chars": {
                "label": "学习·个案问法长度上限",
                "type": "int", "default": 30, "min": 10, "max": 200, "env": None,
                "apply": "hot",
                "hint": "问法超过该长度视为个案照搬，不参与通用提纯",
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
            # ── 直播间主动互动（2026-09-30）──────────────────────────────
            # 🔴 这两个开关管的是**写接口**（/webcast/room/chat/ 与 /webcast/room/like/），
            #    触风控红线，故**默认 False = 休眠**（用户「显式配置原则」：
            #    行为由用户显式选择，不由代码默认替用户决定）。
            #    与上面的 enable_danmaku / enable_send 语义**无关**：
            #      · enable_danmaku = 是否**接收**弹幕（只读）
            #      · enable_send    = 采集到的目标是否**发送私信**
            #      · danmaku_enabled/like_enabled = 是否**主动在直播间发言/点赞**
            #    消费点：backend/api/live.py 的 POST /api/live/danmaku 与 /api/live/like。
            "danmaku_enabled": {
                "label": "发送弹幕（主动发言·写接口）", "type": "bool", "default": False,
                "env": None, "apply": "hot",
                "hint": ("**默认关闭**。开启后，直播页「发送弹幕」才会真实调用 "
                         "/webcast/room/chat/；关闭时端点直接拒发（reason=danmaku_disabled），"
                         "零出站。写接口触风控，非必要不发言。"),
            },
            "like_enabled": {
                "label": "点赞（主动互动·写接口）", "type": "bool", "default": False,
                "env": None, "apply": "hot",
                "hint": ("**默认关闭**。开启后，直播页「点赞 / 批量点赞」才会真实调用 "
                         "/webcast/room/like/；关闭时端点直接拒发（reason=like_disabled），"
                         "零出站。"),
            },
            "like_max": {
                "label": "单次点赞上限", "type": "int", "default": 1000,
                "min": 1, "max": 1000, "env": None, "apply": "hot",
                "hint": "批量点赞单次请求的上限（越界显式拒绝，不静默夹取）",
            },
        },
    },

    # ===== 直播编排策略（ADR-002 §5.4 策略中心；2026-09-22 v0.44.41）=====
    # 走统一配置中心（schema 驱动，前端零改动自动生成表单）。本 section 只声明参数与默认值；
    # 🔴 消费点在 ADR-002 §5.5（(B) 跨账号沉淀池 + 轮转）落地时接入 —— 当前**可配置**，
    #    但运行时**暂不消费**（label 已标「待接线」，避免「改了以为生效」的假成功）。
    "live_orchestration": {
        "label": "直播编排策略（多账号）· 待接线",
        "fields": {
            "connection_mode": {
                "label": "连接模式",
                "type": "select", "default": "credential", "env": None,
                "options": [
                    {"value": "credential", "label": "凭证连接（1 账号 1 任务，可解密）"},
                    {"value": "anonymous", "label": "匿名连接（多房间，身份脱敏）"},
                ],
                "apply": "hot",
                "hint": ("凭证 = 带账号 cookie，单账号单任务、能拿真实昵称/uid；"
                         "匿名 = 不带 cookie，可同时盯多个房间但**身份被脱敏为 111111**"
                         "（只适合开播检测/热度，拿不到真昵称）。两模式风控面不同。"),
            },
            "anonymous_max_rooms": {
                "label": "匿名模式并发房间上限",
                "type": "int", "default": 4, "min": 1, "max": 10, "env": None,
                "apply": "hot",
                "hint": "仅匿名模式生效；凭证模式硬约束为 1 账号 1 任务（不可调）",
            },
            "rotation_strategy": {
                "label": "发送轮转策略（同房间多账号）",
                "type": "select", "default": "per_target", "env": None,
                "options": [
                    {"value": "per_target", "label": "按目标轮转（同一用户交替由不同账号接待）"},
                    {"value": "per_time_window", "label": "按时间窗交替（账号按时间段轮换）"},
                    {"value": "per_room", "label": "按房间分配（每房间固定主责账号）"},
                ],
                "apply": "hot",
                "hint": ("仅多账号同房间（(B) 场景）时生效，决定「由哪个账号发」；"
                         "无论选哪种，同一用户都**绝不被双账号发送**（沉淀池兜底）。"),
            },
            "desensitized_strategy": {
                "label": "脱敏直播间处理策略",
                "type": "select", "default": "skip", "env": None,
                "options": [
                    {"value": "skip", "label": "跳过（不监听）— 默认"},
                    {"value": "observe_only", "label": "仅统计（不采昵称、不发送）"},
                    {"value": "prompt", "label": "提示后由用户决定"},
                    {"value": "reduce_anonymous", "label": "降级为匿名模式"},
                ],
                "apply": "hot",
                "hint": ("检测判据 = 无解密权（弹幕 uid=111111）。解密权取决于**房间归属**"
                         "（自营房间有、他人房间默认脱敏）；脱敏是他人房间正常态，非故障。"),
            },
            "sink_global_scope": {
                "label": "沉淀池全局作用域（跨账号去重）",
                "type": "bool", "default": True, "env": None,
                "apply": "hot", "risk": True,
                "hint": ("开启 = 同一用户被本机**任一**账号发过后，其余账号不再发"
                         "（多账号并发下防重复私信的关键）；关闭会退回按账号各自去重，"
                         "**同一用户可能被双账号发送**，增加风控面。"),
            },
            "sink_cooldown_days": {
                "label": "沉淀池冷却（天）",
                "type": "float", "default": 90.0, "min": 0.0, "max": 3650.0, "env": None,
                "apply": "hot", "risk": True,
                "hint": ("默认 90 天内的用户不再重复发送；0 = 不冷却（每次都发，风控面最大）。"
                         "「永久」档须另开下方开关，默认不选。"),
            },
            "sink_permanent": {
                "label": "「永久冷却」档",
                "type": "bool", "default": False, "env": None,
                "apply": "hot", "risk": True,
                "hint": ("默认关闭（保留可逆性）。开启后忽略上方天数：该用户一经发送**永不再发**"
                         "（不可逆，仅在确认无需复联时开启）。"),
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
            "per_minute_limit": {
                "label": "全账号 每分钟发送上限",
                "type": "int", "default": 3, "min": 0, "max": 60,
                "env": "DY_SEND_PER_MINUTE",
                "apply": "hot", "risk": True,
                "hint": ("账号级分钟窗（滑窗，**物理闸门**：文本/图片/直发全部计入，"
                         "任何路径都无法绕过）。0 = 不启用。"
                         "手动发送是否豁免见下一项「手动豁免分钟窗」"),
            },
            "per_minute_manual_exempt": {
                "label": "手动发送豁免分钟窗",
                "type": "bool", "default": True, "env": None,
                "apply": "hot", "risk": True,
                "hint": ("开（默认）：用户手动发送**不被**每分钟上限拦下，但**仍计入**"
                         "分钟窗（抬高后续自动发送水位）—— 门禁不拦用户显式操作。"
                         "关：手动发送同样受每分钟上限约束（账号风控升级时可收紧）"),
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
            # ── ADR-007 / C-06（2026-09-24）沉淀池增强 ──────────────
            # ⚠️ 默认值 = **关闭**（0），非 ADR 建议的 300/10。
            # 理由：窗口默认开会**静默延迟**发送、阈值默认开（strict 下）会
            #   **过滤掉大部分目标** ⇒ 属「静默改变核心发送行为」，违反用户
            #   「显式配置」原则。故能力就绪但**休眠**，由用户在配置中心显式开启。
            "uid_sink_window_seconds": {
                "label": "沉淀窗口时长（秒）",
                "type": "float", "default": 0.0, "min": 0, "max": 86400,
                "env": "DY_UID_SINK_WINDOW", "apply": "hot",
                "hint": "先收 N 秒再统一发送（0=即见即发，退回原行为）",
            },
            "high_value_window_seconds": {
                "label": "高价值加速窗口（秒）",
                "type": "float", "default": 60.0, "min": 0, "max": 86400,
                "env": "DY_HIGH_VALUE_WINDOW", "apply": "hot",
                "hint": "高价值用户的缩短窗口（ADR-007 D2 热度加速）",
            },
            "high_value_score_threshold": {
                "label": "高价值关键词阈值",
                "type": "int", "default": 0, "min": 0, "max": 1000,
                "env": "DY_HIGH_VALUE_THRESHOLD", "apply": "hot",
                "hint": "关键词权重和 ≥ 该值判为高价值（0=关闭筛查，全部放行）",
            },
            "high_value_llm_enabled": {
                "label": "高价值 LLM 精判",
                "type": "bool", "default": False,
                "env": "DY_HIGH_VALUE_LLM", "apply": "hot",
                "hint": "开则对关键词候选做 LLM 精判；AI 不可用时自动退化为纯关键词（不阻塞）",
            },
            "aggregate_max_chars": {
                "label": "聚合文本上限（字符）",
                "type": "int", "default": 2000, "min": 100, "max": 20000,
                "env": "DY_AGGREGATE_MAX_CHARS", "apply": "hot",
                "hint": "aggregate_text 累积截断上界，防单行无限膨胀",
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
            # ===== 能力探针 M1 阈值（2026-09-21 P2，见 工作记忆/02_效果定义与探针.md）=====
            # 探针只读本地事实（DB + 本项目日志），不发起任何网络/浏览器动作。
            # 这些阈值定义「什么算健康/降级/失败」，可按账号实测调。
            "probe_min_convs": {
                "label": "探针·最低会话数",
                "type": "int", "default": 10, "min": 1, "max": 5000,
                "env": "DY_PROBE_MIN_CONVS", "apply": "hot",
                "hint": "低于此数判 degraded —— 覆盖骤降属典型静默失效",
            },
            "probe_nickname_healthy": {
                "label": "探针·昵称覆盖率健康线",
                "type": "float", "default": 0.95, "min": 0.0, "max": 1.0,
                "env": "DY_PROBE_NICKNAME_HEALTHY", "apply": "hot",
                "hint": "≥此值判 healthy（02 文档：昵称覆盖率 ≥ 95%）",
            },
            "probe_nickname_degraded": {
                "label": "探针·昵称覆盖率失败线",
                "type": "float", "default": 0.50, "min": 0.0, "max": 1.0,
                "env": "DY_PROBE_NICKNAME_DEGRADED", "apply": "hot",
                "hint": "低于此值判 failed；介于两线之间判 degraded（三态，非二态）",
            },
            "probe_capture_stale_sec": {
                "label": "探针·捕获陈旧阈值（秒）",
                "type": "int", "default": 3600, "min": 60, "max": 604800,
                "env": "DY_PROBE_CAPTURE_STALE_SEC", "apply": "hot",
                "hint": "最新捕获日志超此秒数未更新 → 判 failed（能力停摆）",
            },
            # ---- 其余业务域探针（P1，2026-09-21 v0.44.27）----
            "probe_live_window_hours": {
                "label": "探针·直播弹幕观察窗（小时）",
                "type": "int", "default": 24, "min": 1, "max": 720,
                "env": "DY_PROBE_LIVE_WINDOW_HOURS", "apply": "hot",
                "hint": "窗口内无弹幕记录 → 判 unknown（未监听，非失效）",
            },
            "probe_live_healthy": {
                "label": "探针·弹幕真实率健康线",
                "type": "float", "default": 0.95, "min": 0.0, "max": 1.0,
                "env": "DY_PROBE_LIVE_HEALTHY", "apply": "hot",
                "hint": "脱敏判据：uid==111111 且 sec_uid 空（勿用 desensitized_nickname）",
            },
            "probe_live_degraded": {
                "label": "探针·弹幕真实率失败线",
                "type": "float", "default": 0.50, "min": 0.0, "max": 1.0,
                "env": "DY_PROBE_LIVE_DEGRADED", "apply": "hot",
                "hint": "低于此值判 failed（昵称被脱敏 = 无解密权/凭证降权）",
            },
            "probe_ai_window_hours": {
                "label": "探针·AI 活动观察窗（小时）",
                "type": "int", "default": 72, "min": 1, "max": 2160,
                "env": "DY_PROBE_AI_WINDOW_HOURS", "apply": "hot",
                "hint": "窗口内 AI 相关日志行数，用于判断该域是否在活动",
            },
            # ---- 定时巡检（P1 收尾，2026-09-21 v0.44.28）----
            # 探针只读本地事实（DB+本项目日志），零网络零浏览器，故可安全常驻。
            "probe_patrol_enabled": {
                "label": "探针·启用定时巡检",
                "type": "bool", "default": True,
                "env": "DY_PROBE_PATROL_ENABLED", "apply": "restart_backend",
                "hint": "关闭后只能手动跑（/api/probe/run）；探针零风控，建议保持开启",
            },
            "probe_patrol_interval_min": {
                "label": "探针·巡检周期（分钟）",
                "type": "int", "default": 15, "min": 1, "max": 1440,
                "env": "DY_PROBE_PATROL_INTERVAL_MIN", "apply": "restart_backend",
                "hint": "能力劣化时由此周期决定「多久先于用户被发现」",
            },
            "probe_patrol_first_delay_sec": {
                "label": "探针·巡检首轮延迟（秒）",
                "type": "int", "default": 120, "min": 30, "max": 3600,
                "env": "DY_PROBE_PATROL_FIRST_DELAY_SEC", "apply": "restart_backend",
                "hint": "避开启动初始化高峰；最小 30s",
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
