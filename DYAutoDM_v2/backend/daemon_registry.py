"""本进程拉起的 sidecar 守护 pid 台账（跨模块共享）。

背景（2026-09-12 实测缺陷）：前端窗口关闭时并未连带关闭 backend——
  - Tauri 侧 on_window_event 只清理它自己 spawn 的句柄，但前端代码里
    invoke() 只调过 write_boot_log，从未调用 start_backend /
    start_recv_daemon，故 AppState.backend=None、daemons=[]，
    窗口关闭时「杀空气」；
  - 而 backend 进程内 _auto_start_daemons() 用 subprocess.Popen 拉起的
    recv_daemon / browser_daemon 是**独立进程**，Windows 在父进程退出时
    不会级联回收 → 实测残留 9 小时、占着 12687/12726 端口。

因此清扫必须由 backend 自己负责：任何 spawn sidecar 的位置都登记 pid，
退出时（lifespan shutdown / atexit / 信号）逐个 kill。

铁律：只杀**本进程登记过的 pid**，绝不按进程名或端口全杀——避免误伤
用户手动启动的 BCC / 其它实例（不强调杀 BCC/浏览器）。
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading

_lock = threading.Lock()
_pids: set[int] = set()

# 已登记但已自行退出的 pid 会被忽略（taskkill 报错即视为已退出，不重试）


def register(pid: int | None) -> None:
    """登记一个由本进程拉起的 sidecar pid。"""
    try:
        if pid and int(pid) > 0:
            with _lock:
                _pids.add(int(pid))
    except Exception:
        pass


def unregister(pid: int | None) -> None:
    try:
        with _lock:
            _pids.discard(int(pid))
    except Exception:
        pass


def alive_pids() -> list[int]:
    with _lock:
        return sorted(_pids)


def _pid_running(pid: int) -> bool:
    try:
        import psutil  # type: ignore
        return psutil.pid_exists(pid)
    except Exception:
        if sys.platform == "win32":
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                timeout=8, text=True, errors="replace")
            return str(pid) in (out.stdout or "")
        try:
            os.kill(pid, 0)
            return True
        except Exception:
            return False


def kill_all(reason: str = "") -> list[int]:
    """终止本进程登记的所有 sidecar pid；返回实际下手的 pid 列表。"""
    with _lock:
        pids = sorted(_pids)
        _pids.clear()
    killed: list[int] = []
    for pid in pids:
        if not _pid_running(pid):
            continue
        try:
            if sys.platform == "win32":
                # /T 连带子进程树（PyInstaller 解压器形态下 daemon 还有子进程）
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=10)
            else:
                os.kill(pid, 15)
            killed.append(pid)
        except Exception:
            pass
    return killed
