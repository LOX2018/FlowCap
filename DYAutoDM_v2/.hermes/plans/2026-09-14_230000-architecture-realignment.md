# 按源项目架构重设计本项目 —— 架构规划

> **任务**：用户 2026-09-14「**现在按照源项目的架构来重新设计本项目**」
> **分支**：`design/better-douyin`　|　**基准**：`anYuJia/better-douyin` v1.1.18
> **性质**：**架构级设计**（非功能补丁）。按用户「设计优先律」——先定契约，再动代码。
> **取证**：① 源项目逆向（`/d/bdy_reverse/extract_dl/all_strings.txt`）；
> ② 源项目公开壳源码（`better-douyin-main/frontend/src/`，82 tsx + 71 ts）；
> ③ 本项目现状盘点（本文件 §二）。
> **日期**：2026-09-14

---

## 一、两个项目的架构差异（实测对照）

### 1.1 源项目架构（权威参照）

**后端（Rust / Tauri）—— 21 个 `.rs` 模块，三层切分：**

```
src-tauri/src/
├─ api/                        ← 平台接口层（13 个 client，按业务域切）
│   ├─ client.rs               基础客户端（域名/签名/重试）
│   ├─ client_user.rs          用户（query/user、profile/self）
│   ├─ client_video.rs         作品（post、detail）
│   ├─ client_feed.rs          流（推荐流）
│   ├─ client_comments.rs      评论
│   ├─ client_collection.rs    收藏/合集（listcollection、mix、series、favorite）
│   ├─ client_relations.rs     关系（follow、digg、collect）
│   ├─ client_notice.rs        通知
│   ├─ client_im.rs            IM 基座
│   ├─ client_im_messages.rs   IM 消息
│   ├─ client_im_history.rs    IM 历史
│   ├─ client_im_friends.rs    IM 好友
│   └─ media_proxy_cache.rs    媒体代理与缓存
├─ downloader/                 ← 下载子系统（独立目录，7+ 模块）
│   ├─ downloader.rs / tasks.rs / control.rs / events.rs
│   ├─ request_policy.rs / retry.rs / ...
├─ automation.rs               ← 自动化引擎（监控+动作）
├─ download_files.rs           ← 下载落盘
└─ mcp.rs                      ← MCP 服务
```

**前端（React）—— components-first，无 `pages/` 目录：**

```
frontend/src/
├─ components/                 ← 18 个功能目录（按业务域切组件）
│   ├─ home/         (3)   hero / quick-stats / ambient-background
│   ├─ search/       (5)   search-view / user-detail ...
│   ├─ player/       (15)  ★ 播放器独立目录（最大）
│   ├─ friends/      (11)  ★ 好友/IM
│   ├─ settings/     (8)
│   ├─ layout/       (7)   app-shell / sidebar / command-popover
│   ├─ downloads/    (3) / collected/ (2) / automation/ (2)
│   ├─ liked/ (1) / recommended/ (1) / notices/ (1) / link/ (1) / media/ (1)
│   ├─ modals/ (2) / common/ (3) / ui/ (14)  ← 无业务语义的基础组件
├─ stores/                     ← 5 个（app / liked / link / recommended / search）
├─ lib/                        ← 22 个（tauri / contracts / socket / 各 cache）
├─ hooks/ · types/ · assets/
└─ App.tsx                     ← 路由中枢（无 react-router，用 store 切视图）
```

**关键机制（实测）**：
```typescript
// 视图切换：单一枚举 + store，无 URL 路由
type ViewType = "home"|"search"|"user"|"recommended"|"downloads"
              | "liked"|"collected"|"notices"|"friends-status"
              | "automation"|"settings";
currentView: "home",
setView: (view: ViewType) => set({ currentView: view }),
```

### 1.2 本项目现状（改造对象）

**后端（Python / FastAPI）—— 133 个 `.py`：**

