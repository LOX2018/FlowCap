# ADR-035：任务中心归一化 —— 统一任务模型，使采集 / 直播监听 / 定时任务可统一溯源

- **状态**：**已实施（v0.46.53 · 提交 60a2e21）** —— 用户 2026-10-04 拍板方案 A，采集/直播/定时已落同一张 `tasks` 表；D3 经实测**修正**（见下）
- **触发**：用户 2026-10-03 判定「改造统计页面的前提是改造任务中心」：
  > 「直播监听、采集，都是属于任务，溯源其实只需要依照任务来进行溯源就行。
  > 而目前采集还没有实现归一化、直播监听也只实现了单一任务、包括定时任务也没完善，
  > 所以就成了先有鸡还是先有蛋的问题。」
- **前置**：ADR-018（F4 定时任务中心）· ADR-032（总览信息重构）· ADR-033（内容融进采集）·
  ADR-034（采集融进内容总览）· `tasks_history.py`（2026-09-23 主键碰撞修补）
- **解锁**：本 ADR 落地后，「统计页按任务聚合 + 溯源」（ADR-035 的下游）才具备数据基础

---

## 1. 设计意图（本该怎样）

**任务 = 一次有始有终的业务执行**，无论是「采集某关键词的评论」还是「监听某直播间 30 分钟」，
在任务中心里都应是**同一种东西**：

```
一次任务 = 谁(账号) + 做什么(kind) + 对什么(params) + 起止时间 + 状态 + 结果(records快照)
```

据此，上层能力才有统一坐标：

| 能力 | 依赖 |
|---|---|
| **统计** | 对**任务表**聚合（今日几次/多少条/成功率），而非每个业务各造一套统计端点 |
| **溯源** | 打开某条任务的 `records` 快照 → 逐条可查（= 现直播「查阅模式」，应推广到全业务） |
| **任务中心** | 统一列表：直播 / 采集 / 定时 混排，按时间与状态筛选 |
| **并发编排** | 多任务并存（多直播间、多采集）才有统一调度面 |

---

## 2. 观察到的偏离（实测，非推断）

### D1 · 采集**完全不写任务表**

`tasks_history` 的**写入**调用点**只在直播引擎**：

```
core/auto_dm.py:775   from tasks_history import start_task      ← 仅此一处写入
core/auto_dm.py:1153  from tasks_history import finish_task
api/crawl.py          零调用                                     ← 采集不落任务
```

（读取点另有 `api/tasks.py:61` 与 `api/accounts.py:138` 的 `list_history`，
但那只是消费，不改变"采集不产生任务"这一事实。）

采集只写自己的 `crawl_history`（16 行），与 `tasks`（5 行）**两张表、两套语义**。
⇒ 统计页要做"全业务"，只能分别查两张表 —— 这正是 ADR-035 下游统计页卡住的原因。

### D2 · 任务表 schema **为直播量身定做**，采集塞不进去

实测 `tasks` 表列：

```
id · acct · live_id · start_ts · end_ts · status · result_count · config · records · created_at · pid
```

- `live_id` 是**直播专属**字段；采集的维度是 `keyword` / `kind` / `target`，**无处安放**
- 缺 `kind` 列：无法区分"这是直播任务还是采集任务"
- `config` / `records` 虽是 TEXT(JSON)，但语义由直播侧解释，采集复用即为**隐式契约漂移**

### D3 · 直播只支持**单一任务** —— 🔴 **2026-10-04 实测已过时，勿再照此实施**

> **修正**：`services/engine_registry.py`（**ADR-002 §5.2，用户 2026-09-22 拍板**）
> 早已实现「按账号多实例」直播监听，`test_engine_multi_account.py` 23 项测试为证；
> 且**每个 `AutoDM` 实例各自写自己的 `tasks` 行**（`core/auto_dm.py:776`）。
> ⇒ 「同时监听多个直播间」**在引擎层已支持**，本条描述不成立。
>
> **真正的剩余缺口**（本轮已修）：`api/overview.py` 只读 `app.state.adm`
> （最近启动的那**一个**），任务中心因此只显示 1 个直播任务。修法**零新增后端**——
> 改用既有 `GET /api/engine/accounts`（ADR-002 §5.6，本就返回全部账号引擎状态）。
>
> **另发现第 4 个数据源**：`services/live_batch.py`（批量多房间监听，独立管理器，
> 有自身 kv 持久化，**不写 `tasks` 表**）。其是否纳入统一任务表，留待专项。

