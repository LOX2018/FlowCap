# HC-16 / L-19 报告：R3 判据 vs PyInstaller workpath 冲突

- 日期：2026-10-01
- 分支：`design/better-douyin`（git 根 `C:\Users\LOX\Desktop\DYchajian`）
- 执行方式：**静态改动 + 门禁验证**，未跑真打包（遵守硬约束）

---

## ① 改动清单

**选择方案：① 把 workpath/specpath 迁出源码树**（未做方案②，两者未同时实施）。

**选择理由（实测支撑）**：
1. **无既有豁免登记机制** —— 全仓 grep `WARN_ONLY` / `豁免` / `exempt`，`check_iron_rules.py` 内除 `WARN_ONLY = {"R2","R3","R9","R10","R12-B"}`（L547）这一"分级集合"外，**没有任何按路径登记的豁免清单**；写方案②等于新建第二份机制，违反"别新建第二份豁免机制"的要求。
2. **有同源先例可依**，不是自造路线：R10 已把 cargo `target-dir` 经 `src-tauri/.cargo/config.toml:19` 迁到树外 `C:/temp/dyautodm_build/design`，`scripts/build_paths.py` 是产物路径 SSOT。PyInstaller workpath 是**最后一个落在源码树内的构建缓存**，迁出是与既有架构决策对齐。
3. **无下游依赖**：全仓扫描确认没有任何脚本读取 `backend/build` 下的中间产物（见 §⑤），迁移无破坏面。

**改了哪些文件的哪些行**：

| 文件 | 行 | 改动 |
|---|---|---|
| `DYAutoDM_v2/scripts/build_sidecar.py` | L26-44（新增） | 新增 `PYI_WORK_ROOT = ROOT.parent / "build" / "pyi_work"` + `_pyi_work(name)` 辅助函数 + 14 行理由注释（写明"为何迁出、为何不动 --distpath"） |
| `DYAutoDM_v2/scripts/build_sidecar.py` | L146-147 | `"--workpath" / "--specpath"` 由 `str(BACKEND / "build" / name)` → `_pyi_work(name)` |
| `DYAutoDM_v2/scripts/build_sidecar.py` | L775-777 | 并行构建注释同步（原写"各自 backend/build/<name>/"，改为指向 `_pyi_work(name)`） |
| `DYAutoDM_v2/scripts/_rebuild_backend_only.py` | L11-18（新增） | 新增 `PYI_WORK = ROOT.parent / "build" / "pyi_work" / "backend_only"` + 理由注释 |
| `DYAutoDM_v2/scripts/_rebuild_backend_only.py` | L23-24 | `--workpath/--specpath` → `str(PYI_WORK)` |
| `DYAutoDM_v2/scripts/_rebuild_recv_only.py` | L11-18（新增） | 新增 `PYI_WORK = ROOT.parent / "build" / "pyi_work" / "recv_only"` + 理由注释 |
| `DYAutoDM_v2/scripts/_rebuild_recv_only.py` | L23-24 | `--workpath/--specpath` → `str(PYI_WORK)` |

**新落点**：`C:\Users\LOX\Desktop\DYchajian\build\pyi_work\<name>\`
（`ROOT.parent` = 仓库根，在 `DYAutoDM_v2` 源码树**之外**；`<name>` 分目录保留三路并行构建的无共享可写状态前提）

**`.gitignore` 覆盖已实测**（`git check-ignore -v`，逐 name 命中同一条规则）：
```
build/pyi_work/dyautodm-backend/foo.toc           -> .gitignore:17:build/
build/pyi_work/dyautodm-browser-daemon/foo.toc    -> .gitignore:17:build/
build/pyi_work/dyautodm-recv-daemon/foo.toc       -> .gitignore:17:build/
build/pyi_work/backend_only/foo.toc               -> .gitignore:17:build/
build/pyi_work/recv_only/foo.toc                  -> .gitignore:17:build/
```

**`--distpath` 未动**（仍是 `src-tauri/binaries`）—— 产物位置有部署依赖，硬约束明确禁止。

---

## ② 验证数字

**编译自检**（静态，未打包）：
```
$ python -m py_compile scripts/build_sidecar.py _rebuild_backend_only.py _rebuild_recv_only.py
COMPILE_OK
```

**`_pyi_work()` 落点实测**：
```
work   : C:\Users\LOX\Desktop\DYchajian\build\pyi_work\dyautodm-backend
inside src tree? False
```

**门禁真实输出（改后，完整跑）** —— `python DYAutoDM_v2/scripts/check_iron_rules.py`；`EXIT=0`：
```
  [FAIL] R3   backend 无构建产物（命中 5: …\DYAutoDM_v2\backend\build\dyautodm-backend\…exe）
  合计: 20 项，通过 18，未通过 2（阻断 0 / 警告 2）
