# HC-16 / M-29 报告：硬停止 vs 软停止的清队列语义 —— 离线契约门禁

> 子任务：M-29（并行子 agent 之一）
> 日期：2026-10-01
> git 根：`C:\Users\LOX\Desktop\DYchajian`，分支 `design/better-douyin`，HEAD `875e083`
> 执行者约束：只写本文件 + `DYAutoDM_v2/backend/test_engine_hard_stop_gate.py`；
> 未执行任何 git 写操作（`git add/commit/checkout/stash/clean` 一律未执行），
> 未改动版本文件六处，未改动 `core/dispatch.py` / `core/auto_dm.py` / `api/engine.py`。

---

## ① 自述改动清单

| # | 文件 | 动作 | 说明 |
|---|------|------|------|
| 1 | `DYAutoDM_v2/backend/test_engine_hard_stop_gate.py` | **新建**（唯一源码产出） | 硬/软停止清队列语义的离线契约门禁，含 5 条正例判据 + 4 条负控 |
| 2 | `DYAutoDM_v2/artifacts/HC16_M29_report.md` | **新建**（本文件） | 报告 |

**先做的第 1 步：搜索是否已有同语义门禁**（结论 —— **没有**，故新建，未收敛/接线）：

- `grep -rn "stop_hard\|stop_soft\|_clear_queue" --include=test_*.py .` → 命中 **1** 处，
  且是**端点层**：`DYAutoDM_v2/backend/test_engine_multi_account.py:208`
  （把 `engine_api.stop_engine` / `engine_api.stop_soft` 当函数名传进 409 歧义测试，
  只验 HTTP 层账号解析，不验任何队列语义）。
- `grep -rn "queue_size\|wait_done" --include=test_*.py .` → 命中 12 处，
  全部是**替身自造的 `queue_size`**（`test_engine_idle_guard.py:80` 的 `_FakeDispatch`）
  或**无关同名局部变量**（`test_stranger_quota_leak.py`、`test_task_scheduler_gates.py`），
  与 `DispatchCenter` 的真实清队列语义无交集。
- `wait_done` 在所有既有 `test_*.py` 中命中 **0**。

⇒ 判定：**调度层的「硬停真清空 / 软停真保留」此前没有任何机械判据**，符合新建条件。
（详见第 ⑤ 节的逐条 grep 与真实命中数。）

---

## ② 自述测试数字

### 真实 pytest 输出（可复现命令）

```
cd C:/Users/LOX/Desktop/DYchajian/DYAutoDM_v2/backend
python -m pytest test_engine_hard_stop_gate.py -v
```

```
============================= test session starts =============================
platform win32 -- Python 3.14.6, pytest-9.1.1, pluggy-1.6.0 -- C:\...\Python314\python.exe
cachedir: .pytest_cache
rootdir: C:\Users\LOX\Desktop\DYchajian\DYAutoDM_v2\backend
plugins: anyio-4.14.2
collecting ... collected 11 items

test_engine_hard_stop_gate.py::TestHardStopClearsQueue::test_H1_hard_stop_clears_queue_and_pending PASSED [  9%]
test_engine_hard_stop_gate.py::TestHardStopClearsQueue::test_H2_submit_after_hard_stop_is_ignored PASSED [ 18%]
test_engine_hard_stop_gate.py::TestSoftStopKeepsQueue::test_S1_soft_stop_keeps_queue_and_pending PASSED [ 27%]
test_engine_hard_stop_gate.py::TestSoftStopKeepsQueue::test_S2_submit_after_soft_stop_is_ignored PASSED [ 36%]
test_engine_hard_stop_gate.py::TestTwoStatesDivergeOnWaitDone::test_S3_hard_stop_sends_nothing PASSED [ 45%]
test_engine_hard_stop_gate.py::TestTwoStatesDivergeOnWaitDone::test_S3_soft_stop_drains_all_backlog PASSED [ 54%]
test_engine_hard_stop_gate.py::TestTwoStatesDivergeOnWaitDone::test_S3_the_two_states_are_opposite PASSED [ 63%]
test_engine_hard_stop_gate.py::TestNegativeControls::test_X0_real_implementation_passes_the_same_checks PASSED [ 72%]
test_engine_hard_stop_gate.py::TestNegativeControls::test_X1_broken_hard_stop_turns_H1_red PASSED [ 81%]
test_engine_hard_stop_gate.py::TestNegativeControls::test_X2_broken_soft_stop_turns_S1_and_S3_red PASSED [ 90%]
test_engine_hard_stop_gate.py::TestNegativeControls::test_X3_broken_submit_turns_H2_and_S2_red PASSED [100%]

============================= 11 passed in 6.79s =============================
```

