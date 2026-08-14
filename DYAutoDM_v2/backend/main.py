"""FastAPI 应用入口

迁移自 DY_Spider_base/auto_dm/web_bridge.py 的 WebBridge 类。
关键变化：
- 34 个 WebBridge 方法 → 多个 router（按业务域分组）
- 中文字符串协议 → Pydantic 强类型模型
- getAccounts 重操作 → 拆分轻量 list + 重量级 verify
- 3s 轮询 → WebSocket 推送
"""
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from config import settings
from api import accounts, engine, live, messages, overview, settings as settings_api, tasks
from core.auto_dm import AutoDM


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"DYAutoDM 后端启动，端口 {settings.backend_port}")
    # 引擎主控单例（替代原版 WebBridge.adm）
    app.state.adm = AutoDM()
    yield
    logger.info("DYAutoDM 后端关闭")
    await app.state.adm.shutdown()


app = FastAPI(
    title="DYAutoDM API",
    version="0.1.0",
    description="抖音直播间自动私信控制台 - 后端 API",
    lifespan=lifespan,
)

# 允许前端跨域（开发模式 Vite 跑在 1420）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:1420", "http://127.0.0.1:1420", "tauri://localhost"],
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
