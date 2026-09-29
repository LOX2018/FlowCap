# 任务F 报告

## 0. 只读声明
实际改动的文件（恰好 3 个，均在 TASK_F「唯一可写清单」内）：
1. `DYAutoDM_v2/frontend/src/components/accounts/accounts-page.tsx`
2. `DYAutoDM_v2/frontend/src/components/accounts/LoginDialog.tsx`
3. `DYAutoDM_v2/frontend/src/components/accounts/AccountDrawer.tsx`

- 未做任何 git 写操作（add/commit/reset/checkout/stash/clean 全未执行；仅用 `git diff`/`git status` 只读查看）。
- 未改版本号（package.json / tauri.conf.json / Cargo.toml / Cargo.lock / _build_version.py 均未触碰）。
- 未启动浏览器/BCC/守护/uvicorn/打包；未触碰真实数据根 `C:\temp\dyautodm_design` 与 `accounts/*/profile`；未新增任何抖音平台请求（本次改动纯前端呈现/文案层）。
- 清单外文件（`accounts-shared.tsx` / `ProxyDrawer.tsx` / `AccountReview.tsx` 等）一律只 grep/read，未改。
- 说明：`git status` 中 `DYAutoDM_v2/backend/auto_dm/dom_locator.py` 的改动**非本任务产生**（进入工作区即已存在，属他人/既有改动）。

## 1. 结论汇总表
| # | 位置(文件:行) | 判定(真缺陷/误报) | 修法 | 修复前证据 | 修复后证据 |
|---|---|---|---|---|---|
| F-1 | `accounts-page.tsx` 180-188 与 1090-1097 | 真缺陷 | 抽 `promptSmsPhone(): string\|undefined` 共享 helper，两处调用 | `grep -c window.prompt accounts-page.tsx` = **2**（两处逐字重复 prompt+校验）；`手机号格式不正确` 出现 2 次 | `grep -c window.prompt` = **1**（仅 helper 内）；`promptSmsPhone()` 调用点 = 2（178 定义、195、1096 调用）；`手机号格式不正确` = 1 次 |
| F-2 | `accounts-page.tsx` 183（helper 内取消分支） | 真缺陷 | 取消/空串时 `push("已取消（未提供手机号）")` 后再 return | 原 `if (!phone) return;` 静默无提示 | `grep -n 已取消（未提供手机号）` → 181 行命中 |
| F-3 | `accounts-page.tsx` 160-162、177、679-683（及 366/476/527/797 同类）；`LoginDialog.tsx` 61、142、180-181 | 真缺陷（注释腐化） | 精简为意图说明，删日期+历史沿革+字面 `**` | 修复前含 `2026-09-29（方案2·用户拍板）`/`历史：2026-09-28` 等 | `grep -n "2026-0[0-9]-[0-9][0-9]" 三文件` → **无命中** |
| F-4 | `LoginDialog.tsx` 113-117、145-149（及 180-184 同型） | 真缺陷 | 建 `PATH_DESC`/`PATH_LOADING`/`PATH_LABEL` 查表 map，按 `st.path` 取值，消除嵌套三元 | 修复前 3 处 st.path 嵌套三元链 | `grep -c 'st.path === "manual"'` = **0**；3 个 `Record<LoginPath,string>` map 已建 |
| F-5 | `AccountDrawer.tsx` 125 | 真缺陷 | 先 grep 确认真实按钮文案=「手动更新凭证」，据此改文案并去掉字面 `**` | 描述写「点击「重新获取凭证」…**…**」；grep 全库无「重新获取凭证」，真实标签为「手动更新凭证」(158 行) | 描述改为「点击「手动更新凭证」…」；`grep -n 重新获取凭证 AccountDrawer.tsx` → 无命中；可见串无字面 `**` |

## 2. 逐条详述

### F-1 消除分叉（真缺陷）
- 修复前 `runBackupLogin`（180-188）与 `onSwitchMode`（1090-1097）各有一份逐字相同的 `window.prompt` + `/^\d{6,20}$/` 校验；任一处改口径另一处必然漂移。
- 改成共享 helper（`accounts-page.tsx:177-190`）：
  ```tsx
  const promptSmsPhone = (): string | undefined => {
    const phone = window.prompt("请输入该账号绑定的手机号（用于接收短信验证码）")?.trim();
    if (!phone) { push("已取消（未提供手机号）"); return; }
    if (!/^\d{6,20}$/.test(phone)) { push("手机号格式不正确（应为 6~20 位数字）"); return; }
    return phone;
  };
  ```
  两处调用点改为 `phone = promptSmsPhone(); if (!phone) return;`。
- 负控：修复前 `grep -c window.prompt` 应为 2 → 实测 2；修复后 → 1。`promptSmsPhone()` 调用点计数 2（1096 换路、195 备用）证明复用成立。

