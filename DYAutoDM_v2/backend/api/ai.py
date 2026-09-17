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
from typing import Optional
from loguru import logger

from services import ai_reply

router = APIRouter()


# ---------------------------------------------------------------------------
# 对话回复库（命中库 v0.39.0）：案例命中零 token 直接回复
# ---------------------------------------------------------------------------

@router.get("/replies")
async def replies_list():
    from services import reply_kb

    return {"ok": True, "items": reply_kb.list_items()}


class ReplyItemBody(BaseModel):
    id: Optional[int] = None
    question: str = ""
    answer: str = ""
    enabled: Optional[bool] = None


@router.post("/replies")
async def replies_save(body: ReplyItemBody):
    from services import reply_kb

    if body.id:
        it = reply_kb.update_item(body.id, body.question, body.answer,
                                  enabled=body.enabled)
        if not it:
            return {"ok": False, "error": "条目不存在"}
        return {"ok": True, "item": it, "items": reply_kb.list_items()}
    it = reply_kb.add_item(body.question, body.answer, source="manual")
    return {"ok": True, "item": it, "items": reply_kb.list_items()}


@router.delete("/replies/{item_id}")
async def replies_delete(item_id: int):
    from services import reply_kb

    ok = reply_kb.delete_item(item_id)
    return {"ok": ok, "items": reply_kb.list_items()}


@router.post("/replies/learn")
async def replies_learn(account: str = "", limit: int = 200):
    """从聊天记录自动总结学习话术 → 入命中库。"""
    from services import reply_kb

    try:
        r = reply_kb.learn_from_history(account=account or "", limit=limit)
        return {"ok": True, **r, "items": reply_kb.list_items()}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ---------------------------------------------------------------------------
# 专业知识库（思维导图结构 v0.39.1）：主题→子分类→正文→总结
# ---------------------------------------------------------------------------

@router.get("/prokb")
async def prokb_list():
    from services import pro_kb

    return {"ok": True, "items": pro_kb.list_items(), "tree": pro_kb.tree()}


class ProKbItemBody(BaseModel):
    id: Optional[int] = None
    topic: str = ""
    category: str = ""
    content: str = ""
    summary: str = ""
    enabled: Optional[bool] = None


@router.post("/prokb")
async def prokb_save(body: ProKbItemBody):
    from services import pro_kb

    if body.id:
        it = pro_kb.update_item(body.id, topic=body.topic,
                                category=body.category, content=body.content,
                                summary=body.summary, enabled=body.enabled)
        if not it:
            return {"ok": False, "error": "条目不存在"}
    else:
        try:
            it = pro_kb.add_item(body.topic, body.category, body.content,
                                 body.summary)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
    return {"ok": True, "item": it, "items": pro_kb.list_items(),
            "tree": pro_kb.tree()}


@router.delete("/prokb/{item_id}")
async def prokb_delete(item_id: int):
    from services import pro_kb

    ok = pro_kb.delete_item(item_id)
    return {"ok": ok, "items": pro_kb.list_items(), "tree": pro_kb.tree()}


class ProImportConfirmBody(BaseModel):
    items: list[dict]
    replace: bool = False


class SchedulerBody(BaseModel):
    """pro-KB 常驻定时器的启停参数（2026-09-17 新增，修参数绑定方向）。

    原端点用裸标量参数，FastAPI 绑定为 query，导致 JSON body 被静默忽略、
    `enable=false` 无法传达。此模型让 body 提交可用。
    """
    enable: bool = True
    interval_hours: float = 84


