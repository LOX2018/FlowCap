"""账号管理路由

取代原版 WebBridge 的账号相关方法。
关键改进：
- getAccounts 拆为轻量 list（仅端口探活）+ 重量级 verify（按需触发）
- 新增 /scan-status 查询扫码状态（替代 fire-and-forget 的间接推断）
- /check 复用 auto_dm.accounts.verify_account 做双引擎校验（含私信列表拉取）
"""
import os
import asyncio
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, Request, HTTPException
from loguru import logger
from models.account import (
    AccountInfo,
    AddAccountRequest,
    ScanLoginResponse,
    SetRoleRequest,
)
from auto_dm import accounts as acct_core

router = APIRouter()

# 后台扫码状态：name -> {"running": bool, "done": bool, "loggedIn": bool, "error": str}
_scan_state: dict[str, dict] = {}

# 轻量缓存：getAccounts 轮询频繁（默认 3s），而 verify_account 含网络探活（get_my_uid）
# 单次耗时 1~3s。为避免每次轮询都卡顿，对单账号 verify 结果做 TTL 缓存（与轮询间隔一致），
# 命中缓存瞬时返回，把列表刷新延迟从 2.6s 降到亚秒级。
_VERIFY_CACHE: dict[str, tuple[float, dict]] = {}
# 2026-09-06 全局调用链治理（缓存错配修复）：
# 原值 3s 与前端实际轮询间隔严重错配 —— App.tsx 的 ["accounts"] 查询
# refetchInterval=30000（30s）、main.tsx staleTime=20000（20s），
# 3s TTL 意味着 30s 轮询【永远命中不了缓存】，每次都真实跑
# verify_account（含 get_my_uid 外网探活），N 账号串行下来就是
# 每 30s 一轮 N 次外网请求 + 列表卡顿。
# 改为 25s：略小于 30s 轮询间隔，既保证状态足够新鲜（25s 内凭证
# 失效能被感知），又让绝大多数轮询命中缓存（省掉重复外网探活）。
_VERIFY_TTL = 25.0  # 秒，略小于前端 30s 轮询间隔（原 3s 导致缓存永不命中）
_VERIFY_LOCK = threading.Lock()


def _get_adm():
    """懒加载全局引擎实例（app.state.adm）。

    避免在模块顶层 import main 造成的循环依赖：accounts 路由被 main
    导入，但本函数在运行时（请求处理阶段）才访问，此时 main 已就绪。
    """
    try:
        from main import app
        return getattr(app.state, "adm", None)
    except Exception:
        return None


def _last_run_empty() -> dict:
    return {
        "room": "—",
        "roomUrl": "",
        "time": "—",
        "duration": "—",
        "totalRuns": 0,
        "comments": 0,
        "dmSent": 0,
        "dmSuccess": 0,
        "dmFail": 0,
        "dmAfterLive": 0,
    }


def _last_run_runtime() -> dict:
    """兜底：无历史任务时从全局引擎运行时聚合（单账号引擎）。"""
    adm = _get_adm()
    if not adm:
        return _last_run_empty()
    try:
        sent = getattr(adm, "sent_count", 0) or 0
        live_id = getattr(adm, "live_id", None)
        dispatch = getattr(adm, "dispatch", None)
        records = dispatch.records_list() if dispatch else []
        comments = len(records) if records else 0
        from models.enums import RecordStatus
        success = sum(1 for r in records if getattr(r, "status", None) == RecordStatus.SENT)
        fail = sum(1 for r in records if getattr(r, "status", None) == RecordStatus.FAIL)
        status_msg = getattr(adm, "status_msg", "") or ""
        running = status_msg not in ("未启动", "已停止", "运行异常: ")
        return {
            "room": live_id or "—",
            "roomUrl": f"https://live.douyin.com/{live_id}" if live_id else "",
            "time": "进行中" if running else "—",
            "duration": "进行中" if running else "—",
            "totalRuns": 1 if live_id else 0,
            "comments": comments,
            "dmSent": success + fail,
            "dmSuccess": success,
            "dmFail": fail,
            "dmAfterLive": 0,
        }
    except Exception:
        return _last_run_empty()


