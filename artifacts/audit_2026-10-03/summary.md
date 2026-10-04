# DYAutoDM_v2 全量审计 · 合并总报告

- 日期：2026-10-03　基线：`a57aca8..9da7900`（32 提交）+ 全量当前态　HEAD 审计期间：`9da7900`（v0.46.40）
- 方法：本人亲验 + 3 路子代理（存量✅ / 前端✅ / 增量❌输出循环失败→本人亲验补做）+ 全量测试基线对照
- 维度报告：`dim_increment.md`（亲验）· `dim_frontend.md`（子代理，门禁已核验）· `dim_frontend_components.md`（首派子代理，**部分误报，见 §4 勘误**）· `dim_stock.md`（子代理）

---

## §1 方向层结论

**健康方向（已验证整改到位）**
- 2026-09-27 审计 F4 缺陷全闭环：TS-1 接线（main.py:638 双 fail-closed）/ TS-2 闸门解包 / TS-3 投递验证 / TS-4 重入竞态 / TS-5 零锁 / TS-8 默认态——且门禁已升级为**行为断言** B1-B10（14:21 基线日志全 PASS）
- R4 六版本源齐平 v0.46.40；R8 数据契约 6/6 PASS；知识库 7 分册合并为单一 SSOT（+2058/-2588）
- 静默兜底 L1 新增 65（基线 102，**-45 改善中**）；except-pass 严格口径仅 1 处
- 视图白名单双 SSOT 漂移（ad413e1）已用「读 VIEW_TITLE 单一来源」正确修复

**方向风险（需要决策，非修 bug）**
1. 🔴 **auth 路由 3 次反转**（凭证→匿名→凭证）：决策本身已被实测订正，但**无门禁锁定** `ANON_ENDPOINTS` 表——下次凭猜测改表无机械防线。建议：每端点强制「实测日期+结论」注释 + 门禁校验，或回放样本离线探活
2. 🟡 **错误码契约 110/389 缺六段（28.3%）**：AI 域 34 个全缺（核心新功能未同步契约）。优先级：AI > ACC(16) > NTY/SCHED(各12)
3. 🟡 **task_scheduler TS-6 零持久化 + TS-7 僵尸线程仍在**（`_tasks: dict = {}`、`th.join` 后放弃）——功能在、持久性缺，重启即丢

## §2 机制层结论

| 机制 | 状态 |
|---|---|
| 凭证外发门禁 R12 | 🔴 **扫描面只覆盖 `DYAutoDM_v2/`，结构性看不见仓根 `artifacts/`**（见 §3 P0-1） |
| 静默兜底门禁 | ✅ 行为断言化（B1-B10），但 F1 写库 31 / F2 外发 11 pass-only 仍阻断（EXIT=1） |
| 契约门禁 G2 | 🔴 **假绿**：`check_contracts` 报 14/15，实为 `client_video.py:319 get_feed_anon` 在保护清单内无 `signed_url` 且 `.known-gaps.json` 空 ⇒ 新增违规（幸无生产调用方，feed 已 fail-closed 到凭证路径） |
| R8-6 禁同义多名字 | 🔴 **假绿**：扫描面只 8 文件（漏 `mcp/`），正则只认字面量 `IN('a','b')`，匹配不到 `tools_debug.py:627 msg_type IN ('text','7','27')` 与 `ai_reply.py:2507` 参数化 `IN({ph})` |
| R9 审计红线 | 🔴 23/20 已触发，`DEFAULT_SINCE` 未前移（违反 D-02「审计后重置计数基点」，本次审计即应重置到 9da7900） |
| F4 行为门禁 | ✅ 10/10 PASS（14:21 基线实测） |

## §3 P0 清单（必须处理）

1. **凭证进 git 风险**：`artifacts/yingyue_profile_backup_20261003-131055/` 含 `.env`(26.7KB: GITHUB_TOKEN/NVIDIA_API_KEY/LONGCAT_API_KEY 等)、`state.db`(270KB)、`memories/`，**6 文件未被 gitignore**，`git add -A` 即外泄。且 R12 门禁扫描面看不见仓根 artifacts/。处置：加 gitignore 或移出仓根；门禁扫描面扩到仓根
2. **database.py:335**：注释声称 2026-09-17 已改告警，代码仍是 `except: pass`——**文档-代码漂移**，ALTER 失败静默
3. **静默兜底门禁 EXIT=1**（F1/F2/F3 三阻断项）——当前提交门禁应拦着所有提交，先确认另一会话是否在带病提交

## §4 两路子代理冲突的勘误（以实测为准）

