# A 线修复报告 · 核查卷宗

- 待审报告：`artifacts/fix_20260923/A_id_and_p2.md`（367 行）
- 仓库：`C:\Users\LOX\Desktop\DYchajian`，分支 `design/better-douyin`（`git branch --show-current` 实测 = design/better-douyin）
- HEAD：`b455192`（`git log --oneline -1` 实测 = `b455192 docs(ledger): 并发写者 UP 待办清单(T1~T5) 并入 SSOT 台账 + 合并视图`）
- 版本：`DYAutoDM_v2/package.json:3 = "0.44.53"`（实测）
- 核查方式：**只读**（`read_file` / `grep` / `git status` / `git diff` / `stat`）。未 git add/commit/checkout/stash/clean，未改仓库文件，未跑测试。
- 唯一写入：本文件。

---

## 1.【自述改动文件清单】

报告第 288–303 行的「改动文件清单」表格声称改了 **8 个文件**，并单列 **2 个新增测试文件**，合计 **10 个文件**。

| # | 文件 | 报告自称变更 | 仓库实测 |
|---|---|---|---|
| 1 | `backend/api/live_rooms.py` | P0-2a（房间 id + 迁移 id）、P2-7、P2-8、P4 | 存在且已改（`git status` 显示 ` M`） |
| 2 | `backend/api/live_config.py` | P0-2a（策略 id）、P2-10 | 存在且已改（` M`） |
| 3 | `backend/tasks_history.py` | P0-2b | 存在且已改（` M`） |
| 4 | `backend/services/verdicts.py` | P2-4、P2-5（接线） | 存在且已改（` M`） |
| 5 | `backend/services/app_config_schema.py` | P2-9 | 存在且已改（` M`） |
| 6 | `backend/kernel/truth.py` | P2-5（+`UID_MIN_DIGITS`） | 存在且已改（` M`） |
| 7 | `backend/kernel/__init__.py` | **新增**（P2-5 真包） | 存在（`git status` 显示 `??` 未跟踪新文件），mtime 2026-09-23 21:07 |
| 8 | `backend/test_live_rooms.py` | 既有 2 条用例改为「先建策略再绑定」 | 存在且已改（` M`） |
| 9 | `backend/test_id_uniqueness.py` | **新增**（自称 9 条） | 存在（`??` 未跟踪），mtime 21:13 |
| 10 | `backend/test_p2_live_guards.py` | **新增**（自称 26 条） | 存在（`??` 未跟踪），mtime 21:15 |

**报告自称「未触碰」（守范围）清单**（第 303 行，照抄）：
> `probe.py`、`dm_dispatch.py`、`dispatch.py`、`recv_daemon.py`、`client_im.py`、`api/messages.py`、`api/engine.py`、`api/platform.py`、`api/notify.py`、`engine_registry.py`、`replay/*`、`scripts/*`、前端、版本源文件、`工作记忆/`、`artifacts/UP_*`。未执行 `git add/commit/checkout/stash/clean`。

> ⚠️ 注：上述「未触碰」清单中多个文件在 `git status` 中确实为 ` M`（如 `api/messages.py`、`recv_daemon.py`、`api/engine.py`、`api/notify.py`、`api/platform.py`、`dispatch.py`、`services/dm_dispatch.py`、`services/probe.py`、前端 `client.ts` 等）——**但这些改动应归其它 6 条并行修复线**，非 A 线。A 线只对 1/2/8 三个源文件负责；该清单是「本次 A 线未触碰」的边界声明，不代表全仓未改。

---

## 2.【自述测试数字】

报告第 307–349 行的「完整测试输出尾部（实跑）」列出以下计数（均已从报告原文抄录，标注行号）：

