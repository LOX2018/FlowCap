"""统一配置中心。

把散落在 7 个承载层（环境变量 / config.py / kv_store / auto_dm/config.py /
功能页）的配置收敛到这里，供设置页统一管理。

设计要点
--------
1. **单一落盘**：SQLite `kv_store["app_config"]`（单 key，按 section 分节）。
   与既有 `kv_store["config"]`（任务配置）并存，互不覆盖。
2. **schema 驱动**：`SECTIONS` 声明每个字段的 label / type / default / min / max /
   options / apply / hint / risk，前端据此渲染，无需为每字段写 UI。
3. **取值优先级**：配置中心值 → 环境变量 → schema 默认值。
4. **apply 三分类**（UI 必须逐字段标注，否则用户改了以为生效）：
   - `hot`            保存后下一次读取即生效
   - `restart_daemon` 需重启对应守护（recv_daemon / browser_daemon）
   - `restart_backend` 需重启 backend（启动参数类）

本模块**不主动改任何消费方**——消费方按 §B 阶段逐个接入（保留模块级兜底常量，
新增读配置函数）。这样接线失败时行为与接线前一致。
"""

from __future__ import annotations

import os
import threading
from typing import Any

import database

_KV_KEY = "app_config"
_ENV_PREFIX = "DY_"

_lock = threading.RLock()


# ---------------------------------------------------------------------------
# Schema 定义
# ---------------------------------------------------------------------------
# 字段元字段说明：
#   key     配置键（section 内唯一）
#   label   中文名
#   type    int | float | bool | str | select
#   default 默认值
#   min/max 数值范围（type=int/float 时生效）
#   options 下拉选项（type=select）
#   env     关联的环境变量名（None 表示不从环境读）
#   apply   hot | restart_daemon | restart_backend
#   hint    输入下方提示
#   risk    True 表示风控敏感（UI 加下限保护 + 风险提示）

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
                "label": "历史补全会话数",
                "type": "int", "default": 45, "min": 0, "max": 500,
                "env": "DY_HISTORY_MAX", "apply": "hot",
                "hint": "实测有消息会话稳定 44~47 个",
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
            "max_actions_per_run": {
                "label": "单轮最大动作数",
                "type": "int", "default": 20, "min": 1, "max": 500,
                "env": "DY_AUTO_MAX_ACTIONS_PER_RUN",
                "apply": "hot", "risk": True,
                "hint": "源项目核心限速参数：一轮扫描最多执行多少动作（回复/点赞/关注）",
            },
            "send_delay_ms": {
                "label": "动作间隔（毫秒）",
                "type": "int", "default": 1500, "min": 0, "max": 600000,
                "env": "DY_AUTO_SEND_DELAY_MS",
                "apply": "hot", "risk": True,
                "hint": "源项目 `auto_send_delay_ms`：每个动作之间静默等待",
            },
            "scan_interval_seconds": {
                "label": "扫描间隔（秒）",
                "type": "int", "default": 60, "min": 5, "max": 86400,
                "env": "DY_AUTO_SCAN_INTERVAL_SECONDS",
                "apply": "hot", "risk": True,
                "hint": "源项目 `auto_scan_interval_seconds`：两轮扫描之间的间隔",
            },
            "require_context": {
                "label": "需要上下文才动作",
                "type": "bool", "default": True,
                "env": "DY_AUTO_REQUIRE_CONTEXT",
                "apply": "hot",
                "hint": "源项目 `auto_require_context`：无上下文/历史不足时不自动动作",
            },
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
            "min_digg_count": {
                "label": "互动门槛 · 最少点赞数",
                "type": "int", "default": 0, "min": 0, "max": 1000000,
                "env": "DY_AUTO_MIN_DIGG_COUNT",
                "apply": "hot",
                "hint": "源项目 `auto_min_digg_count`：低于此值的目标不动作",
            },
            "min_comment_count": {
                "label": "互动门槛 · 最少评论数",
                "type": "int", "default": 0, "min": 0, "max": 1000000,
                "env": "DY_AUTO_MIN_COMMENT_COUNT",
                "apply": "hot",
                "hint": "源项目 `auto_min_comment_count`",
            },
            "min_play_count": {
                "label": "互动门槛 · 最少播放数",
                "type": "int", "default": 0, "min": 0, "max": 100000000,
                "env": "DY_AUTO_MIN_PLAY_COUNT",
                "apply": "hot",
                "hint": "源项目 `auto_min_play_count`",
            },
            "match_keywords": {
                "label": "关键词准入（逗号分隔，空=不限制）",
                "type": "str", "default": "",
                "env": "DY_AUTO_MATCH_KEYWORDS",
                "apply": "hot",
                "hint": "源项目 `auto_match_keywords`：命中任一才动作",
            },
            "exclude_keywords": {
                "label": "关键词排除（逗号分隔）",
                "type": "str", "default": "",
                "env": "DY_AUTO_EXCLUDE_KEYWORDS",
                "apply": "hot",
                "hint": "源项目 `auto_exclude_keywords`：命中任一则跳过",
            },
            "use_global_gate": {
                "label": "同时使用全局发送闸门",
                "type": "bool", "default": True,
                "env": "DY_AUTO_USE_GLOBAL_GATE",
                "apply": "hot", "risk": True,
                "hint": "源项目无闸门；本分支默认保留（双保险）。关闭即完全照源项目",
            },
        },
    },
}


