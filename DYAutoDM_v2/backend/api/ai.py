# -*- coding: utf-8 -*-
"""AI 获客自动回复 API（挂 /api/ai 前缀）。

端点全部薄封装 services/ai_reply.py；启动时 ensure_tables + WORKER 按配置自启。
"""
from __future__ import annotations

import asyncio
import csv
import io
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from loguru import logger

from services import ai_reply

router = APIRouter()


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

@router.get("/config")
async def get_config():
    return {"ok": True, "config": ai_reply.get_config()}


class SaveConfigBody(BaseModel):
    config: dict


@router.post("/config")
async def save_config(body: SaveConfigBody):
    merged = ai_reply.save_config(body.config)
    # enabled 变化时联动 WORKER 启停
    if merged.get("enabled"):
        ai_reply.WORKER.start()
    else:
        ai_reply.WORKER.stop()
    return {"ok": True, "config": merged,
            "running": ai_reply.WORKER.status["running"]}


# ---------------------------------------------------------------------------
# 连接测试
# ---------------------------------------------------------------------------

@router.post("/test")
async def test_ai():
    """主模型连通性：timeout 放宽到 60s（推理模型首 token 慢）。"""
    cfg = ai_reply.get_config()
    if not cfg.get("api_key") and "127.0.0.1" not in str(cfg.get("base_url", "")):
        return {"ok": False, "msg": "未配置 API Key（本机服务可留空）"}
    client = ai_reply.AIClient({**cfg, "max_tokens": max(int(cfg.get("max_tokens", 1000)), 200)})
    ok, msg = client.test_connection()
    return {"ok": ok, "msg": msg}


@router.post("/test_vision")
async def test_vision():
    """视觉模型连通性：用一张 1x1 像素红点 PNG 测。"""
    import base64
    # 1x1 红色 PNG
    png_b64 = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
               "z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
    cfg = ai_reply.get_config()
    client = ai_reply.AIClient(cfg)
    desc = client.describe_image(png_b64, "png")
    if desc is not None:
        return {"ok": True, "msg": f"视觉模型连通：{desc[:50]}"}
    return {"ok": False, "msg": "视觉模型连接失败：请检查视觉配置（Base URL/Key/模型名）"}


# ---------------------------------------------------------------------------
# 知识库
# ---------------------------------------------------------------------------

@router.get("/knowledge")
async def kb_list():
    return {"ok": True, "items": ai_reply.KB.list_items()}


class KBItemBody(BaseModel):
    id: int = 0
    question: str
    answer: str


@router.post("/knowledge")
async def kb_add(body: KBItemBody):
    try:
        if body.id:
            ai_reply.KB.update(body.id, body.question, body.answer)
            _rebuild_sem_cache_bg()
            return {"ok": True, "msg": "已更新"}
        item = ai_reply.KB.add(body.question, body.answer)
        _rebuild_sem_cache_bg()
        return {"ok": True, "item": item, "msg": "已添加"}
    except Exception as e:
        raise HTTPException(400, str(e))


@router.delete("/knowledge/{item_id}")
async def kb_delete(item_id: int):
    ai_reply.KB.delete(item_id)
    # 清掉该条向量缓存
    try:
        conn = ai_reply.database.get_db()
        conn.execute("DELETE FROM kv_store WHERE key=?",
                     (ai_reply._KV_SEM_PREFIX + str(item_id),))
        conn.commit()
    except Exception:
        pass
    return {"ok": True, "msg": "已删除"}


# ---------------------------------------------------------------------------
# 知识库文件导入（png/jpg/docx/xlsx/pdf/txt/md → 自动生成 QA）
# ---------------------------------------------------------------------------

class KbImportConfirmBody(BaseModel):
    items: list[dict]
    replace: bool = False


@router.post("/knowledge/import")
async def kb_import_upload(file: UploadFile):
    """上传文件 → 解析 + AI 生成 QA 列表（返回给前端预览，不直接入库）。

    支持格式：txt/md/docx/xlsx/pdf；图片需先配置视觉模型。
    前端确认后调 /knowledge/import/confirm 写入。
    """
    from services import kb_import
    import tempfile
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in kb_import.SUPPORTED_EXT:
        raise HTTPException(400, f"不支持的格式 {suffix}（支持: "
                                 f"txt/md/docx/xlsx/pdf/png/jpg）")
    data = await file.read()
    if len(data) > kb_import.MAX_FILE_MB * 1024 * 1024:
        raise HTTPException(400, f"文件超过 {kb_import.MAX_FILE_MB}MB 上限")
    tmp = Path(tempfile.gettempdir()) / f"kb_import_{int(time.time())}{suffix}"
    try:
        tmp.write_bytes(data)
        cfg = ai_reply.get_config()
        result = await asyncio.to_thread(kb_import.import_file, tmp, cfg)
    except kb_import.ImportError_ as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.warning("AI-001", f"[ai-kb-import] 解析异常: {e}")
        raise HTTPException(500, f"解析失败: {e}")
    finally:
        try:
            tmp.unlink()
        except Exception:
            pass
    return {"ok": True, "filename": file.filename, **result}


