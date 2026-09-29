# 任务I 报告

## 0. 只读声明
实际改动的文件（清单内，逐一列出）：
- `C:\Users\LOX\Desktop\DYchajian\DYAutoDM_v2\scripts\verify_lead_disposition_real.py`
- `C:\Users\LOX\Desktop\DYchajian\DYAutoDM_v2\scripts\migrate_fallback_pool.py`

清单外文件全部只读（`backend/services/ai_reply.py`、`scripts/verify_live_contact_fix.py` 仅 grep/read 取证）。
未做任何 git 写操作（无 add/commit/reset/checkout/stash/clean）；未改版本号（package.json/tauri.conf.json/Cargo.toml/Cargo.lock/_build_version.py 未触碰）。
未启动浏览器/BCC/守护/uvicorn/打包；未触碰真实数据根 `C:\temp\dyautodm_design` 与 `accounts/*/profile`（全部负控使用 `$SCRATCH` 下**新构造的最小 sqlite**）。

## 1. 结论汇总表
| # | 位置(文件:行) | 判定(真缺陷/误报) | 修法 | 修复前证据 | 修复后证据 |
|---|---|---|---|---|---|
| I-1 | `verify_lead_disposition_real.py:79`(原) | **真缺陷**（假通过） | 期望布尔 → 期望类型 `ASK`/`ADVANCE`；`ADVANCE` 走与本脚本内联、与「含索要」正交的 `_has_progress`；加负控自检 | `ok=(has_ask if expect_ask else not stalled)`，实测 4 组全部 `ok==has_ask`（坍缩，见 §2） | 自检 5 条全 PASS；**同一「专业回答无索要」样本 old_ok=False → new_ok=True**；负控「嗯嗯好的」new_ok=False（红） |
| I-2 | `verify_lead_disposition_real.py:42-46`(原) | **真缺陷** | 连接前校验 `SNAP/dyautodm.db` **文件存在** + `kv_store` 表存在，缺失退 2；改只读 URI | 无参运行：`SNAP.exists()`=True → 静默新建 **0 字节** `dyautodm.db` → `sqlite3 ... no such table: kv_store`，退出 1 | 无参运行：明确报错「未找到库文件…」退 **2**，CWD **未产生** dyautodm.db；给合法快照则正常跑完 |
| I-3 | `migrate_fallback_pool.py:140/165`(原) | **真缺陷** | 按 `changed` 分键守卫：未迁移 `fallback_pool` 的键既不打印也不纳入读回校验；键数量守恒单列 | 库含 `fallback_image`(旧值) 但**缺** `fallback_pool`：`KeyError: 'fallback_pool'` 退 1 | 同库 dry-run 正常（exit 0）；`--apply` 正常写回「写后读回：fallback_image=… / 其余键数量=3(写前 3)」退 0 |
| I-4 | `migrate_fallback_pool.py:97-98`(原) | **真缺陷**（假阴性） | 无任一候选含 `ai_reply_config` 时抛 `_NoTargetDB` → `main()` 报错退 **2**，不再 `return candidates[0]` | 库无 `ai_reply_config`：打印「无需迁移（走代码默认值）」**exit 0**（假阴性） | 同库：`❌ 无法定位目标库：…没有任何一个含 ai_reply_config…` **exit 2**；正常库（含键）仍 exit 0 成功迁移 |

## 2. 逐条详述（代码原文 + 为何是问题 + 改成什么 + 负控如何变红/转绿）

### I-1（OCR[31] · HIGH · 假通过）
**代码原文（修复前 `verify_lead_disposition_real.py:77-79`）：**
```python
has_ask = bool(reply) and A._has_lead_ask(reply)
stalled = A._is_lead_stalled(reply) if reply else True
ok = (has_ask if expect_ask else not stalled)
```
**为何是问题：** `ai_reply._is_lead_stalled` 的定义即 `return not _has_lead_ask(s)`（`backend/services/ai_reply.py:1003-1008`）。故 `not stalled == has_ask`，于是 `expect_ask=False` 分支 `ok = not stalled = has_ask`，与 `expect_ask=True` 分支 `ok = has_ask` **完全等价** ⇒ 两分支坍缩。第 4 例「我这个情况能评几级」（期望**有推进/专业回答**）从未被真正断言，只是又验了一遍「含索要」= **假通过**。
**改成什么：** 引入**期望类型**取代布尔：
- `ASK`：必须含索要（`A._has_lead_ask`）。
- `ADVANCE`：必须**有推进**，判据为本脚本内联、与「是否含索要」**正交**的 `_has_progress()`（有明确问句 `？/?`，或命中专业知识/引导词；并对「放走语」明确判否）。`ADVANCE` 分支**完全不看** `has_ask`，因此即使回复不含索要也能通过。
**负控如何变红/转绿（确定性，不依赖网关）：** 内置 `_SELFTEST` 5 条，覆盖「含索要→PASS/无索要→FAIL」「专业无索要→**PASS**（证明 ADVANCE 分支不再挟持含索要）」「放走语→FAIL」「**负控：既无专业内容也无索要（『嗯嗯好的』）→ 必须 FAIL**」。实测输出：
```
[PASS] ask-hit：含索要=True → got_ok=True
[PASS] ask-miss：含索要=False → got_ok=False
[PASS] advance-专业无索要：含索要=False, 有推进=True → got_ok=True   ← 旧逻辑此处恒 False
[PASS] advance-放走语：有推进=False → got_ok=False
[PASS] 负控：既无专业内容也无索要（'嗯嗯好的'）：有推进=False → got_ok=False（红）
```
**同一样本 old vs new 对照：**
| 样本 | old_ok(=has_ask) | new_ok(_has_progress) |
|---|---|---|
| 有明确问题→专业回答(无索要) | False | **True** |
| 负控：无专业也无索要 | False | False |
| 放走语 | False | False |

