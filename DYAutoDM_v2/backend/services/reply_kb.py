# -*- coding: utf-8 -*-
"""对话回复库（命中库，v0.39.0）。

语义（用户 2026-09-10 拍板）
----------------------------
- 「对方发送的信息符合库内案例 → 直接自动回复，不用 token」。
- 数据来源：绑定 Agent 的抖音账号的 WS 私信长连接 + 解析后存库的聊天记录
  （dm_messages），自动总结学习出有效话术。
- 与「专业知识库」的关系：专业库是 RAG 前置参考（向量化、分析问题用，
  不直接回复）；本库是命中即回的最高优先级。

自动学习（learn_from_history）
------------------------------
从 dm_messages 取成功会话（该会话有 role='me' 的回复且对方后续继续对话，
视为有效话术）的问答对，经 LLM 提纯（去寒暄、合并同类、口语化保留）后
写入本库。学习结果只入预览/确认流程？—— 直接入库但带 `source: "auto"`
标记 + `enabled` 开关，人工可在前端删改关。
"""

from __future__ import annotations

import json
import math
import threading
import time
import re
from typing import Optional

import database
from services.kv_store import kv_get as _kv_get, kv_set as _kv_set

_KV_REPLY_KB = "ai_reply_chat_replies"   # [{id, question, answer, source, enabled, hits, created_at}]

_lock = threading.RLock()


def list_items() -> list:
    with _lock:
        return _kv_get(_KV_REPLY_KB, []) or []


def save_items(items: list) -> None:
    with _lock:
        _kv_set(_KV_REPLY_KB, items)


def add_item(question: str, answer: str, source: str = "manual") -> dict:
    q, a = (question or "").strip(), (answer or "").strip()
    if not q or not a:
        raise ValueError("问法案例和回复话术都不能为空")
    with _lock:
        items = _kv_get(_KV_REPLY_KB, []) or []
        existing_ids = {it.get("id") for it in items}
        new_id = int(time.time() * 1000)
        while new_id in existing_ids:
            new_id += 1
        item = {
            "id": new_id, "question": q, "answer": a,
            "source": source, "enabled": True, "hits": 0,
            "created_at": time.time(),
            "last_accessed_at": time.time(),
        }
        items.append(item)
        _kv_set(_KV_REPLY_KB, items)
        return item


def update_item(item_id: int, question: str, answer: str,
                enabled: Optional[bool] = None) -> Optional[dict]:
    with _lock:
        items = _kv_get(_KV_REPLY_KB, []) or []
        for it in items:
            if it.get("id") == item_id:
                if question is not None:
                    it["question"] = question.strip()
                if answer is not None:
                    it["answer"] = answer.strip()
                if enabled is not None:
                    it["enabled"] = bool(enabled)
                _kv_set(_KV_REPLY_KB, items)
                return it
        return None


def delete_item(item_id: int) -> bool:
    with _lock:
        items = _kv_get(_KV_REPLY_KB, []) or []
        n = len(items)
        items = [it for it in items if it.get("id") != item_id]
        if len(items) == n:
            return False
        _kv_set(_KV_REPLY_KB, items)
        return True


def clear_items() -> int:
    with _lock:
        items = _kv_get(_KV_REPLY_KB, []) or []
        _kv_set(_KV_REPLY_KB, [])
        return len(items)


# ---------------------------------------------------------------------------
# 命中匹配（零 token：精确/包含 → Jaccard 相似度）
# 注意：命中库不做语义向量匹配 —— 语义向量化是「专业知识库」的职责；
# 命中库追求确定性回复，模糊匹配只到 Jaccard 词级重叠。
# ---------------------------------------------------------------------------

def _jaccard(a: str, b: str) -> float:
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)

