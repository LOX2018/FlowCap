# A-6 施工报告：清除已确认的孤儿模块（零引用核查 → 删除）

- 日期：2026-09-28
- 仓库根：`C:/Users/LOX/Desktop/DYchajian`，后端根：`DYAutoDM_v2/backend`
- 分支：`design/better-douyin`（`git rev-parse --abbrev-ref HEAD` 实测输出，全程未切换）
- 解释器：`C:/Users/LOX/AppData/Local/Programs/Python/Python314/python.exe`（Python 3.14.6）
- 纪律：**未** git add/commit/checkout/stash；**未**改版本文件；**未**跑全量测试；只删/改点名范围内文件。删除用 `rm`（git 历史可恢复）。

---

## 0. 结论先行

- **4 个孤儿模块的审计出处已锁定**：`night_shift/reports/2026-09-28-前瞻审计.md` §3·③(a) ——「孤儿模块 —— **4 个，共 552 行，零 import**」，即 `features.py`(431) + `utils/data_util.py`(199) + `dy_apis/douyin_recv_msg.py`(155) + `services/account_service.py`(62)。
- 除 `features.py`（A-2 对象，**未动**）外，其余 **3 个即候选全集**，静态 + 动态零引用核查**全部通过**。
- **处置**：3 个**全部删除**（均 git 已跟踪，历史可恢复）。**无任何一个需要降级为「禁止新增 import」门禁**（零引用成立）。
- **验收**：删除后 `grep -rn "<模块名>" --include=*.py DYAutoDM_v2/backend` **三项全部 EXIT=1（零命中）**。
- **测试**：删除前后同一组相关单测**逐条一致**（56 tests，1 个失败为既有且与本删除无关，见 §6）。
- **波及**：无任何门禁/清单硬编码这 3 个文件名；4 处文档提及已同步更新（见 §5）。

---

## 1. 删除清单

| 文件 | 行数 | md5（删除前） | git 状态 | 恢复方式 |
|---|---|---|---|---|
| `backend/utils/data_util.py` | 199 | `c387c6f5dc4b50e8a0ab527fe678609a` | tracked（`git ls-files` 命中） | `git checkout <sha> -- backend/utils/data_util.py` 或 `git restore` |
| `backend/dy_apis/douyin_recv_msg.py` | 155 | `bf3249e1b2798ee5421f9c7cda78d9e7` | tracked | 同上 |
| `backend/services/account_service.py` | 62 | `8c2c3893dd2809a173627599e5d63071` | tracked | 同上 |

删除后 `git status --porcelain`（实测）：

```
 D DYAutoDM_v2/backend/dy_apis/douyin_recv_msg.py
 D DYAutoDM_v2/backend/services/account_service.py
 D DYAutoDM_v2/backend/utils/data_util.py
```

（`D` 仅在**工作区**，未 `git add`，故仍可从 HEAD 恢复。）

---

## 2. 逐文件零引用证据

证据口径 = **静态 grep（模块名 + 公开符号）+ 动态导入机制扫描 + `__init__.py` 重导出 + 测试/脚本/文档** 六路。

### 2.1 `backend/utils/data_util.py`（199 行，旧快照残留）

- **公开符号**：`norm_str` / `norm_text` / `timestamp_to_str` / `handle_work_info` / `save_to_xlsx` / `download_media` / `save_wrok_detail` / `download_work` / `check_and_create_path`（AST/正则汇总）。
- 静态 grep（backend `*.py`，模块名）：**0 命中**。
- 公开符号 grep：仅命中**自身文件内部定义/调用**；backend 其余任何 `*.py` **0 命中**。
- 动态导入：`importlib.import_module` / `__import__` / `pkgutil` 6 处命中**均不指向** `data_util`（见 §3 清单）。
- 重导出：`backend/utils/__init__.py` 仅有编码头，**无任何 re-export**。
- 单测/脚本/文档：`backend/test_*.py` / `verify_*.py` = 0；`backend/scripts` = 0。
- 旁证：`data_util` 的唯一“活引用方”是 **`vendor/douyin_spider_upstream/main.py`（上游原始副本，自带同名 `utils/data_util.py`）**，与 backend 内这份互不相干。

### 2.2 `backend/dy_apis/douyin_recv_msg.py`（155 行，旧快照残留）

