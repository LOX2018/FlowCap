# A-4 施工报告：静默兜底门禁扫描口径显式化

- **日期**：2026-09-28
- **被改文件**：`DYAutoDM_v2/scripts/check_silent_fallback.py`（675 → 约 790 行）
- **被改基线**：`artifacts/audit_2026-09-27/silent_fallback_baseline.json`（**只增字段**）
- **备份**：`artifacts/audit_2026-09-27/silent_fallback_baseline.json.bak.20260928_091822`
- **Python**：`C:/Users/LOX/AppData/Local/Programs/Python/Python314/python.exe`（Python 3.14.6）
- **运行目录**：`DYAutoDM_v2`（脚本用 `REPO_ROOT` 推导路径，不依赖 cwd）

---

## 1. 改动点

### 1.1 `scan()` —— 新增 `include_tests` 开关，返回口径 scope

原实现（约 408 行）无条件 `continue` 掉 `test_*` / `verify_*`，且不记录排除了什么：

```python
def scan() -> list[dict]:
    """扫描 backend/ 全部 .py（排除 test_*.py / verify_*.py / 跳过目录）。"""
    ...
            if fn.startswith("test_") or fn.startswith("verify_"):
                continue
    return hits
```

改为（**默认行为逐字节等价**，仅新增显式开关与计数）：

```python
def _is_test_basename(fn): return fn.startswith("test_") or fn.startswith("verify_")

def scan(include_tests: bool = False) -> tuple[list[dict], dict]:
    # 默认 include_tests=False → 仍排除 test_*/verify_*（与历史 386 同口径）
    # include_tests=True（--include-tests）→ 纳入
    # 返回 (hits, scope)，scope = {include_tests, excluded_test_files,
    #                              included_test_files, scanned_py_files}
```

- `include_tests=False` 时走 `is_test and not include_tests → continue` 分支，与旧逻辑**完全一致**（只是多做了计数）。
- 新增辅助函数 `_rel_is_test()` 供「纳入 test 口径」的同口径比对使用。

### 1.2 `main()` —— 新增 `--include-tests`，并把口径**打印进输出**

- 新增参数：`--include-tests`（`store_true`，默认 False）。
- 非 JSON 模式先打印一行 `[口径] …`；门禁头部再打印一行 `本次口径: …`；另有 `[对照]` 行给出「若纳入 test/verify」的数字。
- JSON 模式新增 `scope` 与 `scope_control_include_tests` 两个顶层键（原 `summary` / `hits` 保留）。

### 1.3 `run_gates()` —— 口径显式行 + 对照组

`run_gates(..., scope, hits_include_tests)` 新增两处打印：

```
  本次口径: 含 test_*/verify_* = 否（--include-tests 未开启）；排除 test_/verify_ 文件 = 97 个；纳入 = 0 个；实际扫描 = 196 个 .py
  [对照] 若纳入 test_*/verify_*（--include-tests 口径）: 命中 673 处 (历史基线 386，Δ +287)；同口径子集 = 490 处
```

即使默认运行，也会打印「排除了多少个 test/verify 文件」——**豁免成为显式决策，不再默认静默**。

### 1.4 `save_baseline()` —— `merge` 模式只增口径字段

- 签名新增 `scope`，落盘时**追加**新增字段（`include_tests_summary` 等，见 §3）。
- 新增 `merge=True` 分支（由 `--baseline add-scope` 触发）：读入现有 JSON → `data.update(new_scope_fields)` → 写回，**既有字段一律不动**（保可比性）。

---

## 2. 验收 ② 默认口径：改动前后**完全一致**

命令（改动前后各跑一次）：

```bash
cd DYAutoDM_v2 && <python314> scripts/check_silent_fallback.py
```

