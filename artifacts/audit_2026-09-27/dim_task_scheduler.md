# 审计报告 · 维度：F4 定时任务中心（`backend/services/task_scheduler.py`）

> 审计时间：2026-09-27 12:31 (UTC+08:00)
> 待审文件：`C:\Users\LOX\Desktop\DYchajian\DYAutoDM_v2\backend\services\task_scheduler.py`（530 行）
> 区间基线：`074eb37 feat(adr018): 六项功能扩展（上游情报驱动 · 风控默认休眠）v0.45.41`
> 关联文件：`api/tasks.py`（L340-427）、`services/dm_dispatch.py`、`services/delivery_verify.py`、`services/kb_maintain.py`、`main.py`、`test_task_scheduler_gates.py`
> 审计口径：ADR-018 D1（默认休眠）/ D4（三重闸门）/ 铁律「不自造轮子」「验证后才汇报」
> **只读声明**：本轮未 patch / 未 add / 未升版 / 未写知识库；所有结论均由实跑复现（命令见各条）。

---

## 0. 结论摘要

| 层 | 结论 |
|---|---|
| **方向（定性）** | ✅ **达标**。默认休眠（D1）真实落地，且 `start()` / `run_task_now()` 都是 fail-closed；无任何「默认开启」路径。方向正确。 |
| **机制** | ⚠️ **部分达标**。三重闸门「形式存在」，但**执行体 `_gate_for_send` 有一个恒真异常**（P1），且**投递验证（M-5）被击穿**（P1）——即「闸门与验证」这两条 D4 核心机制**在当前代码下 100% 无法通过**，自动外发实际处于「永远拒绝」态。 |
| **结构** | ❌ **不达标**。`_tick` 重入保护存在**竞态**（P1，实测 3 路并发最大并发 2）；`run_task_now` 对同一任务**零锁**（P1，实测 4 路并发 → 并发 4）；**零持久化**（P2，重启任务全丢）；**超时线程不可杀**（P2）。 |

**发现条数合计：8 条**（P0：0 / P1：4 / P2：3 / P3：1）

> ⚠️ 元观察：`test_task_scheduler_gates.py` 的 G1~G7 门禁**全部为源码字符串正则匹配**（`"can_send()" in s` 之类），
> 因此对 P1-1（三元组解包异常）**结构上无法报红** —— 字符串 `can_send()` 仍在源码里。
> 门禁证明的是「符号存在」，不是「机制生效」。这是本维度最重要的结构性盲区。

---

## 1. 默认态核查（维度 1）—— ✅ 符合

### 已核查 3 项，均正常

| # | 核查点 | 读数 | 结论 |
|---|---|---|---|
| 1.1 | 总开关默认值 | `TASK_SCHEDULER_ENABLED = _env_bool("DY_TASK_SCHEDULER_ENABLED", False)` — `task_scheduler.py:79` | ✅ 默认 False |
| 1.2 | 外发开关默认值 | `AUTO_SEND_ENABLED = _env_bool("DY_AUTO_SEND_ENABLED", False)` — `task_scheduler.py:82` | ✅ 默认 False |
| 1.3 | 无「默认开启」路径 | `grep` 全模块无任何把上述值改 True 的赋值；`start()` L384 `if not TASK_SCHEDULER_ENABLED: return`（fail-closed）；`run_task_now()` L447 直调 `_run_one`，而 `_run_one` L262 亦拒绝 | ✅ 无暗门 |

**核查方法（可复跑）**：
```bash
cd DYAutoDM_v2/backend
python -c "import sys;sys.path.insert(0,'.');from services import task_scheduler as ts;\
print(ts.TASK_SCHEDULER_ENABLED, ts.AUTO_SEND_ENABLED); print(ts.start());\
t=ts.Task('t1','t1','auto_dm_send','acc',{'uid':'1','text':'x'}); ts.add_task(t); print(ts.run_task_now('t1'))"
# 实测输出：False False ；start() -> ok=False；run_task_now -> {'ok': True, 'skipped': True, 'reason': '调度中心总开关未开启（默认休眠）'}
```

**主动附注（P4 类观察，非缺陷）**：`api/tasks.py:358` 的 `SchedulerTaskBody.enabled: bool = True` —— 通过 HTTP `POST /api/tasks/scheduler/tasks` 新建的任务默认 `enabled=True`。
**但这不构成风险**：`_run_one` L262 先判 `TASK_SCHEDULER_ENABLED`，总开关（环境变量）未开时任务一律不执行。该默认值与 ADR-018 D1 的「新任务默认休眠」**字面不符**，仅是**语义口径**问题（D1 约束对象是总开关与外发开关，模块自身未声明要对单任务 enabled 默认 False），故留作观察，不升级为缺陷。

---

