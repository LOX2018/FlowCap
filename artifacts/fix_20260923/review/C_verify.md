# C 线修复报告 · 核查卷宗

- **被审报告**：`artifacts/fix_20260923/C_replay.md`（348 行）
- **仓库**：`C:\Users\LOX\Desktop\DYchajian`　**分支**：`design/better-douyin`　**HEAD**：`b455192b57fe43132318e1ea4d5cfc1554cffc2b`（与报告一致）　**版本**：v0.44.53
- **审查方式**：只读仓库（`git show HEAD:…` / `git diff` / `grep` / `read_file`）；**未跑任何测试**、**未改任何仓库文件**、无 git 写操作。
- **审查日期**：2026-09-23

---

## 1. 自述改动文件清单

报告「改动文件清单」（C_replay.md:295-307）自述 **11 个文件**。逐一核实（`git diff --numstat` / 文件存在性）：

| # | 文件 | 报告声称改动 | 实测（工作区 vs HEAD） | 核实 |
|---|---|---|---|---|
| 1 | `backend/replay/sandbox.py` | P0-5 钉根复位 + 回读自证 | `+64 / -1` | ✔ 命中 |
| 2 | `backend/replay/loader.py` | P3-1 `FixtureUnavailable`+`require_fixture` | `+36 / -0` | ✔ 命中 |
| 3 | `backend/replay/sanitize_db.py` | P0-6 动态清单+默认拒绝+PII 自证 | `+251 / -90` | ✔ 命中 |
| 4 | `backend/replay/selftest.py` | P3-5 `scan_network_calls`+G7b/G7b2/G7c | `+228 / -1` | ✔ 命中 |
| 5 | `backend/scripts/verify_capture_parse.py` | P3-6 skip 计数 + exit≠0 | `+29 / -6` | ✔ 命中 |
| 6 | `backend/test_replay_capture_parse.py` | P3-1 `require_fixture` 替代 SkipTest | `+4 / -7` | ✔ 命中 |
| 7 | `backend/test_replay_conversation_read.py` | P3-1/P3-2/P3-9 | `+43 / -5` | ✔ 命中 |
| 8 | `backend/daemon/browser_daemon.py` | T5-a 仅 line~480 disposable.dispose() | `+30 / -6`（diff 仅 476-511 一段） | ✔ 命中 |
| 9 | `backend/services/env_audit.py` | T5-b 两世界探针 | `+68 / -0` | ✔ 命中（文件 HEAD 已存在，339 行；新增的是探针，见 §6-6） |
| 10 | `DYAutoDM_v2/scripts/verify_live_strategy_live.py` | P3-7 改真退役 | `+36 / -170`，`git status` = ` M` | ✔ 命中 |
| 11 | `backend/test_replay_gates.py` | **新增** 26 项门禁 | 383 行 / 26 个 `def test` / 8 个 class；`git status` = `??`（未跟踪） | ✔ 命中（新文件，未提交批次内正常） |

合计 `789 insertions(+) / 286 deletions(-)`（仅上列 10 个已跟踪文件；#11 为未跟踪新文件，不计入 diffstat）。

### 「未触碰」清单（C_replay.md:309）
报告称未触碰：`probe.py / dispatch.py / dm_dispatch.py / recv_daemon.py / api/* / 前端 / 版本源 / 工作记忆 / artifacts/UP_* / 任何夹具文件`。

- 这些文件中 `core/dispatch.py`、`core/sender.py`、`daemon/recv_daemon.py`、`services/dm_dispatch.py`、`services/probe.py`、`api/*`、`frontend/*` 在工作区**确为 ` M`**，但归属其它线：P0-1 独占 `services/probe.py` / `core/dispatch.py` / `core/sender.py` / `services/dm_dispatch.py` / `daemon/recv_daemon.py` / `api/messages.py`（见 `P0-1_delivery_verify.md:5-9`），A 线独占 `api/live_rooms.py` / `api/live_config.py`，B 线独占 `api/engine.py`。⇒ C 的「未触碰」就其自身写入面成立，**无越界**。
- **夹具文件**（重点核实）：`git status` 对 `backend/replay/fixtures/` **零输出**；`dm_read_fixture.db` 实测 `sha256=ba75d7cee474fb5af37d62d9ab662c30b8b1eb7b6d078c098c5fd9a3787709e1`，`size=409600`，与 `manifest.json` 登记值**逐字节一致** ⇒ 报告「夹具未重生成」的自述**属实**。

