# -*- coding: utf-8 -*-
"""专业知识库（前置参考库，v0.40 思维导图 + 知识演化）。

用户拍板
--------
- 条目按 **思维导图格式** 存储：主题 → 子分类 → 正文内容 → 总结。
- 定位是向量模型的前置参考库：RAG 时**条目全文注入**《资料》段
  （相关性由向量模型/漏斗负责，不做本地切分），不直接回复。
- 旧的 QA 条目已迁往对话回复库（命中库），本库从空开始用新结构。

知识演化（移植自 MalogBot，2026-09-11）
---------------------------------------
1. **写入去重**：新增条目先算 embedding，与同类（同主题）条目余弦比对；
   >=DUP_THRESHOLD(0.90) 不新增，改 occurrence+1（重复即证据，不是垃圾）；
   0.80~0.90 标记 suspected_dup，等人工确认。
2. **时间衰减**：排序分 = 相关度 x e^(-LAMBDA x 距上次命中天数)，LAMBDA=0.02
   （约 50 天衰减到 0.37），基准优先取 last_accessed_at。
3. **陈旧管理**：全自动**扫描出报告**，删除必须人工确认 → 回收站 30 天 → 真删。
   - cold      : hits==0 且 存在 > 180 天
   - low_value : importance < 0.3 且 hits == 0 且 存在 > 90 天
   - duplicate : 两两余弦 >= 0.95（保留 importance 高的）
4. **回收站**：deleted_at 打标，restore() 可复原，purge_expired() 30 天后真删。

存储（kv `ai_pro_kb`）
----------------------
[{id, topic, category, content, summary, enabled,
  importance, hits, occurrence, suspected_dup, last_accessed_at,
  is_stale, stale_reason, deleted_at,
  created_at, updated_at}]
"""

from __future__ import annotations

import json
import math
import threading
import time
from typing import Optional

from loguru import logger

import database

_KV_PRO_KB = "ai_pro_kb"

_lock = threading.RLock()

# ---- 知识演化参数（移植自 MalogBot services/memory/search_engine.py）----
DUP_THRESHOLD = 0.90        # 写入去重：>= 视为重复，不新增只计数
DUP_SUSPECT_THRESHOLD = 0.80  # 疑似重复区间下限
MERGE_THRESHOLD = 0.95      # 维护扫描：重复合并阈值
TIME_DECAY_LAMBDA = 0.02    # 时间衰减速率（50 天 -> 0.37）
DEFAULT_IMPORTANCE = 0.7    # 文件导入条目
MANUAL_IMPORTANCE = 0.5     # 手工条目
COLD_DAYS = 180             # 冷数据：无命中且超过 180 天
LOW_VALUE_DAYS = 90         # 低价值：importance<0.3 且超过 90 天
RECYCLE_DAYS = 30           # 回收站保留天数

_DAY = 86400.0


# ---------------------------------------------------------------------------
# kv 基础
# ---------------------------------------------------------------------------

def _kv_get(key: str, default=None):
    try:
        conn = database.get_db()
        cur = conn.execute("SELECT value FROM kv_store WHERE key=?", (key,))
        row = cur.fetchone()
        if row is None:
            return default
        return json.loads(row[0])
    except Exception:
        return default


def _kv_set(key: str, value) -> None:
    # 2026-09-12：专业库条目变更 → 自动清向量缓存。
    # 放在 _kv_set 唯一写入点，覆盖 add/update/delete/bulk_add/维护等**所有**
    # 改动路径（逐个在函数里插调用会漏早返回分支，实测 update_item 有 2 个 return）。
    if key == "ai_pro_kb":   # = _KV_PRO_KB（避免常量定义顺序依赖）
        try:
            sem_cache_invalidate()
        except Exception:
            pass
    try:
        conn = database.get_db()
        conn.execute(
            "INSERT INTO kv_store(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value, ensure_ascii=False)),
        )
        conn.commit()
    except Exception:
        pass


def _new_id(items: list) -> int:
    existing = {it.get("id") for it in items}
    nid = int(time.time() * 1000)
    while nid in existing:
        nid += 1
    return nid


