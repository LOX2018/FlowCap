# 主链审计发现（本人亲审 · 2026-09-27）

> 审计区间：`c9db81e..HEAD`（14 个产品行为提交 / 119 文件 / +13389 −363）
> 本文件为**主链亲审**记录；三个并行子代理的独立报告见同目录
> `dim_task_scheduler.md` / `dim_p0_p4_verification.md` / `dim_frontend_components.md`。

---

## 维度① 技术债趋势 —— 🔴 继续恶化

| 文件 | 上次审计 | 本次 | 变化 |
|---|---|---|---|
| `backend/services/probe.py` | 1438 | **1438** | 0（已停止膨胀 ✓） |
| `backend/daemon/browser_daemon.py` | 1756 | **1757** | +1（基本持平） |
| `backend/daemon/recv_daemon.py` | 1566 | **1989** | 🔴 **+423** |
| `backend/auto_dm/conversation_capture.py` | ~1500 | **2142** | 🔴 **+642** |

**全仓规模**：672 个 `.py` / 269,934 行。

**判据**：上次审计点名的两个文件**已止住膨胀**（这是改善）；但**新的两个超大文件**
（`recv_daemon.py` 1989 / `conversation_capture.py` 2142）成为新的债务热点，
均**超过 1500 行的单文件阈值**。

**建议**：`conversation_capture.py`（2142 行）应拆分 —— 它同时承载
「消息解析 + 去重 + 归属判定 + 落库」，属 SoC 违例（叠加了多个变化原因）。

---

## 维度② 契约一致性 —— ✅ 全绿

```
check_contracts.py     → G0~G14 全 PASS
audit_data_contract.py → D-08 六项 6/6 PASS
check_version_sync.py  → 6 处齐平 0.45.49
check_iron_rules.py    → 13 项：12 PASS / 1 警告（R3 磁盘卫生）
```

---

## 维度③ 性能 —— ✅ 已从「纸面契约」升级为**真度量**

`scripts/check_nfr_budget.py`（590 行，本区间新增）**是真度量**：
`perf_counter` 实测 P50/P95/max，样本 200 + 预热 20，且对 6 项**诚实标注
NOT_MEASURABLE 并给出具体不可测原因**（不假装测了）。

```
NFR-06-1 mark_seen 写库延迟   P50 0.349 / P95 0.626 / max 1.001  (预算 5ms)   PASS
NFR-06-2 should_send 内存判定 P50 0.077 / P95 0.103 / max 0.238  (预算 1ms)   PASS
NFR-06-3 aggregate_text 追加  P50 0.262 / P95 0.396 / max 0.547  (预算 10ms)  PASS
```

⇒ **上次审计的第 3 维「纸面契约、0 度量资产」已被修复**。

---

## 🔴 主链亲审发现（3 条）

### [P1] `task_scheduler.Task.__init__` 默认 `enabled=True` —— 违反 D1「逐任务默认休眠」

**文件**：`backend/services/task_scheduler.py:135-137`

```python
def __init__(self, id: str, name: str, kind: str, account: str = "",
             params: Optional[dict] = None, interval: float = 3600.0,
             enabled: bool = True):        # ← 默认 True
```

**实测证据**（真机跑，非读码推断）：

```
TASK_KINDS 全表:
  keyword_process    needs_send=False
  hot_comment_crawl  needs_send=False
  auto_dm_send       needs_send=True

新建各类任务的默认 enabled:
  keyword_process    enabled=True  needs_send=False
  hot_comment_crawl  enabled=True  needs_send=False
  auto_dm_send       enabled=True  needs_send=True   ← ⚠️ 新建即「已启用 + 会外发」
```

**风险链**（三条同时成立才触发，但都可达成）：
1. 用户按 ADR-018 说明设 `DY_TASK_SCHEDULER_ENABLED=1` 打开调度中心（**这是正常用法**）；
2. 通过 API 新建 `auto_dm_send` 任务，**不传 enabled**（前端若不给默认值即如此）；
3. ⇒ `enabled=True` + `needs_send=True` ⇒ **到点自动向陌生人发私信**。

