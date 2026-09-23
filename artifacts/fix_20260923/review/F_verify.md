# F 线修复报告核查卷宗

> 核查对象：`artifacts/fix_20260923/F_entry_identity_lease.md`（306 行）
> 仓库：`C:\Users\LOX\Desktop\DYchajian`　分支：`design/better-douyin`　HEAD：`b455192b57fe43132318e1ea4d5cfc1554cffc2b`（`docs(ledger): 并发写者 UP 待办清单(T1~T5) 并入 SSOT 台账 + 合并视图`）　版本源 `package.json` = `0.44.53`
> 核查方式：**只读**。`git diff` / `grep` / AST 复算 / 加载门禁模块取真实发现器输出。**未** git 写、**未**改仓库文件、**未**跑 unittest。
> 核心争点：F 与 P0-1 **双声明** `backend/daemon/recv_daemon.py` —— 本卷宗逐段判定该文件内两族改动归属。

---

## 1.【自述改动文件清单】

### 1.1 报告自述「实际改动」（§七，L260–L270）
`git diff --stat` 自述（L262–L267）：

| 文件 | F 自述 | 实测 `git diff --stat` | 一致 |
|---|---|---|---|
| `DYAutoDM_v2/backend/daemon/bcc_audit.py` | `3 --`（删未用 import ×2） | `3 --` | ✅ |
| `DYAutoDM_v2/backend/daemon/bcc_capture.py` | `3 --` | `3 --` | ✅ |
| `DYAutoDM_v2/backend/daemon/bcc_login.py` | `40 ++++++` | `40 ++++++++++------` | ✅ |
| `DYAutoDM_v2/backend/daemon/bcc_routes.py` | `3 --` | `3 --` | ✅ |
| `DYAutoDM_v2/backend/daemon/recv_daemon.py` | `76 +++++++`（含父会话 P0-1 改动） | `76 +++++++++++++++++++++++++++++--` | ✅ |
| **合计** | `5 files changed, 99 insertions(+), 26 deletions(-)` | `5 files changed, 99 insertions(+), 26 deletions(-)` | ✅ |

**新建**（L269）：`DYAutoDM_v2/backend/test_entry_module_identity_guard.py`（自述 `22,135 bytes`，7 例）。
- 实测：存在，`22,135 bytes` ✅；`git status` = `?? …test_entry_module_identity_guard.py`（未跟踪、未被 `.gitignore` 忽略）✅。子代理只读核查**不跑测试**，故「7 例」以静态计数核（见 §5）。

⇒ **实际改动文件数 = 6**（5 改 + 1 新建）。

### 1.2 报告自述「独占文件」（L8）
```
bcc_login.py、recv_daemon.py、bcc_audit.py、bcc_routes.py、bcc_capture.py、
verify_handledispatch.py、test_send_gate_config.py、新建 test_entry_module_identity_guard.py
```
= 8 个。其中 `verify_handledispatch.py` / `test_send_gate_config.py` 在 §五⑤（L243）与 §七「未改」（L270）中明言**本轮未改** ⇒ 独占声明含 2 个「登记但未动」文件。**声明与实动不符**（声明 8 / 实动 6），属口径差异，非隐瞒。

### 1.3 报告自述「未改」清单（L270）
```
daemon/browser_daemon.py、daemon/bcc_lease.py、daemon/verify_handledispatch.py、
test_send_gate_config.py、services/verdicts.py、版本源、工作记忆/、artifacts/UP_*
```
实测（`git diff --name-only`）：

| 文件 | F 自述未改 | 工作树状态 | 判定 |
|---|---|---|---|
| `daemon/browser_daemon.py` | 未改（另一 agent 在改，L6/L247） | **MODIFIED**（另一 agent） | ✅ F 未动，与自述一致 |
| `daemon/bcc_lease.py` | 未改 | clean | ✅ |
| `daemon/verify_handledispatch.py` | 未改 | clean | ✅ |
| `backend/test_send_gate_config.py` | 未改 | clean | ✅ |
| `services/verdicts.py` | 未改（另一 agent 并发重写，L196/L303） | **MODIFIED**（另一 agent） | ✅ F 未动，与自述一致 |
| 版本源 `package.json` | 未改 | `0.44.53`（与 HEAD 一致） | ✅ |