### F-2 取消无提示（真缺陷）
- 修复前取消/空串直接 `return`，用户点「短信备用」看不到任何反馈。
- helper 内 `push("已取消（未提供手机号）"); return;`。负控：`grep -n 已取消（未提供手机号）` 无→有（181 行）。

### F-3 变更日志式注释（真缺陷）
- 修复前为「带日期+历史沿革」注释（如 `saveEdit` 上方 `2026-09-29（方案2 · 用户拍板）…`、卡片按钮旁 5 行历史叙述）。此类注释点名日期与旧决策，跟代码实际行为脱节后会误导。
- 精简为意图说明，并顺带清理同文件其它日期型注释（366/476/527/797）与 `LoginDialog.tsx` 中三处（61/142/180）。均为**注释**改动，零逻辑影响。
- 负控：修复前 `grep 2026-0[0-9]-[0-9][0-9]` 命中多条 → 修复后三文件 **0 命中**。

### F-4 嵌套三元（真缺陷 · style）
- 修复前 `DialogDescription` 与 loading 文案、以及底部「当前路径」文案各是一条 `manual ? … : sms ? … : …` 嵌套三元，可读性差且新增路径须改三处。
- 建三张 `Record<LoginPath,string>` 查表 + `pathDesc()`（仅短信路径的**描述**随 `stage` 变，故单独保留一条扁平判断，非嵌套）。`st.path` 先归一化到 `"manual"|"sms"|"qr"`，未知/缺失落 `"qr"`（与原 `else` 分支语义一致）。
- 负控：`grep -c 'st.path === "manual"'` 修复前 3 → 修复后 **0**；渲染处改为 `PATH_LOADING[path]` / `PATH_LABEL[path]` / `pathDesc()`。

### F-5 描述串两毛病（真缺陷 · 用户可见）
- 先 grep 确认真实标签：`grep -rn "重新获取凭证\|手动更新凭证" src/` → 只有 `AccountDrawer.tsx:158 {isEdit ? "手动更新凭证" : "确认新增"}`；全库**无**「重新获取凭证」。故 125 行描述指向了不存在的标签。
- 改后：`"点击「手动更新凭证」默认打开有头指纹浏览器，由你手动完成扫码/验证码/滑块，凭证自动写回该账号。…"`，同时去掉字面 `**`（React 会原样显示星号）。
- 负控：`grep -n 重新获取凭证 AccountDrawer.tsx` 修复前命中 → 修复后无命中。

## 3. 验证命令与结果

前端类型检查（TASK_F 硬性要求）：
```
$ cd DYAutoDM_v2/frontend && npx tsc --noEmit; echo "TSC_EXIT=$?"
TSC_EXIT=0
```
- 命令在改动**前**先跑过一次基线：`EXIT=0`。
- 改动后再跑：`EXIT=0`（exit=0 达成）。

机械门禁 / 负控复算（针对 3 个目标文件）：
```
$ grep -c "window.prompt"              accounts-page.tsx   -> 1   (修复前 2)
$ grep -c "手机号格式不正确"            accounts-page.tsx   -> 1   (修复前 2)
$ grep -c "promptSmsPhone()"           accounts-page.tsx   -> 2   (两处复用)
$ grep -n "已取消（未提供手机号）"       accounts-page.tsx   -> 181
$ grep -n "2026-0[0-9]-[0-9][0-9]"     三文件              -> 无命中
$ grep -c 'st.path === "manual"'       LoginDialog.tsx     -> 0
$ grep -c "Record<LoginPath, string>"  LoginDialog.tsx     -> 3
$ grep -n "重新获取凭证"                AccountDrawer.tsx   -> 无命中
$ grep -n "手动更新凭证"                AccountDrawer.tsx   -> 125(描述)/158(按钮)
```
（本次为纯前端呈现/文案改动，不涉及 Python 单测；无 `Ran N tests` 项。）

## 4. 未做/存疑/需真机验证
- **未做真实 UI 交互验证**：本任务禁启浏览器/BCC/应用，故 F-2 提示条、F-4 文案分支、F-5 描述渲染**仅静态保证（类型检查 + grep 负控）**，未在运行中的应用内点击核对；属「仅静态判断」。
- **F-1 判定为真缺陷**（非误报）：两处逐字重复属实，照抽 helper 不破坏任何行为（口径与提示文案逐字保留，仅 `useEffect` 依赖不受影响，因 helper 为组件内闭包、`push` 引用稳定）。
- **F-3 清理范围**：除 brief 点名的两处外，顺带清理了同三文件内其它日期型注释（366/476/527/797 与 LoginDialog 61/142/180），均为注释、零逻辑改动；如父会话希望**严格只动**点名的两处，可回退这批附加注释改动（不影响 tsc）。
- 未触碰清单外文件；未做 git 写；版本号未改。