def _duration_str(start: str, end: str) -> str:
    """'%Y-%m-%d %H:%M:%S' 两个时间戳 -> 'H:MM:SS'；解析失败返回 '—'。"""
    fmt = "%Y-%m-%d %H:%M:%S"
    try:
        from datetime import datetime
        t1 = datetime.strptime((start or "").strip(), fmt)
        t2 = datetime.strptime((end or start or "").strip(), fmt)
        s = max(0, int((t2 - t1).total_seconds()))
        h, rem = divmod(s, 3600)
        m, sec = divmod(rem, 60)
        return f"{h}:{m:02d}:{sec:02d}"
    except Exception:
        return "—"


def _build_last_run(name: str) -> dict:
    """该账号的「上次运行记录」：优先读历史任务（任务中心查阅模式数据源），
    无历史时退回全局引擎运行时聚合。

    数据源 tasks_history.json 每条含 config 快照（直播间）与 records 快照（发送明细），
    故账号管理页看到的最近一次运行结果 = 任务中心历史任务/查阅模式里的同一份数据。
    """
    try:
        from tasks_history import list_history
        hist = list_history() or []
        mine = [it for it in hist if (it.get("acct") or "") == name]
        if not mine:
            return _last_run_runtime()
        it = mine[0]
        records = it.get("records") or []
        cfg = it.get("config") or {}
        live_id = cfg.get("live_id") or it.get("live_id") or ""
        room = cfg.get("live_url") or live_id or "—"
        start = it.get("start_ts") or "—"
        end = it.get("end_ts") or start
        running = it.get("status") == "running"
        duration = "进行中" if running else _duration_str(start, end)
        sent = success = fail = 0
        for r in records:
            st = (r or {}).get("status")
            if st in ("sent", "fail"):
                sent += 1
            if st == "sent":
                success += 1
            elif st == "fail":
                fail += 1
        return {
            "room": room,
            "roomUrl": f"https://live.douyin.com/{live_id}" if live_id else "",
            "time": start,
            "duration": duration,
            "totalRuns": len(mine),
            "comments": len(records),
            "dmSent": sent,
            "dmSuccess": success,
            "dmFail": fail,
            "dmAfterLive": 0,
        }
    except Exception:
        return _last_run_runtime()


def _do_scan(name: str):
    """后台线程：弹指纹浏览器让用户扫码，完成后写回对应账号 .env。"""
    st = _scan_state.setdefault(name, {})
    st["running"] = True
    st["done"] = False
    st["loggedIn"] = False
    st["error"] = ""
    try:
        # 重扫前先停该账号凭证守护，释放与「查看模式」共有的 profile 锁，
        # 否则 force=True 清空 vb_profile_default 时会因 Chromium 占用而失败
        # （WinError 32），导致后续浏览器崩溃落到 about:blank。
        _quit_browser_daemon(name)
        from auth_helper import enrich_auth
        env_path = acct_core.env_path_of(name)
        auth, _ = enrich_auth(None, force=True, env_path=env_path)
        st["loggedIn"] = bool(getattr(auth, "cookie", None))
        st["done"] = True
    except Exception as e:
        logger.error(f"[scan] 账号 {name} 扫码异常: {e}")
        st["error"] = str(e)
        st["done"] = True
    finally:
        st["running"] = False


def _do_open_browser(name: str):
    """后台线程：单纯拉起该账号绑定的指纹浏览器并打开抖音主页（不扫码、不抓凭证）。"""
    st = _scan_state.setdefault(name, {})
    st["running"] = True
    st["done"] = False
    st["error"] = ""
    try:
        import asyncio
        from auto_dm import config as _cfg
        from auto_dm.vbrowser import open_douyin_home, init_vb_config
        init_vb_config(_cfg)
        env_path = acct_core.env_path_of(name)
        profile = acct_core.profile_dir_of(env_path)
        os.makedirs(profile, exist_ok=True)
        logger.info(f"[open-browser] 账号 {name} 打开指纹浏览器(profile={profile})")
        # 前台常驻：阻塞直到用户关闭浏览器窗口
        asyncio.run(open_douyin_home(profile, headless=False,
                                     url="https://www.douyin.com/", account=name))
        st["done"] = True
    except Exception as e:
        logger.error(f"[open-browser] 账号 {name} 打开指纹浏览器异常: {e}")
        st["error"] = str(e)
        st["done"] = True
    finally:
        st["running"] = False


