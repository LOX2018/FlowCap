# L-9 报告：`build_and_deploy.py` 改动检测缺口修复

> 台账编号 **L-9**（`工作记忆/00_交接卡待办台账.md:130`）
> 分支 `design/better-douyin` · 仓库根 `C:/Users/LOX/Desktop/DYchajian`
> 交付文件 `DYAutoDM_v2/scripts/build_and_deploy.py`（**唯一**改动文件）
> 日期 2026-09-24 · 会话 = DYAutoDM_v2 修复子代理 L-9

---

## 1) 改动清单

| 文件绝对路径 | 增/删 | 唯一标识符 |
|---|---|---|
| `C:/Users/LOX/Desktop/DYchajian/DYAutoDM_v2/scripts/build_and_deploy.py` | 改（`git diff --stat`：+166 / −18） | `BASELINE_PATH = os.path.join(ROOT, "artifacts", ".last_built_sha")` |
| 同上（新增基线读/校验/写） | +3 函数 | `def read_baseline()` / `def baseline_is_valid(sha)` / `def write_baseline(sha=None)` |
| 同上（新增 HEAD 解析） | +1 函数 | `def current_head() -> str \| None` |
| 同上（changed_files 改签名+判据） | 改 | `def changed_files(base_sha: str \| None = None) -> tuple[list[str], bool]` |
| 同上（基线比对 git 命令） | +1 行 | `_git(["diff", "--name-only", base_sha, "HEAD"])` |
| 同上（未跟踪改用仓库根相对） | 改 1 行 | `_git(["ls-files", "--others", "--exclude-standard", "--full-name"])` |
| 同上（新增 CLI 动作） | +1 参数 | `ap.add_argument("--mark-built", action="store_true", ...)` |
| 同上（基线解析校验） | +1 行 | `_git(["rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}"])` |
| 同上（无基线安全回退） | +1 分支 | `if not base_ok: needs_sidecar = True` |

**未改动**：`--fast` / `--frontend-only` / `--full` / `--dry-run` 四个 flag 的语义（逐字保留；
`--mark-built` 为**新增独立**动作，未落入三选一互斥组，不影响既有四 flag 组合）。
未改任何版本号文件、未改 sidecar 构建脚本、未改 `deploy.py`、未做任何 git 写操作。

### 修法（与任务要求逐条对应）

- **改动集** = `git diff --name-only <基线sha> HEAD`（已提交未构建）∪ 工作区 `git diff`
  ∪ 暂存 `git diff --cached` ∪ 未跟踪 `ls-files --others --exclude-standard --full-name`。
- **基线持久化** = `DYAutoDM_v2/artifacts/.last_built_sha`（内容 = 一次 `git rev-parse HEAD` 的完整 sha）。
- **基线推进时机** = **构建成功后**由显式动作 `--mark-built` 写入（写 `git rev-parse HEAD`）。
  本脚本是范围门禁/规划器（不自己执行构建），故基线推进不能隐式发生——docstring 明写
  「**不要**在构建前推进基线，否则构建失败会把改动误记为已构建 ⇒ 静默漏打包」。
- **无基线安全回退全量**：基线文件缺失、或基线 sha 无法解析为提交（被 gc / 非本仓）时，
  `baseline_known=False` ⇒ `needs_sidecar=True`（按需要重建处理，只慢不漏）；且此种情况下
  `--fast` 会被**拒绝**（exit 2）。
- **附带修复（同源第二缺口）**：旧代码用 `git ls-files --others`（**cwd 相对**，小写项目里 cwd=DYAutoDM_v2
  时输出 `backend/x.py`），而 `classify()` 按仓库根前缀 `DYAutoDM_v2/backend/` 匹配 ⇒
  **未跟踪的后端新文件同样被漏判**。加 `--full-name` 统一为仓库根相对。

---

## 2) 验证数字（含负控 / 正控）

全部为**离线正/负控**，在 `$LOCALAPPDATA/Temp/l9gate_test` 的**临时 git 仓库**中构造，
**不触碰主仓历史**（临时仓用 `git init` 新建、`git commit` 只发生在临时仓内，用后 `rm -rf` 清理）。