**稳定性自检**：连续跑 3 遍（`-q`）均为 `11 passed`（6.18s / 4.78s / 4.37s），无 flaky。

**`python -m py_compile test_engine_hard_stop_gate.py`** → 退出码 `0`（无输出即通过）。

### 测试清单（11 条 = 7 正例 + 4 负控）

| # | 类名 | 测试函数 | 断言的事实 | 结果 |
|---|------|---------|-----------|------|
| 1 | `TestHardStopClearsQueue` | `test_H1_hard_stop_clears_queue_and_pending` | `stop_hard()` 后 `queue_size()==0`、`pending=={}`、`_clear_queue` 置位 | PASSED |
| 2 | `TestHardStopClearsQueue` | `test_H2_submit_after_hard_stop_is_ignored` | 硬停后 `submit()` 返回 False、队列不增长、但捕获记录仍保留；连提两次也不长 | PASSED |
| 3 | `TestSoftStopKeepsQueue` | `test_S1_soft_stop_keeps_queue_and_pending` | 软停后 `queue_size()>0` 且长度不变、`pending` 内容不变、`_accept_new=False`、`_clear_queue` **不**置位 | PASSED |
| 4 | `TestSoftStopKeepsQueue` | `test_S2_submit_after_soft_stop_is_ignored` | 软停后 `submit()` 同样被忽略，队列与 pending 均不变 | PASSED |
| 5 | `TestTwoStatesDivergeOnWaitDone` | `test_S3_soft_stop_drains_all_backlog` | **决定性**：软停后跑完 `wait_done()`，打桩发送回调记录到 **发出 3 条 == 入队 3 条** | PASSED |
| 6 | `TestTwoStatesDivergeOnWaitDone` | `test_S3_hard_stop_sends_nothing` | **决定性**：硬停后同样跑 `wait_done()`，**发出 0 条** | PASSED |
| 7 | `TestTwoStatesDivergeOnWaitDone` | `test_S3_the_two_states_are_opposite` | 同一批存量两态对比：3 vs 0，且 `assertNotEqual`（两态必须可区分） | PASSED |
| 8 | `TestNegativeControls` | `test_X0_real_implementation_passes_the_same_checks` | **对照**：真实实现在**同一组判据**下必须全绿 | PASSED |
| 9 | `TestNegativeControls` | `test_X1_broken_hard_stop_turns_H1_red` | **负控**：`stop_hard` 漏掉清空循环 → H1 判据必须抛 `AssertionError` | PASSED |
| 10 | `TestNegativeControls` | `test_X2_broken_soft_stop_turns_S1_and_S3_red` | **负控**：`stop_soft` 也清空 → S1 与 S3-软 判据都必须变红 | PASSED |
| 11 | `TestNegativeControls` | `test_X3_broken_submit_turns_H2_and_S2_red` | **负控**：`submit` 不看停止闸门 → H2 与 S2 判据都必须变红 | PASSED |

### 🔴 刻意失败态 / 负控样本 —— 明确标注

**本文件最终没有保留任何会红的测试**：11 条全绿。这是**正确**结果，原因如下：

- 负控不是「让测试失败」，而是**证明判据有判别力**。做法是：把判据抽成模块级函数
  `check_H1 / check_H2 / check_S1 / check_S2 / check_S3_soft / check_S3_hard`，
  **正例与负控共用同一份判据**。
