# C · 回放层 / 验收脚本「门禁假绿」修复报告

**日期**: 2026-09-23 | **项目**: DYAutoDM_v2 | **分支**: design/better-douyin
**HEAD**: b455192 | **版本**: 0.44.53
**隔离根**: `DY_APP_ROOT=$LOCALAPPDATA/Temp/fixC`
**解释器**: `C:\Users\LOX\AppData\Local\Programs\Python\Python314\python.exe`
**范围**: 仅跑 `python -m replay.selftest` + 三个回放测试模块（**未跑全量 discover**）

---

## 0. 结论速览

| # | 缺陷 | 修复前（红/假绿） | 修复后（绿/真拦） | 状态 |
|---|---|---|---|---|
| P0-5 | 沙箱钉根对已固化连接无效 | `IS SANDBOX ROOT? False` | `IS SANDBOX ROOT? True` | ✅ |
| P0-6 | 脱敏漏表 + 列白名单透传 | 漏 `dm_cross_sink`(0行)+明文透传 | 动态表清单+默认拒绝+PII自证 | ✅ |
| P3-1 | 缺夹具 SkipTest → exit 0 | `OK (skipped=2)` EXIT=0 | `FixtureUnavailable` EXIT=1 | ✅ |
| P3-2 | copyfile 绕过 sha256 冻结 | 截断样本零告警通过 | 走 load_fixture → Tampered | ✅ |
| P3-5 | zero-network 只扫 replay/ | 只扫 replay/=恒绿 | 覆盖产品模块+注网变红 | ✅ |
| P3-6 | C 段空转仍「6/6 通过」exit 0 | `6/6 通过` EXIT=0 | `通过 6/7，skip 1` EXIT=1 | ✅ |
| P3-7 | 退役脚本仍可执行改实机 | 无 guard、裸代码发请求 | `__main__` guard + 立即退出 | ✅ |
| P3-9 | manifest.expected 死元数据 | 无消费者 | 消费者读 expected 并与 SQL 对账 | ✅ |
| T5-a | `clear_init_scripts` 恒静默 no-op | 方法不存在，被 except 吞 | disposable.dispose() + 断言 | ✅ |
| T5-b | 缺两世界可见性探针 | 无 | env_audit 探针 + 门禁测试 | ✅ |

**新增测试**：`backend/test_replay_gates.py`（26 项，全绿）。
**最终 `python -m replay.selftest`**：`14/14 PASS`，EXIT=0（见文末）。

---

## P0-5 沙箱「钉根」对已固化连接无效

- **位置**：`backend/replay/sandbox.py:70-94`（`Sandbox.activate`）；受害点 `backend/database.py:84-85`（`_conn` 早退）。
- **判定**：只改 `DY_APP_ROOT`，而 `database.get_db()` 命中已建全局 `_conn` 会**早退**，返回旧根连接。`unittest discover` 同进程导入顺序即满足该前提 ⇒ 沙箱隔离不成立。
- **修法**（`replay/sandbox.py`）：
  1. `activate()` 保存并清空会员上下文（`DY_MEMBER/DY_MEMBER_KEY` + `member_ctx.clear_current()`）——否则 `_db_path()` 经 `member_ctx.db_path()` 绕过 `DY_APP_ROOT` 指向**真实会员库**；
  2. 调 `database.reset_connection()` 强制关闭全局 `_conn`；
  3. **回读自证**：真跑 `get_db()` 并核对 `PRAGMA database_list` 的库文件落在沙箱根内，否则 `raise`；
  4. `cleanup()` 也复位连接（Windows 上连接句柄会阻碍删除沙箱目录）。

**修复前证据**（`$LOCALAPPDATA/Temp/fixC/repro_p05.py`，HEAD 版 sandbox）：
```
before activate: db path = C:\...\Temp\fixC\oldroot\data\dyautodm.db
sandbox root = C:\...\Temp\dybc_replay_p05_8yenxoov
env DY_APP_ROOT = C:\...\Temp\dybc_replay_p05_8yenxoov
after activate: db path = C:\...\Temp\fixC\oldroot\data\dyautodm.db
same conn object? True
IS SANDBOX ROOT? False          <-- 假隔离
```

**修复后证据**（同一脚本）：
```
after activate: db path = C:\...\Temp\dybc_replay_p05_z3nljglt\data\dyautodm.db
same conn object? False
IS SANDBOX ROOT? True           <-- 钉根生效
```
> 该路径已固化为永久回归断言：`test_replay_gates.TestSandboxRepinsExistingConnection`（子进程隔离执行，防串库）。

