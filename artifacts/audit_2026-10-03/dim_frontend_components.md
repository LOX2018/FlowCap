# 前端组件审计报告（2026-10-03）

项目：DYAutoDM_v2  
审计范围：`frontend/src/components` 下全部 .tsx 文件及相关前端源码  
审计维度：孤儿组件、默认态可达性、受保护媒体、用户可见文案、React Query queryKey 一致性、未定义 CSS 变量

---

## 1. 孤儿组件

共发现 **6 个孤儿组件**（无任何真实 import + 渲染点）：

| 级别 | 组件 | 证据 | 后果 | 建议 |
|------|------|------|------|------|
| P2 | `crawl/crawl-page.tsx` | 仅出现在注释中（`platform-page.tsx` 注释引用），无 import | 死代码，增加维护负担 | 确认 ADR-034 后删除 |
| P2 | `live/RoomConfigPage.tsx` | 注释明确标注「已下线」（`live-page.tsx` 注释），无 import | 死代码 | 删除 |
| P2 | `platform/crawl-panel.tsx` | 仅出现在注释中，无 import | 死代码 | 删除 |
| P3 | `ui/scroll-area.tsx` | 无 import | 未使用的 Radix 封装 | 删除或标记为保留 |
| P3 | `ui/separator.tsx` | 无 import | 未使用的 Radix 封装 | 删除或标记为保留 |
| P3 | `ui/tooltip.tsx` | 无 import | 未使用的 Radix 封装 | 删除或标记为保留 |

---

## 2. 默认态可达性

**结论：所有页面默认可达，无静默过滤问题。**

- `sidebar.tsx:39-61` 定义了全部 10 个导航项（overview/msg/live/platform/accounts/stats/kb/tasks/notify/logs）
- `app-store.ts:71-87` 的 `VIEW_TITLE` 包含全部 10 个视图 + settings
- `App.tsx:585-599` 条件渲染覆盖全部视图
- `stats` 已正确加入 sidebar 和 VIEW_TITLE，无遗漏

---

## 3. 受保护媒体

**结论：无裸 `<img src="/api/...">` 破图问题。**

- 发现 1 处裸 `<video>` 标签：`platform-page.tsx:187`，src 为外部 URL `https://v26-web.douyinvod.com/...`，非 `/api/` 端点，不会 401
- 所有 `/api/` 媒体请求均通过 `AuthedImg`/`AuthedVideo` 组件（`LoginDialog.tsx`、`message-bubble.tsx`、`message-viewer.tsx`、`player-media-stage.tsx`）

---

## 4. 用户可见文案中的内部信息

| 级别 | 位置 | 内容 | 后果 | 建议 |
|------|------|------|------|------|
| P2 | `overview/AccountsHealthSection.tsx:113` | `数据源 GET /api/accounts —— 请确认后端已启动。` | 暴露内部 API 路径 | 改为「数据加载失败，请检查后端连接」 |
| P2 | `overview/AiRuntimeSection.tsx:65` | `数据源 GET /api/ai —— 请确认后端已启动。` | 同上 | 同上 |
| P2 | `overview/CapabilityHealthSection.tsx:113` | `数据源 GET /api/probe/status —— 请确认后端已启动。` | 同上 | 同上 |
| P2 | `overview/CrawlSection.tsx:110,117` | `数据源 GET /api/crawl/history —— 请确认后端已启动。` | 同上 | 同上 |
| P2 | `overview/LiveStatusSection.tsx:88` | `数据源 GET /api/live/stream —— 请确认后端已启动。` | 同上 | 同上 |
| P2 | `overview/TaskHistorySection.tsx:123` | `数据源 GET /api/tasks/history —— 请确认后端已启动。` | 同上 | 同上 |
| P2 | `settings/ProbeSection.tsx:132` | `数据源 GET /api/probe/status —— 请确认后端已启动。` | 同上 | 同上 |
| P3 | `stats/stats-page.tsx:221` | `/api/overview/funnel` | 暴露内部 API 路径 | 移除或泛化 |
| P3 | `preview-shell.tsx:50` | `对标 better-douyin 的设计令牌 + 组件体系` | 暴露内部项目名（仅 preview harness，非用户可见） | 可忽略 |

---

## 5. React Query queryKey 一致性

