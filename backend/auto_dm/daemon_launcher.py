"""守护进程 launcher（backend 与校验流程共享）。

方案 A 的缺口：backend lifespan 启动时拉起 daemon，但若账号在 backend 启动后才
扫码上线（或首次引擎校验触发 capture_all），daemon 不会自动拉起，导致 BCC 端口未开、
昵称关联 0。本模块把「拉起 browser_daemon + recv_daemon」提取为**可重入幂等**函数，
backend 启动、账号登录成功、首次引擎校验均可调用，端口已开则跳过。
"""
from __future__ import annotations

import os
import platform
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

    ⚠️ 部署位置铁律（2026-09-13 用户要求）：sidecar 直接放**应用根目录**，
    不再放 <root>/binaries/ 子目录。搜索顺序：根目录优先 → 兼容历史 binaries/。

      1) <root>/<name>-<triple>/<name>-<triple>.exe（onedir 目录，标准）
      2) <root>/<name>-<triple>.exe（onefile 单文件）
      3) 兼容旧部署：<root>/binaries/ 下同名（目录/单文件）
      4) 开发态：<root>/src-tauri/binaries/
    """
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    triple = _target_triple()
    fname = f"{name}-{triple}.exe"
    # 应用根：exe 在根目录 → exe_dir 自身；exe 在 onedir 目录内 → 上溯一级
    _root_cands = [exe_dir]
    if os.path.basename(exe_dir) == f"{name}-{triple}":
        _root_cands.insert(0, os.path.dirname(exe_dir))
    try:
        import vbrowser as _vb
        _r = _vb.app_root()
        if _r and _r not in _root_cands:
            _root_cands.append(_r)
    except Exception:
        pass
    # 1~3) 根目录优先，其次兼容 binaries/ 子目录
    for _r in _root_cands:
        for _sub in ("", "binaries"):
            base = os.path.join(_r, _sub) if _sub else _r
            cand = os.path.join(base, f"{name}-{triple}", fname)
            if os.path.isfile(cand):
                return cand
            cand = os.path.join(base, fname)
            if os.path.isfile(cand):
                return cand
            cand = os.path.join(base, f"{name}.exe")
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
    return None



def _spawn_sidecar(binary: str, args: list, *,
                   no_window: bool = True,
                   own_process_group: bool = False) -> subprocess.Popen:
    """唯一 sidecar 启动入口（ADR-001，2026-09-22 收敛）。

    收敛理由（ADR-001 §3，实测依据见其 §2）：
      · 原有两个同名实现（本文件 / `main.py`），**行为不一致** ——
        本文件用 `CREATE_NO_WINDOW`（不弹窗、同进程组），
        `main.py` 用 `CREATE_NEW_PROCESS_GROUP`（独立进程组、**无** NO_WINDOW）。
      · ADR-001 §2·Q1 实测证明进程组语义**无消费方**：生命周期清扫由
        `daemon_registry` 按登记 pid 显式完成（`main.py::_kill_spawned_daemons`
        + `atexit`），不依赖 OS 进程组级联 ⇒ 收敛**预期零功能影响**。
      · §2·Q2 实测：缺失 `CREATE_NO_WINDOW` 在 Tauri 宿主（无 console）下
        可能新建 console 窗口 —— 与用户反复抱怨的「窗口快闪」机理吻合。

    参数：
      no_window=True（默认）：Windows 下加 `CREATE_NO_WINDOW`，避免弹 console 窗口。
      own_process_group=False（默认）：不建独立进程组。生命周期已由
        daemon_registry 显式负责；保留该参数仅为将来若确需「守护独立于 backend 存活」。
    """
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
    kwargs: dict = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "env": env,
    }
    if platform.system() == "Windows":
        flags = 0
        if no_window:
            flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
        if own_process_group:
            flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        kwargs["creationflags"] = flags
    elif own_process_group:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen([binary, *args], **kwargs)
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
    # 2026-09-14 v0.43.8：用户主动停止 BCC → 自动路径一律不拉起（关得掉）。
    try:
        from auto_dm.accounts import bcc_user_stopped as _stopped
    except Exception:
        _stopped = None
    if _stopped and _stopped():
        logger.info(
            "[daemon-launcher] BCC 已被用户停止，跳过自动拉起")
        return {"browser": False, "recv": False,
                "msg": "BCC 已被用户停止（自动拉起已禁用）"}
    
    result = {"browser": False, "recv": False}
    try:
        # 1. browser_daemon（单例，用首个账号端口；这里直接用本账号端口）
        bport = acct_core.browser_daemon_port(account)
        bcc_binary = _resolve_sidecar_binary("flowcap-browser-daemon")
        if bcc_binary is None:
            logger.warning(f"[SYS-001] " + "[daemon-launcher] 未找到 browser_daemon 二进制，跳过 BCC 拉起")
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
                    logger.warning(f"[SYS-002] [daemon-launcher] BCC 未拉起（{st.get('msg')}）——"
                        f"冷静期内或二进制缺失，属预期，不强制拉起")
            except Exception as e:
                logger.warning(f"[SYS-003] " + f"[daemon-launcher] 拉起 browser_daemon 失败: {e}")

        # 2. recv_daemon
        rport = acct_core.recv_daemon_port(account)
        recv_binary = _resolve_sidecar_binary("flowcap-recv-daemon")
        if recv_binary is None:
            logger.warning(f"[SYS-004] " + "[daemon-launcher] 未找到 recv_daemon 二进制，跳过")
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
                logger.warning(f"[SYS-005] " + f"[daemon-launcher] 拉起 recv_daemon 失败: {e}")
    except Exception as e:
        logger.warning(f"[SYS-006] " + f"[daemon-launcher] ensure_daemons_for 异常（不影响使用）: {e}")
    return result