| 目录 | 文件 | 行数 | 性质 |
|---|---|---|---|
| `api/` | 19 | 6,217 | 已按域切（platform/messages/live/ai/tasks...） |
| `services/` | 17 | 8,091 | 最大，混杂（含 member_ctx、media_proxy 等） |
| `dy_apis/` | 5 | 4,849 | **平台接口（单体 `douyin_api.py` 是巨型文件）** |
| `daemon/` | 4 | 4,690 | BCC 浏览器守护 |
| `auto_dm/` | 9 | 4,052 | 自动化（昵称捕获/会话） |
| `core/` | 5 | 1,873 | |
| `notify/` | 7 | 1,840 | |
| `utils/` | 12 | 1,534 | |
| `mcp/` | 7 | 1,221 | |
| `downloader/` | 4 | 783 | ★ 本轮新建（照源项目） |

**前端（React）—— 56 个 tsx/ts：**
```
frontend/src/
├─ pages/         11 个  ← ★ 与源项目最大差异：本项目 pages-first
├─ components/    32 个
├─ api/ 3 · lib/ 1 · theme/ 2 · utils/ 1
```

### 1.3 差异归纳（架构级）

| 维度 | 源项目 | 本项目 | 判定 |
|---|---|---|---|
| **前端组织** | components-first（无 pages） | **pages-first**（11 pages） | ★ 核心差异 |
| **视图切换** | `ViewType` 枚举 + store | pages 组件 + 侧栏 tab 状态 | 机制不同 |
| **接口层** | `api/client_*.rs` **按域拆 13 文件** | **单体 `douyin_api.py`**（4,849 行/5 文件） | ★ 核心差异 |
| **下载** | 独立 `downloader/` 目录 | 已建 `downloader/`（本轮） | ✅ 已对齐 |
| **状态管理** | 5 个 store（域隔离） | 待查 | 待评 |
| **播放器** | 独立 `player/`（15 组件） | 无独立播放器目录 | 差异 |

---

## 二、设计契约（本次改造的"应该做什么"）

> 按用户「设计优先律」：**先回答这个架构应该是什么样**，而非围绕现有代码打转。

### 契约 A：接口层按业务域切分（对标 `api/client_*.rs`）

**原设计意图**：平台接口不是一坨，而是**按业务域自治**——用户/作品/流/评论/收藏/关系/通知/IM 各自独立。

**预期行为**：
- `dy_apis/` 下单域一个模块，各自封装该域的**全部**接口（读+写）
- 域之间不互相调用；共用的签名/域名/重试下沉到基础 client
- 新增一个域不影响其它域

**验收判据**：`grep -c "def " dy_apis/client_user.py` 等每域文件存在；`douyin_api.py` 不再承载全部方法。

### 契约 B：前端从 pages-first 转为 components-first

**原设计意图**：UI 由**业务域组件**构成，视图切换是**状态**而非路由。

**预期行为**：
- `frontend/src/components/<域>/` 每个业务域一个目录
- `ViewType` 单一枚举 + `app-store` 控制 `currentView`
- `App.tsx` 只做装配（bootstrap + 事件监听 + 视图派发），不含业务
- 基础 UI 沉到 `components/ui/`（无业务语义）

**验收判据**：`frontend/src/pages/` 不再存在；`grep -r "pages/" frontend/src` 无残留；`ViewType` 覆盖全部视图。

### 契约 C：下载子系统独立自治（已达成，作为样板）

**原设计意图**：下载是**独立子系统**，有自己的状态机/重试/限流，不混入业务层。

**现状**：本轮已建 `backend/downloader/`（4 文件 783 行），对齐源项目模块切分。
**判据**：✅ 已达标，作为其余域的改造样板。

### 契约 D：**本项目独有的能力必须保留**（不可为"像源项目"而丢弃）

> 这是本次设计**最重要的一条**：源项目**没有**的资产不能因架构对齐而破坏。

| 本项目独有资产 | 为什么必须保留 |
|---|---|
| 指纹浏览器 + BCC 被动 hook（`daemon/`、`vbrowser.py`） | 用户明确"只保留指纹管控优势" |
| 错误码体系（`api/errcodes.py`、DSSCC 编码） | 用户"核心机制不可妥协" |
| 会员/凭证加密体系（`services/member_ctx.py`、`.env.enc`） | 本项目安全边界 |
| 通知体系（`notify/`，7 文件） | 源项目无对应 |
| MCP 服务（已验证 22 工具） | 本项目独有 |
| 直播（`dy_live/`、`api/live.py`） | 源项目无 |

---

## 三、阶段划分（可独立交付、独立验证、独立提交）