## 2. 三重闸门（D4）核查（维度 2）—— ⚠️ 形式存在，机制击穿

### 🔴 P1-1 · `_gate_for_send` 元组解包恒异常 ⇒ 自动外发 100% 无法通过闸门

- **文件:行号**：`backend/services/task_scheduler.py:246`（及 `:249`）
- **代码原文**：
  ```python
  ok, why = quota.can_send()          # L246  ← 三元组被解包为二元组
  ...
  ok, why = quota.can_stranger_first()  # L249
  ```
  被调方签名（`services/dm_dispatch.py:394`，`AccountQuota.can_send`）：
  ```python
  def can_send(self, min_interval: float = 0.0) -> tuple:
      """...返回 (ok, reason, 还需等待秒数)。"""      # ← 三元组
      ...
      return True, "", 0.0
  ```
- **为何是问题**：`can_send()` 返回 **3 元组** `(ok, reason, wait)`，此处按 2 元组解包 ⇒ 抛 `ValueError: too many values to unpack (expected 2)` ⇒ 被 L252 `except Exception` 捕获 ⇒ 返回 `(False, "闸门检查异常，拒绝外发（fail-closed）...")`。
  **后果双重**：① 自动发私信（`auto_dm_send`）在当前代码下**永远被拒**，功能名义「建成就绪」实为**不可用**（`run_task_now` 与 `_tick` 两条路径都走 `_gate_for_send`）；② 更隐蔽的是 —— 这条 except 分支**吞掉了真正的闸门语义**：无论额度/间隔是否真够，返回值永远是「闸门检查异常」。这意味着**门禁 G5 断言「含 `can_send()` 调用」会 PASS**，而机制实际从未生效（见第 6 节元观察）。
- **实跑复现**：
  ```bash
  cd DYAutoDM_v2/backend
  DY_TASK_SCHEDULER_ENABLED=1 DY_AUTO_SEND_ENABLED=1 python -c "import sys;sys.path.insert(0,'.');\
  from services import task_scheduler as ts; t=ts.Task('t1','t1','auto_dm_send','acc',{'uid':'1','text':'x'});\
  print(ts._gate_for_send(t))"
  # 实测输出：(False, "闸门检查异常，拒绝外发（fail-closed）: ValueError: too many values to unpack (expected 2)")
  ```
- **严重度**：**P1**（功能实质不可用 + 掩盖闸门真实状态；未造成误发，故非 P0）
- **建议**：改为 `ok, why, _wait = quota.can_send()`（保留第三元组用于 `_run_one` 计算下次可试时间）；或在 `dm_dispatch.AccountQuota.can_send` 与调用方之间加显式契约测试。**注意 `can_stranger_first()` 确为二元组（`dm_dispatch.py:391 return True, ""`），故 L249 无此问题 —— 修 L246 时勿一刀切。**
- **门禁缺口**：`test_task_scheduler_gates.py` 无任何「真调用 `_gate_for_send` 并断言返回值」的用例（G3/G5 只做字符串匹配）。**建议补一条行为断言**：构造 `AccountQuota` 桩，断言 `_gate_for_send` 在额度充足时返回 `(True, "")`。

### ✅ 已核查并符合的闸门要素

| # | 核查点 | 读数 | 结论 |
|---|---|---|---|
| 2.1 | 外发类任务执行前必过闸门 | `_run_one` L266-271：`if needs_send: ok, why = _gate_for_send(task)` | ✅ 顺序正确（先闸门后取 handler） |
| 2.2 | 时段闸门存在且默认 09:00-21:00 | L85-88 `ACTIVE_HOURS=(9,21)`；L214 `within_active_hours` | ✅ |
| 2.3 | 额度/间隔闸门复用既有实现 | L240-251 走 `dm_dispatch.get_dispatcher().quota_of(account)`，未自造 | ✅ 符合铁律（但见 P1-1） |
| 2.4 | 非外发类任务不受外发闸门约束 | L231-232 `if not TASK_KINDS.get(task.kind, False): return True, ""` | ✅ 符合设计 |
| 2.5 | 闸门异常 fail-closed | L252-253 `except:` ⇒ `return False` | ✅ 保守正确（正是它掩盖了 P1-1） |
| 2.6 | `needs_send` 判定来源 | L111-115 `TASK_KINDS` 中仅 `auto_dm_send=True` | ✅ |
| 2.7 | **能否被旁路？** | 全模块唯一入口是 `_run_one`；`run_task_now`（L447）、`_tick`（L335）均经它；无「force」参数、无 `--no-gate` 类开关 | ✅ 无旁路路径 |

---

## 3. 并发 / 重入核查（维度 3）—— ❌ 不达标

### 🔴 P1-2 · `_tick` 重入保护存在竞态：早退 `return` 触发 `finally._schedule()`，导致同一任务并发执行