# ---------------------------------------------------------------------------
# 语义级（可选的第 2 级漏斗）
#
# 2026-09-23 修补（用户反馈「命中率差」）：命中库原本只有「精确/包含 → 字符集
# Jaccard」。中文同义句字符重合度天然低（「多少钱」vs「价格多少」字符 Jaccard
# 极低），故接入 ai_reply 已有的 embedding 链路做语义相似度。
#
# ⚠️ 向后兼容红线（改动前先看这里）：
#   1. 语义级**仅在 AI 配置 sem_enabled=True 时才可能是活的**；本项目当前实测
#      配置为 sem_enabled=False ⇒ 默认路径与修复前**逐路径相同**，零行为变更。
#   2. 语义级插在「①精确/包含」与「③Jaccard」之间 —— 它只会**新增**命中，
#      不会取消任何既有命中（Jaccard 分支代码一字未改）。
#   3. embedding 不可用（未启用/无 base_url/请求失败/模型不一致/无缓存且算不出）
#      → 一律 return None，原样落 ③ Jaccard。
#   4. 缓存命名空间独立于专业知识库：reply_kb_semv_* / reply_kb_sem_model，
#      与 ai_reply 的 ai_reply_semv_* / ai_reply_sem_model 不互相污染。
# ---------------------------------------------------------------------------

_KV_SEM_PREFIX = "reply_kb_semv_"      # + item_id -> 向量 JSON
_KV_SEM_MDL = "reply_kb_sem_model"     # 生成该批缓存时用的 embedding 模型


def _cosine_reply(a, b) -> float:
    """余弦相似度（本地实现，不依赖 numpy）。"""
    try:
        dot = na = nb = 0.0
        for x, y in zip(a, b):
            dot += x * y
            na += x * x
            nb += y * y
        if na <= 0 or nb <= 0:
            return 0.0
        return dot / (na ** 0.5 * nb ** 0.5)
    except Exception:
        return 0.0


def _sem_ready(cfg: dict) -> bool:
    """语义级是否允许运行：必须 sem_enabled=True 且有 base_url。

    默认配置 sem_enabled=False ⇒ 永远 False ⇒ find_match 行为零变化。
    """
    try:
        return bool(cfg.get("sem_enabled")) and bool(cfg.get("sem_base_url"))
    except Exception:
        return False


def _ai_get_config() -> dict:
    """读 AI 配置（失败返回空 dict ⇒ 语义级自动失活）。"""
    try:
        from services.ai_reply import get_config
        c = get_config()
        return c if isinstance(c, dict) else {}
    except Exception:
        return {}


def _item_vectors(items: list, cfg: dict, timeout: float = 8.0,
                  force: bool = False) -> tuple:
    """命中库条目的问句向量：优先吃缓存，缓存不全则现场算一批并写回。

    返回 (model, {item_id: vec})；语义级未启用或 embedding 失败返回 (None, {})。
    只向量化 question（照 ai_reply 实测结论：混入 answer 会稀释语义）。
    """
    if not items or not _sem_ready(cfg):
        return None, {}
    q_texts, keys = [], []
    for it in items:
        q = (it.get("question") or "").strip()
        if q:
            q_texts.append(q)
            keys.append(it.get("id"))
    if not q_texts:
        return None, {}

    # ① 缓存齐 → 零网络直返（不现场校验模型，见下方 rebuild 注释）
    if not force:
        cached = {}
        for k in keys:
            v = _kv_get(_KV_SEM_PREFIX + str(k), None)
            if isinstance(v, list) and v:
                cached[k] = v
        if len(cached) == len(keys):
            return (_kv_get(_KV_SEM_MDL, "") or ""), cached

    # ② 缓存不全 → 走 ai_reply 的语义避障链算一批
    try:
        from services.ai_reply import _embed_failover
        vecs, model = _embed_failover(q_texts, timeout=timeout)
    except Exception:
        return None, {}
    if vecs is None or len(vecs) != len(q_texts):
        return None, {}
    out = {}
    for k, v in zip(keys, vecs):
        out[k] = v
    try:
        for k, v in out.items():
            _kv_set(_KV_SEM_PREFIX + str(k), v)
        if model:
            _kv_set(_KV_SEM_MDL, model)
    except Exception:
        pass
    return model, out


