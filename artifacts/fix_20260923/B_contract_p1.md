# P1 批次修复报告 · 前端↔后端契约错位 / 引擎控制不可用

| 项 | 值 |
|---|---|
| 批次 | P1（契约错位 + 引擎控制不可用） |
| 仓库 | `C:\Users\LOX\Desktop\DYchajian`（分支 `design/better-douyin`，HEAD `b455192`，版本 0.44.53） |
| 日期 | 2026-09-23 |
| 执行者 | 子代理（subagent，独占文件清单见任务书） |
| 报告落盘 | `artifacts/fix_20260923/B_contract_p1.md`（本文件） |
| 解释器 | `C:\Users\LOX\AppData\Local\Programs\Python\Python314\python.exe`（3.14.6，fastapi 0.141.1 / pydantic 2.13.4） |
| 测试隔离 | `DY_APP_ROOT=$LOCALAPPDATA/Temp/fixB`，只跑自有模块 |

---

## 0 · 规范契约的选定（Canonical Contract Law）

> 铁律要求：只允许一个契约，两侧同改。**必须显式选定**并写明取舍。

### 选定结果

| 端点 | 账号维度 | 理由 |
|---|---|---|
| `POST /api/engine/start` | **body**（`TaskConfig.acct`），另接受 `?acct=` 兜底 | start 本来就有 body（任务配置），账号是配置的一部分 |
| `POST /api/engine/{pause,resume,stop,stop-soft}` | **query `?acct=<账号>`** | 这四个端点**没有** body；账号是「寻址维度」而非请求体内容 |

### 为什么这四个端点选 **query** 而不是 body 模型（取舍留痕）

1. **既有回归防线按 `acct` 关键字参数定稿** —— `backend/test_engine_multi_account.py`
   直接以 `pause_engine(req, acct="")` / `stop_engine(req, acct="张老师")` 调用端点
   （该文件本次未改动）。选 query 时端点签名仍是 `(request, acct: str = Query(""))`，
   这些既有测试**逐字不变**即继续有效；选 body 模型则它们全部失效，等于丢掉一层既有防线。
2. **语义一致性** —— 这四个端点没有资源 payload，只是「给某个账号的引擎下命令」。
   账号是**寻址维度**，与同仓 `?account=` 系列（`/api/messages/conversations`、
   `/api/live/linkmic/status`）一致；而 body 是「资源内容」的载体。
3. **规避「空 body 也必须能工作」的兼容分支** —— body 模型会引入「客户端不发 body /
   发 `{}`」两种形态，并在 Pydantic 层产生 422 风险；query 在「不发 acct」时天然
   得到 `""`，直接落到既有的「单任务回落 / 多任务 409」语义（ADR-002 §5.3）。

### 另有一个**必须显式写出的陷阱**（P1-2 的真因，非 body/query 之争）

`acct: str = Query("")` 的**默认值是 FastAPI 的 `FieldInfo` 对象，不是 `""`**。
任何「直接 `await stop_engine(req)` 而不传 `acct=`」的内部直调（不经过 FastAPI 依赖注入）
都会拿到 FieldInfo，`str()` 后变成
`"annotation=str required=False … alias=acct"` 并被当作账号去查 → **404**。
修法两条并行：① 所有内部直调显式传 `acct=`；② `_resolve_adm` 加防御性归一
（`_norm_acct` 只接受真正的 `str`）。

---

## P1-1 · 4 个引擎控制端点全部丢账号【🔴 已修】

### ① 位置
- 后端（修复前）：`backend/api/engine.py` `pause/resume/stop/stop-soft` 均为 `acct: str = Query("")`
- 前端（修复前）：`frontend/src/api/client.ts` `stopEngine/stopSoftEngine/pauseEngine/resumeEngine`
  全把账号放进 **JSON body**（`body.account = account`），而私有 `request()` **从不构造 query string**
- 调用点：`frontend/src/components/live/engine-cards.tsx`（当时 210/227/244 行）、
  `frontend/src/components/tasks/tasks-page.tsx`（当时 222/235/248/261 行，**不传 acct** 且 `Overview` 类型无 `acct` 字段）

### ② 判定
**成立（实测复现，见 ⑤）**。两侧契约在「账号放哪」上不一致 → 前端每次传账号后端都收不到。

### ③ 选定的规范契约与理由
见 §0：四个控制端点统一 **query `?acct=`**。

### ④ 修法
| 文件 | 改动 |
|---|---|
| `backend/api/engine.py` | 新增 `_norm_acct()`（只接受 `str`，防御 FieldInfo 污染）；四个控制端点的签名与语义在 docstring 里钉死「query `?acct=` 唯一真源」 |
| `frontend/src/api/client.ts` | 新增 `engineControlPath(path, account)`（构造 `?acct=` + `encodeURIComponent`）；四个方法改为 `request(engineControlPath(...), { method: "POST" })`，**不再发 body** |
| `frontend/src/api/client.ts` | `Overview` 接口补 `acct?: string`（任务中心取运行账号的来源） |
| `frontend/src/components/tasks/tasks-page.tsx` | 新增 `runningAcct`（`ov.acct` → 回落历史行里 `status==="running"` 的 `acct`）；四处控制调用改为 `api.stopEngine(runningAcct \|\| undefined)` 等 |