- 负控里用 `with self.assertRaises(AssertionError): check_XXX(...)`：
  即**在受控作用域内**让缺陷变体吃下 AssertionError，并断言它**确实抛了**。
  若判据退化成恒真（缺陷变体也能过），`assertRaises` 自己会失败 —— 负控就变成红灯报信。
- 缺陷变体仅在内存里用子类注入（`_BrokenHardStop` / `_BrokenSoftStop` / `_BrokenSubmit`），
  **不改动任何生产源码**，磁盘上的 `core/dispatch.py` 一字未动。

**开发过程中确实出现过 2 次真实红灯，均已修复，如实记录：**

1. 首版 X3 误用了 `_BrokenSoftStop` / `_BrokenHardStop`（坏的是 stop 而不是 submit），
   真实实现的 `submit` 闸门照样拦住 → `violated=False` → **1 failed / 10 passed**。
   修正为只坏 `submit` 的 `_BrokenSubmit` 变体。
2. **`check_S3_soft` 初版有一个真缺陷**：期望值在函数内读 `dc.queue_size()`。
   缺陷变体「软停止顺手清空队列」会让期望值退化成 0，判据变成 `0 == 0` **恒真**
   → X2 负控抓不住它（`AssertionError: AssertionError not raised`）。
   修正为**在 `stop_soft()` 之前**把入队数作为 `expected` 显式传入。
   ⚠️ 这条教训已写进代码注释：**期望值必须取自被测动作之前**，否则判据自我消解。

---

## ③ 唯一标识符

- **新文件**：`DYAutoDM_v2/backend/test_engine_hard_stop_gate.py`
  （绝对路径 `C:\Users\LOX\Desktop\DYchajian\DYAutoDM_v2\backend\test_engine_hard_stop_gate.py`）
- **关键测试函数**（grep 锚点，全部以 `test_` 开头）：
  - `test_H1_hard_stop_clears_queue_and_pending`
  - `test_H2_submit_after_hard_stop_is_ignored`
  - `test_S1_soft_stop_keeps_queue_and_pending`
  - `test_S2_submit_after_soft_stop_is_ignored`
  - `test_S3_soft_stop_drains_all_backlog`
  - `test_S3_hard_stop_sends_nothing`
  - `test_S3_the_two_states_are_opposite`
  - `test_X0_real_implementation_passes_the_same_checks`
  - `test_X1_broken_hard_stop_turns_H1_red`
  - `test_X2_broken_soft_stop_turns_S1_and_S3_red`
  - `test_X3_broken_submit_turns_H2_and_S2_red`
- **共用判据函数**（负控复用的同一份事实）：`check_H1 / check_H2 / check_S1 / check_S2 / check_S3_soft / check_S3_hard`
- **缺陷变体类**：`_BrokenHardStop` / `_BrokenSoftStop` / `_BrokenSubmit`
- **打桩类**：`_Auth`（凭证替身）/ `_SendSpy`（发送回调桩，替换 `_do_send`）

---

## ④ 诚实标注

### 没做到的 / 覆盖不到的

1. **真机最后一跳没验**（本门禁**设计上**就覆盖不到）：
   - `/api/tasks/current` 返回的队列长度是否真的归零；
   - 日志里是否真的出现 `[SEND-002] [调度] 硬停止：已清空待发队列，不再发送任何私信`；
   - 前端两个按钮点下去后，状态徽章是否真的走到 STOPPED。
   这些需要真机 + 真实抖音会话，离线门禁无法替代。
2. **`AutoDM.stop(hard=...)` 这一层未直接测**：只测了它下游的 `DispatchCenter`。
   `auto_dm.py:1064` 的分支选择（hard→`stop_hard`、soft→`stop_soft` +
   `create_task(_wait_dispatch_done())`）靠的是**读码确认 + 端点层既有测试**，
   本文件没有为 `AutoDM.stop` 的分支选择建独立判据。