| # | 模块组合 | `Ran N` | 结果 | 行号 | 报告是否解释为刻意失败/负控/无关 |
|---|---|---|---|---|---|
| 1 | `test_live_rooms test_id_uniqueness test_p2_live_guards` | **57** | `OK` | 315–317 | 否（主证据，全绿） |
| 2 | `test_live_rooms test_app_config test_no_dup_dict_keys test_id_uniqueness test_p2_live_guards` | **84** | `OK` | 324–325 | 否 |
| 3 | `test_live_config_guards test_config_tag test_settings_api test_b4_global_switches test_task_delay_sentinel test_engine_idle_guard test_cross_account_sink` | **65** | `OK` | 328–329 | 否 |
| 4 | `test_live_anon_decouple test_b3_capture_probe test_replay_conversation_read test_dm_dispatch_config` | **43** | `OK` | 332–333 | 否 |
| 5 | `test_upstream_p1 test_upstream_p3 test_upstream_p4 test_upstream_p5 test_live_ai_wiring test_live_anon_decouple` | **151** | `OK` | 336–337 | 否 |
| 6 | `test_live_identity_verdict` | **19** | `OK` | 340–341 | 否 |
| 7 | `test_capability_probe test_upstream_p1 test_upstream_p3 test_upstream_p4 test_upstream_p5 test_live_ai_wiring` | **182** | `FAILED (errors=1)` | 344–348 | **是**（见下） |

**报告自身对第 7 项唯一失败的定性**（第 345–348 行，照抄原句）：
> `# 唯一 error = 我拼错模块名 test_live_anonym_decouple（不存在），`
> `# 已用正确名 test_live_anon_decouple 重跑（见 tests_regress8.txt，151 OK）。`
> `# probe 侧 176 条全过（含 test_numeric_nickname_is_degraded_or_failed），`
> `# 证明 P2-4 判据变更未回归。`

即：报告把该 `FAILED (errors=1)` 解释为 **runner 侧模块名拼写错误（人为调用失误），非代码缺陷**，并以正确模块名重跑（第 5 项 151 OK）证明无回归。

**另有一段「独立环境提示（非我的缺陷）」（第 351–355 行），报告将其定性为并发共享目录争用，非本批缺陷（照抄）：**
> `**独立环境提示（非我的缺陷）**：test_capability_probe 后续单独重跑时，因其隔离库`
> `%TEMP%\dyautodm_cfgtest_root 被**另一个 agent 的进程**占用，出现`
> `[db] conv_type 回填失败: database is locked 与 30s busy-wait（单测整体卡住超时）。`
> `这是**并发跑测试的共享目录争用**，与本批代码无关；`

**「负控/刻意失败态样本」的分布**（均**内联在测试用例内**，不是上表的 `Ran N`/`FAILED` 计数，而是测试方法名）：
- `test_id_uniqueness.py`：`TestNegativeControls.test_legacy_room_id_formula_collides`（第 183 行，断言旧公式必碰撞）、`TestNegativeControls.test_legacy_task_pk_formula_collides`（第 194 行，断言旧公式必抛 `IntegrityError`）——共 **2 条负控**。
- `test_p2_live_guards.py`：`TestVerdictSingleSource.test_negative_control_old_two_rules_disagreed`（第 87 行）、`TestExplicitClear.test_negative_control_old_expression_kept_old_value`（第 179 行），及 `TestTimeoutSchemaBounds` 内的越界值巡检——报告自称 **3 条负控**（第 301 行）。
- 报告第 72 行：「含负控 `TestNegativeControls.test_legacy_room_id_formula_collides` 内联旧公式断言必碰撞」；第 107 行：「负控 `test_legacy_task_pk_formula_collides`」。

**无任何 `PASS` 字样计数**（报告用 unittest 默认输出，只有 `OK` / `Ran N`；未出现 pytest 风格逐条 PASS 计数）。

**测试数字合计**（本卷宗自算）：`Ran N` 之和 = 57+84+65+43+151+19+182 = **601**（7 次运行）。

---

## 3.【唯一标识符】（报告声称新增的名称）

