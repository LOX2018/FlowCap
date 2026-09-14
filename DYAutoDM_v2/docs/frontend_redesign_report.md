# 前端重设计完成报告（对标 better-douyin）

- **分支**：`design/better-douyin`
- **版本**：v0.43.16
- **提交**：`f3ebea5`（外壳）→ 本次（功能移植 + 页面迁移，待提交）
- **日期**：2026-09-14

---

## 一、交付内容

### 技术栈（方案乙：完整替换）

| 项 | 变更 |
|---|---|
| 样式 | 手写 CSS → **Tailwind 4**（`@theme` 令牌） |
| 组件库 | 无 → **Radix UI 全套**（18 个包） |
| 变体系统 | 无 → **CVA** |
| 工具 | 无 → **clsx + tailwind-merge**（`cn()`） |
| 图标 | 无 → **lucide-react** |
| 依赖数 | 9 → **29** |

### 新增文件（21 个）

```
src/styles/tokens.css       设计令牌 + 浅色主题 + 玻璃工具类 + 关键帧
src/styles/bridge.css       桥接层（旧变量 → 新令牌）
src/lib/utils.ts            cn() / fmtNum / fmtTime / fmtAgo
src/components/ui/*.tsx     15 个组件（见下）
src/components/layout/      sidebar.tsx / topbar.tsx / app-shell.tsx
src/preview-shell.tsx       设计系统预览入口
preview.html                预览页（无后端即可验证）
docs/frontend_redesign_spec.md  设计契约
```

**15 个 UI 组件**：button / card / badge / input / switch / dialog / tabs /
select / progress / skeleton / tooltip / separator / scroll-area /
status-dot / empty-state

### 设计令牌（对标蓝本，主色改本项目绿）

```
背景   #0e0e14 / #0a0a0f      文字   #e8e8ed / #9b9baa / #8b8b9e
主色   #68cb6e（本项目绿）      语义   成功 #00d68f / 信息 #7c5cfc
                                      警告 #ffaa00 / 危险 #ff4757
圆角   8 / 12 / 18 / 24 / 32px
缓动   cubic-bezier(0.2, 0, 0, 1)（弹簧）
布局   sidebar 240px / collapsed 68px / topbar 36px
```

> **与蓝本的差异**：蓝本主色是抖音红 `#FE2C55`；本项目保留自己的绿
> `#68cb6e`，只吸收其**令牌结构**与**材质手法**。

### 布局骨架（对标蓝本）

```
┌ TopBar（拖拽 + 连接状态 + 会员徽章 + 窗口控制）──────┐
│        │                                            │
│Sidebar │       内容区（懒加载 + 切换动画）           │
│ 三组    │                                            │
│ 240px  │                                            │
│ 可收起  │                                            │
└────────┴────────────────────────────────────────────┘
```

**导航三组编排**（用户选定）：
- **核心**：总览 / 私信 / 直播
- **内容**：采集 / AI 获客 / 知识库
- **系统**：账号 / 任务 / 通知 / 日志 / 设置

> 修正：旧 `TABS` 只有 9 项，**缺 `kb`（知识库）与 `notify`（通知）** ——
> 这两页此前只能靠代码跳转进入，导航上不可见。新导航已补齐 11 项。

---

## 二、关键设计决策：桥接层

### 问题（实测发现）

`global.css` 在 `:root` **硬编码**了另一套 oklch 变量（`--bg`/`--surface`/
`--fg`/`--accent`…），且因加载顺序在 `tokens.css` 之后 → **覆盖新令牌**
（实测：body 背景仍是 `oklch(0.155 0.012 245)`，不是新令牌的 `#0e0e14`）。

但 11 个业务页面（17370 行）**全部使用旧变量名**，一次性改完风险过高。

### 解法

新增 `bridge.css`（**最后加载**），把旧变量名映射到新令牌：

```css
:root {
  --bg:     var(--color-background);
  --fg:     var(--color-text);
  --accent: var(--color-accent);
  --muted:  var(--color-text-secondary);
  ...
}
```

