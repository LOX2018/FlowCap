# DYAutoDM v2

抖音直播间自动私信控制台 — 重构版。

## 架构

采用 **Tauri 2 + FastAPI + PyInstaller Sidecar + Vite/React/TypeScript**：

```
┌──────────────────────────────────────────────────────────────┐
│  Tauri Desktop App（Rust 壳，~10MB）                         │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ Frontend: Vite + React 18 + TypeScript + Framer Motion │  │
│  │  - 7 个页面：overview/crawl/live/msg/accounts/tasks/settings │
│  │  - API 客户端由 OpenAPI 自动生成（强类型）             │  │
│  │  - WebSocket 实时推送（替代 3s 轮询）                  │  │
│  └─────────────────────┬──────────────────────────────────┘  │
│                        │ HTTP / WebSocket                     │
│  ┌─────────────────────▼──────────────────────────────────┐  │
│  │ Backend: FastAPI（PyInstaller 打包为 sidecar 二进制）  │  │
│  │  - Pydantic 强类型协议（OpenAPI 自动 schema）          │  │
│  │  - AutoDM 主控 + DispatchCenter 延迟队列               │  │
│  │  - LiveChatHook 弹幕 WS 监听                           │  │
│  └─────────────────────┬──────────────────────────────────┘  │
│                        │ subprocess                           │
│  ┌─────────────────────▼──────────────────────────────────┐  │
│  │ Sidecar 子进程管理（Rust SidecarManager）              │  │
│  │  - browser_daemon（每账号，凭证保活）                  │  │
│  │  - recv_daemon（每账号，私信接收）                     │  │
│  └────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────┘
```

## 目录结构

```
DYAutoDM_v2/
├── src-tauri/              # Tauri Rust 壳（窗口 + Sidecar 管理）
│   ├── Cargo.toml
│   ├── tauri.conf.json     # Tauri 配置（窗口、sidecar、权限）
│   ├── capabilities/       # Tauri 2 权限声明
│   └── src/
│       ├── main.rs         # 入口
│       ├── lib.rs          # 主逻辑 + 命令注册
│       └── sidecar.rs      # SidecarManager（守护进程生命周期）
│
├── frontend/               # Vite + React + TypeScript
│   ├── package.json
│   ├── vite.config.ts
│   ├── tsconfig.json
│   └── src/
│       ├── App.tsx
│       ├── api/            # OpenAPI 生成的客户端
│       ├── components/     # 通用组件
│       └── pages/          # 7 个业务页面
│
├── backend/                # FastAPI Python 后端
│   ├── requirements.txt
│   ├── main.py             # FastAPI 入口
│   ├── api/                # 路由层
│   ├── core/               # 业务核心（AutoDM/Dispatch/LiveHook）
│   ├── daemon/             # browser_daemon / recv_daemon
│   ├── models/             # Pydantic 协议模型
│   ├── services/           # 业务服务层
│   └── config.py
│
├── scripts/                # 构建辅助脚本
│   ├── build_sidecar.py    # PyInstaller 打包 backend
│   └── gen_api_client.sh   # OpenAPI → 前端 TS 客户端
│
└── docs/                   # 文档
    ├── architecture.md
    ├── migration_guide.md
    └── api_design.md
```

## 开发环境前置要求

| 工具 | 版本 | 用途 |
|---|---|---|
| Node.js | ≥ 20 | 前端 + Tauri CLI |
| Rust | ≥ 1.75 | Tauri 壳编译 |
| Python | ≥ 3.10 | 后端 |
| PyInstaller | ≥ 6.0 | backend 打包为 sidecar |

## 快速开始（开发模式）

### 1. 启动后端（FastAPI 热重载）

```bash
cd backend
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

访问 http://127.0.0.1:8000/docs 查看 OpenAPI 文档。

### 2. 启动前端（Vite 热重载）

```bash
cd frontend
npm install
npm run dev
```

访问 http://localhost:1420。

### 3. 启动 Tauri 桌面壳（集成模式）

```bash
# 在仓库根目录
npm install
npm run tauri dev
```

Tauri 会自动：
1. 启动 Vite dev server
2. 启动 Python sidecar（需先 `python scripts/build_sidecar.py` 打包）
3. 打开桌面窗口加载前端

## 生产打包

```bash
# 1. 打包 Python 后端为单文件 sidecar
python scripts/build_sidecar.py

# 2. 打包 Tauri 应用（含前端 + sidecar）
npm run tauri build
```

产物在 `src-tauri/target/release/bundle/`。

## 与旧版（DY_Spider_base）的关系

- 旧版保留在 `../DY_Spider_base/`，标注为重构前版本
- 旧版业务逻辑（`dy_apis/`、`builder/`、`utils/`）原样迁移到 `backend/`
- 旧版前端 CSS 与组件结构 1:1 移植，仅 `createElement` → JSX
- 详见 [docs/migration_guide.md](docs/migration_guide.md)

## 状态

🚧 **重构中** — 当前为骨架阶段，业务逻辑逐步从 DY_Spider_base 迁移。

重构启动日期：2026-08-13