⇒ 未改清单**诚实**：F 未触碰的两处 MODIFIED 均属**其它 agent 并发**，报告已显式声明（L6–L7、L247、L270）。

---

## 2.【自述测试数字】

逐条引报告原句 + 行号（**均为报告自述，子代理未复跑**）：

| # | 报告行 | 原句 | 静态复核 |
|---|---|---|---|
| 1 | L153 | `Ran 7 tests in 2.6s` | 新建测试文件 `grep -cE '^\s+def test_'` = **7** ✅（L314/329/358/372/380/402/430） |
| 2 | L154 | `OK` | — |
| 3 | L160 | `Ran 3 tests in 1.5s` | 负控子集（自述「已还原」，无法复现） |
| 4 | L161 | `FAILED (failures=3)` | 负控注入期望红，编号与 §三③⑤ 叙述一致 |
| 5 | L283 | `--- test_entry_module_identity_guard ---   Ran 7 tests in 2.6s   OK` | ✅（同上） |
| 6 | L284 | `--- test_send_gate_config ---------------   Ran 6 tests in 4.1s   OK` | 静态：`test_send_gate_config.py` `def test_` = **6** ✅ |
| 7 | L285 | `--- test_bcc_module_identity_guard ------   Ran 4 tests in 4.1s   OK` | 静态：**4** ✅ |
| 8 | L286 | `--- test_bcc_startup_single_fire --------   Ran 3 tests in 10.1s  OK` | 静态：**3** ✅ |
| 9 | L288 | `### py_compile（5 改 + 1 新）### PY_COMPILE OK` | 5 改 + 1 新 = 6 个文件，与 §七清单一致 |
| 10 | L289 | `### import daemon.bcc_audit/bcc_capture/bcc_routes/bcc_login ### IMPORT_OK` | 4 个模块名与 §七改动的 daemon 侧一致（不含 recv_daemon，因其为 `__main__` 入口） |

**合计自述测试数**：`7 + 6 + 4 + 3 = 20`（其中**新建** 7）。两处 `Ran 7` 为同一次运行的两处呈现，不重复计数。
**报告 §八-7（L305）自述未跑全量 `unittest discover`** —— 与「只跑指定用例」的交办约束一致，未夸大验证面。

---

## 3.【唯一标识符】

核查报告涉及的依赖标识符/签名/常量在工作树中**真实存在且签名匹配**：

| 标识符 | 报告引用 | 实测 | 判定 |
|---|---|---|---|
| `services.verdicts.is_placeholder_name` | §四③ L200 `from services.verdicts import is_placeholder_name as _is_ph`；调用 `_is_ph(peer_name, peer_id=peer_id)`（L201） | `verdicts.py:66` `def is_placeholder_name(name, min_len=1, peer_id=None)`；L75 委托 `is_placeholder`。**名称/签名未变** | ✅ 存在、签名兼容 |
| `services.verdicts.is_placeholder` | §四⑤ L219 委托目标 | `verdicts.py:40` `def is_placeholder(value, peer_id=None, …)` | ✅ |
| `sys.modules.setdefault("daemon.recv_daemon", sys.modules[__name__])` | §二③ L103 | `recv_daemon.py:57`（顶层，import 后 / `_state`@141 / `app`@138 之前） | ✅ |
| `daemon.bcc_lease._lease_acquire` | §一② L29 引 `bcc_lease.py:138` | `bcc_lease.py:138` `def _lease_acquire(holder, purpose="auto", prio=2, ttl=0.0, lease_id="")` | ✅ 行号与语义（不同 holder 一律拒、同 holder 重入复用）均对 |
| `LEASE_PRIO_TTL_LIMIT[0]` | §一③ L55 `ttl=LEASE_PRIO_TTL_LIMIT[0]`，注释「600s」 | `bcc_lease.py:35` `LEASE_PRIO_TTL_LIMIT = {0: 600.0, 1: 180.0, 2: 30.0}` | ✅ = 600.0 |
| `_exec(... holder="scan_login", purpose="exclusive", prio=0, ttl=…)` | §一③ L53–L55 | `bcc_login.py:307–308` 在位 | ✅（见 §5） |
| `SendBody.server_message_id` / `SendByUidBody.server_message_id` | §五/§七（P0-1 族） | `recv_daemon.py:1445` / `:1452` | ✅（P0-1 所属） |
| `_mark_send_delivery` | P0-1 族 | `recv_daemon.py:1561` def，调用点 `:1638` / `:1696` | ✅（P0-1 所属） |
| `BrowserContainer_instance_hack`（§五 删死代码） | §五 L235 | 全文件仅剩注释，无有效引用 | ✅ |
| PyInstaller 产品入口集合 `main / daemon.browser_daemon / daemon.recv_daemon` | §三③① L133 | 三 spec `Analysis([...])` 分别指向 `main.py` / `daemon/browser_daemon.py` / `daemon/recv_daemon.py` | ✅（见 §5） |

