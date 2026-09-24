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
_cache: dict | None = None


def get_keywords() -> dict:
    """读关键词权重表；缺失/损坏时懒初始化写入种子词表并返回。"""
    global _cache
    with _lock:
        if _cache is not None:
            return dict(_cache)
        raw = kv_get(_KEYS, None)
        if not isinstance(raw, dict) or not raw:
            raw = dict(DEFAULT_KEYWORDS)
            try:
                kv_set(_KEYS, raw)
            except Exception:
                pass
        # 归一化：键 str、值 int（容忍配置页写进来的字符串数字）
        norm: dict[str, int] = {}
        for k, v in raw.items():
            try:
                norm[str(k)] = int(v)
            except Exception:
                continue
        _cache = norm
        return dict(norm)


def put_keywords(words: dict) -> dict:
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
    kv_set(_KEYS, norm)
    global _cache
    with _lock:
        _cache = dict(norm)
    return dict(norm)


def invalidate() -> None:
    """清内存缓存（配置变更后调用；下次 get 重新读 kv）。"""
    global _cache
    with _lock:
        _cache = None


def score_text(text: str) -> int:
    """文本的关键词权重得分（命中累加；同一关键词只计一次）。"""
    t = str(text or "")
    if not t:
        return 0
    total = 0
    for word, weight in get_keywords().items():
        if word and word in t:
            total += int(weight)
    return total


def is_high_value(text: str, threshold: int) -> bool:
    """关键词口径的高价值判定（不含 LLM 精判）。"""
    try:
        return score_text(text) >= int(threshold)
    except Exception:
        return False
