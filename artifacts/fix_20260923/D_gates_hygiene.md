# D 批 · 门禁恒失败 + 仓库卫生 + 依赖上界（P3-8/P3-9、F-3~F-9、L-10、T3-a、T3-b）

> 仓库：`C:\Users\LOX\Desktop\DYchajian`　分支：`design/better-douyin`　执行时 HEAD：`b455192`　产品版本：`0.44.53`
> 解释器：`C:\Users\LOX\AppData\Local\Programs\Python\Python314\python.exe`　terminal：bash(MSYS)
> 范围纪律：**未**改任何版本源文件（父会话统一升版）•**未**改 `check_contracts.py` 检查逻辑•**未** `git add/commit/checkout/stash/clean`
> 唯一例外且有意为之的索引操作：F-7 的 `git rm --cached`（**只动索引、不动工作区**，见 F-7 §⑤）

---

## 0. 一页总览

| 项 | 判定 | 修法 | 关键实测（前 → 后） |
|---|---|---|---|
| **F-3 / P3-9** 契约门禁长期红 | 真缺陷（**基线漏登**，非新引入） | 逐端点精确登记 2 个端点 | `check_contracts` REAL_EXIT **1 → 0** |
| **F-5** `main.py:642` 硬编码版本 | 真缺陷（断言与代码矛盾 + 门禁盲区） | 改用 `APP_VERSION` 唯一来源 | OpenAPI version **0.43.83 → 0.44.53** |
| **F-6** 构建产物未忽略 | 真缺陷（4 处，审计漏了第 4 处） | 补 3 条 + 根级 `_*.py` | `git add --dry-run` 命中 **4 项 → 0 项** |
| **F-7** 坏 gitlink | 真缺陷 | 择②移出索引 + 忽略 | `submodule status` **fatal → 干净** |
| **F-8** HC-05 编号复用 | 真缺陷 | 改编号为 **HC-09**（保留卡片） | 归档区 HC-05 **2 份 → 1 份** |
| **F-9** 归档动作未提交 | 已登记（**无需改代码**） | 仅登记，交父会话 | 3 条状态实测见 §F-9 |
| **F-4** 提交信息与产物脱节 | 已登记（历史教训） | 仅登记 | `48ae837` 六处版本源 **全部 UNCHANGED** |
| **L-10** Cargo.lock 第六处 | ⚠️ **审计结论已过期**（`48ae837` 已接入） | 补破坏性验证 + 文案去硬编码 | 门禁 **红 → 绿**（破坏→还原） |
| **T3-a** M-11 C 类判据 | 目标态已在 HEAD 达成 | 补 L3 证据路径 + 实测 | 见 §T3-a |
| **T3-b** `playwright` 无上界 | 真缺陷 | 钉上界 + 补 2 个声明 | 干净机解析 **1.63.0 → 1.62.0**；`camoufox+1.63` **unsatisfiable** |

**自纠 1 次**（诚实记录）：验证 L-10 时我先把 `Cargo.lock` 转成 LF 以测 EOL 脆弱性；**假设被实测推翻**，已按 mtime 前备份字节级还原（md5 `147487b7a2e5041a6ecefc4d581ed354` 前后一致，`git diff` 空）。
**发现审计未列的第 5 个污染源**：根级 `_fix_visibility_test.py` 的成因是 `_*.py` 规则**只写在 `DYAutoDM_v2/.gitignore`**，作用域不覆盖仓库根（`#!/_*.py`，已补）。

---

## F-3 / P3-9 契约门禁长期红（G2 有 2 处新增违规）

### ① 位置
- 门禁：`DYAutoDM_v2/scripts/check_contracts.py`（G2 段，行 82–111）
- 基线：`DYAutoDM_v2/docs/design-contracts/.known-gaps.json`
- 违规端点：`backend/dy_apis/client_user.py:/aweme/v1/web/aweme/post/`、`backend/dy_apis/client_video.py:/aweme/v1/web/aweme/detail/`

### ② 判定
**真缺陷，但根因是「基线漏登」，不是本轮引入。** 两点实测坐实：

1. **窗口起点就已存在**（`21fdbf4` = 窗口起点 `v0.44.29`，且实测为 HEAD 祖先）：
   ```
   $ git show 21fdbf4:DYAutoDM_v2/backend/dy_apis/client_user.py | grep -n 'aweme/v1/web/aweme/post/'
   430:        api = f"/aweme/v1/web/aweme/post/"
   $ git show 21fdbf4:DYAutoDM_v2/backend/dy_apis/client_video.py | grep -n 'aweme/v1/web/aweme/detail/'
   53:        api = f"/aweme/v1/web/aweme/detail/"
   ```
2. **窗口内 0 次改动**（`21fdbf4..b455192`，67 个提交）：
   ```
   $ git log --oneline 21fdbf4..b455192 -- .../client_user.py .../client_video.py | wc -l
   0
   ```
3. 根因：`.known-gaps.json` 从 2 条精确化到 5 条（配合 `classify()` 的前缀匹配→逐端点精确匹配改造）时，**只补了 `client_collection.py` 的 4 条 + `client_video.py` 的 `tab/feed`**，漏了这两个端点 ⇒ 门禁长期红（运维读数 5 处 / 实测 7 处）。

### ③ 修法
按该门禁的**设计语义逐端点精确登记**（`target` 与实测 offender 字符串**完全相等**），**不放宽匹配口径、不删/弱化检查逻辑**：

- `check_contracts.py`：**零字节改动**（`git diff` 空）—— 证明没为了变绿而削检查。
- `.known-gaps.json`：新增 2 条 `G2 C-02 secsdk 签名接线` 条目，各带 `reason / status / verify_how / risk`，并在 `reason` 里写明「窗口起点已存在 + 0 次改动 + 漏登原因」；`_version` 1.1.0→1.2.0、`_updated` →2026-09-23。

