# T1 报告 —— 铁律门禁 R9：审计红线计数挂进自动化

- 仓库根：`C:\Users\LOX\Desktop\DYchajian` ；项目根：`C:\Users\LOX\Desktop\DYchajian\DYAutoDM_v2`
- 分支：`design/better-douyin`（未切换、未 add、未 commit）
- 解释器：`C:/Users/LOX/AppData/Local/Programs/Python/Python314/python.exe`
- 日期：2026-09-27

---

## ① 改动文件

| 路径 | 改动 |
|---|---|
| `C:\Users\LOX\Desktop\DYchajian\DYAutoDM_v2\scripts\check_iron_rules.py` | 新增 `r9_audit_redline()` + `_load_redline_module()`；RULES 加入 R9；WARN_ONLY 加入 `"R9"`；selftest 加 R9 负控 + 正控；顶部 docstring 覆盖面表加 R9 行及取舍说明 |

**唯一改动文件**。已核对：`git diff --name-only` 只列出 `DYAutoDM_v2/scripts/check_iron_rules.py`（+120 / -4）。
`audit_redline_count.py` **未触碰**（判据与 THRESHOLD=20 / DEFAULT_SINCE=abd1f29 均保持 SSOT 原样），数据根 `C:\temp\dyautodm_design` 未触碰，未杀任何进程，未 git add / commit。

挂载点链（已存在的 hook，未改）：`.git/hooks/pre-commit → check_iron_rules.py → RULES → r9_audit_redline() → audit_redline_count.count()`。

---

## ② R9 实现要点与 WARN_ONLY 取舍理由

**实现要点**
1. **委托而非重写**：`r9_audit_redline()` 用 `importlib` 动态加载 `audit_redline_count.py`（照抄 R8 范式），调用其现有 `count(mod.DEFAULT_SINCE)`，**不新增任何参数**，判据与阈值仍只存在于 SSOT。
2. **抽出可注入 seam**：加载逻辑单独成 `_load_redline_module()`，函数体本身只做「委托 + 可见化 + 降级」。抽出的**唯一目的**是让自检能注入替身模块，同时保证加载失败降级分支仍在被测路径内（若改成 patch `count()`，该分支会被绕过）。
3. **诚实降级（三段）**：脚本缺失 / `exec_module` 失败 / `count()` 抛异常（git 不可用、仓库损坏、since sha 失效）→ 一律 `check(False, "R9", "...→ 无法判定")`，既不炸掉整个门禁，也**绝不用 PASS 冒充正常**。
4. **永不静默通过**：未触发时也打印 `🟢 审计红线未触发: N/20，距红线还差 M 个节点（since <sha>）`，让「还剩几个」在日常每次 commit 里可见。

**WARN_ONLY 取舍理由（已写入代码注释，见顶部「R9 为何是 WARN_ONLY」小节）**

- 红线突破**不是提交内容的违规**，而是「流程节点到了」的信号 —— 本次提交本身可能完全干净。
- 若据此阻断提交，开发者的出路只有 `--no-verify` 或临时改阈值，**两条都直接摧毁门禁**；按本项目铁律，会被绕过的门禁比没有门禁更坏。与 R2/R3（数据根 .py、backend/build —— 均不进提交）是同一判据：只有会进入提交内容的违规才阻断。
- 因此：**结果仍在 RESULTS 里记为 1 条 FAIL，每次 commit 都会打印出来**（这正是「消除静默突破」的落点），只是不把 exit code 顶到 1；处置责任移交给流程侧（启动全库审计 + 功能冻结）。
- 反证（验收 C）已实测：一旦把 `"R9"` 从 WARN_ONLY 拿掉，同一份仓库状态立刻 exit code = 1。说明「警告」是**有意的分级**，不是空架子。

---

## ③ 验收 A / B / C 真实命令与实际输出

> 命令均在 `C:\Users\LOX\Desktop\DYchajian\DYAutoDM_v2` 下执行，解释器为指定的 Python314。

### A. 常态跑门禁 → 显示计数且 exit 0

