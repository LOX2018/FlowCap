# 架构说明

## 整体架构

```
┌──────────────────────────────────────────────────────────────┐
│  Tauri Desktop App（Rust 壳）                                │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ Frontend (Vite + React + TS)                           │  │
│  │  - 7 页面: overview/crawl/live/msg/accounts/tasks/settings │
│  │  - React Query 管理轮询（替代手写 setInterval）        │  │
│  │  - openapi-fetch 强类型客户端                          │  │
│  └──────────────────────┬─────────────────────────────────┘  │
│                         │ HTTP + WebSocket                    │
│  ┌──────────────────────▼─────────────────────────────────┐  │
│  │ Backend (FastAPI, PyInstaller 打包为 sidecar)          │  │
│  │  - Pydantic 强类型协议                                 │  │
│  │  - AutoDM 单一 enum 状态机                             │  │
│  │  - DispatchCenter 延迟队列 (asyncio)                   │  │
│  │  - LiveChatHook 弹幕 WS                                │  │
│  └──────────────────────┬─────────────────────────────────┘  │
│                         │ Tauri shell sidecar                 │
│  ┌──────────────────────▼─────────────────────────────────┐  │
│  │ Sidecar 子进程 (Rust SidecarManager 统一管理)          │  │
│  │  - dyautodm-browser-daemon (每账号)                    │  │
│  │  - dyautodm-recv-daemon (每账号)                       │  │
│  └────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────┘
```

## 关键改进点（对比 DY_Spider_base）

### 1. 协议层：中文字符串 → Pydantic 枚举

**旧版**：
```python
# 后端
status = f"发送失败({reason})"  # 拼字符串
# 前端
r.status === '发送失败'  # 永远不匹配
```

**新版**：
```python
# 后端 enums.py
class RecordStatus(str, Enum):
    CAPTURED = "captured"
    SENT = "sent"
    FAIL = "fail"  # 原因放 rec.reason

# 前端 schema.d.ts (自动生成)
type RecordStatus = 'captured' | 'sent' | 'fail'

# 比较
if (record.status === 'fail') { /* 显示 record.reason */ }
```

### 2. 状态机：5 个标志位 → 单一 enum

**旧版**：`_running` / `listen_active` / `hard_stopped` / `no_new` / `paused` 互相覆盖

**新版**：
```
IDLE → STARTING → RUNNING ⇄ PAUSED
                        ↘ STOPPING → STOPPED → IDLE
```

### 3. 通信：3s 轮询 → React Query + WebSocket

- React Query 自动管理轮询间隔、缓存、错误重试
- 直播弹幕用 WebSocket 推送，替代 `getLiveStream` 2s 轮询
- `getAccounts` 拆分轻量 list + 重量级 verify

### 4. 守护进程：subprocess.Popen → Tauri sidecar

- Rust `SidecarManager` 统一管理生命周期（启动/停止/重启/存活检测）
- 不再依赖端口哈希探活（仍用哈希分配端口，但管理更稳定）
- 进程崩溃可被 Rust 端感知并重启

### 5. 错误处理：catch 吞错误 → 全链路日志

- 前端：React Query 全局错误回调 + UI toast
- 后端：loguru 统一日志，每个路由 try/except 记录
- Tauri：sidecar stdout/stderr 转 log

## 模块职责

| 模块 | 职责 | 迁移自 |
|---|---|---|
| `src-tauri/src/lib.rs` | Tauri 入口 + 命令注册 | (新) |
| `src-tauri/src/sidecar.rs` | Sidecar 生命周期管理 | (新，替代 subprocess.Popen) |
| `frontend/src/App.tsx` | 路由 + 全局状态 | web/app.js |
| `frontend/src/api/client.ts` | API 客户端 | web/framework.js ApiBridge |
| `frontend/src/pages/*` | 7 个业务页面 | web/pages/*.js |
| `backend/main.py` | FastAPI 入口 | web_bridge.py WebBridge |
| `backend/api/*` | 7 个路由模块 | web_bridge.py 各方法 |
| `backend/models/*` | Pydantic 协议 | (新，替代中文字符串) |
| `backend/core/auto_dm.py` | AutoDM 主控 | run.py AutoDM |
| `backend/core/dispatch.py` | 延迟队列 | core.py DispatchCenter |
| `backend/core/live_hook.py` | 弹幕 WS 监听 | live_hook.py |
| `backend/core/sender.py` | 私信发送 | sender.py |
| `backend/daemon/browser_daemon.py` | 凭证守护 | browser_daemon.py |
| `backend/daemon/recv_daemon.py` | 私信接收守护 | recv_daemon.py |
| `backend/services/account_service.py` | 账号管理 | accounts.py |
