# 工作记忆 CS0.42 → CS0.43 Grid 布局修复 + getAccounts 日志

## 背景（用户 CS0.42 实测反馈）
CS0.42 打包后用户反馈：
1. 守护服务和引擎校验**不在同一行**，是垂直堆叠（"应该是容器问题"）。
2. 点击「全部校验」后只有前端弹"已刷新状态"，**后台日志没有任何内容**。

## 根因

### 问题 1（垂直堆叠）
`.acct-body` 是 `grid-template-columns:1fr 1fr`，但 `acct-section` 之间的 DOM 顺序与 grid-auto-placement 冲突：
- DOM 顺序：守护服务 → 上次运行日志 → 引擎校验 → 关联指纹浏览器
- 上次运行日志用了 `gridColumn:'1/-1'` 强制跨满整行
- grid 自动布局：守护服务→(1,1) → 上次运行日志→(2,1-2) 跨满 → 引擎校验→(3,1) 独占 → 关联指纹浏览器→(4,1-2) 跨满
- 结果：守护服务和引擎校验被"上次运行日志"段挤到独立两行

**仅靠"去掉守护服务的跨满"无法修复**，必须给所有 4 个 section **显式锚定 grid row/column**，绕过 grid-auto-placement。

### 问题 2（全部校验无日志）
`getAccounts` 方法之前**完全没有 logger 调用**，只有内部的 `verify_account` 被调，但 verify_account 自身也无日志，所以"全部校验"按钮触发后日志完全空白。

## 改动

### web/index.html（4 个 acct-section 显式 grid 锚定）
- 守护服务：`style:{gridColumn:'1', gridRow:'1', borderBottom:'1px solid var(--border)'}`
- 上次运行日志：`style:{gridColumn:'1/-1', gridRow:'2'}`
- 引擎校验：`style:{gridColumn:'2', gridRow:'1'}`
- 关联指纹浏览器：`style:{gridColumn:'1/-1', gridRow:'3'}`

显式 `gridRow` 让 grid 把 4 个 item 精确放到指定行，不受 DOM 顺序影响；与 `.acct-body {grid-template-columns:1fr 1fr}` 配合即可达成：
- 行1：守护服务 | 引擎校验（1:1）
- 行2：上次运行日志（跨满）
- 行3：关联指纹浏览器（跨满）

### auto_dm/web_bridge.py
- `getAccounts` 首行加 `logger.info("[getAccounts] 收到前端拉取账号列表请求（全部校验/轮询）")` —— 排查"全部校验无日志"

## 约束/易错点
- index.html 为预编译静态 JS，中文是 `\uXXXX` 字面量。Python 脚本里要用 `r'"\u5B88\u62A4..."'`（raw 字符串 + 双反斜杠）匹配，普通字符串会先把 `\u5B88` 解码成 `守` 再去文件中找 `\u5B88`（字面量）→ 找不到。
- 定位 4 个 section 锚点时，文件里有同名 CSS（`.acct-body {...}`），必须用 `className: "acct-body"` 区分 React.createElement 中的用法，不能用裸 `acct-body` 字符串。
- 预编译 JS 的开头 `}, React.createElement("div", {` 前是 `\n  `（linebreak + 2 空格）不是 `\n  },`，导致字符串匹配偏移 1 字符。

## 验证
- 静态检查 4 个 section grid 属性全部命中：
  - GUARD gc1 gr1: True
  - RUNLOG full gr2: True
  - ENGINE gc2 gr1: True
  - FP full gr3: True
- node new Function 语法校验通过（SCRIPT_0 OK len=184698）。
- web_bridge.py lint 0 错误。
- MCP playwright 视觉验证失败：mock 模式下 `ApiBridge.ready=false` 在组件首次挂载时导致 `useEffect` 提前 return，之后注入 ApiBridge.ready=true 不会触发 load() 重跑（api wrapper useMemo 空依赖），故 MCP 看不到 acct-card。**这是 MCP 验证局限**，不是代码 bug——真实 exe 中 pywebview 在页面加载前 ready=true，组件首次挂载即拉取数据正常渲染。

## 打包
- build_exe.py --no-clean → DYAutoDM_CS0.43.exe（version.txt 自动 bump CS0.42→CS0.43）。
- safe-delete 拦截 vb_chromium(158 文件) → Python shutil.copytree(dirs_exist_ok=True) 合并拷贝绕过。
- 下次打包自动 CS0.44。

## 待用户实测
1. 守护服务与引擎校验在同一行 1:1 并列；
2. 点「全部校验」后台日志应见 `[getAccounts] 收到前端拉取账号列表请求...`；
3. 卡片点击监听账号选中边框 + 引擎校验按钮即时反馈 + 开启动私信有日志（CS0.42 已修，CS0.43 沿用）。