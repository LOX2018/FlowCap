# 内置 Agent 参数面审计 + dispatch Agent 返回值契约违约修复（v0.44.56→v0.44.57）

> 分支 `design/better-douyin` · 基线 v0.44.56（`b1e3f4f`）· 2026-09-24
> 触发：用户问「目前项目中内置的 Agent 是怎样的参数」
> 性质：**只读审计** 衍生出 **1 处真缺陷修复** + **参数面契约化**

---

## 1. 设计意图（Design Intent）

| 项 | 内容 |
|---|---|
| 模块 | `backend/services/ai_agent.py`（Agent 模版层） / `services/ai_reply.py`（参数真源） |
| 设计契约 | ① `ensure_default_dispatch_agent()` 幂等预置，注解声明 `-> dict`；② Agent 参数集 = `_DEFAULT_CONFIG` 键 + `{knowledge_base, blacklist}`；③ 高危权限默认关；④ 绑定键 `ai_account_agent` |
| 预期行为 | 任一调用方按注解取返回值 → 拿到可下发的 Agent dict |
| 设计假设 | 调度 Agent 返回值会被消费（注解即契约）；Agent 参数面稳定可枚举 |

---

## 2. 观测到的偏差（Observed Deviation）

```json
{
  "design_intent": {
    "module": "services.ai_agent.ensure_default_dispatch_agent",
    "design_contract": "注解 -> dict；幂等；返回可下发 Agent",
    "expected_behavior": "返回 dict（含 id/name/kind/config）",
    "assumptions": ["调用方会按注解消费返回值"]
  },
  "observed_deviation": {
    "deviation_type": "data",
    "deviation_point": "ai_agent.py:105 与 :131 两条 return 路径",
    "deviation_from_expectation": "注解 dict，实际返回 tuple('ai_agents', <raw agent>)；raw agent 是 kv 内部形（无 id 字段），与 get_agent() 规范化视图不同形"
  },
  "error_code": "AI-041",
  "severity": "warn",
  "suggested_actions": [
    {"action_id": "unify-return-to-get-agent", "automatic": true, "idempotent": true}
  ]
}
```

**为何当前不炸**：三个调用方（`api/ai.py:452`、`save_dispatch_agent`、`get_dispatch_agent`）
全部忽略返回值 → 缺陷潜伏。**一旦**有人按注解写 `d = ensure_default_dispatch_agent()`，
拿到的是 tuple，且 tuple[1] 是无 `id` 的 kv 内部形，与 `get_agent()` 返回形不一致。

---

## 3. 执行链与根因（RCA）

```
保存路径: data[_DISPATCH_ID] = {...}
   ↓
return _KV_AGENTS, data[_DISPATCH_ID]     ← 手写 tuple，与注解 -> dict 不符
   ↓
调用方忽略返回值（唯二调用点均如此）
   ↓
潜伏：无告警、无测试覆盖返回值类型
```

**根因**：函数**手写返回 tuple**，而模块里已有规范化视图函数 `get_agent()`。
两条 return 路径都绕开了它 → 返回形与模块内其余 API（`get_agent` / `get_dispatch_agent`）
不同形。**不是**逻辑错，是**契约与实现漂移**（Canonical Contract 类缺陷）。

---

## 4. 修复

| 文件 | 改动 |
|---|---|
| `backend/services/ai_agent.py:105` | `return _KV_AGENTS, data[_DISPATCH_ID]` → `return get_agent(_DISPATCH_ID)` |
| `backend/services/ai_agent.py:131` | 同上（新建分支） |

**改动代价**：净 2 行（`git diff --numstat` = `2 2`）。CRLF 计数保持 282 不变，
`--ignore-all-space` numstat 与普通 numstat **逐行相等** → 无 EOL 副作用。
用**字节级脚本**改（遵守交接卡 DY-01 约束：深层嵌套禁用 `patch` 工具）。

**为何不改成 `data[_DISPATCH_ID]`**：那是 kv 内部形，**无 `id` 字段**；
`get_agent()` 才是模块对外的规范化视图（含 `id`/`name`/`kind`/`config`），
与 `get_dispatch_agent()` 返回形一致 —— 修到**同形**而非仅仅「不是 tuple」。

---

## 5. 参数面全景（本次审计产出，事实非推断）

**存储**：kv_store 两键 —— `ai_agents`（模版 dict，id 形如 `ag_<uuid4[:16]>`）、
`ai_account_agent`（账号→Agent 绑定）。调度 Agent 固定 id `ag_dispatch_default`。

**私信 Agent 参数** = `_DEFAULT_CONFIG` 全部键（27）+ `knowledge_base` / `blacklist`：

| 组 | 参数 |
|---|---|
| 主 LLM | `model=glm-5.2`、`base_url=127.0.0.1:31415/v1`、`api_key`、`api_protocol=openai`、`max_tokens=1000`、`temperature=0.7` |
| 视觉 | `vision_enabled=false`、`vision_model=nemotron-3-nano-omni-reasoning`、`vision_prompt`（图述 30 字）、`vision_base_url`、`vision_api_key` |
| 语义 | `sem_enabled=false`、`sem_model=nvidia/nemotron-3-embed-1b`、`sem_threshold=0.40`、`sem_base_url`、`sem_api_key` |
| 人格/获客 | `merchant_name`、`system_prompt`、`strict_level=rag`（kb_only/rag/free）、`lead_confirm`、`max_lead_ask=2` |
| 分类/作用域 | `kind=dm`、`scopes=[dm,live,crawl]` |
| 节奏/兜底 | `min_delay=8`、`max_delay=20`、`max_history=10`、`fallback_pool`(4)、`fallback_image`、`max_reply_len=60`、`forbidden_words`(6，含微信/vx/VX/weixin)、`enabled=false`、`knowledge_first=true` |

