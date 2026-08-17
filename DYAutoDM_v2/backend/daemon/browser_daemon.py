# coding=utf-8
"""凭证守护进程（重构版）

迁移自 DY_Spider_base/auto_dm/browser_daemon.py。
关键变化：
- http.server → FastAPI（路由更清晰，自动 JSON 序列化）
- 启动时立即 refresh 阻塞数秒 → 改为后台任务（不阻塞 /status 响应）
- Tauri sidecar 模式：由 Rust SidecarManager 管理生命周期，不再依赖 alive_flag 文件

运行方式（Tauri sidecar）：
    dyautodm-browser-daemon --account X --port P

业务逻辑（CredentialKeeper）完整保留：
  - 周期性 get_my_uid 探活
  - cookie/签名失效时自动调 DYLoginApi.get_login_auth 刷新
  - 暴露 /status /refresh /quit HTTP 接口
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import threading
import time
from typing import Any

from fastapi import FastAPI
from loguru import logger

# 无控制台模式下 sys.stdout/stderr 可能为 None
if sys.stdout is not None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if sys.stderr is not None:
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from vbrowser import app_root

_ROOT = app_root()
_DAEMON_DIR = os.path.join(_ROOT, "auto_dm")

# 日志同步输出到 stderr（enqueue=True 避免 Windows GBK 控制台中文编码失败中断主线程），
# 这样 Tauri Rust 侧能捕获到守护进程的日志，也会经由 backend 的日志桥接展示到前端「运行日志」。
logger.remove()
logger.add(
    sys.stderr,
    level="INFO",
    colorize=False,
    enqueue=True,
    format="{time:HH:mm:ss} | {level: <8} | {message}",
)

app = FastAPI(title="browser-daemon")

# 全局状态（main 启动时填充）
_state: dict[str, Any] = {
    "account": "",
    "port": 0,
    "started_at": time.time(),
    "keeper": None,  # CredentialKeeper 实例
    "keepalive_thread": None,
    "keepalive_stop": None,
}


# ----------------------------------------------------------------------------
# 凭证保活核心（迁移自旧版 CredentialKeeper）
# ----------------------------------------------------------------------------
class CredentialKeeper:
    """周期性维护某账号的有效凭证（cookie + web_protect/keys 签名）。

    不直接开常驻浏览器——而是由 DYLoginApi.get_login_auth 在需要时开临时浏览器抓取。
    """

    def __init__(self, account: str = "主") -> None:
        self.account = account
        self._lock = threading.Lock()
        self._last_uid: Any = None
        self._last_refresh: float = 0.0

    def refresh(self, force: bool = False) -> bool:
        """刷新凭证。force=True 时忽略现有、重新扫码。返回是否成功。

        单 profile 铁律守卫：凭证不全（cookie/四件套/web_protect 任一缺失，或
        cookie 含风控占位）时【拒绝】弹浏览器抓取，直接返回 False 并明确提示，
        避免守护空跑 + 误捕获污染 .env（如 s_v_web_id=verify_* 占位被写回）。
        用户必须先完成一次成功扫码，凭证齐全后再启动守护。
        """
        from auto_dm import accounts as acc
        from dy_apis.login_api import DYLoginApi
        env_path = acc.env_path_of(self.account)
        # 铁律：凭证不全不启动浏览器、不抓取、不写盘
        complete, reason = acc.credentials_complete(env_path)
        if not complete:
            logger.error(f"[保活] 凭证未就绪，拒绝启动守护抓取: {reason}（请先完成扫码）")
            return False
        try:
            api = DYLoginApi()
            auth = asyncio.run(
                api.get_login_auth(headless=False, env_path=env_path, force=force)
            )
            if auth and getattr(auth, "cookie", None):
                self._last_uid = None
                try:
                    from dy_apis.douyin_api import DouyinAPI
                    self._last_uid = DouyinAPI.get_my_uid(auth)
                except Exception:
                    pass
                self._last_refresh = time.time()
                logger.info(
                    f"[保活] 凭证刷新成功(account={self.account}, uid={self._last_uid})"
                )
                return True
            logger.error("[保活] 凭证刷新失败：未拿到有效 auth")
            return False
        except Exception as e:
            logger.error(f"[保活] 凭证刷新异常: {e}")
            return False

    def _load_uid_from_env(self) -> Any:
        from auto_dm import accounts as acc
        from dy_apis.login_api import DYLoginApi
        from dy_apis.douyin_api import DouyinAPI
        env_path = acc.env_path_of(self.account)
        try:
            auth = DYLoginApi._load_auth_from_env(env_path)
            if auth and auth.cookie:
                return DouyinAPI.get_my_uid(auth)
        except Exception:
            pass
        return None

    def status(self) -> dict:
        from auto_dm import accounts as acc
        st = acc.account_status(self.account, force=False)
        uid = self._last_uid or self._load_uid_from_env()
        return {
            "alive": True,
            "logged_in": bool(st.get("has_cookie")),
            "sign_ready": bool(st.get("has_ticket") and st.get("has_private_key")),
            "uid": uid,
            "account": self.account,
            "last_refresh": int(self._last_refresh),
        }

    def run_keepalive(self, stop_ev: threading.Event, interval: int = 300) -> None:
        """每 interval 秒探活一次；uid 探活失败则自动 refresh。"""
        logger.info(f"[保活] 心跳启动，间隔 {interval}s")
        while not stop_ev.is_set():
            if stop_ev.wait(interval):
                break
            try:
                uid = self._load_uid_from_env()
                if uid:
                    self._last_uid = uid
                    logger.debug(f"[保活] 登录态正常(uid={uid})")
                else:
                    logger.warning("[保活] 登录态失效，自动刷新凭证…")
                    self.refresh(force=False)
            except Exception as e:
                logger.warning(f"[保活] 探活异常: {e}")
        logger.info("[保活] 心跳退出")


# ----------------------------------------------------------------------------
# FastAPI 路由
# ----------------------------------------------------------------------------
@app.on_event("startup")
async def _startup() -> None:
    logger.info(
        f"browser_daemon 启动 account={_state['account']} port={_state['port']}"
    )
    # 启动保活心跳（后台线程，不阻塞 /status）
    keeper = CredentialKeeper(account=_state["account"])
    _state["keeper"] = keeper
    stop_ev = threading.Event()
    _state["keepalive_stop"] = stop_ev
    t = threading.Thread(
        target=keeper.run_keepalive, args=(stop_ev,), daemon=True
    )
    _state["keepalive_thread"] = t
    t.start()
    # 旧版启动时立即 refresh(force=False) 会阻塞数秒甚至弹浏览器，
    # 新版改为后台异步执行（不阻塞启动，/status 立即可用）
    threading.Thread(target=lambda: keeper.refresh(force=False), daemon=True).start()


@app.on_event("shutdown")
async def _shutdown() -> None:
    if _state["keepalive_stop"]:
        _state["keepalive_stop"].set()


@app.get("/status")
async def status() -> dict:
    keeper: CredentialKeeper | None = _state["keeper"]
    if keeper is None:
        return {
            "alive": True,
            "logged_in": False,
            "sign_ready": False,
            "uid": None,
            "account": _state["account"],
            "last_refresh": 0,
        }
    # status() 是同步的，用 to_thread 包装避免阻塞事件循环
    return await asyncio.to_thread(keeper.status)


@app.post("/refresh")
async def refresh(force: bool = False) -> dict:
    """强制重新扫码刷新凭证。"""
    keeper: CredentialKeeper | None = _state["keeper"]
    if keeper is None:
        return {"ok": False, "error": "keeper 未就绪"}
    ok = await asyncio.to_thread(keeper.refresh, force=True)
    return {"ok": ok}


@app.post("/quit")
async def quit_() -> dict:
    """守护进程退出（Tauri SidecarManager 会感知进程结束）。"""
    if _state["keepalive_stop"]:
        _state["keepalive_stop"].set()
    # 延迟退出让响应先返回
    threading.Timer(0.5, lambda: os._exit(0)).start()
    return {"ok": True}


# ----------------------------------------------------------------------------
# 主入口
# ----------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="浏览器常驻守护进程（凭证保活）")
    parser.add_argument("--account", required=True, help="账号名")
    parser.add_argument("--port", type=int, required=True, help="HTTP 控制端口")
    parser.add_argument("--interval", type=int, default=300, help="保活心跳间隔（秒）")
    args = parser.parse_args()

    _state["account"] = args.account
    _state["port"] = args.port

    # 单 profile 铁律：守护启动前置校验——凭证必须全部齐全，否则拒绝启动守护。
    # 凭证不全时启动守护只会“空跑 + 误捕获污染”，且发送必 KICK；应明确提示用户先扫码。
    from auto_dm import accounts as acc
    env_path = acc.env_path_of(args.account)
    complete, reason = acc.credentials_complete(env_path)
    if not complete:
        logger.error(
            f"[守护] 凭证未就绪，拒绝启动凭证守护(account={args.account}): {reason}。"
            f"请先完成扫码（双击账号卡片指纹浏览器 / 点「重新获取凭证」），"
            f"待凭证齐全后再启动守护。"
        )
        sys.exit(2)  # 非零退出，Tauri SidecarManager 感知到启动失败

    # 日志落盘
    try:
        from datetime import datetime
        log_dir = os.path.join(_ROOT, "logs")
        os.makedirs(log_dir, exist_ok=True)
        log_file = os.path.join(
            log_dir, f"browser_daemon_{args.account}_{datetime.now().strftime('%Y%m%d')}.log"
        )
        logger.add(
            log_file,
            level="DEBUG",
            encoding="utf-8",
            format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} - {message}",
            retention="15 days",
        )
    except Exception:
        pass

    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")


if __name__ == "__main__":
    main()