- **诚实标注**：`clear_current()` 顺带把会员登出；沙箱退出时由 `_saved` 原样还原。本修复只影响显式调用 `Sandbox.activate()` 的回放进程，不改产品运行路径。

---

## P0-6 脱敏漏面（漏表 + 列名白名单透传）

- **位置**：`backend/replay/sanitize_db.py:228`（硬编码表清单漏 `dm_cross_sink`）、`_row_sanitize`（列名白名单，未命中列原样透传）。
- **判定**：
  - 旧清单 `(dm_uid_sink, ai_leads, crawl_history, tasks, kv_store)` **漏 `dm_cross_sink`**（`database.py:231`，含 `peer_uid/nickname` PII）→ 实测夹具中该表 **0 行**（表建了、行静默丢弃，`except OperationalError: continue`）；
  - `_row_sanitize` 对未登记列**原样透传** → 实测未登记的 `last_msg_preview / contact_wechat` 明文进夹具，schema 漂移即静默泄漏。
- **修法**（重写 `replay/sanitize_db.py`）：
  1. **表清单从 schema 动态取**（`sqlite_master`）——不漏新表；
  2. **默认拒绝**：`_POLICY` 显式登记每张表**每一列**的 transform；未登记表 → `UndocumentedTable`，未登记列 → `UndocumentedColumn`（fail loud，绝不静默丢弃/透传）。已补登记 `dm_cross_sink`（全列）与 `last_msg_preview=blank`；
  3. **产物自证** `assert_no_pii(out, src)`：PII 列的源库原文、非占位 URL、未登记 CJK 明文任一命中 → `PiiLeak`（不产出半成品）。

**修复前证据**（`$LOCALAPPDATA/Temp/fixC/repro_p06.py`，合成源库）：
```
BEFORE: 旧硬编码表清单 = ('dm_uid_sink','ai_leads','crawl_history','tasks','kv_store')
BEFORE: 漏掉的表 = ['dm_conversations','dm_cross_sink','sqlite_sequence']
        → dm_cross_sink 26 行会被静默丢弃(continue)
BEFORE: 未登记列 last_msg_preview / contact_value 旧实现原样透传 → 明文进夹具
BEFORE: '你今晚方便电话吗 13800138000' 会原样留在夹具（零告警）
```
**修复后证据**（同一脚本）：
```
AFTER: stats = {'ai_leads':1,'dm_conversations':1,'dm_cross_sink':1}
AFTER: dm_cross_sink 行数 = 1 (不再静默丢弃)
AFTER: last_msg_preview = ''
AFTER: contact_value = 'REDACTED'
AFTER: PII 明文字符串是否残留在夹具文件？ False
REVERSE-1: 漏表 → UndocumentedTable ✓
REVERSE-2: 未登记列 → UndocumentedColumn ✓
REVERSE-3: 残留 PII → PiiLeak ✓
```
真实活库（`members/m17db0f8209156f26/data/dyautodm.db`）跑新管道：`dm_cross_sink` 26 行被正确导出且 `assert_no_pii` 通过（旧管道为 0 行）。

- **诚实标注（未落盘的重生成）**：我用新管道重生成过 `dm_read_fixture`（539 会话/779 消息，sha256=`72b19571…`），但在当前 HEAD 的 `api.messages` + 夹具上跑出 **110 条裸 UID 昵称**（`test_no_bare_uid_as_name` 红）——这是**真实数据/产品层问题，非本次 9 项缺陷**。为不动版本源与其它模块，我**回滚**了该夹具，仓库内的 `dm_read_fixture` 保持原样未改：
  - 现 sha256 = `ba75d7cee474fb5af37d62d9ab662c30b8b1eb7b6d078c098c5fd9a3787709e1`（409600 B，与 manifest 一致）；
  - `.old` 备份在 `$LOCALAPPDATA/Temp/fixC/backup/`；
  - 报告上述重生成用**合成源库**证明漏面（内容自足、可复跑）。

---

## P3-1 夹具缺失 = SkipTest 静默通过

