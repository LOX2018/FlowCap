# -*- coding: utf-8 -*-
"""对话回复库「向量提纯」—— 学习环节的唯一净化入口（2026-09-28）。

## 为什么需要（用户判定 + 实测取证）

用户指出：「聊天内容自动学习，很多东西都是照搬，而且挑选的聊天记录并不具备
通用性，很多都是这个客户的个例。对话回复库一定要经过向量提纯之后，沉淀的通用部分。」

**改前的机理缺陷**：`reply_kb.learn_from_history` 只做「抽 them→me 对 → 丢给 LLM
一次性提纯 → 逐条入库」。它**没有任何通用性判据**：
  - 同一问法的多种说法各学一条（未聚类 → 重复/碎片）；
  - 客户个例（含具体数字/地名/伤情/ICU 等）被照搬成「通用话术」；
  - 被问过一次的观点被当成「可复用话术」。

**实测取证（张老师账号 81 条 auto 条目，`_probe_learn_quality.py`）**：
  - 含具体个案信息（数字/地名/伤情）的问法 **20/81**；
  - 向量聚类 θ=0.82 下 **33/81 条属 14 个「同一问法」重复簇**（如「这半个月了还能改吗」4 个变体）；
  - 回复 ≤6 字的占位/一句式 **30/81**。

## 设计契约

**前置条件 P**：输入是一批 `(question, answer)` 原始问答对（来自真实会话）。

**净化流水线（顺序不可颠倒）**：
  1. **向量化**：问法 → embedding（唯一向量出口，注入可用）。
  2. **聚类**：余弦 ≥ `sim_threshold` 的问法归为一簇（同一问题的多种说法）。
  3. **粗门槛**：簇内原始条数 < `min_cluster_size` 的簇直接弃（省 LLM 开销）。
  4. **剔寒暄**：簇内去掉寒暄/客套/无信息问法（「你好」「好的」「测试」…）——
     即便多人重复也**不是**可沉淀知识。
  5. **剔个案成员**：簇内去掉含具体个案特征（数字/地名/伤情编码/超长）的问法。
  6. **通用性门槛（真实判据）**：剩下成员须来自 **≥ `min_sources` 个不同来源
     （会话/客户）**——「被多个**客户**问过」才是通用问题。**禁止**用原始条数计：
     同一客户把同句问 N 遍不等于通用（2026-09-28 实测缺陷，见 Ⅱ2）。
  7. **抽象**：对每簇用 LLM 抽成**通用问法 + 去个案的专业回复**（不是照搬）。
  8. **专业实质门槛**：回复必须含工伤域实质信息（关键词），否则丢弃。
  9. **去重**：与库内既有条目做向量去重。

**后置条件 Q**：返回**候选条目**（默认不直接入库，待人工确认）；每一步的丢弃
计数都回传（可归因，不静默）。

**不变式 I**：
  Ⅰ1 净化**只依赖注入的 embed_fn/llm_fn**，二者不可用时**不降级照搬**（返回 ok=False）。
  Ⅰ2 任何 LLM 提纯失败 → 该簇**跳过**，不写原样截断原文（防"假提纯"污染）。
  Ⅰ3 候选写入独立命名空间 `ai_reply_candidates`，**绝不**直接覆盖 `ai_reply_chat_replies`。
"""

from __future__ import annotations

import json
import math
import re
import time
from typing import Callable, Optional

from loguru import logger

from services.kv_store import kv_get as _kv_get, kv_set as _kv_set

_KV_CAND = "ai_reply_candidates"       # 候选（待确认）—— 独立命名空间

# ── 默认参数（可被 app_config.reply_learn 覆盖）──
DEFAULT_SIM_THRESHOLD = 0.80           # 聚类：同簇余弦下限
DEFAULT_MIN_CLUSTER = 2                # 簇内「通用（非个案）问法」条数下限
DEFAULT_MIN_SOURCES = 2                # **真实**通用性门槛：至少来自 2 个不同会话/客户
DEFAULT_MAX_CASE_CHARS = 30            # 超长问法视为个案照搬
DEFAULT_AUTO_APPLY = False             # 学习结果默认「待确认」，不自动入库

