# ADR-007 · 弹幕沉淀池扩字段（高价值筛查 / 按人聚合）

|| 项 | 值 |
||---|---||
|| **状态** | **草案（待用户拍板）** |
|| **日期** | 2026-09-24 |
|| **决策者** | 用户（LOX） |
|| **来源** | 交接卡 DY-01 T3：P2 弹幕沉淀池 / 高价值筛查 / 按人聚合，三个未拍板决策 |
|| **影响面** | `dm_uid_sink` 表结构（`database.py`）+ `UidSink` 契约（`services/dm_dispatch.py`）+ 发送闸门调度 + 配置中心（`app_config_schema.py`） |

---

## 1. 背景

用户 2026-09-23 实测反馈提出三个 P2 需求，均围绕现有弹幕沉淀池 `dm_uid_sink`：

1. **高价值人群标签筛查**：从直播弹幕/视频采集中筛出「高价值」用户再发送私信，避免无差别骚扰。
2. **按人聚合**：同一个 peer_uid 在该人全部弹幕的基础上再生成/调度，而不是每条弹幕独立触发。
3. **沉淀窗口时长**：先收多久再统一发，而不是一见即发。

三个决策均未拍板。按项目铁律（架构变更须先落 ADR + 设计契约再动代码），本 ADR 先给出各选项分析、推荐项与理由，待用户确认后再进入实施。

## 2. 现状（实测，含绝对路径与行号）

### 2.1 `dm_uid_sink` 表结构

- 文件：`C:/Users/LOX/Desktop/DYchajian/FlowCap/backend/database.py`
- 建表 DDL：**第 218–227 行**
- 当前字段：
  - `account TEXT NOT NULL` —— 发送账号
  - `peer_uid TEXT NOT NULL` —— 对方 UID
  - `nickname TEXT DEFAULT ''` —— 昵称
  - `source TEXT DEFAULT ''` —— 来源（`live` | `crawl` | `manual`）
  - `first_seen_ts REAL NOT NULL` —— 首次见到该 UID 的时间戳
  - `sent_ts REAL` —— 已发送时间（NULL=仅沉淀未发）
  - `send_count INTEGER DEFAULT 0` —— 已发送次数
  - **主键**：`PRIMARY KEY (account, peer_uid)`
- 索引：**第 240 行** `CREATE INDEX IF NOT EXISTS idx_uid_sink_ts ON dm_uid_sink(sent_ts DESC)` —— 仅覆盖 `sent_ts`（已发送记录），**未覆盖 `first_seen_ts`**；`source`、`account` 无独立索引。
- 容量：表本身无上限；SQLite 单表行数上限由磁盘/索引碎片决定。当前生产库（`C:\temp\flowcap_design`）按门禁 **严禁写入**，故不做实测注入。从 `scripts/clear_convs.py` 清理清单看，它与 `dm_conversations`/`dm_messages`/`ai_leads` 同级，属常规业务表。

### 2.2 写入点

读取点与写入点均在 `services/dm_dispatch.py` 的 `UidSink` 类：

| 方法 | 文件 | 行号 | 职责 |
|---|---|---|---|
| `should_send` | `dm_dispatch.py` | **671–690** | 查缓存/库，冷却期内拒绝 |
| `mark_sent` | `dm_dispatch.py` | **692–714** | 发送成功后落库（写 `sent_ts`，`send_count++`） |
| `mark_seen` | `dm_dispatch.py` | **716–731** | 仅沉淀（`INSERT OR IGNORE`，不写 `sent_ts`） |
| `stats` | `dm_dispatch.py` | **733–748** | 按账号 / 全局统计总量与已发送量 |

发送确认写库：**`dm_dispatch.py` 1207–1213 行**（`_send_one` 回调，成功时调 `uid_sink.mark_sent` + `cross_sink.mark_sent`）。

### 2.3 当前承载职责

- **去重**：同一 (account, peer_uid) 只保留一次有效记录，后续同 UID 弹幕被丢弃。
- **冷却**：默认 7 天（`_FALLBACK_UID_SINK_COOLDOWN`，环境变量 `DY_UID_SINK_COOLDOWN`；配置中心 `send.uid_sink_cooldown`，`app_config_schema.py:320–325`）。
- **跨来源共享**：直播监听 + 视频采集 + 手动录入共用同一张表。
- **持久化**：进程重启不丢（内存一级缓存 `_cache` 由 `_ensure_loaded` 从库复原）。

### 2.4 已有跨账号沉淀池