def find_match_semantic_reply(text: str, items: list, cfg: dict,
                              threshold: Optional[float] = None,
                              timeout: float = 8.0) -> Optional[tuple]:
    """语义级匹配：问句向量化 → 与条目向量逐条余弦 → 最高分 ≥ 阈值。

    返回 (item, score)；语义级不可用或未过阈值返回 None（调用方落 Jaccard）。
    """
    if not text or not items or not _sem_ready(cfg):
        return None
    try:
        from services.ai_reply import _embed_failover
    except Exception:
        return None
    model, vectors = _item_vectors(items, cfg, timeout=timeout)
    if not vectors:
        return None
    qvecs, used_model = _embed_failover([text], timeout=timeout)
    if not qvecs:
        return None
    # 向量空间一致性：缓存是别的 embedding 模型生成的 → 余弦不可比，
    # 直接跳过语义级（落 Jaccard），防跨模型混算出假命中。
    if model and used_model and model != used_model:
        return None
    th = float(threshold if threshold is not None
               else cfg.get("sem_threshold", 0.40))
    qv = qvecs[0]
    best, best_score = None, 0.0
    for it in items:
        v = vectors.get(it.get("id"))
        if not v:
            continue
        s = _cosine_reply(qv, v)
        if s > best_score:
            best, best_score = it, s
    if best is not None and best_score >= th:
        return best, best_score
    return None


def rebuild_semantic_cache(cfg: Optional[dict] = None,
                           timeout: float = 20.0) -> dict:
    """为全部命中库条目**强制**重算向量缓存（换模型/批量导入后调用）。

    返回 {ok, embedded, total, model}。语义级未启用或 embedding 失败 → ok=False
    （此时 find_match 自动回落 Jaccard，不影响命中能力）。
    """
    cfg = cfg or _ai_get_config()
    if not _sem_ready(cfg):
        return {"ok": False, "embedded": 0, "total": 0,
                "error": "语义级未启用（sem_enabled=False 或缺 sem_base_url）"}
    items = [it for it in list_items() if it.get("enabled", True)]
    if not items:
        return {"ok": True, "embedded": 0, "total": 0}
    model, vectors = _item_vectors(items, cfg, timeout=timeout, force=True)
    if not vectors:
        return {"ok": False, "embedded": 0, "total": len(items),
                "error": "embedding 调用失败（检查语义链路配置/网络）"}
    return {"ok": True, "embedded": len(vectors), "total": len(items),
            "model": model or ""}


def find_match(text: str, threshold: float = 0.85,
               account: str = "") -> Optional[str]:
    """命中库匹配：enabled 的条目里找精确/包含命中，其次 Jaccard≥threshold。

    时间衰减（移植自 MalogBot）：模糊匹配的候选按
        score * e^(-λ * 距上次命中天数)   （λ=0.02，约 50 天衰减到 0.37）
    重排，避免半年前的旧话术压过新学到的同义话术。
    精确/包含命中仍然零衰减直返（确定性回复优先）。
    """
    qtext = (text or "").lower().strip()
    if not qtext:
        return None
    # 2026-09-28：自动学习的脏条目（占位兜底/随口短句）在匹配侧**失效**（不删数据）
    items = [it for it in list_items()
             if it.get("enabled", True) and _entry_usable(it)]
    # ① 精确/包含
    for it in items:
        q = (it.get("question") or "").lower().strip()
        if q and (q in qtext or qtext in q):
            _bump_hits(it.get("id"))
            return it.get("answer", "")
    # ② 语义级（可选；sem_enabled=True 才活，默认关闭 ⇒ 与修复前完全一致）
    try:
        _cfg = _ai_get_config()
        sem_hit = find_match_semantic_reply(qtext, items, _cfg)
        if sem_hit:
            _it, _score = sem_hit
            _bump_hits(_it.get("id"))
            return _it.get("answer", "")
    except Exception:
        pass  # 语义级任何异常一律吞掉，绝不影响下面的 Jaccard 兜底
    # ③ Jaccard 词级重叠（中文按字符集）+ 时间衰减加权（与修复前逐字相同）
    now = time.time()
    best, best_score = None, 0.0
    best_raw = 0.0
    for it in items:
        q = (it.get("question") or "").lower().strip()
        if not q:
            continue
        raw = _jaccard(qtext, q)
        if raw <= 0.0:
            continue
        ref = it.get("last_accessed_at") or it.get("created_at") or now
        days_ago = max(0.0, (now - float(ref)) / 86400.0)
        score = raw * _decay(days_ago)
        if score > best_score:
            best, best_score = it, score
            # 记录原始相关度，用于阈值判断（阈值针对相关度，不针对衰减后分数）
            best_raw = raw
    # 2026-09-17 修补（OCR 审查 HIGH —— 阈值用错量致旧条目永不命中）：
    # 原为 `if best and best_score >= threshold:` —— 拿**衰减后**分数比阈值，
    # 而衰减因子 e^(-λ·days) ≤ 1，等于把阈值悄悄抬高（50 天前的条目需
    # raw ≥ 0.85/0.37 ≈ 2.3，永不可能）→ 旧的但高度相关的条目**静默永不命中**，
    # 与上方注释「阈值针对相关度」及函数文档「衰减只用于重排」直接矛盾。
    # 现：仍按衰减后分数**重排**选最优，但阈值**只作用于原始相关度**。
    if best and best_raw >= threshold:
        _bump_hits(best.get("id"))
        return best.get("answer", "")
    return None


