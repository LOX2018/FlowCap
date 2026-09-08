# 设置页重构与统一配置中心 实施方案

> **For Hermes:** 用 subagent-driven-development 逐 task 实施；每 task 完成后两阶段评审（规范符合 → 代码质量）。

**Goal:** 把 DYAutoDM_v2 分散在 7 个承载层的配置收敛为「设置页 + 统一配置中心」两层，用户在一个页面内管理所有可统一管理的参数，改完按标注热生效或重启对应守护。

**Architecture:** 新增 `backend/services/app_config.py` 作为唯一配置中心（单 key `app_config` 存 SQLite `kv_store`，按 section 分节，schema 驱动默认值/取值范围/生效方式）。`api/settings.py` 从空壳改造成配置网关，提供 `GET/POST /api/settings` 读写 + `/api/settings/schema` 下发表单元数据（含每个字段的默认值、范围、生效方式）。前端设置页由 schema 驱动渲染，从 3 个分区扩到 7 个。各模块把「模块级 `os.environ.get` 常量」改为「启动读 env → 运行时读配置中心」两级取值。

**Tech Stack:** FastAPI + Pydantic + SQLite kv_store（`database.get_kv_json/set_kv_json`）、React + react-query、Tauri2 sidecar 部署。

---

## 〇、实测现状（决定性事实，动手前必读）

### 0.1 承载层清点（2026-09-08 全仓扫描）

| # | 承载层 | 内容 | 现状 |
|---|---|---|---|
| 1 | `api/settings.py` | 路由存在 | **空壳**：GET 返回 `{"config":{}}`；POST 只打日志「持久化 TODO」 |
| 2 | 前端 `settings.tsx` | 7 个字段 | 实际走 `/api/tasks`（getTasks + saveTaskConfig），**不走 settings 路由** |
| 3 | SQLite `kv_store["config"]` | 任务配置落盘 | 由 `api/tasks.py:171` 写入，是事实上的配置库 |
| 4 | `backend/config.py` | pydantic Settings | 端口范围/图片/图床字段有定义，**前端无入口** |
| 5 | 44 个环境变量 | 风控/捕获/探活参数 | **零 UI**，改值需改环境并重启进程 |
| 6 | `auto_dm/config.py` | 旧基座遗留硬编码 | `LIVE_POLL_INTERVAL=30`、`WS_HEARTBEAT_INTERVAL=300`、`MAX_TARGET=3`（与 settings 双份） |
| 7 | 功能页内嵌配置 | live.tsx / ai.tsx / accounts.tsx | live 页有第二套「保存配置/保存词库」；AI 页有 30+ 配置字段；账号页有 per-account 代理 |

### 0.2 关键事实（含本轮纠偏，勿再推翻）

- **`/api/settings` 无实际功能**：`api/settings.py` 全文 23 行，POST 只有 TODO。前端 `client.ts:441/445` 的 `getConfig/saveConfig` 指向它但**设置页从不调用**——这是「统一配置层从未建立」的证据，不是设计克制。
- **live.tsx 与设置页配置重复**：live.tsx:1285 `saveConfig({liveUrl, maxTarget, interval, delay, dmPool})` + :1268 `saveDmPool(...)`，与 settings.tsx 的 `saveTaskConfig` 写同一批字段、同一个 `kv_store["config"]`。双入口会互相覆盖。
- **`dm_dispatch` 是活的**（本轮更正）：`core/dispatch.py:317` 函数内 `from services.dm_dispatch import get_dispatcher` 动态接入，`api/messages.py:580`、`services/ai_reply.py:1031` 也调 `submit`。**顶层 import 扫描会漏判**（正则是 `^import` 匹配不到函数内 import），因此它的 12 个 `DY_DM_*`/`DY_STRANGER_*`/`DY_WEIGHT_*` 参数**全在主发送链路上**，是风控核心参数，必须进设置页。
- **通知模块零 UI**：`/api/notify/config|status|test|command` 后端完整（渠道 CRUD、级别路由、测试推送，落 `data/notify_config.json`），前端代码 **0 处引用 notify**。这是「后端已就绪、只差 UI」的一块，投入产出比最高。
### 0.2 关键事实（含本轮纠偏，勿再推翻）
- **AI 配置在功能页且是全局单份**：`ai.tsx` 有「Agent 设定/知识库/留资线索/护栏/黑名单」5 个 Section，配置落 `kv_store["ai_reply_config"]`（`ai_reply.py:158` 的 `_KV_CONFIG`）。配置语义与数据视图混在同一页。
- **⚠️ AI 配置无账号维度（本轮实测实锤，本次需求的技术根因）**：
 - `get_config()`（`ai_reply.py:155`）读的是**单一全局 key** `ai_reply_config`，无 account 参数。
 - 但 worker `_tick()`（`ai_reply.py:806-826`）扫的是**全库** `dm_messages WHERE id>? AND role='them'`，**SQL 里没有 account 过滤**。
 - `_handle(row, cfg)`（:840）拿 `row["account"]` 决定发往哪个账号，但用的 `cfg` 仍是全局那一份。
 - **结论：多账号场景下，所有账号共用一个 Agent（同一模型、同一商家名、同一话术、同一知识库）**——这就是用户所说「配置紊乱」的实质。A 账号的客服话术会用在 B 账号的会话里。
 - 同理：知识库 `_KV_KB`（全局）、黑名单 `_KV_BL`（全局）、留资表 `ai_leads`（**有 account 列**）三者维度不一致——留资按账号分，知识和黑名单不按账号分。
- **发送闸门参数在 recv_daemon**：`DY_SEND_MIN_INTERVAL=8` / `DY_SEND_MAX_WAIT=30`（`daemon/recv_daemon.py:987-988`），是模块级常量，**进程启动时求值一次**，改完必须重启该账号 recv_daemon 才生效。

### 0.3 生效方式分类（UI 必须逐字段标注，否则用户改了以为生效）

- **热生效**（保存后下一次读取即生效）：AI 配置（`ai_reply.get_config()` 每次调）、通知配置（`notifier.configure()` 热更新）、任务配置（读 `settings` 单例 + kv）。
- **重启守护生效**：recv_daemon 的发送闸门、browser_daemon 的 cookie 同步/昵称缓存 TTL、uid_probe 的 TTL（模块级常量）。
- **重启 backend 生效**：`DY_BCC_ON_START`、`DY_AUTO_CAPTURE_ON_START`、端口范围、BCC 无头模式（vbrowser 启动参数）。

---

## 一、目标架构

```
前端 settings.tsx（schema 驱动渲染，7 分区）
        │  GET /api/settings/schema   下发表单元数据（默认/范围/生效方式/风险提示）
        │  GET/POST /api/settings     读写全量配置
        ▼
backend/services/app_config.py  ← 唯一配置中心
        │  kv_store["app_config"]（单 key，按 section 分节）
        │  优先级：配置中心值 > 环境变量 > schema 默认值
        ▼
消费方（两级取值）
   ① 模块级常量：启动时 env → 运行时读配置中心（热生效字段）
   ② 守护进程：backend 拉起时把该 section 序列化进子进程 env（重启生效字段）
```