---

## 2. 自述测试数字

逐条抄录（行号为 C_replay.md 行号），并标注性质：

| 行号 | 原句 | 性质 | 备注 |
|---|---|---|---|
| 111 | `Ran 5 tests in 0.042s` | **刻意失败/负控** | 修复前 HEAD 版 + 空 fixtures 目录的复现 |
| 112 | `OK (skipped=2)` | **刻意失败/负控** | 正是被修缺陷（假绿）的现场 |
| 113 | `EXIT=0                                <-- 假绿` | **刻意失败/负控** | 同上 |
| 122 | `require_fixture OK -> True` | 反向断言（正控） | 正常夹具必须放行 |
| 123 | `EXIT=0` | 反向断言（正控） | 同上 |
| 138 | `① shutil.copyfile 路径 -> OK，无任何告警，size = 204800    <-- 绕过冻结` | **刻意失败/负控** | 修 P3-2 前后对照 |
| 143 | `test_truncated_sample_red_via_load_fixture` … `→ PASS` | 正控 | 截断样本经 load_fixture 必红 |
| 144 | `- 实跑 python -m unittest test_replay_conversation_read → Ran 14 tests OK。` | **正控** | 与文末 L338 同值 |
| 167-173 | `[PASS] G7`…`[PASS] G7c`（4 条） | 正控 | selftest 扫描范围门禁 |
| 174 | `test_injecting_requests_turns_gate_red … → PASS` | **反向断言（负控）** | 注网必红 |
| 190 | `HEAD EXIT=0                     <-- 假绿` | **刻意失败/负控** | P3-6 修复前 |
| 199 | `EXIT=1                          <-- 真拦` | 正控 | P3-6 修复后 |
| 219 | `DIRECT-RUN EXIT=1` | 正控 | P3-7 拒绝执行 |
| 222 | `IMPORT EXIT=0` | 正控 | P3-7 import 零副作用 |
| 240 | `Ran 14 tests in 1.216s` | 正控 | |
| 241 | `OK            <-- 含 TestManifestExpectedIsLive…` | 正控 | P3-9 |
| 262 | `python -m unittest test_browser_visibility_guard -> Ran 61 tests OK` | 正控 | T5-a 关联（该文件不归 C） |
| 264-267 | `test_no_clear_init_scripts_call PASS` / `test_uses_disposable_dispose PASS` / `test_method_absence_is_asserted PASS` / `test_clear_init_scripts_really_absent PASS` | 正控 | T5-a 4 条 |
| 284-287 | `test_both_worlds_visible_ok PASS` / `test_main_only_is_red PASS` / `test_nothing_visible_is_red PASS` / `test_probe_js_checks_both_cap_keys PASS` | 正控（含负控） | T5-b 4 条 |
| 319-332 | `[PASS] G1`…`[PASS] G9`（14 条） | 正控 | selftest 全量 |
| 334 | `14/14 PASS` | 正控 | selftest 汇总 |
| 335 | `SELFTEST EXIT=0` | 正控 | |
| 337 | `$ python -m unittest test_replay_capture_parse   -> Ran 11 tests  OK` | 正控 | |
| 338 | `$ python -m unittest test_replay_conversation_read -> Ran 14 tests OK` | 正控 | 与 L144/L240 同值 |
| 339 | `$ python -m unittest test_replay_gates            -> Ran 26 tests OK` | 正控 | |

**计数汇总（自述）**：`Ran N tests` 共 **7 处**（5/14/14/61/11/14/26）；`OK` 6 处；`FAILED` **0 处**；`PASS`/`[PASS]` 共 **34 处**（含 selftest 的 14、gates 明细 10、T5 明细 8、正文 2）。
**门禁测试文件实测对账**：`test_replay_capture_parse.py` 11 个 `def test`、`test_replay_conversation_read.py` 14 个（HEAD 为 13，+1 即新增 `TestManifestExpectedIsLive`）、`test_replay_gates.py` 26 个、`test_browser_visibility_guard.py` 61 个 —— **与自述 `Ran N` 逐一吻合**（静态计数，非实跑）。

