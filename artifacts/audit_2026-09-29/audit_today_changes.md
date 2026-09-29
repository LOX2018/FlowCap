# DYAutoDM_v2 今日改动审计报告（2026-09-29）

> 审计类型：**增量区间审计 + 存量扫描**
> 执行时间：2026-09-29 15:17~15:29 (+0800)
> 审计者：Hermes（本会话）· **严格只读**（未 patch / 未 add / 未升版 / 未写知识库）
> 仓库：`C:\Users\LOX\Desktop\DYchajian` · 分支 `design/better-douyin`

## 0. 审计区间（锚定）

| 项 | 值 |
|---|---|
| 基线 | `2c950fe` (2026-09-28 22:18:06) `fix(bcc): …（v0.45.87）` |
| HEAD | `4f81582` (2026-09-29 14:58:53) `fix(ai-lead): 命中库最短路同受留资/场景护栏（补修 D7）` |
| 提交数 | **9** |
| 净增行 | 66 文件 / **+7208 −254**（净 +6954） |

今日提交（升版序列 v0.45.88 → 0.45.94）：
`2b5c1c7` BCC 身份漂移熔断 → `8c7436e` 直播监听四缺陷+高价值关键词 → `c848c11` 话术专业化 → `0a764c4` 启动防连点+409 文案 → `19ac0c8` 案例归档 → `cc3b07d` 直播首触引导化 → `de526b7` 留资处置铁律 → `d321cd6` DOM 自适应定位+Camoufox 虚拟麦克风 → `4f81582` 命中库护栏补修

## 1. 并发声明（多会话协作 skill 强制）

- `git log --format=%an` 今日作者 **2 个**：`DYchajian Dev`(8) / `LOX`(1)。
- `8c7436e` 提交正文自述「含**两条并行工作线**，来源如实分账」——**另一会话在跑，属实**。
- `19ac0c8` 自述「台账仅提交本会话新增行（**他人未提交的 H-27 行按单写者铁律保留不动**）」——并发处理规范。
- **我不覆盖任何未提交产物**：已发现的他人在制品（下 §4）**原样保留**。

## 2. 验证结果（Live-Instance Verification）

| 验证项 | 命令 | 实测结果 | 判定 |
|---|---|---|---|
| 版本齐平 | `py -3.14 scripts/check_version_sync.py` | **6 处齐平 = 0.45.94**，exit=0 | ✅ |
| 契约门禁 | `py -3.14 scripts/check_contracts.py` | **15/15 PASS**，exit=0 | ✅ |
| 铁律门禁 | `python scripts/check_iron_rules.py` | 11/13 通过；**R9 红线 27/20 已触发** | ⚠️ |
| 今日护栏单测 | `py -3.14 -m unittest test_lead_disposition_guard test_live_contact_guard test_dom_locator_gate test_camoufox_media_prefs test_live_start_no_double_click test_high_value_keywords_entry test_reply_tone_professionalism` | **26/26 OK**（Ran 26，0.516s） | ✅ |
| 全量回归 | `py -3.14 -m unittest discover -s . -p "test_*.py"`（DY_APP_ROOT=临时根） | **1140 tests / 1 failed**（`test_h29 G7` 读真实生产库 22/1057 行未标 kind） | ✅ 与自述一致 |
| 版本源一致性 | 工作区 vs `git show HEAD:` | 4 源一致 = 0.45.94 | ✅ |

> **审计环境坑**：缺 loguru/requests 的解释器会把 `check_contracts` 跑出 **7 处假红**（G1/G4/G5/G8/G9/G10/G14）；用 `py -3.14`（项目运行时）复跑即 **15/15 全绿**。

## 3. 三层分级结论

### 方向层（定性）— ✅ 符合
今日 9 项全部锚定**用户实测反馈原文**（ADR-026/027 §0 引用原话），无一条是「代码内部自嗨」。修复取径一致为「回归设计契约」，符合 Design-First 铁律。

