# F · 入口模块身份归一（P0-4）+ 扫码登录 P0 租约（P0-3）—— 修复报告（2026-09-23）

> 执行者：子代理（本会话）　分支：`design/better-douyin`　HEAD：`b455192`　版本：v0.44.53（**未改版本源**）
> 来源：`D:\SJ  agent\AUDIT_2026-09-22_23_FINAL.md` P0-3 / P0-4 / P2-6 / P4 + `_B_report_snapshot_2026-09-23_2036.md` §六-2（S-3 结构性解法）+ 宿主交办
> 隔离：`DY_APP_ROOT=$LOCALAPPDATA/Temp/fixF`；解释器 `C:\Users\LOX\AppData\Local\Programs\Python\Python314\python.exe`（3.14.6）
> **未** `git add/commit/checkout/stash/clean`；**未**改版本源；**未**碰 `browser_daemon.py`（另一 agent 在改，只登记不修）；
> **未**碰 `services/verdicts.py`（另一 agent 在改，本轮其内容被其并发更新，见 §四）；**未**碰 `工作记忆/` 与 `artifacts/UP_*`。
> 独占文件：`daemon/bcc_login.py`、`daemon/recv_daemon.py`、`daemon/bcc_audit.py`、`daemon/bcc_routes.py`、`daemon/bcc_capture.py`、`daemon/verify_handledispatch.py`、`test_send_gate_config.py`、**新建** `test_entry_module_identity_guard.py`。
> 本报告的每条 = ① 位置 ② 判定 ③ 修法 ④ 修复前/后实测输出 ⑤ 诚实标注。

---

## 一、P0-3 · 扫码登录的 P0 独占租约恒不生效

### ① 位置
`backend/daemon/bcc_login.py` — `BccLoginMixin.scan_login()`（旧 L230，现 L229 附近）。

### ② 判定（真缺陷，已运行态复现）
`scan_login` 的 `_do` 在**外层 `self._exec(_do)` 已取租约**的前提下，**内层又取一次 P0 租约**：

```python
# 旧代码（scan_login._do 内）
_sl_lease = _lease_acquire("scan_login", "exclusive", 0, ttl=LEASE_PRIO_TTL_LIMIT[0])
_sl_lid = _sl_lease.get("lease_id")
...
return await self._exec(_do)          # 外层：holder="bcc-internal", prio=2, ttl=30
```

而 `daemon/bcc_lease.py:138 _lease_acquire` 的语义是：**不同 holder 一律拒绝**（「不做真抢占」，见该模块设计决策第 3 条）。故内层取租约**必然失败**：

```
[lease] bcc-internal 获得租约（prio=2 后台保活, ttl=30s, id=b96f07ddf6fb）
[BCC-047] [lease] scan_login(prio=0) 被拒：当前 bcc-internal(prio=2) 持有，剩余 30.0s
```

⇒ `_sl_lid = None` ⇒ **扫码登录的 P0（prio=0）独占租约从未建立**。同一类根因（P3-5 拆分引入的跨模块边界），与已修的 `2ce728a`（模块身份分叉 + startup 双跑）**同源**。

**修复前实测**（`repro_p03.py`，忠实复刻真实 `_exec` 的租约门 + 真实 `BccLoginMixin.scan_login`；`_do` 打到内层租约即停）：

```
INNER_ACQUIRE_CALLS: [('scan_login', 0, False, 'bcc-internal')]
INNER_OK: False
INNER_BUSY: bcc-internal
SL_LID: None
P03_REPRO_BUSY: True        ← 旧实现返回 busy
```

### ③ 修法（三者择一，选「外层不重取、由外层以 P0 取唯一那把租约」并说明为何不破坏原意图）
`_do` **不再内层重取租约**；改由**外层 `_exec` 以 P0 取唯一那把租约**：

```python
# 新代码（scan_login 末尾）
return await self._exec(
    _do, holder="scan_login", purpose="exclusive", prio=0,
    ttl=LEASE_PRIO_TTL_LIMIT[0])          # prio=0 → ttl 上限 600s（覆盖扫码整轮）
```

`_exec` 原本就正确处理**同 holder 重入复用**并**在 finally 统一释放**（`browser_daemon.py:781/821`），故内层那次 acquire 本就冗余。