| 指标 | 改动前 | 改动后 | 一致 |
|---|---|---|---|
| `summary.total` | 627 | **627** | ✅ |
| `summary.comparable_to_386` | 451 | **451** | ✅ |
| `summary.delta` | +241 | +241 | ✅ |
| `L1_写库` | 69 | 69 | ✅ |
| `L1_外发` | 33 | 33 | ✅ |
| `L2_外部资源` | 61 | 61 | ✅ |
| `L3_内部逻辑` | 464 | 464 | ✅ |
| `L1_写库_pass_only` | 34 | 34 | ✅ |
| `L1_外发_pass_only` | 9 | 9 | ✅ |
| `needs_review` | 42 | 42 | ✅ |
| F1（写库 pass-only） | 29 处 | 29 处 | ✅ |
| F2（外发 pass-only） | 9 处 | 9 处 | ✅ |
| F3 L1 新增 | 0 | 0 | ✅ |
| F4 / F5 | PASS | PASS | ✅ |
| **退出码** | **1** | **1** | ✅ |

> ⚠ **关于验收 ②「默认口径仍 exit 0」的诚实说明**：默认运行**退出码为 1，且改动前后都是 1**——该退出码来自**既有的** F1（L1-写库 pass-only 29 处）/ F2（L1-外发 pass-only 9 处）阻断，与本任务无关，非本次改动引入。本次改动**未改变默认口径的任何数字与退出码**，故「与改动前完全一致」成立；但「exit 0」这一条**未满足**，因为它本就未满足（属既有遗留阻断）。相关数字见上表。

---

## 3. 验收 ① `--include-tests` 真实数字

命令：

```bash
cd DYAutoDM_v2 && <python314> scripts/check_silent_fallback.py --include-tests
```

**真实输出（口径原文 + 基数）**：

```
[口径] 含 test_*/verify_* = 是（--include-tests 开启）；排除 test_/verify_ 文件 0 个，纳入 97 个，实际扫描 293 个 .py
==========================================================================
静默兜底机械门禁 —— 「失败必须可见」不靠自觉
==========================================================================
  扫描根 : C:\Users\LOX\Desktop\DYchajian\DYAutoDM_v2\backend
  排除   : [...SKIP_DIRS...] + test_*.py + verify_*.py
  本次口径: 含 test_*/verify_* = 是（--include-tests 开启）；排除 test_/verify_ 文件 = 0 个；纳入 = 97 个；实际扫描 = 293 个 .py
  基数   : 命中 673 处 (历史基线 386，Δ +287)
           其中与 H6 386 同口径子集 = 490 处 (Δ +104)
  [对照] 若纳入 test_*/verify_*（--include-tests 口径）: 命中 673 处 (历史基线 386，Δ +287)；同口径子集 = 490 处
--------------------------------------------------------------------------
  [FAIL] F1   L1-写库路径 pass-only 静默兜底 = 34 处（另 5 处需人工复核，不阻断）
  [FAIL] F2   L1-外发路径 pass-only 静默兜底 = 9 处
  [FAIL] F3   L1 新增 = 5 处（较基线 102 处）：backend/test_capability_probe.py:73:L1-写库:pass-only
  [PASS] F4   分级口径: L1-写库 74 / L1-外发 33 / L2 88 / L3 478；其中 pass-only: 写库 39 / 外发 9；bare: 写库 0 / 外发 0
  [PASS] F5   待人工复核 = 50 处
--------------------------------------------------------------------------
  合计: 5 项，通过 2，未通过 3（阻断 3）
```

**纳入 test/verify 后的基数（真实数字）**：

| 指标 | 默认（排除） | `--include-tests`（纳入） |
|---|---|---|
| `total` 命中 | 627 | **673**（+46） |
| `comparable_to_386` | 451 | **490**（+39） |
| `delta`（vs 386） | +241 | +287 |
| L1-写库 / L1-外发 | 69 / 33 | 74 / 33 |
| L2 / L3 | 61 / 464 | 88 / 478 |
| `pass-only` 写库/外发 | 34 / 9 | 39 / 9 |
| 纳入 test/verify 文件数 | 0 | 97 |
| 实际扫描 .py | 196 | 293 |
| 退出码 | 1 | 1 |

**真实输出（默认口径的对照行）**：

```
  本次口径: 含 test_*/verify_* = 否（--include-tests 未开启）；排除 test_/verify_ 文件 = 97 个；纳入 = 0 个；实际扫描 = 196 个 .py
  [对照] 若纳入 test_*/verify_*（--include-tests 口径）: 命中 673 处 (历史基线 386，Δ +287)；同口径子集 = 490 处
```

