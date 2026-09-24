# T3 实施报告 · P2 沉淀池 / 高价值筛查 / 按人聚合（ADR-007 / C-06）

> 分支 `design/better-douyin` · 基线 v0.44.60（`c6ff6e2`）· 2026-09-24
> 拍板：用户 2026-09-24「**T3 推进**」（ADR-007 推荐项 D1=C / D2=C / D3=A / D4=扩表 被采纳）
> 性质：**架构建成 + 能力默认休眠**（用户「显式配置」原则）

---

## 1. 改动清单

| 文件 | 增/删 | 内容 |
|---|---|---|
| `backend/database.py` | +27 | `dm_uid_sink` 建表加 5 列；`_migrate_schema` 逐列 `ADD COLUMN` + 建 `idx_uid_sink_window` |
| `backend/services/high_value_keywords.py` | **新增** | 关键词权重表：`score_text()` / `get_keywords()` / `set_keywords()` / `invalidate()`；种子词表 `DEFAULT_KEYWORDS` |
| `backend/services/dm_dispatch.py` | +114/−11 | `_LAZY_MAP` +5；`_FALLBACK_*` +5；`UidSink.should_send` 加窗口/高价值过滤；`mark_seen` 加聚合/打分/窗口；新增 `get_aggregate()` / `window_remaining()` / `_read_row()` / `_refine_high_value_async()` |
| `backend/services/app_config_schema.py` | +35 | `send` 段 +5 配置键 |
| `backend/services/ai_reply.py` | +41 | 新增 `judge_high_value()`（LLM 精判，Phase 4） |
| `backend/core/live_hook.py` | +11 | 弹幕入调度前 `mark_seen(...)`（source=live） |
| `backend/web_probe.py` | +11 | 采集记录入调度前 `mark_seen(...)`（source=crawl） |
| `backend/core/auto_dm.py` | +15 | `_gen` 窗口开启时用 `get_aggregate()` 聚合快照上下文 |
| `backend/test_uid_sink_ext.py` | **新增** | 15 项独立测试（含零回归负控 + 隔离自证） |
| `backend/test_dm_dispatch_config.py` | +9 | 接线基线 11 → **16**（新增 5 项参数） |

**未改动**：发送闸门主体流程（`submit` 顺序 / 队列 / 配额）、`dm_cross_sink`、前端。

---

## 2. 关键设计决策（须上报的取舍）

### 2.1 🔴 两个默认值偏离 ADR 建议 —— 改为「**能力休眠**」
| 配置 | ADR 建议 | 实际默认 | 理由 |
|---|---|---|---|
| `uid_sink_window_seconds` | 300.0 | **0.0** | 默认 300 会**静默延迟全部私信 5 分钟** = 改变核心发送行为 |
| `high_value_score_threshold` | 10 | **0** | 默认 10 + strict 会把**大部分目标判为非高价值而拦截**（阈值 10 需命中≈3个强词），等于静默大幅减少发送量 |
| `high_value_llm_enabled` | false | false | 同 ADR（本无偏离） |

**判据**：用户明确要求「行为/环境由配置**显式选择**决定，判断不依赖外部可变状态」。
把「窗口开/筛查开」默认打开，会让升级后的行为**无声变化**且难以归因。
⇒ 能力**建成就绪**，由用户在配置中心显式打开；`0` 即等价于改造前行为（**零回归**）。

### 2.2 🔴 `high_value_keywords` 懒初始化 = 种子词表（非空 `{}`）
C-06 §2.1 P4 原文写「缺失由 `services.high_value_keywords` 懒初始化写入 `{}`」。
**实测该写法会导致功能静默 no-op**：空词表 ⇒ `score_text` 恒 0 ⇒ `is_high_value` 恒假 ⇒
阈值开启后**全部拒发**（而非「按关键词筛选」）。
⇒ 改为懒初始化写入**工伤域种子词表**（`DEFAULT_KEYWORDS`，24 词带权重），用户可随时覆盖。

### 2.3 D2 采用「固定窗口 + 热度加速」（ADR 推荐 C）
窗口初值按 `is_high_value` 选：高价值用 `high_value_window_seconds`（默认 60），
否则 `uid_sink_window_seconds`；后续命中高价值时用 `MIN(window_end_ts, first_seen_ts + 加速窗)`
把窗口**缩短**（滑动上界，C-06 I7）。

---

## 3. 验证数字（含负控）