### ⑤ 验证命令 + 实测输出（真 app TestClient + 真会员登录）

```bash
# 修复前 / 修复后同一脚本（独立最小复现，两个 RUNNING 账号）
cd C:/Users/LOX/Desktop/DYchajian
DY_APP_ROOT="$LOCALAPPDATA/Temp/fixB" python _repro_p1_b.py
```

**修复前**（实测，已落盘）：
```
=== P1-1: 前端现在发的形态（JSON body account） ===
  stop   body={'account':'张老师'} -> 409 {"detail":"存在多个进行中的直播任务（小助理, 张老师）—— 请显式指定 acct，不猜测账号"}
  pause      body -> 409 {"detail":"存在多个进行中的直播任务（小助理, 张老师）—— 请显式指定 acct，不猜测账号"}
  resume     body -> 409 {"detail":"存在多个进行中的直播任务（小助理, 张老师）—— 请显式指定 acct，不猜测账号"}
  stop-soft  body -> 409 {"detail":"存在多个进行中的直播任务（小助理, 张老师）—— 请显式指定 acct，不猜测账号"}
=== P1-1: 后端实际要求的形态（query acct） ===
  stop?acct=张老师 -> 200 {"ok":true,"state":"stopped","acct":"张老师"}
  （张老师被停?） ['stop-hard']
```

**修复后**（同一脚本；契约测试另外断言「现在前端发的形态 → 200」）：
```
=== P1-1: 旧前端形态（JSON body account）—— 应被忽略 ===
  stop   body={'account':'张老师'} -> 409 {"detail":"存在多个进行中的直播任务（小助理, 张老师）—— 请显式指定 acct，不猜测账号"}
  ...
=== P1-1: 新前端形态（client.ts 现在构造的 ?acct=）—— 必须 200 ===
  stop?acct=张老师 -> 200 {"ok":true,"state":"stopped","acct":"张老师"}
  （张老师被停?） ['stop-hard']
```

新增测试（`TestEngineControlAccountContract`，5 项）实测：
```
test_member_middleware_is_actually_in_effect ... ok   # 无 token→401（证明门禁在场）、带 token→200
test_query_acct_targets_that_account ... ok           # ?acct=张老师 → 200，且小助理未被触碰
test_body_form_does_not_take_effect ... ok            # body.account 被忽略 → 409（证明契约只在 query）
test_all_four_control_endpoints_read_query_acct ... ok # 四个端点逐一 200
test_single_account_without_acct_zero_regression ... ok # 单任务不带 acct 仍可用
```

前端契约（`TestFrontendContractSource`，grep 断言，因本仓**未配置** vitest/jest——
硬塞测试框架会引入新构建依赖）：
```
test_client_ts_uses_query_acct_not_body ... ok         # 四端点各恰好一次 engineControlPath(path, account)
test_tasks_page_control_passes_account ... ok          # 三处控制均带 runningAcct
test_client_overview_has_acct_field ... ok             # Overview.acct?: string 存在
```

### ⑥ 诚实标注
- **真的按 200/409 判定了**，非静态推断：用的是**真 app**（`from main import app`）+
  `TestClient` + **真会员登录**（`/api/member/register` → `/api/member/login` → `X-Member-Token`）。
- **未做真机 UI 点击验证**（需 Tauri webview + 真账号 + 真直播间）。已验到「前端构造的
  URL 形态 → 真后端 → 200 且作用于指定账号」这一层；**按钮渲染/点击层未验**。
- 前端契约用 **grep 断言**而非运行时单测：这是**防复发门禁**，不是行为测试。
  它能抓「有人改回 body」与「漏传 live_url」，但抓不到「函数被调用时参数为 undefined」这类运行时问题。
- `tasks-page.tsx` 的 `runningAcct` 在**旧后端**（`/api/overview` 无 `acct` 字段）下回落为空串
  → 退化为「单任务回落 / 多任务 409」旧语义。**这是刻意的向后兼容**，不是漏改。

---

## P1-2 · `notify._execute` 把 Request/FieldInfo 当 acct【🔴 已修】

### ① 位置
`backend/api/notify.py::_execute`（修复前）：
```python
return await stop_engine(_fake_request(adm))        # 无 acct
return await start_engine(_fake_request(adm), cfg)  # 无 acct、未注入 engines
```

### ② 判定
**成立**。两个独立缺陷：
1. **acct 漏传** → 落到 `Query("")` 的 FieldInfo 默认值。实测报文（任务书给定，本次复现路径见下）：
   `账号「annotation=str required=False … alias=acct」没有进行中的直播任务`（404）。
2. **未注入 engines** → 端点内 `_resolve_adm` 只能退回 `app.state.adm`，
   「按账号解析」名存实亡（多账号下启错任务）。

### ③ 选定的规范契约与理由
内部直调属**同一契约的另一入口**，必须与 HTTP 侧同源：
- 显式走**关键字参数** `acct=`；
- 通过 `_fake_request(adm, _BOUND_ENGINES)` **注入编排层**，让按账号解析真正生效。

