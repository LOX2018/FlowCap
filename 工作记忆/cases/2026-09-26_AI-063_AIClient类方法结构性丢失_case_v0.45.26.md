# 2026-09-26｜AI-063 结构性回归：AIClient 类方法被误嵌为模块级函数内嵌套（v0.45.26）

> 关联：**H-19 第二半闭环**（`llm_no_json` 根因）· **H-16 零 Agent 门禁** · ADR-013 截断修复（引入者）
> 案例性质：**结构性回归**（非业务逻辑错误 —— 代码语义未变，Python 缩进解析层改变结构）
> 严重级：🔴 **致命**（AI 回复 / 图片描述 / 连接测试**全链断裂**，且静默无报错）

---

## 1. 设计意图（Step 1：回归设计意图）

| 项 | 内容 |
|---|---|
| **模块** | `backend/services/ai_reply.py` → `class AIClient` |
| **设计契约** | `AIClient` 必须对外提供 8 个方法：`__init__` / `chat` / `_chat_openai` / `_chat_anthropic` / `chat_failover` / `describe_image` / `describe_image_failover` / `test_connection` |
| **预期行为** | `chat()` 内部通过 `self._chat_openai(...)` / `self._chat_anthropic(...)` 调用私有协议实现；`chat_failover` 供 KB 提纯 / 直播生成调用；`describe_image*` 供图片描述；`test_connection` 供设置页「测试连接」 |
| **设计假设** | ① 类方法的**缩进层级即其归属**（Python 语义）；② 模块级 `def` 之后紧跟的 4 空格 `def` 是**该模块级函数的嵌套函数**，不是类方法 |

---

## 2. 当前状态偏差（Step 2）

| 项 | 内容 |
|---|---|
| **偏差类型** | **resource（结构/归属）** —— 非数据、非时序 |
| **偏差点** | `ai_reply.py` 第 916~985 行插入 3 个**模块级**函数（`_is_reasoner` / `_eff_max_tokens` / `_extract_reply`），**位置错误**：它们被插在 `AIClient.chat` 之后、其余 6 个类方法之前 |
| **偏差表现** | 运行期 `AIClient` 只剩 `__init__` / `chat`；其后的 6 个 4 空格缩进 `def` 被 Python 解析为 `_extract_reply` 的**嵌套函数**（而非类方法） |
| **实测 delta** | 修复前：`chat()` 抛 `AttributeError: 'AIClient' object has no attribute '_chat_openai'` → 返回 `None`；修复后：`chat()` 返回真实文本 `'收到'`，`test_connection() = (True, '你好！…')` |

---

## 3. 执行链追踪（Step 3）

```
[现象] AI 回复为空 / KB-101 提纯 llm_no_json / 图片描述无信息 / 设置页测试连接失败
   ↓
[入口] AIClient(...).chat(...)                       # chat 是唯一幸存的类方法
   ↓
[断点] services/ai_reply.py:902  self._chat_openai(cfg, messages)
   ↓
[根因] AttributeError: 'AIClient' object has no attribute '_chat_openai'
   ↓
[结构根因] AST 判定：_chat_openai / _chat_anthropic / chat_failover /
          describe_image / describe_image_failover / test_connection
          6 个 def 的父节点 = 模块级函数 `_extract_reply`（非 class AIClient）
   ↓
[引入点] git blame + 各提交结构对照（AST）：
         7c5a378^  → AIClient 含全部 8 方法 ✅
         7c5a378   → AIClient 只剩 2 方法，6 方法被吞 ❌
```

**引入提交**：`7c5a378` —— `fix(ai,schema): ADR-013 截断回复不得外发 + extra 类型契约修复（v0.45.7）`（2026-09-26 03:22:51）。

