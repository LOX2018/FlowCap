# D 线核查卷宗 · D_gates_hygiene.md

- 被审报告：`C:/Users/LOX/Desktop/DYchajian/artifacts/fix_20260923/D_gates_hygiene.md`（实测 596 行，与任务书一致）
- 仓库：`C:\Users\LOX\Desktop\DYchajian`　分支：`design/better-douyin`
- 实测 HEAD：`b455192`（`b455192b57fe43132318e1ea4d5cfc1554cffc2b`）——与报告自述一致
- 审查纪律：只读仓库；未 git add/commit/checkout/rm；未修改仓库任何文件；未跑测试；唯一写入为本文件
- 核查时间：2026-09-23　终端：bash(MSYS)

---

## 1.【自述改动文件清单】

### 1.1 报告「本批新改」表（§改动文件清单，9 行）

| # | 报告自述文件 | 关联项 | 工作区实测 |
|---|---|---|---|
| 1 | `DYAutoDM_v2/docs/design-contracts/.known-gaps.json` | F-3/P3-9 | `git diff` 有改动（20 +/-）|
| 2 | `DYAutoDM_v2/backend/main.py` | F-5 | `git diff` 有改动（76 +/-）|
| 3 | `DYAutoDM_v2/.gitignore` | F-6 | `git diff` 有改动（10 +）|
| 4 | `.gitignore`（根） | F-6 / F-7 | `git diff` 有改动（21 +）|
| 5 | `DYAutoDM_v2/scripts/check_version_sync.py` | L-10 | `git diff` 有改动（19 +/-）|
| 6 | `DYAutoDM_v2/backend/requirements.txt` | T3-b | `git diff` 有改动（22 +/-）|
| 7 | `工作记忆/00_交接卡待办台账.md` | T3-a / F-8 | `git diff` 有改动（10 +/-）|
| 8 | `artifacts/handoff_archive/交接卡_HC-09_H9剩余_待实施_2026-09-22.md` | F-8（由 HC-05 改名） | 未跟踪 `??`，文件存在 |
| 9 | `artifacts/fix_20260923/D_gates_hygiene.md` | 本报告 | 未跟踪 `??`，文件存在，596 行 |

（#1–#7 的 `git diff --stat` 汇总：`7 files changed, 146 insertions(+), 32 deletions(-)`——与报告逐文件描述方向一致，未发现报告未自述的额外仓库文件被这两批命令覆盖。）

### 1.2 「索引层」操作（报告自述唯一一处）

| 文件 | 报告自述操作 | 实测 |
|---|---|---|
| `_ext_repos/DouYin_Spider_git` | `git rm --cached`（只移索引，不动工作区） | `git status --short` = `D  _ext_repos/DouYin_Spider_git`（暂存删除）✓；`git diff --cached --name-only` 仅此一项 ✓；磁盘未删（`???` 检查除外，见 §6 说明）|

报告另注明：`check_contracts.py` **零字节改动**——实测 `git diff --stat` 为空 ✓（未为变绿而削检查，属实）。

### 1.3 「声明未触碰」清单（报告自证范围纪律）

1. **版本源文件 6 处零改动**：`package.json` / `frontend/package.json` / `src-tauri/tauri.conf.json` / `src-tauri/Cargo.toml` / `src-tauri/Cargo.lock` / `backend/_build_version.py` —— 实测 `git diff --stat` **空** ✓
2. `DYAutoDM_v2/scripts/check_contracts.py` —— 实测空 ✓
3. F-9 三处（两张归档卡删除 + `verify_live_strategy_live.py` 修改）—— **未 `git add`**，实测仍为未暂存 ` D` / ` M` ✓（见 §6）
4. 临时实验文件（`_l10_probe.py` 等）—— 报告称全删；**未逐项复核**（不做全盘文件系统扫描，超出只读核查可行范围）

> 清单内部一致性：报告 §0 总览承认 `_ext_repos/DouYin_Spider_git` 索引移除为「唯一例外且有意为之」，与 §1.1/§1.2 自洽。

---

## 2.【自述测试数字】

### 2.1 结论：**无测试自证**（报告完全没有 `Ran N`）

