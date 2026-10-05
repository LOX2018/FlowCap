# ADR-020：前瞻审计 A 类六项落地 + B-1 上游 creator 域只读取证（并发写者同批处置）

- **状态**：已接受（Accepted）
- **日期**：2026-09-28
- **版本**：v0.45.61 → **v0.45.67**（6 次提交，每次 patch +0.01）
- **相关**：`night_shift/reports/2026-09-28-前瞻审计.md` §7、台账 M-22/M-23、
  `docs/design-contracts/C-07-live-interaction.md`、`artifacts/fix_a_20260928/`、
  案例 `工作记忆/cases/2026-09-28_B批A类六项施工与B1上游取证.md`

## 背景

2026-09-28 夜间批（v0.45.57→0.45.61）交付 N1~N4 + 前瞻审计后，用户下令
**「把 A 类和 b1 做了」**。A 类 8 条（A-1 已在上批完成）+ B-1（上游 creator 域）。

开工门禁实测：分支 `design/better-douyin` ✅；20s 双拍 md5 一致、无他人已跟踪改动；
版本源 6 处 = 0.45.61；**B-1 许可证实测复核 = GitHub API `license.spdx_id = null`
（无许可证，保留所有权利）**。

## 决策

### 一、可施工项 → 本批落地（各自独立提交，每项 +0.01）

| 项 | 提交 | 版本 | 处置要点 |
|---|---|---|---|
| A-2 features 接线 | `63019ce` | 0.45.62 | `api/crawl.py` 两处（search user / comments）改经 `features.py`；`ok=False` 一律上抛 502，**不把失败降级成空结果**；`search_some_general_work`（含 filter_duration）**不动** |
| A-3 downloader 跳过 | `9d4b164` | 0.45.63 | `run_task` 的前置准备（`extract_media`/`mark`/`archive_dir`/`makedirs`）原本在 `try` **之前** ⇒ 单作品异常经 `ex.map` 冒泡中断整批；改为全部纳入 try |
| A-4 静默兜底口径 | `458cfbc` | 0.45.64 | 新增 `--include-tests`（**默认口径逐字节不变**，保 386 基线可比）；豁免由默认静默改为**打印进输出**；`--baseline add-scope` 只增字段 |
| A-5 C-07 契约 + G15 | `4914d1c` | 0.45.65 | 新增直播交互域契约 C-07（danmaku/dm-template/ws）；`check_contracts.py` 补 G15，复用 `_grep_specs` 范式，判据来源解析 0 条 ⇒ **FAIL（禁静默 SKIP）** |
| A-6 孤儿模块清除 | `7d7a1a6` | 0.45.66 | 删除 `utils/data_util.py`(199) / `dy_apis/douyin_recv_msg.py`(155) / `services/account_service.py`(62)；静态六路 + 动态导入机制 + 编译实测三重零引用证据 |
| A-8 隔离根单一化 | `53922c6` | 0.45.67 | 11 个 `test_*.py` 的「非临时兜底」（真实 design 根 / 源码树）改为 `tempfile.mkdtemp()` **赋值**；`makedirs` 先于赋值；非临时兜底 **11 → 0** |

### 二、两项**不**落地（保留 A/B 边界与「门禁必须真会红」）

- **A-7 最小全局熔断** —— 判据：它不是「项目已有部分的改良」，而是**新增子系统**
  （跨外部依赖的状态机 + 新 service）。随手实现会引入新的单点故障与「隐式自动降级」，
  与用户【显式配置原则】直接冲突。**改出设计要点，进待拍板（M-24）。**
- **A-9 前端最小行为门禁** —— 判据：审计原文只给「照 TSX 扫描范式」，**未给可判定的
  不变量**。造一条没有明确法条的门禁 = 假门禁（本仓已有「假绿门禁」教训 ADR-019）。
  **延后，待先定法条（M-24）。**

### 三、B-1 上游 creator 域 → **只读取证，不落地**

只读报告 `artifacts/fix_a_20260928/B1_upstream_creator_recon.md`（259 行）：

- 规模：`douyin_creator_api.py` 2734 行 / `douyin_im_media.py` 926 行 = 本项目
  `dy_apis/` 的 53%；**真正内容写接口仅 1 个** `POST /web/api/media/aweme/create_v2/`。
- `utils/acrawler_runtime` = **浏览器内 JS 签名运行时的离线 node `vm` 复现**
  （非跑浏览器）：只依赖 **Node ≥18**（无 npm / 无 CDP / 无远程服务）。
- 许可证实测：本地无 LICENSE/COPYING/NOTICE（6 处 0 命中），GitHub `license=null`、
  `/license` 404 ⇒ **无许可 = 保留所有权利**，代码级移植有明确版权风险。
- 两条路：A 引代码（合规风险最高，需带 Node/cv2/av/ffmpeg + 本项目缺失的 creator 属性）；
  B 只取协议形态自研（可大量复用本项目已有实现，合规风险最低，工期更长）。
- **待拍板 5 点**见 M-24。

### 四、并发写者处置（§VIII 协议）

收尾扫描发现**另一会话**在同仓改 7 个文件（`daemon/recv_daemon.py`、`dy_apis/client_im.py`、
`services/{dm_dispatch,send_response,app_config_schema,reply_kb,task_scheduler}.py` +
新测试 `test_send_pacing_and_kind.py`），内容为「发送侧失败枚举 + 风控冷静期 + 分钟级限流」。

- 与本批文件集**零重叠** ⇒ 未发生覆盖；本批**不碰对方一个字节**，快照落
  `artifacts/concurrency_20260928/snapshot_batchB.md`。
- 提交前**逐文件 `git add`**、**每次升版前重读**版本值；提交后核验 6 提交**零夹带**对方文件。
- 对方文件在本批 6 提交后**仍保持未提交态**（归属其本人）。

## 后果

- 版本单调：0.45.61 → 0.45.67，6 处版本源齐平（`check_version_sync` ✓）。
- 门禁横截：`check_iron_rules` 13 项 **11 通过 / 2 警告**（R2 数据根散落源码、R3 backend
  构建产物 —— 均为**磁盘卫生**，非本批引入、不阻断）；R9 审计红线 **5/20** 未触发。
- 遗留（各附复现命令，见台账 M-24 / 案例）：
  - `test_design_root_isolation` 在**活 Camoufox 浏览器**运行时会因浏览器 profile
    写入含根而**间歇红**（环境性；本机实测 8 个 camoufox.exe 常驻）。修法：门禁排除
    `profile/_camoufox`，或验收时选浏览器静默窗口。
  - A-4 `--include-tests` 口径下 F3 报「L1 新增 6 处」，其中 `dm_dispatch.py:1005`
    属**并发写者**改动的文件（非本批）。
  - `--include-tests` 纳入 97 个 test/verify 文件、命中 673 处（默认 627）。
