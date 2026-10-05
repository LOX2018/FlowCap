# 前端重设计设计契约（对标 better-douyin）

- **分支**：`design/better-douyin`
- **版本**：v0.43.15
- **日期**：2026-09-14
- **目标**：以 better-douyin 的**设计体系**为蓝本，重设计本项目前端。

---

## 一、两条技术路线（必须选择）

| | 当前项目 | better-douyin（蓝本） |
|---|---|---|
| 样式 | **手写 CSS**（`global.css` 884 行 + `theme-glass.css`） | **Tailwind 4** + `@theme` 设计令牌 |
| 组件库 | **无**（自建 `ui.tsx`、Glass* 三个） | **Radix UI** 全套（dialog/dropdown/popover/tabs/toast…） |
| 变体系统 | 无 | **CVA**（class-variance-authority） |
| 图标 | 自绘/无 | **lucide-react** |
| 语言 | React **18** | React 19 |
| 状态 | zustand 4 + React Query 5 | zustand 5 |
| 布局 | 顶部横向 Tab（`TopNav`） | **左侧栏 Sidebar + 底部栏 BottomBar** |

**这是范式的差异，不是样式的差异。**「对标重设计」有两种解读，须先定：

### 方案甲：移植设计语言（推荐）
保持当前技术栈（手写 CSS + React 18），把蓝本的**设计令牌、视觉语言、布局骨架**移植过来：
- 引入蓝本的设计令牌体系（深色底 `#0e0e14` + 抖音红 `#FE2C55` + 语义色 + 圆角/阴影/缓动）
- 顶部 Tab → **左侧栏 Sidebar**（可收起）+ 底部状态栏
- 补齐组件层（Card/Button/Badge/Dialog/Toast 变体体系）
- **不引入** Tailwind/Radix（避免 400+ 包与重构风险）

### 方案乙：完整技术栈替换
引入 Tailwind 4 + Radix UI + CVA + lucide-react，按蓝本结构重写 11 个页面。
- 收益：与蓝本**同构**，可直接复用其组件思路
- 代价：**重写 17370 行前端**；新增约 20 个 npm 依赖；Tauri 打包体积上升；回归面很大

---

## 二、蓝本设计令牌（已提取，供两个方案共用）

```css
/* 深色（默认） */
--color-background:      #0e0e14    /* 页面底 */
--color-background-soft: #0a0a0f    /* 更深 */
--color-surface:         rgba(255,255,255,0.04)
--color-surface-solid:   #16161e    /* 卡片实底 */
--color-surface-raised:  rgba(255,255,255,0.07)
--color-border:          rgba(255,255,255,0.08)
--color-border-strong:   rgba(255,255,255,0.14)
--color-text:            #e8e8ed
--color-text-secondary:  #9b9baa
--color-text-muted:      #7a7a8e

--color-accent:          #FE2C55    /* 抖音红 */
--color-accent-hover:    #ff4d73
--color-success:         #00d68f
--color-info:            #7c5cfc
--color-warning:         #ffaa00
--color-danger:          #ff4757

--radius-sm: 8px  --radius-md: 12px  --radius-lg: 18px
--radius-xl: 24px --radius-2xl: 32px

--ease-spring: cubic-bezier(0.2, 0, 0, 1)
--duration-fast: 150ms  --duration-base: 250ms  --duration-slow: 400ms

--sidebar-width: 240px  --bottombar-height: 42px
```

**浅色主题**（`[data-theme="light"]`）：底 `#f5f5f7`、实底 `#ffffff`、文字 `#1d1d1f`。

### 关键视觉手法（蓝本）
| 手法 | 实现 |
|---|---|
| 玻璃拟态 | `surface-solid/70` + `backdrop-blur-3xl` + 1px 白边 + 大阴影 |
| 主色辉光 | `shadow-[0_0_40px_-12px_rgba(254,44,85,0.35)]` |
| 弹簧缓动 | 所有过渡走 `--ease-spring`，`active:scale-[0.96]` |
| 微噪点/星云 | `orb-drift` / `nebula-pulse` 关键帧背景 |
| 细滚动条 | 5px、`rgba(128,128,128,0.12)` |

---

## 三、蓝本布局骨架

```
┌────────────────────────────────────────────────┐
│  拖拽区 36px（窗口控制：最小化/最大化/关闭）        │
├──────────┬─────────────────────────────────────┤
│          │                                     │
│ Sidebar  │       内容区（按 view 切换）          │
│ 240px    │                                     │
│ 可收起    │  - 懒加载：React.lazy + Suspense      │
│          │  - 滚动容器按 view 单独声明            │
│ 首页/搜索  │                                     │
│ 用户主页   │                                     │
│ 推荐视频   │                                     │
│ 我的下载   │                                     │
│ 点赞/收藏  │                                     │
│ 通知/好友  │                                     │
│ 监控/设置  │                                     │
├──────────┴─────────────────────────────────────┤
│  BottomBar 42px（可扩展到 320px）                │
└────────────────────────────────────────────────┘
```

**蓝本 Sidebar 导航项**（11 项）：首页 / 搜索 / 用户主页 / 推荐视频 / 我的下载 /
点赞视频 / 收藏视频 / 通知 / 好友 / 监控 / 设置

---

## 四、本项目页面 → 蓝本导航映射

本项目 11 个页面（`App.tsx` 的 TabId）：

| 本项目 | 功能 | 蓝本对应 | 建议导航位置 |
|---|---|---|---|
| `overview` | 总览 | home | 首页 |
| `messages` | 私信 | friends-status | 私信（核心） |
| `live` | 直播监听 | （无，本项目特有） | 直播 |
| `crawl` | 数据采集 | search（部分） | 采集 |
| `ai` | AI 获客 | automation 的 AI 部分 | AI |
| `kb` | 知识库 | （无，本项目特有） | 知识库 |
| `accounts` | 账号管理 | settings 的账号部分 | 账号 |
| `tasks` | 任务 | automation | 任务 |
| `notify` | 通知渠道 | notices | 通知 |
| `logs` | 日志 | （无） | 日志 |
| `settings` | 设置 | settings | 设置 |

**结论**：本项目业务域与蓝本**不完全重合**（无下载器/点赞/收藏；有直播/知识库/采集）。
故导航项应为**本项目业务**重新编排，而非照抄蓝本的 11 项。

---

## 五、待用户决策

**方案甲 vs 方案乙** —— 这决定后续所有工作量级：

| | 甲：移植设计语言 | 乙：替换技术栈 |
|---|---|---|
| 改动 | CSS 令牌 + 布局骨架 + 组件层 | 全套重写 |
| 依赖 | 不增 | +20 个 |
| 风险 | 低（可增量、可回退） | 高（17370 行重写） |
| 与蓝本相似度 | 视觉 90% / 结构 70% | 视觉+结构 100% |
| 工期 | 分批可交付 | 需一次性完成才可运行 |

---

*本文件为设计基准，方案确定后据此实施。*
