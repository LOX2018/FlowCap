# A-8 施工报告 —— 测试隔离根单一化（根治 M-17：测试写入真实 design 数据根）

- 日期：2026-09-28
- 仓库：`C:/Users/LOX/Desktop/DYchajian`，后端根 `DYAutoDM_v2/backend`
- 分支：`design/better-douyin`
- 解释器：`C:/Users/LOX/AppData/Local/Programs/Python/Python314/python.exe`（3.14.6）
- 角色：子会话 A-8（父会话 M-17 附带高危项）
- 版本文件：**未改动**；**未 `git add/commit`**；**未跑全量 discover**

---

## 1. 结论（一句话）

backend 下所有 `os.environ.setdefault("DY_APP_ROOT", <非临时路径>)` 的模块（11 个）已统一改为
**一次性 `tempfile.mkdtemp()` 临时根**（赋值而非 setdefault，且 `makedirs` 先于赋值）；
未知环境变量时，这些模块不再可能把根指向真实 design 数据根或源码树。
判据 `grep -rn 'setdefault("DY_APP_ROOT"' backend/*.py` 中**「非临时兜底」计数 11 → 0**。

---

## 2. 只读扫描（改前）

命令（结果存 `_grep_before.txt`）：

```
cd DYAutoDM_v2/backend
grep -rn 'setdefault("DY_APP_ROOT"' *.py           # 22 行
grep -rn  'DY_APP_ROOT' *.py                        # 出现于 45 个 .py
```

把每个 `setdefault` 的**兜底值**按「临时目录 vs 真实根/源码树」分类（解析器：
`_verify_after.txt` 内为同一脚本输出；分类脚本逻辑见同目录 `_drift_probe.py` 同一套 tempfile 判据）：

### 改前「非临时兜底」清单（**11 个模块 / 11 处**，全部已修）

| # | 文件 | 行 | 原兜底值 | 类别 |
|---|---|---|---|---|
| 1 | `test_ai_client_method_structure.py` | 34 | `r"C:\temp\dyautodm_design"` | 真实 design 数据根 |
| 2 | `test_h30_rpa_wiring.py` | 27 | `r"C:\temp\dyautodm_design"` | 真实 design 数据根 |
| 3 | `test_h31_ai_reply_safety.py` | 29 | `r"C:\temp\dyautodm_design"` | 真实 design 数据根 |
| 4 | `test_m20_search_transport.py` | 38 | `r"C:\temp\dyautodm_design"` | 真实 design 数据根 |
| 5 | `test_m2_secsdk_send_side.py` | 35 | `r"C:\temp\dyautodm_design"` | 真实 design 数据根 |
| 6 | `test_bcc_kernel_unavailable_breaker.py` | 46 / 107 | `os.environ.get("DY_APP_ROOT", BACKEND)` / `_BACKEND`（子进程 env） | 源码树 |
| 7 | `test_bcc_module_identity_guard.py` | 42 / 69 / 101 / 153 | `... , BACKEND)` / `_BACKEND` | 源码树 |
| 8 | `test_bcc_startup_single_fire.py` | 40 / 96 | `... , BACKEND)` / `_BACKEND` | 源码树 |
| 9 | `test_credential_selflock_recovery.py` | 24 | `os.path.abspath(<backend>/../..)` | 仓库父目录（源码树） |
| 10 | `test_live_cred_writeback_independence.py` | 25 | `os.path.abspath(<backend>/../..)` | 仓库父目录（源码树） |
| 11 | `test_plaintext_env_deprecation.py` | 23 | `os.path.abspath(<backend>/../..)` | 仓库父目录（源码树） |

> 与 N3 卷宗一致的「危险 5 剑客」= #1~#5；本报告另追加 #6~#8（BCC 子进程/探针兜底回源码树）、
> #9~#11（兜底回仓库父目录）共 6 个，N3 未点名的隐式真实根兜底。

### 改前「已经是临时目录」的兜底（**保留，不动**）