**为什么这不破坏「租约占用」的原意图**：
1. **「P0 独占」语义仍在**：`prio=0`（用户显式，ttl 上限 600s）由外层作为**唯一**那把租约持有；低优先级调用方（业务 prio=1 / 内部 prio=2）对同一把租约同样被拒 —— 独占面未缩小。
2. **不是「删掉了租约」**：租约仍被取、仍在 `_do` 全周期有效、仍由 `_exec` 释放；被删的只是**重复的那一次 acquire**（外层已持，内层必被自身拒）。若把两行都删了、也不让外层取，才会退化成「无租约」——本修法**没有**这样做。
3. **holder 命名**：`_lease_acquire` 的重入判据是「同 `lease_id` 或同 `holder`」，与 holder 字符串无关；用 `holder="scan_login"` 使 `/status`、日志与语义一致（原先死代码里的 `_sl_lease` 变量仅为取 `lease_id`）。

**修复后实测**（同一个 `repro_p03.py` 打到**真实 `scan_login` 向 `_exec` 传的参数**）：

```
[lease] scan_login 获得租约（prio=0 用户显式, ttl=600s, id=11862470da55）
EXEC_KWARGS: {'holder': 'scan_login', 'purpose': 'exclusive', 'prio': 0, 'ttl': 600.0}
OUTER_LEASE_ACQUIRED_OK: True
OUTER_LEASE_PRIO: 0
P03_FIXED: True
```

### ⑤ 诚实标注
- 端到端**未**真跑一次扫码（风控红线 + 无 GUI）；以上为「真实 `scan_login` + 真实 `bcc_lease` + 忠实 `_exec` 租约门」的隔离复现。
- `scan_login` 的 `_do` 在隔离探针里跑到 `await api.get_login_auth(...)` 因打桩 auth 不可 await 而抛 `TypeError`（**修复前后同样**，与本缺陷无关）；判据落在**租约 acquired**，不落在那步。
- `prio=0` 的 TTL 上限 600s（`LEASE_PRIO_TTL_LIMIT[0]`）**未变**；本修不新增抢占语义。
- 未改动 `bcc_lease.py`（不属我的独占文件）；`scan_login` 的重复 acquire 是调用侧错误，本修在调用侧消解，`_lease_acquire` 语义保持不变。

---

## 二、P0-4 · `recv_daemon.py` 入口无模块身份归一

### ① 位置
`backend/daemon/recv_daemon.py`（模块级，import 后、`_state` 创建前）。反向着：`test_send_gate_config.py:26`、`daemon/verify_handledispatch.py:111`（`import daemon.recv_daemon as rd`）。

### ② 判定（敞口：无归一、无守卫）
该文件**既以 `__main__` 运行**（PyInstaller `build/dyautodm-recv-daemon/*.spec` → `Analysis(['…/daemon/recv_daemon.py'])`；文件末尾 `if __name__ == "__main__": main()`），**又被按包名 import**（上列两处）。修复前全文 `sys.modules.setdefault` **计数 = 0** ⇒ 同进程内 `sys.modules` 两个键并存、模块级可变状态（`_state` / `_send_gate_last` …）分叉。

**修复前实测**（`repro_p04.py`：以 `__main__` 加载入口 → 写 `_state` → `import daemon.recv_daemon` 读回）：

```
RESULT:DIFF
ACCT:[]                     ← 反向着读到空 _state（accounts=[]）
SAME_OBJ:False
```

### ③ 修法（沿用 REG-01 定式）
在 `recv_daemon.py` 的 import 之后、`os.environ.setdefault(...)` 与 `_state` 定义**之前**加入：

```python
sys.modules.setdefault("daemon.recv_daemon", sys.modules[__name__])
```

先到先得（`setdefault`）：以包名正常 import 时已是自己、不动它；以 `__main__` 启动时补上规范名，消除第二份实例。附同源注释（指向 `browser_daemon.py:78` / `main.py:74` 与 REG-01 事故）。

**修复后实测**（同一探针）：

```
RESULT:SAME
ACCT:['GUARD_PROBE_ACCOUNT']
SAME_OBJ:True               ← __main__ 与 daemon.recv_daemon 同模块、同 _state
```

### ⑤ 诚实标注
- 当前**生产进程未触发**该分叉（这两处 import 各自在独立进程）；本修消除的是**结构性敞口**，不是正在发生的故障。
- 保留了父会话的 P0-1 改动（`_mark_send_delivery` / `SendBody.server_message_id` / 三元组解包），未回退（见 §七核对）。

---

## 三、机械门禁 · 「入口归一」覆盖**全部入口**（B 报告 S-3 结构性解法，本批最高价值）

