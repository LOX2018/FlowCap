# 任务H 报告

## 0. 只读声明

**实际改动的文件（唯一一个）**
- `DYAutoDM_v2/frontend/src/components/settings/HighValueKeywordsSection.tsx`（HEAD `149725e` → 工作树，+178/-47）

**未做**：git 写操作（无 add/commit/reset/checkout/stash/clean；`git status` 未变红新增条目）；未改任何版本号文件（`package.json / tauri.conf.json / Cargo.toml / Cargo.lock / _build_version.py` 的 `git status` 为空）；未起浏览器/BCC/守护/uvicorn/打包；未触碰 `C:\temp\dyautodm_design` 与 `accounts/*/profile`；未新增任何抖音平台请求。
**工作树清理**：取证过程因 MSYS 路径被原生 node 拼接，曾在 `DYAutoDM_v2/{c,tmp}` 下产生 2 个临时文件，已 `rm -rf` 删除并复核 `git status` 恢复原样（该两个目录原本不存在）。

---

## 1. 结论汇总表

| # | 位置(文件:行) | 判定 | 修法 | 修复前证据 | 修复后证据 |
|---|---|---|---|---|---|
| H-1 | `HighValueKeywordsSection.tsx:65-72`（HEAD 版） | **真缺陷** | 抽出纯函数 `checkDraft()`：空词/非整数权重/重词 ⇒ 进 `issues` 并**阻止保存**+高亮+逐行报原因；不再 `continue` 丢行 | 对 HEAD 版原文循环注入工单场景（屏幕 4 行）：提交 `{"工伤":9}` = **1 条**，toast 报 1，与屏幕 4 行不符 ⇒ **3 行静默丢弃** | 同一 draft 走 `checkDraft`：`issues=3`（empty_word/bad_weight/dup_word 各一）、`out={"工伤":5}`，**每行必有归属**（提交或报错），`issues>0 ⇒ return 不发请求` |
| H-2 | 同上 `:68 if(!w) continue` | **真缺陷**（与 H-1 同源） | 同上：空 word 行 → `empty_word`，阻止保存并在提示里点名「第 N 行 关键词为空」 | 同案第 2 行（用户正在编辑的空行）被 `continue` 丢弃，无提示 | 该行被高亮 + 提示「第 2 行 关键词为空」；用户修正或删除后即可保存 |
| H-3 | 同上 `:166-192`（HEAD 版） | **真缺陷** | 每行加稳定 `id`（`useRef` 递增计数器，`toDraft`/`addRow` 分配），`key={row.id}`，`delRow/setRow` 一律按 id 定位 | HEAD 版 `key={i}` + `filter((_, idx) => idx !== i)` + `idx === i` 修改（静态取证通过）；行为取证：[A,B,C] 删中间后 C 下标 2→1，旧逻辑把编辑写到 C 上 | 真实代码已无 `key={i}`、无下标定位；`key={row.id}`、`filter(r => r.id !== id)`、`r.id === id`；`draft.map` 不再暴露下标 `i`；行为取证：按 id 写 ⇒ A 不动、C 被改 |
| H-4 | 同上 `:85`（HEAD 版） | **真缺陷** | `reset()` 首行加 `if (!confirm(...)) return;`，文案点明「覆盖自定义、无法撤销」 | HEAD 版 `reset` 函数体内无 `confirm(`（静态取证通过），直接调 reset 端点 | reset 体内、调用端点之前出现 `confirm(`，形态 `if (!confirm(...))`；与同页 AgentSection/ProviderSection/TagSection 范式一致（三处对照均命中） |

**没有一条被判为误报。** 4 条全部真缺陷并已修。

---

## 2. 逐条详述

### H-1 / H-2 · 保存静默丢行（真缺陷）

**代码原文（HEAD 版 `save()`，`git show HEAD:` 抽出，未经改写）**
```js
const out: Record<string, number> = {};
for (const row of draft) {
  const w = row.word.trim();
  if (!w) continue;                                   // H-2：空词行 → 丢
  const n = parseInt(row.weight, 10);
  if (Number.isNaN(n)) continue; // 非法权重丢弃（与服务层同语义）  // H-1 → 丢
  out[w] = n;                                         // H-1：重词 → 覆盖合并
}
```
**为何是问题**：三条 `continue`/覆盖路径都不产生任何用户可见反馈；而保存成功后 `setDraft(toDraft(r.items))` 用服务端回读**重建** draft ⇒ 被丢弃的行从屏幕上无声消失，`setMsg(\`已保存 ${服务端条数} 个关键词\`)` 报的还是服务端条数，与用户保存前看到的行数不符。这正是工单说的「toast 数量与用户所见不符」。