def _wait_scan_error(name: str, timeout: float = 4.0) -> str:
    """等待扫码线程：若很快以失败结束（如指纹内核缺失），返回错误文案，否则返回空串。

    浏览器弹窗是异步的，enrich_auth 在真正弹窗前若因环境错误（缺 vb_chromium 内核、
    参数错误等）会立即抛异常，此时前端应拿到 ok=False 而非永远 ok=True 却看不到窗口。
    """
    import time as _t
    end = _t.time() + timeout
    while _t.time() < end:
        st = _scan_state.get(name)
        if st and st.get("done") and st.get("error"):
            return st["error"]
        if st and not st.get("running") and st.get("done"):
            break
        _t.sleep(0.2)
    return ""


def _cached_verify(name: str, timeout: int = 3) -> dict:
    """带 TTL 缓存的 verify_account 封装。

    getAccounts 列表轮询（默认 3s）若每次都跑真实 verify（含 get_my_uid 网络探活），
    会产生 1~3s 的可感知延迟。缓存命中（TTL 内）直接返回上次结果，把延迟降到亚秒级。
    只缓存 dm_loopback=False（轮询用）的结果；按钮/自检触发的 dm_loopback=True 不走缓存。
    """
    now = time.time()
    with _VERIFY_LOCK:
        cached = _VERIFY_CACHE.get(name)
        if cached is not None and (now - cached[0]) < _VERIFY_TTL:
            return cached[1]
    # 缓存未命中：真实计算（可能较慢，但并发池内只发生一次）
    try:
        result = acct_core.verify_account(name, timeout=timeout, dm_loopback=False)
    except Exception:
        result = {
            "ok": False,
            "uid": None,
            "wp": {"level": "unknown", "label": "状态获取失败", "detail": ""},
            "dm": {"level": "unknown", "label": "待校验", "detail": ""},
            "auto_fix_triggered": False,
        }
    with _VERIFY_LOCK:
        _VERIFY_CACHE[name] = (now, result)
    return result


def _to_raw_account(name: str) -> dict:
    """把后端真实账号状态映射为前端 RawAccount 兼容结构。

    前端 accounts.tsx 的 RawAccount 期望：
    name/uid/level/label/loggedIn/browserDaemonAlive/wpEngine/dmEngine/
    isCurrent/isMonitor/isSender。

    性能关键：仅调用【一次】verify_account 作为唯一真相源（account_status 已委托它），
    避免原本 account_status + verify_account 两次串行重型探活叠加导致列表刷新卡顿。
    wpEngine / dmEngine 与 level/label/alive/uid 全部取自这一次 verify_account 的同一结果，
    既保证账号管理页与启动自检弹窗结论一致，又把耗时砍半。
    """
    bport = acct_core.browser_daemon_port(name)
    rport = acct_core.recv_daemon_port(name)
    try:
        # 单次 verify_account 含网络探活，较慢；优先用 TTL 缓存避免列表刷新卡顿
        v = _cached_verify(name, timeout=3)
        wp = v.get("wp", {})
        dm = v.get("dm", {})
        wp_level = wp.get("level")
        level = "ok" if wp_level == "ok" else (
            "nosign" if wp_level == "nosign" else (
                "expired" if wp_level in ("fail", "error") else "missing"
            )
        )
        logged_in = wp_level == "ok"
        uid = v.get("uid")
        label = wp.get("label", "")
    except Exception:
        level, label, logged_in, uid = "unknown", "状态获取失败", False, None
        wp = {"level": "unknown", "label": "状态获取失败", "detail": ""}
        dm = {"level": "unknown", "label": "待校验", "detail": ""}
    is_current = (name == acct_core.current_name())
    monitor = (name == acct_core.monitor_name())
    sender = (name == acct_core.sender_name())
    return {
        "name": name,
        "uid": uid,
        "level": level,
        "label": label,
        "loggedIn": bool(logged_in),
        "browserDaemonPort": bport,
        "recvDaemonPort": rport,
        "browserDaemonAlive": acct_core._port_open(bport, timeout=0.3),
        "recvDaemonAlive": acct_core._port_open(rport, timeout=0.3),
        "wpEngine": {
            "level": wp.get("level", "unknown"),
            "label": wp.get("label", "未知"),
            "detail": wp.get("detail", ""),
        },
        "dmEngine": {
            "level": dm.get("level", "unknown"),
            "label": dm.get("label", "待校验"),
            "detail": dm.get("detail", ""),
        },
        "isCurrent": is_current,
        "isMonitor": monitor,
        "isSender": sender,
        "lastRun": _build_last_run(name),
    }


