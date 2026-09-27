# 维度审计：本区间新增前端组件（可达性 + 默认态合规）

- **项目**：DYAutoDM_v2（`C:\Users\LOX\Desktop\DYchajian\DYAutoDM_v2`）
- **审计区间**：`c9db81e..HEAD`（HEAD = `bd266be`）
- **审计对象**：本区间新增的 10 个前端组件
- **审计维度**：① 引用点/渲染（防孤儿）② 默认态（自动外发/采集）③ 主题两态 + 持久化 ④ 命名/契约一致性（不绕 client.ts）⑤ a11y / 边界态
- **审计方法**：静态 `grep` 引用点扫描 + 逐文件阅读 + 后端契约对账 + **真实构建**（`npm run build` = `tsc -b && vite build`）
- **执行日期**：2026-09-27
- **裁决口径**：遵循 `capability-delivery-verification` 四关（可运行·可发现·可达·默认态）；孤儿组件 / 默认态错误 = P0~P1。

---

## 0. 结论速览

| # | 组件 | 引用点（文件:行） | 可达 | 默认态 | 备注 |
|---|---|---|---|---|---|
| 1 | `LoginDialog.tsx` | `accounts-page.tsx:52`(import) / `:1082`(render) | ✅ | ✅ | 🔴 **P1：二维码裸 `<img src=/api/...>` 会被会员门禁 401** |
| 2 | `theme-toggle.tsx` | `App.tsx:41`(import) / `:515`(render) | ✅ | ✅ | 无 |
| 3 | `AccountsHealthSection.tsx` | `overview-page.tsx:27` / `:331` | ✅ | ✅ | 只读 |
| 4 | `LiveStatusSection.tsx` | `overview-page.tsx:28` / `:332` | ✅ | ✅ | 只读 |
| 5 | `TaskHistorySection.tsx` | `overview-page.tsx:29` / `:333` | ✅ | ✅ | P4：queryKey 与 App 预热键不等，重复轮询 |
| 6 | `comment-panel.tsx` | `platform-page.tsx:29` / `:593` | ✅ | ✅ | P3：`hidden md:flex` ⇒ 窄屏整个消失 |
| 7 | `AppearanceSection.tsx` | `settings-page.tsx:40` / `:118` | ✅ | ✅ | 无 |
| 8 | `CrawlPolicySection.tsx` | `settings-page.tsx:35` / `:153` | ✅ | ✅ | 合规声明在位；P4：`push` 契约类型不一致 |
| 9 | `SchedulerSection.tsx` | `tasks-page.tsx:25` / `:422` | ✅ | ✅ | 休眠态呈现完整；P3：`auto_send_enabled` 无独立休眠徽标 |

**总判定：10/10 全部真实被 import 且渲染 —— 无孤儿组件（P0 关通过）。**
**默认态：9/9 涉及副作用/采集的组件的默认态均合规（后端默认休眠，前端如实呈现）。**
**唯一致命问题：P1 × 1（登录二维码在会员门禁下静默破图）。**

真实构建证据：`npm run build` 通过（`✓ built in 6.59s`），产物含
`accounts-page / settings-page / tasks-page / platform-page / overview-page` 各 chunk，
证明 10 个组件**全部进入了实际打包产物**（非仅存在于源码树）。

---

## 1. 可达性 / 孤儿组件（关 2「可发现」+ 关 3「可达」）

### ✅ 结论：10/10 均有真实引用点与渲染点，无孤儿

引用点扫描命令（排除自身定义行）：

```bash
grep -rn "<ComponentName>" --include=*.tsx src/ | grep -v "<ComponentName>.tsx"
```

命中汇总（关键部分）：