def _norm_item(it: dict) -> dict:
    """补齐老条目缺失的新字段（向后兼容）。"""
    it.setdefault("importance", MANUAL_IMPORTANCE)
    it.setdefault("hits", 0)
    it.setdefault("occurrence", 1)
    it.setdefault("suspected_dup", False)
    it.setdefault("last_accessed_at", it.get("created_at") or time.time())
    it.setdefault("is_stale", False)
    it.setdefault("stale_reason", "")
    it.setdefault("deleted_at", None)
    return it


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------

def list_items(include_deleted: bool = False) -> list:
    with _lock:
        items = [_norm_item(it) for it in (_kv_get(_KV_PRO_KB, []) or [])]
        if include_deleted:
            return items
        return [it for it in items if not it.get("deleted_at")]


def list_recycle_bin() -> list:
    """回收站条目（按删除时间倒序）。"""
    with _lock:
        items = [_norm_item(it) for it in (_kv_get(_KV_PRO_KB, []) or [])]
    bin_items = [it for it in items if it.get("deleted_at")]
    return sorted(bin_items, key=lambda x: x.get("deleted_at") or 0, reverse=True)


def topics() -> list[str]:
    """全库主题清单（供导入提纯时复用，保证全局一棵树）。"""
    return sorted({(it.get("topic") or "未分类") for it in list_items()})


def categories(topic: str = "") -> list[str]:
    """某主题下的子分类清单（topic 为空则返回全部）。"""
    t = (topic or "").strip()
    return sorted({
        (it.get("category") or "")
        for it in list_items()
        if (not t or (it.get("topic") or "未分类") == t)
    })


def tree() -> list:
    """按 主题→子分类 分组的树：[{topic, children:[{category, items:[...]}]}]"""
    groups: dict[str, dict] = {}
    for it in list_items():
        if not it.get("enabled", True):
            continue
        topic = it.get("topic") or "未分类"
        cat = it.get("category") or ""
        g = groups.setdefault(topic, {})
        g.setdefault(cat, []).append(it)
    return [
        {
            "topic": topic,
            "count": sum(len(v) for v in cats.values()),
            "children": [
                {"category": cat, "items": items}
                for cat, items in sorted(cats.items())
            ],
        }
        for topic, cats in sorted(groups.items())
    ]


# ---------------------------------------------------------------------------
# 写入（带去重）
# ---------------------------------------------------------------------------

def _embed(texts: list[str]) -> Optional[list]:
    """调用现有语义链路向量化（失败返回 None，调用方降级为不查重）。

    复用 ai_reply._embed_failover，避免另起一套 embedding 配置。
    """
    if not texts:
        return None
    try:
        from services import ai_reply as _ai
        vecs, _model = _ai._embed_failover(texts[:20], consumer_id="ai_sem")
        return vecs
    except Exception:
        return None


def _cosine(a, b) -> float:
    try:
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(y * y for y in b))
        if na <= 0 or nb <= 0:
            return 0.0
        return dot / (na * nb)
    except Exception:
        return 0.0


def _dedup_check(content: str, topic: str, items: list,
                 threshold: float = DUP_THRESHOLD) -> tuple[Optional[dict], float]:
    """在**同主题**已有条目里找最相似的。返回 (命中的条目 or None, 最大相似度)。

    只用正文（content）算相似度：正文是 RAG 注入的实际内容，总结/子分类会稀释。
    """
    candidates = [
        it for it in items
        if (not topic or (it.get("topic") or "未分类") == topic)
        and (it.get("content") or "").strip()
        and not it.get("deleted_at")
    ]
    if not candidates:
        return None, 0.0
    vecs = _embed([content] + [it["content"] for it in candidates])
    if not vecs or len(vecs) != len(candidates) + 1:
        return None, 0.0
    target = vecs[0]
    best, best_sim = None, 0.0
    for it, v in zip(candidates, vecs[1:]):
        sim = _cosine(target, v)
        if sim > best_sim:
            best, best_sim = it, sim
    if best_sim >= threshold:
        return best, best_sim
    return None, best_sim


