# -*- coding: utf-8 -*-
"""模型中心（Model Hub v2）——提供商 / 模型 / 避障链路 / 兜底 / 消费方绑定。

v0.39.0 重构（用户拍板）：
  - **提供商（provider）**：独立模块，只管三件事 —— 密钥（base_url/协议/api_key）、
    拉取该提供商的模型列表（OpenAI 兼容 GET /models）、测试密钥有效性。
  - **模型注册（models）**：提供商提供的模型统一注册，带能力标签
    caps ⊆ {llm, vision, sem}（拉取时启发式预分类，UI 可手动改）。
  - **避障链路（routes）**：按 LLM / 视觉 / 语义三条链配置，每条链最多 6 个模型，
    消费时按序尝试（前一个失败/限流/空回复合自动切下一个）。
  - **兜底模型（fallback）**：必须同时支持 llm+vision（多模态）——附加在
    llm 链与 vision 链末尾；语义链路不用兜底（embedding 模型不通用）。
  - **消费方（consumers）**：绑定二选一 ——
    mode="route"（选一条链路，享避障+兜底）或 mode="fixed"（固定某提供商的
    一个模型，不避障）。未绑定的消费方默认走 llm 链。

兼容层：
  - resolve(consumer_id) 保持旧签名：返回链首模型的
    {base_url, model, api_key, api_protocol}（notify_cmd 等轻量消费方零改动）。
  - resolve_chain(consumer_id) 返回完整避障候选列表（AI 主链路/视觉/语义消费）。
  - 旧 v1 结构（endpoints+consumers）与更旧的 ai_reply_config 字段一次性迁移。

kv 键：`model_hub`。
"""

from __future__ import annotations

import re
import threading
import time
import uuid
from typing import Optional

from loguru import logger

_KV_KEY = "model_hub"
_KV_VERSION = 2
_lock = threading.Lock()

from .model_hub_data import (
    ROUTE_KINDS, MAX_CHAIN, CAPS, CONSUMERS, PROVIDER_PRESETS,
    _VISION_PAT, _SEM_PAT, guess_caps,
)

def _kv_get() -> dict:
    try:
        from database import get_kv_json

        data = get_kv_json(_KV_KEY, {})
        return data if isinstance(data, dict) else {}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[HUB-001] " + f"[model_hub] kv 读取失败: {e}")
        return {}


def _kv_set(data: dict) -> None:
    try:
        from database import set_kv_json

        set_kv_json(_KV_KEY, data)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[HUB-002] " + f"[model_hub] kv 写入失败: {e}")


def mask_secret(token: str) -> str:
    """脱敏展示：只留前 4 后 4（与 mcp/config.py `._mask` 同语义）。

    规则：空串→空串；长度 <= 8 → 全 `*`；否则 前4 + '*'*(len-8) + 后4。

    ⚠️ 仅用于**对外视图**（API 响应 / 日志）。保存与校验路径必须取存储层
    原文，绝不可把本函数的返回值回写进 kv —— 否则真实密钥会被掩码永久
    覆盖（T6-b 事故面）。
    """
    s = str(token or "")
    if not s:
        return ""
    if len(s) <= 8:
        return "*" * len(s)
    return f"{s[:4]}{'*' * (len(s) - 8)}{s[-4:]}"


def _public_provider(p: dict) -> dict:
    """提供商的对外视图：api_key 一律脱敏，另给 `api_key_set` 指示是否已配置。

    前端（ProviderSection.tsx）本就只显示「· 🔑」与 `••••••••` 占位，
    从不消费明文；`api_key_set` 让它仍能区分「已配置 / 未配置」。
    返回的是**副本**，绝不改动存储层的 provider dict。
    """
    d = dict(p)
    raw = str(d.get("api_key") or "")
    d["api_key"] = mask_secret(raw)
    d["api_key_set"] = bool(raw)
    return d