```
LoginDialog          → accounts-page.tsx:52 (import) / :1082 (<LoginDialog .../>)
theme-toggle         → App.tsx:41 (import) / App.tsx:515 (<ThemeToggleButton />)
AccountsHealthSection→ overview-page.tsx:27 (import) / :331 (<AccountsHealthSection {...props} />)
LiveStatusSection    → overview-page.tsx:28 (import) / :332 (<LiveStatusSection {...props} />)
TaskHistorySection   → overview-page.tsx:29 (import) / :333 (<TaskHistorySection {...props} />)
comment-panel        → platform-page.tsx:29 (import) / :593 (<CommentPanel ... />)
AppearanceSection    → settings-page.tsx:40 (import) / :118 (<AppearanceSection />)
CrawlPolicySection   → settings-page.tsx:35 (import) / :153 ({section === "crawlpolicy" && ...})
SchedulerSection     → tasks-page.tsx:25 (import) / :422 (<SchedulerSection {...props} />)
```

**渲染门控核对（防「import 了但不渲染」）**：
- 三个 overview 区块（331-333）位于 `overview-page.tsx` 的 `viewMode === "single"` 分支内，
  而 `viewMode` 默认值 = `"single"`（`overview-page.tsx:42`）⇒ **默认视图即可见**。
- `CrawlPolicySection` 挂在 `settings-page.tsx:153`，其导航项 `crawlpolicy` 已在
  `TABS` 数组登记（`settings-page.tsx:65`，label=「采集策略」）⇒ 有可点击入口。
- `AppearanceSection` 挂在 `settings-page.tsx:118`「通用配置」tab 内 ⇒ 有入口。
- `LoginDialog` 渲染条件 `{scanning && ...}`（`accounts-page.tsx:1080`）⇒ 由扫码流程驱动。

**排除历史高频缺陷「后端有端点前端零引用」**：本次 9 个组件对应的后端端点均**已有前端引用**
（`api/client.ts` 中 `getScheduler/schedulerStart/schedulerStop/listCrawlPolicies/saveCrawlPolicy/
deleteCrawlPolicy/getTaskHistory`、`api/platform.ts` 中 `commentsFull/commentDm` 均已定义且被调用）。

### 🔴 P3｜`comment-panel` 在窄屏（<768px）整块消失，无替代入口

- **文件**：`src/components/platform/platform-page.tsx:588-600`
- **代码原文**：
  ```tsx
  <aside
    className="ml-4 hidden h-[70vh] w-[22rem] shrink-0 flex-col overflow-hidden rounded-[var(--radius-lg)] bg-[var(--color-surface)] p-3 md:flex"
    onClick={(e) => e.stopPropagation()}
  >
    <CommentPanel account={account} awemeId={selectedAweme} push={props.push} />
  </aside>
  ```
- **为何是问题**：`hidden ... md:flex` = 视口 < 768px 时整个评论面板被隐藏，且 `CommentPanel`
  **只在这一处被渲染**（无第二挂载点）。窗口拉窄或桌面分屏时，「评论 + 手动发私信」能力
  变得完全不可得，用户无任何提示（不是空态，是彻底消失）。
- **建议**：为窄屏提供替代路径（下方/抽屉式渲染），或至少渲染一行「窗口过窄，评论面板已隐藏」
  的提示；不要在唯一挂载点上用纯 CSS 隐藏能力。

### 🟤 P4｜`TaskHistorySection` 的 queryKey 与 App 级预热键不一致 ⇒ 重复轮询

- **文件**：`src/components/overview/TaskHistorySection.tsx:89`
- **代码原文**：`queryKey: ["task-history", 0, MAX_SHOW],`
- **对照**：`App.tsx:303` 预热的是 `queryKey: ["task-history", 0]`（2 段），本组件用 3 段
  ⇒ React Query 视为**不同缓存条目**，App 级那份轮询与本组件那份各跑一份。
- **为何是问题**：注释声称「复用 App 级常驻缓存」，实际未复用；对同一后端端点重复轮询
  （本组件 15s + App 15s）。功能不坏，但与该目录其余区块（`LiveStatusSection`/`AccountsHealthSection`
  都精确命中 App 键）不一致，属契约漂移。
- **建议**：统一为 `["task-history", 0]`，或在 App 里改成本组件使用的键。