- `grep -nE 'Ran [0-9]+ test' D_gates_hygiene.md` → **rc=1（0 命中）**
- `grep -n 'Ran ' D_gates_hygiene.md` → **0 命中**（连裸 `Ran ` 都没有）
- ⇒ 本报告**不含任何 unittest/pytest 的 `Ran N tests` 行**，**无测试自证**。D 线无隔离根 `fixD`、无测试运行自证，与背景描述一致。

### 2.2 报告中出现的 PASS/FAIL 计数（**非测试计数**，属门禁脚本输出）

| 计数 | 数值 | 性质 |
|---|---|---|
| `grep -c '\[PASS\]'` | 6 | `check_contracts.py` 门禁行输出片段（修复前 5 PASS / 修复后 4 PASS，跨两段累计）|
| `grep -c '\[FAIL\]'` | 1 | 同上，修复前 G2 一行 |

⇒ 这些是契约门禁（`check_contracts.py`）的逐项输出，**不是**单元测试的 PASS/FAIL 汇总；报告未声明任何测试套件通过数。`tests_total = 0`。

### 2.3 报告自述的「实测」（均为脚本/命令输出，非测试）

F-3 门禁 `REAL_EXIT 1→0`；F-5 `py_compile` OK 与 OpenAPI version 0.44.53；F-6 `git add --dry-run` 命中 4→0；F-7 `submodule status` fatal→干净；L-10 门禁红/绿各一次 + md5 还原一致；T3-b `uv pip install --dry-run` 1.63.0→1.62.0。以上均属命令回显证据，非测试。

---

## 3.【唯一标识符】

| 标识符 | 值 | 出处 |
|---|---|---|
| HEAD | `b455192` / `b455192b57fe43132318e1ea4d5cfc1554cffc2b` | 报告 §头 + 实测一致 |
| 分支 | `design/better-douyin` | 实测一致 |
| 产品版本 | `0.44.53` | 报告 §头 / F-5 实测 |
| `.known-gaps.json _version` | **1.2.0**（原 1.1.0） | 实测 ✓ |
| `.known-gaps.json _updated` | 2026-09-23 | 实测 ✓ |
| 缺口基线条数 | **7 条**（原 5 + 新增 2） | 实测 python json 解析 = 7 |
| 新增 2 端点 | `client_user.py:/aweme/v1/web/aweme/post/`、`client_video.py:/aweme/v1/web/aweme/detail/` | 实测各 1 命中 ✓ |
| Cargo.lock md5 | `147487b7a2e5041a6ecefc4d581ed354`（还原前后一致） | 报告自述；未复算 |
| HC-09 卡 md5 | `e34aca9fbeae93cbdfb197a82e756386` | 实测一致 ✓（报告称改名前=改名后）|
| 坏 gitlink commit | `1712dad4f59a8ccfc54e4e215b79b83d805293c9`（模式 160000） | 报告自述 |
| 相关提交 | `21fdbf4`（窗口起点 v0.44.29）、`48ae837`（v0.44.44）、`58fdc18`（20:02:48）、`da367ad`（17:11:49）、`eb5d5ab` | 报告自述 |
| F-5 目标 | `backend/main.py` `FastAPI(version=APP_VERSION)` | 实测 ✓ |
| T3-b 目标 | `requirements.txt:42` `playwright>=1.62,<1.63` | 实测 ✓ |
| 门禁脚本 | `scripts/check_contracts.py`、`scripts/check_version_sync.py` | — |

---

## 4.【诚实标注】（逐条抄录未验证 / pending / 风险）

**F-3 / P3-9**
- 两个端点「是真实未接线缺口（不是误报）……登记为 **open（work-in-progress）**……**未**谎称已修」。
- 「本轮是**登记未修缺口**（合规方向），不是把已修项留在基线」。

**F-5**
- 「⚠️ 我中途做错过一次并已自纠：第一次 patch 因 `old_string` 不唯一，复制了一份注释块」。
- 「**未验证边界**：`_build_version()` 在 **Frozen（PyInstaller）态**的取值依赖 `_build_version.py` 随包打包，本轮只在源码态实测；打包态行为未变」。

**F-6**
- 「⚠️ 附带发现一个**既有**的 git 行为……`git check-ignore` 在 MSYS 上对「不存在路径」的怪癖……**不作为缺陷上报，仅备查**」。
- 「未改任何已入库文件，未做 `git add`」。