### ④ 修复前 / 后实测输出
**修复前：**
```
$ python scripts/check_contracts.py
  [PASS] G0 契约文件 ≥5                         实测 5 份
  [PASS] G1 C-01 捕获零主动查询                    0 命中
  [FAIL] G2 C-02 secsdk 签名接线                ⚠ 已知缺口 5 处（见 .known-gaps.json）；新增违规 ['backend/dy_apis/client_user.py:/aweme/v1/web/aweme/post/', 'backend/dy_apis/client_video.py:/aweme/v1/web/aweme/detail/']
  [PASS] G3 C-03 dm 不冒充 wp                  0 命中
  [PASS] G4 C-04 投递有回执/落库验证                 存在回执处理
  [PASS] G5 C-05 reflow 主引擎存在               已实现
1 项未通过：['G2 C-02 secsdk 签名接线']
REAL_EXIT=1
```
**修复后：**
```
$ python scripts/check_contracts.py
  [PASS] G2 C-02 secsdk 签名接线                ⚠ 已知缺口 7 处（见 .known-gaps.json）
REAL_EXIT=0
$ python scripts/check_contracts.py --quiet ; echo $?
0
```

### ⑤ 诚实标注
- 两个端点是**真实未接线缺口**（不是误报）：`api = f"/aweme/v1/web/aweme/post/"` 之后 60 行窗口内无 `signed_url(`。登记为 **open（work-in-progress）**，`verify_how` 写明「补 `signed_url()` + 带/不带签名对照实测；再删本项」，**未**谎称已修。
- `.known-gaps.json` 的 `_doc` 规定「任何一项**修复后**必须从本文件删除」⇒ 本轮是**登记未修缺口**（合规方向），不是把已修项留在基线。
- 无权限清理：这两个文件属他人改动范围（`dy_apis/*`），本次只动基线文件。

---

## F-5 `backend/main.py:642` 硬编码版本（断言与代码矛盾 + 门禁盲区）

### ① 位置
- `DYAutoDM_v2/backend/main.py:642`（原 `version="0.43.83"`，其上是 `app = FastAPI(...)`）
- 真权威：同文件 `_build_version()` / `APP_VERSION`（原 837–896 行）

### ② 判定
**真缺陷，三重问题：**
1. **断言与代码矛盾**：上方注释自称「现与产品版本同源（手动同步）」，实际写死 `"0.43.83"`，而产品已是 `0.44.53` ⇒ **漂移 10 个小版本**。
2. **门禁盲区**：`check_version_sync.py` 的 6 处源**不含**这一处，所以它漂移再久也不会红。
3. **两套版本概念**：同一文件里 `FastAPI(version=硬编码)` 与 `_build_version()/APP_VERSION` 并存，读者无法判断哪个是真权威。

### ③ 修法（**选「唯一来源」，不选「纳入门禁」——理由如下**）
把版本计算上移并令 `FastAPI(version=APP_VERSION)`，**删除硬编码字面量**；同时合并掉重复的第二份 `_build_version()/APP_VERSION` 定义（只留一行指针注释），从结构上消除「两套版本概念」。

**为什么不选「把它纳入门禁」**：纳入门禁只能「事后发现漂移」，本文件仍保留一个需要人工同步的字面量（正是本次出问题的形态，`0.1.0`→`0.43.83` 已经错过两轮）；改为引用 `APP_VERSION` 后该值**就是** `_build_version.py` 的编译期常量 —— **恰是门禁已检查的第 4 处** ⇒ 盲区**结构性**关闭，且**无需在门禁里重复登记同一个事实**（避免 M-10「同一事实双写」类问题）。追加收益：`/api/version` 探针、`X-App-Version-Backend` 响应头与 OpenAPI 文档三处现在必然同值。

### ④ 修复前 / 后实测输出
**修复前：**
```
$ grep -n 'version=' backend/main.py
642:    version="0.43.83",
```
**修复后：**
```
$ grep -nE 'version="[0-9]' backend/main.py      # 只命中注释里的历史说明，无代码字面量
646:# 历史（2026-09-23 F-5 修补）：此处曾写死 `version="0.43.83"`（而当时产品已是
875:# 定义，与上方 `FastAPI(version="0.43.83")` 的硬编码字面量并存 ⇒ 同一文件
$ grep -c 'APP_VERSION = _build_version()' backend/main.py
1                                                # 定义只出现一次（重复份已合并）

$ cd backend && python -c "import main; print(main.APP_VERSION, main.app.version, main.app.openapi()['info']['version'])"
APP_VERSION     = 0.44.53
app.version     = 0.44.53          # 0.43.83 -> 0.44.53
OpenAPI version = 0.44.53
single-source   = True

$ python -m py_compile backend/main.py && echo PY_COMPILE_OK
PY_COMPILE_OK
```

### ⑤ 诚实标注
- ⚠️ **我中途做错过一次并已自纠**：第一次 patch 因 `old_string` 在文件内不唯一，**复制了一份注释块**（`main.py` 一度出现两段相同注释）。已立即回退，并用「**断言 diff 为空**」证明恢复到位（`git diff -- DYAutoDM_v2/backend/main.py` 空）后才重做。
- 本文件改动**只涉及 F-5 这一处**（`main.py` 其余部分零改动）。
- 合并第二份定义是在**同一文件同一功能**内消除重复（属 F-5 目标本身），不是顺手重构；`_version_guard` 逻辑**逐字未动**。
- 未验证边界：`_build_version()` 在 **Frozen（PyInstaller）态**的取值依赖 `_build_version.py` 随包打包，本轮只在源码态实测；打包态行为未变（本次未改该函数）。

---

## F-6 构建产物未忽略（`git add .` 即污染仓库）

### ① 位置
仓库根 `.gitignore`、`DYAutoDM_v2/.gitignore`

### ② 判定
**真缺陷，且审计清单漏了一项。** 实测 4 处（非 3 处）会被 `git add .` 带入：

```
$ git add --dry-run . | grep -E "_proto|dist-prototype|vite.prototype|_fix_visibility"
add 'DYAutoDM_v2/frontend/_proto/framer-motion-stub.tsx'
add 'DYAutoDM_v2/frontend/dist-prototype/preview-pages.html'
add 'DYAutoDM_v2/frontend/vite.prototype.config.ts'
add '_fix_visibility_test.py'          ← 审计未列的第 4 项
```
第 4 项的**成因**：一次性脚本约定 `_*.py` **只写在 `DYAutoDM_v2/.gitignore`**（行 90）。`.gitignore` 的作用域是自身所在目录**及其子树**，**不覆盖仓库根** ⇒ 根级新产生的 `_*.py` 一直是裸奔状态（同目录已入库的 `_export_git.py` / `_push_user_profile.py` / `_jscheck.js` 是历史遗留）。