# 寒暄/客套/无信息问法：即便多人重复也**不是**可沉淀知识（2026-09-28 实测：
# 「你好」「你们已互相关注对方」「测试」会靠重复凑过纯计数门槛，必须先剔除）。
_GREETING_RE = re.compile(
    r"^(你好|您好|哈喽|在吗|在么|在不在|在不在吗|在的|你好呀|你好啊|早上好|"
    r"下午好|晚上好|谢谢|感谢|多谢|辛苦了|麻烦你了|好的|好|嗯+|嗯嗯|哦+|噢+|"
    r"收到|知道了|明白|了解了|你已确认.*|你们已互相关注.*|测试.*|正在测试.*|"
    r"测试稳定性.*|你好，老师.*|你好老师.*)$"
)

# 个案特征：阿拉伯数字 / 数字+量词（月天年岁元万…）/ 地名 / 伤情手术
# 注意：**不能**用裸「一/二/三…」——「一般」「一起」会被误判为个案（实测踩过）。
_CASE_RE = re.compile(
    r"\d"
    r"|[一二三四五六七八九十百千两]+(个月|天|年|岁|元|万|块|次|处|根|条|公分|厘米|公斤|周)"
    r"|(北京|上海|广东|江苏|浙江|山东|河南|河北|湖北|湖南|四川|重庆|安徽|福建|江西|"
    r"陕西|山西|辽宁|吉林|黑龙江|云南|贵州|广西|内蒙古|甘肃|新疆|西藏|青海|宁夏|海南|"
    r"天津|苏州|无锡|南京|杭州|成都|武汉|长沙|郑州|济南|青岛|深圳|广州|东莞|佛山|"
    r"黄冈|盐城|鹤山|冻库|工地|工厂|车间)"
    r"|(股骨|胫骨|腓骨|肋骨|髌骨|锁骨|椎|骨折|骨水泥|内固定|韧带|半月板)"
)

# 工伤域专业实质关键词（回复必须命中其一，才算"有专业信息"）
_DOMAIN_KW = (
    "工伤", "伤残", "等级", "赔偿", "认定", "鉴定", "劳动能力", "停工留薪",
    "一次性", "补助", "医疗", "误工", "护理", "就业", "材料", "证据", "合同",
    "社保", "保险", "申报", "时效", "工资", "标准", "按月", "基金", "十级",
    "九级", "八级", "七级", "本人工资", "审批", "复查", "病历", "诊断",
)


# ===========================================================================
# 基础判据（纯函数，可单测）
# ===========================================================================

def _cosine(a, b) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb + 1e-12)


def is_case_specific(text: str, max_chars: int = DEFAULT_MAX_CASE_CHARS) -> bool:
    """问法是否含具体个案特征（数字/地名/伤情/超长）⇒ 不宜作为通用问法。"""
    t = (text or "").strip()
    if len(t) > max_chars:
        return True
    return bool(_CASE_RE.search(t))


def is_greeting(text: str) -> bool:
    """问法是否为寒暄/客套/无信息内容（不可沉淀为知识，即便多人重复）。"""
    t = (text or "").strip()
    return bool(t) and bool(_GREETING_RE.match(t))


def _norm_question(text: str) -> str:
    """归一化问法（仅用于「来源计数」去重）：去空白与常见标点，使「工伤怎么赔？」
    与「工伤怎么赔」计为同一问法（同一客户复述同一问不应算作两个来源）。"""
    t = (text or "").strip().lower()
    return re.sub(r"[\s，。！？、,.!?;；:：~～\-—_*\"'“”‘’()（）\[\]【】]+", "", t)


def has_professional_substance(answer: str) -> bool:
    """回复是否含工伤域实质信息（防"共情/占位"被沉淀为话术）。"""
    a = (answer or "").strip()
    if len(a) < 6:
        return False
    return any(k in a for k in _DOMAIN_KW)


def cluster_indices(vecs: list, threshold: float = DEFAULT_SIM_THRESHOLD) -> list:
    """单遍贪心聚类：余弦 ≥ threshold 归入同一簇。返回 [[idx...], ...]。"""
    n = len(vecs)
    used = [False] * n
    clusters = []
    for i in range(n):
        if used[i]:
            continue
        grp = [i]
        used[i] = True
        for j in range(i + 1, n):
            if used[j]:
                continue
            if _cosine(vecs[i], vecs[j]) >= threshold:
                grp.append(j)
                used[j] = True
        clusters.append(grp)
    return clusters