### D4 · 定时任务**默认休眠**（此项属既有决策，非缺陷）

`services/task_scheduler.py` 出厂 `enabled=False`，`start()` 直接拒绝（fail-closed）。
这是 ADR-018 D1 用户拍板的**风控红线**（定时批量私信敞口最大），**不在本 ADR 推翻范围内**；
本 ADR 只要求：定时任务一旦启用，其每次执行**也应落成一条任务**。

### D5 · 现有 5 条任务**全是空跑记录**（数据面事实）

```
tasks 表 5 行：acct 空、live_id 空、records=[]、result_count=0，status=stopped/finished
```

⇒ 即便打通模型，**直播域目前也无真实明细可溯源**，需跑一次真实监听才有。

---

## 3. 决策：统一任务模型（方案 A，推荐）

### 3.1 表结构演进（**加列，不重建**）

在 `tasks` 表上**新增**通用列（保留 `live_id` 作历史兼容，不再作为语义判据）：

| 新列 | 类型 | 含义 |
|---|---|---|
| `kind` | TEXT | 任务类型：`live` / `crawl` / `scheduled` / `keyword_process` … |
| `params` | TEXT(JSON) | 本类型的入参（直播=`{live_id}`；采集=`{keyword,kind,target,limit}`） |
| `error_code` | TEXT | 失败原因码（对接既有错误码体系，供溯源展示） |
| `updated_at` | REAL | 状态更新时间 |

兼容策略：`kind` 为空 ⇒ 按**既有 `live_id` 非空**判为 `live`（零迁移，老数据可读）。

### 3.2 写入口径归一

| 业务 | 现在 | 改后 |
|---|---|---|
| 直播监听 | `core/auto_dm.py` 直调 `start_task/finish_task` | 同上，但补 `kind=live` + `params={live_id}` |
| 采集 | 只写 `crawl_history` | **每次采集额外落一条 `kind=crawl` 任务**（`crawl_history` 保留作明细流水） |
| 定时任务 | 不落任务 | 每次触发执行落成 `kind=scheduled` 任务，关联被触发的子任务 |

> **`crawl_history` 不删**：它是采集的**结果流水**（每次结果明细），
> 与"任务"是不同粒度（一次任务可产生多条流水）。两者是**任务 → 流水**的一对多关系。

### 3.3 多任务（D3）

- 引擎侧 `_task_history_id` 单值 → 改为 `dict[room_key, task_id]`，支持多直播间并存
- `api/tasks.py` 增加 `GET /current`（复数）返回**任务列表**，保留 `get_current_task` 作兼容
- 前端任务中心从"当前任务"改为"任务列表"

### 3.4 溯源契约（对 downstream 的承诺）

每条任务落 `records` 快照（JSON 数组），**统一行结构**，使统计/查阅模式可复用一套渲染：

```json
{ "ts": "2026-10-03 03:22:41", "target": "73513323440762",
  "nickname": "…", "result": "ok", "reason": "" }
```

⇒ 统计页无需为各域各写明细端点，**打开任务记录即可溯源**（这正是用户要的"依任务溯源"）。

---

## 4. 备选方案（已评估，不推荐）

| 方案 | 说明 | 否决理由 |
|---|---|---|
| **B. 每个业务各建统计/明细端点** | 采集已有 `crawl_history`，再给直播/私信各建一套 | 重复造轮子；统计口径必然漂移（"今日几次"各算各的）；**正是本次要避免的** |
| **C. 先做统计页，任务后补** | 统计页先按现有表拼 | 用户已判定为"鸡生蛋"问题；拼出来的统计**不可溯源**，属假成功 |
| **D. 重建一张新任务表** | 抛弃 `tasks`，新建 `unified_tasks` | 迁移成本高；现有 5 条 + 引擎调用点全改；加列方案可零迁移达成同样目标 |

---

## 5. 完成标准（可判定）

| # | 标准 | 判据 |
|---|---|---|
| A1 | 采集落任务 | 执行一次采集 ⇒ `tasks` 新增一行且 `kind='crawl'`，`params` 含 keyword/target |
| A2 | 直播任务带 kind | 启动监听 ⇒ `kind='live'`，`params.live_id` 非空 |
| A3 | 老数据可读 | 既有 5 行 `kind` 为空时，按 `live_id` 判为 live，任务中心不报错 |
| A4 | 多任务并存 | 并发启动 2 个不同直播任务 ⇒ `tasks` 2 行并存，引擎各自维护 task_id |
| A5 | 溯源可用 | 任取一条任务 ⇒ 可解析 `records` 并按统一行结构渲染 |
| A6 | 定时任务（若启用）落任务 | 触发一次 ⇒ 新增 `kind='scheduled'` 行 |
| A7 | 零回归 | 既有直播任务链路（start/finish/查阅模式）行为不变；`tasks_history` 主键单调性不破 |