@router.get("")
async def list_accounts(request: Request):
    """轻量列表（端口探活 + 状态摘要，对齐前端 getAccounts 期望结构）

    性能关键：各账号的 verify_account 是独立的 IO 操作，用线程池并发执行，
    整体延迟从「串行 N 个账号 × 两次探活」降为「并发后最慢一个账号的一次探活」，
    删除/新增账号后的列表刷新从 3s+ 降到亚秒级。

    注意：auto_dm.accounts.list_accounts() 返回 [(name, env_path), ...] 元组，
    这里需拆包取纯 name 字符串，否则 _to_raw_account 会收到整个元组，
    导致 name 字段被序列化为数组（前端 Avatar.charAt 崩溃）。
    """
    raw = acct_core.list_accounts()
    names = [n[0] if isinstance(n, (tuple, list)) else n for n in raw]
    if not names:
        return {"ok": True, "accounts": []}
    # 并发校验，避免串行卡顿（删除账号后刷新尤其明显）
    # 2026-09-06 全局调用链治理：ThreadPoolExecutor 的 pool.map 本身是
    # 【同步阻塞】调用——虽然池内线程让出了 GIL，但 async 事件循环仍被
    # 卡住直到最慢的账号返回（timeout=3s）。期间 /api/overview、
    # /api/live/stream 等 3s 轮询全部排队。整段丢 run_in_executor，
    # 让事件循环真正空出来。
    def _run_all() -> list:
        with ThreadPoolExecutor(max_workers=min(len(names), 8)) as pool:
            return list(pool.map(_to_raw_account, names))

    loop = asyncio.get_running_loop()
    accounts = await loop.run_in_executor(None, _run_all)
    return {"ok": True, "accounts": accounts}


@router.get("/self-check")
async def self_check(request: Request):
    """启动自检：对所有账号真跑双引擎校验（wp=凭证守护四件套 + dm=私信列表拉取）。

    前端打开时调用，用于一次性判断每个账号的 wp 引擎 / 私信引擎是否可用，
    若不可用返回明细供前端弹「自检说明」弹窗。相比 /{name}/check（按需单账号），
    本接口一次性覆盖全部账号，且对每个账号都跑 dm_loopback（私信列表拉取），
    弥补 list_accounts 轮询接口 dmEngine 恒为「待校验」的盲区。
    """
    raw = acct_core.list_accounts()
    names = [n[0] if isinstance(n, (tuple, list)) else n for n in raw]
    # 2026-09-06 全局调用链治理（串行阻塞 + 事件循环阻塞）：
    # 原实现在 async 路由里【串行】跑 N 个账号的 verify_account（每个
    # 含外网探活 1~8s），N 账号就是 N×8s 的事件循环阻塞 —— 前端整体卡死。
    # 改为：整段丢线程池 + 池内并发（与 list_accounts 一致）。
    def _verify_one(name: str) -> dict:
        entry = {"name": name, "wp": None, "dm": None, "ok": False}
        try:
            verify = acct_core.verify_account(name, timeout=8, dm_loopback=True)
            entry["wp"] = verify.get("wp")
            entry["dm"] = verify.get("dm")
            entry["uid"] = verify.get("uid")
            entry["ok"] = bool(verify.get("ok"))
            entry["autoFixTriggered"] = bool(verify.get("auto_fix_triggered"))
        except Exception as e:  # 单账号校验异常不阻断其他账号
            logger.error(f"[self-check] 账号 {name} 校验异常: {e}")
            entry["wp"] = {"level": "error", "label": "校验异常"}
            entry["dm"] = {"level": "error", "label": "校验异常"}
        return entry

    def _run_all() -> list:
        if not names:
            return []
        with ThreadPoolExecutor(max_workers=min(len(names), 8)) as pool:
            return list(pool.map(_verify_one, names))

    loop = asyncio.get_running_loop()
    items = await loop.run_in_executor(None, _run_all)
    # 整体是否全部可用（无 fail/error/unknown，且至少一个账号）
    any_fail = any(
        it["wp"] and it["wp"].get("level") in ("fail", "warn", "error", "unknown")
        or it["dm"] and it["dm"].get("level") in ("fail", "error", "unknown")
        for it in items
    )
    return {"ok": True, "allOk": (len(items) > 0 and not any_fail), "items": items}


