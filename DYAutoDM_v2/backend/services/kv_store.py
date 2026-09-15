"""KV 存储读写 —— 共享工具

## 为什么独立（2026-09-15 共享提取）

实测发现 `_kv_get` / `_kv_set` 在 6 个服务模块里各写一份：
  ai_agent / ai_reply / config_tag / pro_kb / reply_kb / model_hub

其中 **config_tag / pro_kb / reply_kb / ai_agent 四处实现逐字节相同**
（同一张 kv_store 表、同一段 json 编解码与异常兜底）。按用户要求
「所有能共享的全部共享」，抽到本模块统一维护。

## 保留差异（不做强行合并）
`ai_reply._kv_get` 带 AI-004 日志告警、`model_hub` 的读写签名不同
（无 key 参数、整体读写一份 dict）——它们**不是**同一实现，保留各自版本。

## 语义
- key 不存在 → 返回 default（不抛）
- 任何异常（表缺失/JSON 损坏/DB 未初始化）→ 静默返回 default
  （与原实现一致：KV 是尽力而为的配置存储，不应因读失败中断业务）
"""
from __future__ import annotations

import json
from typing import Any

import database


def kv_get(key: str, default: Any = None) -> Any:
    """读 KV。key 不存在或任何异常 → 返回 default。"""
    try:
        conn = database.get_db()
        cur = conn.execute("SELECT value FROM kv_store WHERE key=?", (key,))
        row = cur.fetchone()
        if row is None:
            return default
        return json.loads(row[0])
    except Exception:
        return default


def kv_set(key: str, value: Any) -> None:
    """写 KV（UPSERT，json 序列化）。任何异常静默忽略（与原实现一致）。"""
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
