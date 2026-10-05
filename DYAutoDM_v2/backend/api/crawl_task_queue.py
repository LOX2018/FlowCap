# coding=utf-8
"""采集任务队列（★ 任务中心：悬浮窗显示「正在运行的采集任务」）。

## 定位与诚实边界（**必读，避免误当持久化**）

本模块**只登记与跟踪任务状态，不执行任何采集**。真实采集仍由
`api/crawl.py` 的 `POST /api/crawl/comments/batch` 负责；两者解耦，
是为了避免与采集主链路的改动互相耦合（采集逻辑演进时任务表不受影响）。

🔴 **状态存放在模块级内存 dict，进程级、重启即丢失。**
这是**有意为之的诚实边界**，不是「还没做完」：
  · 任务队列的语义是「这一次进程运行期间，谁正在跑、跑到第几个」，
    跨重启的「上次任务」在对抗性爬虫场景里没有意义（凭证/接口状态已变）；
  · 落 SQLite 需要新增表 + 迁移，且活跃库由会员态运行时决定
    （`database._db_path()` 有登录态走会员分库），写错库会「静默成功」
    —— 与本项目「禁止假成功」红线冲突；
  · 因此这里**不假装持久化**。若将来要持久化，先确认落哪张库、谁负责清理。

## 并发模型

FastAPI 是 async 事件循环，但进度上报 / 列表查询 / 取消可能来自
不同请求，且采集循环可能在 **worker 线程** 里回调
（本项目 `DouyinAPI` 是同步 requests 实现，路由内一律 `asyncio.to_thread` 包裹）。
⇒ 所有读写内存表的路径统一走 `threading.Lock`（`_LOCK`），
读出的是 **dict 拷贝**，避免锁外改到别人的活对象。

## 路由挂载（⚠️ 待父会话在 main.py 接线）

本文件**不含** `include_router` 调用（避免与并行改动冲突）。项目惯例是
router 定义处 `APIRouter()` 不带 prefix，prefix 在 `main.py` 统一注入：

```python
from api import crawl_task_queue as crawl_tasks_api
app.include_router(crawl_tasks_api.router, prefix="/api/crawl/tasks", tags=["crawl"])
```

未接线时本模块的所有端点**不可达**（这是预期的，不是 bug）。

phase 建议取值：
  · "anon_probe" —— 匿名探针
  · "collect"    —— 真凭证采集
  · "filter"     —— 高价值过滤
  · "dm"         —— 私信发送
"""
from __future__ import annotations

import threading
import time
from typing import Any

from fastapi import APIRouter, HTTPException
from loguru import logger
from pydantic import BaseModel

router = APIRouter()

# ---------------------------------------------------------------------------
# 内存任务表
# ---------------------------------------------------------------------------
# 🔴 进程级内存，**重启丢失**（理由见模块 docstring）。
# 所有读写必须持 _LOCK；对外一律返回 dict 拷贝。
_LOCK = threading.Lock()
_TASKS: dict[str, dict[str, Any]] = {}

# 容量上限：登记表「已有多少条」的总闸。超过时丢**最旧的已结束**条目。
# 理由：采集任务由用户显式触发，一个长跑的桌面会话里可能堆积上百条历史任务；
# 若只靠用户手动 DELETE/clear，表会无限增长（内存泄漏）。同时**不能**丢
# running 的条目 —— 那是悬浮窗正在显示的真实进度，丢了就是「静默假消失」。
_MAX_TASKS = 200

# 允许的 status 取值（收敛，防前端传入任意字符串把状态机写脏）
_ALLOWED_STATUS = ("running", "done", "failed", "cancelled")


def _new_task_id() -> str:
    """任务 id：`ct_<epoch_ms>`。毫秒时间戳保证同毫秒内多次登记也不会撞。"""
    return f"ct_{int(time.time() * 1000)}"


def _prune_locked() -> int:
    """容量保护：**仅在持锁时调用**。返回被丢弃的条目数。

    优先丢弃「最旧的已结束」条目（status != running）。若一条已结束条目都
    没有（全部在跑），则**不丢** —— 宁可短时超限，也不让用户的在跑任务凭空消失
    （这与 `POST /tasks/clear` 的「绝不清 running」是同一条约束）。
    """
    over = len(_TASKS) - _MAX_TASKS
    if over <= 0:
        return 0
    finished = [(t.get("created_at") or 0.0, tid)
                for tid, t in _TASKS.items() if t.get("status") != "running"]
    finished.sort()                       # 最旧在前
    dropped = 0
    for _, tid in finished:
        if dropped >= over:
            break
        _TASKS.pop(tid, None)
        dropped += 1
    if dropped:
        logger.info(f"[crawl-tasks] 任务表超上限，丢弃最旧已结束任务 {dropped} 条"
                    f"（当前 {len(_TASKS)}/{_MAX_TASKS}）")
    return dropped


def _norm_ids(raw: list[str] | None) -> list[str]:
    """去空白 + 去重 + 保序（与 crawl.py 的 ids 归一口径一致）。"""
    ids: list[str] = []
    seen: set[str] = set()
    for x in (raw or []):
        s = str(x or "").strip()
        if s and s not in seen:
            seen.add(s)
            ids.append(s)
    return ids


