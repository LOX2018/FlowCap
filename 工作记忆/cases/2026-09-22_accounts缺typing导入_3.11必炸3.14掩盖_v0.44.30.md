# 案例 · accounts.py 缺 typing 导入 —— 3.11 必炸、3.14 被 PEP 649 掩盖（v0.44.30）

> 日期：2026-09-22　分支：`design/better-douyin`　提交：`20c0909`（v0.44.30）
> 性质：**真实缺陷 → 定位 → 修复 → 双解释器实机验证**（非推断）
> 关联：本案例由「未提交交接卡核验」过程发现（见 `artifacts/核验报告_交接卡未提交项_2026-09-22.md` §四）

---

## 〇、错误报告（§III 强制 schema）

```json
{
  "design_intent": {
    "module": "backend/auto_dm/accounts.py（多账号管理层）",
    "design_contract": "模块级注解所用名称必须在模块作用域可解析；本项目其余模块均显式 `from typing import ...`（pre-condition：导入即可用）",
    "expected_behavior": "任意受支持解释器（3.11 上游基准 / 3.14 打包与测试解释器）下 import 与 get_type_hints 均成功",
    "assumptions": ["accounts.py 顶部集中声明全部依赖"]
  },
  "observed_deviation": {
    "deviation_type": "data",
    "deviation_point": "模块级变量注解 `_im_write_cache`(:1044) / `_live_identity_cache`(:1147)",
    "deviation_from_expectation": "3.11：import 即 NameError（模块根本不可用）；3.14：import 侥幸成功但 get_type_hints 失败"
  },
  "error_code": "（未分配 —— 本次未改 errcode_data.py，故不冒领错误码）",
  "severity": "fatal",
  "suggested_actions": [
    {"action_id": "add-typing-import", "automatic": false, "idempotent": true}
  ]
}
```

---

## 一、设计意图与现状态（Step 1-2）

| 项 | 内容 |
|---|---|
| 模块 | `backend/auto_dm/accounts.py`（1588 行，CRLF） |
| 设计契约 | 模块级注解的**名必须可解析**——Python 语义硬约束；本项目惯例为文件顶部显式导入 |
| 观测偏差 | 终端默认解释器（3.11.16）`import auto_dm.accounts` → `NameError: name 'Dict' is not defined` |
| 连带影响 | `import api.platform` 随之失败（内容页工作线曾据此卡住回归） |

---

## 二、执行链追溯（Step 3）

```
用户/测试触达点:  import auto_dm.accounts
    → 模块体执行到第 1044 行
      _im_write_cache: Dict[str, Tuple[float, bool, str]] = {}
    → 注解求值需要名 'Dict'
      → 模块作用域查 'Dict' → 无
      → 内置作用域查 'Dict' → 无
      ⇒ NameError
```

**上游定位**：文件顶部导入区（第 19-26 行）只有 `os/json/time/asyncio/platform/subprocess/threading/loguru`
—— **全文件零 `typing` 导入**，却使用 `Dict`(×2) / `Optional`(×6) / `Tuple`(×6)。
`git log -S"from typing import"` 证明该文件**历史上从未有过** typing 导入。

---

## 三、根因分析（Step 4）

**不在「哪一行报错」，而在「为什么 3.14 下全绿」：**

| 解释器 | 行为 | 结论 |
|---|---|---|
| Python **3.11** | 注解在**定义时求值** → import 即 `NameError` | 缺陷**显性**，模块不可用 |
| Python **3.14** | **PEP 649 延迟求值注解**：注解默认不求值，仅存入 `__annotate__` | 缺陷**被掩盖**，import 成功 → **测试全绿 = 假绿** |

- 归因到「问题为什么被推迟发现」：本项目的单测/回归**只在 3.14 下跑**（`PY314`），
  PEP 649 把错误从 import 时推迟到「显式解析注解」时，而既有测试**从不调用 `get_type_hints()`**
  ⇒ 假绿持续存在。
- **引入点（提交级）**：
  - `6ee1f82`（v0.44.20）新增 `_im_write_cache: Dict[...]`
  - `4a330f7`（v0.44.23）新增 `_live_identity_cache: Dict[...]`
  - 两次都只加注解、未补导入；且 v0.44.20 的改动**已提交**→ 属「提交即缺陷」。

**为什么此前未被发现（诚实记录）**：内容页工作线曾把 `accounts.py` 的 `Dict NameError`
记为「**另一条工作线的中间态**」并选择等待——**归因错误**：它不是中间态，而是已提交的存量缺陷，
被 3.14 的延迟注解掩盖。这正是「静态/单解释器验证」的盲区。

---

## 四、修复（最小变更）

| 项 | 内容 |
|---|---|
| 文件 | `backend/auto_dm/accounts.py` |
| 改动 | **+1 行**：`from typing import Dict, Optional, Tuple`（插在第 26 行 `import threading` 之后） |
| 手法 | **字节级**（该文件纯 CRLF：CRLF 1588 / LFonly 0）；不用 patch 以防行尾污染 |
| EOL 自证 | `git diff --numstat` == `git diff --ignore-all-space --numstat`（均为 `1 0`） |

---

## 五、实机验证（Step 5，Live Verification）

| 判据 | 修复前 | 修复后 |
|---|---|---|
| 3.11 `import auto_dm.accounts` | ❌ NameError | ✅ OK |
| 3.11 `typing.get_type_hints(probe_im_write)` | ❌ | ✅ OK |
| 3.14 `typing.get_type_hints(probe_im_write)` | ❌ NameError('Optional') | ✅ OK |
| `py_compile` | — | ✅ OK |
| 全量回归 `unittest discover` | 508 项 / 1 存量 Fernet 错误 | **508 项 / 1 存量 Fernet 错误**（逐项一致，**零新增回归**） |
| 版本齐平 | 五处 0.44.29 | 五处 **0.44.30**（`check_version_sync.py` PASS，含 Cargo.lock 手工读回） |

**本次未做（诚实标注）**：未重打包 sidecar、未部署、未实机启动应用
⇒ 故本次**只做 release patch（+0.01）**，未打 `-debug.N` 预发布标签。

---

## 六、教训（可复用判据）

1. **单解释器验证会制造假绿**：本项目的「基线解释器」（3.14）恰好掩盖了注解类缺陷。
   判据：涉及注解/类型语义的改动，**至少一次在 3.11 下 import 该模块**。
2. **`python -m py_compile` 不够**：py_compile **不求值注解**，本缺陷在 py_compile 下完全静默。
3. **PEP 649（3.14）改变了「导入即报错」的时机** —— 跨版本代码必须意识到
   「注解错误可能被推迟到 `get_type_hints()` 才炸」。
4. **别把「别人的中间态」当借口**：一处报错要么是**已提交缺陷**，要么是**未提交在制品**；
   判据 = `git diff --quiet <file>`（有未提交改动才是在制品）。本次即为**已提交缺陷**被误判为中间态。

---

## 七、产物

| 产物 | 路径 |
|---|---|
| 核验报告 | `artifacts/核验报告_交接卡未提交项_2026-09-22.md` |
| 代码提交 | `20c0909`（v0.44.30） |
| 本案例 | `工作记忆/cases/2026-09-22_accounts缺typing导入_3.11必炸3.14掩盖_v0.44.30.md` |