**不做的事（YAGNI）**：
- 不做配置版本迁移/回滚系统（单 key 全量覆盖即可，schema 缺字段自动补默认）。
- 不把 `auto_dm/config.py` 的旧常量全部迁走（只迁有 UI 的，其余标 deprecated 注释）。

**新增（用户本轮明确要求）**：Agent ↔ 账号绑定体系（见 §二·甲），这是唯一要做的 per-account 配置维度。

---

## 二·甲、Agent ↔ 账号绑定设计（新增章节）

### 需求原话
> 「AI 配置移走后需要在 AI 页面加上绑定 Agent，设置页面中设定 agent 和账号关联绑定，免得配置紊乱」

### 核心问题（实测实锤，见 §0.2）
AI 配置当前是**全局单份**，但 worker 会处理**所有账号**的消息 → 多账号共用一个 Agent。

### 数据模型

```python
# kv_store["ai_agents"]  —— Agent 档案库（多份）
{
  "agents": {
    "<agent_id>": {
      "id": "ag_xxx",
      "name": "工伤咨询-标准版",      # 用户可读名
      "note": "用于工伤类账号",
      "config": { ...完整 ai_reply 配置字段... },   # 30+ 字段
      "created_at": 1757...,
      "updated_at": 1757...
    }
  }
}

# kv_store["ai_bindings"]  —— 账号 → Agent 绑定表
{
  "default_agent_id": "ag_xxx",        # 未绑定账号的兜底
  "bindings": {
    "<account_name>": "ag_yyy"         # 账号 → agent_id
  }
}
```

**迁移策略（保证不破坏现有数据）**：
- 现有 `kv_store["ai_reply_config"]` 原样保留，**自动迁移为一个名为「默认 Agent」的档案**，并设为 `default_agent_id`。
- 所有未显式绑定的账号 → 落到 `default_agent_id`。
- 因此单账号用户**感知零变化**，多账号用户才需要去绑定。

### 后端改动（`services/ai_reply.py`）

| 函数 | 改动 |
|---|---|
| `get_config()` (:155) | **保持签名不变**（向后兼容，仍返回全局默认），内部改为返回 `default_agent_id` 对应档案 |
| `get_agent(agent_id)` | 新增：按 id 取档案 |
| `get_config_for(account)` | **新增**：账号 → agent_id → 档案；未绑定则回落默认。**worker 改调它** |
| `list_agents()` / `save_agent(id, cfg)` / `delete_agent(id)` | 新增：Agent CRUD |
| `get_bindings()` / `bind_account(account, agent_id)` / `unbind_account(account)` | 新增：绑定管理 |
| `_tick()` (:806) | **关键**：`cfg = get_config()` → 在 `_handle` 内按 `row["account"]` 取对应配置。**不能整 tick 取一份** |
| `_handle(row, cfg)` (:840) | 签名改为 `_handle(row)`，内部 `cfg = get_config_for(row["account"])` |

⚠️ **实施铁律**：`_tick` 一次取 20 行可能横跨多个账号，**必须在循环内逐行取配置**，不能在循环外取一次。改错等于没改。

⚠️ **知识库/黑名单维度**：`_KV_KB`/`_KV_BL` 当前全局。本轮**先不动**（改动面大），在 Agent 档案里预留 `kb_scope` 字段占位，后续按需扩展。UI 上标注「知识库与黑名单当前为全局共享」。

### 新增 API（`api/ai.py`）

```
GET    /api/ai/agents                 列出全部 Agent 档案（id/name/note/updated_at）
POST   /api/ai/agents                 新建 Agent（body: {name, note, config, copy_from?}）
PUT    /api/ai/agents/{id}            更新 Agent 配置
DELETE /api/ai/agents/{id}            删除（若被绑定则拒绝，提示先解绑）
GET    /api/ai/bindings               绑定表 + 账号列表 + 各账号当前生效 Agent
POST   /api/ai/bindings               绑定（body: {account, agent_id}）
DELETE /api/ai/bindings/{account}     解绑（回落到默认 Agent）
```

### 前端改动

**设置页 → AI 获客分区**（`sections/AiSection.tsx`）新增两小块：
1. **Agent 档案列表**：卡片式，增/删/改/复制；选中一个编辑其 30+ 配置字段
2. **账号绑定表**：左侧账号名，右侧下拉选 Agent（选项 = 全部档案 + 「默认」），保存即调 `/api/ai/bindings`
   - 显示每账号「当前生效：xxx（来源：显式绑定 / 默认）」

**AI 页**（`ai.tsx`）顶部新增：
1. **绑定概览条**（可折叠）：一次性展示「账号 → 生效 Agent」全表 + 「去设置页管理」跳转按钮
2. 各数据区（留资线索/黑名单）显示时按账号分组或加账号列，让用户能看出数据属于哪个 Agent
3. 保留：知识库、留资线索、黑名单

---

## 二、设置页目标分区（7 区）

| 分区 | 字段来源 | 生效方式 |
|---|---|---|
| **通用 / 启动** | force_rescan；BCC 随启动拉起（`DY_BCC_ON_START`）；无头模式（`DY_BCC_HEADLESS_MODE`）；启动自动捕获（`DY_AUTO_CAPTURE_ON_START`） | backend 重启 |
| **直播监听** | liveUrl；每场上限；私信间隔；延迟抖动；私信词库；未开播轮询间隔（`LIVE_POLL_INTERVAL`）；WS 心跳间隔（`WS_HEARTBEAT_INTERVAL`）；弹幕/控制台/发送三开关 | 热生效（下次启动监听读取） |
| **私信发送与风控** | 发送闸门间隔/等待上限（`DY_SEND_MIN_INTERVAL/MAX_WAIT`）；陌生人分钟/日限（`DY_STRANGER_PER_MINUTE/DAY`）；冷却 `DY_DM_COOLDOWN_FREQ/MAX`；权重半衰期/原谅期；去重窗口 `DY_DM_DEDUP_WINDOW`；队列上限 `DY_DM_QUEUE_MAX`；词库严格模式；UID 沉淀池冷却/严格 | 重启 recv_daemon（标注） |
| **捕获与存储** | 历史条数/并发/滑动间隔（`DY_HISTORY_*`）；昵称缓存 TTL；UID 探活 TTL（OK/FAIL/锁等待）；图片内联阈值/TTL/容量上限；图床选择与 token | 混合（逐字段标注） |
| **通知** | 渠道 CRUD + 级别路由 + 测试推送（接现成 `/api/notify/*`） | 热生效 |
| **AI 获客** | 从 ai.tsx 迁入：Agent 档案（模型/视觉模型/Agent 设定/语义检索/护栏）+ **账号绑定表**（见 §二·甲） | 热生效 |
| **IM 消息与 Bot** | Bot 总开关；渠道→账号绑定；6 类意图权限；6 类任务信息推送开关；默认发送通道 ws/wp（见 §二·乙） | 热生效 |
| **独立账号** | 现状保留（账号只读信息 + 跳账号页配代理）+ 账号级发送通道覆盖 | — |