@router.post("/prokb/import")
async def prokb_import_upload(file: UploadFile):
    """上传文件 → 解析 + AI 提纯为思维导图条目（主题/子分类/正文/总结）。

    返回预览列表，不直接入库；前端确认后调 /prokb/import/confirm。
    支持格式：txt/md/docx/xlsx/pdf；图片需先配置视觉模型。
    """
    from services import kb_import
    import tempfile

    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in kb_import.SUPPORTED_EXT:
        raise HTTPException(400, f"不支持的格式 {suffix}（支持: txt/md/docx/xlsx/pdf/png/jpg）")
    data = await file.read()
    if len(data) > kb_import.MAX_FILE_MB * 1024 * 1024:
        raise HTTPException(400, f"文件超过 {kb_import.MAX_FILE_MB}MB 上限")
    tmp = Path(tempfile.gettempdir()) / f"pro_kb_import_{int(time.time())}{suffix}"
    try:
        tmp.write_bytes(data)
        cfg = ai_reply.get_config()
        result = await asyncio.to_thread(kb_import.import_pro_file, tmp, cfg)
    except Exception as e:
        logger.warning(f"[AI-002] " + f"[ai-prokb-import] 解析异常: {e}")
        raise HTTPException(500, f"提纯失败: {e}")
    finally:
        try:
            tmp.unlink()
        except Exception:
            pass
    return {"ok": True, "filename": file.filename, **result}


@router.post("/prokb/import/confirm")
async def prokb_import_confirm(body: ProImportConfirmBody):
    """把预览的思维导图条目写入专业知识库。replace=true 先清空。"""
    from services import pro_kb

    if not body.items:
        raise HTTPException(400, "没有可写入的条目")

    # 2026-09-17 修补（OCR 审查 HIGH —— 数据丢失）：
    # 原实现先 `clear_items()` 清空全库、再 `bulk_add()` 写入，**两步非原子**。
    # 若 bulk_add 中途抛错（单条去重/向量化失败等），旧库已被清空、新数据只写了
    # 一半 → 用户同时丢失旧库和新库，最坏全库归零，且无回滚。
    # 现改为「先备份 → 写入 → 失败回滚」：
    #   1. replace 时先取旧库快照（不出来就不清）；
    #   2. 写入新条目；
    #   3. 任一步失败即用快照恢复，并把异常转成明确错误返回。
    _backup = pro_kb.list_items() if body.replace else None
    try:
        if body.replace:
            pro_kb.clear_items()
        n = pro_kb.bulk_add(body.items)
    except Exception as e:  # noqa: BLE001
        logger.error(f"[AI-021] pro-KB 导入失败，正在回滚: {type(e).__name__}: {e}")
        if _backup is not None:
            try:
                pro_kb.clear_items()
                pro_kb.bulk_add(_backup)
                logger.info(f"[AI-021] 已从快照回滚 {len(_backup)} 条旧条目")
            except Exception as e2:  # noqa: BLE001
                logger.error(
                    f"[AI-021] 回滚失败！旧知识库可能已丢失"
                    f"（快照 {len(_backup)} 条已在内存，建议立即重试导入）: {e2}")
        raise HTTPException(500, f"导入失败（已尝试回滚）: {e}") from e
    return {"ok": True, "added": n, "items": pro_kb.list_items(), "tree": pro_kb.tree()}


@router.post("/prokb/migrate_qa")
async def prokb_migrate_qa():
    """把旧 QA 知识库（Agent config 内）迁移到对话回复库。"""
    from services import ai_agent, reply_kb

    a = ai_agent.get_agent('ag_fa502e7decb547b7')
    kb = (a or {}).get('config', {}).get('knowledge_base', [])
    existing = {(it.get('question') or '').strip()
                for it in reply_kb.list_items()}
    added = 0
    for it in kb:
        q = (it.get('question') or '').strip()
        ans = (it.get('answer') or '').strip()
        if q and ans and q not in existing:
            reply_kb.add_item(q, ans, source='migrated')
            existing.add(q)
            added += 1
    return {"ok": True, "added": added,
            "items": reply_kb.list_items()}


# ---------------------------------------------------------------------------
# 知识维护与自动学习（v0.40，移植自 MalogBot 知识演化体系）
# 删除一律：扫描出报告 → 人工确认 → 回收站 30 天 → 真删
# ---------------------------------------------------------------------------

class ProKbPatchBody(BaseModel):
    topic: Optional[str] = None
    category: Optional[str] = None
    content: Optional[str] = None
    summary: Optional[str] = None
    enabled: Optional[bool] = None
    importance: Optional[float] = None


