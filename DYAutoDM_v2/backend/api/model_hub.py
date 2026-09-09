# -*- coding: utf-8 -*-
"""模型链路中心 API（挂 /api/modelhub 前缀，v0.38.4）。

模型配置唯一真源的 HTTP 面：
  GET  /api/modelhub/overview      链路 + 消费方绑定 + 预设（一次拉全）
  POST /api/modelhub/endpoints     新建/更新链路
  DEL  /api/modelhub/endpoints/{id} 删除链路（绑定自动解绑）
  POST /api/modelhub/bindings      设置消费方绑定 {consumer, endpoint_id, model}
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services import model_hub

router = APIRouter()


class EndpointBody(BaseModel):
    id: str = ""
    name: str = ""
    base_url: str = ""
    api_protocol: str = "openai"
    api_key: str = ""


class BindingBody(BaseModel):
    consumer: str
    endpoint_id: str = ""
    model: str = ""


@router.get("/overview")
async def overview() -> dict:
    eps = model_hub.list_endpoints()
    return {
        "ok": True,
        "endpoints": eps,
        "consumers_meta": model_hub.CONSUMERS,
        "bindings": model_hub.get_bindings(),
        "presets": model_hub.PROVIDER_PRESETS,
    }


@router.post("/endpoints")
async def save_endpoint(body: EndpointBody) -> dict:
    ep = model_hub.save_endpoint(body.model_dump())
    return {"ok": True, "endpoint": ep,
            "endpoints": model_hub.list_endpoints()}


@router.delete("/endpoints/{ep_id}")
async def delete_endpoint(ep_id: str) -> dict:
    r = model_hub.delete_endpoint(ep_id)
    return {"ok": True, "endpoints": model_hub.list_endpoints(),
            "bindings": model_hub.get_bindings(), **r}


@router.post("/bindings")
async def set_binding(body: BindingBody) -> dict:
    r = model_hub.set_binding(body.consumer, body.endpoint_id, body.model)
    if not r.get("ok"):
        raise HTTPException(400, r.get("error") or "绑定失败")
    return {"ok": True, "bindings": model_hub.get_bindings()}