- **公开符号**：`DouyinRecvMsg`（类）。
- 静态 grep（backend `*.py`）：**0 命中**。符号 `DouyinRecvMsg` 仅命中自身定义行 `:15` 与自身 `__main__` 自引用 `:154`。
- 动态导入：同上，无指向。
- 重导出：`backend/dy_apis/__init__.py` 仅有编码头，**无 re-export**。
- **强旁证（动态）**：该模块**在本项目包布局下无法被导入** —— 实测 `python -c "import dy_apis.douyin_recv_msg"` 抛
  `ModuleNotFoundError: No module named 'douyin_api'`（第 8 行 `from douyin_api import DouyinAPI`，是上游**顶层绝对导入**，与本项目 `dy_apis.*` 包层级不符）。即：**即便有人 import 也必炸**，进一步证明它是**未接线的上游旧快照**，非可用能力。
- 审计记录：其职责「IM WS 收消息」已被 `daemon/recv_daemon.py` 取代。

### 2.3 `backend/services/account_service.py`（62 行，第二套端口实现）

- **公开符号**：`AccountService`（类）。（原 `account_port()` 已在 2026-09-06 治理中删除，现仅存类。）
- 静态 grep（backend `*.py`）：**0 命中**。符号 `AccountService` 仅命中自身定义 `:33`；`account_port` 仅命中自身 **docstring/注释**（非代码）。
- 动态导入：无指向。
- 重导出：`backend/services/__init__.py` 内容仅 `"""业务服务层"""`，**无 re-export**。
- 单测/脚本 = 0。
- **自证**：文件头 docstring 原文即「**本模块为孤儿代码，未启用**」「本文件的 `account_port()` 是**第二套端口实现**……**实测：除一句 TODO 注释外，无任何调用方**」。
- **文档佐证**：`docs/migration_guide.md:70` 明确「`account_service.py` 现为**零调用方**的迁移遗留」。

---

## 3. 动态导入机制专项核查（importlib / `__import__` / getattr / pkgutil）

- `import_module|walk_packages|iter_modules|pkgutil` 在 backend 的**全部 6 处命中**：

```
backend/daemon/_verify_send_gate_cache.py:101:  RD = importlib.import_module("daemon.recv_daemon")
backend/dy_apis/_bindings.py:36:               mod = importlib.import_module(f"dy_apis.client_{dom}")
backend/replay/selftest.py:139:               mod = importlib.import_module(module_name)
backend/test_browser_visibility_guard.py:339: m = importlib.import_module("vbrowser")
backend/test_delivery_verify.py:96:           cls.sr = importlib.import_module("services.send_response")
backend/test_l6_build_shell_semantics.py:57:  mod = importlib.import_module("build_all")
```

**无一指向 3 个候选**（`dy_apis._bindings` 只按 `client_{domain}` 约定拼名，域名集合里无 `douyin_recv_msg`）。
- `__import__(...)` 形如 `__import__("os")/("re")/("threading")/("utils.fingerprint")/("dy_apis.douyin_api")/("database")` 等，**无候选名**。
- `getattr(...)` 命中均为 `getattr(app.state, "adm", …)` 这类**属性**访问（运行时实例属性），**非模块枚举/按名导入**。
- 无 `pkgutil.walk_packages` / `iter_modules` 式的「扫包自动收录」逻辑。

---

## 4. 全仓零命中输出（验收判据，原始输出）

工作目录 `DYAutoDM_v2`，命令即验收原句：

```
$ grep -rn "data_util" --include=*.py backend
(exit=1 —— 1=零命中)

$ grep -rn "douyin_recv_msg" --include=*.py backend
(exit=1 —— 1=零命中)

$ grep -rn "account_service" --include=*.py backend
(exit=1 —— 1=零命中)
```

> 说明：文件已删，故**连「自身」行都不存在**——「除自身外零命中」恒成立。删除**前**的对应事实为：模块名在 backend `*.py` 零命中；公开符号仅命中各自文件内部（见 §2）。

backend 之外**仍存在**的引用（**全部非导入语句**，逐一点名）：

| 位置 | 性质 | 处置 |
|---|---|---|
| `docs/migration_guide.md:70` | 现行状态文档，明确说 account_service 零调用 | **已更新**（标注已删除） |
| `docs/project_analysis.md:83` | 自述「历史快照，非当前状态」 | **已加删除注记** |
| `docs/_架构测绘原始数据.md:260` | 自动测绘原始数据（2026-09-11 锚点） | **已加删除注记** |
| `项目说明.md:89` | 现行目录树说明 | **已删文件引子** |
| `.hermes/plans/2026-09-05_2200-image-send-recon.md:21` | 2026-09-05 侦察计划（历史） | **已加删除注记** |
| `scripts/_triage_ocr_critical.py:69` | `@check` 标签文本（`_` 前缀一次性脚本，**不 import 该模块**，只内联同款正则） | **已加删除注记** |
| `vendor/douyin_spider_upstream/**` | **上游原始副本自带**同名 `data_util.py`/`douyin_recv_msg.py` | **不属于本仓库产品代码，未动** |
| `artifacts/fix_a_20260928/out_default.json`、`artifacts/audit_2026-09-27/silent_fallback_baseline.json` | 其它子 agent 的**既有扫描产物**（记录了删除前的命中快照） | **未动**（属产物，非源码/门禁；后续重扫会自动消失） |