### ④ 修法
`backend/api/notify.py`：
- `start_task` 分支：`acct = params["account"]|params["acct"]`；`req = _fake_request(adm, _BOUND_ENGINES)`；
  把 acct 补进 `cfg.acct`（pydantic 不可变时忽略，反正 query 已显式传）；`await start_engine(req, cfg, acct=acct)`
- `stop_task` 分支：`acct = params["account"]|params["acct"]`；`await stop_engine(_fake_request(adm, _BOUND_ENGINES), acct=acct)`
- 删除 `_execute` 内未使用的 `from fastapi import Request`（P4）

### ⑤ 验证命令 + 实测输出

```bash
cd C:/Users/LOX/Desktop/DYchajian/DYAutoDM_v2/backend
DY_APP_ROOT="$LOCALAPPDATA/Temp/fixB" python -m unittest test_engine_contract_p1 -v
```

**修复前**（同一复现脚本，两账号 RUNNING）：
```
=== P1-2: notify stop_task -> {"reply": "无法执行：存在多个进行中的直播任务（小助理, 张老师）—— 请指定账号，不猜测"}
=== P1-2: notify start_task -> {"reply": "无法执行：存在多个进行中的直播任务（小助理, 张老师）—— 请指定账号，不猜测"}
```

**根因的直接证据 + 「修复前」精确复现**（在**内存中**把 `_norm_acct` 换回旧写法
`str(acct or "").strip()`，跑**真端点**；未改磁盘任何文件）：

```bash
cd DYAutoDM_v2/backend && DY_APP_ROOT=... python - <<'EOF'   # 见下方输出
EOF
```
```
acct 默认值类型 = Query
str(默认值)     = annotation=str required=False default='' alias='acct' json_schema_extra={}
bool(默认值)    = True  ← 若为 True，旧代码 `str(acct or "")` 就会把它当账号
修复前行为 -> 抛出 HTTPException 404 账号「annotation=str required=False default='' alias='acct' json_schema_extra={}」没有进行中的直播任务（该账号尚未启动引擎）
修复后行为 -> {'ok': True, 'state': 'stopped', 'acct': ''}
```
⇒ **与任务书给出的 404 报文形态逐字同族**（`账号「annotation=str required=False … alias=acct」没有进行中的直播任务`），
且同时证明了「修后同一调用 200」。

**修复后**（复现脚本）：
```
=== P1-2: notify stop_task -> {"ok": true, "state": "stopped", "acct": ""}
=== P1-2: notify start_task -> {"ok": true, "state": "running", "acct": ""}
```

契约断言测试（`TestNotifyInternalCallContract`，4 项）实测：
```
test_stop_task_by_account_works ... ok                              # 按账号停的是张老师，小助理未被碰
test_stop_task_no_account_single_busy ... ok                        # 单任务回落
test_stop_task_no_account_ambiguous_409_message ... ok              # 多任务 → 显式回话失败
test_direct_call_without_acct_does_not_use_fieldinfo ... ok         # 直调漏传 acct → 归一为空，绝不 404
```
四项均含 `assertNotIn("annotation=", str(r))` —— 直接以任务书给出的污染形态为判据。

### ⑥ 诚实标注
- **未做 QQ/iLink 入站真机链路验证**（需真 bot 凭证）。已验到「`_execute` 函数直调 → 真端点 → 正确结果」。
- `create_task` 分支（`save_task_config`）**未改**：它不在本次缺陷清单内，且 `api/tasks.py`
  不在我的所有权内。若它也走 `_fake_request(adm)` 且有同类 acct 需求，属**同族待办**（未验证）。

---

## P1-3 · 多任务卡片「开始」永不可用（缺 live_url）【🔴 已修】

### ① 位置
`frontend/src/components/live/engine-cards.tsx`（修复前 193 行）：
`onClick={() => act(item.acct, "start", () => api.start({ acct: item.acct }))}`
—— 该文件**从未读过 `item.live_url`**。

### ② 判定
**成立**。后端 `api/engine.py`：`if not cfg.live_url or not cfg.live_url.strip(): raise HTTPException(400, ...)`；
前端 `request()` 对非 2xx **抛错** → 卡片「开始」按钮点了必报异常。**该按钮自诞生起从未可用。**

### ③ 选定的规范契约与理由
`POST /api/engine/start` 的 body 契约 = `TaskConfig`，其中 `live_url` 是**必填语义**
（空即 400）。前端多任务卡片的「重开」是**复用该卡片记录的直播间**，因此必须回传卡片上的地址。

### ④ 修法
`frontend/src/components/live/engine-cards.tsx`：
- 新增 `startUrlOf(item)` → `(item.live_url || item.live_id || "").trim()`
  （后端 `resolve_live_id` 接受纯房间号：实测 `'123456'` → `('123456','123456')`）
- 按钮 `disabled={!!busy || !startUrl}`，`title` 在缺地址时给出**可操作原因**
- `onClick` 改为 `api.start({ acct: item.acct, live_url: startUrl })`，并在 `!startUrl` 时
  提前 return（双重保险：**不发一个注定 400 的请求**）