**夹具构造**：临时仓 `DYAutoDM_v2/{scripts,backend,frontend/src,artifacts}`；
复制交付脚本 + 一个假 `check_version_sync.py`（使联动用例可跑而不触真实门禁）。
`c1`=初始（前端+后端 v1）；`c2`=**已提交**后端改动；`c3/c3b`=**已提交**前端 `frontend/src/` 改动。

| # | 场景 | 输入（基线 / 树态 / flag） | 期望 | 实测输出 | 结果 |
|---|---|---|---|---|---|
| 负控-0 | 复现旧行为 | 无基线；`git diff --name-only` 空 | 旧码报「后端 0」 | `git diff --name-only => []`（空 ⇒ 旧码必报后端/sidecar: 0） | ✅ 复现缺陷 |
| **正控-1** | **HEAD 有已提交后端 + 工作区干净**（L-9 核心） | `base=c1`；树干净；无 flag | 需重建=是 | 改动文件数 1；后端/sidecar: 1 → `backend/svc.py`；需重建 sidecar **是** | ✅ |
| **负控-1** | **无基线安全回退** | 删除基线文件；无 flag | 回退全量 | 「改动基线: 无…安全回退」；改动文件数 0；需重建 sidecar **是**（全量计划） | ✅ |
| 负控-2 | 有基线 + `--fast` 但 HEAD 有已提交后端 | `base=c1`；`--fast --dry-run` | 拒绝 | `✗ 拒绝：--fast 声明「仅前端」…后端/sidecar/backend/svc.py`；**rc=2** | ✅ |
| 负控-3 | 无基线 + `--fast` | `--fast --dry-run` | 拒绝（安全优先） | `✗ 拒绝…无有效构建基线`；**rc=2** | ✅ |
| 负控-4 | 基线 sha 无效 | 基线=`deadbeef…`；无 flag | 回退全量 | 「基线 `deadbeefdead` 无法解析为提交」⇒ 需重建 **是** | ✅ |
| 正控-2b | 已提交纯前端改动 + `--fast` | `base=c2`（HEAD=frontend/src 改动）；`--fast` | 通过、跳 sidecar | 前端 1；需重建 **否**；计划仅 `tauri build + deploy`；rc=0 | ✅ |
| 正控-3c | 已提交联动（后端+前端） | `base=c1`；无 flag | 需重建 + 版本门禁 | 后端 1 / 前端 1；需重建 **是**、联动 **是**、版本齐平门禁 **失败**（夹具未造版本齐平，符合预期） | ✅ |
| 正控-4 | `--full` 强制 | `base=c2`（纯前端）；`--full` | 强制重建 | 需重建 sidecar **是** | ✅ |
| 正控-5 | `--mark-built` 写基线 | `--mark-built` | 写入=HEAD | `✓ 已记录构建基线：041a37ee…`；文件内容 == `git rev-parse HEAD`；rc=0 | ✅ |
| 正控-5b | `--mark-built --dry-run` | 同名 + `--dry-run` | 不写 | 打印「不写基线」；**文件不存在**；rc=0 | ✅ |
| 负控-5 | 基线=HEAD（自构建后无改动） | `base=HEAD` | 跳 sidecar | 改动文件数 0；需重建 **否**（证明不是「永远重建」） | ✅ |
| **正控-6** | **未跟踪后端新文件**（第二缺口） | `base=HEAD`；新增 `backend/brand_new.py`（未跟踪） | 检出并需重建 | 旧 `ls-files --others` 输出 `backend/brand_new.py`（**错前缀**）；新 `--full-name` 输出 `DYAutoDM_v2/backend/brand_new.py`；门禁报后端/sidecar: 1 **是** | ✅ |
| 正控-7 | 工作区未提交后端改动 | `base=HEAD`；未提交改 `backend/svc.py` | 检出 | 后端/sidecar: 2；需重建 **是**（工作区 diff 面仍有效） | ✅ |

### 真实仓库复核（`C:/Users/LOX/Desktop/DYchajian`，只读）