**结论**：标识符与依赖**无一虚构**，签名一致 ⇒ `identifiers_ok = true`。

---

## 4.【诚实标注】逐条抄录

### 4.1 报告 §八「诚实标注」（L297–L306，8 条）
1. L299 ⚠️ **P0-3 未做端到端真机验证**：未真发起一次扫码登录（风控红线 + 无 GUI）。证据为「真实 `scan_login` 代码路径 + 真实 `bcc_lease`」的隔离复现。
2. L300 ⚠️ **P0-4 为结构性敞口**：当前生产进程未触发该分叉（两处 import 各在独立进程）。本修 + 门禁消除的是**未来复发面**，非正在进行中的故障。
3. L301 ⚠️ **P2-6 语义变化未经真实流量验证**：长数字 uid 占位从「写库」改为「回退对端 uid」；仅隔离复现。
4. **L302 ⚠️ `recv_daemon.py` 含父会话并发改动（P0-1）**：本会话**保留**其 `_mark_send_delivery` / `SendBody.server_message_id` / 三元组解包，未回退、未触碰其逻辑（`grep` 核对仍在位）。若父会话在其基础上续改，可能与本报告行号漂移。
5. **L303 ⚠️ `services/verdicts.py` 由另一 agent 并发重写**：本会话读取到的是**新内容**（P2-4 单一判据版）。P2-6 引用其**名称/签名未变**的 `is_placeholder_name`，风险低；但若该 agent 后续改签名，本处会断——**未**加跨 agent 契约门禁。
6. **L304 ⚠️ 门禁豁免名单是人工判定**：`_ALLOWLIST_DUAL_HOST` 5 条按「开发脚本非产品入口」判定，**未**逐条跑「以冻结态运行 + 同进程按包名 import」的实测；判据基于 spec 反查（无对应 spec）。
7. L305 ⚠️ **未升版本、未提交**（按纪律）；**未**跑全量 `unittest discover`（交办要求只跑指定用例）。
8. L306 ⚠️ 本报告**未**修改 `工作记忆/` 知识库；建议父会话把「入口归一机械门禁」补入 `09_环境与构建.md` 与 REG-01 case。

