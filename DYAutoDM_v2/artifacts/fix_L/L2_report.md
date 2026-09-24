# L-2 ｜契约 §「验证方式」收敛为可执行门禁（check_contracts.py）

| 项 | 值 |
|---|---|
| 台账项 | **L-2**（`工作记忆/00_交接卡待办台账.md:123`）剩余工作：把各契约 §验证方式 的命令收敛进 `scripts/check_contracts.py` |
| 仓库 / 分支 | `C:\Users\LOX\Desktop\DYchajian` · `design/better-douyin` |
| 执行者 | 子代理（subagent，**只改 1 个文件**） |
| 解释器 | `python` = 3.11.16（Windows + git-bash/MSYS） |
| 交付物 | 改 `DYAutoDM_v2/scripts/check_contracts.py`；本报告 |
| 红线遵守 | 禁 git 写 ✅ ｜ 禁改契约 md ✅ ｜ 禁改版本号文件 ✅ ｜ 禁硬编码判据副本 ✅ ｜ 禁全量 discover ✅ |

---

## 1. 自述改动清单

**唯一改动文件**：`DYAutoDM_v2/scripts/check_contracts.py`
（`git diff --numstat` = **+221 / −1**；149 行 / 7135 B → **369 行 / 19006 B**）

| 位置 | 改动 | 性质 |
|---|---|---|
| 顶部 import | 增 `glob / inspect / os / subprocess / tempfile` | 支撑新检查 |
| `classify()` | 匹配口径**保持不变**（逐项精确相等），仅追加「就地基线 `INLINE_KNOWN`」并集 | 兼容扩展，**未放宽** G2 既有口径 |
| 新增 `INLINE_KNOWN` | L-2 就地已知缺口基线（逐 (check,target) 精确） | 见 §5 |
| 新增辅助 `_sec/_bash_blocks/_grep_specs/_expand` | 从契约 md 解析判据**定义** | 真源 = 契约文档 |
| 新增 **G6 ~ G14** 共 9 条 `check(...)` | §验证方式 可机械化检查 | 见 §3 |
| G0~G5 | **语义逐字未动**；退出码逻辑（`sys.exit(1 if failed else 0)`）未动 | — |

**未触碰（两证）**：
- 契约 md：`git status --short -- DYAutoDM_v2/docs/design-contracts/` **空**。
- 版本号文件 6 处（`package.json` / `frontend/package.json` / `tauri.conf.json` / `Cargo.toml` / `Cargo.lock` / `_build_version.py`）：`git status` **空**。
- **全程无 `git add/commit/checkout/stash/clean`**。

> ⚠️ **并发写者告警**：本轮期间父会话/另一执行体在并行实施 ADR-007（`dm_dispatch.py` / `database.py` / `app_config_schema.py` / `core/live_hook.py` / `core/auto_dm.py` / `web_probe.py` / `ai_reply.py` + 新增 `high_value_keywords.py`、`test_uid_sink_ext.py`）。我**零改动**这些文件（`git status` 里它们的 ` M` 全部非我所为）。

---

## 2. 步骤① · 契约 §5/§6「验证方式」逐条清点（只读文档）

对 C-01~C-06 的 §验证方式（C-06 为 §6「验证方式（可机械判定）」）逐条拆解，标记**可机械化 / 保持人工**：

| 契约 | §验证方式 条目 | 判定 | 落点 |
|---|---|---|---|
| C-01 | `unittest test_capability_probe`（message_integrity 探针） | ✅ 可机械 | **G14** |
| C-01 | `grep` 禁主动批量查询符号（`bulk_user_info` …） | ✅（**既有 G1 已覆盖**） | G1 |
| C-01 §2·Ⅰ3 | 昵称唯一来源 = IndexedDB `<uid>_user`（`kernel/truth.NICKNAME_SOURCE`） | ✅ 可机械 | **G7** |
| C-02 | `grep params=params.get()` 命中受保护端点 | ✅（**既有 G2 已覆盖**） | G2 |
| C-02 | `is_protected('/aweme/v1/web/mix/aweme/')` 自检 = True | ✅ 可机械 | **G6** |
| C-03 | `unittest test_config_isolation` | ✅ 可机械 | **G14** |
| C-03 | `grep "dm.*=.*wp"` 期望 0 逻辑耦合 | ✅（**既有 G3 已覆盖**） | G3 |
| C-03 §2·Ⅰ1 | `dm` 判定必须来自**真实写**（cmd 609 `create_conversation`） | ✅ 可机械（源码级） | **G8** |
| C-04 | `unittest test_capability_probe`（send_delivery 探针） | ✅ 可机械 | **G14** |
| C-04 | 硬验证：DB `role='me'` + `server_message_id`（盲标记不得称成功） | ✅ 可机械（判定器级） | **G9** |
| C-05 | `unittest test_live` | ✅ 可机械（模块缺失→基线） | **G14** |
| C-05 | reflow 主引擎（`_reflow_resolve` + `reflow/info`） | ✅（**既有 G5 已覆盖**） | G5 |
| C-05 §2·Ⅰ4 | 解密权合取，权威出口 `uid_identity_verdict` 的 `reason` 枚举 | ✅ 可机械 | **G10** |
| C-06 | `grep "ALTER TABLE dm_uid_sink ADD COLUMN"` 期望新列齐 | ✅ 可机械 | **G11** |
| C-06 | `grep is_high_value / window_end_ts / aggregate_text / keyword_score / high_value_*` 等符号守护 | ✅ 可机械 | **G12** |
| C-06 §4 | 配置键 7 项（`uid_sink_* / high_value_* / aggregate_max_chars`） | ✅ 可机械 | **G13** |
| C-06 | `unittest backend/test_uid_sink_ext.py` | ✅ 可机械 | **G14** |