| 冲突点 | 首派（components 报告） | 重派（frontend 报告） | 本人实测 |
|---|---|---|---|
| 孤儿组件 | 6 个 | 2 个 | **6 个**：tooltip/scroll-area/separator/crawl-page/RoomConfigPage/crawl-panel 全部 0 引用 ✅ 首派对 |
| 未定义 CSS 变量 | 14 个（含 --accent） | 未提 | **部分对**：`--border/--danger/--muted/--panel` 4 个确实全 css 无定义且 TS 不注入；`--accent` 是 `theme/accents.ts:198` **运行时注入**，静态判「未定义」= 误报 |
| 实际样式失效点 | 「14 变量全部失效」 | 未提 | **仅 7 处无 fallback 的 `border-[var(--border)]`**（crawl-page:455 / comment-panel:230 / crawl-panel:144,154 / stats-page:255,647,661）；带 fallback 的 8 处不失效 |
| 文案泄漏 /api/ | 8 处 | 门禁 R13 PASS 无违规 | **两边都对**：R13 正则只匹配 `description=/hint=` 属性行，而泄漏在 **JSX 文本子节点**（`<span>数据源 GET /api/accounts…</span>`，overview 8 处）⇒ **R13 门禁本身有盲区（假绿）** |

⇒ **前端 P1**：R13 正则需要扩到 JSX 文本子节点，否则 8 处 `/api/` 泄漏永远查不到；顺带修 7 处边框色（`--border` → `--color-border`）。

## §5 测试状态（诚实标注）

- **本人全量跑「挂起」判断有误，已更正**：全量并非挂住，而是**耗时极长**（浏览器测试真机等待）。已按止损律终止的那次以「僵死」定性是**错误结论**
- **确定性子集（排除 7 个浏览器依赖测试）跑通，权威数据**：
  - 22:44 首次：`1 failed, 1463 passed, 4 skipped, 68 subtests, 17:21`
  - 23:04 复跑：`2 failed, 1462 passed, 4 skipped, 68 subtests, 17:39`
  - **同一命令两次运行、失败集不同 ⇒ 非确定性（flaky），非纯顺序依赖**
- **失败项（均单跑或整文件跑为绿，批次内红）**：
  1. `test_login_channel_config.py::test_p8_rpa_scan_routes_by_config_center` —— 断言 `bridge` 通道未被触碰（`touched=[]`）；单跑 0.56s PASS。机制：`_run_rpa_scan_routing`(test_login_channel_config.py:195) 同时替换 `api.accounts._api_scan_login` / `sys.modules["auto_dm.login_remote"]` / **模块级 `asyncio.run`**，`finally` 还原依赖 `getattr(A,"_api_scan_login",None)` 的采样值——若他人 stub 已位则还原成 stub
  2. `test_no_dup_dict_keys.py::test_whole_backend_has_no_duplicate_dict_keys` —— **单跑 4 passed**，仅批次内红（本次复跑才出现，首次未出现）
- **M-17 顺序依赖门禁为何漏 P8**：`_ALLOWED_UNLOADS` 给该文件登了「合理收尾豁免」(test_m17:64)，但 O1 行为门禁 `_POISON_SET` 只含 8 个**固定样本模块**(test_m17:173-182)，P8 及其真实污染源不在集内 ⇒ 固定样本门禁的结构性盲区
- **基线对照（另一会话 14:21 全量日志）**：`Ran 1385 tests in 246s → FAILED (failures=3, errors=1, skipped=4)`，失败全在 `test_design_root_isolation`（design 根 `cookies.sqlite-wal` 指纹漂移——真实数据根被写，疑似并发会话）；14:16 那份 `test_six_sources_aligned` 失败（1 项，随后版本已修）
- **🔴 审计判据「失败集」会随并发提交漂移**：HEAD 在两次运行之间从 `9da7900`(22:06) 前进到 `39a2df3`(23:04, v0.46.41)。审计区间不稳定时，任何「N failed」读数**只对该时刻的树有效**，不得跨提交泛化
- 浏览器依赖的 7 个测试文件（bcc_kernel_unavailable_breaker / browser_visibility_guard / capability_probe / element_inspector_guards / h30_rpa_wiring / live_cred_writeback_independence / two_world_visibility_gate）**本轮未核验**（本人全量那次在此挂起/超时）

## §6 结构层结论

- 大组件 26 个 >800 行（后端 121,746 行）；`recv_daemon.py` 区间 +119 行（1989→2108）最需拆分
- 并发原语 95 处分布合理；硬编码 timeout= 200 处（login_api 16 最多）——量可接受，无异常集中
- 源码树 `%SystemDrive%/ProgramData/...`（968KB 运行期产物）已 gitignore 但未删；`git add -A` 不会入库（已被 ignore）但占盘

## §7 只读并发声明

- 全程只读；审计期间 HEAD 被并发会话从 `24e5d1d`(21:53) 推进到 `9da7900`(22:06)，工作区 +677/-2282 未提交改动**非我所致**
- 本人终止的 pytest（PID 18880/12184）与 playwright 孤儿已确认清理；**未 touch 任何仓库文件、未提交、未清理工作区**

## §8 建议处置顺序

1. P0-1 artifacts/ 凭证 gitignore（分钟级）
2. P0-2 database.py:335 注释/代码对齐（一行）
3. 前端 R13 门禁补 JSX 文本子节点 + 修 8 处 /api/ 文案 + 7 处 `--border`（一个提交）
4. R8-6 门禁扩扫描面 + 正则支持参数化 IN（防再假绿）
5. G2 补 `get_feed_anon` 签名或登记 known-gap
6. R9 计数基点重置到 9da7900（随本次审计归档）
7. P1：补 AI 域 34 个错误码契约；auth_policy 加实测门禁；task_scheduler 持久化排期