- **位置**：`test_replay_capture_parse.py:121`、`test_replay_conversation_read.py:68`（`SkipTest`）；`loader._read_manifest()` 缺 manifest 返回 `{}`。
- **判定**：夹具/manifest 缺失时套件**仍 exit 0**（"没跑" 被当 "通过"）。
- **修法**：`replay/loader.py` 新增 `FixtureUnavailable(AssertionError)` 与 `require_fixture(name)`（缺清单/缺样本/空壳条目/篡改一律 `raise`）；两个测试模块的 `setUpClass/setUpModule` 改用 `require_fixture`；把 `test_replay_conversation_read.py` 里「无法接管 get_db」的 `SkipTest` 改为 `RuntimeError`（同样是 hard fail）。

**修复前证据**（HEAD 版测试 + 空 fixtures 目录，`repro_p31.py`）：
```
test_truncated_input_changes_reading ... skipped '尚未录制 init_packet 样本'
Ran 5 tests in 0.042s
OK (skipped=2)
EXIT=0                                <-- 假绿
```
**修复后证据**（同一脚本）：
```
replay.loader.FixtureUnavailable: 回放夹具 'dm_read_fixture' 缺失（清单现有：[]）。
夹具缺失 = 功能未验收，必须 hard fail，不得 SkipTest 静默通过。
EXIT=1                                <-- 真拦

=== 反向断言：夹具正常时必须通过 ===
require_fixture OK -> True
EXIT=0
```
> 固化断言：`test_replay_gates.TestFixtureMissingHardFails`（4 项，含「正常夹具放行」反证）。

---

## P3-2 copyfile 绕过 sha256 冻结

- **位置**：`test_replay_conversation_read.py:72`（`shutil.copyfile(loader.fixture_path(FIXTURE), _dbfile)`）。
- **判定**：唯一消费 dm 夹具的路径绕过 sha256 冻结 ⇒ 截断样本零告警通过（同文件 `load_fixture` 本会抛 `FixtureTampered`）。
- **修法**：改为 `loader.require_fixture(FIXTURE)` + 以 `load_fixture` 取字节写盘（`open(_dbfile,'wb').write(loader.load_fixture(FIXTURE))`）。

**修复前证据**（`repro_p32b.py`，篡改样本）：
```
真实夹具 size 409600 / 截断样本 size 204800
① shutil.copyfile 路径 -> OK，无任何告警，size = 204800    <-- 绕过冻结
② load_fixture      -> FixtureTampered 拒绝 ✓
```
**修复后证据**：
- 静态门禁 `test_replay_gates.TestCopyfileBypassIsClosed.test_consumer_module_has_no_copyfile_bypass`：断言消费者源码中**不再出现** `copyfile(loader.fixture_path`，且必须含 `load_fixture` → PASS；
- `test_truncated_sample_red_via_load_fixture`：截断样本经 `load_fixture` **必须** `FixtureTampered` → PASS；
- 实跑 `python -m unittest test_replay_conversation_read` → `Ran 14 tests OK`。

---

## P3-5 zero-network 门禁扫描范围太窄

- **位置**：`backend/replay/selftest.py:39-59`（`_scan_forbidden_imports` 只 AST 扫 `replay/` 目录）。
- **判定**：真实被执行的 `auto_dm/conversation_capture.py`(`requests`) 与 `api/messages.py`(`urllib`) 未被扫描 ⇒ G7 恒绿（假绿）。
- **修法**（`replay/selftest.py`）：
  1. 新增 `scan_network_calls()`（AST + 别名解析，按「调用链根名∈网络模块别名」判定，避免 `x.get()` 误报）；
  2. `REPLAY_EXERCISED_ENTRYPOINTS` 声明回放**实际调用**的入口（`parse_init_protobuf`/`_extract_301_page`/`_parse_301_messages`/`list_conversations`）→ G7b 断言**零网络调用**；
  3. `REPLAY_UNEXECUTED_NET_ALLOWLIST` 登记「回放不执行」但含网络的函数 (`fetch_conversation_history`/`capture_userinfo_via_browser`/`_http_get_json`/`_http_post_json`) 为基线，**未登记的新网络调用点 → G7b2 变红**；
  4. `_scan_replay_scope_coverage()` 把「回放测试 import 的本仓产品模块」与声明清单对账 → G7c（范围不可静默缩水）。

