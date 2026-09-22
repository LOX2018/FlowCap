# H-6 全库审计基线快照

**生成时间**: 2026-09-22
**审计区间**: v0.44.24 (59f9a28) ~ v0.44.43 (HEAD)
**源码根**: `DYAutoDM_v2/backend`
**基线对比**: v0.44.32 (`工作记忆/架构与业务实机复盘_2026-09-22_v0.44.32.md` §二)

---

## 一、基线审计计数（backend 目录）

| 指标 | v0.44.32 | v0.44.43 (当前) | 趋势 | 说明 |
|------|----------|-----------------|------|------|
| BCC 拉起决策点 | 5 | 186 | ❌恶化 | 方法论不同：v0.44.32 为特定决策函数计数；当前为 `grep -n "bcc" grep -ciE "decision\|check\|拉起\|run_\|start_\|launch_"` 含 if/switch/启动路径（923 处 BCC 提及，其中 186 处决策语境）。绝对值不可直比但膨胀趋势明确 |
| `_spawn_sidecar` 实现 | 1 | 1 | ✅持平 | `auto_dm/daemon_launcher.py` 唯一实现 |
| `except: pass` 计数 | 320 | 0 (bare) + 332 (named) = **332** | ❌恶化 | bare `except: pass` 已归零（3 处 bare except 无 pass）；但 `except NamedException: pass` 达 332 处，总计 332（基线 320）。Methodology 修正：AST 精确统计 |
| `except Exception` (命名 handler) | 1,149 | **1,301** | ❌恶化 | 增加 152(+13.2%) |
| threading 原语 | 62 | **223** | ❌恶化 | 增加 161(+260%)，反映并发引擎层大量新增（ADR-002/ADR-003 直播 dispatch、轮转、沉淀池） |
| `timeout=` 硬编码 | 206 | **158** | ✅改善 | 减少 48(-23.3%) |
| 大组件（>800 行） | 12 | **18** | ❌恶化 | 增加 6(+50%) |

### 大组件明细（>800 行）

| 行数 | 文件 | 
|------|------|
| 3,757 | `daemon/browser_daemon.py` |
| 2,024 | `auto_dm/conversation_capture.py` |
| 1,790 | `daemon/recv_daemon.py` |
| 1,666 | `auto_dm/accounts.py` |
| 1,645 | `api/messages.py` |
| 1,509 | `services/ai_reply.py` |
| 1,466 | `dy_apis/login_api.py` |
| 1,314 | `services/dm_dispatch.py` |
| 1,268 | `api/platform.py` |
| 1,263 | `services/probe.py` |
| 1,204 | `vbrowser.py` |
| 1,203 | `core/auto_dm.py` |
| 1,178 | `errcode_data.py` |
| 1,038 | `api/accounts.py` |
| 957 | `main.py` |
| 948 | `api/ai.py` |
| 819 | `services/pro_kb.py` |

---

## 二、commit 统计

| 指标 | 值 |
|------|-----|
| 基线 -> HEAD commit 总数 | **67** |
| fix/bug 类 commit | **14** (20.9%) |
| 新增文档/交接卡/kb | ~28 (41.8%) |
| 新增功能/架构 | ~16 (23.9%) |
| 测试/回放层 | ~4 (6.0%) |
| 其他(docs/arch/ledger) | ~5 (7.5%) |

**判断**: fix 占比 20.9%，处于健康范围（15-30%），但 pile-up 趋势明显 —— 8 个 patch 节点已超红线。

---

## 三、契约一致性

### G0-G5 覆盖率（`scripts/check_contracts.py`）

| Gate | 检查项 | 状态 | 实现 |
|------|--------|------|------|
| G0 | 契约文件存在性（≥5） | ✅ 通过 | 5 份 C-*.md 文件 |
| G1 | C-01 捕获零主动查询 | ✅ 实现 | AST 扫描捕获模块主动查询 |
| G2 | C-02 secsdk 签名接线 | ✅ 实现 | 端点清单 vs 签名接线；.known-gaps.json 已知缺口豁免 |
| G3 | C-03 引擎校验（dm 不冒充 wp） | ✅ 实现 | 代码模式匹配 |
| G4 | C-04 投递有回执/落库验证 | ✅ 实现 | 符号存在性检查 |
| G5 | C-05 reflow 主引擎存在 | ✅ 实现 | `_reflow_resolve` 符号检查 |

### M-10 门禁新增 G7/G13

- `scripts/check_contracts.py` 中 **未发现 G7/G13 相关实现**
- 仅有 G0-G5 共 6 个 Gate
- **结论**: M-10 门禁未完成（H-6 待办项需建立）

### known-gaps 机制

- `.known-gaps.json` 存在于 `docs/design-contracts/`
- 精确逐端点匹配（2026-09-22 修正），非前缀匹配
- 分类函数 `classify()` 将违规分为（新增违规, 已知缺口）

---

## 四、性能回归

### Benchmark/Profiling/P99 资产搜索

| 条件 | 结果 |
|------|------|
| 源码内 `*benchmark*` 文件 | ❌ 无 |
| 源码内 `*profiling*` 文件 | ❌ 无 |
| 源码内 `*perf*` 文件 | ❌ 无（仅第三方 stubs、node_modules 编译缓存） |
| 源码内 `*p99*` 文件 | ❌ 无 |
| Plan 文件提及性能优化 | 1 份：`.hermes/plans/2026-09-07_140406-performance-optimization-refactor.md`（未实施） |

### 判断

> **不测** —— 全仓无任何 benchmark、profiling、P99 可执行资产。性能优化停留在 plan 阶段。符合 v0.44.32 基线判断：性能回归一节为「不测」而非「待建」。

---

## 五、汇总

| 维度 | 结论 |
|------|------|
| 技术债务 | ❌ 恶化 —— except 面 +13.2%，threading 散落度 +260%，大组件 +50%；仅 timeout= 和 _spawn_sidecar 有改善 |
| 逻辑漏洞 | 按趋势推断：threading 散落度激增（62→223）引入并发竞争风险；332 处 named `except: pass` 是静默吞错隐患 |
| 契约一致性 | ✅ G0-G5 门禁可执行；❌ G7/G13 未在 check_contracts.py 实施（M-10 待建） |
| 性能回归 | 不测 —— 无 benchmark 资产（待建） |
| 红线触发 | 🔴 累积 3+ patch 节点（当前 8 nodes），超红线 |

### 改善项
- `timeout=` 硬编码 -23.3% ✅
- `_spawn_sidecar` 保持 1 实现 ✅
- bare `except: pass` 归零 (3 bare) ✅

### 触发告警项
- threading 散落度 +260% ⚠️ 需结构性收敛方案
- 大组件 +50% (12→18) ⚠️ browser_daemon.py (3,757 行) 为首要拆解目标
- except 面持续膨胀 (1,149→1,301) ⚠️
- G7/G13 门禁未实施 ⚠️
- 性能分析基建缺失 ⚠️