@router.post("/{name}/check")
async def check_account(name: str) -> dict:
    """引擎校验（重量级，按需触发）。

    复用 auto_dm.accounts.verify_account（双引擎校验）：
      - wp 引擎：凭证守护是否在跑 + 守护保活的凭证能否还原出完整签名四件套；
      - 私信引擎：拉取全部私信会话列表，验证 imapi 私有网关私信凭证有效、列表可读取。
    返回前端 runCheck 期望的结构 {ok, verify:{wp,dm,uid}}。
    """
    logger.info(f"[check] 账号 {name} 发起双引擎校验（含私信列表拉取）")
    try:
        # 2026-09-06 全局调用链治理（事件循环阻塞）：
        # verify_account 是同步重型函数（含 get_my_uid 外网探活 + 私信列表
        # 拉取，1~8s）。在 async 路由里直接同步调用会【阻塞 uvicorn 事件
        # 循环】——期间所有其他 API（含 3s/5s 高频轮询）全部排队等待，
        # 表现为整个前端卡死。改 run_in_executor 丢线程池执行。
        loop = asyncio.get_running_loop()
        verify = await loop.run_in_executor(
            None, lambda: acct_core.verify_account(name, timeout=8, dm_loopback=True))
        logger.success(
            f"[check] 账号 {name} 校验完成 · wp:{verify['wp']['label']} · dm:{verify['dm']['label']}"
        )
        return {"ok": True, "verify": verify}
    except Exception as e:
        logger.error(f"[check] 账号 {name} 校验异常: {e}")
        return {"ok": False, "error": str(e)}


def _quit_browser_daemon(name: str) -> bool:
    """释放该账号 profile 锁（避免与弹窗的 Chromium 抢锁 SingletonLock 崩溃）。

    返回 True 表示守护原本在跑且已发送停止请求；False 表示守护未运行。

    **2026-09-03 加固（孤儿锁 bug 实机定位）**：
    只探测端口会漏掉一种致命情况 —— browser_daemon 进程已死（端口已释放），
    但它的 chromium 子进程族仍持有 profile（用户数据目录被前一次 app 实例
    遗留，或 daemon 异常退出时子进程未被回收）。此时 `_port_open` 返回 False
    直接跳过 quit → 弹查看浏览器撞上 SingletonLock → exitCode=21
    「Target page, context or browser has been closed」。

    加固逻辑：
      1. 端口活着 → 照常发 /quit（旧逻辑）
      2. 端口已死 → 检查是否仍有 chrome 进程的 --user-data-dir 指向该账号
         profile：有 → 按进程树 kill（释放孤儿锁）；无 → 正常返回
    """
    bport = acct_core.browser_daemon_port(name)
    env_path = acct_core.env_path_of(name)
    try:
        profile = str(acct_core.profile_dir_of(env_path))
    except Exception:
        profile = ""
    if acct_core._port_open(bport, timeout=0.3):
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{bport}/quit", method="POST"
            )
            urllib.request.urlopen(req, timeout=3)
        except Exception as e:
            logger.warning(f"[open-browser] 停止守护 {name} 失败（可能已退出）: {e}")
        # 等待 profile 锁释放（Chromium 退出需要一点时间）
        time.sleep(1.5)
        return True

    # 端口已死：检查孤儿 chrome 是否仍占着 profile
    if profile:
        killed = _kill_profile_holders(profile)
        if killed:
            logger.warning(
                f"[open-browser] 账号 {name} 发现并清理 {killed} 个持有 "
                f"profile 的孤儿浏览器进程（端口 {bport} 已死但锁未释放）"
            )
            return True
    return False


def _kill_profile_holders(profile: str) -> int:
    """按 --user-data-dir 匹配持有该 profile 的 chrome 进程并按树 kill。

    只杀【指向该固定 profile】的进程，绝不误伤其他账号 / 其他浏览实例。
    返回清理的进程数。
    """
    if not profile:
        return 0
    import subprocess
    import platform

    if platform.system() != "Windows":
        return 0
    norm = profile.replace("/", "\\").lower()
    killed = 0
    try:
        # PowerShell 枚举所有 chrome 进程及其命令行，按 user-data-dir 精确匹配
        ps = (
            "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
            "Where-Object { $_.CommandLine -like '*user-data-dir*' } | "
            "ForEach-Object { $_.ProcessId.ToString() + '|' + $_.CommandLine }"
        )
        out = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-Command", ps],
            capture_output=True, timeout=15,
        )
        # Windows PowerShell 输出是 GBK 编码（中文系统默认 OEM 代码页），
        # 不能用 text=True(UTF-8) 解码，否则中文 profile 路径处崩 UnicodeDecodeError
        raw = (out.stdout or b"")
        try:
            text = raw.decode("gbk", errors="replace")
        except Exception:
            text = raw.decode("utf-8", errors="replace")
        for line in text.splitlines():
            line = line.strip()
            if "|" not in line:
                continue
            pid_str, cmd = line.split("|", 1)
            cmd_norm = cmd.replace("/", "\\").lower()
            # 只杀命令行里 user-data-dir 精确包含该 profile 的 chrome
            if f"--user-data-dir=\"{norm}\"" in cmd_norm or \
               f"--user-data-dir={norm}" in cmd_norm:
                try:
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", pid_str],
                        capture_output=True, timeout=10,
                    )
                    killed += 1
                except Exception:
                    continue
    except Exception as e:
        logger.warning(f"[open-browser] 清理孤儿 profile 持有进程失败: {e}")
    return killed


