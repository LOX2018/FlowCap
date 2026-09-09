# -*- coding: utf-8 -*-
"""模型链路中心（Model Hub）——模型配置唯一真源（v0.38.4）。

用户拍板（2026-09-09）：
  - 「把模型配置设定为独立模块，AI 和 IM 通知都直接对接该模块，
    复用该模块中写入的提供商，只能各自选择采用模型和链路」
  - AI 页的模型配置删除，全部收拢到设置页。

设计：
  - **链路（endpoint）**= 一条可用的模型服务连接：名称 / base_url /
    api_protocol / api_key。提供商预设（PROVIDER_PRESETS）快速新建，
    也可完全自定义；key 明文存 kv（与 ai_reply_config 同级别，本机库）。
  - **消费方（consumer）**= 使用方绑定：ai_main（AI 主模型）、
    ai_vision（AI 视觉）、ai_sem（AI 语义/嵌入）、notify_cmd（IM 指令解析）。
    每个消费方绑一条链路 + 一个模型名；未绑定时回落：
    ai_* → 默认链路第一条；notify_cmd → ai_main 的绑定（零模型重复配置）。
  - 消费方把绑定解析成 `base_url/model/api_key/api_protocol` 四键 dict
    注入原消费点（AIClient / describe_image / _embedRemote / cmd_parser），
    **所有既有调用代码零改动**。

kv 键：`model_hub`（{"endpoints": [...], "consumers": {...}}）。
"""

from __future__ import annotations

import threading
from typing import Any, Optional

from loguru import logger

_KV_KEY = "model_hub"
_lock = threading.Lock()

# 旧 ai_reply.PROVIDER_PRESETS 的镜像（import ai_reply 会拖起 database，
# 这里自带一份并允许 api 层转发；后续以本模块为准）
PROVIDER_PRESETS: list[dict] = [
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
     "models_chat": ["kimi-k2-0905-preview", "moonshot-v1-32k"],
     "models_vision": []},
    {"id": "volces", "name": "火山方舟（豆包）",
     "base_url": "https://ark.cn-beijing.volces.com/api/v3",
     "api_protocol": "openai", "needs_key": True,
     "key_hint": "…（方舟控制台，模型用接入点 ID）",
     "models_chat": ["doubao-1-5-pro-32k-250115"],
     "models_vision": ["doubao-1-5-vision-pro-32k-250115"]},
    {"id": "minimax", "name": "MiniMax（Anthropic 兼容端点）",
     "base_url": "https://api.minimaxi.com/anthropic",
     "api_protocol": "anthropic", "needs_key": True,
     "key_hint": "eyJ…（MiniMax 开放平台）",
     "models_chat": ["MiniMax-M2.7", "MiniMax-M3"], "models_vision": []},
    {"id": "custom", "name": "自定义（手填 Base URL + 模型名）",
     "base_url": "", "api_protocol": "openai", "needs_key": True,
     "key_hint": "按服务商要求", "models_chat": [], "models_vision": []},
]

# 消费方注册表（id → 说明）。前端据此渲染绑定 UI。
CONSUMERS: list[dict] = [
    {"id": "ai_main", "label": "AI 主模型（私信回复）", "module": "ai"},
    {"id": "ai_vision", "label": "AI 视觉（图片理解）", "module": "ai"},
    {"id": "ai_sem", "label": "AI 语义检索（知识库向量化）", "module": "ai"},
    {"id": "notify_cmd", "label": "IM 通知指令解析", "module": "notify"},
]


# ---------------------------------------------------------------------------
# 存储
# ---------------------------------------------------------------------------

def _kv_get() -> dict:
    try:
        from database import get_kv_json

        data = get_kv_json(_KV_KEY, {})
        return data if isinstance(data, dict) else {}
    except Exception as e:  # noqa: BLE001
        logger.warning("HUB-001", f"[model_hub] kv 读取失败: {e}")
        return {}


def _kv_set(data: dict) -> None:
    try:
        from database import set_kv_json

        set_kv_json(_KV_KEY, data)
    except Exception as e:  # noqa: BLE001
        logger.warning("HUB-002", f"[model_hub] kv 写入失败: {e}")


# ---------------------------------------------------------------------------
# 链路 CRUD
# ---------------------------------------------------------------------------

def list_endpoints() -> list[dict]:
    """全部链路。首次调用时从旧 ai_reply 配置一次性迁移。"""
    with _lock:
        data = _kv_get()
        eps = data.get("endpoints")
        if eps is None:
            eps = _migrate_legacy()
        return [dict(e) for e in eps]


def save_endpoint(ep: dict) -> dict:
    """新建/更新一条链路。id 为空自动生成；返回保存后的完整链路。"""
    with _lock:
        data = _kv_get()
        eps = data.setdefault("endpoints", [])
        eid = str(ep.get("id") or "")
        if not eid:
            import uuid

            eid = f"ep_{uuid.uuid4().hex[:12]}"
        rec = {
            "id": eid,
            "name": str(ep.get("name") or "未命名链路"),
            "base_url": str(ep.get("base_url") or "").strip(),
            "api_protocol": str(ep.get("api_protocol") or "openai").lower(),
            "api_key": str(ep.get("api_key") or ""),
        }
        for i, old in enumerate(eps):
            if old.get("id") == eid:
                # key 为空/脱敏 → 保留原值
                if not rec["api_key"] or "•" in rec["api_key"]:
                    rec["api_key"] = old.get("api_key") or ""
                eps[i] = rec
                break
        else:
            eps.append(rec)
        _kv_set(data)
        return dict(rec)