3. **真实 `_loop` 的时序行为未测**：`_drain()` 是自己写的消费协程，绕开了真实 `_loop`。
   因此 **`_stop_signal` 打断延迟窗口（ENG-016）**、**`reached_limit` 清队列**、
   **`on_idle` 触发** 这些 `_loop` 内部时序语义**不在本门禁范围内**。
   理由写进了代码注释：真实 `_loop` 含 `sleep(interval)` 与随机延迟，既慢又不稳。
4. **软停止的「pending-only 目标」语义是绕过的**：`_drain()` 末尾直接 `pending.clear()`。
   真实 `_loop` 在软停止时会对「尚未到发送时刻」的目标走丢弃路径
   （`dispatch.py:405-416`：`rec.reason = "软停止：尚未到发送时刻，已丢弃"`）。
   也就是说——**真实软停止未必会把「已捕获但没排期」的那部分发出去**，
   本门禁的 S3-软 只锁定了「已入队的存量必须发完」，没有覆盖这条丢弃分支。
   这是**语义上的真实不确定点**（见 ⑥ 疑点 1）。

### 不确定的

- `_QueueItem` 是 `core/dispatch.py` 的内部类型，测试用 `try/except` 软引用并带元组退化分支。
  当前实际走的是 `_QI` 分支（未触发退化），退化路径**未被执行验证**。
- 测试构造走的是**直填 `_queue` / `pending`**，而非 `submit()` 入队。
  这样可以拿到确定存量，但也意味着「submit → 入队 → 停止」的**完整链路**
  只被 H2/S2 部分覆盖（只覆盖了停止后的拒绝）。

### 真机剩余部分（明确列出）

| 判据 | 离线覆盖 | 真机剩余 |
|------|---------|---------|
| `stop_hard` 清空队列 | ✅ H1 | — |
| `stop_hard` 后不收新目标 | ✅ H2 | — |
| `stop_soft` 保留存量 | ✅ S1 | — |
| `stop_soft` 后不收新目标 | ✅ S2 | — |
| 软停发完 / 硬停不发（决定性） | ✅ S3（打桩发送回调计数） | 需真机确认真实发送通道同样如此 |
| `/api/tasks/current` 队列长度归零 | ❌ | **需真机** |
| 日志出现 `[SEND-002]` | ❌ | **需真机** |
| 前端按钮 → 状态徽章 STOPPED | ❌ | **需真机** |
| `AutoDM.stop` 的 hard/soft 分支选择 | ❌（读码 + 端点层既有测试） | 建议真机顺带确认 |

---

## ⑤ 交叉判据（grep 命令 + 实际命中数）

> 全部为**实际执行**结果。工作目录 `C:\Users\LOX\Desktop\DYchajian`。

### 5.1 先搜索是否已有同语义门禁（第 1 步的实证）

命令：
```bash
grep -rn "stop_hard\|stop_soft\|_clear_queue" --include=test_*.py .
grep -rn "queue_size\|wait_done"              --include=test_*.py .
```

**排除本新文件后的既有命中数**（这才是「有没有同语义门禁」的答案）：

| 关键词 | 既有 `test_*.py` 命中数（排除新文件） | 性质 |
|--------|--------------------------------|------|
| `stop_hard` | **0** | 无 |
| `stop_soft` | **1** | `test_engine_multi_account.py:208`（端点层 409 歧义测试，非队列语义） |
| `_clear_queue` | **0** | 无 |
| `wait_done` | **0** | 无 |
| `queue_size` | 12 | 全是替身自造字段或无关同名局部变量 |

### 5.2 全仓命中数（含源码 / 快照 / 脚本 / 新文件）

```bash
grep -rn "<kw>" --include=*.py .
```

| 关键词 | 全仓 `.py` 命中数 |
|--------|-----------------|
| `stop_hard` | 23 |
| `stop_soft` | 26 |
| `queue_size` | 57 |
| `wait_done` | 30 |
| `_clear_queue` | 15 |

其中生产源码（`backend/core` + `backend/api`，不含测试与快照）：

