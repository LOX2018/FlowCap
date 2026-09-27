# N3 卷宗 —— design 真实数据根「测试写串」的实测 + 机械门禁

- 日期：2026-09-28（夜间）
- 分支：`design/better-douyin`（未切换、未 commit、未 add）
- 版本：v0.45.57（**未改动任何版本源**）
- 执行角色：子会话（N3），父会话 M-17 附带高危项

---

## 1. 自述改动清单

本次**仅**新增以下文件（其余一律未动，已用 `git status --porcelain` 复核）：

| 文件 | 类型 | 说明 |
|---|---|---|
| `DYAutoDM_v2/backend/test_design_root_isolation.py` | 新增（唯一源文件改动） | D1~D4 机械门禁，4 个用例，**stdlib-only** |
| `DYAutoDM_v2/artifacts/fix_night_20260928/probe_design_root.py` | 新增（一次性探测） | 只读指纹快照 / 比对；**故意不放进 backend/**，避免被 discover 收集 |
| `DYAutoDM_v2/artifacts/fix_night_20260928/scan_app_root_assign.py` | 新增（AST 扫描器） | 三分类污染源候选清单生成器 |
| `DYAutoDM_v2/artifacts/fix_night_20260928/pollution_candidates.json` | 产物 | 扫描结果（46 文件 / 56 处写） |
| `DYAutoDM_v2/artifacts/fix_night_20260928/{before,after}_c*.json` | 产物 | 指纹快照，供交叉比对 |
| `DYAutoDM_v2/artifacts/fix_night_20260928/N3_report.md` | 本卷宗 | |

**未做**（红线遵守）：未改动任何源码或其它测试文件；未 `git add/commit/checkout/stash/clean`；
未改版本文件；未删除/修改 design 根任何文件；未使用数据库 LIKE 模糊删；未跑全量 discover。

> ⚠️ 环境异常（**非本会话所为**，如实上报）：`git status` 显示
> `DYAutoDM_v2/backend/services/task_scheduler.py` 与
> `DYAutoDM_v2/backend/test_task_scheduler_gates.py` 处于 **M（已修改）**状态。
> 本会话全程未打开、未写入这两个文件；疑为并行会话改动。请父会话注意这两个文件
> 不属于本次交付，**不应**随本次夜班提交。

### 解释器口径修正（重要）

任务书指定 `python`（3.11.9），但实测 **`python` 缺依赖**：

```
python -m unittest test_ai_agent  →  ModuleNotFoundError: No module named 'loguru'
```

`database.py:25` 依赖 `loguru`，3.11 环境无此包 ⇒ **用 3.11 跑测试是假红**，
任何以 3.11 跑出的结论都不可用。项目既有文档写的也是 `py314`。
故本卷宗全部实测改用 **`py -3.14`（3.14.6，含 loguru）**。
门禁文件本身只用 stdlib ⇒ **两个解释器都能跑**（3.11 实测通过，见 §2）。

---

## 2. 自述测试数字（含哪些是负控）

### 2.1 污染是否仍复现 —— 答：**当前不复现**（快照逐字节相同）

design 根 `C:\temp\dyautodm_design` 实测体量：**43 个 .db / 372 个含 WAL 旁车文件 /
约 12 MB；另有 51,326 个非 db 文件约 3 GB**（浏览器 profile 与缓存）。

| 组合 | 命令 | 结果 | 与基线比对 |
|---|---|---|---|
| C1 | `python -m unittest test_ai_agent` | 14 tests OK | **identical = true**（0 变） |
| C2 | `test_config_isolation test_ai_agent` | 17 ran, **1 failure** | identical = true |
| C3 | `test_capability_probe test_ai_agent` | 54 tests OK | identical = true |
| C4 | 人为先把 `DY_APP_ROOT` 毒化为 design 根再跑 test_ai_agent | 14 tests OK | identical = true |

三个点名组合 **全部 `identical = true`**：无文件增删、无 size 变化、**无 mtime 推进**、
`kv_store` 五个可疑键（ai_agents / ai_account_agent / ai_reply_config /
ai_reply_knowledge_base / ai_reply_blacklist）**全部无变化**，未新增任何 `ag*`。

C2 的 1 个 failure 是**另一回事**（顺序相关假失败，非写污染）：
`test_config_isolation.test_database_resolves_under_isolated_root` 断言 DB 必须落在自己的
`_ROOT`，而 `test_ai_agent` 在 setUp 里把根钉回 `dyautodm_agent_test` ⇒ 红。
这正是 `test_uid_sink_ext.py:20-25` 注释里警告过的同类坑，**与本次 design 根污染无关**。

### 2.2 历史污染**确实存在**（基线里就有）

`before_run0.json` 的 kv 快照显示 design 会员库
`members\m17db0f8209156f26\data\dyautodm.db` 里：

- `ai_agents` 含 `agA`（`{"name": "A", "config": {"merchant_name": "商家A"}}`，created_at=179041762…）
  以及 `agB/ag2/agS/ag1`
- `ai_account_agent` 含 `{"账号A": "ag_not_exist", …}`（与真实账号「尚进工伤小助理」/「四川工伤张老师」同表）

与 2026-09-27 父会话取证**完全一致** ⇒ 污染是**真实发生过的历史事实**，只是**当前三个
点名组合不复现**。本会话**未删除、未修复**这些数据（红线：只观测不修复）。

### 2.3 为什么现在不复现 —— 机制已定位（这轮的主要收获）

不是靠猜，是实测：

```
# A. 先 import 危险模块，再解析 database
import test_ai_client_method_structure
print(os.environ['DY_APP_ROOT'])   →  C:\temp\dyautodm_design
print(database._db_path())         →  C:\temp\dyautodm_design\members\m17db0f8209156f26\data\dyautodm.db
                                      ↑ 真实会员库，污染路径闭环成立

# B. 对照：先 import test_ai_agent，再 import 危险模块
import test_ai_agent              →  DY_APP_ROOT = %TEMP%\dyautodm_agent_test
import test_ai_client_method_structure  →  DY_APP_ROOT **不变**（仍是 temp）
database._db_path()               →  %TEMP%\dyautodm_agent_test\data\dyautodm.db
```

结论：**`setdefault` 只在「自己是第一个写者」时才赢**。
`discover` 按文件名 sorted 导入，实测导入序第 1~8 位里 **#2 就是 `test_ai_agent.py`**
（模块级无条件赋值 → temp），它先手之后，#3 `test_ai_client_method_structure`
以及 #39/#40/#51/#52 四个 `setdefault(design)` **全部退化为 no-op**。

⇒ **当前 alphabetic 顺序恰好把危险模块挡在后面，是一种偶然的幸运 order-dependency。**
任何改动（新增一个排在 `test_ai_agent.py` 之前的 test 文件、或 pytest 换 rolled-order、
或改名、或单独 import 顺序变化）都会让 #3 反超 ⇒ 立刻回到 design 根写入。
**这就是为什么「单独跑不复现」却历史上真发生过** —— 它不是随机的，是**脆弱排序**。

### 2.4 门禁 D1~D4 实测读数

`python -m unittest test_design_root_isolation -v` → **`Ran 4 tests ... OK`**

| 判据 | 读数 | 判定 |
|---|---|---|
| **D1** | `[D1] 基线指纹：watched_files=369  read_errors=0`；收尾比对 369 文件 size+mtime_ns+sha256 **全部未变** | ✅ 绿 |
| **D2a 负控** | `mtime-only 写入 ⇒ 判据报红 ✓`（仅 utime，size/sha 不变也被抓到） | ✅ **负控变红** |
| **D2b 负控** | `content 写入 ⇒ 判据报红 ✓`（victim 选会员库路径） | ✅ **负控变红** |
| **D2c 负控反证** | `未写副本 ⇒ 判据保持绿 ✓（共 369 文件）` —— 证明**非恒红** | ✅ 绿 |
| **D3** | `[D3] 缺失根分支可用：errors=['root 不存在: …']`，另有 `[D3-SKIP]` 显式打印 | ✅ 非静默 |
| **D4** | `[D4] import 期 DY_APP_ROOT = None` ≠ design 根 | ✅ 绿 |
| **D4 反向** | 注入 `DY_APP_ROOT=C:\temp\dyautodm_design` 再跑 → **`FAILED (failures=1)`** | ✅ 该红的红了 |

稳定性：连续 3 次独立运行全绿（369 文件读数一致）。
可移植性：**3.14 与 3.11 均通过**（stdlib-only，不 import database/vbrowser，
因此本模块**不会**参与 DY_APP_ROOT 争夺 —— 这是刻意的）。

**哪些是负控**：`test_1_negative_control_criterion_actually_goes_red` 整个用例（D2a/D2b/D2c
三条腿）与 D4 反向注入。其余 D1/D3 为正向判据。

---

## 3. 唯一标识符

| 项 | 值 |
|---|---|
| 门禁类名 | `TestDesignRootIsolation` |
| 判据函数（唯一） | `assert_root_unchanged(baseline, current, label)` |
| 指纹函数 | `fingerprint(root)` → `{relpath: (size, mtime_ns, sha256)}` |
| 观测后缀 | `.db` / `-wal` / `-shm` / `-journal`（四个 SQLite 写路径入口全覆盖） |
| design 根常量 | `DESIGN_ROOT = r"C:\temp\dyautodm_design"` |
| OS 噪声排除器 | `_is_os_managed(rel)` |
| 负控 seam | 两个独立副本根：`mut_root`（写→必红）/ `clean_root`（不写→必绿） |
| 卷宗路径 | `C:/Users/LOX/Desktop/DYchajian/DYAutoDM_v2/artifacts/fix_night_20260928/N3_report.md` |

---

## 4. 诚实标注

1. **污染当前不复现**，本次没有抓到「活的」写入。三个点名组合 + 一个人为毒化组合
   均为 `identical = true`。历史污染证据（agA / 账号A）仍在库里，**未清理**（红线）。
2. **任务书给的 `python`（3.11.9）跑不了这些测试**（缺 `loguru`）⇒ 结论一律以
   `py -3.14` 为准；若后续会话照抄任务书用 3.11，会撞 `ModuleNotFoundError` 假红。
3. **两个 M 文件不是我改的**（`task_scheduler.py` / `test_task_scheduler_gates.py`），
   疑并行会话残留，已如实上报。
4. **禁止跑全量 discover** 我遵守了 ⇒ 「真实 discover 下是否整批写 design 根」
   **仍未实测**，只有机制级证明（§2.3 的 A/B 对照）与 order 分析。这是本次最大的未闭环。
5. `_is_os_managed()` 是**排除法**：它让 `%SystemDrive%\ProgramData\...\Caches\*.db`
   这类 OS 自管文件不参与判据。代价是：理论上若污染**只**写这些路径会被漏掉 ——
   但那本来也不属于本项目数据，判漏风险可接受，且已在门禁 docstring 里写明理由。
6. 副本根只复制被观测文件（372 个 / 3 秒），不复制 3GB 整根 —— 判据只观测这些文件，
   故副本对判据**忠实**；但副本**不是**完整 design 根，不可用于数据恢复。

---

## 5. 交叉判据

| 结论 | 证据 1 | 证据 2（独立来源） |
|---|---|---|
| 当前无写污染 | 指纹 diff identical=true（size+mtime+sha256 三路） | `kv_store` 五键内容字典比对无差异 |
| 历史污染存在 | `before_run0.json` kv 含 agA/账号A | 与父会话 2026-09-27 取证一致 |
| 危险模块真能抢到 design 根 | `import test_ai_client_method_structure` → `DY_APP_ROOT=design` | `database._db_path()` 实测返回 design 会员库真实路径 |
| 门禁非恒绿 | D2a/D2b 两个负面 Assembly 均抛 AssertionError | D2c 反证未写时保持绿 |
| 门禁非恒红 | 干净副本 369 文件比对通过 | 连续 3 次运行 OK + D1 显式比对通过 |
| 环境已污染时会报警 | D4 反向注入 → FAILED | `[D4] import 期 DY_APP_ROOT` 打印值为 design 根 |
| 无变量 tranquil 自污染 | 门禁单跑 4 绿且 design 根 0 变化 | 门禁 import database 之外只用 stdlib（源码可查） |

---

## 6. 疑点 + 待办（给下一个会话）

1. **未闭环**：整批 `discover` 下的行为没实测（本次禁止跑）。建议下一次用
   **fronzen order** 做一个「显式按 sorted 逐个 import 全部 78 个 test 模块」的脚本，
   在 `%TEMP%` 沙箱化的假 design 根上跑（**不要**再拿真 design 根做实验）。
2. **危险 5 剑客仍原样在线**，本次未修（越界禁止）。修法建议：把
   `os.environ.setdefault("DY_APP_ROOT", r"C:\temp\dyautodm_design")` 统一换成
   `os.environ.setdefault("DY_APP_ROOT", <tempfile.mkdtemp()>)`，或直接 import
   `test_config_isolation`（项目既有范式，`.py:21`）。
3. **`scripts/verify_*.py` 里 10 个模块级无条件赋值指向 design 根**（catA）。
   这些是**脚本**不是测试，其设计意图可能就是「自设 design 根」（docstring 明写了
   「不靠调用方记忆」）。**但它们一旦被 test 模块 import 就变成污染源** ——
   建议单独审计 `test_*.py` 是否 import 了这些 verify 脚本。
4. **C2 的顺序相关假失败**是独立缺陷（非本次目标），但会让任何人跑双文件组合
   误以为「隔离崩了」，建议另开一项。
5. `test_ai_agent.reset_all()`（`:38`）在每次 setUp/tearDown 把根钉回自己的 temp ——
   这是它**逃过**本次 C4 毒化实验的直接原因。也就是说：**其它 51 个不做任何
   DY_APP_ROOT 写入的测试模块才是真正的受害者**（它们纯继承环境）。

---

## 附：污染源候选清单（三分类）

扫描口径：AST 解析 `backend/*.py` 的 `test_*`/`verify_*` 与 `backend/scripts/*.py`，
对 `DY_APP_ROOT` 的**写**（赋值 / setdefault）分类。
结果：46 个有写、**合计 56 处**（catA 25 / catB 16 / catC 15）。原始明细见
`pollution_candidates.json`。

### 🔴 最可疑：模块级写且**字面量直接指向 design 根**（5 个）

> 这 5 个是唯一「把 `DY_APP_ROOT` 写成 `C:\temp\dyautodm_design`」的测试模块。
> 排序见 §2.3 —— 谁最先被 import，谁就赢。

| # | 文件 | 行 | 类别 | 语句 | discover 序 |
|---|---|---|---|---|---|
| 1 | `test_ai_client_method_structure.py` | 34 | B 模块级 setdefault | `os.environ.setdefault("DY_APP_ROOT", r"C:\temp\dyautodm_design")` | **#3（最靠前，最危险）** |
| 2 | `test_h30_rpa_wiring.py` | 27 | B 模块级 setdefault | 同上 | #39 |
| 3 | `test_h31_ai_reply_safety.py` | 29 | B 模块级 setdefault | 同上 | #40 |
| 4 | `test_m20_search_transport.py` | 38 | B 模块级 setdefault | 同上 | #51 |
| 5 | `test_m2_secsdk_send_side.py` | 35 | B 模块级 setdefault | 同上 | #52 |

> **Top1 判定理由**：它是 5 个里 import 序最靠前的（#3），且**实测**（§2.3-A）
> 只要它先于 `test_ai_agent` 被导入，`database._db_path()` 就直指 design 会员库。

### 🟠 次可疑：模块级**无条件**赋值（import 期必然抢跑，但目标是临时目录 → 目前安全）

共 25 处 catA。其中 **test 类（15 处）**虽然目标是 temp，但它们是
「单进程抢跑机制」的直接参与者，决定了**谁说了算**：

| 文件 | 行 | 赋给 |
|---|---|---|
| `test_cross_account_sink.py` | 10 | `_TMP` |
| `test_model_hub_v2.py` | 10 | `_ROOT` |
| `test_b3_capture_probe.py` | 17 | `_tmp` |
| `test_settings_api.py` | 18 | `_TMP` |
| `test_app_config.py` | 19 | `_TMP` |
| **`test_ai_agent.py`** | **21** | **`_tmp`＝`%TEMP%\dyautodm_agent_test`（当前意外挡刀者）** |
| `test_config_isolation.py` | 21 | `_ROOT`＝`%TEMP%\dyautodm_cfgtest_root`（既有范式） |
| `test_dm_dispatch_config.py` | 20 | `_TMP` |
| `test_send_gate_config.py` | 24 | `_TMP` |
| `test_config_tag.py` | 25 | `_tmp` |
| `test_model_hub_key_masking.py` | 28 | `_ROOT` |
| **`test_uid_sink_ext.py`** | **39** | **`_ROOT`＝mkdtemp（既有范式）** |
| `test_f1_d1_d3.py` | 38 | `_ROOT` |
| `test_task_scheduler_gates.py` | 54 | `_ROOT` |
| `verify_f1_d1_d3_live.py` | 30 | `_ROOT` |

**scripts 类 catA（10 处，全部 `_DESIGN_ROOT` / `DESIGN_ROOT` / `VERIFY_ROOT`）**：
`verify_ai_agent_contract.py:42`、`verify_ai_live_wiring.py:41`、`verify_api_split.py:35`、
`verify_automation.py:32`、`verify_history_context_fix.py:53`、
`verify_live_ai_observability.py:40`、`verify_logic_replication.py:34`、
`verify_player.py:30`、`verify_reply_kb_learning.py:42`、`verify_t1_no_copy_send.py:28`
> 这些是**脚本**（docstring 声明自设 design 根属于设计意图），风险在于
> **被某个 test 模块 import 时**会把整个测试进程钉死在 design 根。待审计（§6.3）。

### 🟡 catC：函数 / setUp 内赋值（15 处，隔离语义基本正确）

`test_delivery_verify.py:69/82/95/246`、`test_capability_probe.py:44/49/59`
（`setUpModule` / helper 内重复钉回 —— **正确的补救范式**）、
`test_member_smoke.py:58`（每个沙箱一个根，正确）、
`test_replay_*`、`replay/sandbox.py:129` 等。

> 其中 `test_capability_probe.py` 的 `setUpModule()`（:44）是**正解样本**：
> 它承认「后导入者覆盖」这个事实，主动在每个入口重新钉回自己的隔离根。
> 建议把 `test_capability_probe.py:38-44` 的这个模式推广到其余所有 test 模块。

### ⚪ 未参与（51 / 78 个 test 模块）

这 51 个模块**完全不写** `DY_APP_ROOT`，纯继承当时环境 ⇒
**它们是污染的受害者而非加害者**（§6.5）。`agA`/`账号A` 这种测试脏数据出现在
design 根，正是这一类模块在错误的根上跑了嘲讽的结果。