@router.post("/knowledge/import/confirm")
async def kb_import_confirm(body: KbImportConfirmBody):
    """把预览的 QA 列表写入知识库（replace=true 清空后写入）。"""
    if not body.items:
        raise HTTPException(400, "没有可写入的条目")
    if body.replace:
        ai_reply._kv_set(ai_reply._KV_KB, [])
    n = 0
    for it in body.items[:200]:
        q = (it.get("question") or "").strip()
        a = (it.get("answer") or "").strip()
        if q and a:
            try:
                ai_reply.KB.add(q, a)
                n += 1
            except Exception:
                pass
    # 知识库变更 → 语义缓存失效重算（后台尽力，失败不阻塞）
    _rebuild_sem_cache_bg()
    return {"ok": True, "added": n,
            "total": len(ai_reply.KB.list_items()),
            "msg": f"已导入 {n} 条"}


# ---------------------------------------------------------------------------
# 语义检索（三级漏斗第 2 级）
# ---------------------------------------------------------------------------

def _rebuild_sem_cache_bg() -> None:
    """后台重建语义向量缓存（尽力而为，失败只记日志）。"""
    def _run():
        try:
            r = ai_reply.kb_rebuild_semantic_cache()
            if r.get("ok"):
                logger.info(f"[ai] 语义缓存已重建: {r.get('embedded')}/{r.get('total')}")
            else:
                logger.warning("AI-002", f"[ai] 语义缓存重建失败: {r.get('error')}")
        except Exception as e:
            logger.warning("AI-003", f"[ai] 语义缓存重建异常: {e}")
    import threading
    threading.Thread(target=_run, daemon=True, name="ai-sem-cache").start()


class SemTestBody(BaseModel):
    text_a: str = "价格是多少"
    text_b: str = "咋收费的啊"


@router.post("/semantic/test")
async def semantic_test(body: SemTestBody):
    """语义检索连通性+效果测试：两句话向量化算余弦，直观验证阈值。"""
    cfg = ai_reply.get_config()
    if not cfg.get("sem_base_url"):
        return {"ok": False, "msg": "未配置语义检索 Base URL"}
    vecs = ai_reply._embedRemote(cfg.get("sem_base_url", ""),
                                 cfg.get("sem_api_key", ""),
                                 cfg.get("sem_model", ""),
                                 [body.text_a, body.text_b])
    if vecs is None:
        return {"ok": False,
                "msg": "embedding 调用失败：检查 Base URL/Key/模型名（本机 FreeLLM 需 nvidia/nemotron-3-embed-1b）"}
    score = ai_reply._cosine(vecs[0], vecs[1])
    th = float(cfg.get("sem_threshold", 0.40))
    return {"ok": True, "score": round(score, 4), "threshold": th,
            "msg": f"相似度 {score:.3f}（阈值 {th}）→ {'✅ 会命中' if score >= th else '❌ 低于阈值不命中'}"}


@router.post("/semantic/rebuild")
async def semantic_rebuild():
    """手动重建全部知识库条目的向量缓存（同步，前端可等待结果）。"""
    r = ai_reply.kb_rebuild_semantic_cache()
    if not r.get("ok"):
        raise HTTPException(500, r.get("error", "重建失败"))
    return {"ok": True, **r, "msg": f"已向量化 {r.get('embedded')}/{r.get('total')} 条"}


@router.get("/semantic/cache_status")
async def semantic_cache_status():
    """缓存覆盖情况：多少条目有向量缓存。"""
    items = ai_reply.KB.list_items()
    have = sum(1 for it in items if ai_reply._kv_get(ai_reply._KV_SEM_PREFIX + str(it["id"]), None))
    cfg = ai_reply.get_config()
    cached_model = ai_reply._kv_get(ai_reply._KV_SEM_MDL, "")
    return {"ok": True, "total": len(items), "embedded": have,
            "model": cfg.get("sem_model", ""), "cached_model": cached_model,
            "stale": bool(cached_model and cached_model != cfg.get("sem_model", ""))}


# ---------------------------------------------------------------------------
# 黑名单
# ---------------------------------------------------------------------------