`test_entry_module_identity_guard.py:240/285`（`%LOCALAPPDATA%/Temp/fixF`）、
`test_id_uniqueness.py:38` + `test_p2_live_guards.py:37`（`%TEMP%/fixA`）、
`test_replay_capture_parse.py:41` + `test_replay_conversation_read.py:38` +
`test_replay_gates.py:21`（`tempfile.gettempdir()/dyautodm_replay_root`）。
→ 兜底值本身即临时区，**按任务书「已经是 mkdtemp/临时目录的不要动」处理**。

---

## 3. 施工内容（改后）

统一范式（任务书指定，见 `test_uid_sink_ext.py:17-39`）：

```python
import tempfile
_ROOT = tempfile.mkdtemp(prefix="<模块名>_")
os.makedirs(_ROOT, exist_ok=True)          # 必须先 mkdir
os.environ["DY_APP_ROOT"] = _ROOT          # ← 赋值，不是 setdefault
```

- **真实 design 根 5 剑客**（#1~#5）：删除字面量 `r"C:\temp\dyautodm_design"`，改为上述范式。
- **BCC 3 模块**（#6~#8）：模块级新增 `_ROOT = mkdtemp()`；子进程 `env.setdefault("DY_APP_ROOT", _BACKEND)`
  改为 `_ROOT`；子进程内联探针 `os.environ.setdefault("DY_APP_ROOT", os.environ.get("DY_APP_ROOT", BACKEND))`
  改为「已有则沿用、否则 `tempfile.mkdtemp()` + `makedirs` + 赋值」。
- **仓库父目录 3 模块**（#9~#11）：`setdefault("DY_APP_ROOT", abspath(<backend>/../..))` 改为上述范式。

改动行数（`git diff --stat`）：11 个 `test_*.py`，共 +72 / −34 行。

---

## 4. 判据（真实 grep 数字，命令可复跑）

```
cd C:/Users/LOX/Desktop/DYchajian
grep -rn 'setdefault("DY_APP_ROOT"' DYAutoDM_v2/backend/*.py
```

| 指标 | 改前 | 改后 |
|---|---|---|
| `setdefault("DY_APP_ROOT"` 命中总行数 | **22** | **10** |
| 其中**非临时兜底**（真实根/源码树/仓库父目录） | **11** | **0** |
| 其中兜底为临时目录 | 11 | 10 |

改后 10 处的兜底值逐条解析（`_verify_after.txt`，解析器把裸变量名回溯到其赋值表达式）：

| 文件:行 | 变量 | 解析后的兜底表达式 | 判定 |
|---|---|---|---|
| `test_bcc_kernel_unavailable_breaker.py:117` | `_ROOT` | `tempfile.mkdtemp(prefix="bcc_kernel_breaker_")` | TEMP |
| `test_bcc_module_identity_guard.py:114/166` | `_ROOT` | `tempfile.mkdtemp(prefix="bcc_module_identity_")` | TEMP |
| `test_bcc_startup_single_fire.py:105` | `_ROOT` | `tempfile.mkdtemp(prefix="bcc_startup_fire_")` | TEMP |
| `test_entry_module_identity_guard.py:240/285` | — | `%LOCALAPPDATA%/Temp/fixF` | TEMP |
| `test_id_uniqueness.py:38` | `_ROOT` | `os.path.join(os.environ.get("TEMP","."), "fixA")` | TEMP |
| `test_p2_live_guards.py:37` | `_ROOT` | 同上 | TEMP |
| `test_replay_capture_parse.py:41` | `_FALLBACK_ROOT` | `tempfile.gettempdir()/dyautodm_replay_root` | TEMP |
| `test_replay_conversation_read.py:38` | `_FALLBACK_ROOT` | 同上 | TEMP |
| `test_replay_gates.py:21` | — | `tempfile.gettempdir()/dyautodm_replay_root` | TEMP |

解析器收尾输出：`RESULT: 0 non-temp fallbacks — judgement satisfied.`