### 机制层 — ✅ 高（**但有一项须降级，见 §10**）
逐条落地且可执行：留资护栏（`_is_lead_stalled`/`append_lead_ask`，AI-070）、场景护栏（AI-069）、本机账号互回拦截（AI-066）、命中库开关（`live_reply_kb_enabled` 默认 False，G6 负控接线）、DOM 自适应定位兜底（`dom_locator.py`）、Camoufox 虚拟麦克风（`vbrowser_camoufox.py`）。

> 🔴 **2026-09-29 16:0x 更正（OCR 交叉对账后）**：本行原列的「**受理≠送达口径拆分（live-page 受理/送达/拒收三档）**」为 **假落地**——
> `tasks.py:140` 取的 `getattr(adm, "account_name", "")` 属性在 `AutoDM` 上**不存在** ⇒ `_acct` 恒空 ⇒
> `delivery_state_of()` 从不被调用 ⇒ 前端 `deliveryState` **恒为空字符串**，「送达率/拒收」永远显示 0。
> 详见 §10 与 §11。
**用户高频踩坑已避**：新增开关默认关闭，且门禁断言的是 `_DEFAULT_CONFIG` 默认值（非生效值）——无持久化覆盖风险（DM 侧 `live_reply_kb_enabled` 为新增键，历史 kv 无此键）。
**提交信息**：9/9 完整套用用户 [Design-Intent / Standard-Contract / Problem-Analysis / Live-Verification / Knowledge-Archival] 模板。

### 结构层 — ⚠️ 两处违反
1. **版本粒度违反**（Version Increment Law）：`4f81582` 是代码修复（修 `_generate_reply` 命中库短路），却**未升版**，与 `d321cd6` **共用 0.45.94**（`--stat` 确认未触碰任何版本源）。⇒ 两个不同的产品行为 change 共用一个版本号，破坏 bisect 粒度，违「每次修改必升 +0.01」。
2. **审计红线已触发未清**（R9，27/20 since `586381e`；今日 `--since 2c950fe` 增量仅 +1）。ADR-018 六项功能扩展 + 红线计数**双重触发**「全库审计 + 功能冻结」，今日 9 笔代码改动是在**冻结窗口内**继续施工。属既有欠账（09-27 起），但今日延续。

## 4. 未提交产物（我不覆盖）

| 项 | 内容 | 判定 |
|---|---|---|
| ` M 工作记忆/00_交接卡待办台账.md` | 第 54 行（H-27 行）被写入**行号前缀 `54|`**，markdown 表格结构被破坏；mtime 09-29 11:44 | 🔴 **格式损坏/并发在制品**，未提交，保留原样 |
| `?? DYAutoDM_v2/backend/data/` | 源树内新建 `data/dyautodm.db`（94KB，09-28） | 🟡 疑似门禁/测试运行副作用，非今日新增 |
| `?? DYAutoDM_v2/data/dyautodm.db` | mtime 15:23（**本审计 check_contracts 运行所致**） | gitignored（`.gitignore:51 DYAutoDM_v2/data/`），非提交内容 |

## 5. 存量扫描维度（增量审看不见的区域）

| 维度 | 读数 | 判定 |
|---|---|---|
| 错误码注册表唯一性 | `errcode_data.py` ERRCODES **383 键全唯一**（"同码两义"= 0）；今日 AI-066 冲突已按规范修（残句→AI-068） | ✅ |
| 今日新增静默兜底 | **4 处 `except Exception: pass`**：`dom_locator.py:95/123`（iterchildren、iterancestors 防御）、`ai_reply.py`新增（Agent 配置读取）、`vbrowser_camoufox.py:96`（cfg 读取）。**均在导入/配置防御路径，非 L1 写库/外发** | 🟡 可接受，建议登记 |
| 静默兜底总存量 | `check_silent_fallback.py`：基数 657 处（基线 386，Δ+271）；**F1 写库 29 / F2 外发 10 处 pass-only（阻断）**；F3 新增 30 处 | 🔴 存量欠账 + 新增超基线 |
| 风控红线（用户最高优先） | 今日 diff 新增行**零**硬编码 UA / credential-reuse / 批量昵称调用 | ✅ |
| 契约治理 | 6 份 C-01~C-06 + C-07 契约文档；G12/G15 符号守护（6/7 条 grep 判据）全命中 | ✅ |