```
⚠ **R3 此刻仍在告警 —— 但告警的 5 项 100% 是既有磁盘残留，与本次改动无关**。逐项归因（实测枚举）：

| # | 命中项 | mtime | 性质 |
|---|---|---|---|
| 1-3 | `backend/build/dyautodm-{backend,browser-daemon,recv-daemon}/…/*.exe`（3 个，共 ~92MB） | 2026-10-01 **02:34:52** | 02:35 那次打包的残留（改动于 02:5x 完成 → **改动前**产物） |
| 4 | `backend/build`（目录本体，298M） | 2026-09-30 15:55 | 同上 |
| 5 | `backend/%SystemDrive%`（目录本体） | 2026-10-01 **00:54** | **另一个独立残留**，与 workpath 无关（R3 的历史事故项分支） |

判定依据：`find DYAutoDM_v2/backend -iname "*.exe" -printf '%T+'` 最新 = `2026-10-01+02:34:52`，早于我的改动时间；且 `Get-CimInstance Win32_Process` 枚举全部 python 进程命令行，**无任何 PyInstaller 在跑**（只有 hermes/openviking/mcp），残留为死数据。

**"R3 在我的改动生效后会转 PASS"的正向验证**（用真实 `r3_no_build_artifacts_in_src()` 函数，喂一个无 build/dist/.exe 的干净 backend）：
```
$ python -c "... g.BACKEND = <clean tmp backend>; g.r3_no_build_artifacts_in_src()"
  [PASS] R3   backend 无构建产物（命中 0）
```
⇒ 判据会在残留清除后归绿，**无需改 R3 判据本身**，方案①自洽。

**"没有引入新红项"的证据**：改动前后门禁读数完全一致 ——
- 改前：20 项 / 通过 18 / 未通过 2（**阻断 0**，警告 R3+R9）
- 改后：20 项 / 通过 18 / 未通过 2（**阻断 0**，警告 R3+R9）
- R1/R2/R4-R8/R10-R15 逐项均为 PASS，两条告警均为**既有项**（R9 = 红线 28/20，与 L-19 无关）。

**未越界自证**：`git status --porcelain` 中属我改动的仅 `scripts/build_sidecar.py`（`_rebuild_*.py` 两个文件 **git 未跟踪**，故不出现在 diff）；版本文件（package.json / tauri.conf.json / Cargo.toml / `_build_version.py`）diff 为**空**；未执行任何 `git add/commit/checkout/stash/clean`。

---

## ③ 唯一标识符

| 标识符 | 值 | 出处 |
|---|---|---|
| 台账条目 | **L-19** | `工作记忆/00_交接卡待办台账.md:215`、`:491` |
| 交接卡 | **HC-16** | `DYAutoDM_v2/artifacts/交接卡_HC-16_…2026-10-01.md:207,244` |
| 门禁规则 | **R3** | `scripts/check_iron_rules.py:27`（定义）、`:155-167`（实现） |
| 分级集合 | `WARN_ONLY = {"R2","R3","R9","R10","R12-B"}` | `check_iron_rules.py:547` |
| 新落点常量 | `PYI_WORK_ROOT` / `_pyi_work()` / `PYI_WORK` | `build_sidecar.py:38,43`；`_rebuild_*_only.py:18` |
| 忽略规则 | `.gitignore:17` `build/` | 仓库根 |

---

## ④ 诚实标注

1. **R3 告警未消失**，且本报告不声称它已消失。告警项 5 条全部是**改动前**就存在的磁盘残留（最晚 02:34:52，早于改动）。要让它归绿，需清除 `backend/build/`（298M）+ `backend/%SystemDrive%`。
2. **我没有删除这两处残留**，理由：① 硬约束只授权写"改的那几个脚本 + 报告文件"，`git clean` / 目录删除不在授权内；② 另有 3 个并行子 agent 在同一仓库工作，此刻删 298M 目录属跨线破坏性动作。⇒ **需上层决策后由人执行或另行授权**。
3. **未做真打包验证**。workpath 新落点只做了静态验证（路径解析 + `.gitignore` 命中 + 判据函数喂干净样本）。真实 `PyInstaller` 是否会因路径变更产生行为差异（例如相对路径资源解析）**未实测** —— 按硬约束禁止真打包，此项留空不编造。
4. `backend/%SystemDrive%` 是 R3 里**另一个独立分支**（"历史事故项"），与 workpath 冲突无关，不在 L-19 处置范围，仅在此记录共现事实，未改、未删。
5. `_rebuild_backend_only.py` / `_rebuild_recv_only.py` 两个脚本 **git 未跟踪**（`git ls-files --error-unmatch` 报 not known to git），故 `git diff` 不体现它们的改动 —— 改动确已落盘（`py_compile` 通过 + 文件内容已核）。
6. `scripts/build_paths.py` 是 cargo 产物路径 SSOT，我**没有**把 `PYI_WORK_ROOT` 并入它：并入要改 SSOT 文件且牵动 build_all.py/deploy.py/package_installer.py，风险面大于收益（PyInstaller workpath 仅 3 处使用，且 `_pyi_work()` 已单点收敛）。若后续要求"产物路径一律走 SSOT"，此处是可收敛点。

---

## ⑤ 交叉判据（grep 命令 + 命中数）

**判据 A：无既有豁免登记机制**（先搜索再实施，避免新建第二份）
```bash
grep -rn "WARN_ONLY|豁免|exempt" <仓库根>
```
- 命中 80 处（含 `limit` 截断），`check_iron_rules.py` 中**仅** L547 的 `WARN_ONLY` 与 L534 注释；**无任何按路径登记的豁免清单** ⇒ 结论：不存在可复用的豁免机制，方案②必然新建。

**判据 B：无下游读取 `backend/build` 中间产物**
```bash
grep -rn "backend/build\|backend\\build" . \
  --exclude-dir=artifacts --exclude-dir=.git --exclude-dir=node_modules \
  --exclude-dir=__pycache__ --exclude-dir=.hermes --exclude-dir=工作记忆 \
  --exclude-dir=data --exclude-dir=accounts | grep -vE "\.log:|__pycache__|/backend/builder/"
```
- 命中文件数 **12**；剔除后**真正引用 workpath 目录的源码/脚本 = 0**。分类：
  - 真实引用（已全部改掉的 3 处）：`build_sidecar.py`、`_rebuild_backend_only.py`、`_rebuild_recv_only.py`
  - 忽略规则：`DYAutoDM_v2/.gitignore:53` `backend/build/`
  - 注释/文档：`check_iron_rules.py:534`、若干 md
  - **误命中（`backend/builder/` 源码包，非构建产物目录）**：`docs/migration_guide.md:25`、`docs/replication_plan.md:83`、`verify_im_protocol_2026.py:56`、`_triage_ocr_critical.py:135`
  - 历史日志：`build_sidecar_backend.log`（旧构建记录，非代码依赖）
- ⇒ **交叉判据成立**：迁移无破坏面，方案①可行，无需退到方案②。

**判据 C：新落点被 .gitignore 覆盖**
```bash
git check-ignore -v build/pyi_work/<name>/foo.toc   # 5 个 name 逐个
```
- 命中 **5/5**，全部 `.gitignore:17:build/`。

**判据 D：残留为死数据（非在跑构建持有）**
```powershell
Get-CimInstance Win32_Process -Filter "Name like '%python%'" | Select-Object ProcessId,CommandLine
```
- 命中 8 个 python 进程，**PyInstaller 命中 0** ⇒ 残留可安全清理（清理动作未执行，见 §④-2）。

**判据 E：未引入新红项**
```bash
python DYAutoDM_v2/scripts/check_iron_rules.py ; echo "EXIT=$?"
```
- 改前 / 改后均 `EXIT=0`，`阻断 0`，警告集合同为 {R3, R9} ⇒ **无新增红项**。

---

## ⑥ 疑点

1. **`backend/%SystemDrive%` 从哪来？** mtime `2026-10-01 00:54`，内含 `ProgramData/Microsoft/Windows/…`。这是某处把未展开的环境变量字面量 `%SystemDrive%` 当作相对目录名创建出来的产物（典型：`os.makedirs(os.path.join(base, "%SystemDrive%", ...))` 未 `os.path.expandvars`）。**根因未定位**（未 grep 定位创建点，超出 L-19 范围），但它独立触发 R3 的第 5 项命中 —— 即使清掉 `backend/build`，R3 仍会报 1 项。建议单开一条台账（L-19 之外）。
2. **R3 的 `walk(BACKEND, (".exe",))` 为何能穿透 `build/`（已当场复核解决，非遗留疑点）**：`walk()` 默认 `skip_dirs` 含 `"build"`，但 R3 调用处 `check_iron_rules.py:157` **显式传了 `skip_dirs={"__pycache__"}`**，覆盖了默认值 ⇒ `build` 未被剪枝，故 3 个 exe 被扫到。实测：`'build' in 默认skip_dirs = True`，`walk(BACKEND,('.exe',),skip_dirs={'__pycache__'})` 命中 **3**。⇒ 这是 R3 **有意为之**（宁可穿透 build 也要抓到 exe），不是 bug；但也意味着**只删 `backend/build` 目录名而不删其中 exe，或只删 exe 不删目录，R3 都会继续报警**。清理时必须两者都清。
3. **`_rebuild_*_only.py` 未纳入 git**：这两个 hotfix 脚本游离在版本控制外，任何人改动都不产生 diff、不可追溯。本次改动因此**不体现在 `git diff`**。建议后续决定是否入库（非 L-19 范围，未执行 `git add`）。
4. **并行构建前提未被实测证伪也未被证实**：原注释称三路并行依赖"workpath 各自独立"。我把它们迁到 `build/pyi_work/<name>` 后仍保持按 name 隔离，语义上等价，但**未跑真构建验证并发安全性**（禁止真打包）。
5. **298M 残留的成本**：`backend/build` 长期滞留会持续触发 R3 警告，而警告长期存在等于"狼来了"。若本次不清理，L-19 只能算"半闭环"（改了因，未除果）。**建议上层尽快安排一次清理**（`backend/build` + `backend/%SystemDrive%`），清理后 R3 应归绿 —— 该预期已由 §② 的判据函数正向验证支撑。
