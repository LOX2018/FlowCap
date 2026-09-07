"""FastAPI 应用入口

迁移自 DY_Spider_base/auto_dm/web_bridge.py 的 WebBridge 类。
关键变化：
- 34 个 WebBridge 方法 → 多个 router（按业务域分组）
- 中文字符串协议 → Pydantic 强类型模型
- getAccounts 重操作 → 拆分轻量 list + 重量级 verify
- 3s 轮询 → WebSocket 推送
"""
from contextlib import asynccontextmanager
import asyncio
import os
import platform
import subprocess
import sys
import threading
import time
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from config import settings
from api import accounts, engine, live, messages, overview, settings as settings_api, tasks, logs as logs_api
from api import ai as ai_api
from core.auto_dm import AutoDM

# 2026-09-06 全局治理（D：系统死代理隔离）：
# Windows 注册表系统代理（ProxyEnable=1，如 v2rayN 写入的 127.0.0.1:10808）
# 会被 Python requests 自动继承（urllib.getproxies_registry）。代理软件核心
# 没运行时，本进程所有 douyin API 请求全部 ProxyError（实测 20:36 日志：
# 10808 积极拒绝 → capture/verify 全灭）。而本项目 requests 全链路【从不使用
# 代理】——账号代理(DY_PROXY)只注入 Playwright 浏览器，不作用于 requests。
# 故在本进程内禁用 requests 的环境/注册表代理探测（仅本进程生效，
# 不动系统设置、不影响其它软件；浏览器侧代理防护见 vbrowser._dead_system_proxy_arg）。
os.environ.setdefault("NO_PROXY", "*")
os.environ.setdefault("no_proxy", "*")


def _target_triple() -> str:
    """返回当前平台的 Rust target triple（与 Tauri externalBin / build_sidecar.py 命名一致）。"""
    sys_name = platform.system()
    machine = platform.machine().lower()
    if sys_name == "Windows":
        arch = "aarch64" if "arm" in machine or "aarch" in machine else "x86_64"
        return f"{arch}-pc-windows-msvc"
    if sys_name == "Darwin":
        arch = "aarch64" if machine == "arm64" else "x86_64"
        return f"{arch}-apple-darwin"
    arch = "aarch64" if "arm" in machine or "aarch" in machine else "x86_64"
    return f"{arch}-unknown-linux-gnu"


def _resolve_sidecar_binary(name: str) -> str | None:
    """解析 sidecar 二进制路径（recv-daemon / browser-daemon）。

    Backend exe 在发布态位于 <app_root>/binaries/ 下，与其他 sidecar 同级。
    搜索顺序：
      1) backend exe 所在目录 / <name>-<triple>.exe
      2) backend exe 所在目录 / <name>.exe（无 triple 别名）
      3) 开发态：<root>/src-tauri/binaries/<name>-<triple>.exe
    """
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    triple = _target_triple()
    # 1) 同目录 / <name>-<triple>.exe
    cand = os.path.join(exe_dir, f"{name}-{triple}.exe")
    if os.path.isfile(cand):
        return cand
    # 2) 同目录 / <name>.exe
    cand = os.path.join(exe_dir, f"{name}.exe")
    if os.path.isfile(cand):
        return cand
    # 3) 开发态：backend 在 <root>/backend/，需上溯到 <root>/src-tauri/binaries/
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)
    cand = os.path.join(project_root, "src-tauri", "binaries", f"{name}-{triple}.exe")
    if os.path.isfile(cand):
        return cand
    return None


def _wait_for_port(port: int, timeout: int = 30) -> bool:
    """等待 127.0.0.1:port 开始监听（轮询间隔 0.5s），超时返回 False。"""
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


def _spawn_sidecar(binary: str, args: list[str]) -> subprocess.Popen:
    """spawn sidecar 子进程（独立进程组，不阻塞 backend）。"""
    kwargs: dict = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if platform.system() == "Windows":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[assignment]
    else:
        kwargs["start_new_session"] = True  # type: ignore[assignment]
    return subprocess.Popen([binary] + args, **kwargs)


