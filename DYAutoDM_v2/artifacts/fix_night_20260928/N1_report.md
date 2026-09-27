# N1 卷宗 · ADR-018 F4 定时任务中心三处运行时缺陷修复

- 任务来源：2026-09-28 夜间无人值守批次 · 并行施工子任务 N1
- 仓库根：`C:/Users/LOX/Desktop/DYchajian`　分支：`design/better-douyin`（未切换）
- 版本：**未动**（v0.45.57，由父会话统一升）
- 施工时间：2026-09-28（Asia/Chongqing）
- 唯一可写文件：`DYAutoDM_v2/backend/services/task_scheduler.py`、`DYAutoDM_v2/backend/test_task_scheduler_gates.py`

---

## 1 · 自述改动清单

### 1.1 `services/task_scheduler.py`（+78 / -9，共 637 行）

| # | 位置 | 缺陷 | 改动 | 说明 |
|---|---|---|---|---|
| A | `run_task_now`（L478~521） | **TS-5 零锁 + TS-4 绕过重入保护** | 复用模块既有 `_lock` / `_running`，在锁内完成「判重入 → 置位 → 执行 → 状态写回 → 复位」全段 | **未自造新锁**；被重入时 fail-closed 返回 `{"ok": False, "rejected": True, "busy": True, "error": ...}`，不排队、不等待、不静默执行 |
| B | `_tick` 内任务写回（L347~362） | **MC-2** last_run_at 写的是本轮扫描**开始时刻** `now` | 改为 `finished = time.time()`（每个任务**跑完后**取当前时刻）再写回 | 语义 = 「上次执行**结束**时刻」；下轮 due 判定自然推后到「结束 + interval」，长任务不再被连续重复调度 |
| C | `run_task_now` 写回（L505~512） | **MC-2 + TS-5** | `finished = time.time()`；`last_run_at/last_result/run_count/fail_count` 四字段**同一临界区**内写 | 计数不再因并发互相覆盖 |
| D | `_tick` 的 `finally`（L387~391） | **TS-4 的另一半（父会话清单未列，本次实测发现）** | 新增 `entered` 标志：只有**真正抢到** `_running` 的一方才复位 | 🔴 见下方「疑点」—— 这是**超出派工范围**但必须修的缺口 |
| E | `run_task_now` 异常兜底 | 原形态 `_run_one` 抛异常会**直接冒泡**给调用方（api/tasks.py:423），且 `_running` 由谁复位无保障 | 补 try/except/finally：异常记 `SCHED-006` + fail_count，finally 复位 `_running` | 与 `_tick` 的「异常不打断循环」范式对齐 |

**未改动（行为保持）**：`TASK_SCHEDULER_ENABLED` / `AUTO_SEND_ENABLED` 默认 False、`start()` fail-closed、三重闸门语义、`Task.enabled` 默认 False、`register_builtin_handlers` 投递三态语义 —— 均未触碰。

### 1.2 `test_task_scheduler_gates.py`（+319 / -0）

| # | 内容 |
|---|---|
| B8 | 重入保护：真起第二线程，`Event` 卡住 handler 制造真实重入窗口；断言第二次 `ok=False` 且 handler **只命中 1 次**（计数器证明） |
| B9 | MC-2：走 **`_tick`**（病灶所在，见表下注），耗时 1.2s / interval 0.3s；断言 `last_run_at - 开始 >= 1.2s`、紧接下轮 `due=False`、`run_count=1` |
| B10 | 状态更新在锁内：12 线程 `Barrier` 同时发起；断言 `run_count == 真执行次数`、`真执行+被拒 == 12`、`_running` 复位、`fail_count=0`、**执行区间无重叠** |
| 辅助 | `_reset_scheduler_state()`（清模块级 `_tasks/_handlers/_running`，防用例间污染造成假失败）；`import threading/time/unittest` |
| 负控 | 新增 3 组：`B8/B10` 去掉重入保护、`B10b` TOCTOU（锁内查锁外设）、`B9` 改回 `last_run_at = now` |
| 桥接 | **新增 unittest 桥接**（见 §4 诚实标注第 1 条）：每条 B 判据 → 一个 TestCase，并加 `test_selftest` 元门禁 |

