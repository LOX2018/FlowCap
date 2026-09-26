# F1 报告 · 标签配置中心（ADR-018 F1）现状核实与收口

- 时间：2026-09-27
- 版本：v0.45.40（**未改动版本号**）
- 仓库：`C:\Users\LOX\Desktop\DYchajian\DYAutoDM_v2`（分支 `design/better-douyin`）
- 红线遵守：未 `git add` / 未 `git commit` / 未改版本文件 / 未重写 `config_tag.py` / 未启动浏览器 / 未调真实接口 / 未杀进程

---

## ① 三个待核实项的实证结论

### 待核实项 1：后端 5 个 `/api/settings/tags*` 路由是否真注册到 app

**结论：✅ 已接线（5 个路由全部注册且可调用）**

证据 1 —— `backend/main.py:794` 注册 settings 路由（模块顶层，非条件分支）：

```
$ grep -n "include_router(settings" backend/main.py
794:app.include_router(settings_api.router, prefix="/api/settings", tags=["settings"])
```

用 AST 校验该行确在**模块顶层**（不在 `if` 里，不会被开关跳过）：

```
$ python -c "...ast.walk... find include_router with prefix=='/api/settings'..."
include_router(settings, /api/settings) at module level lines: [794]
```

证据 2 —— 从真实 router 对象枚举路由（非读代码推测）：

```
$ cd backend && python -c "
import sys; sys.path.insert(0,'.')
from api import settings as s
for r in s.router.routes: print(','.join(sorted(r.methods or [])).ljust(8), r.path)"

GET      /schema
POST     /schema
GET      /tags            ← backend/api/settings.py:85
POST     /tags            ← backend/api/settings.py:93
DELETE   /tags/{tag_id}   ← backend/api/settings.py:101
POST     /tags/bind       ← backend/api/settings.py:115
GET      /tags/bind       ← backend/api/settings.py:124
POST     /scoped          ← backend/api/settings.py:138
GET      /scoped/{tag_id} ← backend/api/settings.py:162
POST     /reset           ← backend/api/settings.py:172
```

证据 3 —— 前端 client 与后端路径**逐条对得上**（`frontend/src/api/client.ts`）：

| client.ts | 方法 | 后端对应 |
|---|---|---|
| `listTags()` L1426 | GET | `/api/settings/tags` ✓ |
| `saveTag()` L1437 | POST | `/api/settings/tags` ✓ |
| `deleteTag()` L1444 | DELETE | `/api/settings/tags/{id}` ✓ |
| `bindTag()` L1454 | POST | `/api/settings/tags/bind` ✓ |
| `saveScoped()` L1469 | POST | `/api/settings/scoped` ✓ |
| `getScoped()` L1480 | GET | `/api/settings/scoped/{id}` ✓ |

### 待核实项 2：前端 TagSection 是否「用户点得到」（挂载 ≠ 可达）

**结论：✅ 用户点得到（导航项存在、可点击、点击后渲染）**

证据 —— `frontend/src/components/settings/settings-page.tsx`：

- L34 `import TagSection from "./TagSection";`
- L44 SectionKey 联合类型含 `"tag"`：`| "ai" | "agent" | "tag" | "notify" | "mcp";`
- L59 **TABS 数组里有可点导航项**：
  `{ key: "tag", label: "配置标签", hint: "发送策略：怎么发", icon: <Tags .../> }`
- L79~99 左侧 `<nav>` 用 `TABS.map` 渲染 `<button onClick={() => setSection(t.key)}>` ⇒ 每个 TAB 都是**真实可点按钮**（含 `tag`）
- L140 `{section === "tag" && <TagSection {...props} />}` ⇒ 点击后渲染

上层入口链路（证明不是死代码）：

- `frontend/src/App.tsx:27` `const SettingsPage = lazy(() => import("./components/settings/settings-page"));`
- `frontend/src/App.tsx:562` `{tab === "settings" && <SettingsPage {...pageProps} />}`
- `frontend/src/components/layout/sidebar.tsx:171~191` 侧栏底部独立「配置中心」入口，`onClick={() => setTab("settings")}`