### 3.1 新增函数 / 方法 / 常量
| 标识符 | 所属文件（报告自述） | 报告出处行 |
|---|---|---|
| `_id_lock`（模块级锁） | `api/live_rooms.py`、`api/live_config.py` | 32, 34 |
| `_last_id_ms` | `api/live_rooms.py`、`api/live_config.py` | 32 |
| `_next_id_ms()` | `api/live_rooms.py` | 32 |
| `new_room_id(existing=None)`（改签名） | `api/live_rooms.py` | 32 |
| `new_strategy_id(existing=None)`（改签名） | `api/live_config.py` | 34 |
| `_task_id_lock` | `tasks_history.py` | 86 |
| `_last_task_id` | `tasks_history.py` | 86 |
| `_next_task_id(conn)` | `tasks_history.py` | 86 |
| `_strategy_exists(sid)` | `api/live_rooms.py` | 139 |
| `is_placeholder(value, peer_id, min_digits=UID_MIN_DIGITS, min_len=1)` | `services/verdicts.py` | 179 |
| `UID_MIN_DIGITS` | `kernel/truth.py` | 179, 206, 295 |
| `_FIELDS`（改为真消费） | `api/live_rooms.py` | 269–280 |

### 3.2 新测试文件名
- `backend/test_id_uniqueness.py`（自称 9 条）
- `backend/test_p2_live_guards.py`（自称 26 条）

### 3.3 报告声称的新测试类 / 用例名
`test_id_uniqueness.py`：`TestRoomIdUniqueness`、`TestStrategyIdUniqueness`、`TestTaskIdUniqueness`、`TestNegativeControls.test_legacy_room_id_formula_collides`、`test_legacy_task_pk_formula_collides`。
`test_p2_live_guards.py`：`TestVerdictSingleSource`、`TestKernelTruthWired`、`TestExplicitClear`、`TestReferentialIntegrity`、`TestDeleteStrategyOrdering`、`TestTimeoutSchemaBounds`、`TestWhitelistIsRealGate`。

### 3.4 报告声称的新断言串 / 响应串（用于核实文案）
- `"直播策略 ... 不存在（禁止写入悬空引用）"`（P2-8，第 139/142 行）
- `"解绑引用房间失败，已中止删除以避免悬空引用: ..."`（P2-10，第 156/162 行）
- `NICKNAME_SOURCE = "indexeddb:<uid>_user"`、`NICKNAME_SOURCES_ORDER = ("indexeddb", "dom", "active_batch")`（第 218–219 行）

---

## 4.【诚实标注】（逐条抄录报告的「未验证/未做/pending/风险」条目）

### 4.1 各小节 ⑤ 诚实标注（照抄要点）
- **P0-2a（第 75 行）**：「跨进程查重是**对目标 dict 快照**查重（`new_room_id(existing)`），非数据库级原子；两个 sidecar 在同一毫秒对**同一旧快照**取号仍可能撞……未做 DB 级唯一约束（kv 无此能力），故此项标注为**部分保证**。」
- **P0-2b（第 110 行）**：「跨进程靠 `MAX(id)+1` 兜底，属**读后写**，非原子；两个 sidecar 在同一毫秒都读到同一 `MAX(id)` 时仍可能撞，此时靠 `IntegrityError` 重试一次兜底；若重试仍撞则**显式抛出**……调用方 `auto_dm` 仍会吞成 ENG-003 警告，这一层不在我文件内。」
- **P2-7（第 129 行）**：「**未验证真实前端**……真实浏览器渲染留待前端侧验证（不在本批）。」
- **P2-8（第 145 行）**：「引用完整性现在是**双向**维护……竞态窗口：策略在「校验通过」与「落库」之间被另一进程删除，仍可能写入悬空引用 —— 未做事务化，如实标注。」
- **P2-10（第 169 行）**：「响应新增 `ok=False` 分支。前端是否已有「非 ok 也提示」的处理未逐行核验……已如实保留 `deleted` 字段为空串以示「未删除」。」
- **P2-4（第 192 行）**：「语义发生了变化：**纯数字但长度 < 6 的值（如 '12345'）现在判「真实」**……**对下游的影响未逐点评估**（如 `probe` 的 nickname_ratio 会因短数字昵称不再算占位而变化）；已知 `test_capability_probe` 全绿，故未见回归。」
- **P2-5（第 224–225 行）**：「现在 `NICKNAME_SOURCE` / `NICKNAME_SOURCES_ORDER` 的**唯一真消费者是文档**……**两个字符串常量本身仍未被代码逻辑读取**……如实登记为**部分接线**。」「已知残留不一致（不在我文件内，如实登记）：`backend/errcode_data.py:83` 的 `CAP.intent` 仍写「昵称/头像唯一来源 = BCC 被动 hook 截前端自发 im/user/info」，与 `NICKNAME_SOURCE` 的现行事实相反 —— 属**父会话/其它文件**的活口。」
- **P2-6（第 240 行）**：「我只做了静态确认（grep），**未运行** recv_daemon 验证其行为，因为那会启动浏览器/WS 达人对端点（超出本批、且文件不归我）。」
- **P2-9（第 265 行）**：「「**消费端未全接线**」这部分我**未改**……消费端是否真读这些键、是否仍 hardcode，未逐点核验 —— **另批**。」
- **P4（第 282 行）**：「`_FIELDS` 仍与 `_normalize_room` 的 `setdefault` 集合**在语义上重叠**……未合并成单一数据结构 —— 合并属重构，未做。」