def _decay(days_ago: float, lam: float = 0.02) -> float:
    """时间衰减因子 decay = e^(-λ * days_ago)。"""
    try:
        return math.exp(-lam * max(0.0, float(days_ago)))
    except Exception:
        return 1.0


def _bump_hits(item_id) -> None:
    with _lock:
        items = _kv_get(_KV_REPLY_KB, []) or []
        for it in items:
            if it.get("id") == item_id:
                it["hits"] = int(it.get("hits") or 0) + 1
                it["last_accessed_at"] = time.time()
                _kv_set(_KV_REPLY_KB, items)
                return


# ---------------------------------------------------------------------------
# 自动学习：从 dm_messages 成功会话总结有效话术
# ---------------------------------------------------------------------------

# 提纯 prompt：让 LLM 把"成功问答对"整理成客户口语问法 + 可复用回复
LEARN_PROMPT = """你是私信话术整理员。下面是从抖音私信成功对话中提取的问答片段。
请整理成"客户问法案例 → 应复用的回复话术"对：
- 问法要模仿客户的口语（短句、口语词），去掉个人信息（名字/手机号/地名）
- 回复话术保留原意和语气，去掉个人信息
- 同类问法合并，只保留最典型的一条
- 无效内容（纯表情、仅"嗯/哦"、系统消息）丢弃
- **只保留包含实质性专业信息的问答**：凡回复只是寒暄/共情/占位（如"理解""稍等""图我看到了""嗯嗯"）、
  或答非所问、或与工伤业务无关的，一律丢弃
只输出 JSON 数组：[{"question":"客户问法","answer":"回复话术"}]，不要其他文字。

对话片段：
"""


# ---------------------------------------------------------------------------
# 学习质量门槛（2026-09-28）
# ---------------------------------------------------------------------------
# 背景（用户反馈「AI 像客服、不专业」的实证根因之一）：
#   reply_kb.learn_from_history 把**对话里的任意 them→me 对**当话术学习，其中包括
#   我方的占位兜底（"图我看到了哈，稍等我看看再回你"）和随口的短句回复，
#   随后 find_match 又给这些 auto 条目**最高优先级（命中即回、零 token）**
#   ⇒ 满屏"可以的""嗯嗯"式客服腔，且盖过 AI。
# 修法：设**质量门槛**，同时在①写入侧（不再学进来）②匹配侧（已存在的脏条目
#   失效但**不删除**，可逆）生效。门槛只针对 source=auto；人工/迁移条目不受限。
_LEARN_JUNK_PREFIXES = (
    "图我看到了", "稍等", "收到", "嗯嗯", "好的", "ok", "OK", "在的", "在呢",
    "我看看", "这边", "行，", "嗯，", "你好", "在忙",
)
_LEARN_JUNK_EXACT = frozenset({
    "嗯", "哦", "好", "好的", "可以", "可以的", "收到", "行", "在", "在的",
    "谢谢", "好的哈", "ok", "OK", "嗯嗯", "嗯好", "好嘞",
})
_LEARN_MIN_ANSWER_LEN = 8      # 回复话术至少 8 字（口语短句过滤）
_LEARN_MIN_QUESTION_LEN = 3    # 问法至少 3 字


