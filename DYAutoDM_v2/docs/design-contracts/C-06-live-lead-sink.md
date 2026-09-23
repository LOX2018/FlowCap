# 设计契约 · C-06 弹幕沉淀池 / 高价值筛查 / 按人聚合（dm_uid_sink 扩）

> 关联 ADR：`docs/adr/ADR-007-live-lead-sink.md`（决策稿，2026-09-24 草案）。
> 本文件是该模块扩字段后的**唯一语义契约**（SSOT）。代码改动若与本文冲突，须先改本文。
> 来源：R8 核对（2026-09-24）实测 + ADR-007 决策稿 + 现有 `UidSink` 类（`services/dm_dispatch.py`）。

## 1. 设计意图

扩 `dm_uid_sink` 表（不新建平行表），在保留「同一 (account, peer_uid) 去重 + 7 天冷却」基础上，新增三项能力：
- **高价值筛查**：关键词权重初筛 + 可选 LLM 精判，仅高价值目标进入发送闸门；
- **沉淀窗口**：先收 N 分钟再统一发送，避免一见即发；
- **按人聚合**：同一 peer_uid 在窗口内的全部弹幕聚合后生成上下文，生成完即弃。

## 2. 设计契约（DbC）

### 2.1 前置条件（P）

| 编号 | 前置条件 | 违约后果 |
|---|---|---|
| P1 | `account`、`peer_uid` 非空且为合法字符串 | 直接拒绝，日志 `[SILENT-00]` |
| P2 | `dm_uid_sink` 表已按 §3 字段规范扩列（`keyword_score`、`is_high_value`、`high_value_reason`、`window_end_ts`、`aggregate_text`） | 启动时 `_migrate_schema` 自动执行；失败则拒绝所有 `mark_seen`/`mark_sent` |
| P3 | 配置中心已就绪 `send.uid_sink_cooldown`、`send.uid_sink_window_seconds`、`send.high_value_score_threshold`、`send.high_value_llm_enabled` | 缺失走默认值（见 §5），不抛错 |
| P4 | `high_value_keywords` kv 键存在（至少为 `{}`） | 缺失由 `services.high_value_keywords` 懒初始化写入 `{}` |
| P5 | 内存一级缓存 `_cache` 与 DB 一致（`_ensure_loaded` 首次调用成功） | 失败降级为纯内存去重（日志 `[SEND-025]`） |

### 2.2 后置条件（Q）

| 编号 | 后置条件 | 可观测证据 |
|---|---|---|
| Q1 | `mark_seen`：若 DB 无该 (account, peer_uid) → `INSERT` 新行，`first_seen_ts = now`，`window_end_ts = now + window_seconds`，`keyword_score = score_text(text)`，`aggregate_text = text`；若已存在 → `aggregate_text = COALESCE(aggregate_text, '') || ' ' || text`，`keyword_score = max(keyword_score, score_text(text))`，`window_end_ts = now + window_seconds`（续期） | `stats(account)` 返回 `total` + 1；`SELECT` 回读字段一致 |
| Q2 | `mark_sent`：成功写入 `sent_ts = now`，`send_count += 1`，`is_high_value` / `high_value_reason` / `aggregate_text` **不被清空**（保留审计）；内存缓存 `[(account, peer_uid)] = now` | `SELECT sent_ts FROM dm_uid_sink WHERE account=? AND peer_uid=?` 非空；内存 `_cache[key] == now` |
| Q3 | `should_send`：若 `now < window_end_ts` → 返回 `(False, "仍在聚合窗口")`；若 `now >= window_end_ts` 且未过冷却 → 按 `is_high_value` 过滤（`false` 且非严格模式放行）；若冷却期内已发送 → `(False, "UID 已发送过")` | 返回值 `(ok, reason)` 与判定日志 `[uid-sink]` 一致 |
| Q4 | 高价值判定（Phase 2+）：`keyword_score >= high_value_score_threshold` → `is_high_value = true`；若 `high_value_llm_enabled` → 异步 LLM 调用更新 `is_high_value` + `high_value_reason`（异步期间 `is_high_value` 暂由关键词决定） | 判后 `SELECT is_high_value FROM dm_uid_sink WHERE ...` 与判定日志一致；LLM 调用失败时 `[AI-004]` 日志 |
| Q5 | 聚合文本：`aggregate_text` 在 `mark_sent` 成功后**保留**（用于审计），但发送生成上下文时**独立快照**（每次 `should_send` 通过时复制，生成完即弃，不在 DB 变更） | 生成日志 `[agg-sink]` 含 peer_uid 与快照长度；`aggregate_text` 行数不变 |

