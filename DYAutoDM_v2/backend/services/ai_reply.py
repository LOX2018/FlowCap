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
    # 2026-09-28（D5 / ADR-025）：默认开启。收益实测明确 —— pro_kb RAG 选条由
    # 「时间衰减前5（混无关）」变为「语义 TopK（全相关）」，且 prompt 3261→1303 字。
    # 注意：这里开的只是**知识库 RAG 语义**；命中库语义另有独立开关 reply_sem_enabled
    # （默认关，因命中即回绕过护栏，错选=张冠李戴）。embedding 不可用时各级自动降级。
    "sem_enabled": True,
    "sem_base_url": "http://127.0.0.1:31415/v1",
    "sem_api_key": "",
    "sem_model": "nvidia/nemotron-3-embed-1b",   # 旧单配置路径的兜底名；实际以 ai_sem 链为准（2026-09-28 实采链模型 = llama-nemotron-embed-vl-1b-v2，2048 维）
    "sem_threshold": 0.40,                        # 实测：同义聚簇 0.44~0.59，跨意图 <0.21
    # 2026-09-28（D5）：命中库（reply_kb）语义级**独立开关 + 严格阈值**。
    # 为何不用 sem_enabled/en_threshold：命中库是「命中即直接回复、绕过一切护栏」，
    # 风险等级远高于知识库 RAG（后者错选只是噪声）。实采阈值 0.40 会张冠李戴
    # （陈旧性骨折的问题 -> 「工伤认定书还能销毁吗」@0.502）⇒ 默认关闭，开启用 0.78。
    "reply_sem_enabled": False,
    "reply_sem_threshold": 0.78,
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
    # 2026-09-29（用户拍板「话术一定要专业」）：兜底池由「拖延型」改为**专业承接型**。
    # 原四条（"嗯嗯稍等哈，我问下马上回你" 等）全是拖延话术、零专业信息，
    # 而它恰在**AI 被护栏拦下 / 模型不可用时**发给真实客户 —— 最需要专业度的
    # 时刻，客户收到的却是最像小白的话（实测用户原话「显得像一个小白」）。
    # 新池判据（三条同时满足）：① 表明顾问身份，不平庸寒暄；
    #   ② **直接对接咨询意图**，引导客户补充可判定的关键信息（部位/诊断/城市/劳动合同）；
    #   ③ 给客户一个明确预期，而不是空泛的"稍等"。
    # 2026-09-30（用户实测反馈）：兜底话术按**首触三段结构**（表明身份 / 结合需求 /
    # 留资钩子）重写，且**去掉「大概几级」**——首触没有下等级结论的余地，
    # 属错误基准。这里只是**默认模板**，Agent 的 fallback_pool 可整组覆盖。
    "fallback_pool": [
        "我是{merchant}的顾问。请补充你的具体情况和相关资料，"
        "我帮你先判断一下；算好清单留个手机号发你。",
        "您的情况需要看具体材料才能给准话。方便说下具体情况和所在城市吗？"
        "有合同或工资记录的话，判断会顺利很多。",
        "收到。判断要看三点：事实经过、相关证据、诊断材料。"
        "您先说下具体情况和现在有没有在处理，我帮您对照一下。",
        "理解您的情况。请您说下：具体什么情况、什么时候的事、单位有没有买社保，"
        "我按相关标准帮您估一下。",
    ],
    "fallback_image": "图片收到，我看下材料再给您准话。方便的话补充说明下"
                      "具体情况和所在城市，判断会更快。",
    # ---- 2026-09-29（用户拍板「留资是唯一目的」）----
    # 「客户没有明确问题」时的**专用留资话术**。为什么必须与主兜底池分开：
    # 主池 4 条全是「请您说下部位/材料」型（引导补信息），**不含任何索要动作** ——
    # 客户只回「好 / 嗯 / 没下来」时用主池 = 白放走一次留资机会。
    # 用户原话：「如果是这种对话对象，他的聊天内容没有明确目标的人群直接引导留资就行，
    # 别说什么之后再联系，抖音是快平台…根本没有沉淀的必要」。
    "lead_first_reply": (
        "你这个情况我得按你当地标准细算才能给准数——你留个手机号，"
        "我算好把清单发你，跟公司谈心里也有底。"
    ),
    # A-1（2026-09-29）：已达 max_lead_ask 上限、且原句含「放走语」时的**
    # 不含索要的引导话术**（保证不再索要的同时也不把线索放走）。
    "lead_no_ask_reply": (
        "你这个伤情能不能评级、大概几级，得看诊断报告上的描述和治疗后的"
        "功能恢复情况，最终以劳动能力鉴定的结论为准。"
    ),
    "forbidden_words": [
        "微信", "vx", "VX", "weixin",  # 站外引流敏感词（"加我"单字误杀率高，已用 prompt 铁律约束）
        "作为一个AI", "作为一个ai",
    ],
    "max_reply_len": 60,         # 超长截第一句
    # 2026-09-28（D4）：最短回复长度（残句防线）—— 实测推理超时降级后
    # deepseek-v4.1-flash 只回「能认」两字且 finish_reason≠length，
    # 截断检测抓不到 ⇒ 2 字残句直发客户。低于此值判残句走兜底。
    "min_reply_len": 5,
    # ---- 2026-09-29（用户实测反馈驱动）----
    # 直播弹幕首触是否继续走「命中库」（reply_kb.find_match）——
    # **默认 False（停用）**。理由：命中库是「命中即直接回复、零 token、
    # 绕过全部出口护栏」的最高优先级短路分支，而它当前 28 条条目
    # **全部来自自动学习**（碎片化、含个案裸答），实测已产出
    # 「保守治疗 → 有等级，工伤10级」这类**无依据的等级直答**；
    # 用户 2026-09-29 拍板：「直播首触不再查命中库，改为 AI 生成 + 引导型兜底」，
    # 同时保留开关以便随时恢复（私信侧不受本开关影响，另由
    # strict_level / reply_sem_enabled 控制）。
    "live_reply_kb_enabled": False,
}