### ③ 修法
- `DYAutoDM_v2/.gitignore`（`# Frontend` 段）补 3 条：`frontend/dist-prototype/`、`frontend/_proto/`、`frontend/vite.prototype.config.ts`（附「原型预览临时资产」说明）。
- 仓库根 `.gitignore` 补 1 条 `/_*.py`（**锚定根目录**，语义与 `DYAutoDM_v2/.gitignore` 的 `_*.py` 一致），并注明「gitignore 不影响已跟踪文件 ⇒ 三个历史脚本照旧入库」。

### ④ 修复前 / 后实测输出
**修复前：**
```
$ for p in .../dist-prototype/ .../_proto/ .../vite.prototype.config.ts _fix_visibility_test.py; do git check-ignore -v "$p" || echo NOT_IGNORED; done
NOT_IGNORED      (×4，逐项与上表一致)
```
**修复后：**
```
$ for p in DYAutoDM_v2/frontend/dist-prototype/ DYAutoDM_v2/frontend/_proto/ \
           DYAutoDM_v2/frontend/vite.prototype.config.ts _fix_visibility_test.py; do
    git check-ignore -v "$p" || echo "*** STILL NOT IGNORED ***"; done
DYAutoDM_v2/.gitignore:42:frontend/dist-prototype/	DYAutoDM_v2/frontend/dist-prototype/
DYAutoDM_v2/.gitignore:43:frontend/_proto/	DYAutoDM_v2/frontend/_proto/
DYAutoDM_v2/.gitignore:44:frontend/vite.prototype.config.ts	DYAutoDM_v2/frontend/vite.prototype.config.ts
.gitignore:70:/_*.py	_fix_visibility_test.py                     ← 4/4 全部命中

$ git add --dry-run . | grep -E "_proto|dist-prototype|vite.prototype|_fix_visibility"; echo "rc=$?"
rc=1                                             # 污染项 4 -> 0
$ git status --ignored --short -- DYAutoDM_v2/frontend/ | grep -E "_proto|dist-prototype|prototype"
!! DYAutoDM_v2/frontend/_proto/
!! DYAutoDM_v2/frontend/dist-prototype/
!! DYAutoDM_v2/frontend/vite.prototype.config.ts
```
**回归反证**（没把「新目录里的正常文件」一起误伤；探针目录用后即删）：
```
$ mkdir -p DYAutoDM_v2/backend/_f6ok && echo y > DYAutoDM_v2/backend/_f6ok/keep.txt
$ git add --dry-run DYAutoDM_v2/backend/_f6ok/keep.txt
add 'DYAutoDM_v2/backend/_f6ok/keep.txt'          # 正常文件仍可入库
$ rm -rf DYAutoDM_v2/backend/_f6ok
```
**已入库的 `_*.py` 未受影响：**
```
$ git ls-files | grep -E '^_[^/]*\.(py|js)$'
_export_git.py
_jscheck.js
_push_user_profile.py
```

### ⑤ 诚实标注
- 验证期间我用**双向命令**做过等价性检查：`git add --dry-run`（判定「会不会入库」）与 `git status --ignored --short`（判定忽略态）**双向一致**。
- ⚠️ 附带发现一个**既有**的 git 行为（**与 F-6 无关、非我引入**）：对本仓库根**不存在的**路径加尾斜杠做 `git check-ignore -v` 会返回上面最后一条规则且 rc=0（如 `git check-ignore -v DYAutoDM_v2/backend/zzz-absent-dir/` → 命中 `.gitignore:127`）。我用**只含空白/注释行的 .gitignore** 无法复现，且该现象**不产生兜底豁免**（缺失文件加尾斜杠仍 rc=1）。⇒ 属 `git check-ignore` 在 MSYS 上对「不存在路径」的怪癖，**不影响任何真实文件的忽略/入库判定**（已用 `git add --dry-run` 交叉验证）。**不作为缺陷上报，仅备查**，以免我把它当成 F-9 那类真问题虚报。
- 未改任何已入库文件，未做 `git add`。

---

## F-7 `_ext_repos/DouYin_Spider_git` 坏 gitlink

### ① 位置
索引与 HEAD 树中的 `_ext_repos/DouYin_Spider_git`（模式 `160000`）；仓库根 `.gitignore`

### ② 判定
**真缺陷，四处可观测后果全部实测坐实：**
```
$ git ls-tree HEAD _ext_repos/DouYin_Spider_git
160000 commit 1712dad4f59a8ccfc54e4e215b79b83d805293c9	_ext_repos/DouYin_Spider_git
$ ls .gitmodules
ls: cannot access '.gitmodules': No such file or directory
$ git submodule status ; echo rc=$?
fatal: no submodule mapping found in .gitmodules for path '_ext_repos/DouYin_Spider_git'
rc=128
```
⇒ ① `git submodule status` fatal；② **他人 clone 得到空目录**（gitlink 无 URL 可拉）；③ ocr 每轮报 `cannot read file _ext_repos/DouYin_Spider_git at ref HEAD: exit status 128`，污染统计；④ 工作区里它其实是**完好的本地仓库**（108 个文件），只是被错误地当成「子模块引用」记进索引。

### ③ 修法（**择②**）
**从索引移除 + 加入 .gitignore；不新建 `.gitmodules`。**

理由：它是**本地对照参考库**，不是本项目依赖 —— 同一目录下 `DouYin_Spider-master` / `DouYin_Spider_latest` / `douyin-jiliu-src` 三个同源对照库**均未按 submodule 管理**（`DouYin_Spider-master` 是普通目录树、正常入库）。把它改成 submodule 会引入「别人 clone 必须带 `--recursive` 且能访问上游 URL」的新约束，而实际上没有任何代码依赖它 ⇒ 违反最小意外原则。