**改成什么**
- 抽出可单测的纯函数 `checkDraft(rows): {out, issues}`：逐行判定，不合格行进 `issues`（`empty_word` / `bad_weight` / `dup_word`），**任何行都不会既不提交也不报错**。
- 权重判据由 `parseInt` 换成**整串**匹配 `/^[+-]?\d+$/` + `Number.isSafeInteger`：旧版 `parseInt("3个")` 会静默存成 `3`，同样是静默改写用户输入（顺手堵掉，见 §3 的 C 组）。
- `save()` 在 `issues.length > 0` 时：`setBadIds(...)` 高亮 → 用行号+原因拼提示写进现有 `err` 提示条 → **`return`（不发请求、不 busy）**。
- 用户一改某行即从 `badIds` 移除该行（提示与画面不脱节）。
- 保存成功再加一道**防御性核对**：`gotN === sent` 才报「已保存 N 个」，不等则明说「服务端返回 X 个（本次提交 Y 个）」。
- 语义保留：前后空白仍 `trim`；`out` 结构不变；**空 draft 仍合法提交 `{}`**（清空 = 不按关键词过滤，服务层 G2 语义不破）。

**负控如何变红/转绿**：把同一个 4 行 draft 分别喂给「HEAD 版原文循环」与「`checkDraft`」——前者 4 行→1 条（丢 3 行，红），后者 3 行入 `issues`、1 行入 `out`（0 丢失，绿）。另加 500 组随机 draft 属性测试，断言「每行必有归属」，反例 0。