**为何是问题**：ADR-018 D1 原文是「**能力建成就绪但默认休眠 enabled=False，
你要用再开 —— 与 T3 高价值筛查同款处理**」。这个「默认休眠」的语义在
**任务粒度**上被破坏：总开关是**粗粒度**的（一开全开），而任务级默认是「开」。
用户清空任务列表前，任何历史 `auto_dm_send` 任务都会在总开关打开的瞬间**全部复活**。

**用户已定红线**：「自动外发是本项目迄今最大风控敞口」、「宁可保守」。
⇒ 应改为 `enabled: bool = False`，**且**为 `auto_dm_send` 类任务在未显式
传参时**强制 False**（kind 感知的默认值）。

**严重度依据**：不阻断（仍有三重闸门 + `AUTO_SEND_ENABLED` 第二道开关），
但**违反设计文档明文约定**，且第二道开关（`DY_AUTO_SEND_ENABLED`）容易被
「只开调度中心」的用户忽略 ⇒ P1。

---

### [P2] `_tick` 用「本轮开始时刻」写 `last_run_at` ⇒ 长任务跨轮重叠

**文件**：`backend/services/task_scheduler.py:324,337`

```python
now = time.time()                      # L324 本轮开始
...
for t in due:
    res = _run_one(t)                  # L335 可能耗时很久
    with _lock:
        t.last_run_at = now            # L337 ← 写的是「开始时刻」而非「完成时刻」
```

**问题**：`last_run_at` 语义应是「上次**执行**时间」。用开始时刻会让
「耗时 > interval」的任务在下轮被判定为**已到期**（`now - last_run_at >= interval`），
于是**同一任务在上一轮还没跑完时，下一轮又调度它** —— 对 `auto_dm_send`
即**重复发私信**。

**当前缓解**：`_running` 标志挡住「单轮内重入」，但**挡不住跨轮重叠**
（`finally` 里已把 `_running=False`，下一轮 tick 会正常进入）。

**判据**：`interval` 是用户可配的（`Task.interval`，默认 3600s）；若用户设成
60s 而任务实际跑 120s，必然重叠。

**建议**：`last_run_at = time.time()`（完成时刻）——
或在任务入口加「该任务是否还在跑」的 per-task 标志。

---

### [P3] `check_nfr_budget.py` 文案陈旧 —— 与已更新的契约**共存冲突**

**文件**：`scripts/check_nfr_budget.py:536`

```
NFR-06-2b ℹ 旁证：稳态单次调用发起 2 次 SQL
          契约 §5 L85 称「纯缓存读，零 DB 查」 —— 实测偏离，见报告 §5
```

**但契约 `docs/design-contracts/C-06-live-lead-sink.md` L85 现在写的是**：

```
| `should_send` 内存判定 | ≤ 1ms（**非**零 DB 查：窗口/阈值/冷却参数经 `cfg()`
  实时取配置，稳态每次 2 次 `kv_store` 读；实测 P50 0.073ms，仍 ≤ 1ms 预算）|
  ...`cfg()` 不引入缓存系刻意选择（风控参数须实时生效，见「显式配置原则」）|
```

⇒ 契约**已更新**并给出了设计理由（风控参数须实时生效，**这是用户的
「显式配置原则」要求的**）；而门禁文案**仍指向旧契约**，每次运行都报
「实测偏离」——**误导性告警**。

**违反**：Knowledge Freshness Law（陈旧规格不得与有效规格共存 / SSOT）。
**危害**：这类恒假的告警会让开发者**钝化对门禁输出的信任**
（本项目已有先例：「会被绕过的门禁比没有门禁更坏」）。

**建议**：更新门禁文案为当下契约的措辞（「契约已声明为非零 DB 查」），
保留度量值，去掉「实测偏离」的误导表述。
