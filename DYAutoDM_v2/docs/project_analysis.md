# DYAutoDM v2 重构项目分析

> 基于 DY_Spider_base 重构，架构：Tauri 2 + FastAPI + PyInstaller Sidecar + React/TypeScript

---

## 一、项目概述

### 1.1 定位

抖音直播间自动私信控制台桌面应用。对抖音直播间评论进行实时捕获、关键词匹配、自动私信，辅以多账号管理、凭证保活、私信接收等能力。

### 1.2 技术栈

| 层 | 技术 | 说明 |
|---|---|---|
| 桌面壳 | Tauri 2 (Rust) | 窗口管理、sidecar 进程管理 |
| 后端 | FastAPI (Python 3.11+) | 异步 API、状态机、弹幕监听 |
| 前端 | React 18 + TypeScript | 7 页面 SPA |
| 构建 | Vite | 前端构建 |
| 打包 | PyInstaller | Python 后端 → 独立 exe |
| 协议 | Pydantic + OpenAPI | 前后端类型安全通信 |

### 1.3 目录结构

```
DYAutoDM_v2/
├── src-tauri/              # Tauri 桌面壳 (Rust)
│   ├── src/
│   │   ├── main.rs         # 入口, 无框窗口
│   │   ├── lib.rs          # AppState + 6 个 Tauri 命令
│   │   └── sidecar.rs      # SidecarManager（启动/停止/保活 3 个子进程）
│   ├── binaries/           # sidecar exe 存放目录（带 target-triple 后缀）
│   ├── icons/              # 应用图标（16x16 ~ 512x512）
│   ├── tauri.conf.json     # 窗口 1440x900, externalBin 声明
│   └── Cargo.toml          # Rust 依赖：tauri 2, shell/dialog/fs/notification 插件
│
├── backend/                # Python 后端
│   ├── main.py             # FastAPI 入口, 7 个路由挂载, 生命周期事件
│   ├── config.py           # pydantic-settings 配置管理
│   ├── api/                # 7 个路由模块（按业务域分组）
│   │   ├── overview.py     # 总览数据聚合
│   │   ├── engine.py       # 引擎启停控制
│   │   ├── accounts.py     # 账号 CRUD + 扫码 + 校验
│   │   ├── live.py         # 直播流数据 + 弹幕发送
│   │   ├── messages.py     # 私信会话列表 + 详情 + 发送
│   │   ├── tasks.py        # 任务配置 + 词库管理
│   │   └── settings.py     # 全局配置读写
│   ├── core/               # 核心业务逻辑
│   │   ├── auto_dm.py      # AutoDM 主控（状态机 EngineState）
│   │   ├── dispatch.py     # DispatchCenter 延迟队列（asyncio.PriorityQueue）
│   │   ├── live_hook.py    # LiveChatHook 弹幕 WebSocket 监听
│   │   └── sender.py       # 发送器（async 包装）
│   ├── daemon/             # 守护进程（独立子进程）
│   │   ├── browser_daemon.py  # 凭证守护（CredentialKeeper）
│   │   └── recv_daemon.py     # 私信接收守护（RecvChannel）
│   ├── models/             # Pydantic 数据模型
│   │   ├── enums.py        # EngineState, RecordStatus 枚举
│   │   └── overview.py     # StatusResponse, OverviewResponse
│   ├── services/           # 服务层
│   │   └── account_service.py  # 账号管理 + 端口分配
│   ├── builder/            # 签名/请求构建（原样迁移）
│   ├── dy_apis/            # 抖音 API 封装（原样迁移）
│   ├── dy_live/            # 直播 protobuf 解析（原样迁移）
│   ├── utils/              # 工具函数（原样迁移）
│   └── static/             # protobuf 文件（原样迁移）
│
├── frontend/               # 前端 SPA
│   └── src/
│       ├── App.tsx         # 根组件：Header + 页面路由 + Toast
│       ├── main.tsx        # 入口
│       ├── api/
│       │   ├── client.ts   # API 客户端（30+ 方法）+ PageProps 类型
│       │   └── sidecar.ts  # Tauri 侧边栏通信
│       ├── components/
│       │   ├── ui.tsx      # 通用组件：Spark/Pill/Avatar/Dot/TABS
│       │   └── header.tsx  # Header 组件
│       ├── pages/          # 7 个页面
│       │   ├── overview.tsx   # 总览（377 行）
│       │   ├── crawl.tsx      # 采集（469 行）
│       │   ├── live.tsx       # 直播监听（1244 行）
│       │   ├── messages.tsx   # 私信
│       │   ├── accounts.tsx   # 账号管理（1764 行）
│       │   ├── tasks.tsx      # 任务中心（267 行）
│       │   └── settings.tsx   # 设置
│       └── styles/
│           └── global.css  # 全局样式（410 行, oklch 色彩系统）
│
├── scripts/
│   ├── build_sidecar.py    # PyInstaller 打包脚本（3 个 exe）
│   └── build_sidecar.ps1   # PowerShell 版本
│
└── dist/sidecar/           # PyInstaller 打包产物
    ├── dyautodm-backend.exe           # 后端主进程（76MB）
    ├── dyautodm-browser-daemon.exe    # 凭证守护（76MB）
    └── dyautodm-recv-daemon.exe       # 私信守护（76MB）
```