> B9 注：判据**刻意走 `_tick` 而非 `run_task_now`**。`_tick` 写的是**循环开始时刻** `now`（真病灶）；`run_task_now` 旧形态用的是 `_run_one` 之后的 `time.time()`，反而**接近**完成时刻。只测 `run_task_now` 会「测了噪声、漏了病灶」。

---

## 2 · 自述测试数字

**唯一执行过的命令**（未跑全量、未跑 discover）：
```
cd C:/Users/LOX/Desktop/DYchajian/DYAutoDM_v2/backend && python -m unittest test_task_scheduler_gates -v
```

| 项目 | 实测数字 |
|---|---|
| unittest 用例数 | **Ran 11 tests / OK**（B1~B10 各 1 + `test_selftest` 元门禁 1） |
| 门禁判据条数 | **10 条（B1~B10），通过 10 / 未通过 0** |
| 负控用例总数 | **7 组**（既有 B3/B4、B3/B4b、B6、B7 共 4 组 + 新增 B8/B10、B10b、B9 共 3 组） |
| 负控**变红**条数 | **7/7 全部如期报红**（新增 3 组：`B8/B10`→`['B10','B8']`、`B10b`→`['B10','B8']`、`B9`→`['B9']`） |
| 还原后复绿 | **exit = 0**（还原后复跑 B1~B10 = 10/10 全绿） |

**负控读数明细（新增 3 组）**

| 负控 | 注入形态 | 判据读数 |
|---|---|---|
| `B8/B10` | `run_task_now` 去掉重入保护 | B8：`第二次 ok=True，handler 命中 2 次` ❌ 红；B10：`真执行 12 + 被拒 0，区间重叠=True` ❌ 红 |
| `B10b` | 判重入与置位分离（TOCTOU） | B8 ❌ 红；B10：`区间重叠=True` ❌ 红 |
| `B9` | `_tick` 的 `last_run_at` 改回 `now` | B9：`gap=0.00s ≥ 耗时 1.2s ⇒ False` ❌ 红 |

**源码还原确认**：负控装置在 `finally` 中**无条件回写原文**并 `importlib.reload`。收工后核验 `grep -c "注入缺陷" task_scheduler.py` = **0**；`git diff --stat` 仅 2 个文件（即 §1 的两个可写文件），其余零改动。

---

## 3 · 唯一标识符

| 类别 | 值 |
|---|---|
| 缺陷编号 | **TS-4**（`_tick` 重入保护被 `run_task_now` 绕过）、**TS-5**（`run_task_now` 零锁）、**MC-2**（`last_run_at` 记开始时刻） |
| 关联 | ADR-018 F4；2026-09-27 全库审计报告 `artifacts/audit_2026-09-27/AUDIT_2026-09-27_全库审计报告.md` |
| 新增判据 | **B8 / B9 / B10**（沿用既有 B1~B7 编号序列） |
| 新增负控 tag | **`B8/B10`**、**`B10b`**、**`B9`** |
| 新增符号 | `finished`（局部变量，×2 处：`_tick` / `run_task_now`）、`entered`（`_tick` 局部标志）、`run_task_now` 返回值新键 `rejected` / `busy` |
| 新增测试符号 | `b8_reentrancy_fail_closed`、`b9_last_run_at_is_finish_time`、`b10_state_update_no_race`、`_reset_scheduler_state`、`GateTestCase`、`SelfTestNegativeControl.test_selftest` |
| 错误码 | 复用 `SCHED-006`（`run_task_now` 异常兜底），**未新增错误码** |
| 卷宗路径 | `C:/Users/LOX/Desktop/DYchajian/DYAutoDM_v2/artifacts/fix_night_20260928/N1_report.md` |

