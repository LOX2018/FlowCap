# coding=utf-8
"""常驻浏览器守护进程（独立进程，脱离 GUI 主程序运行）。

设计目标（用户诉求）：
  1) 守护进程：本文件用 `pythonw` 启动（无控制台窗口），主体 GUI 关闭后它继续存活，
     通过本地 HTTP 端口（127.0.0.1:9911）对外暴露状态与指令。
  2) 凭证保活：周期性用 get_my_uid 探活；cookie / web_protect 失效时自动重新走
     DYLoginApi.get_login_auth 刷新凭证（同账号 profile 已登录则可免扫码复用）。
     只要守护不退，凭证即有效；GUI 关闭不影响。
  3) 浏览器容器：登录抓取由 DYLoginApi.get_login_auth 内部负责（打开指纹浏览器、
     等 security-sdk 生成【有效】web_protect、写回 .env、含扫码对比报告）。
     【注意】抖音私信是左侧抽屉页、没有独立 URL，私信签名(web_protect/keys)在首页登录后
     即由 security-sdk 自动生成（原代码明确：无需手动打开私信对话框），故守护不尝试
     任何“点开私信页”的动作——这是上一代实现踩过的坑，已删除。
  4) 凭证清空策略：仅本守护进程自身退出(且非 --no-clear)时才清 .env；GUI 单独关闭不清。

对外 HTTP API（仅 127.0.0.1）：
  GET  /status            -> {"alive":true,"logged_in":bool,"sign_ready":bool,"uid":..,"account":name}
  POST /refresh?account=NAME -> 强制重新 get_login_auth(force=True) 刷新凭证（如需扫码会弹浏览器）
  POST /quit              -> 守护进程优雅退出（默认清空凭证）；返回 {"ok":bool}

启动：
  python -m auto_dm.browser_daemon            # 调试（带控制台）
  pythonw -m auto_dm.browser_daemon           # 生产（无窗口常驻，推荐）
"""

import sys
import os
import json
import time
import asyncio
import threading
import argparse
import urllib.request

# 无控制台模式（PyInstaller --noconsole / pythonw）下 sys.stdout/stderr 为 None，
# 直接 reconfigure 会 AttributeError，需先判空。
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

from loguru import logger

from auto_dm.vbrowser import app_root  # 统一应用根：源码态=项目根，打包态=exe 所在目录

# ----------------------------------------------------------------------------
# 路径与端口
# ----------------------------------------------------------------------------
_ROOT = app_root()        # DY_Spider_base（源码态）/ exe 所在目录（打包态）
_DAEMON_DIR = os.path.join(_ROOT, "auto_dm")   # 打包态=exe 旁 auto_dm（持久目录）
CONTROL_PORT = 9911
_ALIVE_FLAG = os.path.join(_DAEMON_DIR, ".daemon_alive")


def _setup_logger():
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    try:
        os.makedirs(os.path.join(_ROOT, "logs"), exist_ok=True)
        from datetime import datetime
        log_file = os.path.join(
            _ROOT, "logs", f"browser_daemon_{datetime.now().strftime('%Y%m%d')}.log")
        logger.add(log_file, level="DEBUG", encoding="utf-8",
                   format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} - {message}",
                   retention="15 days")
    except Exception:
        pass


