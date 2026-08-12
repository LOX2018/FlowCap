# -*- coding: utf-8 -*-
"""抖音自动私信 - 守护进程 Windows 服务宿主。

把「私信接收守护(recv_daemon 9912)」与「凭证保活守护(browser_daemon 9911)」
注册为单个 Windows 服务，开机自启、后台一直运行、非托盘。

用法（管理员权限，用真实 python）：
    python auto_dm/service.py install      # 安装服务（开机自启）
    python auto_dm/service.py start        # 启动服务
    python auto_dm/service.py stop         # 停止服务
    python auto_dm/service.py remove       # 卸载服务
    python auto_dm/service.py --interactive # 前台调试运行

说明：
- 服务运行在 Session 0（无桌面会话），recv/browser 守护均为纯 HTTP+WebSocket
  后台进程，不依赖 GUI/桌面，完全适合服务化。
- browser_daemon 在服务里只做凭证保活（复用已登录 profile，force=False），
  不弹窗扫码；扫码仍通过主体应用 GUI 独立完成。
"""
import os
import sys
import time
import threading
import subprocess

# 优先让 pyinstaller 打包也能找到模块
_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BASE not in sys.path:
    sys.path.insert(0, _BASE)


def _pythonw():
    """返回可用的无窗口 python（源码态用 pythonw，缺失回退 python）。"""
    exe = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return exe if os.path.exists(exe) else sys.executable


def _module_cmd(module, *args):
    """源码态：用 pythonw -m module 拉起守护子进程。"""
    return [_pythonw(), "-m", module, *args]


def _exe_cmd(mode, *args):
    """打包态：用本 exe --mode 拉起守护子进程（与 launcher 一致）。"""
    return [sys.executable, "--mode", mode, *args]


def _spawn_daemons():
    """按当前运行形态（源码/打包）启动 recv + browser 两个守护子进程。

    返回 (procs, cmd_ready)：procs 为子进程列表；cmd_ready 表示是否用 --mode 方式。
    """
    procs = []
    is_frozen = getattr(sys, "frozen", False)

    # 1) 私信接收守护 recv_daemon(9912) —— 多账号，全部账号
    recv_cmd = _exe_cmd("recv") if is_frozen else _module_cmd("auto_dm.recv_daemon")
    # 2) 凭证保活守护 browser_daemon(9911) —— 当前账号（current_name 优先，回退首个账号）
    acct = "主"
    try:
        from auto_dm import accounts as _acc
        acct = _acc.current_name() or _acc.monitor_name() or ""
        if not acct:
            lst = [n for n, _ in _acc.list_accounts()]
            acct = lst[0] if lst else "主"
    except Exception:
        acct = "主"
    bd_cmd = _exe_cmd("daemon", "--account", acct) if is_frozen \
        else _module_cmd("auto_dm.browser_daemon", "--account", acct)

    # 无控制台，避免黑窗
    kwargs = {
        "creationflags": subprocess.CREATE_NO_WINDOW,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    } if os.name == "nt" else {}
    for cmd in (recv_cmd, bd_cmd):
        try:
            p = subprocess.Popen(cmd, **kwargs)
            procs.append(p)
        except Exception as e:
            print(f"[service] 启动守护子进程失败 {cmd}: {e}", flush=True)
    return procs


try:
    import win32serviceutil
    import win32service
    import win32event

    class DYAutoDMDaemon(win32serviceutil.ServiceFramework):
        """把 recv_daemon + browser_daemon 封装成 Windows 服务。"""

        _svc_name_ = "DYAutoDMDaemon"
        _svc_display_name_ = "抖音自动私信守护服务"
        _svc_description_ = "开机自启、后台常驻运行私信接收(9912)与凭证保活(9911)守护进程，非托盘。"

        def __init__(self, args):
            win32serviceutil.ServiceFramework.__init__(self, args)
            self._stop_evt = win32event.CreateEvent(None, 0, 0, None)
            self._procs = []
            self._ready = threading.Event()

        def SvcStop(self):
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            win32event.SetEvent(self._stop_evt)
            # 优雅停止守护：POST /quit 给 9911/9912
            self._request_quit(9911)
            self._request_quit(9912)
            time.sleep(1)
            # 兜底强制结束子进程
            for p in self._procs:
                if p.poll() is None:
                    try:
                        p.terminate()
                    except Exception:
                        pass
            self.ReportServiceStatus(win32service.SERVICE_STOPPED)

        def SvcDoRun(self):
            self.ReportServiceStatus(win32service.SERVICE_START_PENDING)
            self._procs = _spawn_daemons()
            self.ReportServiceStatus(win32service.SERVICE_RUNNING)
            # 等待停止事件（守护在子进程内跑，本线程仅阻塞）
            win32event.WaitForSingleObject(self._stop_evt, win32event.INFINITE)

        @staticmethod
        def _request_quit(port):
            import urllib.request
            try:
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/quit",
                    data=b"{}", method="POST")
                urllib.request.urlopen(req, timeout=3)
            except Exception:
                pass

except ImportError:
    DYAutoDMDaemon = None  # 未安装 pywin32

if __name__ == "__main__":
    if DYAutoDMDaemon is not None:
        win32serviceutil.HandleCommandLine(DYAutoDMDaemon)
    else:
        # pywin32 未安装：退化为前台调试（直接拉起两个守护）
        print("[service] 未安装 pywin32，以前台调试方式运行守护…", flush=True)
        _spawn_daemons()
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
