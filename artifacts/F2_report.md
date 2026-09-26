# ADR-018 F2 —— 概览页数据组件丰富化

> 版本 v0.45.40（**未改任何版本号文件**） · 零新增后端端点（ADR-018 决策：只用既有探针/API） · 未改动 `backend/` 下任何文件

---

## ① 改动文件清单

| 文件 | 类型 | 说明 |
|---|---|---|
| `frontend/src/components/overview/AccountsHealthSection.tsx` | **新增** | 账号凭证健康卡（wp/dm 双引擎结论） |
| `frontend/src/components/overview/LiveStatusSection.tsx` | **新增** | 直播在线状态卡（在线人数/点赞/监听活性） |
| `frontend/src/components/overview/TaskHistorySection.tsx` | **新增** | 任务历史速览卡（最近 5 条 + 成败统计） |
| `frontend/src/components/overview/overview-page.tsx` | **修改** | 挂载上述三个 Section（+3 处 import、+4 行挂载） |

概览页由「3 个 Section」扩到「6 个 Section」：原 AI 运行状态 / 能力健康 / 运行中任务 保留不动，新增三卡并列在 `grid-cols-2` 栅格内。

未新增/未修改任何 `backend/` 文件，未新增任何 API 端点，未改版本号。

---

## ② 新卡片 ↔ 后端真实端点 ↔ 前端方法（逐项对照，硬性验收）

| # | 卡片 | 后端端点路径 | 端点定义位置（grep 实证） | 前端 client.ts 方法名 | 方法定义位置 |
|---|---|---|---|---|---|
| 1 | 账号凭证健康 | `GET /api/accounts` | `backend/api/accounts.py:498` `@router.get("")` → `list_accounts()`；字段由 `_to_raw_account()`（同文件 :436）下发 `wpEngine`/`dmEngine`/`lastRun` | `api.getAccounts()` | `client.ts:736` |
| 2 | 直播在线状态 | `GET /api/live/stream` | `backend/api/live.py:19` `@router.get("/stream")` → `get_stream()`；响应模型 `backend/models/live.py::LiveStreamResponse` | `api.getStream()` | `client.ts:913` |
| 3 | 任务历史速览 | `GET /api/tasks/history?limit=&offset=` | `backend/api/tasks.py:54` `@router.get("/history")` → `get_history()`；数据源 `backend/tasks_history.py:167 list_history()`（SQLite `tasks` 表） | `api.getTaskHistory(limit, offset)` | `client.ts:1310` |

路由前缀实证（`backend/main.py`）：

```
781: app.include_router(overview.router,   prefix="/api",          tags=["overview"])
783: app.include_router(accounts.router,   prefix="/api/accounts", tags=["accounts"])
784: app.include_router(live.router,       prefix="/api/live",     tags=["live"])
793: app.include_router(tasks.router,      prefix="/api/tasks",    tags=["tasks"])
```

⇒ `/api/accounts` + `/api/live/stream` + `/api/tasks/history` 三端点**全部真实存在**，前端三个方法**全部已存在于 client.ts**（本次未改动 client.ts）。

三卡均只读 **GET**，与既有 `AiRuntimeSection` / `CapabilityHealthSection` 同风格（`Section` + `Tone` + `Row`/`KeyValue` + `SkeletonRows`/`Blank`，颜色一律走设计令牌）。

### 缓存复用（不额外打后端）
- 账号卡复用 App 级常驻缓存 `queryKey: ["accounts"]`（`App.tsx:278`，30s 轮询）
- 直播卡复用 App 级常驻缓存 `queryKey: ["live-stream"]`（`App.tsx:290`，3s 轮询）
- 历史卡用 `queryKey: ["task-history", 0, 5]`，15s 轮询（与 App 的 `["task-history", 0]` 不同 key，避免影响既有分页缓存）

---

## ③ 被跳过的建议方向（诚实标注）

| 建议方向 | 结论 | 原因（grep 实证） |
|---|---|---|
| **④ 错误码 TOP-N** | **跳过** | 后端确有 `GET /api/errcodes`（`backend/api/errcodes.py:15`，`main.py:777` 挂载），但：① 它是**静态代码字典**（`errcode.all_codes()`），**没有任何「频次/TOP-N/聚合统计」字段或端点**（grep `top|freq|digest|aggregate` 于 `api/errcodes.py`、`api/probe.py` 均 **0 命中**）；② `client.ts` 中 **无对应前端方法**（grep `errcodes` 于 client.ts **0 命中**）。要做就得新增后端聚合逻辑或裸写 fetch —— 前者违反 ADR-018「零新采集」红线，后者违反「绝不调不存在的接口」与统一 `request()` 封装约定（会丢 `X-Member-Token` 被会员门禁拦成 401）。故**换做**任务历史速览（端点与方法均已存在）。 |
| 账号「按需单账号校验」 | 不做 | `POST /api/accounts/{name}/check`（`accounts.py:576`）虽存在且 client.ts 有 `checkAccount()`，但后端注释明写它是**重型按需校验**（真跑 validate_cookie + 请求 douyin）。概览页轮询它会打爆后端，故只读 `list_accounts` 已带出的 wp/dm 结论（与账号管理页同源同口径）。 |

---

## ④ 验收 A/B：真实命令与 exit code

**命令均在 `C:\Users\LOX\Desktop\DYchajian\DYAutoDM_v2\frontend` 下执行。**