---

## 2. 默认态合规（关 4「默认态」—— 本审计最高权重项）

> 判据（`capability-delivery-verification`）：自动外发类能力的**受控默认休眠**要求
> 「默认 false + 显式登记 + 界面显示休眠警示 + 有启用条件」四者齐全。

### ✅ 结论：涉及自动外发/采集的组件默认态**全部合规**

#### 2.1 `SchedulerSection.tsx` —— 自动外发（本项目最大风控敞口）：合规且实现质量高

- **后端默认**（已对账）：`services/task_scheduler.py:79,82`
  ```python
  TASK_SCHEDULER_ENABLED = _env_bool("DY_TASK_SCHEDULER_ENABLED", False)  # 默认 False
  AUTO_SEND_ENABLED      = _env_bool("DY_AUTO_SEND_ENABLED", False)       # 默认 False
  ```
  且 `start()` 在总开关未开时 **fail-closed 拒绝启动**（`task_scheduler.py:382-387`）。
- **前端休眠态呈现**（`SchedulerSection.tsx:96,148-163`）：
  ```tsx
  const dormant = st.scheduler_enabled === false;
  ...
  {dormant && (
    <div className="...">
      <ShieldAlert ... />
      <div>当前处于休眠态，所有定时任务（含自动发私信）均不会执行</div>
      <div>这是刻意设计（ADR-018 D1）…需后端设置 DY_TASK_SCHEDULER_ENABLED=1
           （自动外发另需 DY_AUTO_SEND_ENABLED=1）并重启后才会生效。前端不提供开启入口，避免误开。</div>
    </div>
  )}
  ```
- **✅ 四要件齐全**：默认休眠（后端 env=False）✅ / 显式登记（注释 + 徽标 + 文案）✅ /
  休眠警示（黄色 ShieldAlert 横幅，且**置于一切其它内容之前**）✅ / 启用条件（明确写出两个 env 变量 + 重启）✅。
- **✅ 防假成功**：`last_result.skipped` 分支原样展示拦截原因，不包装成成功（`:248-250`）。
- **✅ 不提供前端开启暗门**：组件与 `api/client.ts:1366-1368` 注释均明写「本组不提供开启自动外发接口」。

**🟤 P4（可改进，非缺陷）**：休眠判据只锚 `scheduler_enabled`（总开关），
`auto_send_enabled` 仅在状态行以文本「自动外发：已开启/关闭」呈现（`:184`），
没有独立徽标。极端态（总开调度、但自动外发关）下用户需细读文本才能看出「外发仍关」。
建议对 `auto_send_enabled=false` 也挂一个次级休眠徽标。（当前文案已足够，不构成失守。）

#### 2.2 `comment-panel.tsx` —— 评论采集 + 手动私信：合规

- **文件头契约**（`:21-26`）明写「D7 风控红线：昵称只用评论自带；D1 默认不自动发：
  『发私信』只能手动点按钮，组件内无任何自动/批量/定时发送逻辑」。
- **代码核对**：全组件无 `setInterval`/定时器/批量循环；发私信唯一入口是用户点击 `onSend`
  （`:81-88` 按钮 → `sendOne`，`:110`）。✅ 无自动外发。
- **✅ 诚实呈现**：`accepted` = 入队受理，UI 写「已受理：…（等待投递回执确认）」
  （`:118-123`），不谎称「已发送」。`blocked=true`（风控拦截）单独渲染，
  明确写「这不是『没有评论』」（`:143-152`）。这层「禁止假成功」做得到位。

#### 2.3 `CrawlPolicySection.tsx` —— 采集策略：合规（「只存参数」声明在位）

- **设计声明**（`:15-18` 头注 + `:118-121` 卡片内可见横幅）：
  ```tsx
  <div className="...">
    采集策略 = <b>一组可复用的采集参数</b>（怎么采），不含关键词等身份信息。
    本卡片只存参数，<b>不会自动采集</b> —— 采集动作仍由你显式触发。
  </div>
  ```
