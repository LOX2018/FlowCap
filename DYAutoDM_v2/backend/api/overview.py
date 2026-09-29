"""总览路由：getOverview / getStats / getFunnel"""
import asyncio
import time

from fastapi import APIRouter, Request
from models.overview import OverviewResponse, StatusResponse
from auto_dm import accounts as acct_core

router = APIRouter()


def _daemon_alive(port: int) -> bool:
    return acct_core._port_open(port, timeout=0.3)


def _any_daemon_alive(port_of) -> bool:
    """聚合判定：**任一已登记账号**的守护在监听即为真。

    设计意图（SoC，2026-09-22 修复）：`/overview` 是**会员级聚合视图**，不应依赖
    「某个默认账号」的语境。本会员的 `accounts_index.current` 可为 None（新版已取消
    「默认账号」概念），旧实现用 `browser_daemon_port()` 的默认参数 →
    `name = name or current_name()` → `hash(None)` → 端口 10288/12258（无监听）
    → 总览页**恒报** `alive:false`，与账号页的 `browserDaemonAlive:true` 自相矛盾。

    判据（可判定）：`/api/overview.browserDaemon.alive == /api/accounts[任一].browserDaemonAlive`
    在同机同刻一致。
    """
    try:
        names = [n for n, _p in acct_core.list_accounts()]
    except Exception:
        names = []
    for n in names:
        try:
            if _daemon_alive(port_of(n)):
                return True
        except Exception:
            continue
    return False


@router.get("/overview")
async def get_overview(request: Request) -> dict:
    """总览（替代原版 WebBridge.getOverview）

    返回字段对齐前端 client.ts 的 Overview 接口：
    running / paused / sent / limit / queue /
    browserDaemon{alive,signReady} / recvDaemon{alive}
    """
    adm = request.app.state.adm
    # 2026-09-22 修复（聚合视图 SoC）：此处**不再**取「默认账号」端口。
    # 旧实现 `browser_daemon_port()` / `recv_daemon_port()` 走默认参数
    # `name = name or current_name()`；本会员 `current` 常为 None（已取消默认账号
    # 概念）→ 端口按 hash(None) 算成 10288/12258（无监听）→ 总览恒报「守护未运行」。
    # 现改为「任一已登记账号的守护在监听」即为真（与 /api/accounts 的口径一致）。
    # 2026-09-17 修补（OCR 审查 HIGH —— 在 async 处理器里做阻塞 IO）：
    # `_port_open` 是同步 `connect_ex`（超时 0.3s）。改到线程里并发探测（asyncio.to_thread）。
    b_alive, r_alive = await asyncio.gather(
        asyncio.to_thread(_any_daemon_alive, acct_core.browser_daemon_port),
        asyncio.to_thread(_any_daemon_alive, acct_core.recv_daemon_port),
    )
    # running 用 adm.is_running（含 starting/stopping）：软停止后存量私信仍在发送，
    # 任务中心必须保留「运行中」行，否则停止存量私信、进入任务的入口就消失了。
    return {
        "running": adm.is_running,
        "paused": adm.state.value == "paused",
        "engineState": adm.state.value,
        "statusMsg": adm.status_msg,
        "sent": adm.sent_count,
        "limit": adm.limit,
        "queue": adm.dispatch.queue_size() if adm.dispatch else 0,
        "browserDaemon": {"alive": b_alive, "signReady": b_alive},
        "recvDaemon": {"alive": r_alive},
        "engine_state": adm.state.value,  # 冗余字段，兼容旧调用
    }


@router.get("/stats")
async def get_stats(request: Request) -> dict:
    """实时统计（前端轮询 / WebSocket 推送）

    返回对齐前端 overview.tsx 的 StatsResp：
    { ok, total, sent, list:[{captureTs,status,nickname,comment,content}] }
    """
    adm = request.app.state.adm
    records = (adm.dispatch.records if adm.dispatch else {}) or {}
    return {
        "ok": True,
        "total": len(records),
        "sent": adm.sent_count,
        "list": [
            {
                "captureTs": r.captured_at,
                "status": r.status.value if hasattr(r.status, "value") else r.status,
                "nickname": r.nickname,
                "comment": r.comment,
                "content": r.content,
            }
            for r in records.values()
        ],
    }


