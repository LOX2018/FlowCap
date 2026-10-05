# 列表类组件居中问题 —— 实测诊断与本任务规格

> **诊断者**：主 Agent（已实测，非推断）
> **任务**：把"页面内容因宽度限制而左偏"这个视觉缺陷修好

---

## 一、实测到的问题（**证据**）

**环境**：`http://127.0.0.1:5199/preview-pages.html?p=tasks`（dev server 已在跑）

**测量方法**：读取页面主容器（带内联 `maxWidth` 的那个 div）的 `getComputedStyle()` 与 `getBoundingClientRect()`：

```json
{
  "main":        { "w": 1022, "x": 240, "right": 1262 },
  "container": {
    "cls":        "mx-auto w-full px-5 py-4 ",
    "inlineMW":   "1680px",
    "computedMaxW": "1680px",
    "display":    "block",
    "w":          1012,
    "x":          240,
    "marginLeft": "0px",
    "marginRight": "0px",
    "leftGap":    0,
    "rightGap":   10
  }
}
```

**结论**：容器**没有居中**——`leftGap: 0` 而 `rightGap: 10`（那 10px 只是右边滚动条）。`mx-auto` 失效。

**根因**（已核实）：该容器同时是 `display:block` + `w-full`（即 `width:100%`）。
`margin-inline:auto` 只在元素有**剩余空间可分配**时才起居中作用；
`width:100%` 把宽度占满父级 → 没有剩余空间 → `auto` 解析为 `0`。

> 验证过 `.mx-auto{margin-inline:auto}` 规则**确实生成在构建产物 CSS 里**，
> 所以这不是 Tailwind 没编译的问题，是**用法**的问题。

**影响范围**：所有用 `PageContainer` 的页面 —— 当窗口宽度 **> 1680px** 时，
内容区右侧会空出一大片、而左侧紧贴侧栏（视觉上"内容贴在左边"）。
窗口 ≤1680px 时容器本来就是满宽，看不出问题（所以容易被忽略）。

---

## 二、修复目标（用户原话："列表类组件未实现居中"）

1. **窗口足够宽时，页面内容整体水平居中**：容器左右留白相等。
2. **窄窗口时行为不变**：容器占满可用宽度（不出现横向滚动条）。
3. **"列表类组件"同样要居中**——即页面内的表格 / 列表 / 卡片栅格
   不应各自贴左，而应与页面容器一起居中。
4. **不能回归**：`PageContainer` 的 `maxWidth` / `className` 传参契约不变
   （各页有传 `maxWidth="860px"` / `"1080px"` 的，必须仍然生效）。

---

## 三、技术约束

| 约束 | 说明 |
|---|---|
| 技术栈 | Tailwind 4（`@theme` 令牌）+ Radix + CVA，见 `src/styles/tokens.css` |
| 不可引入新依赖 | 不许加 npm 包 |
| 保持既有令牌体系 | 颜色/圆角/间距用 `var(--color-*)` 等，不要写死色值 |
| 深/浅主题都要正常 | 令牌已双主题，改样式不得破坏 |
| 项目是桌面端（Tauri） | 不要加移动端断点 |

**改的核心文件**：`src/components/layout/app-shell.tsx` 的 `PageContainer`
（以及必要时它包裹的内容结构）。

---

## 四、验收（必须自己实测，不接受"看起来对了"）

dev server 已在 `127.0.0.1:5199` 运行，页面走
`http://127.0.0.1:5199/preview-pages.html?p=<page>`（14 个页面可选，
如 `tasks` / `accounts` / `overview` / `live` / `messages`）。

**验收判据（必须用计算样式证明，不许只看截图）**：

1. 在**宽窗口**下（把浏览器视口调到 >1680px，例如 1920：
   `cdp('Emulation.setDeviceMetricsOverride', width=1920, height=1080, ...)`），
   对每个抽查页面断言：
   ```js
   Math.abs(leftGap - rightGap) <= 2   // 左右留白相等（±2px 容差）
   ```
2. 在**窄窗口**下（如 1280）断言容器仍占满、无横向溢出：
   ```js
   container.scrollWidth <= container.clientWidth + 1
   ```
3. 传了 `maxWidth="860px"` 的页面（notify）与 `"1080px"` 的（kb）
   仍按其值生效（`computedMaxW` 等于传入值）。
4. `npx tsc -b` → EXIT=0。

---

## 五、交付要求

1. **改完后必须跑上面的实测**，并把**修复后的实测 JSON**（左右 gap 的数值）贴进报告。
2. 报告里列出：改了哪些文件、每个文件的改动要点、`tsc -b` 输出。
3. 如果发现"列表类组件"另有专属的居中问题（例如某页的表格自己限宽贴左），
   一并说明并修（**但不要顺手重构无关代码**）。