---

## 3. 唯一标识符

**新增函数/方法**
- `sandbox.py`：`Sandbox._reset_db_bindings()`（P0-5）
- `loader.py`：`require_fixture(name: str) -> dict`
- `selftest.py`：`scan_network_calls(module_name, funcs=None, path=None, source=None)`、`_scan_replay_scope_coverage()`
- `sanitize_db.py`：`build(src_db, out_db)`（重写）、`assert_no_pii(out_db, src_db=None)`
- `verify_capture_parse.py`：`skip(name, detail="")`
- `env_audit.py`：`compare_two_world_visibility(main_world, default_world)`
- `verify_live_strategy_live.py`：`_refuse()`

**新增类 / 异常**
- `loader.FixtureUnavailable(AssertionError)`
- `sanitize_db.UndocumentedTable(RuntimeError)`、`UndocumentedColumn(RuntimeError)`、`PiiLeak(RuntimeError)`

**新增常量**
- `selftest.py`：`REPLAY_EXERCISED_ENTRYPOINTS`（含 `parse_init_protobuf`/`_extract_301_page`/`_parse_301_messages`/`list_conversations`）、`REPLAY_EXERCISED_MODULES`、`REPLAY_SCOPE_IGNORE_TOP`、`REPLAY_AUX_MODULES`、`REPLAY_UNEXECUTED_NET_ALLOWLIST`
- `sanitize_db.py`：`_POLICY`（含 `dm_cross_sink` 全列登记、`dm_conversations.last_msg_preview="blank"`）、`_SKIP_TABLES`、`_TABLE_MODE`
- `env_audit.py`：`TWO_WORLD_PROBE_JS`
- `verify_live_strategy_live.py`：`RETIRED` / `RETIRED_REASON` / `SUCCESSOR`
- `selftest.py` 新增门禁码：`G7b` / `G7b2` / `G7c`；`env_audit.py` 新增 `BCC-070`

**新测试文件名**：`backend/test_replay_gates.py`（383 行）

**新测试类**（8 个，均在 gates）：`TestSanitizeDefaultDeny`、`TestSandboxRepinsExistingConnection`、`TestFixtureMissingHardFails`、`TestCopyfileBypassIsClosed`、`TestZeroNetworkGateScope`、`TestManifestExpectedConsumed`、`TestInitScriptDisposeGate`、`TestTwoWorldVisibilityProbe`；消费者新增 `TestManifestExpectedIsLive`

**断言串（核实）**：`copyfile(loader.fixture_path`（禁止出现）、`load_fixture`（必须出现）、`TestManifestExpectedIsLive`、`"expected"`、`require_fixture`、`SANDBOX_ROOT_OK True`、`__CAP_USERINFO__`、`__CAP_WP_MESSAGE__`、`raise RuntimeError`、`dispose`

---

## 4. 诚实标注（逐条抄录）

1. **L59 / P0-5**：「`clear_current()` 顺带把会员登出；沙箱退出时由 `_saved` 原样还原。本修复只影响显式调用 `Sandbox.activate()` 的回放进程，不改产品运行路径。」
2. **L95-98 / P0-6（未落盘的重生成）**：新管道重生成 `dm_read_fixture`（539 会话/779 消息，`sha256=72b19571…`）在 HEAD 的 `api.messages` 上跑出 **110 条裸 UID 昵称**（`test_no_bare_uid_as_name` 红），「这是**真实数据/产品层问题，非本次 9 项缺陷**」，故**回滚**夹具，仓库夹具保持 `ba75d7ce…`（409600 B），`.old` 备份在 `$LOCALAPPDATA/Temp/fixC/backup/`。
3. **L176 / P3-5**：「被扫模块 `conversation_capture.py` / `api/messages.py` 不归我改，我只扩扫描范围与门禁；两者本身仍含网络调用（其『回放不执行』的函数已在 allowlist 登记）。」
4. **L227 / P3-7**：「**未运行**该脚本对实机的原逻辑（那正是要阻止的行为）；`DIRECT-RUN EXIT=1` 是 `SystemExit` 退出码（非异常栈）。」
5. **L269 / T5-a**：「本次**未做真机浏览器验证**（需 BCC/真 profile，超出 9 项缺陷的离线范围）；判据为 AST 静态断言 + patchright API 实测。」
6. **L289 / T5-b**：「**未接入 bcc_audit 的常驻采集**（`daemon/bcc_audit.py` 不归我改）。已登记为『可供 env_audit/门禁调用的探针 + 机械判据』，接线留待 env_audit 宿主方。」
7. **L344-348 / 诚实标注汇总**：①夹具未重生成；②T5-a 未真机验证；③T5-b 未接线常驻采集；④P3-7 未运行原实机逻辑；⑤**未跑全量 `unittest discover`**（只跑 selftest + 三个回放测试模块）。
8. **L7（范围声明）**：「仅跑 `python -m replay.selftest` + 三个回放测试模块（**未跑全量 discover**）」。