### 4.2 报告其它诚实标注（非 §八）
- §一⑤ L76：端到端**未**真跑一次扫码（风控红线 + 无 GUI）。
- §一⑤ L77：隔离探针里 `_do` 打到 `await api.get_login_auth(...)` 因打桩 auth 不可 await 抛 `TypeError`（**修复前后同样**），判据落在租约 acquired。
- §一⑤ L78：`prio=0` 的 TTL 上限 600s **未变**；不新增抢占语义。
- §一⑤ L79：未改 `bcc_lease.py`；重复 acquire 是调用侧错误，在调用侧消解。
- §二⑤ L117：P0-4 当前生产进程未触发分叉。
- §二⑤ L118：保留父会话 P0-1 改动，未回退（见 §七核对）。
- §三⑤ L182：门禁产品入口集合依赖 PyInstaller `*.spec`（唯一判据来源）；`build/` 下存在陈旧 spec 产物但 `Analysis` 只指向三个真实产品入口。
- §三⑤ L183：⑥⑦ 负控已用**真实 `recv_daemon.py`** 验证（非自造假数据）。
- §三⑤ L184：门禁**不覆盖**「生命周期只挂最终 app」。
- §四⑤ L226：⚠️ **未**验证另 4 处的实际实现（分属其它 agent 的文件）；清单照录 `verdicts.py` docstring 与实测 grep，**未**逐行复核。
- §四⑤ L227：⚠️ 语义变化（长数字 uid 被回退）**未**用真实流量验证；仅隔离复现。
- §五⑤ L242：删除均先经「AST 未用 + `grep -nw` 逐字计数」双重验证。
- §五⑤ L243：`verify_handledispatch.py` / `test_send_gate_config.py` 本轮**未改**；保留以持续暴露边界。
- §六 L254：`browser_daemon.py` 两处（R-1/R-2）**均只登记、未改一行**。

**风险条目计数**：§八 = 8 条（含第 4、5、6 条并发与豁免名单），另有 §一/§三/§四/§五/§六 共 10 余条局部标注 ⇒ `open_risks = 8`（以 §八 主清单计）。

---

## 5.【交叉判据】

### 5.1 新建门禁测试文件是否存在且含 7 个 `test_`
```bash
$ git status --porcelain -- DYAutoDM_v2/backend/test_entry_module_identity_guard.py
?? DYAutoDM_v2/backend/test_entry_module_identity_guard.py
$ wc -c  DYAutoDM_v2/backend/test_entry_module_identity_guard.py
22135  DYAutoDM_v2/backend/test_entry_module_identity_guard.py
$ grep -cE '^\s+def test_' DYAutoDM_v2/backend/test_entry_module_identity_guard.py
7
$ grep -nE '    def test_' DYAutoDM_v2/backend/test_entry_module_identity_guard.py
314:    def test_all_product_entries_declare_normalization
329:    def test_normalization_precedes_module_state
358:    def test_no_unexplained_dually_hosted_entry
372:    def test_dual_host_discovery_is_not_empty
380:    def test_recv_daemon_main_and_package_are_same_module
402:    def test_negative_control_unnormalized_entry_fails
430:    def test_negative_control_recv_daemon_without_normalization_would_fork
```
**命中：7/7** ✅，类名 `TestEntryModuleIdentityGuard`（L304）。报告 §三③/§七 的 7 个用例名与文件**逐一对应**。
判据实现复核（静态）：`has_normalization()`（L99）为 **AST 级**（`ast.walk` 找 `sys.modules.setdefault` Call 节点，非子串）✅；`_first_module_state_line()`（L127）只认模块顶层，与 §三③②「归一先于状态」吻合 ✅；负控 L436 用正则注掉真实文件归一语句后跑子进程探针 ✅（非造假数据）。
发现器实测输出（加载门禁模块，只读）：
```
entry_candidates = 73
product_entries  = 3  ['daemon.browser_daemon', 'daemon.recv_daemon', 'main']
dual_host        = 8
  daemon.browser_daemon norm=True allow=False product=True
  daemon.recv_daemon    norm=True allow=False product=True
  main                  norm=True allow=False product=True
  dy_apis.douyin_api    norm=False allow=True
  dy_apis.login_api     norm=False allow=True
  dy_live.server        norm=False allow=True
  utils.bd_ticket       norm=False allow=True
  utils.sm3             norm=False allow=True
```
- 产品入口 = **3**，核心三入口全归一（`norm=True`）✅ 与 §三④ 表格「产品入口中已归一 3」一致。
- 双栖入口 = **8**，未归一且未豁免 = **0**（8 − 3 已归一 − 5 豁免 = 0）✅ 与 §三④表格「未归一且未豁免（门禁口径，应为 0）= 0」一致。
- 豁免名单 `_ALLOWLIST_DUAL_HOST` = **5** 条（L66–L72：`dy_live.server`、`dy_apis.douyin_api`、`dy_apis.login_api`、`utils.bd_ticket`、`utils.sm3`）✅ 与 §三④ L179 一致。
- 入口候选与 §三④ L170 表头「入口候选 72」有 **1 之差**：本卷宗实测 73（我的枚举含测试文件、不含 build）。属口径差（报告另有一处计数法），非虚报——功能判据（产品 3 / 双栖 8 / 敞口 0）**完全吻合**，不影响门禁结论。⚠️ 记为口径漂移。