> 分区数因此为 **8 区**（原 7 区 + IM 消息与 Bot）。

---

## 二·乙、IM 消息收发与 Bot 配置（新增章节）

### 需求原话
> 「目前 IM 消息收发在前端没有任何配置页面，在设置中主要设定 bot 绑定账号、可操作权限、收发任务信息的开关」
> 「IM bot 对于任务启停的设定是**无法通过 UID 绑定**的，需要根据 github 原项目的设定来规范绑定权限，IM bot 绑定 UID 是为了**规范对 UID 任务的筛选，别乱窜 UID**」
> 「层级应该是 **agent 绑定账号交由调度器处理，会话加上 UID 交给沉淀池**就行」（2026-09-08 架构定稿）

### ⚠️ 概念澄清（用户 2026-09-08 纠正，此前理解错误已作废）

> 「IM bot 对于任务启停的设定是**无法通过 UID 绑定**的，需要根据 github 原项目的设定来规范绑定权限，IM bot 绑定 UID 是为了**规范对 UID 任务的筛选，别乱窜 UID**」

**原项目规范**（AstrBot，`https://github.com/AstrBotDevs/AstrBot`，AGPL-3.0，40k stars）：
- 会话标识是 **UMO（unified_msg_origin）**，格式 `platform_name:message_type:session_id`
  （源码 `astr_message_event.py`：`@property unified_msg_origin -> str(self.session)`）
- `platform_name` = 平台 id（如 `aiocqhttp` / `wecom` / `dingtalk`）；`message_type` = `FRIEND_MESSAGE` / `GROUP_MESSAGE`；`session_id` = 平台侧会话 id（群号或用户 id）
- **AstrBot 用 UMO 绑定"会话"，不是绑定"账号"**：`conversation_mgr.py` 里 `session_conversations[unified_msg_origin]`、`delete_conversations_by_user_id(unified_msg_origin)` 全部以 UMO 为键
- 权限维度是 AstrBot 的 **`role`（`member` / `admin`）**，与 UID 无关
  （`astr_message_event.py`：`self.role = "member"` —「用户是否是管理员」）

**因此本项目正确拆分为三件事，此前混为一谈：**

| 维度 | 绑什么 | 用途 | 归属 |
|---|---|---|---|
| **会话绑定（权限载体）** | **UMO**（`platform:msgtype:session_id`） | 决定"谁在发指令、能执行什么" | **Bot 守卫** |
| **Agent → 账号** | 抖音账号名 | Agent 用哪个身份发 | **调度器 `DmDispatcher`** |
| **会话 + UID** | `(account, peer_uid)` | 去重 / 冷却 / 解析真实对端 | **沉淀池 `UidSink` / `ConvPool`** |

**❌ 错误做法（已作废）**：
1. 用抖音 UID 做任务启停的权限依据 —— UID 是对端客户标识，不是操作者身份，用它做权限既不安全也语义错乱。
2. **在 Bot 层做 UID 名单过滤** —— UID 与会话本就有归属（沉淀池/整理池），Bot 再插一层是层级跑偏（用户 2026-09-08 指出）。

### 现状实测（三处缺口，均为零 UI + 零持久化）

**① 发送通道 `channel` 无持久化**
- `api/messages.py:44` `channel: str = "ws"` 是**请求级字段**，每次发送由前端传入，默认 `ws`
- 前端 `messages.tsx:1166-1175` 有一组单选（ws/wp），但**不保存**，刷新即回默认
- 现有双通道 + 自动降级已实现（`api/messages.py:550-635`：`ws` 失败回退 `wp`，`wp` 失败回退 `ws`）
- **缺口**：没有「账号级默认通道」——想让某账号固定走 WP，目前做不到

**② Bot 绑定账号：不存在**
- `notify/cmd_parser.py` 已定义 7 种意图：`create_task` / `start_task` / `stop_task` / `query_status` / `recapture` / `help` / `unknown`
- 入口 `POST /api/notify/command`（`api/notify.py`）→ `parse_command()` → `_execute()`
- ⚠️ **`_execute` 直接操作全局唯一引擎实例 `_get_adm()`，没有任何账号维度、没有任何身份校验**——任何人能对任何渠道发「启动任务」指令并生效
- 实测：`_execute` 里 `start_task` / `stop_task` / `create_task` / `recapture` 全部无权限检查
- **缺口**：Bot 不知道自己属于哪个账号

**③ 权限与收发开关：不存在**
- 账号的 `roles.monitor` / `roles.sender` 存在（`accounts.py:663/671` `set_monitor` / `set_sender`，存 `kv_store["accounts_index"]`），但那是**直播监听用的监测/发送角色**，与 Bot 权限无关
- **缺口**：没有「Bot 能执行哪些意图」的权限表；没有「Bot 是否接收任务信息推送」的开关

### ✅ 层级定稿（用户 2026-09-08 拍板，此前「Bot 管 UID」的设计已作废）

```
Agent  ──绑定──> 账号        →  交给【调度器】DmDispatcher（per-account 队列/配额/闸门）
会话 + UID                   →  交给【沉淀池】UidSink / ConvPool（去重/冷却/整理）
Bot（UMO + role）            →  只管【指令权限】，不碰 UID
```

**实测依据（`services/dm_dispatch.py`）**：
- `DmDispatcher`（调度器）持有 `self.uid_sink`、`self._queues[account]`、`self._quotas[account]`（`AccountQuota`）→ **天然按账号隔离**，Agent 绑账号后由它调度是本征契合
- `UidSink`（沉淀池）以 **`(account, peer_uid)` 复合键** 持久化（`dm_uid_sink` 表，PRIMARY KEY(account, peer_uid)），`should_send(account, peer_uid)` / `mark_sent` / `mark_seen` 全按这个键
- `ConvPool`（会话整理池）以 **`(account, conv_id)`** 归一化解析 `peer_uid`，TTL 60s
- 现成入口：`submit(account, conv_id, text, ...)`（走 ConvPool 解析）、`submit_by_uid(account, peer_uid, text, ...)`（采集/监听直发）

**结论：UID 与会话的管控本来就有归属，Bot 不该再插一手。**
→ §二·乙 里 `uid_scope` / `notify/uid_scope.py` / `filter_uids` **全部撤销**。

---

### 数据模型（Bot 只管 UMO 权限）

