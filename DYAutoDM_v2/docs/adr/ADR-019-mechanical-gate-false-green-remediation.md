# ADR-019：机械门禁「真绿/假绿」体检后的四条判据强化 + 两处自检装置补齐

- **状态**：已接受（Accepted）
- **日期**：2026-09-27
- **版本**：v0.45.54 → **v0.45.55**（调试修复 · patch +0.01）
- **相关**：ADR-012（数据契约）、`knowledge/cases/mechanical-gate-false-green-audit.md`、
  体检报告 `artifacts/gate_audit_2026-09-27/体检报告_机械门禁.md`

## 背景

2026-09-27 对 `DYAutoDM_v2` 的 8 个机械门禁做了一轮「真绿/假绿」体检（判据逐条定性
+ 注入式证伪试验）。结论：7 个真绿、1 个（`scripts/check_contracts.py`）混合——
存在 **2 条判据级假绿**（G1、G12）与 **2 条弱判据**（G4、G5），另有
`scripts/check_iron_rules.py --selftest` 对 **R8 六项从未负控**。

「假绿门禁」= 门禁存在、常年报绿，但对**本应拦住的真缺陷**不报红 —— 它给人虚假
安全感，比没有门禁更坏（与 2026-09-27 全库审计发现的 `test_task_scheduler_gates.py`
7/7 全绿却藏两条 P1 缺陷同型）。

## 决策

### D1. G1：字符串匹配 → AST 调用点分析 → **运行时出站拦截**（三段演进）
- **v1 原判据**：`[s for s in ["bulk_user_info(", ...] if s in txt]`（源码子串）。
  **失效证据**：注入 `getattr(_a, "bulk"+'_user_info_by_uid')(...)` 发起**真实主动
  批量查询**（风控红线）→ **不报红**。
- **v2 中间方案（AST 调用点，已被取代）**：`_capture_outbound_calls()` 用 `ast` 识别
  `ast.Call` 函数名 + `getattr(obj,'常量')` + `getattr(obj,'a'+'b')` 拼接折叠。
  **仍不够**：它是**静态**判据 —— 拦不住「函数名由字符串拼出后再动态调用」
  （如 `getattr(requests, 'post')(host+path)` 而 `host`/`path` 也是拼接），且**不覆盖
  「出站目标是不是抖音域」这一真正红线**（它只证明「某符号未出现」）。
- **v3 现方案（运行时行为断言）**：`_probe_capture_outbound()` 装 in-process `requests`
  拦截器，**真调** `capture_userinfo_via_browser`（C-01 风控红线的核心路径），断言
  发起的所有出站 URL **全部**是本地 BCC 网关（`127.0.0.1`/`localhost`），且**零**个指向
  抖音业务域（`douyin.com`/`amemv.com`/`snssdk.com`…）。判据对象 = 「运行时到底往哪发」
  ⇒ 别名 / 拼接 / 动态取名 / 间接调用全部无处遁形。
  **实测**：基线下出站 2 次全为本地、抖音 0 次；注入「拼域名 + getattr 动态调用」→ 报红。

### D2. G4：零区分度字符串 → 行为断言
- **原判据**：`("role" in txt and "me" in txt) or "回执" in txt`（"me" 是任意子串，
  `message`/`time` 全命中，近似零区分度）。
- **改法**：真调 `services.delivery_verify._resolve_evidence`（纯函数、无 DB/网络），
  断言契约不变式 Ⅰ1：无 `server_message_id` ⇒ 非证据(False)；有 ⇒ True；8610 ⇒ False。

### D3. G5：两名字共现 → 引擎可用于断言
- **原判据**：`"_reflow_resolve" in txt and "reflow/info" in txt`。
- **失效证据**：把 `def _reflow_resolve` 改名成 `_reflow_resolve_v2`（调用处保留）→
  旧判据仍绿。
- **改法**：真 `import link_resolve`，断言 `_reflow_resolve` 可调用 + 签名≥2参 +
  `_REFLOW_URL` 指向 `reflow/info`。

### D4. G12：双重假绿 → 词边界 + 判据来源缺失即 FAIL
- **① 子串匹配**：`is_high_value` 改名成 `is_high_value_renamed`（含原串）→ 旧判据
  仍绿。改法：`\b<符号>\b` 词边界精确匹配（抽出纯函数 `_g12_offenders`）。
- **② 静默 SKIP**：契约 §6 grep 行格式破坏 ⇒ `_grep_specs` 返回空 ⇒ `not []` 恒真通过。
  改法：**空判据集返回哨兵违例** ⇒ FAIL（判据来源缺失 ≠ 通过）。

### D5. R8 自检装置补齐（seam 注入）
- **问题**：`r8_data_contract()` 委托的 `audit_data_contract.py` 硬编码真实 `_BACKEND`，
  selftest 的临时目录替换对它无效 ⇒ `failed_expect` 不含 R8 ⇒ R8 六项无负控。
- **改法**：抽出 `_load_datacontract_module()` seam（与既有 `_load_redline_module` 同法），
  selftest 用替身模块注入「R8 子项报红」负控 + 「子项全通过」正控。

### D6. 给 `check_contracts.py` 新增 `--selftest`（此前无任何内置负控）
- 10 条负控/正控：G1（别名/拼接/干净）、G4（盲ok/真msg_id）、G5（可调用/签名/端点）、
  G12（词边界/精确在场/空判据集）。全部真调判据函数，不靠阅读推断。

### D7. 订正 `check_nfr_budget.py` 的 doc-rot 文案
- 契约 `C-06 §5 L85` **已**订正为「**非**零 DB 查：稳态每次 2 次 `kv_store` 读」，
  但 NFR 脚本内仍有 3 处旧文案（"纯缓存读，零 DB 查"/"零 DB 查旁证"）与之矛盾。
  统一改为与契约 L85 一致，消除 SSOT 冲突。

## 后果

- **正面**：4 条判据从「文本匹配」升级为「行为/结构断言」，2 条判据级假绿消除；
  2 处自检装置缺口补齐（R8 有负控，`check_contracts` 有 selftest）；1 处 doc-rot 消除。
- **代价/约束**：G1 的 AST 方案仍非运行时行为（是静态结构分析）——它拦得住别名/拼接，
  但若将来出现**动态**（如从配置读函数名再 `getattr`），仍需升级为「出站请求计数器」
  的纯运行时断言。已记为后续项。
- **回归防线**：`--selftest` 已自证（破任一条修复 → selftest 报红，10/10 负控有效）。

## 未纳入本次（如实登记）

- G13「代码键 ⊆ 契约」反向断言（体检未复现假绿，属增强项，非缺陷）。
- `check_contracts.py` 的 `.known-gaps.json` 豁免机制未构造命中场景。
- 其余 ~15 个 `backend/test_*gate*.py` / `test_*guard*.py` 未纳入（抽查为功能测试，
  非门禁定义脚本）。
