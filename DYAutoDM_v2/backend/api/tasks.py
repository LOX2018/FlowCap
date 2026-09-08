"""任务路由

取代原版 WebBridge.getTasks / saveDmPool。
注意：删除原版 tasks.js 的本地 setInterval 模拟任务进度（误导）。

前端 tasks.tsx 依赖的返回字段（扁平）：
  {ok, dmPool, maxTarget, interval, delay, forceRescan,
   liveUrl, enableDanmaku, enableConsole, enableSend}
"""
from fastapi import APIRouter, Request
from loguru import logger
from models.task import TaskConfig

router = APIRouter()


def _dm_pool_from_adm(adm) -> list[dict]:
    """从 adm 取私信词库（优先 dispatch 记忆，否则 settings）。"""
    pool = getattr(adm, "dm_template", None)
    if not pool:
        try:
            from config import settings

            pool = settings.dm_pool
        except Exception:
            pool = []
    out = []
    for item in pool or []:
        if isinstance(item, dict):
            out.append({"text": item.get("text", ""), "enabled": item.get("enabled", True)})
        else:
            out.append({"text": str(item), "enabled": True})
    return out


@router.get("/history")
async def get_history(request: Request, limit: int = 0, offset: int = 0) -> dict:
    """历史任务列表（任务中心展示；含运行结果 records 快照供查阅模式跳转）。

    支持分页：limit>0 时只返回该页（offset 起），total 返回总数供前端翻页。
    """
    try:
        from tasks_history import list_history, count_history
        items = list_history(limit=limit, offset=offset)
        total = count_history() if limit else len(items)
        return {"ok": True, "list": items, "total": total}
    except Exception as e:
        logger.warning(f"[tasks] 读取历史任务失败: {e}")
        return {"ok": False, "list": [], "total": 0, "error": str(e)}


@router.get("/current")
async def get_current_task(request: Request) -> dict:
    """当前任务容器快照（任务中心「进入任务」/ 直播监听页回读的唯一数据源）。

    把引擎进程状态（engine_state/status_msg/配置快照/直播流/调度进度/发送记录）
    封装成单个容器，前端两处直接读取，切页后仍能还原任务真实情况。
    """
    adm = getattr(request.app.state, "adm", None)
    if adm is None:
        return {"ok": True, "has_task": False, "engine_state": "idle", "config": {}}
    try:
        return adm.snapshot()
    except Exception as e:
        logger.warning(f"[tasks] 读取任务容器失败: {e}")
        return {"ok": False, "has_task": False, "error": str(e)}


@router.post("/history/clear")
async def clear_history(request: Request) -> dict:
    """清空历史任务"""
    try:
        from tasks_history import clear_history
        clear_history()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _records_from_adm(adm) -> list[dict]:
    """从 adm.dispatch.records 取实时发送记录，转成前端 live.tsx/tasks.tsx 期望的 dict 列表。

    关键字段映射（SendRecord -> 前端 Row）：
      captured_at -> captured_at（前端 fmtTime 用）
      uid         -> uid（前端 nickname||uid 兜底）
      nickname    -> nickname
      comment     -> comment（前端 r.comment||r.content 兜底）
      content     -> content（dmText）
      status      -> status（英文枚举 captured/sent/fail/skipped，前端 toDmStatus 匹配）
      sent_at     -> send_ts（前端 dmTime）
    """
    if adm is None or getattr(adm, "dispatch", None) is None:
        return []
    recs = getattr(adm.dispatch, "records", {}) or {}
    out = []
    for r in recs.values():
        d = r if isinstance(r, dict) else r.model_dump()
        out.append({
            "key": d.get("key", ""),
            "uid": d.get("uid", "") or d.get("sec_uid", "") or "",
            "nickname": d.get("nickname", ""),
            "sec_uid": d.get("sec_uid"),
            "status": d.get("status", "captured"),
            "reason": d.get("reason"),
            # 2026-09-08：失败原因结构化分类（前端弹窗区分调度堵塞/凭证失效/风控等）
            "fail_kind": d.get("fail_kind"),
            "fail_label": d.get("fail_label"),
            "fail_advice": d.get("fail_advice"),
            "captured_at": d.get("captured_at", 0),
            "send_at": d.get("send_at"),
            "send_ts": d.get("sent_at"),
            "content": d.get("content") or "",
            "comment": d.get("comment") or "",
        })
    return out


@router.get("")
async def get_tasks(request: Request) -> dict:
    """任务配置 + 发送记录（对齐前端 tasks.tsx / live.tsx 字段）"""
    adm = request.app.state.adm
    from config import settings

    delay_range = list(getattr(adm, "delay_range", None) or getattr(settings, "delay_range", [40, 65]))
    delay_str = f"{delay_range[0]},{delay_range[1]}" if len(delay_range) == 2 else str(delay_range)
    return {
        "ok": True,
        "dmPool": _dm_pool_from_adm(adm),
        "maxTarget": int(getattr(adm, "limit", getattr(settings, "max_target", 3))),
        "interval": float(getattr(adm, "interval", getattr(settings, "interval", 60.0))),
        "delay": delay_str,
        "forceRescan": bool(getattr(adm, "force_rescan", getattr(settings, "force_rescan", False))),
        "liveUrl": getattr(adm, "live_url", "") or "",
        "enableDanmaku": bool(getattr(settings, "enable_danmaku", True)),
        "enableConsole": bool(getattr(settings, "enable_console", True)),
        "enableSend": bool(getattr(settings, "enable_send", True)),
        "records": _records_from_adm(adm),
    }


