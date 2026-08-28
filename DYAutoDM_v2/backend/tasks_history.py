# -*- coding: utf-8 -*-
"""历史任务记录（任务中心「历史任务」数据源）—— SQLite 版

每次引擎启动记录一条历史任务，停止/结束时更新状态与结果条数。
持久化到 data/dyautodm.db（SQLite，WAL 模式），重启后仍保留。
数据永不过期，仅用户「清空」时删除；列表查询支持分页。
"""
import json
import os
import time

from loguru import logger


def start_task(acct: str, live_id: str, config: dict | None = None, records: list | None = None) -> int:
    """记录一条新历史任务（状态=运行中），返回任务 id。

    同时写入当前进程 pid，用于区分「本进程正在运行」与「上次进程退出未收尾
    残留的悬空 running」——后者由 fix_stuck_tasks 按 pid 比对兜底修正。
    """
    from database import get_db
    tid = int(time.time() * 1000)
    conn = get_db()
    conn.execute(
        "INSERT INTO tasks(id,acct,live_id,start_ts,end_ts,status,result_count,"
        "config,records,created_at,pid) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (tid, acct or "", live_id or "", time.strftime("%Y-%m-%d %H:%M:%S"),
         "", "running", len(records or []),
         json.dumps(config or {}, ensure_ascii=False),
         json.dumps(records or [], ensure_ascii=False),
         tid / 1000.0, os.getpid()),
    )
    conn.commit()
    logger.info(f"[history] 记录历史任务: 账号={acct} live={live_id} id={tid} pid={os.getpid()}")
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
    """兜底修正悬空「运行中」任务（按进程 pid 比对，而非靠查询时机启发式）。

    根因：引擎崩溃/被强杀/自然关播时，正常收尾路径（AutoDM.shutdown →
    stop → _finish_history_task）没机会执行，导致历史任务永远停在「运行中」。
    start_task 已记下每条任务的进程 pid，因此「不属于当前进程的 running」一定是
    上次进程遗留的悬挂任务，可直接收尾为 stopped——这不会误伤本进程真正在跑的任务。

    - force=False（默认，list 查询时）：只修 pid 不匹配当前进程的 running；
    - force=True（backend 启动时）：当前进程必然还没开始任何任务，
      所有 running（含 pid 匹配当前进程的，理论上不存在）都是遗留，全部收尾。
      保留该参数仅为语义清晰，实际与 False 行为一致（pid 不匹配即修）。
    """
    from database import get_db
    cur_pid = os.getpid()
    conn = get_db()
    if force:
        # 启动时：所有 running 都是上次进程遗留（无论 pid 是否巧合匹配）
        rows = conn.execute(
            "SELECT id FROM tasks WHERE status='running'"
        ).fetchall()
    else:
        # 列表查询时：仅收尾 pid 不匹配当前进程的 running，
        # 本进程自己那条 running 永远保留（不会被误杀）
        rows = conn.execute(
            "SELECT id FROM tasks WHERE status='running' AND pid<>?",
            (cur_pid,),
        ).fetchall()
    stuck_ids = [r["id"] for r in rows]
    if not stuck_ids:
        return
    conn.execute(
        "UPDATE tasks SET status='stopped', end_ts=start_ts WHERE id IN("
        + ",".join("?" * len(stuck_ids)) + ")",
        stuck_ids,
    )
    conn.commit()
    logger.info(f"[history] 收尾 {len(stuck_ids)} 条悬空「运行中」任务 -> 已停止")


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
