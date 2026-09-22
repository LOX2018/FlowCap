# M-5 send_delivery 投递验证钩子 — 方案设计

> 状态：设计草案（**不改代码**）。关联 M-9（SEND-006）已闭环，本事项独立。
> 分支：`design/better-douyin`
> 日期：2026-09-22

## 1. 问题分析

### 1.1 当前状态

见 `工作记忆/架构与业务实机复盘_2026-09-22_v0.44.32.md`：

| 账号 | `role='me'` 落库 | `[投递验证]` 标记 | 探针结果 |
|------|-----------------|-------------------|---------|
| 张老师 | 269 条 | 0 条 | **degraded**（有落库无标记） |
| 尚进 | 98 条 | 1 条 | healthy |

张老师 269 条私信实质已发出（日志写 `[私信] 发送结果: …=成功`），但探针报 degraded——因为系统从未在 DM 中写入「这个私信确实投递到了」的显式证据。

### 1.2 根因：两个缺口

**缺口 A（dispatch 路径，优先级高）**  
`dispatch._do_send()` 在 `submit_by_uid` 返回 `_r.accepted=True` 后，把 `ok, reason = True, "已入池（调度器异步发送）"` 当作成功。  
→ 这是「入池成功」，不是「投递成功」。实际投递在 `DmDispatcher._send_one()` 异步执行，但无论成功还是失败，`_send_one` 都**没有回写**任何「投递验证」标记到 `dm_messages`。

```
dispatch._do_send
  └─ dm_dispatch.submit_by_uid(acct, uid, content)   ← 只入池，不投递
       └─ ok = True, "已入池"                          ← 调度器标记为成功
                                                         ↑ 缺口 A 在此：ok 不代表对端收到了
             └─ (异步) DmDispatcher._send_one
                  └─ recv_daemon /send_by_uid           ← 这里才真的投递
                       └─ HTTP response {'ok': true}    ← 也没有写投递验证标记
                                                         ↑ 缺口 A 的延续：响应没被加工成 DB 证据
```

**缺口 B（sender 直发路径，优先级低）**  
当 `dm_dispatch` 不可达时，`send_target_async`（直发）被调用。`send_by_uid()` 执行 `create_conversation + send_msg` 后，若返回 `ok=True` 则只打印日志 `[私信] 发送结果: …=成功`。

→ 这条日志从未被解析为 `[投递验证]` DB 记录。探针读不到。

### 1.3 更深层：系统把「API 响应 ok」等同于「投递成功」

当前所有投递点都以「HTTP 200 + JSON `{'ok': true}`」作为成功判据。但 C-04 设计契约明确禁止：  
> **Ⅰ1 严禁仅凭抓包到上传步骤或 UI 弹窗宣称发送成功**

`verify_text_send_delivery.py`（诊断脚本）已经实现正确的判定协议：  
- `statusCode==0`  
- `server_message_id` 非空非 0  
- `check_code != 8610`（安全检查未通过）  
- 可选：`cmd 301` 读取会话确认该 `server_message_id` 真的出现

生产代码的投递点**全都没有实现这个协议**。

### 1.4 为什么探针报 degraded 是正确行为

探针 `probe_send_delivery` 在 `[投递验证] == 0` 时只能报 degraded，**即使日志显示「发送成功」**。这不是探针保守，而是整个投递链路从未产出「可用作探针证据」的事实。  
— 探针诚实反映了系统能力的真实缺口。

---

## 2. 方案选项

### 方案 A（推荐）：在投递确认点插入 DB 验证标记

#### 思路

在私信投递成功（确认 API 响应有效）后，**直接向 `dm_messages` INSERT 一条 `[投递验证]` 记录**，使探针能读到。

探针搜索的是：
```sql
SELECT ... FROM dm_messages 
WHERE account=? AND role='me' AND text LIKE '%投递验证%'
```

插入的行格式：
```sql
INSERT INTO dm_messages(account, conv_id, msg_id, role, text, extra, sent_at)
VALUES (?, ?, ?, 'me', '[投递验证] conv_id=... msg_id=...',
        '{"delivery_verified":true}', ?)
```

#### 插入点（两个，共用同一工具函数）