def add_item(topic: str, category: str, content: str,
             summary: str = "", importance: Optional[float] = None,
             dedup: bool = True) -> dict:
    """新增条目。dedup=True 时先查重：
       >=0.90 -> 不新增，原条目 occurrence+1，返回 {"merged_into": id,...}
       0.80~0.90 -> 新增但标 suspected_dup
    """
    topic, category = (topic or "").strip(), (category or "").strip()
    content, summary = (content or "").strip(), (summary or "").strip()
    if not topic or not content:
        raise ValueError("主题和正文内容都不能为空")
    imp = DEFAULT_IMPORTANCE if importance is None else float(importance)
    with _lock:
        items = [_norm_item(it) for it in (_kv_get(_KV_PRO_KB, []) or [])]
        if dedup:
            hit, sim = _dedup_check(content, topic, items)
            if hit is not None:
                for it in items:
                    if it.get("id") == hit.get("id"):
                        it["occurrence"] = int(it.get("occurrence") or 1) + 1
                        it["updated_at"] = time.time()
                        break
                _kv_set(_KV_PRO_KB, items)
                return {
                    "merged_into": hit.get("id"),
                    "similarity": round(sim, 4),
                    "occurrence": [i for i in items if i.get("id") == hit.get("id")][0]
                                  .get("occurrence"),
                    "topic": hit.get("topic"), "category": hit.get("category"),
                    "content": hit.get("content"), "summary": hit.get("summary"),
                    "id": hit.get("id"), "deduped": True,
                }
            suspect = sim >= DUP_SUSPECT_THRESHOLD
        else:
            suspect = False
        item = {
            "id": _new_id(items),
            "topic": topic, "category": category,
            "content": content, "summary": summary,
            "enabled": True,
            "importance": imp,
            "hits": 0,
            "occurrence": 1,
            "suspected_dup": suspect,
            "last_accessed_at": time.time(),
            "is_stale": False,
            "stale_reason": "",
            "deleted_at": None,
            "created_at": time.time(), "updated_at": time.time(),
        }
        items.append(item)
        _kv_set(_KV_PRO_KB, items)
        return item


def update_item(item_id: int, topic: Optional[str] = None,
                category: Optional[str] = None,
                content: Optional[str] = None,
                summary: Optional[str] = None,
                enabled: Optional[bool] = None,
                importance: Optional[float] = None) -> Optional[dict]:
    with _lock:
        items = [_norm_item(it) for it in (_kv_get(_KV_PRO_KB, []) or [])]
        for it in items:
            if it.get("id") == item_id:
                if topic is not None:
                    it["topic"] = topic.strip()
                if category is not None:
                    it["category"] = category.strip()
                if content is not None:
                    it["content"] = content.strip()
                if summary is not None:
                    it["summary"] = summary.strip()
                if enabled is not None:
                    it["enabled"] = bool(enabled)
                if importance is not None:
                    it["importance"] = float(importance)
                it["updated_at"] = time.time()
                # 人工编辑即视为"恢复关注"：清掉陈旧标记
                it["is_stale"] = False
                it["stale_reason"] = ""
                _kv_set(_KV_PRO_KB, items)
                return it
        return None


def delete_item(item_id: int, to_recycle: bool = True) -> bool:
    """删除。默认进回收站（deleted_at 打标），30 天后由 purge_expired 真删。"""
    with _lock:
        items = [_norm_item(it) for it in (_kv_get(_KV_PRO_KB, []) or [])]
        found = False
        for it in items:
            if it.get("id") == item_id:
                found = True
                if to_recycle:
                    it["deleted_at"] = time.time()
                break
        if not found:
            return False
        if not to_recycle:
            items = [it for it in items if it.get("id") != item_id]
        _kv_set(_KV_PRO_KB, items)
        return True


# ---------------------------------------------------------------------------
# 回收站
# ---------------------------------------------------------------------------

def restore_item(item_id: int) -> bool:
    with _lock:
        items = [_norm_item(it) for it in (_kv_get(_KV_PRO_KB, []) or [])]
        for it in items:
            if it.get("id") == item_id and it.get("deleted_at"):
                it["deleted_at"] = None
                it["updated_at"] = time.time()
                _kv_set(_KV_PRO_KB, items)
                return True
        return False