**效果**：旧页面**零改动**即刻继承新配色与材质；再按页渐进替换为新组件。

> ⚠️ **加载顺序不可换**（先后换过会失效，已实测）：
> `tokens.css` → `global.css` → `theme-glass.css` → **`bridge.css`**

---

## 三、实机验证（全部通过）

| 验证项 | 方法 | 结果 |
|---|---|---|
| 类型检查 | `npx tsc -b` | **EXIT=0**，零错误 |
| 构建 | `npx vite build` | 2325 模块 / 4.29s / CSS 90KB(gzip 17KB) |
| 布局渲染 | 浏览器读 DOM | Sidebar 240px ✅ 三组 ✅ 11 项 ✅ |
| 令牌生效 | 读计算样式 | `--color-background=#0e0e14`、`--color-accent=#68cb6e` ✅ |
| **桥接生效** | 读旧变量 | `--bg=#0e0e14`、`--accent=#68cb6e`（不再是 oklch）✅ |
| 材质 | 读计算样式 | 卡片 `radius=12px`、`backdrop-filter=blur(24px)` ✅ |
| 视觉核对 | vision 读截图 | 侧栏+分组+TopBar+卡片正常，无错位/冲突 ✅ |
| 对比度 | vision 反馈 | 辅助文字偏暗 → 已修（`#7a7a8e` → `#8b8b9e`） |

**验证手段说明**：全部读**计算样式**与**DOM 结构**，不看源码推断；
视觉用 vision 读真实截图（非猜测）。

---

## 四、页面迁移（第二批 · 已完成）

外壳（第一批）只换了 chrome，11 个业务页面靠桥接层继承外观。
本批把页面**逐页迁到新设计体系**（脱离旧 CSS 类）。

### 4.1 页面级组件族（新增 `src/components/page/kit.tsx`，490 行）

把 11 个页面反复出现的模式收敛为具名组件，消灭每页各写一遍的重复：

| 组件 | 替代的旧写法 |
|---|---|
| `Section` | `.card` + `.section-head` |
| `Stat` / `StatRow` | `.stat` / `.num` / `.label` / `.unit` / `.delta` |
| `Row` / `RowText` | `.acct-row` / `.head-row` / 手写 flex 行 |
| `KeyValue` | `.k` / `.v` / `.field` |
| `Tone` | `.Pill` / `ui.tsx` 的 `Pill` |
| `SkeletonRows` | `.sk` 骨架屏 |
| `Blank` | `.blank` 空态 |
| `SegmentedTabs` | 手搓的 `.seg` / `.btn.ghost` 切换 |
| `Toolbar` | `.head-row` + 右侧按钮组 |
| `Collapse` | notify/ai/kb 三页各自实现的折叠卡片 |
| `FormField` | 各页内联 label+hint 结构 |

### 4.2 迁移进度

| 页面 | 行数 | 状态 |
|---|---|---|
| `overview.tsx` | 364 | ✅ |
| `platform.tsx` | **新增** | ✅（对标蓝本内容面） |
| `crawl.tsx` | 505 | ✅ |
| `settings.tsx` | 122 | ✅ |
| `notify.tsx` | 330 → 349 | ✅（去重后复用 kit.Collapse） |
| `tasks.tsx` | 409 | ✅ |
| `logs.tsx` | 447 | ✅ |
| `ai.tsx` | 543 | ✅ |
| `kb.tsx` | 670 | ✅ |
| `messages.tsx` | 1229 → 1410 | ✅ |
| `live.tsx` | 1891 → 2023 | ✅ |
| `accounts.tsx` | 2058 → 2218 | ✅ |

**12 个页面全部迁移完成**，全部旧 CSS 类残留为 **0**（实测逐页 grep）。

### 4.3 功能面移植（用户要求「全部新增移植」）