### ① 位置
**新建** `backend/test_entry_module_identity_guard.py`（7 例）。

### ② 判定
既有 `test_bcc_module_identity_guard.py`（4 例）仅**硬编码** `browser_daemon.py` + `main.py`，对其它入口等于**没有门禁**（`mechanical-gate-verification` 第二节：覆盖面本身就是判据）。S-3 建议：把「入口模块身份归一」做成**枚举式**门禁。

### ③ 修法（门禁设计）
| # | 用例 | 判据 |
|---|---|---|
| ① | `test_all_product_entries_declare_normalization` | **产品入口**（从 PyInstaller `Analysis(['<entry>.py'])` **唯一判据来源**反查）必须含归一；核心三入口 `main` / `daemon.browser_daemon` / `daemon.recv_daemon` **必须**在要求集合内（防扫描失败→空集假绿） |
| ② | `test_normalization_precedes_module_state` | 归一语句必须出现在**任何模块级状态创建之前**（AST 取首个模块顶层 `_state/app/…` 赋值行对比） |
| ③ | `test_no_unexplained_dually_hosted_entry` | 枚举全仓 `if __name__ == "__main__":` ∩ 被按包名 import = **双栖入口**；「双栖且未归一」必须 ⊆ 逐条写明理由的豁免名单（新出现的=变红） |
| ④ | `test_dual_host_discovery_is_not_empty` | **正控边界**：发现器必须真能发现 `recv_daemon`/`main`/`browser_daemon`（防发现逻辑退化） |
| ⑤ | `test_recv_daemon_main_and_package_are_same_module` | **行为探针**（子进程）：`__main__` 与 `daemon.recv_daemon` 必须同模块（`_state` 同一对象 / `SAME_OBJ:True`）。判据写文件，防 `main()` 重定向 stdout |
| ⑥ | `test_negative_control_unnormalized_entry_fails` | **负控**：无归一的 tmp 入口 → 源码判据与行为探针都必须红；**被注释掉**的归一语句也不得判通过（AST 判据，修 P3-4 的子串假绿）；同形归一入口必须 SAME（正控，证明非恒红） |
| ⑦ | `test_negative_control_recv_daemon_without_normalization_would_fork` | **负控（真实文件形态）**：把 **真实 `recv_daemon.py`** 的 setdefault 行注掉、拷到 tmp 跑行为探针 → 必须 DIFF |

①③ 用 **AST 级**判据（非子串），专治审计 P3-4 记录的「纯子串匹配 → 把归一语句注释掉仍 PASS」的假绿。

### ④ 实测输出
**正常态（7/7 绿）**：
```
test_all_product_entries_declare_normalization ... ok
test_dual_host_discovery_is_not_empty ... ok
test_negative_control_recv_daemon_without_normalization_would_fork ... ok
test_negative_control_unnormalized_entry_fails ... ok
test_no_unexplained_dually_hosted_entry ... ok
test_normalization_precedes_module_state ... ok
test_recv_daemon_main_and_package_are_same_module ... ok
Ran 7 tests in 2.6s
OK
```

**负控（临时注释真实 `recv_daemon.py` 的归一语句 → 覆盖率类用例必须变红；已还原）**：
```
### 注入后（期望变红）###
Ran 3 tests in 1.5s
FAILED (failures=3)          ← test_all_product_entries / test_no_unexplained / test_recv_daemon_...same_module 全部红
### 还原 ###
57:sys.modules.setdefault("daemon.recv_daemon", sys.modules[__name__])
RESTORED-OK
```

**「未归一入口数」前后对比**（机械计数，`counts.py` 复跑）：
| 指标 | 修复前 | 修复后 |
|---|---|---|
| 入口候选（含 `__main__`，全仓） | 72 | 72 |
| **产品入口**（PyInstaller spec） | 3（`main` / `daemon.browser_daemon` / `daemon.recv_daemon`） | 3 |
| **产品入口中已归一** | **2** | **3** ✅ |
| **双栖入口**（`__main__` 且被按包名 import） | 8 | 8 |
| 双栖且未归一 | 6 | 5 |
| 　其中：产品入口敞口 | **1（`daemon.recv_daemon`）** | **0** ✅ |
| 　其中：已豁免（开发脚本，见下） | 5 | 5 |
| **未归一且未豁免（门禁口径，应为 0）** | 1 | **0** ✅ |