---

## 4 · 诚实标注

1. **我改了一个父会话没列的点（D 项），这是越界吗 —— 我认为不是，但请父会话复核。** 派工清单只点了 TS-4「`_tick` 的 `_running` 保护本身正确，是 `run_task_now` 绕过它」。但实测发现 **`_tick` 自身也有缺口**：它的 `finally` 无条件 `_running = False`，当 `_tick` 因重入**提前 return** 时（自己从未置位），却会把 `run_task_now` 正在持有的标志**抹除** ⇒ 第三个调用者长驱直入。不修 D，A 项的保护就是**可被外部撤销的**。我按「fail-closed、最小改动」修了（加 `entered` 标志，只复位自己置的）。若父会话认为越界，此改动独立、`git` 可精确回滚。
2. **`test_task_scheduler_gates.py` 原本跑 `python -m unittest` 是 `Ran 0 tests`（空跑全绿）。** 它是脚本式门禁（`__main__` → `run()`），unittest 收集不到任何用例 —— 这是本项目最危险的假绿形态：**命令没报错、exit 0、但一条都没测**。派工指定的命令因此**在我动手前就已经是「绿灯」**。我加了 unittest 桥接（每条 B → 一个 TestCase，外加 `test_selftest` 元门禁）才让这条命令真正有 11 个用例。这一点必须让父会话知道：**派工命令在原文件状态下不具鉴别力。**
3. **B4 我改了判据（非被测代码）。** 原 B4 不锁定时段，只有 09:00~21:00 之间跑才会走到额度闸门；本次在凌晨 2 点跑 ⇒ 被时段闸门提前挡掉 ⇒ **`calls=[]`，B4 恒红**。这是**判据依赖墙上时钟**的假信号（白天绿、夜里红），与被测代码正确性无关。我在 B4 内临时放开 `ACTIVE_HOURS=(0,24)`（finally 还原），使其任何时刻都能命中它声称要测的闸门。**被测代码未因此改动。**
4. **环境处置（需父会话知悉）**：本机 `python`（3.11.9）**缺 `loguru`**，导致 B1~B10 全部以 `ModuleNotFoundError` 报红 —— 这是**环境问题，非代码缺陷**，`loguru` 在 `backend/requirements.txt` 内。我执行了 `python -m pip install loguru`（装了 loguru 0.7.3 + colorama + win32-setctime 三个包）才跑通。**这改动了本机 Python 环境，未改仓库任何文件**，若环境需保持原样请告知。
5. **未做的取证**：派工说明「父会话已实测，不要重复取证」，故我**未**独立复现修复前的缺陷现场，也未跑除指定命令外的任何测试（含全量/discover）**。
6. **B10 的 `allowed` 实测为 1**（12 次并发只有 1 次真执行、11 次被拒）。这是**符合预期**的：`_running` 是**模块级**全局标志（既有设计），同一时刻只允许一个任务在跑。判据断言的是「记账与真执行次数一致 + 无区间重叠」，**不**断言「12 次都执行」。若业务期望「同一任务的手动触发可排队/可并发」，那是**另一项设计决策**，本次未做（保持既有 fail-closed 语义）。
7. **未验证真机/集成**：未启动应用、未发任何网络请求、未碰真实数据根（测试用临时 `DY_APP_ROOT`）。

---

## 5 · 交叉判据（证明新增符号真实存在，非自述）

命令均在 `C:/Users/LOX/Desktop/DYchajian/DYAutoDM_v2/backend/services` 下执行（Python 计数，规避 shell 转义）：

```python
s = open('task_scheduler.py', encoding='utf-8').read()
```