@router.patch("/prokb/{item_id}")
async def prokb_patch(item_id: int, body: ProKbPatchBody):
    """单字段/多字段局部更新（表格视图的单元格直接编辑用）。"""
    from services import pro_kb

    it = pro_kb.update_item(item_id, topic=body.topic,
                            category=body.category, content=body.content,
                            summary=body.summary, enabled=body.enabled,
                            importance=body.importance)
    if not it:
        return {"ok": False, "error": "条目不存在"}
    return {"ok": True, "item": it, "tree": pro_kb.tree()}


@router.get("/prokb/topics")
async def prokb_topics():
    """全库主题/子分类清单（导入提纯时复用，保证全局一棵树）。"""
    from services import pro_kb

    return {"ok": True, "topics": pro_kb.topics(),
            "categories": pro_kb.categories()}


class RenameTopicBody(BaseModel):
    old: str
    new: str


@router.post("/prokb/rename_topic")
async def prokb_rename_topic(body: RenameTopicBody):
    """主题改名（级联该主题下所有条目）——统一树的关键操作。"""
    from services import pro_kb

    n = pro_kb.rename_topic(body.old, body.new)
    return {"ok": True, "renamed": n, "items": pro_kb.list_items(),
            "tree": pro_kb.tree()}


@router.get("/prokb/maintain/status")
async def prokb_maintain_status():
    """维护体系状态：定时器 / 上次运行 / 上次扫描报告。"""
    from services import kb_maintain

    st = kb_maintain.get_state()
    return {"ok": True, "state": {
        "enabled": st.get("enabled"),
        "interval_hours": st.get("interval_hours"),
        "last_run_at": st.get("last_run_at"),
        "next_run_at": st.get("next_run_at"),
        "runs": st.get("runs"),
        "last_run_result": st.get("last_run_result"),
        "errors": st.get("errors", [])[-5:],
    }, "last_scan": kb_maintain.get_last_scan()}


@router.post("/prokb/maintain/scan")
async def prokb_maintain_scan():
    """立即执行一次维护扫描（只出报告，不动数据）。"""
    from services import kb_maintain

    r = await asyncio.to_thread(kb_maintain.run_scan_job)
    return {"ok": r.get("ok", False), "report": r.get("report"),
            "error": r.get("error")}


class MaintainApplyBody(BaseModel):
    items: list = []
    ids: list = []
    action: str = "recycle"

    def pick_ids(self) -> list:
        out = list(self.ids or [])
        for it in self.items or []:
            if isinstance(it, dict):
                _id = it.get("id") or it.get("drop_id")
            else:
                _id = it
            if _id is not None:
                out.append(_id)
        return out


@router.post("/prokb/maintain/apply")
async def prokb_maintain_apply(body: MaintainApplyBody):
    """执行确认过的维护项：进回收站（默认）或打陈旧标记。"""
    from services import pro_kb

    ids = body.pick_ids()
    if not ids:
        raise HTTPException(400, "未选择任何条目")
    r = pro_kb.apply_maintenance(ids, action=body.action)
    return {**r, "items": pro_kb.list_items(), "tree": pro_kb.tree()}


@router.post("/prokb/maintain/merge")
async def prokb_maintain_merge(body: MaintainApplyBody):
    """合并重复：保留 keep_id，drop_id 进回收站。"""
    from services import kb_maintain, pro_kb

    pairs = [it for it in (body.items or []) if isinstance(it, dict)]
    r = kb_maintain.merge_duplicate_pairs(pairs)
    return {**r, "items": pro_kb.list_items(), "tree": pro_kb.tree()}


@router.post("/prokb/maintain/learn")
async def prokb_maintain_learn(account: str = "", limit: int = 200):
    """手动触发一次自动学习（从聊天记录提炼话术 → 命中库）。"""
    from services import kb_maintain

    r = await asyncio.to_thread(kb_maintain.run_learn_job,
                                account=account or "", limit=limit)
    return r


@router.post("/prokb/maintain/run")
async def prokb_maintain_run():
    """手动跑一轮完整周期任务（学习 + 扫描 + 回收站清理）。"""
    from services import kb_maintain

    r = await asyncio.to_thread(kb_maintain.run_all_jobs)
    return {"ok": True, **r}