豁免名单（`_ALLOWLIST_DUAL_HOST`，逐条写理由；判据=`__main__` 是开发/自检脚本、非 PyInstaller 入口、产品内仅按库名 import，故不存在「同进程既冻结运行又按包名 import」形态）：`dy_live.server`、`dy_apis.douyin_api`、`dy_apis.login_api`、`utils.bd_ticket`、`utils.sm3`。

### ⑤ 诚实标注
- 门禁的**产品入口集合**依赖 PyInstaller `*.spec`（唯一判据来源）；本机 `backend/build/` 下存在陈旧 spec 产物，但 spec 的 `Analysis(['…entry.py'])` 只指向三个真实产品入口，无假阳性。若将来新增 spec/入口，门禁会**自动纳入**（这正是扩围价值）。
- ⑥⑦ 的负控证明了「注入真实事故形态会变红」；①③ 的负控（覆盖类）亦已用**真实 `recv_daemon.py`** 验证（非自造假数据）。
- 门禁**不覆盖**「生命周期只挂最终 app」（该约束已有 `test_bcc_startup_single_fire.py` 3 例守；且 `browser_daemon.py` 本轮不可碰）。B 报告 S-3 的两条定式中，本轮落地的是**入口身份归一**这一条；生命周期那条既有门禁在位。

---

## 四、P2-6 · 昵称占位判据第 5 处仍内联

### ① 位置
`backend/daemon/recv_daemon.py` — `AccountInbox.get_or_create()` 的 `elif peer_id and not c.peer_id:` 分支（旧 L507–527；交办所指 L517 即该 `UPDATE dm_conversations …`）。

### ② 判定
原分支内联：`if peer_name: c.peer_name = peer_name` + SQL 占位回退用 `peer_name or peer_id`。**只判空**，不判「数字 uid / 等于对端 uid」，与唯一实现 `services/verdicts.is_placeholder_name` 不一致 —— `verdicts.py` docstring 声称「收敛自 **5** 处」实为 **4/5**（第 5 处即此处）。

**关键坐标（本会话实测）**：`services/verdicts.py` 在本轮**被另一 agent 并发重写**为 P2-4 单一判据（`is_placeholder`；`is_placeholder_name`/`is_uid_placeholder` 记为薄封装），其新 docstring 明确把 `recv_daemon.py` 的第 5 处列为「尚未接线，**父会话负责**」⇒ 本任务与该收敛目标一致。核心判据 `is_placeholder_name` **名称与签名未变**，故我引用它安全。

### ③ 修法（**只改 recv_daemon 这一处**，引用唯一实现）
```python
from services.verdicts import is_placeholder_name as _is_ph
_nick_ok = bool(peer_name) and not _is_ph(peer_name, peer_id=peer_id)
if _nick_ok:
    c.peer_name = peer_name
_write_name = peer_name if _nick_ok else peer_id     # 占位 → 回退对端 uid
# UPDATE … 的原 SQL 不变，仅把 peer_name or peer_id 换成 _write_name
```

### ④ 实测输出（`repro_p26.py`，真实 `get_or_create` + in-memory SQLite）
```
6位纯数字 uid   name='999999'        pred=True   旧内联存='999999'      现实现存='999999'
13位 uid        name='1234567890123' pred=True   旧内联存='1234567890123' 现实现存='999999'   ← 分歧点
真实昵称        name='Alice'         pred=False  旧内联存='Alice'        现实现存='Alice'
空              name=''              pred=True   旧内联存='999999'       现实现存='999999'
```
- **修复前后在多种寻常输入上行为一致**（空 / 短数字 / 真实昵称）—— 说明这是**收敛**而非语义翻转，回归面小。
- 分歧恰在**长数字 uid 占位**（`'1234567890123'`）：旧内联把 uid 当昵称写库，新实现回退对端 uid。与 `services/nickname_fallback.missing_nickname_convs` 的「仅处理 peer_name 为空 / 数字 uid、已有真昵称绝不覆盖」意图同源。

### ⑤ 诚实标注（**应收敛到哪个函数、另 4 处在哪** —— 交办要求写清）
- **应收敛到**：`services/verdicts.is_placeholder_name`（→ 委托唯一实现 `services/verdicts.is_placeholder`，阈值 `kernel/truth.UID_MIN_DIGITS`）。
- **另 4 处**（`verdicts.py` docstring 原文）：
  1. `services/probe.py::_is_real_nickname`（L166–169，已收敛）
  2. `services/nickname_fallback.py::_is_uid_placeholder`（L150–153，已收敛）
  3. `api/messages.py` peer_name 过滤（L24 导入 / L342、L368 调用，已收敛）
  4. `recv_daemon.py` 的 SQL AND 条件处（L352 `… OR peer_name=?`，**已在本文件内、早于本轮**接线 `self.my_uid`）
  - 本处（第 5） = `get_or_create` 的 `elif` 分支 —— **本轮接线**。
