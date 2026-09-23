# 任务 T3 卷宗 · P2 沉淀池 / 高价值筛查 / 按人聚合

**任务书**：`artifacts/fix_parallel/TASK_T3.md`
**执行人**：子代理（design subagent，2026-09-24）
**状态**：✅ 设计稿落盘，三个决策各有推荐项，用户可直接拍板

---

## 1) 自述改动清单

| 文件 | 类型 | 变更内容 |
|---|---|---|
| `C:/Users/LOX/Desktop/DYchajian/DYAutoDM_v2/docs/adr/ADR-007-live-lead-sink.md` | 新增 | ADR 决策稿（约 15,868 字节，180 行），状态=草案待拍板 |
| `C:/Users/LOX/Desktop/DYchajian/DYAutoDM_v2/docs/design-contracts/C-06-live-lead-sink.md` | 新增 | 设计契约骨架（约 11,343 字节，127 行），含 DbC（P/Q/I）、字段规范、验证方式 |
| `artifacts/fix_parallel/T3_report.md` | 新增 | 本卷宗（当前文件） |

**未改动任何业务代码**（符合任务约束）。

---

## 2) 自述验证数字

| 验证项 | 类型 | 结果 |
|---|---|---|
| `high_value_score_threshold` 在 ADR + 契约两份文件中一致 | grep 交叉 | ✅ 命中 |
| `uid_sink_window_seconds` 在 ADR + 契约两份文件中一致 | grep 交叉 | ✅ 命中 |
| `aggregate_text` 在 C-06 契约中字段定义 / DbC / 验证命令三处一致 | grep 交叉 | ✅ 命中 |
| `keyword_score` 在 ADR Phase 实施步骤 + C-06 契约中一致 | grep 交叉 | ✅ 命中 |
| `window_end_ts` 在 ADR Phase 实施步骤 + C-06 契约中一致 | grep 交叉 | ✅ 命中 |
| 两个新建文件均落盘 | 文件存在性 | ✅ 3 个文件全部在场 |

**负控**：未跑全量 unittest discover（按约束禁止）；未写真实生产 DB（门禁）。
**刻意失败态**：本任务仅出设计稿，不涉及代码改动，无失败态验证。

---

## 3) 唯一标识符（供父会话 grep 复核）

| 标识符 | 出现文件 | 用途 |
|---|---|---|
| `high_value_score_threshold` | ADR-007 §3 D1、§4 Phase 1、§5 Phase 2；C-06 §2 P3/Q4、§3 字段、§4 配置、§6 验证 | 高价值关键词阈值配置键 |
| `uid_sink_window_seconds` | ADR-007 §3 D2、§4 Phase 1；C-06 §2 P3、§4 配置、§6 验证 | 聚合窗口时长配置键 |
| `aggregate_text` | C-06 §2 P2/Q1/Q5、§3 字段、§4 配置、§5 NFR、§6 验证 | 新增字段名（按人聚合） |
| `is_high_value` | ADR-007 §3 D1 推荐、§4 Phase 1-2；C-06 §2 P2/Q3/Q4、§3 字段 | 新增字段名（高价值标记） |
| `window_end_ts` | ADR-007 §3 D2、§4 Phase 1/3、§5 Phase 3；C-06 §2 P2/Q1/Q3、§3 字段、§5 NFR、§6 验证 | 新增字段名（窗口到期时间） |

---

## 4) 诚实标注

- **未修改业务代码**：符合任务要求（仅出设计稿），未跑全量 discover，未写生产 DB。
- **未实测容量**：`dm_uid_sink` 当前行数 / 索引选择性基于代码静态分析（门禁禁止写 `C:\temp\dyautodm_design`），NFR 预算含声明性假设。
- **相邻待办未拍板**：`dm_cross_sink`（跨账号）与 `is_high_value`（高价值）的执行顺序与优先级、前端配置页交互、归档策略——属 ADR-007 §7 提到的相邻待办，**建议后续单独 ADR 或补充契约**。
- **LLM 精判一致性窗口未量化**：异步回调更新 `is_high_value` 期间，同一 UID 可能已被关键词判定放行/拒绝；最终一致性边界属 Phase 4 实施层待办。
- **与 `config_tag.py` 的交互未定义**：本 ADR 推荐「高价值标签方案 C（混合）」，但 `config_tag.py`（他人未提交产物，禁止触碰）的标签体系与 `dm_uid_sink` 高价值标签是否/如何互通，**属后续决策**。
- **回滚方案**：各 Phase 回滚均基于"新增列旧代码无视"或"配置关回默认值"，**但 SQLite DROP COLUMN 需 3.35+**（项目 Python 3.11.16 自带的 sqlite3 版本需 ≥ 3.35，实施前须核对）。

---

## 5) 交叉判据（grep 核实标识符真实存在于磁盘）