def _normalize(data: dict) -> dict:
    """确保结构完整、字段合法（防手改 kv 弄崩）。"""
    out = {
        "v": _KV_VERSION,
        "providers": [],
        "models": [],
        "routes": {k: {"models": []} for k in ROUTE_KINDS},
        "fallback": {"model_id": ""},
        "consumers": {},
        "migrated_v1": bool(data.get("migrated_v1")),
    }
    seen_p = set()
    for p in data.get("providers") or []:
        pid = str(p.get("id") or "")
        if not pid or pid in seen_p:
            continue
        seen_p.add(pid)
        out["providers"].append({
            "id": pid,
            "name": str(p.get("name") or "未命名提供商"),
            "base_url": str(p.get("base_url") or "").strip(),
            "api_protocol": str(p.get("api_protocol") or "openai").lower(),
            "api_key": str(p.get("api_key") or ""),
            "key_status": p.get("key_status") if isinstance(p.get("key_status"), dict) else {},
            "models_fetched_at": p.get("models_fetched_at") or 0,
        })
    seen_m = set()
    for m in data.get("models") or []:
        mid = str(m.get("id") or "")
        if not mid or mid in seen_m:
            continue
        if m.get("provider_id") not in seen_p:
            continue  # 模型必须挂在存在的提供商上
        seen_m.add(mid)
        caps = [c for c in (m.get("caps") or []) if c in CAPS]
        out["models"].append({
            "id": mid,
            "provider_id": m["provider_id"],
            "model": str(m.get("model") or "").strip(),
            "caps": caps or ["llm"],
            "source": str(m.get("source") or "manual"),
        })
    for kind in ROUTE_KINDS:
        ids = []
        for mid in (data.get("routes") or {}).get(kind, {}).get("models") or []:
            mid = str(mid)
            if mid in seen_m and mid not in ids:
                ids.append(mid)
        out["routes"][kind]["models"] = ids[:MAX_CHAIN]
    fb = data.get("fallback") or {}
    fbid = str(fb.get("model_id") or "")
    out["fallback"] = {"model_id": fbid if fbid in seen_m else ""}
    valid_c = {c["id"] for c in CONSUMERS}
    for cid, c in (data.get("consumers") or {}).items():
        if cid not in valid_c or not isinstance(c, dict):
            continue
        if c.get("mode") == "fixed":
            if c.get("model_id") in seen_m:
                out["consumers"][cid] = {"mode": "fixed", "model_id": c["model_id"]}
        elif c.get("mode") == "route":
            if c.get("route") in ROUTE_KINDS:
                out["consumers"][cid] = {"mode": "route", "route": c["route"]}
    return out


def _load() -> dict:
    data = _kv_get()
    if not data.get("providers") and not data.get("models") and not data.get("migrated_v1"):
        # 全新库：尝试从旧结构迁移
        data = _migrate_v1(data)
    return _normalize(data)


def _save(data: dict) -> None:
    _kv_set(_normalize(data))


# ---------------------------------------------------------------------------
# 迁移：v1（endpoints+consumers）与更旧（ai_reply_config 字段）→ v2
# ---------------------------------------------------------------------------

_MIGRATED_KEY = "model_hub.migrated"


