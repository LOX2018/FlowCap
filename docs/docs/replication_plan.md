# better-douyin 全盘复刻映射（设计契约）

- **分支**：`design/better-douyin`
- **日期**：2026-09-14
- **蓝本**：`anYuJia/better-douyin` v1.1.18（二进制情报见 `arch_better_douyin.md`）
- **口径**：以蓝本的**架构分层 / 子系统切分 / 接口面 / 安全模型**为契约，逐子系统映射落地。
  闭源内核（Rust 签名、EC 实现细节）**不在可复刻范围**（拿不到，且本项目已有更强实现）。

---

## 零、两条并列主线（用户 2026-09-14 定调）

> **「性能和功能都很重要，纳入规划重点」** —— 性能与功能均为规划重点，不可偏废。

| 主线 | 目标 | 本次范围 |
|---|---|---|
| **① 功能复刻** | 对齐蓝本的架构与接口面（MCP / 媒体代理 / 自动化） | `backend/mcp/`、`backend/services/media_proxy.py` |
| **② 性能优化** | 体积 / 启动 / 内存 / 资源复用 | 三份 `_internal` 合并共享（已实测可行） |

---

## 一·甲、性能基线（本次实测，非估计）

### 现状体积（`src-tauri/binaries/`）

| 项 | 体积 |
|---|---|
| `flowcap-backend-<triple>/`（exe + `_internal`） | **282 MB** |
| `flowcap-browser-daemon-<triple>/` | **282 MB** |
| `flowcap-recv-daemon-<triple>/` | **282 MB** |
| **三份合计** | **846 MB** |
| （另有遗留 onefile `.exe` 各 114.6MB，属旧形态） | 344 MB |

### 实测：三份 `_internal` 内容完全相同

```
backend  files= 6514 size= 246.3MB
browser  files= 6514 size= 246.3MB
recv     files= 6514 size= 246.3MB
三者交集 6514 / 并集 6514 / 各自独有 0
```

- 抽样 43 个文件做 sha256：**42 个完全一致**；唯一差异是 `base_library.zip`
- 追查 `base_library.zip`：解包 155 个内部条目逐个 sha256 → **0 个内容不同**
  ⇒ 差异仅为 zip 容器时间戳元数据，**功能等价**

### 实测：共享 `_internal` 可正常启动（决定性验证）

用 NTFS junction 挂接共享目录，构造两种布局实测：

| 布局 | 结果 |
|---|---|
| `exe` 在 `<full>/` 目录内 + 同级 junction `_internal` | ✅ 依赖加载成功（走到参数解析） |
| **三个 exe 平铺** + 共享一个 `_internal` | ✅ 三个全部依赖加载成功 |

**关键结论**：平铺布局正好是现有代码**已支持的候选路径**
（`resolve_sidecar` 候选 2 `<root>/<full>.exe`；`main.py` / `daemon_launcher` / `accounts.py` 同）
⇒ **零 Rust 改动、零 Python 改动**即可落地。

### 预期收益

| 项 | 前 | 后 |
|---|---|---|
| 部署体积 | 846 MB | **约 318 MB**（246 共享 + 3×24 exe） |
| **净省** | — | **约 528 MB** |
| 附带 | 3 份重复依赖 | 1 份，**升级/查错时只对一处** |

### 启动性能（已有成果，来自 `工作记忆/01`）

| 模式 | BCC sidecar 启动耗时 |
|---|---|
| onefile（解包 114MB 到 %TEMP%） | **13.24 s** |
| onedir（免解压） | **1.10 s** |

⇒ **约 12× 提速**；本次共享化**不改变**该收益（仍为 onedir 形态）。

---

## 一、蓝本架构 → 本项目落点

| 蓝本（Rust） | 职责 | 本项目落点 | 状态 |
|---|---|---|---|
| `src/api/client*.rs`（16 模块） | 平台接口 | `backend/dy_apis/` + `backend/builder/` | ✅ 已有 |
| `src/commands/*.rs`（18 模块） | 命令边界 | `backend/api/*.py`（159 路由） | ✅ 已有 |
| `src/downloader/*.rs`（11 模块） | 下载流水线 | 无（非本项目业务） | ⛔ 不涉及 |
| `src/im_listener.rs` | IM 长连接 | `backend/daemon/recv_daemon.py` | ✅ 已有 |
| `src/automation.rs` | 自动化执行器 | `backend/auto_dm/` + `api/engine.py` | ✅ 已有 |
| **`src/mcp.rs`** | **MCP 权限/令牌/日志** | **`backend/mcp/`** | 🔨 **本次复刻** |
| **`src/bin/douyin-dl.rs`** | **MCP 独立入口（stdio）** | **`backend/mcp/__main__.py`** | 🔨 **本次复刻** |
| **`src/media_proxy_cache.rs`** | **媒体代理+缓存** | **`backend/services/media_proxy.py`** | 🔨 **本次复刻** |
| `src/media_utils_download_items.rs` | 媒体条目 | `backend/auto_dm/origin_image_resolver.py` | ✅ 已有 |
| `src/downloader/request_policy.rs` | 请求策略 | `backend/services/browser_gate.py` | ✅ 已有 |
| `src/history/mod.rs` | 历史/状态 | `backend/tasks_history.py` | ✅ 已有 |

