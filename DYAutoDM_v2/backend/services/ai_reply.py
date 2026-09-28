# -*- coding: utf-8 -*-
"""
AI 获客留资自动回复服务（嵌入自 xyc667/douyin-auto-reply-assistant）。

来源与改造说明（2026-09-06 嵌入）：
- 原项目：https://github.com/xyc667/douyin-auto-reply-assistant
  （基于 Douyin_Spider 二开的 AI 自动回复桌面助手）
- 嵌入子集：KnowledgeBase（知识库模糊匹配）+ AIClient（Anthropic 兼容
  /v1/messages 协议）+ 随机延迟，三类核心能力整体移植。
- 获客改造（2026-09-06，用户明确目标）：
  agent 核心目标 = 留资（联系方式捕获入线索表），专业性由知识库定义。
  三档回复档位：kb_only（仅知识库）/ rag（AI 只准依据知识库答）/ free。
- 改造点（适配本仓库架构 + 铁律）：
  1. 存储全走全局 SQLite kv_store / ai_leads 表（database.py）。
  2. 回复只生成文本，发送 100% 复用 /api/messages/send 双通道链路
     （recv_daemon WS 主通道 + BCC WP 兜底），绝不复刻原项目直连协议层。
  3. 昵称/头像零主动查询 —— 纯读 dm_messages / dm_conversations。
  4. 图片理解用独立配置的视觉模型（主 LLM 未必多模态）：
     msg_type='27'（实库证真）图片消息 extra 含 skey/origin_url →
     origin_image_resolver.resolve 解密落盘 → base64 → 视觉模型(OpenAI
     兼容 /chat/completions) → 描述文本并入主回复流程。视觉未配置时
     图片走兜底话术，绝不瞎猜。
  5. AI 出口护栏：长度/违禁词/AI 腔校验，不通过改发兜底话术。
"""
from __future__ import annotations

import json
import random
import re
import threading
import time
import base64
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

import requests
from loguru import logger

import database


# ---------------------------------------------------------------------------
# 表结构：ai_leads（留资线索）。幂等建表，import 后由 ensure_tables 调用。
# ---------------------------------------------------------------------------

# === 测试专用探测针（2026-09-12 端到端实测注入）===
# 格式：PROBE|{trace_id}|{环节}|{结果}|{字段}
# 单独落文件，便于全流程追踪；不影响业务日志。
import os as _os, json as _json, time as _time

_PROBE_LOG = _os.environ.get(
    "AI_PROBE_LOG",
    _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "ai_probe.log"),
)