| # | 场景 | 输入 | 实测输出 | 结果 |
|---|---|---|---|---|
| real-1 | 真仓当前无基线文件 | 无 flag | 「安全回退…需重建 sidecar: 是」；改动文件数 9（工作区/未跟踪面） | ✅ |
| real-2 | 临时基线=真 `HEAD~2`（真实已提交后端改动在区间内） | 无 flag | 改动文件数 21；后端/sidecar: 4（含 `backend/config_tag.py` 等**已提交**文件）；前端 1；Tauri 壳 3 | ✅ |
| real-3 | 真仓 `--mark-built --dry-run` | 同名 | 打印「不写基线」；**文件未落盘**（确认无泄漏） | ✅ |
| real-4 | 真仓 `--frontend-only` | 同名 | 现有「被忽略后端清单 + 部署后果警告」文案**逐字保留**；rc=0 | ✅ |

**清理与无污染证据**：临时仓 `rm -rf` 后 `[ -d … ]` 判为 `gone`。
主工作区 `git rev-parse HEAD` = `c6ff6e27eac62a251aea7d977df35f73a5b9627d`（与开工时**同一** sha，未变）；
`git status --porcelain` 仅本任务目标文件 + **开工前已存在的**并行工作线改动；`git status | grep -c last_built` = **0**（无基线文件泄漏）。
`python -m py_compile DYAutoDM_v2/scripts/build_and_deploy.py` → **PYCOMPILE_OK**；
`write_file` 内置 lint → **ok**。

---

## 3) 唯一标识符（供父会话 grep 复核）

```bash
cd C:/Users/LOX/Desktop/DYchajian/DYAutoDM_v2/scripts
grep -n 'BASELINE_PATH = '        build_and_deploy.py   # :69
grep -n 'def current_head'        build_and_deploy.py   # :117
grep -n 'def read_baseline'       build_and_deploy.py   # :126
grep -n 'def baseline_is_valid'   build_and_deploy.py   # :138
grep -n 'def write_baseline'      build_and_deploy.py   # :146
grep -n 'def changed_files'       build_and_deploy.py   # :163
grep -n 'base_sha, "HEAD"'        build_and_deploy.py   # :177（基线..HEAD 比对）
grep -n '"--full-name"'           build_and_deploy.py   # :190（未跟踪改仓库根相对）
grep -n '"--mark-built"'          build_and_deploy.py   # :242（新增 flag）
grep -n 'if not base_ok'          build_and_deploy.py   # :286（无基线安全回退 needs_sidecar）
```

1. `BASELINE_PATH = os.path.join(ROOT, "artifacts", ".last_built_sha")` — `build_and_deploy.py:69`
2. `def changed_files(base_sha: str | None = None) -> tuple[list[str], bool]` — `:163`
3. `def current_head() -> str | None`（`git rev-parse HEAD`）— `:117`
4. `ap.add_argument("--mark-built", …)` — `:242`
5. 基线解析校验 `["rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}"]` — `:142`
6. 安全回退 `if not base_ok: needs_sidecar = True` — `:286`

**分类前缀常量逐字未动**（`SIDECAR_ENTRY_PREFIXES` / `BACKEND_SIGNAL` / `FRONTEND_SIGNAL` / `SHELL_SIGNAL` / `VERSION_FILES`）。

---

## 4) 诚实标注（实测 / 未验证）

- **【未验证·关键】真实构建流程未跑**：红线禁真实构建，本报告**全部**证据来自 `--dry-run`
  与离线临时仓。**未验证**项：① 真实「构建→`--mark-built`→下次门禁」的完整闭环
  （`--mark-built` 的写盘逻辑已在临时仓与真仓 `--dry-run` 下验证，但**未**在真仓真写）；
  ② 三份 PyInstaller / tauri / deploy 的真实耗时与产物；③ 真仓首次建立基线后的实际范围判定。
  ⇒ **不得**据此声称「L-9 已在生产构建链闭环」；本次交付是**判据层 + 门禁层**修复，
  真实构建链的接线（在构建成功后自动/手工调 `--mark-built`）由父会话决定。
