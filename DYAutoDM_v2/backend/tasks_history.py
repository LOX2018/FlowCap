# -*- coding: utf-8 -*-
"""历史任务记录（任务中心「历史任务」数据源）—— SQLite 版

每次引擎启动记录一条历史任务，停止/结束时更新状态与结果条数。
持久化到 data/dyautodm.db（SQLite，WAL 模式），重启后仍保留。
数据永不过期，仅用户「清空」时删除；列表查询支持分页。
"""
import json
import time

from loguru import logger


def start_task(acct: str, live_id: str, config: dict | None = None, records: list | None = None) -> int:
    """记录一条新历史任务（状态=运行中），返回任务 id。"""
    from database import get_db
    tid = int(time.time() * 1000)
    conn = get_db()
    conn.execute(
        "INSERT INTO tasks(id,acct,live_id,start_ts,end_ts,status,result_count,"
        "config,records,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (tid, acct or "", live_id or "", time.strftime("%Y-%m-%d %H:%M:%S"),
         "", "running", len(records or []),
         json.dumps(config or {}, ensure_ascii=False),
         json.dumps(records or [], ensure_ascii=False),
         tid / 1000.0),
    )
    conn.commit()
    logger.info(f"[history] 记录历史任务: 账号={acct} live={live_id} id={tid}")
    return tid


def finish_task(tid: int, status: str = "finished", result_count: int = 0,
                 records: list | None = None) -> None:
    """结束历史任务：更新状态、结果条数、记录快照。"""
    from database import get_db
    conn = get_db()
    sets = ["status=?", "end_ts=?", "result_count=?"]
    vals: list = [status, time.strftime("%Y-%m-%d %H:%M:%S"), result_count or 0]
    if records is not None:
        sets.append("records=?")
        vals.append(json.dumps(
            [r if isinstance(r, dict) else _rec_to_dict(r) for r in records],
            ensure_ascii=False))
    vals.append(tid)
    conn.execute(f"UPDATE tasks SET {','.join(sets)} WHERE id=?", vals)
    conn.commit()


def fix_stuck_tasks(force: bool = False) -> None:
    """同步引擎状态机：多任务「运行中」时只保留最新一条，其余标为「已停止」。

    根因：引擎崩溃/自然关播后队列发空时未收尾，导致历史任务永远停留在「运行中」。
    - force=False（默认，list 查询时）：保留最新一条 running（其它标的都收尾），
      避免引擎启动瞬间查询列表把正在运行的任务误标；
    - force=True（backend 启动时调用）：全部 running 收尾为 stopped——
      启动瞬间引擎必然尚未开始任何任务，所有 running 都是上一次进程遗留的悬挂任务。
    """
    from database import get_db
    conn = get_db()
    rows = conn.execute(
        "SELECT id FROM tasks WHERE status='running' ORDER BY id DESC"
    ).fetchall()
    if not rows:
        return
    if force:
        stuck_ids = [r["id"] for r in rows]
    else:
        if len(rows) <= 1:
            return
        stuck_ids = [r["id"] for r in rows[1:]]
    if not stuck_ids:
        return
    conn.execute(
        "UPDATE tasks SET status='stopped', end_ts=start_ts WHERE id IN("
        + ",".join("?" * len(stuck_ids)) + ")",
        stuck_ids,
    )
    conn.commit()
    logger.info(f"[history] 自动修复 {len(stuck_ids)} 条悬空「运行中」任务 -> 已停止")


def list_history(limit: int = 0, offset: int = 0) -> list[dict]:
    """返回历史任务列表（新的在前），支持分页。limit=0 表示全量。"""
    from database import get_db
    fix_stuck_tasks()
    conn = get_db()
    if limit and limit > 0:
        rows = conn.execute(
            "SELECT * FROM tasks ORDER BY id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM tasks ORDER BY id DESC").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["config"] = json.loads(d.get("config") or "{}")
        d["records"] = json.loads(d.get("records") or "[]")
        out.append(d)
    return out


def get_task(tid: int) -> dict | None:
    """单条任务详情。"""
    from database import get_db
    conn = get_db()
    r = conn.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["config"] = json.loads(d.get("config") or "{}")
    d["records"] = json.loads(d.get("records") or "[]")
    return d


def count_history() -> int:
    """历史任务总数（分页用）。"""
    from database import get_db
    conn = get_db()
    r = conn.execute("SELECT COUNT(*) AS c FROM tasks").fetchone()
    return r["c"] if r else 0


def clear_history() -> None:
    """清空历史任务（仅用户主动触发）。"""
    from database import get_db
    conn = get_db()
    conn.execute("DELETE FROM tasks")
    conn.commit()
    logger.info("[history] 历史任务已清空（用户主动操作）")


def _rec_to_dict(rec) -> dict:
    """把 SendRecord 模型转成可 JSON 序列化的 dict。"""
    if rec is None:
        return {}
    if isinstance(rec, dict):
        return dict(rec)
    out = {}
    for k, v in rec.__dict__.items():
        if k.startswith("_"):
            continue
        try:
            json.dumps(v)
            out[k] = v
        except (TypeError, ValueError):
            out[k] = str(v)
    return out
