# 审计取证归档 · 2026-09-30

> 来源：`审计昨晚和今日的修改` → V3「未跟踪取证产物散落」处置。
> **性质**：取证快照层（**非** SSOT）。SSOT 在 git 提交 + `工作记忆/`。
> **归属裁决**：见 `OWNERSHIP.md`（本目录为**本会话提交**的审计整改，不涉及他人工作区）。

## 目录内容

### `snapshots/` —— 并发写者在场证据（原 `artifacts/conc_backup_20260930/`）

`7831a4c`(D-12 前) 时刻，另一并发会话留下的**未提交工作区快照** + 一次性补丁脚本 + 污染样本：
在 `c8f0a20`「测试顺序依赖根治」提交**之前**落盘，本会话审计期间**逐字节比对确认其 md5
与 7831a4c→a26d684 之间的任何提交态均不一致**（即非冗余、确为某个中间态），故**保留归案**。

| 文件 | 性质 |
|---|---|
| `DYAutoDM_v2_backend_{api_tasks,core_auto_dm,core_dispatch,models_task,services_ai_reply}.py` | 5 个后端源码在 D-12 前的中间态快照 |
| `DYAutoDM_v2_frontend_src_components_live_{live-page,live-shared}.tsx` | 2 个前端中间态快照 |
| `fix_and_wire.py` / `fix_fixation_block.py` / `fix_guard_findings.py` / `patch_live_table.py` / `wire_shared.py` | D-12 一次性补丁脚本（与 `DYAutoDM_v2/artifacts/_patch_20260930_*` 不同 md5，属早期版本） |
| `task_scheduler.py.contaminated.1790743630` | 污染样本（D-2 负控泄漏实证） |
| `pro-kb.tsx.workspace.1790744459` | 并发会话工作区副本（D-5 编译阻塞佐证） |

### 根目录

| 文件 | 性质 |
|---|---|
| `N1_报告.md` | 子代理 N1 的 M-28 修复报告（原 `DYAutoDM_v2/artifacts/fix_night_20260929/`）；本会话取用其钉根点计数（实测 2/9/2/5/2/3/1，其中 `test_task_scheduler_gates` 实际 3 —— 报告记 2，差异已核对） |
| `OWNERSHIP.md` | 本会话所有权声明 |
| `MANIFEST.md` | 本文件 |
| `_audit_regression_20260930.txt` | 本批全量回归基线读数（父目录 `artifacts/`） |

## 保留判据（不删理由）
- `snapshots/` 各文件**均非**任一已提交态的字节副本 ⇒ 删除即丢失「D-12 修复前中间态」这一取证层。
- 已登记于台账 §五·乙（V3）。如需回收空间，须先确认对应用例已闭环且无回溯需求。