- **代码核对**：组件只有 `list/save/delete` 三个调用（`:73,90,108`），
  **零采集触发**、零轮询 `useEffect`（`:79-81` 只在挂载时 `load()` 一次读取列表）。✅
- **✅ 非法输入拒绝而非静默回落**（关 4 负控）：后端对非法枚举明确拒绝，
  前端如实展示错误（`:92-93` 注释 + `push("保存失败: " + (r.error || "未知原因"))`）。✅

#### 2.4 其余组件默认态

- `LoginDialog` / `theme-toggle` / `AppearanceSection` / 三个 overview 区块：
  均无自动外发/采集副作用（只读 GET 或本地偏好），默认态无风险。✅

---

## 3. 主题切换（深/浅两态 + 持久化）

### ✅ 结论：两态覆盖完整，持久化真实生效

- **入口双通道**：
  - 主入口（有状态语义）：`settings-page.tsx:118` 内 `<AppearanceSection />`，
    用 `Switch`（`AppearanceSection.tsx:69-76`），旁标「日间/夜间」文字 + 顶部胶囊徽标（`:41-57`）。
  - 快捷入口：`App.tsx:515` `<ThemeToggleButton />`，`theme-toggle.tsx:26` 点击
    `setTheme(isDay ? "aerospace" : "day")`。
- **ThemeId 合法性核对**：`theme/ThemeContext.tsx:36-40`
  ```ts
  export function normalizeTheme(raw) { if (t === "day") return "day"; return "aerospace"; }
  ```
  ⇒ 组件使用的 `"day"` / `"aerospace"` 均为合法值，与 `ThemeContext` 契约一致。✅
- **持久化核对**：`setTheme` → `applyThemeToDom` → `persist`（`ThemeContext.tsx:83-88`）
  写 `localStorage.setItem("dy.theme", theme)`；`ThemeProvider` 初始化读 `readStoredTheme()`
  （`:105`）⇒ **重启保留**，是真实持久化（非仅内存态）。✅
- **两态 CSS 覆盖核对**：`src/styles/tokens.css:94` 存在 `[data-theme="day"] { ... }`
  整套亮色 token，`:217-219` 还有 day 态滚动条覆盖 ⇒ 亮色态有真实样式支撑。
- **一致性**：两入口调用**同一个** `setTheme`，且 `theme-toggle.tsx:6` 头注明写
  「不引入任何新状态、不读后端、不写 localStorage（持久化由 ThemeContext 负责）」
  ⇒ 无双真相源。（注：`AppearanceSection` 头注声称默认 `aerospace` 是老用户观感；
  与 `normalizeTheme` 默认分支一致。✅）
- **⚠️ 未做运行态目视验证**：本审计未启动后端 + 浏览器实拍深/浅两态截图
  （真实构建已通过，两态 token 与持久化代码路径已核对，但**像素级渲染未取证**）。
  该项建议由 `frontend-visual-verification` 补一台真实浏览器两态截图 —— 见 §6「未取证项」。

---

## 4. 命名 / 契约一致性

### ✅ 无裸 fetch 绕过统一封装

扫描 9 个新组件的 `fetch(` 调用：**零命中**（唯一命中是 `TaskHistorySection.tsx:97` 的
`void refetch()`，是 React Query 方法名，非网络 fetch）。

- `LoginDialog` 通过动态 `await import("@/api/client")` 取 `api.submitSmsCode`
  （`LoginDialog.tsx:85-86`）—— 走统一封装，非裸 fetch。✅
- `CrawlPolicySection` 直接 `import { api } from "../../api/client"`（`:21`）✅
- 三个 overview 区块用 `props.api.getXxx()`（统一 `request()`，带 `X-Member-Token`）✅
- `comment-panel` 用 `platformApi.commentsFull/commentDm`（`@/api/platform` 的 `post<T>`，
  底层同 `request()`）✅