### B. `npx tsc --noEmit` → **exit 0** ✅（最终态）

```
$ npx tsc --noEmit
TSC_EXIT=0
```

### A. `npm run lint` → **exit 0** ✅（最终态）

```
$ npm run lint
> dyautodm-frontend@0.45.40 lint
> tsc --noEmit
LINT_EXIT=0
```

（`npm run lint` 的脚本体就是 `tsc --noEmit`，见 `package.json`，故 A 与 B 同源同结论；`tsconfig.json` 开 `strict` + `noUnusedLocals` + `noUnusedParameters`。）

### 过程中的一次干扰（诚实记录，最终已解除）

首次运行全树时即 `exit 0`。**中途复跑**一度变成 `exit 2`，报错全部落在本次未触及的文件：

```
src/components/live/RoomManagePage.tsx(26,13): error TS6133: 'Search' is declared but its value is never read.   （+9 条同类）
src/components/settings/TagSection.tsx(59,31): error TS2339: Property 'bindings_section' does not exist ...
src/api/client.ts(995,13): error TS2304: Cannot find name 'DiscoveredRoom'   （中途出现）
```

`git status` 实证这些文件（`App.tsx`、`api/client.ts`、`settings/TagSection.tsx`、`live/RoomManagePage.tsx`、`platform-page.tsx`、`tasks-page.tsx`）当时正被**其他 ADR-018 并行子代理编辑中**；grep 确认 `DiscoveredRoom` 非本次引入。为自证改动干净，我用临时 `tsconfig.f2check.json`（`files` 只列本次 4 个文件）隔离编译，过滤后**本次文件零 error / 零 unused**（该临时文件校验完**已删除**，`ls tsconfig*.json` 仅剩 `tsconfig.json`、`tsconfig.node.json`）。

随后其他子代理落定，**最终复跑 A、B 双双 exit 0**，无需我再改动任何代码。

---

## ⑤ 空态 / 加载态 / 错误态（禁假成功 · 逐卡落实）

| 卡片 | 加载 | 取不到数据 | 有数据但为空 |
|---|---|---|---|
| 账号凭证健康 | `SkeletonRows rows={3}` | 显示 `读取账号列表失败：<errMsg>` + 数据源路径 | `尚未添加账号 —— 请先在「账号」页添加并登录` |
| 直播在线状态 | `SkeletonRows rows={2}` | 显示 `读取直播状态失败：<errMsg>` + 数据源路径 | 按三种成因分别措辞：引擎 `starting` →「正在连接直播流（尚未取到在线人数）」；引擎忙但未挂 WS →「WS 未连上，在线人数暂不可得」；否则 →「当前没有直播监听任务」。**只要 `alive=false` 就不展示任何数字**（后端把 `online_count` 初始化为 0，那是「尚未取到」不是「在线 0 人」） |
| 任务历史速览 | `SkeletonRows rows={3}` | 请求异常 → `读取任务历史失败：<errMsg>`；**后端 `ok=false` → 单独分支显示后端 error 原文**（`ok=false` 是读库失败，不是「没有任务」，绝不显示 0） | 仅当 `ok=true && list 空` 才显示 `暂无历史任务` |

另外的共同约束：
- 账号卡的 `wpEngine.level == "unknown"` 单列一档显示「**未校验**」，不并入「异常」计数，也不显示成「可用」——后端 TTL 缓存里没有结论 ≠ 账号有问题。
- 历史卡把 `ok=false` 与「空列表」**物理分开**，是本项目「禁止把失败空当成功空」铁律的直接落实。

---

## 中文总结

本次 ADR-018 F2 为概览页新增了 **3 个数据卡片**（账号凭证健康、直播在线状态、任务历史速览），概览页 Section 数从 3 个增到 6 个，成为真正的运行信息总览。

严格守住了三条红线：**零新增后端端点**（三个端点 `/api/accounts`、`/api/live/stream`、`/api/tasks/history` 全部 grep 实证既已存在，且 `client.ts` 中对应方法 `getAccounts()` / `getStream()` / `getTaskHistory()` 本就已封装，本次一个后端文件都没碰）、**未改版本号**、**未启动 dev server**。

三卡全部照抄既有 `AiRuntimeSection` / `CapabilityHealthSection` 的写法（`Section` + `Tone` + `Row`/`KeyValue` + 设计令牌），并复用 App 级常驻 React Query 缓存，没有引入额外的后端压力。三态处理严格落实「禁假成功」：`alive=false` 时不展示任何在线数字（区分「未启动」「等待开播」「WS 未连上」三种成因）、`ok=false` 与「空列表」物理分开、`unknown` 不并入异常计数。

**需要父会话注意两点**：一是建议方向「④ 错误码 TOP-N」**被我跳过**——后端 `/api/errcodes` 只有静态码表、没有任何频次聚合字段，`client.ts` 也没有对应方法，做它就必须新增后端逻辑或裸写 fetch，两者都违反红线，故改做任务历史速览；二是执行期间 `npm run lint` 曾一度报 exit 2，但那批报错全在**其他并行子代理正在编辑的文件**（`RoomManagePage.tsx`、`TagSection.tsx`、`client.ts`）上，我据此用只含本次 4 个文件的隔离 tsconfig 自证改动干净（临时文件已删）。**最终全树 `npm run lint` 与 `npx tsc --noEmit` 双双 exit 0**，验收 A/B 通过。