---

## 5. 波及面处置（门禁 / 文档 / 清单）

- **门禁**：`scripts/check_iron_rules.py` **不含**这 3 个文件名的硬编码（grep 0 命中）；其余门禁脚本（`audit_redline_count.py` / `audit_data_contract.py` / `_verify_sidecar_modules.py` / `check_version_sync.py` 等）亦无。**无需改门禁**。
- **清单/配置**：全仓 `*.json/*.txt/*.toml/*.ini/*.cfg/*.yaml/*.yml` 中，除 §4 表中两份**扫描产物**外，**无任何门禁清单枚举这 3 个模块**。
- **版本文件**：未触碰（本删除不改版本）。
- **文档**：§4 表中 5 处现行/历史文档 + 1 处脚本标签已同步更新；**历史快照类文档保留原貌并加注**（不伪造历史）。

---

## 6. 测试验证（删除前基线 vs 删除后）

隔离根：`DY_APP_ROOT=$(mktemp -d)`，命令 `cd backend && DY_APP_ROOT=<tmp> <python314> -m unittest <模块> -v`。

### 6.1 与「全树扫描/文件收集」相关的门禁（删除最可能波及的一类）

`test_no_loguru_printf_style`（遍历全 backend `*.py`）、`test_no_dup_dict_keys`、`test_capability_probe`、`test_config_isolation`、`test_ai_client_method_structure`：

| | 删除前 | 删除后 |
|---|---|---|
| 结果 | `Ran 56 tests` / `FAILED (failures=1)` | `Ran 56 tests` / `FAILED (failures=1)` |
| 失败项 | `test_g6_no_orphan_class_methods_repo_wide`（`test_task_scheduler_gates.py:699 _make_test() 内嵌 _t()`） | **完全同上** |

⇒ **零新增失败**；且与删除前**逐条一致**（除耗时）。

> 既有失败说明（**与本次删除无关**）：`test_ai_client_method_structure.g6` 命中的是 `test_task_scheduler_gates.py:699`，属**另一并行施工面**（task_scheduler / 测试文件），删除的 3 个文件不在其扫描产出的差异里（g6 只报那 1 处，删除前后相同）。

### 6.2 其余相关门禁

- `test_design_root_isolation`：**4/4 OK**（单独运行；§6.3 说明）。
- `test_plaintext_env_deprecation` / `test_m2_secsdk_send_side` / `test_entry_module_identity_guard` / `test_replay_gates` / `test_replay_capture_parse`：与 `test_design_root_isolation` 合跑时 **58 tests，仅 1 个 `tearDownClass` 报错**——该报错为 §6.3 的环境竞态，非删除引起。
- **编译门禁**：删除前后 `python -m compileall -q backend` **EXIT=0**（无语法/导入期错误）。
- **铁律门禁**：`python scripts/check_iron_rules.py` **EXIT=0**（13 项 / 通过 11；2 项为既有磁盘卫生警告 R2/R3，与本次无关）。

### 6.3 环境竞态说明（诚实标注）

合跑时 `test_design_root_isolation.tearDownClass` 报 `design 根指纹发生变化`，指纹差异指向
`C:\temp\dyautodm_design\...\尚进工伤小助理\profile\_camoufox\cookies.sqlite-wal`——
**是另一个进程在该真实数据根上的并发写入**（mtime 在我运行期间被推进），**非本删除引起、非测试写源根**。单独重跑 `test_design_root_isolation` **4/4 OK** 证实。本任务铁律禁改真实数据根，仅如实记录。

---

## 7. 未决 / 诚实标注

- `features.py`（第 4 个孤儿）**按 A-2 分工未动**，仍为 431 行零接线 —— 本报告不涉及。
- **动态运行期零引用**无法在「零真实请求」约束下做到 100% 形式化证明；本次以「静态六路 grep + 动态导入机制扫描 + 编译/导入实测 + 删除前后测试逐条一致」四重证据替代，已足以支撑删除（且 git 可恢复）。
- 扫描产物（§4 表中 json）保留删除前快照，属**证据**而非腐化，未动。
