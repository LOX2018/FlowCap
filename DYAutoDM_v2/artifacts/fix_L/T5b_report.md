# T5-b 卷宗：两世界可见性探针纳入环境基线/门禁

> 任务来源：台账 **M-11** · **T5-b**（`工作记忆/00_交接卡待办台账.md:101`）
> 背景出处：`artifacts/UP_L3_浏览器依赖升级评估_20260923.md` §四（含 §4.3-bis / §4.4）
> 分支：`design/better-douyin` · 工作区 D:\驱动仓 `C:/Users/LOX/Desktop/DYchajian/DYAutoDM_v2`
> 日期：2026-09-24

---

## 1) 自述改动清单

| 文件绝对路径 | 增/删 | 唯一标识符 |
|---|---|---|
| `C:/Users/LOX/Desktop/DYchajian/DYAutoDM_v2/backend/services/env_baseline.py` | +238 行（`compare_baseline` 之后追加 T5-b 段） | `def probe_two_world_visibility(page, *, applicable: bool = True) -> dict` |
| 同上 | 探针 JS 常量 | `VISIBILITY_PROBE_JS` / `VISIBILITY_KEYS` |
| 同上 | 纯判据 + 三态 | `def classify_two_world_visibility(...)`、`VIS_STATUS_PASS/FAIL/UNKNOWN` |
| 同上 | 基线读写（分键，不污染出口 IP 基线） | `_VIS_KEY_PREFIX = "env_baseline_vis_"`、`record_visibility_baseline` / `latest_visibility` |
| 同上 | 门禁入口 | `def visibility_gate(account="", page=None, *, applicable=True)` |
| `C:/Users/LOX/Desktop/DYchajian/DYAutoDM_v2/backend/test_two_world_visibility_gate.py` | **新增**（266 行 / 18 测试） | `TwoWorldPageStub`（两世界离线替身） |

**未改动**：`daemon/browser_daemon.py`、`daemon/bcc_capture.py`、`daemon/` 下任何文件、
`services/env_audit.py`、任何版本号文件、任何夹具、`requirements.txt`。
**未做任何 git 写操作**（仅 `git status` 只读）。

## 2) 自述验证数字

- 独立回归模块（单模块运行，**非**全量 discover）：
  `python -m unittest -v test_two_world_visibility_gate` → **Ran 18 tests … OK**（EXIT=0）。
  - 正控 1：两世界都可见 → PASS（`test_positive_control_both_worlds_visible_pass`）
  - **负控 3**：主世界有/默认世界无 → FAIL；两世界皆无 → FAIL；默认有/主世界无 → FAIL
  - 回退 1：原生 playwright（无 `isolated_context`）→ 单世界回退，键在即 PASS
  - 三态 5：无页面 / 无 evaluate / not_applicable / 读取异常 / 返回结构不符 → **全 unknown，且 `ok=False`**
  - 基线 4：pass 落、fail 落、**unknown 不落且不落键**、无基线读数 → unknown
  - 接线 1：探针键覆盖 hook 脚本写的全部 `window.__CAP_*__`（防空转门禁）
- `python -m py_compile services/env_baseline.py test_two_world_visibility_gate.py` → PYCOMPILE_OK
- 既有语义未破坏：`python -m unittest test_replay_gates` → 26 项中 25 通过；
  唯一 `ERROR` 是既有用例 `test_clear_init_scripts_really_absent` 因本机**未装 patchright**
  （`ModuleNotFoundError: No module named 'patchright'`）——非本次改动引入，纯环境缺依赖。
- 编译期检查：`env_baseline.py` 现有 10 个公开符号为原样 + 新增符号，原 4 个函数体未触碰。

## 3) 唯一标识符（供父会话 grep 复核）

```bash
cd C:/Users/LOX/Desktop/DYchajian/DYAutoDM_v2/backend
grep -n 'def probe_two_world_visibility' services/env_baseline.py   # env_baseline.py:309
grep -n 'def visibility_gate' services/env_baseline.py             # env_baseline.py:377
grep -n 'VISIBILITY_PROBE_JS = ' services/env_baseline.py          # env_baseline.py:205
grep -n '_VIS_KEY_PREFIX = ' services/env_baseline.py              # env_baseline.py:221
```
1. `def probe_two_world_visibility(page, *, applicable: bool = True) -> dict` — `services/env_baseline.py:309`
2. `def visibility_gate(account: str = "", page=None, *, applicable: bool = True) -> dict` — `:377`
3. `VISIBILITY_PROBE_JS` — `:205`；`_VIS_KEY_PREFIX = "env_baseline_vis_"` — `:221`（与出口 IP 基线 `env_baseline_` 分键）
4. 测试类 `TwoWorldPageStub` — `backend/test_two_world_visibility_gate.py:40`

