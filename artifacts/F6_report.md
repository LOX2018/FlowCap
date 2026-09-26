# ADR-018 F6 — 日夜主题切换（前端入口）交付报告

日期：2026-09-27　项目：DYAutoDM_v2　版本：v0.45.40（**未改动任何版本号文件**）

---

## ① 改动文件清单（绝对路径）

**新增（2）**

| 文件 | 说明 |
|---|---|
| `C:\Users\LOX\Desktop\DYchajian\DYAutoDM_v2\frontend\src\components\settings\AppearanceSection.tsx` | 设置页「外观」卡 —— **主入口** |
| `C:\Users\LOX\Desktop\DYchajian\DYAutoDM_v2\frontend\src\components\layout\theme-toggle.tsx` | 顶栏快捷切换按钮 `ThemeToggleButton`（附属） |

**修改（2）**

| 文件 | 改动 |
|---|---|
| `C:\Users\LOX\Desktop\DYchajian\DYAutoDM_v2\frontend\src\components\settings\settings-page.tsx` | 新增 `AppearanceSection` import；`general`（通用配置）tab 内、在 `UnifiedConfigSection` 之后追加 `<AppearanceSection />` |
| `C:\Users\LOX\Desktop\DYchajian\DYAutoDM_v2\frontend\src\App.tsx` | 新增 `ThemeToggleButton` import；顶栏 `topRight` 首位插入 `<ThemeToggleButton />`（在「待授权」徽章与「退出」之前） |

**未触碰（红线核对，git status 实机取证）**
- `src/theme/**` —— 0 改动（上游 satelite-proxy 移植资产，保持原样）
- `backend/**` —— 0 改动
- `package.json` / `tauri.conf.json` / `Cargo.toml` 等版本源 —— 0 改动
- 未执行 git add / commit / checkout

git status 实际输出：
```
 M DYAutoDM_v2/frontend/src/App.tsx
 M DYAutoDM_v2/frontend/src/components/settings/settings-page.tsx
?? DYAutoDM_v2/frontend/src/components/layout/theme-toggle.tsx
?? DYAutoDM_v2/frontend/src/components/settings/AppearanceSection.tsx
```

---

## ② 用了哪个现成 ui 组件、为什么选它

主入口用的是 **`src/components/ui/switch.tsx` 的 `Switch`（Radix `@radix-ui/react-switch` 封装）**。

选它的理由（实测，非推测）：
- **它是全项目「布尔偏好」的既有范式** —— `Switch` 已在 6 处复用（logs-page / notify-page / RoomConfigPage / RoomManagePage / reply-kb / preview-shell），语义一致，用户已熟悉。
- **项目铁律要求复用 `components/ui/`，禁止自研控件**；`ui/` 目录下无 Segmented / Button.Group 组件（`button.tsx` 只有 variant×size 的 CVA 体系，无分组能力），`kit.tsx` 的 `SegmentedTabs` 是页签组（定位是页面级 tab 切换，非设置项），因此开关是最贴合的既有件。
- **二值语义天然对齐** —— 主题只有 `day` / `aerospace` 两个值，无中间态，开关比下拉少一次点击。

配套复用的既有件（同样不自研）：
- 卡片骨架：`components/page/set-card` 的 `SetCard` / `SetCardHead` / `SetCardBody` —— 与 `McpSection`、`settings-shared.SectionBlock` 同源，保证设置页视觉一致。
- 顶栏按钮：`components/ui/button` 的 `Button`（`variant="ghost" size="icon-sm"`，再覆盖成顶栏既有 7×8 规格，与最小化/最大化/关闭同高）；图标 `Sun` / `Moon` 来自已在依赖里的 `lucide-react`。
- 主题状态获取：`useTheme()`（`src/theme/ThemeContext.tsx`，只读调用，未修改该文件）。

**「当前哪个主题」不靠猜**：开关两侧各放一个 Moon / Sun 图标（当前态高亮主色），右侧再补一行明文 `日间` / `夜间`，卡头右上角还有一枚状态徽章 —— 三处一致回显，避免「checked 到底是亮还是暗」的歧义。

---

## ③ 验收命令与真实 exit code

工作目录：`C:\Users\LOX\Desktop\DYchajian\DYAutoDM_v2\frontend`

### A. `npm run lint`
```
$ npm run lint
npm notice run dyautodm-frontend@0.45.40 lint
npm notice run tsc --noEmit
LINT_EXIT=0
```
**exit code = 0**
（注：本项目 `package.json` 的 `lint` 脚本即 `tsc --noEmit`，与 B 同源；两者都实跑。）