**归因（诚实的因果澄清）**：ADR-013 的目标是「截断回复不得外发」，本身**正确**（`max_tokens=1000` 时 deepseek-v4.1-flash 在复杂题下 `finish_reason=length`）。缺陷**不是**它的业务逻辑，而是**插入位置**：新增的 3 个模块级函数被放在类方法之间，破坏了 Python 的缩进归属语义。**HEAD、HEAD~1、7c5a378 三处结构一致 ⇒ 该缺陷自 v0.45.7 起持续存在，且未被任何一次性测试捕获。**

---

## 4. 根因分析（Step 4：RCA）

**为什么这条链在此节点断裂？**

Python 的类方法归属**只由缩进决定**，与「它看起来像谁的方法」无关。`def _extract_reply(...)` 是**模块级**函数（0 缩进），其函数体（4 缩进）中出现的 `def _chat_openai(self, ...)` 自然是**它的嵌套函数**。

⇒ 只要在「最后一个类方法」与「其后仍是类方法」之间插入**模块级 def**，其后所有类方法都会被静默吞掉。**语法完全合法、`py_compile` 通过、静态 grep 也照样能搜到 `def _chat_openai(self`** —— 所以：

- ❌ 代码审查靠「肉眼看到 `def _chat_openai(self, cfg...)` 存在」会误判为「方法在」
- ❌ `grep -c "def _chat_openai"` 命中 ≠ 它是类方法
- ✅ 唯一可靠判据 = **AST 结构断言**（父节点是否为 `ast.ClassDef`）

**为何长时间未暴露？** ① 该缺陷不报错（属性访问才炸，且被 try/except 收敛为 None / 静默回落）；② 下游把「AI 无输出」解释为「LLM 输出格式问题」（H-19 的 `llm_no_json` 一度被如此归因）；③ 无任何**结构性**测试（只测行为，不测归属）。

---

## 5. 实机验证（Step 5：Live-Instance Verification）

**环境**：`DY_APP_ROOT=C:\temp\dyautodm_design`（设计分支数据根）· 真网关 `127.0.0.1:31415` · Python 3.11

### 5.1 结构修复前后对照（AST + 运行期）

| 判据 | 修复前 | 修复后 |
|---|---|---|
| `AIClient` 方法数 | 2（`__init__`,`chat`） | **8**（全齐） |
| `_extract_reply` 内嵌套 def | 6 个（含全部类方法）❌ | **无** ✅ |
| 模块级函数 `_is_reasoner`/`_eff_max_tokens`/`_extract_reply` | 在（但位置错误） | 在（位置正确） |
| `AIClient._chat_openai` 存在 | False | **True** |
| `AIClient.chat_failover` 存在 | False | **True** |
| `chat()` 真机返回 | `None`（AttributeError） | **`'收到'`** |
| `test_connection()` 真机返回 | 失败 | **`(True, '你好！请问有什么我可以帮你的吗？')`** |

### 5.2 H-19 第二半闭环（`llm_no_json` 根因）

- **`chat_failover` 真机调用**（消费者 `kb101_livetest`）：修复前**方法不存在**；修复后返回 `[{"q": "测试"}]`。
- **KB-101 端到端**（`reply_kb.learn_from_history(account='四川工伤张老师', limit=200)`）：
  - 修复后实测 `{"ok": true, "reason": "", "scanned": 26, "extracted": 60, "added": 0, "purified": true, "message": "LLM 提纯成功，新增 0 条"}`
  - ⇒ 原 `reason: 'llm_no_json'` 的**上游断点**（`chat_failover` 不存在 → 提纯链整体失败）**已消除**，链路恢复为 `purified=True`。

> **诚实标注（时间线）**：H-19 登记于 **2026-09-25**，早于回归引入（**2026-09-26 03:22**）。故 H-19 当时报告的 `llm_no_json` 与本次结构回归**不是同一诱因**；本次修复**消除了该路径未来唯一的必然失败点**，并使 KB-101 全链恢复 `purified=True`。二者在**现象面**（KB 提纯失败、AI 无输出）同源，在**根因面**须分开陈述。

### 5.3 H-19 第一半：巡检 `unknown=6` 逐项归因