---

## 5. 交叉判据（grep/facts 核实）

全部命令在 `C:\Users\LOX\Desktop\DYchajian\DYAutoDM_v2\backend` 下执行（bash/MSYS）：

| # | 判据 | 命令 | 结果 |
|---|---|---|---|
| 1 | `loader.FixtureUnavailable` | `grep -n "FixtureUnavailable" replay/loader.py` | **4 命中**（L94 class、L113/117/124 raise）✔ |
| 2 | `loader.require_fixture` | `grep -n "require_fixture" replay/loader.py` | **2 命中**（L103 def、docstring）✔ |
| 3 | `selftest.scan_network_calls` | `grep -n "scan_network_calls" replay/selftest.py` | **3 命中**（L123 def、L368/L375 调用）✔ |
| 4 | `verify_capture_parse` skip 计数改动 | `grep -n "SKIP\|skip" scripts/verify_capture_parse.py` | **命中**：`PASS, FAIL, SKIP = [], [], []`（L46）、`def skip`（L54）、`skip("C 段…")`（L133）、汇总改 `通过 X/Y…未执行(skip)W`（L213-229）、`sys.exit(0 if (not FAIL and not SKIP) else 1)`（L230）✔ |
| 5 | `test_replay_gates.py` 存在且规模 | `wc -l test_replay_gates.py` / `grep -c "def test"` | **383 行 / 26 项 / 8 class** —— 与「26 项」自述一致 ✔ |

**修复前定位复核**（`git show HEAD:<file>` 逐条比对，证明 C 对「缺陷位置/行号」的描述准确）：

| 报告声称 | HEAD 实测 | 结论 |
|---|---|---|
| `sanitize_db.py:228` 硬编码表清单漏 `dm_cross_sink`（L65/L67） | HEAD L228 = `for t in ("dm_uid_sink","ai_leads","crawl_history","tasks","kv_store")`，L231 `except sqlite3.OperationalError: continue` | ✔ 行号与判据均准 |
| `loader._read_manifest()` 缺 manifest 返回 `{}`（L104） | HEAD loader.py L34-38 `if not exists: return {}` | ✔ |
| `selftest.py:39-59` 只 AST 扫 `replay/`（L150） | HEAD L39 `def _scan_forbidden_imports(pkg_dir)`，L173 调用 | ✔ |
| `verify_capture_parse.py:124` C 段全 skip（L182） | HEAD L124 `[SKIP] 未找到真实首包样本`；L207 `sys.exit(1 if FAIL else 0)` | ✔ |
| `browser_daemon.py:480` `clear_init_scripts`（L249） | HEAD L479-482 `await self._context.clear_init_scripts()` + `except: logger.debug` | ✔ |
| `test_replay_capture_parse.py:121` SkipTest（L104） | HEAD L120-122 `raise unittest.SkipTest("尚未录制 init_packet 样本")` | ✔ |
| `test_replay_conversation_read.py:68` SkipTest（L104） | HEAD L67-68 `raise unittest.SkipTest(...)` | ✔ |
| `test_replay_conversation_read.py:72` `shutil.copyfile`（L131） | HEAD L72 `shutil.copyfile(loader.fixture_path(FIXTURE), _dbfile)` | ✔ 行号精确 |
| P3-7 HEAD 版 `PORT/BASE/_TOKEN/req` 在模块级、无 guard（L206/L213） | HEAD 版 3170 行→改后 206 行删减；工作区 ` M`，现为 `if __name__=="__main__": _refuse()` | ✔ |
| `dm_read_fixture` sha256 未变 `ba75d7ce…`（L96/L309） | 实测 `sha256sum` = `ba75d7cee474fb5af37d62d9ab662c30b8b1eb7b6d078c098c5fd9a3787709e1`，409600 B，`git status` 干净 | ✔ |