⇒ **路径完整**：侧栏「配置中心」→ 左导航「配置标签」→ `TagSection`。

### 待核实项 3：`_KV_BIND_SECTION` 板块级绑定是否有 API 暴露

**结论：❌ 原本无（真实缺口）—— 本次已补（唯一改动点）**

补之前的实测（grep 全 backend，排除 `config_tag.py` 自身）：

```
$ grep -rn "bind_section\|bindings_section\|effective_bindings\|scope_of" --include="*.py" backend/ | grep -v config_tag.py
backend/services/dm_dispatch.py:126:  scope = config_tag.scope_of(account)   ← 唯一消费点
```

即：

- `bind_section()` / `bindings_section()` / `effective_bindings()` **零调用方**（`scope_of` 只被 `dm_dispatch` 调用，且**不传 section** ⇒ 永不命中板块级分支）
- `backend/api/settings.py` 补前只有 `/tags/bind`（整账号），**无任何板块级端点**
- 前端 `TagSection.tsx` 补前只有整账号一个下拉，`_KV_BIND_SECTION` 在 UI 上**完全不可达**

⇒ 判据：能力在位但**不可得**（与 ADR-018 里 MCP 入口同为「能力在位不可得」型缺口）。这是 B-4 §3.2 明写「**已做，待接通**」、§6 明写「**未接线**」的那一项。

---

## ② 台账 T8 行「无 API 路由 / 无前端入口」—— 当前是否成立？

**判定：台账 T8 已过期（doc-rot）— 该句「不成立」，但台账的「待拍板」部分仍成立。**

| 台账 T8 断言 | 实测 | 结论 |
|---|---|---|
| 「无 API 路由」 | `backend/api/settings.py` 有 5 个 `/tags*` 路由，`main.py:794` 已注册，真实 router 枚举可见 | ❌ **不成立**（已过期） |
| 「无前端入口」 | `settings-page.tsx:59` 有可点 TAB、L140 挂载、App.tsx:562 可达、sidebar.tsx:171 入口 | ❌ **不成立**（已过期） |
| 「`config_tag.py` +82 行（`_KV_BIND_SECTION` 等，向后兼容）」 | 现 274 行，含 `_KV_BIND_SECTION` L42、`bind_section` L204 | ✅ 成立 |
| 「待拍板 D1/D2/D3」 | ADR-018 L95「F1 的 D1/D2/D3 三个子决策**尚未拍板**」；B-4 §四 同 | ✅ **仍成立** |
| 「B-4 规划已出」 | `docs/配置标签体系规划_B-4.md` 178 行在库 | ✅ 成立 |

**精确表述**：台账把「整账号标签体系未接线」记成了当前状态 —— 那是 `docs/配置标签体系规划_B-4.md` §6（写作时点）的状态，**整账号层早已接线**；台账漏记了。真正未接线的是**第二层板块级绑定**（`_KV_BIND_SECTION`），恰好与 B-4 §6「**未接线**：新函数尚无 API 路由与前端入口」逐字吻合。

⇒ 台账 T8 应拆成两行修订：
- 整账号标签体系：**已接线**（5 路由 + 设置页入口）—— 本行过期
- 板块级绑定：`_KV_BIND_SECTION` **未接线**（本次补齐）

**ADR-018 自身也有同款 doc-rot**：L64「缺口：前端 `api/client.ts:1423` 已声明 `ConfigTagSummary` 但**后端无 `/api/settings/tags` 路由**」—— 与实测矛盾（`settings.py:85` 有该路由）。一并标注为待修订。

---

## ③ 补了什么 / 为什么只补这些

只补**一个真实缺口**：板块级绑定（`_KV_BIND_SECTION`）的 API + UI 入口。**未重写** `config_tag.py`（其 274 行向后兼容设计原样保留，改动仅在外围调用层）。