```python
# kv_store["im_bot_config"]
{
  "enabled": False,

  # ===== ① 会话绑定（对齐 AstrBot UMO）—— 权限载体 =====
  "umo_bindings": {
    # UMO = "platform_name:message_type:session_id"，与 AstrBot 同格式
    "wecom:FRIEND_MESSAGE:<session_id>": {
      "role": "admin",            # admin | member（对齐 AstrBot role）
      "account": "工伤小助理",     # 该会话下发的任务交给哪个抖音账号
      "note": "老板的企微私聊"
    },
    "dingtalk:GROUP_MESSAGE:<群号>": {
      "role": "member",
      "account": "工伤小助理",
      "note": "运营群"
    }
  },
  "default_umo_role": "member",     # 未登记 UMO 的默认角色（最小权限）
  "allow_unregistered_umo": False,  # False=未登记 UMO 一律拒绝

  # ===== ② 意图权限（按 role 分级）=====
  "permissions": {
    "admin":  {"query_status": True,  "help": True,
               "start_task": True,  "stop_task": True,
               "create_task": True, "recapture": True},
    "member": {"query_status": True,  "help": True,
               "start_task": False, "stop_task": False,
               "create_task": False,"recapture": False}
  },

  # ===== ③ 任务信息推送开关 =====
  "notify_on": {
    "task_started": False, "task_finished": False, "task_failed": True,
    "credential_invalid": True, "risk_control": True, "lead_captured": False
  },

  "default_channel": "ws"     # 账号默认发送通道（ws/wp）
}
```

**职责边界（写死，防再次跑偏）**：
| 关注点 | 归属 | 实现位置 |
|---|---|---|
| Agent 用哪个账号发 | 调度器 | `DmDispatcher._queues[account]` / `_quotas[account]` |
| 同 UID 是否重复发 | 沉淀池 | `UidSink.should_send(account, peer_uid)`（`dm_uid_sink`） |
| 会话 → 真实 peer_uid | 整理池 | `ConvPool.resolve(account, conv_id)` |
| Bot 能不能启停任务 | Bot 守卫 | `bot_guard.check_command(umo, intent)` |

⚠️ **Bot 不得实现任何 UID 过滤**——它只产出「意图 + 目标账号」，UID 去重/冷却由沉淀池在 `submit*` 入口内部处理。

**危险意图处理**（`DESTRUCTIVE = {create_task, recapture}`，`cmd_parser.py:40`）：
- 即使 `role=admin` 且权限开启，**仍保留二次确认**（`need_confirm` + `confirmed`），权限只决定是否"能发起"，不绕过确认

### 后端改动

| 文件 | 改动 |
|---|---|
| 新增 `notify/umo.py` | `parse_umo(s) -> UMO`、`make_umo(platform, msgtype, sid)`；与 AstrBot 同格式（`platform:msgtype:session_id`） |
| 新增 `notify/bot_guard.py` | `check_command(umo, intent) -> (allowed, role, account, reason)`：① `enabled`；② UMO 已登记 / `allow_unregistered_umo`；③ `permissions[role][intent]` |
| `api/notify.py` `_execute` | **开头加闸**（任何引擎操作之前）：`check_command`；拿到 `account` 后交给调度器，**不做 UID 过滤** |
| `api/notify.py` `/config` | 配置模型扩展 `im_bot` 段 |
| `api/messages.py` | 发送时若请求未显式传 `channel`，按账号读 `im_bot_config.default_channel` |

⚠️ **实施铁律 1**：`_execute` 的权限检查必须在**任何引擎操作之前**，不能放在各意图分支里（会漏）。

⚠️ **实施铁律 2**：`_execute` 只输出「意图 + 账号」，**绝不实现 UID 名单过滤**。UID 重复/冷却交给 `UidSink`（`dm_dispatch.submit*` 内部已调用）。若发现自己在 Bot 层写 UID 过滤 = 层级跑偏，立即停手。

⚠️ **风控红线合规**：Bot 指令全部走 DYAutoDM 自有内部接口（`_execute` docstring 已声明"不新增抖音请求"），本改动不引入任何新的抖音 API 调用，不触碰昵称批量查询红线。

### 新增/扩展 API

```
GET  /api/notify/config          扩展返回 im_bot 段
POST /api/notify/config          扩展保存 im_bot 段
POST /api/notify/command         行为变更（UMO 权限闸），body 增可选 umo
GET  /api/notify/bot/accounts    可绑定账号列表（复用 /api/accounts）
```

### 前端改动（设置页新增「IM 消息与 Bot」分区）

1. **Bot 总开关** + 状态（运行中 / 未启用）
2. **会话绑定表（UMO）**：`UMO → role(admin/member) → 操作账号`，支持新增/编辑/删除
   - UMO 输入提供三个字段（平台 / 消息类型 / 会话ID）自动拼成 `platform:msgtype:sid`
   - 显示「未登记 UMO 一律拒绝」开关
3. **角色权限矩阵**：admin / member 两列 × 6 意图的开关，危险项标红
4. **任务信息收发开关**：6 类事件推送开关（凭证失效/风控默认开）
5. **默认发送通道**：ws / wp 单选

> ⚠️ **本分区不含 UID 配置**。UID 去重/冷却归沉淀池（`dm_uid_sink`），
> 会话→UID 整理归 `ConvPool`，Agent→账号调度归 `DmDispatcher`。
> 若有人往这里加 UID 名单 = 层级跑偏。

---

### 阶段 A：配置中心地基（后端，无 UI 变化）

#### Task A1: 新建 `backend/services/app_config.py`

**Files:** Create `backend/services/app_config.py`

写入 schema 驱动的配置中心，包含：
- `SECTIONS: dict[str, Section]` —— 每个 section 含字段定义：`key / label / type(int|float|bool|str|list) / default / min / max / options / apply（"hot"|"restart_daemon"|"restart_backend"）/ hint / risk`
- 初始值：section 结构按「二、目标分区」7 区建立，**字段先只登记不接线**（A 阶段先跑通读写）。
- `get(section, key)` / `get_section(name)` / `save_section(name, values)` / `get_all()` / `schema()`
- 取值优先级：配置中心 → `os.environ` → schema default
- 落盘：`database.get_kv_json("app_config", {})` / `set_kv_json(...)`

**Step 1（TDD）**：写 `backend/test_app_config.py`
```python
def test_default_when_empty():
    clear(); assert get("send", "min_interval") == 8.0

def test_save_then_get():
    save_section("send", {"min_interval": 12.0})
    assert get("send", "min_interval") == 12.0

def test_env_overrides_default():
    os.environ["DY_SEND_MIN_INTERVAL"] = "20"
    clear(); assert get("send", "min_interval") == 20.0

def test_config_overrides_env():
    os.environ["DY_SEND_MIN_INTERVAL"] = "20"
    save_section("send", {"min_interval": 15.0})
    assert get("send", "min_interval") == 15.0

def test_schema_exposes_apply_mode():
    assert schema()["send"]["fields"]["min_interval"]["apply"] == "restart_daemon"
```