## 6. 主动补的缺失维度（用户未问但必须提）

1. **留资护栏缺「客户明确拒绝」停止条件**：ADR-027 §5 自述「单次追加/替换**不区分客户情绪**（如已明确拒绝留资）—— `max_lead_ask` 只按次数兜底」。默认 `max_lead_ask=2`，但**拒绝后仍会再追加一次**。建议新增「已明确拒绝 ⇒ 本轮不追加」判据——否则「每轮必须推进留资」会与「客户说不需要别再发」冲突，既损体验也增风控面。
2. **R3 磁盘卫生已有漂移**：`backend/build/` 残留 sidecar 构建产物（今日 build 所致）。
3. **契约门禁运行有副作用**：`check_contracts` 会初始化源树内 `data/dyautodm.db` 并发起网络探针（CAP-014，599 降级）——在源树跑的门禁应显式钉 `DY_APP_ROOT`，否则「验证」本身污染工作区。

## 7. 机读错误报告（强制 schema）

```json
[
  {
    "design_intent": {
      "module": "version-source(6处)",
      "design_contract": "每次产品行为变更必须单调 +0.01 并同步全部版本源（Version Increment Law）",
      "expected_behavior": "每个提交对应唯一版本号，可 bisect",
      "assumptions": ["提交 4f81582 是独立的产品行为修复"]
    },
    "observed_deviation": {
      "deviation_type": "data",
      "deviation_point": "commit 4f81582 未触碰 package.json/tauri.conf.json/Cargo.toml/_build_version.py",
      "deviation_from_expectation": "实测与 d321cd6 共用 0.45.94；两个行为 change 共用一号（9 提交仅 7 次升版）"
    },
    "error_code": "MISC-002",
    "severity": "warn",
    "suggested_actions": [{"action_id": "bump-0.45.95-and-resync-6-sources", "automatic": false, "idempotent": true}]
  },
  {
    "design_intent": {
      "module": "工作记忆/00_交接卡待办台账.md",
      "design_contract": "台账是开放事项 SSOT，markdown 表格结构必须合法",
      "expected_behavior": "每行以 | 开头、无行号前缀",
      "assumptions": ["文件为人工/会话维护"]
    },
    "observed_deviation": {
      "deviation_type": "data",
      "deviation_point": "工作区未提交改动，第 54 行（H-27 行）",
      "deviation_from_expectation": "行首被写入 '54|' 前缀，表格结构破坏"
    },
    "error_code": "MISC-003",
    "severity": "error",
    "suggested_actions": [{"action_id": "remove-stray-line-number-prefix", "automatic": false, "idempotent": true}]
  },
  {
    "design_intent": {
      "module": "services/ai_reply.py 留资护栏（ADR-027）",
      "design_contract": "每轮外发必须含留资动作；同时不得对明确拒绝的客户反复索要",
      "expected_behavior": "客户已明确拒绝留资 ⇒ 本轮不追加索要",
      "assumptions": ["max_lead_ask=2 足以防刷屏"]
    },
    "observed_deviation": {
      "deviation_type": "data",
      "deviation_point": "append_lead_ask / _is_lead_stalled 仅按次数判据",
      "deviation_from_expectation": "ADR-027 §5 自述不区分客户情绪；拒绝后仍会追加一次"
    },
    "error_code": "AI-071(待登记)",
    "severity": "warn",
    "suggested_actions": [{"action_id": "add-refusal-aware-stop-condition", "automatic": false, "idempotent": false}]
  },
  {
    "design_intent": {
      "module": "审计红线（D-02，阈值 20 patch 节点）",
      "design_contract": "累计达阈值 ⇒ 功能冻结 + 全库审计 + standards 文档除锈",
      "expected_behavior": "达阈值后停止功能施工直至审计清场",
      "assumptions": ["586381e 为上次全库审计基线"]
    },
    "observed_deviation": {
      "deviation_type": "resource",
      "deviation_point": "scripts/audit_redline_count.py",
      "deviation_from_expectation": "实测 27/20 已触发；今日仍新增 1 个节点并继续施工"
    },
    "error_code": "MISC-004",
    "severity": "warn",
    "suggested_actions": [{"action_id": "run-full-audit-and-reset-baseline", "automatic": false, "idempotent": true}]
  }
]
```