- `dm_cross_sink`：`database.py:231–239`，`peer_uid` 全局唯一（与 `dm_uid_sink` 并存，由 `sink_global_scope` 开关选路）。
- `CrossAccountSink` 类：`dm_dispatch.py:541–604`，2026-09-22 ADR-002 §5.5(B) 引入。
- 发送闸门选路：**`dm_dispatch.py` 1033–1048 行**（`sink_global_scope=true` 先查跨账号，再查 per-account）。

### 2.5 与高价值/聚合/窗口的差距

| 需求 | 现状 | 差距 |
|---|---|---|
| 高价值标签 | 无字段、无判定逻辑 | 需扩展字段 + 判定入口 |
| 按人聚合 | 每行一条 (account, peer_uid)，无「该人全部弹幕」聚合 | 需在 `first_seen_ts` 与 `sent_ts` 之间保留聚合窗口 |
| 沉淀窗口 | 冷却在发送后生效，发送前无窗口 | 需引入「沉淀期」概念（先收再发） |

## 3. 决策

### D1：高价值标签方案

| 选项 | 描述 | 成本 | 风险 | 可逆性 |
|---|---|---|---|---|
| **A. 可配关键词权重表** | 在 `dm_uid_sink` 新增 `keyword_score`（默认 0），并引入「高价值关键词白名单 + 权重表」（走 `kv_store` 或新表 `high_value_keywords`）；弹幕文本命中关键词则累加 `keyword_score`，超过阈值 `HIGH_VALUE_SCORE_THRESHOLD` 标记为高价值。 | 低：仅加 1 列 + 1 个新 kv/小表；判定逻辑纯内存字符串匹配，零 LLM 依赖。 | ① 关键词误命（如「工伤」出现在无关上下文）→ 权重表需维护排除词；② 语义盲区（「赔我钱」不在词表）→ 覆盖率依赖人工维护。 | **高**：删列/阈值即回退；数据不丢失。 |
| **B. LLM 现判** | 每条弹幕（或每个 peer_uid 聚合后）送 LLM 判定是否高价值，返回结构化标签（如 `high_value: true` + 理由）。 | 高：每条弹幕耗 token；延迟不可控（LLM 响应时间）；依赖 LLM 可用性；需新增批量/异步调用链路。 | ① 成本不可控（弹幕量大时 token 爆炸）；② LLM 漂移/幻觉导致判定不一致；③ 未开 AI 时整条链路退化。 | **中**：需清理已写入的 LLM 标签列；判定结果不可完全复现。 |
| **C. 混合（关键词初筛 + LLM 精判）** | 关键词权重做粗筛（低成本过滤 80% 噪声），仅对超过初筛阈值的候选送 LLM 精判；最终标签由 LLM 输出覆盖。 | 中：需两套逻辑共存 + 协调阈值；复杂度高于 A 但远低于纯 B。 | ① 两个阈值需调参；② LLM 精判仍受 B 的风险影响；③ 关键词初筛的漏判无法被 LLM 补回。 | **高**：关掉 LLM 精判即退化为 A。 |

**推荐项：C（混合）**。理由：
- 纯 A 语义盲区明显（工伤咨询场景下，用户措辞高度变体，纯关键词漏判率高）；
- 纯 B 成本与延迟在弹幕量级下不可接受（一个直播间同时几百条弹幕，全送 LLM 不现实）；
- C 用关键词做「降噪漏斗」，把 LLM 调用量压到 5–20%，既保语义覆盖又控成本；
- 未开 AI 时自动退化为纯 A（见 D3），**零功能阻塞**；
- 可逆性最高：任一阶段都可降级，数据不丢。

实施建议：
- `dm_uid_sink` 新增 `keyword_score INTEGER DEFAULT 0`、`is_high_value BOOLEAN DEFAULT 0`、`high_value_reason TEXT DEFAULT ''`（可选，存 LLM 理由摘要）。
- 新增 `high_value_keywords` kv（或 `kv_store` 键 `high_value_keywords`），结构 `{word: weight}`，前端可配置。
- 新增配置 `send.high_value_score_threshold`（默认 10）、`send.high_value_llm_enabled`（默认 false，等用户 AI 配置就绪再开）。

### D2：沉淀窗口时长