**保持人工**（不可离线机械守，见 §4）：见下节。

---

## 3. 步骤② · 新增门禁 G6~G14（判据取自真源，禁硬编码副本）

**统一原则**：判据**定义**取自契约 md（`_sec()` 解析对应节）；判据**取值**取自被检模块/文件（`import` 后 `inspect.getsource`/属性，或读源码文本）—— **不把判据字面量抄进脚本**。

| 门禁 | 名称 | 判据定义（真源） | 取值来源（被检真源） |
|---|---|---|---|
| **G6** | C-02 签名自检 | C-02 §5 的 `is_protected('…')` 断言 | `utils.secsdk_web_sign.is_protected()` 实调 |
| **G7** | C-01 昵称源 SSOT | C-01 §2·Ⅰ3（§5 引用） | `kernel.truth.NICKNAME_SOURCE` + `NICKNAME_SOURCES_ORDER` |
| **G8** | C-03 真实写校验 | C-03 §2·Ⅰ1 | `auto_dm.accounts.probe_im_write` 源码符号 |
| **G9** | C-04 投递硬验证 | C-04 §2·Q① / §5 | `services.delivery_verify._resolve_evidence()` 实调 + 标记行 `role` |
| **G10** | C-05 解密权合取 | C-05 §7 `reason ∈ {…}` | `auto_dm.accounts._VERDICT_LABEL` 键集 |
| **G11** | C-06 字段规范 | C-06 §3 字段表 + §6 表名 | `backend/database.py` 的 `CREATE TABLE`/`ALTER ADD COLUMN` |
| **G12** | C-06 符号守护 | C-06 §6 的 `grep` 判据（**解析 bash 块**） | 被 grep 的文件文本 |
| **G13** | C-06 配置键 | C-06 §4 配置键表 | `services.app_config_schema.SECTIONS["send"]["fields"]` |
| **G14** | C-01~C-06 单测可运行 | 各契约 §5/§6 的 `unittest` 行（**解析**） | `python -m unittest <mod>` 子进程真跑；失败**逐用例**登记（`FAIL:/ERROR:` 头） |

**G6~G13** 均为**离线、只读**（导入/读文件，零网络、零 DB 写；实测导入 `services.probe`/`accounts`/`delivery_verify`/`link_resolve` 不落库、不建文件）。
**G14** 用 `DY_APP_ROOT=<临时目录>` 隔离**逐个**跑「由契约 §5/§6 文本解析出的」单测模块（本轮实测解析到 4 个：`test_capability_probe` / `test_config_isolation` / `test_live` / `test_uid_sink_ext`），**非全量 discover**。

---

## 4. 步骤③ · 保持人工清单（如实列出）

以下为 §验证方式 中**不可离线机械守**的条目，本门禁**不覆盖**，保持人工/Live-Instance：

