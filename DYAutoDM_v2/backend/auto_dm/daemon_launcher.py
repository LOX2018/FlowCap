"""守护进程 launcher（backend 与校验流程共享）。

方案 A 的缺口：backend lifespan 启动时拉起 daemon，但若账号在 backend 启动后才
扫码上线（或首次引擎校验触发 capture_all），daemon 不会自动拉起，导致 BCC 端口未开、
昵称关联 0。本模块把「拉起 browser_daemon + recv_daemon」提取为**可重入幂等**函数，
backend 启动、账号登录成功、首次引擎校验均可调用，端口已开则跳过。
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

from loguru import logger

from auto_dm import accounts as acct_core

# sidecar pid 台账（backend 退出时统一清扫；跨 spawn 路径共享一份）
try:
    import daemon_registry as _dreg
except Exception:  # 极端情况：路径未就绪，退化为不登记（不影响主流程）
    _dreg = None


def _target_triple() -> str:
    return "x86_64-pc-windows-msvc"


def _resolve_sidecar_binary(name: str) -> str | None:
    """解析 sidecar 二进制路径（recv-daemon / browser-daemon）。

    搜索顺序（onedir 免解压优先）：
      1) backend exe 所在目录 / <name>-<triple>/<name>-<triple>.exe（onedir 目录）
      2) backend exe 所在目录 / <name>-<triple>.exe（onefile 单文件）
      3) 开发态：<root>/src-tauri/binaries/（onedir 目录与单文件都试）
    """
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    triple = _target_triple()
    fname = f"{name}-{triple}.exe"
    # 1) onedir 目录形态（免解压）：<exe_dir>/<full>/<full>.exe
    cand = os.path.join(exe_dir, f"{name}-{triple}", fname)
    if os.path.isfile(cand):
        return cand
    # 2) onefile 单文件（旧部署）
    cand = os.path.join(exe_dir, fname)
    if os.path.isfile(cand):
        return cand
    cand = os.path.join(exe_dir, f"{name}.exe")
    if os.path.isfile(cand):
        return cand
    # 3) 开发态
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)
    cand = os.path.join(project_root, "src-tauri", "binaries", f"{name}-{triple}", fname)
    if os.path.isfile(cand):
        return cand
    cand = os.path.join(project_root, "src-tauri", "binaries", fname)
    if os.path.isfile(cand):
        return cand
    # 4) app_root 兜底（backend 以 onedir 形态运行时 exe_dir 在目录内一层，
    #    上面各档落空——从 app_root()/binaries 再找一遍，onedir 优先）
    try:
        import vbrowser
        root = vbrowser.app_root()
        cand = os.path.join(root, "binaries", f"{name}-{triple}", fname)
        if os.path.isfile(cand):
            return cand
        cand = os.path.join(root, "binaries", fname)
        if os.path.isfile(cand):
            return cand
    except Exception:
        pass
    return None


def _spawn_sidecar(binary: str, args: list) -> subprocess.Popen:
    # 会员体系（v0.37.0）：子进程继承会员空间与主密钥（环境变量透传，不落盘）
    env = {k: v for k, v in os.environ.items()
           if k.startswith("DY_") or k in ("PYTHONPATH", "SYSTEMROOT", "TEMP", "TMP",
                                           "COMPUTERNAME", "USERPROFILE", "APPDATA",
                                           "LOCALAPPDATA", "PROGRAMDATA", "WINDIR")}
    try:
        from services import member_ctx as _mctx
        if _mctx.current():
            env["DY_MEMBER"] = _mctx.current_member_id() or ""
            mk = _mctx.master_key()
            if mk:
                env["DY_MEMBER_KEY"] = mk
    except Exception:
        pass
    proc = subprocess.Popen(
        [binary, *args],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    # 登记 pid：backend 退出时清扫，避免孤儿进程占端口
    if _dreg is not None:
        _dreg.register(proc.pid)
    return proc


def _wait_for_port(port: int, timeout: int = 30) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            import socket
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.5)
                if s.connect_ex(("127.0.0.1", port)) == 0:
                    return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def ensure_daemons_for(account: str, wait: bool = True,
                       skip_cooldown: bool = False) -> dict:
    """确保指定账号的 browser_daemon（单例）+ recv_daemon 已运行，未运行则拉起。

    幂等：端口已开则跳过。返回 {browser: bool, recv: bool} 表示拉起是否成功。
    skip_cooldown：透传给 ensure_bcc，预对齐路径（启动即拉齐）豁免冷静期。
    """
    result = {"browser": False, "recv": False}
    try:
        # 1. browser_daemon（单例，用首个账号端口；这里直接用本账号端口）
        bport = acct_core.browser_daemon_port(account)
        bcc_binary = _resolve_sidecar_binary("dyautodm-browser-daemon")
        if bcc_binary is None:
            logger.warning("SYS-001", "[daemon-launcher] 未找到 browser_daemon 二进制，跳过 BCC 拉起")
        elif acct_core._port_open(bport, timeout=0.2):
            result["browser"] = True
        else:
            # 2026-09-06 全局治理（BCC spawn 路径收敛）：
            # 本处原本是第 3 条独立 spawn 路径，绕过 accounts.ensure_bcc
            # 的【启动冷静期】与【防重复拉起】保护 —— 这正是「启动时 BCC
            # 快闪唤醒」的结构根因之一（更新会话/凭证校验走到这里就直接拉）。
            # 改为统一委托 ensure_bcc，全项目只剩一个 BCC 拉起入口。
            try:
                st = acct_core.ensure_bcc(account, wait_ready=wait,
                                          skip_cooldown=skip_cooldown)
                result["browser"] = bool(st.get("ok"))
                if st.get("ok"):
                    logger.info(f"[daemon-launcher] BCC 已就绪 (port={bport}) via ensure_bcc")
                else:
                    logger.warning("SYS-002", 
                        f"[daemon-launcher] BCC 未拉起（{st.get('msg')}）——"
                        f"冷静期内或二进制缺失，属预期，不强制拉起")
            except Exception as e:
                logger.warning("SYS-003", f"[daemon-launcher] 拉起 browser_daemon 失败: {e}")

        # 2. recv_daemon
        rport = acct_core.recv_daemon_port(account)
        recv_binary = _resolve_sidecar_binary("dyautodm-recv-daemon")
        if recv_binary is None:
            logger.warning("SYS-004", "[daemon-launcher] 未找到 recv_daemon 二进制，跳过")
        elif acct_core._port_open(rport, timeout=0.2):
            result["recv"] = True
        else:
            try:
                # 2026-09-06 全局治理：原写 "--account" 是历史 bug，recv_daemon 要求 "--accounts"
                proc = _spawn_sidecar(recv_binary, ["--accounts", account, "--port", str(rport)])
                logger.info(f"[daemon-launcher] 已拉起 recv_daemon (port={rport}, pid={proc.pid})")
                if wait:
                    _wait_for_port(rport, timeout=30)
                result["recv"] = True
            except Exception as e:
                logger.warning("SYS-005", f"[daemon-launcher] 拉起 recv_daemon 失败: {e}")
    except Exception as e:
        logger.warning("SYS-006", f"[daemon-launcher] ensure_daemons_for 异常（不影响使用）: {e}")
    return result