### ④ 修复前 / 后实测输出
**修复前：** 见 ②（`ls-tree` 报 `160000 commit 1712dad...`；`submodule status` fatal rc=128）。

**修复后：**
```
$ git rm --cached --quiet _ext_repos/DouYin_Spider_git && echo "rm --cached ok"
rm --cached ok
$ git ls-files -s _ext_repos/DouYin_Spider_git        # 索引条目已消失（无输出）
$ git status --short -- _ext_repos/
D  _ext_repos/DouYin_Spider_git                        # 删除已暂存 -> 父会话提交即移除
$ git check-ignore -v _ext_repos/DouYin_Spider_git/ _ext_repos/DouYin_Spider_git/Dockerfile
.gitignore:82:_ext_repos/DouYin_Spider_git/	_ext_repos/DouYin_Spider_git/
.gitignore:82:_ext_repos/DouYin_Spider_git/	_ext_repos/DouYin_Spider_git/Dockerfile
$ find _ext_repos/DouYin_Spider_git -type f | wc -l
108                                                    # 工作区内容零损失
$ ls .gitmodules
ls: cannot access '.gitmodules': No such file or directory   # 未新增 .gitmodules（如设计）
```

### ⑤ 诚实标注
- **这是本批唯一一次写索引**：`git rm --cached`（**不是** `git rm`）。它只删索引条目、**不删磁盘文件**（108 个文件原样保留，已核对）。之所以必须写索引：仅靠 `.gitignore` **无法**让已跟踪的 gitlink 消失。
- ⚠️ **风险提示（请父会话确认）**：若父会话习惯用 `git add -A` 提交，此删除会**一并被提交**（这是达成 F-7 所必需的）；若父会话想单独审阅，可先 `git restore --staged _ext_repos/DouYin_Spider_git` 还原索引、再用 `.gitignore` 规则在下次改动时生效 —— 但那样 F-7 需要**再来一轮**才会在上游消失。
- 未验证：`ocr` 的实际输出（我未运行 ocr，仅复现了它读取失败的同源命令 `git ls-tree` / `submodule status`）。「clone 为空」为**推导**（gitlink 无 `.gitmodules` 映射时 git 不会拉取），未实机 clone 验证。

---

## F-8 交接卡编号被复用（违反「编号永不复用」）

### ① 位置
- `artifacts/handoff_archive/交接卡_HC-05_H9剩余_待实施_2026-09-22.md`（3,039 B，落盘 2026-09-22 20:37）
- `artifacts/handoff_archive/交接卡_HC-05_多账号并发与直播间登记表_待实施_2026-09-22.md`（9,242 B，落盘 2026-09-22 22:25）
- 台账 `工作记忆/00_交接卡待办台账.md` §5.4 编号登记表

### ② 判定
**真缺陷。** 两份**不同内容**共用 `HC-05` ⇒ 用户说「HC-05」时指向不可判定。

**判定「谁是真正最早的 HC-05」（用分配时间，不猜）**：
```
$ git log --diff-filter=A --format='%ci %h %s' -- <两份卡路径>
2026-09-22 20:02:48 +0800 58fdc18 docs(handoff): 落盘 HC-05 剩余交接卡（H-9 后续 + 其他待办）— 会话收尾
2026-09-22 17:11:49 +0800 da367ad docs(handoff): HC-05 建立（多账号并发 ADR-002 + 直播间登记表 ADR-003，待实施）+ 台账登记
```
- `da367ad`（**17:11**）：**创建** HC-05，**同时**改名台账即登记 ⇒ 这是**编号的正式分配** ⇒ **`多账号并发与直播间登记表` 是真正的 HC-05**。
- `58fdc18`（**20:02**）：**晚 2h51m**，标题自称「**HC-05 剩余**」，卡内也写「编号：HC-05-H9-剩余」⇒ 它把自己挂在别人的编号下 ⇒ **应改编号的是这一份**。§5.4 登记表也印证：HC-05 行指向「多账号并发」卡。
- 另：`eb5d5ab` 曾把它从 `artifacts/` 顶层移入 `handoff_archive/`（**未删除**，符合「不要删除任何归档卡」）。

**编号可用性核对**：`§5.4` 已用到 **HC-08**，`artifacts/` + `工作记忆/` 全文 `grep "HC-09"` **0 命中** ⇒ 选 **HC-09**（紧邻下一号，避免留空号更难追溯）。

### ③ 修法
1. **文件系统改名**（`mv`，**不是** `git mv` —— 该文件从未入库，用 `git mv` 会产生无法提交的 bug）：`交接卡_HC-05_H9剩余_待实施_2026-09-22.md` → `交接卡_HC-09_H9剩余_待实施_2026-09-22.md`。**卡片保留、零删除、内容零修改。**
2. 台账 §5.4 登记表**新增 HC-09 行**（记录改编号事实 + 原文件名），并在 **HC-05 行**加消歧指针「同名的「H-9 剩余」旧卡已改编号为 **HC-09**（见下行）」。

### ④ 修复前 / 后实测输出
**修复前：**
```
$ ls artifacts/handoff_archive/ | grep HC-05
交接卡_HC-05_H9剩余_待实施_2026-09-22.md
交接卡_HC-05_多账号并发与直播间登记表_待实施_2026-09-22.md      ← 2 份
```
**修复后：**
```
$ ls artifacts/handoff_archive/*.md
交接卡_HC-01_台账接续与知识库同步_2026-09-22.md
交接卡_HC-02_架构业务复盘与T-01冻结_2026-09-22.md
交接卡_HC-03_T-01实机取证与并发冲突处置_2026-09-22.md
交接卡_HC-04_回放层与错误码收口_待重建部署04436_2026-09-22.md
交接卡_HC-05_多账号并发与直播间登记表_待实施_2026-09-22.md
交接卡_HC-06_剩余待办_2026-09-22.md
交接卡_HC-07_P3架构重构全链路闭环_2026-09-23.md
交接卡_HC-09_H9剩余_待实施_2026-09-22.md                      ← 唯一 HC-05
$ ls artifacts/handoff_archive/ | grep -c HC-05
1
$ md5sum 交接卡_HC-09_H9剩余_待实施_2026-09-22.md
e34aca9fbeae93cbdfb197a82e756386   # 与改名前的 e34aca9fbeae93cbdfb197a82e756386 相同 ⇒ 内容零改动
```

