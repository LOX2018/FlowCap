# A-5 施工报告 · C-07 直播交互设计契约 + 门禁 G15

- 日期：2026-09-28
- 范围：直播交互域（`backend/api/live.py` 的 `/danmaku` · `/dm-template` · `/ws`）
- Python：`C:/Users/LOX/AppData/Local/Programs/Python/Python314/python.exe`
- 仓库：`C:/Users/LOX/Desktop/DYchajian/DYAutoDM_v2`
- 结论：**门禁绿（exit 0）· 负控红（exit 1）· selftest 绿（exit 0）** —— 全部真机执行取证。

---

## 0. 交付物与边界

| 类型 | 路径 | 变更 |
|---|---|---|
| 新增 | `docs/design-contracts/C-07-live-interaction.md` | 120 行（照 C-06 结构：1 意图 / 2 DbC / 3 字段表 / 4 配置项 / 5 语义 / 6 机械验证 / 7 已知缺口） |
| 修改 | `scripts/check_contracts.py` | +16 行，末尾追加 G15（在 G14 块之后、`--selftest` 之前） |
| **未动** | `backend/api/live.py` | sha256 `a79fcdcef56865aca747e6e4410510303ff60b0353887d6dd6048328e18793f3`（改前=改后，被检对象零改动） |
| **未动** | `backend/main.py`（`_MEMBER_EXEMPT`） | 零改动 |
| **未动** | `docs/design-contracts/.known-gaps.json` | `known_gaps: []` 保持为空，**未据此假豁免** |
| **未动** | `scripts/check_contracts.py` 的 `INLINE_KNOWN` | 第 65 行 `= set()` 保持为空，**未据此假豁免** |

> 仓库内另有 `crawl.py` / `downloader.py` 等改动与其它 `artifacts/*`、`test_*.py`、`scripts/普查脚本_*.py`
> 属**其它会话/任务**的在制品，非本次施工所改。

未越界：仅改 `scripts/check_contracts.py` + 新建 `C-07`。未执行 `git add`/`commit`；未改版本文件；未跑全量测试。

---

## 1. C-07 判据清单（§6 · 机读形式 `grep -n "<符号>" <仓库相对路径>`）

C-07 §6 共 **7 条** grep 判据（G15 实测解析到 7 条），覆盖任务要求的三个最低面：

| # | 判据（符号） | 目标文件 | 守护的语义 | 关联契约 |
|---|---|---|---|---|
| 1 | `DouyinAPI.sendMsgInRoom` | `backend/api/live.py` | **danmaku 真调写接口**（而非恒 `ok:true` 空壳） | P4·Q1·I2 |
| 2 | `status_code` | `backend/api/live.py` | 成功 = 上游 `status_code == 0`（非「没抛异常」） | Q1·I2 |
| 3 | `set_kv_json` | `backend/api/live.py` | **dm-template 真落盘**（写 `kv_store config`） | Q3·I3 |
| 4 | `dm_template` | `backend/api/live.py` | payload 落在 `config.dm_template` 下 | Q3 |
| 5 | `get_kv_json` | `backend/api/live.py` | **落盘后读回校验**（防空转） | Q4 |
| 6 | `persist_failed\|readback_mismatch\|readback_failed` | `backend/api/live.py` | **失败一律 fail-closed 不报成功**（落盘/读回分型齐备） | I1·I3 |
| 7 | `empty_content\|no_account\|no_room_id\|credential_unavailable\|credential_empty\|upstream_failed` | `backend/api/live.py` | danmaku 各失败分型齐备（缺任一 ⇒ 失败路径可能静默成功） | I1 |

> §6 **不含** `-m unittest <模块>` 命令 —— 刻意规避 G14（G14 会解析全部 `C-*.md` §5/§6/§7 的
> unittest 命令并要求模块真实存在）。本域改用 grep 形式守护。

判据清单中的 7 个符号于施工前已逐一在 `backend/api/live.py` 上做**词边界**存在性预核
（`\b<符号>\b`）全部命中，故 G15 不产生任何新增违规、**无需**任何豁免登记。

---

## 2. G15 实现说明

在 `scripts/check_contracts.py` 的 G14 块与 `--selftest` 块之间追加：

```python
# ── G15: C-07 §6 符号守护（契约 grep 判据 → live.py 符号存在性）──────
_c07 = DC / "C-07-live-interaction.md"
_specs15 = _grep_specs(_sec(_c07, 6))
_off15 = _g12_offenders(_specs15)
check("G15 C-07 直播交互符号守护", not _off15,
      f"§6 全部 {len(_specs15)} 条 grep 判据命中（词边界精确匹配）"
      if not _off15 else f"违规 {_off15}")
```

设计要点（**照 G12 `_grep_specs` / `_g12_offenders` 范式**，未复制判据字面量）：

1. **判据真源 = 契约 md**：`_sec(_c07, 6)` 取 C-07 §6 原文；`_grep_specs(...)` 解析
   `grep -n "<pat>" <path>` → `[(path, [符号…])]`。判据**定义**取自契约，**取值**取自被检文件。
2. **词边界精确匹配（非子串）**：复用 `_g12_offenders`，对每个符号以 `\b<符号>\b` 正则断言在场
   ⇒ `DouyinAPI.sendMsgInRoom` 改名为 `..._disabled` **即红**（子串匹配会假绿）。