**Step 2**：`cd C:\Users\LOX\Desktop\DYchajian\DYAutoDM_v2\backend && python -m pytest test_app_config.py -v` → 预期 5 fail（模块不存在）

**Step 3**：实现 `app_config.py`

**Step 4**：重跑 → 预期 5 passed

**Step 5**：`git add backend/services/app_config.py backend/test_app_config.py && git commit -m "feat(config): 统一配置中心 schema + kv 持久化"`

---

#### Task A2: `api/settings.py` 改为配置网关

**Files:** Modify `backend/api/settings.py`（全文 23 行，整体重写）

- `GET /api/settings` → 返回全量配置 + schema（`{"ok":true,"config":{...},"schema":{...}}`）
- `POST /api/settings` → 按 section 保存，返回 `{ok, saved_sections, restart_required:[...]}`；`restart_required` 由本次改动字段的 `apply` 去重得出
- `GET /api/settings/schema` → 只下发表单元数据（前端首屏用）
- `POST /api/settings/reset` → 清空某 section 回默认

**验证**：
```bash
curl -s http://127.0.0.1:8000/api/settings | python -c "import sys,json;d=json.load(sys.stdin);print(list(d['config'].keys()), list(d['schema'].keys()))"
curl -s -X POST http://127.0.0.1:8000/api/settings -H "Content-Type: application/json" -d '{"send":{"min_interval":12}}'
# 预期 {"ok":true,"saved_sections":["send"],"restart_required":["recv_daemon"]}
```

**注意**：`main.py` 的 `_MEMBER_EXEMPT` 免登录豁免需同时加 `/api/settings` 与 `/api/settings/` 两项（dev-guards §7d 踩过的坑，只加前者带路径参数的 GET 仍 401）。

**Commit**: `feat(api): settings 路由改为统一配置网关`

---

### 阶段 B：接线现有参数（后端，UI 后补）

#### Task B1: 发送闸门参数接线（recv_daemon）

**Files:** Modify `backend/daemon/recv_daemon.py:987-988`

现：`_SEND_GATE_MIN_INTERVAL = float(os.environ.get("DY_SEND_MIN_INTERVAL", "8") or 8)`
改：模块级兜底常量保留，新增取值函数：
```python
def gate_min_interval() -> float:
    try:
        from services.app_config import get
        return float(get("send", "min_interval"))
    except Exception:
        return _SEND_GATE_MIN_INTERVAL
```
在 `_send_gate_acquire` 内调用（**不是模块级常量**，这样配置改了下一次发送即生效，把「重启生效」降级为热生效）。

**验证**：改配置 → 连续发两条间隔验证（用非风控账号、`DY_DM_TEST_WHITELIST` 白名单，一次失败即停，勿连测）。

**Commit**: `feat(send): 发送闸门参数接入统一配置（热生效）`

---

#### Task B2: dm_dispatch 调度参数接线

**Files:** Modify `backend/services/dm_dispatch.py:69-197`

12 个模块级常量（`QUEUE_MAX`/`POOL_STRICT`/`DEDUP_WINDOW`/`STRANGER_PER_MINUTE`/`STRANGER_PER_DAY`/`COOLDOWN_ON_FREQUENT`/`COOLDOWN_MAX`/`WEIGHT_*`/`UID_SINK_*`）改为按需读配置中心的函数。

**风险**：这些是风控核心参数，改错直接导致限流或封号。**接线时默认值必须与现有 env 默认逐字一致**（200/true/5s/2/30/600/3600/21600/86400/604800/true），不得顺手调整。

**验证**：`python -c "import services.dm_dispatch as d; print(d.STRANGER_PER_MINUTE, d.COOLDOWN_MAX)"` 与接线前一致。

**Commit**: `feat(dispatch): 调度风控参数接入统一配置`

---

#### Task B3: 捕获 / 探活 / 图片参数接线

**Files:**
- `backend/auto_dm/conversation_capture.py:331,1007,1122-1176`（`IMAGE_INLINE_MAX_KB`、`DY_USERINFO_CACHE_SEC`、`DY_HISTORY_*`）
- `backend/services/uid_probe.py:56-60`（`DY_UID_PROBE_TTL_OK/FAIL/LOCK_WAIT`）
- `backend/auto_dm/origin_image_resolver.py:53,89,109,317`（图片 TTL/容量/强制图床）
- `backend/vbrowser.py:627,704`（`DY_BCC_HEADLESS_MODE`）
- `backend/daemon/browser_daemon.py:655`（`DY_USERINFO_CACHE_SEC`）

同一模式：保留模块级兜底 + 新增读配置函数。**逐字段标注 apply**：
- `DY_HISTORY_*`、图片 TTL/容量 → 热生效（每次调用读）
- `DY_USERINFO_CACHE_SEC`、`DY_UID_PROBE_TTL_*`、`DY_BCC_HEADLESS_MODE` → 重启守护/backend

**Commit**: `feat(config): 捕获/探活/图片参数接入统一配置中心`

---

#### Task B4: 启动类参数接线（旧 `auto_dm/config.py` 定位澄清）

**Files:** Modify `backend/main.py:222,267`（`DY_BCC_ON_START`/`DY_AUTO_CAPTURE_ON_START`）、`backend/core/auto_dm.py`（`LIVE_POLL_INTERVAL`/`WS_HEARTBEAT_INTERVAL`）

- 两个启动开关改读配置中心（apply=restart_backend，UI 明示）
- 把 `auto_dm/config.py` 的 `LIVE_POLL_INTERVAL=30`、`WS_HEARTBEAT_INTERVAL=300` 迁移进配置中心「直播监听」区；`auto_dm/config.py` 里对应常量加 `DEPRECATED: 已迁移至 services/app_config` 注释（**先不删**，避免影响未扫到的引用）

**⚠️ `auto_dm/config.py` 定位（本轮实测澄清，勿删勿迁）**：
该文件**不是死文件**，它承担**指纹浏览器内核配置**这一关键职责，7 处调用：
- `VB_MODE` / `VB_CHROME_EXE` / `VB_API_BASE` / `VB_ENV_ID` / `VB_LAUNCH_TIMEOUT` / `USE_VIRTUAL_BROWSER`
- 调用点：`browser_daemon.py:316`、`login_api.py:115/196/618/702/843`、`web_probe.py:104`、`link_resolve.py:272`、`accounts.py:211`（`init_vb_config(_cfg)` 注入全局 `_CFG`）
- `should_use_vb()` 在 `USE_VIRTUAL_BROWSER=False` 时**直接抛 RuntimeError**（禁止回退原生 Playwright）

**处理结论**：
- **保留该文件**，仅在文件头加注释说明职责边界（业务参数已迁 `services/app_config`，本文件仅留浏览器内核配置 + `pick_dm_message`）
- `VB_*` 六项**不进设置页**（改错会导致指纹浏览器整体起不来，属"改了出大事且基本不用改"），UI 无入口
- 死配置（`ENABLE_DANMAKU`/`MAX_TARGET`/`SEND_INTERVAL`/`DM_MESSAGE_POOL` 等十几个）**本轮不删**——扫描基于 `config.X` 属性访问，可能漏掉 `getattr(cfg,"X")` 动态访问