# ════════════════════════════════════════════════════════════════
# 业务漏斗聚合（ADR-032，2026-09-30）
# ════════════════════════════════════════════════════════════════
#
# ## 为什么需要它（ADR-032 §4.1）
#
# `/overview` 的 sent/limit 是**单个引擎实例的内存态**（`adm.limit` 未启动时
# 回落为配置默认值），把它当全局指标展示会得到恒定的假数字。
# 本端点改为**按业务真值从 SQLite 聚合**，总览页不再直读引擎内存态。
#
# ## 风控契约（铁律，不可破坏）
#
# 只读本地 SQLite（`crawl_history` / `dm_messages`），**零网络零浏览器**，
# 不触发任何捕获 / 昵称查询 / 浏览器动作。可安全高频轮询。
#
# ## 🔴 口径铁律：role='me' ≠ 真实已发私信
#
# `dm_messages` 里 `role='me'` 含**平台提示文案**（实测某账号 183 条全为平台
# 提示，非真实私信）。禁止直接 COUNT。清洗判据**复用既有 SSOT，禁止自造**：
#   · services.message_schema.is_system_text / is_noise_text
#   · 读侧口径对齐 services/dm_search.py:233-234
# 被平台拒发（`PLATFORM_REJECT_PREFIX`）**独立为 `rejected`**，不混入 sent
# —— 混入即是「假成功」，违反项目铁律。


def _day_start_ts(tz_hours: int, day: str = "") -> float:
    """指定日 00:00 的 Unix 时间戳（按给定时区切日，非 UTC）。

    日切必须按本地时区：否则北京时间 08:00 前「今日」会错位到前一天。
    `tz_hours` 夹取 [-12, 14] —— 与 `dm_search.daily_stats` 既有约定一致。
    `day` 传 `YYYY-MM-DD` 则取该日；空串取本日。
    """
    try:
        off = int(tz_hours)
    except Exception:
        off = 8
    off = max(-12, min(off, 14))
    local_now = time.time() + off * 3600
    local_midnight = local_now - (local_now % 86400)
    if day:
        try:
            y, m, d = (int(x) for x in day.split("-"))
            # 该日本地 00:00 → 转回 UTC ts
            import calendar
            local_target = calendar.timegm((y, m, d, 0, 0, 0, 0, 0, 0))
            local_midnight = float(local_target)
        except Exception:
            pass  # 解析失败 → 回落到本日
    return local_midnight - off * 3600


def _latest_active_day(tz_hours: int) -> str:
    """最近有数据的那一天（`YYYY-MM-DD` 本地日）；无数据返回空串。

    用途：凌晨看总览时今日尚无数据，全 0 会被误读为「系统没工作」。
    只读两表 MAX(ts)，零网络。
    """
    from database import exec_query
    off = max(-12, min(int(tz_hours), 14))
    best = 0.0
    for tbl in ("crawl_history", "dm_messages"):
        try:
            r = exec_query(f"SELECT MAX(ts) mx FROM {tbl}")  # noqa: S608 - 表名白名单固定
            v = float((r[0] or {}).get("mx") or 0)
        except Exception:
            v = 0.0
        best = max(best, v)
    if best <= 0:
        return ""
    return time.strftime("%Y-%m-%d", time.gmtime(best + off * 3600))


