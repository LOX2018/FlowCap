# 按源项目架构重设计 —— 执行记录

> **任务**：用户 2026-09-14「**现在按照源项目的架构来重新设计本项目**」→「**全部做**」
> **分支**：`design/better-douyin`　|　**版本**：0.43.20 → **0.43.21**
> **基准**：`anYuJia/better-douyin` v1.1.18
> **规划**：`.hermes/plans/2026-09-14_230000-architecture-realignment.md`

---

## 一、用户拍板的三项口径

| # | 决策 | 执行结果 |
|---|---|---|
| 1 | **接口层先行**（后端，对标 `api/client_*.rs`） | ✅ 阶段1 完成 |
| 2 | **只对齐结构**，本项目独有能力保留 | ✅ 无删除（契约 D） |
| 3 | **门面模式**（保 300+ 调用方） | ✅ 零改动 |

---

## 二、四阶段执行结果

### 阶段1：接口层按业务域切分 ✅ `cb37d9d`

**问题**：`dy_apis/douyin_api.py` 是**单体**（60 方法 / **2792 行**）。

**取证**：源项目 `src-tauri/src/api/` 为 **13 个 `client_*.rs`** 按域切分。

**改动**：
```
779 行单体 → dy_apis/
├─ client.py            基础层 BaseClient（域名/签名/请求）
├─ client_user.py       9 方法 ← client_user.rs
├─ client_video.py      4 方法 ← client_video.rs
├─ client_comments.py   6 方法 ← client_comments.rs
├─ client_collection.py 8 方法 ← client_collection.rs
├─ client_relations.py  5 方法 ← client_relations.rs
├─ client_notice.py     2 方法 ← client_notice.rs
├─ client_search.py     4 方法 ← client_feed.rs
├─ client_live.py      13 方法 ← client_feed.rs
├─ client_im.py         8 方法 ← client_im*.rs
└─ _bindings.py         门面接线（bind_all）
douyin_api.py → 176 行（组装门面）
```

**关键技术**（实测验证）：
- 方法体由 **`ast` 机械取体**（逐字节原样，非重写）
- 原文件 **109 处**内部调用写作 `DouyinAPI.xxx(...)` → 用 **`_bindings.bind_all()`**
  在组装后注入最终类到各域模块全局名 ⇒ 内部/外部调用**零改动**
- ⚠️ **实测推翻的假设**：模块级 `__getattr__` **无效**（函数内全局名查找不触发），
  必须用显式注入

**验证**：`verify_api_split.py` **11/11** + 回归 `verify_logic_replication.py` **13/13**

---

### 阶段2：前端 pages-first → components-first ✅ `2fd8317`

**问题**：前端为 **pages-first**（`src/pages/*.tsx` 11 页 + `App.tsx` 的
`useState<TabId>` 条件渲染）。

**取证**：源项目前端 **components-first**（18 个功能目录，**无 `pages/`**），
视图切换 = **单一 ViewType 枚举 + zustand store**。

**改动**：
- 11 页 → `components/<域>/<域>-page.tsx`；11 个根级组件归入所属域；`src/pages/` **消除**
- 新增 `stores/app-store.ts`：`ViewType` / `currentView` / `setView` / `VIEW_TITLE`
  / `SOURCE_VIEWS`；持久化键 `dy:tab` **与原实现逐字一致**
- `App.tsx` 改用 store（`setTab` 对外签名与持久化语义不变 ⇒ 调用方零改动）

**风险实测远低于预期**：`@/*`→`src/*` 别名已在、pages 间 **0 交叉引用**、
仅 2 文件引用 `pages/`。

**验证**：`tsc -b` **0 错误**、`npm run build` **成功**（2423 modules）、
契约校验 **8/8**、**业务逻辑零改动核验 19/19 文件行数完全一致**（373→373、2025→2025…）

---

### 阶段3：自动化引擎（监控 × 过滤 × 动作）✅ `92c2aa1`

**问题**：`app_config.automation` 只有 12 个自拟字段，**无引擎实现**。

**取证（两轮，第二轮推翻第一轮）**：
- 第一轮：逆向 `all_strings.txt` 提取 `auto_*` **粘连字符串**（无分隔）→ 字段名靠猜，
  误得 `auto_monitor_feed`（单数）、漏了 `match_keywords` 维度
- **第二轮（决定性）**：发现源项目壳含**完整可读的
  `frontend/src/lib/ai-automation.ts`** → 权威契约：
  25 字段 + 默认值 + clamp 范围 + **算法原文**
  （`tokens` / `targetKeywords` / `matchesAutomationText` / `meetsVideoAutomationMetrics` /
  `videoAutomationText` / `runVideoAutomation` / `rememberAutomationKey`）

**改动**：
- 新增 `services/automation_engine.py`（照权威契约逐条实现，含 5 组 clamp）
- `app_config.automation`：12 → **35 字段**（源项目 25 项全覆盖）