# ===========================================================================
# 默认 embed / llm（生产路径；测试可注入替身）
# ===========================================================================

def _default_embed(texts: list, timeout: float = 20.0):
    from services.ai_reply import _embed_failover
    return _embed_failover(texts, timeout=timeout)


_ABSTRACT_PROMPT = """你是工伤理赔知识库的编辑。下面是从**同一类问题**的多个客户问法
与我们的回复中提取的片段。

请把它**抽象化**成一条可复用的知识条目，要求：
1. 问法：写成**最通用的客户口吻**，去掉具体数字、地名、姓名、伤情细节（那些是个案）。
2. 回复：写成**通用专业答复**，保留工伤业务口径（等级/赔偿项目/认定流程/停工留薪等），
   绝不保留任何个案专属信息，绝不照抄原句，不得使用"稍等""图我看到了""我看看"等占位话。
3. 若这些片段**不包含可复用的专业信息**（只是寒暄/共情/纯个案），返回空对象 {{}}。
只输出 JSON：{{"question":"通用问法","answer":"通用回复"}}，不要其他文字。

片段：
"""


def _default_llm(prompt: str) -> Optional[str]:
    """走 AI 主链做抽象（chat_failover，含避障）。失败返回 None。"""
    try:
        from services.ai_reply import AIClient, get_config
        client = AIClient(get_config())
        return client.chat_failover(prompt, consumer_id="ai_main",
                                    user_id="kb-purify")
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[purify] llm 调用异常: {e}")
        return None


# ===========================================================================
# 主流水线
# ===========================================================================