def purge_expired(days: int = RECYCLE_DAYS) -> int:
    """清理回收站中超期条目（真删）。返回删除条数。"""
    cutoff = time.time() - days * _DAY
    with _lock:
        items = [_norm_item(it) for it in (_kv_get(_KV_PRO_KB, []) or [])]
        keep = [
            it for it in items
            if not (it.get("deleted_at") and (it.get("deleted_at") or 0) < cutoff)
        ]
        n = len(items) - len(keep)
        if n:
            _kv_set(_KV_PRO_KB, keep)
        return n


def clear_recycle_bin() -> int:
    with _lock:
        items = [_norm_item(it) for it in (_kv_get(_KV_PRO_KB, []) or [])]
        keep = [it for it in items if not it.get("deleted_at")]
        n = len(items) - len(keep)
        _kv_set(_KV_PRO_KB, keep)
        return n


# ---------------------------------------------------------------------------
# 命中 / 时间衰减
# ---------------------------------------------------------------------------

def touch(item_id: int) -> None:
    """条目被 RAG 注入时调用：hits+1 并刷新 last_accessed_at（喂时间衰减）。"""
    with _lock:
        items = [_norm_item(it) for it in (_kv_get(_KV_PRO_KB, []) or [])]
        for it in items:
            if it.get("id") == item_id:
                it["hits"] = int(it.get("hits") or 0) + 1
                it["last_accessed_at"] = time.time()
                _kv_set(_KV_PRO_KB, items)
                return


def time_decay_factor(item: dict, now: Optional[float] = None,
                      lam: float = TIME_DECAY_LAMBDA) -> float:
    """decay = e^(-lam * days_ago)，基准优先 last_accessed_at。"""
    now = now or time.time()
    ref = item.get("last_accessed_at") or item.get("created_at") or now
    days_ago = max(0.0, (now - float(ref)) / _DAY)
    return math.exp(-lam * days_ago)


def quality_ok(item: dict, min_importance: float = 0.3) -> bool:
    """RAG 注入质量门槛：importance 太低且从未命中 -> 不进提示词。"""
    try:
        imp = float(item.get("importance", MANUAL_IMPORTANCE))
    except Exception:
        imp = MANUAL_IMPORTANCE
    hits = int(item.get("hits") or 0)
    return not (imp < min_importance and hits == 0)


# ---------------------------------------------------------------------------
# 扫描 / 维护（只标记，不删除）
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 语义检索（2026-09-12）：RAG 注入前按客户问题筛最相关条目
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 语义检索（2026-09-12）：专业库向量筛选，缓存优先
# ---------------------------------------------------------------------------
# 用户拍板两点：
#   ① 专业库必须向量筛选，不能全库注入（旧实现 3 万字符撑爆上下文 → AI 返回空）。
#   ② **向量结果必须落库**，检索时优先匹配数据库缓存；
#      只有未命中/未收录的条目才实时调 embedding，算完回写缓存。
#
# 缓存位置：kv_store（与回复库 ai_reply_semv_ 同机制，便于统一维护）
#   key   = "pro_kb_semv_" + item_id
#   value = {"vec": [...], "model": "...", "ts": ...}
# 模型一致性：缓存记录的 model 与当前不同 → 视为失效，重算（防跨模型混算）。

_PRO_SEM_PREFIX = "pro_kb_semv_"
_PRO_SEM_MDL = "pro_kb_sem_model"


def _sem_vec_get(item_id, model: str = "") -> Optional[list]:
    """读条目向量缓存；模型不一致返回 None（缓存作废）。"""
    rec = _kv_get(_PRO_SEM_PREFIX + str(item_id))
    if not isinstance(rec, dict):
        return None
    if model and rec.get("model") and rec.get("model") != model:
        return None
    v = rec.get("vec")
    return v if isinstance(v, list) and v else None


def _sem_vec_set(item_id, vec: list, model: str = "") -> None:
    _kv_set(_PRO_SEM_PREFIX + str(item_id),
            {"vec": vec, "model": model or "", "ts": time.time()})