**修复前证据**（复刻 HEAD 扫描）：
```
HEAD 门禁只扫 replay/ → 命中: [] == > G7 恒绿（假绿）
但真实回放路径 import 的模块含网络：
  auto_dm/conversation_capture.py -> ['requests']
  api/messages.py -> ['fastapi','fastapi.responses','urllib.parse','urllib.request']
```
**修复后证据**：
```
[PASS] G7  replay 包零打网/浏览器 import  — []
[PASS] G7b 执行入口零网络调用（conversation_capture/api.messages）  — 4 个入口均无网络调用
[PASS] G7b2 无未登记网络调用点（防扫描范围缩水/新增打网）  — 无
[PASS] G7c 扫描范围覆盖回放用例 import 的产品模块
       — ['api.messages','auto_dm.conversation_capture','auto_dm.im_protobuf',
          'daemon.browser_daemon','services.env_audit']
```
反证（注网必红）：`test_replay_gates.TestZeroNetworkGateScope.test_injecting_requests_turns_gate_red` —— 合成源码注入 `requests.post(...)` 后 `scan_network_calls` 返回非空（干净源码返回 `[]`）→ PASS。

- **诚实标注**：被扫模块 `conversation_capture.py` / `api/messages.py` 不归我改，我只扩扫描范围与门禁；两者本身仍含网络调用（其「回放不执行」的函数已在 allowlist 登记）。

---

## P3-6 `verify_capture_parse.py` C 段空转仍报通过

- **位置**：`backend/scripts/verify_capture_parse.py:124`（C 段全 skip）+ `sys.exit(1 if FAIL else 0)`。
- **判定**：C 段整体跳过仍打印「6/6 通过」且 exit=0。
- **修法**：新增 `skip()` 计数；C 段缺失改记 `[SKIP]`；汇总有 skip 时不再打印「N/N 通过」，改打印 `通过 X/Y，失败 Z，**未执行(skip) W**`；退出码改 `0 if (not FAIL and not SKIP) else 1`。

**修复前证据**（HEAD 版，临时置入 `scripts/`）：
```
[INFO] 本节不计入通过项（空转的验收 = 未验收，不得当作 PASS）
结果: 6/6 通过
HEAD EXIT=0                     <-- 假绿
```
**修复后证据**：
```
[C] 真实首包解析（缺陷①+②的症状）
  [SKIP] C 段：真实首包解析（缺陷①+②的症状）  — 未找到真实首包样本 …...
结果: 通过 6/7，失败 0，**未执行（skip）1** —— 未执行 ≠ 通过
未执行项（空转的验收 = 未验收，不得当作 PASS）:
   - C 段：真实首包解析（缺陷①+②的症状）
EXIT=1                          <-- 真拦
```

---

## P3-7 退役脚本不阻止执行、无 guard、会改 :8000 实机

- **位置**：`DYAutoDM_v2/scripts/verify_live_strategy_live.py`（工作区已 `D` 删除，HEAD 仍有**可运行**版本）。
- **判定**：退役横幅只是注释；无 `if __name__=="__main__"` guard；模块级裸代码**无条件对 :8000 实机部署建/改/删策略**。
- **处置选择**：**改为「真退役」**（不删文件，保留追溯），使提交态安全。
  - 模块级零副作用（import 不再发任何请求）；
  - 任何直接运行都在 `__main__` guard 内 `raise SystemExit(...)` 立即退出；
  - 保留 `RETIRED/RETIRED_REASON/SUCCESSOR` 元数据与历史判据说明；承接者为 `scripts/verify_live_strategy.py`。

**修复前证据**：HEAD 版 `PORT/BASE/_TOKEN/req(...)` 均在模块级，`python scripts/verify_live_strategy_live.py` 会立即向 `http://127.0.0.1:8000` 发真实请求并写策略（无 guard）。
**修复后证据**：
```
$ python scripts/verify_live_strategy_live.py
[RETIRED] ...verify_live_strategy_live.py 已退役，拒绝执行：判据写死 v0.43.93...
请改用承接者：scripts/verify_live_strategy.py
DIRECT-RUN EXIT=1
$ python -c "importlib... exec_module"
IMPORT: no side effects, RETIRED= True
IMPORT EXIT=0
has __main__ guard: True
```
`git status`：` M DYAutoDM_v2/scripts/verify_live_strategy_live.py`（提交态由删除变为「真退役」版）。

- **诚实标注**：**未运行**该脚本对实机的原逻辑（那正是要阻止的行为）；`DIRECT-RUN EXIT=1` 是 `SystemExit` 退出码（非异常栈），符合「立即拒绝」。

---

## P3-9 manifest `expected` 死元数据

