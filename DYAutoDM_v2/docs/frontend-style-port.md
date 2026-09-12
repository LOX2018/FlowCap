# 前端设计风格对标说明 —— satelite-proxy → DYAutoDM_v2

> **状态：已落地（2026-09-11 校准）**
>
> 本文最初是**阶段一分析产物**（当时只做比对、未改代码）。此后移植已实施，
> 现更新为**实施后状态记录**：哪些照搬、哪些按本项目改造、哪些仍未接入。
> 源：`https://github.com/zn0wii/satelite-proxy`；目标：`DYAutoDM_v2/frontend`。

---

## 一、源项目设计系统（satelite-proxy）

源码自称 **`style3 — macOS glass + mission console`**：以 **macOS 毛玻璃**为材质基底，叠加**航天任务控制台**的信息密度与氛围光。三个支柱：

1. **玻璃材质**：交互控件（按钮/分段器/开关）同属一族「磨砂胶囊」，靠 `backdrop-filter` + 半透明白渐变存活。
2. **氛围光（glow）**：页面背后一层随主题/主色变化的径向渐晕，顶栏胶囊自带 `::after` 光晕层。
3. **双主题 + 主色可换**：`day`（亮）/ `aerospace`（暗）两套 token，主色 6 组预设 + 自定义 hex，**同一主色在两主题下各有专门明度变体**。

关键设计主张：**暗主题没有卡片阴影**（`--shadow-card: none`）——暗色下的「浮起感」全靠玻璃面 + 描边，不靠投影。

---

## 二、移植实施结果（对照当初的 4.1 / 4.2 / 4.3 建议）

### ✅ 已落地

| 项 | 落地位置 | 说明 |
|---|---|---|
| **双主题引擎** | `theme/ThemeContext.tsx`（180 行）+ `styles/theme-glass.css`（1,110 行） | `data-theme` 驱动 `day` / `aerospace`；`theme-glass.css` 中 `data-theme` 出现 25 处 |
| **主色引擎** | `theme/accents.ts`（261 行） | 6 组马卡龙预设（薄荷/天蓝/香芋/蜜桃/奶橙/湖蓝），每色给 `aerospace` + `day` 两套 hex；`applyAccentToDom()` 运行时注入 |
| **变量改名对齐** | `accents.ts` 头部注释 | `--primary`→`--accent`、`--primary-muted`→`--accent-bg`、`--on-primary`→`--accent-ink` 等，**避免 22 个页面全量改名** |
| **语义色保护** | 同上 | `--ok / --warn / --danger` 不受 accent 影响（源项目同样刻意不动 `--success`） |
| **玻璃控件族** | `styles/theme-glass.css` | `glass-btn`×20 / `glass-seg`×13 / `glass-switch`×28 / `topnav`×38 规则已写入 |
| **氛围光** | 同上（`--glow`×11 处） | 随主题/主色变化的 glow token |
| **主题持久化** | `ThemeContext.tsx` | `localStorage` + `data-theme`；含 `glass-frost` 降级开关 |
| **默认观感零变化** | `accents.ts` 注释 | 默认 `green` 预设的 aerospace 色沿用改造前的 `oklch(76% 0.16 145) ≈ #68cb6e`，老用户无感 |

### ⚠️ 尚未接入（保留现状）

| 项 | 现状 | 说明 |
|---|---|---|
| `GlassButton.tsx` | **零引用** | 组件已写，但仅 `GlassSwitch.tsx` 内部引用；无页面导入 |
| `GlassSeg.tsx` | **零引用** | 同上 |
| `GlassSwitch.tsx` | **零引用** | 同上（三组件互相引用，形成闭环但无外部调用方） |

> 亦即：**材质 CSS 已生效**（`theme-glass.css` 参与构建），但**这三层封装组件是「护栏文件」**——留着备用，页面仍用 `btn/seg/card` 等 `framework.js` 遗留类名。已实测 `import ... from './GlassButton'` 在 `src` 下为 **0 处**。

### ⏸ 保留本项目主张（未照搬源项目）

| 项 | 源项目 | 本项目 | 理由 |
|---|---|---|---|
| 等宽字体 | `--mono = var(--font)`（刻意取消） | 保留 `--font-mono`（真等宽） | 本项目大量用其做数字对齐（`.mono` / `.stat .num` / `.feed-item .tm`） |
| 页面底纹 | 径向 glow 渐晕 | `body` 44px 网格线 + glow | 二者叠加需调，当前保留网格 |
| class 命名 | `glass-*` / `topnav-*` | `btn/seg/card/pill/tab…` | `ui.tsx` 注释明确「与旧版 `framework.js` 完全一致」，改名会连带影响其它组件 |

---

## 三、迁移时必须遵守的项目铁律（已核对 guard skill）

- 目标 `ui.tsx` 注释明确：**class 命名与旧版 `framework.js` 完全一致** —— 换材质可以，**改名会连带影响其他组件，须全链路验证**。
- 样式改动历来是事故高发区（知识库记载「CSS 改动需全链路验证（曾改废）」），移植应从「token 层」往「组件层」推进，每步在实机确认。
- **验证方式**：`global.css` 884 行 + `theme-glass.css` 1,110 行；`theme/` 目录 2 文件（441 行）；`ui.tsx` 126 行（导出 `Spark/Pill/Avatar/Dot/Num` + `tick/nowHM/pick/hue` 工具）。

---

## 四、规模现状（2026-09-11 实测）

| 项 | 数值 |
|---|---|
| 前端 `.tsx` / `.ts` | 28 / 5 |
| 页面 | 11 个 / 8,490 行 |
| 组件 | 14 个 |
| 样式 | `global.css` 884 行 + `theme-glass.css` 1,110 行 |
| 主题文件 | `ThemeContext.tsx` 180 行 + `accents.ts` 261 行 |

---

*本文档为对标记录，不含待办承诺；后续如接入 `Glass*` 组件族请同步更新本章「尚未接入」表。*