3. **判据来源缺失 = FAIL（禁静默 SKIP）**：`_g12_offenders` 在 `specs` 为空时返回哨兵
   `["<判据来源缺失：契约 §6 未解析到任何 grep 判据>"]` ⇒ `not _off15` 为假 ⇒ **报红**。
   杜绝「契约 §6 被改坏（去掉 `grep ` 前缀）→ 解析 0 条 → 恒真通过」的假绿。
4. **无豁免**：G15 不进 `classify()`、不查 `KNOWN`/`INLINE_KNOWN`、不写 `.known-gaps.json`。
   本域实现（N4 修复后）本就满足全部判据，**无既有缺口需登记**；任何登记都属禁止的假豁免。

---

## 3. 验收证据（真实命令 + 退出码）

### 3.1 门禁绿

```
$ cd DYAutoDM_v2 && <python314> scripts/check_contracts.py
...
  [PASS] G12 C-06 符号守护        §6 全部 6 条 grep 判据命中（词边界精确匹配）
  [PASS] G13 C-06 配置键          7 键齐备
  [PASS] G14 C-01~C-06 单测可运行   5 个契约单测模块可运行
  [PASS] G15 C-07 直播交互符号守护   §6 全部 7 条 grep 判据命中（词边界精确匹配）
EXIT=0
```

```
$ <python314> scripts/check_contracts.py --quiet ; echo $?
EXIT=0
```

### 3.2 负控红（真会红 —— 三条）

```
== 负控① 改坏 danmaku 真调符号 DouyinAPI.sendMsgInRoom → DouyinAPI.sendMsgInRoom_disabled ==
$ <python314> scripts/check_contracts.py --quiet
NEG1_EXIT=1

== 负控② 改坏 fail-closed 分型常量 persist_failed → persist_error ==
$ <python314> scripts/check_contracts.py --quiet
NEG2_EXIT=1

== 负控③ 破坏判据来源（C-07 §6 去掉 `grep ` 前缀 ⇒ 解析 0 条 ⇒ 必须 FAIL，禁静默 SKIP）==
$ <python314> scripts/check_contracts.py --quiet
NEG3_EXIT=1
```

三条负控均为**临时改坏 → 跑门禁 → 立即还原**，还原后复核：

```
restore live.py sha=a79fcdcef56865aca747e6e4410510303ff60b0353887d6dd6048328e18793f3   （= 施工前原值）
restore C-07   sha=d23eb9848ccd3cacd1b6c7ee72b3c85509ab7a77357093a1f89e747de4cfe706
$ <python314> scripts/check_contracts.py --quiet
AFTER_RESTORE_EXIT=0
```

### 3.3 `--selftest` 仍绿

```
$ <python314> scripts/check_contracts.py --selftest
  [✔] G1 抖音出站计入主动查询        active=2 by_entry={'e': 2}
  [✔] G1 本地网关不计主动查询        active=0 total=2
  [✔] G1 探针真捕获到出站请求        出站 2 次 / 主动查询 0 次
  [✔] G4 盲ok不冒充投递证据        盲ok=False
  [✔] G4 真msg_id判为证据        真msg_id=True
  [✔] G5 _reflow_resolve 可调用
  [✔] G5 签名≥2参
  [✔] G5 端点常量 reflow/info
  [✔] G12 词边界拦住子串改名        违例=['…\\m.py:is_high_value']
  [✔] G12 精确在场不误报          0 违例
  [✔] G12 空判据集判 FAIL        哨兵=['<判据来源缺失：…>']
------------------------------------------------------------
✓ 自检通过：G1/G4/G5/G12 对坏状态均报红，对好状态不误报
SELFTEST_EXIT=0
```

---

## 4. 诚实记录（未核验 / 缺口）

- **`/ws` 空转未实现**：C-07 §7 已显式登记 —— `live_ws` 仍为 `# TODO` 桩，`accept()` 后仅
  `receive_text()` 循环，**不推送**真实弹幕/热度；G15 以符号在场守护**已实现的**写/落盘路径，
  不把 `/ws` 桩伪装成已实现（未把 `receive_text`/`live_ws` 作为「功能已实现」判据）。
- **`/ws` token 自验未实现**：`/api/live/ws` 在 `_MEMBER_EXEMPT` 内、宣称「自验 token」，
  但当前 `live_ws` 无任何 token 校验逻辑 —— 已在 C-07 §7 登记为缺口。
- **弹幕真机投递未核验**：`sendMsgInRoom` 是否真的在服务端落地弹幕，**未做**真机端到端验证
  （本任务红线禁止真机发送弹幕）；契约只保证「真调写接口 + 按上游 `status_code` 判成败」。
- **`/dm-template` 读路径未接**：前端模板回读仍走 `GET /api/tasks/current` 的 `dmPool`，
  **不读**本端点写的 `config.dm_template` 键；C-07 §7 已登记。
- G15 的「判据来源缺失哨兵」经负控③实测会红；`--selftest` 未把 G15 纳入（`--selftest` 保持
  G1/G4/G5/G12 原样，未改一行为求最小改动），G15 的负控由本报告 §3.2 独立取证。