## 4) 诚实标注

- **真机未验证（关键）**：本环境（Windows + Python 3.11，**未装 patchright / 无浏览器**）无法跑
  真实 patchright 页面。本次全部证据来自**离线替身**（`TwoWorldPageStub` 桩对象模拟两世界）。
  **已证明**：判据三态正确（正控 PASS / 三类负控 FAIL / 缺证据 unknown）、探针键覆盖真实 hook 键、
  unknown 不落盘。**未证明**：真实 patchright 页面下探针读数的具体表现——**不得**据此声称
  「生产链路已修复」或「跨世界不可见已复现/已消除」。
- **探针未接入常驻采集**：`visibility_gate()` 是**可调用入口**，但**未**接线到 `bcc_audit` /
  `env_audit_snapshot` / keepalive（`daemon/` 与 `env_audit.py` 均不在本任务文件所有权内，
  属 T5-b 前序 `fix_20260923` 已登记的同类缺口）。⇒ 当前是「门禁就绪、待宿主接线」，
  **不是**「已在生产自动拦截」。
- **红线遵守**：未跑真实浏览器、未跑全量 discover、未改版本号、未做 git 写操作、未改任何非独占文件。
- **环境依赖**：`test_replay_gates` 的 1 个既有 ERROR 源于本机无 patchright，非本次引入。

## 5) 交叉判据（grep 命中数）

```bash
grep -c 'def probe_two_world_visibility' services/env_baseline.py   # → 1
grep -c 'env_baseline_vis_' services/env_baseline.py                # → 1（前缀只定义一次）
grep -n 'BCC-070' services/env_baseline.py                          # → 3 处泄漏项（未生效/跨世界/反向不一致）
python -m unittest test_two_world_visibility_gate                   # → Ran 18 tests … OK
```
- `env_baseline.py` 原 4 个函数（`get_baseline` / `record_baseline` / `clear_baseline` /
  `compare_baseline`）签名与实现**逐字未动**；新增段全部在其后。
- 新增 kv 前缀 `env_baseline_vis_` 与既有 `env_baseline_` **不互为前缀碰撞**（后者+账号名
  不等于前者），两基线互不覆盖。

## 6) 疑点

- **对其它待办的影响**：
  - **T5-a**（`browser_daemon.py` 自持 `_init_script_disposables`）：无交叉，本次未碰该文件；
    但其 `test_clear_init_scripts_really_absent` 依赖本机 patchright，**在本环境会 ERROR**，
    与本次改动无关，属环境缺依赖，父会话在装 patchright 的机器上应能通过。
  - **`env_audit.py` 的 `compare_two_world_visibility`（前序 `fix_20260923` T5-b）与本次新增
    存在语义重叠**：前者是「传两世界数据 → 判 leaks」的纯判据（仅 ok/not-ok 两态）；
    本次补的是**页面无关探针 + 三态（unknown 绝不通过）+ 基线落盘**。两者**不冲突**，
    建议后续由宿主方把 `env_audit` 的调用点统一收敛到 `env_baseline.visibility_gate()`
    （避免同一事实两处实现——M-10 单一来源化的同类债）。
- **相邻问题**：根因修复（读取侧改走主世界 `isolated_context=False`）由 L3 报告 §4.4「方案 A」
  给出，**不在本任务范围**（本任务只做「探针/门禁」，不改读取侧）。⇒ 门禁已可拦复发，
  但**若读取侧不改，复发时门禁会稳定报 FAIL（BCC-070）**——这正是期望行为（暴露而非静默）。
- **待接线**：`visibility_gate(account, page)` 需要宿主在拿到 `self._page` 的处调用
  （建议 `_launch`/`_ensure_alive` 后、或 keepalive 低频轮），并据 `status` 三分支处理；
  接线前本门禁**不会被自动执行**。
- **离线替身的边界**：`TwoWorldPageStub` 以「evaluate 是否收到 `isolated_context=False`」区分两世界，
  是**判据级**模拟，不模拟真实 patchright 的 Route 注入时序；故它证明「门禁逻辑对」，
  不替代真机复验。