def _funnel_sync(tz_hours: int, day: str = "") -> dict:
    """在**非事件循环**线程里做全部同步 SQLite 读（调用方用 to_thread 包裹）。"""
    from database import exec_query
    from services.message_schema import is_system_text, is_noise_text

    # `day="latest"` → 回到最近有数据那天（凌晨看总览不至于全 0）
    latest_day = ""
    if day == "latest":
        latest_day = _latest_active_day(tz_hours)
        day = latest_day

    start = _day_start_ts(tz_hours, day)
    end = start + 86400
    off = max(-12, min(int(tz_hours), 14))

    # ── ① 采集（crawl_history：account/kind/keyword/target/result_count/ts）──
    crawl_rows = exec_query(
        "SELECT kind, COUNT(*) AS n, COALESCE(SUM(result_count), 0) AS rc "
        "FROM crawl_history WHERE ts >= ? AND ts < ? GROUP BY kind",
        (start, end),
    )
    crawl_kinds: dict[str, int] = {}
    crawl_runs = 0
    crawl_results = 0
    for r in crawl_rows:
        k = str(r.get("kind") or "video")
        crawl_kinds[k] = int(r.get("n") or 0)
        crawl_runs += int(r.get("n") or 0)
        crawl_results += int(r.get("rc") or 0)

    # ── ② 捕获（今日入库的消息，按 role 分客户来消息 / 我方）──
    #    读侧口径对齐 dm_search.py:233-234：剔除 msg_type=50001 与未知媒体占位。
    msg_rows = exec_query(
        "SELECT role, text, msg_type FROM dm_messages "
        "WHERE ts >= ? AND ts < ? AND ts > 0 "
        "  AND msg_type <> '50001' "
        "  AND (text IS NULL OR (text NOT LIKE '[未知媒体]%' AND text <> '[分享视频]'))",
        (start, end),
    )
    today_theirs = 0
    raw_me = 0
    today_sent = 0
    rejected = 0
    for r in msg_rows:
        role = str(r.get("role") or "")
        text = r.get("text") or ""
        if role == "them":
            today_theirs += 1
            continue
        if role != "me":
            continue
        raw_me += 1
        # 🔴 清洗：平台提示 / 噪音前缀一律不计入「真实已发」。
        if is_noise_text(text):
            # 平台拒发文案归入 rejected 独立维度；其余噪音直接丢弃。
            if "对方回复或关注你之前" in text or "对方回复你或互关之前" in text:
                rejected += 1
            continue
        if is_system_text(text):
            rejected += 1
            continue
        today_sent += 1

    # ── ③ 账号（凭证可用性）──
    #     复用 api/accounts.py::_to_raw_account（前端账号页的同一 SSOT）
    #     —— 它内含 TTL 缓存，不会因本端点轮询而重复触发重型网络探活。
    #     🔴 禁止在此自造凭证判定（会与账号页结论不一致）。
    acct_total = 0
    acct_active = 0
    acct_cred_ok = 0
    try:
        from api.accounts import _to_raw_account
        names = [n for n, _p in acct_core.list_accounts()]
        for name in names:
            acct_total += 1
            try:
                raw = _to_raw_account(name)
            except Exception:
                continue
            if (raw.get("wpEngine") or {}).get("level") == "ok":
                acct_cred_ok += 1
            if raw.get("browserDaemonAlive") or raw.get("loggedIn"):
                acct_active += 1
    except Exception:
        pass

    day = time.strftime("%Y-%m-%d", time.gmtime(start + off * 3600 + 12 * 3600))
    today = time.strftime("%Y-%m-%d", time.gmtime(time.time() + off * 3600))
    return {
        "ok": True,
        "date": day,
        "tz": off,
        # 前端据此决定是否提示「今日尚无数据，展示最近活跃日」
        "is_today": day == today,
        "latest_day": latest_day or _latest_active_day(off),
        "crawl": {
            "today_runs": crawl_runs,
            "today_results": crawl_results,
            "kinds": crawl_kinds,
        },
        "capture": {
            "today_comments": today_theirs,
            "today_theirs": today_theirs,
        },
        "dm": {
            "today_sent": today_sent,
            "rejected": rejected,
            "raw_me_rows": raw_me,  # 诊断用：清洗前的 role='me' 行数
        },
        "accounts": {
            "total": acct_total,
            "active": acct_active,
            "credential_ok": acct_cred_ok,
        },
    }


@router.get("/funnel")
async def get_funnel(tz: int = 8, day: str = "") -> dict:
    """业务漏斗聚合（ADR-032）。只读本地库，零网络零浏览器。

    `tz`  默认 +8（中国时区），日切按该时区。
    `day` 可选 `YYYY-MM-DD`；缺省为**今日**。给 `"latest"` 则回到最近有数据的
          那一天 —— 凌晨看总览时今日往往尚无数据，全 0 会被误读为「没工作」。

    同步 IO 走 to_thread，不阻塞事件循环。
    """
    return await asyncio.to_thread(_funnel_sync, tz, day)
