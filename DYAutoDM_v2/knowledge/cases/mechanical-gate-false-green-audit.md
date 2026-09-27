# 案例：机械门禁「假绿」体检与修复（G1/G4/G5/G12 + R8 负控）

- **日期**：2026-09-27
- **版本**：v0.45.54 → v0.45.55
- **类型**：调试修复（judgment-level false-green remediation）
- **关联**：ADR-019、体检报告 `artifacts/gate_audit_2026-09-27/体检报告_机械门禁.md`

## 一、问题（设计意图 → 观察偏离）

**设计意图**：机械门禁是「声明式规则不靠自觉」的执行层 —— 它存在的意义是
**对本应拦住的坏状态报红**。门禁常年报绿 **≠** 代码正确；只有「注入坏状态 → 报红」
被实际验证过，才叫真绿。

**观察偏离（全库审计 2026-09-27 发现）**：`backend/test_task_scheduler_gates.py`
7 项判据**全部是源码字符串匹配**，导致同一份代码里两条 P1 真缺陷（元组解包 bug 使
闸门 100% 恒拒；accepted 冒充 delivery_verified）同时存在时，门禁仍 **7/7 全绿**。
→ 「假绿门禁」：给人虚假安全感，比没有门禁更坏。

## 二、调查链（体检方法）

对 `DYAutoDM_v2` 8 个门禁逐条判定 + **注入式证伪**（在 `git archive` 干净副本上
byte-exact 注入真缺陷，看是否报红）：

| 门禁 | 判据数 | 假绿 | 结论 |
|---|---|---|---|
| test_task_scheduler_gates.py（基准） | 7 | 0 | 真绿 |
| test_replay_gates.py | 13 | 0 | 真绿 |
| **check_contracts.py** | 15 | **G1、G12** + G4/G5 弱 | 混合 |
| check_iron_rules.py | 13 | 0（R8 无负控） | 真绿 |
| check_nfr_budget.py | 4 | 0 | 真绿 |
| check_version_sync.py | 6 | 0 | 真绿 |
| check_fingerprint_consistency.py | 12 | 0（A8 降级软判据） | 真绿 |
| check_media_auth_render.py | 3 | 0 | 真绿 |

## 三、根因（RCA）

**根因：判据锚在「源码文本」而非「行为/结构」**。同一根因的四个面孔：

- **G1**：`"bulk_user_info(" in txt` —— 别名 `getattr(a,'bulk'+'_user_info_by_uid')`
  发起真主动查询时不报红。
- **G4**：`("role" and "me") or "回执" in txt` —— "me" 是任意子串，零区分度。
- **G5**：两名字共现即通过 —— 改 `def` 名（调用处保留）仍绿。
- **G12**：① 子串匹配（改名含原串仍绿）；② 判据来源解析为空时 `not []` 恒真通过
  （**静默 SKIP**：判据来源消失 ≠ 通过）。

**装置缺口**：`check_iron_rules --selftest` 的 `failed_expect` 不含 R8 —— 因 R8
委托的 `audit_data_contract.py` 硬编码真仓 `_BACKEND`，临时目录替换对它无效。

## 四、修复（逐项对应）

| 编号 | 修复 |
|---|---|
| G1 | AST 调用点分析（`_capture_outbound_calls`）：识别 `Call` + `getattr` 常量/拼接 |
| G4 | 真调 `delivery_verify._resolve_evidence`，断言「盲 ok 非证据」 |
| G5 | 真 `import link_resolve`：可调用 + 签名≥2参 + `_REFLOW_URL` 指向 reflow/info |
| G12 | ① 词边界 `\b符号\b`；② 空判据集 → 哨兵违例 = FAIL（抽纯函数 `_g12_offenders`） |
| R8 | 抽 `_load_datacontract_module()` seam，selftest 注入负控+正控 |
| 装置 | `check_contracts.py --selftest`（新增，10 条负控/正控） |
| doc-rot | `check_nfr_budget.py` 3 处「零 DB 查」旧文案 → 对齐契约 L85 |

## 五、实机验证（Live Verification）

```
# 基线仍绿（修复不误伤）
$ python scripts/check_contracts.py --quiet        → rc=0
$ python scripts/check_contracts.py --selftest     → rc=0（10/10 负控成立）
$ python scripts/check_iron_rules.py --selftest    → rc=0（含 R8 负控 + 正控）

# 注入式证伪（副本上执行，证明修复有效）
G1  getattr 别名主动查询     → [FAIL] 出站调用点 ['getattr:...@L2146']   ✅
G1  getattr 拼接变体         → [FAIL] ['getattr-concat:...@L2146']        ✅
G4  退化 _resolve_evidence   → [FAIL] 盲ok=True（应 False）               ✅
G5  def 改名（调用处保留）    → [FAIL] _reflow_resolve 可调用=False       ✅
G12 符号改名（含原串）        → [FAIL] 违规 [...is_high_value]             ✅（旧版仅 G14 红）
G12 契约 grep 语法破坏        → [FAIL] 判据来源缺失 ⇒ FAIL                ✅（旧版静默绿）
R8  注入失败子项             → [FAIL] R8-1 写入出口收敛（注入样本）        ✅

# 负控之负控（破任一条修复 → selftest 应报红）
G1 判据退回空集              → ✗ 自检失败：G1 对坏状态不报红              ✅
G12 空判据集退回 []          → ✗ 自检失败：G12 空判据集对坏状态不报红      ✅
G12 词边界退回子串           → ✗ 自检失败：G12 词边界对坏状态不报红        ✅

# 全门禁套件（8 个）
check_contracts / check_iron_rules / check_version_sync /
check_fingerprint_consistency / check_nfr_budget / check_media_auth_render /
test_replay_gates / test_task_scheduler_gates           → 全部 rc=0
```

## 六、教训（可迁移）

1. **「报绿」不等于「正确」**：判据必须验证过「注入坏状态 → 报红」，否则是假绿。
   每个门禁都该配 `--selftest`（负控 + 正控），且 selftest 本身要能被证伪。
2. **字符串匹配是最弱的判据形态**：文本在场 ≠ 逻辑正确。签名/结构/行为三层里，
   行为断言最强，AST 结构次之，字符串最弱（别名、拼接、注释、改名都能绕过）。
3. **「判据来源消失」必须判 FAIL，不是 PASS**：解析到 0 条判据 = 没有覆盖，
   绝不能静默通过（G12 的空 grep 集、`.known-gaps.json` 读不到时同此理）。
4. **委托型门禁的负控要「穿透」被委托脚本的硬编码根**：委托脚本若硬编码扫描根，
   父门禁的临时目录替换对它无效 ⇒ 必须为「加载」留 seam 才能注入负控。
5. **自检装置本身也要被证伪**：只写「负控应报红」不算数 —— 必须实际破坏一条修复，
   确认 selftest **真的**变红（10/10 已验证，避免「元问题假绿」）。

## 七、审计器工作目录（可复现）

`C:\Users\LOX\AppData\Local\hermes\profiles\lox\cache\scratch\gateaudit\` 与
`...\repair\`（含 `harness.py`、`pristine.tar`、`cases_batch*.json`、`results.json`）。
所有注入在副本上做，真实仓库零改动（`git diff --stat` 空）。