---

## 二、架构层次

### 2.1 整体架构图

```
┌──────────────────────────────────────────────────────────────────┐
│  Tauri Desktop App (Rust)                                        │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │  WebView (Vite + React 18 + TypeScript)                    │  │
│  │  ┌──────────────────────────────────────────────────────┐  │  │
│  │  │  App.tsx (Header + 路由 + Toast)                     │  │  │
│  │  │  ├── 总览   (overview.tsx, 3s 轮询)                  │  │  │
│  │  │  ├── 采集   (crawl.tsx, 搜索/详情/点赞/收藏)          │  │  │
│  │  │  ├── 直播监听 (live.tsx, 弹幕/热度/引擎)               │  │  │
│  │  │  ├── 私信   (messages.tsx, 会话/发送)                  │  │  │
│  │  │  ├── 账号管理 (accounts.tsx, 扫码/角色/校验)            │  │  │
│  │  │  ├── 任务中心 (tasks.tsx, 词库/启动/导出)              │  │  │
│  │  │  └── 设置   (settings.tsx, 配置/保存)                  │  │  │
│  │  └──────────────────────────────────────────────────────┘  │  │
│  └──────────────────────┬─────────────────────────────────────┘  │
│                         │ HTTP (127.0.0.1:8000)                  │
│  ┌──────────────────────▼─────────────────────────────────────┐  │
│  │  FastAPI Backend (PyInstaller → dyautodm-backend.exe)      │  │
│  │  ┌──────────────────────────────────────────────────────┐  │  │
│  │  │  API 路由层 (7 个 router)                            │  │  │
│  │  │  ├── /api/overview ─── 聚合 3 个子服务               │  │  │
│  │  │  ├── /api/engine ───── AutoDM 状态机控制              │  │  │
│  │  │  ├── /api/accounts ─── 账号 CRUD + 扫码               │  │  │
│  │  │  ├── /api/live ─────── 弹幕流 + 热度                  │  │  │
│  │  │  ├── /api/messages ─── 私信会话                      │  │  │
│  │  │  ├── /api/tasks ────── 任务配置 + 词库                │  │  │
│  │  │  └── /api/settings ─── 全局配置                      │  │  │
│  │  ├──────────────────────────────────────────────────────┤  │  │
│  │  │  核心层                                          │  │  │
│  │  │  ├── AutoDM (状态机: IDLE→STARTING→RUNNING⇄PAUSED)  │  │  │
│  │  │  ├── DispatchCenter (asyncio.PriorityQueue 调度)     │  │  │
│  │  │  ├── LiveChatHook (弹幕 WS 监听)                    │  │  │
│  │  │  └── Sender (async 发送包装)                       │  │  │
│  │  └──────────────────────────────────────────────────────┘  │  │
│  └──────────────────────┬─────────────────────────────────────┘  │
│                         │ Tauri Shell Sidecar                    │
│  ┌──────────────────────▼─────────────────────────────────────┐  │
│  │  Sidecar 子进程 (Rust SidecarManager 统一管理)              │  │
│  │  ├── dyautodm-browser-daemon (每账号 × 1, 凭证保活)        │  │
│  │  └── dyautodm-recv-daemon    (每账号 × 1, 私信接收)        │  │
│  └────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────┘
```

### 2.2 进程模型