- **位置**：`replay/fixtures/manifest.json` 的 `dm_read_fixture.expected`；消费者 `test_replay_conversation_read.py` 只用 SQL 现算期望，从不读 manifest。
- **判定**：两份判据各自演化（`init_packet` 的 expected 有消费者，`dm_read_fixture` 无）。
- **修法**：**让消费者读 manifest 的 `expected`**（非删字段）——新增 `TestManifestExpectedIsLive`，把 `_entry["expected"]` 与夹具现算真值逐项互证；`setUpModule` 用 `require_fixture` 取 entry，确保 expected 存在。

**修复后证据**：
```
python -m unittest test_replay_conversation_read
Ran 14 tests in 1.216s
OK            <-- 含 TestManifestExpectedIsLive.test_manifest_expected_matches_sql_truth
```
`test_replay_gates.TestManifestExpectedConsumed` 断言消费者确实 import/读取 `expected` 且 `dm_read_fixture` 有 expected 块 → PASS。

---

## T5-a `clear_init_scripts` 恒静默 no-op

- **位置**：`backend/daemon/browser_daemon.py:480` 附近。
- **判定**：patchright 1.62.3/1.63.0 的 `BrowserContext` **无 `clear_init_scripts`**（实测 `hasattr=False`）⇒ 调用必抛 `AttributeError` 被 `except: logger.debug` **静默吞掉**，「每个 context 恰好一套」意图从未生效。
- **修法**：改为持有 `add_init_script` 返回的 **Disposable**，注入前 `await dispose()` 清理上一批；并对「句柄不可 dispose」**显式 `raise RuntimeError`**（不再静默吞）。

**修复前证据**：
```
>>> from patchright.async_api import BrowserContext
>>> hasattr(BrowserContext, 'clear_init_scripts')
False                     <-- 方法不存在
```
HEAD 源码：`try: await self._context.clear_init_scripts() except Exception: logger.debug(...)` → 恒 no-op。
**修复后证据**：
```
python -m unittest test_browser_visibility_guard   -> Ran 61 tests OK
test_replay_gates.TestInitScriptDisposeGate:
  test_no_clear_init_scripts_call        PASS (AST 判据：全文件零 clear_init_scripts 调用)
  test_uses_disposable_dispose           PASS
  test_method_absence_is_asserted        PASS (含 raise RuntimeError)
  test_clear_init_scripts_really_absent  PASS (实测方法确实不存在)
```
- **诚实标注**：本次**未做真机浏览器验证**（需 BCC/真 profile，超出 9 项缺陷的离线范围）；判据为 AST 静态断言 + patchright API 实测。

---

## T5-b 两世界可见性探针

- **位置**：`backend/services/env_audit.py`（新增）。
- **判定**：`add_init_script` 注入后读取侧在**默认世界** `page.evaluate`；若注入落进隔离世界 → 主世界看得到、默认世界读不到 ⇒「注入成功却读到空」，**且不报错**（假成功）。原无任何门禁。
- **修法**：`env_audit.py` 新增 `TWO_WORLD_PROBE_JS`（探针：主世界读 `__CAP_USERINFO__/__CAP_WP_MESSAGE__`）与 `compare_two_world_visibility(main_world, default_world)`：
  - 主世界无任何 CAP_* 键 → `BCC-070 fatal`（注入未生效）；
  - 主世界有、默认世界无 → `BCC-070 fatal`（跨世界不可见）；
  - 一致 → ok。

**修复后证据**（`test_replay_gates.TestTwoWorldVisibilityProbe`，4 项全绿）：
```
test_both_worlds_visible_ok     PASS
test_main_only_is_red           PASS  <-- 主有默无 → fatal（正是复发形态）
test_nothing_visible_is_red     PASS
test_probe_js_checks_both_cap_keys PASS
```
- **诚实标注**：**未接入 bcc_audit 的常驻采集**（`daemon/bcc_audit.py` 不归我改）。已登记为「可供 env_audit/门禁调用的探针 + 机械判据」，接线留待 env_audit 宿主方。patchright 的注入经 `install_inject_route`（主世界）——此探针把该设计前提钉成可验证判据。

---

## 改动文件清单