@router.post("/prokb/maintain/scheduler")
async def prokb_maintain_scheduler(
        enable: bool = True,
        interval_hours: float = 84,
        body: Optional[SchedulerBody] = None):
    """启停常驻定时器（默认 84 小时一轮）。

    2026-09-17 修补（OCR 审查 HIGH —— 参数绑定方向错误）：
    `enable` / `interval_hours` 原为**纯标量参数**，FastAPI 会把它们绑定为
    **query 参数**；客户端以 JSON body 提交 `{"enable": false}` 时 body 被
    静默忽略 → `enable` 恒为默认 `True` → `stop_scheduler()` 分支**永不可达**，
    用户停不掉定时器。
    现新增可选 Pydantic body（body 优先，query 仍兼容旧调用方）。
    """
    from services import kb_maintain

    if body is not None:
        enable = body.enable
        interval_hours = body.interval_hours
    if enable:
        return kb_maintain.start_scheduler(interval_hours=interval_hours)
    return kb_maintain.stop_scheduler()


# ---- 回收站 ----

@router.get("/prokb/recycle")
async def prokb_recycle_list():
    from services import pro_kb

    return {"ok": True, "items": pro_kb.list_recycle_bin(),
            "retention_days": pro_kb.RECYCLE_DAYS}


@router.post("/prokb/recycle/{item_id}/restore")
async def prokb_recycle_restore(item_id: int):
    from services import pro_kb

    ok = pro_kb.restore_item(item_id)
    return {"ok": ok, "items": pro_kb.list_recycle_bin(),
            "tree": pro_kb.tree()}


@router.post("/prokb/recycle/purge")
async def prokb_recycle_purge():
    """立即清空回收站（真删，慎用）。"""
    from services import pro_kb

    n = pro_kb.clear_recycle_bin()
    return {"ok": True, "purged": n, "items": pro_kb.list_recycle_bin()}


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Agent 模版 + 账号绑定（v0.38.0）
#
# 设计（用户 2026-09-09 拍板）：Agent 是**模版**，账号绑定 Agent。
# 绑定关系在**设置页**维护（不在 AI 页），避免两处配置分裂。
# 改 Agent 一次 → 所有绑定它的账号同步生效。
# ---------------------------------------------------------------------------

@router.get("/agents")
async def list_agents():
    from services import ai_agent

    return {"ok": True,
            "agents": [a for a in ai_agent.list_agents()
                       if a.get("kind") != "dispatch"],
            "bindings": ai_agent.get_bindings()}


@router.get("/dispatch_agent")
async def get_dispatch_agent():
    """调度 Agent（IM Bot 默认，仅一个）。不存在自动预置。"""
    from services import ai_agent

    a = ai_agent.get_dispatch_agent()
    return {"ok": True, "agent": a}


class SaveDispatchBody(BaseModel):
    config: dict = {}


@router.post("/dispatch_agent")
async def save_dispatch_agent(body: SaveDispatchBody):
    """更新调度 Agent（只允许 system_prompt/permissions/enabled）。"""
    from services import ai_agent

    a = ai_agent.save_dispatch_agent(body.config or {})
    return {"ok": True, "agent": a}


@router.get("/agents/{agent_id}")
async def get_agent(agent_id: str):
    from services import ai_agent

    a = ai_agent.get_agent(agent_id)
    if not a:
        raise HTTPException(404, "Agent 不存在")
    return {"ok": True, "agent": a}


class SaveAgentBody(BaseModel):
    id: str = ""
    name: str = ""
    config: dict = {}