def _auto_start_daemons() -> None:
    """启动后为所有账号拉起 browser_daemon + recv_daemon sidecar。

    browser_daemon（BCC 浏览器容器）是昵称关联的前置条件：
      - capture_all(with_browser=True) 需要 BCC 提供无头浏览器环境
      - 无头浏览器截 im/user/info → 数字 UID 桥接 → 写昵称/头像

    recv_daemon（私信接收守护）是会话列表的前置条件：
      - 首次 HTTP 请求触发全量拉取（get_message_by_init）
      - 端口未就绪 → recvDaemonDown → 日志刷屏 + 渲染崩溃

    PyInstaller onefile 解压+冷启动约需 3~10 秒，spawn 后轮询端口直到 bind 成功
    （最多等 30 秒），确保 backend 启动完成时 daemon 已就绪。

    昵称关联（capture_all with_browser=True）需要 BCC 在 chat 页滚动 40 轮触发全部
    im/user/info，耗时 ~66s，远超 backend 启动超时。因此 nickname sync 在 daemon 线程
    中异步执行，不阻塞 backend 启动完成。前端首次拉取可能仍有数字 UID，WS B 机制会
    逐步补齐，待 nickname sync 完成后刷新即可全量命中。
    """
    try:
        from auto_dm import accounts as acct_core
        names = [n[0] if isinstance(n, (tuple, list)) else n for n in acct_core.list_accounts()]
        if not names:
            logger.info("[startup] 无账号，跳过 daemon 自动拉起")
            return

        # 2026-09-06 性能修复（用户实测：前端启动 40s）：串行拉起 + 逐个
        # _wait_for_port 导致 N 个账号 ≈ N×15s 阻塞。改为【全部并行 spawn】
        # 后统一轮询等端口 —— recv_daemon 之间互不依赖，BCC 与它们也无依赖，
        # 并行后总耗时 ≈ 最慢一个（~15s），N 账号不再线性叠加。
        spawned = []  # (port, pid, label)

        # 1. browser_daemon（BCC 容器）—— 2026-09-06 改为【默认不随启动拉起】
        #    历史理由（昵称自动捕获）已于 2026-08-29 废除（风控保护，改手动触发）。
        #    BCC 的消费者（WP 发送/昵称捕获/回声轮询）全部按需或可延后，
        #    启动时不该为「可能不用」的功能预付 15s 启动成本 + 风控暴露。
        #    需要随启动拉起时设 DY_BCC_ON_START=1。
        bcc_binary = _resolve_sidecar_binary("dyautodm-browser-daemon")
        if bcc_binary is None:
            logger.warning("[startup] 未找到 dyautodm-browser-daemon 二进制，跳过 BCC 拉起")
        else:
            import os as _os
            if _os.environ.get("DY_BCC_ON_START", "0") == "1":
                try:
                    bport = acct_core.browser_daemon_port(names[0])
                    if acct_core._port_open(bport, timeout=0.2):
                        logger.info(f"[startup] browser_daemon 已在运行 (port={bport})，跳过")
                    else:
                        proc = _spawn_sidecar(bcc_binary, ["--account", names[0], "--port", str(bport)])
                        spawned.append((bport, proc.pid, f"browser_daemon({names[0]})"))
                except Exception as e:
                    logger.warning(f"[startup] 拉起 browser_daemon 失败: {e}")
            else:
                logger.info(
                    "[startup] BCC 不随启动拉起（懒加载：WP 发送/更新会话首次使用时自动拉起；"
                    "设 DY_BCC_ON_START=1 可恢复随启动拉起）")

        # 2. 每个账号的 recv_daemon（并行 spawn，不等待）
        recv_binary = _resolve_sidecar_binary("dyautodm-recv-daemon")
        if recv_binary is None:
            logger.warning("[startup] 未找到 dyautodm-recv-daemon 二进制，跳过 recv_daemon 拉起")
            return
        for name in names:
            try:
                port = acct_core.recv_daemon_port(name)
                if acct_core._port_open(port, timeout=0.2):
                    logger.info(f"[startup] {name} 的 recv_daemon 已在运行 (port={port})，跳过")
                    continue
                proc = _spawn_sidecar(recv_binary, ["--accounts", name, "--port", str(port)])
                spawned.append((port, proc.pid, f"recv_daemon({name})"))
            except Exception as e:
                logger.warning(f"[startup] 拉起 {name} 的 recv_daemon 失败: {e}")

        # 3. 统一等端口就绪（并行后总耗时 ≈ 最慢一个）
        for port, pid, label in spawned:
            ok = _wait_for_port(port, timeout=30)
            logger.info(
                f"[startup] {label} (pid={pid}) "
                + ("端口已就绪" if ok else "等待端口超时(30s)，继续启动不阻塞"))

        # 3. 昵称关联（数字 UID → 昵称/头像）
        #    2026-08-29 风控收敛（用户要求）：启动【不再自动】触发 capture_all。
        #    原逻辑会在启动 45s 后自动跑首包 HTTP + 20 次 cmd 301 补全，
        #    等于每次开软件都在抓包，频次高且有风控风险；而私信页本应纯读库。
        #    现改为开关控制，默认关闭；需要捕获时由用户点「更新会话」按钮触发。
        #    开启方式：设置环境变量 DY_AUTO_CAPTURE_ON_START=1
        import os as _os
        if _os.environ.get("DY_AUTO_CAPTURE_ON_START", "0") == "1":
            logger.info("[startup] DY_AUTO_CAPTURE_ON_START=1，启动后将自动触发一次会话捕获")
            threading.Thread(target=_nickname_sync_background, args=(names,), daemon=True).start()
        else:
            logger.info(
                "[startup] 启动不自动抓包（风控保护）：私信页纯读库，"
                "需要更新会话列表/聊天记录请在私信页点「更新会话」按钮"
            )

    except Exception as e:
        logger.warning(f"[startup] 自动拉起 daemon 失败（不影响使用）: {e}")