def _migrate_v1(data: dict) -> dict:
    """一次性迁移。来源优先级：kv 里的 v1 结构 > 旧 ai_reply_config 字段。"""
    try:
        from database import get_kv, set_kv

        if get_kv(_MIGRATED_KEY):
            return data
        # 2026-09-17 修补（OCR 审查 HIGH —— 迁移标志提前置位）：
        # 原实现在此处（迁移**尚未执行**前）就 `set_kv(_MIGRATED_KEY, True)`。
        # 若后续迁移体抛异常（下面有 except 兜底），标志已是 True →
        # **下次启动直接 `return data` 永久跳过迁移**，旧配置再也迁不进来
        # （静默数据迁移丢失，且无任何提示）。
        # 现改为：只在迁移**成功持久化**后才置位（见函数末尾 _save 之后）。
    except Exception:  # noqa: BLE001
        return data

    try:
        eps = data.get("endpoints") or []
        old_cons = data.get("consumers") or {}
        if not eps:
            # 更旧：从 ai_reply_config 迁
            from services import ai_reply

            cfg = ai_reply.get_config()
            if not (cfg.get("base_url") and cfg.get("model")):
                return data
            eps = [{"id": "ep_main", "name": "默认提供商（迁移）",
                    "base_url": cfg.get("base_url"), "api_protocol":
                    cfg.get("api_protocol") or "openai",
                    "api_key": cfg.get("api_key") or ""}]
            old_cons = {"ai_main": {"endpoint_id": "ep_main", "model": cfg.get("model")}}

        providers: list[dict] = []
        models: list[dict] = []
        routes: dict[str, list[str]] = {k: [] for k in ROUTE_KINDS}
        ep2pid: dict[str, str] = {}
        # 旧消费方模型名 → (provider, caps) 记录，迁移后挂进对应链
        cons_route = {"ai_main": "llm", "notify_cmd": "llm",
                      "ai_vision": "vision", "ai_sem": "sem"}

        def _ensure_provider(ep: dict) -> str:
            eid = str(ep.get("id") or "")
            if eid in ep2pid:
                return ep2pid[eid]
            pid = "pv_" + uuid.uuid4().hex[:12]
            ep2pid[eid] = pid
            providers.append({
                "id": pid, "name": str(ep.get("name") or "迁移提供商"),
                "base_url": str(ep.get("base_url") or "").strip(),
                "api_protocol": str(ep.get("api_protocol") or "openai").lower(),
                "api_key": str(ep.get("api_key") or ""),
                "key_status": {}, "models_fetched_at": 0,
            })
            return pid

        def _ensure_model(pid: str, name: str, caps: list[str]) -> Optional[str]:
            name = str(name or "").strip()
            if not name:
                return None
            for m in models:
                if m["provider_id"] == pid and m["model"] == name:
                    for c in caps:
                        if c not in m["caps"]:
                            m["caps"].append(c)
                    return m["id"]
                if m["model"] == name and m["provider_id"] != pid:
                    pass  # 同名模型允许挂多个提供商
            mid = "md_" + uuid.uuid4().hex[:12]
            models.append({"id": mid, "provider_id": pid, "model": name,
                           "caps": caps or ["llm"], "source": "migrated"})
            return mid

        # 旧链路 → 提供商 + 模型注册；绑定了的模型进对应分类链
        for cid, b in old_cons.items():
            if not isinstance(b, dict):
                continue
            eid = str(b.get("endpoint_id") or "")
            ep = next((e for e in eps if str(e.get("id")) == eid), None)
            if not ep:
                continue
            pid = _ensure_provider(ep)
            route = cons_route.get(cid)
            caps = {"ai_vision": ["llm", "vision"], "ai_sem": ["sem"]}.get(cid, ["llm"])
            mid = _ensure_model(pid, b.get("model"), caps)
            if mid and route and mid not in routes[route]:
                routes[route].insert(0, mid)

        out = {
            "v": _KV_VERSION, "providers": providers, "models": models,
            "routes": {k: {"models": v} for k, v in routes.items()},
            "fallback": {"model_id": ""},
            # 消费方绑定改为 route 模式（旧「绑一条链路+一个模型」≈ 新「选分类链路」）
            "consumers": {cid: {"mode": "route", "route": r}
                          for cid, r in cons_route.items()},
            "migrated_v1": True,
        }
        _save(out)  # 迁移结果必须持久化，否则二次 _load 会退回空结构
        # 2026-09-17 修补：迁移**成功持久化后**才置位一次性标志
        # （原实现提前置位，导致失败后永久跳过迁移，见函数开头说明）。
        try:
            from database import set_kv
            set_kv(_MIGRATED_KEY, True)
        except Exception as e:  # noqa: BLE001
            # 标志写失败不影响本次迁移结果（已 _save），但下次会再迁一次
            # —— 迁移是幂等的（同结构重建），可接受；记录以便观察。
            logger.warning(f"[HUB-004] [model_hub] 迁移标志写入失败"
                           f"（下次启动会重试迁移，结果幂等）: {e}")
        logger.info(f"[model_hub] v1 已迁入 v2（{len(providers)} 提供商 / "
                    f"{len(models)} 模型）")
        return out
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[HUB-003] " + f"[model_hub] 迁移失败（不影响运行）: {e}")
        return data


