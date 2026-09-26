# T2 · NFR 预算度量脚本（机制建议 B）

> 对应全库审计 §2·4「纸面契约」：5 份设计契约都写了 NFR 预算表，但**全仓零性能度量资产** ⇒ 预算不可验证。
> 本次交付把 C-06 §5 的预算从「纸面」变成「可跑、可读数、会变红」的最小度量。
> 日期：2026-09-27　分支：`design/better-douyin`（未提交）　Python：`C:/Users/LOX/AppData/Local/Programs/Python/Python314/python.exe`

---

## ① 脚本路径

```
C:\Users\LOX\Desktop\DYchajian\DYAutoDM_v2\scripts\check_nfr_budget.py
```

唯一新增文件。`git status` 显示本任务只触碰了这一个文件
（`scripts/check_iron_rules.py` 的 M 来自父会话并行改动，非本任务；未做任何 `git add` / `commit`）。

用法：

```bash
python scripts/check_nfr_budget.py                  # 度量，超预算 exit 1
python scripts/check_nfr_budget.py --json           # 机器可读（CI）
python scripts/check_nfr_budget.py --selftest       # D-07 负控（预期 exit 1）
python scripts/check_nfr_budget.py -n 200 --warmup 20
```

退出码语义：`0`=全部在预算内；`1`=存在超预算项（**含** `--selftest` 的预期红色读数）；
`2`=负控不成立（注入后没变红或还原后没回绿）；`3`=隔离守卫触发（拒跑）。

---

## ② 覆盖的 NFR 项（契约文件 + 行号）

| 编号 | NFR 项 | 预算 | 契约出处 | 是否真的被度量 |
|---|---|---|---|---|
| **NFR-06-1** | `UidSink.mark_seen` 写库延迟（单次 INSERT/UPDATE） | ≤ 5ms | `docs/design-contracts/C-06-live-lead-sink.md` **§5 L84** | ✅ 实测 200 样本 |
| **NFR-06-2** | `UidSink.should_send` 内存判定 | ≤ 1ms（纯缓存读，零 DB 查） | `docs/design-contracts/C-06-live-lead-sink.md` **§5 L85** | ✅ 实测 200 样本 |
| **NFR-06-3** | `aggregate_text` 单次追加 | ≤ 10ms（文本拼接 ≤ 2000 字符） | `docs/design-contracts/C-06-live-lead-sink.md` **§5 L86** | ✅ 实测 200 样本 |
| NFR-06-2b | 「零 DB 查」旁证（每次调用发起的 SQL 数） | — | 同上 L85（预算的**依据**栏） | ✅ 旁证（见 §5 偏离） |

被测符号（先 grep 确认、未凭空设计）：`backend/services/dm_dispatch.py`
`UidSink.should_send`（L701）/ `mark_seen`（L765）/ `get_aggregate`（L856）。

---

## ③ A / B 的真实命令与实际读数

### A. 正常度量

```bash
cd C:\Users\LOX\Desktop\DYchajian\DYAutoDM_v2
C:/Users/LOX/AppData/Local/Programs/Python/Python314/python.exe scripts/check_nfr_budget.py
```
**exit 0**（三次连续复跑均为 0）

```
==========================================================================
  NFR 预算度量 —— C-06 §5（NFR 从「纸面契约」到可读数）
==========================================================================
  隔离沙箱: C:\Users\LOX\AppData\Local\Temp\nfr_budget_m_y6mchc
  度量库  : C:\Users\LOX\AppData\Local\Temp\nfr_budget_m_y6mchc\data\dyautodm.db
  样本/项 : 200（另有 20 次预热不计入）
--------------------------------------------------------------------------
  编号              P50      P95      max      预算  结论
  NFR-06-1      0.315    0.538    0.866     5.0  PASS  mark_seen 写库延迟（单次 INSERT/UPDATE）
  NFR-06-2      0.073    0.097    0.457     1.0  PASS  should_send 内存判定
  NFR-06-3      0.240    0.333    0.472    10.0  PASS  aggregate_text 单次追加
  NFR-06-2b         -        -        -       -  ℹ 旁证：稳态单次调用发起 2 次 SQL（契约 §5 L85 称「纯缓存读，零 DB 查」 —— 实测偏离，见报告 §5）
             SQL> SELECT value FROM kv_store WHERE key='app_config'
             SQL> SELECT value FROM kv_store WHERE key='app_config'
--------------------------------------------------------------------------
  单位 ms（perf_counter 实测）｜受预算约束项 3 个：通过 3 / 失败 0；旁证项 1 个（不计通过/失败）
```