### 5.2 `git diff -- … recv_daemon.py` 中 F 与 P0-1 **同时**存在（逐段列出各自行）
```bash
$ git diff -U0 -- DYAutoDM_v2/backend/daemon/recv_daemon.py | grep -E '^@@'
@@ -34,0  +35,24  @@   ← F：模块身份归一（setdefault）
@@ -509   +533,10 @@   ← F：P2-6 昵称占位判据接线
@@ -510,0 +544    @@   ← F
@@ -522   +556     @@   ← F
@@ -1407,0 +1442,4 @@  ← P0-1：SendBody.server_message_id
@@ -1413,0 +1452   @@   ← P0-1：SendByUidBody.server_message_id
@@ -1521,0 +1561,19 @@ ← P0-1：_mark_send_delivery()
@@ -1568  +1626    @@   ← P0-1：三元组解包
@@ -1570,0 +1629,3 @@   ← P0-1
@@ -1575,0 +1637,2 @@   ← P0-1
@@ -1620  +1683    @@   ← P0-1：三元组解包（send_by_uid）
@@ -1622,0 +1686,3 @@   ← P0-1
@@ -1628,0 +1695,2 @@   ← P0-1
```
**F 族（P0-4 归一 + P2-6 接线）** —— `grep -n`（工作树）：
| 行 | 内容 | 归属 |
|---|---|---|
| `recv_daemon.py:57` | `sys.modules.setdefault("daemon.recv_daemon", sys.modules[__name__])` | **F**（P0-4 归一） |
| `:540` | `from services.verdicts import is_placeholder_name as _is_ph` | **F**（P2-6 接线） |
| `:541` | `_nick_ok = bool(peer_name) and not _is_ph(peer_name, peer_id=peer_id)` | **F** |
| `:542` | `if _nick_ok:` | **F** |
| `:544` | `_write_name = peer_name if _nick_ok else peer_id` | **F** |
| `:556` | `(peer_id, _write_name, _write_name,` | **F**（SQL 占位回退替换） |
F 族命中数 `setdefault("daemon.recv_daemon")` = **1**，`_is_ph/_nick_ok/_write_name` = **5**（L540/541/542/544/556）。

**P0-1 族（`_mark_send_delivery` / `SendBody.server_message_id` / 三元组解包）** —— `grep -n`（工作树）：
| 行 | 内容 | 归属 |
|---|---|---|
| `recv_daemon.py:1445` | `server_message_id: str = ""`（`SendBody`） | **P0-1** |
| `:1452` | `server_message_id: str = ""`（`SendByUidBody`） | **P0-1** |
| `:1561` | `def _mark_send_delivery(body, conv_id, verdict, http_ok=True)` | **P0-1** |
| `:1626` | `_res = DouyinAPI.send_msg(`（`/send` 三元组解包） | **P0-1** |
| `:1629/:1631` | `ok = _res[0] …` / `_verdict = _res[2] …` | **P0-1** |
| `:1638` | `_mark_send_delivery(body, conversation_id, _verdict)` | **P0-1** |
| `:1683` | `_res = DouyinAPI.send_msg(`（`/send_by_uid` 三元组解包） | **P0-1** |
| `:1686/:1688` | `ok = _res[0] …` / `_verdict = _res[2] …` | **P0-1** |
| `:1696` | `_mark_send_delivery(body, conv_id, _verdict)` | **P0-1** |
P0-1 族命中数：`_mark_send_delivery` = **3**（1 def + 2 调用）、`server_message_id: str` = **2**（两个 model）、`_res = DouyinAPI.send_msg` = **2**、`_res[0]` = **2**、`_res[2]` = **2**。