**插入点 1：`_do_send` 成功分支（dispatch 直发路径 / 回落直发）**  
位置：`backend/core/dispatch.py` `_do_send()`，L469-476 的 `if ok:` 内部。  
在 `rec.status = RecordStatus.SENT` 之后，调 `mark_delivery_verified(acct, ...)`。

**插入点 2：`_send_one` 成功分支（dm_dispatch 异步调度路径）**  
位置：`backend/services/dm_dispatch.py` `_send_one()`，L1193-1206 的 `if ok:` 内部。  
在 `task.status = "done"` 之后，调同样的 `mark_delivery_verified()`。

#### 判定「真投递」而非「API 返回 ok」的改进

当前 `_send_one` 的 `ok = bool(data.get("ok"))` 只判断 recv_daemon 回应，不判断抖音 IM API 的真实投递状态。为了符合 C-04 契约，**需要将判定口径从「HTTP 200 + ok」提升到「解析 IM API 的 send_msg 响应」**。

具体：
1. `sender.send_by_uid()` 的 `DouyinAPI.send_msg()` 调用乘以返回体解析，提取 `server_message_id`, `statusCode`, `check_code`
2. `mark_delivery_verified()` 接收这些字段作为输入，只有满足以下条件才写入验证标记：
   - `statusCode == 0`
   - `server_message_id` 非空且非 `"0"`
   - `check_code != 8610`

注意：此改进**不影响**发送流程本身——发送的 `ok/reason` 返回值不变，仅影响是否写入投递验证标记。

#### 需要改动的文件

| # | 文件 | 改动 | 影响 |
|---|------|------|------|
| 1 | `backend/services/sender.py` | `send_by_uid()` 返回结构扩展，含 `server_message_id`/`statusCode`/`check_code`；或新增 `parse_send_response()` 工具函数 | 无回归：现有调用方仍只用 `(bool, str)` 元组首两项 |
| 2 | `backend/core/dispatch.py` | `_do_send()` 成功分支调 `mark_delivery_verified()` | 仅加一行函数调用，不影响正常发送逻辑 |
| 3 | `backend/services/dm_dispatch.py` | `_send_one()` 成功分支调 `mark_delivery_verified()` + 解析 response 获取结构化投递信息 | 同上 |
| 4 | **新建** `services/delivery_verify.py` | 工具函数 `mark_delivery_verified()`：解析 IM API 响应 → 条件满足则 INSERT `[投递验证]` 行到 `dm_messages` | 无侵入，可独立测试 |
| — | `backend/services/probe.py` | **无需改动** | 探针已会搜索 `[投递验证]` |

**总计：4 个文件（1 新建，3 修改）。**

#### 行为变化

- before：`_do_send` ok → 写 `RecordStatus.SENT`，不写 `[投递验证]`
- after：`_do_send` ok → 写 `RecordStatus.SENT` **+** INSERT `[投递验证]` 行

- before：`_send_one` ok → `task.status = "done"`，不写 `[投递验证]`
- after：`_send_one` ok → `task.status = "done"` **+** INSERT `[投递验证]` 行

#### 验收预期

```log
# 日志预期
[delivery-verify] 投递已验证: conv_id=0:1:123:456 msg_id=789012 statusCode=0
[delivery-verify] 已写入 dm_messages: account=张老师 text='[投递验证] conv_id=... msg_id=...'
```

```sql
-- DB 预期
SELECT COUNT(*) FROM dm_messages 
WHERE account='张老师' AND role='me' AND text LIKE '%投递验证%';
-- → ≥ 成功发送条数（有延迟，但最终一致）
```

探针：
```
send_delivery@张老师:
  state=healthy, delivery_verified=N (probe 计 ver)
  → 之前 degraded → healthy
```

---

### 方案 B：在 dispatcher 层面加日志标记，不写 DB

#### 思路

在 `dispatch._do_send()` 成功时，写一行日志 `[投递已验证] conv_id=... msg_id=...`。探针改为**搜索日志文件**而非 DB。

#### 问题