### ⑤ 诚实标注
- 「最早」依据是 **commit 的 creation date（`git log --diff-filter=A`）+ 台账 §5.4 均指向「多账号并发」**；两者一致，故判据不是单一来源。
- 该卡**从未入库**（`git show HEAD:<path>` fatal，`git ls-files` 无）⇒ 归档区里它现在是**未跟踪文件**，需父会话 `git add` 才会进仓库。**我没有 `git add`。**
- 未改卡内正文（含卡内自称的「编号：HC-05-H9-剩余」）——**只改文件名**，避免无授权改写他人卡片内容；编号纠正事实记在**台账**（SSOT）里。
- 未做「HC-09 引用回填」：全文搜索显示**除台账与新文件名外无其它文件引用**这个旧编号（无断链需修）。

---

## F-9 归档动作未提交（工作区 vs HEAD 不一致）

### ① 位置
| 路径 | 工作区 vs HEAD | 实测 |
|---|---|---|
| `artifacts/交接卡_HC-05_多账号并发与直播间登记表_待实施_2026-09-22.md` | **已删（工作区）/ 仍在 HEAD** | `git cat-file -e HEAD:<path>` OK 且 `[ ! -e path ]` ⇒ 待提交的删除 |
| `artifacts/交接卡_HC-06_剩余待办_2026-09-22.md` | **已删（工作区）/ 仍在 HEAD** | 同上 |
| `DYAutoDM_v2/scripts/verify_live_strategy_live.py` | **已改（工作区）/ HEAD 是旧版** | `git diff --stat` = `36 insertions(+), 170 deletions(-)` |

```
$ git status --short -- artifacts/ DYAutoDM_v2/scripts/verify_live_strategy_live.py
 D artifacts/交接卡_HC-05_多账号并发与直播间登记表_待实施_2026-09-22.md
 D artifacts/交接卡_HC-06_剩余待办_2026-09-22.md
 M DYAutoDM_v2/scripts/verify_live_strategy_live.py
```

### ② 判定 / ③ 修法
**无需改代码 —— 按要求仅登记，交父会话提交时一并处理。**

### ④ 实测输出
见上表；另确认**我没有把这些加进暂存区**：
```
$ git diff --cached --name-only
_ext_repos/DouYin_Spider_git          # 仅 F-7 的索引移除（见 F-7 §⑤）
```

### ⑤ 诚实标注
- **并发写者告警**：`verify_live_strategy_live.py` 的 mtime 是 **21:10:07**（我本轮 21:17 采样），且我开工时的首次 `git status`（约 20:38）显示它是 ` D`（已删）、现在变成 ` M`（已改）⇒ **该文件在本轮期间被另一执行体改动过**（不是我）。**我零改动它**，也不在它的所有权范围内。
- 三条均属**父会话的提交职责**，我按任务书要求**未 `git add`**。
- 顺手确认父会话会遇到的连带影响：这两个归档卡的删除 + F-8 的改名应由同一次提交收口（改名属新增未跟踪文件，删除属待提交删除）。

---

## F-4 提交信息版本号与产物脱节（历史事实，仅登记）

### ① 位置
提交 `48ae837`（`fix(guards): M-10 G13/G7 门禁 + H-8 收尾 + H-6 审计快照 + M-5 方案（v0.44.44）`，2026-09-22 22:51:03）

### ② 判定
**真缺陷（历史事实，已确认，无需改代码）。** 标题声称 `v0.44.44`，但**六处版本源一个都没改**：
```
$ for f in package.json frontend/package.json tauri.conf.json Cargo.toml Cargo.lock _build_version.py; do
    print  ^ = version@48ae837^ , = version@48ae837 ; done
package.json        ^=0.44.43  =0.44.43  UNCHANGED
package.json        ^=0.44.43  =0.44.43  UNCHANGED     (frontend/)
tauri.conf.json     ^=0.44.43  =0.44.43  UNCHANGED
Cargo.toml          ^=0.44.43  =0.44.43  UNCHANGED
Cargo.lock          ^=2.0.1    =2.0.1    UNCHANGED     (脚本按 dyautodm-v2 段取，见注)
_build_version.py   ^=0.44.43  =0.44.43  UNCHANGED
$ git show --stat --format='' 48ae837
 .../backend/test_element_inspector_guards.py |   2 +-
 DYAutoDM_v2/backend/test_no_dup_dict_keys.py |  13 +
 DYAutoDM_v2/scripts/check_version_sync.py    |   8 +-
 DYAutoDM_v2/scripts/verify_live_strategy_live.py | 12 +-
 artifacts/h6_audit_snapshot_2026-09-22.md    | 126 ++++
 artifacts/m5_send_delivery_design.md         | 299 ++++
 工作记忆/00_交接卡待办台账.md                |   8 +-
 7 files changed, 460 insertions(+), 8 deletions(-)     ← 无一个版本源
```
⇒ **提交信息声明的版本 ≠ 实际产物版本**，且该提交本身**没有**触发任何版本门禁（门禁只验文件、不验提交信息）。

### ③ 修法
**无代码改动**（历史事实，不改写已发布历史）。

### ④ / ⑤ 诚实标注
- 上表 `Cargo.lock` 行我用的原始 grep 取到的是 `2.0.1`（文件里**第一个** `version=`，属某个依赖），**不是** `dyautodm-v2` 段 —— 这是**我的取证方法偏差**，已在此标注；正确的取法见 L-10 用的 `name = "dyautodm-v2"` 锚点。不影响结论 UNCHANGED。
- **教训（建议写入版本纪律）**：提交信息里的版本号必须由**门禁**产出（`check_version_sync.py <版本>` 先跑绿，再拿该值写标题），否则等于一句无法证伪的话。`check_version_sync.py` 支持位置参数断言（`python scripts/check_version_sync.py 0.44.53`）⇒ 已有现成机械手段：**先断言再提交**。
- 我**未**改写该历史提交（禁 `git commit --amend` / rebase / filter-repo）。