- ⚠️ **未**验证另 4 处的实际实现（分属其它 agent 的文件）；上述清单照录 `verdicts.py` 的 docstring 与实测 grep，**未**逐行复核。
- ⚠️ 语义变化（长数字 uid 被回退）**未**用真实流量验证；仅隔离复现。

---

## 五、P4 · 死代码 / 未用 import（均已 AST 或 grep 逐一验证「确实无用」后才删）

| ① 位置 | ② 判定 | ③ 修法 | ④ 实测 |
|---|---|---|---|
| `daemon/bcc_login.py:85` | 引用**不存在**的名 `BrowserContainer_instance_hack`，靠 `if False` 短路存活；下一行才是正解（审计 ocr #2 判「真·无功能影响」） | 删该死分支，保留真正的 `cls = type(self)` / `cached = getattr(cls, "_uid_probe_cache", None)` | `grep -n BrowserContainer_instance_hack` → 仅剩注释；`py_compile` OK |
| `daemon/bcc_audit.py:14,18` | `from typing import Any`、`from daemon.bcc_lease import ContainerBusy` **各仅 1 次出现=定义处**，无任何使用 | 删两行 import | `grep -nw` → `Any` 0、`ContainerBusy` 0；`import daemon.bcc_audit` OK |
| `daemon/bcc_routes.py:13,16` | `from fastapi.responses import JSONResponse`（0 处使用）、`from daemon.bcc_lease import ContainerBusy`（未用；函数内用别名 `import … as _ContainerBusy`） | 删两行 import；保留 `Request`（L620 真用） | `JSONResponse` 0、顶层 `ContainerBusy` 0（函数内别名 2 处留存）；`import daemon.bcc_routes` OK |
| `daemon/bcc_capture.py:14,18` | `from typing import Any`、`from daemon.bcc_lease import ContainerBusy` 各仅定义处 1 次 | 删两行 import | `Any` 0、`ContainerBusy` 0；`import daemon.bcc_capture` OK |
| `daemon/bcc_login.py:20-26` | 因 P0-3 修法后：`ContainerBusy`/`_lease_acquire`/`_lease_current`/`_lease_release`/`_scan_exclusive_get` 全部失去引用 | 从 import 列表删除；保留 `_lease_status` / `_scan_exclusive_set` / `LEASE_PRIO_TTL_LIMIT`（均仍被使用） | `grep` 确认无残余调用；`py_compile` + `import` OK |

### ⑤ 诚实标注
- 全部删除均先经「AST 未用 + `grep -nw` 逐字计数」双重验证，**未**凭猜。
- `daemon/verify_handledispatch.py` 与 `test_send_gate_config.py` 本轮**未改**（无缺陷）；二者正是 P0-4 的反向着，保留以持续暴露该边界。

---

## 六、只登记不修（**`browser_daemon.py`，另一 agent 正在改其 480 行**）

| # | 位置 | 判定 | 为何不修 | 建议 |
|---|---|---|---|---|
| R-1 | `daemon/browser_daemon.py:41` `from daemon.bcc_lease import … _is_busy …` | `_is_busy` **未用 import**（其余出现均在注释） | 独占权在另一 agent，禁碰 | 该行随其收尾清理；`_is_busy` 在 `bcc_lease.py` 仍有定义，删 import 即可 |
| R-2 | `daemon/browser_daemon.py:192` `_SWITCH_COOLDOWN_SEC = 180` | **双宿主常量**：`bcc_audit.py:19` 同名同值亦定义；`bcc_audit` 内 2 处使用各自模块的那个；`browser_daemon.py` 的定义疑似无消费方 | 同上，禁碰 | 收敛为**单宿主**（留 `bcc_audit.py`，`browser_daemon.py` 删除或改为引用），可加「同常量不得双定义」机械门禁 |

> 两处**均只登记、未改一行**；已按交办标为待办。

---

## 七、改动文件清单 + 新增测试名 + 实测输出尾部