> 每阶段结束：**实机验证 + 提交 + 递增版本号**（用户版本号铁律 +0.01）。

### 阶段 1：接口层按域切分（后端，风险最低，先做）

**目标**：`dy_apis/douyin_api.py`（单体）→ `dy_apis/client_*.py`（13 域文件）

**现状取证**：先确认 `douyin_api.py` 的方法清单与分组（已实测 46 个方法）。

**步骤**：
1. 盘点 `douyin_api.py` 全部方法 → 按域归类（用户/作品/流/评论/收藏/关系/通知/IM/搜索/直播）
2. 建 `dy_apis/client.py`（基础：域名/签名/重试/统一请求）
3. 逐域迁移 → `client_user.py` / `client_video.py` / ...
4. **保留 `douyin_api.py` 作为**兼容门面**（`class DouyinAPI` 委托到各 client）→ 零破坏现有调用方**
5. 实机验证：`verify_logic_replication.py` 13 项全过 + 端到端调接口

**风险**：本项目有 300+ 处调用 `DouyinAPI.xxx`。**门面模式**是关键——改造期不破坏调用方。

### 阶段 2：前端转 components-first（前端，中风险）

**目标**：`pages/` → `components/<域>/` + `ViewType` 枚举

**步骤**：
1. 定义 `types/index.ts` 的 `ViewType`（照源项目 11 视图 + 本项目独有视图）
2. 建 `stores/app-store.ts`（`currentView` / `setView`）
3. 逐页迁移：`pages/overview.tsx` → `components/overview/` 等
4. 重建 `components/layout/sidebar.tsx`（导航项 = `ViewType` 数组）
5. `App.tsx` 改为视图派发
6. **保留本项目独有的页面**（直播/通知/知识库/配置中心）

**风险**：前端改动面大（56 文件）。用 **tsc -b + 实机渲染**逐页验证。

### 阶段 3：自动化引擎对齐（对标 `automation.rs`）

**目标**：`auto_dm/` 对齐源项目 `automation.rs` 的「监控 + 动作」模型。

**前置**：阶段 1 完成后（自动化依赖接口层）。
**现状**：`automation` 配置分区（12 字段）已就位（本轮 C 项）。

### 阶段 4：播放器独立目录（对标 `player/`，15 组件）

**目标**：本项目播放/浏览逻辑 → `components/player/`。
**现状**：本项目无独立播放器（作品浏览散在 live/知识库页）。

---

## 四、执行顺序与依赖

```
阶段1（接口层）───┬──→ 阶段3（自动化引擎，依赖接口域）
                │
                └──→ 阶段4（播放器，依赖作品/视频域）

阶段2（前端）─── 独立，可与阶段1并行，但需阶段1的 API 路径稳定后收尾
```

**建议**：**先做阶段 1**（后端、风险可控、可验证、是阶段 3/4 的前置）。

---

## 五、待用户确认的开放问题

| # | 问题 | 我的建议 |
|---|---|---|
| 1 | **改造范围**：全量对齐，还是只对齐"结构"（目录/切分）而保留本项目独有能力？ | **只对齐结构**（契约 D：独有资产必须保留） |
| 2 | **接口层门面**：是否保留 `douyin_api.py` 作为兼容门面？ | **保留**（300+ 调用方，门面可零破坏迁移） |
| 3 | **前端视图**：本项目独有页（直播/知识库/配置中心）如何安放？ | 作为 `ViewType` 的**本项目扩展项**，与源项目 11 视图并列 |
| 4 | **改造顺序**：先后端（阶段1）还是先前端（阶段2）？ | **先后端**（可验证、是前置） |
| 5 | **风险接受度**：前端大改（56 文件）是否接受一次性重构？ | 分域渐进，每域验证后提交 |

---

## 六、本规划的边界（诚实声明）

- **不做**：重写技术栈（Python/Rust 之别不可跨越；本项目是 Python 后端 + Tauri 壳）
- **不做**：把本项目独有能力（指纹/BCC/会员/通知/直播/MCP）为"像源项目"而删除
- **不确定**：源项目部分模块（`automation.rs` 内部结构、`player/` 组件职责）只有字符串级情报，**具体实现需逐步逆向**（Rust 反编译不出源码）
- **必须**：每阶段实机验证 + 提交 + 版本递增（用户铁律）