1. **探针当前搜索 `dm_messages` 表**——要改用日志搜索需改 `probe_send_delivery`，增加代码复杂度，且日志搜索比 SQL 查询慢一个数量级。
2. 日志可能被轮转/截断/清空（本项目日志文件无强制保留策略），标记丢失风险高。
3. 不符合探针「只读本地事实」中的「DB 为权威」的设计——日志是辅助证据，DB 才是主证据。

#### 需要改动的文件

| # | 文件 | 改动 |
|---|------|------|
| 1 | `backend/core/dispatch.py` | ok 分支加 log |
| 2 | `backend/services/probe.py` | `probe_send_delivery` 加日志搜索分支 |

**总计：2 个文件（纯修改）。**

#### 不推荐理由

- DB 为权威（项目既有判据），日志不稳定
- 探针实现复杂度上升
- 张老师 269 条已发却无证据的问题**仍然需要补 DB 记录**才能被探针探测——日志搜索只是换了扫描源，不解决「现有私信无证据」问题

---

### 方案 C（不推荐）：让 recv_daemon 在捕获时自动识别本机发出的消息并标记

#### 思路

私信发送后，recv_daemon 的下一次 `list_conversations` 捕获中，会把该消息作为 `role='me'` 行写库。让捕获层识别这些行中的特有字段（如 `skey`），自动在 text 中追加 `[投递验证]`。

#### 问题

1. 改动面极大：涉及 recv_daemon 的写库逻辑（核心捕获链路，破坏面宽）
2. `skey` 存在性已被探针明确否决（`"skey {sk} 条只是「实发要素」，不作为投递证据"`）
3. 捕获可能是**延迟**的（长轮询间隔），导致投递标记在发送后几分钟才出现

**不推荐。**

---

## 3. 推荐方案

**方案 A**（DB 直接插入 `[投递验证]` + 升级投递判定口径）。

理由：
1. **改动收敛**：只在投递成功路径的尾部加一个 INSERT，不干扰既有发送流程
2. **探针零改动**：`probe_send_delivery` 的 SQL 搜索 `[投递验证]` 已到位
3. **诚实性**：只有 IM API 证实「投递」（statusCode/checkCode 达标）才写入，不是「入池成功」就写
4. **可回滚**：删掉 `mark_delivery_verified()` 调用即完全回退，不影响发送逻辑
5. **回溯兼容**：新发私信会有标记；已发私信（张老师 269 条）在重跑探针后仍然是 degraded，诚实反映「历史数据无标记」的事实——不会 false healthy

### 与 M-8/M-9 的关系

| 事项 | 关系 |
|------|------|
| M-9（SEND-006 修复） | 独立，已闭环。M-9 保证「文案不空 → 能被发送」，与投递验证无关 |
| M-8 | 独立。M-8 修的是 AI 文案/回复路径，不涉及本钩子 |

---

## 4. 风险

### R1：虚假 healthy（最严重）

如果 `mark_delivery_verified()` 在 API 返回 200 但抖音实际上没投递时也写入 `[投递验证]`，探针会从 degraded 升到 healthy，**掩盖投递失效问题**。

**缓解**（三重防御）：
1. **判定口径收紧**：只解析 IM API 的 `send_msg` 原始响应（`server_message_id` / `statusCode` / `check_code`），不使用上游封装层的 `{'ok': true}`。非结构化错误/异常时**不写入**标记。
2. **兜底降级**：如果解析出错（响应格式变了、字段缺失），`mark_delivery_verified()` **静默跳过**，不写入任何标记。
3. **审计**：日志打印判定结果（`statusCode=xx check_code=xx → 写入/跳过`），供人工核查。

### R2：`dm_messages` 重复行/唯一索引冲突

`dm_messages` 有 `uniq_dmmsg` 部分唯一索引 `(account, conv_id, msg_id)`。投递验证行使用实际 `msg_id`，插入时若该消息已被 capture 捕获（以 `role='me'` 存在），INSERT 可能冲突。

**缓解**：使用 `INSERT OR IGNORE`（SQLite 语法），或先 SELECT 再 INSERT。验证行与真实消息行共用同一 `msg_id`，不应重复——如果重复说明消息已被捕获，投递验证标记已是冗余，跳过无害。