### 改动文件（**仅本会话独占文件**；`git diff --stat`）
```
DYAutoDM_v2/backend/daemon/bcc_audit.py   |  3 --      (删未用 import ×2)
DYAutoDM_v2/backend/daemon/bcc_capture.py |  3 --      (删未用 import ×2)
DYAutoDM_v2/backend/daemon/bcc_login.py   | 40 ++++++  (P0-3 租约 + 死代码 + 未用 import)
DYAutoDM_v2/backend/daemon/bcc_routes.py  |  3 --      (删未用 import ×2)
DYAutoDM_v2/backend/daemon/recv_daemon.py | 76 +++++++ (P0-4 归一 + P2-6 接线；含父会话 P0-1 改动)
5 files changed, 99 insertions(+), 26 deletions(-)
```
**新建**：`DYAutoDM_v2/backend/test_entry_module_identity_guard.py`（22,135 bytes，7 例）。
**未改**：`daemon/browser_daemon.py`、`daemon/bcc_lease.py`、`daemon/verify_handledispatch.py`、`test_send_gate_config.py`、`services/verdicts.py`、版本源、`工作记忆/`、`artifacts/UP_*`。

### 新增测试名
- `test_entry_module_identity_guard.TestEntryModuleIdentityGuard`（**7 例**）：
  `test_all_product_entries_declare_normalization` / `test_normalization_precedes_module_state` /
  `test_no_unexplained_dually_hosted_entry` / `test_dual_host_discovery_is_not_empty` /
  `test_recv_daemon_main_and_package_are_same_module` /
  `test_negative_control_unnormalized_entry_fails` /
  `test_negative_control_recv_daemon_without_normalization_would_fork`
- 本会话**未新增**其它测试文件（P0-3/P2-6 的复现脚本在隔离 tmp，未入库——见 §八）。

### 回归实测输出尾部（`cd DYAutoDM_v2/backend && DY_APP_ROOT=$LOCALAPPDATA/Temp/fixF python -m unittest <t>`）
```
--- test_entry_module_identity_guard ---   Ran 7 tests in 2.6s   OK
--- test_send_gate_config ---------------   Ran 6 tests in 4.1s   OK
--- test_bcc_module_identity_guard ------   Ran 4 tests in 4.1s   OK
--- test_bcc_startup_single_fire --------   Ran 3 tests in 10.1s  OK

### py_compile（5 改 + 1 新）### PY_COMPILE OK
### import daemon.bcc_audit/bcc_capture/bcc_routes/bcc_login ### IMPORT_OK
```

### 关键复现脚本落点（隔离，**未入库**）
`%LOCALAPPDATA%\Temp\fixF\`：`repro_p03.py`、`repro_p04.py`、`repro_p26.py`、`counts.py`、`recv_daemon.bak`。

---

## 八、诚实标注（未做 / 未验证 / 风险）

1. ⚠️ **P0-3 未做端到端真机验证**：未真发起一次扫码登录（风控红线 + 无 GUI）。证据为「真实 `scan_login` 代码路径 + 真实 `bcc_lease`」的隔离复现。
2. ⚠️ **P0-4 为结构性敞口**：当前生产进程未触发该分叉（两处 import 各在独立进程）。本修 + 门禁消除的是**未来复发面**，非正在进行中的故障。
3. ⚠️ **P2-6 语义变化未经真实流量验证**：长数字 uid 占位从「写库」改为「回退对端 uid」；仅隔离复现。
4. ⚠️ **`recv_daemon.py` 含父会话并发改动（P0-1）**：本会话**保留**其 `_mark_send_delivery` / `SendBody.server_message_id` / 三元组解包，未回退、未触碰其逻辑（`grep` 核对仍在位）。若父会话在其基础上续改，可能与本报告行号漂移。
5. ⚠️ **`services/verdicts.py` 由另一 agent 并发重写**：本会话读取到的是**新内容**（P2-4 单一判据版）。P2-6 引用其**名称/签名未变**的 `is_placeholder_name`，风险低；但若该 agent 后续改签名，本处会断——**未**加跨 agent 契约门禁。
6. ⚠️ **门禁豁免名单是人工判定**：`_ALLOWLIST_DUAL_HOST` 5 条按「开发脚本非产品入口」判定，**未**逐条跑「以冻结态运行 + 同进程按包名 import」的实测；判据基于 spec 反查（无对应 spec）。
7. ⚠️ **未升版本、未提交**（按纪律）；**未**跑全量 `unittest discover`（交办要求只跑指定用例）。
8. ⚠️ 本报告**未**修改 `工作记忆/` 知识库；建议父会话把「入口归一机械门禁」补入 `09_环境与构建.md` 与 REG-01 case。