| 关键词 | 命中数 |
|--------|-------|
| `stop_hard` | 4 |
| `stop_soft` | 3 |
| `queue_size` | 5 |
| `wait_done` | 6 |
| `_clear_queue` | 4 |

### 5.3 新文件自身命中数

| 关键词 | 在新文件中出现次数 |
|--------|-----------------|
| `stop_hard` | 15 |
| `stop_soft` | 17 |
| `queue_size` | 14 |
| `wait_done` | 7 |
| `_clear_queue` | 5 |

### 5.4 交叉核对的源码行（只读确认，未修改）

- `backend/core/dispatch.py:127` `stop_hard()` → 置 `_clear_queue/_accept_new/_stopped`、
  `while not _queue.empty(): get_nowait()`、`pending.clear()`、日志 `[SEND-002]` ✅
- `backend/core/dispatch.py:142` `stop_soft()` → 只置 `_accept_new=False` + `_stop_signal.set()` ✅
- `backend/core/dispatch.py:209` `queue_size()` ✅ ／ `:560` `wait_done()` ✅
- `backend/core/auto_dm.py:1079 / 1081` → `stop_hard()` / `stop_soft()` 分支 ✅
- `backend/api/engine.py:253 / 265` → `POST /stop` / `POST /stop-soft` ✅

### 5.5 风控红线自查

- 未调用任何真实发送路径：`_do_send` 被整体替换为 `_SendSpy._fake_do_send`（只 `list.append`）；
  `auth.account_name` 置空，杜绝 `_do_send` 走 `dm_dispatch.submit_by_uid` 路由（双保险）。
- 未联网、未启动浏览器、未触碰抖音。
- 未执行任何 git 写操作（`git status` 为只读）。当前工作区仍有**其他并行子 agent 的改动**
  （`test_high_value_keywords_entry.py`、`core/live_hook.py`、`app_config_schema.py`、
  `_build_version.py` 等），**本人未碰其中任何一个**。

---

## ⑥ 疑点

1. **（语义疑点，值得产品确认）软停止未必「发完已捕获的全部」**
   `dispatch.py:399-416`：`_loop` 在软停止后若被 `_stop_signal` 唤醒，**会主动丢弃**
   尚未到发送时刻的目标，并写 `reason="软停止：尚未到发送时刻，已丢弃"`。
   而 `stop_soft()` 的 docstring 与 `errcode_data.py:786` 的描述是「存量发完」。
   ⇒ **「已入队的存量」与「已捕获但未到发送时刻的目标」在真实软停止下命运不同**，
   前端文案若承诺「会发完」，可能与用户实际观感不符。
   **未擅自修改**，仅记录。建议由产品侧定调后决定是否调整文案或语义。

2. **（本门禁自身的边界）`check_S3_soft` 的期望值来源**
   初版在函数内读 `queue_size()`，被缺陷变体「自我消解」成 `0 == 0`。
   已修为调用方显式传入 `expected`（取自 `stop_soft()` 之前）。
   ⇒ 教训：任何「清空类」语义的判据，期望值**必须**在被测动作之前固化，
   否则判据会被被测代码自身的副作用吃掉。已写进代码注释。

3. **（未被门禁约束的既有行为）`submit()` 被拒时仍写 CAPTURED 记录**
   `dispatch.py:284-285` 在**所有拒绝点之前**先 `_ensure_record()`。
   这是有意设计（不丢数据），本门禁在 H2/S2 里顺带锁住了它
   （`assertIn("new1", dc.records)`）。若将来有人为了「干净」把它挪到拒绝点之后，
   这两条断言会红 —— 属于**有意保留**的约束，非缺陷。

4. **（未验证）`AutoDM.stop(hard=False)` 的 `_wait_dispatch_done()` 任务**
   `auto_dm.py` 在 `queue_size()>0 or pending` 时挂 `asyncio.create_task(...)`。
   该任务**没有句柄保存**（fire-and-forget），重复调用 stop 可能挂出多个。
   本门禁未覆盖，也未验证是否存在重复挂任务的问题 —— 仅记疑点，未改动。