### I-2（OCR[30] · MED）
**代码原文（修复前 `:23/:42-46`）：**
```python
SNAP = Path(sys.argv[1] if len(sys.argv) > 1 else "").resolve()
...
if SNAP.exists():
    c = sqlite3.connect(SNAP / "dyautodm.db")
```
**为何是问题：** 无 CLI 参数时 `Path("").resolve()` = CWD（**恒存在**）⇒ `SNAP.exists()` 守卫失效 ⇒ `sqlite3.connect(SNAP/"dyautodm.db")` **静默新建空库** ⇒ 下一条 `SELECT key,value FROM kv_store ...` 报 `sqlite3.OperationalError: no such table: kv_store`。
**修复前证据（无参运行）：** 报 `no such table: kv_store`，退出 1，且 CWD 被创建出 **0 字节** `dyautodm.db`。
**改成什么：** 参照兄弟脚本 `verify_live_contact_fix.py`（其 `main()` 首行 `if not (SNAP_DB/"dyautodm.db").exists(): ... return 2`）——`main()` 开头校验 `DB_FILE = SNAP/"dyautodm.db"` **文件存在**，并改**只读 URI** 连接；连接后再校验 `sqlite_master` 里 `kv_store` 表存在，任一缺失打印明确错误并 `return 2`。
**修复后证据：** 无参运行打印「错误: 未找到库文件 …（无参数时 SNAP=CWD 恒存在会静默建空库 ⇒ 已改为显式校验）」退 **2**，`ls -la` 确认 CWD **未产生** dyautodm.db；传入合法快照目录则正常执行（自检 5 PASS + 4 用例 PASS，exit 0）。

### I-3（OCR[28] · MED）
**代码原文（修复前 `:140` 与 `:165`）：**
```python
print("新 fallback_pool:")
for x in cfg["fallback_pool"]:      # ⇒ KeyError
...
print("  fallback_pool[0] =", chk["fallback_pool"][0][:40], "…")
ok = chk["fallback_pool"] == NEW_POOL and len(chk) == ...
```
**为何是问题：** `fallback_pool` 若缺失，上文 `else` 分支已明确「缺失 ⇒ 不动」（不加入 `changed`），但打印与读回校验**无条件**取该键 ⇒ `KeyError`（写后读回还会「验了未迁移的值」）。
**修复前证据（缺 `fallback_pool`、含旧 `fallback_image` 的库）：** dry-run 即 `KeyError: 'fallback_pool'` 退 1。
**改成什么：** 打印与读回校验全部**按 `changed` 分键守卫**——仅当 `"fallback_pool" in changed` 才打印/校验该键，`fallback_image` 同理；键数量守恒（迁移只改值、不增删键）单列一条校验；`ok = all(checks)`。
**修复后证据（同一缺键库）：** dry-run 正常输出「将替换字段: ['fallback_image']」退 0；`--apply` 副本正常「写后读回：fallback_image=… / 其余键数量 = 3（写前 3）/ ✅ 成功」退 0。

### I-4（OCR[29] · MED · 假阴性）
**代码原文（修复前 `:97-98`）：**
```python
    if candidates:
        return candidates[0]
```
**为何是问题：** 该回退可能返回**不含 `ai_reply_config` 键**的库（docstring 明示的 `hotswap-probe` 探针库即此形态）⇒ `main()` 读到缺键即 `print("无需迁移…")` **退出 0** = **假阴性**（真生产库根本没找到）。
**修复前证据（构造仅含 kv_store、无 ai_reply_config 的库）：** 输出「未找到 ai_reply_config 键 —— 无需迁移（走代码默认值）」**exit 0**。
**改成什么：** 新增 `class _NoTargetDB(Exception)`；`_find_db` 在「有候选但无任一含 `ai_reply_config`」时抛 `_NoTargetDB`（附候选清单），`main()` 捕获并打印 `❌ 无法定位目标库：…` 退 **2**，绝不静默退 0。
**修复后证据（同库）：** `❌ 无法定位目标库：找到 1 个 dyautodm.db，但没有任何一个含 ai_reply_config 键… ⇒ 真生产库未定位，拒绝静默退出 0（假阴性）。` **exit 2**；正常库（含 `ai_reply_config` 及旧池）仍 exit 0 并逐字段成功迁移。

