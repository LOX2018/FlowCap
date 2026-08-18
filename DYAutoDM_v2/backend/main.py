"""FastAPI 应用入口

迁移自 DY_Spider_base/auto_dm/web_bridge.py 的 WebBridge 类。
关键变化：
- 34 个 WebBridge 方法 → 多个 router（按业务域分组）
- 中文字符串协议 → Pydantic 强类型模型
- getAccounts 重操作 → 拆分轻量 list + 重量级 verify
- 3s 轮询 → WebSocket 推送
"""
from contextlib import asynccontextmanager
import sys
import threading
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from config import settings
from api import accounts, engine, live, messages, overview, settings as settings_api, tasks, logs as logs_api
from core.auto_dm import AutoDM


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
    # 引擎主控单例（替代原版 WebBridge.adm）
    app.state.adm = AutoDM()
    # 后台预热账号校验缓存（并发，不阻塞启动）
    threading.Thread(target=_warm_verify_cache, daemon=True).start()
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