### 3.1 独立测试 `test_uid_sink_ext.py`（15 项，3.11 + 3.14 双绿）
| 组 | 用例 | 判据 |
|---|---|---|
| 隔离自证 2 | 沙箱目录存在 / DB 路径含 `uid_sink_ext_test` | ✅ |
| **零回归 2（负控）** | 默认关闭 → `should_send` 放行且无理由；冷却逻辑仍拦 | ✅ |
| 表结构 1 | 5 新列 + `idx_uid_sink_window` 齐备 | ✅ |
| 关键词 1 | 工伤文本正分 / 空与无关文本 0 分 | ✅ |
| 窗口 2 | 窗口开启 → 拒「仍在聚合窗口」；窗口=0 → `window_end_ts IS NULL` 且放行 | ✅ |
| 高价值 2 | 低分（阈值 50）→ 拒「非高价值」；高分 → 放行且 `is_high_value=1` | ✅ |
| 聚合 3 | 累积两条；截断 ≤ `aggregate_max_chars`；`mark_sent` 后 `aggregate_text` 保留 | ✅ |
| 幂等 1 | 5 次 `mark_seen` 只 1 行 | ✅ |
| 不变式 1 | `mark_seen` 永不写 `sent_ts`（C-06 I2） | ✅ |

### 3.2 生产库**副本**迁移验证（不碰真库）
`dm_uid_sink` 列由 7 → **12**（+5 齐备）；索引含 `idx_uid_sink_window`；**存量 34 行完好**。
迁移**幂等**：连跑 3 次 `get_db()` 列数恒 12。

### 3.3 相关既有测试回归
`test_live_ai_wiring` 23 / `test_config_tag` 9 / `test_app_config` 23 /
`test_cross_account_sink` 4 / `test_send_gate_config` 6 / `test_member_smoke` 10 /
`test_live_config_guards` 16 —— **全绿**。

### 3.4 🔴 测试期间抓到并修复的**两处真缺陷**（非本报告自述，是实测）
1. **索引建在 ALTER 之前**：`_init_tables` 的 executescript 里建 `window_end_ts` 索引，
   而该列对**已存在的旧库**要等 `_migrate_schema` 的 ALTER 才出现 ⇒ 直接
   `no such column: window_end_ts`（首轮 13 ERROR 全部由此）。修正：索引移到迁移后。
2. **测试沙箱逃逸污染仓库**：`vbrowser.app_root()` 对**不存在的** `DY_APP_ROOT`
   打 `[BCC-039]` 后**忽略**它 → 回落相对路径 `data/` ⇒ 实际写入
   `DYAutoDM_v2/data/dyautodm.db`（gitignored，**不进 git status**，极隐蔽）。
   修正：测试先 `os.makedirs(_ROOT)`，并**加隔离自证用例**（断言 DB 路径落在沙箱内）。
   已清理污染文件。

### 3.5 契约门禁
`check_contracts.py` 6/6 PASS；`check_version_sync.py` 六处齐平 0.44.60；
C-06 §6 六条机械判据逐条命中（5 列 / `is_high_value` 过滤 / `score_text`+阈值+LLM 开关 / 窗口 / 聚合 / 未开 AI 退化）。

---

## 4. 诚实标注

- **未真机验证**：本环境无正在开播的直播间，**未**跑真实弹幕流；全部证据来自
  独立沙箱测试 + 生产库副本迁移。**不得**据此声称「线上已按窗口延迟发送」。
- **前端未接线**：`high_value_keywords` 的权重表 CRUD 页 / `is_high_value` 展示
  未做（C-06 §7 已声明属实施层，走统一配置中心）。当前只能改 kv / 配置值。
- **窗口到期扫描未实现**：C-06 NFR 提到「每 30s 扫 `window_end_ts` 到期行」的
  定时任务**未做** —— `should_send` 是「下次遇到该 UID 时才判窗口」的**被动**到期。
  对直播场景够用（目标会被反复提交）；对「窗口到期后主动补发」的语义**不完整**，
  已登记为后续项。
- **`dm_cross_sink` 与高价值过滤的顺序**未拍板（C-06 §7 相邻待办），当前顺序 =
  跨账号池 → per-account（含窗口/高价值），未做交叉语义。
- **LLM 精判的最终一致性窗口未量化**（C-06 §7 同款）：异步回调期间同一 UID
  可能已被关键词判定放行/拒绝。
- **未做归档策略**：`aggregate_text` 累积导致 `dm_uid_sink` 长期膨胀（C-06 §7）。

## 5. 疑点

- **对其它待办的影响**：M-11 T1/T2（写接口实机验证）与本改动**文件不重叠**（`dy_apis/*` vs `services/*`）；
  DY-T7（LLM 调旋钮）会碰 `ai_reply._chat_openai`，与本改动同文件但不同函数，**顺序无关**。
- **`mark_seen` 调用点新增 2 处**（live_hook / web_probe），均为 try 包裹 + 失败静默，
  不阻断原有 `dispatch.submit` 路径。
- **`_LAZY_MAP` 基线仍硬编码**（`test_dm_dispatch_config.py`）：本次已同步，但
  该测试是 M-10 的「判据副本」同型债 —— 每次加参数都要手改两处。