def sem_cache_invalidate(item_id=None) -> None:
    """内容变更后清向量缓存：单条传 item_id；传 None 清全部。

    增删改条目后必须调用，否则 build_system_prompt 会命中**旧内容**的向量，
    导致检索结果与正文不符（改了内容却仍按旧语义排序）。
    """
    try:
        conn = database.get_db()
        if item_id is None:
            # 2026-09-17 修补（审查 P2-10）：原为两条 `DELETE ... LIKE ?`
            # 前缀模糊删。虽前缀是模块常量、非用户输入，且只作用于 kv_store
            # 向量缓存，但项目铁律为「删除必须逐条确认、禁 LIKE 模糊删」
            # （历史事故：LIKE 误删 25 条真实聊天记录）。
            # 现改为**先精确选出待删 key、再逐条按主键删**，行为等价但
            # 彻底消除 LIKE 模式，且可记录实际删除条数。
            prefixes = (_PRO_SEM_PREFIX, "__q__")
            keys: list[str] = []
            for pfx in prefixes:
                rows = conn.execute(
                    "SELECT key FROM kv_store WHERE substr(key,1,?)=?",
                    (len(pfx), pfx)).fetchall()
                keys.extend(str(r["key"]) for r in rows)
            for k in keys:
                conn.execute("DELETE FROM kv_store WHERE key=?", (k,))
            conn.commit()
            if keys:
                logger.debug(f"[pro_kb] 向量缓存已清（逐条删 {len(keys)} 键）")
        else:
            conn.execute("DELETE FROM kv_store WHERE key=?",
                         (_PRO_SEM_PREFIX + str(item_id),))
            conn.commit()
    except Exception as e:
        logger.warning("PKB-001", f"[pro_kb] 清向量缓存失败: "
                                  f"{type(e).__name__}: {e}")


def current_sem_model() -> str:
    """当前语义链路实际使用的模型名（用于缓存一致性校验）。"""
    try:
        from services import ai_reply as _ai
        _v, mdl = _ai._embed_failover([" probe "], consumer_id="ai_sem")
        return mdl or ""
    except Exception:
        return ""


def semantic_topk(query: str, items: list, k: int = 5,
                  threshold: float = 0.0,
                  max_chars: int = 6000) -> Optional[list]:
    """客户问题 → 专业库最相关条目（缓存优先，未命中才实时算）。

    流程：
      1. 取当前 embedding 模型名（用于缓存校验）
      2. 对每个候选条目：先查缓存，未命中/失效的收集为 miss
      3. miss 非空 → 一次性批量调 embedding，结果回写缓存
      4. 全量算余弦 → 按分降序 → 累加不超 max_chars → 取 TopK

    embedding 完全不可用且全无缓存 → 返回 None（调用方降级，绝不阻塞）。
    返回列表（可能少于 k 条，也可能为空 list 表示无命中）。
    """
    if not query or not items:
        return None
    cands = [it for it in items
             if (it.get("content") or "").strip() and not it.get("deleted_at")]
    if not cands:
        return None

    model = current_sem_model()

    # ① 缓存优先
    vecs, miss = {}, []
    for it in cands:
        v = _sem_vec_get(it.get("id"), model)
        if v is None:
            miss.append(it)
        else:
            vecs[it["id"]] = v

    # ② 未命中的才实时算 —— **必须分批**：_embed 内部 texts[:20] 截断，
    # 一次传 >20 条会返回不足（len(got) != len(miss)），导致整批判定失败。
    # （2026-09-12 实测：56 条全 miss 时一次性调用只回 20 条 → 全部降级为 None）
    if miss:
        for i in range(0, len(miss), 20):
            batch = miss[i:i + 20]
            try:
                got = _embed([it["content"] for it in batch])
            except Exception:
                got = None
            if got and len(got) == len(batch):
                for it, v in zip(batch, got):
                    vecs[it["id"]] = v
                    _sem_vec_set(it["id"], v, model)   # ③ 回写缓存
    if not vecs:
        return None              # 全无缓存且实时算全失败 → 降级

    # ④ query 向量（也缓存，同一问题复用）
    qkey = "__q__" + str(abs(hash(query)) % (10 ** 8))
    qvec = _sem_vec_get(qkey, model)
    if qvec is None:
        try:
            qv = _embed([query])
        except Exception:
            qv = None
        if not qv or not qv[0]:
            return None
        qvec = qv[0]
        _sem_vec_set(qkey, qvec, model)

    # ⑤ 余弦排序 + 预算截断
    scored = []
    for it in cands:
        v = vecs.get(it["id"])
        if not v:
            continue
        sim = _cosine(qvec, v)
        if sim >= threshold:
            scored.append((sim, it))
    if not scored:
        return []
    scored.sort(key=lambda x: x[0], reverse=True)

    out, used = [], 0
    for sim, it in scored:
        c = len(it.get("content") or "")
        if out and used + c > max_chars:
            continue
        if not out and c > max_chars:
            it = dict(it)
            it["content"] = (it.get("content") or "")[:max_chars]
            c = max_chars
        out.append(it)
        used += c
        if len(out) >= k:
            break
    return out