# ---------------------------------------------------------------------------
# 提供商 CRUD
# ---------------------------------------------------------------------------

def list_providers() -> list[dict]:
    with _lock:
        return [dict(p) for p in _load()["providers"]]


def save_provider(p: dict) -> dict:
    with _lock:
        data = _load()
        pid = str(p.get("id") or "")
        rec = {
            "id": pid or ("pv_" + uuid.uuid4().hex[:12]),
            "name": str(p.get("name") or "未命名提供商"),
            "base_url": str(p.get("base_url") or "").strip().rstrip("/"),
            "api_protocol": str(p.get("api_protocol") or "openai").lower(),
            "api_key": str(p.get("api_key") or ""),
            "key_status": {},  # 连接参数变了旧状态作废
            "models_fetched_at": 0,
        }
        for i, old in enumerate(data["providers"]):
            if old["id"] == rec["id"]:
                # key 为空/脱敏 → 保留原值；沿用旧状态
                if not rec["api_key"] or "•" in rec["api_key"]:
                    rec["api_key"] = old.get("api_key") or ""
                rec["key_status"] = old.get("key_status") or {}
                rec["models_fetched_at"] = old.get("models_fetched_at") or 0
                data["providers"][i] = rec
                break
        else:
            data["providers"].append(rec)
        _save(data)
        return dict(rec)


def delete_provider(pid: str) -> dict:
    """删除提供商：其模型级联删除，链路/兜底/绑定自动清理。"""
    with _lock:
        data = _load()
        mids = [m["id"] for m in data["models"] if m["provider_id"] == pid]
        data["providers"] = [p for p in data["providers"] if p["id"] != pid]
        data["models"] = [m for m in data["models"] if m["provider_id"] != pid]
        mid_set = {m["id"] for m in data["models"]}
        for kind in ROUTE_KINDS:
            data["routes"][kind]["models"] = [
                x for x in data["routes"][kind]["models"] if x in mid_set]
        if data["fallback"]["model_id"] not in mid_set:
            data["fallback"]["model_id"] = ""
        cons = {}
        for cid, c in data["consumers"].items():
            if c.get("mode") == "fixed" and c.get("model_id") not in mid_set:
                continue  # 固定模型没了 → 解绑（走默认 llm 链）
            cons[cid] = c
        data["consumers"] = cons
        _save(data)
        return {"removed_models": len(mids)}


def _provider_models_url(p: dict) -> str:
    base = str(p.get("base_url") or "").rstrip("/")
    if p.get("api_protocol") == "anthropic":
        return base + "/v1/models"
    return base + "/models"


def _provider_auth_headers(p: dict) -> dict:
    h: dict = {}
    if p.get("api_protocol") == "anthropic":
        if p.get("api_key"):
            h["x-api-key"] = p["api_key"]
        h["anthropic-version"] = "2023-06-01"
    else:
        if p.get("api_key"):
            h["Authorization"] = f"Bearer {p['api_key']}"
    return h


def test_provider_key(pid: str) -> dict:
    """测试密钥有效性：GET /models（anthropic 走 /v1/models）。

    200 → 有效；401/403 → 密钥无效；其余 → 连接/服务问题。结果写回 key_status。
    """
    import requests

    with _lock:
        data = _load()
        p = next((x for x in data["providers"] if x["id"] == pid), None)
    if not p:
        return {"ok": False, "detail": "提供商不存在"}
    if not p.get("base_url"):
        return {"ok": False, "detail": "未配置 Base URL"}
    status = {"ok": False, "checked_at": int(time.time()), "detail": ""}
    try:
        resp = requests.get(_provider_models_url(p),
                            headers=_provider_auth_headers(p), timeout=15)
        if resp.status_code == 200:
            status["ok"] = True
            status["detail"] = "密钥有效"
        elif resp.status_code in (401, 403):
            status["detail"] = f"密钥无效（HTTP {resp.status_code}）"
        else:
            status["detail"] = f"HTTP {resp.status_code}: {resp.text[:120]}"
    except Exception as e:  # noqa: BLE001
        status["detail"] = f"连接失败: {e}"
    with _lock:
        data = _load()
        for x in data["providers"]:
            if x["id"] == pid:
                x["key_status"] = status
                break
        _save(data)
    return status