**取舍说明（诚实标注）**：H-2 给了「提示/高亮/**阻止保存**」三选一，我选**阻止保存**（这是唯一能同时满足 H-1「不得静默丢」的选项）。副作用：用户为「占位」留的空行必须先删掉才能保存。我认为这是对的取舍，理由是同卡另有「添加」按钮、空行零信息量，而静默丢自定义词条是不可接受的；若后续用户嫌烦，退路是「跳过但保留该行不清屏」——但那会重新引入「所见 ≠ 已存」，故未采用。

### H-3 · `key={i}` + 下标定位（真缺陷）

**代码原文（HEAD 版）**：`{draft.map((row, i) => (<tr key={i} …` + `delRow = (i) => setDraft(d => d.filter((_, idx) => idx !== i))` + `setRow = (i, patch) => d.map((r, idx) => idx === i ? …)`。
**为何是问题**：`key` 与增删改定位都绑数组下标 ⇒ 删中间行后 React 按下标复用 DOM 节点，且**写操作会落到别的行**：`[A,B,C]` 删 `B` 后 `C` 的下标由 2 变 1，此时任何仍按下标 1 的写入都会改到 `C`。输入框焦点/输入态随位置漂移。
**改成什么**：`DraftRow = {id, word, weight}`，`id` 由 `useRef` 递增计数器 `r1,r2,…` 生成（`toDraft` 建行、`addRow` 建行统一走它，跨 load/add 不重复）；`key={row.id}`；`delRow(id)`/`setRow(id, patch)` 按 id 定位。参考同页 `TagSection` 的 `key={t.id}`。
**负控**：静态判据要求修复版真实代码里**不再出现** `key={i}` 与下标定位、且 `draft.map` 不再暴露 `i`；行为判据复现「旧逻辑把编辑写到 C 上」而新逻辑只改 C、不动 A。

### H-4 · 「恢复默认」无二次确认（真缺陷）

**代码原文（HEAD 版）**：`const reset = async () => { setBusy(true); … await api.aiHighValueKeywordsReset(); … }` —— 函数体内无 `confirm`。
**为何是问题**：端点实现是 `put_keywords(DEFAULT_KEYWORDS)`（`api/ai.py:903-911`），即**整表覆盖**用户全部自定义权重，且不可撤销；而同页其它破坏性操作（`AgentSection:216`、`ProviderSection:121`、`TagSection:187`）都有 `confirm()`，唯此处漏了 ⇒ 误点即清空自定义。
**改成什么**：`reset()` 首行加守卫
```ts
if (!confirm("确认恢复默认关键词表？你自定义的全部关键词与权重会被覆盖为「工伤业务域种子词表」，且无法撤销。")) return;
```
**负控**：静态判据断言「HEAD 版 reset 体内无 confirm（红）」→「修复版 reset 体内、调用端点之前有 `if (!confirm(...))`（绿）」，并对照同页三个 Section 的 `confirm` 范式。

---

## 3. 验证命令与结果

取证脚本与日志在（job scratch，24h 内有效）：
`C:\Users\LOX\AppData\Local\Hermes Agent CN Desktop\data\hermes-home\profiles\lox\cache\scratch\`
`run_evidence.sh` · `_extract.mjs` · `_hv_before.mjs`（修复前基线） · `_hv_after.mjs`（修复后，`checkDraft` 真实打包代码） · `_hv_static.mjs`（H-3/H-4 机械判据） · `evidence.log`

复现入口：
```bash
cd "$LOCALAPPDATA/Hermes Agent CN Desktop/data/hermes-home/profiles/lox/cache/scratch" && bash run_evidence.sh
# 其中 [2] 修复前基线用：git -C <repo> show HEAD:DYAutoDM_v2/frontend/src/.../HighValueKeywordsSection.tsx
# [3] 修复后判据用：DYAutoDM_v2/node_modules/.bin/esbuild <该组件> --bundle --format=esm --platform=node
```

### [2] 修复前基线（红）
```
[before] 屏幕上的行数 = 4
[before] 提交给服务端的条目 = {"工伤":9} => 1 条
[before] toast 报数（=服务端条数）=  1  ← 与屏幕 4 行不符
[before] ✗ 复现：3 行被静默丢弃（无任何提示）
```

### [3] 修复后判据（绿）—— 真实 `checkDraft`，非重写副本
```
== A. 工单场景：屏幕 4 行 / 3 行有问题 ==
  ✓ 3 行被判不合格（实得 3）
  ✓ 仅合法行进入提交集: {"工伤":5}
  ✓ 无行被静默丢弃（每行要么提交、要么报错）
  ✓ 第2行(空词) → empty_word
  ✓ 第3行(abc) → bad_weight
  ✓ 第4行(重词) → dup_word
  ✓ issues>0 ⇒ save() 直接 return，不发请求（阻止保存）
== B. toast 数量与所见一致 ==
  ✓ 无不合格行
  ✓ 提交条数 3 == 屏幕行数 3
  ✓ 词前后空白被 trim，行未丢
== C. 回归：旧版 parseInt 的「部分解析成功」也不再静默改写 ==
  ✓ "3个" → bad_weight（旧版会静默存成 3）
== D. 清空语义仍可用 ==
  ✓ 空 draft 合法 → 提交 {}
== E. 属性测试：任何输入都不静默丢行 ==
  ✓ 500 组随机 draft 无一行丢失（反例 0）
== F. 文案同源 ==
  ✓ issueLabel(empty_word/bad_weight/dup_word) 均有文案
结论：全部通过 ✓
```
> 判据脚本自身踩过一次坑并已修：初版静态判据在**注释里复述的旧代码**（`原 key={i}`）上误命中（自造假红，同 `test_high_value_keywords_entry.py:63` 记载的教训）。已改为先剥离注释再判定。

### [4] H-3/H-4 机械判据（绿）
```
== H-3 ==
  ✓ HEAD 版确有 key={i}（缺陷基线）   ✓ HEAD 版删除/修改按下标
  ✓ 修复版真实代码已无 key={i}        ✓ 修复版 key={row.id}
  ✓ 删除按 id 过滤   ✓ 修改按 id 定位  ✓ 新增行分配新 id
  ✓ id 由递增计数器生成（跨 load/add 不重复）
  ✓ draft.map 不再暴露数组下标 i
  ✓ 旧逻辑：C 下标 2→1、编辑落到 C 上（症状复现）
  ✓ 新逻辑按 id 写入：A 不动、C 被改（身份稳定）
== H-4 ==
  ✓ HEAD 版 reset 无 confirm（缺陷基线）
  ✓ reset 体内、调用端点之前出现 confirm(，形态 if (!confirm(...))
  ✓ 同页 AgentSection / ProviderSection / TagSection 的破坏性操作确有 confirm
结论：全部通过 ✓
```

### [5] 工单硬性要求：`cd DYAutoDM_v2/frontend && npx tsc --noEmit`
```
tsc-exit=0
```
（TSC 5.9.3，npx 解析到 `DYAutoDM_v2/node_modules/typescript`；`--listFiles` 复核含本文件与 108 个 src 文件，且全仓 exit=0 是**本文件改动后**的结果。）

### [6] 附带：后端入口门禁未回归
```
cd DYAutoDM_v2 && export DY_APP_ROOT=$(mktemp -d) && py -3.14 -m pytest backend/test_high_value_keywords_entry.py -q
......                                                                   [100%]
6 passed in 0.06s      # 含 G6「组件必须在 settings-page 中渲染」——只读校验，本次未动挂载点
```

---

## 4. 未做 / 存疑 / 需真机验证

- **未做真机/浏览器验证**：本批禁起浏览器。以下三项为**静态+可执行逻辑级**结论，未在真实页面上点过按钮：
  1. H-3 的「焦点/输入态错位」我只证明了**身份漂移与写错行**（可复现的逻辑层证据），未在真实 DOM 上抓焦点错位现象。
  2. 高亮样式（`bg-[var(--color-danger)]/10`、`placeholder:text-[var(--color-danger)]`）能否被 Tailwind 正确产出，属视觉层，未在浏览器里目视确认；类名写法与同文件既有危险色用法一致。
  3. H-4 的 `confirm` 弹窗未实机点过（Radix/Tauri WebView 下 `window.confirm` 可用性未验；同页 4 处已在用同一 API，风险低）。
- **存疑（留给父会话判断）**：
  - 我改成了「有空行/错权重就不让存」。若产品更倾向「跳过但保留行不清屏」，需改 `save()` 的阻止策略——但那样必须同时停止「保存后从服务端回读重建 draft」，否则会重新引入「所见≠已存」。当前实现两处都堵死了。
  - 顶层 `export function issueLabel/checkDraft` 与 `export type` 是为了让门禁可机械验证（同 `frontend/scripts/_verify_init_key_fix.ts` 的做法）。若父会话认为不该扩大模块导出面，可下移到 `settings-shared.tsx`（那会动第二个文件，超出我的可写清单，故未做）。
- **未做**：git 提交、版本号、真机（依工单约束）。