### 4.2 「诚实总结（未验证部分）」（第 359–367 行，7 条，逐条照抄）
1. **P0-2a 跨进程**：仅对目标 dict 快照查重，非 DB 级原子；多 sidecar 同毫秒对同一旧快照取号仍可能撞（与修复前同风险，未加 DB 约束）。同一进程内串行取号已彻底修好。
2. **P0-2b 跨进程**：`MAX(id)+1` 读后写非原子，靠一次 `IntegrityError` 重试兜底；调用方 `core/auto_dm.py` 仍会把最终异常吞成 `ENG-003` 警告（该文件不归我）。
3. **P2-10 响应形状变化**：新增 `ok=False` 分支；前端对非 ok 呈现未逐行核验。
4. **P2-5**：`NICKNAME_SOURCE` / `NICKNAME_SOURCES_ORDER` 的代码消费者仍无（只有文档按名引用）；代码消费链只到 `UID_MIN_DIGITS`。`errcode_data.py:83` 仍与现行事实相反（不归我）。
5. **P2-9**：消费端接线未核验、未改（另批）。
6. **P2-6**：仅静态登记，未跑 recv_daemon。
7. **真实 UI/浏览器**：本批全部为单测 + 冻结时钟脚本，**未做真机渲染/端到端**验证（不在本批范围）。

---

## 5.【交叉判据】（grep/rg 实测）

命令统一在 `C:/Users/LOX/Desktop/DYchajian/DYAutoDM_v2/backend` 下执行（bash/MSYS）。