---

## 二、子系统 1：MCP 层（对标 `mcp.rs` + `douyin-dl.rs`）

### 蓝本设计（二进制实测）

```
传输:     本机 HTTP，127.0.0.1，首选端口 preferred_port
安全:     Bearer Token；regenerate_mcp_token 后旧票立即失效
权限:     allow_write_actions=false 默认只读
          require_confirmation=true  → 写操作双闸
          close_prompt_shown / log_retention 配置项
日志:     脱敏——只记工具名、字段摘要、耗时、错误码
入口:     独立二进制 douyin-dl，子命令 serve
          仅暴露 stdio（"Other automation entrypoints intentionally not exposed"）
协议:     MCP JSON-RPC，protocolVersion 2025-03-26
```

### 复刻设计

| 项 | 落地 |
|---|---|
| 工具注册表 | `backend/mcp/registry.py` —— 声明式登记工具（名/描述/只读或写/参数 schema/后端路由） |
| 权限与令牌 | `backend/mcp/config.py` —— `enabled/preferred_port/allow_write_actions/require_confirmation/token/log_retention` |
| 确认闸 | 写工具默认挂起 → `confirm(token)` 放行，单次有效 |
| 审计日志 | `backend/mcp/audit.py` —— 脱敏结构化记录，环形保留 `log_retention` 条 |
| HTTP 服务 | `backend/mcp/server.py` —— 127.0.0.1 + Bearer 校验 + 端口自增探测 |
| stdio 入口 | `backend/mcp/__main__.py` —— `python -m backend.mcp serve`（对齐蓝本单入口） |
| 挂载 | `backend/main.py` 注册 `/api/mcp/*` 管理面（状态/令牌轮换/启停） |

### 工具面（由 159 路由派生，按风险分级）

- **只读（READ）**：`overview/*`、`messages/conversations`、`messages/conversation`、
  `ai/replies`(GET)、`ai/leads`(GET)、`tasks/history`、`tasks/current`、
  `accounts/*`(GET)、`logs/*`(GET)、`live/status`、`errcodes/*`
- **写（WRITE，需确认）**：`messages/send`、`messages/send_image`、`messages/wp_send`、
  `messages/request`、`ai/start`、`ai/stop`、`engine/*`、`crawl/*`、
  `linkmic/*`、`notify/command`、`accounts/{name}/scan` 等

---

## 三、子系统 2：媒体代理层（对标 `media_proxy_cache.rs`）

### 蓝本设计

```
把「加密取回 → 本地解密 → 喂前端」收敛为单一 proxy；
前端只拿明文 URL（契约 mediaProxyUrl()）；
带 cache（cacheable image body）、aes_chunk_size 分块。
```

### 本项目现状（实测）

AES-256-GCM 解密逻辑**散落在 7 个文件**，各有一份 `_decrypt`：
```
backend/api/messages.py:430
backend/auto_dm/conversation_capture.py:374,378
backend/auto_dm/origin_image_resolver.py:206,210,222,225
... 等共 7 处
```

### 复刻设计

| 项 | 落地 |
|---|---|
| 单一解密实现 | `backend/services/media_proxy.py` —— `decrypt_image(cipher, skey)` 唯一副本 |
| 缓存 | 同模块 LRU + 磁盘缓存（对齐 cacheable） |
| 对外 URL 契约 | `/api/messages/origin_image/{filename}`（已存在，收敛为走 proxy） |
| 迁移 | 7 处旧 `_decrypt` 改为调用 proxy（保持向后兼容，不删原函数签名） |

---

## 四、明确不复刻

| 项 | 原因 |
|---|---|
| `/aweme/v1/web/im/user/info/` 主动拉昵称 | 🔴 **风控红线**（用户铁律：仅 BCC 被动 hook） |
| 自算 `a_bogus` 复刻 | 本项目 `ab_pure.py` 已有，无收益 |
| 注入式 signer probe | 与 BCC 容器路线冲突 |
| 下载流水线（11 模块） | 非本项目业务范围 |

---

## 五、验收标准

1. **MCP**：`python -m backend.mcp serve` 可启动；Bearer 校验生效；只读工具免确认、
   写工具被闸；令牌轮换后旧票失效；审计日志脱敏。
2. **媒体代理**：解密实现收敛为 1 处（可 `grep` 计数验收）。
3. **不回归**：既有 159 路由不变；前端契约不变。

---

*落地代码见 `backend/mcp/` 与 `backend/services/media_proxy.py`。*