### 3.1 口径数字的「抖动」诚实说明

`included_test_files` / `scanned_py_files` 在本次施工期间**持续变化**（实测 95 → 96 → 97），**原因已定位**：另一并发会话在 `backend/` 下**新建 test 文件**（`backend/test_features_wiring.py` 于 2026-09-28 09:20 落盘；`find -mmin` 佐证）。这是**仓库状态在被并行改动**，**不是**本脚本口径错误。

- 命中基数 `total`（627 / 673）在多次重跑中**稳定**（因新文件本身暂无 `try` 静默兜底命中）。
- 「文件个数」类字段随仓库存量变动，属预期；`--include-tests` 的**含义与开关行为**不受影响。

---

## 4. 基线 JSON：只增字段（保可比性）

先备份 → 再以 `--baseline add-scope`（**只增口径字段**）写回：

```bash
cp artifacts/audit_2026-09-27/silent_fallback_baseline.json \
   artifacts/audit_2026-09-27/silent_fallback_baseline.json.bak.20260928_091822
cd DYAutoDM_v2 && <python314> scripts/check_silent_fallback.py --include-tests --baseline add-scope
```

**程序化校验结果**（对比 backup）：

- 原有字段 `生成时间口径` / `历史基线_H6_386` / `summary` / `L1_keys` / `all_keys`：**全部逐字节 IDENTICAL**。
- 被删除字段：`set()`（**无删除**）。
- 新增的顶层字段（8 个）：
  - `include_tests_summary` = `{"include_tests": true, "excluded_test_files": 0, "included_test_files": 97, "scanned_py_files": 293, "total_hits": 673, "comparable_to_386": 490}`
  - `include_tests_total` = 673
  - `include_tests_comparable_to_386` = 490
  - `include_tests_hits_total` = 673
  - `L1_keys_with_tests`（纳入 test 口径下的 L1 键集）
  - `all_keys_with_tests`（纳入 test 口径下的全量键集）
  - `test_verify_hit_count` = 46（来自 test/verify 文件的命中数）
  - `current_scope` = `{"include_tests": true, "excluded_test_files": 0, "included_test_files": 97, "scanned_py_files": 293}`
- 原有 `summary.total` = **627**、`summary.comparable_to_386` = **451** 保持不动（默认口径历史数字可比）。

---

## 5. 回归自检

```bash
cd DYAutoDM_v2 && <python314> scripts/check_silent_fallback.py --selftest   # exit 0
```

```
  负控1 写库 pass-only 应命中 L1-写库: ✓
  负控2 外发 pass-only 应命中 L1-外发: ✓
  正控  干净代码应零命中: ✓
✓ 自检通过：分级判据随代码内容变化，非写死
```

---

## 6. 证据文件（同目录 `artifacts/fix_a_20260928/`）

| 文件 | 内容 |
|---|---|
| `out_default.txt` | 默认口径完整 stdout |
| `out_include_tests.txt` | `--include-tests` 完整 stdout |
| `out_default.json` | `--json` 默认口径（含 scope / control） |
| `exit_default.txt` | `DEFAULT_EXIT=1` |
| `exit_include.txt` | `INCLUDE_EXIT=1` |

---

## 7. 结论

- ✅ 新增 `--include-tests`（默认关闭）；**默认行为与数字逐项不变**（total 627 / comparable 451，退出码一致）。
- ✅ 口径（是否含 test/verify、排除/纳入文件数、实际扫描数）+ 对照组**每次都打印进输出**，豁免从「默认静默」变为「显式决策」。
- ✅ 基线 JSON **只增 8 个字段**，原有 5 个字段逐字节不变、无删除；备份先行。
- ⚠ 验收 ②「默认 exit 0」**未满足**：默认运行退出码为 1，但这是**既有** F1/F2 阻断（改动前后都是 1），非本次引入，已如实列数字。
- ⚠ test/verify 文件计数含并发抖动（95→97），已定位为并发会话新建测试文件所致，命中基数稳定。

（本报告所有数字均来自上述真实运行；无一项为估算或推断。）