### B. `npx tsc --noEmit`
```
$ npx tsc --noEmit
npm notice run dyautodm-v2@0.45.40 npx
npm notice run tsc --noEmit
TSC_EXIT=0
```
**exit code = 0**

基线对照：改动前先跑了一次 `npx tsc --noEmit` → `BASELINE_TSC_EXIT=0`，说明本仓库改动前即干净，改动后仍为 0，非「本来就报错」。

### C. 未修改 `src/theme/` —— 见 ① 的 git status 实机输出，`src/theme/` 无条目。

### D. 未启动 dev server（按红线，视觉验收由用户明早在桌面自己做）。

---

## ④ 切换后会发生什么（引 ThemeContext 实际代码）

两处入口调用的都是同一个 `setTheme`（`src/theme/ThemeContext.tsx:130-136`）：

```ts
const setTheme = useCallback(
  (next: ThemeId) => {
    setThemeState(next);          // ① React state 更新 → 全树重渲染，UI 立刻变色
    applyThemeToDom(next, accent, glow);   // ② 写 DOM + 持久化
  },
  [accent, glow],
);
```

`applyThemeToDom`（`ThemeContext.tsx:83-90`）一次做四件事：

```ts
export function applyThemeToDom(theme: ThemeId, accent: string, glow: string): void {
  document.documentElement.dataset.theme = theme;                                   // 1
  document.documentElement.style.colorScheme = theme === "day" ? "light" : "dark";  // 2
  applyAccentToDom(accent, theme);                                                  // 3
  applyGlowToDom(glow, accent, theme);                                              // 4
  persist(theme, accent, glow);                                                     // 5
}
```

逐条说明：
1. **`<html data-theme="day|aerospace">`** —— 全站 CSS 变量（`styles/tokens.css`）由此切换，**点击即生效、无需刷新、无需重启**。
2. **`style.colorScheme`** 同步切换 —— 让 WebView2 / WKWebView 里的**原生表单控件**（`<select>`、滚动条、日期选择器）跟着变亮/变暗，否则暗色主题下原生下拉仍是白底。
3/4. **主色（accent）与氛围光（glow）按新主题重算** —— 保证同一个 accent id 在亮/暗两套底上都有可读对比度（因为主题是它们的入参）。
5. **持久化**：`persist`（`ThemeContext.tsx:72-80`）写入 localStorage ——

```ts
localStorage.setItem(THEME_KEY, theme);   // THEME_KEY = "dy.theme"
```

**重启后自动恢复**：`ThemeProvider` 用 `useState<ThemeId>(readStoredTheme)` 初始化（`ThemeContext.tsx:117`），`readStoredTheme` 读的就是 `dy.theme`（`ThemeContext.tsx:40-46`）；解析走 `normalizeTheme`，**非 "day" 一律归为 aerospace**（`ThemeContext.tsx:34-38`）。挂载时的 `useEffect`（`ThemeContext.tsx:123-128`）还会在**首帧**就把主题铺到 `<html>`，避免亮/暗闪现（FOUC）。

补充：
- 默认态不变 —— 老用户 `dy.theme` 为空 → `readStoredTheme` 返回 `"aerospace"`（暗色），**升级后观感与现在一致**，不会有人被强制拉到亮色。
- 本卡只碰 `theme`；`accent` / `glow` / `glassFrost` 不在 UI 暴露（无需求），保持改动面最小。
- 纯前端偏好，**不写后端、不发请求**（与 `UnifiedConfigSection` 走后端 schema 的配置分开，故单独成卡而非塞进 schema）。

---

## 中文总结

主题引擎本来就完整（ThemeProvider / useTheme / setTheme / localStorage 持久化 / 首帧防闪现全都在位），**缺的只是用户可点击的入口**。本次补上：设置页「配置中心 → 通用配置」下新增「外观」卡，用项目既有的 Radix `Switch` 做日夜切换；顶栏再加一个 Sun/Moon 图标按钮作快捷方式，两处调用同一个 `setTheme()`。点击后 `<html data-theme>` 与 `colorScheme` 立即改写 → 全站 CSS 变量换套、原生控件同步跟色，同时写入 `localStorage` 的 `dy.theme`，重启自动恢复；默认仍是 aerospace（暗色），老用户升级观感不变。

复核结果：`npm run lint` exit 0、`npx tsc --noEmit` exit 0（已跑基线对照，改动前即为 0）。改动 4 个文件（新增 2、修改 2），`src/theme/`、`backend/`、所有版本文件均零改动，未做 git add/commit。按红线未启动 dev server，视觉验收留给用户明早在桌面进行。