def fetch_provider_models(pid: str) -> dict:
    """拉取提供商模型列表（GET /models），注册进模型表（保留已有 caps）。"""
    import requests

    with _lock:
        data = _load()
        p = next((x for x in data["providers"] if x["id"] == pid), None)
    if not p:
        return {"ok": False, "error": "提供商不存在"}
    if not p.get("base_url"):
        return {"ok": False, "error": "未配置 Base URL"}
    names: list[str] = []
    err = ""
    try:
        resp = requests.get(_provider_models_url(p),
                            headers=_provider_auth_headers(p), timeout=15)
        if resp.status_code != 200:
            err = f"HTTP {resp.status_code}: {resp.text[:160]}"
        else:
            d = resp.json()
            items = d.get("data") if isinstance(d, dict) else d
            if not isinstance(items, list):
                err = "响应不是模型列表"
            else:
                for it in items:
                    if isinstance(it, dict) and it.get("id"):
                        names.append(str(it["id"]))
    except Exception as e:  # noqa: BLE001
        err = f"连接失败: {e}"
    if err and not names:
        return {"ok": False, "error": err}

    with _lock:
        data = _load()
        existing = {(m["provider_id"], m["model"]): m for m in data["models"]}
        added = 0
        for name in names:
            key = (pid, name)
            if key in existing:
                continue
            data["models"].append({
                "id": "md_" + uuid.uuid4().hex[:12],
                "provider_id": pid, "model": name,
                "caps": guess_caps(name), "source": "fetch",
            })
            added += 1
        for x in data["providers"]:
            if x["id"] == pid:
                x["models_fetched_at"] = int(time.time())
                if err:  # 拉取有告警但拿到了部分列表
                    x["key_status"] = {"ok": True, "checked_at": int(time.time()),
                                       "detail": f"拉到 {len(names)} 个模型（{err}）"}
                else:
                    x["key_status"] = {"ok": True, "checked_at": int(time.time()),
                                       "detail": f"拉到 {len(names)} 个模型"}
                break
        _save(data)
    return {"ok": True, "total": len(names), "added": added, "warning": err}


# ---------------------------------------------------------------------------
# 模型注册
# ---------------------------------------------------------------------------

def list_models() -> list[dict]:
    with _lock:
        return [dict(m) for m in _load()["models"]]


def add_model(provider_id: str, model: str, caps: Optional[list] = None) -> dict:
    with _lock:
        data = _load()
        if not any(p["id"] == provider_id for p in data["providers"]):
            return {"ok": False, "error": "提供商不存在"}
        model = str(model or "").strip()
        if not model:
            return {"ok": False, "error": "模型名不能为空"}
        for m in data["models"]:
            if m["provider_id"] == provider_id and m["model"] == model:
                return {"ok": False, "error": "该提供商下已有同名模型"}
        rec = {
            "id": "md_" + uuid.uuid4().hex[:12],
            "provider_id": provider_id, "model": model,
            "caps": [c for c in (caps or guess_caps(model)) if c in CAPS] or ["llm"],
            "source": "manual",
        }
        data["models"].append(rec)
        _save(data)
        return {"ok": True, "model": dict(rec)}


def set_model_caps(mid: str, caps: list[str]) -> dict:
    valid = [c for c in caps if c in CAPS]
    if not valid:
        return {"ok": False, "error": "caps 不能为空"}
    with _lock:
        data = _load()
        for m in data["models"]:
            if m["id"] == mid:
                m["caps"] = valid
                # 兜底模型能力被改掉时自动失效
                if data["fallback"]["model_id"] == mid and \
                        not ({"llm", "vision"} <= set(valid)):
                    data["fallback"]["model_id"] = ""
                _save(data)
                return {"ok": True}
        return {"ok": False, "error": "模型不存在"}


