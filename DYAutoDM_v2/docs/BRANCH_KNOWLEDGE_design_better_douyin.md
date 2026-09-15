# 本分支知识（design/better-douyin）—— 与主分支的差异备案

> **为什么单列一份**：用户铁律「工作记忆/skills/知识库必须**按项目隔离**，
> 严禁跨项目混写」。本分支的架构已与主分支**显著不同**（见下），
> 若把本分支内容写进主分支的 `工作记忆/00_架构与版本.md`，会**污染主分支知识**
> （该文件仍描述 `src/pages/` 与主分支环境铁律）。
> 故本分支的架构知识**只落在这里**（仓库内 `docs/`，随分支走）。
>
> **同步约定**：本文件是本分支的权威架构说明。待本分支合入主线时，
> 再由主线统一决定如何并入 `工作记忆/`。

---

## 一、环境隔离（本分支铁律）

| 项 | 主分支 | **本分支** |
|---|---|---|
| 部署/测试目录 | `C:\temp\dyautodm_test` | **`C:\temp\dyautodm_design`** |
| 互串 | **禁止**（两环境不得交叉） | ← 同 |
| 实测门禁 | — | **`backend/scripts/verify_*.py` 硬编码本分支环境，指向主分支即拒绝运行** |

> 教训（2026-09-14）：靠"记住"遵守隔离**必然失守**（本会话曾全程误用主分支环境致污染）。
> ⇒ **隔离必须固化为代码门禁**，而非依赖记忆。

本分支环境的会员会话与凭证（本分支独立）：
- `members/.session.json`（`member_id` / `master_key`）
- `members/<mid>/auto_dm/accounts/<账号>/.env.enc`（**Fernet 加密凭证**）

---

## 二、架构差异（相对主分支）

> 背景：用户 2026-09-14 要求「按源项目 better-douyin 的架构重新设计本项目」，
> 并拍板「**只对齐结构，本项目独有能力完整保留**」。四阶段执行记录见
> `architecture_realignment_executed.md`。

### 2.1 后端接口层：单体 → 按业务域切分

| | 主分支 | **本分支** |
|---|---|---|
| `dy_apis/` | `douyin_api.py` **单体 2792 行 / 60 方法** | **9 域 + 基础层**（照源项目 `api/client_*.rs`） |
| 入口 | `class DouyinAPI` 直接定义全部方法 | `class DouyinAPI(9 个 mixin)` **组装门面** |
| 兼容 | — | `_bindings.bind_all()` 注入最终类 ⇒ 109 处内部 + 300+ 处外部调用**零改动** |

**关键文件**：`dy_apis/client.py`（BaseClient：双域名/公共参数/统一出口）·
`client_{user,video,comments,collection,relations,notice,search,live,im}.py` ·
`_bindings.py`（门面接线）。

⚠️ **不要**把域方法搬回 `douyin_api.py`；新增接口请加到对应域模块。

### 2.2 前端：pages-first → components-first

| | 主分支 | **本分支** |
|---|---|---|
| 页面位置 | `src/pages/*.tsx`（11 页） | **`src/components/<域>/<域>-page.tsx`**（`pages/` 已消除） |
| 视图状态 | `App.tsx` 的 `useState<TabId>` | **`stores/app-store.ts`** 的 `currentView` / `setView`（zustand） |
| 持久化键 | `localStorage["dy:tab"]` | ← **逐字保持一致**（用户现有选择不丢） |

**关键文件**：`stores/app-store.ts`（`ViewType` / `VIEW_TITLE` / `SOURCE_VIEWS`）·
`components/player/`（**新增**，照源项目独立播放域）· `components/<域>/`（14 个域目录）。

⚠️ **不要**新建 `src/pages/`；新页面请建 `components/<域>/<域>-page.tsx` 并登记 `ViewType`。

### 2.3 自动化引擎（新增）

`services/automation_engine.py` —— 照源项目 `automation.rs` 与
**壳内权威契约 `frontend/src/lib/ai-automation.ts`**（完整可读）实现：
监控（`auto_monitor_{notices,friends,comments,feed}`）×
过滤（`tokens` / `matchesAutomationText`：专属词优先、空则回落通用词）×
门槛（`meetsVideoAutomationMetrics`）× 动作 × 去重（上限 1000）× 节流。

`app_config.automation`：12 → **35 字段**（源项目 25 项全覆盖）。

⚠️ **教训**：字段名以**源项目壳的可读 TS 契约**为准，
**不要**以逆向 `all_strings.txt` 的粘连字符串为准（第一轮据此猜错，
实测 `scanned=0` 才发现）。

### 2.4 媒体取址（新增）

`POST /api/platform/media/resolve` —— 供播放器 / 下载。