| 选项 | 描述 | 成本 | 风险 | 可逆性 |
|---|---|---|---|---|
| **A. 固定窗口（如 5 分钟）** | 每个 peer_uid 从 `first_seen_ts` 起算，固定时长内只沉淀不发送，到期后一次性聚合发送。 | 低：加 `window_end_ts` 列 + 定时扫描。 | ① 固定窗口 vs 直播实时性矛盾（用户可能已流失）；② 窗口到期后批量发送，可能触发平台频控。 | **高**：改阈值即生效。 |
| **B. 滑动窗口 + 动态收敛** | 同一 peer_uid 在滑动窗口内（如 5 分钟）的弹幕合并为一条摘要，窗口续期；有新弹幕则窗口重置。 | 中：需维护 per-uid 窗口状态 + 摘要生成逻辑。 | ① 活跃用户窗口永不收敛 → 一直不发；② 实现复杂度高。 | **中**：需回退到固定窗口逻辑。 |
| **C. 固定窗口 + 热度加速** | 默认固定窗口（如 5 分钟），但对高价值用户（D1 判定）缩短窗口（如 1 分钟）甚至即时发送。 | 中：需 D1 判定先就绪；两套窗口逻辑。 | 依赖 D1；若 D1 未就绪则退化为 A。 | **高**：关热度加速即回 A。 |

**推荐项：C（固定窗口 + 热度加速）**。理由：
- 纯 A 简单但对高价值用户太慢（他们可能下一秒就下播）；
- 纯 B 工程风险大（活跃用户永不发送是灾难）；
- C 在保底（固定窗口）之上给高价值用户加速，风险可控；
- 与 D1 天然耦合，先做 D1 再接 D2 即可。

实施建议：
- `dm_uid_sink` 新增 `window_end_ts REAL`（聚合窗口到期时间）。
- 新增配置 `send.uid_sink_window_seconds`（默认 300 = 5 分钟）、`send.high_value_window_seconds`（默认 60）。
- `should_send` 逻辑改为：若 `window_end_ts` 未到 → 沉淀（返回 False + 理由「仍在聚合窗口」）；到期后按 `is_high_value` 选窗口长度，再判断是否过了冷却。

### D3：未开 AI 时是否只做词级筛选

| 选项 | 描述 | 成本 | 风险 | 可逆性 |
|---|---|---|---|---|
| **A. 只做词级筛选（退化为 D1-A）** | AI 未配置/调用失败时，仅用关键词权重表判定高价值，不送 LLM。 | 零：D1-C 的默认退化路径。 | 覆盖率受词表限制（同 D1-A 风险）。 | **N/A**（本身就是退化态）。 |
| **B. 阻塞等待 AI** | AI 不可用时，高价值判定阻塞，所有目标暂停发送。 | 低：加一个守卫即可。 | **高**：AI 抖动即停摆整个沉淀池，不符合「降级优先」原则。 | 高。 |
| **C. 跳过 AI 判定，全部放行** | 不开 AI 时不做高价值筛选，所有沉淀用户均发送。 | 零：关闭 `is_high_value` 过滤即可。 | 高：退回无差别发送，与「高价值筛查」需求矛盾。 | 高。 |

**推荐项：A（只做词级筛选）**。理由：
- 项目铁律：降级优于阻塞；AI 是增强而非依赖；
- D1-C 的设计天然是退化的（关键词初筛永远在线，LLM 精判可选）；
- 未开 AI 时 `is_high_value` 由关键词权重阈值决定，功能完整，仅语义精度下降。

### D4：扩 `dm_uid_sink` 而非新建平行表

**推荐：扩 `dm_uid_sink`**。理由：
1. **语义一致**：高价值/聚合/窗口是同一域（UID 沉淀）的增强，不是新域。新建平行表会造成「同一个 UID 在两张表里不同态」的分裂。
2. **共享写入点**：`mark_seen`/`mark_sent` 已有 3 处调用点（直播监听、视频采集、发送确认），扩表只需改一处 `UidSink`；新建表需复制写入逻辑，增加漂移风险。
3. **跨来源共享**：直播 + 采集 + 手动共用 `dm_uid_sink`；新建表需重新做来源路由，破坏现有共享。
4. **索引复用**：`idx_uid_sink_ts` 与新字段 `window_end_ts` 可共用查询模式（按时间扫描待发送），无需新建索引。
5. **已有先例**：`dm_cross_sink` 是 ADR-002 为解决「跨账号」特殊语义而新建的平行表；本次需求是同一表的字段增强，语义不同，不应套用先例。
6. **回滚简单**：新增列在 SQLite 中 `ALTER TABLE ADD COLUMN` 是向前兼容的；回滚只删列（SQLite 3.35+ 支持 `DROP COLUMN`），数据不丢。

## 4. 分批实施步骤（待用户拍板后执行，本 ADR 不改动代码）

### Phase 1：字段扩展（无行为变更）
1. `database.py` `_init_tables`：`ALTER TABLE dm_uid_sink ADD COLUMN keyword_score INTEGER DEFAULT 0`；`ADD COLUMN is_high_value BOOLEAN DEFAULT 0`；`ADD COLUMN high_value_reason TEXT DEFAULT ''`；`ADD COLUMN window_end_ts REAL`。
2. `app_config_schema.py` `send` 段：新增 `high_value_score_threshold`、`high_value_llm_enabled`、`uid_sink_window_seconds`、`high_value_window_seconds`。
3. **py_compile 验证**。

