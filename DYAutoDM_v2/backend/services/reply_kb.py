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

_KV_REPLY_KB = "ai_reply_chat_replies"   # [{id, question, answer, source, enabled, hits, created_at}]

_lock = threading.RLock()


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
    items = [it for it in list_items() if it.get("enabled", True)]
    # ① 精确/包含
    for it in items:
        q = (it.get("question") or "").lower().strip()
        if q and (q in qtext or qtext in q):
            _bump_hits(it.get("id"))
            return it.get("answer", "")
    # ② Jaccard 词级重叠（中文按字符集）+ 时间衰减加权
    now = time.time()
    best, best_score = None, 0.0
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
    if best and best_score >= threshold:
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
只输出 JSON 数组：[{"question":"客户问法","answer":"回复话术"}]，不要其他文字。

对话片段：
"""


def learn_from_history(account: str = "", limit: int = 200) -> dict:
    """扫描 dm_messages 成功会话，提取问答对 → LLM 提纯 → 入库。

    成功会话判定：会话内存在 role='me' 回复，且对方在回复后仍有跟进
    （说明回复有效，未被拉黑/无视）。每会话取最早的 them 问句 + 紧随的 me 回复。
    返回 {scanned, extracted, added}。
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
        # 找第一个有效 them 文本（跳过系统/图片）及其后最近的 me 回复
        q = None
        for role, text, mtype in msgs:
            t = (text or "").strip()
            # msg_type 实际取值：'text' / 'image' / '27'（系统引导）——只收 text；
            # 系统提示（"你已确认聊天…"）、[图片] 等前缀消息跳过
            is_text = (mtype in (1, 0, None, "text", "") or str(mtype) == "text")
            if not is_text or t.startswith("[") or t.startswith("你已确认"):
                continue
            if role == "them" and q is None:
                q = t
            elif role == "me" and q:
                # 简化判定：有问有答即收录（有效性由人工在库里删改把关）
                pairs.append((q, t))
                q = None
        if len(pairs) >= 60:
            break

    if not pairs:
        return {"scanned": len(rows), "extracted": 0, "added": 0}

    # LLM 提纯（失败则原样入库，人工可删）
    learned: list[dict] = []
    try:
        from services.ai_reply import AIClient, get_config
        client = AIClient(get_config())
        blob = "\n".join(f"客户: {q}\n我方: {a}" for q, a in pairs[:40])
        raw = client.chat_failover(
            blob, consumer_id="ai_main", user_id="kb-learn",
            system_prompt=LEARN_PROMPT)
        m = re.search(r"\[.*\]", raw or "", re.S)
        if m:
            learned = json.loads(m.group(0))
    except Exception:
        learned = []
    if not learned:
        learned = [{"question": q[:60], "answer": a[:200]} for q, a in pairs[:20]]

    # 查重后入库（问法近似的不重复加）
    existing = {(it.get("question") or "").strip() for it in list_items()}
    added = 0
    for it in learned:
        q = (it.get("question") or "").strip()
        if q and q not in existing:
            add_item(q, (it.get("answer") or "").strip(), source="auto")
            existing.add(q)
            added += 1
    return {"scanned": len(rows), "extracted": len(pairs), "added": added}