| # | 待核实标识符 | 命令 | 命中数 | 结论 |
|---|---|---|---|---|
| 1 | `kernel/truth.py` 的 `UID_MIN_DIGITS` | `grep -n "UID_MIN_DIGITS" kernel/truth.py` | **1**（= `39:UID_MIN_DIGITS = 6`） | **真实存在** |
| 2 | `backend/kernel/__init__.py` 是否存在 | `ls -la kernel/__init__.py` | 命中（502 字节，mtime 2026-09-23 21:07） | **真实存在** |
| 3 | `backend/test_id_uniqueness.py` | `ls -la test_id_uniqueness.py` | 命中（9672 字节，mtime 21:13） | **真实存在** |
| 4 | `backend/test_p2_live_guards.py` | `ls -la test_p2_live_guards.py` | 命中（15653 字节，mtime 21:15） | **真实存在** |
| 5 | `verdicts.py` 真消费 `UID_MIN_DIGITS` | `grep -nE "from kernel.truth import\|UID_MIN_DIGITS" services/verdicts.py` | **3**（`30:from kernel.truth import UID_MIN_DIGITS`、13、22） | **真实存在**（消费链成立） |
| 6 | `is_placeholder` / `is_placeholder_name` / `is_uid_placeholder` | `grep -nE "def is_placeholder" services/verdicts.py` | **3**（40、66、78 行） | **真实存在** |
| 7 | `api/live_rooms.py` 的 `_id_lock`/`_last_id_ms`/`_next_id_ms`/`new_room_id`/`_FIELDS`/`k in _FIELDS`/`_strategy_exists` | `grep -cF`（逐项） | 2 / 4 / 2 / 1 / 5 / 2 / 2 | **全部真实存在** |
| 8 | `api/live_config.py` 的 `new_strategy_id` | `grep -cF "new_strategy_id" api/live_config.py` | **3**（def @132 + 引用） | **真实存在** |
| 9 | `tasks_history.py` 的 `_task_id_lock`/`_last_task_id`/`_next_task_id`/`IntegrityError`/`MAX(id)` | `grep -cF`（逐项） | 2 / 4 / 3 / 2 / 2 | **全部真实存在** |
| 10 | `_strategy_exists` 拒绝文案 | `grep -n "不存在（禁止写入悬空引用）" api/live_rooms.py` | **1**（`375`） | **真实存在** |
| 11 | P2-10 中止文案 | `grep -n "解绑引用房间失败" api/live_config.py` | **1**（`251`） | **真实存在** |
| 12 | `model_fields_set` 判据（P2-7） | `grep -n "model_fields_set" api/live_rooms.py` | **2**（346、357） | **真实存在** |
| 13 | `test_id_uniqueness.py` 自称 9 条 | `grep -cE "def test" test_id_uniqueness.py` | **9** | **一致** |
| 14 | `test_p2_live_guards.py` 自称 26 条 | `grep -cE "def test" test_p2_live_guards.py` | **26** | **一致** |
| 15 | 主证据 `Ran 57` 的构成（test_live_rooms 应 = 57−9−26） | `grep -cE "def test" test_live_rooms.py` | **22**（22+9+26=57） | **自洽** |
| 16 | P2-9 三个 timeout 边界 | `grep -nE "timeout_bcc_http\|timeout_fast_probe\|timeout_http_req" services/app_config_schema.py` | 3 处（63/70/77）；含 `min/max`（65/72/79） | **真实存在** |
| 17 | P2-5 文档指向 `kernel/truth.py` | `grep -rn "kernel/truth.py" docs/architecture.md docs/design-contracts/C-01-im-capture.md` | 3 处（architecture.md:8,11,166；C-01:3,21） | **真实存在**（≥3 处，与自述一致） |
| 18 | P2-4 消费方 | `grep -rn "is_placeholder_name\|is_uid_placeholder" services/probe.py api/messages.py services/nickname_fallback.py` | 命中（probe:168-169、messages:24/342/368、nickname_fallback:139/150/152-153/249） | **真实存在** |
| 19 | `errcode_data.py:83` 残留 | `grep -n "im/user/info" errcode_data.py` | **1**（`83`，与自述一致） | **真实存在**（自述属实） |
| 20 | 既有契约守卫 `TestDeleteStrategyUnbinds` | `grep -c "class TestDeleteStrategyUnbinds" test_live_rooms.py` | **1**（`114`；含 2 用例 130/143） | **真实存在** |
| 21 | **P2-6 自述「0 命中（未接线）」** | `grep -c "is_placeholder" daemon/recv_daemon.py` | **2**（`540` `from services.verdicts import is_placeholder_name as _is_ph`、`535` 注释） | **❌ 与自述矛盾**（详见第 6 节） |
| 22 | P2-6 自述「`:376 OR peer_name=peer_id`」 | `grep -n "peer_name=peer_id" daemon/recv_daemon.py` | **1**（`376`） | **真实存在**（该行属实） |

**汇总：第 3 节列出的 A 线新增函数名/常量/新测试文件名/新断言串，除 P2-6 的「未接线」缺失声明外，全部在仓库中真实存在；无「缺失」标识符。**

---

## 6.【疑点】（自述与仓库实际不符处）

