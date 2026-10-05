# ADR-021：发送回执类型化（Canonical Kind）+ 账号级分钟窗限流

- 状态：已采纳（2026-09-28，v0.45.68）
- 关联：`services/send_response.py`、`services/dm_dispatch.py`、`daemon/recv_daemon.py`、`dy_apis/client_im.py`
- 契约文档：`工作记忆/02_效果定义与探针.md`（投递判据）、`工作记忆/05a_私信捕获与协议台账.md`

## 背景

1. **发送失败原因被"字符串化"**：从 `client_im` 生成的**人类可读文案**一路透传到
   `recv_daemon`（JSON `error`）再到 `dm_dispatch`，后者用**关键字子串匹配**反推是否频控。
   任何文案改动即静默失效（契约漂移）。**实测断点**：抖音风控返回 `{"decision":"KICK"}`
   时文案为「抖音拒绝发送：KICK（风控/限流…）」，**不含**关键字表中的任一 token
   ⇒ 最该冷静的一类失败反而不冷静。
2. **无账号级分钟窗**：`stranger_per_minute` 仅约束陌生人首发；熟客/AI 回复不受
   分钟约束 ⇒ 无法把"每分钟 2~3 条"做成硬约束。

## 决策

### D1 失败类型提升为枚举（Canonical Contract）

`services/send_response.py` 定义 `KIND_*`（delivered / safety_blocked / under_review /
rate_limited / risk_control / need_follow / privacy / user_gone / denied / http_error /
parse_error / unknown）+ 唯一判定出口 `failure_kind(resp_json, verdict, http_ok)` +
`kind_is_cooldown(kind)`。`delivery_verdict()` 输出补 `error_kind`；全链路透传；
消费方（冷静期）**只认枚举，不再猜文案**。`COOLDOWN_KINDS = {rate_limited, risk_control}`。

- **权衡**：新增枚举层带来契约维护成本；换取"文案可变、判定不漂移"。
- **不变式**：`KIND_*` 一经消费不得改名（改名=破坏性变更，须走新 ADR）。

### D2 冷静期触发改为"枚举优先 + 关键字兜底"

`AccountQuota.on_result(ok, detail, kind)`：`kind in COOLDOWN_KINDS` 即触发；
无 kind（旧调用方/直发路径）才回落关键字（兜底表补入 KICK/INVALID_REQUEST/RISK/风控）。
默认冷静 600s（10 分钟），连续触发翻倍，封顶 3600s。

### D3 账号级分钟窗（`send.per_minute_limit`，默认 3）

`AccountQuota` 增 `_minute_sends` 滑窗；`can_send(min_interval, per_minute)` 新增
`per_minute` 判定；`note_sent()` 记账。新配置键 `per_minute_limit`（env `DY_SEND_PER_MINUTE`，
0=关闭）。

- **手动发送豁免**：`source=='manual'` 不做 check，**但仍计入**分钟窗 —— 遵守
  「门禁不拦用户显式操作」（§〇·丁）且不牺牲账号保护。
- **权衡**：手动豁免意味着用户狂点可能短时超过 N/min；为避免"拦用户"这一更差体验，
  接受该风险（用户显式行为自担，且仍抬高水位抑制后续自动发送）。

### D4 AI「客服腔/不专业」定位为**内容层**问题，非发送层

- 补 `pro_kb` 专业库（张老师账号）；`reply_kb` 加质量门槛（写入侧不再学占位/短句，
  匹配侧脏 auto 条目**失效不删除**）；张老师 Agent prompt 去僵化。
- **边界**：本 ADR 只固化 D1~D3（发送层，全账号级风控面）。D4 属内容层，按
  skill §十六.7「改内容层与改发送层必须分批」独立处理。

## 后果

- 正向：失败可归因且类型稳定；频控/风控失败**必然**冷静；频率分钟级硬可控。
- 负向：`_LAZY_MAP`/`app_config_schema` 需同步登记（否则新参数永远用旧值）；
  `test_dm_dispatch_config.BASELINE` 需同步（已改，否则门禁红）。
- 未覆盖：图片发送（`image_sender.py`）仍走各账号闸门，未接入账号级分钟窗（后续项）。

## 修订 2026-09-28（v0.45.72 → v0.45.74）：分钟窗下沉物理闸门 + 手动豁免配置化

### 一、分钟窗下沉（v0.45.72）

**发现**：D3 的分钟窗只在**调度器策略层**（`dm_dispatch.AccountQuota.can_send`）。
但 `api/messages.send_image_dm` 的图片发送**不过调度器**（直接转发 `recv_daemon
/send_image`），且 `/send`、`/send_by_uid` 存在绕过调度器的直发路径 ⇒
**图片发送与直发完全不受分钟窗约束**（原文「未覆盖」项）。

**修订**：把分钟窗加进 `recv_daemon._send_gate_acquire`（三端点**共同物理出口**）：

- 新增 `_cfg_per_minute()` + `_send_gate_minute` 滑窗，与既有 `min_interval`
  同层、共用 `_send_gate_lock` ⇒ **汇总计数、任何路径无法绕过**。
- 超限**快速失败**（不忙等 —— 等满 60s 会堆死事件循环），返回可归因的
  `error_kind=rate_limited` + `reason_code="minute_limit"` + 可读 `msg`。
- 上限取**与调度器相同的硬上限**（同配置键 `send.per_minute_limit` 作 SSOT）。
- 边界：`per_minute` 取整下限 1（>0 但 <1 的分数值不得被 `int()` 成 0 而恒拦一切）。
- 前端两处发送失败提示改为优先展示 `msg`（可读原因），不再只显示 `rate_limited`。

### 二、手动豁免**配置化**（v0.45.74，用户 2026-09-28 拍板）

**用户原话**：「调度肯定需要手动开放，并不是直接默认定死」。

**改判（原文「物理层不做手动豁免」已废弃）**：v0.45.72 初版把「物理层不豁免手动」
**硬编码**了 —— 违反用户「显式配置原则」。现改为**由配置显式控制**：

- 新增配置 `send.per_minute_manual_exempt`（**bool，默认 True**）。
- `source=="manual"` 时：**开**（默认）⇒ 不拦手动，但手动**仍计入**分钟窗
  （抬高后续自动发送水位）；**关** ⇒ 手动同受每分钟上限约束。
- 来源经 `body.source` 从 `api/messages`（手动端点默认 `"manual"`）贯穿到
  `recv_daemon` 三个 body（`SendBody`/`SendByUidBody`/`SendImageBody`）；
  绕过调度器的编程式直发可显式传 `source="dispatch"`（受严格管控）。
- 与调度器层**同一条铁律**：「门禁不拦用户显式操作」。

**验证**：`test_send_gate_config` **13/13**（新增 3 条：默认豁免放行手动 + 关闭豁免
则拦 + `_source_is_manual` 容错）· `test_send_pacing_and_kind` 12/12 +
`test_dm_dispatch_config` 8/8 零回归 · 前端 `npm run build` ✓。

**并发（VIII-C/D）**：本项实施期间另一会话提交 `057a58f`（v0.45.73，凭证更新路由）
已升位；其唯一相交文件 `frontend/src/api/client.ts` 改动**互不重叠**（判
disjoint-intent），本会话在**其新基线上**升位 → **0.45.74**。

**残留**：`send.min_interval` 在调度器层对手动豁免 ⇒ 手动不受最小间隔约束
（分钟窗的拦截已可配置）。

## 验证

见 `工作记忆/cases/2026-09-28_发送回执类型化+分钟级限流+AI获客治客服腔_v0.45.68.md` §六。
