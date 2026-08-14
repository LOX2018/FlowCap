# 迁移指南（DY_Spider_base → DYAutoDM_v2）

## 总原则

1. **业务逻辑直接搬运**：抖音 API 调用、签名算法、protobuf 解析等几乎原样迁移
2. **协议层重写**：所有前后端通信改用 Pydantic 模型 + OpenAPI 自动生成 TS 类型
3. **前端 1:1 视觉移植**：CSS 整体搬迁，组件结构保持一致，仅 `createElement` → JSX
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
| `auto_dm/run.py` | `backend/core/auto_dm.py` | 5 标志位 → 单一 enum；async |
| `auto_dm/core.py` | `backend/core/dispatch.py` | threading → asyncio；status 枚举化 |
| `auto_dm/live_hook.py` | `backend/core/live_hook.py` | threading → asyncio |
| `auto_dm/sender.py` | `backend/core/sender.py` | async |
| `auto_dm/accounts.py` | `backend/services/account_service.py` | 保留端口哈希；守护管理移交 Tauri |
| `auto_dm/browser_daemon.py` | `backend/daemon/browser_daemon.py` | 改 FastAPI；启动阻塞改后台 |
| `auto_dm/recv_daemon.py` | `backend/daemon/recv_daemon.py` | 改 FastAPI |
| `auto_dm/config.py` | `backend/config.py` | pydantic-settings；JSON 持久化 |

### 重写（协议层）

| 旧 | 新 | 说明 |
|---|---|---|
| `web_bridge.py` WebBridge 类 (34 方法) | `backend/api/*.py` (7 路由) | 按业务域分组 |
| 中文字符串协议 | `backend/models/*.py` Pydantic | OpenAPI 自动生成 TS |
| `web/framework.js` ApiBridge | `frontend/src/api/client.ts` | openapi-fetch 强类型 |

### 前端移植

| 旧 | 新 | 改造点 |
|---|---|---|
| `web/index.html` 的 `<style>` 块 | `frontend/src/styles/global.css` | 整体搬迁，class 名不变 |
| `web/pages/overview.js` | `frontend/src/pages/overview.tsx` | createElement → JSX；useQuery |
| `web/pages/crawl.js` | `frontend/src/pages/crawl.tsx` | 同上 |
| `web/pages/live.js` | `frontend/src/pages/live.tsx` | 同上 + WebSocket 替代轮询 |
| `web/pages/messages.js` | `frontend/src/pages/messages.tsx` | 同上 |
| `web/pages/accounts.js` | `frontend/src/pages/accounts.tsx` | 同上 + 修复 status 字段 bug |
| `web/pages/tasks.js` | `frontend/src/pages/tasks.tsx` | 同上 + 删除本地模拟 |
| `web/pages/settings.js` | `frontend/src/pages/settings.tsx` | 同上 |

## 迁移步骤（建议顺序）

### 阶段 1：业务核心迁移（无 UI）

1. 复制 `dy_apis/` `builder/` `utils/` `static/` 到 `backend/`
2. 迁移 `core/auto_dm.py`：5 标志位 → enum，threading → async
3. 迁移 `core/dispatch.py`：队列逻辑保留，status 枚举化
4. 迁移 `core/live_hook.py`：WS 监听改 async
5. 迁移 `core/sender.py`：私信发送改 async
6. 单元测试：能跑通"收弹幕 → 入队 → 发送"

### 阶段 2：守护进程迁移

1. 迁移 `daemon/browser_daemon.py`：改 FastAPI 路由
2. 迁移 `daemon/recv_daemon.py`：改 FastAPI 路由
3. 用 `python scripts/build_sidecar.py` 打包
4. 测试 Tauri sidecar 启停

### 阶段 3：API 层

1. 实现 `api/accounts.py`：迁移 verify_account + scanLogin
2. 实现 `api/engine.py`：迁移 start/stop/pause
3. 实现 `api/live.py`：迁移 getLiveStream + WebSocket 推送
4. 跑 `bash scripts/gen_api_client.sh` 生成前端类型

### 阶段 4：前端移植

1. 把 `web/index.html` 的 CSS 整体搬到 `frontend/src/styles/global.css`
2. 逐页迁移（建议顺序：overview → accounts → tasks → live → msg → crawl → settings）
3. 每页迁移步骤：
   - 把 createElement 结构转 JSX（可借 AI 辅助）
   - 把 `window.ApiBridge.xxx()` 替换为 `api.xxx()` 或 `useQuery`
   - 把 `.catch(() => {})` 替换为带 toast 的错误处理
   - 修复原版 bug（如 live.js 的 status 比较）

### 阶段 5：集成测试

1. `npm run tauri:dev` 完整启动
2. 逐页验证功能
3. 对比旧版行为，确认无回归
4. 打包 `npm run tauri:build`

## 关键 Bug 修复清单

迁移过程中**必须**修复的旧版 bug（详见 [项目分析报告.md](../../DY_Spider_base/项目分析报告.md)）：

- [ ] **live.js status 比较**：`r.status === '发送失败'` 永不匹配 → 改用枚举 + `reason` 字段
- [ ] **前端 catch 吞错误**：所有 `.catch(() => {})` 加 toast 反馈
- [ ] **scanLogin fire-and-forget**：新增 `/scan-status` 接口，前端轮询它
- [ ] **scanLogin 与 daemon 写 .env 竞态**：加文件锁或 IPC 协调
- [ ] **is_running 与 UI 不一致**：用单一 enum 状态机
- [ ] **配置写回字符串匹配**：改 JSON 文件持久化
- [ ] **browser_daemon 启动阻塞**：refresh 移到后台任务
- [ ] **useEffect 清理不彻底**：React Query 自动管理
- [ ] **tasks.js 本地模拟数据**：删除，改用真实数据

## 工具链安装

```bash
# Node.js (≥20) + Rust (≥1.75) + Python (≥3.10) 前置

# 前端依赖
cd frontend && npm install

# 后端依赖
cd backend && pip install -r requirements.txt

# Tauri CLI（已在根 package.json devDependencies）
cd .. && npm install

# 验证
npm run tauri:dev   # 应打开窗口
```