1. **🔴 P2-6「未接线」结论与仓库当前实际矛盾（唯一实质不符）**
   - 报告第 235 行断言：「实际第 5 处（recv_daemon）仍是内联 SQL/比较，**未接线到 `services.verdicts`**」；第 239 行给出证据：`grep -n "is_placeholder\|is_uid_placeholder" daemon/recv_daemon.py` → **0 命中**（未接线）。
   - 仓库实测：同一 grep 命中 **2**（`545?: from services.verdicts import is_placeholder_name as _is_ph`，及第 535 行注释「P2-6（审计 2026-09-23）：本处昵称占位判据原为内联」）。
   - **时间线**：`daemon/recv_daemon.py` mtime = **21:11:51**；A 报告 mtime = **21:33:48**。即报告落盘时该文件**已被接线**（应能 grep 到命中），但报告仍写「0 命中」。
   - **结论**：P2-6 属「不归我、仅登记」，这**不否定 A 线自身改动**，但该条登记结论与仓库实际相反，属**过期/失真的登记**，应作废或由父会话回写为「已由 recv_daemon 归属方接线（`from services.verdicts import is_placeholder_name`）」。grep 同时显示 `:376 OR peer_name=peer_id` 仍为内联 SQL（部分未收敛），故真实状态是**部分接线**，报告写的「0 命中即完全未接线」不成立。

2. **P2-9 schema 行号偏移（轻微）**：报告第 246 行称旧 min/max 缺失位于 `app_config_schema.py:55 / :61 / :67`；仓库实测现行三个键在 `63 / 70 / 77`（修补注释块 55–62 占位，故原始行号已被注释挤后移）。属行号漂移，不影响事实成立（现声明均含 min/max）。

3. **「未触碰」清单易被误读**：第 303 行列 16 项「未触碰」，但其中 `recv_daemon.py`、`api/messages.py`、`api/engine.py`、`api/notify.py`、`api/platform.py`、`dispatch.py`、`services/probe.py`、`services/dm_dispatch.py`、前端等在当前 `git status` 中均为 ` M`。A 线只声明「**本线**未触碰」无误，但这些改动来自并行兄弟线，卷宗需标注以免父会话误判为「全仓只有 A 线改了这些」。

4. **无法在本卷宗内复核的项（非矛盾，属证据边界）**：报告引用的实测文本 `%LOCALAPPDATA%\Temp\fixA\reproA_BEFORE.txt` / `reproA_AFTER.txt` / `final_run.txt` / `tests_regress*.txt` 均在 A 线自建隔离目录（`%TEMP%\fixA`）与未入库的临时产物中，**本卷宗只读仓库、未复跑测试**，故上述 `Ran N/OK` 数字**未被独立复现**，仅核对了与被测对象（测试文件、函数、断言串）的一致性。

---

## 核查结论

A 线自述的 10 个文件、全部新增标识符（`_id_lock`/`_last_id_ms`/`_next_id_ms`/`new_room_id`/`new_strategy_id`/`_task_id_lock`/`_last_task_id`/`_next_task_id`/`_strategy_exists`/`is_placeholder`/`UID_MIN_DIGITS`/`kernel/__init__.py`/两个新测试文件、9+26 条用例）、两条新响应文案、三处 timeout 边界、文档 3 处指向 `kernel/truth.py`，**均已 grep 到实存，无缺失**。测试方法计数自洽（22+9+26=57）。唯一实质疑点为 **P2-6 登记失真（登记为「未接线/0 命中」，仓库实为已接线 2 命中）**，该条不属 A 线改动范围；`Ran N` 数字本身未独立复现（只读约束）。

```json
{"line":"A","files_claimed":10,"tests_total":601,"identifiers_ok":true,"open_risks":7,"verdict":"A线10个文件与全部新增标识符经grep实存、无缺失，测试计数自洽，唯一实质疑点是P2-6登记为未接线但仓库显示已接线（该条不归A线且为其自身登记）；Ran N数字未独立复现（只读约束）。"}
```