### 后端 `backend/api/settings.py`（+41 / -1）

1. `GET /tags` 响应增加 `bindings_section`（前端一次取全，省一次往返）
2. 新增 `POST /tags/bind/section` —— `TagBindSectionBody{account,section,tag_id}`；转发 `config_tag.bind_section()`；标签不存在 → 404；非法板块 → 400（含 `MANAGED_SECTIONS` 校验）
3. 新增 `GET /tags/bind/section` —— 返回全部板块级绑定

### 前端 `frontend/src/api/client.ts`（+134 / -0）

- `TAG_MANAGED_SECTIONS` / `TagManagedSection` 类型 / `TAG_SECTION_LABELS`（对应后端 `MANAGED_SECTIONS`，中文名唯一处定义）
- `bindTagSection()` / `listTagSectionBindings()`
- `listTags()` 返回类型补 `bindings_section?`

### 前端 `frontend/src/components/settings/TagSection.tsx`（+131 / -33）

- 账号绑定卡：每个账号从「1 个整账号下拉」扩为「整账号下拉（回落值）+ send/live/capture 三个板块下拉」
- 板块未绑时显示「跟随：<整账号标签名>」，实际生效值一眼可见（落实 B-4 §3.4「一处聚合」）
- 抽出 `selectStyle` 常量，消除两处下拉的重复样式

### 为什么没补别的

- 整账号体系 5 路由 + UI 入口**已完整可用** ⇒ 不动（判据 A）
- `config_tag.py` **一个字没改**（红线：不得重写，其向后兼容设计会被破坏）
- D1/D2/D3 中**会改产品运行时行为**的部分只出方案（见 ④）

### 零回归论证（H-22 冻结期核心）

板块级绑定的**默认态是空** `{}`：

```
默认态实测输出：
default bindings_section = {}
scope_of(账号X)          = None     ← 不传 section：与改造前逐字一致
scope_of(账号X, send)    = None     ← 未绑板块：回落整账号/全局
```

即：**不点任何板块下拉，所有消费方读数与改造前完全相同**。`dm_dispatch.py:126` 调的是 `scope_of(account)`（不传 section），本次改动对其**零影响**。

---

## ④ D1 / D2 / D3 落地情况

> 🔴 **未经用户单独拍板**：以下三项均按 ADR-018 给的**建议值**处理（用户授权「遇到需拍板的按 ADR 选择执行」，ADR-018 L95：「F1 的 D1/D2/D3 三个子决策尚未拍板，实施时按建议值落地并显式上报」）。**本处仅出方案，未改任何产品运行时行为**。

### D1 · 直播按房间绑标签 —— 建议值 B（房间级）→ ⚠️ 只出方案，未改代码

- ADR-018 L66 / B-4 §四 L131：建议 **B 房间级**（`live_rooms` 挂 `tag_id`），理由「直播间天然一场一策，账号级会互相覆盖」
- 实测现有结构：`backend/api/live_rooms.py` L94 `_FIELDS` 含 `strategy_id`（引用 `live_room_configs` 策略），**但无 `tag_id`**，且 `grep -n "tag" api/live_rooms.py` 零命中
- 实测策略层 `api/live_config.py` L27~30 明确「**策略层零身份字段**，身份由房间层承载」，`strategy_id` 已被房间引用；删除策略有 `unbind_strategy()` L455 自动解绑
- **为何不改**：给 `live_rooms._FIELDS` 加 `tag_id` 会改**产品运行时行为**（房间级参数解析），且动 ADR-003 房间登记层结构 ⇒ B-4 §五明确列为 **P3「触碰红线：是，需 ADR」**。H-22 冻结期 ⇒ **只出方案**
- **方案（待拍板后实施）**：`live_rooms._FIELDS` 增 `tag_id`；新增 `config_tag.scope_of_room(room_id, section)`；直播消费方解析顺序 `房间 tag_id > 账号板块绑 > 账号整绑 > 全局`；参照 `unbind_strategy()` 实现删除标签时的房间解绑。**需先出 ADR**