# ---------------------------------------------------------------------------
# 读写
# ---------------------------------------------------------------------------

def _load(scope_key: str | None = None) -> dict:
    """读配置。scope_key 为 None 读全局；否则读该 scope（如标签）的配置。

    v0.38.2：标签只是「指引」，参数仍由本模块（原单位）按 scope 隔离存储，
    标签自身不持有任何副本 —— 避免两处存储不一致。
    """
    try:
        from database import get_kv_json
        data = get_kv_json(scope_key or _KV_KEY, {})
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(data: dict, scope_key: str | None = None) -> None:
    try:
        from database import set_kv_json
        set_kv_json(scope_key or _KV_KEY, data)
    except Exception:
        # 落盘失败不影响内存语义，消费方仍能读到本次值
        pass


def section_stored(section: str) -> dict:
    """读该分区**用户实际保存过**的原始值（不含默认值/环境变量）。

    v0.38.3：供一次性迁移判断「统一中心是否配置过该分区」。
    """
    return dict(_load().get(section) or {})


def scope_key(scope: str | None) -> str:
    """scope（如标签 id）→ kv key。None 返回全局 key。"""
    return f"{_KV_KEY}::{scope}" if scope else _KV_KEY


def drop_scope(scope: str) -> bool:
    """删除某 scope 的全部参数（删标签时清理，避免孤儿数据残留）。

    返回是否删除成功。**不吞异常** —— 曾因漏 import database 导致
    NameError 被 except 静默吞掉，表现为「删了标签但参数还在」。
    """
    try:
        conn = database.get_db()
        cur = conn.execute("DELETE FROM kv_store WHERE key=?", (scope_key(scope),))
        conn.commit()
        return cur.rowcount > 0
    except Exception as e:
        try:
            from loguru import logger
            logger.warning("CFG-010", f"[config] drop_scope 失败: {e}")
        except Exception:
            pass
        return False


def _field_meta(section: str, key: str) -> dict | None:
    sec = SECTIONS.get(section)
    if not sec:
        return None
    return (sec.get("fields") or {}).get(key)


def _from_env(meta: dict, ftype: str):
    name = meta.get("env")
    if not name:
        return None
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return None
    try:
        if ftype == "bool":
            return str(raw).strip() not in ("0", "false", "False", "no", "")
        if ftype == "int":
            return int(float(raw))
        if ftype == "float":
            return float(raw)
        return str(raw)
    except (TypeError, ValueError):
        return None