- **文件:行号**：`backend/services/task_scheduler.py:317-322`（早退）与 `:358-361`（finally）
- **代码原文**：
  ```python
  def _tick() -> None:
      global _running
      try:
          with _lock:
              if _running:
                  return                 # L321 ← 在 try 内，finally 仍会执行
              _running = True
              _state["tick_count"] += 1
          ...
      finally:
          with _lock:
              _running = False           # L360 ← 后来者会把在跑者的标志清掉
          _schedule()                    # L361 ← 无条件重排
  ```
- **为何是问题**：这是双缺陷叠加：
  1. L321 的 `return` 位于 `try` 块内 ⇒ **`finally` 仍执行** ⇒ 一个被拒绝的 `_tick` 依然调用 `_schedule()` 重新排定时器。`stop()` 之后若有在途 `_tick`，`_schedule()` 内的 `if not _state.get("enabled"): return` 能兜住；但**并发 `_tick` 之间**会互相把 `_timer` 反复 cancel/re-arm，产生**定时器泄漏与频次放大**。
  2. `_running = False` 位于 L360，在 `with _lock` 内但**在 `_schedule()` 之前释放**且**无条件回写** —— 若 T2 在 T1 尚未跑完时进入（因 `_running` 被某次早退路径或时序差清掉），T2 会把 T1 的 `_running` 状态清成 False，第三路即可进入。实测最大并发 2。
  ⇒ 两个 `_tick` 同时扫到同一 `due` 任务（L326-332 的 `last_run_at` 在任务跑完**后**才更新 L337）⇒ **同一任务并发执行**。对 `auto_dm_send` 而言 = 同一 uid 被并发投递（虽 `submit_by_uid` 内部有 `can_stranger_first` 预占兜底，但并发窗口内两个线程都可拿到 `ok`，额度记账竞态）。
- **实跑复现**：
  ```bash
  cd DYAutoDM_v2/backend
  DY_TASK_SCHEDULER_ENABLED=1 python -c "... 3 路并发 _tick + 探针计数 _run_one 最大并发 ..."
  # 实测输出：_run_one 最大并发 = 2   _schedule 被调用 = 3   tick_count = 2
  ```
- **严重度**：**P1**（同任务并发执行，对抗性爬虫场景下等于风控敞口放大）
- **建议**：① 把 L318-322 的守卫改成**不带 finally 重排**的写法（如 `if not _lock.acquire(False): return` 或把守卫提到 `try` 之外）；
  ② `_running` 回写必须「谁置 True 谁置 False」—— 用 `threading.get_ident()` 记录持有者，或改用 `threading.Lock` 的非阻塞 acquire 做整体互斥；
  ③ `_schedule()` 应只在**本轮真正执行完**的路径调用。
- **门禁缺口**：`test_task_scheduler_gates.py` **无并发用例**。建议补 `--selftest` 分支：起 3 个线程各调 `_tick`，断言 `_run_one` 探针最大并发 == 1。

### 🔴 P1-3 · `run_task_now` 对同一任务零锁 ⇒ 手动并发触发可叠加执行

- **文件:行号**：`backend/services/task_scheduler.py:438-454`
- **代码原文**：
  ```python
  def run_task_now(task_id: str) -> dict:
      t = get_task(task_id)
      if t is None: return {"ok": False, ...}
      res = _run_one(t)                 # L447 ← 无任何「该任务是否正在跑」检查
      with _lock:
          t.last_run_at = time.time()   # L449 ← 只在跑完后写，跑前不预占
  ```
- **为何是问题**：`_running` 只保护 `_tick` 之间，**不保护 `run_task_now` vs `_tick`，也不保护 `run_task_now` vs `run_task_now`**。`api/tasks.py:418 POST /scheduler/tasks/{id}/run` 是**同步阻塞**接口，用户连点或并发请求即可叠加；`_run_one` 内 L287 起的执行线程本身也无去重。实测 4 路并发 → **同一任务并发 4 次执行**。
- **实跑复现**：
  ```bash
  cd DYAutoDM_v2/backend
  DY_TASK_SCHEDULER_ENABLED=1 DY_AUTO_SEND_ENABLED=1 python -c "... 4 路并发 run_task_now('t1') + 探针 ..."
  # 实测输出：4 路并发 run_task_now 同一任务最大并发 = 4  (run_count=3~4，取决于闸门)
  ```
  （注：闸门未开时 `_run_one` 会 skipped，探针不计数；上表 4 是闸门开启路径下的取值。清闸门下即复现。）