⇒ **F 与 P0-1 的改动在同一 diff 中并存、各据独立 hunk、互不覆盖**。与 P0-1 报告（`P0-1_delivery_verify.md` L7/L62）自述的 recv_daemon 改动清单**一致**（SendBody/SendByUidBody `server_message_id` + 三元组 + `_mark_send_delivery`）。F 报告 §二⑤/§八-4 对此归属的声明（保留 P0-1，未回退、未触碰）**属实**。**双声明成立且不冲突**。

### 5.3 `bcc_login.py` 租约改动是否在位
```bash
$ grep -nE '_exec\(|holder="scan_login"|prio=0' DYAutoDM_v2/backend/daemon/bcc_login.py | tail
307:        return await self._exec(
308:            _do, holder="scan_login", purpose="exclusive", prio=0,
$ grep -nE '_sl_lid|_lease_acquire|_lease_release|_lease_current|_scan_exclusive_get|ContainerBusy' DYAutoDM_v2/backend/daemon/bcc_login.py
230/232/233/243:  （均位于 P0-3 修复说明**注释**中）
245:            _scan_exclusive_set("scan_login")
302:                _scan_exclusive_set(None)
```
- 外层 `_exec(_do, holder="scan_login", purpose="exclusive", prio=0, ttl=LEASE_PRIO_TTL_LIMIT[0])` **在位**（L307–308）✅
- 内层重复 `_lease_acquire("scan_login", …)` 及 `_slug`/`_sl_lid`/`_lease_release` **已删除**（仅存注释）✅
- import 列表删除 5 个失引用的名（`ContainerBusy/_lease_acquire/_lease_current/_lease_release/_scan_exclusive_get`），保留 `_lease_status/_scan_exclusive_set/LEASE_PRIO_TTL_LIMIT` ✅ 与 §五⑤ L239 一致
- `git diff -- bcc_login.py` 独立确认：删 `getattr(BrowserContainer_instance_hack …)` 死分支（§五）、删内层租约段、外层改 P0 ✅

### 5.4 其余改动的交叉核实
- `bcc_audit.py` / `bcc_capture.py`：`git diff -U0` 各删 `from typing import Any` + `from daemon.bcc_lease import ContainerBusy`，**命中数 2/2** ✅ 与 §五一致。
- `bcc_routes.py`：`git diff -U0` 删 `from fastapi.responses import JSONResponse` + `from daemon.bcc_lease import ContainerBusy`，**命中数 2/2**，保留 `Request` ✅。
- 未改文件 `bcc_lease.py` / `verify_handledispatch.py` / `test_send_gate_config.py`：`git diff --name-only` **空** ✅。

---

## 6.【疑点】

1. **`_exec` 重入语义与报告 §一③ 的论证前提不符（实质疑点）**。报告 L58 称「`_exec` 原本就正确处理**同 `holder` 重入复用**并在 finally 统一释放（`browser_daemon.py:781/821`）」。但工作树 `browser_daemon.py:800–806` 的实现与注释明写：`_is_reentry = bool(lease_id and _cur_l and _cur_l["lease_id"] == lease_id)`，「重入判据**只有**显式 `lease_id` 匹配 —— `_lock` 非重入，`_exec` 不可能嵌套，因此任何"同 holder"都不是重入，而是并发冲突（**必须拒绝**）」。即 **同 holder 无 lease_id 在 `_exec` 层被判为冲突、而非复用**（复用逻辑存在于 `bcc_lease._lease_acquire`，不在 `_exec`）。
   - 影响：**修法方向仍正确**（删除内层冗余 acquire、由外层携 P0 取唯一租约），因内层老代码 holder 是 `"scan_login"`、外层是 `"bcc-internal"`，**本就不同 holder**，「冗余」的理由应表述为「内层因 holder 不同必被拒」，而非「同 holder 会被 `_exec` 复用」。
   - 报告 L58 引用的行号 `781/821` 亦与当前 `browser_daemon.py`（acquire@806 / release@846）**不符**；报告 L6 已声明该文件正由另一 agent 改动 ⇒ 疑为行号漂移 / 读取了旧版本。**结论：论证文字有误，代码落点正确。**