| 模式 | 命中数 | 含义 |
|---|---|---|
| `finished = time.time()` | **2** | `_tick`（L355）与 `run_task_now`（L505）各一处 —— 两处 last_run_at 均已改「完成时刻」 |
| `if _running:` | **3** | L332（注释内）、L339（`_tick` 真实守卫）、L493（`run_task_now` 新增守卫）⇒ **2 处真实代码守卫** |
| `entered` | **4** | `_tick` 的 `entered=False` / `entered=True` / `if entered:` 及注释 |
| `"rejected": True` | **1** | `run_task_now` fail-closed 返回键 |
| `last_run_at = now` | **1** | ⚠️ 唯一命中在 **L357 注释行**（「旧写法 `t.last_run_at = now`…」）⇒ **代码中已无开始时刻写回**，已逐行核验 |
| `t.last_run_at` | **5** | 全部为完成时刻语义或注释/读取 |

逐行核验（`last_run_at = now` / `if _running:` 的命中行）：
```
332  #   （`if _running: return`）**提前返回**时，它自己**从未置位**却仍会   ← 注释
339  if _running:                                                          ← _tick 真实守卫
357  # 旧写法 `t.last_run_at = now` 记的是**本轮扫描的开始时刻**：           ← 注释
493  if _running:                                                          ← run_task_now 新增守卫
```

测试侧（`test_task_scheduler_gates.py`）：
```
CHECKS 含 10 项：B1 B2 B3 B4 B5 B6 B7 B8 B9 B10
unittest 实测：Ran 11 tests in 21.767s / OK
```

还原核验：
```
grep -c "注入缺陷" services/task_scheduler.py  →  0
git diff --stat → 仅 2 文件：services/task_scheduler.py (+78/-9)、test_task_scheduler_gates.py (+319/-0)
```

---

## 6 · 疑点

1. **`_running` 是模块级全局标志，粒度是「整个调度中心」而非「单个任务」。** 这意味着：任务 A 在跑时，手动触发**任务 B** 也会被拒（返回 busy）。本次**保持既有语义**未改（改粒度 = 设计变更，超出「最小改动」授权）。但需确认这是否符合产品预期 —— 若用户期望「A 跑着也能手动跑 B」，应另立条目改成分任务锁（`{task_id: bool}`）。**这是本次修复的一个已知副作用边界。**
2. **`run_task_now` 现在会与后台 `_tick` 互斥**，极端情况下用户点「立即执行」可能撞上 `_tick` 而收到 `busy` 拒绝（此前是静默并发执行）。**这是 fail-closed 的预期代价**，但 UI 层（`api/tasks.py:423`）—— 是否会把 `{"ok": False, "busy": True}` 显示成「执行失败」而误导用户，需前端确认。**我未改 api 层（越界禁止），仅在此标注。**
3. **MC-2 的「冷却自结束起算」改变了既有节奏语义**：原形态下长任务会被更频繁地调度（ albeit 错误地）。修复后，耗时 D 的任务其实际周期变为 `D + interval` 而非 `interval`。若某任务依赖「准点每 interval 跑一次」的语义（而非「间隔 interval 空闲」），行为会变化。**我按派工要求取「完成时刻」语义，但这一取舍值得父会话知悉。**
4. **D 项（`_tick` 的 `entered`）在派工清单之外**，见 §4 第 1 条，请复核是否接受。
5. **B4 的时段依赖是既有缺陷**（不是我引入的），我只修了判据侧；`_gate_for_send` 本身**未动**。若其它门禁也存在「依赖墙上时钟」的同类问题，建议单独立项普查。
6. **本机缺 `loguru` 说明该 Python 环境并非项目完整运行环境**（`requirements.txt` 内还有其它依赖可能同样缺失）。本次只装了 `loguru` 一个就跑通了，但**不能证明其它测试在此环境可跑**。父会话若要在 CI/真机复现，建议先对齐依赖。
7. **负控的 `B10b`（TOCTOU）注入体带 `time.sleep(0.05)`**，其报红依赖时序窗口；虽实测稳定报红，但理论上存在**弱时序依赖**。若将来 CI 上偶发不报红，应改用 `Event` 同步而非 sleep。