**F-7**
- 「⚠️ **风险提示（请父会话确认）**：若父会话习惯用 `git add -A` 提交，此删除会**一并被提交**……」
- 「未验证：`ocr` 的实际输出（我未运行 ocr）……「clone 为空」为**推导**……未实机 clone 验证」。

**F-8**
- 「该卡**从未入库**……需父会话 `git add` 才会进仓库。**我没有 `git add`。**」
- 「未改卡内正文（含卡内自称的「编号：HC-05-H9-剩余」）——**只改文件名**」。

**F-4**
- 「上表 `Cargo.lock` 行我用的原始 grep 取到的是 `2.0.1`……**不是** `dyautodm-v2` 段 —— 这是**我的取证方法偏差**，已在此标注」。
- 「我**未**改写该历史提交」。

**L-10**
- 「🔴 **我犯了一个实验错误并已自纠（重要）**：……我把 `Cargo.lock` **转成 LF** 再跑门禁 —— 门禁照常绿……⇒ **EOL 脆弱性假设被实测推翻，不成立**」。

**T3-a**
- 「⚠️ **任务书写「M-11 行现写『C 类…已授权实施』」，实测该描述已过期**……我**没有**按过期的任务书去「改判」」。

**T3-b**
- 「🔴 **一处断言我验不了，故只作背景不写成「已实测」**：……「分两步安装」的失败**取决于顺序与是否锁版本**，我没有复现出硬失败。**我不声称「pip install 一定报错」**」。
- 「环境限制：直连 `pypi.org` 在本机对 uv 不稳定……改用清华镜像……未真机执行安装（只用 `--dry-run`）」。
- 「未改 `requirements.txt` 之外的依赖声明文件（如 `build_sidecar.py` 内嵌的隐藏依赖列表 —— 若存在，需另立一项审计）」。

**遗留 / 交父会话**
- 「`.known-gaps.json` 的 7 条缺口仍是 open……**登记 ≠ 修复**」。
- 「未验证/未复现的断言：F-7 的「clone 为空」为推导未实机验证；T3-b 的「分两步安装硬失败」未能复现（已降级表述）」。
- 「`verify_live_strategy_live.py` 在本轮被**另一执行体**改动……存在并发写者，父会话提交前需确认其归属」。
- 「台账 §5.4 原有的**表格结构损坏**……**同类损坏可能存在于本文件其它表**……建议另立一项做**全文件表格结构校验**」。

---

## 5.【交叉判据】（只读 grep 核实，附命令 + 命中数）

| 核验项 | 命令 | 结果 | 判定 |
|---|---|---|---|
| `.known-gaps.json _version` = 1.2.0 | `grep -n '"_version"' DYAutoDM_v2/docs/design-contracts/.known-gaps.json` | `3:  "_version": "1.2.0",`（命中 1）| **✓ 成立** |
| `main.py` 含 `FastAPI(version=APP_VERSION)` | `grep -n 'FastAPI(version=APP_VERSION)' DYAutoDM_v2/backend/main.py` | 命中 1（注释行 642）；实际构造 `671:app = FastAPI(` + `680:    version=APP_VERSION,`；硬编码字面量仅剩注释引用（646/875）| **✓ 成立** |
| `.gitignore` 含 `frontend/dist-prototype/` | `grep -n 'frontend/dist-prototype/' DYAutoDM_v2/.gitignore` | `42:frontend/dist-prototype/`（另 38 行注释），有效规则命中 1 | **✓ 成立** |
| 根 `.gitignore` 含 `/_*.py` | `grep -n '^/_\*\.py' .gitignore` | `70:/_*.py`（命中 1）| **✓ 成立** |
| 根 `.gitignore` 含 `_ext_repos/DouYin_Spider_git/` | `grep -n '_ext_repos/DouYin_Spider_git/' .gitignore` | `82:_ext_repos/DouYin_Spider_git/`（命中 1）| **✓ 成立** |
| `requirements.txt` 含 `playwright>=1.62,<1.63` | `grep -n 'playwright>=1.62,<1.63' DYAutoDM_v2/backend/requirements.txt` | `42:playwright>=1.62,<1.63`（命中 1）| **✓ 成立** |

**附加回证（与清单/自述互洽）：**
- `python -c "json.load(...)"` → `_version=1.2.0`、`_updated=2026-09-23`、`known_gaps` 条数 **7**（原 5 + 2），新增两端点各命中 1 ✓
- 报告自述行号（`.gitignore` dist-prototype 42、根 `/_*.py` 70、`_ext_repos/…` 82）与实测**完全一致** ✓