def learn_quality_ok(question: str, answer: str) -> tuple:
    """自动学习条目的质量门槛。返回 (是否合格, 原因)。

    仅用于 source='auto'；人工条目不经此门。
    """
    q = (question or "").strip()
    a = (answer or "").strip()
    if len(q) < _LEARN_MIN_QUESTION_LEN:
        return False, "question_too_short"
    if len(a) < _LEARN_MIN_ANSWER_LEN:
        return False, "answer_too_short"
    if a in _LEARN_JUNK_EXACT:
        return False, "answer_is_placeholder"
    for p in _LEARN_JUNK_PREFIXES:
        if a.startswith(p):
            return False, f"answer_starts_with_placeholder({p})"
    return True, ""


def _entry_usable(it: dict) -> bool:
    """匹配侧门槛：脏 auto 条目直接**失效**（不删除数据，可逆）。

    人工/迁移条目（source != 'auto'）一律可用 —— 只约束自动学习产物。
    """
    if (it.get("source") or "") != "auto":
        return True
    ok, _ = learn_quality_ok(it.get("question"), it.get("answer"))
    return ok


def _is_valid_text(text: str, mtype) -> bool:
    """消息是否能作为「文本」参与抽对（跳过系统提示/图片/你已确认）。

    照搬修复前的过滤条件，一字未改：
    - msg_type 实际取值：'text' / 'image' / '27'（系统引导）——只收 text
    - "[...]" 前缀（[图片] 等）、"你已确认…" 系统提示跳过
    """
    t = (text or "").strip()
    if not t:
        return False
    is_text = (mtype in (1, 0, None, "text", "") or str(mtype) == "text")
    if not is_text or t.startswith("[") or t.startswith("你已确认"):
        return False
    return True


def _extract_pairs_legacy(msgs) -> list:
    """修复前的抽对实现（留档，仅供验证脚本做前后对比，业务不调用）。

    缺陷：一旦锚定了第一条 them，后面插进来多少条不相关的 them 都不管，
    拿「其后第一条 me」就配成一对 ⟹ 「牛头不对马尾」的直接来源。
    """
    pairs = []
    q = None
    for role, text, mtype in msgs:
        t = (text or "").strip()
        is_text = (mtype in (1, 0, None, "text", "") or str(mtype) == "text")
        if not is_text or t.startswith("[") or t.startswith("你已确认"):
            continue
        if role == "them" and q is None:
            q = t
        elif role == "me" and q:
            pairs.append((q, t))
            q = None
    return pairs


def _extract_pairs(msgs) -> list:
    """2026-09-23 修补：抽对加「不得跨越另一条 them」约束。

    规则：一条 them 与其配对的 me 之间，不得出现第二条 them；出现 ⟹ 前一条
    them 未被回答（或已被后续提问覆盖），丢弃它并把锚点重新落到新的 them 上。
    过滤条件（系统提示 / [图片] / 你已确认）与修复前完全一致。

    入参：[(role, text, msg_type), ...]（按时间正序）
    返回：[(question, answer), ...]
    """
    pairs = []
    q = None
    for role, text, mtype in msgs:
        if not _is_valid_text(text, mtype):
            continue
        t = (text or "").strip()
        if role == "them":
            # 关键修复点：**无条件重新锚定**。只要中间又冒出一条 them，
            # 前一条 them 与其后 me 不构成问答对 —— 旧实现这里写的是
            # `if role == "them" and q is None`，才让隔着 5 条 them 的
            # 两条消息被硬配成一对。
            q = t
            continue
        if role == "me" and q:
            pairs.append((q, t))
            q = None
    return pairs


