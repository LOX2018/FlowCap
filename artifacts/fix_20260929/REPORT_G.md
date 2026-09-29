# 任务G 报告 · 前端 直播（live 2 文件）3 条 + 数字去具体化

## 0. 只读声明

**实际改动的文件（全部在「唯一可写清单」内）：**
- `DYAutoDM_v2/frontend/src/components/live/live-page.tsx`（+80 / −25 区间，见 diffstat）
- `DYAutoDM_v2/frontend/src/components/live/live-shared.tsx`

**未做**任何 git 写操作（add/commit/reset/checkout/stash/clean 全未用；仅用 `git status`/`git diff`
只读命令核对改动范围）；**未改**版本号（package.json / tauri.conf.json / Cargo.* / _build_version.py 未触碰）；
**未改** `backend/services/delivery_verify.py`（属 B 桶）；未起浏览器/守护/打包；未触碰真实数据根。
`git diff --stat` 显示清单外文件（tasks.py / delivery_verify.py / accounts/*.tsx 等）也有改动 ——
经核对均为**本批次其它桶的既有改动**，非本任务所为（本次仅改动上列 2 个文件）。

---

## 1. 结论汇总表

| # | 位置(文件:行) | 判定 | 修法 | 修复前证据 | 修复后证据 |
|---|---|---|---|---|---|
| G-1 | `live-page.tsx:306-331`（计数）+ `:1122-1152`（展示） | **真缺陷** | 投递档（delivered/rejected）改为**只在 `dmStatus==='sent'` 行内统计**（与受理数同分母）；`delivery_state` 全空时降级为定性提示 | 复现脚本对 5 行样本：已受理=2 / 已送达=4 ⇒ **送达率 200%**，`deliveredCount>acceptedCount=true` | 同一样本：已受理=2 / 已送达=1 / 被拒=1 ⇒ **送达率 50%**，`delivered<=accepted` 且 `rate<=100%` 为 true；空态样本走「暂无法判定」不显示 0% |
| G-2 | `live-shared.tsx:96-115`（新 helper）+ `live-page.tsx:330-331,1214`（过滤） | **真缺陷** | 抽共享 `isIssue(r)`（判据 = `displayStatus(r)[1]==='danger'`），过滤与状态列**同一真源** | 行 `dmStatus='fail' & deliveryState='delivered'`：旧过滤命中=true，而 `displayStatus`=`["已送达","ok"]` ⇒ **过筛却渲染绿色** | 同一行：`isIssue=false`、`displayStatus=["已送达","ok"]` ⇒ 过滤/展示一致；「仅看异常」只命中 `deliveryState='rejected'` 的「丙」 |
| G-3 | `live-page.tsx:330-331`（visibleRows）+ `:1181-1189`（空态行） | **真缺陷** | `useMemo` 抽 `visibleRows`；新增「`rows.length>0 && visibleRows.length===0`」明确提示行 | 2 行数据全非异常、开「仅看异常」⇒ `visible=0` 但旧空态只认 `rows.length===0` ⇒ **表体空白无任何提示** | `noIssue.length>0 && vis2.length===0` 触发新提示行 ⇒ 转绿 |
| G-4 | `live-page.tsx:309,1124` + `live-shared.tsx:88` | **真缺陷（口径措辞）** | 两处硬编码数字改为**不含数字的定性描述** | `live-page.tsx` 写「19 条里有 **24** 次」、`live-shared.tsx` 写「19 条、**34** 条」自相矛盾 | `grep '19 条\|34 条\|24 次'` 于 live 目录 **0 命中** |

---

## 2. 逐条详述

### G-1 · 送达率分子分母口径不一（HIGH · 真缺陷）

**代码原文（修复前，`live-page.tsx`）：**
```ts
const acceptedCount = useMemo(
  () => rows.filter((r) => r.dmStatus === "sent").length, [rows]);
const deliveredCount = useMemo(
  () => rows.filter((r) => r.deliveryState === "delivered").length, [rows]);
const rejectedCount = useMemo(
  () => rows.filter((r) => r.deliveryState === "rejected").length, [rows]);
```
展示：`（送达率 {Math.round((deliveredCount / acceptedCount) * 100)}%）`

**为何是问题：** `delivery_state` 由后端 `api/tasks.py::_records_from_adm` **按 uid/会话历史**派生
（直读后端源码确认：`_cache[_uid] = _dstate(_acct, uid=_uid)`，同一 uid 的所有记录共享同一结局）。
⇒ 同一 uid 的多条记录（含 `un`/`wait`/`fail` 行）都会被标 `delivered`，而 `deliveredCount` 数**全部 rows**、
`acceptedCount` 只数 `dmStatus==='sent'` ⇒ `deliveredCount > acceptedCount` 成立 ⇒ **送达率可 >100%**。

**口径定义（我采用的）：** 投递结局（delivered/rejected）**只在「已受理」（`dmStatus==='sent'`）行内统计**，
与受理数**同分母**；不变式 `deliveredCount + rejectedCount ≤ acceptedCount`，送达率 ∈ [0,100]%。
（未采用「按去重 uid/会话计」——那会改变受理数分母的口径、且与状态列的逐行语义脱节。）

**空态降级：** 上游 `delivery_state` 仍可能整列为空（根因由 B 桶修）。新增 `hasDeliveryEvidence`：
无任何证据时**不渲染 `已送达 0 条 / 送达率 0%`**（会被误读为「全部未送达」），改为
`送达情况暂无法判定 · 暂无投递回执证据`。

**负控（红→绿）：** 样本 5 行（其中 4 行共享 `delivered`）：
- 前：`已受理=2 已送达=4 被拒=1 送达率=200%`（`rate>100%` = true）
- 后：`已受理=2 已送达=1 被拒=1 送达率=50%`（`rate<=100%` = true）
- 空态样本：`hasDeliveryEvidence=false` ⇒ 走定性分支（不显示 0%/NaN）= true

### G-2 · 筛选与展示口径不一致（MED · 真缺陷）

**代码原文（修复前）：** 过滤 `r.deliveryState === "rejected" || r.dmStatus === "fail"`，
状态列 `displayStatus(r)`。二者在 `dmStatus==='fail' && deliveryState==='delivered'` 这格**判据相反**。

**修法：** 在 `live-shared.tsx` 新增 `isIssue(r)`，判据取展示档的 tone：
```ts
export function isIssue(r: Row): boolean {
  return displayStatus(r)[1] === "danger";
}
```
过滤（`visibleRows`）与状态列**共用 `displayStatus`** ⇒ 结构上不可能再漂移。
`live-page.tsx` 导入 `isIssue`，`visibleRows = onlyIssues ? rows.filter(isIssue) : rows`。

**负控（红→绿）：** 行 `{dmStatus:'fail', deliveryState:'delivered'}`：
- 前：旧过滤命中=true、渲染 `["已送达","ok"]` ⇒ **过筛却绿色**（复现 true）
- 后：`isIssue=false`、渲染 `["已送达","ok"]` ⇒ 过滤/展示一致（true）

### G-3 · 「仅看异常」无命中只剩表头（LOW · 真缺陷）

**修法：** `useMemo` 抽 `visibleRows`（避免 JSX 内重复 filter）；表体新增：
```tsx
{rows.length > 0 && visibleRows.length === 0 && (
  <tr><Td colSpan={6}><Blank>✅ 当前筛选下无异常记录 · …点「仅看异常 ✓」可恢复全部</Blank></Td></tr>
)}
```
明确区分「无异常」（提示行）与「渲染坏了」（原 `rows.length===0` 空态）。
**负控（红→绿）：** 2 行全非异常 + 开筛选：前=`visible=0 且旧空态不触发`（空白），后=`rows>0 && visible==0` 触发提示行 = true。

### G-4 · 硬编码业务数字自相矛盾（LOW · 真缺陷）

三处含具体数字（`live-page.tsx:309`「19 条里有 24 次」、`:1109`「19 条 / 34 条」、
`live-shared.tsx:88`「19 条、34 条」）→ 全部改为**不含数字的定性描述**，桶间无需一致。
`delivery_verify.py` 未触碰。验证：`grep '19 条\|34 条\|24 次'` 于两个 tsx **0 命中**。

---

## 3. 验证命令与结果

**① 强制类型检查（任务硬门槛）**
```
$ cd DYAutoDM_v2/frontend && npx tsc --noEmit
(无输出)
EXIT=0
```
修复前基线同命令亦 exit=0（确认我的改动**未引入**新类型错误、也未因既有错误掩盖）。

**② 可复现证据（真跑，非静态）** —— 用 esbuild 把真实 `live-shared.tsx` 打包后 node 回放：
```
$ node_modules/.bin/esbuild <entry.ts> --bundle --platform=node --format=cjs \
    --tsconfig=frontend/tsconfig.json --outfile=bundle_after.cjs
Done in 122ms              # 打包真实模块成功

$ node repro_after.cjs
[G-2] 行 dmStatus=fail & deliveryState=delivered:
      isIssue(前过滤) = false   displayStatus = ["已送达","ok"]
      => 过滤与展示一致（均非异常、渲染 ok）: true
[G-2] 仅看异常命中行: [ '丙' ] （应只含被平台拒绝的『丙』）
[G-3] 无异常时 visibleRows.length = 0  ⇒ 新空态提示行触发(rows>0 && visible==0): true
[G-1] NEW  已受理=2 已送达=1 被拒=1 送达率=50% 有证据=true
      deliveredCount<=acceptedCount ? true  rate<=100% ? true
[G-1] delivery_state 全空 ⇒ hasDeliveryEvidence = false  ⇒ 走『暂无法判定』定性提示(不显示 0%/NaN): true
EXIT=0
```
修复前同法回放（`bundle_pre.cjs`）红：`送达率=200%`、`过筛却绿色=true`、`筛选空态无提示=true`。

**③ 数字去具体化核查**
```
search '19 条|34 条|24 次|19条|34条|24次' in src/components/live/  →  total_count: 0
```

**④ 改动范围**
```
$ git diff --stat -- <两个 tsx>
 live-page.tsx   | 84 ++++++++++++++--------
 live-shared.tsx | 21 ++++++-        2 files changed, 80 insertions(+), 25 deletions(-)
```

---

## 4. 未做/存疑/需真机验证

- **未起浏览器/UI 未做像素级验证**（任务禁令）。G-2/G-3 的结论基于**真实模块函数回放**（非纯静态读代码）：
  `isIssue`/`displayStatus` 的返回是实测的；但「提示行在 DOM 里真的渲染出来」仅静态判断（JSX 条件分支）。
- **`delivery_state` 根因（恒空）由 B 桶修**，本任务仅在展示层做**空态降级**；若 B 桶把该字段填上，
  本页会自动从「暂无法判定」切回「已送达 N 条 / 送达率 X%」，无需再改。
- **G-1 采用「同分母 = 已受理行内统计」口径**（非「按去重 uid/会话计」）。理由与不变式见 §2；若父会话
  认为应按去重 uid 计，属**口径选择**分歧，非缺陷，可再议。
- 未跑单测（本任务无相关前端测试；门槛命令为 `tsc --noEmit`，已 exit=0）。
- 结论区分：**「我实测过的」** = tsc 结果 + esbuild 打包真实模块 + node 回放三个缺陷的负控；
  **「仅静态判断的」** = JSX 条件分支真的渲染、CSS 呈现效果。