def _coerce(value: Any, ftype: str, meta: dict):
    """把输入值强转到字段类型，并做范围/选项校验。越界返回 None（调用方忽略）。"""
    try:
        if ftype == "bool":
            return bool(value) if not isinstance(value, str) else value.strip() not in (
                "0", "false", "False", "no", "")
        if ftype == "int":
            v = int(float(value))
        elif ftype == "float":
            v = float(value)
        else:
            v = str(value)
    except (TypeError, ValueError):
        return None

    if ftype in ("int", "float"):
        lo, hi = meta.get("min"), meta.get("max")
        if lo is not None and v < lo:
            return None
        if hi is not None and v > hi:
            return None
    if ftype == "select":
        opts = meta.get("options") or []
        if opts and v not in opts:
            return None
    return v


def get(section: str, key: str, default: Any = None,
        scope: str | None = None) -> Any:
    """取一个配置值。优先级：scope（标签）→ 全局 → 环境变量 → schema 默认。

    v0.38.2：scope 为标签 id 时先读该标签的值，未配则回落全局。
    标签只作「指引」，参数仍由本模块按 scope 隔离存储。
    """
    meta = _field_meta(section, key)
    if meta is None:
        return default
    ftype = meta.get("type", "str")

    # ① scope（标签）优先
    if scope:
        sv = (_load(scope_key(scope)).get(section) or {}).get(key)
        if sv is not None:
            cv = _coerce(sv, ftype, meta)
            if cv is not None:
                return cv
    # ② 全局
    stored = (_load().get(section) or {}).get(key)
    if stored is not None:
        v = _coerce(stored, ftype, meta)
        if v is not None:
            return v

    v = _from_env(meta, ftype)
    if v is not None:
        cv = _coerce(v, ftype, meta)
        if cv is not None:
            return cv

    dv = _coerce(meta.get("default"), ftype, meta)
    return dv if dv is not None else default


def get_section(section: str, scope: str | None = None) -> dict:
    """取整节配置（含默认值）。scope 为标签 id 时叠加标签值。"""
    sec = SECTIONS.get(section)
    if not sec:
        return {}
    return {k: get(section, k, scope=scope) for k in (sec.get("fields") or {})}


def get_all() -> dict:
    return {s: get_section(s) for s in SECTIONS}


def save_section(section: str, values: dict, scope: str | None = None) -> dict:
    """保存整节（只认 schema 内字段，越界/非法值直接丢弃）。

    返回最终生效值。scope 为标签 id 时写入该标签的隔离存储。
    """
    if section not in SECTIONS:
        return {}
    fields = SECTIONS[section].get("fields") or {}
    with _lock:
        data = _load(scope_key(scope))
        cur = dict(data.get(section) or {})
        for k, v in (values or {}).items():
            meta = fields.get(k)
            if meta is None:
                continue
            cv = _coerce(v, meta.get("type", "str"), meta)
            if cv is None:
                continue
            cur[k] = cv
        data[section] = cur
        _save(data, scope_key(scope))
    return get_section(section, scope=scope)


def reset_section(section: str, scope: str | None = None) -> dict:
    """清空某节回默认值。scope 为标签 id 时只清该标签的覆盖值。"""
    with _lock:
        data = _load(scope_key(scope))
        data.pop(section, None)
        _save(data, scope_key(scope))
    return get_section(section, scope=scope)


def schema() -> dict:
    """下发给前端的表单元数据（含当前默认值，不含已存值）。"""
    out = {}
    for sname, sec in SECTIONS.items():
        out[sname] = {
            "label": sec.get("label", sname),
            "fields": {
                k: {
                    "label": m.get("label", k),
                    "type": m.get("type", "str"),
                    "default": m.get("default"),
                    "min": m.get("min"),
                    "max": m.get("max"),
                    "options": m.get("options"),
                    "apply": m.get("apply", "hot"),
                    "hint": m.get("hint", ""),
                    "risk": bool(m.get("risk", False)),
                }
                for k, m in (sec.get("fields") or {}).items()
            },
        }
    return out


def apply_modes_of(section: str, keys: list[str]) -> list[str]:
    """给定改动字段，返回需要重启的目标列表（去重）。"""
    mods = set()
    for k in keys or []:
        m = _field_meta(section, k)
        if not m:
            continue
        a = m.get("apply", "hot")
        if a == "restart_daemon":
            mods.add("daemon")
        elif a == "restart_backend":
            mods.add("backend")
    return sorted(mods)