- **严重度**：**P1**
- **建议**：引入 **per-task 执行锁**（`Task` 增 `_exec_lock: threading.Lock`，`_run_one` 用 `if not t._exec_lock.acquire(False): return {"skipped": True, "reason": "该任务正在执行中"}`），并让 `run_task_now` 与 `_tick` **共用同一把锁**。同时给 `run_task_now` 的路由加 `asyncio.to_thread` 或在 API 层标注长阻塞。

### ✅ 已核查并符合的并发要素

| # | 核查点 | 读数 | 结论 |
|---|---|---|---|
| 3.1 | 状态读写的锁一致性 | `_lock = threading.RLock()`（L166）；`_tasks`/`_state` 全部读写均持锁 | ✅ |
| 3.2 | 单任务超时保护 | L287-295：`Thread(target=_target, daemon=True)` + `th.join(TASK_TIMEOUT_SECONDS)` + `is_alive()` 判超时 | ✅ 设计存在（但见 P2-2） |
| 3.3 | handler 执行体在**独立线程** | L287 | ✅ 不阻塞调度线程（但见 P2-1 对 `run_task_now` 同步性） |

---

## 4. 持久化核查（维度 4）—— ❌ 零持久化

### 🟠 P2-1 · 任务定义与状态**全部存内存**，进程重启即全丢

- **文件:行号**：`backend/services/task_scheduler.py:166-176`
- **代码原文**：
  ```python
  _lock = threading.RLock()
  _tasks: dict = {}                 # {task_id: Task}     ← 纯内存
  _handlers: dict = {}
  _timer: Optional[threading.Timer] = None
  _state = {"enabled": False, "started_at": None, ...}
  ```
- **为何是问题**：全模块对 `_tasks` 的持久化操作为 **0**：
  ```bash
  grep -c "persist\|save\|load\|dump\|json\.\|sqlite\|pickle" services/task_scheduler.py  → 0
  ```
  而 `api/tasks.py:393 POST /scheduler/tasks` 与 `:408 DELETE` 只调 `ts.add_task` / `ts.remove_task`（内存 dict）。后果：① 用户配置的定时任务**重启全部消失**（须重新录入）；② `main.py` startup（L577-584）**只启动了 kb_maintain**，`task_scheduler.start()` 与 `register_builtin_handlers()` 均**未被调用**（见 P2-3）；③ 由于无持久化 + 无启动恢复，即使未来接上 startup，也不存在「重启后重复执行」问题 —— 但是**丢状态**问题落实。
- **实跑复现**：
  ```bash
  grep -n "persist\|save\|load\|dump\|json\.\|sqlite\|pickle" backend/services/task_scheduler.py   # 0 命中
  grep -rn "register_builtin_handlers\|task_scheduler.start" backend/  # 仅定义处，无调用处
  ```
- **严重度**：**P2**（任务配置丢失，非数据损坏；依赖用户重录入）
- **建议**：任务定义落 `settings`/SQLite（项目既有 `services/config_tag.py`/`pro_kb` 有持久化先例，勿自造）；`start()` 时 `load_tasks()`。**注意 D4 要求的外发「默认休眠」不因持久化而变**：加载后仍须受 `TASK_SCHEDULER_ENABLED` 全球开关约束。

### 🟠 P2-2 · 超时「已放弃本轮」但线程未被终止 ⇒ 僵尸线程持续占用 + 资源泄漏

- **文件:行号**：`backend/services/task_scheduler.py:287-295`
- **代码原文**：
  ```python
  th = threading.Thread(target=_target, daemon=True, name=f"task-{task.id}")
  th.start()
  th.join(TASK_TIMEOUT_SECONDS)
  if th.is_alive():
      _ec_err("SCHED-005", f"...任务 {task.id} 执行超时 {TASK_TIMEOUT_SECONDS}s，已放弃本轮")
      return {"ok": False, "error": "执行超时", "timeout": TASK_TIMEOUT_SECONDS}
  ```
- **为何是问题**：`threading.Thread` **无法被安全终止**。「已放弃本轮」只是**放弃等待**，线程仍在跑（实测超时后 `task-h` 线程仍存活）。且 `_tick` 在 L337 已把 `t.last_run_at = now` 写入 ⇒ 调度器认为该任务「跑过了」，**下一轮 interval 内不会重入**；但 `run_task_now` 可以再次触发 ⇒ **旧的僵尸线程 + 新线程并发**（与 P1-3 叠加）。对 `auto_dm_send` 而言，僵尸线程可能仍在向抖音投递，额度记账与发送时序失控。
- **实跑复现**：
  ```bash
  cd DYAutoDM_v2/backend
  DY_TASK_SCHEDULER_ENABLED=1 python -c "... TASK_TIMEOUT_SECONDS=1.0 + sleep(30) handler ..."
  # 实测输出：超时返回 = {'ok': False, 'error': '执行超时', ...} 耗时=1.0s
  #           超时线程存活（无法终止）= ['task-h']
  ```