复跑稳定性（`-n 100 --warmup 20` 连跑 3 次，单位 ms）：

| 项 | run1 P50/P95/max | run2 | run3 |
|---|---|---|---|
| NFR-06-1 | 0.306 / 0.460 / 0.686 | 0.336 / 0.497 / 0.669 | 0.337 / 0.544 / 0.818 |
| NFR-06-2 | 0.074 / 0.084 / 0.118 | 0.072 / 0.085 / 0.096 | 0.072 / 0.095 / 0.133 |
| NFR-06-3 | 0.273 / 0.545 / 0.858 | 0.258 / 0.386 / 0.568 | 0.268 / 0.414 / 0.619 |

三次均 PASS，读数离散度可接受 → 不是「刚好卡线通过」，预算有余量。

### B. D-07 负控

```bash
C:/Users/LOX/AppData/Local/Programs/Python/Python314/python.exe scripts/check_nfr_budget.py --selftest
```
**exit 1**（注入后变红的**预期**红色读数；若负控自身不成立则为 exit 2）

```
==========================================================================
  NFR 预算度量 —— D-07 负控（注入慢路径，验证「真的会变红」）
==========================================================================
  隔离沙箱: C:\Users\LOX\AppData\Local\Temp\nfr_budget_8osm1cvd
--------------------------------------------------------------------------
  NFR-06-1  mark_seen 写库延迟（单次 INSERT/UPDATE）
    预算 ≤ 5.0ms   注入 +20.0ms/钩子调用
    [注入后] P50=21.099ms P95=21.495ms max=47.57ms -> RED ✔
    [还原后] P50=0.289ms P95=0.412ms max=0.727ms -> PASS ✔
--------------------------------------------------------------------------
  NFR-06-2  should_send 内存判定
    预算 ≤ 1.0ms   注入 +5.0ms/钩子调用
    [注入后] P50=11.142ms P95=11.479ms max=11.883ms -> RED ✔
    [还原后] P50=0.074ms P95=0.089ms max=0.109ms -> PASS ✔
--------------------------------------------------------------------------
  NFR-06-3  aggregate_text 单次追加
    预算 ≤ 10.0ms   注入 +20.0ms/钩子调用
    [注入后] P50=20.989ms P95=21.377ms max=21.733ms -> RED ✔
    [还原后] P50=0.232ms P95=0.291ms max=0.429ms -> PASS ✔
==========================================================================
  负控结论: 注入后变红 是 / 还原后回绿 是
  ✔ D-07 负控成立：超预算项确实会让门禁变红。
  ℹ 本模式 exit code = 1 —— 这是**预期**的红色读数，
    用于证明「失败态会变红」，不代表产品违反预算。
==========================================================================
```

**三项全部：注入后确实变红，还原后确实回绿。**

---

## ④ 生产库前后实测比对（硬验收 C）

文件：`C:\temp\dyautodm_design\members\m17db0f8209156f26\data\dyautodm.db`

| 时点 | md5 | size (bytes) | mtime |
|---|---|---|---|
| 跑之前 | `4f97288b066770c5dcc08670adf44313` | 1548288 | `1790363272` = 2026-09-26 03:07:52.679144400 +0800 |
| 跑之后（A+B 各跑多轮） | `4f97288b066770c5dcc08670adf44313` | 1548288 | `1790363272` = 2026-09-26 03:07:52.679144400 +0800 |

**md5 / size / mtime 三项完全一致 —— 生产库未被写。**