新增 `backend/api/platform.py`（**12 个端点**）+ `frontend/src/api/platform.ts` +
`frontend/src/pages/platform.tsx`，把基座已有的平台能力接成「最后一公里」：

| 端点 | 基座方法 | 说明 |
|---|---|---|
| `POST /api/platform/feed` | `get_feed` | 推荐流 |
| `POST /api/platform/user/works` | `get_user_all_work_info` | 用户作品（自带翻页） |
| `POST /api/platform/user/info` | `get_user_info` | 用户资料（**响应自带昵称，不补查**） |
| `POST /api/platform/search` | `search_some_general_work` / `search_some_user` | 搜索 |
| `POST /api/platform/collected` | `get_collect_list` | 收藏夹 |
| `POST /api/platform/liked` | `get_user_favorite` | 点赞列表 |
| `POST /api/platform/relation/list` | `get_user_follower_list` / `following_list` | 关注/粉丝 |
| `POST /api/platform/notice/list` | `get_notice_list` | 站内通知 |
| `POST /api/platform/comments` | `get_work_out_comment` | 作品评论 |
| `POST /api/platform/action/digg` | `digg(auth, aweme_id, digg_type)` → bool | 点赞/取消 |
| `POST /api/platform/action/collect` | `collect_aweme` | 收藏/取消 |
| `POST /api/platform/action/follow` | — | **显式 501**（基座无 follow 写方法，不假装成功） |

**风控约束（写进代码注释，铁律级）**：
1. 复用账号 `.env` 凭证被动签名（与手动网页浏览同源）；
2. **绝不批量补查用户信息** —— 响应自带昵称直接用；
3. 所有请求由**用户显式动作触发**，不做后台轮询；
4. `DouyinAPI` 是同步 requests → 一律 `asyncio.to_thread` 包裹，避免阻塞事件循环。

### 4.4 实机验证（全部通过）

| 验证项 | 方法 | 结果 |
|---|---|---|
| 类型检查 | `npx tsc -b` | **EXIT=0**，零错误（多轮） |
| 平台路由注册 | `TestClient` 真发请求 | **6/6 通过** |
| 基座方法核对 | `hasattr` 逐个检查 | **14/14 全在** |
| 令牌生效 | 读计算样式 | `--color-accent=#68cb6e`、`--background=#0e0e14` ✅ |
| 页面渲染 | 浏览器读 DOM | 总览/采集/任务等 9 页结构完整 ✅ |
| 采集页交互 | 输入→搜索→抽屉 | 4 卡片 / 3 评论 / 抽屉开闭正常 ✅ |
| **对比度（WCAG）** | 计算相对亮度比 | 次要文字 **7.66:1**、弱化 **6.28:1** —— 均超 AA(4.5:1) ✅ |
| 12 页旧类残留 | 逐页 grep | **全部 0** ✅ |
| 3 巨型页渲染 | 浏览器读 DOM | 私信 29831 / 直播 55977 / 账号 38096 字符，无异常 ✅ |

### 4.5 巨型页迁移的独立验证（不采信子代理自述）

三个巨型页（5178 行）由子代理并行迁移。**子代理的自述不是证据**，故逐项独立核对：

| 验证项 | 方法 | 结果 |
|---|---|---|
| API 调用参数 | 括号配对逐字节比对 | messages **16 处**、live **8 种**、accounts **7 种** —— **全部一致** |
| `useQuery` 配置 | 比对 queryKey / refetchInterval / enabled | **逐项一致**（5000ms 轮询保留） |
| 关键业务函数 | 规范化后比对函数体 | `isSystemTip` / `parseMedia` / `sanitizeDataUri` / `mapAcct` / `enginePill` / `parseDelayRange` / `acctValid` / `localClassifyFail`(1401字符) / `failInfoOf` / `toDmStatus` / `recordsToRows` —— **原样保留** |
| 唯一函数差异 | `renderTextWithEmoji` 逐行 diff | 仅 CSS 类名（`.emoji` → Tailwind），正则与 `EMOJI_MAP` 查找**未动** |
| 中文业务文案 | 单行字面量比对 | accounts **0 丢失**；live/messages 共 5 处差异均为 **emoji → lucide 图标**，文字本体保留 |
| **风控红线** | 检索主动批量查昵称的调用名 | **0 命中**（三页均未引入） |