---

## L-10 `Cargo.lock` 是第六处版本源但门禁未检查

### ① 位置
`DYAutoDM_v2/scripts/check_version_sync.py` 的 `TARGETS`；`DYAutoDM_v2/src-tauri/Cargo.lock` 的 `[[package]] name = "dyautodm-v2"` 段。

### ② 判定
⚠️ **审计结论在本版 HEAD 上已过期 —— 第六处早已接入，但「从来没被破坏性验证过」。**
```
$ git log --oneline -S 'Cargo.lock' -- DYAutoDM_v2/scripts/check_version_sync.py
48ae837 fix(guards): M-10 G13/G7 门禁 + H-8 收尾 + H-6 审计快照 + M-5 方案（v0.44.44）
$ git show HEAD:DYAutoDM_v2/scripts/check_version_sync.py | grep -n Cargo.lock
37:    ("src-tauri/Cargo.lock", "cargo_lock", "Rust Cargo.lock（dyautodm-v2 段，构建时自动改写）"),
```
⇒ `48ae837`（v0.44.44）已把 Cargo.lock 加进 `TARGETS`，提取器 `cargo_lock` 用 `name = "dyautodm-v2"\nversion = "..."` 锚点（**不是** 依赖版本）。**真正的缺口是：原提交只改了代码、没有任何一次「改坏它看门禁会不会红」的证据** ⇒ 无法排除「只打印不判定」的假门禁。

### ③ 修法
1. **补做缺失的破坏性验证**（无产物风险）：`0.44.53 → 0.99.99` 看是否变红，再还原看是否变绿。
2. `check_version_sync.py` 两处**非削弱性**加固：
   - docstring 追加「L-10 补记」：记录第六处引入提交（`48ae837`）、本轮红/绿两次实测、以及 **F-5 盲区为何无需重复登记**（它已收敛到本门禁的第 4 处 `_build_version.py`）。
   - 总结行 `f"✓ 六处版本齐平"` → `f"✓ {len(TARGETS)} 处版本齐平"`（**去硬编码**：以后增删目标时文案不再说谎）。
   - **检查逻辑（`_extract` / 比较 / 退出码）逐字未动。**

### ④ 修复前 / 后实测输出
**基线（绿）：** `check_version_sync.py` → `✓ 六处版本齐平: 0.44.53`，`GREEN_EXIT=0`

**破坏 `Cargo.lock`（`0.44.53` → `0.99.99`）：**
```
$ python _l10_probe.py break
BROKEN -> 0.99.99
$ python scripts/check_version_sync.py
  [OK  ] frontend/package.json              = 0.44.53     # ...
  [OK  ] src-tauri/Cargo.lock               = 0.99.99     # Rust Cargo.lock（dyautodm-v2 段，构建时自动改写）
✗ 版本门禁未通过：
   - 版本不一致: ['0.44.53', '0.99.99']
RED_EXIT=1                                     ← 门禁真的会红（不是假门禁）
```
**还原：**
```
$ python _l10_probe.py restore
RESTORED -> 0.44.53
$ python scripts/check_version_sync.py ; echo GREEN_EXIT=$?
✓ 六处版本齐平: 0.44.53
GREEN_EXIT=0
$ md5sum src-tauri/Cargo.lock
147487b7a2e5041a6ecefc4d581ed354    # 与操作前完全一致
$ git diff --stat -- DYAutoDM_v2/src-tauri/Cargo.lock
(empty)                              # Cargo.lock 未被改坏留下
```
**加固后：**
```
$ python scripts/check_version_sync.py ; echo EXIT=$?
✓ 6 处版本齐平: 0.44.53              # 动态计数（原为硬编码「六处」）
EXIT=0
$ python scripts/check_version_sync.py 0.44.53 >/dev/null ; echo $?   # 位置参数断言仍工作
0
$ python scripts/check_version_sync.py 9.9.9   >/dev/null 2>&1 ; echo $?
1
```

### ⑤ 诚实标注
- 🔴 **我犯了一个实验错误并已自纠（重要）**：为验证「`Cargo.lock` 是 CRLF（`core.autocrlf=true` 后的产物）」是否会让 `cargo_lock` 提取器失效，我把 `Cargo.lock` **转成 LF** 再跑门禁 —— **门禁照常绿**。原因：`Path.read_text()` 默认做 universal-newline 翻译（`\r\n`→`\n`），所以 `name = "dyautodm-v2"\nversion = "..."` 的锚点**对 CRLF/LF 都成立**。⇒ **EOL 脆弱性假设被实测推翻，不成立**（我原想报这个 bug，实测后撤销）。文件已按 mtime 前备份**字节级还原**，md5 前后一致、`git diff` 空。
- **我未改 `Cargo.lock` 本身**（父会话提交前保证六处齐平）；改动仅在 `check_version_sync.py`（docstring + 计数文案）。
- **门禁未涵盖 `backend/main.py`** 曾是真盲区，但 F-5 选择「收敛到 `APP_VERSION`」后它归到 `_build_version.py`（本门禁第 4 处）⇒ 无需新增第 7 条检查。

---

## T3-a 台账 M-11「C 类已授权实施」改判

### ① 位置
`工作记忆/00_交接卡待办台账.md` 第 79 行（M-11 行），其中「C 类（patchright/playwright 1.62→1.63 升级）」判据。

### ② 判定
**目标态已在 HEAD 达成（`b455192` 已完成改判），本轮为「补证据 + 补实测」。**
- 位置与职责**已由另一会话在同一行完成**（该行现文：「🔧 **C 类…已改判为「暂缓」**（superseded）：原拍板早于 L3 证据…」）—— 这条判据的修订**发生在 `b455192` 这一个提交里**，且该提交**已经落盘**（`git status` 对该文件干净）。⇒ 第 79 行**不是**「C 类已授权实施」的旧文案了。
- 缺失项：结论**只给了「实测 camoufox 硬钉」四个字**，**没有 L3 证据路径、没有那条决定性的 unsatisfiable 命令** ⇒ 下个会话无法独立复核。

