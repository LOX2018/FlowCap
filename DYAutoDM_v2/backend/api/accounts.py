"""账号管理路由

取代原版 WebBridge 的账号相关方法。
关键改进：
- getAccounts 拆为轻量 list（仅端口探活）+ 重量级 verify（按需触发）
- 新增 /scan-status 查询扫码状态（替代 fire-and-forget 的间接推断）
- /check 复用 auto_dm.accounts.verify_account 做双引擎校验（含私信列表拉取）
"""
import os
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
                                     url="https://www.douyin.com/"))
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
        # 单次 verify_account：无守护时仅做端口探测（不探活），有守护才真实探活
        v = acct_core.verify_account(name, timeout=3, dm_loopback=False)
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
    with ThreadPoolExecutor(max_workers=min(len(names), 8)) as pool:
        accounts = list(pool.map(_to_raw_account, names))
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
    items = []
    for name in names:
        entry = {"name": name, "wp": None, "dm": None, "ok": False}
        try:
            verify = acct_core.verify_account(name, timeout=8, dm_loopback=True)
            entry["wp"] = verify.get("wp")
            entry["dm"] = verify.get("dm")
            entry["uid"] = verify.get("uid")
            entry["ok"] = bool(verify.get("ok"))
        except Exception as e:  # 单账号校验异常不阻断其他账号
            logger.error(f"[self-check] 账号 {name} 校验异常: {e}")
            entry["wp"] = {"level": "error", "label": "校验异常"}
            entry["dm"] = {"level": "error", "label": "校验异常"}
        items.append(entry)
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
        verify = acct_core.verify_account(name, timeout=8, dm_loopback=True)
        logger.success(
            f"[check] 账号 {name} 校验完成 · wp:{verify['wp']['label']} · dm:{verify['dm']['label']}"
        )
        return {"ok": True, "verify": verify}
    except Exception as e:
        logger.error(f"[check] 账号 {name} 校验异常: {e}")
        return {"ok": False, "error": str(e)}


def _quit_browser_daemon(name: str) -> bool:
    """向该账号凭证守护端口发 /quit，释放 profile 锁（避免与弹窗的 Chromium 抢锁）。

    返回 True 表示守护原本在跑且已发送停止请求；False 表示守护未运行。
    """
    bport = acct_core.browser_daemon_port(name)
    if not acct_core._port_open(bport, timeout=0.3):
        return False
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{bport}/quit", method="POST"
        )
        urllib.request.urlopen(req, timeout=3)
    except Exception as e:
        logger.warning(f"[open-browser] 停止守护 {name} 失败（可能已退出）: {e if False else e}")
    # 等待 profile 锁释放（Chromium 退出需要一点时间）
    time.sleep(1.5)
    return True


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