**门禁**：新增 `backend/test_unified_task_model.py` 覆盖 A1–A5（A6 因默认休眠用打桩验证）。

---

## 6. 风险与红线

| 风险 | 处置 |
|---|---|
| **并发写入**（多 sidecar 写同一 db） | 沿用 `tasks_history` 既有方案：进程内锁 + 取库内 MAX(id) + IntegrityError 重试 |
| **采集高频**导致任务表膨胀 | 采集任务按**批次**落（一次批量采集 = 一条任务），明细仍在 `crawl_history` |
| **records 快照体积** | 沿用现直播做法（只存结果摘要，不存原始报文）；超限时截断并标注 |
| **风控红线** | 本 ADR **不改动**任何采集/发送的风控口径；定时任务仍保持默认休眠（ADR-018 D1） |
| **统计页** | 本 ADR **不动**统计页；待 A1–A5 达成后，统计改为对任务表聚合（单独 ADR/实施） |

---

## 7. 实施顺序建议

1. **加列 + 兼容读取**（`kind`/`params`/`error_code`/`updated_at`；空 kind 回落 live）
2. **采集落任务**（`api/crawl.py` 调 `start_task/finish_task`，带 `kind=crawl`）
3. **直播补 kind/params**（引擎侧）
4. **多任务**（引擎 dict + 任务列表 API）
5. **门禁测试** A1–A5
6. （后续）统计页改为对任务表聚合 → 闭环用户提出的"依任务溯源"

---

## 8. 待用户拍板

1. **是否采纳方案 A**（加列归一）而非 B/C/D？
2. **采集任务粒度**：按「一次批量采集 = 一条任务」还是「每个作品 = 一条任务」？
3. **是否在本 ADR 一并解决多任务**（D3），还是拆成独立专项？

---

## 9. 实施记录（2026-10-04 · v0.46.53 · 提交 60a2e21）

**拍板**：① 方案 A（加列，零迁移）；② 采集粒度 = **一次批量采集一条任务**；
③ 多任务（D3）**本批一并做**（经实测 D3 实为「任务中心显示全部」，非「重构引擎」）。

**已落地**：

| 层 | 改动 | 文件 |
|---|---|---|
| 表结构 | 加 4 列 `kind/params/error_code/updated_at` + `idx_tasks_kind`（幂等 ADD COLUMN，零迁移） | `database.py` |
| 任务模型 | `start_task` 加 `kind/params`（默认 `live` ⇒ 直播调用点零改动）；`finish_task` 加 `error_code/updated_at`；`_normalize_task` 统一解析 + 老数据 kind 回落 | `tasks_history.py` |
| 采集落任务 | `_save_history` 额外落 `kind='crawl'` 一行（`crawl_history` 仍作结果流水） | `api/crawl.py` |
| 定时落任务 | `_sched_record`，`_run_one` 每次执行落 `kind='scheduled'`（四态各记 error_code） | `services/task_scheduler.py` |
| 错误码 | 新增 `SCHED-013` / `CRAWL-009`（各带六段契约） | `errcode_data.py` |
| 前端 | 运行任务读 `/api/engine/accounts`（多账号全部直播）；采集并入运行任务表 | `tasks-page.tsx` |
| 门禁 | `test_unified_task_model.py`（A1/A2/A3/A5/A6 + 迁移自证 + 端到端，含负控） | `backend/` |

**验收**：`check_contracts` 16/16 · `check_iron_rules` 20/20 · `check_version_sync` 六源
0.46.53 · `test_unified_task_model` 8/8 · `test_id_uniqueness` 9/9 ·
`test_engine_multi_account` 23/23 · `test_task_scheduler_gates` 10/10 · SSR 冒烟 15/15（含负控变红）。

**未做（诚实边界）**：
- `services/live_batch.py`（批量监听）**未**纳入统一任务表 —— 留待专项。
- 统计页改为「对任务表聚合」仍**未做**（本 ADR 的下游，需单独实施）。
- A6 定时任务默认休眠（ADR-018 D1），落任务仅在其被启用后生效。