### 4.1 「兜底值不再是真实根」的决定性实测

`DY_APP_ROOT` **不设**时 import 原危险模块，打印其落点（脚本 `_unset_proof` 内联，见 `_verify_after.txt` 同目录）：

```
env -u DY_APP_ROOT python -c "import <module>; print(os.environ['DY_APP_ROOT'])"
```

| 模块 | 改后落点（`DY_APP_ROOT` 未设） |
|---|---|
| `test_ai_client_method_structure` | `C:\Users\LOX\AppData\Local\Temp\ai_client_ms_<rand>` |
| `test_h30_rpa_wiring` | `C:\Users\LOX\AppData\Local\Temp\h30_rpa_<rand>` |
| `test_m2_secsdk_send_side` | `C:\Users\LOX\AppData\Local\Temp\m2_secsdk_<rand>` |
| `test_plaintext_env_deprecation` | `C:\Users\LOX\AppData\Local\Temp\plaintext_env_dep_<rand>` |

→ 全部落在系统临时区，**无一处为 `C:\temp\dyautodm_design`，也无一处为源码树**。
改前同样的命令对 #1 会打印 `C:\temp\dyautodm_design`（N3 卷宗 §2.3-A 实测）。

---

## 5. 验收

### ① 门禁 `test_design_root_isolation`（未改动，仅运行）

```
cd DYAutoDM_v2/backend
DY_APP_ROOT=<已建好的临时目录> python -m unittest test_design_root_isolation -v
```

| 运行 | 结果 | 变化路径分类 |
|---|---|---|
| 连续 4 次（`_gate_green_run3/4.txt`） | run1/2 `FAILED`（各 4~5 fail, 1 err），**run3/4 `Ran 4 tests … OK`** | `browser_profile_paths` 12/10/**0**/**0**，`project_db_paths` 全程 **0** |

**状态诚实标注：**

- 本机 `test_design_root_isolation` **不是稳定全绿**：它间歇红。但红因**全部**是本机
  **正在运行的 Camoufox 守护进程**在写 design 根内的**浏览器 profile**
  （`.../members/<member>/auto_dm/accounts/<账号>/profile/_camoufox/**.sqlite-wal/-shm`），
  **与本项目数据无关**。
- 独立只读探测（25 s 窗口，零测试在跑）实测：`changed=15 / removed=2`，**100% 为
  browser profile 的 idb/cookies/places sqlite 旁车**，`PROJECT_DB` 变化 **0**；进程表确认
  本机有多套 `camoufox.exe` / `dyautodm-browser-daemon` / `dyautodm-recv-daemon` 常驻。
- 门禁证据中**唯一**出现过的变化文件是 `...\尚进工伤小助理\profile\_camoufox\cookies.sqlite-{shm,wal}`，
  **没有任何 `dyautodm.db`（项目/会员库）**出现在变化清单里。
- 在浏览器活动静默的窗口（run3/4）门禁**全绿**，与 N3 交付时的绿态一致（N3：`Ran 4 tests … OK`）。
- ⇒ 本任务未改动该门禁（**验收器**），也未制造任何项目数据写入。门禁间歇红的环境根因
  另见 §7 疑点 1。

### ② 抽取改过的模块单跑

`run_mods.sh`（每个模块独立 `DY_APP_ROOT=<fresh temp>`）：

| 模块 | 改前 | 改后 |
|---|---|---|
| `test_ai_client_method_structure` | 7 ran, **1 fail**（G6，见下） | 7 ran, **1 fail**（同一处，未动） |
| `test_bcc_kernel_unavailable_breaker` | 2 OK | 2 OK |
| `test_bcc_module_identity_guard` | 4 OK | 4 OK |
| `test_bcc_startup_single_fire` | 3 OK | 3 OK |
| `test_credential_selflock_recovery` | 11 OK | 11 OK |
| `test_h30_rpa_wiring`（pytest 风格） | n/a | **21 passed**（`pytest test_h30_rpa_wiring.py`） |
| `test_h31_ai_reply_safety` | 9 OK | 9 OK |
| `test_live_cred_writeback_independence` | 12 OK | 12 OK |
| `test_m2_secsdk_send_side` | 4 OK | 4 OK |
| `test_m20_search_transport` | 6 OK | 6 OK |
| `test_plaintext_env_deprecation` | 6 OK | 6 OK |