@router.post("/{name}/open-browser")
async def open_fingerprint_browser(name: str) -> ScanLoginResponse:
    """单纯打开该账号绑定的指纹浏览器并打开默认抖音主页（查看/手动操作）。

    与“重新获取凭证/扫码登录”是两条不同的路径：本接口【不扫码、不抓凭证、
    不写回 .env】，只拉起浏览器让用户查看或手动操作抖音页面。
    为避免与常驻的凭证守护争抢 Chromium profile 锁，先临时停止该账号凭证守护，
    浏览器关闭后请在前端重新启动守护以恢复凭证保活。
    """
    env_path = acct_core.env_path_of(name)
    if not os.path.exists(os.path.dirname(env_path)):
        return ScanLoginResponse(ok=False, msg=f"账号 {name} 不存在")
    prev = _scan_state.get(name)
    if prev and prev.get("running"):
        return ScanLoginResponse(ok=True, msg=f"账号 {name} 指纹浏览器已打开，请完成操作")
    # 先停守护释放 profile 锁
    daemon_was_alive = _quit_browser_daemon(name)
    t = threading.Thread(target=_do_open_browser, args=(name,), daemon=True)
    t.start()
    # 捕获立即发生的失败（如指纹内核缺失），否则前端永远 ok=True 却看不到浏览器
    err = _wait_scan_error(name)
    if err:
        return ScanLoginResponse(ok=False, msg=f"打开指纹浏览器失败: {err}")
    hint = "（已先停止凭证守护释放浏览器，操作完后请在卡片重新启动守护）" if daemon_was_alive \
        else "（该账号守护未运行，直接打开）"
    return ScanLoginResponse(ok=True, msg=f"已打开指纹浏览器（查看模式）· {name}{hint}")


@router.post("/{name}/scan")
async def scan_login(name: str) -> ScanLoginResponse:
    """扫码登录（启动后台扫码，立即返回）。

    后台线程弹指纹浏览器让用户扫码，前端轮询 /scan-status 获取进度。
    """
    env_path = acct_core.env_path_of(name)
    if not os.path.exists(os.path.dirname(env_path)):
        return ScanLoginResponse(ok=False, msg=f"账号 {name} 不存在")
    # 同一账号已有扫码在跑则直接返回
    prev = _scan_state.get(name)
    if prev and prev.get("running"):
        return ScanLoginResponse(ok=True, msg=f"账号 {name} 已在扫码中，请完成扫码")
    t = threading.Thread(target=_do_scan, args=(name,), daemon=True)
    t.start()
    err = _wait_scan_error(name)
    if err:
        return ScanLoginResponse(ok=False, msg=f"扫码登录失败: {err}")
    return ScanLoginResponse(ok=True, msg=f"已弹出指纹浏览器，请扫码登录账号 {name}")


@router.get("/{name}/scan-status")
async def scan_status(name: str):
    """扫码状态查询（替代间接推断）"""
    st = _scan_state.get(name, {})
    return {
        "name": name,
        "running": st.get("running", False),
        "done": st.get("done", False),
        "loggedIn": st.get("loggedIn", False),
        "error": st.get("error", ""),
    }


@router.post("/{name}/role")
async def set_role(name: str, body: SetRoleRequest):
    # body.role 是 AccountRole(str) 枚举，str() 取 "watch"/"send"/"both"
    return {"ok": True, "name": name, "role": str(body.role)}