```
$ C:/Users/LOX/AppData/Local/Programs/Python/Python314/python.exe scripts/check_iron_rules.py
======================================================================
铁律机械门禁 —— 声明式规则不靠自觉
======================================================================
  源码根 : C:\Users\LOX\Desktop\DYchajian\DYAutoDM_v2
  数据根 : C:\temp\dyautodm_design
----------------------------------------------------------------------
  [PASS] R1   源码树无数据根内容（命中：无）
  [PASS] R2   数据根顶层无散落源码（命中 0 个）
  [FAIL] R3   backend 无构建产物（命中 5: ...\dyautodm-backend-x86_64-pc-windows-msvc.exe）
  [PASS] R4   六处版本齐平 = ['0.45.40'] (0.45.40)
  [PASS] R5   源码无明文凭证（命中 0）
  [PASS] R6   无 taskkill 杀浏览器（命中 0）
  [PASS] R8-1 dm_messages 写入出口收敛（5 文件）
  [PASS] R8-2 类型注册表覆盖（已登记 9 种）
  [PASS] R8-3 text 不得承载 base64/图片 URL 载荷
  [PASS] R8-4 语义标签须取自 Schema SSOT（禁硬编码）
  [PASS] R8-5 前向兼容（未知类型降级 + 门禁在位）
  [PASS] R8-6 同一语义不得多名字（禁 msg_type 多值并列）
  [FAIL] R9   🔴 审计红线已触发: 41/20（since abd1f29） → 应启动全库审计 + 功能冻结（本次提交本身仍允许）
----------------------------------------------------------------------
  合计: 13 项，通过 11，未通过 2（阻断 0 / 警告 2）

警告项（不阻断提交，属磁盘卫生）：
  - R3: backend 无构建产物（命中 5: ...）
  - R9: 🔴 审计红线已触发: 41/20（since abd1f29） → 应启动全库审计 + 功能冻结（本次提交本身仍允许）

⚠ 仅有警告项，允许提交
EXITCODE=0
```

✅ **exit code = 0**（WARN_ONLY 只警告），R9 出现且读数可见。

### B. `--selftest` → 全通过，R9 负控确实报红

```
$ C:/Users/LOX/AppData/Local/Programs/Python/Python314/python.exe scripts/check_iron_rules.py --selftest
======================================================================
自检：验证门禁在违规时**真的会报红**（D-07）
======================================================================
  [FAIL] R1   源码树无数据根内容（命中：['members']）
  [FAIL] R2   数据根顶层无散落源码（命中 1 个: ...\iron_selftest_j0e6xz83\data\leak.py）
  [FAIL] R3   backend 无构建产物（命中 1: ...\iron_selftest_j0e6xz83\src\backend\evil.exe）
  [FAIL] R4   版本源缺失: ['tauri.conf.json', 'Cargo.toml', 'package.json', 'frontend/package.json', '_build_version.py']
  [FAIL] R5   源码无明文凭证（命中 1: cred.py:1）
  [FAIL] R6   无 taskkill 杀浏览器（命中 1: killer.py:1）
  [PASS] R8-1 ~ R8-6   （六项全 PASS）
  [FAIL] R9   🔴 审计红线已触发: 30/20（since deadbeef） → 应启动全库审计 + 功能冻结（本次提交本身仍允许）
  [PASS] R9   🟢 审计红线未触发: 3/20，距红线还差 17 个节点（since deadbeef）
----------------------------------------------------------------------
  期望报红: ['R1', 'R2', 'R3', 'R5', 'R6', 'R9']
  实际报红: ['R1', 'R2', 'R3', 'R4', 'R5', 'R6', 'R9']
  R9 正控（未触发形态应 PASS）: 通过

✓ 自检通过：所有可判定规则在违规时均会报红（非空架子）
EXITCODE=0
```

✅ R9 负控报红、整体自检仍 PASS。（R4 意外报红是既有行为：自检把 SRC_ROOT 换到空临时目录，版本源必然缺失 —— `failed_expect` 用 `missing = expect - got` 判定，不在期望集合内的多余报红不影响结论。此为改动前既有现象，未在本次范围内调整。）

### C. 负控反证 → exit code 变 1（**已还原**）

临时把 WARN_ONLY 里的 `"R9"` 去掉后重跑：

```
$ # sed 方式临时改 WARN_ONLY = {"R2", "R3"}，然后：
$ C:/Users/LOX/AppData/Local/Programs/Python/Python314/python.exe scripts/check_iron_rules.py
----------------------------------------------------------------------
  合计: 13 项，通过 11，未通过 2（阻断 1 / 警告 1）

警告项（不阻断提交，属磁盘卫生）：
  - R3: backend 无构建产物（命中 5: ...）

⛔ 阻断项（必须修复）：
  - R9: 🔴 审计红线已触发: 41/20（since abd1f29） → 应启动全库审计 + 功能冻结（本次提交本身仍允许）
EXITCODE=1
```

✅ **exit code = 1** —— 证明 R9 是真的会拦，WARN_ONLY 是有意分级而非空架子。

**还原核验**（同一条命令序列紧跟其后）：