## 8. 验收核对（只读声明）

- [x] 未 patch / 未 write 源码或知识库（仅本报告为新文件）。
- [x] 未 `git add` / `commit` / 升版。
- [x] 他人未提交产物（台账 M、backend/data ??）**原样保留，未 checkout/stash/clean**。
- [x] 每条结论均挂可复跑命令或实测读数，无裸形容词。
- [x] 区间锚定一句话可说清（`2c950fe..4f81582` / 9 提交 / +7208−254）。
- [x] 已含存量扫描维度（错误码唯一性 / 静默兜底存量 / 风控红线）。

## 9. 复核用命令（可复跑）

```bash
cd /c/Users/LOX/Desktop/DYchajian
git log --since='2026-09-29 00:00:00' --date=iso --format='%h|%ad|%an|%s'
git diff --numstat 2c950fe..HEAD | awk '{a+=$1;d+=$2}END{print a"/"d}'
cd DYAutoDM_v2
py -3.14 scripts/check_version_sync.py
py -3.14 scripts/check_contracts.py
py -3.14 scripts/check_iron_rules.py
py -3.14 -m unittest discover -s backend -p "test_*.py"   # 期望 1140/1 failed(基线)
```

---

# §10 OCR 交叉对账（2026-09-29 15:35~15:41）

**引擎**：`open-code-review v1.12.9 (bccbc15f)` · 模型 `deepseek-v4.1-flash`（与 Hermes 顶层一致）
**命令**：`ocr review --from 2c950fe --to HEAD --format json --audience agent --timeout 60 --concurrency 4`
**报告**：`D:/SJ  agent/DYChajian-review-20260929-today.json`（55KB · **status=complete** · 32 文件 · 31 条 · 6m28s · 8.19M tokens）
**severity**：critical 1 / high 3 / medium 13 / low 14 · **抽检 11 条，全部属实（本轮误报率 0）**

> ⚠️ **首轮空跑（已修复）**：ocr config.json 的 API key 尾号 `c0f4` 为旧值，profile `.env` 现用 `a6a0`
> ⇒ 401 Unauthorized、`done=0/failed=32`，报告作废。根因：`sync_ocr_llm.py` 的 `PROF` 路径为旧版
> `~/AppData/Local/hermes/profiles/lox`（缺 CN Desktop 段）⇒ 读不到 `.env` ⇒ 静默保留旧 key。
> 已按凭据铁律「备份 → 合并写 → 读回校验」修复（备份 `config.json.bak.20260929_153450`）。

## 10.1 三栏对账表

### A. 双方一致（我的报告 + OCR 都抓到，= 高置信真实缺陷）
| # | 项 | 我的发现 | OCR 条目 |
|---|---|---|---|
| A1 | 留资护栏 `max_lead_ask` 上限未真正生效 | §6.1（我判「缺拒绝停止条件」） | **[22] HIGH** ai_reply.py:2155 —— **更准确**：`else` 回退分支**无条件** `append_lead_ask`+`_bump_lead_ask`，`_n_ask>=_max_ask` 时仍每轮索要 ⇒ **上限从未生效**（比我的"缺停止条件"更严重） |