**Commit**: `feat(config): 启动策略与直播轮询参数接入配置中心`

---

### 阶段 C：通知模块 UI（后端已就绪，纯前端收益）

#### Task C1: 设置页「通知」分区

**Files:** Create `frontend/src/pages/settings/sections/NotifySection.tsx`；Modify `frontend/src/api/client.ts`（加 notify 系列方法）

- 接 `GET/POST /api/notify/config`、`GET /api/notify/status`、`POST /api/notify/test`
- 渠道列表：新增/编辑/删除；敏感字段按后端约定回传 `••••••••` 表示不改（后端 `api/notify.py:88-93` 已实现脱敏与保留原值）
- 「测试推送」按钮调用 `/api/notify/test`

**验证**：配一个渠道 → 点测试 → 真收到推送（这条必须实收验证，不能只看 200，调试铁律 0）。

**Commit**: `feat(ui): 设置页新增通知渠道配置`

---

### 阶段 D：配置入口去重（消除双写）

#### Task D1: live.tsx 移除配置保存入口

**Files:** Modify `frontend/src/pages/live.tsx:1262-1299`

删掉「保存词库」「保存配置」两个按钮，改为只读展示 + 一行提示「配置已迁移至设置页 → 直播监听」+ 跳转按钮（`setTab("settings")`）。

**注意**：删除前确认 live 页没有其他依赖这两个 state 的逻辑（`dmTemplates`/`dmLimit`/`dmInterval`/`dmJitter` 若只用于保存，一并收敛；若用于展示则保留只读）。

**Commit**: `refactor(ui): 直播页配置入口收敛至设置页`

---

#### Task D2: Agent 档案体系（后端）

**Files:** Modify `backend/services/ai_reply.py`、Modify `backend/api/ai.py`

**Step 1（TDD）**：写 `backend/test_ai_agents.py`
```python
def test_migrate_legacy_config_to_default_agent():
    # 旧 kv_store["ai_reply_config"] = {"model":"glm-5.2"}
    ensure_agents_migrated()
    agents = list_agents()
    assert len(agents) == 1 and agents[0]["name"] == "默认 Agent"
    assert get_config()["model"] == "glm-5.2"

def test_config_for_unbound_account_falls_back_to_default():
    assert get_config_for("某未绑定账号")["model"] == get_config()["model"]

def test_config_for_bound_account_uses_its_agent():
    aid = save_agent(None, {"name":"A号专用","config":{"model":"m-a"}})
    bind_account("账号A", aid)
    assert get_config_for("账号A")["model"] == "m-a"
    assert get_config_for("账号B")["model"] != "m-a"

def test_delete_bound_agent_rejected():
    aid = save_agent(None, {"name":"x","config":{}})
    bind_account("账号A", aid)
    with pytest.raises(ValueError):
        delete_agent(aid)
```

**Step 2**：`python -m pytest test_ai_agents.py -v` → 4 fail

**Step 3**：实现（按 §二·甲 数据模型）
- `_KV_AGENTS = "ai_agents"` / `_KV_BINDINGS = "ai_bindings"`
- `ensure_agents_migrated()`：**幂等**，无 agents 时把现有 `ai_reply_config` 包成「默认 Agent」
- `get_config()` 保持签名（返回默认 Agent 配置），`get_config_for(account)` 新增
- CRUD + 绑定管理，删除被绑定档案时抛 ValueError

**Step 4**：重跑 → 4 passed

**Step 5**: `git commit -m "feat(ai): Agent 档案库 + 账号绑定（兼容旧全局配置）"`

---

#### Task D3: worker 按账号取配置（核心修复）

**Files:** Modify `backend/services/ai_reply.py:806-841`

现：
```python
def _tick(self):
    cfg = get_config()          # ← 循环外取一次
    ...
    for r in rows:
        self._handle(r, cfg)    # ← 所有账号共用
```
改：
```python
def _tick(self):
    cfg_default = get_config()
    if not cfg_default.get("enabled"):
        ...推进水位; return
    for r in rows:
        try:
            self._handle(r)     # ← 内部按 account 取
...
def _handle(self, row):
    account = row["account"]
    cfg = get_config_for(account)      # ← 逐行取
    if not cfg.get("enabled"):         # 单 Agent 可独立关停
        return
```

⚠️ **这是整个需求的技术核心**：`_tick` 一次取 20 行可能横跨多账号，**必须在循环内逐行取配置**。在循环外取一次 = 等于没做绑定。

**验证（不启应用即可测）**：
```python
bind_account("账号A", aid_a)   # model=m-a
bind_account("账号B", aid_b)   # model=m-b
w = AutoReplyWorker()
rows = [ {"account":"账号A",...}, {"account":"账号B",...} ]
# 断言两次 _handle 内部拿到的 cfg["model"] 不同
```
**实机验证**：两个已登录账号各发一条消息，日志应出现两条不同 `model=` / 不同 `merchant_name` 的生成记录。

**Commit**: `fix(ai): worker 逐账号取 Agent 配置，修复多账号串配置`

---

#### Task D4: 新增 Agent / 绑定 API

**Files:** Modify `backend/api/ai.py`

按 §二·甲 的 7 个端点实现（agents CRUD + bindings CRUD）。

**注意**：
- `/api/ai/agents` 与 `/api/ai/bindings` 需鉴权；若前端还走免登录豁免，按 §A2 规则同时加带尾斜杠形式
- 删除被绑定 Agent → 返回 400 + 明确中文提示「该 Agent 已绑定到账号 X，请先解绑」
- 配置里 `api_key` 等敏感字段可沿用 notify 的脱敏回传约定（`••••••••` = 不改）

**验证**：`curl /api/ai/agents`、`curl -X POST /api/ai/bindings -d '{"account":"x","agent_id":"ag_1"}'`

**Commit**: `feat(api): Agent 档案与账号绑定接口`

---

#### Task D5: 设置页 AI 分区（Agent 列表 + 绑定表）

**Files:** Create `frontend/src/pages/settings/sections/AiSection.tsx`；Modify `frontend/src/api/client.ts`

**Step 1**：Agent 档案列表（卡片式）
- 每个卡片：名称 / 备注 / 更新时间 / [编辑] [复制] [删除]
- 「新建 Agent」→ 可「从现有复制」

**Step 2**：选中 Agent 后的配置编辑区
- 迁入 ai.tsx 的「Agent 设定」+「护栏配置」全部字段
- 复用 `aiTest` / `aiTestVision` / `aiSemTest` / `aiSemRebuild` 做连通性测试
- 「测试连接」按钮必须留在每个 Agent 内（不同 Agent 可能用不同模型/密钥）