@router.post("/agents")
async def save_agent(body: SaveAgentBody):
    from services import ai_agent

    # 只认 ai_reply._DEFAULT_CONFIG 里已有的键，防止脏键污染
    allowed = set(ai_reply._DEFAULT_CONFIG.keys()) | {
        "knowledge_base", "blacklist"}
    incoming = {k: v for k, v in (body.config or {}).items() if k in allowed}

    # 2026-09-10 事故修复：save_agent 是整体覆盖 config。前端编辑器只传
    # 表单里的几个键（name/model/enabled/scopes…），直接覆盖会把既有
    # system_prompt/knowledge_base 等清空（唐律助理曾因此丢人格+30条KB）。
    # 改为合并语义：已存在的 Agent 先取旧 config，再把传入的键盖上去；
    # 传入 None 的键视为"不修改"。新建时保持原样。
    existing = ai_agent.get_agent(body.id) if body.id else None
    if existing:
        merged = dict(existing.get("config") or {})
        for k, v in incoming.items():
            if v is not None:
                merged[k] = v
        cfg = merged
    else:
        cfg = incoming
    a = ai_agent.save_agent(body.id, body.name or "未命名 Agent", cfg)
    return {"ok": True, "agent": a, "agents": ai_agent.list_agents()}


@router.delete("/agents/{agent_id}")
async def delete_agent(agent_id: str):
    from services import ai_agent

    return ai_agent.delete_agent(agent_id)


class BindBody(BaseModel):
    account: str
    agent_id: str = ""   # 空 = 解绑


@router.post("/bind")
async def bind_account(body: BindBody):
    from services import ai_agent

    if body.agent_id:
        a = ai_agent.get_agent(body.agent_id)
        if not a:
            raise HTTPException(404, "Agent 不存在")
    return ai_agent.bind(body.account, body.agent_id)


@router.get("/bind")
async def get_bindings():
    from services import ai_agent

    return {"ok": True, "bindings": ai_agent.get_bindings(),
            "agents": ai_agent.list_agents()}


@router.get("/config")
async def get_config(agent_id: str = ""):
    """读配置。带 agent_id 时读该 Agent 的（叠加在全局之上）。

    v0.38.3：AI 页变成 Agent 编辑器 —— 顶部选了哪个 Agent，本页就编辑哪个。
    不传 agent_id 时与改造前完全一致（全局配置，零回归）。
    """
    base = ai_reply.get_config()
    if not agent_id:
        return {"ok": True, "config": base, "scope": "global"}
    from services import ai_agent

    a = ai_agent.get_agent(agent_id)
    if not a:
        raise HTTPException(404, "Agent 不存在")
    merged = ai_agent.resolve_config_for(agent_id, base)
    return {"ok": True, "config": merged, "scope": agent_id,
            "agent_name": a.get("name")}


class SaveConfigBody(BaseModel):
    config: dict
    agent_id: str = ""   # 空 = 存全局；否则存到该 Agent


@router.post("/config")
async def save_config(body: SaveConfigBody):
    if body.agent_id:
        # Agent 模式：只写该 Agent，不动全局
        from services import ai_agent

        a = ai_agent.get_agent(body.agent_id)
        if not a:
            raise HTTPException(404, "Agent 不存在")
        allowed = set(ai_reply._DEFAULT_CONFIG.keys()) | {
            "knowledge_base", "blacklist", "scopes"}
        cfg = {k: v for k, v in (body.config or {}).items() if k in allowed}
        cur = dict(a.get("config") or {})
        cur.update(cfg)
        ai_agent.save_agent(body.agent_id, a.get("name") or "", cur)
        merged = ai_agent.resolve_config_for(body.agent_id, ai_reply.get_config())
        return {"ok": True, "config": merged, "scope": body.agent_id,
                "running": ai_reply.WORKER.status["running"]}

    # 全局模式（原行为不变）
    merged = ai_reply.save_config(body.config)
    # enabled 变化时联动 WORKER 启停
    if merged.get("enabled"):
        ai_reply.WORKER.start()
    else:
        ai_reply.WORKER.stop()
    return {"ok": True, "config": merged, "scope": "global",
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
        logger.warning(f"[AI-001] " + f"[ai-kb-import] 解析异常: {e}")
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
                logger.warning(f"[AI-002] " + f"[ai] 语义缓存重建失败: {r.get('error')}")
        except Exception as e:
            logger.warning(f"[AI-003] " + f"[ai] 语义缓存重建异常: {e}")
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