### B. OCR 独有（我漏掉，经抽检**属实**）
| # | sev | 位置 | 缺陷 | 抽检证据 |
|---|---|---|---|---|
| **B1** | 🔴CRITICAL | `backend/api/tasks.py:140` | `getattr(adm,"account_name","")` —— `AutoDM` 上**无**该属性（`_acct` 私有、`account_name` 只 set 在 `monitor_auth`/`auth` 上）⇒ `_acct=""` ⇒ `delivery_state_of` **从不被调用** ⇒ `delivery_state` 恒空 ⇒ 前端「送达率/拒收」**恒为 0** | `grep account_name core/auto_dm.py` 仅见 `setattr(self.monitor_auth/auth,…)`；API/console 层唯一写法 vs `core/dispatch.py:438` 的 `auth.account_name` |
| B2 | HIGH | `frontend/.../live-page.tsx:311` | 送达率分子分母口径不一（`deliveryState` 按 uid×会话历史派生 vs `acceptedCount` 按 `dmStatus=='sent'`）⇒ 可 >100% | 读代码：`acceptedCount`/`deliveredCount` 两套口径 |
| B3 | HIGH | `scripts/verify_lead_disposition_real.py:77` | `expect_ask` 标志**对结果无效**（`not stalled == has_ask`）⇒ 第 4 例「有明确问题应先专业回答」**从未被真正断言** ⇒ 脚本给出**假通过** | `_is_lead_stalled = not _has_lead_ask` ⇒ 两分支坍缩为 `ok = has_ask` |
| B4 | MEDIUM | `backend/api/linkmic.py:181` | `async def` 端点内做**阻塞 IO**（`requests.get/post timeout=120`，DOM 流程等 ~40s）⇒ 冻结事件循环 | 读 `_run_linkmic_dom` 全程同步 requests |
| B5 | MEDIUM | `backend/auto_dm/dom_locator.py:136` | exact 模式仅去 ASCII 空格，**NBSP U+00A0** 未归一 ⇒ `"登\u00a0录"` 永不等 `"登录"` ⇒ 兜底静默失效 | 读 `own.replace(" ","")` |
| B6 | MEDIUM | `backend/api/linkmic.py:125` | 点中按钮即**无条件** `out.ok=true`，与「选设备→确定」是否真的完成无关 ⇒ **假成功**（本项目最高频缺陷族） | `grep` 仅 1 处 `out.ok = true`；`result.get("ok")` 即判成功 |
| B7 | MEDIUM | `backend/services/ai_reply.py:1020` | `_lead_ask_count`/`_bump_lead_ask` 对共享 KV 做**非同步 read-modify-write**，跨会话并发（pool=8）丢计数 ⇒ 多发索要 | 读 `_kv_get`→mutate→`_kv_set` 无锁 |
| B8 | MEDIUM | `backend/services/ai_reply.py:2067` | 命中库超长（>~99 字）⇒ `append_lead_ask` 返 None ⇒ **整句丢掉库内专业内容**，替换为通用兜底 | `_LEAD_ASK_HARD_CAP=120` 逻辑 |
| B9 | MEDIUM | `scripts/migrate_fallback_pool.py:165` | 写后读回**无条件**取 `chk["fallback_pool"]` ⇒ 该键缺失/用户自写时 `KeyError` 或验了未迁移的值 | 读 `else: print("缺失⇒不动")` + 后续 `chk["fallback_pool"]` |
| B10 | MEDIUM | `scripts/migrate_fallback_pool.py:97` | `return candidates[0]` 可能返回**不含 `ai_reply_config` 的库** ⇒ `main()` 静默退出 0 报「无需迁移」= **假阴性**（真库没找到） | 读候选回退逻辑 |
| B11 | MEDIUM | `frontend/.../HighValueKeywordsSection.tsx:69` | 保存时非法权重行被 `continue` **静默丢弃**、重词静默合并；toast 报的数量与用户所见不符 | 读保存循环 |
| B12 | MEDIUM | `frontend/.../HighValueKeywordsSection.tsx:85` | 「恢复默认」**无二次确认**，与同页其它破坏性操作（都 `confirm()`）不一致 ⇒ 误点清空自定义 | 读 `reset()`：无 confirm |
| B13 | MEDIUM | `frontend/.../live-page.tsx:1162` | 「仅看异常」过滤（`deliveryState=="rejected"‖dmStatus=="fail"`）与展示（`displayStatus`）**口径不一致** ⇒ 筛出却显示绿色「已送达」 | 读 1164 行 |
| B14 | MEDIUM | `frontend/.../live-page.tsx:309` | 硬编码业务数字自相矛盾：本处「19 条 / **24** 次拒收」vs `live-shared.tsx:88`、`delivery_verify.py:143` 写「19 条 / **34** 条」 | 三处 grep：**24 vs 34 确认不一致** |
| B15–B23 | LOW×9 | 各文件 | 嵌套三元（style×2）· 变更日志式注释 · 死代码 `_norm_conv/_peer_of` · 死 helper `click_adaptive` · 未转义选择器插值 · 死参数 `link_type` · index key 导致删行错位 · 空行静默丢弃 · 空筛选只剩表头 · 取消 prompt 无 toast | 逐条读代码 |