- **【行为变更·需知会】** `--mark-built` 是**新增显式动作**，脚本**不会**自动写基线 ⇒
  若无人调用，基线永不推进，门禁将**永远**安全回退全量（只慢不漏，但快速通道失效）。
  这是刻意的安全取向；若要自动推进，需在真实构建成功路径上接线。
- **【未验证·真仓基线首建】** 真仓当前**无** `artifacts/.last_built_sha`（开工前即无），
  首建基线需在**下次成功构建后**执行，否则一直全量。
- **【已做·合规】** 未 `git add/commit/checkout/stash/clean`（临时仓内的 `commit/rm` 仅作用于
  临时目录，非主仓）；未改版本号文件；未真实构建（仅 `--dry-run`）；未跑全量 `discover`。
- **【环境】** `python` = 3.11.16（命令名 `python`，非 `py314`）；Windows + git-bash。

---

## 5) 交叉判据（grep 命中数 / 命令）

```bash
F=DYAutoDM_v2/scripts/build_and_deploy.py
grep -c 'def changed_files'   $F   # → 1
grep -c 'last_built_sha'      $F   # → 5
grep -c 'mark-built'          $F   # → 10
grep -c 'baseline_known'      $F   # → 6
grep -c 'ls-files --others'   $F   # → 1（唯一未跟踪面调用点）
grep -c -- '--full-name'      $F   # → 3
python -m py_compile $F            # → PYCOMPILE_OK
git diff --stat $F                 # → 1 file changed, 166 insertions(+), 18 deletions(-)
```

- **门禁不空转自证**：`负控-5`（基线=HEAD ⇒ 改动 0 ⇒ 跳 sidecar）与 `正控-1/2b/3c`
  （有改动 ⇒ 检出）成对，证明判据**有区分度**（非无脑全拦、也非全放）。
- **负控用真实缺陷向量**：`负控-0` 直接复现旧代码的漏判条件（工作区 diff 为空），
  `正控-1` 对同一夹具证明新代码检出——「改前不中 / 改后命中」对照成立。
- **基线文件自身被排除**：`.last_built_sha` 落在 `artifacts/` 下，`changed_files()` 末尾
  显式过滤它，避免「写基线」这一动作**自身**被计为一次改动（自指污染）。
- **路径前缀一致性**：`--full-name` 输出与 `classify()` 的 `DYAutoDM_v2/…` 前缀同源（`正控-6` 对照旧/新输出实证）。

---

## 6) 疑点

- **对其它待办的影响**：
  - **L-7 / M-11 等**：无交叉——本改动只碰 `build_and_deploy.py` 一个文件。
  - **`build_all.py`（L-6）**：`build_all.py` **不调用** `build_and_deploy.py`（已 `search_files` 证实二者无引用关系），
    故本次修复**不会**自动进入 `build_all.py` 构建链。若希望 `build_all.py --skip-sidecar` 路径也受基线门禁约束，
    需**另做接线**（不在本任务范围，登记为疑点）。
- **相邻问题**：
  - **基线粒度是「HEAD sha」而非「构建产物指纹」**：若两次构建之间**回退**了提交（HEAD 后退但内容不同），
    `diff <基线> HEAD` 仍可能漏判——但本仓无回退工作流，且无基线/基线无效时安全回退全量，风险被兜住。
    更稳的形态是记「构建产物 sha256」，但那需要真实构建产物，超出本次「不真构建」红线。
  - **`--frontend-only` 与无基线组合**：现会在忽略后端清单前**额外**打印「无有效基线，无法证明已提交改动里没有后端」警告，
    但**不阻断**（保留调用者的显式意图权）。这是刻意设计，非缺陷；如欲更强约束可改为拒绝。
  - **基线文件未加入 `.gitignore`**：`git check-ignore -v DYAutoDM_v2/artifacts/.last_built_sha` → rc=1（未被忽略）。
    ⇒ 若误 `git add .`，会随源码树入库（本地构建状态入版本库 = 噪音）。建议由父会话在 `DYAutoDM_v2/.gitignore`
    补一行 `artifacts/.last_built_sha`（本任务「只改一个文件」红线未做）。
- **待接线**：`--mark-built` 需由真实构建成功路径调用，否则门禁恒回退全量（见 §4）。