**缺失项**：无。上列标识符/文件/判据**全部命中，无「缺失」**。

---

## 6. 疑点

1. **门禁文件未跟踪**：`backend/test_replay_gates.py` `git status` = `??`（不入索引）。报告称「新增」与事实一致，但在本批「未提交未审查」语境下，该文件不在 diff 追踪面内 —— 合并/提交时须显式 `git add`，否则门禁会**静默缺席**。
2. **测试数字不可独立复现**：全部 `Ran N`/`OK`/`PASS` 均为报告自述的终端转录。本审查受「只读 + 不跑测试」硬约束，仅能核实「被测代码的 `def test` 数与自述 `Ran N` 逐一吻合」（静态），**无法验证实跑结果**。
3. **解释器不一致未说明**：报告 L6 用 `Python314`；同仓其它线（如 P0-1 L118）记「`get_type_hints` 3.11 必炸 ⇒ 3.14」。解释器版本选择未在 C 报告中标注理由，亦无法核实。
4. **退役脚本正文与代码注释不一致（自相矛盾）**：`verify_live_strategy_live.py` 顶部注释（文件 L18）写「都在 `__main__` guard 内立即 `SystemExit(2)`」，而实际 `_refuse()` 是 `raise SystemExit(msg)` ⇒ **退出码 1**；报告正文 L219/L227 记 `DIRECT-RUN EXIT=1`（与实跑一致）。即：报告正文对、脚本内注释错（`SystemExit(2)` 措辞与代码不符）。属文档细节瑕疵。
5. **`env_audit.py` 措辞失真（轻微）**：报告 L275 写 `backend/services/env_audit.py（新增）`，但该文件**在 HEAD 已存在（339 行）**，C 实际是 `+68/-0` 追加探针。「新增」指的是探针/函数，非文件；表格 L305 表述正确。易误读为「新建文件」。
6. **诚实标注覆盖面完整**：未发现被隐藏的未验证项；报告列出的 5 条未验证/未接线（§4）与代码事实一致，无夸大「已跑通」的表述。
7. **P0-6 的「真实活库 26 行」仅单方陈述**：报告 L93 称「真实活库 `members/m17db0f8209156f26/…` 跑新管道 `dm_cross_sink` 26 行正确导出」。该活库不在仓库内、本审查无法读取 ⇒ 该条为**不可核实的具体数字**（合成源库部分可由 `test_replay_gates.TestSanitizeDefaultDeny` 静态复核其判据逻辑）。

**本卷宗认定的 open risks（6 项，未闭合）**：
1. T5-b 两世界探针**未接线**到 `bcc_audit` 常驻采集（仅探针与门禁就绪）。
2. T5-a `disposable.dispose()` 改动**未经真机浏览器验证**（仅 AST 静态断言 + patchright API 实测）。
3. 夹具重生成暴露的 **110 条裸 UID 昵称**问题被**回滚、未修**（归因于产品层，但仍在）。
4. 全部测试数字**无法独立复现**（本审查受「不跑测试」约束，仅静态计数吻合）。
5. `test_replay_gates.py` **未跟踪**，提交/合并时若漏 `git add` 则门禁静默缺席。
6. 退役脚本内注释「`SystemExit(2)`」与实际退出码 1 不符（文档瑕疵，非行为缺陷）。

---

## 结论

- **文件清单**：11 项自述全部落盘且与 `git diff` 吻合；「未触碰」清单与其它线的归属声明无冲突；夹具未改（sha256 实测一致）。
- **标识符**：§3 全部标识符在源码中**逐一命中**，无缺失、无张冠李戴；缺陷定位行号（HEAD 侧）逐条复核准。
- **测试数字**：自述的可疑点仅在于「无法独立复现」；静态计数（`def test`）与自述 `Ran N` 全数吻合。
- **风险**：6 项自认/发现的未验证或未接线（T5-b 未接线最重；夹具重生成暴露的 110 条裸 UID 问题被回滚、未修）。