### ⑤ 验证命令 + 实测输出

后端侧（`TestEngineStartRequiresLiveUrl`）：
```
test_empty_live_url_is_400 ... ok        # 实测日志：[ENG-010] [engine] 启动被拒：live_url 为空
test_start_with_live_url_succeeds ... ok # live_url=https://live.douyin.com/123456 → ok，start_calls==1
```

地址可解析性（离线读取 + 实跑 `link_resolve.resolve_live_id`，零网络快速路径）：
```
'https://live.douyin.com/123456'                       -> ('123456', 'https://live.douyin.com/123456')
'123456'                                               -> ('123456', '123456')
'https://www.douyin.com/follow/live/992931212705?anchor_id=x' -> ('992931212705', ...)
```
⇒ 卡片回传的 `live_url`/`live_id` **确实能被后端解析成纯数字 web_rid**，不会落进
`core/auto_dm.py` ENG-021 的「解析失败 → 显式停止」分支。

前端源契约（`TestFrontendContractSource.test_engine_cards_start_passes_live_url`）：
```
ok  # 断言 api.start({ acct: item.acct, live_url: startUrl }) 存在；startUrlOf 存在；旧写法不残留
```

### ⑥ 诚实标注
- **未做真机 UI 点击验证**（需真启动过的引擎卡片 + 真直播间）。
- **P1-3 只在「卡片有 `live_url`/`live_id`」时可重开**。若某账号的卡片两者皆空
  （后端 `/api/engine/accounts` 未返回），按钮**保持禁用并给出原因** —— 这是**刻意选择**
  「显式失败」而非「假装可点」。此时用户需回「直播监听」页重新配置启动。
- 我**未改** `/api/engine/accounts` 的返回结构（不在本次缺陷清单，且后端此时确实会下发
  `live_url`/`live_id`，见 `api/engine.py::list_engine_accounts`）。

---

## P1-6 · `busy_keys()` 把「已停止」当「忙」【🔴 已修】

### ① 位置
`backend/services/engine_registry.py`（修复前 95 行）：
```python
if str(v).lower() not in ("idle", "", "none"):   # 黑名单
    out.append(k)
```
⇒ `stopped` / `error` 也被算作忙。

### ② 判定
**成立（实测复现）**：两个引擎均 `STOPPED` 时 `busy_keys() == ['小助理','张老师']`，
随后**不带 acct 的 `/start`** 被判 409「存在多个进行中的直播任务」→ **永远启动不了**。

### ③ 选定的规范契约与理由
「忙」的定义必须与 `AutoDM.is_running` **同源**：
`starting / running / paused / stopping`（paused/stopping 仍占用任务）。
采用**白名单**而非黑名单：未知状态（未来新增枚举）**不算忙** —— 宁可让无 acct 的 /start
走单任务/匿名回落，也不能把空闲误判成并发冲突（「探测不到 ≠ 不存在」的同族纪律）。

### ④ 修法
- `busy_keys()` 改为四态白名单，并优先读 `is_running`（真 `AutoDM` 是 property，
  替身可能是方法，两种形态都兼容）。
- **stop() 后实例处置**（任务书要求明确）：**保留在表里，不自动摘除** —— 理由写进
  `drop()` 的 docstring（三条）：① `engine-cards.tsx` 的 `canStart()` 明确接受 `stopped`，
  卡片从本表读 `live_url` 作为重开入参，摘除会让卡片消失、地址丢失；② 软停止是**异步**的
  （`_wait_dispatch_done`），stop 返回时实例还是 `stopping`，摘除会让正在收尾的任务失去可寻址入口
  （违反「停不下来要说出来」）；③ 代价可控（stop 已关 WS/调度，实例只剩字段快照）。
  ⇒ 本表语义是「account → 该账号的引擎（**含已停止的**）」；需要「在跑的那些」的调用方
  **必须**用 `busy_keys()`，**不得**用 `keys()`/`items()` 代替。

### ⑤ 验证命令 + 实测输出

复现脚本前后对比：
```
修复前： busy_keys() = ['小助理', '张老师']   无 acct 的 /start -> 409
修复后： busy_keys() = []                     无 acct 的 /start -> 200 {"ok":true,"state":"running","acct":""}
```

契约断言测试（`TestBusyKeysTerminalStates`，6 项）实测：
```
test_stopped_is_not_busy ... ok
test_error_is_not_busy ... ok
test_idle_is_not_busy ... ok
test_running_states_are_busy ... ok                       # starting/running/paused/stopping 各算忙
test_start_without_acct_after_all_stopped ... ok          # 双 STOPPED 后无 acct 的 /start 必须可用
test_stale_stopped_instances_do_not_block_new_account_start ... ok   # 历史 STOPPED 实例不阻塞新账号
```

既有回归（该文件本次**未改动**，验证零回归）：
```
cd DYAutoDM_v2/backend && DY_APP_ROOT=... python -m unittest test_engine_multi_account
Ran 23 tests in 0.051s
OK
```