| 级别 | 数据键 | 证据 | 后果 | 建议 |
|------|--------|------|------|------|
| P1 | `ai-config` | `AiEngineSection.tsx:90,118` 用 `["ai-config", agentId]`；`GlobalModelCard.tsx:19` 用 `["ai-config", ""]`；`GlobalModelCard.tsx:35` 用 `["ai-config"]` | 同一 AI 配置数据缓存键不一致，可能导致重复请求或缓存失效 | 统一为 `["ai-config", agentId ?? ""]` |
| P1 | `msg-convs` | `messages-page.tsx:171` 用 `["msg-convs", activeAcct]`；`messages-page.tsx:747` 用 `["msg-convs"]` | 会话列表缓存不一致 | 统一为 `["msg-convs", activeAcct]` |
| P1 | `msg-detail` | `messages-page.tsx:240` 用 `["msg-detail", activeAcct, conv.conv_id]`；`messages-page.tsx:746,1056` 用 `["msg-detail"]` | 消息详情缓存不一致 | 统一为 `["msg-detail", activeAcct, convId]` |
| P2 | `overview-funnel` | `overview-page.tsx:163` 用 `["overview-funnel"]`；`stats-page.tsx:164` 用 `["overview-funnel", fallbackLatest]` | 漏斗数据缓存不一致 | 统一为 `["overview-funnel"]` |
| P2 | `task-history` | `App.tsx:321` 用 `["task-history", 0]`；`TaskHistorySection.tsx:89` 用 `["task-history", 0, MAX_SHOW]`；`tasks-page.tsx:38` 用 `["task-history", historyPage]`；`tasks-page.tsx:53` 用 `["task-history"]` | 任务历史缓存不一致，4 种键 | 统一为 `["task-history", page]` |

---

## 6. 未定义 CSS 变量

共发现 **17 个未定义 CSS 变量**，分布在多个组件中：

| 级别 | 变量 | 使用位置 | 后果 | 建议 |
|------|------|----------|------|------|
| P1 | `--accent` | `player/player-playback-bar.tsx:90` | 样式失效 | 在 tokens.css 中定义或改用 `--color-accent` |
| P1 | `--border` | `crawl/crawl-page.tsx:455`、`platform/comment-panel.tsx:230`、`platform/crawl-panel.tsx:144` | 样式失效 | 改用 `--color-border` |
| P1 | `--danger` | `settings/AgentSection.tsx:159`、`settings/ModelHubSection.tsx:214,340` | 样式失效 | 改用 `--color-danger` |
| P1 | `--muted` | `logs/logs-page.tsx:9`、`tasks/tasks-page.tsx:7` | 样式失效 | 改用 `--color-text-muted` |
| P1 | `--panel` | `notify/notify-page.tsx:13` | 样式失效 | 改用 `--color-surface` |
| P2 | `--cell-line` | `kb/pro-kb.tsx:563,580,672` | 样式失效 | 在 tokens.css 中定义 |
| P2 | `--color-bg-hover` | `tasks/SchedulerSection.tsx:225` | 样式失效 | 在 tokens.css 中定义 |
| P2 | `--color-ok` | `crawl/CrawlFloatingPanel.tsx:572`、`settings/HighValueKeywordsSection.tsx:310` | 样式失效 | 改用 `--color-success` |
| P2 | `--color-text-tertiary` | `settings/CrawlPolicySection.tsx:172` | 样式失效 | 在 tokens.css 中定义 |
| P2 | `--color-warn` | `tasks/SchedulerSection.tsx:150,152` | 样式失效 | 改用 `--color-warning` |
| P2 | `--color-warn-bg` | `tasks/SchedulerSection.tsx:149` | 样式失效 | 改用 `--color-warning-soft` |
| P2 | `--color-warn-border` | `tasks/SchedulerSection.tsx:149` | 样式失效 | 在 tokens.css 中定义 |
| P2 | `--warn` | `settings/AuthorizationCard.tsx:101,105`、`settings/NotifySection.tsx:366` | 样式失效 | 改用 `--color-warning` |
| P3 | `--radix-select-trigger-width` | `ui/select.tsx:56,67,68` | Radix 内部变量，可能未定义 | 检查 Radix 版本或移除 |

---

## 已核验 / 未核验标注

- **已核验**：孤儿组件（import 扫描）、默认态可达性（sidebar + VIEW_TITLE + App.tsx 渲染分支）、受保护媒体（全量 `<img>`/`<video>` 标签扫描）、用户可见文案（正则匹配）、queryKey 一致性（全量 queryKey 提取）、未定义 CSS 变量（全量 var() 引用 vs tokens.css 定义）
- **未核验**：真机渲染取证（未启动应用/浏览器，无法验证 CSS 变量失效的实际视觉效果、queryKey 不一致的实际缓存行为）