1. **C-04 投递硬验证的运行时执行**：真发一条私信后查 DB `role='me'` 且 `skey/msg_id` 为本次记录 —— 需真账号 + 真会话。门禁只守「判定器在场且盲标记判负」（G9）。
2. **C-05 §6 在线验证**：给真实直播间 URL → 返回 `room_id` —— Live-Instance，需网络。
3. **C-03 只读态服务端触发条件**：为何账号级进入只读 —— 需 A/B 双账号对照实测（C-03 §6 遗留项）。
4. **C-02 §6 / C-05 §3 上游签名版本对照**：带/不带签名各发一次比对 `status_code`；webcast 通道 X-Bogus→a_bogus 换代确认。
5. **C-06 §5 NFR 预算**：`mark_seen` ≤5ms、`should_send` ≤1ms、内存 ≤10MB、窗口扫描 ≤50ms —— 需真生产库/压测。
6. **C-06 §7 归档策略 / LLM 最终一致性窗口** —— 未设计。
7. **C-01 NFR 捕获延迟 ≤2s、C-03 `timeout=8s`** 等运行期预算。
8. **C-03 §5 单测的「只读态须判 fail 而非 ok」反向用例**：已由独立守卫模块 `test_credential_verification_guards.py` 覆盖（**非 §5 原始命令、非本门禁范围**，仅备注其存在）。

---

## 5. 步骤④ · 已知缺口基线（只对新增违规失败）

**基线机制**：新增门禁命中**既有**未修缺口时，复用 `.known-gaps.json` **同款口径**（逐 `(check, target)` **精确相等**，禁前缀/文件级豁免）。

**边界冲突与处置（诚实说明）**：本轮边界严格限定「**只改 `check_contracts.py` 一个文件**」，**不得写 `.known-gaps.json`**。故新增门禁（G14）命中的既有缺口**就地登记**在脚本内的 `INLINE_KNOWN`（同形同口径），**不写外部基线文件**。

**逐项登记（2 条，均为既有缺口，均非本轮引入）**：

| check | target | 理由 |
|---|---|---|
| `G14 C-01~C-06 单测可运行` | `backend/test_live.py` | C-05 §6 引用的 `test_live` 模块**从未建立**（其守卫已被 C-05 §7 的 `test_live_identity_verdict.py` 覆盖并通过，但那不是 §6 命令所指模块）。修复方向：建 `test_live.py` 或改 C-05 §6 命令（**均非本轮权限内**）。 |
| `G14 C-01~C-06 单测可运行` | `backend/test_uid_sink_ext.py::T3UidSinkExtTest.test_window_delays_send` | 父会话并行实施 ADR-007 时**新建**该模块（C-06 §6 引用）；我实测其 15 项中 **1 项失败**（`窗口未到期必须拒绝`：`assertFalse(ok)` 得 `True`），其余 14 项通过。按**逐用例**精确登记，**只对新增违规失败**。修复方向：父会话修 `should_send` 窗口逻辑或测试（**非我权限，属 WIP**）。 |

**逐用例粒度的必要性（实测留痕）**：G14 首版按**模块**登记 offender，因父会话新建 `test_uid_sink_ext.py` 后整模块变红 → 误判「新增违规」；改为解析 `FAIL:/ERROR:` 头**逐用例**登记后，仅那条真失败用例入基线、其余 14 项照常守护 ⇒ **既未文件级豁免、也未掩盖**。

**已从候选基线中移除**：`backend/test_uid_sink_ext.py`（**模块级**）—— 父会话建立该文件后按模块级会误红；改逐用例后**精确**为上述 1 条。

> 登记≠修复。`test_live.py` 建立且通过、`test_uid_sink_ext.py::test_window_delays_send` 修好后，**必须删除** `INLINE_KNOWN` 对应条目（同 `.known-gaps.json` 的删除纪律）。

---

## 6. 步骤⑤ · 离线自证（PASS → 注入 FAIL → 字节级还原）

### 6.1 基线 PASS（改后）
```
$ cd DYAutoDM_v2 && python scripts/check_contracts.py
  [PASS] G0 契约文件 ≥5                 实测 6 份
  [PASS] G1 C-01 捕获零主动查询            0 命中
  [PASS] G2 C-02 secsdk 签名接线        ⚠ 已知缺口 7 处（见 .known-gaps.json）
  [PASS] G3 C-03 dm 不冒充 wp          0 命中
  [PASS] G4 C-04 投递有回执/落库验证         存在回执处理
  [PASS] G5 C-05 reflow 主引擎存在           已实现
  [PASS] G6 C-02 签名自检               1 条断言全真
  [PASS] G7 C-01 昵称源 SSOT           NICKNAME_SOURCE='indexeddb:<uid>_user' 首选=['indexeddb']
  [PASS] G8 C-03 真实写校验              probe_im_write 走 create_conversation 且分型只读态
  [PASS] G9 C-04 投递硬验证              无证据=False 有msg_id=True 8610=False 标记role=me:True
  [PASS] G10 C-05 解密权合取             契约 […] ⊆ 实现 […]
  [PASS] G11 C-06 字段规范              12 字段齐备
  [PASS] G12 C-06 符号守护              §5 全部 grep 判据命中
  [PASS] G13 C-06 配置键               7 键齐备
  [PASS] G14 C-01~C-06 单测可运行        ⚠ 已知缺口 2 处（见 .known-gaps.json）
EXIT=0
$ python scripts/check_contracts.py --quiet ; echo $?
0
```
**G0~G5 逐字不变**（新门禁仅**追加**，未改既有语义/退出码）；`git diff` 对 G0~G5 段为空。

