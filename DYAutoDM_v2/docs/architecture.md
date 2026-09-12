# 架构说明

> 配套文档：业务链路与功能域见 [`项目说明.md`](../项目说明.md)；
> 完整架构与业务全解见 [`架构与业务逻辑全解.md`](架构与业务逻辑全解.md)。
> 本文聚焦**架构演进与关键设计取舍**，数字均为 2026-09-11 实测。

---

## 一、整体架构

```
┌────────────────────────────────────────────────────────────────┐
│  Tauri Desktop App（Rust 壳，1280×720）                        │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │ Frontend (Vite + React 18 + TS)                          │  │
│  │  - 9 主 tab + 2 子页（kb / notify）                      │  │
│  │  - @tanstack/react-query 统一管理轮询（3s/5s/15s/30s）    │  │
│  │  - 手写 fetch 客户端 api/client.ts（157 端点）            │  │
│  │  - 双主题玻璃体系（theme/ + theme-glass.css）             │  │
│  └───────────────────────┬──────────────────────────────────┘  │
│                          │ HTTP 127.0.0.1:8000                 │
│  ┌───────────────────────▼──────────────────────────────────┐  │
│  │ Backend (FastAPI, PyInstaller 打包为 sidecar)            │  │
│  │  - 16 路由模块 / 157 端点                                │  │
│  │  - core/ 引擎骨架 + services/ 业务实现层                 │  │
│  │  - 双通道私信发送（ws ⇄ wp 自动降级）                    │  │
│  └───────────────────────┬──────────────────────────────────┘  │
│                          │ Tauri shell sidecar（懒加载）        │
│  ┌───────────────────────▼──────────────────────────────────┐  │
│  │ Sidecar 子进程（Rust SidecarManager 统一管理）           │  │
│  │  - dyautodm-browser-daemon（每账号，BCC 浏览器容器）      │  │
│  │  - dyautodm-recv-daemon（每账号，私信接收）               │  │
│  └──────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────┘
```

---

## 二、自 V1（DY_Spider_base）以来的架构演进

### 1. 协议层：中文字符串 → Pydantic 枚举

```python
# V1：后端拼字符串，前端永远不匹配
status = f"发送失败({reason})"      # 后端
r.status === '发送失败'             # 前端 —— 永不相等

# V2：backend/models/enums.py
class RecordStatus(str, Enum):
    CAPTURED = "captured"
    SENT = "sent"
    FAIL = "fail"                   # 原因放 rec.reason
```

### 2. 状态机：5 个标志位 → 单一 enum

V1 用 `_running` / `listen_active` / `hard_stopped` / `no_new` / `paused` 互相覆盖判断。
V2 收敛为 `EngineState`（`backend/models/enums.py`），唯一真源 `self.state`：

```
IDLE → STARTING → RUNNING ⇄ PAUSED
                     ↘ STOPPING → STOPPED → IDLE
```

### 3. 通信：手写 setInterval → React Query

- React Query 统一管理轮询间隔、缓存、错误重试（现存间隔：3s / 5s / 15s / 30s）。
- `getAccounts` 拆分「轻量列表」+「重量级校验」两个端点。
- 前端 tab 选择用 `localStorage` 持久化。

### 4. 守护进程：subprocess.Popen → Tauri sidecar

- Rust `SidecarManager` 统一管理生命周期（启动 / 停止 / `kill_tree` 递归清理）。
- 仍用稳定哈希分配端口，但改由 Rust 端统一拉起，崩溃可被感知。
- **v0.40 起改为懒加载**：按账号并发去重锁 + 冷静期按需拉起，不再启动即全量。

### 5. 发送通道：单通道硬分支 → 双通道自动降级

| | 早期 | 0.41.1 |
|---|---|---|
| 发送通道 | 单通道硬分支 | **双通道 + 自动降级**（ws ⇄ wp 互备） |
| 发送调度 | 直接发送 | 会话整理池 + **per-account 串行调度器** |
| 成功判据 | 受理即返回 | **同步等至最多 90s 到 `done`** |

### 6. 错误处理：catch 吞错误 → 全链路日志

- 前端：统一 `errMsg()` 格式化 + `push()` toast。
- 后端：loguru + **统一报错代码体系**（`backend/errcode.py`，如 `ACC-004` / `DB-005` / `SYS-015`）。
- Tauri：sidecar stdout/stderr 转 log。

### 7. 新增模块（V1 完全没有）

| 模块 | 起始版本 | 说明 |
|---|---|---|
| 统一配置中心 | v0.38.0 | `services/app_config.py`，5 分区 / 45 字段，apply 三分类 |
| 配置标签 | v0.38.2 | 按标签差异化覆盖账号参数 |
| 模型链路中心 | v0.38.4 | AI 与 IM 通知共用的模型唯一真源 |
| IM 通知网关 | v0.38.5 | QQ 官方机器人 / iLink 入站 + 授权审批 + 权限组 |
| AI 获客回复 | — | 54 端点；双知识库 reply_kb / pro_kb |
| 会员体系 | v0.37.0 | 多会员分库隔离 + 本地 AES 加密凭证 |
| 直播连麦 | — | `api/linkmic.py` + `daemon/browser_daemon` `/linkmic_run` |
| 图片收发 | 2026-09 | `image_sender` 上传链路 + `origin_image_resolver` 解密 |

---

## 三、模块职责