## 3. 验证命令与结果
运行解释器统一 `py -3.14`（Python 3.14.6）。所有负控库均在 `$SCRATCH`（profile cache scratch）下新构造、以 `DY_APP_ROOT` 隔离，未触真实库。

**编译（必须过）：**
```
$ py -3.14 -m py_compile scripts/verify_lead_disposition_real.py scripts/migrate_fallback_pool.py
COMPILE OK            # exit 0
```

**I-3/I-4 负控转绿（无 pytest 框架，用脚本自带输出 + 退出码佐证；脚本均为顶层 `sys.exit(main())`）：**
```
# I-3 dry-run（缺 fallback_pool 键，含旧 fallback_image）
$ DY_APP_ROOT=<root_i3> py -3.14 scripts/migrate_fallback_pool.py
目标库: …\root_i3\members\m_i3\data\dyautodm.db
fallback_pool 缺失 ⇒ 不动（走代码默认）
将替换字段: ['fallback_image']
…(--dry-run：未写入…)                                   # exit=0  （修复前：KeyError，exit=1）

# I-3 --apply（同样缺键）
$ DY_APP_ROOT=<root_i3_apply> py -3.14 scripts/migrate_fallback_pool.py --apply
已备份: …（读回比对通过）
写后读回：
  fallback_image = 图片收到，我看下材料再给您准话。… 
  其余键数量 = 3 （写前 3 ）
迁移结果: ✅ 成功                                          # exit=0  （修复前：dry-run 阶段即崩）

# I-4（无 ai_reply_config）
$ DY_APP_ROOT=<root_i4> py -3.14 scripts/migrate_fallback_pool.py
❌ 无法定位目标库：找到 1 个 dyautodm.db，但**没有任何一个**含 ai_reply_config 键 …
候选:
  …\root_i4\members\m_i4\data\dyautodm.db                  # exit=2  （修复前：exit=0 假阴性）

# 正常路径回归（含 ai_reply_config + 旧池）
$ DY_APP_ROOT=<root_ok> py -3.14 scripts/migrate_fallback_pool.py --apply
将替换字段: ['fallback_pool', 'fallback_image'] … 迁移结果: ✅ 成功   # exit=0
```

**I-2 负控转绿：**
```
# 无参运行（CWD 无 dyautodm.db）
$ py -3.14 scripts/verify_lead_disposition_real.py
用法: py -3.14 scripts/verify_lead_disposition_real.py <库快照目录>
错误: 未找到库文件 …\cwd_i2\dyautodm.db（无参数时 SNAP=CWD 恒存在会静默建空库 ⇒ 已改为显式校验）
                                                          # exit=2；ls 确认 CWD 无 dyautodm.db
（修复前：sqlite3.OperationalError: no such table: kv_store，exit=1，CWD 被建出 0 字节库）

# 合法快照
$ py -3.14 scripts/verify_lead_disposition_real.py <snap_i2>
[判据自检 · I-1 负控] … 5 条全 PASS
system prompt 含留资铁律: True  长度=622
  … 4 用例全部 PASS
全部 PASS                                                 # exit=0
```
脚本无 pytest 测试类；如按「Ran N tests」口径，等价于 **I-1 自检 5 条全部 PASS，4 用例全部 PASS，OK**（退出码 0）。

## 4. 未做/存疑/需真机验证（诚实标注）
- **I-1 的 `ADVANCE` 判据（`_has_progress`）是本脚本内联的启发式**，非 `ai_reply` 生产判据（生产里没有「推进」这一概念，只有「含索要」）。这是刻意的：脚本目的是区分「期望含索要」与「期望有推进」两类用例；词表可能对真机/网关产出的新表述有覆盖盲区 —— 真网关用例的最终结论仍需在有本机网关（`127.0.0.1:31415`）的机器上复跑确认。本次因网关非本机，4 用例走的是「兜底」来源（已 PASS），属**我实测过**的路径；真实 AI 生成路径**未实测**。
- I-2 的「合法快照」验证里，`ai_reply` 报了 401 Invalid API key（快照的 `model_hub` 与生产密钥组合不同所致，脚本注释已述），这与本次修复无关，**未改动**。
- I-1 只强化了「判定」；未触碰 `ai_reply` 护栏本身（清单外，只读）。
- 未运行项目级 pytest / 未做 git 提交（父会话统一提交）。
- 三处「修复前」证据均为本机 `py -3.14` 实跑复现（非静态推断）；三处「修复后」均为实跑转绿。