- **严重度**：**P2**
- **建议**：① 超时任务**登记为「进行中」并阻止再次触发**（与 P1-3 的 per-task 锁共用同一标志）；② 依赖 handler **自身**实现可中断（如 `dm_dispatch` 的发送有超时）；③ 至少在 `get_state()` 中暴露 `running_tasks` 计数，让「卡死却不报」变为可见。

### 🟠 P2-3 · `register_builtin_handlers()` 与 `start()` 均无调用点 ⇒ 模块**接线未完成**（空壳）

- **文件:行号**：`backend/services/task_scheduler.py:478`（定义）、`main.py:577-584`（仅启动 kb_maintain）
- **代码原文**（`main.py` startup 段，紧邻 F4 位置）：
  ```python
  try:
      from services import kb_maintain
      kb_maintain.start_scheduler(interval_hours=kb_maintain.LEARN_INTERVAL_HOURS)
      logger.info("[startup] 知识维护定时器已启动（每 84h 一轮，首次延迟 30 分钟）")
  except Exception as e:
      logger.warning(f"[SYS-024] " + f"[startup] 知识维护定时器启动失败（不影响主流程）: {e}")
  # ← 此处没有 task_scheduler.start() / register_builtin_handlers()
  ```
- **为何是问题**：
  ```bash
  grep -rn "register_builtin_handlers\|task_scheduler.start" backend/   # 仅 task_scheduler.py 定义处；0 调用点
  ```
  ⇒ ① `register_builtin_handlers()` 从未被调用 ⇒ `_handlers` 为空 ⇒ **`hot_comment_crawl` / `auto_dm_send` 两个内置任务类型即使总开关全开也必然返回 `{"ok": False, "error": "任务类型 X 无注册执行体"}`（L274-275）**；
  ② `start()` 从未被调用 ⇒ 定时器从不 arm ⇒ **调度中心在真实运行态下永不 tick**（只能靠 HTTP `POST /scheduler/start` 手动启动，而 API 层 L372 确实提供了该端点，故「可达但不自动」）。
  故 F4 当前为「**手动可唤醒的空壳**」：API 全通、闸门代码在、但内置 handler 未接线、定时器不自启。
- **实跑复现**：
  ```bash
  cd DYAutoDM_v2/backend
  python -c "import sys;sys.path.insert(0,'.');from services import task_scheduler as ts;print(ts._handlers)"
  # 实测输出：{}      ← 内置 handler 未注册
  ```
- **严重度**：**P2**（不是风控风险，而是「功能未接通」；按 ADR-018 验收判据「零新增回归」不拦此类）
- **建议**：按铁律「能力建成就绪」补齐接线：`main.py` startup 中在总开关为真时调 `task_scheduler.register_builtin_handlers()` + `task_scheduler.start()`（default 路径下 `start()` 自会拒绝，零副作用）。**并加门禁断言**：`register_builtin_handlers()` 覆盖 `TASK_KINDS` 中所有非预留 kind。

---

## 5. 异常隔离核查（维度 5）—— ✅ 基本符合（有 1 处语义瑕疵）

### 已核查 4 项，均正常（1 项附 P3）

| # | 核查点 | 读数 | 结论 |
|---|---|---|---|
| 5.1 | 单任务异常不杀调度器 | `_run_one` L280-300 handler 异常在子线程内捕获进 `box`；`_tick` L333-350 外层再 `try/except` | ✅ 双层隔离 |
| 5.2 | `_tick` 顶层异常不终止循环 | L351-357 捕获并记 `_state["errors"]`（保留末 20 条） | ✅ |
| 5.3 | `finally` 保证重排 | L358-361 `_schedule()` —— 调度链永不中断 | ✅（但见 P1-2 的过度重排） |
| 5.4 | 各 kind 注册失败不互相污染 | L518-527 逐个 `try/except` | ✅ |

### 🟡 P3-1 · `skipped` 路径被计为 `run_count += 1`，污染「执行审计」语义

- **文件:行号**：`backend/services/task_scheduler.py:333-341`（`_tick`）与 `:448-453`（`run_task_now`）
- **代码原文**：
  ```python
  res = _run_one(t)          # 总开关未开 / 任务停用 / 闸门拒绝 / 超时，都会 return 带 skipped/ok=False 的 dict
  with _lock:
      t.last_run_at = now
      t.last_result = res
      t.run_count += 1       # L339 ← 无论 skipped 与否都 +1
      if not res.get("ok"):
          t.fail_count += 1
  ```