实机 `probe.run_probes()`（账号 `四川工伤张老师` + `尚进工伤小助理`，12 项）：

```
summary = {'healthy': 5, 'degraded': 0, 'failed': 1, 'unknown': 6, 'total': 12}
```

| # | capability | account | state | 判定源（为何如此） | 性质 |
|---|---|---|---|---|---|
| 1 | `conversation_capture` | 张老师 | unknown | 最近一次写库 `with_browser=0`（recv_daemon 启动前移捕获，**按设计不抓昵称**）⇒ uid 关联=0 属**预期**；窗口内无带浏览器捕获作证 → 昵称能力**本次未定论** | ✅ 按设计正确 |
| 2 | `live_danmaku` | 张老师 | unknown | 近 24h 无属于本账号的 `[弹幕]` 记录（**窗口内未开播监听**）→ 无法判定，**不得报 healthy** | ✅ 按设计正确 |
| 3 | `ai_lead_capture` | 张老师 | unknown | 留资 6 条（联系方式有效 6）；近 72h AI 相关日志行 0 条 → 有数据但无 AI 活动证据 | ✅ 按设计正确 |
| 4 | `conversation_capture` | 小助理 | unknown | 同 #1（按设计不抓昵称） | ✅ 按设计正确 |
| 5 | `live_danmaku` | 小助理 | unknown | 同 #2（未开播） | ✅ 按设计正确 |
| 6 | `ai_lead_capture` | 小助理 | unknown | 同 #3 | ✅ 按设计正确 |
| 附 | `credential_identity` | 张老师 | **failed** | `UID 漂移（AUTH-050）`：`.env` 落盘 uid=`3119773958541104` ≠ 库内历史本号 uid=`3887506227210423` | 🔴 **已登记待办（H-27 / H-30）**，非新缺陷 |

**结论**：`unknown=6` **全部为「按设计正确 unknown」**（诚实三态：无证据 ⇒ 不报 healthy），**不是缺陷**。`failed=1` 是已登记的凭证过期（H-27①，须用户重新扫码），属**已知项**。

---

## 6. 修复与门禁（Step 6）

### 6.1 修复（字节级，保留 CRLF）

文件全 CRLF（1992 CRLF / 0 bare LF）。用**字节级**重排（不用 `patch` 工具，避免 EOL 规范化）：

- 取出 3 个模块级函数（`_is_reasoner` / `_eff_max_tokens` / `_extract_reply`）与其后的 6 个类方法块；
- 调整顺序为：`…chat 结尾` → **6 个类方法** → 3 个模块级函数 → 原有尾部；
- 边界用**内容定位**（非行号），并加结构断言防止错切（首次行号版被断言拦下，未写入）。

### 6.2 机械门禁（防复发，含负控）

新增 `backend/test_ai_client_method_structure.py`（**AST 结构断言**，非 grep）：

| 断言 | 内容 |
|---|---|
| G1/G2 | `AIClient` 具备全部 8 方法，且 6 个曾丢失者均为**类方法**（AST body 内） |
| G3 | 任一模块级函数**不得**把类方法吞成嵌套 def |
| G3b | **负控自证**：构造旧形态源码 → 断言必须变红（证明门禁非空转） |
| G4 | `_is_reasoner` / `_eff_max_tokens` / `_extract_reply` 仍是模块级 |
| G5 | 运行时 `hasattr` 自证（AST 与 import 结果一致） |

**破坏性验证（证明它真会拦）**：用 `git show HEAD:` 旧形态覆盖 → 门禁 **3 项失败**（G1/G2、G3、G5）；
恢复修复版 → **5/5 OK**。

### 6.3 H-16 零 Agent 门禁（本会话同批交付）