### Phase 2：关键词权重判定（D1-A，可用）
1. 新增 `services/high_value_keywords.py`：读 kv `high_value_keywords`，提供 `score_text(text) -> int`。
2. `UidSink.mark_seen`：在 `INSERT OR IGNORE` 前调用 `score_text`，写入 `keyword_score`。
3. `UidSink.should_send`：新增 `is_high_value` 过滤（`keyword_score >= threshold` 或通过 LLM 判定）。
4. 前端配置页：关键词权重表 CRUD（走统一配置中心 `UnifiedConfigSection`）。
5. **py_compile + 独立 unittest**。

### Phase 3：沉淀窗口（D2）
1. `UidSink.mark_seen`：设置 `window_end_ts = now + window_seconds`。
2. `UidSink.should_send`：若 `now < window_end_ts` → 拒绝（仍在聚合）；到期后按 `is_high_value` 选窗口长度。
3. 新增定时任务或发送闸门内扫描：到期 `window_end_ts` 的 UID 进入发送队列。
4. **py_compile + 独立 unittest**。

### Phase 4：LLM 精判（D1-C，可选）
1. `services/ai_reply.py` 新增 `judge_high_value(text, context) -> dict`。
2. `should_send` 对 `keyword_score` 达初筛阈值的调用 LLM，更新 `is_high_value` + `high_value_reason`。
3. 仅 `high_value_llm_enabled=true` 时生效。
4. **py_compile + 独立 unittest**。

### Phase 5：按人聚合（P2 核心）
1. `dm_uid_sink` 新增 `aggregate_text TEXT DEFAULT ''`（累积该 UID 在窗口内的全部弹幕文本）。
2. `mark_seen`：`aggregate_text = COALESCE(existing.aggregateText, '') || ' ' || new_text`。
3. 发送时：用 `aggregate_text` 生成上下文（而非单条弹幕），生成完即弃（不变式：每次生成独立快照，键用 uid 不用昵称）。
4. **py_compile + 独立 unittest**。

## 5. 回滚方案

| 阶段 | 回滚动作 | 影响 |
|---|---|---|
| Phase 1（字段扩展） | `ALTER TABLE dm_uid_sink DROP COLUMN keyword_score` 等（SQLite 3.35+）；或保留列、将新配置恢复默认。 | 零功能影响——旧代码无视新列。 |
| Phase 2（关键词） | 删除 `services/high_value_keywords.py`（或关 `high_value_llm_enabled` 并清空关键词表）；`mark_seen` 不再写 `keyword_score`。 | 退化为原始去重逻辑；已写分数不影响旧逻辑。 |
| Phase 3（窗口） | 恢复 `should_send` 为原始冷却逻辑；`window_end_ts` 留空。 | 所有 UID 即见即发（原始行为）。 |
| Phase 4（LLM） | 关 `high_value_llm_enabled`；`is_high_value` 仅由关键词决定。 | 退化为 Phase 2。 |
| Phase 5（聚合） | 停止累积 `aggregateText`；发送时用单条文本。 | 退化为 Phase 3 行为。 |

**关键回滚不变式**：任何阶段回滚后，`dm_uid_sink` 的原始 (account, peer_uid) 去重 + 冷却逻辑**必须完整可用**，零数据丢失。

## 6. 不变式（实施时必须维持）

- I1：`PRIMARY KEY (account, peer_uid)` 不变——不改为 UID 全局唯一（保留 per-account 维度，与 `dm_cross_sink` 分工）。
- I2：`mark_seen` 永远不写 `sent_ts`（仅沉淀）——不改变现有语义。
- I3：高价值判定是**过滤器**而非**发送触发器**——仍需通过 `should_send` + 冷却窗口 + 发送闸门。
- I4：聚合文本在发送后即弃——不在 DB 长期留存（隐私/容量考虑）。
- I5：未开 AI 时自动退化为关键词判定——不阻塞发送闸门。

## 7. 诚实标注

- 本 ADR **未修改任何业务代码**；所有实施步骤待用户拍板后由后续会话执行。
- `dm_uid_sink` 的当前行数/索引选择性未在真实生产库上实测（门禁禁止写 `C:\temp\flowcap_design`），容量评估基于代码静态分析。
- `high_value_keywords` 的 kv schema 与前端配置交互细节待实施时定，本 ADR 只给方向。
- 与 `dm_cross_sink` 的交互（跨账号 + 高价值双重过滤）的优先级与冲突策略**未在本 ADR 拍板**——属 T3 之外的相邻待办，建议后续 ADR 或设计契约补充。