### R3：写入验证行时 DB 连接失败

`dm_messages` 写入可能在发送成功后但 DB 异常时失败——此时发送本身已成功，只是标记没落盘。

**缓解**：写入失败只打 warning 不抛异常，不阻塞发送流程。下次探针巡检时仍按既有逻辑报 `degraded`（诚实），不会因此变成 failed。

### R4：对既有 degraded 账号的「降级保真」

改动后，**已成功发送但无标记的旧消息**不会被追溯到——它们不会被补 INSERT `[投递验证]`。所以：
- 张老师 269 条：`[投递验证]` 仍为 0 → **仍是 degraded**（诚实）
- 新发送的私信：如有标记 → healthy

这确保「加了钩子 → 从 degraded 变 healthy」的现象**只发生在有真实标记的新发私信上**，旧数据不受影响。

---

## 5. 验收判据

### 5.1 测试用例

| # | 场景 | 操作 | 预期 |
|---|------|------|------|
| T1 | dispatch 直发成功 | 发送后查看 DB | `dm_messages` 中多一条 `role='me'` 且 `text LIKE '%[投递验证]%'` 的行 |
| T2 | dispatch dm_dispatch 异步成功 | 同上 | 同上（最终一致，可能在 `_send_one` 执行后写入） |
| T3 | API 返回 `{'ok': false}` | 触发发送失败 | 不写入 `[投递验证]` |
| T4 | API 返回 `{'ok': true}` 但 `check_code=8610` | 触发安全隐患拒绝 | 解析到 `check_code=8610` → 不写入 `[投递验证]` |
| T5 | DB 写入异常 | 发送成功但 DB 写 `[投递验证]` 失败 | 只打 warning，不抛异常，发送不中断 |

### 5.2 端到端验收

```bash
# 1. 发一条私信（正常监听流程触发）
# 2. 查 DB
sqlite3 data/dm.db "SELECT account, text FROM dm_messages WHERE text LIKE '%投递验证%';"
# → 应出现一行，格式如：
# 张老师 | [投递验证] conv_id=0:1:xxx:xxx msg_id=1234567890

# 3. 跑探针
py -3 backend/run_probe.py
# → send_delivery@张老师: state=healthy
# → evidence 含: delivery_verified=N (N ≥ 1)
```

### 5.3 日志格式（最终形态）

```
[SEND-041] [delivery-verify] 投递判定: account=张老师 uid=123456 conv_id=0:1:xxx:xxx
  statusCode=0 server_message_id=987654 check_code=8101
   → 写入 [投递验证] 标记 (msg_id=987654)
[SEND-042] [delivery-verify] 投递判定: account=张老师 uid=123456 conv_id=0:1:xxx:xxx
  statusCode=0 server_message_id=987655 check_code=8610
   → check_code=8610 安全检查未通过，跳过写入投递验证标记
[SEND-043] [delivery-verify] DB 写入 [投递验证] 失败（不阻断发送）: {error}
```

探针搜索 `text LIKE '%投递验证%'` 将命中这些行 → `delivery_verified >= 1` → state 升 **healthy**。

---

## 6. 设计决策记录

| 决策 | 选项 | 选择 | 理由 |
|------|------|------|------|
| 证据载体 | DB vs 日志 | **DB** | 探针目前读 DB，日志不稳定，改探针增加复杂度 |
| 判定口径 | 上游 ok vs IM 协议 | **IM 协议** | C-04 契约禁止「仅凭接口 200」；`verify_text_send_delivery.py` 已有参考实现 |
| 写入时机 | 同步 vs 延迟 | **同步**（发送后立即） | 探针不定时巡检，延迟写入导致窗内 read 不到标记，报 degraded 后再变 healthy → 抖动 |
| 重复处理 | `INSERT OR IGNORE` | **IGNORE** | 同一 msg_id 若已被 capture 插入（role='me' 真实消息行），验证行冲突无害跳过的 |
| 历史数据 | 追溯补写 vs 不追溯 | **不追溯** | 追溯需扫描历史日志推理已成功发送的私信，工程量大且不可靠（日志可能已被轮转）；不追溯保证「旧 degraded 不假 healthy」 |