# M-15 完整收口报告 · C-06 契约两处失真修订

分支：`design/better-douyin`（未切换、未 add/commit）｜版本：0.45.41（未改）｜日期：2026-09-27

---

## ① 两处改动的前后对照

### 改动 1（第①条 · should_send「零 DB 查」→ 订正契约文案，代码不动）

文件：`DYAutoDM_v2/docs/design-contracts/C-06-live-lead-sink.md` §5 NFR 表（原 L85）

**改前：**
```
| `should_send` 内存判定 | ≤ 1ms（纯缓存读，零 DB 查） | 内存一级缓存 `_cache` 优先 |
```

**改后：**
```
| `should_send` 内存判定 | ≤ 1ms（**非**零 DB 查：窗口/阈值/冷却参数经 `cfg()` 实时取配置，稳态每次 2 次 `kv_store` 读；实测 P50 0.073ms，仍 ≤ 1ms 预算） | 内存一级缓存 `_cache` 优先判定；`cfg()` 不引入缓存系刻意选择（风控参数须实时生效，见「显式配置原则」） |
```

要点：`≤ 1ms` 预算**原样保留**（实测达标，不是性能缺陷）；仅订正「依据」栏的失真描述。

### 改动 2（第②条 · 窗口到期扫描 → 从 NFR 表删行 + 就近注明）

**改前（原 L87）：**
```
| 窗口到期扫描 | ≤ 50ms（每 30s 扫一次，命中 ≤ 1000 行） | `idx_uid_sink_ts` 覆盖 `window_end_ts`（Phase 1 新增索引） |
```

**改后：** 该行删除；紧接 NFR 表下方新增说明段：
```
> **§5 说明（2026-09-27 M-15 修订）**：原「窗口到期扫描 ≤50ms（每 30s 扫一次，命中 ≤1000 行）」一行已**删除**——该能力**从未实现**：
> 索引 `idx_uid_sink_window`（`backend/database.py:308`）虽已建，但全仓无任何消费者（无到期扫描任务/无 `scan_expired` 类实现），
> 窗口到期实际由 `should_send` 在判定时**惰性比较** `now >= window_end_ts` 完成，不存在周期性扫描。
> 该行曾给一个不存在的能力写了性能预算，属契约失真，故删除而非保留；功能缺口另记于台账 §一·乙 T3「窗口到期扫描定时任务未做」。
> 若后续立项实现扫描器，须同时补回本预算行。
```

---

## ② 为什么选这两个方向

- **① 订正文案而非加缓存**：`cfg()` 加缓存会让风控参数（冷却/窗口/阈值/严格模式）变更不即时生效，与「显式配置原则」冲突，改动风险 > 收益；而延迟本就达标（P50 0.073ms ≤ 1ms），缺陷在描述而非实现 → 只订正契约。
- **② 删行而非实现扫描器**：该能力从未立项（台账 T3 已记「定时任务未做」），实现一个未立项的扫描器属擅自扩大范围；保留一条指向不存在能力的预算会误导后来人 → 删行 + 就近注明「为什么没有这行」并在将来立项时要求补回。

---

## ③ `check_contracts.py` 真实输出（改后）

```
============================================================
契约门禁 check_contracts
============================================================
  [PASS] G0 契约文件 ≥5                         实测 6 份
  [PASS] G1 C-01 捕获零主动查询                    0 命中
  [PASS] G2 C-02 secsdk 签名接线                0 命中
  [PASS] G3 C-03 dm 不冒充 wp                  0 命中
  [PASS] G4 C-04 投递有回执/落库验证                 存在回执处理
  [PASS] G5 C-05 reflow 主引擎存在               已实现
  [PASS] G6 C-02 签名自检                       1 条断言全真
  [PASS] G7 C-01 昵称源 SSOT                   NICKNAME_SOURCE='indexeddb:<uid>_user' 首选=['indexeddb']
  [PASS] G8 C-03 真实写校验                      probe_im_write 走 create_conversation 且分型只读态
  [PASS] G9 C-04 投递硬验证                      无证据=False 有msg_id=True 8610=False 标记role=me:True
  [PASS] G10 C-05 解密权合取                     契约 ['no_credential', 'not_logged_in', 'ok', 'uid_drift', 'unknown'] ⊆ 实现 [...]
  [PASS] G11 C-06 字段规范                      12 字段齐备
  [PASS] G12 C-06 符号守护                      §5 全部 grep 判据命中
  [PASS] G13 C-06 配置键                       7 键齐备
  [PASS] G14 C-01~C-06 单测可运行                5 个契约单测模块可运行
EXIT:0
```

G0~G14 全 PASS，退出码 0。门禁判据未受影响：G11/G13 读 §3/§4（未动），G12 读 §6 的 grep 判据（未动），§5 NFR 表不是任何门禁判据的输入源。

---

## ④ git status / diff 证明未碰产品代码

```
$ git status --porcelain
 M DYAutoDM_v2/docs/design-contracts/C-06-live-lead-sink.md
```

工作区**仅 1 个文件**变更，即契约文档；`backend/` 无任何改动。

完整 diff（仅 §5 两处 + 说明段，无其它行被牵动）：

```
@@ -82,13 +82,18 @@
 | 指标 | 预算 | 依据 |
 |---|---|---|
 | `mark_seen` 写库延迟 | ≤ 5ms（单次 INSERT/UPDATE） | SQLite WAL + 30s busy_timeout |
-| `should_send` 内存判定 | ≤ 1ms（纯缓存读，零 DB 查） | 内存一级缓存 `_cache` 优先 |
+| `should_send` 内存判定 | ≤ 1ms（**非**零 DB 查：... 实测 P50 0.073ms，仍 ≤ 1ms 预算） | 内存一级缓存 `_cache` 优先判定；`cfg()` 不引入缓存系刻意选择 ... |
 | `aggregate_text` 单次追加 | ≤ 10ms（文本拼接 ≤ 2000 字符） | `aggregate_max_chars` 截断 |
-| 窗口到期扫描 | ≤ 50ms（每 30s 扫一次，命中 ≤ 1000 行） | `idx_uid_sink_ts` 覆盖 `window_end_ts`（Phase 1 新增索引） |
 | LLM 精判 | 异步，不阻塞 `should_send` | 高价值候选暂由关键词判定；LLM 异步回调更新 |
 | 内存缓存 | ≤ 10MB（100k UID × ~100 字节/条） | `_cache: Dict[(str,str), float]` |
 | `dm_uid_sink` 行数上限 | 无硬上限；建议定期归档 `sent_ts < now - 30天` 的行 | 归档策略待实施时定 |
 
+> **§5 说明（2026-09-27 M-15 修订）**：…（5 行，见 ①）
+
 ## 6. 验证方式（可机械判定）
```

---

## 中文总结（是否达成）

**达成。** 两处契约失真已按既定口径各修一条：第①条订正 `should_send` NFR 行的依据栏为如实描述（保留 ≤1ms 预算不变），代码零改动；第②条删除「窗口到期扫描」NFR 行并在 §5 表下就近注明删除理由（索引 `idx_uid_sink_window` 已建但无消费者，缺口记于台账 T3）。改后 `python scripts/check_contracts.py` 实测 G0~G14 全 PASS（exit 0），`git status` 显示仅契约文档一处变更，`backend/` 产品代码、版本号均未触碰，未执行 add/commit。
