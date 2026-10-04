# 前端维度审计报告 — DYAutoDM_v2

- 审计日期：2026-10-03
- 范围：`DYAutoDM_v2/frontend/src`（107 个 .tsx；组件目录 100 个，排除 `preview-*.tsx`）
- 性质：**只读静态审计**；未修改任何仓库文件
- 方式：批量 python 脚本（引用扫描 / queryKey 抽取 / 端点对齐）+ 项目自带门禁

---

## 0. 门禁结果（已核验）

| 门禁 | 命令 | 结果 |
|---|---|---|
| 受保护媒体渲染 | `scripts/check_media_auth_render.py` | **PASS 3/3**（R1 无裸 `<img>` 指向受保护端点 / R2 走 Authed* 家族 / R3 isLocalApiUrl 契约在位）|
| 铁律 R13 文案 | `scripts/check_iron_rules.py`（R13 项）| **PASS**（用户可见文案无内部开发信息，命中 0）|
| 铁律 R15 配置文案 | 同上（R15 项）| **PASS**（hint ≤18 字，全部字段合规）|

> 结论：检查清单第 2、3 项**无违规**，门禁已覆盖，不再展开。

---

## 1. 孤儿组件（未核验·静态引用扫描）

方法：遍历 `components/**/*.tsx`（排除 `preview-*`），对每个组件名在 `src` 全树做标识符引用搜索（排除自身），零引用即判孤儿。

- 受检组件：**100**；孤儿：**2**。

### [P2] `components/ui/scroll-area.tsx`（33 行）为死代码
- 证据：全树 grep `ScrollArea|scroll-area` 仅命中自身，无任何 import/JSX 使用。
- 后果：未使用的 shadcn 壳组件随构建进入源码树，稀释 `ui/` 目录信噪比；若后续误以为在用，会重复引入滚动容器方案。
- 建议：删除；或若为预留，移入 `ui/_unused/` 并加注释说明来源与保留理由。

### [P2] `components/ui/separator.tsx`（26 行）为死代码
- 证据：全树 `Separator` 命中项全部是 `DropdownMenuSeparator`（`components/ui/dropdown-menu.tsx` 内的独立导出），**无一处**引用 `components/ui/separator.tsx`。
- 后果：与上同；且 `Separator` 与 `DropdownMenuSeparator` 命名近似，易误判"在用"。
- 建议：删除，或并入 `ui/_unused/`。

---

## 2. React Query queryKey 一致性（未核验·静态抽取）

方法：正则抽取全树 `queryKey: [...]`，按「根键」与「端点」归并，识别同数据多键 / 同端点多轮询。

### [P1] `App.tsx` 内 `["accounts"]` 查询**完全重复定义两次**
- 位置：`App.tsx:296-301` 与 `App.tsx:332-337`
- 证据：两处 `queryKey: ["accounts"]`、`queryFn: api.getAccounts()`、`refetchInterval: 30000`、`enabled: ready` 逐字相同；仅 332 处解构出 `accountsCache` 供 `setMsgAcct` 使用，296 处无消费者。
- 后果：React Query 按 key 去重 fetch，**不会双请求**，但同一查询被订阅两次属冗余/死订阅；两处参数若日后只改一处，会产生"键同参数不同"的隐性行为分叉。
- 建议：删除 `App.tsx:296-301`，保留带 `accountsCache` 解构的那一处（或在 296 处解构复用）。

### [P2] `task-history` 同端点跑**两套 limit + 两套 key**，重复轮询
- 位置：
  - `App.tsx:320-328` → `queryKey: ["task-history", 0]`，`getTaskHistory(50, 0)`，`refetchInterval: 15000`
  - `components/overview/TaskHistorySection.tsx:88-92` → `queryKey: ["task-history", 0, MAX_SHOW]`（`MAX_SHOW=5`），`getTaskHistory(5, 0)`，`refetchInterval: 15000`
