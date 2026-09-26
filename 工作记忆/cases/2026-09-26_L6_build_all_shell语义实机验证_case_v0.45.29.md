# 2026-09-26｜L-6 闭环：`build_all.py::_run_shell` 的 shell 语义实机验证（v0.45.29）

> 关联：**L-6**（台账原记「`_run_shell`（Win 需 `shell=True`）**已修未验证**」）· 缺陷编号 **BLD-006**
> 案例性质：**「已修未验证」状态收口** —— 把猜测性结论替换为实机取证 + 机械门禁
> 严重级：🟡 中（不修则 Windows 上 `npx`/`npm` 无法解析 ⇒ **打包链直接断**，但不会静默出错）

---

## 1. 设计意图（Step 1）

| 项 | 内容 |
|---|---|
| **模块** | `scripts/build_all.py::_run_shell` |
| **设计契约** | 构建脚本要跑 `npm` / `npx` / `cargo` 等命令；**Windows 上 `npm`/`npx` 是 `.cmd` 批处理**，不是可执行文件 |
| **预期行为** | ① 经 shell 解析，`.cmd` 可执行；② 退出码**如实回传**（构建失败不得被吞成 0）；③ 带 `log_path` 时日志落盘并可读尾部 |
| **设计假设** | `subprocess.run(..., shell=True)` 在 Windows 上是解析 `.cmd` 的**必要条件** |

---

## 2. 当前状态偏差（Step 2）

| 项 | 内容 |
|---|---|
| **偏差类型** | **resource（进程创建语义）** |
| **偏差点** | 台账 L-6 原文：「`_run_shell`（Win 需 `shell=True`）**已修未验证**」 |
| **偏差表现** | 代码**看起来**已修（有 `shell=True`），但**无任何实机证据**、**无机械门禁** —— 属「声明式结论」，正是本项目反复出现的失效模式（有铁律无执行机制） |

---

## 3. 执行链追踪（Step 3）

```
[声明] 台账 L-6：已修未验证
   ↓
[代码] scripts/build_all.py:78 _run_shell(...) → subprocess.run(..., shell=True)
   ↓
[后果链] 若 shell 语义被改回 False（或将来重构）：
         npx.cmd / npm.cmd 无法被 CreateProcess 直接解析 → FileNotFoundError
         → 前端构建失败 → 打包链断
   ↓
[缺口] ① 无实机读数 ② 无门禁 ⇒ 回归不可见
```

---

## 4. 根因分析（Step 4）

**为什么会出现「已修未验证」？** 因为**修复动作**与**验证动作**被分开，而验证需要「真的跑一次构建」——
构建耗时（约 5 分钟）且会产出 277MB 构建物，于是被推迟。**推迟的验证等于没有验证**（本项目铁律：
静默失效最贵的债）。

**本条的解法不是「跑一次完整构建」**（成本高、且与 L-6 要证的事无关），而是：
**把「shell 语义」抽成可离线、秒级、可复现的判据** —— 用真实 `.cmd` 文件验证解析能力与退出码透传。

---

## 5. 实机验证（Step 5）

**环境**：`DY_APP_ROOT=C:\temp\dyautodm_design` · Python 3.11 · Windows

| # | 场景 | 命令 | 实测 |
|---|---|---|---|
| 1 | 普通内建命令 | `echo L6_OK` | `rc = 0` ✅ |
| 2 | 带 `log_path` | `echo L6_LOGGED` → `a.log` | `rc = 0`，日志存在，内容 `'L6_LOGGED'` ✅ |
| 3 | **非零退出码透传** | `exit 7` | `rc = 7`（**未被吞**）✅ |
| 4 | **真实 `.cmd` 解析** | `t.cmd`（`exit /b 3`） | `rc = 3`，输出 `CMD_RESOLVED` ✅ |

**第 4 项是关键判据**：它在**离线、秒级、无副作用**的前提下，复现了「Windows 上 `npx` 必须经 shell 才能解析」的同一根因。

---

## 6. 门禁与验收（Step 6）

### 6.1 新增门禁 `backend/test_l6_build_shell_semantics.py`（5 项）

| 断言 | 内容 |
|---|---|
| R1 | **AST 判定**：`_run_shell` 内 `subprocess.run` 的 `shell` 参数恒为 `True`（防退回） |
| R2 | **实机**：真实 `.cmd` 可解析 + 退出码如实回传（`rc==3`） |
| R3 | **实机**：非零退出码不得被吞（`rc==7`） |
| R3b | **负控**：普通命令仍返回 0（证明 R3 非恒真） |
| R4 | **实机**：带 `log_path` 日志真实落盘 |

### 6.2 破坏性验证（证明门禁是真的）

把 `scripts/build_all.py` 里的 `shell=True` 改回 `shell=False`（注入 3 处）
→ **R1 精确报出** `subprocess.run 的 shell 参数须恒为 True，实测=[False, False]` 并 FAILED；
恢复后 **5/5 OK**。

### 6.3 全量回归

**935 tests**，failures=5 / errors=7 = **12 项，与干净 HEAD 基线逐条 diff 完全一致 ⇒ 零新增回归**。

---

## 7. 教训

1. ✅ **「已修未验证」是可收口的状态，不该长期挂着**：把「验证」从「跑完整构建」解耦为**对语义的离线取证**，成本从分钟级降到秒级 ⇒ 没有理由不验。
2. ✅ **Windows 上 `npm`/`npx` = `.cmd`，必须 `shell=True`**：`shell=False` 会 `FileNotFoundError`。判据可用「真实 `.cmd` 文件」离线复现，不必跑真构建。
3. ✅ **退出码透传必须单独验**：仅验「能跑」不验「失败能报」，会把构建失败误报为成功（假阳性的一种）。
4. 🔴 **门禁要能抓住「退回去」**：R1 用 AST 断言 `shell=True`，破坏性验证证明它能拦 —— 否则文档写「必须 shell=True」而代码哪天被改成 False 也无人知晓。

---

## 8. 归档元数据

| 项 | 值 |
|---|---|
| 版本 | v0.45.28 → **v0.45.29**（debug patch +0.01，六处齐平） |
| 缺陷编号 | **BLD-006**（`_run_shell` shell 语义未验证） |
| 涉及文件 | `scripts/build_all.py`（只读验证，**未改动**） |
| 新增文件 | `backend/test_l6_build_shell_semantics.py`（5 项含负控） |
| 关联台账 | L-6（本批闭环）· M-10（同批订正 doc-rot） |