脚本自身在 `--json` 输出里也会打印 `prod_db_before` / `prod_db_after`，实测二者逐字段相同：

```
prod before {'size': 1548288, 'mtime': 1790363272.679, 'md5': '4f97288b066770c5dcc08670adf44313', 'exists': True}
prod after  {'size': 1548288, 'mtime': 1790363272.679, 'md5': '4f97288b066770c5dcc08670adf44313', 'exists': True}
```

### 隔离机制（为什么不可能写生产库）

1. **临时目录**：`tempfile.mkdtemp(prefix="nfr_budget_")`，在**导入 backend 任何模块之前**就把
   `DY_APP_ROOT` 指到它（隔离样式照抄 `backend/test_uid_sink_ext.py` 的模块级说明）；
   同时清空 `DY_MEMBER` / `DY_MEMBER_KEY`（会员上下文的盘上会话回退会把 `get_db()`
   重定向回生产库 —— 这是该项目实测踩过的坑，不清必中）。
2. **前置守卫 `_assert_sandbox()`**：度量**开始前**校验两件事 ——
   ① `database._db_path()` 落在沙箱内；② 真实连接的 `PRAGMA database_list` 落在沙箱内，
   且与生产库路径不等。任一条不成立抛 `SandboxViolation` → exit 3 **拒跑**（先证再跑，而非写完再道歉）。
3. **守卫本身被验证过会拦**（不是摆设）：把 `_db_path` 强制指向生产库后执行 ——

   ```
   [2] 强制指向生产库 —— 期望抛 SandboxViolation: OK ->拦截:
       database._db_path() 落在沙箱外: C:\temp\dyautodm_design\members\m17db0f8209156f26\data\dyautodm.db
       （沙箱=C:\Users\LOX\AppData\Local\Temp\nfr_budget_06j4ng5o）
   ```

### D. 临时目录已清理

```
leftovers=0        # ls -d $LOCALAPPDATA/Temp/nfr_budget_*
```
清理逻辑（`finally`）：先 `database.reset_connection()` 释放句柄再 `rmtree`；
并加了**重试到成功为止**（Windows 上 SQLite 句柄滞后释放会让单次 `rmtree` 静默失败、
留下空目录伪装成「已清理」），仍失败则显式打印残留路径而不是吞掉。

---

## ⑤ NOT_MEASURABLE 清单（离线测不了，不假装测了）

| 编号 | NFR 项 | 契约出处 | 为什么不能离线测 |
|---|---|---|---|
| NFR-06-4 | 窗口到期扫描 ≤ 50ms（每 30s 扫一次，命中 ≤1000 行） | C-06 §5 L87 | 全仓**没有**该扫描 routine。grep `window_end_ts` 只有 `mark_seen` 写 / `should_send` 读 / `window_remaining` 读三处，**无定时扫描器**可计时；索引 `idx_uid_sink_window`（`database.py` L308）已建但无调用方。去度量它等于自己造实现，读数无契约意义。⚠️ **这本身是契约 §7 已知缺口的新证据：预算给了一条不存在的路径。** |
| NFR-01-1 | 捕获延迟 ≤ 2s 落库 | C-01 §4 L40 | 输入是「前端响应到达」，依赖真实浏览器 WS 推送；离线无事件源，且本任务禁止启浏览器。 |
| NFR-01-2 | 主动请求数恒 0 | C-01 §4 L41 | 需在真实会话存续期内统计出站请求，依赖浏览器网络栈；`test_capability_probe` 已以静态形态覆盖，此处不重复。 |
| NFR-05-1 | 解析超时 `timeout=15s` | C-05 §5 L41 | 15s 是网络操作的超时**上限**而非预算耗时；真实耗时依赖直播间网页/WebSocket。 |
| NFR-03-1 | 校验耗时 `timeout=8s` | C-03 §4 L40 | 同上：8s 是超时上限；真实校验走浏览器 + 外网接口（本任务禁联网/禁浏览器）。 |
| NFR-06-5 | 内存缓存 ≤ 10MB（100k UID） | C-06 §5 L89 | 需真实 100k UID 规模才有效；本脚本只跑几百条样本（且刻意保持小规模以便彻底清理），观测值无意义。 |