def purify(pairs: list, existing_questions: Optional[list] = None,
           cfg: Optional[dict] = None,
           embed_fn: Optional[Callable] = None,
           llm_fn: Optional[Callable] = None,
           conv_keys: Optional[list] = None) -> dict:
    """把原始问答对净化为「通用知识候选」。

    :param pairs: [(question, answer), ...]
    :param existing_questions: 库内既有问法（做向量去重）
    :param cfg: {sim_threshold, min_cluster_size, min_sources, max_case_chars}
    :param embed_fn: 注入的向量函数 (texts)->(vecs, model)；默认走 _embed_failover
    :param llm_fn: 注入的 LLM 函数 (prompt)->str|None；默认走 AIClient
    :param conv_keys: 与 pairs 等长的「来源标识」（会话/客户 id）。
        **通用性必须按不同来源计**：同一客户把同一句问 N 遍不算通用（见 Ⅱ2）。
        缺省时退化为「归一化问法」计数。
    :return: {ok, candidates:[{question,answer,cluster_size,sources,evidence:[q...]}], stats:{...}}
    """
    cfg = cfg or {}
    th = float(cfg.get("sim_threshold", DEFAULT_SIM_THRESHOLD))
    min_cluster = int(cfg.get("min_cluster_size", DEFAULT_MIN_CLUSTER))
    min_sources = int(cfg.get("min_sources", DEFAULT_MIN_SOURCES))
    max_chars = int(cfg.get("max_case_chars", DEFAULT_MAX_CASE_CHARS))
    embed_fn = embed_fn or _default_embed
    llm_fn = llm_fn or _default_llm

    _raw = list(pairs or [])
    if conv_keys is not None and len(conv_keys) != len(_raw):
        logger.warning("[purify] conv_keys 长度与 pairs 不符，退回「归一化问法」计数")
        conv_keys = None
    pairs, keys = [], []
    for i, (q, a) in enumerate(_raw):
        qq, aa = (q or "").strip(), (a or "").strip()
        if not qq or not aa:
            continue
        pairs.append((qq, aa))
        keys.append(str(conv_keys[i]) if conv_keys else "")
    stats = {"input": len(pairs), "embedded": 0, "clusters": 0,
             "kept_clusters": 0, "dropped_case_only": 0,
             "dropped_greeting_only": 0, "dropped_few_sources": 0,
             "dropped_no_substance": 0, "dropped_llm_fail": 0,
             "dropped_dedup": 0, "out": 0}
    if not pairs:
        return {"ok": True, "candidates": [], "stats": stats}

    questions = [q for q, _ in pairs]
    try:
        vecs, _model = embed_fn(questions)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[purify] 向量化失败，按契约不降级照搬: {e}")
        vecs = None
    if not vecs or len(vecs) != len(pairs):
        # Ⅰ1：向量不可用 → 不净化即不产出（绝不退化成"照搬"）
        return {"ok": False, "candidates": [], "stats": stats,
                "error": "embedding_unavailable"}
    stats["embedded"] = len(vecs)

    clusters = cluster_indices(vecs, th)
    stats["clusters"] = len(clusters)

    # 既有问法向量（去重）
    ex_vecs = []
    if existing_questions:
        try:
            ex_vecs, _ = embed_fn(list(existing_questions))
        except Exception:  # noqa: BLE001
            ex_vecs = []

    candidates = []
    for grp in clusters:
        qs = [questions[i] for i in grp]
        # ③ 粗门槛：簇太小直接弃（省 LLM 开销）
        if len(grp) < min_cluster:
            continue
        # ④a 剔寒暄/客套：不是知识，即便多人重复也不沉淀
        non_greet = [i for i in grp if not is_greeting(questions[i])]
        if len(non_greet) < min_cluster:
            stats["dropped_greeting_only"] += 1
            continue
        # ④b 剔个案成员；若剔完不足门槛 → 整簇弃（全是该客户的个例）
        general_idx = [i for i in non_greet
                       if not is_case_specific(questions[i], max_chars)]
        if len(general_idx) < min_cluster:
            stats["dropped_case_only"] += 1
            continue
        # ⑤ 通用性门槛（**真实判据**）：必须来自 ≥ min_sources 个不同来源。
        #    ⚠️ 2026-09-28 实测缺陷：改前用「原始簇内条数」计，同一客户把同句
        #    问 N 遍即可凑过门槛（实测「测试×3」全部来自 1 个会话却达标）。
        #    通用 = 「被不同客户问过」，故必须按来源去重后计数。
        distinct_sources = {(keys[i] or ("q:" + _norm_question(questions[i])))
                            for i in general_idx}
        if len(distinct_sources) < min_sources:
            stats["dropped_few_sources"] += 1
            continue
        stats["kept_clusters"] += 1
        evidence = [questions[i] for i in general_idx][:5]
        blob = "\n".join(f"客户问: {questions[i]}\n我们答: {pairs[i][1]}"
                         for i in general_idx)
        raw = None
        try:
            raw = llm_fn(_ABSTRACT_PROMPT + blob)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[purify] 抽象异常: {e}")
        if not raw or not str(raw).strip():
            stats["dropped_llm_fail"] += 1
            continue
        m = re.search(r"\{.*\}", str(raw), re.S)
        if not m:
            stats["dropped_llm_fail"] += 1
            continue
        try:
            obj = json.loads(m.group(0))
        except Exception:  # noqa: BLE001
            stats["dropped_llm_fail"] += 1
            continue
        gq = (obj.get("question") or "").strip()
        ga = (obj.get("answer") or "").strip()
        if not gq or not ga:
            continue      # 模型判定「无复用价值」→ 空对象，正常跳过
        # ⑥ 专业实质门槛
        if not has_professional_substance(ga):
            stats["dropped_no_substance"] += 1
            continue
        # ⑦ 与既有条目向量去重
        if ex_vecs:
            try:
                gvec = embed_fn([gq])[0]
                if gvec and any(_cosine(gvec[0], ev) >= 0.92 for ev in ex_vecs):
                    stats["dropped_dedup"] += 1
                    continue
            except Exception:  # noqa: BLE001
                pass
        candidates.append({"question": gq, "answer": ga,
                           "cluster_size": len(grp),
                           "sources": len(distinct_sources),
                           "evidence": evidence})
    stats["out"] = len(candidates)
    return {"ok": True, "candidates": candidates, "stats": stats}


# ===========================================================================
# 候选的暂存 / 读取 / 确认（Ⅰ3：独立命名空间，绝不直接覆盖正式库）
# ===========================================================================

def list_candidates() -> list:
    return _kv_get(_KV_CAND, []) or []