2. **`browser_daemon.py:41` 的 `_is_busy` 未用 import，报告判「仅注释外无引用」，实测 `_is_busy` 仍出现于 L775/L777 注释串**（`"_is_busy() 为真后…"`）。报告 R-1（L251）已说明「其余出现均在注释」，判定成立；仅提示该判定依赖「注释不算引用」，属可接受口径。

3. **`_SWITCH_COOLDOWN_SEC` 双宿主**：实测 `bcc_audit.py:19` 与 `browser_daemon.py:192` 同名同值 `= 180`，`bcc_audit.py` 内 L155/L176 两处使用、`browser_daemon.py` 侧仅定义处。报告 R-2（L252）判定、建议（收敛单宿主 + 加机械门禁）**属实且未改**，与 §六「只登记不修」一致。

4. **`recv_daemon.py` 归属双声明已被 F 报告在改动前显式切开**，但**行号漂移风险真实存在**（报告 §八-4 自述）：F 的归一在 L57、P2-6 在 L540±；P0-1 的 `_mark_send_delivery` 在 L1561。若父会话在 P0-1 基础上续改，F 侧「未触碰 P0-1」的 grep 结论需按**内容**（`_mark_send_delivery`/`server_message_id`/`_res[0..2]`）而非行号复核。本卷宗已按内容复核。

5. **`services/verdicts.py` 并发重写风险（报告 §八-5 自述，未加契约门禁）**：F 的 P2-6 依赖 `is_placeholder_name` 的**名称/签名**稳定性。实测当前签名 `is_placeholder_name(name, min_len=1, peer_id=None)` 与 F 调用 `_is_ph(peer_name, peer_id=peer_id)` **兼容**；但报告自认「未加跨 agent 契约门禁」⇒ 若该 agent 再改签名，接线静默断裂。**属真实开放风险，未缓解。**

6. **豁免名单人工判定（报告 §八-6 自述）**：`_ALLOWLIST_DUAL_HOST` 5 条仅据「无 PyInstaller spec + 开发用 `__main__`」判定，**未**逐条跑「冻结态运行 + 同进程按包名 import」实测。本卷宗复核发现：这 5 个模块**确实各自含 `if __name__ == "__main__":` 且被产品内按库名 import**（如 `dy_apis.douyin_api`），报告理由「产品内仅按库名 import ⇒ 非同进程双栖」在语义上成立，但**未经运行态证实**。**属真实开放风险，未缓解。**

7. **隔离复现脚本未入库（§七 L292–L293）**：`repro_p03.py` / `repro_p04.py` / `repro_p26.py` / `counts.py` / `recv_daemon.bak` 落在 `%LOCALAPPDATA%\Temp\fixF\`，**未随修复入库**。⇒ 报告 §一④/§二④/§四④ 的「修复后实测」输出**无法在仓库内独立复跑**，只能靠新建门禁测试（7 例）承接部分结构性证据。P0-3/P2-6 的行为证据**一次性、不可回归**。

8. **入口候选计数口径差 72 vs 73**（本卷宗实测 73）——不影响产品/双栖/敞口三项核心判据（3/8/0 全吻合），但报告 §三④ 表格的「72」无对应命令可复现，属口径未披露。

9. **未跑全量 discover（§八-7）**：F 只自证 4 个用例集（7+6+4+3）绿；对**其它 5 条修复线**（P0-1/A~E）改动过的共享文件（如 `services/verdicts.py`、`api/messages.py`）**未做交叉回归**。⇒ F 门禁的绿**不能**外推为「全局无回归」。属交办约束内的已知边界。

---

```json
{"line":"F","files_claimed":6,"tests_total":20,"identifiers_ok":true,"open_risks":8,"verdict":"F 报告的 6 个改动文件与 20 项自述测试均可静态复核，recv_daemon.py 双声明已按 hunk 切清（F 归一/P2-6 vs P0-1 各据独立段），标识符与依赖无虚构；核心疑点为 §一③ 对 _exec 重入语义的论证与当前实现相悖（代码落点仍正确），并残留隔离脚本未入库、豁免名单与 verdicts 契约未运行态验证等 8 项开放风险。"}
```