清单在脚本内以 `NOT_MEASURABLE` 常量硬编码，两种输出模式（人类表 / `--json`）都会打印 —— 不会被静默略过。

---

## ⑥ 负控怎么构造的（D-07）

**原则：慢路径只做注入，绝不改产品源码**（`backend/` 下业务模块零改动，符合 H-22 冻结期约束）。

注入点选择的是**被测调用真实经过的路径内的钩子**（不是给计时函数打补丁，那种负控是自欺）：

| NFR 项 | 注入的钩子 | 为什么是这个钩子 | 注入量 | 实测注入后读数 |
|---|---|---|---|---|
| NFR-06-1 `mark_seen` | monkeypatch `services.high_value_keywords.score_text` | `mark_seen` L788-792 真的会 import 并调用 `_hv.score_text(text)` 打分 | +20ms/次 | P50 21.099ms → 超 5ms 预算 |
| NFR-06-2 `should_send` | monkeypatch `services.dm_dispatch.cfg` | `should_send` L727-728 真的会调 `cfg("UID_SINK_WINDOW")` / `cfg("HIGH_VALUE_THRESHOLD")` 等；单次调用走 cfg 多次 ⇒ 注入被放大到必然超 1ms | +5ms/次 | P50 11.142ms → 超 1ms 预算 |
| NFR-06-3 聚合追加 | 同 NFR-06-1（`score_text`） | 追加走同一 `mark_seen` 路径 | +20ms/次 | P50 20.989ms → 超 10ms 预算 |

实现是 `@contextmanager`：**进入时替换钩子、`finally` 里还原**（`hvk.score_text = orig` / `dd.cfg = orig`），
所以「还原」不依赖调用方记得调用清理 —— 失败路径也保证还原。

判据是**双向**的（只验「变红」是不够的，还得验「还原后回绿」，否则可能永远红着）：

- 注入后：`P95 > 预算 或 max > 预算` ⇒ 必须是 `FAIL`（变红）；
- 还原后：必须回到 `PASS`（回绿）；
- 只有两项都成立，`selftest_ok = True`；任何一项不成立 ⇒ exit 2 并打印
  「✘ 负控不成立：该度量不可信 —— 禁止用它做放行判据」。

### 构造过程中实测踩到并修掉的两个坑（诚实记录）

1. **`max` 判据对抖动过敏**：初版负控跑出 `[还原后] P50=0.239 P95=0.378 max=5.373 -> 未回绿 ✘`
   —— P50/P95 明明正常，单个 `max` 尖刺（5.373ms，GC/调度抖动）就让「还原」判定失败。
   若照搬来做人肉 pipeline 也会偶发假红。
   **修法**：补上真正生效的 warmup 路径（抽成统一的 `_sample(factory, n, warmup, offset)`，
   预热与样本走同一 factory、用不重叠的 uid 段），让冷启动抖动不进样本。修后 3/3 项稳定回绿。
2. **`sqlite3.Connection.execute` 是只读属性**：初版用 monkeypatch 连接来统计 SQL 数，
   直接 `AttributeError: object attribute 'execute' is read-only`。
   **改用**标准手段 `set_trace_callback`（sqlite3 自带语句级探针，比替换方法更贴近真实执行）。

---

## ⑦ 顺带发现：一处**契约与实现的真实偏离**（建议回写 C-06）

`C-06 §5 L85` 写 `should_send` 的预算是 **≤ 1ms，依据「纯缓存读，零 DB 查」**。

用 `set_trace_callback` 实测**稳态**（已过 `_ensure_loaded` 预热）单次 `should_send`：

```
db_queries_per_call = 2
SQL> SELECT value FROM kv_store WHERE key='app_config'
SQL> SELECT value FROM kv_store WHERE key='app_config'
```

即：**每次调用读两次 kv_store**（`app_config.get()` 无内存副本，`_load()` 每次都重新查库）。

判读：