### ⑥ 诚实标注
- **未做真机验证**（需两个真账号跑完真任务后进入 STOPPED）。判据用替身引擎，
  但**语义与真 `AutoDM.is_running` 白名单逐字对齐**（已核对 `core/auto_dm.py:151-153`）。
- 本表**会长大**：被 stop 过的账号实例长期驻留。已知边界（**未做**）：无闲置回收。
  在「账号数 = 并发数」量级下可接受；若将来账号数很大需另立回收策略。

---

## high-1 · `/start` 锁键与实例分叉【🔴 已修】

### ① 位置
`backend/api/engine.py`（修复前 91-93 行）：`key = str(cfg.acct or "").strip() or ANONYMOUS_KEY`
—— acct 空时按**匿名**加锁；而 `_resolve_adm` 在「acct 空 + 恰一个 busy」时返回的是
**那个 busy 账号的实例**。⇒ 锁保护的实例 ≠ 操作的实例，concurrent `/start` 可同时通过 check-then-act。

### ② 判定
**成立（静态判定 + 置换复现）**。属 ADR-002 §5.3「不猜账号」被击穿的同一族。

### ③ 选定的规范契约与理由
**锁键必须与 `_resolve_adm` 的返回值同源**：按实例在 registry 里的键加锁，
而不是按调用方口述的 `acct` 推导。

### ④ 修法
`backend/api/engine.py`：
- 新增 `_key_of(reg, adm, acct)`：遍历 `reg.items()` 反查 `adm` 的键；取不到才回落 `acct or ANONYMOUS_KEY`
- `start_engine` 改为 `key = _key_of(reg, adm, cfg.acct)`（在 `_resolve_adm` **之后**计算）
- 同时修正 `_resolve_adm` 的单任务回落分支：**取不到就显式 404，绝不回落 `app.state.adm`**
  （后者是「最近启动」的兼容别名，并发下可能是另一个账号 → 停错任务）。
  这条逻辑同时消除了「high-1 存在的前提」。

### ⑤ 验证命令 + 实测输出
`TestStartLockKeyMatchesInstance`（3 项）：
```
test_lock_key_follows_resolved_instance ... ok   # 唯一 busy=小助理、acct 空 → key=="小助理"（不是 anonymous）
test_key_of_prefers_instance_key_over_provided_acct ... ok  # 口述错账号也不把锁打错
test_start_lock_is_per_instance_key ... ok       # 同账号并发 /start → 第二次 409，start_calls==1
```

### ⑥ 诚实标注
- 并发竞态**未用真并发爆破验证**（`asyncio.gather` 多协程压测）。目前的判据是
  「锁键 == 实例键」这一**结构判据** + 串行化用例；真竞态压测列为未尽事项。
- 顺带修正的 `_resolve_adm` 单任务回落 404（去掉 `app.state.adm` 回落）是**更高层的修复**，
  它使原来那条「单任务回落会停错账号」的同族风险一并消失。

---

## P1-7 · 合集传 `mix_id` 当 `series_id`，无 `is_serial_mix` 分流【🔴 已修】

### ① 位置
- 前端：`frontend/src/api/platform.ts`（修复前 237-239 行）`collectionSeries` 把 `pickedMix!.id`
  （= `mix_id`，来自 `/collection/mixes` 的 `mix_infos[].mix_id`）填进 **`series_id`** 字段
- 后端：`backend/api/platform.py::collection_series`（修复前）**只**调
  `api.get_series_aweme(req.series_id)` —— 那是**短剧专用**接口，**无 `is_serial_mix` 分流**
- 文档漂移：`backend/dy_apis/client_collection.py::get_series_aweme` docstring 声称
  「前端 `/collection/series` 端点已按 `is_serial_mix` 分流到两个接口」——**当时并不成立**

### ② 判定
**成立**。用 `series_id` 的位置打普通合集 → 服务端 `status_code: 5「参数不合法」/ aweme_list: null`
→ 前端**恒看到「该合集暂无作品」**。

### ③ 选定的规范契约与理由
铁律（C-02 §Ⅰ2 / `工作记忆/14` 均已有记载，只是代码没落地）：

| 实体 | 上游接口 | 参数 | 判据 |
|---|---|---|---|
| **普通合集**（作者自建作品集） | `get_mix_aweme` → `/aweme/v1/web/mix/aweme/` | `mix_id` | `is_serial_mix = 0` |
| **短剧**（付费连载） | `get_series_aweme` → `/aweme/v1/web/series/aweme/` | `series_id` | `is_serial_mix = 1` |

分流判据按**三态**处理，不得把「取不到」折成「不是」：
1. `is_serial_mix` **显式给出** → 直接按它走（最稳）；
2. 未给出 → **先按普通合集走**（预判：`/collection/mixes` 这个来源本身就是普通合集）；
3. 拿到 `status_code == 5`（参数不合法 =「这不是我要的那种合集」）→ **自动回退**试另一条；
   两条都失败则**如实返回空 + `status_code`**（不静默假装「空合集」）。