def learn_from_history(account: str = "", limit: int = 200) -> dict:
    """扫描 dm_messages 成功会话，提取问答对 → LLM 提纯 → 入库。

    会话内按「them 不得跨越另一条 them」约束抽问答对（详见 `_extract_pairs`）。

    返回 dict：
        ok        是否真的完成了 LLM 提纯并写入
        reason    失败原因码（ok=True 时为 ""）
        scanned / extracted / added / purified

    ⚠️ 2026-09-23 修补（用户反馈数据「牛头不对马尾」却看不出问题）：
    旧的 LLM 提纯失败时是**静默降级** —— 直接把原样截断的原文
    （question 截 60 字 / answer 截 200 字）写库，前端只看到 added>0，
    根本不知道这批数据没经过 LLM。现改为：**提纯失败即不写库**，
    并把原因码回给调用方（由 api 层透传给前端提示）。
    """
    conn = database.get_db()
    # 候选会话：既有 them 又有 me 的会话（有来往 = 有成功回复）
    rows = conn.execute(
        "SELECT account, conv_id FROM dm_messages GROUP BY account, conv_id "
        "HAVING SUM(CASE WHEN role='me' THEN 1 ELSE 0 END) > 0 "
        "AND SUM(CASE WHEN role='them' THEN 1 ELSE 0 END) > 0 "
        "ORDER BY MAX(id) DESC LIMIT ?", (limit,)).fetchall()

    pairs = []  # (question, answer)
    for acct, conv in rows:
        if account and acct != account:
            continue
        msgs = conn.execute(
            "SELECT role, text, msg_type FROM dm_messages "
            "WHERE account=? AND conv_id=? AND TRIM(COALESCE(text,''))<>'' "
            "ORDER BY id ASC LIMIT 40", (acct, conv)).fetchall()
        pairs.extend(_extract_pairs(msgs))
        if len(pairs) >= 60:
            break

    if not pairs:
        return {"ok": True, "reason": "no_pairs", "scanned": len(rows),
                "extracted": 0, "added": 0, "purified": False,
                "message": "未提取到合规问答对（库未变更）"}

    # LLM 提纯：失败即中止，**不再**静默降级为原样截断入库
    learned: list[dict] = []
    purified = False
    reason = ""
    try:
        from services.ai_reply import AIClient, get_config
        cfg = get_config()
        if not cfg.get("base_url"):
            reason = "llm_not_configured"      # 没配 base_url，压根没法提纯
        else:
            client = AIClient(cfg)
            blob = "\n".join(f"客户: {q}\n我方: {a}" for q, a in pairs[:40])
            raw = client.chat_failover(
                blob, consumer_id="ai_main", user_id="kb-learn",
                system_prompt=LEARN_PROMPT)
            if not raw or not str(raw).strip():
                # chat_failover 全链失败（坏 key / 断网 / HTTP 错 / 空回复
                # / 思考泄漏被丢弃）都表现为返回 None
                reason = "llm_unavailable"
            else:
                m = re.search(r"\[.*\]", str(raw), re.S)
                if not m:
                    reason = "llm_no_json"     # 回了文字但不是 JSON 数组
                else:
                    try:
                        parsed = json.loads(m.group(0))
                    except Exception:
                        reason = "llm_json_bad"  # JSON 解析失败
                    else:
                        if isinstance(parsed, list) and parsed:
                            learned = parsed
                            purified = True
                        else:
                            reason = "llm_empty_array"
    except Exception as e:
        reason = "llm_exception:" + str(e)[:60]

    if not purified:
        return {"ok": False, "reason": reason or "llm_unavailable",
                "scanned": len(rows), "extracted": len(pairs), "added": 0,
                "purified": False,
                "message": "LLM 提纯失败，本次未写入任何条目（库未变更）"}

    # 查重后入库（问法近似的不重复加）
    existing = {(it.get("question") or "").strip() for it in list_items()}
    added = 0
    skipped_junk = 0
    for it in learned:
        if not isinstance(it, dict):
            continue
        q = (it.get("question") or "").strip()
        a = (it.get("answer") or "").strip()
        if not q or q in existing:
            continue
        # 2026-09-28：写入侧质量门槛 —— 占位兜底/随口短句不再学进来（防再污染）
        ok_q, why = learn_quality_ok(q, a)
        if not ok_q:
            skipped_junk += 1
            continue
        add_item(q, a, source="auto")
        existing.add(q)
        added += 1
    return {"ok": True, "reason": "", "scanned": len(rows),
            "extracted": len(pairs), "added": added, "skipped_junk": skipped_junk,
            "purified": True,
            "message": f"LLM 提纯成功，新增 {added} 条（跳过低质/占位 {skipped_junk} 条）"}