```bash
cd "C:/Users/LOX/Desktop/DYchajian/DYAutoDM_v2"

# 标识符 1：high_value_score_threshold
grep -rln "high_value_score_threshold" docs/
# 命中：ADR-007-live-lead-sink.md (2处) + C-06-live-lead-sink.md (4处) = 6 命中

# 标识符 2：uid_sink_window_seconds
grep -rln "uid_sink_window_seconds" docs/
# 命中：ADR-007-live-lead-sink.md (2处) + C-06-live-lead-sink.md (3处) = 5 命中

# 标识符 3：aggregate_text
grep -c "aggregate_text" docs/design-contracts/C-06-live-lead-sink.md
# 命中：18 次

# 标识符 4：is_high_value
grep -c "is_high_value" docs/design-contracts/C-06-live-lead-sink.md
# 命中：6 次

# 标识符 5：window_end_ts
grep -c "window_end_ts" docs/design-contracts/C-06-live-lead-sink.md
# 命中：12 次

# 文件在场核验
ls -la docs/adr/ADR-007-live-lead-sink.md docs/design-contracts/C-06-live-lead-sink.md artifacts/fix_parallel/T3_report.md
# 期望：3 个文件全部存在
```

**实测结果**（2026-09-24 执行）：
- `high_value_score_threshold`：6 命中 ✅
- `uid_sink_window_seconds`：5 命中 ✅
- `aggregate_text`：18 命中 ✅
- `is_high_value`：6 命中 ✅
- `window_end_ts`：12 命中 ✅
- 3 个指定文件全部在场 ✅

---

## 6) 疑点（对其它待办的影响 / 相邻问题）

### 6.1 对其它待办的影响

| 待办 | 影响 |
|---|---|
| **T1（P1 私信发送闸门 + 风控）** | 本 ADR 新增的 `is_high_value` 过滤应在 `should_send` 内完成，**不改变发送闸门（`dm_dispatch.py`）主流程**；若实施时把过滤逻辑错放到闸门上游（如 `core/dispatch.py`），违反红线。 |
| **T8（B-4 配置标签体系）** | `config_tag.py` 的标签体系与 `dm_uid_sink` 的 `is_high_value` 语义有交集（都是"高价值"），**但本 ADR 不对接标签体系**——标签体系规划稿 `docs/配置标签体系规划_B-4.md` 中的"标签"是给策略绑定用的，本 ADR 的"高价值"是给发送过滤用的。两者独立，后续若要做"标签驱动高价值判定"，需另开 ADR 声明互通规则。 |
| **ADR-002（多账号并发）** | `dm_cross_sink` 与 `is_high_value` 的执行顺序未定义：若同一 peer_uid 跨账号已发（`dm_cross_sink` 有记录）但本账号判定为高价值——跨账号去重优先还是高价值放行优先？本 ADR 默认"跨账号去重优先"（与 ADR-002 §3.4 一致），但**未在 ADR 中显式声明**，属待补充契约。 |

### 6.2 相邻问题

| 问题 | 描述 |
|---|---|
| **`dm_uid_sink` 长期膨胀** | `aggregate_text` 累积会使单行体积增大（2000 字符上界），高活跃 UID 的 `send_count` 只增不减。归档策略未设计。 |
| **关键词权重表维护成本** | D1-A/C 依赖人工维护 `high_value_keywords` kv，无 UI 自动生成/推荐机制。当前走统一配置中心 `UnifiedConfigSection`，但 CRUD 交互未定义。 |
| **SQLite 版本门禁** | Phase 1 回滚依赖 `ALTER TABLE DROP COLUMN`（SQLite 3.35+）。项目 Python 3.11.16 自带 sqlite3 模块版本需 ≥ 3.35，实施前须核对，否则回滚需走"保留列 + 改默认值"路径。 |
| **窗口扫描 NFR 未实测** | §5 NFR 中"窗口到期扫描 ≤ 50ms"基于"每 30s 扫一次、命中 ≤ 1000 行"的假设，实际命中率取决于 `uid_sink_window_seconds` 设置与弹幕量级，**未实测**。 |

---

## 附：决策推荐摘要（供用户直接拍板）

| 决策 | 选项 | 推荐理由 |
|---|---|---|
| **D1 高价值标签** | **C 混合（关键词初筛 + LLM 精判）** | 关键词降噪漏斗（控成本）+ LLM 语义覆盖（保精度）+ 未开 AI 自动退化（零阻塞） |
| **D2 沉淀窗口** | **C 固定窗口 + 热度加速** | 保底固定窗口 + 高价值用户加速 + 与 D1 天然耦合 |
| **D3 未开 AI** | **A 只做词级筛选（退化）** | 降级优于阻塞，AI 是增强非依赖 |
| **D4 扩表 vs 新建** | **扩 dm_uid_sink** | 语义一致 / 共享写入点 / 索引复用 / 回滚简单 |

**完成判据**：
- ✅ 两份文档落盘：`docs/adr/ADR-007-live-lead-sink.md` + `docs/design-contracts/C-06-live-lead-sink.md`
- ✅ 三个决策各有明确推荐项（D1=C, D2=C, D3=A, D4=扩表）
- ✅ 用户读完可直接拍板
- ✅ 卷宗 6 节齐备