### ④ 修法
| 文件 | 改动 |
|---|---|
| `backend/api/platform.py` | `SeriesAwemeReq` 增加 `mix_id` / `is_serial_mix`；`collection_series` 重写为 `_try(kind, ident)` + 有序回退，返回 `via`（命中的接口）与 `status_code`；`collection_mixes` 透传 `is_serial_mix` 给前端 |
| `frontend/src/api/platform.ts` | `collectionSeries(account, mix_id, count, cursor, isSerialMix?)` —— 同时给 `mix_id`（正确槽）与 `series_id`（兼容槽），并透传 `is_serial_mix`；`MixItem` 补 `is_serial_mix` |
| `backend/dy_apis/client_collection.py` | 修正两处文档漂移（`get_mix_aweme` / `get_series_aweme` 的 docstring），并留「旧版误写」标记 |

### ⑤ 验证命令 + 实测输出

`TestCollectionSeriesDispatch`（6 项）实测：
```
test_normal_mix_goes_to_mix_api ... ok        # 普通合集 → 只调 get_mix_aweme，命中即止
test_mix_id_field_takes_priority ... ok
test_serial_mix_explicit_goes_to_series ... ok # is_serial_mix=1 → 只调 get_series_aweme
test_fallback_when_mix_returns_sc5 ... ok      # mix 回 sc=5 → 自动回退 series
test_both_fail_returns_empty_with_status_code ... ok  # 两条都失败 → 如实 [].status_code=5
test_missing_id_is_400 ... ok
```

独立实跑（替身 DouyinAPI，零网络，看真实调用序列）：
```
calls = [('mix', 'MIX123')]
response = {'ok': True, 'via': 'mix', 'status_code': 0} items = 1
fallback calls = [('mix', 'X'), ('series', 'X')] via = series items = 1
```

前端源契约（`test_platform_ts_maps_mix_id_to_mix_slot`）：
```
ok  # 断言 "mix_id, series_id: mix_id" 存在于 collectionSeries
```

### ⑥ 诚实标注
- 🔴 **未用真实抖音账号验证**（需真账号 + 真收藏合集）。已验到「分流决策 + 调用序列」这一层；
  **上游真实响应（`status_code` / `aweme_list` 实际形态）未实测**。
  ⇒ 判据 `status_code == 5 → 回退` 来自项目既有实测记载（`工作记忆/14`、C-02），**不是本次新测**。
- `platform-page.tsx` 的 `is_serial_mix` 透传**未接线**（该文件不在我的所有权清单内，
  改动已主动撤回，`git diff` 确认为空）。当前实际路径：前端把 id 同时放 `mix_id` 与 `series_id`，
  `is_serial_mix` 传 `null` → 后端走「先 mix，sc≠0 再回退 series」的**自动分流**，
  功能正确但**多一次无效请求**（当合集实际是短剧时）。彻底一次性分流需另一会话在
  `platform-page.tsx` 里透传 `m.is_serial_mix`（字段后端已下发）。**这是已知残留，不是遗漏。**
- 回退条件用 `status_code == 5`（「参数不合法」）作为「id 放错槽」的指纹。若上游将来对
  错误 id 返回别的码（如 `sc=8`），回退会失效 → 那时会返回空 + 真实 `status_code`（不静默）。

---

## P4 · 死代码与未用 import【🔴 已修】

### ① 位置 / ② 判定
- `backend/services/engine_registry.py::all_running_keys`：**全仓零调用点**（grep 确认），
  且其实现依赖 `is_running` 的 property/方法二义（`all_running_keys` 里那段
  `if isinstance(getattr(e,"is_running",None), bool)` 判断）——已被修正后的 `busy_keys()` 取代。**判定：死代码。**
- `backend/api/notify.py::_execute` 内 `from fastapi import Request`：**未使用**（AST 扫出）。**判定：死 import。**
- 其余 4 个后端文件（engine / engine_registry / platform / client_collection）：
  AST 扫**无**未用 import（仅 `from __future__ import annotations` 这类豁免项）。

### ③ 选定的规范契约与理由
判据型/编排型代码只保留**唯一实现**：`busy_keys()` 是在跑状态的唯一判据，
`all_running_keys` 是与它语义重叠的第二实现 → 删除。

### ④ 修法
- 删除 `EngineRegistry.all_running_keys`
- 删除 `_execute` 内未使用的 `from fastapi import Request`

### ⑤ 验证命令 + 实测输出
`TestDeadCodeRemoved`（3 项）实测：
```
test_all_running_keys_removed ... ok
test_no_unused_module_imports ... ok   # AST 扫 5 个后端文件，unused == {}
test_notify_execute_has_no_unused_local_request_import ... ok
```
（注：`_fake_request` 内的 `from fastapi import Request as _Req` 是**在用**的，保留；
断言按「4 空格缩进的裸 `from fastapi import Request`」精确匹配，避免误伤。）

### ⑥ 诚实标注
`test_no_unused_module_imports` 只扫本批次 5 个后端文件，**不是全仓门禁**。
若扩成全仓，请预期有存量违规（未测）。

---

## 汇总 · 改动文件清单