# 直播首触专用规则（2026-09-29，用户拍板）
#
# 为什么必须单独拼一段、而不是改 Agent 的 system_prompt：
#   直播与私信共用同一个 Agent prompt，而该 prompt 第 ① 条原文要求
#   「拿到伤情就给判断：…给出等级的初步口径与依据」——**方向与本规则相反**。
#   在弹幕首触场景里，观众只发了一句话（常常连部位/城市都没有），
#   此时给等级 = 无依据断言，实测已产出多起错答（用户原话：
#   「部分弹幕已经说了骨折，生成回复还让别人说伤情」「『鼻骨骨折鼻中隔骨折，
#   保守治疗』明显是问等级，生成的却是赔偿的内容」）。
#   故此处以**显式覆盖**的方式给出场景契约（含「覆盖上文任何相反要求」），
#   不动私信场景的既有行为。
_LIVE_CONTACT_RULES = """

【场景 · 直播间弹幕首触 —— 最高优先级，覆盖上文任何相反要求】
你在给「刚在公屏发过一句话」的直播观众发第一条私信，不是会话内回复。
1. **绝对不要报等级 / 评级结论 / 赔偿金额**。公屏一句话判断不了等级。
   凡涉及「几级 / 能不能评上 / 赔多少」：一律**先引导补充可判定材料**。
   需要补哪些材料、按什么口径判定，**一律以本 Agent 的 system_prompt 与
   知识库为准** —— 不得自行发明清单，也不得假设某个项目是必要条件。
2. **不要把话说成"有等级 / 没等级"这种二选一直答**。先问、先引导，
   不要替对方下结论（对方的信息量还不支持任何结论）。
3. 🔴 **禁止把某一项材料/手术/检查/地区规定，当作「有没有结果」的通用门槛**。
   这类前置条件通常只适用于**部分情形或部分地区**，把它说成普适门槛即是错答
   （例如「必须有某项手术/某项材料才有等级」这类句式）。
   判定依据**唯一来源 = 本 Agent 的 system_prompt 与知识库**；Agent 没说的，
   就是「不确定」，只能引导补充材料，不能当作结论讲出来。
   允许给的只有**方向性口径**（是否为该情形的构成要件，取决于具体条款与
   属地规定，以主管部门最终结论为准），并紧跟一句「你把 X 说一下，我帮你对一下」，
   其中 X 取自 Agent 自己要求的判定材料。
4. 引导式追问**一次只问 1~2 个关键项**，不要一口气把所有问题都问完。
5. 观众已经说过的信息**不要重复问**。观众没说清楚部位/城市时，
   就只问这一个。
6. **首触三段结构（顺序固定）**：
   ① **表明身份** —— 用本 Agent 设定的身份/商家名让陌生人知道你是谁；
   ② **结合他的需求** —— 紧扣他公屏说的那句话回应，不要答非所问；
   ③ **留资钩子** —— 给出**具体价值**并要一个联系方式（具体内容见本 Agent
      的留资设置），不要空泛的「之后联系」。
7. 第 6 条与第 1 条**不冲突**：先引导补充材料 ≠ 只问不推进 ——
   引导之后**同一轮必须收口到留资钩子**。
"""

# 直播兜底池追加位（2026-09-29）：主兜底池偏「会话内承接」，缺一条
# **纯引导型**话术；直播首触被护栏拦下/模型不可用时，最需要的是
# 「把对方拉进可判定的对话」而不是承接上文的措辞。
#
# A-4（OCR[8]，2026-09-29）：原实现为 `_LIVE_FALLBACK_EXTRA
# or fallback_reply(cfg)`，而 `_LIVE_FALLBACK_EXTRA` 是**恒非空的模块级常量**
# ⇒ `fallback_reply(cfg)` 分支**永不执行**（死代码），docstring 承诺的
# 「额外池不可用时回退主池」也**永不发生**。
# 修法（选 b，语义对齐 docstring）：把「额外池」提升为**配置项**
# `live_fallback_extra`（本常量仅作其默认值）—— 配置留空 ⇒ 真正回退主兜底池。
# 🔴 2026-09-30（用户实测反馈，架构层校正）：原文案把「有没有做内固定」写死成
# 评级前置条件 —— **基准错误**（该前置只适用于部分条款/部分地区），且把领域知识
# 硬编码进了**通用引擎**。本项目是通用采集/私信引擎，领域规则属 **Agent 配置**
# （system_prompt / 知识库 / 兜底池，用户可改），引擎层只保留**与领域无关的结构**。
# 故这里改为：结构固定（身份 + 结合需求 + 留资钩子），**句子内容取 Agent 配置**；
# 未配置时用通用问法，不替任何行业断言判定条件。
_LIVE_FALLBACK_EXTRA = (
    "我是{merchant}的顾问。你刚才说的这个情况，我需要看一下你的具体资料才能"
    "给准话——你把相关资料说一下，我先帮你对一遍，算好留个手机号我发你。"
)

# ---------------------------------------------------------------------------
# 留资铁律（2026-09-29，用户拍板；**会话内回复专用，覆盖式追加**）
#
# 用户原话：「要知道私信的目的就是留资，这个对方的聊天内容如果是有明确的问题
#   则先针对问题回复体现专业性同时引导留资，但如果是这种对话对象，他的聊天内容
#   没有明确目标的人群直接引导留资就行，别说什么之后再联系，抖音是快平台，
#   客户前一秒还在你这下一秒就去别人那了，根本没有沉淀的必要。」
#
# **为什么不能改 Agent 的 system_prompt**（与 _LIVE_CONTACT_RULES 同一条纪律）：
#   Agent prompt 是**用户资产**（用户可自定义），且实测其漏斗是
#   「先判断 → 再算清单 → **最后**才要联系方式」——用户要的是**更早要**。
#   把这条诉求写进用户资产里 = 越权改动用户的配置；写在系统层覆盖式追加，
#   才能保证「无论用户怎么写 prompt，留资铁律都成立」。
#
# 该段的判据来自实测事故：会话「668」中客户回「好」之后，
#   AI 回了「好，问问进度。认定书下来第一时间通知我，我帮你算清赔偿清单。」
#   + 「我等你人社局问完的消息」= **把客户放走**（等对方回头 = 抖音平台上等于丢单）。
# ---------------------------------------------------------------------------
_LEAD_DISPOSITION_RULES = """

【留资铁律 —— 最高优先级，覆盖上文一切相反要求】
这个账号做私信的唯一目的就是**留资（拿到手机号）**。抖音是快平台：
客户前一秒在你这里，下一秒就去别人那里 —— **没有"之后再联系"这回事**。

1. **有明确问题**（问结果/赔偿/流程/材料/认定）：先**正面答到点子上**（体现专业性，
   2~3 句，紧扣他的问题），然后**同一轮**收口问手机号。
   不要等"判断清楚"再要 —— 每轮都要往前推一步。
2. **没有明确问题**（只回"好/嗯/没下来/在忙/谢谢"这类，或与业务无关的闲聊）：
   **不要再问细节、不要再寒暄、不要再等**。直接用一句话把留资递出去，
   例如「你这个情况得按你当地标准细算，你留个手机号，算好我发你」。
3. **【机械禁止】任何"以后再说"式收尾** —— 不许出现：
   · 等对方回头的表述（「等你消息」「你问完告诉我」「下来了通知我」「后面再说」）；
   · 单方面结束对话的表述（「好」「好的」「了解了」「知道了」结尾，后面没有索要）；
   · 把留资推后（「看完材料再说」「确认完再联系」）。
   每一轮回复的最后一步只能是：**给出价值 + 要联系方式**。
4. 客户已经留过手机号 → 只回确认，不再索要。
5. 一次只索要手机号；绝不说"加微信"、绝不提其他平台。
"""