- **延迟预算本身仍达标**（P50 0.073ms ≤ 1ms），所以不判 FAIL —— 脚本把它列为 `INFO` 旁证项，不计入通过/失败。
- 但**预算的「依据」栏是错的**：它不是「零 DB 查」，而是「2 次 kv 全表点查（走了 hit 缓存不足→每次回库）」。
- 影响面：这是每个 UID 每条弹幕都会过一遍的热路径，kv 查询虽轻（SQLite 点查），但**随 `dm_uid_sink` /
  `kv_store` 增大，P95 会漂移**；一旦 kv_store 膨胀或并发写竞争触发 busy_timeout，`should_send` 会从
  0.07ms 掉到 ms 级 —— 那时 L85 的「零 DB 查」依据会让人误判是不可能的。
- **建议**（非本任务权限，属 WIP）：要么给 `app_config` 加带 TTL 的进程内缓存、把 L85 的依据坐实；
  要么把 C-06 §5 L85 的依据栏改成事实描述。**别改代码迁就契约，也别改契约掩盖代码。**

另发现（同样不建议本次改）：`NFR-06-4` 的「窗口到期扫描」预算对应**全仓不存在**的 routine，
索引 `idx_uid_sink_window` 已建但无消费者。这两条建议合并到契约修订待办。

---

## ⑧ 中文总结

**机制建议 B 已达成，四项验收判据全部实测通过。**

- **交付物**：唯一新增文件 `DYAutoDM_v2/scripts/check_nfr_budget.py`（约 580 行，含 `--json` 机器可读输出、
  退出码语义 0/1/2/3、`--selftest` 负控模式、`NOT_MEASURABLE` 诚实清单）。风格照抄
  `scripts/audit_data_contract.py` / `check_fingerprint_consistency.py`（argparse + `raise SystemExit(main())` 收尾、
  表格化 PASS/FAIL 输出），未自创写法。
- **A（正常度量）**：实测读数 NFR-06-1 P50 0.315/P95 0.538/max 0.866ms（预算 5ms），
  NFR-06-2 P50 0.073/P95 0.097/max 0.457ms（预算 1ms），NFR-06-3 P50 0.240/P95 0.333/max 0.472ms（预算 10ms）
  —— 三项全部 PASS，exit 0，连跑三次读数稳定。
- **B（负控）**：注入慢路径后三项**全部真的变红**（21.099 / 11.142 / 20.989ms），还原后**全部回绿**，
  exit 1 为预期的红色读数；负控结论「注入后变红 是 / 还原后回绿 是」。
- **C（生产库）**：`C:\temp\dyautodm_design\...\dyautodm.db` 前后 **md5 `4f97288b066770c5dcc08670adf44313`、
  size 1548288、mtime 1790363272 三项完全一致**；且隔离守卫本身被强制故障注入验证过会真的拦截（exit 3 拒跑）。
- **D（清理）**：临时沙箱残留 0 个。
- **红线遵守**：未 `git add` / `commit`（`git status` 仅本文件为 `??`）；未改 `backend/` 任何业务代码；
  未写生产库、未联网、未启浏览器、未杀进程；未切分支（`design/better-douyin`）。

超出「最小度量」范围、但本次实证顺带坐实的两项契约缺陷（**建议回写 C-06，未擅自改**）：

1. `C-06 §5 L85` 的「**零 DB 查**」依据与实现不符 —— 稳态每次 `should_send` 实际发起 **2 次**
   `SELECT ... FROM kv_store WHERE key='app_config'`。延迟仍达标（0.073ms），但依据栏失真，
   是 `app_config` 无进程内缓存导致的。
2. `C-06 §5 L87` 的「窗口到期扫描 ≤50ms」预算对应**全仓不存在**的 routine ——
   索引 `idx_uid_sink_window`（`database.py` L308）已建却无消费者，属「给不存在的路径定预算」。

一句话：预算从此**可读数、可回归、会变红**；同时第一次让契约里两条「看起来很专业」的 NFR 依据
暴露出与实现的偏离 —— 这正是「纸面契约」被照亮后的预期收益。