@router.post("")
async def add_account(body: AddAccountRequest):
    try:
        acct_core.add_account(body.name)
        return {"ok": True, "name": body.name}
    except ValueError as e:
        logger.warning(f"[accounts] add_account 失败: {e}")
        return {"ok": False, "error": str(e), "name": body.name}


@router.delete("/{name}")
async def remove_account(name: str):
    try:
        acct_core.remove_account(name)
        return {"ok": True, "name": name}
    except ValueError as e:
        logger.warning(f"[accounts] remove_account 失败: {e}")
        return {"ok": False, "error": str(e), "name": name}


def _quit_daemon_http(port: int) -> bool:
    """向守护 HTTP /quit 端口发停止请求，守护自身会 os._exit(0)。

    返回 True 表示请求成功送达（守护即将退出），False 表示端口不可达（守护未运行）。
    这是停止守护的【首选】方式，不依赖 Rust SidecarManager 的进程 label 精确匹配。
    """
    if not port:
        return False
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/quit", data=b"", method="POST"
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            resp.read()
        return True
    except Exception:
        return False


@router.post("/{name}/stop-browser")
async def stop_browser_daemon(name: str):
    """停止该账号的凭证守护（经由守护自身 HTTP /quit 端口，不依赖 Rust label 匹配）。"""
    bport = acct_core.browser_daemon_port(name)
    if not acct_core._port_open(bport, timeout=0.3):
        return {"ok": True, "wasRunning": False, "msg": f"凭证守护 {name} 未运行"}
    ok = _quit_daemon_http(bport)
    return {"ok": True, "wasRunning": True, "stopped": ok,
            "msg": f"已向凭证守护 {name} 发送停止请求"}


@router.post("/{name}/stop-recv")
async def stop_recv_daemon(name: str):
    """停止该账号的私信守护（经由守护自身 HTTP /quit 端口，不依赖 Rust label 匹配）。"""
    rport = acct_core.recv_daemon_port(name)
    if not acct_core._port_open(rport, timeout=0.3):
        return {"ok": True, "wasRunning": False, "msg": f"私信守护 {name} 未运行"}
    ok = _quit_daemon_http(rport)
    return {"ok": True, "wasRunning": True, "stopped": ok,
            "msg": f"已向私信守护 {name} 发送停止请求"}


_RECAP_LANDING = "https://www.douyin.com/chat?isPopup=1"


@router.post("/{name}/auto-recapture")
async def auto_recapture(name: str) -> ScanLoginResponse:
    """私信凭证失效自动重新捕获（手动触发入口）。

    后端发送链路检测到凭证失效（三件套缺失 / INVALID_REQUEST）时会自动调用
    auto_dm.accounts.auto_recapture 在后台拉起指纹浏览器打开 chat?isPopup=1 重新授权，
    本路由供前端“立即处理验证”按钮或手动触发使用，行为与自动触发一致。
    """
    env_path = acct_core.env_path_of(name)
    if not os.path.exists(os.path.dirname(env_path)):
        return ScanLoginResponse(ok=False, msg=f"账号 {name} 不存在")
    # 直接走后台重捕获（复用 send_target 自动触发同一条路径，带 5 分钟节流）
    try:
        acct_core.auto_recapture(name, landing_url=_RECAP_LANDING)
        return ScanLoginResponse(
            ok=True,
            msg=f"已拉起指纹浏览器重新捕获私信凭证（{_RECAP_LANDING}）· {name}",
        )
    except Exception as e:
        return ScanLoginResponse(ok=False, msg=f"自动重新捕获失败: {e}")


@router.get("/{name}/proxy-status")
async def proxy_status(name: str):
    """账号代理状态查询（借鉴 OpenBrowser egress check 的只读轻量版）。

    返回 {configured, masked, error}：
      - configured: 该账号 .env 是否配置了 DY_PROXY；
      - masked: 脱敏后的代理地址（日志/前端展示用，绝不回传明文凭据）；
      - error: DY_PROXY 配置格式错误信息（无则空串）。
    纯读 .env 单行，零网络请求、零浏览器操作，可被前端随列表轮询。
    """
    env_path = acct_core.env_path_of(name)
    from auto_dm.vbrowser import parse_proxy_env, _mask_proxy
    proxy_url, err = parse_proxy_env(env_path)
    if err:
        return {"configured": False, "masked": "", "error": err}
    if not proxy_url:
        return {"configured": False, "masked": "", "error": ""}
    return {"configured": True, "masked": _mask_proxy(proxy_url), "error": ""}