**实测决定的设计**（重要）：
| 路径 | 结果 |
|---|---|
| 列表类接口返回的作品对象 | ✅ **自带播放地址**（125 键 / 含 `video.play_addr` / 45 URL） |
| 作品详情 `/aweme/v1/web/aweme/detail/` | ❌ **HTTP 200 但响应体 0 字节** |

⇒ 端点**以 `raw`（前端回传列表对象）为首选入参**；`aweme_id`/`url` 仅作 fallback。

---

## 三、本分支新增的验证脚本（**必跑**）

| 脚本 | 覆盖 | 基线 |
|---|---|---|
| `backend/scripts/verify_logic_replication.py` | 阶段1/2/4/5（媒体代理/双域名/内容面/下载/MCP） | **13/13** |
| `backend/scripts/verify_api_split.py` | 接口层按域切分（方法守恒/ MRO / 端到端） | **11/11** |
| `backend/scripts/verify_automation.py` | 自动化引擎（字段契约 / clamp / 过滤 / 门槛 / 编排） | **33/33** |
| `backend/scripts/verify_player.py` | 播放器 + 媒体取址 | **30/30** |

**全部自带环境隔离门禁**（硬编码 `C:\temp\dyautodm_design`）。
改动后**至少跑一次**这 4 个 + `frontend && npx tsc -b`。

---

## 四、接口适配实测台账（**照响应结构适配，勿照字段名猜**）

> 2026-09-14/15 实机逐条验证。每条都附**真实响应证据**，修改前先对照。

| 能力 | 真实响应结构 | 结论 |
|---|---|---|
| **推荐流** `get_feed` | 返回 **不是** `aweme_list`，而是 **`cards[]`**；且 `card["aweme"]` 是 **JSON 字符串**，需 `json.loads` | ✅ 已修（原读 `aweme_list` 恒 0）→ 实测 10 条 |
| **站内通知** `get_notice_list` | 数据在 **`notice_list_v2`**（`notice_list` 恒空）；且 v2 条目**无 `content`**，文案在 `digg.aweme.desc`、作者在 `digg.aweme.author.nickname` | ✅ 已修（原 0 条）→ 实测 10 条 |
| **收藏夹列表** `get_collect_list` | `collects_list: null` = 该账号**确实没有收藏夹**（`collection/items` 能取到作品可佐证） | 非缺陷 |
| **收藏作品** `get_aweme_list_collection` | `aweme_list`（正常） | ✅ 可用 |
| **收藏合集** `get_mix_list_collection` | `mix_infos`（正常） | ✅ 可用 |
| **取自己 sec_uid** | `/user/self` 的 HTML **已不含 secUid**（实测 72KB 响应中 `secUid`/`sec_uid`/`MS4wLjABAAAA` 均 **0 次**，`_ROUTER_DATA` 等均不存在 = 纯异步渲染）→ **HTML 正则已失效**<br>**正解**：`/aweme/v1/web/user/profile/self/`（照源项目）→ 返回 `user.sec_uid`（实测 17954 字节，uid 与 `query/user` 一致） | ✅ 已改用接口（原 `[0]` 索引致 IndexError） |
| **取自己 uid** | `/aweme/v1/web/query/user` 返回 `id` | ✅ 可用 |
| **账号真源** | `/api/accounts` —— **`/api/overview` 不含 accounts 字段** | 前端统一用 `/api/accounts` |

## 四之二、已知平台侧现象（**非本项目缺陷**，勿当 bug 修）

实测（2026-09-14，真实账号）：
- **写操作族**（点赞 `commit/item/digg` / 关注 `commit/follow/user` / 收藏）
  统一 **HTTP 200 但响应体 0 字节**。
- **作品详情**（`/aweme/v1/web/aweme/detail/`）同样 **0 字节**。
- **点赞列表**（`/aweme/v1/web/aweme/favorite/`）同样 **0 字节**
  —— 逆向情报显示**源项目的"点赞列表"也用该接口**，故属平台侧行为；
  本分支已改为**优雅降级**（空列表 + `unavailable` 标记，不抛 502）。

对照：搜索（585KB）、评论列表（108B）、收藏夹列表（正常）等**读列表类**均正常。
⇒ 判读：**平台侧对详情/写操作的处理**；源项目二进制中亦有
`RELATION_SECURITY_GATEWAY: HTTP403` 与 TicketGuard 应对代码（即它面对同一层）。

---

## 五、本分支规则（风控铁律状态）

见 `BRANCH_RULES_design_better_douyin.md`：
用户 2026-09-14 明确授权**全解除**主分支 R1–R7（昵称主动查 / 凭证复用 /
强制走 BCC / 单 profile / 发送闸门 / 杀浏览器 / 日志去重）。

**仍保留**：凭证不打印 · 逆向产物不入库 · 不污染主分支 · 实机验证 · 隔离目录。