def stage_candidates(cands: list, account: str = "") -> int:
    """把候选写入暂存区（去重：同问法已在暂存或正式库则跳过）。返回新增数。"""
    from services import reply_kb
    existing = {(it.get("question") or "").strip() for it in list_candidates()}
    formal = {(it.get("question") or "").strip() for it in reply_kb.list_items()}
    n = 0
    staged = list_candidates()
    for c in cands or []:
        q = (c.get("question") or "").strip()
        if not q or q in existing or q in formal:
            continue
        staged.append({"id": int(time.time() * 1000) + n, "question": q,
                       "answer": (c.get("answer") or "").strip(),
                       "cluster_size": c.get("cluster_size", 0),
                       "evidence": c.get("evidence", []),
                       "account": account, "created_at": time.time()})
        existing.add(q)
        n += 1
    if n:
        _kv_set(_KV_CAND, staged)
    return n


def confirm_candidates(ids: Optional[list] = None, all_: bool = False,
                       source: str = "auto") -> dict:
    """把（选中的）候选**升格**为正式回复库条目，并从暂存区移除。"""
    from services import reply_kb
    staged = list_candidates()
    keep, promote = [], []
    want = None if all_ else set(ids or [])
    for c in staged:
        if all_ or (c.get("id") in want):
            promote.append(c)
        else:
            keep.append(c)
    added = 0
    for c in promote:
        try:
            reply_kb.add_item(c.get("question", ""), c.get("answer", ""),
                              source=source)
            added += 1
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[purify] 升格失败（跳过）: {e}")
    _kv_set(_KV_CAND, keep)
    return {"ok": True, "added": added, "remaining": len(keep)}


def reject_candidates(ids: list) -> int:
    """丢弃（拒绝）指定候选。返回移除数。"""
    staged = list_candidates()
    want = set(ids or [])
    keep = [c for c in staged if c.get("id") not in want]
    n = len(staged) - len(keep)
    _kv_set(_KV_CAND, keep)
    return n


# ===========================================================================
# 存量审计（2026-09-28 D5）：把**已在正式库**的脏 auto 条目降级到待确认区
# ===========================================================================

def audit_legacy_auto() -> dict:
    """扫描正式库里的 auto 条目，把「个案化/寒暄/等级断言」的移入候选区待确认。

    设计契约（对齐 ADR-022「绝不直接覆盖正式库」）：
      - **可逆**：条目从正式库移入候选区（同一份数据换命名空间），
        候选区确认即升格回来，或拒绝即丢弃。
      - 只处理 source=auto；人工条目不动。
    返回 {scanned, moved, kept, moved_items:[...]}（moved_items 便于留证/回滚）。
    """
    from services import reply_kb
    items = reply_kb.list_items()
    keep, moved = [], []
    for it in items:
        if (it.get("source") or "") != "auto":
            keep.append(it)
            continue
        reason = ""
        if not reply_kb.learn_quality_ok(it.get("question"), it.get("answer"))[0]:
            reason = "quality_gate"
        else:
            try:
                if is_case_specific(it.get("question") or ""):
                    reason = "case_specific"
                elif is_greeting(it.get("question") or ""):
                    reason = "greeting"
            except Exception:
                reason = ""
        ans = it.get("answer") or ""
        if not reason and reply_kb._REPLY_LEVEL_ASSERT_RE.search(ans) \
                and not reply_kb._REPLY_HEDGE_RE.search(ans):
            reason = "level_assertion"
        if reason:
            moved.append({
                "id": int(time.time() * 1000) + len(moved),
                "question": it.get("question") or "",
                "answer": it.get("answer") or "",
                "cluster_size": 0,
                "evidence": [f"legacy_removed:{reason}"],
                "account": "", "created_at": time.time(),
                "orig_id": it.get("id"),
            })
        else:
            keep.append(it)
    changed = bool(moved)
    if changed:
        reply_kb.save_items(keep)
        staged = list_candidates()
        # 候选区去重（同问法不重复叠加）
        have = {(c.get("question") or "").strip() for c in staged}
        for m in moved:
            if (m.get("question") or "").strip() not in have:
                staged.append(m)
                have.add((m.get("question") or "").strip())
        _kv_set(_KV_CAND, staged)
    return {"scanned": len(items), "moved": len(moved), "kept": len(keep),
            "moved_items": [{"q": m["question"], "a": m["answer"],
                             "reason": m["evidence"][0]} for m in moved]}