@router.get("/blacklist")
async def bl_list():
    return {"ok": True, "items": ai_reply.blacklist_list()}


class BlackBody(BaseModel):
    user_id: str


@router.post("/blacklist")
async def bl_add(body: BlackBody):
    ai_reply.blacklist_add(body.user_id.strip())
    return {"ok": True, "items": ai_reply.blacklist_list()}


@router.delete("/blacklist")
async def bl_remove(user_id: str):
    ai_reply.blacklist_remove(user_id)
    return {"ok": True, "items": ai_reply.blacklist_list()}


# ---------------------------------------------------------------------------
# 模型提供商（前端下拉框数据源）
# ---------------------------------------------------------------------------

@router.get("/providers")
async def list_providers():
    return {"ok": True, "providers": ai_reply.get_providers()}


@router.get("/providers/freellm_models")
async def freellm_models():
    """在线拉取本机 FreeLLM 的 /v1/models，分为 chat/vision/embed 三组。"""
    cfg = ai_reply.get_config()
    base = cfg.get("sem_base_url") or "http://127.0.0.1:31415/v1"
    key = cfg.get("sem_api_key") or ""
    try:
        import requests as _rq
        headers = {}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        resp = _rq.get(f"{base.rstrip('/')}/models", headers=headers, timeout=8)
        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code}")
        ids = sorted(m.get("id", "") for m in resp.json().get("data", []))
    except Exception as e:
        return {"ok": False, "error": f"FreeLLM 不可达: {e}",
                "chat": [], "vision": [], "embed": []}
    vis_kw = ("vision", "-vl", "4o", "omni", "gemini", "v-plus", "4v")
    emb_kw = ("embed", "bge", "gte", "e5", "nemotron-3-embed")
    chat = [i for i in ids if not any(k in i.lower() for k in vis_kw + emb_kw)]
    vision = [i for i in ids if any(k in i.lower() for k in vis_kw)]
    embed = [i for i in ids if any(k in i.lower() for k in emb_kw)]
    return {"ok": True, "chat": chat, "vision": vision, "embed": embed,
            "total": len(ids)}


# ---------------------------------------------------------------------------
# 运行控制 / 状态
# ---------------------------------------------------------------------------

@router.get("/status")
async def get_status():
    st = dict(ai_reply.WORKER.status)
    st["enabled"] = bool(ai_reply.get_config().get("enabled"))
    # 线索总数（轻查询，状态页顺手返回）
    try:
        row = ai_reply.database.get_db().execute(
            "SELECT COUNT(*) FROM ai_leads").fetchone()
        st["leads_total"] = row[0] if row else 0
    except Exception:
        st["leads_total"] = 0
    return {"ok": True, **st}


@router.post("/start")
async def start_worker():
    ai_reply.save_config({"enabled": True})
    ai_reply.WORKER.start()
    return {"ok": True, "running": True}


@router.post("/stop")
async def stop_worker():
    ai_reply.save_config({"enabled": False})
    ai_reply.WORKER.stop()
    return {"ok": True, "running": False}


# ---------------------------------------------------------------------------
# 留资线索
# ---------------------------------------------------------------------------

@router.get("/leads")
async def leads_list(limit: int = 200):
    try:
        ai_reply.ensure_tables()
    except Exception:
        pass
    return {"ok": True, "items": ai_reply.list_leads(limit)}


class LeadStatusBody(BaseModel):
    id: int
    status: str


@router.post("/leads/status")
async def lead_status(body: LeadStatusBody):
    try:
        ai_reply.set_lead_status(body.id, body.status)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(400, str(e))


@router.get("/leads/export")
async def leads_export():
    """CSV 导出（UTF-8 BOM，Excel 直接打开不乱码）。"""
    ai_reply.ensure_tables()
    rows = ai_reply.list_leads(5000)
    buf = io.StringIO()
    buf.write("\ufeff")
    w = csv.writer(buf)
    w.writerow(["时间", "账号", "客户昵称", "联系方式类型", "联系方式",
                "状态", "来源消息"])
    for r in rows:
        w.writerow([
            time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["created_at"])),
            r["account"], r.get("peer_name") or "",
            "手机号" if r["contact_type"] == "phone" else "微信号",
            r["contact_value"],
            {"new": "新线索", "followed": "已跟进",
             "invalid": "无效"}.get(r.get("status", "new"), r.get("status")),
            (r.get("source_text") or "")[:80],
        ])
    buf.seek(0)
    fname = time.strftime("ai_leads_%Y%m%d_%H%M%S.csv")
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={fname}"},
    )