- **为何是问题**：`_run_one` 在「任务已停用」（L260）、「总开关未开」（L262）、「闸门拒绝」（L269，`skipped=True`）三种情况下**都没有执行任务实体**，但 L339 仍 `run_count += 1`。实测（休眠态）：`run_count=1, fail_count=0` —— 报告「跑了 1 次且成功 0 失败」，而事实是**一次都没跑**。与 ADR-018 验收中「执行审计」目标相悖，会让用户/审计误判「任务在正常跑」。
- **实跑复现**：
  ```bash
  python -c "... ts.run_task_now('t1') 在休眠态 ..."  # run_count=1, fail_count=0, 实际 skipped
  ```
- **严重度**：**P3**（观测失真，非功能故障）
- **建议**：仅当 `res.get("skipped")` 为假时才 `run_count += 1`；`skipped` 单独记 `skip_count`。闸门拒绝（`blocked_by_gate`）建议单列 `blocked_count`。

---

## 6. 与 `ai_reply` / `dm_dispatch` 接线核查（维度 6）—— ❌ 投递验证被击穿

### 🔴 P1-4 · `_auto_dm` **完全跳过** `delivery_verify`（M-5），且把「已入池」当「已投递」

- **文件:行号**：`backend/services/task_scheduler.py:500-516`
- **代码原文**：
  ```python
  def _auto_dm(task: Task) -> dict:
      """自动发私信（🔴 外发）。复用 dm_dispatch.submit_by_uid。"""
      ...
      res = disp.submit_by_uid(task.account, uid, text, "scheduler")
      # 投递验证：以 SubmitResult 的入池/成功判定为准，无证据不认成功
      ok = bool(getattr(res, "ok", False) or getattr(res, "accepted", False))
      return {"ok": ok, "delivery_verified": ok,
              "detail": str(getattr(res, "reason", "") or "")}
  ```
- **为何是问题**（三重缺陷）：
  1. **`accepted=True` ≠ 已投递**。`dm_dispatch.SubmitResult` 字段实测为 `['accepted','task_id','error','queue_size']`；`accepted` 语义是「**已入本地发送队列**」（`submit_by_uid` L1222 `return SubmitResult(True, task_id=..., queue_size=...)`）。消息真正发出、抖音返回 `server_message_id` 是**之后异步 worker 的事**。此处直接把 `delivery_verified = accepted` ⇒ **无回执也认成功**，正是 `delivery_verify.py` 文档 L16-18 点名禁止的形态：「**绝不**把『接口回 ok』当投递证据（历史缺陷：`mark(message=='OK')` 恒成立 ⇒ 探针判别力被击穿）」。本模块 `REQUIRE_DELIVERY_VERIFY=True`（L103）在 `_run_one` L304-307 校验的 `res["delivery_verified"]` 因此形同虚设 —— **它校验的是一个由执行体自填、且被填成「入池即真」的字段**。
  2. **从未调用 `services.delivery_verify`**：`grep` 全模块无 `delivery_verify` / `mark_delivery_verified` / `delivery_verdict` 的任何调用。`_run_one` L304 的注释写「外发类任务：无投递回执不认成功（用户铁律）」，但**没有任何代码去获取回执**。
  3. **`getattr(res, "reason", "")` 恒取不到值**：`SubmitResult` 无 `reason` 字段（字段名是 `error`），故 `detail` 永远为空串，失败原因/成功详情**全部丢失**，用户与审计都拿不到文本证据。
- **严重度**：**P1**（违反 D4 明文要求 + 用户铁律「验证后才汇报」；当前因 P1-1 闸门恒拒而**未实际发生误报**，一旦 P1-1 被修复即立刻暴露为 P0 级误报）
- **建议**：
  ① `_auto_dm` 改为**二段式**：`submit_by_uid` 只负责入池，投递成功判定必须拿到 `server_message_id`（走 `services/send_response.delivery_verdict()` → `services.delivery_verify.mark_delivery_verified()`），`delivery_verified` 只能由该证据置真；
  ② 若调度器不适合等待异步回执，则**明确降级 API 语义**：`delivery_verified` 恒为 `False`，改返回 `queued=True` + `task_id`，由 `_run_one`/UI 明示「已入池，待回执」，**不得**标 `ok=True`；
  ③ `detail` 改读 `res.error`。
- **门禁缺口**：`test_task_scheduler_gates.py:96` G6 断言 `"delivery_verified" in s and "无投递验证回执" in s` —— **字符串匹配在「执行体自填 delivery_verified」的实现下依然全绿**。这正是该门禁**假绿**的实证。**建议 G6 改为断言 `_auto_dm` 源码中不存在 `delivery_verified: ok` 字面绑定**，且断言 `from services import delivery_verify`（或 `send_response`）真实出现。

### ✅ 已核查并符合的接线要素