**Step 3**：账号绑定表
- 行：`[账号名] [下拉选 Agent] [当前生效：xxx（显式绑定/默认）]`
- 保存调 `POST /api/ai/bindings`

**Commit**: `feat(ui): 设置页 AI 分区——Agent 档案 + 账号绑定`

---

#### Task D6: AI 页加绑定概览 + 移除配置 Section

**Files:** Modify `frontend/src/pages/ai.tsx`

- **删除**：「Agent 设定」「护栏配置」两个 Section（已迁设置页）
- **保留**：知识库、留资线索、黑名单（数据视图）
- **新增顶部「绑定概览」**（可折叠卡片）：
  - 表格：账号 → 生效 Agent 名 → 来源（显式绑定 / 默认）
  - 未绑定任何 Agent 的账号高亮提示「当前使用默认 Agent」
  - 「去设置页管理绑定」按钮 → `setTab("settings")`
- 留资线索/黑名单加「账号」列或按账号分组，让数据归属可见

**Commit**: `refactor(ui): AI 页改为数据视图 + Agent 绑定概览`

---

#### Task D7: Bot 权限守卫（后端，安全前置）

**Files:** Create `backend/notify/umo.py`、`backend/notify/bot_guard.py`；Modify `backend/api/notify.py`（`_execute` 开头）

> 注：原计划的 `notify/uid_scope.py` **已撤销**（层级定稿：UID 归沉淀池）。

**Step 1（TDD）**：写 `backend/test_bot_guard.py`
```python
def test_disabled_bot_rejected():
    save_bot_cfg({"enabled": False, "umo_bindings": {"wecom:FRIEND_MESSAGE:s1": {"role": "admin"}}})
    ok, role, acct, reason = check_command("wecom:FRIEND_MESSAGE:s1", "start_task")
    assert not ok and reason == "bot_disabled"

def test_unregistered_umo_rejected():
    save_bot_cfg({"enabled": True, "umo_bindings": {}, "allow_unregistered_umo": False})
    ok, _, _, reason = check_command("dingtalk:GROUP_MESSAGE:g1", "query_status")
    assert not ok and reason == "umo_unregistered"

def test_member_cannot_start_task():
    save_bot_cfg({"enabled": True,
                  "umo_bindings": {"wecom:FRIEND_MESSAGE:s1": {"role": "member", "account": "A"}}})
    ok, _, _, reason = check_command("wecom:FRIEND_MESSAGE:s1", "start_task")
    assert not ok and reason == "permission_denied"

def test_admin_allowed_returns_account():
    save_bot_cfg({"enabled": True,
                  "umo_bindings": {"wecom:FRIEND_MESSAGE:s1": {"role": "admin", "account": "A"}}})
    ok, role, acct, _ = check_command("wecom:FRIEND_MESSAGE:s1", "start_task")
    assert ok and role == "admin" and acct == "A"

def test_umo_format_roundtrip():
    u = make_umo("wecom", "FRIEND_MESSAGE", "s1")
    assert u == "wecom:FRIEND_MESSAGE:s1"
    assert parse_umo(u) == ("wecom", "FRIEND_MESSAGE", "s1")

def test_bot_does_not_filter_uid():
    # 层级保证：bot_guard 不提供任何 UID 过滤接口
    import notify.bot_guard as bg
    assert not hasattr(bg, "filter_uids")
```

**Step 2**：`python -m pytest test_bot_guard.py -v` → 6 fail

**Step 3**：实现两个模块
- `notify/umo.py`：`parse_umo` / `make_umo`，格式 `platform:msgtype:session_id`（对齐 AstrBot）
- `notify/bot_guard.py`：`check_command(umo, intent) -> (bool, role, account, reason)`
- 配置读 `kv_store["im_bot_config"]`，缺失时按 §二·乙 默认（enabled=False、member 最小权限）
- **不实现任何 UID 相关函数**

**Step 4**：重跑 → 6 passed

**Step 5**：在 `api/notify.py` `_execute` **第一行**接入：
```python
async def _execute(intent, params, umo: str = ""):
    ok, role, account, reason = check_command(umo, intent)
    if not ok:
        return {"reply": _DENY_MSG[reason]}
    # 只把 account 往下传；UID 去重/冷却由沉淀池 UidSink 在 submit* 内处理
    ...
```
⚠️ 权限检查必须在任何引擎操作之前；`CommandIn` 增加可选 `umo` 字段（**不是 uid**）。
⚠️ **禁止**在此处加 UID 过滤——层级跑偏。

**Step 6**：回归验证——member 发「启动任务」应被拒且**引擎状态不变**（查 `/api/engine/status` 确认仍 IDLE）。

**Commit**: `feat(bot): UMO 会话绑定 + role 权限闸（对齐 AstrBot，UID 归沉淀池）`

---

#### Task D8: 设置页「IM 消息与 Bot」分区

**Files:** Create `frontend/src/pages/settings/sections/ImBotSection.tsx`；Modify `frontend/src/api/client.ts`

按 §二·乙 前端改动 5 项实现：总开关 / **UMO 会话绑定表** / **role 权限矩阵** / 6 类事件推送开关 / 默认发送通道 ws-wp 单选。

**注意**：
- **UMO 不是 UID**：UMO 输入拆三个字段（平台 / 消息类型 / 会话ID），拼成 `platform:msgtype:sid`；UI 上标注「对齐 AstrBot 会话标识」
- **本分区不放 UID 配置**（层级定稿：UID 归沉淀池、会话归整理池）
- `start_task`/`stop_task`/`create_task`/`recapture` 四项标红 + 文案「影响真实任务，谨慎开启」
- `create_task`/`recapture` 即使开启也保留二次确认，UI 注明
- 账号下拉复用 `api.getAccounts()`（勿用 overview，overview 无 accounts 字段——见 dev-guards §10.1）

**验证**：member 发指令被拒；admin 能执行。

**Commit**: `feat(ui): 设置页新增 IM 消息与 Bot 分区`

---

### 阶段 E：设置页 UI 重构（schema 驱动）

#### Task E1: 设置页改为 schema 驱动渲染

**Files:**
- Modify `frontend/src/pages/settings.tsx`（616 行，大改）
- Create `frontend/src/pages/settings/ConfigField.tsx`（通用字段渲染器：number/text/bool/select/list）

- 左侧子导航从 3 项扩到 **8 项**（通用/直播监听/私信发送与风控/捕获与存储/通知/AI 获客/**IM 消息与 Bot**/独立账号）
- 右侧按 `schema` 渲染字段，每个字段显示：label、输入控件、hint、`apply` 徽章（热生效 / 需重启守护 / 需重启应用）
- 风控敏感字段（`DY_STRANGER_*`、冷却、权重）额外显示风险提示，并设置**下限保护**（低于安全值拒绝保存并提示）
- 底部「保存」按钮 → `POST /api/settings`，按返回的 `restart_required` 弹提示

