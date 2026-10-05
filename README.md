# FlowCap v2

抖音直播间自动私信控制台 —— Tauri 2 桌面应用。

**当前版本：0.41.1**

---

## 一、架构

采用 **Tauri 2（Rust 壳）+ React 18 / TypeScript / Vite + FastAPI + PyInstaller Sidecar + SQLite**：

```
┌────────────────────────────────────────────────────────────────┐
│  Tauri Desktop App（Rust 壳，窗口 1280×720）                   │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │ Frontend：Vite + React 18 + TS + framer-motion           │  │
│  │  - 9 个主 tab（TABS）+ 2 个子页（AI 知识库 / IM 通知）    │  │
│  │  - 28 个 .tsx / 11 个页面 / 14 个组件                    │  │
│  │  - 双主题（day / aerospace）+ 可换主色 + 玻璃材质族      │  │
│  │  - 手写 fetch 客户端 client.ts（157 端点）              │  │
│  │  - @tanstack/react-query 统一轮询                        │  │
│  └───────────────────────┬──────────────────────────────────┘  │
│                          │ HTTP 127.0.0.1:8000                 │
│  ┌───────────────────────▼──────────────────────────────────┐  │
│  │ Backend：FastAPI（PyInstaller 打包为 sidecar）           │  │
│  │  - 16 个路由模块 / 157 个端点                            │  │
│  │  - 五大业务域：私信收发 · 直播监听+连麦 · AI 获客回复     │  │
│  │    · IM 通知网关 · 会员体系+统一配置中心                  │  │
│  │  - services/ 为业务实现层（dm_dispatch / ai_reply / kb…） │  │
│  └───────────────────────┬──────────────────────────────────┘  │
│                          │ subprocess（每账号懒加载）           │
│  ┌───────────────────────▼──────────────────────────────────┐  │
│  │ Sidecar 守护进程（Rust SidecarManager 统一管理）         │  │
│  │  - browser_daemon（BCC 常驻浏览器容器 / 凭证保活）        │  │
│  │  - recv_daemon（私信接收）                                │  │
│  └──────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────┘
```

**端口段**（`zlib.crc32` + salt 稳定哈希，见 `backend/auto_dm/accounts.py`）：

| 用途 | 段位 |
|---|---|
| backend | `8000` |
| browser_daemon（BCC） | `[10000, 11999]` |
| recv_daemon | `[12000, 13999]` |

---

## 二、代码规模（2026-09-11 实测）

| 项 | 数量 |
|---|---|
| backend Python | **114 个 `.py` / 34,961 行** |
| API 路由模块 | 16 个 / **157 端点**（156 HTTP + 1 WebSocket） |
| 前端 `.tsx` | 28 个（11 页面 + 14 组件 + App/main/ThemeContext） |
| 前端页面行数合计 | 8,490 行 |
| 样式 | `global.css` 884 行 + `theme-glass.css` 1,110 行 |
| Tauri 命令 | 9 个 |
| 统一配置中心 | 5 分区 / 45 字段 |

---

## 三、目录结构

```
FlowCap/
├── src-tauri/                  # Tauri Rust 壳（窗口 + Sidecar 管理）
│   ├── Cargo.toml
│   ├── tauri.conf.json         # 窗口 / 打包 / externalBin
│   ├── capabilities/           # Tauri 2 权限声明
│   ├── binaries/               # 3 个带 triple 后缀的 sidecar exe（构建产物）
│   └── src/
│       ├── main.rs
│       ├── lib.rs              # 主逻辑 + 9 个命令注册
│       └── sidecar.rs          # SidecarManager（启动/停止/kill_tree）
│
├── frontend/                   # Vite + React 18 + TypeScript
│   └── src/
│       ├── App.tsx             # 路由 + 启动自检 + 会员门禁
│       ├── api/
│       │   ├── client.ts       # 手写 fetch 客户端（BASE = 127.0.0.1:8000）
│       │   └── sidecar.ts      # Tauri 侧通信 + ensureBackendReady
│       ├── components/         # 14 个组件（TopNav / GlassButton / ui.tsx …）
│       ├── pages/              # 11 个页面
│       ├── theme/              # ThemeContext.tsx + accents.ts
│       └── styles/             # global.css + theme-glass.css
│
├── backend/                    # FastAPI 后端（114 .py / 34,961 行）
│   ├── main.py                 # 入口 / lifespan / 16 个 router 注册
│   ├── api/                    # 路由层（16 模块）
│   ├── core/                   # 引擎骨架（auto_dm / dispatch / live_hook / sender）
│   ├── services/               # 业务实现层（15 模块：dm_dispatch / ai_reply / app_config…）
│   ├── daemon/                 # browser_daemon / recv_daemon / wp_recv
│   ├── dy_apis/                # 抖音接口（douyin_api / image_sender / login_api…）
│   ├── auto_dm/                # 账号与浏览器（accounts / vbrowser / daemon_launcher…）
│   ├── notify/                 # IM 通知网关（gateway / channels / inbound…）
│   ├── models/ · builder/ · utils/ · dy_live/ · static/
│   ├── database.py             # SQLite 封装（WAL + JSON 迁移）
│   └── requirements.txt
│
├── scripts/                    # 构建与验证脚本（build_sidecar.py / gen_api_client.sh …）
└── docs/                       # 项目文档
    ├── 架构与业务逻辑全解.md     # 权威架构文档（推荐首读）
    ├── 项目说明.md · README.md
    ├── architecture.md · api_design.md · project_analysis.md
    ├── migration_guide.md      # 历史迁移记录（已完结）
    ├── upload_flow.md · frontend-style-port.md · 报错代码体系.md
    └── 未提交改动核查.md · _架构测绘原始数据.md
```