| # | 核查点 | 读数 | 结论 |
|---|---|---|---|
| 6.1 | 外发走既有 `dm_dispatch` 而非自造 HTTP | L509 `dm_dispatch.get_dispatcher().submit_by_uid(task.account, uid, text, "scheduler")` | ✅ 符合铁律 |
| 6.2 | 走 `submit_by_uid`（陌生人首发语义）而非 `submit` | `dm_dispatch.py:1141` 强制 `is_stranger_first=True`，进 2/分钟·30/天 限流 | ✅ 正确选型 |
| 6.3 | 未绕过 `dm_dispatch` 内部风控 | `submit_by_uid` 内：本账号校验（L1150）、跨账号沉淀池（L1170）、UID 沉淀池（L1179）、队列容量（L1196）、额度预占（L1203-1208）**全部保留** | ✅ 无旁路 |
| 6.4 | 未触碰昵称红线（D7） | 全模块无 `bulk_user_info` / `get_im_user_info` 调用 | ✅ |
| 6.5 | 与 `ai_reply` 无耦合 | `grep ai_reply` 在 task_scheduler.py 无命中 | ✅ 职责分离 |
| 6.6 | `_handlers` 无并发写风险 | `register_handler` L188 持 `_lock` 写 | ✅ |

---

## 7. 存量扫描维度（不做 diff 视角的补充审计）

> 铁律一·丙要求：审计必须含「存量扫描」维度，不能只审新增 diff。

**扫描判据**：全仓检索「投递验证」相关符号，确认**新增的 task_scheduler 是否构成既有 M-5 机制的第 N 个接入点**，以及是否存在「同一语义两个名字」的存量债。

```bash
cd DYAutoDM_v2/backend
grep -rn "delivery_verified" --include=*.py .          # 出现处
grep -rn "mark_delivery_verified\|delivery_verdict" --include=*.py .   # M-5 唯一出口的实际调用者
```

**读数**：`delivery_verified` 由 `task_scheduler.py`（L304/305/514）与 `delivery_verify.py` 共同使用；
而 `mark_delivery_verified` / `delivery_verdict` 的**真实调用者**中**不含 `task_scheduler.py`**。
⇒ 存量结论：**F4 是本项目 M-5 投递验证链路上第一个「自定义 `delivery_verified` 同名字段、却不接唯一判定出口」的接入点**。
这是「同一语义两个名字」的存量债新形态（合法字段名 + 非法来源），**在纯 diff 审计中不可见**，只有做「M-5 唯一出口的调用者清单」这一存量扫描才能发现。**建议把它立为常设判据**：凡出现 `delivery_verified` 字面，必须能追到 `delivery_verify` 的调用。

---

## 8. 机读错误报告