### C. 我报告独有（OCR **未**报，仍成立）
| # | 项 | 说明 |
|---|---|---|
| **C1** | 🔴 **版本粒度违反**：`4f81582` 代码修复**未升版**，与 `d321cd6` 共用 0.45.94 | OCR 的 `review` 语义不做版本合规检查（属项目铁律层），**留在我报告** |
| **C2** | 🔴 **台账表格损坏**：未提交的 `工作记忆/00_交接卡待办台账.md:54` 被写入 `54|` 前缀 | 被 `--exclude '**/工作记忆/**'` 排除；OCR 未覆盖 |
| **C3** | ⚠️ **审计红线 27/20 已触发未清**（R9） | OCR 不查项目自定的红线计数 |
| **C4** | 🟡 门禁运行副作用（`check_contracts` 初始化源树 DB + 网络探针）；R3 构建产物残留 | 属审计动作卫生，见 §4/§6 |
| **C5** | ✅ 风控红线全绿（零硬编码 UA / 凭证复用 / 批量昵称）；错误码 383 键全唯一 | **正向结论，OCR 未触及** |

## 10.2 分诊结论（给修复排期）

| 优先级 | 条目 | 理由 |
|---|---|---|
| **P0** | **B1** + **[22]/A1** | 两条都是「今天宣称已修复、实际功能是死的/护栏未生效」——**假落地**，直接违背用户「验证后才汇报」红线 |
| **P1** | **B3**（验证脚本假通过）· **B6**（连麦假成功）· **B2/B13/B14**（送达口径自相矛盾，即「受理≠送达」用户反馈的核心） | 都在今日交付面上且属「假成功/假口径」族 |
| **P2** | B5（NBSP 使兜底失效）· B4（阻塞事件循环）· B8/B7 · B9/B10（迁移脚本假阴性） | 真实但边界/脚本类 |
| **P3** | B11/B12 及 LOW 易用性项 | 体验与卫生 |

> **本轮 OCR 价值**：抓出 2 处我**漏判的假落地**（B1 送达口径死、[22] 留资上限失效），并**推翻我 §3 的一处正向结论**。符合本项目「审计须含存量/交叉维度」的教训——我原来只做了「代码是否存在」的静态判据，OCR 做了「属性是否真存在」的接线判据。

## 10.3 本轮未做（诚实标注）
- **未逐条派 subagent 甄别全部 31 条**：仅**抽检 11 条**（含全部 critical/high），其余 LOW/MEDIUM 为**读码判定**，未做运行期复现。
- **未做运行期复现**：B4（事件循环冻结）、B6（连麦假成功）、B7（并发丢计数）需真机/多线程实验，本轮仅静态确认，**未给「修复前复现」证据**。
- **B1 未在真机验证**：`AutoDM` 实例属运行期对象，静态 grep 已足够证明「无该属性」，但未起实例实测 `delivery_state` 是否恒空。**判据**：起实例后打 `/api/tasks` 看 `delivery_state` 是否全空。
