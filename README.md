# FlowCap — DYAutoDM v2

抖音直播间自动私信控制台。Tauri 2 桌面应用：Tauri 2（Rust 壳）+ React 18 / TypeScript / Vite + FastAPI（PyInstaller sidecar）+ SQLite。

**当前版本：0.47.24**

完整文档见 [`DYAutoDM_v2/README.md`](DYAutoDM_v2/README.md)，权威架构说明见 [`DYAutoDM_v2/docs/架构与业务逻辑全解.md`](DYAutoDM_v2/docs/架构与业务逻辑全解.md)。

---

## 仓库结构

```
DYAutoDM_v2/
├── src-tauri/    Tauri Rust 壳（窗口 + SidecarManager）
├── frontend/     Vite + React 18 + TypeScript（11 页面 / 14 组件）
├── backend/      FastAPI（114 个 .py / 34,961 行 / 157 端点）
├── scripts/      构建与验证脚本（门禁 / 打包 / 版本同步）
├── docs/         项目文档与 ADR
├── vendor/       第三方上游副本（**不在本仓库**，见 NOTICE.md）
├── tests_integration/
├── package.json · package-lock.json · dev.ps1 · start_dev.ps1
├── 项目说明.md · README.md
└── .gitignore
```

## 快速开始

```bash
cd DYAutoDM_v2

# 后端（FastAPI，端口 8000）
cd backend && pip install -r requirements.txt && python -m backend.main --port 8000

# 前端（Vite dev server，端口 1420）
cd frontend && npm install && npm run dev

# 桌面壳（集成模式）
npx tauri dev
```

前置工具：Node.js ≥ 20、Python ≥ 3.12、Rust / cargo、PyInstaller ≥ 6.10。

## 测试

```bash
cd DYAutoDM_v2

# 前端门禁（tsc → vitest → vite build → chunk 扫描，共 4 阶段）
py -3 scripts/check_frontend_gate.py

# 铁律门禁（25 项：凭证 / 版本同步 / 数据契约 / 顺序依赖 …）
py -3 scripts/check_iron_rules.py
```

## 生产打包

> 🔴 正式安装包**必须**走 `python scripts/package_installer.py`，
> `npx tauri build` 单独跑会因缺契约门禁而出包不完整。

```bash
py -3 scripts/build_sidecar.py     # 3 个 Python sidecar → src-tauri/binaries/
npx tauri build
```

## 开发约定

- **测试环境**：`C:\temp\dyautodm_test`（绝不在源码仓库直接跑真机验证）
- **版本递增**：每次修改必 bump（`package.json` + `Cargo.toml` 两处同步）
- **凭证**：`.env` / `accounts/` / `data/*.db` 已被 `.gitignore` 排除，不得入库

## 文档入口

| 想了解 | 看哪份 |
|---|---|
| 架构与业务全解（推荐首读） | `DYAutoDM_v2/docs/架构与业务逻辑全解.md` |
| 项目定位 / 功能 / 目录 | `DYAutoDM_v2/项目说明.md` |
| 架构演进与设计取舍 | `DYAutoDM_v2/docs/architecture.md` |
| 设计决策记录（ADR） | `DYAutoDM_v2/docs/adr/` |
| API 端点规范 | `DYAutoDM_v2/docs/api_design.md` |
| 统一报错代码体系 | `DYAutoDM_v2/docs/报错代码体系.md` |

## 第三方依赖声明

本仓库**不包含** `DYAutoDM_v2/vendor/` 目录。它是运行时依赖但**未随仓库发布**
（原因与获取方式见 [`NOTICE.md`](NOTICE.md)）。

首次克隆后需要按 `NOTICE.md` 的说明手动获取 vendor，否则
`backend/auto_dm/login_api_vendor.py`（ADR-017 的 API 备用登录路径）不可用。