| 文件 | 改动 |
|---|---|
| `backend/replay/sandbox.py` | P0-5：activate 强制复位 DB 连接+会员上下文+回读自证；cleanup 复位 |
| `backend/replay/loader.py` | P3-1：新增 `FixtureUnavailable` + `require_fixture()` |
| `backend/replay/sanitize_db.py` | P0-6：动态表清单 + 默认拒绝（表/列）+ 产物 PII 自证；补登记 dm_cross_sink/last_msg_preview |
| `backend/replay/selftest.py` | P3-5：`scan_network_calls` + G7b/G7b2/G7c；范围对账 |
| `backend/scripts/verify_capture_parse.py` | P3-6：skip 计数 + 不再「N/N 通过」+ skip 也 exit≠0 |
| `backend/test_replay_capture_parse.py` | P3-1：require_fixture 替代 SkipTest |
| `backend/test_replay_conversation_read.py` | P3-1/P3-2/P3-9：require_fixture + load_fixture 替代 copyfile + expected 消费 |
| `backend/daemon/browser_daemon.py` | T5-a：仅 line~480 的 init script 清理改 disposable.dispose() + 断言 |
| `backend/services/env_audit.py` | T5-b：两世界可见性探针 + 比较函数 |
| `DYAutoDM_v2/scripts/verify_live_strategy_live.py` | P3-7：改为真退役（guard + 立即退出，零网络） |
| `backend/test_replay_gates.py` | **新增**：26 项门禁回归测试 |

**未改动**：`probe.py / dispatch.py / dm_dispatch.py / recv_daemon.py / api/* / 前端 / 版本源 / 工作记忆 / artifacts/UP_* / 任何夹具文件`（`dm_read_fixture` sha256 保持 `ba75d7ce…`）。

## 新增测试

- `backend/test_replay_gates.py`（26 项）：P0-5 钉根、P0-6 默认拒绝/自证、P3-1 hard fail、P3-2 copyfile 闭合、P3-5 范围与注网变红、P3-9 expected 消费、T5-a/T5-b 门禁。

## 实测输出尾部

```
$ DY_APP_ROOT=$LOCALAPPDATA/Temp/fixC python -m replay.selftest
  [PASS] G1 清单可读且有样本  — ['dm_read_fixture','dom_list_snapshots','idb_userinfo','init_packet']
  [PASS] G2 样本加载且 sha256/size 校验通过  — dm_read_fixture 409600B
  [PASS] G3 清单 sha 与样本不符 → FixtureTampered 拒绝  — FixtureTampered
  [PASS] G4 沙箱拒绝真实数据根/源码树（4 项）  — 4/4 均被拒
  [PASS] G4b 临时目录被放行（门禁非无脑全拒）
  [PASS] G5 沙箱把 DY_APP_ROOT 钉到独立临时根
  [PASS] G5b 退出后根目录已回收且 env 已还原
  [PASS] G6 materialize 落盘 + 拒绝 .. 逃逸
  [PASS] G7  replay 包零打网/浏览器 import  — []
  [PASS] G7b 执行入口零网络调用（conversation_capture/api.messages）  — 4 个入口均无网络调用
  [PASS] G7b2 无未登记网络调用点（防扫描范围缩水/新增打网）  — 无
  [PASS] G7c 扫描范围覆盖回放用例 import 的产品模块
  [PASS] G8 两次独立沙箱读数逐字节一致  — 409600B
  [PASS] G9 未知样本名 → FixtureMissing

14/14 PASS
SELFTEST EXIT=0

$ python -m unittest test_replay_capture_parse   -> Ran 11 tests  OK
$ python -m unittest test_replay_conversation_read -> Ran 14 tests OK
$ python -m unittest test_replay_gates            -> Ran 26 tests OK
```

## 诚实标注汇总

1. **夹具未重生成**：新管道重生成 dm 夹具会暴露 110 条裸 UID 昵称（真实数据/产品层问题，非本 9 项），故已回滚；仓库夹具 sha256 未变。漏面用合成源库自足证明。
2. **T5-a 未做真机验证**：判据为 AST 静态断言 + patchright API 实测（`hasattr=False`）。
3. **T5-b 未接入常驻采集**：探针与判据已就绪并测试，接线 `bcc_audit` 需宿主方（文件不归我）。
4. **P3-7 未运行原实机逻辑**（那正是要阻止的行为）；`DIRECT-RUN EXIT=1` 为 `SystemExit`。
5. **未跑全量 `unittest discover`**（按任务硬性要求，只跑 selftest + 三个回放测试模块）；未 ad-hoc 修改任何非独占文件。
