# 迁移记录（DY_Spider_base → FlowCap）

> ## ⚠️ 历史文档说明
>
> **本迁移早已完成**，`DY_Spider_base`（V1）不再作为活跃代码库。本文仅作**追溯用途**，
> 记录当初的迁移映射与修复清单，**不是当前架构说明**——当前的目录结构、模块职责与端点清单请以
> [`架构与业务逻辑全解.md`](架构与业务逻辑全解.md)、[`项目说明.md`](../项目说明.md) 为准。
>
> 下文表格中的「新位置」为**迁移当时**的目标路径；其中若干模块此后已被重构或改名（见文末「与本文件的差异」）。

## 总原则

1. **业务逻辑直接搬运**：抖音 API 调用、签名算法、protobuf 解析近乎原样迁移
2. **协议层重写**：前后端通信改用 Pydantic 模型 + 枚举，替代 V1 的中文字符串协议
3. **前端 1:1 视觉移植**：CSS 整体搬迁，class 名保持不变，仅 `createElement` → JSX
4. **守护进程保留逻辑**：`browser_daemon` / `recv_daemon` 改为 Tauri sidecar

## 文件迁移映射

### 直接迁移（业务无改动）

| 旧文件 | 新位置 | 说明 |
|---|---|---|
| `dy_apis/*` | `backend/dy_apis/*` | 抖音 API 封装，原样 |
| `builder/*` | `backend/builder/*` | 签名/参数构建，原样 |
| `utils/*` | `backend/utils/*` | 工具函数，原样 |
| `static/*` | `backend/static/*` | protobuf 文件，原样 |

### 改造迁移（保留逻辑，改组织）

| 旧文件 | 新位置 | 改造点 |
|---|---|---|
| `auto_dm/run.py` | `backend/core/auto_dm.py` | 5 标志位 → 单一 enum；threading → async |
| `auto_dm/core.py` | `backend/core/dispatch.py` | threading → asyncio；status 枚举化 |
| `auto_dm/live_hook.py` | `backend/core/live_hook.py` | threading → asyncio |
| `auto_dm/sender.py` | `backend/core/sender.py` | async |
| `auto_dm/browser_daemon.py` | `backend/daemon/browser_daemon.py` | 改 FastAPI；启动阻塞改后台 |
| `auto_dm/recv_daemon.py` | `backend/daemon/recv_daemon.py` | 改 FastAPI |

### 重写（协议层）

| 旧 | 新 | 说明 |
|---|---|---|
| `web_bridge.py` WebBridge 类（34 方法） | `backend/api/*.py` | 按业务域拆分为 16 个路由模块 |
| 中文字符串协议 | `backend/models/*.py` Pydantic | 状态枚举化，杜绝字符串比较 |
| `web/framework.js` ApiBridge | `frontend/src/api/client.ts` | 手写 fetch 客户端（**非** openapi-fetch） |

### 前端移植

| 旧 | 新 | 改造点 |
|---|---|---|
| `web/index.html` 的 `<style>` 块 | `frontend/src/styles/global.css` | 整体搬迁，class 名不变 |
| `web/pages/*.js` | `frontend/src/pages/*.tsx` | createElement → JSX；接 React Query |

## 关键 Bug 修复清单（迁移期，均已处理）

- [x] **`live.js` status 比较**：`r.status === '发送失败'` 永不匹配 → 改用枚举 + `reason` 字段
- [x] **前端 catch 吞错误**：`.catch(() => {})` 改为统一 `errMsg()` + toast
- [x] **scanLogin fire-and-forget**：新增 `/scan-status` 接口供前端轮询
- [x] **`is_running` 与 UI 不一致**：收敛为 `EngineState` 单 enum 状态机
- [x] **配置写回字符串匹配**：改 JSON / SQLite `kv_store` 持久化
- [x] **`browser_daemon` 启动阻塞**：refresh 移到后台任务
- [x] **`useEffect` 清理不彻底**：交 React Query 管理
- [x] **`tasks.js` 本地模拟数据**：删除，改用真实数据

## 与本文件的差异（迁移后发生的重构，勿按上表找文件）

| 上表「新位置」 | 当前实际 | 说明 |
|---|---|---|
| `backend/services/account_service.py` | **已被 `backend/auto_dm/accounts.py` 取代；2026-09-28 已删除（A-6 孤儿模块清理，git 历史可恢复）** | 原为零调用方的迁移遗留（自身 docstring 已注明），端口 salt 算法在 `auto_dm/accounts.py`；两者曾**必须对齐否则端口错位**，删除后端口实现单一 SSOT = `auto_dm/accounts.py` |
| `backend/config.py` | **仍在使用**（39 处引用） | pydantic-settings 全局配置（端口段/路径/默认参数），与 `services/app_config.py` 的统一配置中心**并存且分工不同**：前者是进程级设置，后者是用户可改的 5 分区 / 45 字段业务配置 |
| `backend/api/*`（7 路由） | 现为 **16 个路由模块 / 157 端点** | 迁移后新增 ai / notify / member / model_hub / linkmic / live_config / errcodes 等 |

## 工具链（当前有效）

```bash
# 前端依赖
cd frontend && npm install

# 后端依赖
cd backend && pip install -r requirements.txt

# 根目录（Tauri CLI 在根 package.json）
npm install

# 开发
npm run tauri:dev          # 桌面壳 + 前端
npm run backend:dev        # 仅后端（uvicorn --reload）

# 打包
npm run build:sidecar      # 3 个 Python sidecar
npm run tauri:build        # 完整桌面应用
```