```json
[
  {"design_intent":"ADR-018 D4：自动外发执行前必须依次通过 额度/间隔/时段 三重闸门（复用 dm_dispatch 既有仲裁）",
   "observed_deviation":"_gate_for_send 以二元组解包 can_send() 的三元组返回值 (ok, reason, wait)，抛 ValueError 并被 except 吞掉，恒返回 fail-closed；闸门从未真正生效",
   "error_code":"SCHED-D4-GATE-UNPACK",
   "severity":"error",
   "file":"backend/services/task_scheduler.py:246",
   "suggested_actions":["改为 ok, why, _wait = quota.can_send()","补行为级门禁用例：桩 AccountQuota 断言 _gate_for_send 返回 (True,'')"]},

  {"design_intent":"调度器同一任务不得并发执行（防同 uid 重复投递放大风控敞口）",
   "observed_deviation":"_tick 早退 return 位于 try 内使 finally 仍触发 _schedule()；_running 无条件回写。实测 3 路并发 _tick → 同一任务并发 2 次",
   "error_code":"SCHED-TICK-REENTRANT",
   "severity":"error",
   "file":"backend/services/task_scheduler.py:317-322,358-361",
   "suggested_actions":["守卫移出 try 或改非阻塞 acquire","_running 由持有者置位/复位（记录 thread ident）","_schedule 仅在本轮真正执行完时调用","补并发门禁"]},

  {"design_intent":"任何执行入口都不得使同一任务重叠执行",
   "observed_deviation":"run_task_now 无 per-task 锁，与 _tick 及自身并发；实测 4 路并发 → 同一任务并发 4 次",
   "error_code":"SCHED-RUNNOW-NO-LOCK",
   "severity":"error",
   "file":"backend/services/task_scheduler.py:438-454",
   "suggested_actions":["Task 增 _exec_lock，_run_one 统一非阻塞 acquire","run_task_now 与 _tick 共用同锁"]},

  {"design_intent":"ADR-018 D4 + 用户铁律：每条外发走 M-5 投递验证，无回执不认成功",
   "observed_deviation":"_auto_dm 把 SubmitResult.accepted（本地已入池）直接当 delivery_verified，且全程不调用 services.delivery_verify / delivery_verdict；detail 读不存在的 .reason 字段恒为空",
   "error_code":"SCHED-M5-BYPASS",
   "severity":"error",
   "file":"backend/services/task_scheduler.py:500-516",
   "suggested_actions":["接入 delivery_verdict→mark_delivery_verified 作为唯一证据出口","或在未取到 server_message_id 前令 delivery_verified 恒 False 并改用 queued 语义","detail 改读 res.error","G6 门禁改为断言不存在 delivery_verified: ok 字面绑定"]},

  {"design_intent":"任务定义与状态应可持久化，重启不丢",
   "observed_deviation":"_tasks 为纯内存 dict，全模块持久化操作 0；重启任务配置全丢",
   "error_code":"SCHED-NO-PERSIST",
   "severity":"warning",
   "file":"backend/services/task_scheduler.py:166-176",
   "suggested_actions":["任务定义落 SQLite/settings（复用既有持久化层）","start() 时 load_tasks()"]},

  {"design_intent":"超时任务应真正停止或至少不可再次触发",
   "observed_deviation":"join 超时仅放弃等待，线程无法终止仍在跑；实测超时后线程存活",
   "error_code":"SCHED-TIMEOUT-ZOMBIE",
   "severity":"warning",
   "file":"backend/services/task_scheduler.py:287-295",
   "suggested_actions":["超时任务登记进行中并阻止再次触发","依赖 handler 自身可中断","get_state 暴露 running_tasks"]},

  {"design_intent":"F4 定时任务中心应作为可用能力建成（默认休眠、用户开启即可用）",
   "observed_deviation":"register_builtin_handlers() 与 start() 在 main.py startup 无调用点，全仓 0 调用；内置 handler 未注册 ⇒ hot_comment_crawl/auto_dm_send 必返回「无注册执行体」，定时器不自启",
   "error_code":"SCHED-NOT-WIRED",
   "severity":"warning",
   "file":"backend/services/task_scheduler.py:478 / backend/main.py:577-584",
   "suggested_actions":["startup 中注册内置 handler 并调 start()（default 下 start 自会 fail-closed）","门禁断言 register_builtin_handlers 覆盖全部非预留 kind"]},

  {"design_intent":"执行审计须真实反映「跑了几次」",
   "observed_deviation":"skipped（停用/总开关未开/闸门拒绝）路径仍 run_count += 1；实测休眠态 run_count=1 而实际未执行",
   "error_code":"SCHED-AUDIT-OVERCOUNT",
   "severity":"warn",
   "file":"backend/services/task_scheduler.py:339,451",
   "suggested_actions":["仅非 skipped 才计 run_count","单列 skip_count / blocked_count"]},

  {"design_intent":"验收判据必须自证失败态会变红（D-07）",
   "observed_deviation":"test_task_scheduler_gates.py G1~G7 全为源码字符串正则匹配，对 GATE-UNPACK / M5-BYPASS 两类实质缺陷结构上无法报红（can_send() 与 delivery_verified 字面仍在）",
   "error_code":"SCHED-GATE-FALSE-GREEN",
   "severity":"warn",
   "file":"backend/test_task_scheduler_gates.py:53-105",
   "suggested_actions":["G3/G5/G6 改为行为断言（真调 _gate_for_send / 断言无自填 delivery_verified）","--selftest 增加并发与解包两类注入形态"]}
]
```

> 上表 9 条 JSON 中，前 8 条为**模块内代码缺陷**（P1-1~P3-1）；
> 第 9 条 `SCHED-GATE-FALSE-GREEN` 为**门禁自身的结构性盲区**（元发现，不计入「代码缺陷」计数）。
> 故「发现条数」口径 = **8 条代码缺陷** + 1 条门禁元发现。

---

## 9. 自检

- [x] 审计区间可一句话说清：`074eb37`（v0.45.41，ADR-018 落地提交）中的 `task_scheduler.py` 及其接线。
- [x] 立显式维度清单，含**存量扫描**维度（第 7 节：M-5 唯一出口的调用者清单）。
- [x] 每条结论均挂可复跑命令或实跑输出，无裸形容词。
- [x] 三层（方向/机制/结构）分别给结论，且**同时含符合项与违反项**（第 1、2、3、5、6 节的 ✅ 表）。
- [x] 机读错误报告 schema 完整（design_intent / observed_deviation / error_code / severity / suggested_actions）。
- [x] 主动补的缺失维度：门禁**假绿**（G1~G7 正则匹配对实质缺陷无判别力）—— 该盲区在 ADR-018 验收判据「风控：自动外发默认关闭可被机械断言」中被默认已覆盖，实则未覆盖机制层。
- [x] 只读 + 并发声明已给；未改动工作区任何文件（`git status --porcelain` 对待审三文件为空）。
