"""任务路由

取代原版 WebBridge.getTasks / saveDmPool。
注意：删除原版 tasks.js 的本地 setInterval 模拟任务进度（误导）。

前端 tasks.tsx 依赖的返回字段（扁平）：
  {ok, dmPool, maxTarget, interval, delay,
   liveUrl, enableDanmaku, enableConsole, enableSend}
"""
import time

from fastapi import APIRouter, Request
from loguru import logger
from models.task import TaskConfig

router = APIRouter()


def _fmt_ts(v) -> str:
    """时间戳 → 可读字符串（Excel 导出用；空值返回空串）。

    2026-09-17 新增（配合导出列字段名修复）：`SendRecord` 的时间是
    float 时间戳，直接写进 Excel 是难读的数字。
    """
    try:
        if v is None or v == "":
            return ""
        if isinstance(v, (int, float)):
            return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(v)))
        return str(v)
    except Exception:
        return str(v) if v is not None else ""


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
        logger.warning(f"[TSK-001] " + f"[tasks] 读取历史任务失败: {e}")
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
        logger.warning(f"[TSK-002] " + f"[tasks] 读取任务容器失败: {e}")
        return {"ok": False, "has_task": False, "error": str(e)}


@router.post("/history/clear")
async def clear_history(request: Request) -> dict:
    """清空历史任务。

    2026-09-17 修补（审查 P2-9）：调用方必须显式传 confirmed=true。
    （tasks_history.clear_history 已加 confirm 门禁，默认拒绝。）
    """
    try:
        # 兼容前端可能以 query 或 body 传递确认标记
        confirmed = False
        try:
            q = request.query_params.get("confirmed")
            if q is not None:
                confirmed = str(q).lower() in ("1", "true", "yes")
        except Exception:
            pass
        if not confirmed:
            try:
                body = await request.json()
                confirmed = bool((body or {}).get("confirmed"))
            except Exception:
                pass
        from tasks_history import clear_history
        n = clear_history(confirm=confirmed)
        return {"ok": True, "deleted": n}
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
    # 2026-09-29：「受理 ≠ 送达」—— records.status 的 SENT 只表示**已入池**
    # （core/dispatch.py:446 的 `accepted=True`），不代表服务端确认送达。
    # 这里按**真实投递证据**（回声帧 / 投递标记 / 平台拒收回执）派生
    # `delivery_state` 字段下发给前端，**不改任何既有状态与计数口径**
    # （sent/captured/fail 语义逐字不变，避免破坏别处消费点）。
    try:
        from services.delivery_verify import delivery_state_of as _dstate
    except Exception:                                    # pragma: no cover
        _dstate = None
    _acct = getattr(adm, "account_name", "") or ""
    out = []
    _cache: dict = {}
    for r in recs.values():
        d = r if isinstance(r, dict) else r.model_dump()
        _uid = str(d.get("uid", "") or "")
        _state = ""
        if _dstate and _acct and _uid:
            if _uid not in _cache:
                try:
                    _cache[_uid] = _dstate(_acct, uid=_uid)
                except Exception:
                    _cache[_uid] = ""
            _state = _cache[_uid]
        out.append({
            "key": d.get("key", ""),
            "uid": d.get("uid", "") or d.get("sec_uid", "") or "",
            "nickname": d.get("nickname", ""),
            "sec_uid": d.get("sec_uid"),
            "status": d.get("status", "captured"),
            # 真实投递结局：delivered / rejected / ""（无证据）
            "delivery_state": _state,
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


def _flag(key: str, settings_obj) -> bool:
    """读全局开关：**settings 实例优先**，配置中心仅作镜像同步。

    2026-09-09 定调（用户决策）：配置中心不反向覆盖 settings。
    理由：app_config.get() 在 reset 后会返回 schema 默认 True，
    若让配置中心优先，则 settings 里的 False 永不可达（回落路径是死代码），
    会出现「用户在任务页关了开关、切到设置页又被重置为开」的错乱。

    分工：
      - 读：一律以 settings 为准（保留接线前行为，零回归）
      - 写：save_config 双写，把值镜像进配置中心，供设置页展示
    """
    return bool(getattr(settings_obj, key, True))


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
        "liveUrl": getattr(adm, "live_url", "") or "",
        # 三个全局开关：优先读统一配置中心（未配置时回落到 settings 实例，零回归）
        "enableDanmaku": _flag("enable_danmaku", settings),
        "enableConsole": _flag("enable_console", settings),
        "enableSend": _flag("enable_send", settings),
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
        settings.enable_danmaku = cfg.enable_danmaku
        settings.enable_console = cfg.enable_console
        settings.enable_send = cfg.enable_send
        # 同时写入统一配置中心（设置页与任务页双向同步，B4）
        try:
            from services.app_config import save_section

            save_section("live", {
                "enable_danmaku": bool(cfg.enable_danmaku),
                "enable_console": bool(cfg.enable_console),
                "enable_send": bool(cfg.enable_send),
            })
        except Exception as e:  # 配置中心失败不影响原保存路径
            logger.warning(f"[TSK-004] " + f"[tasks] 开关写入配置中心失败: {e}")
        # 落盘到 SQLite kv_store（替代 config.json）
        from database import get_kv_json, set_kv_json
        data = get_kv_json("config", {}) or {}
        # 2026-09-17 修补（OCR 审查 HIGH —— 写回时抹掉词条 enabled 标记）：
        # `body.resolved()` 的 `cfg.dm_pool` 已被规范化成 **list[str]**
        # （models/task.py resolved(): [t if isinstance(t,str) else t.get("text")]），
        # 直接写回会把每条 `{text, enabled}` 压成纯文本 → **重载后启用/停用状态
        # 全部丢失**。而 POST /dm-pool 走 save_dm_pool() 存的是 `[{text,enabled}]`，
        # 两条写路径不一致。现统一：落盘前按**已有配置的 enabled 状态**重建对象。
        _old_pool = {
            (t.get("text") if isinstance(t, dict) else str(t)): bool(t.get("enabled", True))
            for t in (data.get("dm_pool") or []) if isinstance(t, (dict, str))
        }
        _pool_obj = [{"text": str(t), "enabled": _old_pool.get(str(t), True)}
                     for t in (cfg.dm_pool or [])]
        data.update({
            "max_target": cfg.max_target,
            "dm_pool": _pool_obj,
            "delay_range": cfg.delay_range,
            "interval": cfg.interval,
            "enable_danmaku": cfg.enable_danmaku,
            "enable_console": cfg.enable_console,
            "enable_send": cfg.enable_send,
        })
        set_kv_json("config", data)
    except Exception as e:
        logger.warning(f"[TSK-003] " + f"[tasks] 配置落盘失败（不影响本次保存）: {e}")
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
                # 2026-09-17 修补（OCR 审查 HIGH —— 导出列取错字段名恒为空）：
                # `SendRecord` 的时间字段是 `captured_at`（捕获）/ `sent_at`（已发），
                # 而元组分支原读 `capture_ts`/`send_ts` —— 两个名字都不存在 →
                # 「捕获时间/发送时间」两列**永远导出为空**。
                # （dict 分支才用 `send_ts`，与 /records 的对外字段一致。）
                if isinstance(r, dict):
                    ws.append([
                        i, r.get("nickname", ""), r.get("comment", ""),
                        r.get("status", ""), r.get("content", ""),
                        _fmt_ts(r.get("captured_at")), _fmt_ts(r.get("sent_at") or r.get("send_ts")),
                    ])
                else:
                    ws.append([i, getattr(r, "nickname", ""), getattr(r, "comment", ""),
                               getattr(r, "status", ""), getattr(r, "content", ""),
                               _fmt_ts(getattr(r, "captured_at", None)),
                               _fmt_ts(getattr(r, "sent_at", None))])
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
        logger.warning(f"[TSK-004] " + f"[tasks] 导出失败: {e}")
        return {"ok": False, "error": str(e)}

# ===========================================================================
# 定时任务中心（ADR-018 F4）
#
# 🔴 风控口径（ADR-018 D1 · 用户 2026-09-27 拍板）：**默认休眠**。
#    调度中心总开关与自动外发开关出厂均为 False，未显式开启时：
#      · start() 拒绝启动（fail-closed）
#      · 所有任务一律不执行
#    原因：定时自动向陌生人批量发私信是本项目迄今最大风控敞口。
#    本路由层**不提供**"绕过闸门强制执行"的接口 —— 那会成为绕过休眠的暗门。
# ===========================================================================

from pydantic import BaseModel


class SchedulerTaskBody(BaseModel):
    id: str = ""
    name: str = ""
    kind: str = "keyword_process"
    account: str = ""
    params: dict = {}
    interval: float = 3600.0
    enabled: bool = True


@router.get("/scheduler")
async def scheduler_state() -> dict:
    """定时任务中心状态（含**当前是否休眠**，如实上报，不粉饰）。"""
    try:
        from services import task_scheduler as ts
        return {"ok": True, "state": ts.get_state(), "tasks": ts.list_tasks()}
    except Exception as e:
        logger.warning(f"[SCHED-003] [tasks] 读取调度中心状态失败: {e}")
        return {"ok": False, "error": str(e)}


@router.post("/scheduler/start")
async def scheduler_start() -> dict:
    """启动调度中心。**总开关未开则拒绝**（fail-closed，ADR-018 D1）。"""
    try:
        from services import task_scheduler as ts
        return ts.start()
    except Exception as e:
        logger.warning(f"[SCHED-008] [tasks] 启动调度中心失败: {e}")
        return {"ok": False, "error": str(e)}


@router.post("/scheduler/stop")
async def scheduler_stop() -> dict:
    try:
        from services import task_scheduler as ts
        return ts.stop()
    except Exception as e:
        logger.warning(f"[SCHED-009] [tasks] 停止调度中心失败: {e}")
        return {"ok": False, "error": str(e)}


@router.post("/scheduler/tasks")
async def scheduler_save_task(body: SchedulerTaskBody) -> dict:
    """新增/更新一个定时任务（**不改任何开关**，开关只由环境变量控制）。"""
    try:
        from services import task_scheduler as ts
        tid = body.id or f"task_{int(time.time() * 1000)}"
        t = ts.Task(id=tid, name=body.name or tid, kind=body.kind,
                    account=body.account, params=body.params,
                    interval=body.interval, enabled=body.enabled)
        return ts.add_task(t)
    except Exception as e:
        logger.warning(f"[SCHED-010] [tasks] 保存定时任务失败: {e}")
        return {"ok": False, "error": str(e)}


@router.delete("/scheduler/tasks/{task_id}")
async def scheduler_delete_task(task_id: str) -> dict:
    try:
        from services import task_scheduler as ts
        return ts.remove_task(task_id)
    except Exception as e:
        logger.warning(f"[SCHED-011] [tasks] 删除定时任务失败: {e}")
        return {"ok": False, "error": str(e)}


@router.post("/scheduler/tasks/{task_id}/run")
async def scheduler_run_task(task_id: str) -> dict:
    """手动立即执行一个任务 —— **仍受全部闸门约束**（不提供绕过通道）。"""
    try:
        from services import task_scheduler as ts
        return ts.run_task_now(task_id)
    except Exception as e:
        logger.warning(f"[SCHED-012] [tasks] 手动执行任务失败: {e}")
        return {"ok": False, "error": str(e)}