**验证**：`verify_automation.py` **33/33**

**教训**：**别把"逆向字符串"当契约** —— 真正的契约在源项目壳的可读 TS 文件里。

---

### 阶段4：播放器独立业务域 ✅ `599a3e3`

**问题**：本项目**没有播放器**（平台页注释明写「不做播放器」）。

**取证**：源项目 `components/player/` = **15 组件 + 7 hooks + 2 工具**
（含 62KB 主体、32KB 评论 hook）；常量与类型契约可逐条照抄。

**改动**：
- 新增 `frontend/src/components/player/`：`player-types.ts` / `player-utils.ts`
  （**10 个源项目常量逐条照抄**）/ `player-media-stage.tsx` / `player-playback-bar.tsx` /
  `fullscreen-player.tsx` / `index.ts`
- 新增后端 `POST /api/platform/media/resolve`（+ `/media/stats`）
- `platform-page.tsx` 接线：卡片点击 → 取址 → 播放器浮层

**验证**：`verify_player.py` **30/30**（真实可播放 URL）

**决策记录（实测驱动）**：
| 接口 | 实测结果 |
|---|---|
| 列表类（feed/works/search/collection/liked） | ✅ **作品对象自带播放地址**（125 键 / 45 URL） |
| 作品详情 `/aweme/v1/web/aweme/detail/` | ❌ **HTTP 200 但响应体 0 字节** |

⇒ `/media/resolve` **以 `raw`（前端回传列表对象）为首选入参**
（避免再发一次必然失败的详情请求，**少一次请求也更安全**）。

---

## 三、顺带修复的既有缺陷（由本次改动暴露）

| # | 缺陷 | 根因 | 影响面 |
|---|---|---|---|
| 1 | `get_profile()["platform"]` **KeyError** | `platform` 键位于**已被取代的 `_build_profile()`**；单源化后真源 `fingerprint_profile()` 无此键 | **12 处**（collection 4 / live 4 / comments 2 / search 1 / video 1）→ 修法：统一字面量 `"Win32"`（项目主流写法；**不新增键**以免"两套派生逻辑"重新分叉） |
| 2 | `.gitignore` 的 `_*.py` 误伤 `__init__.py`（前一提交 `3e680ce` 已修） | glob `*` 可匹配空串 | 新包入口被静默排除 |

---

## 四、契约 D 遵守情况（本项目独有能力保留）

**未删除任何**：指纹浏览器 + BCC 被动 hook · 错误码体系 · 会员凭证加密（`.env.enc`）·
通知体系 · 直播 · MCP（22 工具）· MemberGate 门禁 · SelfCheckModal ·
知识库/采集/账号/任务/日志视图（作为 `ViewType` 成员并列保留）。

---

## 五、与源项目的差异（诚实记录）

| 维度 | 差异 |
|---|---|
| 技术栈 | 源项目 Rust（Tauri 原生）；本项目 Python（FastAPI sidecar）——**不可跨越** |
| 播放器 | 源项目 15 组件 + 7 hooks（62KB 主体 + 32KB 评论 hook + BGM/分享/更多菜单/进度预览）；本项目按结构落地核心链路，**未对齐**：评论子系统、BGM、分享、更多菜单 |
| 接口层 | 基础层 `client.py` 与各 mixin 内既有写法**并存**（mixin 方法体仍用原写法）；后续可渐进改用 `BaseClient` 出口 |
| 自动化 | 源项目 `runVideoAutomation` 只做 like/collect；本项目动作位含 comment/private/follow（业务需要），但**过滤与门槛算法完全照源项目** |
| 引擎接线 | `AutomationEngine` 的监控源与动作实现通过**依赖注入**，当前仅单测用假回调，**未接线**到既有 `auto_dm` 能力 |

---

## 六、未验证 / 边界

- 前端**浏览器级实机**未做（会员门禁 MemberGate 需真实登录）→ 留待部署后验收
- 图集 / Live Photo 播放路径未用真实数据跑（当前账号收藏夹仅 1 个视频）
- 播放器互动/下载按钮未接线（互动受平台写接口空响应限制；下载待接 downloader）
- 真实平台写操作（点赞/收藏/关注）仍返回空（**平台侧行为**，非本项目缺陷）

---

## 七、提交链

| SHA | 内容 |
|---|---|
| `cb37d9d` | 阶段1 接口层按域切分（2792 行 → 9 域 + 基础层） |
| `2fd8317` | 阶段2 前端 components-first + 视图 store |
| `92c2aa1` | 阶段3 自动化引擎 + 照源项目权威契约补齐配置 |
| `599a3e3` | 阶段4 播放器独立业务域 + 媒体取址端点 |
| `eb68cee` | 版本 0.43.20 → 0.43.21 |

**全量回归**：13/13 + 11/11 + 33/33 + 30/30 + `tsc -b` 0 错误