- **无回归**：唯一红是 `test_ai_client_method_structure::test_g6_no_orphan_class_methods_repo_wide`，
  **改前红、改后同红**，报错指向 `test_task_scheduler_gates.py:699 _make_test() 内嵌 _t()` ——
  **不是我改的文件**（属并行会话领地），与本次改动无关（对照日志：`_before_results.txt` / `_after_results.txt`）。
- `test_h30_rpa_wiring.py` 是 pytest 风格（无 `unittest.TestCase`），用
  `python -m pytest test_h30_rpa_wiring.py -v` 运行，**21/21 通过**。

---

## 6. 红线遵守

- 仅修改 `backend/test_*.py`（11 个），**未越界**：未碰 `test_design_root_isolation.py`、
  `features.py`、`api/crawl.py`、`downloader/downloader.py`、`scripts/`。
- 未 `git add/commit`；未改版本文件；未跑全量测试（只跑被改模块 + 门禁）。
- `git status` 中另有 `api/crawl.py`、`daemon/recv_daemon.py`、`downloader/downloader.py`、
  `dy_apis/*`、`services/*`、`utils/data_util.py` 等 M/D 状态 —— **均为并行会话改动，非本次交付**。

---

## 7. 疑点 / 未核验（如实）

1. **门禁间歇红的根因不在被改模块**：red 全部落在 design 根内**浏览器 profile** 的 sqlite 旁车，
   来自常驻 Camoufox 进程；**项目/会员库（`dyautodm.db`）零变化**。属门禁的 `_WATCH` 把
   browser profile 的 `*-wal/-shm` 也纳入守护范围（设计如此）而本机有活浏览器所致。
   **未核验**：这是否就是父会话期望的「全绿」口径 —— 若验收要求严格全绿，需在浏览器静默窗口运行，
   或（超出本任务范围）由门禁侧把 `profile/_camoufox` 路径列为「浏览器自管、非本项目数据」排除项。
   **本任务未改门禁**（它是验收器，明确禁改）。
2. **未跑全量 discover**（任务书禁止）⇒ 「整批 discover 下是否仍有任意模块抢到真实根」未实测；
   但已用「模块级兜底值全部为临时路径 + 未设环境时 import 落点全为临时区」两路证据覆盖。
3. `test_ai_client_method_structure` 的 G6 红是**既存**问题（指向 `test_task_scheduler_gates.py`，
   不在本次范围），**未修**。
4. `scripts/verify_*.py` 里 10 处模块级赋值指向 `DESIGN_ROOT` 的是**脚本**（非 `backend/*.py`
   顶层、非本次判据范围），**未动**（任务边界仅 `backend/test_*.py`）。

---

## 8. 证据文件清单（均在 `artifacts/fix_a_20260928/`）

| 文件 | 内容 |
|---|---|
| `A8_root_isolation_report.md` | 本报告 |
| `_grep_before.txt` | 改前 `grep -rn 'setdefault("DY_APP_ROOT"' backend/*.py` 原始输出（22 行） |
| `_grep_after.txt` | 改后同一命令原始输出（10 行） |
| `_verify_after.txt` | 兜底值解析器对 10 行的逐条判定 + `RESULT: 0 non-temp fallbacks` |
| `_before_results.txt` / `_after_results.txt` | 11 个模块改前/改后单跑结果 |
| `_gate_green_run3.txt` / `_gate_green_run4.txt` | 门禁全绿两次运行的完整输出 |
| `_gate_after.txt` | 门禁一次运行输出（含 RUN 明细） |
| `_drift_probe.py` | design 根只读漂移探测脚本（复用门禁同款判据） |