**验证**：
1. `npx tsc --noEmit` 通过
2. 每个分区改一个值 → 保存 → 重开设置页值仍在
3. 改 `apply=restart_daemon` 字段 → 提示正确出现

**Commit**: `feat(ui): 设置页改为 schema 驱动，7 分区`

---

### 阶段 F：构建部署与知识库回写

#### Task F1: 构建部署

**Files:** 版本号四处同步：`package.json` / `frontend/package.json` / `src-tauri/tauri.conf.json` / `src-tauri/Cargo.toml`（按铁律逐位 +0.01）

顺序（dev-guards §五 + dyautodm-development §10.3）：
1. `build_one('main.py','dyautodm-backend')` 重打 backend sidecar → `cp` 到 `C:\temp\dyautodm_test\binaries\`
   （注：改了 `daemon/recv_daemon.py` 需另打 `dyautodm-recv-daemon`；改了 `vbrowser.py` 需三个 exe 全打）
2. `export PATH="/c/Users/LOX/.rustup/toolchains/stable-x86_64-pc-windows-msvc/bin:$PATH"`
3. `npx vite build` → `npx tauri build --no-bundle`
4. `cp src-tauri/target/release/dyautodm-v2.exe C:\temp\dyautodm_test\DYAutoDM_v2_<ver>.exe`
5. **从部署目录启动**：`powershell -Command "Start-Process -FilePath 'C:\temp\dyautodm_test\DYAutoDM_v2_<ver>.exe' -WorkingDirectory 'C:\temp\dyautodm_test'"`

**前置**：停进程走 BCC `/quit` 优雅退出，绝不强杀浏览器（铁律 §9.17）。

#### Task F2: 知识库回写（用户明确期望）

1. 新建/追加 `C:\Users\LOX\Desktop\DYchajian\工作记忆\15_统一配置中心与设置页.md`
   - 记录：7 层承载清点结果、`/api/settings` 空壳这一事实、schema 驱动架构、apply 三分类、每字段默认值与来源文件行号
   - 记录纠偏：`dm_dispatch` **是活的**（`core/dispatch.py:317` 函数内动态 import）——顶层 import 扫描会漏判，以后判断「某模块是否死代码」不能用 `^import` 正则
2. `cp` 到 `D:\文档\Biancheng   CK\DY v2\DY v2\raw\sources\工作记忆\`（`Biancheng` 与 `CK` 之间多空格）
3. 调 LLM Wiki `llm_wiki_rescan_sources` 触发重扫

---

## 四、验证清单（端到端）

| 项 | 命令 / 动作 | 预期 |
|---|---|---|
| 配置读写 | `curl /api/settings` → `curl -X POST /api/settings -d '{"send":{"min_interval":12}}'` | 返回 ok + `restart_required` 正确 |
| 持久化 | 重启 backend 后再 `curl /api/settings` | 值仍在（kv_store） |
| 热生效 | 改 AI 配置 → 立即 `curl /api/ai/config` | 新值 |
| 重启生效 | 改发送闸门 → 提示「需重启接收守护」 → 走 `/quit` 重拉 → `/status` | 新值 |
| 通知 | 配渠道 → 点测试推送 | **真收到**（非仅 200） |
| UI | 8 分区逐个改值保存重开 | 值保持，apply 徽章正确 |
| **Agent 绑定** | 两账号各绑不同 Agent，各发一条消息 | 日志出现两条不同 model/merchant_name |
| **Bot 权限** | member UMO 发「启动任务」 | 被拒 + 引擎仍 IDLE |
| **UID 不重复** | 同一 UID 二次下发（沉淀池职责） | `UidSink.should_send` 拒绝（**非 Bot 层**） |
| 无回归 | 私信收发、直播监听、更新会话各跑一遍 | 与改前一致（尤其发送闸门默认 8s 未变） |

---

## 五、风险与开放问题

**风险**
1. **风控参数改错 = 封号**：陌生人限速/冷却/权重类参数接线时默认值必须逐字一致；UI 加下限保护。低值不放开。
2. **守护进程配置传递**：`restart_daemon` 类字段若不做「拉起时注入 env」，用户改了必须手动重启——方案选择：**不注入 env，改为守护每次调用时读 DB**（守护与 backend 同机同库，可行），这样大多数字段能降级为热生效。需在 B1/B3 实施时逐字段确认守护进程能否访问同一 DB（会员空间路径由 `DY_MEMBER`/`DY_MEMBER_KEY` 决定，需验证）。
3. **双写窗口**：阶段 D 完成前，live.tsx 与设置页会同时写 `kv_store["config"]`，存在互相覆盖。阶段 D 应尽早做，或与 B 阶段并行。
4. **前端一次改太多**：设置页 616 行大改 + 7 分区，建议 E 阶段一次做完再构建（前端每次构建 ~3min）。

**已拍板（用户确认）**：
1. ✅ **AI 知识库留在 AI 页**（RAG 数据源，与留资线索/黑名单同类）；设置页 AI 分区只放 Agent 配置 + 绑定
2. ✅ **AI 配置全迁**（Agent 设定 + 护栏 + 模型），并加 Agent ↔ 账号绑定（§二·甲）
3. ✅ **`auto_dm/config.py` 保留不删**（它是指纹浏览器内核配置载体，`VB_*` 六项不进设置页）；死配置本轮不删，只加注释
4. ✅ **Bot 权限用 UMO 不用 UID**（对齐 AstrBot）；**UID 与会话不归 Bot 管**
5. ✅ **层级定稿**：`Agent → 账号` 交调度器 `DmDispatcher`；`会话 + UID` 交沉淀池 `UidSink` / `ConvPool`；Bot 只管 UMO + role 的指令权限。Bot 层不实现任何 UID 过滤。

**已全部拍板，无遗留问题**：
6. ✅ **Agent 绑定粒度 = 账号级**（与 `DmDispatcher` 的 per-account 队列天然对齐）；会话级留作后续扩展。

---

## 七、执行记录

> 实施时逐 task 在此登记：状态 / 提交号 / 验证结果 / 偏离说明。

| Task | 状态 | Commit | 验证 |
|---|---|---|---|
| A1 app_config 地基 | 进行中 | — | — |

---

## 六、实施顺序建议

```
A1 → A2（地基，后端可独立验证）
        ↓
B1 → B2 → B3 → B4（接线，逐模块提交）
        ↓
C1（通知 UI，独立收益，可与 B 并行）
        ↓
D1（live 页去重，消除双写）
D2 → D3 → D4（Agent：档案库 → worker 逐账号取 → API）
D5 → D6（Agent UI：设置页分区 → AI 页绑定概览）
D7 → D8（Bot：UMO+UID 守卫后端 → 设置页 UI）
        ↓
E1（UI 重构，一次做完）
        ↓
F1 → F2（部署 + 知识库）
```

每 task 2-5 分钟粒度，逐 task 提交，不批量合并。