# ----------------------------------------------------------------------------
# 守护核心：凭证保活（不直接持有浏览器，委托 DYLoginApi 抓取）
# ----------------------------------------------------------------------------
class CredentialKeeper:
    """周期性维护某账号的有效凭证（cookie + web_protect/keys 签名）。

    不直接开常驻浏览器——而是由 DYLoginApi.get_login_auth 在需要时开临时浏览器抓取
    （其内部已正确等待有效 web_protect 并写回 .env）。守护进程的职责是：
      - 定时探活(get_my_uid)，发现失效即自动刷新；
      - 暴露 HTTP 控制端口供 GUI 查询/触发刷新；
      - 作为独立进程存活，使凭证在 GUI 关闭后仍然有效。
    """

    def __init__(self, account="主"):
        self.account = account
        self._lock = threading.Lock()
        self._last_uid = None
        self._last_refresh = 0.0

    # ---- 凭证刷新 ----
    def refresh(self, force=False):
        """刷新凭证。force=True 时忽略现有、重新扫码。返回是否成功。"""
        from auto_dm import accounts as acc
        from dy_apis.login_api import DYLoginApi
        env_path = acc.env_path_of(self.account)
        try:
            api = DYLoginApi()
            # get_login_auth 是 async 协程，必须 await 才能真正执行（否则返回未 awaited 的
            # coroutine 对象，导致 auth 为 None / RuntimeWarning: never awaited）。
            # refresh 运行在线程中、该线程无已运行事件循环，用 asyncio.run 安全驱动。
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
                logger.info(f"[保活] 凭证刷新成功(account={self.account}, uid={self._last_uid})")
                return True
            logger.error("[保活] 凭证刷新失败：未拿到有效 auth")
            return False
        except Exception as e:
            logger.error(f"[保活] 凭证刷新异常: {e}")
            return False

    def _load_uid_from_env(self):
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

    def status(self):
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

    # ---- 保活心跳：探活 + 自动刷新 ----
    def run_keepalive(self, stop_ev: threading.Event, interval=300):
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
# HTTP 控制服务（仅 127.0.0.1）
# ----------------------------------------------------------------------------
class ControlServer(threading.Thread):
    def __init__(self, keeper):
        super().__init__(daemon=True)
        self.keeper = keeper
        self._stop = threading.Event()

    def run(self):
        import http.server
        import urllib.parse
        keeper = self.keeper

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, obj, code=200):
                data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path == "/status":
                    self._send(keeper.status())
                else:
                    self._send({"error": "not found"}, 404)

            def do_POST(self):
                parsed = urllib.parse.urlparse(self.path)
                length = int(self.headers.get("Content-Length", 0) or 0)
                if length:
                    self.rfile.read(length)
                try:
                    q = urllib.parse.parse_qs(parsed.query)
                except Exception:
                    q = {}
                if parsed.path == "/refresh":
                    account = q.get("account", [keeper.account])[0]
                    keeper.account = account
                    ok = keeper.refresh(force=True)
                    self._send({"ok": ok})
                elif parsed.path == "/quit":
                    self._send({"ok": True})
                    _request_quit()
                else:
                    self._send({"error": "not found"}, 404)

        server = http.server.HTTPServer(("127.0.0.1", CONTROL_PORT), Handler)
        logger.info(f"[控制] HTTP 控制端口已监听 127.0.0.1:{CONTROL_PORT}")
        while not self._stop.is_set():
            server.handle_request()
        server.server_close()

    def stop(self):
        self._stop.set()
        # 触发一次连接使 handle_request 返回
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{CONTROL_PORT}/status", timeout=1).read()
        except Exception:
            pass


# ----------------------------------------------------------------------------
# 守护主入口
# ----------------------------------------------------------------------------
_quit_event = threading.Event()
_CLEAR_ON_QUIT = {"value": True}


def _request_quit():
    _quit_event.set()


def _touch_alive_flag():
    try:
        with open(_ALIVE_FLAG, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except Exception:
        pass


def _remove_alive_flag():
    try:
        if os.path.exists(_ALIVE_FLAG):
            os.remove(_ALIVE_FLAG)
    except Exception:
        pass


def is_daemon_running():
    """外部（GUI）判断守护是否在跑。"""
    if not os.path.exists(_ALIVE_FLAG):
        return False
    try:
        with open(_ALIVE_FLAG, "r", encoding="utf-8") as f:
            pid = int(f.read().strip())
        try:
            import psutil
            return psutil.pid_exists(pid)
        except Exception:
            import socket
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                return s.connect_ex(("127.0.0.1", CONTROL_PORT)) == 0
            finally:
                s.close()
    except Exception:
        return False


def main():
    parser = argparse.ArgumentParser(description="浏览器常驻守护进程（凭证保活）")
    parser.add_argument("--account", default="主", help="默认账号名")
    parser.add_argument("--no-clear", action="store_true",
                        help="守护退出时不清空凭证（用于调试）")
    parser.add_argument("--interval", type=int, default=300,
                        help="保活心跳间隔（秒），默认 300")
    args = parser.parse_args()

    _setup_logger()
    if args.no_clear:
        _CLEAR_ON_QUIT["value"] = False

    logger.info("=== 浏览器常驻守护进程启动 ===")
    _touch_alive_flag()

    keeper = CredentialKeeper(account=args.account)
    # 启动即尝试恢复/刷新凭证（已登录 profile 可免扫码复用）
    try:
        keeper.refresh(force=False)
    except Exception as e:
        logger.error(f"[守护] 初始凭证刷新异常（守护仍运行，可后续 /refresh）: {e}")

    ctrl = ControlServer(keeper)
    ctrl.start()

    hb_stop = threading.Event()
    hb = threading.Thread(target=keeper.run_keepalive, args=(hb_stop, args.interval), daemon=True)
    hb.start()

    try:
        while not _quit_event.is_set():
            _quit_event.wait(1)
    except KeyboardInterrupt:
        pass

    logger.info("[守护] 收到退出信号，清理…")
    hb_stop.set()
    ctrl.stop()
    if _CLEAR_ON_QUIT["value"]:
        try:
            from auto_dm import accounts as acc
            acc.clear_credentials(force=True)
        except Exception as e:
            logger.warning(f"[守护] 清空凭证失败: {e}")
    _remove_alive_flag()
    logger.info("=== 浏览器常驻守护进程已退出 ===")


if __name__ == "__main__":
    main()
