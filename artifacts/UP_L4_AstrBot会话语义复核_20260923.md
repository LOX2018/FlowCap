# UP_L4 上游 AstrBot「分离 reset 与 new conversation 语义」复核

> 任务：复核 AstrBot `fix: separate reset and new conversation semantics (#10118)` 是否影响本项目 IM 网关（`backend/notify/`）的会话语义。
> 复核日期：2026-09-23　　HEAD：`9b4b588`（v0.44.52）　　性质：**只读取证**（无任何写入本仓库文件）
> 依据铁律：Open-Source Provenance（锚定上游 sha/PR）+ Design-First（禁无证据静态改动）

---

## 〇 结论

**判定：无需改（本会话语义不受 #10118 影响）。**

- 上游 #10118 改的是 **AstrBot 自身的 `ConversationManager` + Agent Runner 行为**：`/reset` 与 `/new` 两条**框架级指令**对「会话上下文 / 会话记录」的处置分家；**没有触及 UMO（`unified_msg_origin`）标识的构造规则**。
- 本项目 `backend/notify/` 从未实现 AstrBot 的 UMO / `conversation_manager` / `/reset`·`/new` 指令体系（全仓检索为空，见 §二·证据），因此**不存在依赖旧语义的代码**。
- 本项目 IM 网关的「会话标识」= `sender_key = f"{channel_id}:{sender_id}"`（协议层来源键）+ iLink `context_token`（协议层回推令牌），二者均与 AstrBot 的「对话上下文生命周期」**正交**。
- **若不改的后果：无会话语义层面后果**（无迁移、无兼容风险）。
- ⚠️ 复核中**顺带发现一个独立于 #10118 的本项目缺陷**（IM 侧「回复『确认』执行」承诺未兑现），详见 §四·建议动作 R2。该缺陷由本项目源码自身可证，**与上游 #10118 无因果关系**，不应并入本次上游追踪结论。

---

## 一 上游改动事实（附 sha/PR 链接）

### 1.1 目标提交