def sem_cache_stats(items: Optional[list] = None) -> dict:
    """专业库向量缓存统计（供前端/排查用）。"""
    try:
        pool = items if items is not None else list_items()
        pool = [it for it in pool if (it.get("content") or "").strip()]
        model = current_sem_model()
        hit = sum(1 for it in pool if _sem_vec_get(it.get("id"), model))
        return {"ok": True, "total": len(pool), "cached": hit,
                "miss": len(pool) - hit, "model": model or "(unknown)"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def sem_cache_rebuild() -> dict:
    """全量重建专业库向量缓存（导入/编辑后调用，或前端按钮）。"""
    try:
        pool = [it for it in list_items()
                if (it.get("content") or "").strip() and not it.get("deleted_at")]
        if not pool:
            return {"ok": True, "embedded": 0, "total": 0}
        model = current_sem_model()
        n = 0
        for i in range(0, len(pool), 20):          # 与 _embed 内部上限一致，分批
            batch = pool[i:i + 20]
            try:
                got = _embed([it["content"] for it in batch])
            except Exception:
                continue
            if got and len(got) == len(batch):
                for it, v in zip(batch, got):
                    _sem_vec_set(it["id"], v, model)
                    n += 1
        return {"ok": True, "embedded": n, "total": len(pool), "model": model}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def scan_stale(cold_days: int = COLD_DAYS,
               low_value_days: int = LOW_VALUE_DAYS) -> dict:
    """三档扫描，**只返回报告不改数据**（用户确认后才 apply）。

    返回 {'cold': [...], 'low_value': [...], 'duplicate': [...],
          'scanned': n}
    """
    now = time.time()
    items = [it for it in list_items() if not it.get("deleted_at")]
    cold, low_value = [], []
    for it in items:
        created = float(it.get("created_at") or now)
        age_days = (now - created) / _DAY
        hits = int(it.get("hits") or 0)
        try:
            imp = float(it.get("importance", MANUAL_IMPORTANCE))
        except Exception:
            imp = MANUAL_IMPORTANCE
        row = {
            "id": it.get("id"), "topic": it.get("topic"),
            "category": it.get("category"),
            "summary": it.get("summary") or (it.get("content") or "")[:60],
            "hits": hits, "importance": imp,
            "age_days": round(age_days, 1),
        }
        if hits == 0 and age_days > cold_days:
            row2 = dict(row)
            row2["reason"] = "cold"
            cold.append(row2)
        if imp < 0.3 and hits == 0 and age_days > low_value_days:
            row3 = dict(row)
            row3["reason"] = "low_value"
            low_value.append(row3)

    # 重复：同主题内两两余弦 >= MERGE_THRESHOLD
    duplicate = _scan_duplicates(items)

    return {
        "cold": cold, "low_value": low_value, "duplicate": duplicate,
        "scanned": len(items),
        "thresholds": {
            "cold_days": cold_days, "low_value_days": low_value_days,
            "merge_similarity": MERGE_THRESHOLD, "dup_similarity": DUP_THRESHOLD,
        },
    }


def _scan_duplicates(items: list) -> list:
    """同主题内两两比对，返回 [{keep_id, drop_id, similarity, topic, ...}]。"""
    out = []
    by_topic: dict[str, list] = {}
    for it in items:
        if (it.get("content") or "").strip():
            by_topic.setdefault(it.get("topic") or "未分类", []).append(it)
    for topic, group in by_topic.items():
        if len(group) < 2:
            continue
        vecs = _embed([it["content"] for it in group])
        if not vecs or len(vecs) != len(group):
            continue
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                sim = _cosine(vecs[i], vecs[j])
                if sim >= MERGE_THRESHOLD:
                    a, b = group[i], group[j]
                    try:
                        ia = float(a.get("importance", MANUAL_IMPORTANCE))
                        ib = float(b.get("importance", MANUAL_IMPORTANCE))
                    except Exception:
                        ia = ib = MANUAL_IMPORTANCE
                    keep, drop = (a, b) if ia >= ib else (b, a)
                    out.append({
                        "topic": topic,
                        "keep_id": keep.get("id"),
                        "drop_id": drop.get("id"),
                        "keep_summary": keep.get("summary")
                        or (keep.get("content") or "")[:60],
                        "drop_summary": drop.get("summary")
                        or (drop.get("content") or "")[:60],
                        "similarity": round(sim, 4),
                        "reason": "duplicate",
                    })
    return out


def apply_maintenance(item_ids: list, action: str = "recycle") -> dict:
    """执行维护：把确认过的条目进回收站 / 打陈旧标记。"""
    done = []
    with _lock:
        items = [_norm_item(it) for it in (_kv_get(_KV_PRO_KB, []) or [])]
        for it in items:
            if it.get("id") in set(item_ids):
                if action == "recycle":
                    it["deleted_at"] = time.time()
                elif action == "mark":
                    it["is_stale"] = True
                    it["stale_reason"] = "manual"
                it["updated_at"] = time.time()
                done.append(it.get("id"))
        _kv_set(_KV_PRO_KB, items)
    return {"ok": True, "action": action, "count": len(done), "ids": done}


def mark_stale(ids: list, reason: str = "auto") -> int:
    """按扫描报告打陈旧标记（不改数据内容，只是标记，供界面展示）。"""
    n = 0
    id_set = set(ids or [])
    with _lock:
        items = [_norm_item(it) for it in (_kv_get(_KV_PRO_KB, []) or [])]
        for it in items:
            if it.get("id") in id_set:
                it["is_stale"] = True
                it["stale_reason"] = reason
                n += 1
        _kv_set(_KV_PRO_KB, items)
    return n


def clear_items() -> int:
    with _lock:
        items = _kv_get(_KV_PRO_KB, []) or []
        _kv_set(_KV_PRO_KB, [])
        return len(items)


def rename_topic(old: str, new: str) -> int:
    """主题改名（级联该主题下所有条目）——统一树的关键操作。"""
    old, new = (old or "").strip(), (new or "").strip()
    if not old or not new or old == new:
        return 0
    n = 0
    with _lock:
        items = [_norm_item(it) for it in (_kv_get(_KV_PRO_KB, []) or [])]
        for it in items:
            if (it.get("topic") or "未分类") == old:
                it["topic"] = new
                it["updated_at"] = time.time()
                n += 1
        if n:
            _kv_set(_KV_PRO_KB, items)
    return n


def bulk_add(items: list) -> dict:
    """批量导入（文件提纯结果）。条目：{topic, category, content, summary}。

    逐条走 add_item 的去重逻辑；返回 {added, merged, suspected, details}。
    """
    added = merged = suspected = 0
    details = []
    for raw in items or []:
        topic = (raw.get("topic") or raw.get("question") or "").strip()
        content = (raw.get("content") or raw.get("answer") or "").strip()
        if not topic or not content:
            continue
        r = add_item(
            topic=topic,
            category=(raw.get("category") or "").strip(),
            content=content,
            summary=(raw.get("summary") or "").strip(),
            importance=raw.get("importance", DEFAULT_IMPORTANCE),
        )
        if r.get("deduped"):
            merged += 1
            details.append({"action": "merged", "into": r.get("id"),
                            "similarity": r.get("similarity"),
                            "topic": r.get("topic")})
        else:
            added += 1
            if r.get("suspected_dup"):
                suspected += 1
                details.append({"action": "suspected", "id": r.get("id"),
                                "topic": r.get("topic")})
    return {"added": added, "merged": merged, "suspected": suspected,
            "details": details}