### D2 · 私信 WS 只管频率 —— 建议值 A（只管频率类）→ ✅ 已符合，无需改代码

- ADR-018 L66 / B-4 §四 L132：建议 **A**，理由「内容归 Agent 是既有契约，越界会职责打架」
- 实测**现状已天然符合**建议值：
  - `dm` section 现有 4 字段全为**频率/上限类**：`nickname_fallback_enabled / min_interval_sec / max_per_run / daily_cap`（实测打印）
  - `services/dm_dispatch.py` L86~104 `_LAZY_MAP` 消费的 section **只有 `send`**（实测 `sections consumed by dm_dispatch: ['send']`），全为风控频率参数
  - 内容逻辑在 `services/ai_reply.py` / `reply_kb.py`，**不在标签受管范围**（`MANAGED_SECTIONS = ('send','live','capture')`，已实测打印）
- ⇒ **「标签只管频率、内容归 Agent」已是当前实现的事实状态**，D2 按建议值**无需任何代码改动**。本次也未引入任何越界（新端点只写 `send/live/capture`）

### D3 · 采集策略新建 schema —— 建议值 B（新建）→ ⚠️ 只出方案，未改代码

- ADR-018 L66 / B-4 §四 L133 + §4.1 L135~148：建议 **B（已核实，非推断）**
- 实测复核（本次独立重跑，确认 B-4 §4.1 结论）：`capture` section 27 字段**全部**为「捕获与存储」类（`history_*` / `probe_*` / `userinfo_cache_sec` / `image_*`），**关键字搜索、目标范围、采集量上限、去重口径零配置项**
- **为何不改**：新建 `crawl_policy` schema 属**新增产品配置项**（B-4 §五 P2），会引入新的运行时可配参数 ⇒ H-22 冻结期 **只出方案**
- **方案（待拍板后实施）**：新建 section `crawl_policy`（`keyword_max / max_per_run / dedup_scope / target_scope` 等），加进 `app_config_schema.SECTIONS`；若纳入标签指引则同步加进 `config_tag.MANAGED_SECTIONS` 与前端 `TAG_MANAGED_SECTIONS` / `TAG_SECTION_LABELS`（**本次已把这两个前端常量抽成单点，届时只改一处**）

---

## ⑤ 验收命令与 exit code

| # | 命令 | exit | 结果 |
|---|---|---|---|
| 1 | `cd backend && python -m py_compile api/settings.py services/config_tag.py` | **0** | `PY_COMPILE_EXIT=0` |
| 2 | `cd frontend && npx tsc --noEmit` | **0** | `TSC_EXIT=0`（无输出） |
| 3 | `cd backend && python -m unittest test_config_tag` | **0** | `Ran 9 tests ... OK`（基线 9 OK，**零新增回归**） |
| 4 | `python scripts/check_contracts.py` | **0** | G0~G14 全 PASS |
| 5 | `python scripts/check_iron_rules.py` | **0** | 13 项：通过 11，**未通过 2（阻断 0 / 警告 2）** |

### 判据 B 说明

改动文件均过编译：`api/settings.py`（py_compile 0）、`client.ts` + `TagSection.tsx`（`npx tsc --noEmit` exit 0）。

### 判据 A 说明

结论「**已接线（整账号层）+ 已补齐缺口（板块级层）**」，证据见 ①②③，全部为真实文件行号 + 命令输出。

### 关于门禁 5 的两个警告（均为**既有**、非本次引入、不阻断）

- `R3 backend 无构建产物` —— 磁盘卫生类，指向 `backend/build/...exe`
- `R9 🔴 审计红线已触发: 41/20（since abd1f29）→ 应启动全库审计 + 功能冻结` —— 这**正好对应 ADR-018 D2 的约定**（L89「ADR-018 落地后须跑一次全库审计」）与本次任务的 H-22 冻结期红线。本次改动严格限制在**外围接线层**（新增端点 + UI 入口），未触碰任何产品运行时行为路径，与 R9 的冻结要求一致。

