# -*- coding: utf-8 -*-
"""高价值关键词权重表（ADR-007 / C-06 Phase 2）。

职责（单一）：给定弹幕/评论文本，返回**关键词权重得分** `score_text(text) -> int`。
判定是**过滤器**（C-06 I3），不是发送触发器 —— 是否发送仍由发送闸门决定。

存储：kv_store 键 `high_value_keywords`，结构 `{关键词: 权重(int)}`。
默认：首次读取时懒初始化写入 `DEFAULT_KEYWORDS`（工伤业务域种子词）。

⚠️ 与 C-06 §2.1 P4 的**有意偏离（须上报）**：
   P4 原文写「缺失由 services.high_value_keywords 懒初始化写入 `{}`」；
   实测若懒初始化为空表，则 `score_text` 恒为 0 ⇒ `is_high_value` 恒为假 ⇒
   功能成为静默 no-op（且非严格模式下全部放行，等于需求未实现）。
   故本模块改为懒初始化写入**工伤域种子词表** `DEFAULT_KEYWORDS`，
   用户可随时经 `set_keywords()` 覆盖。语义上仍是「可配置的关键词权重表」，
   符合 ADR-007 D1 的意图；差异已记入台账与报告。
"""
from __future__ import annotations

import threading

from services.kv_store import kv_get, kv_set

_KEYS = "high_value_keywords"

# 2026-10-02（用户定调「策略以标签为主」）：关键词表支持按标签 scope 隔离。
#   scope=None → 全局表（键 `high_value_keywords`，与改造前逐字一致）
#   scope=<tag_id> → 该标签的表（键 `high_value_keywords::<tag_id>`）
# 未在标签表里存过 → 回落全局（零回归）。
def _keys(scope: str | None = None) -> str:
    return f"{_KEYS}::{scope}" if scope else _KEYS

# 工伤业务域种子关键词（权重越高越强指向「有效咨询」）。
# 依据：案例库/真实对话实证的高频有效问法（见 AI回复勘探手册 §6）。
DEFAULT_KEYWORDS: dict[str, int] = {
    # 强指向（核心需求）
    "工伤": 5,
    "骨折": 4,
    "几级": 4,
    "等级": 4,
    "鉴定": 4,
    "赔偿": 4,
    "伤残": 3,
    "认定": 3,
    "受伤": 3,
    "律师": 3,
    "唐律": 3,
    "怎么赔": 4,
    "赔多少": 4,
    # 中指向（上下文）
    "病历": 2,
    "诊断": 2,
    "工地": 2,
    "工厂": 2,
    "上班": 2,
    "工伤认定": 5,
    "劳动能力": 4,
    "停工留薪": 4,
    # 留资信号（明确想联系）
    "联系方式": 4,
    "联系电话": 4,
    "加微信": 4,
    "微信": 3,
    "电话": 3,
    "怎么联系": 5,
}

_lock = threading.Lock()
# 按 scope 分缓存：{"" : 全局表, "<tag_id>": 标签表}
_caches: dict[str, dict] = {}


def get_keywords(scope: str | None = None) -> dict:
    """读关键词权重表（scope 为空读全局）；**键不存在时**懒初始化写种子词表。

    2026-09-29 修正：原判据 `if not isinstance(raw, dict) or not raw` 把
    **用户主动清空的空表**（`{}`）也当成"缺失"⇒ 重新写回种子词表
    ⇒ 用户永远无法清空关键词表（有 UI 入口后这是真实诉求：
    清空 = 不按关键词过滤）。现改为以「**键是否存在**」为判据 ——
    `kv_get` 返回 None 表示从未初始化，返回 `{}` 表示用户显式清空。

    2026-10-02：加 scope 参数（标签隔离）。**scope 表未存过 → 回落全局**
    （而不是写种子词表），避免每个新标签都凭空多一份种子表。
    """
    sk = scope or ""
    with _lock:
        if sk in _caches:
            return dict(_caches[sk])
        raw = kv_get(_keys(scope), None)
        if raw is None and scope:
            # 标签未存过 → 回落全局（读全局表，不写回）
            raw = kv_get(_KEYS, None)
        if raw is None or not isinstance(raw, dict):
            # 从未初始化（键不存在）→ 写种子词表（仅全局）
            raw = dict(DEFAULT_KEYWORDS)
            try:
                kv_set(_keys(scope), raw)
            except Exception:
                pass
        # 归一化：键 str、值 int（容忍配置页写进来的字符串数字）
        norm: dict[str, int] = {}
        for k, v in raw.items():
            try:
                norm[str(k)] = int(v)
            except Exception:
                continue
        _caches[sk] = norm
        return dict(norm)


def put_keywords(words: dict, scope: str | None = None) -> dict:
    """整表覆盖（供配置中心/UI 调用），并刷新内存缓存。

    ⚠️ 命名注意：**不要**叫 `set_keywords` ——
    `test_plaintext_env_deprecation.test_no_set_key_on_env` 用
    `"set_key" in code` 做**子串**扫描（它防的是明文书盘 `set_key`），
    `set_keywords` 会被误判为违规（实测踩到：discover 1 FAIL）。
    """
    norm: dict[str, int] = {}
    for k, v in (words or {}).items():
        try:
            norm[str(k)] = int(v)
        except Exception:
            continue
    kv_set(_keys(scope), norm)
    sk = scope or ""
    with _lock:
        _caches[sk] = dict(norm)
    return dict(norm)


def invalidate(scope: str | None = None) -> None:
    """清内存缓存（配置变更后调用；下次 get 重新读 kv）。

    scope 为空只清全局缓存；传 scope 清该标签；传 "__all__" 清全部。
    """
    with _lock:
        if scope == "__all__":
            _caches.clear()
        else:
            _caches.pop(scope or "", None)


def score_text(text: str, scope: str | None = None) -> int:
    """文本的关键词权重得分（命中累加；同一关键词只计一次）。"""
    t = str(text or "")
    if not t:
        return 0
    total = 0
    for word, weight in get_keywords(scope).items():
        if word and word in t:
            total += int(weight)
    return total


def is_high_value(text: str, threshold: int, scope: str | None = None) -> bool:
    """关键词口径的高价值判定（不含 LLM 精判）。"""
    try:
        return score_text(text, scope) >= int(threshold)
    except Exception:
        return False