def _probe(trace_id, stage, result, **kw):
    try:
        rec = {"ts": _time.strftime("%H:%M:%S"), "trace": trace_id,
               "stage": stage, "result": result}
        rec.update(kw)
        with open(_PROBE_LOG, "a", encoding="utf-8") as f:
            f.write(_json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def ensure_tables() -> None:
    conn = database.get_db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS ai_leads (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account TEXT NOT NULL,
        conv_id TEXT NOT NULL,
        peer_name TEXT,
        contact_type TEXT NOT NULL,      -- phone / wechat
        contact_value TEXT NOT NULL,
        source_text TEXT DEFAULT '',
        status TEXT DEFAULT 'new',       -- new / followed / invalid
        created_at REAL NOT NULL,
        UNIQUE(account, conv_id, contact_type, contact_value)
    );
    CREATE INDEX IF NOT EXISTS idx_aileads_created
        ON ai_leads(created_at DESC);
    """)
    conn.commit()


# ---------------------------------------------------------------------------
# kv_store 读写小工具
# ---------------------------------------------------------------------------

def _kv_get(key: str, default):
    try:
        conn = database.get_db()
        row = conn.execute(
            "SELECT value FROM kv_store WHERE key=?", (key,)
        ).fetchone()
        if row and row[0]:
            return json.loads(row[0])
    except Exception as e:
        logger.warning(f"[AI-004] " + f"[ai] kv 读失败 {key}: {e}")
    return default


def _kv_set(key: str, value) -> None:
    conn = database.get_db()
    conn.execute(
        "INSERT INTO kv_store(key, value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, json.dumps(value, ensure_ascii=False)),
    )
    conn.commit()


# -- 图片描述缓存（ADR-008 决策 2）--------------------------------------

_IMG_DESC_MAX = 2000          # 缓存条数上限（超出按 at 淘汰最旧）

# 🔴 P1-① 修复（H-22 审计 · 多线程实测：4 线程×25 次写仅存 32 条）：
# `_img_desc_get/_known/_mark` 对**整个 dict** 做读-改-写；ADR-008 决策 3 开启
# `reply_concurrency` 线程池后，不同会话会并发调用 `_describe_image` ⇒
# 无锁的 last-write-wins 会**静默丢弃**彼此的缓存条目（实测丢失率 >60%）。
_IMG_DESC_LOCK = threading.Lock()


def _img_desc_get(msg_id) -> Optional[str]:
    """取图片视觉描述；无缓存或负缓存返回 None。"""
    with _IMG_DESC_LOCK:
        d = _kv_get(_KV_IMG_DESC, {})
        if not isinstance(d, dict):
            return None
        v = d.get(str(msg_id))
        if isinstance(v, dict):
            return str(v.get("desc") or "") or None
        return str(v) if v else None


def _img_desc_known(msg_id) -> bool:
    """该图是否已有缓存记录（含负缓存 ""）—— 负缓存不再重试。"""
    with _IMG_DESC_LOCK:
        d = _kv_get(_KV_IMG_DESC, {})
        return isinstance(d, dict) and str(msg_id) in d


def _img_desc_mark(msg_id, desc: str, model: str = "") -> None:
    """写描述缓存。desc 为空串 = 负缓存（数据级不可解，不再重试）。

    只记录**数据级**失败（缺 skey/origin_url、解密失败）；网络级失败
    （限流/超时）不写负缓存，留待下轮重试。
    """
    with _IMG_DESC_LOCK:
        d = _kv_get(_KV_IMG_DESC, {})
        if not isinstance(d, dict):
            d = {}
        d[str(msg_id)] = {"desc": str(desc or "")[:2000], "model": model,
                          "at": time.time()}
        if len(d) > _IMG_DESC_MAX:
            for k in sorted(d, key=lambda x: (d[x] or {}).get("at") or 0
                            )[: len(d) - _IMG_DESC_MAX]:
                d.pop(k, None)
        _kv_set(_KV_IMG_DESC, d)


# ---------------------------------------------------------------------------
# 配置（kv_store: ai_reply_config）
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG = {
    "enabled": False,            # 总开关（全自动监听）
    # ---- 主 LLM（文本）----
    "api_key": "",
    "api_protocol": "openai",    # openai(/chat/completions) | anthropic(/v1/messages)
    "base_url": "http://127.0.0.1:31415/v1",   # 默认本机 FreeLLM（OpenAI 兼容）
    "model": "glm-5.2",
    # ADR-013 / AI-061（2026-09-26 实测修正）：
    # 原 1000 对推理模型**必然不够** —— 实测 deepseek-v4.1-flash 在复杂
    # 推理题下 mt=1000/2000/4000 **全部 finish_reason=length 被截断**
    # （reasoning 先吃掉额度）。故默认提到 4000，并对识别出的推理模型
    # 再上浮（见 _eff_max_tokens）。
    "max_tokens": 4000,
    # 推理模型额外系数：识别为推理模型时 max_tokens *= 该值（1.0 = 不放大）
    "reasoner_max_tokens_factor": 1.5,
    "temperature": 0.7,
    # ---- 视觉模型（独立，OpenAI 兼容 /chat/completions）----
    "vision_enabled": False,
    "vision_base_url": "http://127.0.0.1:31415/v1",
    "vision_api_key": "",
    "vision_model": "nemotron-3-nano-omni-reasoning",
    "vision_prompt": (
        "客观描述这张图片的内容，重点提取与产品咨询/故障/需求相关的信息，"
        "30字以内，不要评论图片质量。"
    ),
    # ---- Agent 设定（获客留资）----
    "merchant_name": "",         # 商家名（注入 prompt）
    "strict_level": "rag",       # kb_only / rag / free
    "system_prompt": "",         # 获客 prompt 模板（空 = 用内置默认）
    "lead_confirm": "好的～稍后这边联系你哈",   # 留资成功后的确认话术
    "max_lead_ask": 2,           # 单会话最多主动索要联系方式次数
    # ---- Agent 分类与作用域（v0.38.6）----
    "kind": "dm",                # dm=私信 Agent | dispatch=调度 Agent（IM Bot）
    "scopes": ["dm", "live", "crawl"],  # 作用域：AI 智能回复注入哪些模块
    # ---- 知识库/兜底 ----
    "knowledge_first": True,
    # ---- 语义检索（三级漏斗第2级；本机 FreeLLM OpenAI 兼容 /embeddings）----
    "sem_enabled": False,
    "sem_base_url": "http://127.0.0.1:31415/v1",
    "sem_api_key": "",
    "sem_model": "nvidia/nemotron-3-embed-1b",   # 本机 FreeLLM 实测唯一可用 embedding 模型
    "sem_threshold": 0.40,                        # 实测：同义聚簇 0.44~0.59，跨意图 <0.21
    "min_delay": 8,
    "max_delay": 20,
    # 0 = 全文注入（ADR-008 决策 1，推荐默认）；>0 = 仅最近 N 条（旧行为兼容）
    "max_history": 0,
    # 上下文预算口径（D1：按模型窗口比例）。预算 = context_window * pct
    # ⚠️ ADR-013 / AI-062（2026-09-26）：models 元数据**不含窗口字段**
    # （实测仅 id/provider_id/model/caps/source）⇒ 系统无法自动获取真实窗口。
    # 按用户的「显式配置原则」：**0 = 未配置**（不拍脑袋定值）。
    # 未配置时 _context_budget 走保守下限 8192 + 告警（猜大 = 超窗被拒；猜小安全）。
    # 部署时请按模型真实窗口填入（如 agnes-2.5-flash / deepseek-v4.1-flash）。
    "context_window": 0,
    "context_budget_pct": 0.6,
    # 并发处理池大小（ADR-008 决策 3）：同会话严格串行、跨会话并行。
    # 8 = 可同时接待 8 个会话（端点实测 10 并发无压力）；>1 才启用并行。
    "reply_concurrency": 8,
    "fallback_pool": [
        "嗯嗯稍等哈，我问下马上回你",
        "这个我得确认一下哈，稍等",
        "收到，我问好了发你",
        "稍等哈，我看看",
    ],
    "fallback_image": "图我看到了哈，稍等我看看再回你",
    "forbidden_words": [
        "微信", "vx", "VX", "weixin",  # 站外引流敏感词（"加我"单字误杀率高，已用 prompt 铁律约束）
        "作为一个AI", "作为一个ai",
    ],
    "max_reply_len": 60,         # 超长截第一句
    # 2026-09-28（D4）：最短回复长度（残句防线）—— 实测推理超时降级后
    # deepseek-v4.1-flash 只回「能认」两字且 finish_reason≠length，
    # 截断检测抓不到 ⇒ 2 字残句直发客户。低于此值判残句走兜底。
    "min_reply_len": 5,
}

_KV_CONFIG = "ai_reply_config"
_KV_KB = "ai_reply_knowledge_base"
_KV_BL = "ai_reply_blacklist"
_KV_MARKER = "ai_reply_last_msg_id"
_KV_ASK_COUNT = "ai_reply_lead_ask"      # {account:conv_id: count}
# ADR-008 决策 2：图片视觉描述缓存 {msg_id: {desc, model, at}}。
# 存独立 KV 而非 dm_messages 新列 —— 不改原始取证数据（用户 D3 裁决）。
_KV_IMG_DESC = "ai_img_desc"

# ---------------------------------------------------------------------------
# 上下文消息类型白名单（P1-1，2026-09-23）
# ---------------------------------------------------------------------------
# 权威映射来源：backend/api/messages.py 的 _front_type（约 156-166 行，**只读参考**）：
#     "7"     -> text      （抖音 WS 实时路径落库的文本消息）
#     "5"     -> sticker
#     "17"    -> voice
#     "27"    -> image
#     "8"     -> video
#     "50001" -> read_receipt
#   另（同函数 fallback）：msg_type 为 None/空 -> "text"；未知值原样透传。
#
# 2026-09-25（H-25 脏数据治理）：噪音前缀 —— 写入侧占位/探针标记，非真实
# 会话内容，**不得进入 AI 上下文**。实测该账号白名单内混入 58 条：
#   [投递验证]48（探针证据，正确形态是 msg_type='delivery_marker'）
#   [系统提示]7 / [系统消息]5（抖音系统通知，由 capture 按 biz 映射生成）
#   [未知类型N]/[未知媒体]（解析噪音）
# 注：`[分享视频] 视频ID x`（带 ID）是**真实分享**，不排除；空 `[分享视频]`
# 由写入侧 _is_noise_text 拦截。与 api/messages.py 读侧口径保持一致。
#   ⇒ 旧 SQL 硬筛 msg_type='text'，与 WS 实时路径的 '7' 永不相等
#     ⇒ history 恒为空 ⇒ AI 每次只见当前一句。
#   ⇒ 本白名单**只收已确认文本语义**的 'text' 与 '7'；'1'/'50010' 语义未经
#     确认，一律排除。白名单优于排除式：宁可少收，不可收进语义不明的数据。
# 2026-09-25（ADR-008 决策 2）：纳入 '27'（图片）。SQL 层先收进来，
# 再由 _build_history 按「是否拿到视觉描述」二次筛选 —— 有描述才注入为
# [客户发来图片] <描述>；拿不到描述则等同旧行为（不注入，零回归）。
_HISTORY_TEXT_TYPES: tuple = ("text", "7", "27")

# 噪音前缀排除（H-25）：写入侧占位/探针标记，非真实会话内容，不得进入 AI 上下文。
_HISTORY_NOISE_SQL = (
    " AND text NOT LIKE '[投递验证]%'"
    " AND text NOT LIKE '[系统提示]%'"
    " AND text NOT LIKE '[系统消息]%'"
    " AND text NOT LIKE '[未知类型%'"
    " AND text NOT LIKE '[未知媒体]%'"
)


def _sanitize_history_text(text: str) -> str:
    """收敛图片文本为纯语义标签（2026-09-25 H-25 统一落库契约）。

    契约（写侧已收口）：`text` **只**承载语义标签，缩略图字节走
    `extra['thumb']`、原图要素走 `extra.skey/origin_url`，读侧由后端
    `/conversation` 派生下发 `image_url` / `thumb_url`。

    本函数现为**契约级兜底**（存量数据专用）：历史行里残留的
    `[图片] data:image/...`（25 条 / 10.3 万字符 / 单条最长 6577）与
    `[图片] https://...` 一律折叠为 `[图片]` —— 保留「对方发过一张图」
    的语义，绝不把 base64 或加密 URL 送进模型。

    ❗ 这不再是「读侧补丁掩盖写侧缺陷」：写侧已按契约收口（见
    conversation_capture._thumb_semantic_label / _extract_thumb_data_uri），
    本函数只负责存量行的兼容收敛。
    """
    if not text:
        return text
    t = text.strip()
    if t.startswith("[图片]"):
        # 有语义标签即折叠为纯标签（无论其后跟 base64 / URL / 无内容）
        return "[图片]"
    if "data:image" in t:
        head = t.split("data:image", 1)[0].strip()
        return head or "[图片]"
    return text



def get_config() -> dict:
    cfg = dict(_DEFAULT_CONFIG)
    saved = _kv_get(_KV_CONFIG, {})
    if isinstance(saved, dict):
        cfg.update(saved)
    return cfg


def apply_model_hub(cfg: dict) -> dict:
    """把模型链路中心的绑定**叠加**进配置（v0.38.4，返回副本不改原 dict）。

    AI 的三个消费方（主模型/视觉/语义）改为绑 model_hub 的链路；
    hub 解析结果覆盖 cfg 里对应三组键 —— 下游 AIClient / describe_image /
    _embedRemote 等消费代码零改动。hub 不可用或未解析到时保持原值
    （即 ai_reply_config 里存的全局值，兜底零回归）。
    """
    try:
        from services import model_hub as hub

        out = dict(cfg)
        mapping = {
            "ai_main": ("base_url", "model", "api_key", "api_protocol"),
            "ai_vision": ("vision_base_url", "vision_model",
                          "vision_api_key", None),
            "ai_sem": ("sem_base_url", "sem_model", "sem_api_key", None),
        }
        for cid, (k_base, k_model, k_key, k_proto) in mapping.items():
            r = hub.resolve(cid)
            if not r:
                continue
            out[k_base] = r["base_url"]
            out[k_model] = r["model"]
            out[k_key] = r["api_key"]
            if k_proto:
                out[k_proto] = r["api_protocol"]
        return out
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[AI-030] " + f"[ai] model_hub 叠加失败（用原配置）: {e}")
        return cfg


# ---------------------------------------------------------------------------
# 避障链路引擎（v0.39.0）：hub 链候选按序尝试，全部失败返回 None。
# ---------------------------------------------------------------------------

def resolve_chain_hub(consumer_id: str) -> Optional[dict]:
    """取消费方的 hub 避障链（未配置/异常返回 None，调用方回落旧单配置）。"""
    try:
        from services import model_hub as hub

        return hub.resolve_chain(consumer_id)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[AI-030] " + f"[ai] model_hub 链路解析失败: {e}")
        return None


def _cand_cfg(c: dict) -> dict:
    """hub 候选 → AIClient 认的四键 cfg。"""
    return {"base_url": c["base_url"], "api_key": c["api_key"],
            "api_protocol": c["api_protocol"], "model": c["model"]}



def save_config(cfg: dict) -> dict:
    merged = get_config()
    for k in _DEFAULT_CONFIG:
        if k in cfg:
            merged[k] = cfg[k]
    _kv_set(_KV_CONFIG, merged)
    return merged


# ---------------------------------------------------------------------------
# 内置获客 prompt（用户可覆盖：配置里 system_prompt 非空则优先）
# ---------------------------------------------------------------------------

_DEFAULT_AGENT_PROMPT = """你是「{merchant}」的抖音私信客服。唯一目标：解答客户问题的同时，让有意向的客户留下手机号。

工作方式（分阶段推进）：
1. 解答阶段：只依据《资料》回答，专业、简短、口语化，先建立信任。
2. 意向阶段：客户表现出兴趣（问价格/效果/怎么办理）→ 自然推进留资。
3. 留资阶段：用自然话术索要手机号，例如："这边给你申请个专属优惠，你手机号多少？我备注一下"。已被拒绝 {max_ask} 次就不再索要，安心解答。
4. 留资成功（客户已发号码）：只回确认话术，不再多说。

铁律：
- 《资料》里没有的信息一律不许编；不确定就回"嗯嗯这个我问下稍等哈"。
- 绝不说"加微信"、绝不提其他平台；只索要手机号。
- 回复 5-30 字，口语化像真人，一次只回一句，不排队比句式。
- 不出现价格数字承诺、不承诺时间效果。"""

# AI-058（2026-09-25）：移除「客户消息：{text}」。客户消息已作为 user 消息
# 由 AIClient.chat 追加（权威位置），在 system 里再重复一次是冗余，且会让
# 模型误以为 system 段内也有一条「客户消息」而重复回应。
_RAG_SUFFIX = """

《资料》（你唯一的事实来源，全部来自知识库）：
{kb}"""


_LAST_PROMPT_STATS = {"kb_mode": "none", "pro_kb_chars": 0}


def build_system_prompt(cfg: dict, text: str) -> str:
    """组装 system 提示词。

    AI-059（2026-09-25）：原第三参数 kb_items 是死参数（函数体内从未读取，
    专业知识库走 pro_kb 语义检索）。已移除，消除"传了就会生效"的误导。
    """
    global _LAST_PROMPT_STATS
    _LAST_PROMPT_STATS = {"kb_mode": "none", "pro_kb_chars": 0}
    merchant = cfg.get("merchant_name") or "本店"
    base = (cfg.get("system_prompt") or "").strip() or _DEFAULT_AGENT_PROMPT
    base = (base.replace("{merchant}", merchant)
                .replace("{max_ask}", str(cfg.get("max_lead_ask", 2))))
    if cfg.get("strict_level") == "rag":
        # v0.39.1：专业库改为思维导图结构（主题→子分类→正文→总结），
        # RAG 注入条目全文（相关性由漏斗/向量负责，不本地切分）。
        # 两库职责明确：专业库=RAG 参考；命中库=直接回复。不互相回落。
        # v0.40：注入前跑质量门槛（低质量且从未命中 → 不进提示词），
        #        按时间衰减排序（越久没用到的越靠后），并给注入条目记 hits。
        try:
            from services import pro_kb

            pro = pro_kb.list_items()
            pro = [it for it in pro if it.get("enabled", True)]
            # 质量门槛：importance<0.3 且 hits==0 的低质条目不进提示词
            pro = [it for it in pro if pro_kb.quality_ok(it)]
        except Exception:
            pro = []
        if pro:
            # 2026-09-12：专业库改为**语义筛选**（用户拍板：不能全库注入）。
            # 原因：旧实现 list_items()[:40] 全文注入可达 3 万字符，
            # 撑爆模型上下文 → AI 返回空 → 全部降级兜底话术。
            # 新流程：客户问题 → 语义 TopK(5) → 仅注入最相关条目（≤6000 字）。
            # embedding 不可用 → 降级为「时间衰减排序取前 5」，绝不阻塞。
            hits = None
            # AI-054（2026-09-25）：尊重语义总开关。此前 pro_kb RAG 无条件走
            # 语义检索 —— sem_enabled=False 时仍出网，与 reply_kb 语义级（受该
            # 开关控制）语义不一致，同一开关两套行为属契约漂移。
            if cfg.get("sem_enabled"):
                try:
                    # AI-056：threshold=0.0 等于不筛相关性。实测同一专业库：
                    # 0.0 -> 注入 3 条（含「无关主题/天气」）；0.30 -> 2 条；
                    # 0.40 -> 1 条（无关条目被滤除）；0.60 -> 0 条。
                    # 取 0.40（与 sem_threshold 既有默认一致）。全部被滤除时
                    # hits 为空 -> 落 fallback_decay，兜底不丢。
                    hits = pro_kb.semantic_topk(text, pro, k=5, threshold=0.40,
                                                max_chars=6000)
                except Exception:
                    hits = None
            if hits:
                picked = hits
                kb_mode = "semantic"
            else:
                now = time.time()
                picked = sorted(
                    pro, key=lambda it: pro_kb.time_decay_factor(it, now=now),
                    reverse=True)[:5]
                kb_mode = "fallback_decay"
            lines = []
            for it in picked:
                head = it.get("topic", "")
                if it.get("category"):
                    head += f" / {it['category']}"
                summ = it.get("summary") or ""
                lines.append(
                    f"【{head}】{summ}\n{it.get('content', '')}".strip())
            kb_text = "\n\n".join(lines)
            _LAST_PROMPT_STATS = {"kb_mode": kb_mode, "pro_kb_chars": len(kb_text),
                                  "picked": len(picked)}
            # 记命中：喂 hits/last_accessed_at（下次排序与陈旧判定用）
            try:
                for it in picked:
                    pro_kb.touch(it.get("id"))
            except Exception:
                pass
        else:
            kb_text = "（知识库暂无条目）"
        base += _RAG_SUFFIX.format(kb=kb_text, text=text)
    return base


# ---------------------------------------------------------------------------
# KnowledgeBase —— 移植自原项目（存储改 kv_store）
# ---------------------------------------------------------------------------

class KnowledgeBase:
    """知识库：问答对，关键词模糊匹配，命中直接回复不耗 AI 额度。"""

    def __init__(self):
        self.lock = threading.Lock()

    def _load(self) -> list:
        return _kv_get(_KV_KB, [])

    def _save(self, items: list) -> None:
        _kv_set(_KV_KB, items)

    def list_items(self) -> list:
        with self.lock:
            return self._load()

    def add(self, question: str, answer: str) -> dict:
        q, a = (question or "").strip(), (answer or "").strip()
        if not q or not a:
            raise ValueError("问题和答案都不能为空")
        with self.lock:
            items = self._load()
            # id：毫秒时间戳 + 序号防同毫秒冲突（曾因同毫秒 id 相同导致两条互相覆盖）
            existing_ids = {it.get("id") for it in items}
            new_id = int(time.time() * 1000)
            while new_id in existing_ids:
                new_id += 1
            item = {"id": new_id, "question": q, "answer": a}
            items.append(item)
            self._save(items)
            return item

    def update(self, item_id: int, question: str, answer: str) -> None:
        q, a = (question or "").strip(), (answer or "").strip()
        if not q or not a:
            raise ValueError("问题和答案都不能为空")
        with self.lock:
            items = self._load()
            for it in items:
                if it.get("id") == item_id:
                    it["question"], it["answer"] = q, a
                    break
            self._save(items)

    def delete(self, item_id: int) -> None:
        with self.lock:
            items = self._load()
            self._save([it for it in items if it.get("id") != item_id])

    def find_match(self, question: str, threshold: float = 0.7,
                   account: str = "") -> Optional[str]:
        """三级漏斗：①精确/包含 ②语义向量（可配） ③Jaccard 兜底。

        v0.38.0：新增 account 参数 —— 绑定了 Agent 时用 Agent 的知识库
        （用户决策：知识库跟随 Agent）。account 为空则走全局知识库（零回归）。
        """
        qtext = (question or "").lower().strip()
        if not qtext:
            return None
        cfg = get_config()

        items = self._load()
        # v0.38.0：Agent 级知识库隔离
        if account:
            try:
                from services import ai_agent

                items = ai_agent.resolve_knowledge(account, items)
                cfg = ai_agent.resolve_config(account, cfg)
            except Exception:
                pass

        for item in items:
            q = (item.get("question") or "").lower().strip()
            if not q:
                continue
            # ① 精确/包含（零成本，最高置信）
            if q in qtext or qtext in q:
                return item.get("answer", "")
        # ② 语义向量匹配（sem_enabled 且配置齐全时；失败自动落 ③）
        sem = find_match_semantic(question, cfg, items=items)
        if sem:
            return sem
        # ③ Jaccard 兜底（embedding 不可用/未启用时唯一防线）
        for item in items:
            q = (item.get("question") or "").lower().strip()
            if not q:
                continue
            qw, uw = set(q), set(qtext)
            union = qw | uw
            overlap = (len(qw & uw) / len(union)) if union else 0
            if overlap >= threshold:
                return item.get("answer", "")
        return None


KB = KnowledgeBase()


# ---------------------------------------------------------------------------
# 语义检索（三级漏斗第 2 级）：OpenAI 兼容 /embeddings + 余弦相似度暴力遍历。
# 知识库条目向量缓存在 kv_store（key 前缀 ai_reply_semv_），导入/编辑时预计算。
# ---------------------------------------------------------------------------

def _embedRemote(base_url: str, api_key: str, model: str,
                 texts: list[str], timeout: float = 20.0) -> Optional[list[list[float]]]:
    """调 OpenAI 兼容 /v1/embeddings，批量向量化。失败返回 None（调用方降级）。"""
    if not base_url or not texts:
        return None
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        resp = requests.post(
            f"{str(base_url).rstrip('/')}/embeddings",
            headers=headers,
            json={"model": model, "input": texts},
            timeout=timeout,
        )
        if resp.status_code != 200:
            logger.warning(f"[AI-005] " + f"[ai] embeddings {resp.status_code}: {resp.text[:120]}")
            return None
        d = resp.json()
        data = d.get("data") or []
        if len(data) != len(texts):
            logger.warning(f"[AI-006] " + f"[ai] embeddings 返回数不符: {len(data)}/{len(texts)}")
            return None
        # 按 index 排序保证顺序
        data.sort(key=lambda x: x.get("index", 0))
        return [item["embedding"] for item in data]
    except Exception as e:
        logger.warning(f"[AI-007] " + f"[ai] embeddings 调用失败: {e}")
        return None


def _embed_failover(texts: list[str], consumer_id: str = "ai_sem",
                    timeout: float = 20.0) -> tuple:
    """语义避障链：sem 链候选按序尝试（sem 链无兜底）。

    返回 (vecs, model_name)；链未配置回落旧单配置路径。
    返回实际生效的模型名，供缓存一致性校验（不同 embedding 模型的向量
    空间不可比，混用会让余弦相似度失真）。
    """
    chain = resolve_chain_hub(consumer_id)
    if not chain or not chain.get("candidates"):
        cfg = get_config()
        m = cfg.get("sem_model", "")
        v = _embedRemote(cfg.get("sem_base_url", ""),
                         cfg.get("sem_api_key", ""), m, texts, timeout)
        return (v, m) if v is not None else (None, None)
    for c in chain["candidates"]:
        v = _embedRemote(c["base_url"], c["api_key"], c["model"],
                         texts, timeout)
        if v is not None:
            return v, c["model"]
        logger.warning(f"[AI-033] " + f"[ai] 语义候选失败，切下一个: {c['model']}")
    return None, None

def _cosine(a: list[float], b: list[float]) -> float:
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0 or nb <= 0:
        return 0.0
    return dot / (na ** 0.5 * nb ** 0.5)


_KV_SEM_PREFIX = "ai_reply_semv_"       # + item_id -> 向量 JSON
_KV_SEM_MDL = "ai_reply_sem_model"      # 生成缓存时用的模型（模型换了缓存作废）


def kb_rebuild_semantic_cache(cfg: Optional[dict] = None) -> dict:
    """为全部知识库条目预计算向量并写 kv 缓存。返回统计。

    模型或内容变化时调用；单条失败跳过（该条退回 Jaccard 兜底）。
    """
    cfg = cfg or get_config()
    items = KB.list_items()
    if not items:
        return {"ok": True, "embedded": 0, "total": 0}
    # 只向量化的 question：实测混合 answer 会稀释语义（0.358→纯问句 0.582，
    # 同义改写「你们这个什么价格呀」因此掉到阈值之下漏召回）
    texts = [it.get("question", "") for it in items]
    vecs, used_model = _embed_failover(texts)
    if vecs is None:
        return {"ok": False, "error": "embedding 调用失败（检查语义链路配置）"}
    n = 0
    for it, v in zip(items, vecs):
        _kv_set(_KV_SEM_PREFIX + str(it["id"]), v)
        n += 1
    _kv_set(_KV_SEM_MDL, used_model or "")
    return {"ok": True, "embedded": n, "total": len(items)}


def find_match_semantic(question: str, cfg: dict,
                        threshold: Optional[float] = None,
                        items: Optional[list] = None) -> Optional[str]:
    """语义检索：问题向量化 → 与缓存向量逐条余弦 → 最高分≥阈值即命中。

    缓存缺失的条目自动跳过（不现场补算，避免消息路径阻塞）。
    embedding 调用失败返回 None（调用方走 Jaccard 兜底）。

    v0.38.0：items 可显式传入（Agent 级知识库），默认 None 走全局 KB。
    """
    if not cfg.get("sem_enabled") or not cfg.get("sem_base_url"):
        return None
    qvec, used_model = _embed_failover([question])
    if qvec is None:
        return None
    # 向量空间一致性：缓存是别的 embedding 模型生成的 → 余弦不可比，
    # 直接跳过语义级（落 Jaccard 兜底），防跨模型混算出假命中。
    try:
        cached_mdl = _kv_get(_KV_SEM_MDL, "")
        if cached_mdl and used_model and cached_mdl != used_model:
            logger.warning(f"[AI-034] " + f"[ai] 语义缓存模型不一致（缓存={cached_mdl} "
                           f"现用={used_model}），本次跳过语义级")
            return None
    except Exception:
        pass
    if qvec is None:
        return None
    qv = qvec[0]
    th = float(threshold if threshold is not None else cfg.get("sem_threshold", 0.40))
    best_score = 0.0
    best_answer: Optional[str] = None
    for it in (items if items is not None else KB.list_items()):
        cached = _kv_get(_KV_SEM_PREFIX + str(it["id"]), None)
        if not cached:
            continue
        score = _cosine(qv, cached)
        if score > best_score:
            best_score = score
            best_answer = it.get("answer", "")
    if best_answer and best_score >= th:
        logger.info(f"[ai] 语义命中 score={best_score:.3f} (阈值{th})")
        return best_answer
    logger.debug(f"[ai] 语义未过阈值 best={best_score:.3f} th={th}")
    return None


# ---------------------------------------------------------------------------
# 思考过程泄漏检测：推理模型 max_tokens 不足时 content 会是分析文本
#（实测 glm-5.2 样例：'1. **分析请求：**\n * 角色：客服…'）。特征：
# 数字编号列表开头 + 分析/思考类词汇。命中且 finish=length 则丢弃。
# ---------------------------------------------------------------------------

_REASONING_PATTERN = re.compile(
    r"^\s*(?:\d+[\.\)、]\s*)?[\*\#]*\s*"
    r"(?:分析|思考|第一步|步骤|让我|首先|The user|Let me|I need to|Sure[,.]|Okay[,.]|Based on|Alright)",
    re.IGNORECASE)


def _looks_like_reasoning(text: str) -> bool:
    return bool(_REASONING_PATTERN.match(text or ""))


# ---------------------------------------------------------------------------
# 专业准确性护栏（2026-09-28，D4）：绝对化断言与专业知识相悖 —— 禁止外发。
# 实测事故：AI 对「陈旧性骨折还能认工伤吗」回「能认」（与专业知识完全相悖）。
# 这里只拦「把话说死」的形态，正常分析（含「最终以认定结论为准」）不命中。
# ---------------------------------------------------------------------------

_RE_ASSERTIVE = [
    # ① 「陈旧性骨折/旧伤」+ **直接肯定认定**（同一句内）。
    #    必须：肯定词前无「不/否」（排除「不能认/能否认」），
    #    且尾部无「吗/呢/？」（排除疑问句「旧伤能不能认」—— 实测误伤原文）。
    re.compile(r"(陈旧性骨折|陈旧骨折|旧伤)[^。！？!?\n]{0,10}?"
               r"(?<![不否])(?:能认定|可以认定|能认|可以认|算工伤"
               r"|能评上|可以评上|能评|可以评|能算)"
               r"(?!吗|呢|？)"),
    # ② 等级断言（能/可以 评上N级）——同样排除否定与疑问形态。
    re.compile(r"(?<![不否])(?:能评上|可以评上|能定|可以定|能评|可以评)"
               r"[^。！？!?\n]{0,3}[一二三四五六七八九十0-9]+\s*级(?!吗|呢|？)"),
    # ③ 「肯定是/就是几级」等确定性断言
    re.compile(r"(?:肯定|必然|绝对|一定|确定|确认|准是)[^。！？!?\n]{0,3}"
               r"(?:是|能|可以|就)[^。！？!?\n]{0,3}[一二三四五六七八九十0-9]+\s*级"),
]


def _est_tokens(text: str) -> int:
    """粗估 token 数（无分词器时的保守估算，供上下文预算使用）。

    CJK 按 1 字 ~= 1 token；其余按 4 字符 ~= 1 token；空串为 0。
    """
    s = text or ""
    if not s:
        return 0
    cjk = 0
    for ch in s:
        if "\u4e00" <= ch <= "\u9fff":   # 基本汉字区
            cjk += 1
    return cjk + (len(s) - cjk) // 4 + 1


def _context_budget(cfg: dict) -> int:
    """历史上下文 token 预算（ADR-008 决策 1 / D1：按模型窗口比例）。

    ⚠️ ADR-013（2026-09-26）：`context_window` **必须显式配置**。
    系统无法从 provider 取得真实窗口（models 元数据仅
    id/provider_id/model/caps/source，无窗口字段），此前我用 65536 兜底
    属「拍脑袋定值」，违反用户的「显式配置原则」。
    故：未配置（0/空）→ 走**保守下限** 8192 并**告警一次**（不静默猜大值，
    猜大 = 超窗被 provider 拒；猜小 = 少喂上下文，安全方向）。
    """
    raw = cfg.get("context_window")
    win = 0
    try:
        win = int(raw or 0)
    except (TypeError, ValueError):
        win = 0
    if win <= 0:
        logger.warning(
            "[AI-062] context_window 未配置（=0）→ 按保守下限 8192 计算上下文预算。"
            "请在 AI 配置里填入模型真实窗口（系统无法从 provider 自动获取，"
            "models 元数据不含窗口字段），否则会少喂历史上下文。")
        win = 8192
    try:
        pct = float(cfg.get("context_budget_pct") or 0.6)
    except (TypeError, ValueError):
        pct = 0.6
    if not (0.1 <= pct <= 0.9):
        pct = 0.6
    return max(1000, int(win * pct))


def _fit_history(history: list, budget: int) -> list:
    """按 token 预算裁剪历史：从最新往旧累加，**保证单条消息完整**（D2）。

    返回仍为时间升序。最新一条即使超预算也保留 —— 宁可真喂最大信息，
    也不把当前对话喂成空。budget<=0 视为不限。
    """
    if not history:
        return []
    if budget <= 0:
        return list(history)
    kept: list = []          # 逆序累加（新 -> 旧）
    used = 0
    for msg in reversed(history):
        t = _est_tokens(str(msg.get("content") or ""))
        if kept and used + t > budget:
            break
        kept.append(msg)
        used += t
    kept.reverse()
    return kept


# ---------------------------------------------------------------------------
# 模型提供商预设（前端下拉框数据源；用户自主选择，不默认绑定任何一家）
# provider_type: openai | anthropic —— 端点协议
# ---------------------------------------------------------------------------

PROVIDER_PRESETS = [
    {"id": "freellm", "name": "FreeLLM（本机聚合，优先推荐）",
     "base_url": "http://127.0.0.1:31415/v1", "api_protocol": "openai",
     "needs_key": False, "key_hint": "本机部署可留空",
     "models_chat": ["glm-5.2", "deepseek-v3.2", "kimi-k2.6", "minimax-m3",
                     "qwen3-235b-a22b", "nemotron-3-ultra"],
     "models_vision": ["nemotron-3-nano-omni-reasoning", "glm-4.6v-flash"]},
    {"id": "deepseek", "name": "DeepSeek 深度求索",
     "base_url": "https://api.deepseek.com/v1", "api_protocol": "openai",
     "needs_key": True, "key_hint": "sk-…（platform.deepseek.com）",
     "models_chat": ["deepseek-chat", "deepseek-reasoner"], "models_vision": []},
    {"id": "zhipu", "name": "智谱 GLM",
     "base_url": "https://open.bigmodel.cn/api/paas/v4", "api_protocol": "openai",
     "needs_key": True, "key_hint": "…（open.bigmodel.cn）",
     "models_chat": ["glm-4-plus", "glm-4-flash", "glm-4.5"],
     "models_vision": ["glm-4v-plus", "glm-4v-flash"]},
    {"id": "dashscope", "name": "阿里云百炼（通义千问）",
     "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
     "api_protocol": "openai", "needs_key": True, "key_hint": "sk-…（百炼控制台）",
     "models_chat": ["qwen-plus", "qwen-max", "qwen-turbo", "qwen3-235b-a22b"],
     "models_vision": ["qwen-vl-plus", "qwen-vl-max"]},
    {"id": "moonshot", "name": "月之暗面 Kimi",
     "base_url": "https://api.moonshot.cn/v1", "api_protocol": "openai",
     "needs_key": True, "key_hint": "sk-…（platform.moonshot.cn）",
     "models_chat": ["kimi-k2-0905-preview", "moonshot-v1-32k"], "models_vision": []},
    {"id": "volces", "name": "火山方舟（豆包）",
     "base_url": "https://ark.cn-beijing.volces.com/api/v3", "api_protocol": "openai",
     "needs_key": True, "key_hint": "…（方舟控制台，模型用接入点 ID）",
     "models_chat": ["doubao-1-5-pro-32k-250115"],
     "models_vision": ["doubao-1-5-vision-pro-32k-250115"]},
    {"id": "minimax", "name": "MiniMax（Anthropic 兼容端点）",
     "base_url": "https://api.minimaxi.com/anthropic", "api_protocol": "anthropic",
     "needs_key": True, "key_hint": "eyJ…（MiniMax 开放平台）",
     "models_chat": ["MiniMax-M2.7", "MiniMax-M3"], "models_vision": []},
    {"id": "custom", "name": "自定义（手填 Base URL + 模型名）",
     "base_url": "", "api_protocol": "openai", "needs_key": True,
     "key_hint": "按服务商要求", "models_chat": [], "models_vision": []},
]


def get_providers() -> list:
    """前端下拉框数据：预设 + 可选的 FreeLLM 在线模型列表合并标记。"""
    return [dict(p) for p in PROVIDER_PRESETS]


# ---------------------------------------------------------------------------
# AIClient —— 主 LLM（OpenAI 兼容 + Anthropic 兼容双协议）
# ---------------------------------------------------------------------------

class AIClient:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.session_history: dict[str, list] = {}
        self.session_lock = threading.Lock()

    def chat(self, message: str, user_id: str = "default",
             system_prompt: str = "", history_extra: Optional[list] = None
             ) -> Optional[str]:
        cfg = self.cfg
        if not cfg.get("api_key") and str(cfg.get("base_url", "")).find("127.0.0.1") < 0:
            return None  # 云端提供商必须配 Key；本机服务（FreeLLM 等）可免 Key
        with self.session_lock:
            history = list(self.session_history.get(user_id, []))
        if history_extra:
            history = history + list(history_extra)
        messages: list = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        # ADR-008 决策 1：历史交由 token 预算裁剪（保消息完整），
        # 不再按 max_history 硬截条数（0=全文注入）。
        messages.extend(_fit_history(history, _context_budget(cfg)))
        messages.append({"role": "user", "content": message})
        protocol = str(cfg.get("api_protocol", "openai")).lower()
        try:
            if protocol == "anthropic":
                reply = self._chat_anthropic(cfg, messages)
            else:
                reply = self._chat_openai(cfg, messages)
        except Exception as e:
            logger.warning(f"[AI-008] " + f"[ai] AI 请求失败: {e}")
            return None
        if reply:
            with self.session_lock:
                h = self.session_history.setdefault(user_id, [])
                h.append({"role": "user", "content": message})
                h.append({"role": "assistant", "content": reply})
                del h[:-40]
        return reply

    def _chat_openai(self, cfg: dict, messages: list) -> Optional[str]:
        """OpenAI 兼容 /chat/completions（FreeLLM/DeepSeek/GLM/Qwen 等通用）。"""
        headers = {"Content-Type": "application/json"}
        if cfg.get("api_key"):
            headers["Authorization"] = f"Bearer {cfg['api_key']}"
        resp = requests.post(
            f"{str(cfg.get('base_url', '')).rstrip('/')}/chat/completions",
            headers=headers,
            json={
                "model": cfg.get("model", ""),
                "messages": messages,
                "max_tokens": _eff_max_tokens(cfg),
                "temperature": float(cfg.get("temperature", 0.7)),
            },
            timeout=60,
        )
        if resp.status_code != 200:
            logger.warning(f"[AI-009] " + f"[ai] AI API {resp.status_code}: {resp.text[:200]}")
            return None
        # ADR-013 / AI-061：统一走安全提取出口（含 finish_reason 截断判定）
        return _extract_reply(resp.json())

    def _chat_anthropic(self, cfg: dict, messages: list) -> Optional[str]:
        """Anthropic 兼容 /v1/messages（MiniMax anthropic 端点、Claude 官方）。"""
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {cfg.get('api_key', '')}",
            "anthropic-version": "2023-06-01",
        }
        resp = requests.post(
            f"{str(cfg.get('base_url', '')).rstrip('/')}/v1/messages",
            headers=headers,
            json={
                "model": cfg.get("model", ""),
                "messages": messages,
                "max_tokens": _eff_max_tokens(cfg),
                "temperature": float(cfg.get("temperature", 0.7)),
            },
            timeout=30,
        )
        if resp.status_code != 200:
            logger.warning(f"[AI-012] " + f"[ai] AI API {resp.status_code}: {resp.text[:200]}")
            return None
        result = resp.json()
        # ADR-013 / AI-061：Anthropic 协议同样走安全提取出口。
        # Anthropic 在 token 用尽时报 stop_reason="max_tokens"（等价 length）
        # —— 此前同样未处理，截断内容会被直接发给客户。
        try:
            if str(result.get("stop_reason") or "").strip().lower() == "max_tokens":
                logger.warning(
                    "[AI-061] Anthropic 回复被 max_tokens 截断"
                    "（stop_reason=max_tokens）-> 判失败走兜底")
                return None
        except Exception:
            pass
        reply = ""
        content = result.get("content", [])
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    reply = (item.get("text") or "").strip()
                    break
        if not reply:
            logger.warning(f"[AI-013] " + f"[ai] AI 返回为空: {str(result)[:200]}")
            return None
        if _looks_like_reasoning(reply):
            logger.warning(f"[ai] 思考过程泄漏检测命中，丢弃: {reply[:60]}")
            return None
        return reply

    # -- 视觉模型（独立配置，OpenAI 兼容 /chat/completions）---------------

    def chat_failover(self, message: str, consumer_id: str = "ai_main",
                      user_id: str = "default", system_prompt: str = "",
                      history_extra: Optional[list] = None) -> Optional[str]:
        """沿 hub 避障链按序尝试：候选失败（异常/HTTP错/空回复/思考泄漏）
        自动切下一个；llm 链末尾自动附加兜底模型。链未配置时回落旧
        单配置 chat()，全部失败返回 None（调用方走兜底话术）。
        """
        chain = resolve_chain_hub(consumer_id)
        if not chain or not chain.get("candidates"):
            return self.chat(message, user_id=user_id,
                             system_prompt=system_prompt,
                             history_extra=history_extra)
        attempts = list(chain["candidates"])
        if chain.get("fallback"):
            attempts.append(chain["fallback"])
        for c in attempts:
            cfg = dict(self.cfg)
            cfg.update(_cand_cfg(c))
            reply = AIClient(cfg).chat(message, user_id=user_id,
                                       system_prompt=system_prompt,
                                       history_extra=history_extra)
            if reply:
                if len(attempts) > 1:
                    logger.info(f"[ai] 避障命中模型 {c['model']}（候选"
                                f"{attempts.index(c) + 1}/{len(attempts)}）")
                return reply
            logger.warning(f"[AI-031] " + f"[ai] 链路候选失败，切下一个: {c['model']}")
        return None


    def describe_image(self, image_b64: str, mime: str = "jpeg") -> Optional[str]:
        cfg = self.cfg
        base = str(cfg.get("vision_base_url") or "")
        # 本机服务（127.0.0.1）可免 Key；云端必须配 Key
        if not base or (not cfg.get("vision_api_key") and "127.0.0.1" not in base):
            return None
        data_url = f"data:image/{mime};base64,{image_b64}"
        vision_model = cfg.get("vision_model", "")
        # omni/reasoning 系视觉模型：先思考后作答，max_tokens 须给足
        is_reasoner = any(k in vision_model.lower()
                          for k in ("omni", "reasoning", "thinking"))
        try:
            resp = requests.post(
                f"{base.rstrip('/')}/chat/completions",
                headers={
                    "Content-Type": "application/json",
                    **({"Authorization": f"Bearer {cfg['vision_api_key']}"}
                       if cfg.get("vision_api_key") else {}),
                },
                json={
                    "model": vision_model,
                    "messages": [{
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": data_url}},
                            {"type": "text", "text": cfg.get("vision_prompt", "描述图片")},
                        ],
                    }],
                    "max_tokens": 800 if is_reasoner else 200,
                },
                timeout=60 if is_reasoner else 45,
            )
            if resp.status_code != 200:
                logger.warning(f"[AI-014] " + f"[ai] 视觉 API {resp.status_code}: {resp.text[:200]}")
                return None
            r = resp.json()
            msg = (r.get("choices") or [{}])[0].get("message") or {}
            text = (msg.get("content") or "").strip()
            if not text and msg.get("reasoning_content"):
                # omni/reasoning 视觉模型可能把描述写进思考段
                rc = str(msg.get("reasoning_content") or "").strip()
                text = rc[-120:] if rc else ""
            return text or None
        except Exception as e:
            logger.warning(f"[AI-015] " + f"[ai] 视觉请求失败: {e}")
            return None

    def describe_image_failover(self, image_b64: str, mime: str = "jpeg",
                                consumer_id: str = "ai_vision") -> Optional[str]:
        """视觉避障链：vision 链候选按序尝试（兜底模型附末尾）；
        链未配置回落旧单配置 describe_image()。"""
        chain = resolve_chain_hub(consumer_id)
        if not chain or not chain.get("candidates"):
            return self.describe_image(image_b64, mime)
        attempts = list(chain["candidates"])
        if chain.get("fallback"):
            attempts.append(chain["fallback"])
        for c in attempts:
            cfg = dict(self.cfg)
            cfg["vision_base_url"] = c["base_url"]
            cfg["vision_api_key"] = c["api_key"]
            cfg["vision_model"] = c["model"]
            text = AIClient(cfg).describe_image(image_b64, mime)
            if text:
                if len(attempts) > 1:
                    logger.info(f"[ai] 视觉避障命中 {c['model']}")
                return text
            logger.warning(f"[AI-032] " + f"[ai] 视觉候选失败，切下一个: {c['model']}")
        return None

    def test_connection(self) -> tuple[bool, str]:
        reply = self.chat("你好", user_id="__api_test__",
                          system_prompt="请用一句话回复")
        if reply:
            return True, reply[:80]
        return False, "AI 连接失败：请检查 API Key / Base URL / 模型名"


def _is_reasoner(model_name: str) -> bool:
    """是否推理模型（会先吐 reasoning 再吐正文，token 消耗显著更高）。

    实测依据（2026-09-26）：`deepseek-v4.1-flash` 在复杂推理题下
    reasoning 吃掉 365~1009 tokens，直接挤压正文额度。
    """
    n = str(model_name or "").lower()
    return any(k in n for k in ("r1", "reasoner", "reasoning", "think",
                                "deepseek-v4", "glm-5", "o1", "o3"))


def _eff_max_tokens(cfg: dict) -> int:
    """有效 max_tokens：推理模型按配置系数上浮（ADR-013 / AI-061）。"""
    base = cfg.get("max_tokens", 4000)
    try:
        base = int(base)
    except (TypeError, ValueError):
        base = 4000
    if base <= 0:
        base = 4000
    if _is_reasoner(cfg.get("model", "")):
        try:
            f = float(cfg.get("reasoner_max_tokens_factor") or 1.0)
        except (TypeError, ValueError):
            f = 1.0
        if f > 1.0:
            base = int(base * f)
    return base


def _extract_reply(result: dict) -> Optional[str]:
    """从 chat 响应里安全取出可发送给客户的正文（**两处协议共用**）。

    ADR-013 / AI-061：此前**全仓零处处理 `finish_reason`** —— 实测
    `deepseek-v4.1-flash` 在复杂推理题下 `max_tokens`=1000/2000/4000
    **全部 `finish_reason=length`（被截断）**，而截断的半截话被当成
    正常回复直接发给客户 —— 这比走兜底更危险（客户收到不完整答案）。

    判据（任一命中即判失败 -> 走兜底，绝不外发）：
      1. `finish_reason == "length"` => token 用尽被截断
      2. content 空（含推理模型 reasoning 占用情形）
      3. 内容像思考过程（_looks_like_reasoning）
    """
    ch = (result.get("choices") or [{}])[0] if isinstance(result, dict) else {}
    finish = str(ch.get("finish_reason") or "").strip().lower()
    msg = ch.get("message") or {}
    reply = (msg.get("content") or "").strip()
    rc = str(msg.get("reasoning_content") or "").strip()

    # (1) 被截断 —— 最高优先级：半截话绝不能发给客户
    if finish == "length":
        logger.warning(
            f"[AI-061] AI 回复被 max_tokens 截断（finish_reason=length，"
            f"content={len(reply)} reasoning={len(rc)}）-> 判失败走兜底")
        return None

    # (2) content 空 -> 用 reasoning 尾部兜底（历史行为），仍空则失败
    if not reply and rc:
        logger.info("[ai] content 为空，尝试 reasoning_content 兜底")
        reply = rc[-200:]
    if not reply:
        logger.warning(f"[ai] AI 返回为空: {str(result)[:200]}")
        return None

    # (3) 思考过程泄漏
    if _looks_like_reasoning(reply):
        logger.warning(f"[ai] 思考过程泄漏检测命中，丢弃: {reply[:60]}")
        return None
    return reply


# ---------------------------------------------------------------------------
# 留资提取（纯本地正则，零网络请求）
# ---------------------------------------------------------------------------

_PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_PHONE_SPACED_RE = re.compile(  # 手机号带空格/横线分隔：138 1234 5678 / 138-1234-5678
    r"(?<!\d)1[3-9]\d[-\s]?\d{4}[-\s]?\d{4}(?!\d)")
# 微信号：宽松匹配 wx/wxid/v_/weixin 开头 6-20 位字母数字下划线（用户主动发才算）
_WECHAT_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:wx|weixin|wxid|v_)[A-Za-z0-9_-]{5,19}(?![A-Za-z0-9_-])",
    re.IGNORECASE)


def extract_contacts(text: str) -> list[tuple[str, str]]:
    """从客户消息提取 (contact_type, value)。只认用户主动发出的。"""
    out: list[tuple[str, str]] = []
    if not text:
        return out
    compact = re.sub(r"[-\s]", "", text)  # 去掉空格/横线再匹配手机号
    for m in _PHONE_RE.findall(compact):
        out.append(("phone", m))
    # 分隔格式（138 1234 5678）可能因去空格后跨字粘连产生误报，单独提取并还原
    for m in _PHONE_SPACED_RE.finditer(text):
        v = re.sub(r"[-\s]", "", m.group(0))
        if ("phone", v) not in out and re.fullmatch(r"1[3-9]\d{9}", v):
            out.append(("phone", v))
    for m in _WECHAT_RE.findall(text):
        out.append(("wechat", m))
    # 去重保序
    seen, uniq = set(), []
    for t, v in out:
        if (t, v) not in seen:
            seen.add((t, v))
            uniq.append((t, v))
    return uniq


def save_lead(account: str, conv_id: str, peer_name: str,
              ctype: str, cvalue: str, source_text: str) -> bool:
    """写线索表。同会话同联系方式只写一次（UNIQUE 约束兜底）。"""
    try:
        conn = database.get_db()
        cur = conn.execute(
            "INSERT OR IGNORE INTO ai_leads(account, conv_id, peer_name, "
            "contact_type, contact_value, source_text, status, created_at) "
            "VALUES(?,?,?,?,?,?, 'new', ?)",
            (account, conv_id, peer_name or "", ctype, cvalue,
             (source_text or "")[:200], time.time()),
        )
        conn.commit()
        return cur.rowcount > 0
    except Exception as e:
        logger.warning(f"[AI-016] " + f"[ai] 线索写入失败: {e}")
        return False


# ---------------------------------------------------------------------------
# 护栏：AI 出口文本校验
# ---------------------------------------------------------------------------

def validate_reply(reply: str, cfg: dict) -> Optional[str]:
    """校验 AI 文本；不通过返回 None（调用方改发兜底话术）。

    2026-09-28（D4 内容层）新增**最小长度护栏**（残句防线）：
    实测 `deepseek-v4.1-flash` 在推理超时降级后只回了「能认」两字 ——
    该情形 `finish_reason` **不是** `length`（截断检测 `_extract_reply` 抓不到），
    于是 2 字残句被当正常回复**直发客户**（客户视角＝敷衍/不专业）。
    低于 `min_reply_len`（默认 5，与 prompt「5-40 字」口径一致）判残句 → 走兜底。
    """
    if not reply or not reply.strip():
        return None
    reply = reply.strip().strip('"“”')
    low = reply.lower()
    for w in cfg.get("forbidden_words", []):
        if w and (w.lower() in low or w in reply):
            logger.info(f"[ai] 护栏拦截（命中违禁词「{w}」）: {reply[:40]}")
            return None
    min_len = int(cfg.get("min_reply_len", 5))
    if len(reply) < min_len:
        logger.info(f"[AI-064] 护栏拦截（回复过短疑似残句「{reply}」"
                    f"<{min_len} 字）-> 走兜底")
        return None
    # 2026-09-28（D4）**专业准确性护栏**：绝对化断言与专业知识相悖，禁止外发。
    # 实测：AI 对「陈旧性骨折还能认工伤吗」回「能认」—— 与专业知识完全相悖
    # （陈旧性骨折难以证明本次因果，实务中基本不予评定；等级按功能障碍，
    #   不得凭伤情断言）。这里只拦「把话说死」的形态；正常分析（含糊型
    #   「可以申请，最终以认定结论为准」）**不命中**，不会误伤。
    for pat in _RE_ASSERTIVE:
        m = pat.search(reply)
        if m:
            logger.warning(f"[AI-065] 护栏拦截（绝对化断言「{m.group(0)}」与专业"
                           f"知识相悖）-> 走兜底: {reply[:40]}")
            return None
    max_len = int(cfg.get("max_reply_len", 60))
    if len(reply) > max_len:
        # 截第一句（句号/问号/感叹号/换行处）
        m = re.split(r"[。？！?!~\n]", reply)
        first = next((s for s in m if s.strip()), "")
        reply = first.strip() or reply[:max_len]
        # 2026-09-28（D4 修正）：截断把「长度检查」与「实际外发文本」解耦 ——
        # 实测长回复若首句极短（如「很难。……」），截断后**又变成残句外发**
        # （65 字 -> 首句「很难」2 字，绕过前面的 min_len 检查直发）。
        # 故截断后必须**复核 post-condition**：最终外发文本仍须 >= min_reply_len。
        if len(reply) < min_len:
            logger.info(f"[AI-066] 护栏拦截（截断后疑似残句「{reply}」"
                        f"<{min_len} 字）-> 走兜底")
            return None
    return reply


def fallback_reply(cfg: dict, kind: str = "text") -> str:
    pool = cfg.get("fallback_pool", [])
    if kind == "image":
        return cfg.get("fallback_image", "图我看到了哈，稍等我看看再回你")
    return random.choice(pool) if pool else "稍等哈，我看下"


# ---------------------------------------------------------------------------
# 黑名单
# ---------------------------------------------------------------------------

def blacklist_list() -> list:
    return _kv_get(_KV_BL, [])


def blacklist_add(user_id: str) -> None:
    bl = blacklist_list()
    if user_id and user_id not in bl:
        bl.append(user_id)
        _kv_set(_KV_BL, bl)


def blacklist_remove(user_id: str) -> None:
    bl = blacklist_list()
    if user_id in bl:
        bl.remove(user_id)
        _kv_set(_KV_BL, bl)


# ---------------------------------------------------------------------------
# 自动回复监听器
# ---------------------------------------------------------------------------

class AutoReplyWorker:
    """全自动监听：轮询 dm_messages 新入库的 role='them' 消息 →
    留资提取 / 知识库 / AI（RAG）→ 复用 /api/messages/send 发送。

    铁律对齐：
    - 纯读 SQLite，零捕获、零昵称查询；
    - 发送走既有双通道（recv_daemon /send 主 + BCC /wp_send 兜底）；
    - 同一会话同一消息只回一次；同一联系方式只写一条线索。
    """

    POLL_INTERVAL = 5.0
    # 上下文条数的**兜底**上限：仅当配置 max_history 缺失/非法时生效。
    # 正式取值走 _history_limit() —— 读配置 max_history
    # （_DEFAULT_CONFIG 里默认 10；该键此前从未被任何代码读取，属悬空契约，
    #   P1-1 一并接线）。
    HISTORY_LIMIT = 6

    def __init__(self):
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        # ADR-008 决策 3：per-session 串行 + 跨会话并行。
        # `_pool` 每次 start 重建（尺寸随 reply_concurrency 配置）；
        # `_sess_locks` 保证同一 account:conv_id 严格串行；
        # `_inflight` 在途会话集合 —— _tick 对已在处理的会话**先推进水位**
        # 再跳过，避免下一轮重复捞取（避免同会话双写）。
        self._pool: ThreadPoolExecutor | None = None
        self._sess_locks: dict = {}
        self._sess_locks_guard = threading.Lock()
        self._inflight: set = set()
        self.status = {
            "running": False,
            "last_tick": 0.0,
            "processed": 0,
            "replied": 0,
            "leads": 0,
            "errors": 0,
            "last_reply": "",
        }

    def _sess_lock(self, key: str):
        """取该会话的互斥锁（同会话串行、跨会话互不阻塞）。"""
        with self._sess_locks_guard:
            lk = self._sess_locks.get(key)
            if lk is None:
                lk = threading.Lock()
                self._sess_locks[key] = lk
            return lk

    def _handle_session(self, r, cfg: dict):
        """线程池回调：持会话锁处理单条消息，结束后摘除在途标记。

        同会话串行（锁）保证客户看到的顺序 = 发言顺序、上下文不串；
        不同会话由线程池并行。
        """
        key = "%s:%s" % (r["account"], r["conv_id"])
        try:
            with self._sess_lock(key):
                self._handle(r, cfg)
            self.status["processed"] += 1
        except Exception as e:
            self.status["errors"] += 1
            try:
                _probe(r["id"], "TICK", "error", err=str(e)[:160])
            except Exception:
                pass
            logger.warning(f"[AI-018] " + f"[ai] 单条处理异常: {e}")
        finally:
            with self._sess_locks_guard:
                self._inflight.discard(key)

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        try:
            _n = int((get_config() or {}).get("reply_concurrency") or 1)
        except (TypeError, ValueError):
            _n = 1
        _n = max(1, min(_n, 32))
        self._pool = (ThreadPoolExecutor(max_workers=_n,
                                        thread_name_prefix="ai-reply")
                      if _n > 1 else None)
        with self._sess_locks_guard:
            self._inflight.clear()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="ai-autoreply")
        self._thread.start()
        self.status["running"] = True
        logger.info("[ai] 获客自动回复监听已启动（全自动模式）")

    def stop(self):
        self._stop.set()
        if self._pool is not None:
            try:
                self._pool.shutdown(wait=True, cancel_futures=True)
            except Exception:  # noqa: BLE001
                pass
            self._pool = None
        self.status["running"] = False
        logger.info("[ai] 获客自动回复监听已停止")

    def _run(self):
        # 启动时刻打水位：只回开启之后的新消息，绝不回历史
        conn = database.get_db()
        row = conn.execute("SELECT COALESCE(MAX(id),0) FROM dm_messages").fetchone()
        if int(_kv_get(_KV_MARKER, 0) or 0) == 0:
            _kv_set(_KV_MARKER, row[0] if row else 0)
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as e:
                self.status["errors"] += 1
                logger.warning(f"[AI-017] " + f"[ai] 监听 tick 异常: {e}")
            self._stop.wait(self.POLL_INTERVAL)

    def _tick(self):
        # v0.38.4：模型链路中心叠加 —— 主模型/视觉/语义的连接参数以
        # model_hub 绑定为准（未配置时返回原 cfg，零回归）。
        cfg = apply_model_hub(get_config())
        conn = database.get_db()
        row = conn.execute("SELECT COALESCE(MAX(id),0) FROM dm_messages").fetchone()
        max_id = row[0] if row else 0
        if not cfg.get("enabled"):
            _kv_set(_KV_MARKER, max_id)  # 关闭时推进水位
            self.status["last_tick"] = time.time()
            return
        last_id = int(_kv_get(_KV_MARKER, 0) or 0)
        rows = conn.execute(
            "SELECT m.id, m.account, m.conv_id, m.role, m.text, m.msg_type, "
            "       m.extra, c.peer_id, c.peer_name "
            "FROM dm_messages m LEFT JOIN dm_conversations c "
            "  ON c.account=m.account AND c.conv_id=m.conv_id "
            "WHERE m.id>? AND m.role='them' "
            "  AND TRIM(COALESCE(m.text,''))<>'' "
            "  AND m.text NOT LIKE '[对方已读%' "
            "ORDER BY m.id ASC LIMIT 20",
            (last_id,),
        ).fetchall()
        for r in rows:
            # [PROBE-1] 水位/捞取：记录进入决策链的消息
            try:
                _probe(r["id"], "TICK", "pick",
                       account=r["account"], conv_id=r["conv_id"],
                       peer=r.get("peer_name") or r.get("peer_id"),
                       text=(r["text"] or "")[:80],
                       msg_type=r["msg_type"], last_id=last_id)
            except Exception:
                pass
            # ADR-008 决策 3：跨会话并行、同会话串行。
            _skey = "%s:%s" % (r["account"], r["conv_id"])
            with self._sess_locks_guard:
                if _skey in self._inflight:
                    # 🔴 AI-018 竞态修复（2026-09-26 H-22 审计 · 实跑复现）：
                    # 会话在途 ⇒ **中断本轮，绝不越过它推进水位**。
                    # 原实现「先推进水位再 continue」会让本行之后的所有消息
                    # （m.id > 水位）下轮不再被捞出 ⇒ **永久丢消息**
                    # （实跑：同会话 id 5&6 同 tick，5 提交后 6 静默丢失）。
                    # 中断后水位停在已处理行，下轮从水位重捞 ⇒ 行 6 必被取回；
                    # 且本轮未提交其后行，故不会重复处理（避免重复回复）。
                    break
                self._inflight.add(_skey)
            # 仅对**真正提交处理**的行推进水位 ⇒ 维持不变式「水位之前必已处理」
            last_id = max(last_id, r["id"])
            _kv_set(_KV_MARKER, last_id)
            if self._pool is None:
                self._handle_session(r, cfg)
            else:
                try:
                    self._pool.submit(self._handle_session, r, cfg)
                except Exception as e:  # 池已关闭等
                    with self._sess_locks_guard:
                        self._inflight.discard(_skey)
                    self.status["errors"] += 1
                    logger.warning(f"[AI-018] " + f"[ai] 提交并发池失败: {e}")
        self.status["last_tick"] = time.time()

    # -- 单条处理 ----------------------------------------------------------

    def _handle(self, row, cfg: dict):
        account = row["account"]
        conv_id = row["conv_id"]
        text = (row["text"] or "").strip()
        peer_name = row["peer_name"] or row["peer_id"] or "对方"
        key = f"{account}:{conv_id}"

        # v0.38.0：Agent 模版解析 —— 按账号绑定的 Agent 覆盖全局配置。
        # 未绑定 Agent 时原样返回 cfg（零回归），绑定则用 Agent 的
        # 模型/档位/prompt/知识库/黑名单/兜底话术。
        try:
            from services import ai_agent

            cfg = ai_agent.resolve_config(account, cfg)
        except Exception:  # Agent 模块异常绝不影响回复主流程
            pass
        # 2026-09-10：Agent 作用域（scopes）决定该 Agent 应用到哪些模块
        #（私信中心/直播监听/视频采集）。作用域检查由各消费方自行判断，
        # 此处只做配置解析，不做账号级拦截。

        bl = set(blacklist_list())
        # v0.38.0：黑名单跟随 Agent（用户决策：知识库/黑名单/兜底全部 Agent 级）
        try:
            from services import ai_agent

            bl = set(ai_agent.resolve_blacklist(account, list(bl)))
        except Exception:
            pass
        if (row["peer_id"] and row["peer_id"] in bl) or peer_name in bl:
            _probe(row["id"], "BLACKLIST", "hit", account=account,
                   peer_id=row["peer_id"], peer_name=peer_name)
            return
        _probe(row["id"], "BLACKLIST", "pass", account=account,
               peer_id=row["peer_id"], peer_name=peer_name, bl_size=len(bl))

        # 会话维度防重：同会话同文本 60s 内只处理一次（WS+WP 双通道落库去重）
        with self._lock:
            recent_key = f"ai_reply_recent_{key}"
            recent = _kv_get(recent_key, {"last_at": 0, "last_text": ""})
            now = time.time()
            if now - float(recent.get("last_at", 0)) < 60 and \
                    recent.get("last_text") == text:
                return
            self._mark_recent(recent_key, text, now)

        # ---- 0) 留资提取（最高优先级：客户主动留的号原样入库，不过护栏）----
        contacts = extract_contacts(text)
        _probe(row["id"], "LEAD_EXTRACT", "hit" if contacts else "miss",
               account=account, conv_id=conv_id,
               types=[c[0] for c in contacts],
               masked=[(c[1][:3] + "****" + c[1][-2:]) if len(c[1]) > 6 else "***"
                       for c in contacts])
        if contacts:
            n_new = 0
            for ctype, cvalue in contacts:
                if save_lead(account, conv_id, peer_name, ctype, cvalue, text):
                    n_new += 1
                    self.status["leads"] += 1
            _probe(row["id"], "LEAD_SAVE", "saved" if n_new else "dup",
                   n_new=n_new, types=[c[0] for c in contacts])
            if n_new:
                logger.info(f"[ai] 🎯 留资成功 {peer_name}: "
                            f"{[v for _, v in contacts]} → ai_leads")
                # 留资成功 → 回确认话术（不调 AI）
                self._send_delayed(account, conv_id,
                                   cfg.get("lead_confirm", "收到～稍后这边联系你哈"),
                                   cfg, source="留资确认")
                return

        # ---- 1) 图片消息（msg_type='27' 实库证真；text 以 [图片] 开头）----
        is_image = text.startswith("[图片]") or str(row["msg_type"]) == "27"
        vision_text = None
        if is_image:
            vision_text = self._describe_image(account, row, cfg)
            if vision_text is None:
                # 视觉未配置/失败 → 固定兜底，绝不瞎猜
                self._send_delayed(account, conv_id,
                                   fallback_reply(cfg, "image"), cfg,
                                   source="图片兜底")
                return
            # 视觉有描述 → 把描述当作客户消息内容继续走文本流程
            text = f"[客户发来图片] {vision_text}"

        # ---- 2) 生成回复（档位分流）----
        reply, source = self._generate_reply(cfg, text, account, conv_id, row["id"])
        if not reply:
            logger.info(f"[ai] 未生成回复（档位={cfg.get('strict_level')}）: "
                        f"{peer_name}: {text[:40]}")
            return
        self._send_delayed(account, conv_id, reply, cfg, source=source)

    # -- 回复生成（档位 + 护栏 + 兜底）-------------------------------------

    def _generate_reply(self, cfg: dict, text: str, account: str,
                        conv_id: str, before_id: int) -> tuple[Optional[str], str]:
        level = cfg.get("strict_level", "rag")
        _probe(before_id, "GEN_REPLY", "enter", level=level,
               account=account, conv_id=conv_id, text=text[:80])

        # v0.39.0：① 对话回复库（命中库）—— 案例命中直接回复，零 token。
        try:
            from services import reply_kb

            hit = reply_kb.find_match(text, account=account)
            _probe(before_id, "REPLY_KB", "hit" if hit else "miss",
                   level=level, reply=(hit or "")[:100])
            if hit:
                return hit, "回复库"
        except Exception as e:
            _probe(before_id, "REPLY_KB", "error", error=str(e)[:120])

        # 2026-09-10 用户拍板：两库职责明确，不混淆——
        #   命中即回只走 ①对话回复库（reply_kb）；
        #   ②专业知识库只做 RAG 参考（build_system_prompt 内 pro_kb 全文注入），
        #     绝不直接回复。旧 KB.find_match 从回复链路移除（旧 QA 已迁入回复库）。

        if level == "kb_only":
            # 严格档：AI 完全不参与 → 兜底话术池
            _probe(before_id, "LEVEL", "kb_only_fallback", level=level)
            return fallback_reply(cfg), "兜底"

        # rag / free：调 AI（RAG 注入专业库条目全文）
        _probe(before_id, "LEVEL", "ai_call", level=level)
        client = AIClient(cfg)
        kb_items = KB.list_items()
        try:
            from services import ai_agent

            kb_items = ai_agent.resolve_knowledge(account, kb_items)
        except Exception:
            pass
        prompt = build_system_prompt(cfg, text)
        _probe(before_id, "PROMPT", "built", level=level,
               prompt_len=len(prompt), kb_items=len(kb_items),
               **_LAST_PROMPT_STATS)
        history = self._build_history(account, conv_id, before_id, cfg)
        raw = client.chat_failover(text, consumer_id="ai_main",
                                   user_id=f"{account}:{conv_id}",
                                   system_prompt=prompt, history_extra=history)
        _probe(before_id, "AI_RAW", "ok" if raw else "empty", level=level,
               raw=(raw or "")[:200])
        if raw:
            # 二次防线：推理模型思考过程泄漏（首层在 _chat_openai 已拦，
            # 这里兜住 reasoning 兜底提取出的残留思考文本）
            if _looks_like_reasoning(raw) and len(raw) > 40:
                _probe(before_id, "GUARD", "reasoning_leak", raw=raw[:80])
                logger.warning(f"[AI-019] " + f"[ai] 回复疑似思考过程残留，丢弃改兜底: {raw[:40]}")
                raw = None
        if raw:
            cleaned = validate_reply(raw, cfg)
            _probe(before_id, "GUARD", "pass" if cleaned else "blocked",
                   level=level, cleaned=(cleaned or "")[:120],
                   raw=raw[:120])
            if cleaned:
                return cleaned, "AI"
            logger.info(f"[ai] AI 输出被护栏拦截，改发兜底: {raw[:40]}")
        fb = fallback_reply(cfg)
        _probe(before_id, "FALLBACK", "used", level=level, reply=fb[:120])
        return fb, "兜底"

    # -- 图片 → 视觉模型 ---------------------------------------------------

    def _describe_image(self, account: str, row, cfg: dict) -> Optional[str]:
        """解密图片 → base64 → 视觉模型描述。失败返回 None（不瞎猜）。

        ADR-008 决策 2：缓存优先 —— 命中缓存直接返回（历史重放零成本）；
        负缓存（数据级不可解）不再重试。缓存读刻意放在 vision_enabled
        门之前，使「曾描述过的图片」在视觉关闭后仍可被历史引用。
        """
        mid = str(row["id"])
        cached = _img_desc_get(mid)
        if cached:
            return cached
        if _img_desc_known(mid):
            return None
        if not cfg.get("vision_enabled"):
            return None
        try:
            extra = json.loads(row["extra"] or "{}")
        except Exception:
            extra = {}
        skey = extra.get("skey") or ""
        origin_url = extra.get("origin_url") or ""
        # AI-060（2026-09-25）：原「从 text 兜底取 URL」已删除。
        # 实测本项目真实数据 text 恒为 '[图片]'（URL 只在 extra.origin_url），
        # 该正则 r"\[图片\]\s+(\S+)" 永不匹配 —— 死代码，且会让排障者误以为
        # 存在第二条恢复路径。缺 origin_url 一律走下方显式失败分支。
        if not skey or not origin_url:
            logger.info("[ai] 图片消息缺 skey/origin_url，无法解密")
            try:
                _img_desc_mark(mid, "")      # 数据级不可解 → 负缓存
            except Exception:
                pass
            return None
        try:
            from auto_dm import origin_image_resolver
            res = origin_image_resolver.resolve(
                account=account, msg_id=str(row["id"]), skey=skey,
                origin_url=origin_url)
        except Exception as e:
            logger.warning(f"[AI-020] " + f"[ai] 图片解密失败: {e}")
            return None
        if not res.get("ok"):
            logger.warning(f"[AI-021] " + f"[ai] 图片解密未成功: {res.get('error')}")
            try:
                _img_desc_mark(mid, "")      # 解密态失败 → 负缓存
            except Exception:
                pass
            return None
        # 本地文件 → base64（kind=local / inline_base64 都可能）
        img_bytes: Optional[bytes] = None
        mime = "jpeg"
        if res.get("kind") == "local":
            fname = str(res.get("url", "")).rsplit("/", 1)[-1]
            fpath = Path(origin_image_resolver._origin_cache_dir()) / fname
            if fpath.exists():
                img_bytes = fpath.read_bytes()
                mime = res.get("format") or "jpeg"
                if mime == "heic":
                    mime = "jpeg"  # HEIC 已在 resolve 里转码
        elif res.get("kind") == "inline_base64":
            try:
                data_url = res.get("url", "")
                head, b64 = data_url.split(",", 1)
                img_bytes = base64.b64decode(b64)
                mime = "jpeg" if "png" not in head else "png"
            except Exception as e:
                logger.warning(f"[AI-022] " + f"[ai] inline base64 解码失败: {e}")
        if not img_bytes:
            return None
        client = AIClient(cfg)
        desc = client.describe_image_failover(
            base64.b64encode(img_bytes).decode(), mime)
        if desc:
            # 视觉成功 → 落缓存，供后续每轮历史重放（ADR-008 决策 2）
            try:
                _img_desc_mark(mid, desc)
            except Exception as e:  # noqa: BLE001
                logger.debug(f"[ai] 图片描述缓存写入失败: {e}")
        return desc

    # -- 上下文 / 发送 -----------------------------------------------------

    @classmethod
    def _history_limit(cls, cfg: dict | None = None) -> int:
        """上下文条数上限：读配置 max_history，缺失/非法时回落 HISTORY_LIMIT。

        P1-1：_DEFAULT_CONFIG["max_history"] 此前是悬空契约（无人读取），
        这里把它接进 _build_history；cfg 为 None 时自行 get_config()。
        上界 50 只是防御（防止误配把整段会话灌进 prompt）。
        """
        if not isinstance(cfg, dict):
            try:
                cfg = get_config()
            except Exception:  # noqa: BLE001
                cfg = {}
        try:
            n = int(cfg.get("max_history", cls.HISTORY_LIMIT) or 0)
        except (TypeError, ValueError):
            n = 0
        # 0 = 全文注入（ADR-008 决策 1，推荐默认）；>0 = 仅最近 N 条（兼容）
        return max(0, min(n, 50))

    def _build_history(self, account: str, conv_id: str, before_id: int,
                       cfg: dict | None = None) -> list:
        """取当前消息之前的会话上下文（按 id 升序，role 归一化）。

        P1-1（2026-09-23）：msg_type 由「硬筛 'text'」改为**白名单**
        _HISTORY_TEXT_TYPES = ('text', '7')。原因见该常量上方注释：
        WS 实时路径落库的是数字字符串 '7'，与 'text' 永不相等，导致
        history 恒为空。白名单只收已确认的文本语义，'1'/'50010' 等
        语义未确认取值一律排除。
        """
        try:
            limit = self._history_limit(cfg)
            ph = ",".join(["?", ] * len(_HISTORY_TEXT_TYPES))
            sql = (
                "SELECT id, role, text, msg_type, extra FROM dm_messages "
                "WHERE account=? AND conv_id=? AND id<? "
                f"  AND msg_type IN ({ph}) "
                "  AND TRIM(COALESCE(text,''))<>'' "
                + _HISTORY_NOISE_SQL + " "          # H-25：噪音前缀不进 prompt
                "ORDER BY COALESCE(ts,0) DESC, id DESC"
            )
            params = [account, conv_id, before_id, *_HISTORY_TEXT_TYPES]
            if limit > 0:                    # 0 = 全文注入（不截条数）
                sql += " LIMIT ?"
                params.append(limit)
            rows = database.get_db().execute(sql, tuple(params)).fetchall()
            hist: list = []
            for r in reversed(rows):
                # ADR-012：读侧白名单**升维到 kind**（应用层过滤）。
                # 存量行无 extra.kind，且系统文案的 msg_type 恰是 'text'
                # （在 SQL 白名单内）⇒ 只能在应用层用 Schema SSOT 判定。
                # 这样未知/系统类型天然被挡住，无需再补黑名单。
                try:
                    from services.message_schema import readable as _readable
                    if not _readable(r["text"], r["msg_type"], r["extra"]):
                        continue
                except Exception:  # 判据不可用时不得静默放行 → 保守放行但记
                    logger.debug("[ai] kind 判定失败，回退按 msg_type")
                role = "assistant" if r["role"] == "me" else "user"
                if str(r["msg_type"]) == "27":
                    # ADR-008 决策 2：图片还原为会话消息（有描述才注入）。
                    # 已缓存 → 零成本；无缓存 → 尝试补描述（受 vision_enabled
                    # 门控）；仍无 → 跳过（等同旧行为，零回归）。
                    desc = None
                    try:
                        desc = self._describe_image(account, r, cfg)
                    except Exception as e:  # noqa: BLE001
                        logger.debug(f"[ai] 历史图片补描述失败: {e}")
                    if not desc:
                        continue
                    hist.append({"role": role,
                                 "content": f"[客户发来图片] {desc}"})
                    continue
                hist.append({"role": role,
                             "content": _sanitize_history_text(r["text"])})
            # ADR-008 决策 1：条数不截，改由 token 预算兜底（保消息完整）。
            return _fit_history(hist, _context_budget(cfg))
        except Exception as e:
            logger.debug(f"[ai] 组装上下文失败: {e}")
            return []

    def _send_delayed(self, account: str, conv_id: str, text: str,
                      cfg: dict, source: str = ""):
        """随机延迟（模拟真人）→ 复用 /send 双通道发送。延迟期间放锁。"""
        lo = float(cfg.get("min_delay", 8))
        hi = float(cfg.get("max_delay", 20))
        delay = random.uniform(min(lo, hi), max(lo, hi))
        logger.info(f"[ai] {source} → {conv_id[:12]}…: {text[:30]}… "
                    f"延迟 {delay:.1f}s")
        # 随机延迟期间不持锁（self._lock 只保护 recent 防重段），
        # 等待中若 stop 被置位则放弃发送
        self._stop.wait(delay)
        if self._stop.is_set():
            return
        if self._send_via_chain(account, conv_id, text):
            self.status["replied"] += 1
            self.status["last_reply"] = f"{source}: {text[:40]}"

    def _send_via_chain(self, account: str, conv_id: str, text: str) -> bool:
        """复用 /api/messages/send 双通道（与前端手动发送同一链路）。"""
        try:
            # 2026-09-07：私信统一调度——AI 回复也走「会话整理池」，
            # 与手动/批量共享同一 per-account 串行队列（源=ai，优先级 1）。
            # 好处：① 会话 peer 被整理校验（防发给污染 uid）② 与手动发送
            # 不抢不重 ③ 高频 AI 回复受队列 + 既有频率闸门双重约束。
            from services.dm_dispatch import submit as _dm_submit
            r = _dm_submit(account, conv_id, text, "ai", 1)
            _probe("send", "DISPATCH_POOL", "accepted", account=account,
                   conv_id=conv_id, task_id=getattr(r, "task_id", None),
                   text=text[:60])
            if r.accepted:
                # 入池即受理：AI 场景不阻塞等结果（延迟线程本就是异步的），
                # 结果由 task 状态记录，失败会记日志。
                logger.info(f"[ai] 已入池待发 (task={r.task_id}): {text[:30]}")
                return True
            _probe("send", "DISPATCH_POOL", "rejected", account=account,
                   conv_id=conv_id, error=str(getattr(r, "error", ""))[:120])
            logger.warning(f"[AI-023] " + f"[ai] 入池被拒: {r.error}")
            if r.error == "duplicate":
                return True      # 重复消息视为已处理，不重试
        except Exception as e:
            _probe("send", "DISPATCH_POOL", "error", account=account,
                   conv_id=conv_id, error=str(e)[:120])
            logger.warning(f"[AI-024] " + f"[ai] 调度入池异常: {e}")
        try:
            from api.messages import _recv_url, _bcc_url, _http_post_json
            d = _http_post_json(_recv_url(account, "/send"), {
                "account": account, "conv_id": conv_id, "text": text,
            }, timeout=15.0)
            if d and d.get("ok"):
                _probe("send", "WS", "ok", account=account, conv_id=conv_id,
                       text=text[:60])
                logger.info(f"[ai] 已发送 (WS): {text[:30]}")
                return True
            _probe("send", "WS", "fail", account=account, conv_id=conv_id,
                   resp=str(d)[:150])
        except Exception as e:
            _probe("send", "WS", "error", account=account, conv_id=conv_id,
                   error=str(e)[:150])
            logger.warning(f"[AI-025] " + f"[ai] WS 通道失败: {e}")
        try:
            d = _http_post_json(_bcc_url(account, "/wp_send"), {
                "account": account, "conv_id": conv_id, "text": text,
            }, timeout=30.0)
            if d and d.get("ok"):
                _probe("send", "WP", "ok", account=account, conv_id=conv_id,
                       text=text[:60])
                logger.info(f"[ai] 已发送 (WP): {text[:30]}")
                return True
            _probe("send", "WP", "fail", account=account, conv_id=conv_id,
                   resp=str(d)[:150])
            logger.warning(f"[AI-026] " + f"[ai] WP 通道失败: {(d or {}).get('error')}")
        except Exception as e:
            _probe("send", "WP", "error", account=account, conv_id=conv_id,
                   error=str(e)[:150])
            logger.warning(f"[AI-027] " + f"[ai] WP 通道异常: {e}")
        _probe("send", "ALL_CHANNELS", "failed", account=account,
               conv_id=conv_id, text=text[:60])
        return False

    def _mark_recent(self, key: str, text: str, now: float) -> None:
        _kv_set(key, {"last_at": now, "last_text": text})


WORKER = AutoReplyWorker()


# ---------------------------------------------------------------------------
# 线索查询 / 导出（API 层用）
# ---------------------------------------------------------------------------

def generate_dm_for_live(account: str, peer_name: str, comment: str,
                         cfg: dict, uid: str = "") -> tuple[str, str]:
    """为「直播监听 / 视频采集」来源生成一条开场私信文案（同步，供 to_thread 调用）。

    设计契约（用户 2026-09-19 口径 + 9.29 §AI 接入点收敛）：
      直播与采集两场景的私信统一走 core/dispatch 调度，文案由
      `pick_dm_message`（词库）扩展为「AI 生成优先 -> 词库回落」。
      本函数是**唯一**的生成入口 —— 不允许在 live/crawl 各自实现一份。

    与 AutoReplyWorker._generate_reply 的差异（刻意不同，勿强行合并）：
      - 无 conv_id / 无历史消息（目标是陌生人首触，不是会话内回复）；
      - 语境 = 对方在公屏的评论/弹幕内容（`comment`）；
      - 严格档（kb_only）由调用方拦掉，不进来。

    返回 `(text, source)`：
      - `"AI"`   -> 生成成功且过了护栏；
      - `"兜底"` -> 生成/护栏失败 -> 返回兜底话术；
      - `""`     -> 未启用 / 配置异常 -> **返回空串让调度器回落词库**
                   （这是与会话回复不同的处置：直播侧词库本就存在）。
    """
    try:
        if not cfg or not cfg.get("enabled"):
            return "", ""
        if str(cfg.get("strict_level", "rag")) == "kb_only":
            return "", ""
        text_in = (comment or "").strip() or "（直播间新观众，暂无发言）"

        # ① 对话回复库（命中库）：命中即回，零 token（与私信侧同一条链路）
        try:
            from services import reply_kb

            hit = reply_kb.find_match(text_in, account=account)
            if hit:
                return str(hit).strip(), "回复库"
        except Exception:
            pass

        # ② 专业库（RAG 参考）+ 系统提示词（与私信侧同一构建器）
        kb_items = KB.list_items()
        try:
            from services import ai_agent

            kb_items = ai_agent.resolve_knowledge(account, kb_items)
        except Exception:
            pass
        prompt = build_system_prompt(cfg, text_in)
        # 直播首触没有对话历史：明确告知模型，避免它编造上下文
        prompt = (prompt + "\n\n【场景】这是你主动向直播间观众发起的第一条私信。"
                  "请紧扣对方在公屏说的话开场，不要假装此前有过对话。"
                  f"对方昵称：{peer_name or '观众'}。")

        raw = AIClient(cfg).chat_failover(
            text_in, consumer_id="ai_main",
            user_id=f"live:{account}:{uid or peer_name or 'unknown'}",
            system_prompt=prompt)
        if not raw:
            logger.info(f"[ai] 直播文案 AI 返回空，回落兜底（账号={account}）")
            return str(fallback_reply(cfg) or "").strip(), "兜底"
        if _looks_like_reasoning(raw) and len(raw) > 40:
            logger.warning(f"[AI-019] " + f"[ai] 直播文案疑似思考过程残留，丢弃改兜底: {raw[:40]}")
            return str(fallback_reply(cfg) or "").strip(), "兜底"
        cleaned = validate_reply(raw, cfg)
        if not cleaned:
            logger.info(f"[ai] 直播文案被护栏拦截，改发兜底: {(raw or '')[:40]}")
            return str(fallback_reply(cfg) or "").strip(), "兜底"
        return str(cleaned).strip(), "AI"
    except Exception as e:
        logger.warning(f"[AI-032] " + f"[ai] 直播文案生成失败: {e}")
        return "", ""


def judge_high_value(text: str, account: str = "") -> dict:
    """ADR-007 / C-06 Phase 4：LLM 精判弹幕/评论是否高价值。

    返回 `{"high_value": bool, "reason": str}`；任何失败返回 `{"high_value": None}`
    （调用方据此**保持关键词判定**，不阻塞、不降级为假）。

    设计契约（C-06 I5 / Q4）：
      - 仅供 `high_value_llm_enabled=true` 时调用；失败绝不抛异常；
      - 判定是**过滤器增强**，不是发送触发器。
    """
    try:
        if not str(text or "").strip():
            return {"high_value": None, "reason": ""}
        cfg = get_config()
        if not cfg.get("base_url"):
            return {"high_value": None, "reason": "llm_not_configured"}
        prompt = (
            "你是工伤法律咨询的线索质检员。判断下面这条直播间弹幕/评论是否"
            "「高价值工伤咨询线索」——即：发信人很可能本人或近亲遭遇工伤，"
            "且**有寻求赔偿/鉴定/律师帮助的真实意图**。\n"
            "只回一个 JSON：{\"high_value\": true/false, \"reason\": \"≤20字理由\"}。\n"
            "无关闲聊、同行打广告、纯情绪宣泄、无工伤要素 → false。"
        )
        raw = AIClient(cfg).chat_failover(
            str(text)[:500], consumer_id="ai_main",
            user_id="hv:" + (account or "unknown"), system_prompt=prompt)
        if not raw:
            return {"high_value": None, "reason": "llm_empty"}
        m = re.search(r"\{.*\}", str(raw), re.S)
        if not m:
            return {"high_value": None, "reason": "llm_no_json"}
        data = json.loads(m.group(0))
        hv = data.get("high_value")
        if not isinstance(hv, bool):
            return {"high_value": None, "reason": "llm_bad_type"}
        return {"high_value": hv, "reason": str(data.get("reason") or "")[:60]}
    except Exception as e:
        logger.debug(f'[SILENT-00] services.ai_reply: judge_high_value failed: {e}')
        return {"high_value": None, "reason": "llm_exception"}


def list_leads(limit: int = 200) -> list:
    rows = database.get_db().execute(
        "SELECT * FROM ai_leads ORDER BY created_at DESC LIMIT ?",
        (int(limit),),
    ).fetchall()
    return [dict(r) for r in rows]


def set_lead_status(lead_id: int, status: str) -> None:
    if status not in ("new", "followed", "invalid"):
        raise ValueError("非法状态")
    conn = database.get_db()
    conn.execute("UPDATE ai_leads SET status=? WHERE id=?", (status, lead_id))
    conn.commit()