> ⚠️ **测试本身也会错**：首轮用跨行正则比对"丢失文案"，产出 285 条垃圾结果；
> 修正为单行匹配后才得到可信结论。**判据错了，结论就不可信** —— 故测试脚本必须自检。

### 4.6 对子代理"疑虑项"的实证裁定

子代理在报告中列出若干不确定项。**自述不是证据**，逐项实测裁定：

| 子代理疑虑 | 实测方法与结果 | 裁定 |
|---|---|---|
| `pt-4.5` 可能不是有效 Tailwind 刻度，内边距会失效 | 构建 CSS 产物后 grep：**生成了** `.pt-4\.5{padding-top:calc(var(--spacing) * 4.5)}`，且 `--spacing:.25rem` 已定义 → 18px | ❌ **不成立**（Tailwind4 支持任意倍数刻
度） |
| `px-4.5` 同理 | 同上，**生成** `.px-4\.5{padding-inline:calc(var(--spacing) * 4.5)}` | ❌ **不成立** |
| `--muted-foreground` 未定义 → 旧代码该变量无效，改成 `--color-text-muted` 造成"唯一视觉语义变化" | 实测 `getComputedStyle(documentElement).getPropertyValue('--muted-foreground')` = **`#9b9baa`**（`theme-glass.css:24` 定义为 `var(--muted)`） | ❌ **不成立**（该变量**有**定义，且解析值与新令牌同色） |
| `color-mix` 依赖浏览器支持，未实测 | 浏览器实测：**生效**（accounts 9 个 / live 9 个 / messages 5 个元素使用） | ✅ 支持良好 |
| 未做运行时渲染验证 | 浏览器实测：三页 DOM 29842 / 55988 / 38107 字符，**0 渲染异常**，0 个透明文字元素 | ✅ 已补验 |

> **结论**：子代理提出的 3 项"潜在缺陷"经实测**全部不成立**。
> 这类"过于谨慎的自述"同样需要证伪 —— 既不轻信"我做完了"，也不轻信"可能有问题"。

> ⚠️ **以事实纠正观感**：vision 曾判"对比度不足"，实测 6.28~7.66:1 **达标**；
> 曾判"无玻璃拟态"，实测 `backdrop-filter` 确实生效（深色下较含蓄）。以实测为准。

---

## 五、验证方法论（可追溯）

1. **不看源码推断** —— 一律读**计算样式**（`getComputedStyle`）与 **DOM 结构**。
2. **不信观感** —— vision 的视觉判断只作线索，量化结论必须用实测数值（如 WCAG 比值）裁定。
3. **判据要能区分真伪** —— 例：验证路由注册时，本项目对"账号未登记"也返回 404，
   故不能只看状态码，必须**读响应体**区分 `{"detail":"Not Found"}`（未注册）
   与 `{"detail":"账号 x 未登记"}`（已注册）。
4. **抓缺陷要抓真因** —— 页面渲染空白时，真因是预览 harness 缺
   `QueryClientProvider`（React Query 抛错），不是页面本身有缺陷。

---

## 六、遗留与下一步

1. ~~三个巨型页迁移~~ **已完成**（见 §4.2）
2. `theme-glass.css` 在页面全迁完后可下线
3. `global.css`（884 行）最终可移除（`bridge.css` 是过渡层）
4. `preview.html` / `preview-kit.html` / `preview-pages.html` 是开发工具，
   正式发布可不打包（Vite 多入口按需）
5. `dy_apis` 无 follow 写方法 → `action/follow` 返回 501（如需启用须先补基座链路）

---

*本文件记录前端重设计的完成状态与验证证据。*