⇒ **identifiers_ok = true**：5 项交叉判据全部命中，无一项落空。

---

## 6.【疑点】

### 6.1 F-9 现况（报告称：两张归档卡删除 + `verify_live_strategy_live.py` 改动，仅登记未提交）

`git status --short -- artifacts/ DYAutoDM_v2/scripts/verify_live_strategy_live.py` 实测：

```
 M DYAutoDM_v2/scripts/verify_live_strategy_live.py
 D artifacts/交接卡_HC-05_多账号并发与直播间登记表_待实施_2026-09-22.md
 D artifacts/交接卡_HC-06_剩余待办_2026-09-22.md
```

- **两张归档卡**：` D`（工作区删除、未暂存）。另实测 `artifacts/handoff_archive/` 下同名的 HC-05 / HC-06 卡以 `??` 存在（归档副本为新增未跟踪文件）⇒ 与报告「已删（工作区）/ 仍在 HEAD」+ §F-9⑤「删除与改名应由同一次提交收口」的判断**一致** ✓
- **`verify_live_strategy_live.py`**：` M`，`git diff --stat` = `36 insertions(+), 170 deletions(-)` —— 与报告 §F-9 表**逐字一致** ✓
- **未 `git add`**：`git diff --cached --name-only` 仅 `_ext_repos/DouYin_Spider_git` 一项，三处均未被暂存 ✓
- ⇒ 报告对 F-9 的「登记未提交、交父会话」描述**属实**。

### 6.2 并发写者告警（F-9 关联疑点）

报告 §F-9⑤ 称 `verify_live_strategy_live.py` 在本轮由**另一执行体**改动（` D`→` M`，mtime 21:10:07）。实测该文件当前确为 ` M`，且同类迹象出现在 `artifacts/`（多个 `??` 未跟踪新文件、`artifacts/HC-10_…审查…md` 等）。⇒ **该文件归属未经我复核确认**，父会话提交前须确认归属，属未决风险。

### 6.3 报告未覆盖 / 无法证实的点

- **index 层唯一性**：报告称 `git rm --cached` 为唯一索引写操作——`git diff --cached` 仅 1 项，**与自述互洽**，未见其他暂存改动的痕迹。
- **L-10 的 md5 还原**：报告自述 `147487b7a2e5041a6ecefc4d581ed354` 前后一致——本次**仅读**，未复算 Cargo.lock md5（因只读且避免触碰），故该行按「报告自述、未独立复算」采信。
- **F-4 取证偏差**：报告已自认 `Cargo.lock` grep 取到依赖 `2.0.1` 属方法偏差；结论 UNCHANGED 不因此受影响（六个版本源 diff 实测为空）✓。

---

## 核查结论摘要

- **自述文件清单**：本批新改 9 项 + 索引层 1 项，实测方向一致，未见报告外的仓库改动。
- **测试自证**：报告**完全没有 `Ran N`**，**无测试自证**；`tests_total = 0`（6 个 `[PASS]` / 1 个 `[FAIL]` 属契约门禁脚本输出，非测试计数）。
- **唯一标识符**：5 项交叉判据（`_version`=1.2.0、`FastAPI(version=APP_VERSION)`、`playwright>=1.62,<1.63`、`dist-prototype/`、根 `/_*.py` + `_ext_repos/…/`）**全部命中**，`identifiers_ok = true`。
- **诚实标注**：多处主动自纠（F-5 注释复制、L-10 EOL 假设被推翻、F-4 取证偏差、T3-b 断言降级），标注质量高；但存在以下未决：F-3 七条缺口仍 open；F-5 Frozen 态、F-7 clone 为空未验证；T3-b 分步安装未复现；F-9 并发写者归属未确认。
- **F-9 现况**：两张归档卡 ` D` + `verify_live_strategy_live.py` ` M`（36+/170-），均未 `git add`，与报告自述完全一致。

{"line":"D","files_claimed":9,"tests_total":0,"identifiers_ok":true,"open_risks":5,"verdict":"D 线报告自证无测试（0 个 Ran N），五项交叉判据全部命中、改动清单与工作区一致；主要未决为 7 条契约缺口仍 open、Frozen/clone 态未验证及 verify_live_strategy_live.py 并发写者归属待确认"}
