# -*- coding: utf-8 -*-
"""模型中心 API（挂 /api/modelhub 前缀，v0.39.0）。

三层结构：提供商（密钥/拉模型/测密钥）→ 分类避障链路（llm/vision/sem，
每链 ≤6 模型）+ 兜底模型 → 消费方绑定（route / fixed）。

  GET  /api/modelhub/overview            全量（提供商/模型/链路/兜底/绑定/预设）
  POST /api/modelhub/providers           新建/更新提供商
  DEL  /api/modelhub/providers/{id}      删除提供商（模型级联清理）
  POST /api/modelhub/providers/{id}/test   测试密钥有效性
  POST /api/modelhub/providers/{id}/fetch  拉取模型列表并注册
  POST /api/modelhub/models              手动添加模型
  POST /api/modelhub/models/{id}/caps    修改模型能力标签
  DEL  /api/modelhub/models/{id}         删除模型（链路/绑定自动清理）
  POST /api/modelhub/routes/{kind}       保存链路（≤6 模型，按序避障）
  POST /api/modelhub/fallback            设置兜底模型（须 llm+vision 双能力）
  POST /api/modelhub/consumers           设置消费方绑定（route/fixed）
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services import model_hub
from services.model_hub import _public_provider

router = APIRouter()


class ProviderBody(BaseModel):
    id: str = ""
    name: str = ""
    base_url: str = ""
    api_protocol: str = "openai"
    api_key: str = ""


class ModelBody(BaseModel):
    provider_id: str
    model: str
    caps: list[str] = []


class CapsBody(BaseModel):
    caps: list[str]


class RouteBody(BaseModel):
    model_ids: list[str] = []


class FallbackBody(BaseModel):
    model_id: str = ""


class ConsumerBody(BaseModel):
    consumer: str
    mode: str
    route: str = ""
    model_id: str = ""


@router.get("/overview")
async def overview() -> dict:
    """全量视图。providers[].api_key 已由 services.model_hub 脱敏
    （T6-b，2026-09-28），并存 `api_key_set` 指示是否已配置。此处只透传。"""
    return {"ok": True, **model_hub.overview()}


@router.post("/providers")
async def save_provider(body: ProviderBody) -> dict:
    # rec 是**存储层原文**（含明文 api_key，写侧必须拿到真值）；
    # 只有 providers/models 汇总走 overview() 的脱敏视图。
    rec = model_hub.save_provider(body.model_dump())
    return {"ok": True, "provider": _public_provider(rec), **{k: v for k, v in
            model_hub.overview().items() if k in ("providers", "models")}}


@router.delete("/providers/{pid}")
async def delete_provider(pid: str) -> dict:
    r = model_hub.delete_provider(pid)
    return {"ok": True, **r, **{k: v for k, v in
            model_hub.overview().items() if k in ("providers", "models",
            "routes", "fallback", "consumers")}}


@router.post("/providers/{pid}/test")
async def test_provider(pid: str) -> dict:
    r = model_hub.test_provider_key(pid)
    if not r.get("ok"):
        # 结果照常回写 key_status，给前端可读的失败详情
        return {"ok": False, "result": r}
    return {"ok": True, "result": r}


@router.post("/providers/{pid}/fetch")
async def fetch_provider_models(pid: str) -> dict:
    r = model_hub.fetch_provider_models(pid)
    if not r.get("ok"):
        raise HTTPException(400, r.get("error") or "拉取失败")
    return {"ok": True, **r, **{k: v for k, v in
            model_hub.overview().items() if k in ("providers", "models")}}


@router.post("/models")
async def add_model(body: ModelBody) -> dict:
    r = model_hub.add_model(body.provider_id, body.model, body.caps)
    if not r.get("ok"):
        raise HTTPException(400, r.get("error") or "添加失败")
    return r


# 让返回类型标注简单化：上面手误的 ModelHubDict 在下方修正为 dict
@router.post("/models/{mid}/caps")
async def set_model_caps(mid: str, body: CapsBody) -> dict:
    r = model_hub.set_model_caps(mid, body.caps)
    if not r.get("ok"):
        raise HTTPException(400, r.get("error") or "设置失败")
    return {"ok": True}


@router.delete("/models/{mid}")
async def delete_model(mid: str) -> dict:
    r = model_hub.delete_model(mid)
    return {"ok": True, **r, **{k: v for k, v in
            model_hub.overview().items() if k in ("providers", "models",
            "routes", "fallback", "consumers")}}


@router.post("/routes/{kind}")
async def save_route(kind: str, body: RouteBody) -> dict:
    r = model_hub.save_route(kind, body.model_ids)
    if not r.get("ok"):
        raise HTTPException(400, r.get("error") or "保存失败")
    return {"ok": True, "route": r.get("route"),
            "routes": model_hub.get_routes()}


@router.post("/fallback")
async def set_fallback(body: FallbackBody) -> dict:
    r = model_hub.set_fallback(body.model_id)
    if not r.get("ok"):
        raise HTTPException(400, r.get("error") or "设置失败")
    return {"ok": True, "fallback": r.get("fallback")}


@router.post("/consumers")
async def set_consumer(body: ConsumerBody) -> dict:
    r = model_hub.set_consumer(body.consumer, body.mode, body.route, body.model_id)
    if not r.get("ok"):
        raise HTTPException(400, r.get("error") or "绑定失败")
    return {"ok": True, "consumers": r.get("consumers")}
