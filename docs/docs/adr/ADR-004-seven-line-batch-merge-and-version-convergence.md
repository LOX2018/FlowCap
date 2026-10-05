# ADR-004 · 七路修复批次的合并、去重与版本收口

- **状态**：Accepted（2026-09-23）
- **决策者**：宿主会话（HC-10 收尾）
- **相关**：ADR-001（sidecar 启动唯一实现）、ADR-002（多账号直播编排）、ADR-003（直播间登记表）
- **版本**：v0.44.53 → **v0.44.54**

---

## 1. 背景

用户以「按批次修缺陷」为一轮指令，宿主并行铺开 **7 条工作线**（P0-1 + A~F），
每条线**独占文件清单**、在**独立隔离根**（`FLOWCAP_APP_ROOT=%TEMP%/fix<线号>`）内改码并落盘报告：

| 线 | 主题 |
|---|---|
| P0-1 | 投递验证「假成功」闭环 |
| A | 房间/任务 ID 生成碰撞 + P2 判据失真 |
| B | 前后端契约错位 / 引擎控制不可用（P1 批次） |
| C | 回放层 / 验收脚本「门禁假绿」 |
| D | 门禁恒失败 + 仓库卫生 + 依赖上界 |
| E | 上游写接口对齐（T1 弹幕 / T2 评论） |
| F | 入口模块身份归一 + 扫码登录租约 |

七线**均未提交、未升版**。本 ADR 记录合并决策。

---

## 2. 决策

### 2.1 七线产出**全部接受**，按「内容」而非「行号」归并

**理由**：七线报告经**逐份审查**（5 份卷宗 `artifacts/fix_20260923/review/`），
自述改动清单与 `git diff --numstat` 逐字吻合、唯一标识符 `grep` 全部实存、无虚构。

**唯一重复声明**：`daemon/recv_daemon.py` 被 **P0-1 与 F 双声明**。
→ **处置（disjoint-intent，可共存）**：两份改动在**不同代码段**，均**保留**：

| 归属 | 内容 | 判据 |
|---|---|---|
| **F** | 入口模块身份归一（`sys.modules.setdefault("daemon.recv_daemon", …)`）+ P2-6 昵称占位改引唯一实现 | 顶部归一 + `from services.verdicts import is_placeholder_name` |
| **P0-1** | `SendBody.server_message_id` + 三元组解包 + `_mark_send_delivery()` | `server_message_id` / `_mark_send_delivery` |

**规范**：该文件混装两线 hunk 时，提交前按**内容**复核两族标识符**同时在位**（不按行号）。

### 2.2 测试层新增 1 处**隔离缺陷修复**（本会话亲自发现，非七线之一）

`test_delivery_verify._fetch()` 临时卸载 `api.*`/`database` 后**不还原** ⇒
与其后 `test_p2_live_guards` 的 `mock.patch.object` **打到被丢弃的旧模块对象** ⇒
「解绑失败却回 ok=True」的**顺序相关假失败**。
→ 修：`finally` 中逐键 `sys.modules.update(快照)` 还原。详见
`工作记忆/cases/2026-09-23_HC-10七路批次收尾_测试隔离假失败+版本收口_v0.44.54.md`。

### 2.3 已知**测试隔离债**（M-12）判为**既有债**，不阻塞本批

**HEAD 对照实验**（隔离 worktree，`b455192`）证明：`test_upstream_p4` 的 2 个 seq 用例
在 HEAD 上**同样**被 `test_replay_gates` / `test_engine_contract_p1` 污染而失败
⇒ **非本批引入**。按**迭代止损律**：同类问题已定位分类，**停止局部修补**，
登记为独立工作线 M-12（另含 `test_capability_probe` 固定共享根争用）。

### 2.4 版本收口

合并后**最后一步** `+0.01`：`0.44.53 → 0.44.54`，
六处版本源齐平（`scripts/check_version_sync.py` 门禁通过）。

---

## 3. 后果

| 项 | 结论 |
|---|---|
| 产品代码 | 七线改动 + 1 处测试修复，全部保留 |
| 全量回归 | `788` 项；干净根串行下仅 `test_upstream_p4` 2 项失败（M-12 既有债）；**本批新增 157 项组合全绿** |
| 未验证项 | E 线端到端真发 pending（用户本轮明示不真发）；P0-1 端到端 pending；注册 12 项开放风险入台账 |
| 单活跃交接卡 | HC-08 归档（开放项全抽入台账）、HC-10 归档，`artifacts/` 顶层 0 张在役 |

---

## 4. 未决（交后续）

1. **M-12**：`test_capability_probe` 残留清理 + `test_upstream_p4` 两用例隔离化
   → 目标：干净根 + 无并发下 `discover` **788/788 绿**且顺序无关。
2. **E-R9**：`test_upstream_write_align_t1_t2` 的上游逐字对账依赖**未入库**的
   `_ext_repos/DouYin_Spider_git` 快照 ⇒ 他人 clone 会**静默 skip** 5 条（40→35）。
   需固定上游快照或改为 `require_fixture` 式**硬失败**。
3. **E 线同族残留**：`diggLiveRoom` 仍用主站 Origin；`splice_url` 对 `/` 未编码；
   评论发布缺 4 项上游前置；`check_risk_response`/`dtrait` 未移植（假成功风险）。
4. **D-F3**：7 条契约缺口仍 open（登记 ≠ 修复）。
5. **隔离复现脚本未入库**（F 线 P0-3/P2-6 的行为证据一次性）。