### 🔴 P1｜`LoginDialog` 手写裸 `<img src="/api/...">` —— 违反项目既有铁律，会员门禁下静默破图

- **严重度**：**P1**（登录主路径能力静默失效；且违反项目已明文写入的头号约定）
- **文件**：`src/components/accounts/LoginDialog.tsx:119-123`
- **代码原文**：
  ```tsx
  {/* 本地绝对路径 ⇒ 必须经后端受保护端点取字节 */}
  <img
    src={`/api/accounts/qr-image?path=${encodeURIComponent(st.qrPng)}`}
    alt="登录二维码"
    className="h-[220px] w-[220px] rounded-[10px] border border-[var(--color-border)] bg-white"
  />
  ```
- **为何是问题**（三层证据）：
  1. **端点在会员门禁内**：`backend/main.py:729-751` 的 `member_auth_middleware` 对
     所有 `/api/*` 校验 `X-Member-Token`，`_MEMBER_EXEMPT` 白名单**不含** `/api/accounts/qr-image`
     ⇒ 无令牌访问一律 **401**。
  2. **`<img src>` 无法携带自定义请求头**（浏览器限制）⇒ 该请求**必然**不带令牌 ⇒ **必然 401**。
  3. **项目已明文禁止此写法**：`src/api/client.ts:125` 头注写着
     「调用点统一走 `@/lib/authed-media`，不要在组件里手写」；项目为此专门造了
     `AuthedImg`（`components/ui/authed-img.tsx`）与 `useAuthedMediaUrl`，
     消息原图/视频已全部改用它。本文件是全前端**唯一**残留的裸 `/api/` `<img>`（grep 仅此 1 处命中）。
- **后果**：扫码登录是本项目账号接入**主路径**。会员门禁开启时，用户点「扫码登录」
  只会看到一个**破图图标**（该 `<img>` 无 `onError`，连降级/提示都没有），
  却没有任何错误文案 ⇒ 用户无法判断是「二维码没生成」还是「网络问题」。
  这正是 `authed-media.ts:11-13` 记载的历史缺陷模式（「静默破损、没人发现」）的复发。
- **建议**：改用项目既有组件
  ```tsx
  import { AuthedImg } from "@/components/ui/authed-img";
  <AuthedImg src={`/api/accounts/qr-image?path=${encodeURIComponent(st.qrPng)}`}
             alt="登录二维码" className="h-[220px] w-[220px] ... bg-white" />
  ```
  复用统一封装；`AuthedImg` 自带加载占位与失败降级，可同时消除「破图无提示」。

### 🟤 P4｜`CrawlPolicySection` 的 `push` 契约类型与全局 `PageProps.push` 不一致

- **文件**：`src/components/settings/CrawlPolicySection.tsx:58,60-64`
- **代码原文**：
  ```tsx
  type Push = (msg: string) => void;
  export default function CrawlPolicySection({ push }: { push: Push }) { ... }
  ```
- **对照**：全局 `PageProps.push` 定义（`api/client.ts:2437`）为
  `(msg: string, holdMs?: number) => void`。本组件自造了一个更窄的 `Push` 类型。
- **为何是问题**：签名兼容（更窄）不会编译报错，但属命名/契约漂移：一旦其它调用方
  传入依赖第二参 `holdMs` 的实现，此处类型契约无法表达。属轻度不一致。
- **建议**：直接复用 `PageProps["push"]`，删除本地 `Push` 别名。

### ✅ 字段命名一致性

- 三个 overview 区块的 `queryKey` 精确命中 App 级预热键（`["accounts"]`、`["live-stream"]`），
  与既有 `AiRuntimeSection` 等一致 —— 唯 `["task-history", ...]` 例外（见 §1 P4）。
- `SchedulerResp`/`HistoryResp` 等接口字段（`ok/list/total/error`）与后端返回结构对齐
  （已对账 `api/tasks.py`、`tasks_history.list_history`）。

---

## 5. a11y / 边界态

### ✅ 边界态覆盖明显优于均值（这批组件的亮点）