| 模块 | 职责 |
|---|---|
| `src-tauri/src/lib.rs` | Tauri 入口 + **9 个命令**注册 |
| `src-tauri/src/sidecar.rs` | Sidecar 生命周期管理（启动 / 停止 / `kill_tree`） |
| `frontend/src/App.tsx` | 路由 + 启动自检 + 会员门禁 |
| `frontend/src/api/client.ts` | 手写 fetch API 客户端（157 端点） |
| `frontend/src/components/ui.tsx` | 通用组件（`Spark/Pill/Avatar/Dot`）+ `TABS` 常量 |
| `frontend/src/theme/` | 主题引擎（`ThemeContext.tsx` + `accents.ts`，`applyAccentToDom`） |
| `frontend/src/pages/*` | 11 个页面 |
| `backend/main.py` | FastAPI 入口 + lifespan + 16 router 注册 |
| `backend/api/*` | 路由层（16 模块，157 端点） |
| `backend/core/auto_dm.py` | AutoDM 主控（`EngineState` 状态机） |
| `backend/core/dispatch.py` | DispatchCenter 延迟队列（asyncio.PriorityQueue） |
| `backend/core/live_hook.py` | 弹幕 WS 监听 |
| `backend/core/sender.py` | 发送器 |
| `backend/services/dm_dispatch.py` | **双通道发送调度**（含自动降级、串行调度器） |
| `backend/services/app_config.py` | 统一配置中心（5 分区 / 45 字段） |
| `backend/services/ai_reply.py` · `ai_agent.py` | AI 获客回复 + Agent 模版 |
| `backend/services/reply_kb.py` · `pro_kb.py` | 双知识库（命中即回 / RAG 注入） |
| `backend/services/member_store.py` · `member_ctx.py` | 会员分库与上下文 |
| `backend/services/config_tag.py` | 配置标签（账号级差异化覆盖） |
| `backend/daemon/browser_daemon.py` | BCC 浏览器容器 / 凭证保活 |
| `backend/daemon/recv_daemon.py` | 私信接收守护 |
| `backend/daemon/wp_recv.py` | wp 通道接收 |
| `backend/notify/*` | IM 通知网关（gateway / channels / cmd_parser / inbound / notifier / events） |
| `backend/dy_apis/douyin_api.py` | 抖音私信接口（imapi protobuf 签名鉴权） |
| `backend/dy_apis/image_sender.py` | 图片发送上传链路 |
| `backend/dy_apis/login_api.py` | 扫码登录抓凭证 |
| `backend/auto_dm/accounts.py` | 账号管理 + 端口稳定哈希 |
| `backend/auto_dm/daemon_launcher.py` | 守护懒加载（并发去重锁 + 冷静期） |
| `backend/auto_dm/origin_image_resolver.py` | 原图解密（AES-256-GCM） |
| `backend/vbrowser.py` | 指纹浏览器真实实现 |

> 注：`backend/auto_dm/vbrowser.py` 只是 `from vbrowser import ...` 的重导出 stub；
> 真实实现在顶层 `backend/vbrowser.py`。新增符号必须同步在 stub 中重导出。

---

## 四、关键架构事实（已验证，避免重复踩坑）

1. **端口用 `zlib.crc32` 而非 `hash()`**：`hash()` 受 `PYTHONHASHSEED` 影响跨进程随机，会导致 web_bridge 算出的端口与 subprocess 拉起的守护算出的端口不一致。
2. **加 salt 打破同步撞车**：早期 browser / recv 用同一 crc32 值仅换 base，导致「同一对账号在两段同时撞车」。加 salt 使两段哈希输入不同。
   **span 已从 500 扩到 2000**（2026-09-06 治理），20 账号碰撞率由约 **32%** 降至约 **9.1%**（生日悖论，仍受 span 限制；50 账号约 46%）。
3. **`app_root()` frozen 态处理**：frozen（PyInstaller onefile）返回 `dirname(sys.executable)`；若父目录是 `binaries/`/`bin/` 则上溯一级，否则 `vb_chromium` 会拼成 `binaries/vb_chromium` 找不到。启动 BCC / backend / recv_daemon 必须带 `DY_APP_ROOT`。
4. **`NO_PROXY=*` 隔离**：后端进程禁用代理，`DY_PROXY` 只进浏览器子进程。
5. **PyInstaller onefile 下 `from main import app` 不可靠**：改为启动即 `bind_adm(app.state.adm)` 显式绑定引擎实例给通知模块。
6. **私信 IM 私有网关**（`imapi.douyin.com`）靠 protobuf body 内 ticket / ts_sign / sdk_cert 签名鉴权，**不叠加** `www.douyin.com` 的 `bd-ticket-guard-*` 头。
7. **消息方向只能用 sender UID 判断**，不可用 `aweType` / 类型推断；sender 空 = me（自动欢迎语）。
8. **昵称唯一来源是 BCC 被动 hook**（复用账号常驻浏览器截前端自发 `im/user/info`），**绝不**做后端批量查昵称（风控红线）。

---

## 五、当前限制 / 待办

1. **直播页仍为 5s 轮询**：后端 `@router.websocket("/ws")`（`/api/live/ws`）已实现弹幕推送，但前端 `live.tsx` 尚未接入 WebSocket，仍用 `refetchInterval: 5000`。
2. **视频发送未实现**：`image_sender.py` 仅支持图片（`aweType=2702`），视频（2703/2704）需要额外 `duration` 等参数，尚未实现。
3. **护栏文件保留**：`frontend/src/components/GlassButton.tsx`、`GlassSeg.tsx`、`GlassSwitch.tsx` 当前零引用（`theme-glass.css` 中材质已生效，组件封装待接入）。
4. **测试阶段不打安装包**：`tauri build --no-bundle`；NSIS/MSI 仅在正式发布时打。