def delete_model(mid: str) -> dict:
    with _lock:
        data = _load()
        data["models"] = [m for m in data["models"] if m["id"] != mid]
        mid_set = {m["id"] for m in data["models"]}
        for kind in ROUTE_KINDS:
            data["routes"][kind]["models"] = [
                x for x in data["routes"][kind]["models"] if x in mid_set]
        if data["fallback"]["model_id"] not in mid_set:
            data["fallback"]["model_id"] = ""
        cons = {}
        for cid, c in data["consumers"].items():
            if c.get("mode") == "fixed" and c.get("model_id") not in mid_set:
                continue
            cons[cid] = c
        data["consumers"] = cons
        _save(data)
        return {"ok": True}


# ---------------------------------------------------------------------------
# 链路与兜底
# ---------------------------------------------------------------------------

def save_route(kind: str, model_ids: list[str]) -> dict:
    if kind not in ROUTE_KINDS:
        return {"ok": False, "error": f"未知链路类型: {kind}"}
    ids: list[str] = []
    for x in model_ids or []:
        x = str(x)
        if x and x not in ids:
            ids.append(x)
    if len(ids) > MAX_CHAIN:
        return {"ok": False, "error": f"每条链路最多 {MAX_CHAIN} 个模型"}
    with _lock:
        data = _load()
        mid_set = {m["id"] for m in data["models"]}
        bad = [x for x in ids if x not in mid_set]
        if bad:
            return {"ok": False, "error": f"模型不存在: {','.join(bad[:3])}"}
        if kind == "llm":
            _auto = [m["id"] for m in data["models"]
                     if str(m.get("model") or "").strip().lower() == "auto"]
            if _auto and (set(_auto) & set(ids)):
                return {"ok": False, "error": (
                    "llm 链不允许使用 auto：auto 是随机路由，实测同一批并发请求"
                    "命中 4 个模型、约 40% 输出不可用（AI-050）。请指定具体模型，"
                    "例如 agnes-2.5-flash。")}
        data["routes"][kind]["models"] = ids
        _save(data)
        return {"ok": True, "route": dict(data["routes"][kind])}


def get_routes() -> dict:
    with _lock:
        return {k: dict(v) for k, v in _load()["routes"].items()}


def set_fallback(model_id: str) -> dict:
    """设置兜底模型；空串清除。模型必须同时支持 llm+vision。"""
    with _lock:
        data = _load()
        model_id = str(model_id or "")
        if model_id:
            m = next((x for x in data["models"] if x["id"] == model_id), None)
            if not m:
                return {"ok": False, "error": "模型不存在"}
            if not ({"llm", "vision"} <= set(m.get("caps") or [])):
                return {"ok": False,
                        "error": "兜底模型必须同时支持 LLM 和视觉（多模态）"}
            if str(m.get("model") or "").strip().lower() == "auto":
                return {"ok": False, "error": (
                    "兜底模型不允许使用 auto：auto 为随机路由（AI-050），"
                    "会在主模型失败时随机命中不可用模型。请改用具体多模态模型。")}
        data["fallback"]["model_id"] = model_id
        _save(data)
        return {"ok": True, "fallback": dict(data["fallback"])}


# ---------------------------------------------------------------------------
# 消费方绑定
# ---------------------------------------------------------------------------

def get_consumers() -> dict:
    with _lock:
        return {cid: dict(c) for cid, c in _load()["consumers"].items()}


def set_consumer(consumer_id: str, mode: str, route: str = "",
                 model_id: str = "") -> dict:
    valid = {c["id"] for c in CONSUMERS}
    if consumer_id not in valid:
        return {"ok": False, "error": f"未知消费方: {consumer_id}"}
    with _lock:
        data = _load()
        cons = data["consumers"]
        if mode not in ("route", "fixed"):
            return {"ok": False, "error": "mode 必须是 route 或 fixed"}
        if mode == "route":
            if route not in ROUTE_KINDS:
                return {"ok": False, "error": f"未知链路: {route}"}
            cons[consumer_id] = {"mode": "route", "route": route}
        else:
            model_id = str(model_id or "")
            if model_id and model_id not in {m["id"] for m in data["models"]}:
                return {"ok": False, "error": "模型不存在"}
            if not model_id:
                cons.pop(consumer_id, None)  # 清空 = 解绑走默认
            else:
                cons[consumer_id] = {"mode": "fixed", "model_id": model_id}
        _save(data)
        return {"ok": True, "consumers": {k: dict(v) for k, v in cons.items()}}


