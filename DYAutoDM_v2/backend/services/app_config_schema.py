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
                "label": "启动时拉起浏览器容器",
                "type": "bool", "default": False, "env": "DY_BCC_ON_START",
                "apply": "restart_backend",
            },
            "bcc_headless_mode": {
                "label": "浏览器容器无头模式",
                "type": "select", "default": "native", "env": "DY_BCC_HEADLESS_MODE",
                "options": [{"value": "native", "label": "无头（仅此模式）"}],
                "apply": "restart_backend",
            },
            "cred_refresh_mode": {
                "label": "凭证更新方式",
                "type": "select", "default": "observe", "env": "DY_CRED_REFRESH_MODE",
                "options": [
                    {"value": "observe", "label": "静默更新（推荐）"},
                    {"value": "popup", "label": "弹窗提醒更新"},
                    {"value": "both", "label": "静默为主，失败再弹窗"},
                ],
                "apply": "restart_daemon",
            },
            # 凭证更新通道（扫码 / 短信）—— 2026-10-01 配置化
            # 背景：原先通道只能靠环境变量 DY_LOGIN_QR_BACKEND 切换（api=纯协议 /
            # 空或 bridge=接口桥），用户在 UI 上无从选择/无从知晓。现提升为一等公民
            # 配置项，热生效（每次扫码前读取，无需重启）。
            # 取值 ancestry（读取端见 backend/api/accounts.py::_resolve_login_channel）：
            #   配置中心 general.login_channel  →  环境变量 DY_LOGIN_CHANNEL
            #   →  兼容旧环境变量 DY_LOGIN_QR_BACKEND  →  默认 bridge
            # label 必须**如实标注能力边界**（禁止把实验通道包装得像可用通道）：
            #   api 通道 2026-10-01 实测三轮一致：出码仅 3.5s，但二维码约 65s 即被
            #   服务端判 expired（正常 5 分钟的 1/5）⇒ 出码快但无法完成登录。
            "login_channel": {
                "label": "凭证更新通道",
                "type": "select", "default": "bridge", "env": "DY_LOGIN_CHANNEL",
                "options": [
                    {"value": "bridge", "label": "接口桥（推荐）"},
                    {"value": "api",
                     "label": "API（实验·当前跑不通）"},
                    {"value": "manual", "label": "有头浏览器（兜底）"},
                ],
                "apply": "hot",
                "hint": "默认接口桥",
                # 🔴 完整能力边界（hint 限 10 字，故详注在此，勿删）：
                #   · bridge = 无头 Camoufox、真实身份（推荐·默认）
                #   · api    = 上游纯协议链路（qrcodeMain）。**设计上**可完成登录
                #             （自建 P-256 密钥 + redirect_url/status==2 判据），
                #             但 2026-10-01 实测**当前跑不通** —— status=no_qrcode，
                #             连码都出不来。两条已证成因：
                #             ① 该路径走 **plain requests**（非 curl_cffi）⇒ TLS 指纹暴露，
                #                get_qrcode 返回 10168 字节 **HTML** 而非 JSON ⇒ safe_json 降级 None；
                #             ② AUTH-062：`keys 有效=False`（账号未登录 ⇒ 取不到 security-sdk）
                #                ⇒ 请求缺签名四件套。
                #             ⇒ 结论应表述为「**当前跑不通**」，而非「方案无用」。
                #             修 ②（登录态写回浏览器 profile）后 keys 前置有望满足，可再测。
                #   · manual = 弹出有头浏览器由用户自行完成（最终兜底）
                #   兼容旧环境变量 DY_LOGIN_QR_BACKEND（配置中心留空时生效）。
            },
            "auto_capture_on_start": {
                "label": "启动时自动捕获会话",
                "type": "bool", "default": False, "env": "DY_AUTO_CAPTURE_ON_START",
                "apply": "restart_backend",
            },
            # ===== 批量采集（2026-10-03）=====
            # 🔴 放 `general` 而非 `live_orchestration` 的理由（用户 2026-10-03 定调）：
            #   `live_orchestration` **整体受标签管**（config_tag.MANAGED_SECTIONS 含它），
            #   UI 会弹出「保存到 全局/标签」切换栏。但标签是「给账号/房间分发送参数」
            #   的机制，而批量总开关是**功能门** —— 把它放进标签域会出现
            #   「标签 A 开、标签 B 关」的功能级分裂状态（无法解释也无法排查），
            #   且用户在标签下改了值却**不生效**（消费侧刻意不传 scope）。
            #   `general` 不在 MANAGED_SECTIONS 内 ⇒ UI 无标签栏、纯全局，语义干净。
            # apply 刻意用 "hot"（区别于本分区既有的 restart_backend）：
            #   开关改完应**立即**可用，不该要求重启 backend。
            "batch_enabled": {
                "label": "批量采集总开关",
                "type": "bool", "default": False, "env": "DY_LIVE_BATCH_ENABLED",
                "apply": "hot", "risk": True,
                "hint": "关闭时写操作拒绝"
            },
            "batch_max_concurrent": {
                "label": "批量并发实例上限",
                "type": "int", "default": 3, "min": 1, "max": 10, "env": None,
                "apply": "hot", "risk": True,
                "hint": "同时监听房间数"
            },
            "batch_rate_limit_per_min": {
                "label": "批量速率上限（次/分）",
                "type": "int", "default": 60, "min": 1, "max": 600, "env": None,
                "apply": "hot", "risk": True,
                "hint": "防止批量轮询"
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
                "label": "浏览器容器 HTTP 请求超时（秒）",
                "type": "float", "default": 15.0, "min": 0.5, "max": 120.0,
                "env": "DY_TIMEOUT_BCC_HTTP",
                "apply": "hot",
            },
            "timeout_fast_probe": {
                "label": "端口快速探活超时（秒）",
                "type": "float", "default": 0.3, "min": 0.05, "max": 10.0,
                "env": "DY_TIMEOUT_FAST_PROBE",
                "apply": "hot",
            },
            "timeout_http_req": {
                "label": "通用 HTTP 请求超时（秒）",
                "type": "float", "default": 30.0, "min": 1.0, "max": 300.0,
                "env": "DY_TIMEOUT_HTTP_REQ",
                "apply": "hot",
            },
        },
    },

    # ===== 系统（2026-10-02 用户要求：系统页的文件导出路径管理）=====
    # 与前端「系统」tab 对应。放独立分区而非 general，避免同一字段
    # 在「通用配置」与「系统」两处重复出现（用户要求该管理在系统页）。
    "system": {
        "label": "系统",
        "fields": {
            "export_dir": {
                "label": "文件导出目录",
                "type": "str", "default": "", "env": None,
                "apply": "hot",
                "hint": "留空用默认目录",
            },
        },
    },

    # ===== 私信列表（原「私信 / 昵称兜底」；2026-10-04 用户指令改名）=====
    "dm": {
        "label": "私信列表",
        "fields": {
            "nickname_fallback_enabled": {
                "label": "启用昵称兜底查询（默认关闭）",
                "type": "bool", "default": False, "env": None,
                "apply": "hot",
                "hint": "无昵称时低频补查"
            },
            "nickname_fallback_min_interval_sec": {
                "label": "兜底最小间隔（秒）",
                "type": "int", "default": 600, "env": None,
                "apply": "hot",
            },
            "nickname_fallback_max_per_run": {
                "label": "单次最多查询用户数",
                "type": "int", "default": 10, "env": None,
                "apply": "hot",
            },
            "nickname_fallback_daily_cap": {
                "label": "每日最多查询用户数",
                "type": "int", "default": 50, "env": None,
                "apply": "hot",
            },
            # ── 对话回复库「向量提纯」参数（2026-09-28）──
            "learn_min_cluster": {
                "label": "学习·通用性门槛（最少问法数）",
                "type": "int", "default": 2, "min": 1, "max": 20, "env": None,
                "apply": "hot",
                "hint": "至少几种问法算通用"
            },
            "learn_min_sources": {
                "label": "学习·通用性门槛（最少不同客户数）",
                "type": "int", "default": 2, "min": 1, "max": 50, "env": None,
                "apply": "hot",
                "hint": "至少几个客户算通用"
            },
            "learn_sim_threshold": {
                "label": "学习·聚类相似度阈值",
                "type": "float", "default": 0.80, "min": 0.5, "max": 0.99, "env": None,
                "apply": "hot",
                "hint": "问法相似度阈值"
            },
            "learn_max_case_chars": {
                "label": "学习·个案问法长度上限",
                "type": "int", "default": 30, "min": 10, "max": 200, "env": None,
                "apply": "hot",
                "hint": "超此长度视为个案"
            },
        },
    },

    # ===== 监听策略（原「直播监听」；2026-10-04 改名）=====
    # ⚠️ 本分区是**通用策略配置**，不绑定某个直播间 —— 故不含 live_url。
    # 直播间链接只由**直播间号**（room_id）推导，见 api/live_config.resolve_live_url()；
    # live_url 早被列为「身份/废弃字段」（live_config.py:110 会剔除）。
    "live": {
        "label": "监听策略",
        "fields": {
            "max_target": {
                "label": "每场私信上限",
                "type": "int", "default": 3, "min": 1, "max": 200, "env": None,
                "apply": "hot",
            },
            "interval": {
                "label": "私信间隔（秒）",
                "type": "float", "default": 60.0, "min": 1, "max": 3600, "env": None,
                "apply": "hot",
            },
            "delay_min": {
                "label": "延迟抖动下限（秒）",
                "type": "int", "default": 40, "min": 0, "max": 600, "env": None,
                "apply": "hot", "hint": "发送前随机延迟"
            },
            "delay_max": {
                "label": "延迟抖动上限（秒）",
                "type": "int", "default": 65, "min": 0, "max": 900, "env": None,
                "apply": "hot",
            },
            "live_poll_interval": {
                "label": "未开播轮询间隔（秒）",
                "type": "int", "default": 30, "min": 5, "max": 600, "env": None,
                "apply": "hot",
            },
            "ws_heartbeat_interval": {
                "label": "WS 心跳间隔（秒）",
                "type": "int", "default": 300, "min": 30, "max": 3600, "env": None,
                "apply": "hot", "hint": "定期探活重连"
            },
            # ── 红心（真实点赞）/ 贡献榜 刷新节拍（2026-10-01，HC-16 M-30）────
            # 红心的 `real` 取自 reflow/info 的 room.like_count，目前挂在贡献榜轮询
            # 节拍上 ⇒ 总量最多滞后一个节拍。此键让节拍**显式可配**（用户「显式
            # 配置原则」：行为由配置显式选择，不依赖本机外部可变状态）。
            #   · 默认 60 —— 与改前的硬编码完全一致 ⇒ **不改配置 = 行为不变**；
            #   · 下限 15 —— 更低会把 reflow/info 的请求频次放大到异常形状
            #     （风控敏感），故写小一律抬回下限；
            #   · 上限 600 —— 更慢已失去「实时」意义（此时 WS 增量仍即时可见）。
            # 消费点：core/live_hook.py::start_rank_poll
            "rank_poll_interval_sec": {
                "label": "红心刷新间隔（秒）",
                "type": "int", "default": 60, "min": 15, "max": 600, "env": None,
                "apply": "hot", "hint": "越小越频繁"
            },
            "enable_danmaku": {
                "label": "接收弹幕", "type": "bool", "default": True, "env": None,
                "apply": "hot",
            },
            "enable_console": {
                "label": "控制台输出", "type": "bool", "default": True, "env": None,
                "apply": "hot",
            },
            "enable_send": {
                "label": "启用发送", "type": "bool", "default": True, "env": None,
                "apply": "hot", "hint": "关 = 只采集不发送"
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
                "hint": "开启后才能发言"
            },
            "like_enabled": {
                "label": "点赞（主动互动·写接口）", "type": "bool", "default": False,
                "env": None, "apply": "hot",
                "hint": "开启后才能点赞"
            },
            "like_max": {
                "label": "单次点赞上限", "type": "int", "default": 1000,
                "min": 1, "max": 1000, "env": None, "apply": "hot",
            },
            # ===== 写接口**自动化**（2026-10-01 新增；总开关默认关）=====
            # 全部默认休眠：不改任何配置 ⇒ 零出站（可断言的零回归）。
            # 消费点：services/live_automation.py（由 live_hook 随监听生命周期启停）。
            "automation_enabled": {
                "label": "写接口自动化总开关", "type": "bool", "default": False,
                "env": None, "apply": "hot",
                "hint": "关则以下全不生效"
            },
            "danmaku_timer_enabled": {
                "label": "定时发弹幕", "type": "bool", "default": False,
                "env": None, "apply": "hot",
                "hint": "监听开始才生效"
            },
            "danmaku_timer_min": {
                "label": "弹幕间隔下限", "type": "float", "default": 3.0,
                "min": 0.5, "max": 120.0, "env": None, "apply": "hot",
                "hint": "单位：分钟"
            },
            "danmaku_timer_max": {
                "label": "弹幕间隔上限", "type": "float", "default": 6.0,
                "min": 0.5, "max": 240.0, "env": None, "apply": "hot",
                "hint": "每轮在此区间随机"
            },
            "danmaku_timer_max_per_run": {
                "label": "单轮最多发几条", "type": "int", "default": 3,
                "min": 1, "max": 20, "env": None, "apply": "hot",
                "hint": "正常每轮 1 条"
            },
            "like_batch_enabled": {
                "label": "分步批量点赞", "type": "bool", "default": False,
                "env": None, "apply": "hot",
                "hint": "监听开始才生效"
            },
            "like_batch_total": {
                "label": "批量点赞总数", "type": "int", "default": 3000,
                "min": 1, "max": 100000, "env": None, "apply": "hot",
            },
            "like_batch_steps": {
                "label": "分几步完成", "type": "int", "default": 4,
                "min": 1, "max": 50, "env": None, "apply": "hot",
            },
            "like_batch_step_max": {
                "label": "单步点赞上限", "type": "int", "default": 1000,
                "min": 1, "max": 1000, "env": None, "apply": "hot",
                "hint": "超出则自动增加步数"
            },
            "like_batch_cooldown_sec": {
                "label": "步间冷却（秒）", "type": "int", "default": 150,
                "min": 120, "max": 1800, "env": None, "apply": "hot",
                "hint": "下限 120 秒"
            },
            # 2026-10-02（用户定调「策略以标签为主」）：原 `live_room_configs`
            # 房间级策略的字段迁入本分区 —— 否则下线 RoomConfigPage 后
            # 连麦设置 / 弹幕文案库将「无处可配」（能力净损失）。
            # 列表型用**换行分隔字符串**承载（与 automation 的 *_keywords 同范式）。
            # ⚠️ 2026-10-04：`dm_pool` 已迁回 send 分区（私信文案归属「私信发送」），
            #    本分区只保留**监听侧**的弹幕文案库。
            "danmaku_pool": {
                "label": "弹幕文案库（每行一条）", "type": "str", "default": "",
                "env": None, "apply": "hot",
                "hint": "每行一条，定时发送"
            },
            "auto_link_mic": {
                "label": "自动申请连麦", "type": "bool", "default": False,
                "env": None, "apply": "hot",
                "hint": "写接口·需开连麦"
            },
            "link_mic_mode": {
                "label": "连麦方式", "type": "select", "default": "audio",
                "options": [{"value": "audio", "label": "语音"},
                            {"value": "video", "label": "视频"}],
                "env": None, "apply": "hot",
                "hint": "语音或视频"
            },
        },
    },

    # ===== 直播编排策略（ADR-002 §5.4 策略中心；2026-09-22 v0.44.41）=====
    # 走统一配置中心（schema 驱动，前端零改动自动生成表单）。本 section 只声明参数与默认值；
    # 🔴 消费点在 ADR-002 §5.5（(B) 跨账号沉淀池 + 轮转）落地时接入 —— 当前**可配置**，
    #    但运行时**暂不消费**（label 已标「待接线」，避免「改了以为生效」的假成功）。
    "live_orchestration": {
        "label": "直播编排策略（多账号）",
        "fields": {
            "connection_mode": {
                "label": "连接模式",
                "type": "select", "default": "credential", "env": None,
                "options": [
                    {"value": "credential", "label": "凭证连接"},
                    {"value": "anonymous", "label": "匿名连接"},
                ],
                "apply": "hot",
                "hint": "凭证或匿名"
            },
            "anonymous_max_rooms": {
                "label": "匿名模式并发房间上限",
                "type": "int", "default": 4, "min": 1, "max": 10, "env": None,
                "apply": "hot",
                "hint": "仅匿名模式生效"
            },
            "rotation_strategy": {
                "label": "发送轮转策略（同房间多账号）",
                "type": "select", "default": "per_target", "env": None,
                "options": [
                    {"value": "per_target", "label": "按目标轮转"},
                    {"value": "per_time_window", "label": "按时间段轮换"},
                    {"value": "per_room", "label": "每房间固定账号"},
                ],
                "apply": "hot",
                "hint": "多账号由谁发"
            },
            "desensitized_strategy": {
                "label": "脱敏直播间处理策略",
                "type": "select", "default": "skip", "env": None,
                "options": [
                    {"value": "skip", "label": "跳过（默认）"},
                    {"value": "observe_only", "label": "仅统计，不发送"},
                    {"value": "prompt", "label": "提示后由用户决定"},
                    {"value": "reduce_anonymous", "label": "降级为匿名模式"},
                ],
                "apply": "hot",
                "hint": "无解密权时处理"
            },
            "sink_global_scope": {
                "label": "沉淀池全局作用域（跨账号去重）",
                "type": "bool", "default": True, "env": None,
                "apply": "hot", "risk": True,
                "hint": "跨账号去重"
            },
            "sink_cooldown_days": {
                "label": "沉淀池冷却（天）",
                "type": "float", "default": 90.0, "min": 0.0, "max": 3650.0, "env": None,
                "apply": "hot", "risk": True,
                "hint": "冷却期内不重发"
            },
            "sink_permanent": {
                "label": "「永久冷却」档",
                "type": "bool", "default": False, "env": None,
                "apply": "hot", "risk": True,
                "hint": "开启后永不重发"
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
                "hint": "两条发送的最小间隔"
            },
            "max_wait": {
                "label": "闸门排队等待上限（秒）",
                "type": "float", "default": 30.0, "min": 5, "max": 300,
                "env": "DY_SEND_MAX_WAIT",
                "apply": "restart_daemon", "risk": True,
                "hint": "超时快速失败"
            },
            "per_minute_limit": {
                "label": "全账号 每分钟发送上限",
                "type": "int", "default": 3, "min": 0, "max": 60,
                "env": "DY_SEND_PER_MINUTE",
                "apply": "hot", "risk": True,
                "hint": "每分钟发送上限"
            },
            "per_minute_manual_exempt": {
                "label": "手动发送豁免分钟窗",
                "type": "bool", "default": True, "env": None,
                "apply": "hot", "risk": True,
                "hint": "手动发送不受限"
            },
            "stranger_per_minute": {
                "label": "陌生人首发 每分钟上限",
                "type": "int", "default": 2, "min": 0, "max": 60,
                "env": "DY_STRANGER_PER_MINUTE",
                "apply": "hot", "risk": True, "hint": "陌生人每分钟上限"
            },
            "stranger_per_day": {
                "label": "陌生人首发 每日上限",
                "type": "int", "default": 30, "min": 0, "max": 2000,
                "env": "DY_STRANGER_PER_DAY",
                "apply": "hot", "risk": True, "hint": "陌生人每日上限"
            },
            "cooldown_freq": {
                "label": "频控命中冷静期（秒）",
                "type": "float", "default": 600.0, "min": 0, "max": 86400,
                "env": "DY_DM_COOLDOWN_FREQ",
                "apply": "hot", "risk": True, "hint": "被频控后的暂停时长"
            },
            "cooldown_max": {
                "label": "冷静期上限（秒）",
                "type": "float", "default": 3600.0, "min": 0, "max": 604800,
                "env": "DY_DM_COOLDOWN_MAX",
                "apply": "hot", "risk": True, "hint": "冷静期封顶值"
            },
            "weight_recover_halflife": {
                "label": "权重恢复半衰期（秒）",
                "type": "float", "default": 21600.0, "min": 60, "max": 604800,
                "env": "DY_WEIGHT_RECOVER_HALFLIFE",
                "apply": "hot", "risk": True, "hint": "降权后恢复速度"
            },
            "weight_forgive_after": {
                "label": "权重原谅期（秒）",
                "type": "float", "default": 86400.0, "min": 60, "max": 2592000,
                "env": "DY_WEIGHT_FORGIVE_AFTER",
                "apply": "hot", "risk": True, "hint": "视为已原谅的时长"
            },
            "dedup_window": {
                "label": "去重窗口（秒）",
                "type": "float", "default": 5.0, "min": 0, "max": 3600,
                "env": "DY_DM_DEDUP_WINDOW", "apply": "hot", "risk": True,
                "hint": "同文本去重窗口"
            },
            "queue_max": {
                "label": "队列上限",
                "type": "int", "default": 200, "min": 1, "max": 10000,
                "env": "DY_DM_QUEUE_MAX", "apply": "hot", "risk": True,
                "hint": "超出则拒绝入队"
            },
            "pool_strict": {
                "label": "会话整理严格模式",
                "type": "bool", "default": True, "env": "DY_DM_POOL_STRICT",
                "apply": "hot", "risk": True,
                "hint": "无法识别对端即拒绝"
            },
            "uid_sink_cooldown": {
                "label": "UID 沉淀冷却（秒）",
                "type": "float", "default": 604800.0, "min": 0, "max": 31536000,
                "env": "DY_UID_SINK_COOLDOWN", "apply": "hot", "risk": True,
                "hint": "同 UID 冷却时长"
            },
            "uid_sink_strict": {
                "label": "UID 沉淀严格模式",
                "type": "bool", "default": True, "env": "DY_UID_SINK_STRICT",
                "apply": "hot", "risk": True, "hint": "关则仅提示不拦截"
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
                "hint": "先收集再统一发送"
            },
            "high_value_window_seconds": {
                "label": "高价值加速窗口（秒）",
                "type": "float", "default": 60.0, "min": 0, "max": 86400,
                "env": "DY_HIGH_VALUE_WINDOW", "apply": "hot",
                "hint": "高价值的加速窗口"
            },
            "high_value_score_threshold": {
                "label": "高价值关键词阈值",
                "type": "int", "default": 0, "min": 0, "max": 1000,
                "env": "DY_HIGH_VALUE_THRESHOLD", "apply": "hot",
                "hint": "判高价值的权重门槛"
            },
            "high_value_llm_enabled": {
                "label": "高价值 LLM 精判",
                "type": "bool", "default": False,
                "env": "DY_HIGH_VALUE_LLM", "apply": "hot",
                "hint": "对候选做 AI 精判"
            },
            "aggregate_max_chars": {
                "label": "聚合文本上限（字符）",
                "type": "int", "default": 2000, "min": 100, "max": 20000,
                "env": "DY_AGGREGATE_MAX_CHARS", "apply": "hot",
                "hint": "聚合文本截断上限"
            },
            # 2026-10-04（用户指令）：私信词库从 live 分区**迁回** send 分区。
            # 它本就是「私信文案」，归属「私信发送」；此前由 f08288a
            # （「策略以标签为主」）随房间级策略一并挪进 live，属错位。
            # 列表型用**换行分隔字符串**承载（与 automation 的 *_keywords 同范式）。
            # 读取方：api/live_config._tag_to_strategy_cfg（单任务）、
            #         services/live_batch.resolve_tag_send_params（批量）、
            #         api/crawl.py（采集后私信文案，本就按 send 读）。
            "dm_pool": {
                "label": "私信词库（每行一条）", "type": "str", "default": "",
                "env": None, "apply": "hot",
                "hint": "每行一条，随机选用"
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
                "hint": "留空用全局配置"
            },
            "llm_model": {
                "label": "通知解析模型",
                "type": "str", "default": "glm-5.2",
                "env": None, "apply": "hot",
                "hint": "指令转结构化操作"
            },
            "llm_api_key": {
                "label": "通知解析 API Key",
                "type": "str", "default": "",
                "env": None, "apply": "hot",
                "hint": "留空用全局配置"
            },
            "llm_enabled": {
                "label": "启用模型指令解析",
                "type": "bool", "default": False,
                "env": None, "apply": "hot",
                "hint": "关则只用规则"
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
            },
            "history_max_rounds": {
                "label": "单次最多轮数（分批）",
                "type": "int", "default": 3, "min": 1, "max": 10,
                "env": "DY_HISTORY_MAX_ROUNDS", "apply": "hot",
                "hint": "更新会话最多跑几轮"
            },
            "history_batch_gap": {
                "label": "轮间隔（秒）",
                "type": "float", "default": 60.0, "min": 0, "max": 3600,
                "env": "DY_HISTORY_BATCH_GAP", "apply": "hot",
                "hint": "轮间等待防风控"
            },
            "history_sleep": {
                "label": "补全间隔（秒）",
                "type": "float", "default": 1.5, "min": 0, "max": 30,
                "env": "DY_HISTORY_SLEEP", "apply": "hot", "hint": "每次请求之间的间隔"
            },
            "history_workers": {
                "label": "补全并发数",
                "type": "int", "default": 4, "min": 1, "max": 8,
                "env": "DY_HISTORY_WORKERS", "apply": "hot",
            },
            "history_full": {
                "label": "启用历史补全",
                "type": "bool", "default": True, "env": "DY_HISTORY_FULL",
                "apply": "hot", "hint": "长会话需补全历史"
            },
            "history_skip_paged": {
                "label": "跳过翻页补全",
                "type": "bool", "default": False, "env": "DY_HISTORY_SKIP_PAGED",
                "apply": "hot", "hint": "调试用"
            },
            "userinfo_cache_sec": {
                "label": "昵称缓存 TTL（秒）",
                "type": "int", "default": 600, "min": 0, "max": 86400,
                "env": "DY_USERINFO_CACHE_SEC", "apply": "restart_daemon",
                "hint": "昵称缓存的有效期"
            },
            "uid_probe_ttl_ok": {
                "label": "UID 探活缓存 TTL（秒）",
                "type": "float", "default": 300.0, "min": 0, "max": 86400,
                "env": "DY_UID_PROBE_TTL_OK", "apply": "restart_daemon",
                "hint": "探活成功后的缓存时长"
            },
            "uid_probe_ttl_fail": {
                "label": "探活失败退避（秒）",
                "type": "float", "default": 60.0, "min": 0, "max": 3600,
                "env": "DY_UID_PROBE_TTL_FAIL", "apply": "restart_daemon",
                "hint": "失败后不重试"
            },
            "uid_probe_lock_wait": {
                "label": "探活锁等待上限（秒）",
                "type": "float", "default": 10.0, "min": 1, "max": 120,
                "env": "DY_UID_PROBE_LOCK_WAIT", "apply": "restart_daemon",
                "hint": "超时退化为读缓存"
            },
            # ===== 能力探针 M1 阈值（2026-09-21 P2，见 工作记忆/02_效果定义与探针.md）=====
            # 探针只读本地事实（DB + 本项目日志），不发起任何网络/浏览器动作。
            # 这些阈值定义「什么算健康/降级/失败」，可按账号实测调。
            "probe_min_convs": {
                "label": "探针·最低会话数",
                "type": "int", "default": 10, "min": 1, "max": 5000,
                "env": "DY_PROBE_MIN_CONVS", "apply": "hot",
                "hint": "低于此数判为异常"
            },
            "probe_nickname_healthy": {
                "label": "探针·昵称覆盖率健康线",
                "type": "float", "default": 0.95, "min": 0.0, "max": 1.0,
                "env": "DY_PROBE_NICKNAME_HEALTHY", "apply": "hot",
                "hint": "达到此值判健康"
            },
            "probe_nickname_degraded": {
                "label": "探针·昵称覆盖率失败线",
                "type": "float", "default": 0.50, "min": 0.0, "max": 1.0,
                "env": "DY_PROBE_NICKNAME_DEGRADED", "apply": "hot",
                "hint": "低于此值判失败"
            },
            "probe_capture_stale_sec": {
                "label": "探针·捕获陈旧阈值（秒）",
                "type": "int", "default": 3600, "min": 60, "max": 604800,
                "env": "DY_PROBE_CAPTURE_STALE_SEC", "apply": "hot",
                "hint": "捕获超时未更新判失败"
            },
            # ---- 其余业务域探针（P1，2026-09-21 v0.44.27）----
            "probe_live_window_hours": {
                "label": "探针·直播弹幕观察窗（小时）",
                "type": "int", "default": 24, "min": 1, "max": 720,
                "env": "DY_PROBE_LIVE_WINDOW_HOURS", "apply": "hot",
                "hint": "窗口内无弹幕判未知"
            },
            "probe_live_healthy": {
                "label": "探针·弹幕真实率健康线",
                "type": "float", "default": 0.95, "min": 0.0, "max": 1.0,
                "env": "DY_PROBE_LIVE_HEALTHY", "apply": "hot",
            },
            "probe_live_degraded": {
                "label": "探针·弹幕真实率失败线",
                "type": "float", "default": 0.50, "min": 0.0, "max": 1.0,
                "env": "DY_PROBE_LIVE_DEGRADED", "apply": "hot",
            },
            "probe_ai_window_hours": {
                "label": "探针·AI 活动观察窗（小时）",
                "type": "int", "default": 72, "min": 1, "max": 2160,
                "env": "DY_PROBE_AI_WINDOW_HOURS", "apply": "hot",
                "hint": "AI 活动观察窗"
            },
            # ---- 定时巡检（P1 收尾，2026-09-21 v0.44.28）----
            # 探针只读本地事实（DB+本项目日志），零网络零浏览器，故可安全常驻。
            "probe_patrol_enabled": {
                "label": "探针·启用定时巡检",
                "type": "bool", "default": True,
                "env": "DY_PROBE_PATROL_ENABLED", "apply": "restart_backend",
                "hint": "关闭后只能手动巡检"
            },
            "probe_patrol_interval_min": {
                "label": "探针·巡检周期（分钟）",
                "type": "int", "default": 15, "min": 1, "max": 1440,
                "env": "DY_PROBE_PATROL_INTERVAL_MIN", "apply": "restart_backend",
                "hint": "多久自动巡检一次"
            },
            "probe_patrol_first_delay_sec": {
                "label": "探针·巡检首轮延迟（秒）",
                "type": "int", "default": 120, "min": 30, "max": 3600,
                "env": "DY_PROBE_PATROL_FIRST_DELAY_SEC", "apply": "restart_backend",
                "hint": "首轮巡检延迟"
            },
            "image_inline_max_kb": {
                "label": "图片内联阈值（KB）",
                "type": "int", "default": 32, "min": 0, "max": 10240,
                "env": "IMAGE_INLINE_MAX_KB", "apply": "hot",
                "hint": "≤此值内联"
            },
            "origin_image_ttl_days": {
                "label": "原图保留天数",
                "type": "int", "default": 30, "min": 1, "max": 365,
                "env": "ORIGIN_IMAGE_TTL_DAYS", "apply": "hot", "hint": "原图保留天数"
            },
            "origin_image_max_mb": {
                "label": "原图目录上限（MB）",
                "type": "int", "default": 2048, "min": 64, "max": 102400,
                "env": "ORIGIN_IMAGE_MAX_MB", "apply": "hot", "hint": "超过上限按时间清理"
            },
            "image_force_hosted": {
                "label": "强制上图床",
                "type": "bool", "default": False, "env": "IMAGE_FORCE_HOSTED",
                "apply": "hot", "hint": "关则本地托管"
            },
            "image_host_backend": {
                "label": "图床后端",
                "type": "select", "default": "tucdn", "env": "IMAGE_HOST_BACKEND",
                "options": [{"value": "tucdn", "label": "tucdn（国内·内置）"},
                            {"value": "imgbb", "label": "imgbb（境外·内置）"},
                            {"value": "custom", "label": "自定义（自建图床）"}],
                "apply": "hot",
                "hint": "自定义用下方地址"
            },
            # 2026-10-02 用户要求：图床是用户自己的服务，必须能填地址与密钥。
            # 此前只有后端二选一，端点（UPLOAD_URL/TUCDN_URL）硬编码在 image_host.py，
            # UI 无从填写 ⇒ 用户自建图床无法接入。
            "image_host_custom_url": {
                "label": "图床 API 地址",
                "type": "str", "default": "", "env": None,
                "apply": "hot",
                "hint": "自定义图床上传端点"
            },
            "image_host_custom_key": {
                "label": "图床 API Key",
                "type": "str", "default": "", "env": None,
                "apply": "hot",
                "hint": "留空则不鉴权"
            },
        },
    },

    # ===== 自动化频率控制（★ 本分支 design/better-douyin 新增）=====
    # 照源项目 better-douyin 的 `auto_*` 模型（实测提取自 douyin-dl 二进制，
    # 见 docs/reverse_interface_spec.md §三）：源项目**没有全局发送闸门**，
    # 风控靠「按动作限速 + 关键词准入 + 互动门槛 + 单轮上限」四件套。
    # 本分支按用户「全解除」授权，补上这套模型（与既有 send 闸门并存，可各自关闭）。
    "automation": {
        "label": "自动化频率控制",
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
            },
            "max_new_per_check": {
                "label": "单次检查最多处理新条目",
                "type": "int", "default": 10, "min": 1, "max": 200,
                "env": "DY_MONITOR_MAX_NEW_PER_CHECK",
                "apply": "hot",
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
                "hint": "新粉丝/评论/赞"
            },
            "monitor_friends": {
                "label": "监控·好友动态", "type": "bool", "default": False,
                "env": "DY_AUTO_MONITOR_FRIENDS", "apply": "hot",
            },
            "monitor_comments": {
                "label": "监控·评论", "type": "bool", "default": False,
                "env": "DY_AUTO_MONITOR_COMMENTS", "apply": "hot",
            },
            "monitor_feed": {
                "label": "监控·推荐流", "type": "bool", "default": False,
                "env": "DY_AUTO_MONITOR_FEED", "apply": "hot",
            },
            "follow_back_on_new_follower": {
                "label": "新粉丝自动回关", "type": "bool", "default": False,
                "env": "DY_AUTO_FOLLOW_BACK_ON_NEW_FOLLOWER", "apply": "hot", "risk": True,
            },
            "match_keywords": {
                "label": "通用·包含词", "type": "str", "default": "",
                "env": "DY_AUTO_MATCH_KEYWORDS", "apply": "hot",
                "hint": "逗号分隔，空=不过滤"
            },
            "exclude_keywords": {
                "label": "通用·排除词", "type": "str", "default": "",
                "env": "DY_AUTO_EXCLUDE_KEYWORDS", "apply": "hot",
                "hint": "逗号分隔，空=不过滤"
            },
            "private_match_keywords": {
                "label": "私信·包含词", "type": "str", "default": "",
                "env": "DY_AUTO_PRIVATE_MATCH_KEYWORDS", "apply": "hot",
                "hint": "空则回落通用词"
            },
            "private_exclude_keywords": {
                "label": "私信·排除词", "type": "str", "default": "",
                "env": "DY_AUTO_PRIVATE_EXCLUDE_KEYWORDS", "apply": "hot",
                "hint": "空则回落通用词"
            },
            "comment_match_keywords": {
                "label": "评论·包含词", "type": "str", "default": "",
                "env": "DY_AUTO_COMMENT_MATCH_KEYWORDS", "apply": "hot",
                "hint": "空则回落通用词"
            },
            "comment_exclude_keywords": {
                "label": "评论·排除词", "type": "str", "default": "",
                "env": "DY_AUTO_COMMENT_EXCLUDE_KEYWORDS", "apply": "hot",
                "hint": "空则回落通用词"
            },
            "like_match_keywords": {
                "label": "点赞·包含词", "type": "str", "default": "",
                "env": "DY_AUTO_LIKE_MATCH_KEYWORDS", "apply": "hot",
                "hint": "空则回落通用词"
            },
            "like_exclude_keywords": {
                "label": "点赞·排除词", "type": "str", "default": "",
                "env": "DY_AUTO_LIKE_EXCLUDE_KEYWORDS", "apply": "hot",
                "hint": "空则回落通用词"
            },
            "collect_match_keywords": {
                "label": "收藏·包含词", "type": "str", "default": "",
                "env": "DY_AUTO_COLLECT_MATCH_KEYWORDS", "apply": "hot",
                "hint": "空则回落通用词"
            },
            "collect_exclude_keywords": {
                "label": "收藏·排除词", "type": "str", "default": "",
                "env": "DY_AUTO_COLLECT_EXCLUDE_KEYWORDS", "apply": "hot",
                "hint": "空则回落通用词"
            },
            "min_digg_count": {
                "label": "门槛·最少点赞", "type": "int", "default": 0, "min": 0, "max": 100000000,
                "env": "DY_AUTO_MIN_DIGG_COUNT", "apply": "hot",
            },
            "min_comment_count": {
                "label": "门槛·最少评论", "type": "int", "default": 0, "min": 0, "max": 100000000,
                "env": "DY_AUTO_MIN_COMMENT_COUNT", "apply": "hot",
            },
            "min_play_count": {
                "label": "门槛·最少播放", "type": "int", "default": 0, "min": 0, "max": 1000000000,
                "env": "DY_AUTO_MIN_PLAY_COUNT", "apply": "hot",
            },
            "scan_interval_seconds": {
                "label": "扫描间隔（秒）", "type": "int", "default": 30, "min": 10, "max": 300,
                "env": "DY_AUTO_SCAN_INTERVAL_SECONDS", "apply": "hot", "risk": True,
            },
            "max_actions_per_run": {
                "label": "单轮最大动作数", "type": "int", "default": 5, "min": 1, "max": 50,
                "env": "DY_AUTO_MAX_ACTIONS_PER_RUN", "apply": "hot", "risk": True,
            },
            "send_delay_ms": {
                "label": "动作间隔（毫秒）", "type": "int", "default": 0, "min": 0, "max": 10000,
                "env": "DY_AUTO_SEND_DELAY_MS", "apply": "hot", "risk": True,
            },
            "return_shared_media": {
                "label": "回流·共享媒体", "type": "bool", "default": False,
                "env": "DY_AUTO_RETURN_SHARED_MEDIA", "apply": "hot",
            },
            "return_shared_allow_images": {
                "label": "回流·允许图片", "type": "bool", "default": True,
                "env": "DY_AUTO_RETURN_SHARED_ALLOW_IMAGES", "apply": "hot",
            },
            "return_shared_allow_videos": {
                "label": "回流·允许视频", "type": "bool", "default": True,
                "env": "DY_AUTO_RETURN_SHARED_ALLOW_VIDEOS", "apply": "hot",
            },
            "return_shared_max_size_mb": {
                "label": "回流·单文件上限(MB)", "type": "int", "default": 20, "min": 1, "max": 200,
                "env": "DY_AUTO_RETURN_SHARED_MAX_SIZE_MB", "apply": "hot",
            },
            "return_shared_max_media_count": {
                "label": "回流·最大媒体数", "type": "int", "default": 9, "min": 1, "max": 20,
                "env": "DY_AUTO_RETURN_SHARED_MAX_MEDIA_COUNT", "apply": "hot",
            },
            "auto_like": {
                "label": "动作·点赞", "type": "bool", "default": False,
                "env": "DY_AUTO_LIKE", "apply": "hot", "risk": True,
            },
            "auto_collect": {
                "label": "动作·收藏", "type": "bool", "default": False,
                "env": "DY_AUTO_COLLECT", "apply": "hot", "risk": True,
            },
            "auto_comment": {
                "label": "动作·评论", "type": "bool", "default": False,
                "env": "DY_AUTO_COMMENT", "apply": "hot", "risk": True,
            },
            "auto_private": {
                "label": "动作·私信", "type": "bool", "default": False,
                "env": "DY_AUTO_PRIVATE", "apply": "hot", "risk": True,
            },
            "auto_follow": {
                "label": "动作·关注", "type": "bool", "default": False,
                "env": "DY_AUTO_FOLLOW", "apply": "hot", "risk": True,
            },
            "require_context": {
                "label": "需要上下文才动作", "type": "bool", "default": True,
                "env": "DY_AUTO_REQUIRE_CONTEXT", "apply": "hot",
            },
            "use_global_gate": {
                "label": "同时使用全局发送闸门", "type": "bool", "default": True,
                "env": "DY_AUTO_USE_GLOBAL_GATE", "apply": "hot",
            },
        },
    },

    # ===== 内容采集 / 评论截流（★ 2026-09-30 新增，v0.45.125）=====
    # 采集页评论拉取的效率参数。设计依据：`/aweme/v1/web/comment/list/` 的
    # `count` 原被硬编码为 5 条/页（采集 100 条 = 20 次请求），实测服务端支持
    # 20~50 条/页 —— 见 `工作记忆/14_业务域_内容采集.md`。此处把「每页条数」与
    # 「多作品之间的间隔」显式化，遵循本项目「禁止代码自动探测本机状态」与
    # 「风控参数可观测、可调」的既有原则（对齐 automation 分区的做法）。
    "crawl": {
        "label": "内容采集 / 评论截流",
        "fields": {
            "comment_page_count": {
                "label": "评论每页条数", "type": "int", "default": 20,
                "min": 5, "max": 50,
                "env": "DY_CRAWL_COMMENT_PAGE_COUNT", "apply": "hot",
                "hint": "单页条数5~50"
            },
            "batch_interval": {
                "label": "多作品采集间隔（秒）", "type": "float", "default": 1.5,
                "min": 0.0, "max": 30.0,
                "env": "DY_CRAWL_BATCH_INTERVAL", "apply": "hot",
                "hint": "两作品之间的停顿"
            },
            "batch_max_works": {
                "label": "单次批量作品上限", "type": "int", "default": 20,
                "min": 1, "max": 100,
                "env": "DY_CRAWL_BATCH_MAX_WORKS", "apply": "hot",
                "hint": "单次最多采集几个作品"
            },
            "anon_preview": {
                "label": "匿名评论预览（探针）", "type": "bool", "default": True,
                "env": "DY_CRAWL_ANON_PREVIEW", "apply": "hot",
                "hint": "匿名预览评论"
            },
            "anon_preview_interval": {
                "label": "匿名预览间隔（秒）", "type": "float", "default": 0.6,
                "min": 0.0, "max": 10.0,
                "env": "DY_CRAWL_ANON_PREVIEW_INTERVAL", "apply": "hot",
                "hint": "仅串行模式生效的停顿"
            },
            "anon_preview_concurrency": {
                "label": "匿名预览并发度", "type": "int", "default": 6,
                "min": 1, "max": 12,
                "env": "DY_CRAWL_ANON_PREVIEW_CONCURRENCY", "apply": "hot",
                "hint": "并发数1=串行"
            },
            "anon_preview_max_works": {
                "label": "匿名预览作品上限", "type": "int", "default": 24,
                "min": 1, "max": 60,
                "env": "DY_CRAWL_ANON_PREVIEW_MAX_WORKS", "apply": "hot",
                "hint": "预览作品上限"
            },
            # ★ 2026-10-02：批量采集/私信的高价值关键词最低得分门槛
            "batch_min_score": {
                "label": "批量采集·高价值门槛", "type": "int", "default": 0,
                "min": 0, "max": 100,
                "env": "DY_CRAWL_BATCH_MIN_SCORE", "apply": "hot",
                "hint": "0=不过滤"
            },
        },
    },
}


SCHEMA_VERSION = 1