### 行尾（CRLF/LF）卫生

主会话做行尾清理时曾误将 `TagSection.tsx` 还原为 HEAD 版本，改动一度丢失。已用**字节级精确替换**重新应用（`open(p,"rb")` + `count(pattern)==1` 校验 + `replace`，未用整文件覆写）：

```
edit #1..#5 applied (count=1)
before: CRLF=0 LF=261
after : CRLF=0 LF=359     ← 全 LF，无行尾 churn
```

最终 `git diff --numstat` 与 `--ignore-all-space --numstat`：

| 文件 | numstat | ignore-all-space | 判读 |
|---|---|---|---|
| `backend/api/settings.py` | 41 / 1 | 41 / 1 | 一致，无 churn |
| `frontend/src/api/client.ts` | 134 / 0 | 134 / 0 | 一致，无 churn |
| `frontend/src/components/settings/TagSection.tsx` | **131 / 33** | **111 / 13** | 差 20/20 = 被替换的旧代码块（含缩进变更），**非行尾 churn**（文件 CRLF=0 已证） |

`TagSection.tsx` 两数相差 20 的原因：`--ignore-all-space` 把「缩进从 14 空格变 16 空格」的重排块折叠了 —— 这是**真实内容重排**（账号行由平铺改为整账号行 + 板块行两层结构），不是行尾问题。文件实测 `CRLF=0 LF=359`，行尾保持全 LF。

---

## 中文总结

**核实结论：标签配置中心「已接线」，但台账 T8 记的缺口是过期的，真实缺口在另一处。**

1. **整账号层早已完整可用** —— 后端 5 个 `/api/settings/tags*` 路由（`settings.py:85/93/101/115/124`）经 `main.py:794` 注册，真实 router 枚举可见；前端 `settings-page.tsx:59` 有可点导航项「配置标签」、L140 挂载，`App.tsx:562` + `sidebar.tsx:171` 链路完整 ⇒ **用户确实点得到**。台账「无 API 路由/无前端入口」这句**当前不成立**（doc-rot）；ADR-018 L64「后端无 `/api/settings/tags` 路由」也是同款过期断言。

2. **真实缺口是板块级绑定** —— `config_tag._KV_BIND_SECTION`（L42）配套三个函数 `bind_section/bindings_section/effective_bindings` 在补之前**零 API、零 UI、零调用方**（唯一消费点 `dm_dispatch.py:126` 调 `scope_of(account)` 不传 section，永不命中板块分支），与 B-4 §6「未接线」逐字吻合。

3. **只补了这一处**（最小必要）：后端加 `POST/GET /tags/bind/section` + `GET /tags` 回 `bindings_section`；前端加 `bindTagSection()` 与每个账号的 send/live/capture 三个板块下拉，板块未绑时显示「跟随：<整账号标签>」。`config_tag.py` **一字未改**。默认态 `bindings_section = {}` ⇒ 不点板块下拉时所有读数与改造前**逐字一致**，H-22 冻结期零运行时影响。

4. **D1/D2/D3 按 ADR 建议值处理，⚠️ 未经用户单独拍板**：D2（私信 WS 只管频率）实测现状**已天然符合**，无需改代码；D1（直播按房间绑，需给 `live_rooms` 加 `tag_id`、动 ADR-003 结构）与 D3（新建采集策略 schema，新增产品配置项）**均会改产品运行时行为 ⇒ 只出方案未改代码**，实施前需先出 ADR / 拍板。

5. **验收全绿**：`py_compile` 0、`npx tsc --noEmit` 0、`test_config_tag` 9/9 OK（基线一致，零新增回归）、`check_contracts.py` G0~G14 全 PASS、`check_iron_rules.py` 阻断 0（2 个既有警告，其中 R9 审计红线正对应 ADR-018 D2 与 H-22 冻结要求）。未 git add / commit，未改版本号。