### 2.3 不变式（I）

| 编号 | 不变式 | 违反代价 |
|---|---|---|
| I1 | `PRIMARY KEY (account, peer_uid)` 不变——per-account 维度保留（跨账号由 `dm_cross_sink` 负责） | 同一 peer_uid 被同一账号重复发送 |
| I2 | `mark_seen` **永远不写 `sent_ts`**——仅沉淀语义不变 | 沉淀态记录被误判为已发送，永久丢失发送机会 |
| I3 | 高价值判定是**过滤器**而非**发送触发器**——仍需通过冷却窗口 + 发送闸门（`cross_sink` + `uid_sink` + `quota.can_stranger_first`） | 高价值用户绕过限流直接发送 → 风控 |
| I4 | `aggregate_text` 在窗口内累积，窗口到期后冻结（不再追加）——发送后保留原值审计 | 窗口到期后继续累积导致无限增长 |
| I5 | 未开 AI（`high_value_llm_enabled=false`）时，`is_high_value` 仅由 `keyword_score` 决定——不阻塞 `should_send` | AI 抖动导致整个发送闸门停摆 |
| I6 | 幂等性：同一 (account, peer_uid) 多次 `mark_seen` → DB 只一行，`aggregate_text` 拼接；多次 `mark_sent` → `sent_ts` 取最后一次，`send_count` 递增 | 重复写入导致 `send_count` 虚高 / `aggregate_text` 重复 |
| I7 | 窗口边界：`window_end_ts = first_seen_ts + window_seconds`（初值），续期不超过 `now + window_seconds`（滑动上界）——即 `window_end_ts <= max(first_seen_ts, last_seen_ts) + window_seconds` | 活跃用户窗口永不收敛 → 一直不发 |
| I8 | 按人聚合的键定义：**始终用 `peer_uid`（字符串），绝不用昵称**——昵称可能重复/变更 | 按昵称聚合导致 UID 碰撞（不同人同昵称）或 UID 漂移（同人改昵称） |
| I9 | 上下文隔离：每次生成使用 `aggregate_text` 的独立快照（深拷贝），生成完即弃——不在 `UidSink` 内持有生成态引用 | 并发任务共享可变字符串 → 交叉污染 |
| I10 | `dm_uid_sink` 写入失败（`[SILENT-00]` / `[SEND-025]`）时，内存一级缓存仍生效（降级去重）——不因 DB 抖动丢失去重 | DB 抖动期间同一 UID 重复发送 |

## 3. 规范契约（字段命名 · Canonical Contract Law）

| 字段 | 类型 | 默认 | 写入点 | 说明 | 禁止的别名 |
|---|---|---|---|---|---|
| `account` | TEXT NOT NULL | — | `mark_seen` / `mark_sent` | 发送账号 | `acct` / `uid` |
| `peer_uid` | TEXT NOT NULL | — | `mark_seen` / `mark_sent` | 对方 UID（字符串） | `uid` / `user_id` |
| `nickname` | TEXT | `''` | `mark_seen`（可选） | 昵称（被动来源） | `nick` / `name` |
| `source` | TEXT | `''` | `mark_seen` | 来源：`live` / `crawl` / `manual` | `src` / `origin` |
| `first_seen_ts` | REAL NOT NULL | — | `mark_seen`（首次） | 首次见到该 UID 的时间戳 | `first_seen` / `first_ts` |
| `sent_ts` | REAL | `NULL` | `mark_sent` | 已发送时间（NULL=仅沉淀） | `send_time` / `sent_at` |
| `send_count` | INTEGER | `0` | `mark_sent` | 已发送次数 | `sent_count` |
| `keyword_score` | INTEGER | `0` | `mark_seen`（Phase 2+） | 关键词权重累加 | `kw_score` / `score` |
| `is_high_value` | BOOLEAN | `0` | `mark_seen` / LLM 回调 | 是否高价值 | `high_value` / `hv` |
| `high_value_reason` | TEXT | `''` | LLM 回调（Phase 4+） | 判定理由摘要 | `reason` / `hv_reason` |
| `window_end_ts` | REAL | `NULL` | `mark_seen` | 聚合窗口到期时间 | `window_end` / `window_ts` |
| `aggregate_text` | TEXT | `''` | `mark_seen`（Phase 5+） | 窗口内累积弹幕文本 | `agg_text` / `context` |

## 4. 配置项规范（`app_config_schema.py` → `send` 段）