---

## 四、开发环境前置要求

| 工具 | 版本（本机实测） | 用途 |
|---|---|---|
| Node.js | v24.18.1 | 前端 + Tauri CLI |
| npm | 12.0.2 | 包管理 |
| Python | 3.14.6 | 后端 |
| Rust / cargo | 见 `C:\Users\LOX\.cargo\bin` | Tauri 壳编译 |
| PyInstaller | ≥ 6.10 | backend 打包为 sidecar |

> 本机真实 Python 路径：`C:\Users\LOX\AppData\Local\Programs\Python\Python314\python.exe`（Store 别名不可用）。

---

## 五、快速开始

### 1. 后端（源码态）

```bash
cd backend
pip install -r requirements.txt
python -m backend.main --port 8000
```

### 2. 前端

```bash
cd frontend
npm install
npm run dev          # http://localhost:1420
```

### 3. 桌面壳（集成模式）

```bash
# 仓库根目录
npm install
npx tauri dev
```

---

## 六、生产打包

> 🔴 **正式安装包（MSI/NSIS）必须走 `python scripts/package_installer.py`**（见 `项目说明.md §7.0`）。
> `npx tauri build` 单独跑只会**按 `tauri.conf.json` 的 targets 出包**，且没有契约门禁与出包自证；
> 其根因（`externalBin` 不带 contents 目录 / WiX 剥下划线 / MSI 装只读位置）曾导致装机必卡门禁。
> 校验：`python scripts/check_packaging_contract.py`（无需构建）。



```bash
# 1. 打包 3 个 Python sidecar → src-tauri/binaries/
"C:\Users\LOX\AppData\Local\Programs\Python\Python314\python.exe" scripts/build_sidecar.py

# 2. 打包 Tauri 应用（先确保 cargo 在 PATH）
export PATH="$PATH:/c/Users/LOX/.cargo/bin"
npx tauri build
```

**仅改后端时**：只需重跑 `build_sidecar.py`，把 3 个 exe 复制到部署目录 `C:\temp\flowcap_test\`（先走 `/quit` 优雅停旧进程防占用），**不必**完整 tauri build。
**改了前端或 `src-tauri/src/*.rs`**：必须完整 `npx tauri build` 重嵌。

---

## 七、测试与部署约定

- **测试目录**：`C:\temp\flowcap_test\FlowCap_<版本>.exe` + 同目录 3 个 daemon exe + `binaries/`。
- **测试阶段只打前后端并部署，不打 NSIS/MSI 安装包**（`tauri build --no-bundle`）；仅正式发布才需安装包。
- **所有真机测试 / 浏览器激活 / 运行验证一律在 `C:\temp\flowcap_test` 进行**，绝不在源码仓库直接跑。
- 启动 BCC / backend / recv_daemon 必须带 `FLOWCAP_APP_ROOT=C:\temp\flowcap_test`（frozen 态 exe 在 `binaries/` 时 `app_root` 会错位，报「无 .env」）。

---

## 八、文档入口

| 想了解 | 看哪份 |
|---|---|
| **权威架构与业务全解** | `docs/架构与业务逻辑全解.md` |
| 项目定位 / 功能 / 目录 | `项目说明.md` |
| 架构演进与设计取舍 | `docs/architecture.md` |
| API 命名与端点规范 | `docs/api_design.md` |
| 图片发送上传链路 | `docs/upload_flow.md` |
| 统一报错代码体系 | `docs/报错代码体系.md` |
| 从 V1 迁移的历史记录 | `docs/migration_guide.md`（已完成，仅供追溯） |