### ③ 修法
在**只改这一行**的前提下补两件事实：① L3 证据路径；② 决定性命令与结果（`uv pip install --dry-run camoufox==0.5.6 playwright==1.63.0` → `No solution found`）。**不改判据本身的方向（仍是暂缓），不改其它行。**

### ④ 修复前 / 后实测输出
**修复前（`b455192` 该行的相关片段）：**
```
🔧 **C 类（patchright/playwright 1.62→1.63 升级）已改判为「暂缓」**（superseded）：原拍板早于 L3 证据
—— 实测 `camoufox 0.5.6` 硬钉 `playwright (<1.63)`，升 1.63 会破坏当前**唯一生效内核**（无 Chromium 回退）⇒ 见 **T3**。
```
**修复后：**
```
🔧 **C 类（patchright/playwright 1.62→1.63 升级）已改判为「暂缓」**（superseded）：原拍板早于 L3 证据
—— 实测 `camoufox 0.5.6` 硬钉 `playwright (<1.63)`（`uv pip install --dry-run camoufox==0.5.6
playwright==1.63.0` → `No solution found` / unsatisfiable），升 1.63 会破坏当前**唯一生效内核**
（无 Chromium 回退）⇒ 见 **T3**。证据：`artifacts/UP_L3_浏览器依赖升级评估_20260923.md`；
上界已在 **T3-b** 落地（`requirements.txt`：`playwright>=1.62,<1.63` + 补 `patchright`/`camoufox` 声明）。
```
**幂等/最小编辑证明（`git diff --stat` + 行数）：**
```
$ git diff --stat -- 工作记忆/00_交接卡待办台账.md
 工作记忆/00_交接卡待办台账.md | 10 ++++++----
 1 file changed, 6 insertions(+), 4 deletions(-)
$ 行数 196 -> 198（仅 +2 行，均为本批 §5.4 的 HC-09 登记行与拆行所致，见 F-8）
$ 文件换行风格：CR count = 0（保持 LF，未破坏原有风格）
```

### ⑤ 诚实标注
- ⚠️ **任务书写「M-11 行现写『C 类…已授权实施』」，实测该描述已过期** —— HEAD 上它已是「已改判为「暂缓」」。我**没有**按过期的任务书去「改判」，而是核对实际文案后**只补证据**。⇒ 该行不是被我改判的；改判归属 `b455192`（另一会话）。
- 我**只动了 M-11 这一行**（外加 §5.4 三处，属 F-8 范围）；其它行零字节改动（逐行比对 diff 确认无相邻行变化、无重排、无全文件覆盖）。
- 判据未被我单方面加强/削弱：**仍是「暂缓」**，只是它现在有可复核的出处与实测。

---

## T3-b `backend/requirements.txt` `playwright` 无上界 ⇒ 干净机解析即撞内核

### ① 位置
`DYAutoDM_v2/backend/requirements.txt:31`（原 `playwright>=1.47.0`）

### ② 判定
**真缺陷，链条三步全部实测坐实：**
1. **本机已装版本**（补声明的依据）：
   ```
   camoufox 0.5.6        requires: [..., 'playwright (<1.63)', ...]
   patchright 1.62.3     requires: ['pyee<14,>=13', 'greenlet<4.0.0,>=3.1.1']
   playwright 1.62.0     requires: ['pyee<14,>=13', 'greenlet<4.0.0,>=3.1.1']
   ```
   ⇒ `camoufox 0.5.6` **硬钉 `playwright (<1.63)`**；而 `patchright` / `camoufox` **在 requirements.txt 中从未声明**（干净机重建环境会缺包）。
2. **干净机解析到 1.63.0**（旧文件）：
   ```
   $ uv pip install --dry-run -r <HEAD:requirements.txt>          # 无上界
   + playwright==1.63.0
   ```
3. **1.63 与 camoufox 不可同时满足**（决定性证据）：
   ```
   $ uv pip install --dry-run camoufox==0.5.6 playwright==1.63.0
   × No solution found when resolving dependencies:
   ╰─▶ Because camoufox>=0.5.6 depends on playwright<1.63 and you require
       camoufox==0.5.6, we can conclude that you require playwright<1.63.
       And because you require playwright==1.63.0, we can conclude that your
       requirements are unsatisfiable.
   C_EXIT=1
   ```
   ⇒ 干净机上「先 `pip install -r requirements.txt` 得 1.63，再装 camoufox（本项目**唯一生效内核**，无 Chromium 回退）」= **装不上**。

### ③ 修法
`playwright>=1.47.0` → **`playwright>=1.62,<1.63`**（下界同时上调到 1.62：与本机实测版本及 camoufox 约束区间一致），并补两个此前缺失的声明：**`patchright>=1.62,<1.63`**、**`camoufox>=0.5.6,<0.6`**（每项附一行 why，含 L3 证据路径）。

### ④ 修复前 / 后实测输出
**修复前（`HEAD:requirements.txt` + camoufox 一起装 —— 即干净机的真实路径）：**
```
$ uv pip install --dry-run -r <HEAD:requirements.txt>
+ playwright==1.63.0                    ← 无上界，解析到 1.63
```
**修复后：**
```
$ uv pip install --dry-run -r backend/requirements.txt
Resolved 100 packages in 40.57s
Would install 100 packages
 + camoufox==0.5.6
 + patchright==1.62.3
 + playwright==1.62.0                   ← 不再选 1.63
A_EXIT=0
```
**独立第二通道（pip，非 uv）复核：**
```
$ python -m pip install --dry-run -r backend/requirements.txt   # 干净 venv，index=tuna
Would install ... camoufox-0.5.6 ... patchright-1.62.3 ... playwright-1.62.0 ...
PIP_EXIT=0
```
**交叉对照（同一次 pip 跑旧文件）：**
```
$ python -m pip install --dry-run -r <HEAD:requirements.txt>
Collecting playwright... playwright-1.63.0      ← 旧文件确实会拉到 1.63
```