| 组件 | 加载中 | 错误态 | 空态 | 备注 |
|---|---|---|---|---|
| `LiveStatusSection` | `SkeletonRows` | `Blank`+error 原文 | 三种 `alive=false` 成因分述 | ✅ 明确区分「未启动/等待开播/未连上」，不用 0 冒充未取到 |
| `AccountsHealthSection` | `SkeletonRows` | `Blank`+error | 「尚未添加账号」 | ✅ `unknown`（未校验）单独一档，不并成异常 |
| `TaskHistorySection` | `SkeletonRows` | `Blank`+error | `ok=true && 空` 才允许「暂无」 | ✅ `ok=false` 显式写「不代表没有任务」 |
| `CrawlPolicySection` | `Blank 加载中` | `push(错误)` | 「尚无采集策略」 | ✅ |
| `comment-panel` | `LoadingState` | `ErrorState`+重试 | `EmptyState`（区分 blocked / 真无评论） | ✅ |
| `SchedulerSection` | — | 独立 `q.isError` 分支 | 「暂无定时任务」 | ✅ 错误态明写「按不可用处理，不显示为可用」 |
| `LoginDialog` | Loader 占位 | `st.error` 文案 | — | ✅ 无二维码图不渲染假图 |

### 🟡 P3｜`LoginDialog` 二维码 `<img>` 缺 `onError`（与 §4 P1 同源，单独列出）

- **文件**：`LoginDialog.tsx:119-123`
- **问题**：即便修复令牌问题，该 `<img>` 仍无 `onError` 降级 —— 任何取图失败（404 过期、
  路径校验 403）都会退化成破图而无任何提示。
- **建议**：改用 `AuthedImg`（自带 `onError` → 「图片不可用」占位），与 §4 P1 一并修复。

### 🟤 P4｜`comment-panel` 的 `dmState` 以 `user_uid` 为键，uid 为空时多行互相覆盖

- **文件**：`src/components/platform/comment-panel.tsx:99,122,180-184`
- **代码原文**：`const [dmState, setDmState] = useState<Record<string, string>>({});`
  后续 `setDmState((s) => ({ ...s, [uid]: ... }))`
- **问题**：若某条评论 `user_uid` 为空串（`CommentRow:55` 允许 uid 为空），
  多条空 uid 评论会共用一个 `""` 键 ⇒ 发送状态文案串台。
  实际影响小（无 uid 的评论不渲染发送按钮），但键设计不唯一。
- **建议**：以 `cid` 为键存发送态，或对空 uid 直接跳过写入。

### 🟤 P4｜`CrawlPolicySection.load` 的 `useEffect` 缺依赖（eslint-disable 未加）

- **文件**：`src/components/settings/CrawlPolicySection.tsx:79-81`
- **代码原文**：
  ```tsx
  useEffect(() => { load(); }, []);
  ```
  `load` 是组件内闭包，`[]` 依赖会触发 `react-hooks/exhaustive-deps` 告警
  （本仓库其它文件如 `LoginDialog.tsx:74` 对同类情形显式加了 `eslint-disable`）。
- **问题**：风格不一致；若未来 CI 打开 `--max-warnings 0` 会拦。功能无碍（`push` 稳定）。
- **建议**：补 `// eslint-disable-next-line react-hooks/exhaustive-deps`，或把 `load`
  `useCallback` 化后纳入依赖。

### ✅ a11y 正面项

- `theme-toggle.tsx:30,38`：`title` + `aria-label` 双层标注（「切换到夜间/日间主题」）✅
- `AppearanceSection.tsx:74`：`Switch` 带 `aria-label="日夜主题切换"`，且旁有文字态标注 ✅

---

## 6. 未取证项（诚实标注）

以下项本审计**未能取得运行态证据**，不得据此判「已通过」：

1. **主题深/浅两态的真实浏览器渲染**：仅核对了 token 定义与持久化代码路径，
   未启动前后端实拍两态截图。建议 `frontend-visual-verification` 补证。
