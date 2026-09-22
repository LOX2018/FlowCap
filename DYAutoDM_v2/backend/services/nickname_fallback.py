# -*- coding: utf-8 -*-
"""昵称兜底补全（2026-09-17 新增，**默认关闭**）。

## 为什么要单列一个模块（而不是直接调 im/user/info）

用户原话（2026-09-17）：「im/user/info 主动批量查昵称可作为兜底方案，低频查询
风控系数较低。」

这**修订**了此前「昵称绝不主动查询」的绝对禁令，但**没有取消风控边界**。本模块
用「配置 + 限速 + 最小化」三重约束把风险压到最低，并把规则写死在代码里而不是
靠记忆遵守（Iron Law 6：显式配置，不做隐式探测）：

| 约束 | 实现 |
|---|---|
| **默认关闭** | `dm.nickname_fallback_enabled` 默认 `False`，须显式开启 |
| **硬限速** | 两次调用间隔 ≥ `min_interval_sec`（默认 600s） |
| **单次上限** | 每次最多 `max_per_run`（默认 10）个用户 |
| **每日上限** | 每日最多 `daily_cap`（默认 50）个用户，超限直接拒绝 |
| **只补缺失** | 仅处理 `peer_name` 为空/等于数字 UID 的会话，**已有昵称绝不覆盖** |
| **必须走 BCC** | 请求在账号自己的浏览器页面上下文发出（复用登录态），
  绝不在后端用 cookie 拼 requests 直发（铁律 §一·2） |

## 与既有链路的关系

· **常规来源仍是 BCC 被动 hook**（`conversation_capture` 截获前端自发请求），
  本模块只是**兜底**：正常情况下永远不该被触发；
· `api/platform._im_user_info_by_sec` 是**后端直发**版本（复用 cookie），
  属旧路径；本模块**不调用它**，改走 BCC 页面上下文。
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any, Callable

from loguru import logger

IM_USER_INFO_PATH = "/aweme/v1/web/im/user/info/"
BATCH = 20                      # 上游实测每批 20 个 sec_uid
DEFAULTS = {"enabled": False, "min_interval_sec": 600, "max_per_run": 10,
            "daily_cap": 50}

# 在**已登录页面上下文**里发同域 POST（表单编码，照实测参数）
FETCH_JS = """async (arg) => {
    const [path, ids] = arg;          // 本项目约定：exec_js(js, [a, b]) → JS 内解构
    if (location.origin !== 'https://www.douyin.com')
        throw new Error('需要抖音网页登录上下文');
    const body = new URLSearchParams();
    body.set('sec_user_ids', JSON.stringify(ids));
    const r = await fetch(path + '?aid=6383&device_platform=webapp', {
        method: 'POST', credentials: 'include',
        headers: {'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8'},
        body, signal: AbortSignal.timeout(20000),
    });
    return {status: r.status, body: await r.text()};
}"""

# 运行期状态（进程内；重启即重置，符合「低频兜底」语义）
# 端点经 asyncio.to_thread 调用 → 多请求可并发；限流是「读-判-写」序列，
# 必须加锁，否则两个请求能同时通过 cooldown/daily_cap 判定（check-then-act 竞态）。
_last_run_at = 0.0
_day_key = ""
_day_count = 0
_STATE_LOCK = threading.Lock()


def _cfg() -> dict:
    """读配置（缺项用默认；读失败也退回默认，绝不因配置缺失就放开限速）。"""
    out = dict(DEFAULTS)
    try:
        from services import app_config as AC
        for k, d in DEFAULTS.items():
            v = AC.get("dm", f"nickname_fallback_{k}", d)
            out[k] = v if v is not None else d
    except Exception:
        pass
    try:
        out["enabled"] = bool(out["enabled"])
        out["min_interval_sec"] = max(60, int(out["min_interval_sec"]))
        out["max_per_run"] = max(1, min(int(out["max_per_run"]), BATCH))
        out["daily_cap"] = max(1, int(out["daily_cap"]))
    except Exception:
        out.update(DEFAULTS)
    return out


def rate_limit_check(now: float | None = None) -> tuple[bool, str]:
    """限速判定 → (是否放行, 原因)。**先判限速再取数据**，不做无谓读库。

    判定与「跨日清零」在同一把锁内，避免并发下两个请求同时放行。
    """
    global _last_run_at, _day_key, _day_count
    cf = _cfg()
    if not cf["enabled"]:
        return False, "disabled"
    now = time.time() if now is None else now
    today = time.strftime("%Y-%m-%d", time.localtime(now))
    with _STATE_LOCK:
        if today != _day_key:
            _day_key, _day_count = today, 0
        if _day_count >= cf["daily_cap"]:
            return False, "daily-cap"
        if _last_run_at and (now - _last_run_at) < cf["min_interval_sec"]:
            left = int(cf["min_interval_sec"] - (now - _last_run_at))
            return False, f"cooldown({left}s)"
        return True, "ok"


def _mark_run(n: int, now: float | None = None) -> None:
    global _last_run_at, _day_key, _day_count
    now = time.time() if now is None else now
    today = time.strftime("%Y-%m-%d", time.localtime(now))
    with _STATE_LOCK:
        if today != _day_key:
            _day_key, _day_count = today, 0
        _last_run_at = now
        _day_count += max(0, int(n))


def missing_nickname_convs(account: str, limit: int, db=None) -> list[dict]:
    """挑出**确需兜底**的会话：peer_name 为空，或 peer_name 是数字 UID 占位。

    已有真实昵称的会话**绝不**进入候选（避免把兜底变成批量刷新）。
    ⚠️ 占位判定用「纯数字且长度 ≥6」而不是「等于 peer_id」：
    实测 peer_name 可能被写成发送方 UID，而 peer_id 列存的是会话内对端 UID，
    两者不总是相等（曾据此漏判，测试用例 test_candidates_only_missing 抓出）。
    """
    from database import get_db
    conn = db or get_db()
    # 本项目的 sec_uid 存放在 `short_id` 列（见 conversation_capture 的写入注释）。
    rows = conn.execute(
        "SELECT conv_id, peer_id, peer_name, short_id FROM dm_conversations "
        "WHERE account=? ORDER BY last_ts DESC LIMIT 500", (account,)).fetchall()
    out: list[dict] = []
    for r in rows:
        nm = (r["peer_name"] or "").strip()
        pid = (r["peer_id"] or "").strip()
        if nm and not (_is_uid_placeholder(nm)):
            continue                      # 已有真实昵称 → 跳过
        sec = (r["short_id"] or "").strip()
        if not sec or sec.isdigit():
            continue                      # 无 sec_uid 无从查询（sec_user_ids 只认 sec_uid）
        out.append({"conv_id": r["conv_id"], "sec_uid": sec, "peer_id": pid})
        if len(out) >= limit:
            break
    return out


def _is_uid_placeholder(name: str) -> bool:
    """数字 UID 占位判定，委托至 verdicts 判据（M-10 F7）。"""
    from services.verdicts import is_uid_placeholder
    return is_uid_placeholder(name)


def parse_user_info(body: Any) -> dict[str, dict]:
    """解析 im/user/info 响应 → {sec_uid: {nickname, avatar, uid}}（只取需要的三键）。"""
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except Exception:
            return {}
    if not isinstance(body, dict) or body.get("status_code") != 0:
        return {}
    out: dict[str, dict] = {}
    for u in (body.get("data") or []):
        if not isinstance(u, dict):
            continue
        sec = str(u.get("sec_uid") or "")
        nick = str(u.get("nickname") or "").strip()
        if not sec or not nick:
            continue
        av = ""
        for k in ("avatar_small", "avatar_thumb", "avatar"):
            v = u.get(k)
            if isinstance(v, dict):
                lst = v.get("url_list") or []
                if lst and isinstance(lst[0], str):
                    av = lst[0]
                    break
            elif isinstance(v, str) and v.startswith("http"):
                av = v
                break
        out[sec] = {"nickname": nick, "avatar": av,
                    "uid": str(u.get("uid") or "")}
    return out


def run_fallback(account: str, exec_js: Callable[[str, Any], Any], *,
                 limit: int | None = None, db=None,
                 dry_run: bool = False) -> dict[str, Any]:
    """执行一次昵称兜底（受配置与限速约束）。

    `exec_js(script, arg)` 由调用方注入（端点侧走 BCC `/exec_js`）。
    返回 `{ok, reason, candidates, queried, updated, skipped, limit_info}`。
    """
    cf = _cfg()
    if limit is not None:
        cf["max_per_run"] = max(1, min(int(limit), BATCH))
    allow, why = rate_limit_check()
    if not allow:
        return {"ok": False, "reason": why, "candidates": 0, "queried": 0,
                "updated": 0, "skipped": 0, "limit_info": _limit_info(cf)}
    cands = missing_nickname_convs(account, cf["max_per_run"], db=db)
    if not cands:
        _mark_run(0)
        return {"ok": True, "reason": "no-candidates", "candidates": 0,
                "queried": 0, "updated": 0, "skipped": 0,
                "limit_info": _limit_info(cf)}
    if dry_run:
        return {"ok": True, "reason": "dry-run", "candidates": len(cands),
                "queried": 0, "updated": 0, "skipped": len(cands),
                "would_query": [c["sec_uid"] for c in cands],
                "limit_info": _limit_info(cf)}

    ids = [c["sec_uid"] for c in cands]
    try:
        res = exec_js(FETCH_JS, [IM_USER_INFO_PATH, ids])
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[NICK-001] " + f"兜底查询失败: {type(e).__name__}")
        # 失败也必须记账：否则上游持续 4xx/超时时永不进入冷静期、不耗日额，
        # 调用方可死循环重试 —— 恰好击穿本模块唯一的风控意图。
        _mark_run(len(ids))
        return {"ok": False, "reason": f"exec:{type(e).__name__}",
                "candidates": len(cands), "queried": 0, "updated": 0,
                "skipped": len(cands), "limit_info": _limit_info(cf)}
    if not isinstance(res, dict) or res.get("status") != 200:
        _mark_run(len(ids))                      # 同上：非 200 同样计入限流
        return {"ok": False, "reason": f"http:{res.get('status') if isinstance(res, dict) else '?'}",
                "candidates": len(cands), "queried": 0, "updated": 0,
                "skipped": len(cands), "limit_info": _limit_info(cf)}
    users = parse_user_info(res.get("body"))
    _mark_run(len(ids))

    from database import get_db
    conn = db or get_db()
    updated = 0
    for c in cands:
        u = users.get(c["sec_uid"])
        if not u:
            continue
        # 只补缺失：再次确认该会话仍无真实昵称（防并发把新昵称覆盖掉）
        row = conn.execute(
            "SELECT peer_name, peer_id FROM dm_conversations WHERE account=? AND conv_id=?",
            (account, c["conv_id"])).fetchone()
        if row is None:
            continue
        nm0 = (row["peer_name"] or "").strip()
        if nm0 and not _is_uid_placeholder(nm0):
            continue
        if u["avatar"]:
            conn.execute(
                "UPDATE dm_conversations SET peer_name=?, avatar=COALESCE(NULLIF(avatar,''),?) "
                "WHERE account=? AND conv_id=?",
                (u["nickname"], u["avatar"], account, c["conv_id"]))
        else:
            conn.execute(
                "UPDATE dm_conversations SET peer_name=? WHERE account=? AND conv_id=?",
                (u["nickname"], account, c["conv_id"]))
        updated += 1
    try:
        conn.commit()
    except Exception:
        pass
    logger.info(f"[NICK-002] " + f"昵称兜底: 候选 {len(cands)} 查询 {len(ids)} "
                f"更新 {updated}（限速 {cf['min_interval_sec']}s / 日上限 {cf['daily_cap']}）")
    return {"ok": True, "reason": "ok", "candidates": len(cands),
            "queried": len(ids), "updated": updated,
            "skipped": len(cands) - updated, "limit_info": _limit_info(cf)}


def _limit_info(cf: dict | None = None) -> dict:
    """本次生效的限速参数。传入 `cf` 时用它（避免与调用侧 `limit` 改写不一致）。"""
    cf = cf if cf is not None else _cfg()
    return {"enabled": cf["enabled"], "min_interval_sec": cf["min_interval_sec"],
            "max_per_run": cf["max_per_run"], "daily_cap": cf["daily_cap"],
            "used_today": _day_count, "last_run_at": int(_last_run_at or 0)}


def status() -> dict:
    """当前兜底状态（供状态页/端点展示，便于用户确认它处于关闭态）。"""
    cf = _cfg()
    allow, why = rate_limit_check()
    return {"ok": True, "config": cf, "limit_info": _limit_info(),
            "would_allow_now": allow, "gate": why}
