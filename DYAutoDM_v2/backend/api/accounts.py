"""账号管理路由

取代原版 WebBridge 的账号相关方法。
关键改进：
- getAccounts 拆为轻量 list（仅端口探活）+ 重量级 verify（按需触发）
- 新增 /scan-status 查询扫码状态（替代 fire-and-forget 的间接推断）
- /check 复用 auto_dm.accounts.verify_account 做双引擎校验（含私信回环）
"""
import os
import threading
import time
import urllib.request

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
    后端 account_status 提供 level/label/alive/uid；守护端口由 core 计算。
    """
    try:
        st = acct_core.account_status(name, force=False, timeout=3)
    except Exception:
        st = {"name": name, "level": "unknown", "label": "状态获取失败",
              "alive": False, "uid": None}
    is_current = (name == acct_core.current_name())
    monitor = (name == acct_core.monitor_name())
    sender = (name == acct_core.sender_name())
    bport = acct_core.browser_daemon_port(name)
    rport = acct_core.recv_daemon_port(name)
    return {
        "name": name,
        "uid": st.get("uid"),
        "level": st.get("level", "unknown"),
        "label": st.get("label", ""),
        "loggedIn": bool(st.get("alive")),
        "browserDaemonPort": bport,
        "recvDaemonPort": rport,
        "browserDaemonAlive": acct_core._port_open(bport, timeout=0.3),
        "recvDaemonAlive": acct_core._port_open(rport, timeout=0.3),
        "wpEngine": {
            "level": "ok" if st.get("has_web_protect") else "warn",
            "label": "凭证守护已捕获" if st.get("has_web_protect")
                     else "wp 凭证未持久化（四件套兼容）",
            "detail": st.get("label", ""),
        },
        "dmEngine": {"level": "unknown", "label": "待校验", "detail": ""},
        "isCurrent": is_current,
        "isMonitor": monitor,
        "isSender": sender,
    }


@router.get("")
async def list_accounts(request: Request):
    """轻量列表（端口探活 + 状态摘要，对齐前端 getAccounts 期望结构）

    注意：auto_dm.accounts.list_accounts() 返回 [(name, env_path), ...] 元组，
    这里需拆包取纯 name 字符串，否则 _to_raw_account 会收到整个元组，
    导致 name 字段被序列化为数组（前端 Avatar.charAt 崩溃）。
    """
    raw = acct_core.list_accounts()
    names = [n[0] if isinstance(n, (tuple, list)) else n for n in raw]
    return {"ok": True, "accounts": [_to_raw_account(n) for n in names]}


@router.post("/{name}/check")
async def check_account(name: str) -> dict:
    """引擎校验（重量级，按需触发）。

    复用 auto_dm.accounts.verify_account（双引擎校验）：
      - wp 引擎：凭证守护是否在跑 + 守护保活的凭证能否还原出完整签名四件套；
      - 私信引擎：对自身 uid 发一条回环测试文本，验证 imapi 私有网关建会话+发送链路。
    返回前端 runCheck 期望的结构 {ok, verify:{wp,dm,uid}}。
    """
    logger.info(f"[check] 账号 {name} 发起双引擎校验（含私信回环）")
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
        logger.warning(f"[open-browser] 停止守护 {name} 失败（可能已退出）: {e}")
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