def _contains_personal_contact(text: str) -> bool:
    """文本里是否已含手机号/微信号（决定是否还要继续索要）。"""
    try:
        return bool(extract_contacts(text or ""))
    except Exception:
        return False

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

# 2026-09-30（架构层校正）：原模板写死「工伤理赔顾问（唐律工伤团队）」及
# 工伤专属三要素/等级/赔偿话术 —— 本项目是**通用**采集/私信引擎，不得内置
# 任何行业实体。改为**领域中立**的通用获客模板：结构（身份→解答→留资）保留，
# 具体行业知识由 Agent 的 system_prompt / 知识库提供。
_DEFAULT_AGENT_PROMPT = """你是「{merchant}」的资深顾问，在抖音私信中接待咨询的客户。

身份与语气（**专业是第一位**）：
- 你是**专业顾问**，不是客服机器人：说话有依据、有判断，不做无意义的寒暄。
- 用**客户听得懂的话**讲专业结论，专业术语后紧跟一句人话解释。
- 直接、利落。**禁止**"嗯嗯""哈""哦哦""收到收到"这类口语填充词。
- **禁止**用"稍等/我问下/我确认一下"当回复内容 —— 除非确实需要用户补充信息，
  此时必须**明确说要补什么**。

工作方式（分阶段推进）：
1. 解答阶段：只依据《资料》回答。**先给结论，再给依据，最后给下一步该做什么**，先建立信任。
   客户描述不完整时，主动问清关键要素（**以本 Agent 的知识库为准**，不自行发明清单）。
2. 意向阶段：客户表现出兴趣（问结果/赔偿/流程/怎么办理）→ 自然推进留资。
3. 留资阶段：用自然话术索要手机号，例如"这边给您算个准确数字，你手机号多少？我备注一下"。已被拒绝 {max_ask} 次就不再索要，安心解答。
4. 留资成功（客户已发号码）：只回确认话术，不再多说。

铁律：
- 《资料》里没有的信息一律不许编。不确定时**说清楚不确定的是哪一点**，
  并给出可核实的路径，**不得**用空话搪塞。
- 绝不说"加微信"、绝不提其他平台；只索要手机号。
- 回复 15-100 字（**够讲清一个专业判断**），一次只回一句主题，不排队比句式。
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
    # 2026-09-30（架构层校正）：原兜底写死「唐律工伤团队」—— 本项目是**通用**
    # 采集/私信引擎，不得内置任何具体商家名或行业实体。未配置商家名时用中性称谓
    # （占位替换仍需成立，否则 prompt 里会残留 {merchant} 字面量）。
    merchant = (cfg.get("merchant_name") or "").strip() or "本团队"
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
    # 2026-09-29（用户拍板「私信的目的就是留资」）：追加**留资铁律**。
    # 放在函数**最末**（紧贴 user 消息，权重最高），且显式声明「覆盖上文一切
    # 相反要求」——因为 Agent prompt 的漏斗是「最后才要联系方式」，
    # 而用户要的是「每轮都要往前推一步」。见 _LEAD_DISPOSITION_RULES 注释。
    return base + _LEAD_DISPOSITION_RULES


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
# 直播场景专用护栏（2026-09-29，用户实测反馈驱动）
#
# 实测事故（用户原话）：「部分弹幕已经说了骨折，生成回复还让别人说伤情」
# 「『鼻骨骨折鼻中隔骨折，保守治疗』明显是问等级，生成的却是赔偿的内容」
# 「对于弹幕的内容不要直接说有等级/没等级，直接让发病例，先引导对话」。
#
# 与下面的 _RE_ASSERTIVE 的区别（两者不可互相替代）：
#   · _RE_ASSERTIVE（会话内专业准确性护栏）只拦「陈旧性骨折能认」这类
#     **与专业知识相悖**的断言，允许「十级有依据」这种有出处的口径；
#   · 本组拦的是**场景违规** —— 直播间首触（对方只发了一句话）就下结论，
#     无论结论对错都不该发：判据是**无依据断言**本身，不是结论是否正确。
#   ⇒ 故只在 generate_dm_for_live 的出口启用（live_guard=True），
#     私信会话内回复**不受影响**（那里已经有上下文，允许给判断）。
# ---------------------------------------------------------------------------

# ① 具体等级结论：**任何**「N 级」出现即拦。
#    为什么放宽到「不论句式」：用户原话「不要直接说有等级/没等级」——
#    直播间首触（对方只发了一句话）**根本没有**下等级结论的依据，
#    所以判据不是「说得对不对」，而是「该不该在这个场景出现」。
#    首触唯一合法口径是「能不能评要看 X，具体以鉴定结论为准」（无数字级）。
_RE_LIVE_GRADE = re.compile(r"[一二三四五六七八九十0-9]+\s*级")

# ② 等级结论（无数字级）：有/无/评上/评不上… + 等级
#    实测漏网样本：「轻微骨裂大概率**评不上等级**」——原实现只匹配
#    「有/没有/无/算」，未覆盖「评不上」，故被判为放行（负控当场抓出）。
_RE_LIVE_YESNO = re.compile(
    r"(?:有|没有|无|不算|算|评上|评不上|够上|够不上|达到|达不到|够到|够不到)"
    r"[^。！？!?~\n]{0,3}等级"
)

# ③ 赔偿金额 / 清单承诺（直播间首触不该出现，对方信息量不支持算数）
_RE_LIVE_MONEY = re.compile(
    r"(?:个月本人工资|赔偿清单|赔多少钱|赔多少|能赔[0-9]|[0-9]+\s*万)"
)


def _live_guard_violation(text: str) -> Optional[str]:
    """直播首触出口护栏：命中返回原因串（调用方改发引导型兜底）。"""
    s = text or ""
    for pat, label in ((_RE_LIVE_GRADE, "等级断言"),
                       (_RE_LIVE_YESNO, "有/无等级二选一直答"),
                       (_RE_LIVE_MONEY, "赔偿金额或清单承诺")):
        m = pat.search(s)
        if m:
            return f"{label}「{m.group(0)}」"
    return None


# 直播首触「表明身份」的**结构**补齐（2026-09-30，领域中立版）：
# 只保证「有身份」这一结构要件，**不写死任何行业/团队名**——身份内容由 Agent 的
# merchant_name / system_prompt 决定。幂等：已含身份词则原样返回。
_LIVE_BRAND_RE = re.compile(r"(顾问|律师|法务|助理|团队|老师|专家)")


def ensure_live_brand(text: str, cfg: dict | None = None) -> str:
    """补齐「表明身份」（幂等）：已含身份词则原样返回；空串返回空串。

    cfg 为 None 时取全局配置（`get_config()`），保证独立调用也能拿到商家名。
    """
    s = (text or "").strip()
    if not s or _LIVE_BRAND_RE.search(s):
        return s
    _cfg = cfg if cfg is not None else get_config()
    _m = (_cfg.get("merchant_name") or "").strip() or "顾问"
    return f"我是{_m}的顾问。" + s


def live_fallback_reply(cfg: dict) -> str:
    """直播首触专用兜底：**引导型**（不作结论、只把对方拉进可判定对话）。

    与 fallback_reply 的区别：主兜底池是为「会话内承接」写的（含「请您说下
    哪里受的伤」这类措辞），在直播间首触场景里读起来像客服话术；本函数优先
    取配置的纯引导话术，取不到才回落主池 —— 保证**任何情况下都不会无话可说**。

    A-4（OCR[8]，2026-09-29）：额外池取 `cfg["live_fallback_extra"]`，缺省回落到
    模块默认 `_LIVE_FALLBACK_EXTRA`；仅当**两者都为空**时才走主池 fallback
    —— 使 docstring 承诺的「额外池不可用 ⇒ 回退主池」成为**可达路径**
    （原实现拿恒非空常量做短路，回退分支是死代码）。
    """
    extra = cfg.get("live_fallback_extra", _LIVE_FALLBACK_EXTRA)
    # 2026-09-30：默认模板含 {merchant} 占位，须与 build_system_prompt 同口径替换；
    # 否则外发文案会带上未替换的占位符（用户会看到 `{merchant}` 字面量）。
    _m = (cfg.get("merchant_name") or "").strip() or "本团队"
    extra = str(extra or "").replace("{merchant}", _m).strip()
    return extra or str(fallback_reply(cfg) or "").strip()


# ---------------------------------------------------------------------------
# 留资护栏（2026-09-29，用户拍板「私信的目的就是留资」）
#
# 判据来自实测事故（会话「668」）：客户回「好」之后，AI 发出
#   ·「好，问问进度。认定书下来第一时间通知我，我帮你算清赔偿清单。」
#   ·「我等你人社局问完的消息」
# ⇒ 这是**把客户放走**（等对方回头）；在抖音上等于丢单。用户原话：
#   「别说什么之后再联系…根本没有沉淀的必要」。
#
# 本护栏只管**一句话形态**：这轮回复若**既不含索要、也不含明确问题**，
# 就是「对话沉降」→ 换引导留资话术。它**不要求每句都索要** ——
# 「我帮你对一下，你在哪个省受的伤？」是在推进（问明确问题），合格。
# ---------------------------------------------------------------------------

# 轮次分类关键词（保守：宁愿判「模糊」而给留资话术，也不误伤专业问答）。
# 🔴 判据演进（两轮实机验证驱动，务必按此理解）：
#   初版用「无索要**且**无提问 ⇒ 沉降」，实机打回两处：
#     ① 客户说「我下午去人社局问问」（**没明确问题**）→ 模型回专业清单但没索要，
#        因为句中出现「有没有提交…材料」这种**解释性**疑问词，被误判成"在提问"⇒ 漏判；
#     ② 客户问「我能评几级」（**有明确问题**）→ 模型给专业口径但没索要，
#        若直接换成通用留资话术 ⇒ **专业性丢失**（与用户"先针对问题回复体现专业性"相悖）。
#   ⇒ 现判据 = **「不含索要就是没推进」**（与用户第 3 条机械规则字面一致）；
#      处理不再"整句替换"，而是**先定向重试**（保留专业回答 + 末尾补索要），
#      重试仍不合格才退回引导留资话术。且受 `max_lead_ask` 上限约束（防刷屏）。
_LEAD_ASK_RES = [
    # 明确索取联系方式（私信里提这些词≈在要号）
    re.compile(r"(手机号|手机号码|电话号码|电话|号码|联系方式|联系我|加微|加个微|微信|vx)"),
    re.compile(r"(留个|留一下|发个|给我个)"),
]


def _has_lead_ask(reply: str) -> bool:
    """回复里是否**明确索要了联系方式**（唯一"在推进留资"的判据）。"""
    s = (reply or "").strip()
    return bool(s) and any(p.search(s) for p in _LEAD_ASK_RES)


def _is_lead_stalled(reply: str) -> bool:
    """这轮回复是否**没在推进留资**（= 不含索要）。保留旧名供既有调用/测试引用。"""
    s = (reply or "").strip()
    if not s:
        return True
    return not _has_lead_ask(s)


# A-3（OCR[10]，2026-09-29）：`_KV_ASK_COUNT` 是**共享 KV 上的读-改-写**
# （`_kv_get` → 改 dict → `_kv_set`），而 `_tick` 经 `reply_concurrency` 线程池
# （默认 8）**跨会话并发** ⇒ 无锁时两线程读同一旧 dict、各自 +1 再写回 =
# last-write-wins，**丢计数**（`max_lead_ask` 上限随之失效）。
# 复用本模块既有锁原语（与 `_IMG_DESC_LOCK` 同款 `threading.Lock`），不新造机制。
_LEAD_ASK_LOCK = threading.Lock()


def _lead_ask_count(account: str, conv_id: str) -> int:
    """本会话已索要次数（`max_lead_ask` 上限用；键与既有 _KV_ASK_COUNT 一致）。"""
    try:
        with _LEAD_ASK_LOCK:
            d = _kv_get(_KV_ASK_COUNT, {}) or {}
            return int(d.get(f"{account}:{conv_id}", 0) or 0)
    except Exception:
        return 0


def _bump_lead_ask(account: str, conv_id: str) -> int:
    """本会话索要次数 +1（A-3：读-改-写原子化，防并发丢计数）。"""
    try:
        with _LEAD_ASK_LOCK:
            d = _kv_get(_KV_ASK_COUNT, {}) or {}
            k = f"{account}:{conv_id}"
            n = int(d.get(k, 0) or 0) + 1
            d[k] = n
            _kv_set(_KV_ASK_COUNT, d)
            return n
    except Exception:
        return 0


def lead_fallback_reply(cfg: dict) -> str:
    """「客户无明确问题」时的专用留资话术（配置可覆盖）。"""
    return str(cfg.get("lead_first_reply")
               or _DEFAULT_CONFIG["lead_first_reply"]).strip()


def _lead_no_ask_reply(cfg: dict) -> str:
    """已达 `max_lead_ask` 上限、且原句含「放走语」时的**不含索要**引导话术。

    A-1（2026-09-29）：上限生效后不能再追加索要，但也不能让「等你消息」这类
    放走语外发；本函数给出「专业引导 + 不再索要」的收口（配置可覆盖）。
    """
    return str(cfg.get("lead_no_ask_reply")
               or _DEFAULT_CONFIG["lead_no_ask_reply"]).strip()


# 「把线索放走」的形态（出现这些 ⇒ **不能**只在末尾补索要，
# 必须整句替换 —— 否则「等你消息…方便留个手机号」自相矛盾）。
# 🔴 判据必须锚「人称」：`等认定/等结果` 是**陈述流程**（合法专业回答，
# 实测「接下来等认定结果」被误判成放走语而丢掉专业内容）⇒ 不算放走；
# 只有「等**你的**消息 / 通知**我** / 回头再说」才是把线索放走。
_LEAD_DEFER_RES = [
    re.compile(r"等[你您]{1,2}[^。！？!?~\n]{0,8}(消息|回复|结果|答复|认定|通知)"),
    re.compile(r"等[我咱][^。！？!?~\n]{0,6}(消息|回复|结果|答复)"),
    re.compile(r"(第一时间|到时候|下来)[^。！？!?~\n]{0,6}(通知|告诉)"),
    re.compile(r"(以后|后面|回头|改天)[^。！？!?~\n]{0,4}(再说|联系|聊|找|看)"),
    re.compile(r"(有消息|有结果|有情况|问完|问清|问问)"
               r"[^。！？!?~\n]{0,4}再说"),
    re.compile(r"(再说|再聊|再联系|再回你|再找你)[吧。！~]"),
    re.compile(r"(再联系你|再回你|再找你|稍后联系你)"),
]

# 追加用（短，便于塞进 max_reply_len）
_LEAD_ASK_TAIL = "方便留个手机号，我按你当地标准算份清单发你。"
#: 追加后允许的硬上限（比 max_reply_len 宽松：`max_reply_len` 是给**模型输出**
#: 的截断线，而这里是把专业回答保下来 + 尾接一句索要，略长优于丢掉专业性）。
_LEAD_ASK_HARD_CAP = 120


def _is_deferring(reply: str) -> bool:
    """回复是否包含「把线索放走」的表述（等对方回头/推后处理）。"""
    s = (reply or "").strip()
    return bool(s) and any(p.search(s) for p in _LEAD_DEFER_RES)


def append_lead_ask(reply: str, cfg: dict) -> Optional[str]:
    """把「要联系方式」追加到专业回答末尾（**保留专业性**的首选处置）。

    返回 None 表示「不该追加」（原句含放走语）——调用方据此退回
    `lead_fallback_reply`。

    A-2（OCR[9]，2026-09-29）：追加后超 `_LEAD_ASK_HARD_CAP` 时**不再整句丢弃**。
    旧实现直接 `return None` ⇒ 调用方整句替换为通用引导话术，
    把库内/模型给出的**专业正文**（>~99 字）一起丢掉，与「保专业性优先」相悖。
    现改为：**截断专业正文**后再补索要（优先在句末标点处断，保留关键片段）；
    确无正文可留时才返回 None。
    """
    s = (reply or "").strip()
    if not s or _is_deferring(s):
        return None
    if _has_lead_ask(s):
        return s
    tail = _LEAD_ASK_TAIL

    def _join(body: str) -> str:
        sep = "" if body.endswith(("。", "！", "？", "?", "!", "~")) else "。"
        return f"{body}{sep}{tail}"

    combined = _join(s)
    if len(combined) <= _LEAD_ASK_HARD_CAP:
        return combined
    # 超长：保专业正文，把正文截到「正文(+句号) + tail <= 硬上限」。
    room = _LEAD_ASK_HARD_CAP - len(tail) - 1     # 预留一个句末标点
    if room <= 0:
        return None
    body = s[:room]
    # 优先在句末标点处断句（不把话截半），且截断后仍需“像一句话”。
    cut = max(body.rfind("。"), body.rfind("；"), body.rfind("！"),
              body.rfind("？"), body.rfind("."), body.rfind(";"))
    if cut >= int(room * 0.6):
        body = body[:cut]
    body = body.strip().rstrip("，,、；;")
    if not body:
        return None
    out = _join(body)
    return out if len(out) <= _LEAD_ASK_HARD_CAP else None


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

def validate_reply(reply: str, cfg: dict, live_guard: bool = False) -> Optional[str]:
    """校验 AI 文本；不通过返回 None（调用方改发兜底话术）。

    2026-09-28（D4 内容层）新增**最小长度护栏**（残句防线）：
    实测 `deepseek-v4.1-flash` 在推理超时降级后只回了「能认」两字 ——
    该情形 `finish_reason` **不是** `length`（截断检测 `_extract_reply` 抓不到），
    于是 2 字残句被当正常回复**直发客户**（客户视角＝敷衍/不专业）。
    低于 `min_reply_len`（默认 5，与 prompt「5-40 字」口径一致）判残句 → 走兜底。

    2026-09-29：新增 `live_guard` 开关 —— 直播弹幕首触场景**额外**过
    场景护栏（禁等级断言 / 禁「有·无等级」直答 / 禁赔偿金额承诺）。
    默认 False ⇒ 私信会话内回复行为**逐字不变**（零回归）。
    """
    if not reply or not reply.strip():
        return None
    reply = reply.strip().strip('"“”')
    # 2026-09-29：**剔掉 Markdown 强调符**。实测事故：AI 生成
    # 「轻微骨裂**大概率评不上等级**，除非影响手指功能」—— 私信是纯文本，
    # `**` 会**原样显示给客户**（截图可见），既不专业也像机器产物。
    # 只处理成对强调符；单独一个 `*` 不动（可能是有意使用）。
    if "**" in reply:
        reply = reply.replace("**", "")
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
    # 2026-09-29（直播首触）：**场景护栏** —— 与下面的「专业准确性护栏」是
    # 两件不同的事（前者管「在什么场景不该下结论」，后者管「结论对不对」），
    # 只在 live_guard=True 时生效，私信会话内回复零影响。
    if live_guard:
        _lg = _live_guard_violation(reply)
        if _lg:
            logger.info(f"[AI-069] 直播首触护栏拦截（{_lg}）-> 走引导型兜底: "
                        f"{reply[:40]}")
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
            # 2026-09-29：错误码由 AI-066 改为 AI-068 —— 原值与本轮新增的
            # 「本机账号互回拦截」(AI-066) 冲突（同码两义，破坏可检索性）。
            logger.info(f"[AI-068] 护栏拦截（截断后疑似残句「{reply}」"
                        f"<{min_len} 字）-> 走兜底")
            return None
    return reply


def fallback_reply(cfg: dict, kind: str = "text") -> str:
    # 2026-09-30：默认模板里用 {merchant} 占位，取配置商家名替换；
    # 未配置则回落到中性词，**不硬编码任何行业/团队名**（通用引擎铁律）。
    _m = (cfg.get("merchant_name") or "").strip() or "顾问"
    _pool = [str(x).replace("{merchant}", _m) for x in (cfg.get("fallback_pool") or [])]
    pool = _pool
    if kind == "image":
        return cfg.get("fallback_image",
                       "图片收到，我看下材料再给您准话。方便的话补充说明下"
                       "受伤部位和所在城市，判断会更快。")
    # 2026-09-29：末位兜底同步改专业（原「稍等哈，我看下」属拖延型，
    # 与本轮「话术必须专业」的拍板相悖）。
    # 2026-09-30：末位默认话术同步三段结构（身份 / 结合需求 / 留资钩子）；
    # 身份取配置的商家名，未配置时用中性称谓（不替任何行业断言）。
    return random.choice(pool) if pool else (
        f"我是{(cfg.get('merchant_name') or '顾问').strip()}的顾问。"
        "请补充你的具体情况和相关资料，我帮你先判断一下；"
        "算好清单留个手机号发你。")


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


def _own_account_uids() -> set:
    """本机全部账号自身的 uid 集合（用于识别「对端是本机另一个账号」）。

    **为什么需要**（2026-09-29 实测）：同机多账号是本项目常规部署形态
    （日志可见同时拉起两个 BCC）。当账号 A 的 AI 回复落库、经对端守护同步后，
    在账号 B 视角下会成为 `role=them` 的**正常客户消息** ⇒ B 的 AI 继续回复
    ⇒ **两个 AI 无限互回**。实测铁证（conv 0:1:316276709526638:3887506227210423，
    小助理 ↔ 张老师互为对端）：
        id=13777 张老师 role=me   「嗯嗯稍等哈，我问下马上回你」
        id=13779 小助理 role=them 「嗯嗯稍等哈，我问下马上回你」
        id=13790 小助理 role=me   「您好，我是唐律工伤团队的理赔顾问」
        id=13792 张老师 role=them 「您好，我是唐律工伤团队的理赔顾问」

    现有护栏（黑名单 / UID 沉淀池）**都不识别**这一形态 ⇒ 必然触发。

    复用既有能力（不自造）：`auto_dm.accounts.list_accounts()` 枚举本机账号，
    `services.conv_identity.my_uid()` 取各账号 uid（零网络、会话池统计推断）。
    取不到证据（新账号无会话）⇒ 该账号不参与判定（保守放行，不误伤）。
    """
    out = set()
    try:
        from auto_dm.accounts import list_accounts
        from services.conv_identity import my_uid as _my_uid

        for name, _env in (list_accounts() or []):
            try:
                u = str(_my_uid(str(name)) or "").strip()
                if u:
                    out.add(u)
            except Exception:
                continue
    except Exception:
        return set()
    return out


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
        #
        # 🔴 2026-09-29 修复（AI-067 · 用户报障「AI 回复了历史消息」）
        # 原实现：
        #     if int(_kv_get(_KV_MARKER, 0) or 0) == 0:      # ← 仅当为 0 才打
        #         _kv_set(_KV_MARKER, row[0] if row else 0)
        # 缺陷（实测后果）：注释承诺「**启动时刻**打水位」，实现却是「只在 kv
        # 为 0 时才打」。只要库里留有上次运行的**非 0 旧水位**，本次启动就沿用
        # 它 ⇒ `id > 旧水位` 把关停期间累积的**全部历史消息**捞出并逐条回复。
        # 实机铁证（ai_probe.log，2026-09-29 10:19~10:22）：进决策链的 17 条
        # trace **全部**是 09-25~09-28 的历史消息（13598=09-28 18:46 … 13797），
        # 间隔整 20s（= POLL_INTERVAL）⇒「绝不回历史」契约被完全违反。
        #
        # 正解：**每次启动都无条件把水位推进到当刻 MAX(id)** —— 这才是注释
        # 本意（"启动时刻打水位"）。历史消息不属于"开启之后的新消息"，
        # 任何情况下都不该捞。旧值只在**同一进程运行期内**才有意义。
        conn = database.get_db()
        row = conn.execute("SELECT COALESCE(MAX(id),0) FROM dm_messages").fetchone()
        _boot_id = int((row[0] if row else 0) or 0)
        _prev = int(_kv_get(_KV_MARKER, 0) or 0)
        _kv_set(_KV_MARKER, _boot_id)
        if _prev != _boot_id:
            logger.info(f"[AI-067] [ai] 启动水位已推进 {_prev} → {_boot_id}"
                        f"（只回开启之后的新消息；关停期间累积的历史一律不捞）")
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

        # ══════════ 2026-09-29【本机账号互回护栏】(AI-066) ═══════════════════
        # 对端若本机**另一个账号**的 uid ⇒ 跳过回复（拦截 + 告警）。
        # 为什么必须拦（实测）：同机多账号是常规部署形态，账号 A 的 AI 回复
        # 落库后经对端守护同步，在账号 B 视角成为 role=them 的正常客户消息
        # ⇒ B 的 AI 继续回复 ⇒ **两个 AI 无限互回**（无上限自激）。
        # 实测铁证见 `_own_account_uids` docstring（conv 0:1:316276709638:...）。
        # 危害：自激循环 + 消耗真实发送配额 + 污染真实客户会话 +
        #       在抖音侧形成两账号间异常高频往返（风控面）。
        # 判据来源：本机账号 uid 集合（conv_identity 推断，零网络）。
        # 取不到证据时（集合为空/peer_id 缺失）⇒ 保守放行，绝不误伤真实客户。
        try:
            _own = _own_account_uids()
            _pid = str(row["peer_id"] or "").strip()
            if _pid and _pid in _own:
                _probe(row["id"], "OWN_ACCOUNT", "blocked",
                       account=account, peer_id=_pid, peer_name=peer_name,
                       own_count=len(_own))
                logger.warning(
                    f"[AI-066] [ai] 跳过回复：对端 {peer_name}({_pid}) "
                    f"是本机**另一个账号** —— 回复会导致两个账号 AI 互相回复"
                    f"（自激循环 + 风控面）。如需对聊请人工介入。")
                return
            _probe(row["id"], "OWN_ACCOUNT", "pass",
                   account=account, peer_id=_pid, own_count=len(_own))
        except Exception as e:  # 护栏自身异常绝不影响主流程（保守放行）
            _probe(row["id"], "OWN_ACCOUNT", "error", err=str(e)[:120])

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
                # 🔴 2026-09-29（用户拍板「私信的目的就是留资」）：命中库是
                # **直接 return 的最短路**，此前**完全绕过**留资处置护栏
                # （库里就有一条 `没有保守治疗 → 有等级，工伤10级`，命中即原件外发
                #   且不含任何索要）。此处把命中文本**交给同一套留资判据**：
                # 库内话术若不推进留资，补上索要（保留库内专业内容）。
                _hit = str(hit).strip()
                if not _contains_personal_contact(text) and _is_lead_stalled(_hit):
                    _fixed = append_lead_ask(_hit, cfg)
                    if _fixed:
                        logger.info(f"[AI-070] 留资护栏：命中库话术未索要 → 追加: "
                                    f"{_hit[:24]!r} → {_fixed[:40]!r}")
                        _hit = _fixed
                    else:
                        _lead = lead_fallback_reply(cfg)
                        logger.info(f"[AI-070] 留资护栏：命中库话术未索要且不可追加"
                                    f" → 改引导留资: {_hit[:24]!r}")
                        _hit = _lead
                    _bump_lead_ask(account, conv_id)
                return _hit, "回复库"
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
            # 2026-09-29（用户拍板「私信的目的就是留资」）：**留资护栏**。
            # 判据：**不含索要 ⇒ 没推进留资**（与用户第 3 条机械规则字面一致）。
            # 处理顺序（两轮实机验证后确定，勿改回"整句替换"）：
            #   ① 未达 max_lead_ask 且还能索要 → **定向重试**：要求「保留专业判断，
            #      末尾补一句索要」，既保专业性又拿到线索；
            #   ② 重试仍不合格 → 保留专业回答 + 末尾追加索要，含放走语才换引导；
            #   ③ **已达 max_lead_ask 上限 → 不再索要、不再计次**（A-1：上限真正生效，
            #      只保留专业回答 / 换不含索要的引导）。
            if cleaned and not _contains_personal_contact(text):
                if _is_lead_stalled(cleaned):
                    _max_ask = int(cfg.get("max_lead_ask", 2) or 0)
                    _n_ask = _lead_ask_count(account, conv_id)
                    _patched = None
                    if _n_ask < _max_ask:
                        try:
                            _hint = (
                                "重要：你上一条回复**没有向客户要联系方式**。请重写："
                                "① 先保留你上一条里给客户的**专业判断**（原文要点不要丢）；"
                                "② 然后在**同一句末尾**自然补上索取手机号（给理由，"
                                "例如「我按你当地标准算份清单发你」）；"
                                "③ 不要出现「等你消息/回头再说/通知我」这类等对方的话。"
                                "总长 15-100 字，直接给可发送的成品。")
                            _raw2 = client.chat_failover(
                                text, consumer_id="ai_main",
                                user_id=f"{account}:{conv_id}",
                                system_prompt=prompt + "\n\n" + _hint,
                                history_extra=history)
                            if _raw2:
                                _c2 = validate_reply(_raw2, cfg)
                                if _c2 and not _is_lead_stalled(_c2):
                                    _patched = _c2
                                    _probe(before_id, "LEAD_RETRY", "ok",
                                           reply=_c2[:120])
                        except Exception as e:
                            _probe(before_id, "LEAD_RETRY", "error",
                                   err=str(e)[:120])
                        if _patched:
                            cleaned = _patched
                            _bump_lead_ask(account, conv_id)
                        else:
                            # 回退顺序（保专业性优先）：
                            #   ① 原句**不含放走语** ⇒ 保留专业回答 + 末尾追加索要；
                            #   ② 含放走语（追加无意义）⇒ 整句改为引导留资话术。
                            _appended = append_lead_ask(cleaned, cfg)
                            if _appended:
                                logger.info(
                                    f"[AI-070] 留资护栏：为专业回答追加索要: "
                                    f"{cleaned[:24]!r} → {_appended[:40]!r}")
                                cleaned = _appended
                            else:
                                _lead = lead_fallback_reply(cfg)
                                logger.info(
                                    f"[AI-070] 留资护栏：回复未索要/含放走语"
                                    f"→ 整句改引导留资: {cleaned[:30]!r} → {_lead[:30]!r}")
                                cleaned = _lead
                            _bump_lead_ask(account, conv_id)
                    else:
                        # 🔴 A-1（OCR[22]）修复：**`max_lead_ask` 上限真正生效**。
                        # 旧实现只有「重试分支」判了 `_n_ask < _max_ask`；达上限时
                        # `_patched` 恒为 None ⇒ 落入 else **无条件**追加索要 /
                        # 换引导留资并 `_bump_lead_ask` ⇒ 每一轮都继续索要，
                        # 与 ADR-027 D5「被拒 N 次不再索要」承诺**直接矛盾**。
                        # 现：达上限一律**不再追加索要、也不再 bump**；只保留专业回答
                        # / 引导。若原句含「放走语」（等对方回头），因其不含索要、
                        # 又不能追加，改发**不含索要的引导话术**（不再放走线索）。
                        if _is_deferring(cleaned):
                            logger.info(
                                f"[AI-071] 留资护栏：已达 max_lead_ask({_max_ask}) 上限，"
                                f"原句含放走语 → 改专业引导（不再索要/不再计次）: "
                                f"{cleaned[:30]!r}")
                            cleaned = _lead_no_ask_reply(cfg)
                        else:
                            logger.info(
                                f"[AI-071] 留资护栏：已达 max_lead_ask({_max_ask}) 上限，"
                                f"不再追加索要（保留专业回答，且不再计次）: "
                                f"{cleaned[:30]!r}")
            _probe(before_id, "GUARD", "pass" if cleaned else "blocked",
                   level=level, cleaned=(cleaned or "")[:120],
                   raw=raw[:120])
            if cleaned:
                return cleaned, "AI"
            # 2026-09-29（用户拍板「话术一定要专业」）：**短回复先重试，再兜底**。
            # 背景（实测）：AI 对简短/无意义输入常只吐「我在的」「你好」等 3 字，
            # 被 min_reply_len 判残句 → 直接换兜底池 ⇒ 客户**从来看不到 AI 的专业输出**
            # （probe 铁证：trace 13598/13599 AI_RAW='我在的'/'你好' → GUARD=blocked
            #  → FALLBACK='嗯嗯稍等哈'）。而兜底池正是最不专业的文案，等于
            # 「AI 偶尔发挥失常 ⇒ 客户收到最差话术」——最坏组合。
            # 正解：给模型**一次明确的补救机会**（要求完整、专业、含判断），
            # 仍不合格才回落兜底。重试失败不视为异常，只是多一次调用。
            _too_short = bool(raw) and len(str(raw).strip()) < int(
                cfg.get("min_reply_len", 5))
            if _too_short:
                try:
                    _retry_hint = (
                        "你上一条回复太短，无法向客户传达专业判断。"
                        "请重新给出**完整、专业、可直接发送**的回复：先给结论，"
                        "再给依据，最后告诉对方下一步该做什么；"
                        "15-100 字，不要寒暄，不要用「稍等/我问下」。")
                    raw2 = client.chat_failover(
                        text, consumer_id="ai_main",
                        user_id=f"{account}:{conv_id}",
                        system_prompt=prompt + "\n\n" + _retry_hint,
                        history_extra=history)
                    _probe(before_id, "RETRY", "ok" if raw2 else "empty",
                           raw=(raw2 or "")[:200])
                    if raw2:
                        cleaned2 = validate_reply(raw2, cfg)
                        _probe(before_id, "GUARD2",
                               "pass" if cleaned2 else "blocked",
                               cleaned=(cleaned2 or "")[:120], raw=raw2[:120])
                        if cleaned2:
                            logger.info("[ai] 短回复重试成功（首次过短已补救）")
                            return cleaned2, "AI"
                except Exception as e:  # 重试失败不算错误，正常回落兜底
                    _probe(before_id, "RETRY", "error", err=str(e)[:120])
            logger.info(f"[ai] AI 输出被护栏拦截，改发兜底: {raw[:40]}")
        # 🔴 2026-09-29：**降级路径同样不能放走线索**。实测：网关限流/模型不可用
        # 时走 `fallback_reply`（主池 4 条**全都不含索要**）⇒ 客户收到的是
        # 「请补充部位/诊断」而**没有任何留资动作** —— 正是用户指出的失效形态，
        # 只不过换成了"模型挂了"这条触发路径。故终末兜底也过同一判据。
        fb = fallback_reply(cfg)
        if not _contains_personal_contact(text) and _is_lead_stalled(fb):
            fb = lead_fallback_reply(cfg)
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

        # ① 对话回复库（命中库）：命中即回，零 token
        # 🔴 2026-09-29（用户拍板）：**直播首触默认不再查命中库** ——
        # 它是「命中即直接回复、绕过全部出口护栏」的最高优先级短路分支，
        # 而库内条目**全部来自自动学习**（碎片化、含个案裸答），实测已直发
        # 「保守治疗 → 有等级，工伤10级」这类无依据等级结论。开关
        # `live_reply_kb_enabled` 默认 False（保留可打开）。私信侧不受影响。
        if cfg.get("live_reply_kb_enabled"):
            try:
                from services import reply_kb

                hit = reply_kb.find_match(text_in, account=account)
                if hit:
                    # 2026-09-29：命中库直回同样要过**直播首触场景护栏**
                    # （库里碎片含无依据等级结论，命中即原件外发 = 绕过护栏）。
                    _hit = str(hit).strip()
                    _v = _live_guard_violation(_hit)
                    if _v:
                        logger.info(f"[AI-069] 直播命中库话术违反场景护栏（{_v}）"
                                    f" -> 改引导型兜底: {_hit[:30]!r}")
                        return str(live_fallback_reply(cfg) or "").strip(), "兜底"
                    return _hit, "回复库"
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
        # 直播首触没有对话历史：明确告知模型，避免它编造上下文。
        # 🔴 2026-09-29：追加**首触场景规则**（禁等级断言 / 禁有无等级直答 /
        #    只引导补材料）。必须放在 last（紧贴 user 消息），并显式声明
        #    「覆盖上文任何相反要求」—— 因为 Agent prompt 里有一条要求
        #    「拿到伤情就给判断…给出等级的初步口径与依据」，方向相反。
        prompt = (prompt + _LIVE_CONTACT_RULES
                  + f"\n【观众信息】昵称：{peer_name or '观众'}；"
                    f"公屏发言：{text_in[:200]}")

        raw = AIClient(cfg).chat_failover(
            text_in, consumer_id="ai_main",
            user_id=f"live:{account}:{uid or peer_name or 'unknown'}",
            system_prompt=prompt)
        if not raw:
            logger.info(f"[ai] 直播文案 AI 返回空，回落引导型兜底（账号={account}）")
            return str(live_fallback_reply(cfg) or "").strip(), "兜底"
        if _looks_like_reasoning(raw) and len(raw) > 40:
            logger.warning(f"[AI-019] " + f"[ai] 直播文案疑似思考过程残留，丢弃改兜底: {raw[:40]}")
            return str(live_fallback_reply(cfg) or "").strip(), "兜底"
        cleaned = validate_reply(raw, cfg, live_guard=True)
        if not cleaned:
            logger.info(f"[ai] 直播文案被护栏拦截，改发引导型兜底: {(raw or '')[:40]}")
            return str(live_fallback_reply(cfg) or "").strip(), "兜底"
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
        # 2026-09-30（架构层校正）：原 prompt 写死「工伤法律咨询」—— 通用引擎
        # 不得内置行业。改为领域中立：判断「是否有寻求专业帮助的真实意图」，
        # 具体行业由 Agent 的知识库/prompt 决定。
        prompt = (
            "你是线索质检员。判断下面这条直播间弹幕/评论是否"
            "「高价值咨询线索」——即：发信人很可能本人或近亲遭遇了某件事，"
            "且**有寻求专业帮助（赔偿/鉴定/律师/咨询）的真实意图**。\n"
            "只回一个 JSON：{\"high_value\": true/false, \"reason\": \"≤20字理由\"}。\n"
            "无关闲聊、同行打广告、纯情绪宣泄、无咨询要素 → false。"
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