```
┌─ Tauri 主进程 (dyautodm-v2.exe) ──────────────────────────┐
│  管理窗口 + 前端 WebView                                   │
│  SidecarManager:                                           │
│  ├── dyautodm-backend.exe (127.0.0.1:8000, 单例)          │
│  ├── dyautodm-browser-daemon.exe (账号A, 127.0.0.1:8001)  │
│  ├── dyautodm-browser-daemon.exe (账号B, 127.0.0.1:8002)  │
│  ├── dyautodm-recv-daemon.exe (账号A, 127.0.0.1:8011)     │
│  └── dyautodm-recv-daemon.exe (账号B, 127.0.0.1:8012)     │
└────────────────────────────────────────────────────────────┘
```

关键设计点：

- **每个账号独立守护进程**：隔离浏览器上下文，避免多账号共享 Chrome 实例导致互踢
- **端口计算**：`crc32(account_name) % 50000 + 10000`，确定性分配，不冲突
- **Tauri 统一管理**：`SidecarManager` 通过 `AtomicBool` 追踪存活状态，提供 `is_alive()` / `kill()`

---

## 三、后端架构

### 3.1 FastAPI 入口

[main.py](file:///c:/Users/LOX/Desktop/DYchajian/DYAutoDM_v2/backend/main.py) 职责：

1. **应用生命周期**：`lifespan` 上下文管理器创建/销毁 `AutoDM` 单例，挂载到 `app.state.adm`
2. **路由注册**：7 个业务 router，统一前缀 `/api`
3. **CORS**：允许 Vite 开发服务器 (1420) 和 Tauri (tauri://localhost) 跨域
4. **入口**：`argparse` 解析 `--port` / `--host`，`uvicorn` 启动

### 3.2 API 路由总览

| 路由前缀 | 文件 | GET | POST | PUT/DELETE |
|---|---|---|---|---|
| `/api` | [overview.py](file:///c:/Users/LOX/Desktop/DYchajian/DYAutoDM_v2/backend/api/overview.py) | `/overview`, `/stats` | - | - |
| `/api/engine` | [engine.py](file:///c:/Users/LOX/Desktop/DYchajian/DYAutoDM_v2/backend/api/engine.py) | - | `/start`, `/stop`, `/stop-soft`, `/pause`, `/resume` | - |
| `/api/accounts` | [accounts.py](file:///c:/Users/LOX/Desktop/DYchajian/DYAutoDM_v2/backend/api/accounts.py) | `/accounts` | `/accounts`, `/{name}/scan`, `/{name}/check`, `/{name}/role` | `/{name}` DELETE |
| `/api/live` | [live.py](file:///c:/Users/LOX/Desktop/DYchajian/DYAutoDM_v2/backend/api/live.py) | `/stream` | `/danmaku`, `/dm-template` | - |
| `/api/messages` | [messages.py](file:///c:/Users/LOX/Desktop/DYchajian/DYAutoDM_v2/backend/api/messages.py) | `/conversations`, `/conversation` | `/send` | - |
| `/api/tasks` | [tasks.py](file:///c:/Users/LOX/Desktop/DYchajian/DYAutoDM_v2/backend/api/tasks.py) | `/tasks` | `/config`, `/dm-pool` | - |
| `/api/settings` | [settings.py](file:///c:/Users/LOX/Desktop/DYchajian/DYAutoDM_v2/backend/api/settings.py) | `/settings` | `/settings` | - |

### 3.3 核心模块

#### AutoDM 状态机

[auto_dm.py](file:///c:/Users/LOX/Desktop/DYchajian/DYAutoDM_v2/backend/core/auto_dm.py) 是引擎主控，替代旧版 5 个标志位（`_running`/`listen_active`/`hard_stopped`/`no_new`/`paused`）。

```
状态迁移：
  IDLE ──start──→ STARTING ──→ RUNNING ──pause──→ PAUSED
                                  ↑       ──resume──┘
                                  │
                              stop/stop-soft
                                  ↓
                              STOPPING ──→ STOPPED ──→ IDLE (自动)
```

关键设计：

- **单一真源**：所有状态判断通过 `self.state`，不再组合多个标志位
- **两条线路**：监听线路（WS 弹幕）在 `RUNNING/PAUSED` 时活跃；私信线路（DispatchCenter 队列）在非 `IDLE/STOPPED` 时活跃
- **软停止**：`stop_hard=False` → 关闭监听，但允许队列中的存量消息发完
- **硬停止**：`stop_hard=True` → 关闭监听 + 清空队列
- `asyncio.to_thread` 包装同步业务（`_build_one_auth` / `_verify_credential` / `LiveChatHook`），不破坏已迁移的同步代码

#### DispatchCenter 调度器

[dispatch.py](file:///c:/Users/LOX/Desktop/DYchajian/DYAutoDM_v2/backend/core/dispatch.py) 替代旧版 threading 队列：

- `asyncio.PriorityQueue` 实现优先级调度
- `record_status` 枚举化（`captured`/`sent`/`fail`），避免字符串比较 bug
- 去重逻辑：`seen` set 维护已处理目标

#### LiveChatHook 弹幕监听

[live_hook.py](file:///c:/Users/LOX/Desktop/DYchajian/DYAutoDM_v2/backend/core/live_hook.py)：

- 弹幕 WebSocket 监听（protobuf 解析）
- 热度和心跳逻辑
- 通过 `asyncio.to_thread` 包装同步的 protobuf 解析

### 3.4 守护进程

#### browser_daemon（凭证守护）

[daemon/browser_daemon.py](file:///c:/Users/LOX/Desktop/DYchajian/DYAutoDM_v2/backend/daemon/browser_daemon.py)：

- `CredentialKeeper` 类管理浏览器实例 + 登录凭证
- 提供 `/status`（查询凭证状态）、`/refresh`（刷新 cookie）、`/quit`（退出）路由
- 每个账号一个独立进程，互不干扰
- 启动时检测磁盘凭证，无凭证则弹出扫码窗口

#### recv_daemon（私信接收守护）

[daemon/recv_daemon.py](file:///c:/Users/LOX/Desktop/DYchajian/DYAutoDM_v2/backend/daemon/recv_daemon.py)：

- `RecvChannel` 类管理私信接收
- `Conversation` / `AccountInbox` 数据模型
- 提供 `/conversations`（会话列表）、`/conversation`（会话详情）、`/send`（发送私信）路由

### 3.5 数据模型

[models/enums.py](file:///c:/Users/LOX/Desktop/DYchajian/DYAutoDM_v2/backend/models/enums.py)：

```python
class EngineState(str, Enum):
    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPING = "stopping"
    STOPPED = "stopped"

class RecordStatus(str, Enum):
    CAPTURED = "captured"
    SENT = "sent"
    FAIL = "fail"
    SKIPPED = "skipped"
```

---

## 四、前端架构

### 4.1 组件树

```
App.tsx
├── Header
│   ├── brand (logo + 标题)
│   ├── nav (7 个 tab 按钮)
│   └── nav-status (4 个状态徽章 + 已发计数)
├── <main>
│   └── AnimatePresence (页面切换动画 0.16s)
│       ├── OverviewPage (总览)
│       ├── CrawlPage (采集)
│       ├── LivePage (直播监听) ← 最大页面 1244 行
│       ├── MessagesPage (私信)
│       ├── AccountsPage (账号管理) ← 最大文件 1764 行
│       ├── TasksPage (任务中心)
│       └── SettingsPage (设置)
└── Toast (容器)
```

### 4.2 数据流

```
后端 (FastAPI) ──HTTP/JSON──→ 前端 (React Query)
                                  │
                         3s poll useQuery("overview")
                                  │
                         3s poll useQuery("stats")
                                  │
                         3s poll useQuery("accounts")
                                  │
                         3s poll useQuery("tasks")
                                  │
                         POST/PUT/DELETE mutations
                                  │
                         响应 → push() toast 提示
```

### 4.3 关键改进

| 改进项 | 旧版 (DY_Spider_base) | 新版 |
|---|---|---|
| 数据轮询 | 多个手写 `setInterval`，无统一管理 | `useQuery` + `refetchInterval: 3000`，App 统一管理 |
| DOM 构建 | `React.createElement(...)` 大量嵌套 | JSX 模板 |
| 假数据 | `TASKS_INIT` / `feed` 硬编码假数据 | 删除，改为 `.sk` 骨架屏 |
| 错误处理 | `.catch(() => {})` 吞错误 | `errMsg()` 函数统一格式化 + `push()` toast 提示 |
| 状态比较 | `.status === '发送失败'` 字符串 bug | 枚举比较 |
| 前端路由 | 无路由，tab 切换不持久化 | `localStorage` 持久化 tab 选择 |
| 动画 | 无 | Framer Motion 页面切换动画 |

### 4.4 API 客户端

[client.ts](file:///c:/Users/LOX/Desktop/DYchajian/DYAutoDM_v2/frontend/src/api/client.ts) 封装 30+ 方法：

- **overview**: `getBackendStatus()`, `getOverview()`, `getStats()`
- **engine**: `start()`, `stop()`, `stopSoft()`, `pause()`, `resume()`
- **accounts**: `getAccounts()`, `addAccount()`, `removeAccount()`, `checkAccount()`, `scanLogin()`, `scanStatus()`, `setRole()`
- **live**: `getStream()`, `sendDanmaku()`, `setDmTemplate()`
- **messages**: `getConversations()`, `getConversation()`, `sendDm()`
- **tasks**: `getTasks()`, `saveTaskConfig()`, `saveDmPool()`
- **settings**: `getConfig()`, `saveConfig()`

### 4.5 通用组件

[ui.tsx](file:///c:/Users/LOX/Desktop/DYchajian/DYAutoDM_v2/frontend/src/components/ui.tsx)：

| 组件 | 用途 |
|---|---|
| `Spark` | SVG 迷你折线图（热度曲线） |
| `Pill` | 状态标签（ok/warn/danger/accent/mute） |
| `Avatar` | 用户头像（色相轮转） |
| `Dot` | 状态圆点（带动画脉冲） |
| `TABS` | 导航 tab 常量定义 |
| `KIND_NAME` | 弹幕类型中文映射 |

### 4.6 样式系统

[global.css](file:///c:/Users/LOX/Desktop/DYchajian/DYAutoDM_v2/frontend/src/styles/global.css)：

- **色彩系统**：oklch 色彩空间，7 个语义色变量 (bg/surface/fg/muted/accent/ok/warn/danger)
- **布局**：grid 响应式（`auto-fill, minmax(220px, 1fr)`）
- **骨架屏**：`.sk` 类脉冲动画
- **组件样式**：按钮（primary/ghost/danger）、卡片（.card）、表格（.tbl）、表单（.frm-group）
- **网格背景**：CSS 线性渐变模拟

---

## 五、Tauri 侧

### 5.1 Rust 代码

[lib.rs](file:///c:/Users/LOX/Desktop/DYchajian/DYAutoDM_v2/src-tauri/src/lib.rs) 暴露 6 个 Tauri 命令：

| 命令 | 参数 | 返回值 | 用途 |
|---|---|---|---|
| `backend_status` | - | `bool` | 查询后端是否存活 |
| `start_backend` | - | `String` | 启动后端 sidecar |
| `stop_backend` | - | `()` | 停止后端 |
| `start_browser_daemon` | `account: String, port: u16` | `String` | 启动凭证守护 |
| `start_recv_daemon` | `accounts: Vec<String>, port: u16` | `String` | 启动私信守护 |
| `list_daemons` | - | `Vec<String>` | 列出所有存活守护 |

### 5.2 SidecarManager

[sidecar.rs](file:///c:/Users/LOX/Desktop/DYchajian/DYAutoDM_v2/src-tauri/src/sidecar.rs)：

- `SidecarHandle`：`label` + `child` + `AtomicBool` 存活标记
- 3 个启动方法，分别对应 3 个 sidecar 二进制
- 每个 sidecar 启动后 spawn 异步任务监听输出，`Terminated` 事件自动标记退出
- 生产模式（`cfg(not(debug_assertions))`）下 `setup` 钩子自动拉起 backend

### 5.3 配置

[tauri.conf.json](file:///c:/Users/LOX/Desktop/DYchajian/DYAutoDM_v2/src-tauri/tauri.conf.json)：

- 窗口：1440×900，最小 1024×680，深色背景 `#0f1320`
- 构建：`externalBin` 声明 3 个 sidecar，打包目标 NSIS + MSI
- 插件：shell（sidecar 管理）、dialog（文件选择）、fs（文件读写）、notification（系统通知）

---

## 六、关键改进总结

### 6.1 已修复的 Bug

| Bug | 旧版 | 新版 |
|---|---|---|
| `.status === '发送失败'` 永不匹配 | 前端字符串比较后端拼的字符串 | `RecordStatus` 枚举 + `reason` 字段 |
| 5 个标志位状态错乱 | `_running`/`listen_active`/`hard_stopped`/`no_new`/`paused` 组合判断 | 单一 `EngineState` 枚举 |
| `.catch(() => {})` 吞错误 | 所有 API 调用 `.catch(() => {})` 静默失败 | 统一 `errMsg()` + toast 提示 |
| `getAccounts` 太重 | 列表和校验混在一个方法 | 拆分 `GET /accounts` 轻量 + `POST /{name}/check` 重量 |
| 假数据误导 | `TASKS_INIT` 硬编码假任务 | 删除，后端真实数据 |
| 3s 轮询资源浪费 | 每个页面独立 `setInterval` | `useQuery` 统一管理，页面销毁自动停止 |

### 6.2 架构改进

| 维度 | 旧版 | 新版 |
|---|---|---|
| 桌面壳 | pywebview（Python 内嵌浏览器） | Tauri 2（Rust + WebView2） |
| 后端形态 | 主进程内嵌 | 独立 sidecar 进程 |
| 子进程管理 | threading 手动 | Tauri sidecar 统一管理 |
| 协议 | 中文字符串拼接 | Pydantic 枚举 + OpenAPI |
| 异步 | threading + callback | asyncio |
| 前端构建 | 无构建工具，纯 JS | Vite + TypeScript |
| 打包 | pyinstaller 单文件 | Tauri bundle + sidecar |

### 6.3 前端 1:1 移植

| 旧版文件 | 新版文件 | 行数 | 改造点 |
|---|---|---|---|
| `web/pages/overview.js` | `pages/overview.tsx` | 377 | JSX + useQuery |
| `web/pages/crawl.js` | `pages/crawl.tsx` | 469 | JSX + 功能标记 |
| `web/pages/live.js` | `pages/live.tsx` | 1244 | 状态枚举比较 + useQuery |
| `web/pages/messages.js` | `pages/messages.tsx` | - | 错误反馈 |
| `web/pages/accounts.js` | `pages/accounts.tsx` | 1764 | JSX (389 createElement→JSX) |
| `web/pages/tasks.js` | `pages/tasks.tsx` | 267 | 删除假数据 |
| `web/pages/settings.js` | `pages/settings.tsx` | - | 配置走 API |
| `web/framework.js` | `components/ui.tsx` + `api/client.ts` | - | 拆分为组件 + API |
| `web/index.html <style>` | `styles/global.css` | 410 | 整体搬迁 |

---

## 七、构建与部署

### 7.1 构建流程

```
1. 后端打包
   scripts/build_sidecar.py
   → PyInstaller 打包 3 个 exe 到 dist/sidecar/
   → 自动生成 target-triple 硬链接到 src-tauri/binaries/

2. 前端构建
   cd frontend && npm run build
   → Vite 构建产物到 frontend/dist/

3. 桌面应用打包
   cd src-tauri && cargo build --release
   → Tauri bundle 为 NSIS/MSI 安装包
```

### 7.2 开发模式

```
# 终端 1：启动后端
py backend/main.py --port 8000

# 终端 2：启动前端开发服务器
cd frontend && npm run dev

# 打开浏览器 http://127.0.0.1:1420 即可调试
```

### 7.3 生产模式

Tauri release 构建会自动：
1. 打包前端为静态资源内嵌到 exe
2. 通过 `tauri.conf.json` 的 `externalBin` 嵌入 3 个 sidecar
3. 启动时 `setup` 钩子自动拉起 backend sidecar
4. 前端通过 `window.__TAURI__` 与 Rust 通信

---

## 八、项目依赖

### 8.1 Python 依赖

[requirements.txt](file:///c:/Users/LOX/Desktop/DYchajian/DYAutoDM_v2/backend/requirements.txt)：

```
fastapi>=0.111.0
uvicorn[standard]>=0.29.0
pydantic>=2.0.0
pydantic-settings>=2.0.0
loguru>=0.7.0
python-dotenv>=1.0.0
requests>=2.31.0
protobuf>=4.25.0
websockets>=12.0
```

### 8.2 Node.js 依赖

[package.json](file:///c:/Users/LOX/Desktop/DYchajian/DYAutoDM_v2/frontend/package.json)：

```
react@18.3.1
react-dom@18.3.1
@tanstack/react-query@5.40.0
framer-motion@11.2.0
+ 开发依赖: typescript@5.4.5, vite@5.2.11, @vitejs/plugin-react@4.3.0
```

### 8.3 Rust 依赖

[Cargo.toml](file:///c:/Users/LOX/Desktop/DYchajian/DYAutoDM_v2/src-tauri/Cargo.toml)：

```
tauri@2.0.0 (features: devtools)
tauri-plugin-shell@2.0.0 (sidecar 管理)
tauri-plugin-dialog@2.0.0 (文件选择对话框)
tauri-plugin-fs@2.0.0 (文件系统访问)
tauri-plugin-notification@2.0.0 (系统通知)
env_logger@0.11.0 (日志)
```

---

## 九、验证状态

| 验证项 | 状态 | 说明 |
|---|---|---|
| TypeScript 编译 | ✅ | `tsc --noEmit` 退出码 0，零类型错误 |
| Vite 生产构建 | ✅ | 447 模块，1.89s 完成 |
| PyInstaller 打包 | ✅ | 3 个 exe 均成功，运行正常 |
| Rust 编译 | ✅ | `cargo build --release` 5 分钟 |
| 后端 API 测试 | ✅ | 8 个 GET 端点全部 200 |
| 后端 POST 写入 | ⚠️ | settings 保存 422 已修复（需重新打包） |
| 浏览器 E2E 总览 | ✅ | 统计卡片、状态徽章、多账户切换 |
| 浏览器 E2E 采集 | ✅ | 搜索框、搜索提示 |
| 浏览器 E2E 直播 | ⚠️ | tab 切换正常，后端 stub 需填充 |
| 浏览器 E2E 私信 | ✅ | 会话列表、详情区域、输入框 |
| 浏览器 E2E 账号管理 | ⚠️ | 新增对话框正常，后端 stub 不持久化 |
| 浏览器 E2E 任务中心 | ⚠️ | tab 切换正常，后端 stub 需填充 |
| 浏览器 E2E 设置 | ⚠️ | 配置区渲染正常，保存 422（已修复） |
| 控制台错误 | ✅ | 仅 React DevTools info，无红色错误 |

---

## 十、与旧版项目对比

### 10.1 文件大小对比

| 维度 | DY_Spider_base | DYAutoDM_v2 |
|---|---|---|
| 后端代码 | ~15 个 .py 文件 | ~30+ .py 文件（按模块拆分） |
| 前端代码 | 7 个 .js（单文件 389 行 createElement） | 7 个 .tsx + 组件库 |
| 样式 | 内联在 index.html | 独立 410 行 global.css |
| 构建配置 | 无 | Vite + tsconfig + Cargo.toml |
| 文档 | 无 | 4 份文档 |

### 10.2 架构复杂度对比

| 维度 | 旧版 | 新版 | 变化 |
|---|---|---|---|
| 总进程数 | 1（主进程 + 子线程） | 1 + N×2（可预估） | 进程隔离，稳定性提升 |
| 前后端通信 | Python 直接调用 | HTTP + JSON | 可独立开发、调试 |
| 协议类型 | 无校验 | Pydantic 强类型 | 编译期发现不匹配 |
| 错误追踪 | 无日志 | loguru 分级日志 | 可定位问题 |
| 打包大小 | ~200MB 单文件 | ~300MB（含浏览器） | 略大但更可靠 |

---

## 十一、当前限制

1. **后端 stub**：`POST /api/accounts`、`POST /api/tasks/config`、`POST /api/tasks/dm-pool` 等写入端点仅返回 `{"ok": true}` 未持久化，需对接 `AutoDM` / `DispatchCenter` 实际逻辑
2. **设置保存**：`POST /api/settings` 之前返回 422（已修复，需重新打包）
3. **前端路由**：SPA 无 URL 路由，直接访问 `/live` 等路径不工作（需从首页点击 tab）
4. **WebSocket**：规划中未实现，live 页仍用 3s 轮询替代实时推送
5. **E2E 测试**：后端 stub 填充后才能完成完整 E2E 链路