def delete_endpoint(ep_id: str) -> dict:
    """删除链路；绑定了它的消费方自动解绑（回落默认链路）。"""
    with _lock:
        data = _kv_get()
        eps = data.get("endpoints") or []
        eps = [e for e in eps if e.get("id") != ep_id]
        data["endpoints"] = eps
        cons = data.get("consumers") or {}
        unbound = [cid for cid, b in cons.items()
                   if isinstance(b, dict) and b.get("endpoint_id") == ep_id]
        for cid in unbound:
            cons.pop(cid, None)
        data["consumers"] = cons
        _kv_set(data)
        return {"unbound_consumers": unbound}


def _default_endpoint_id(eps: list[dict]) -> str:
    if not eps:
        return ""
    return str(eps[0].get("id") or "")


# ---------------------------------------------------------------------------
# 消费方绑定
# ---------------------------------------------------------------------------

def get_bindings() -> dict:
    """消费方绑定表 {consumer_id: {endpoint_id, model}}。"""
    with _lock:
        return dict(_kv_get().get("consumers") or {})


def set_binding(consumer_id: str, endpoint_id: str, model: str) -> dict:
    """设置消费方绑定。endpoint_id 为空 = 清除绑定（走回落）。"""
    valid = {c["id"] for c in CONSUMERS}
    if consumer_id not in valid:
        return {"ok": False, "error": f"未知消费方: {consumer_id}"}
    with _lock:
        data = _kv_get()
        cons = data.setdefault("consumers", {})
        if endpoint_id:
            cons[consumer_id] = {"endpoint_id": str(endpoint_id),
                                 "model": str(model or "")}
        else:
            cons.pop(consumer_id, None)
        data["consumers"] = cons
        _kv_set(data)
        return {"ok": True, "consumers": dict(cons)}


def resolve(consumer_id: str) -> Optional[dict]:
    """解析消费方最终生效的连接参数 → {base_url, model, api_key, api_protocol}。

    落链路取绑定的 endpoint_id；未绑定/链路已删 → 默认链路（第一条）。
    notify_cmd 未绑定时回落 ai_main 的绑定（IM 通知零重复配置）。
    返回 None = 无法解析（调用方走自身兜底，如规则解析）。
    """
    data = _kv_get()
    eps = data.get("endpoints") or []
    if not eps:
        return None
    cons = data.get("consumers") or {}

    cid = consumer_id
    if cid == "notify_cmd" and "notify_cmd" not in cons and "ai_main" in cons:
        cid = "ai_main"  # 未显式绑定的通知指令解析跟随 AI 主模型

    b = cons.get(cid)
    ep_id = (b or {}).get("endpoint_id") or _default_endpoint_id(eps)
    ep = next((e for e in eps if e.get("id") == ep_id), None)
    if not ep:
        return None
    out = {
        "base_url": ep.get("base_url") or "",
        "api_key": ep.get("api_key") or "",
        "api_protocol": ep.get("api_protocol") or "openai",
        "model": (b or {}).get("model") or "",
    }
    if not out["base_url"] or not out["model"]:
        return None
    return out


# ---------------------------------------------------------------------------
# 一次性迁移（旧 ai_reply_config 的 base_url/model/api_key* → 链路+绑定）
# ---------------------------------------------------------------------------

_MIGRATED_KEY = "model_hub.migrated"


def _migrate_legacy() -> list[dict]:
    """旧配置一次性迁入链路中心（只在首次 list_endpoints 时跑）。

    ai_reply 的主模型 → ep + ai_main 绑定；视觉/语义各建独立 ep 并绑定。
    notify 的旧 llm 迁移由 notify 侧自行完成（v0.38.3 已做，勿动）。
    """
    try:
        from database import get_kv, set_kv

        if get_kv(_MIGRATED_KEY):
            return []
        set_kv(_MIGRATED_KEY, True)
        from services import ai_reply

        cfg = ai_reply.get_config()
        eps: list[dict] = []
        cons: dict = {}

        def _add_ep(name: str, base: str, key: str, proto: str) -> str:
            import uuid

            eid = f"ep_{uuid.uuid4().hex[:12]}"
            eps.append({"id": eid, "name": name, "base_url": base,
                        "api_protocol": proto, "api_key": key})
            return eid

        if cfg.get("base_url") and cfg.get("model"):
            eid = _add_ep("AI 主模型（迁移）", cfg["base_url"],
                          cfg.get("api_key") or "",
                          cfg.get("api_protocol") or "openai")
            cons["ai_main"] = {"endpoint_id": eid, "model": cfg["model"]}
        if cfg.get("vision_base_url") and cfg.get("vision_model"):
            eid = _add_ep("AI 视觉（迁移）", cfg["vision_base_url"],
                          cfg.get("vision_api_key") or "", "openai")
            cons["ai_vision"] = {"endpoint_id": eid, "model": cfg["vision_model"]}
        if cfg.get("sem_base_url") and cfg.get("sem_model"):
            eid = _add_ep("AI 语义（迁移）", cfg["sem_base_url"],
                          cfg.get("sem_api_key") or "", "openai")
            cons["ai_sem"] = {"endpoint_id": eid, "model": cfg["sem_model"]}

        data = {"endpoints": eps, "consumers": cons}
        _kv_set(data)
        logger.info(f"[model_hub] 旧模型配置已迁入（{len(eps)} 条链路）")
        return eps
    except Exception as e:  # noqa: BLE001
        logger.warning("HUB-003", f"[model_hub] 迁移失败（不影响运行）: {e}")
        return []