# ---------------------------------------------------------------------------
# 解析（消费侧注入）
# ---------------------------------------------------------------------------

def _candidate(mid: str, models_by_id: dict, providers_by_id: dict) -> Optional[dict]:
    m = models_by_id.get(mid)
    if not m:
        return None
    p = providers_by_id.get(m["provider_id"])
    if not p or not p.get("base_url") or not m.get("model"):
        return None
    return {
        "base_url": p["base_url"],
        "api_key": p.get("api_key") or "",
        "api_protocol": p.get("api_protocol") or "openai",
        "model": m["model"],
        "model_id": m["id"],
        "provider_id": m["provider_id"],
    }


def resolve_chain(consumer_id: str) -> Optional[dict]:
    """解析消费方的避障候选列表。

    返回 {"mode": "route"|"fixed", "candidates": [{base_url, api_key,
    api_protocol, model, model_id, provider_id}], "fallback": {...}|None}。
    route 模式：链内模型按序为候选；llm/vision 链把兜底模型附在末尾
    （链里已含兜底模型则不重复）。fixed 模式：单候选，无兜底。
    未绑定/绑定失效 → 默认走 llm 链。链为空 → None（调用方走自身兜底）。
    """
    data = _load()
    models_by_id = {m["id"]: m for m in data["models"]}
    providers_by_id = {p["id"]: p for p in data["providers"]}
    if not models_by_id or not providers_by_id:
        return None

    cons = data["consumers"]
    c = cons.get(consumer_id)
    cands: list[dict] = []
    mode = "route"
    route = "llm"
    if c and c.get("mode") == "fixed":
        x = _candidate(str(c.get("model_id") or ""), models_by_id, providers_by_id)
        if x:
            return {"mode": "fixed", "candidates": [x], "fallback": None}
        return None  # 固定模型失效 = 解析失败，不偷偷换模型
    if c and c.get("mode") == "route" and c.get("route") in ROUTE_KINDS:
        route = c["route"]

    for mid in data["routes"].get(route, {}).get("models") or []:
        x = _candidate(mid, models_by_id, providers_by_id)
        if x:
            cands.append(x)

    fb = None
    if route != "sem":
        fbid = data["fallback"].get("model_id") or ""
        if fbid and fbid not in [x["model_id"] for x in cands]:
            fb = _candidate(fbid, models_by_id, providers_by_id)

    if not cands and not fb:
        return None
    return {"mode": "route", "candidates": cands, "fallback": fb}


def resolve(consumer_id: str) -> Optional[dict]:
    """兼容旧签名：返回链首（或固定）模型的四键 dict。

    notify_cmd 等轻量消费方继续用它，零改动。
    """
    r = resolve_chain(consumer_id)
    if not r or not r["candidates"]:
        return None
    c = r["candidates"][0]
    return {"base_url": c["base_url"], "model": c["model"],
            "api_key": c["api_key"], "api_protocol": c["api_protocol"]}


# ---------------------------------------------------------------------------
# 汇总视图（API overview 用）
# ---------------------------------------------------------------------------

def overview() -> dict:
    with _lock:
        data = _load()
        return {
            # 2026-09-28（T6-b）：providers 走脱敏视图 —— api_key 明文不再透传
            # 给前端（GET /api/modelhub/overview）。存储层仍是原文，写侧零改动。
            "providers": [_public_provider(p) for p in data["providers"]],
            "models": [dict(m) for m in data["models"]],
            "routes": {k: dict(v) for k, v in data["routes"].items()},
            "fallback": dict(data["fallback"]),
            "consumers": {cid: dict(c) for cid, c in data["consumers"].items()},
            "consumers_meta": [dict(c) for c in CONSUMERS],
            "presets": [dict(p) for p in PROVIDER_PRESETS],
            "max_chain": MAX_CHAIN,
        }