| 键 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `uid_sink_cooldown` | float | 604800.0（7 天） | 同 UID 冷却时间（秒） |
| `uid_sink_strict` | bool | true | 严格模式（冷却期内拦截） |
| `uid_sink_window_seconds` | float | 300.0（5 分钟） | 聚合窗口时长（秒） |
| `high_value_window_seconds` | float | 60.0（1 分钟） | 高价值用户加速窗口 |
| `high_value_score_threshold` | int | 10 | 关键词权重阈值（≥ 该值为高价值候选） |
| `high_value_llm_enabled` | bool | false | 是否启用 LLM 精判 |
| `aggregate_max_chars` | int | 2000 | `aggregate_text` 最大字符数（截断上界） |

## 5. NFR（性能 / 隔离预算）

| 指标 | 预算 | 依据 |
|---|---|---|
| `mark_seen` 写库延迟 | ≤ 5ms（单次 INSERT/UPDATE） | SQLite WAL + 30s busy_timeout |
| `should_send` 内存判定 | ≤ 1ms（纯缓存读，零 DB 查） | 内存一级缓存 `_cache` 优先 |
| `aggregate_text` 单次追加 | ≤ 10ms（文本拼接 ≤ 2000 字符） | `aggregate_max_chars` 截断 |
| 窗口到期扫描 | ≤ 50ms（每 30s 扫一次，命中 ≤ 1000 行） | `idx_uid_sink_ts` 覆盖 `window_end_ts`（Phase 1 新增索引） |
| LLM 精判 | 异步，不阻塞 `should_send` | 高价值候选暂由关键词判定；LLM 异步回调更新 |
| 内存缓存 | ≤ 10MB（100k UID × ~100 字节/条） | `_cache: Dict[(str,str), float]` |
| `dm_uid_sink` 行数上限 | 无硬上限；建议定期归档 `sent_ts < now - 30天` 的行 | 归档策略待实施时定 |

## 6. 验证方式（可机械判定）

```bash
# 契约守护：字段规范核对
grep -n "ALTER TABLE dm_uid_sink ADD COLUMN" backend/database.py
# 期望：4 条新列（keyword_score / is_high_value / high_value_reason / window_end_ts / aggregate_text）

# 契约守护：DbC 不变式核对
grep -n "is_high_value" backend/services/dm_dispatch.py | head
# 期望：should_send 中有 is_high_value 过滤逻辑

# 契约守护：高价值判定路径（关键词 → LLM 可选）
grep -n "score_text\|high_value_score_threshold\|high_value_llm_enabled" backend/services/*.py
# 期望：services/high_value_keywords.py 有 score_text；dm_dispatch.py 有阈值判断；ai_reply.py 有 LLM 调用

# 契约守护：窗口逻辑
grep -n "window_end_ts\|uid_sink_window_seconds" backend/services/dm_dispatch.py
# 期望：mark_seen 设 window_end_ts；should_send 比较 now >= window_end_ts

# 契约守护：按人聚合（uid 键，不用昵称）
grep -n "aggregate_text\|peer_uid" backend/services/dm_dispatch.py | head
# 期望：聚合键为 peer_uid；aggregate_text 在 mark_sent 后保留

# 契约守护：未开 AI 退化路径
grep -n "high_value_llm_enabled\|keyword_score" backend/services/dm_dispatch.py
# 期望：high_value_llm_enabled=false 时 is_high_value 仅由 keyword_score 决定

# 单元测试（独立脚本，不跑全量 discover）
py314 -m unittest backend/test_uid_sink_ext.py   # 新增验证脚本（Phase 2+）
```

## 7. 已知缺口（诚实记录）

- **容量**：`dm_uid_sink` 当前行数 / 索引选择性未在真实生产库上实测（门禁禁止写 `C:\temp\dyautodm_design`），NFR 预算基于代码静态分析 + SQLite 通用经验。
- **跨账号 + 高价值冲突**：`dm_cross_sink`（跨账号去重）与 `is_high_value`（高价值过滤）的执行顺序与优先级未在本契约拍板——属 ADR-007 §7 提到的相邻待办，建议后续补充。
- **归档策略**：`dm_uid_sink` 长期膨胀（`aggregate_text` 累积）的归档/清理机制未设计，属后续迭代。
- **前端交互**：`high_value_keywords` 权重表的前端配置页 / `is_high_value` 标记的 UI 展示未在本契约定义——走统一配置中心 `UnifiedConfigSection`，属实施层。
- **LLM 精判的延迟与一致性**：异步回调更新 `is_high_value` 期间，同一 UID 可能已被关键词判定放行/拒绝；最终一致性窗口未量化——Phase 4 实施时需补充边界契约。