# ---------------------------------------------------------------------------
# 请求模型
# ---------------------------------------------------------------------------
class CrawlTaskRegisterRequest(BaseModel):
    """登记一个采集任务（**只登记与跟踪，不执行采集**）。

    🔴 `extra="forbid"`（必读）：Pydantic 默认**静默丢弃**未知字段。
    本项目已实测踩过：旧 sidecar 对 `start_date` 返回 200 且不生效，
    表现为「日期筛选没用」却毫无报错 —— 正是「假成功」红线。
    ⇒ 前端比后端新时**响亮地 422**，而不是假装成功。
    """
    model_config = {"extra": "forbid"}
    account: str
    aweme_ids: list[str] = []
    min_score: int = 0        # 高价值关键词最低得分（0 = 不过滤）
    start_date: str = ""      # YYYY-MM-DD，空 = 不限
    end_date: str = ""        # YYYY-MM-DD，空 = 不限


class CrawlTaskProgressRequest(BaseModel):
    """采集循环上报进度。字段全部可缺省 ⇒ 只带要改的。"""
    model_config = {"extra": "forbid"}
    phase: str = ""
    done: int = 0
    total: int = 0
    ok_works: int = 0
    fail_works: int = 0
    error: str = ""
    status: str = ""


# ---------------------------------------------------------------------------
# 端点
# ---------------------------------------------------------------------------
@router.get("/tasks")
async def list_tasks():
    """列出采集任务，按 created_at **倒序**（新的在前）。

    `storage` 字段如实透出状态存储位置，前端/排障时不必猜「这数据能不能信」。
    """
    with _LOCK:
        tasks = [dict(t) for t in _TASKS.values()]
    tasks.sort(key=lambda t: t.get("created_at") or 0.0, reverse=True)
    return {"ok": True, "storage": "memory(process-level, lost on restart)",
            "count": len(tasks), "tasks": tasks}


@router.post("/tasks")
async def register_task(body: CrawlTaskRegisterRequest):
    """登记一个采集任务，返回 `task_id` 供后续 progress 上报。

    **fail-closed**：`aweme_ids` 为空直接 400「缺少作品 ID 列表」——
    与 `crawl.py:comments/batch` 同口径。静默接受空任务会造出一条永远
    「running」但什么都不会做的僵尸任务，悬浮窗上显示为在跑 = 假成功。
    """
    ids = _norm_ids(body.aweme_ids)
    if not ids:
        raise HTTPException(400, "缺少作品 ID 列表")

    now = time.time()
    tid = _new_task_id()
    task = {
        "id": tid,
        "account": body.account,
        "aweme_ids": ids,
        "phase": "queued",
        "done": 0,
        "total": len(ids),
        "ok_works": 0,
        "fail_works": 0,
        "created_at": now,
        "updated_at": now,
        "status": "running",
        "error": "",
    }
    with _LOCK:
        _TASKS[tid] = task
        _prune_locked()
    logger.info(f"[crawl-tasks] 登记任务 {tid} account={body.account} "
                f"作品 {len(ids)} 个")
    return {"ok": True, "task_id": tid}


@router.post("/tasks/{task_id}/progress")
async def report_progress(task_id: str, body: CrawlTaskProgressRequest):
    """上报进度。找不到任务 → **404**，绝不静默新建。

    status 传了就用（须在 `_ALLOWED_STATUS` 内）；传空则：若
    `total > 0 且 done >= total` 自动置 "done"，否则保持原状。
    """
    status = (body.status or "").strip()
    if status and status not in _ALLOWED_STATUS:
        raise HTTPException(400, f"status 非法，允许值：{'/'.join(_ALLOWED_STATUS)}")

    with _LOCK:
        task = _TASKS.get(task_id)
        if task is None:
            # 🔴 fail-closed：找不到就 404。不新建 —— 新建会让调用方
            # 「以为上报成功」，实际进度丢失且表里多一条幽灵任务。
            raise HTTPException(404, "任务不存在")
        if body.phase:
            task["phase"] = body.phase
        task["done"] = int(body.done)
        task["total"] = int(body.total)
        task["ok_works"] = int(body.ok_works)
        task["fail_works"] = int(body.fail_works)
        if body.error:
            task["error"] = body.error
        if status:
            task["status"] = status
        elif int(body.total) > 0 and int(body.done) >= int(body.total):
            task["status"] = "done"
        task["updated_at"] = time.time()
        out = dict(task)
    logger.info(f"[crawl-tasks] 进度 {out['id']} phase={out['phase']} "
                f"{out['done']}/{out['total']} status={out['status']}")
    return {"ok": True, "task": out}


@router.delete("/tasks/{task_id}")
async def delete_task(task_id: str):
    """取消/移除单个任务。**幂等**：不存在时 `deleted: false`，不报错。"""
    with _LOCK:
        removed = _TASKS.pop(task_id, None) is not None
    if removed:
        logger.info(f"[crawl-tasks] 移除任务 {task_id}")
    return {"ok": True, "deleted": removed}


@router.post("/tasks/clear")
async def clear_tasks():
    """清空所有**已结束**的任务（status != "running"）。

    🔴 running 的绝不删：在跑的任务一删，悬浮窗上就消失了 —— 用户以为
    停了，实际还在采集且不再上报进度（后续 progress 会 404）。这是本项目
    「静默假成功」的典型形态，故此处显式只清已结束条目。
    """
    with _LOCK:
        victims = [tid for tid, t in _TASKS.items() if t.get("status") != "running"]
        for tid in victims:
            _TASKS.pop(tid, None)
        remaining = len(_TASKS)
    if victims:
        logger.info(f"[crawl-tasks] 清理已结束任务 {len(victims)} 条，"
                    f"剩余 {remaining} 条（含在跑）")
    return {"ok": True, "removed": len(victims), "remaining": remaining}