- 证据：key 形状不同（2 段 vs 3 段）→ **不共享缓存**，两者各自激活，每 15s 向 `/api/tasks/history` 发**两次**请求（一次 limit=50、一次 limit=5）。
- 后果：冗余后端轮询；且"同数据两套 limit"使缓存语义模糊。
- 建议：统一 key 形状与 limit（Section 直接读 App 级 `["task-history",0]` 缓存并 `slice(0,5)`），或让 Section 复用 App 的 key。

### [P2] `overview-funnel` key 不一致，且以 boolean 作缓存后缀
- 位置：
  - `components/overview/overview-page.tsx:163` → `queryKey: ["overview-funnel"]`（queryFn 内部 today→latest 回落）
  - `components/stats/stats-page.tsx:164` → `queryKey: ["overview-funnel", fallbackLatest]`（`fallbackLatest` 为 boolean）
- 证据：同端点 `/api/overview/funnel`，两个不同 key；stats-page 把 boolean 塞进 key。
- 后果：两页不共享缓存；boolean 作 key 后缀语义脆弱（true/false 各占一份缓存，语义上应对应 `day` 值而非布尔）。
- 建议：统一 key 为 `["overview-funnel", day]`，`day` 取 `"today"|"latest"`；today→latest 回落逻辑收进 queryFn 一处。

### [P2] `ai-config` key 两文件不一致（空串 vs agentId）
- 位置：
  - `components/settings/GlobalModelCard.tsx:19` → `queryKey: ["ai-config", ""]`
  - `components/settings/AiEngineSection.tsx:90` → `queryKey: ["ai-config", agentId]`
- 证据：GlobalModelCard 用空串表示"全局配置"，`invalidateQueries({queryKey:["ai-config"]})`（line 35）靠前缀匹配侥幸覆盖。
- 后果：以 `""` 作 key 段是魔法值；若后端/上层约定变化，"全局配置"缓存键含义不明。
- 建议：改用显式哨兵如 `["ai-config", "__global__"]` 或 `["ai-config", {scope:"global"}]`。

### [P3] `["accounts"]` queryFn 在 9 个文件各自重复定义，无共享 hook
- 位置（均 `queryKey:["accounts"]` + `api.getAccounts()`）：`App.tsx:297,333`、`accounts-page.tsx:86`、`engine-cards.tsx:85`、`live-page.tsx:127`、`messages-page.tsx:162`、`AccountsHealthSection.tsx:75`、`overview-page.tsx:131`、`AgentSection.tsx:52`、`TagSection.tsx:59`
- 证据：多为 `enabled:!!ready` 只读共享缓存（`App.tsx:294-295` 注释显式声明"共享缓存"设计意图），**非缺陷**。
- 后果：属可接受的既有约定，但 9 处重复 queryFn 使类型断言（`as RealAcct[]` / `as RawAccount[]` / `as Account[]` / `as unknown[]`）各自为政，类型漂移风险。
- 建议：抽 `useAccounts()` 共享 hook，统一返回类型；非阻断项。

---

## 3. 汇总

| 级别 | 数量 | 条目 |
|---|---|---|
| P0 | 0 | — |
| P1 | 1 | App.tsx `["accounts"]` 重复定义 |
| P2 | 5 | 2 孤儿组件 + task-history 双轮询 + overview-funnel key 不一致 + ai-config key 不一致 |
| P3 | 1 | accounts queryFn 无共享 hook |

**发现合计：7 条。**

---

## 4. 核验状态标注

- **已核验**：第 0 节全部（跑了 `check_media_auth_render.py` 与 `check_iron_rules.py`，引用其实际输出）。
- **未核验（静态分析）**：第 1、2 节全部——结论来自源码文本扫描，未运行时验证（本轮为只读审计，未启动应用/dev server/浏览器）。queryKey 重复是否在运行时造成可观测冗余请求，建议在 dev 环境下用 React Query Devtools 复核。