| 项 | 值 |
|---|---|
| PR | [#10118 fix: separate reset and new conversation semantics](https://github.com/AstrBotDevs/AstrBot/pull/10118) |
| 合并提交 sha | `3ada8aa0aed711f3277fb8ed4f31864a1fefa0f2` |
| 合并时间 | 2026-09-21T08:22:39Z |
| 作者 / 评审 | w31r4 / RC-CHN |
| 规模 | +255 −59，共 **5 个文件** |
| 父提交 | `8b5e24ba2eac3375a0e17bdf3f0ea790eb91de15`（= #10163 的合并提交） |
| Refs | [#10114](https://github.com/AstrBotDevs/AstrBot/issues/10114) |
| commit 历史 | 提交信息含「apply → revert(e24f5ff) → reapply(3fa0477)」三段，最终**生效的是分离语义** |

改动文件（GitHub commits API 实测）：

```
astrbot/builtin_stars/builtin_commands/commands/conversation.py   +34 −6
astrbot/builtin_stars/builtin_commands/main.py                   +3  −11
docs/en/use/command.md                                           +10 −10
docs/zh/use/command.md                                           +10 −10
tests/unit/test_conversation_restart.py                          +198 −22
```

### 1.2 到底改了什么（diff 实证）

**(a) 指令入口分家** —— `main.py`：

```python
# 改前：/reset 直接复用 new_conv
async def reset(self, message):
    """Start a new conversation, keeping previous history."""
    await self.conversation_c.new_conv(message)

# 改后：/reset 走独立实现；/new 保持 new_conv
async def reset(self, message):
    """Clear the context of the current conversation."""
    await self.conversation_c.reset(message)
```

**(b) 新增 `conversation.py::reset()`** —— 核心语义（原文关键行为）：

```python
umo = message.unified_msg_origin
agent_runner_type = cfg["agent_runner"]["runner_type"]
active_event_registry.stop_all(umo, exclude=message)
cid = await self.context.conversation_manager.get_curr_conversation_id(umo)
if agent_runner_type in THIRD_PARTY_AGENT_RUNNER_KEY:
    await _clear_third_party_agent_runner_state(...)      # 清远端 runner 上下文
else:
    if cid:
        await self.context.conversation_manager.update_conversation(
            umo, cid, history=[],                         # 只清历史，保留 cid
        )
message.set_extra("_clean_group_context_session", True)
message.set_result("✅ The current conversation context has been cleared.")
```

**(c) `new_conv()` 收口**：`active_event_registry.stop_all(...)` 前移到第三方分支之外（两条路径都停任务），第三方分支不再提前 `return`，统一走到 `conversation_manager.new_conversation(...)` 建**新**会话（旧本地记录保留）。

**(d) 文档语义重写**（`docs/zh/use/command.md`）：

- 改前：`/reset`：与 `/new` 一样，创建并切换到新对话；
- 改后：`/reset`：**清空当前对话的上下文**（保留对话 ID / 标题 / Persona / Token 统计）；
  `/new`：**创建并切换到一个新对话**（保留旧对话记录）。

**(e) 回归测试**（`test_conversation_restart.py`）把「两条指令走同一路径」的断言改为分叉断言：

```python
if entry == "reset":
    restart.manager.update_conversation.assert_awaited_once_with(umo, "old-id", history=[])
    restart.manager.new_conversation.assert_not_awaited()
else:
    restart.manager.new_conversation.assert_awaited_once_with(umo, "qq", persona_id="persona")
    restart.manager.update_conversation.assert_not_awaited()
```

### 1.3 关键判读

- #10118 **只改「对话（conversation）记录与上下文」的生命周期**，作用对象是 `conversation_manager` 的 `curr_conversation_id` / `history` / 第三方 runner 远端 thread。
- **diff 全部 5 个文件均非 UMO 构造代码**；`umo` 在改动中只作为**入参键**被读取（`get_config(umo=umo)` / `get_curr_conversation_id(umo)`），其**字符串格式与生成规则未被触碰**。
- 因此：#10118 **不构成 UMO 标识格式（或语义）的破坏性变更**。

### 1.4 同批另两条（按要求一并判定：均与本项目无关）

| PR | 合并 sha | 内容 | 本项目相关性 |
|---|---|---|---|
| [#10163](https://github.com/AstrBotDevs/AstrBot/pull/10163) respect session plugin filters in cron agents | `8b5e24b` | `CronMessageEvent.plugins_name` 从 session 的 `plugin_set` 初始化（禁用插件不再跑 hook / 暴露 LLM 工具） | **无关**：本项目无 cron agent、无插件过滤体系 |
| [#10156](https://github.com/AstrBotDevs/AstrBot/pull/10156) read WebUI versions from the served asset directory | `95e98b8` | WebUI 版本读取改为以「实际服务的 asset 目录」为准，避免读陈旧 `data/dist` | **无关**：本项目无 AstrBot WebUI 版本/更新检查模块 |

---

## 二 本项目 UMO 实现现状（file:line）

### 2.1 事实一：本项目**没有** AstrBot 的 UMO / 会话上下文体系

全仓（`backend/`、`frontend/src/`）检索 `unified_msg_origin`、`umo`、`conversation_manager`、`new_conversation`（IM 会话语义）、`/reset`、`/new`：**零命中**。

`backend/notify/inbound.py` 全文件检索 `reset` / `new_conv` / `new_conversation` / `umo` / `清空` / `重置`：**零命中**（该文件 424 行，只做渠道长轮询 + 扫码登录 + 消息派发）。

### 2.2 事实二：本项目的「会话标识」只有两层，且都是协议层

**层 1 —— 来源键 `sender_key`（网关授权 / 去重口径）**

```python
# backend/notify/gateway.py:55-57
def sender_key(channel_id: str, sender_id: str) -> str:
    """渠道内唯一的发送者键。"""
    return f"{channel_id}:{sender_id}"
```

```python
# backend/notify/inbound.py:139          ← 唯一的「会话语义-ish」标识注入点
meta["sender_key"] = gateway.sender_key(channel_id, sender_id)
```

- `sender_key` 用途**仅为**授权/权限归属（`gateway.check` @ `gateway.py:186`；`gateway.check_intent` @ `gateway.py:263`），**不承载任何对话历史**。
- QQ 群场景把会话主体记为 `group_<group_openid>`（`inbound.py:283`），同样只是「来源键」。

**层 2 —— iLink `context_token`（回推令牌，内存态）**

```python
# backend/notify/channels.py:153-159
# user_id -> context_token（真实环境应持久化；此处内存态 + 可选落盘）
self._ctx: dict[str, str] = dict(cfg.get("context_tokens") or {})

def remember_context(self, user_id: str, context_token: str) -> None:
    """收到入站消息时登记 context_token —— 之后才能对该用户回推。"""
```

```python
# backend/notify/inbound.py:216-222            ← 入站时登记 token
ctx = str(msg.get("context_token", "")).strip()
if ctx and hasattr(ch, "remember_context"):
    ch.remember_context(sender, ctx)
text = _ilink_text(msg)
reply = await self._dispatch(cid, sender, text, {"context_token": ctx},
                             channel_kind="weixin_oc")
```

```python
# backend/notify/channels.py:167-173          ← 出站时校验 token，缺失即拒绝
ctx = self._ctx.get(str(target), "")
if not ctx:
    return ChannelResult(False, self.name,
        "缺少 context_token：iLink 需对方先发一条消息后才能回推")
```

- 该 token 是 **iLink 协议自身的「被动应答」约束**（`channels.py:135-140`），语义为「能不能回推」，**不是对话上下文**。

**层 3 —— 指令处理链（无会话状态）**

```python
# backend/api/notify.py:175-201（handle_inbound_command）
key = meta.get("sender_key") or gateway.sender_key(channel_id, sender_id)   # :181
parsed = await parse_command(text, _resolve_llm(cfg))                       # :183
perm = gateway.check_intent(key, intent)                                    # :187
if parsed.get("need_confirm"):                                             # :192
    confirm = parsed.get("confirm_text") or "请确认是否执行"
    return f"{confirm}\n（回复「确认」执行；本会话 5 分钟内有效）"             # :194
result = await _execute(intent, parsed.get("params") or {})                 # :197
```

```python
# backend/api/notify.py:331-348（HTTP /command 入口）
if parsed["need_confirm"] and not body.confirmed:        # :339  ← 确认靠调用方传参
    return {"ok": True, "pending": True, **parsed}       # :340
...
return {"ok": True, "pending": False, "intent": intent, "result": result}
```

- 意图集合见 `backend/notify/cmd_parser.py:31-55`（`create_task/start_task/stop_task/query_status/recapture/help/unknown`）——**无 `reset`、无 `new_conversation`、无任何会话管理意图**。
- 需二次确认的意图标记：`cmd_parser.py:227-228`。

### 2.3 事实三：本项目的「上下文重置」等价物 = 不存在

本模块**没有任何**按会话维度的历史 / 游标 / 上下文存储（既无 `conversation_manager` 的 cid，也无 `history` 列表）。唯一跨消息存续的状态是 iLink `_ctx`（token 映射，进程内存，`channels.py:154`），其用途是「回推资格」而非「对话上下文」。**因此没有可被「reset vs new」语义区分的对象。**

> 参考：本项目其它模块的「会话」是指**抖音私信会话**（`dm_conversations` 表 / `conversation_capture.py`），与 IM 网关的会话标识**不同域**，同样不受 #10118 影响。

---

## 三 影响判定

| 判据 | 上游 #10118 | 本项目 | 结论 |
|---|---|---|---|
| UMO 标识格式/构造规则 | **未改动**（diff 未触及） | 本项目无 UMO（检索零命中） | 无影响 |
| `/reset` 语义（清上下文保 cid） | 仅第三方 runner 状态 / `history=[]` | 本项目无 `/reset` 指令 | 无影响 |
| `/new` 语义（建新会话保旧记录） | `conversation_manager.new_conversation` | 本项目无 `conversation_manager` | 无影响 |
| 会话标识 | `unified_msg_origin` | `sender_key`（`gateway.py:55`）+ iLink `context_token`（`channels.py:154`） | 概念正交，无需对齐 |
| 指令体系 | `filter.command("reset"/"new")` | 意图白名单 `cmd_parser.py:47-55` | 无交集 |

**明确判定：无需改。**

**若不改的后果：无。** 具体而言——

1. 无迁移成本：本项目不存 `curr_conversation_id` / `history`，不存在「旧语义写入的数据需要按新语义解读」的问题；
2. 无行为回归：本项目 IM 网关不暴露 `/reset`、`/new`，用户无从触发上游语义分叉；
3. 无协议风险：`sender_key` 与 `context_token` 的取值只由渠道协议与 `channel_id/sender_id` 决定，#10118 不改变这两者的来源；
4. 上游该改动**未引入新渠道协议字段**（`inbound.py` docstring 所列 iLink `getupdates`/`sendmessage`、QQ `botpy` 协议均无变化），故本项目入站/出站协议实现亦无需同步。

---

## 四 建议动作

| # | 动作 | 优先级 | 说明 |
|---|---|---|---|
| **R1** | **将本项从「待复核」关闭为「已复核·无需改」** | — | 更新 `工作记忆/10_上游情报_更新报告_20260923.md` §2.3 行动项第一项（当前为 `[ ] 检查 AstrBot 的 reset vs new conversation 语义…`）与 §「影响评估」中「可能影响 UMO 会话标识语义…须复核」的暂定措辞 → 结论落定为「无需改」。**该文件不在本任务写入授权内，须由所有者执行。** |
| **R2** | ⚠️ **独立缺陷（非 #10118 引起）**：IM 侧二次确认承诺未兑现 | 中 | `api/notify.py:192-194` 向用户承诺「回复『确认』执行；本会话 5 分钟内有效」，但：① IM 侧**无待确认状态存储**（全模块检索 `pending_confirm` 类状态为空）；② 实测 `cmd_parser._rule_parse("确认")` → `intent == "unknown"`（规则兜底不识别「确认」）⇒ 用户回复「确认」只会收到「没理解你的意思」。仅 HTTP `/command` 入口靠调用方传 `confirmed`（`api/notify.py:339`）才能执行。**建议：要么实现按 `sender_key` 的短时 pending 状态机，要么删除该承诺话术**（避免误导）。此项应单独开卡，禁止与上游追踪混记。 |
| **R3** | 建立「UMO 相关上游改动」的持续监测词 | 低 | 本次核查手法可固化：上游 diff 命中 `unified_msg_origin` / `conversation_manager` 构造代码时，才触发本项目会话语义复核；仅命中 `reset/new` 指令行为时可直接判定无关。 |

---

## 五 证据

### 5.1 上游证据（可复现命令）

```bash
# 代理（本机：http://127.0.0.1:10808）
curl -s -x http://127.0.0.1:10808 \
  "https://api.github.com/repos/AstrBotDevs/AstrBot/commits/3ada8aa0aed711f3277fb8ed4f31864a1fefa0f2"
# → sha=3ada8aa0aed711f3277fb8ed4f31864a1fefa0f2
#   msg="fix: separate reset and new conversation semantics (#10118)"
#   parents=["8b5e24ba2eac3375a0e17bdf3f0ea790eb91de15"]
#   files=5（conversation.py / main.py / docs/en / docs/zh / test_conversation_restart.py）
```

- PR 页面（web_extract 实测）：State=merged，Merge commit=`3ada8aa0aed711f3277fb8ed4f31864a1fefa0f2`，Merged=2026-09-21T08:22:39Z，+255/−59 in 5 files，Refs #10114。
- #10163 页面：merged commit `8b5e24b`（= `8b5e24ba…`，即 #10118 的父提交），改动为 `CronMessageEvent.plugins_name` 初始化。
- #10156 页面：merged commit `95e98b8`（=`95e98b8aed75d56713666eff39e31bafffd95426`，本仓 `docs/upstream_baseline.json` 记录的 astrbot head_sha）。
- 直接 diff（commit patch 摘录）已在 §1.2 逐段引用。
- 注：`api.github.com` 匿名限流 60/h（直连命中 rate limit，经 `127.0.0.1:10808` 代理后可用）；一次性拉取后本地缓存于 `%LOCALAPPDATA%\Temp\astr_c10118.json`（31564 B）。

### 5.2 本项目证据（file:line，只读）

| 结论 | 证据 |
|---|---|
| 无 UMO / conversation_manager | 全仓 grep `unified_msg_origin`、`umo`、`conversation_manager`、`new_conversation`（IM 语义）→ **零命中** |
| `inbound.py` 无 reset/new 语义 | `inbound.py` 全文件 grep `reset`/`new_conv`/`new_conversation`/`umo`/`清空`/`重置` → **零命中** |
| 会话标识 = 来源键 | `backend/notify/gateway.py:55-57`（`sender_key`）；`backend/notify/inbound.py:139`（注入 `meta["sender_key"]`）；`inbound.py:283`（QQ 群 `group_{group_openid}`） |
| 仅有协议层 token 状态 | `backend/notify/channels.py:153-159`（`_ctx` / `remember_context`）；`channels.py:167-173`（缺 token 即拒绝回推）；`inbound.py:216-222`（入站登记） |
| 指令集无会话管理 | `backend/notify/cmd_parser.py:31-55`（intent 定义 + 白名单）；`cmd_parser.py:227-228`（need_confirm）；`cmd_parser.py:242-272`（确认话术） |
| 二次确认无状态（R2） | `backend/api/notify.py:181`（仅 `sender_key`，无 pending 存储）；`api/notify.py:192-194`（承诺 5 分钟有效）；实测 `_rule_parse("确认") == {"intent":"unknown"}` |
| 上游基线登记 | `DYAutoDM_v2/docs/upstream_baseline.json`（astrbot head_sha `95e98b8a…`，pushed_at 2026-09-23T07:34:04Z） |
| 上游追踪来源 | `工作记忆/10_上游情报_更新报告_20260923.md:82-84, 90, 94, 178`（本任务所复核的暂定结论出处） |

### 5.3 复核边界（诚实声明）

- 本次判定基于 **#10118 合并提交 `3ada8aa` 的完整文件清单与逐段 patch**（5 文件，未触及 UMO 构造）+ **本项目全仓检索（零命中）**。二者足以支撑「无需改」。
- 未做且不需要做：AstrBot 全仓 UMO 构造点的穷举（因其不在 #10118 diff 内，且本项目不消费该标识）。
- `_rule_parse("确认")` 结论为本次静态执行 `cmd_parser._rule_parse` 实测所得（非推断）。

---

**复核人**：Hermes 子代理（L4）　**复核方式**：上游 API/PR 取证 + 本仓只读检索/静态执行
**唯一输出**：本文件（未创建/修改任何其它文件；未执行任何 git 写操作）