- `core/auto_dm.py`：`evaluate_live_ai` 在「未绑定 Agent」时**显式否决**返回 `reason_code=no_agent`（原静默回落全局配置）。
- `api/ai.py` / `core/auto_dm.py`：`reason_code` 文档串增 `no_agent`。
- `scripts/verify_live_ai_observability.py`：新增 **正控**（未绑定 → `no_agent`）+ **负控**（已绑定 → 不得再报 `no_agent`）。探针 **35/35 通过**。
- `test_live_ai_wiring.py`：旧用例 `test_unbound_agent_uses_global_config` 按**新契约**改写为 `test_unbound_agent_not_wired_no_agent`（旧的「未绑定→用全局」契约**已被用户显式推翻** = superseded，非回归）。

### 6.4 验收读数

| 项 | 结果 |
|---|---|
| 全量回归 | **922 tests**；failures=5 / errors=7 = **12 项，与基线（干净 HEAD）逐条 diff 完全一致** ⇒ **零新增回归** |
| 基线对照法 | 同目录 `git stash` 取基线（避免 worktree 的模块身份冲突） |
| 新增结构门禁 | **5/5 OK**；破坏性验证变红 3 项 |
| H-16 探针 | **35/35 PASS**（含新增正/负控） |
| 契约门禁 | **G0~G14 全 PASS** |
| 铁律门禁 | **12/12 PASS** |
| 版本门禁 | **6 处齐平 0.45.26** |

> **基线 12 项既有债（非本批引入）**：`test_kernel_availability_probe`×6（Camoufox 内核不可用，BCC-070 / H-14）、`test_replay_gates`×1、`test_replay_capture_parse`×2（回放样本漂移）、`test_ai_agent`×2（顺序相关假失败）、`test_no_loguru_printf_style`×1。
> ⚠️ **诚实标注**：`test_ai_agent` 的 2 项**单跑 14/14 全绿**（顺序相关既有债，M-12 型），且 `ai_agent.py` 本批**未改动**。

---

## 7. 教训（写入反面教训清单）

1. 🔴 **「grep 到 `def foo(self` ≠ 它是类方法」**：Python 的类方法归属**只由缩进/AST 决定**。凡涉及「类结构完整性」的验证，必须用 **AST**（父节点是否 `ast.ClassDef`），grep / 肉眼看缩进都不算判据。
2. 🔴 **在类方法之间插入模块级函数 = 静默吞掉其后所有类方法**：语法合法、`py_compile` 通过、无任何报错。**新增模块级函数必须放在整个 class 之后**（或 class 之前），绝不可插进类体中间的缩进块之间。
3. 🔴 **「静默失效」是本项目最贵的一类债**（与 H-24 WP 通道同型）：代码在跑、无报错、无人知晓；下游把「无输出」误解释为「上游格式问题」（本例 `llm_no_json`）。
4. ✅ **基线对照必须同目录**：`git worktree` 隔离基线会产生**模块身份冲突**（`test_capability_probe` 从主工作区被导入）⇒ 全量 discover 无法运行。正确做法 = 同目录 `git stash` 取基线，再 `pop` 恢复。
5. ✅ **破坏性验证必须做**：门禁只跑绿不算数；把事故形态**注入**后必须变红，才算「真门禁」（本例旧形态覆盖 → 3 项变红）。

---

## 8. 归档元数据

| 项 | 值 |
|---|---|
| 版本 | v0.45.25 → **v0.45.26**（debug patch +0.01，六处齐平） |
| 缺陷编号 | **AI-063**（AIClient 类方法结构性丢失） |
| 引入提交 | `7c5a378`（v0.45.7，ADR-013，2026-09-26 03:22） |
| 修复文件 | `backend/services/ai_reply.py`（结构重排，字节级）/ `backend/core/auto_dm.py` / `backend/api/ai.py` / `backend/scripts/verify_live_ai_observability.py` / `backend/test_live_ai_wiring.py` |
| 新增文件 | `backend/test_ai_client_method_structure.py`（AST 结构门禁，5 项含负控） |
| 关联待办 | H-19（第二半闭环）· H-16（零 Agent 门禁交付） |
| 关联案例 | `2026-09-26_凭证3小时失效_Camoufox内核与Chrome档案层矛盾_case_v0.45.10.md` |