2. **P1 二维码 401 的真机复现**：结论由「中间件白名单 + `<img>` 无法带头的浏览器约束」
   **静态推导**得出（证据链：`main.py:729-751` 豁免清单 + `client.ts:125` 项目自述
   曾实测「无令牌一律 401」）。未在会员门禁开启态下真机抓一次 401。
   —— 反驳该结论只需一步：确认 `qr-image` 曾被加入 `_MEMBER_EXEMPT`。经查**从未加入**
   （`grep "qr-image" backend/ --include=*.py` 仅命中 `api/accounts.py:995` 路由定义，
   无豁免登记）⇒ 静态结论成立。
3. **`/api/tasks/scheduler` 端点真实返回**：仅对账后端 `get_state()` 会下发
   `scheduler_enabled`/`auto_send_enabled`（`task_scheduler.py:428-429`），未 curl 实测。
4. **`grid` 视图下三个 F2 区块不可见**是否属预期：`overview-page.tsx:168` 有
   `viewMode === "grid"` 分支，F2 区块仅在 single 分支 ⇒ 切到「网格」视图时不可见。
   默认视图是 single，故判「可达」；但若产品预期两视图都能看到 F2 区块，则属缺陷。
   **需产品确认**。

---

## 7. 发现清单（按严重度）

| ID | 严重度 | 位置 | 一句话 |
|---|---|---|---|
| F-1 | **P1** | `LoginDialog.tsx:119-123` | 裸 `<img src="/api/accounts/qr-image...">` 违反项目铁律，会员门禁下必 401，扫码登录静默破图 |
| F-2 | P3 | `platform-page.tsx:588-600` | `hidden md:flex` 使评论面板在窄屏整块消失，且是唯一挂载点 |
| F-3 | P3 | `LoginDialog.tsx:119-123` | 二维码 `<img>` 无 `onError`，取图失败无提示（与 F-1 同处，一并修） |
| F-4 | P4 | `TaskHistorySection.tsx:89` | queryKey `["task-history",0,5]` 与 App 预热键 `["task-history",0]` 不等 ⇒ 重复轮询 |
| F-5 | P4 | `CrawlPolicySection.tsx:58` | 自造 `Push` 类型窄于全局 `PageProps.push`（缺 `holdMs?`） |
| F-6 | P4 | `comments-panel.tsx:99` | `dmState` 以 `user_uid` 为键，空 uid 多行串台 |
| F-7 | P4 | `CrawlPolicySection.tsx:79-81` | `useEffect([])` 调 `load` 缺 eslint-disable，与仓库风格不一致 |
| F-8 | P4(改进) | `SchedulerSection.tsx:184` | `auto_send_enabled` 无独立休眠徽标，仅文本行 |

**统计：P0 = 0，P1 = 1，P2 = 0，P3 = 2，P4 = 5，合计 8 条。**

---

## 8. 四关裁决（按 `capability-delivery-verification`）

| 关 | 判据 | 结论 | 证据 |
|---|---|---|---|
| 1 可运行 | 真实构建通过 | ✅ | `npm run build` → `✓ built in 6.59s` |
| 2 可发现 | `grep` 引用点 > 0 | ✅ 10/10 | 见 §1 引用点表（含 nav/入口登记） |
| 3 可达 | 渲染点 + 调用边存在 | ⚠️ 9/10 完全达标；`comment-panel` 窄屏不可达 | 见 §1 |
| 4 默认态 | 风险能力默认关闭 + 警示 | ✅ 9/9 | 后端 env 默认 False + 前端休眠横幅 |

**总评：本批新增组件在「防孤儿」与「默认态合规」两个历史高频失守项上表现优秀
（10/10 有引用、休眠态呈现完整且防假成功做得到位）。唯一致命问题是
`LoginDialog` 的二维码取图绕过了项目自建的会员鉴权媒体封装（P1），
会使扫码登录在主路径上静默失效。**