### ⑤ 诚实标注
- 🔴 **一处断言我验不了，故只作背景不写成「已实测」**：把旧 requirements 解出的 `playwright 1.63` 与 `camoufox 0.5.6` **分两步安装**时，pip/uv 都会**向后回溯、静默降级**到 `playwright 1.62.0`（实测：两步法 `D_EXIT=0`，输出 `+ camoufox==0.5.6 / + playwright==1.62.0`，**没有报冲突**）。⇒ 准确表述是：**解析器会拒斥「同一次解析里同时要求 1.63 与 camoufox」**（由 C 证）；而「分两步安装」的失败**取决于顺序与是否锁版本**，我没有复现出硬失败。**我不声称「pip install 一定报错」**。
- 反过来说，「旧文件会解析到 1.63.0」这条**是硬实测**（uv 与 pip 双通道一致），这才是 T3-b 要修的真实缺口（构建产物会打进 1.63 的 sidecar 依赖集）。
- 环境限制（已如实处理）：直连 `pypi.org` 在本机对 uv 不稳定（`Request failed after 3 retries`），改用清华镜像 `https://pypi.tuna.tsinghua.edu.cn/simple` 完成全部解析实验；镜像只影响下载通道，不影响解析语义。
- 未做：未真机执行安装（只用 `--dry-run`）；未改任何已装包；未改 `requirements.txt` 之外的依赖声明文件（如 `build_sidecar.py` 内嵌的隐藏依赖列表 —— 若存在，需另立一项审计）。

---

## 改动文件清单

### 本批新改（我写，**未** `git add`）
| # | 文件 | 改动 | 关联项 |
|---|---|---|---|
| 1 | `DYAutoDM_v2/docs/design-contracts/.known-gaps.json` | 新增 2 条逐端点登记；`_version` 1.1.0→1.2.0、`_updated`→2026-09-23 | F-3/P3-9 |
| 2 | `DYAutoDM_v2/backend/main.py` | **仅 F-5 一处**：`_build_version()/APP_VERSION` 上移、`FastAPI(version=APP_VERSION)` 去硬编码、合并重复定义 | F-5 |
| 3 | `DYAutoDM_v2/.gitignore` | 补 `frontend/dist-prototype/`、`frontend/_proto/`、`frontend/vite.prototype.config.ts` | F-6 |
| 4 | `.gitignore`（仓库根） | 补 `/_*.py`（根级一次性脚本）；补 `_ext_repos/DouYin_Spider_git/`（含 F-7 处置说明） | F-6 / F-7 |
| 5 | `DYAutoDM_v2/scripts/check_version_sync.py` | docstring 追加 L-10 补记；总结行计数改 `len(TARGETS)`（**检查逻辑未动**） | L-10 |
| 6 | `DYAutoDM_v2/backend/requirements.txt` | `playwright>=1.62,<1.63`；新增 `patchright>=1.62,<1.63`、`camoufox>=0.5.6,<0.6` | T3-b |
| 7 | `工作记忆/00_交接卡待办台账.md` | M-11 行补 L3 证据路径（T3-a）；§5.4 新增 HC-09 行 + HC-05 消歧指针 + 修 HC-06/07 串行行与 HC-08 多余 `\|\|` | T3-a / F-8 |
| 8 | `artifacts/handoff_archive/交接卡_HC-09_H9剩余_待实施_2026-09-22.md` | 由 `交接卡_HC-05_H9剩余_…` **改名**而来（内容零改动，md5 不变） | F-8 |
| 9 | `artifacts/fix_20260923/D_gates_hygiene.md` | 本报告 | — |

### 索引层（唯一一处，属 F-7 必需）
| 文件 | 操作 | 说明 |
|---|---|---|
| `_ext_repos/DouYin_Spider_git` | `git rm --cached`（**记入暂存**：`D `） | 只移出索引；工作区 108 个文件原样保留；`.gitignore` 已加规则防 `git add -A` 回灌 |

### 声明未触碰（证明范围纪律）
- **版本源文件 6 处全部零改动**：`git diff --stat` 对 `package.json` / `frontend/package.json` / `src-tauri/tauri.conf.json` / `src-tauri/Cargo.toml` / `src-tauri/Cargo.lock` / `backend/_build_version.py` **空**。
- `DYAutoDM_v2/scripts/check_contracts.py`：**零字节改动**（未为变绿而削检查）。
- F-9 三处（两张归档卡删除 + `verify_live_strategy_live.py` 修改）：**未 `git add`**，仅登记。
- 临时实验文件（`_l10_probe.py` / `_mut_cargo_lock.py` / 探针目录 / 临时 venv / `$LOCALAPPDATA/Temp` 沙箱仓库）**全部已删除**，无残留。

---

## 遗留 / 交父会话

1. **提交职责**：以上 1–8 项 + F-9 三处一起提交；`Cargo.lock` 等六处版本源齐平由父会话统一升版后跑 `python scripts/check_version_sync.py <新版本>` 断言通过再写提交标题（**修 F-4 的机械手段**，见 F-4 §⑤）。
2. **`.known-gaps.json` 的 7 条缺口仍是 open**：`client_user.py:/aweme/v1/web/aweme/post/`、`client_video.py:/aweme/v1/web/aweme/detail/`（本批新增登记）+ 原有 5 条；**登记 ≠ 修复**，修复需补 `signed_url()` 并做「带/不带签名」对照实测后再从基线删除。
3. **未验证/未复现的断言**（已在各节标注）：F-7 的「clone 为空」为推导未实机验证；T3-b 的「分两步安装硬失败」未能复现（已降级表述）。
4. **本批新发现的独立问题（超出本批范围，供父会话决定是否开项）**：
   - 台账 `#5.4` 原有的**表格结构损坏**（HC-06/HC-07 行里塞了**字面 `\n` 与游离 `\r`**、HC-06/08 行首多一个 `|`、HC-06 行还**重复挂了一份 HC-05 卡片路径**）—— 我在 F-8 范围内顺手修正了这 3 行；**同类损坏可能存在于本文件其它表**（我只改了 §5.4 与 M-11），建议另立一项做**全文件表格结构校验**。
   - `verify_live_strategy_live.py` 在本轮被**另一执行体**改动（` D` → ` M`，mtime 21:10）⇒ 存在并发写者，父会话提交前需确认其归属。