def _nickname_sync_background(names: list[str]) -> None:
    """后台线程触发昵称关联：等待 BCC 完全就绪后调 capture_all(with_browser=True)。

    BCC 启动后需要 ~30s 加载到 chat 页 + ~66s 滚动触发 im/user/info。
    此线程不阻塞 backend 启动，前端首次拉取可能仍有数字 UID，
    待 sync 完成后刷新页面即可 100% 命中。
    """
    import time
    try:
        from auto_dm.conversation_capture import capture_all
        # 等 BCC 完全就绪（导航到 chat + 前端加载）
        time.sleep(45)
        for name in names:
            try:
                logger.info(f"[nickname] 触发 {name} 的昵称关联（数字 UID 桥接）…")
                n_conv, n_msg = capture_all(name, with_browser=True)
                logger.info(f"[nickname] {name} 昵称关联完成：{n_conv} 会话，{n_msg} 消息")
            except Exception as e:
                logger.warning(f"[nickname] {name} 昵称关联失败（WS B 机制兜底）: {e}")
    except Exception as e:
        logger.warning(f"[nickname] 昵称关联流程失败: {e}")


def _warm_verify_cache() -> None:
    """后台预热账号校验缓存：getAccounts 首次进入页面时 verify 是网络探活(1~3s/账号)，
    启动即并行预热一遍写入 TTL 缓存，用户打开账号管理页时列表秒出，消灭 4s 首屏等待。"""
    try:
        from api.accounts import _cached_verify
        from auto_dm import accounts as acct_core
        names = [n[0] if isinstance(n, (tuple, list)) else n for n in acct_core.list_accounts()]
        if not names:
            return
        logger.info(f"[warmup] 后台预热 {len(names)} 个账号的校验缓存…")
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(len(names), 8)) as pool:
            list(pool.map(lambda n: _cached_verify(n, timeout=3), names))
        logger.info("[warmup] 账号校验缓存预热完成（账户页首屏将秒出）")
    except Exception as e:
        logger.warning(f"[warmup] 账号校验缓存预热失败（不影响使用）: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"DYAutoDM 后端启动，端口 {settings.backend_port}")
    # 数据库初始化（SQLite WAL，替代 JSON 文件存储）
    try:
        import database
        database.get_db()
        logger.info("[db] SQLite 数据库已就绪")
    except Exception as e:
        logger.error(f"[db] 数据库初始化失败: {e}")
    # 启动收尾上次进程遗留的悬空「运行中」历史任务（按 pid 比对兜底，
    # 不会误伤本进程将要运行的任务；正常退出已由 AutoDM.shutdown 真实收尾）
    try:
        from tasks_history import fix_stuck_tasks
        fix_stuck_tasks()
    except Exception as e:
        logger.warning(f"[history] 启动收尾悬空任务失败（不影响使用）: {e}")
    # 引擎主控单例（替代原版 WebBridge.adm）
    app.state.adm = AutoDM()
    # 后台预热账号校验缓存（并发，不阻塞启动）
    threading.Thread(target=_warm_verify_cache, daemon=True).start()
    # 原图缓存 TTL 清理(后台延迟 60s,删除过期/超容的本地解密图)
    try:
        from auto_dm.origin_image_resolver import sweep_background
        sweep_background()
    except Exception as e:
        logger.warning(f"[origin_image] 启动 TTL 清理失败（不影响使用）: {e}")
    # 启动后为所有账号拉起 daemon（browser + recv）并触发昵称关联
    # 同步执行，确保 backend 启动完成时 daemon 已就绪
    _auto_start_daemons()
    # WP 通道私信接收循环（抖音网页版 chat 页 hook）
    # 2026-09-05 新增。BCC 是单例（所有账号共享一个浏览器，用 names[0] 的端口），
    # 故 wp_recv 也只对第一个账号轮询。与 WS 通道（recv_daemon）并存、应用层去重。
    try:
        from auto_dm import accounts as _acct_wp
        from daemon.wp_recv import run_wp_recv_loop
        _wp_names = [
            n[0] if isinstance(n, (tuple, list)) else n
            for n in _acct_wp.list_accounts()
        ]
        if _wp_names:
            _wp_task = asyncio.create_task(run_wp_recv_loop(_wp_names[0]))
            # 防止任务被 GC（asyncio 只持有弱引用）
            app.state.wp_recv_task = _wp_task
            logger.info(f"[startup] WP 通道接收循环已启动 (account={_wp_names[0]})")
    except Exception as e:
        logger.warning(f"[startup] WP 接收循环启动失败（不影响 WS 通道）: {e}")
    # AI 获客自动回复：建表 + 若配置启用则自启监听（2026-09-06 嵌入）
    try:
        from services import ai_reply as _ai
        _ai.ensure_tables()
        if _ai.get_config().get("enabled"):
            _ai.WORKER.start()
            logger.info("[startup] AI 获客自动回复已按配置自启")
    except Exception as e:
        logger.warning(f"[startup] AI 自动回复初始化失败（不影响主流程）: {e}")
    yield
    logger.info("DYAutoDM 后端关闭")
    await app.state.adm.shutdown()


app = FastAPI(
    title="DYAutoDM API",
    version="0.1.0",
    description="抖音直播间自动私信控制台 - 后端 API",
    lifespan=lifespan,
)

# 允许前端跨域（开发模式 Vite 跑在 1420，preview 跑在 4173，Tauri 用 tauri://localhost）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 路由挂载
app.include_router(overview.router, prefix="/api", tags=["overview"])
app.include_router(engine.router, prefix="/api/engine", tags=["engine"])
app.include_router(accounts.router, prefix="/api/accounts", tags=["accounts"])
app.include_router(live.router, prefix="/api/live", tags=["live"])
app.include_router(messages.router, prefix="/api/messages", tags=["messages"])
app.include_router(tasks.router, prefix="/api/tasks", tags=["tasks"])
app.include_router(settings_api.router, prefix="/api/settings", tags=["settings"])
app.include_router(logs_api.router, prefix="/api/logs", tags=["logs"])
# AI 获客自动回复（嵌入自 douyin-auto-reply-assistant，2026-09-06）
app.include_router(ai_api.router, prefix="/api/ai", tags=["ai"])

# 运行日志输出到控制台（CMD 窗口），方便在桌面应用外独立查看
logger.remove()
logger.add(
    "logs/run_{time:YYYYMMDD_HHMMSS}.log",
    level="INFO",
    rotation="20 MB",
    retention=5,
    encoding="utf-8",
    format="{time:HH:mm:ss} | {level: <8} | {message}",
)
# stderr sink：enqueue=True 让日志在独立线程写出，即使 Windows 控制台 GBK 编码
# 中文失败也不会中断请求处理（异常被 loguru 吞掉而非抛出到主线程）。
logger.add(
    sys.stderr,
    level="INFO",
    colorize=True,
    enqueue=True,
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
)


@app.get("/api/status")
async def status():
    from models.overview import StatusResponse
    return StatusResponse(ok=True, running=False)


@app.get("/")
async def root():
    return {"name": "DYAutoDM API", "docs": "/docs", "version": "0.1.0"}


if __name__ == "__main__":
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser(description="DYAutoDM FastAPI 后端")
    parser.add_argument("--port", type=int, default=8000, help="监听端口")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址")
    args = parser.parse_args()

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