### 6.2 注入违反 → 变红
对 **`backend/kernel/truth.py`**（G7 取值源，非契约文本）注入一处违反：
`NICKNAME_SOURCE = "indexeddb:<uid>_user"` → `"dom_hook"`。
```
$ python scripts/check_contracts.py
  ...
  [FAIL] G7 C-01 昵称源 SSOT           NICKNAME_SOURCE='dom_hook' 首选=['indexeddb']
  ...
1 项未通过：['G7 C-01 昵称源 SSOT']
INJECT_EXIT=1
```
⇒ 门禁**真的会红**（非假门禁），且只命中对应检查。

### 6.3 字节级还原 + 核对
```
$ cp <备份> backend/kernel/truth.py && rm <备份>
AFTER  bytes=2740  sha256=d74827e1f26bfb70ed03bbd03cf6f8e8c611197456bf3aded03d18a4781d2c61
BEFORE bytes=2740  sha256=d74827e1f26bfb70ed03bbd03cf6f8e8c611197456bf3aded03d18a4781d2c61   # 完全一致
$ python scripts/check_contracts.py --quiet ; echo $?
0
```
⇒ 注入源**逐字节还原**（字节数 2740 与 sha256 前后一致），门禁恢复 PASS。

**本轮改动文件指纹**：`scripts/check_contracts.py` md5=`48515ab3412b9e9fe54d3772b2e1432f`，sha256=`a4af8c80b4bb28c3d9cb61c4e7a3f2f2cfc988ccaa59dde0758c672d83f2671d`，369 行 / 19006 B。

> 注入源 `backend/kernel/truth.py` 前后均为 **bytes=2740 / sha256=`d74827e1…d2c61`**（逐字节一致）；`RESTORE_BYTE_IDENTICAL=YES`；还原后 `--quiet` 退出码 **0**。

---

## 7. 诚实标注 / 遗留（超 6 节骨架的补充，如实记录）

1. **G14 的运行时开销**：以子进程真跑 4 个单测模块（~5s），使门禁从「纯静态」变为「秒级」。可接受但需知悉。
2. **G12 是文件级符号存在性**（与契约 `grep` 同粒度），**不做方法级归属**（如「`is_high_value` 出现在 `should_send` 内」需 AST 才能精确判定，本轮未做）。
3. **G11/G13 依赖契约表格列 0 的**反引号字段**解析**；若契约表格式大改（前导列变化），解析可能失配 → 会以「契约 §X 未解析到字段/键」显式变红（**失败模式为显式，不是静默跳过**）。
4. **G6 仅 1 条断言**（契约 §5 只有 1 个 `is_protected` 示例）。
5. **导入副作用**：门禁 `import` 后端模块（`accounts`/`delivery_verify` 等）。已实测**无 DB 写、无文件落盘、无网络**；但在极少数模块 import 失败的机器上，G7~G10 会显式 FAIL（非静默）。
6. **未写 `.known-gaps.json`**（边界所致）：G14 的 2 条既有缺口改用**就地 `INLINE_KNOWN`**。代价 = 基线**两处真源**；修复后需**手工**删除就地条目。
7. **两处既有缺口登记在基线（`test_live.py` 缺失 + `test_uid_sink_ext.py::…test_window_delays_send` 子测失败）**：G14 已按**逐用例精确**登记 ⇒ 只对新增违规失败。二者均为**真实未修缺口**（前者=契约 §6 命令所指模块未建；后者=父会话并行 ADR-007 的 WIP，属进程内**真实失败**，非我引入），修复需建模块/修窗口逻辑（**均不在本轮权限**）。
8. **未做**：未对 G6~G13 逐一做「注入→变红」破坏性验证（**仅对 G7 做了字节级自证**，见 6.2）；其余门禁的正确性依据是「PASS 态实测取值 + 判据取自真源」的静态推理，**未逐条反证**。
9. **无 git 写**：全程未 `add/commit/checkout/stash/clean`；工作区改动交父会话统一提交。