**LLM 调优面现状（实测固化）**：`_chat_openai` 组装的 payload **恰好 4 键**
—— `model` / `messages` / `max_tokens` / `temperature`。
**无** `top_p` / `top_k` / `presence_penalty` / `frequency_penalty` / `stop` / `seed`。

**调度 Agent 默认权限**（源码硬编码，非 DB）：
`query_status` / `create_crawl_task` / `create_live_task` / `stop_task` / `update_kb` = **True**；
`create_agent` / `recapture` = **False**（高危默认关）。
可改字段仅 `system_prompt` / `permissions` / `enabled`。

**运行时实库取证**（`C:\temp\dyautodm_design\members\m17db0f8209156f26\data\dyautodm.db`）：
- 1 个真实 Agent `ag_88387f9ee4e34dc1`「工伤赔偿留资助手（唐律）」：
  `max_tokens=1000`、`temperature=0.7`、`strict_level=rag`、`merchant_name=唐律工伤团队`、
  scopes 全开、**`enabled=false`**（保守先建不启）
- 绑定：尚进工伤小助理 → 该 Agent
- **dispatch Agent 尚未实例化**（kv 中无 `ag_dispatch_default`），首次调
  `/api/ai/dispatch_agent` 才预置

---

## 6. 实机验证（Live Verification）

| 项 | 命令 | 结果 |
|---|---|---|
| 新契约脚本 | `python backend/scripts/verify_ai_agent_contract.py` | RED **40 PASS / 7 FAIL** → GREEN **48 PASS / 0 FAIL** |
| 回归·直播可观测性 | `python backend/scripts/verify_live_ai_observability.py` | **31/31**（未改相关代码，零破坏） |
| 回归·Agent 单测 | `python backend/test_ai_agent.py` | **14 tests OK** |
| 编译 | `python -m py_compile backend/services/ai_agent.py` | OK |
| 净改动 | `git diff --numstat` vs `--ignore-all-space` | `2 2` vs `2 2` —— **相等**，无 EOL 污染 |

**RED→GREEN 明细**：首次运行 L2 两条 `返回 dict` FAIL（实际 tuple），
修复后转 PASS；L3 另 5 条 FAIL 是**脚本自身调用错签名**（`chat(message, user_id, system_prompt, history_extra)`，
我误写成 `chat(user_id, message, history=...)`），修正调用后转 PASS —— **非被测代码缺陷**，
据实记录，不粉饰。

---

## 7. 遗留风险（**未修**，需用户决策）

### R-1 🔴 API Key 明文落库
`ai_reply_config` 与 `model_hub` 中 `api_key` 均为**明文**（实库可见
`freellmapi-c912…`）。kv_store 是 sqlite 明文表 → **拷贝/导出库 = 泄密钥**。

**为何现在提出**：这是**存储格式**问题。若日后 Agent 扩字段（加 top_p 等）
再补加密层，会是一次**不兼容迁移**，成本比现在高一个数量级。

**建议**：决定统一密钥保管方式（系统钥匙串 / 环境变量 / 加密 kv），
在扩参数面**之前**落地。**本会话未动，等拍板。**

### R-2 🟡 调优面窄
只有 `max_tokens` / `temperature` 两旋钮。要个性化 Agent 语气/稳定性，
需先扩契约（`_DEFAULT_CONFIG` + 前端表单 + payload 组装三处同步）。
属**契约变更**，需走变更评审（Canonical Contract Law）。**本会话未动。**

---

## 8. 教训

1. **注解就是契约，但没人验证**：类型注解 `-> dict` 与实际 tuple 共存数月无告警，
   因为全部调用方都不消费返回值。**判据**：凡有返回值契约的函数，
   必须有脚本断言其类型——不看调用方当前用不用。
2. **返回形要同形，不只是「类型对」**：改成 `data[_DISPATCH_ID]` 也满足 `dict`，
   但那是无 `id` 的 kv 内部形；应复用模块已有的规范化视图 `get_agent()`。
3. **参数面要有可执行契约**：27 个键此前只存在于源码。本次固化成
   `verify_ai_agent_contract.py`，后续漂移会被红灯拦住。
4. **RED 阶段的失败要分类**：本轮 7 条 FAIL 里 5 条是**测试脚本自身写错**，
   2 条是**真缺陷**。不先把两者区分开就动手改代码，会去改好的代码。

---

## 9. 相关提交与产物

- 修复：`backend/services/ai_agent.py`（2 行）
- 新增：`backend/scripts/verify_ai_agent_contract.py`（48 断言）
- 新增：`knowledge/cases/ai-agent-config-contract.md`（本文件）
- 版本：0.44.56 → 0.44.57（6 处版本源，`check_version_sync.py` 校验）