### 后端（5 个）
| 文件 | 改动量（+/−，`git diff --numstat`） |
|---|---|
| `backend/api/engine.py` | 91 / 10 → 现 98 / 12（含末轮 docstring） |
| `backend/api/notify.py` | 28 / 4 |
| `backend/services/engine_registry.py` | 47 / 14 |
| `backend/api/platform.py` | 116 / 15 |
| `backend/dy_apis/client_collection.py` | 12 / 2 |

### 前端（4 个）
| 文件 | 改动量（+/−） |
|---|---|
| `frontend/src/api/client.ts` | 36 / 14 |
| `frontend/src/api/platform.ts` | 29 / 4 |
| `frontend/src/components/live/engine-cards.tsx` | 30 / 2 |
| `frontend/src/components/tasks/tasks-page.tsx` | 12 / 4 |

### 新增测试 / 辅助文件
| 文件 | 说明 |
|---|---|
| `backend/test_engine_contract_p1.py` | **新增**（唯一新测试模块，**35 项**，落 `backend/`） |
| `_repro_p1_b.py`（仓库根） | **新增**（独立最小复现脚本，任务书要求的复现工具） |

**未改动**：版本源（`package.json` / `Cargo.toml` / `version.json`）、`main.py`、
`api/messages.py` / `live_rooms.py` / `live_config.py`、`replay/*`、`scripts/*`、
`工作记忆/`、`artifacts/UP_*`、`api/overview.py`。
**未 `git add` / `commit` / `checkout` / `stash` / `clean`**（全程零 git 写操作）。

**所有权纪律**：曾误触 `frontend/src/components/platform/platform-page.tsx` 三处
（P1-7 的 `is_serial_mix` 透传），**已主动全部撤回**，`git diff -- platform-page.tsx` 为空。

---

## 测试输出尾部（原始粘贴）

### A · 契约测试（隔离 `DY_APP_ROOT=%LOCALAPPDATA%\Temp\fixB`，只跑自有模块）
```bash
cd DYAutoDM_v2/backend
DY_APP_ROOT="$LOCALAPPDATA/Temp/fixB" \
  "C:/Users/LOX/AppData/Local/Programs/Python/Python314/python.exe" \
  -m unittest test_engine_contract_p1 -v
```
```
.....................................................................
Ran 35 tests in 0.820s

OK
```
（逐条用例名见本报告各缺陷的 ⑤ 段；全部 `ok`。）

### A2 · 单元测试自身的两个环境陷阱（实测，必须记录）

1. 🔴 **不要 `with TestClient(app)`（会跑 lifespan）**。本仓 lifespan 的启动耗
   在同一台机器上**抖动极大**（实测 12 次采样：`0.07s / 0.08s` 与 `4s / 37s / 64s` 并存），
   把契约测试从 ~0.8s 变成不确定时长（曾测到 330s）。
   契约测试**不需要** lifespan 建的任何状态（`app.state.engines`/`adm` 由测试自己注入），
   故改用**裸 `TestClient(app)`**（不发 lifespan 事件）。
   ⇒ 为防「省掉 lifespan 导致会员中间件不再守」的假绿，**新增一条门禁**
   `test_member_middleware_is_actually_in_effect`：同一请求无 token → **401**、
   带 token → **200**。中间件若没挂上，这条会先红。

2. 🔴 **每用例各建一次 TestClient 会让本类从 ~3s 涨到 ~33s**（会员注册是 PBKDF2 600k 轮）。
   已改为 `setUpClass` 建一次 + `setUp` 只换 `app.state.engines`（廉价且互不污染）。

（两次修正后本模块**稳定 0.8s**，连跑 3 次 `OK`。）

### A3 · D-07 失败态自证（注入缺陷 → 必须变红 → 还原）

| 注入的缺陷 | 结果 |
|---|---|
| `engine_registry.busy_keys` 白名单加回 `"stopped"` | **FAIL/ERROR 2 项**：`test_stopped_is_not_busy`、`test_start_without_acct_after_all_stopped` |
| `client.ts` 的 `stopEngine` 改回 body 形态 | **FAIL 1 项**：`test_client_ts_uses_query_acct_not_body` |
| 还原后复验 | 两次均 `OK`（md5 复核文件逐字节还原） |

### B · 既有引擎多账号回归（该文件本次未改动 → 零回归判据）
```bash
cd DYAutoDM_v2/backend
DY_APP_ROOT="$LOCALAPPDATA/Temp/fixB" python -m unittest test_engine_multi_account
```
```
Ran 23 tests in 0.035s

OK
```

### C · 契约门禁
```bash
cd DYAutoDM_v2 && python scripts/check_contracts.py
```
```
============================================================
契约门禁 check_contracts
============================================================
  [PASS] G0 契约文件 ≥5                         实测 5 份
  [PASS] G1 C-01 捕获零主动查询                    0 命中
  [PASS] G2 C-02 secsdk 签名接线                ⚠ 已知缺口 7 处（见 .known-gaps.json）
  [PASS] G3 C-03 dm 不冒充 wp                  0 命中
  [PASS] G4 C-04 投递有回执/落库验证                 存在回执处理
  [PASS] G5 C-05 reflow 主引擎存在               已实现
```