```
$ git checkout 语义等价：cp "$TMPDIR/cir_backup.py" scripts/check_iron_rules.py
$ grep -n 'WARN_ONLY' scripts/check_iron_rules.py
339:WARN_ONLY = {"R2", "R3", "R9"}   # R9 理由见顶部「R9 为何是 WARN_ONLY」
$ python scripts/check_iron_rules.py            → EXITCODE_restored=0
$ python scripts/check_iron_rules.py --selftest → SELFTEST_EXITCODE_restored=0
$ git diff --name-only
DYAutoDM_v2/scripts/check_iron_rules.py        # 仍只有这一个文件被改
```

### D. SSOT 未被污染

`git diff --name-only` 输出只有 `DYAutoDM_v2/scripts/check_iron_rules.py`。`audit_redline_count.py` 无 diff，THRESHOLD / DEFAULT_SINCE / is_product_behavior 口径一字未动；R9 仅调用其既有 `count()`，未新增参数、未新增导出。

---

## ④ selftest 负控是怎么构造的

**为什么不能用现有手段**：selftest 现有做法是 monkeypatch `SRC_ROOT / BACKEND / DATA_ROOT` 到临时目录并投放违规文件。R9 读的是 **git log**，与这三个路径无关 —— 换临时目录对它完全无效，必须单独构造。

**做法（双向验证，均通过 seam 注入实现）**：

1. 在 `r9_audit_redline()` 上方抽出 `_load_redline_module()`（唯一的模块加载 seam，返回已 exec 的 module 对象）。
2. **负控**：selftest 里 `globals()["_load_redline_module"] = lambda: _FakeRedlineModule`，替身模块的 `count()` 返回 `{"count": 30, "threshold": 20, "triggered": True, "remaining": 0, ...}` —— 形态与真实 `count()` 返回字典完全一致。断言 `"R9" in got_failed`。
3. **正控**（额外加的，原需求未强制）：再注入一份 `triggered=False, count=3, remaining=17` 的干净形态，断言 R9 **PASS**。理由：只有负控自证不了任何东西 —— 一个「永远返回 False」的写死判据同样能过负控。正反两侧都跟着数据走，才算证明判据是数据驱动的。
4. **为什么 patch 加载 seam 而不是直接 patch `count()`**：前者让「脚本缺失 / 加载失败 → 诚实降级」这条分支仍在被测路径上；后者会把该分支旁路掉，降级逻辑将永远不被自检覆盖。
5. **还原安全性**：负控的 `saved_loader` 在取完 `got_failed` 后立即还原；正控用 `try/finally` 还原。selftest 结束不会污染真实门控状态。

---

## ⑤ 当前红线计数读数

- **41 / 20，已触发**（`since = abd1f29`），`remaining = 0`。
- 交叉核对：`python scripts/audit_redline_count.py --json` 同样返回 `"count": 41, "triggered": true, "skipped_total": 60`。
- ⚠ **与任务书背景不一致**：任务书写「当前应该是 30/20」，实测 **41/20**。差异来源应为背景数字取自较早快照/人工统计；本次两个脚本独立读数一致（41），以机械读数为准。**计数比原估计又多出 11 个节点**，恰好反向印证了 §6·1 建议 A 的必要性 —— 在此之前没有任何自动化会去看它。

---

## 总结（中文）

**达成。** 在唯一文件 `DYAutoDM_v2/scripts/check_iron_rules.py` 中新增 R9：委托 `audit_redline_count.count()`（SSOT 判据与 THRESHOLD=20 一字未改、未加参数），在 `.git/hooks/pre-commit → check_iron_rules.py` 这条唯一挂载点上实现了「谁来跑、挂在哪」的答案 —— 从此每次 commit 都会执行红线计数并打印读数，静默突破的窗口被关闭。设计上 R9 归入 WARN_ONLY：红线突破是流程节点信号、不是提交内容违规，阻断会把人逼向 `--no-verify` 或改阈值从而摧毁门禁；但它仍以 FAIL 形式打印，且未触发时也显示「距红线还差 N 个」，兼具可见性与可持续性。降级路径诚实：脚本缺失/加载失败/git 不可用时报「无法判定」而非 PASS，也不炸掉整个门禁。自检采用双向注入（负控 triggered=True 断言报红、正控 triggered=False 断言 PASS），避免「写死红灯」式伪自证；三项验收实测结果：A exit=0 且显示 41/20；B `--selftest` exit=0 且 R9 负控报红；C 去掉 WARN_ONLY 后 exit=1（**已还原，git diff 确认仅一个文件改动**）。唯一偏差是红线计数实测 **41/20** 而非任务书背景所述的 30/20 —— 两份脚本独立读数一致口径可核对，以机械读数为准，且该偏差本身进一步佐证了本机制的必要性。未 add / 未 commit，等待父会话统一提交。