@router.post("/config")
async def save_config(body: TaskConfig, request: Request):
    """保存任务配置（写入运行时 settings 单例，并落盘到 SQLite kv_store）"""
    cfg = body.resolved()
    adm = request.app.state.adm
    adm.limit = cfg.max_target
    try:
        from config import settings

        settings.max_target = cfg.max_target
        settings.dm_pool = cfg.dm_pool
        settings.delay_range = cfg.delay_range
        settings.interval = cfg.interval
        settings.force_rescan = cfg.force_rescan
        settings.enable_danmaku = cfg.enable_danmaku
        settings.enable_console = cfg.enable_console
        settings.enable_send = cfg.enable_send
        # 落盘到 SQLite kv_store（替代 config.json）
        from database import get_kv_json, set_kv_json
        data = get_kv_json("config", {}) or {}
        data.update({
            "max_target": cfg.max_target,
            "dm_pool": cfg.dm_pool,
            "delay_range": cfg.delay_range,
            "interval": cfg.interval,
            "force_rescan": cfg.force_rescan,
            "enable_danmaku": cfg.enable_danmaku,
            "enable_console": cfg.enable_console,
            "enable_send": cfg.enable_send,
        })
        set_kv_json("config", data)
    except Exception as e:
        logger.warning(f"[tasks] 配置落盘失败（不影响本次保存）: {e}")
    return {"ok": True}


@router.post("/dm-pool")
async def save_dm_pool(items: list[dict], request: Request):
    """保存私信模板池（前端传 [{text, enabled}] 数组）"""
    adm = request.app.state.adm
    pool = []
    for it in items or []:
        if isinstance(it, dict):
            pool.append({"text": str(it.get("text", "")), "enabled": bool(it.get("enabled", True))})
        else:
            pool.append({"text": str(it), "enabled": True})
    adm.dm_template = pool
    try:
        from config import settings

        settings.dm_pool = [p["text"] for p in pool]
    except Exception:
        pass
    return {"ok": True, "count": len(pool)}


@router.post("/export")
async def export_stats(request: Request):
    """导出统计 xlsx（从 adm.dispatch.records 生成，落盘 backend/exports/）

    对齐前端 tasks.tsx 的 exportStats() 按钮调用。
    """
    import os
    from datetime import datetime

    adm = request.app.state.adm
    records = []
    if adm is not None and getattr(adm, "dispatch", None) is not None:
        records = getattr(adm.dispatch, "records", []) or []
    try:
        from config import settings

        out_dir = settings.data_dir / "exports"
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = out_dir / f"stats_{ts}.xlsx"
        try:
            from openpyxl import Workbook

            wb = Workbook()
            ws = wb.active
            ws.title = "明细"
            ws.append(["序号", "发言人", "评论内容", "私信状态", "私信内容", "捕获时间", "发送时间"])
            for i, r in enumerate(records, 1):
                if isinstance(r, dict):
                    ws.append([
                        i, r.get("nickname", ""), r.get("comment", ""),
                        r.get("status", ""), r.get("content", ""),
                        r.get("capture_ts", ""), r.get("send_ts", ""),
                    ])
                else:
                    ws.append([i, getattr(r, "nickname", ""), getattr(r, "comment", ""),
                               getattr(r, "status", ""), getattr(r, "content", ""),
                               getattr(r, "capture_ts", ""), getattr(r, "send_ts", "")])
            wb.save(path)
            return {"ok": True, "path": str(path), "count": len(records)}
        except ImportError:
            # 无 openpyxl 时退化为 csv
            import csv

            csv_path = out_dir / f"stats_{ts}.csv"
            with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
                w = csv.writer(f)
                w.writerow(["发言人", "评论内容", "私信状态", "私信内容", "捕获时间", "发送时间"])
                for r in records:
                    if isinstance(r, dict):
                        w.writerow([r.get("nickname", ""), r.get("comment", ""),
                                    r.get("status", ""), r.get("content", ""),
                                    r.get("capture_ts", ""), r.get("send_ts", "")])
                    else:
                        w.writerow([getattr(r, "nickname", ""), getattr(r, "comment", ""),
                                    getattr(r, "status", ""), getattr(r, "content", ""),
                                    getattr(r, "capture_ts", ""), getattr(r, "send_ts", "")])
            return {"ok": True, "path": str(csv_path), "count": len(records)}
    except Exception as e:
        logger.warning(f"[tasks] 导出失败: {e}")
        return {"ok": False, "error": str(e)}