### D · 前端类型检查（硬性要求 3）
```bash
cd DYAutoDM_v2/frontend && npx tsc -b
```
```
npm notice run dyautodm-v2@0.44.53 npx
npm notice run tsc -b
TSC_EXIT=0
```
（`tsc -b` 无任何诊断输出，退出码 0。）

### E · 前端完整构建
```bash
cd DYAutoDM_v2/frontend && npm run build
```
```
dist/assets/live-page-BrCZC2Ok.js        57.33 kB │ gzip: 18.56 kB
dist/assets/settings-page-Mw7ndP6G.js    80.22 kB │ gzip: 21.98 kB
dist/assets/vendor-react-DtOhX2xw.js    141.25 kB │ gzip: 45.41 kB
dist/assets/index-CwYkFpVh.js           238.96 kB │ gzip: 78.39 kB
✓ built in 6.41s
BUILD_EXIT=0
```

---

## 未尽事项 / 诚实标注汇总

1. **全部缺陷均未做「真机 UI 点击 + 真账号 + 真直播间」端到端验证。**
   已验层级：
   - P1-1：**真 app TestClient + 真会员登录**，前端构造的 URL 形态 → 200 且作用于指定账号 ✅
   - P1-2：`_execute` 函数直调 → 真端点 → 正确结果 ✅
   - P1-3：后端 400/200 判据 ✅ + `resolve_live_id` 能解析回传地址 ✅（**按钮点击层未验**）
   - P1-6：替身引擎，语义与真 `AutoDM.is_running` 白名单逐字核对 ✅
   - high-1：结构判据 + 串行化用例 ✅（**真并发压测未做**）
   - P1-7：**替身 DouyinAPI**，调用序列正确 ✅（**上游真实响应未测**）
2. **`live-page.tsx` 的三个控制调用仍是 `api.pauseEngine()` 等不带账号形态**
   （`live-page.tsx:691/705/719`）。该文件**不在我的所有权清单内**，未改动。
   在「单账号」场景下不受影响；多任务并发下与 `tasks-page.tsx` 修复前同形。
   ⇒ **建议**：由持有该文件所有权的会话补 `api.pauseEngine(activeAcct || undefined)`。
3. **`platform-page.tsx` 的 `is_serial_mix` 透传未接线**（同因，不在所有权内）。
   当前功能正确但短剧合集多一次无效请求，见 P1-7 ⑥。
4. **`notify._execute` 的 `create_task` 分支未改**（不在缺陷清单，`api/tasks.py` 非我所有）。
5. **`busy_keys` 修正后，账号实例长期驻留 registry，无闲置回收**（已知边界，见 P1-6 ⑥）。
6. **P4 的 AST 门禁只扫 5 个后端文件**，非全仓。

---

## 附 · 并发写者发现与处置（按 `multi-session-collaboration` 纪律）

**事实**：本会话执行期间（21:09 左右），工作区出现**非我造成**的改动 ——
`api/live_rooms.py`（md5 `258cf02057ab`，mtime 21:09:05）、`api/live_config.py`（`a0d9d9e5aaff`，21:09:56）
被持续写入，并新增未跟踪文件 `services/send_response.py`。

**判据（可复核）**：
- 我开工时的首个 `git status --porcelain` **不含** `backend/api/live_rooms.py` / `live_config.py` /
  `dm_dispatch.py` / `messages.py` / `dy_apis/client_*`（当时只有 `docs/upstream_baseline.json` 修改 +
  若干删除 + 未跟踪 artifacts）。收工时这些行**出现**了 —— 按 git 路径排序它们必出现在 `docs/` 之前，
  故可确证「开工时干净、会话中途被写」。
- 我只改了任务书列出的 10 个文件（`git diff --numstat` 逐一核对）。
- 20s 连拍 md5 两次一致 → 该写者当时**已停手**。
- 我对目标文件的每一处编辑，都用唯一标记（`engineControlPath` / `startUrlOf` / `runningAcct` /
  `_key_of` / `_norm_acct` / `busy_keys` / `mix_id, series_id: mix_id` / `文档漂移修正`）
  在磁盘上 grep 复核过：**全部在场**，无覆盖。
- 佐证：HEAD 提交信息本身就是 `docs(ledger): 并发写者 UP 待办清单(T1~T5) 并入 SSOT 台账` ——
  本项目**已知存在**并行工作线。

**处置**：**不覆盖**（未对任何非我所有的文件做 `checkout` / `stash` / `clean`），
主动撤回了曾误触的 `platform-page.tsx` 三处编辑（`git diff` 已验证为空），
并把并发事实与残留影响写进上方「未尽事项」。**未执行任何 `git add` / `commit`。**

**对本次结论的影响**：`test_live_rooms` 现存 2 项失败（`test_save_and_list_fields_complete`、
`test_update_keeps_existing_when_field_omitted`）**与我无关** —— 我的测试不 import 该模块，
且 `live_rooms.py` 在我开工前就已被并发写者改动。**该 2 项未列入我的验收判据。**
