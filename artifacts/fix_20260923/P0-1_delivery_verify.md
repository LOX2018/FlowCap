# P0-1 · 投递验证「假成功」闭环 —— 修复报告（2026-09-23）

> 执行者：父会话（本会话）　分支：`design/better-douyin`　基线：v0.44.53
> 来源：`D:\SJ  agent\AUDIT_2026-09-22_23_FINAL.md` P0-2（合并版 P0-1）
> 归属声明（并发模式）：本批独占 `services/send_response.py`(新建) / `services/delivery_verify.py` /
> `services/probe.py` / `core/dispatch.py` / `core/sender.py` / `services/dm_dispatch.py` /
> `daemon/recv_daemon.py` / `dy_apis/client_im.py` / `api/messages.py` / `test_capability_probe.py` /
> `test_delivery_verify.py`(新建) / `scripts/diag/verify_text_send_delivery.py`

---

## 一、设计意图 → 观测偏差

**模块**：私信投递验证（M-5 钩子 + `probe_send_delivery` 探针）

**设计契约**（`工作记忆/02_效果定义与探针.md` §2.2、`docs/design-contracts/C-04`）：
> Ⅰ1 **严禁**仅凭抓包到上传步骤或 UI 弹窗宣称发送成功；投递判据必须是服务端事实。

**观测到的偏差**（四路证据：ocr high + subagent3 独立复现 + 宿主核实）：

| # | 偏差 | 证据 |
|---|---|---|
| ① | 两个判定守卫**生产永不触发** | `delivery_verify.py` 的 `status_code != 0` / `check_code == 8610` 分支，被两个调用点硬编码 `status_code=0, check_code=0` 短路；全仓无任何 `server_message_id`/`statusCode` 生产者 |
| ② | 写入唯一前提 = 「调用方自称 ok」 | `dm_dispatch.py:1195 ok = bool(data.get("ok"))` ← recv_daemon `{"ok":true}` ← `client_im.py:408 message=='OK'`，而同一文件自注：「发送被静默拦截（返回 **OK** 但未实际投递）」 |
| ③ | 探针自证闭环 | marker 行 `role='me'` 同时充当证据并计入 `sent_db_rows` ⇒ `state=healthy, confidence=A` 仅凭 marker |
| ④ | marker 泄漏进聊天框 | `api/messages.py` 过滤子句**无**投递验证排除 |
| ⑤ | 无上限堆积 | marker 恒 `msg_id=NULL`，唯一索引为 `WHERE msg_id IS NOT NULL` 的**部分索引** ⇒ `INSERT OR IGNORE` 不去重（实测连调 3 次 = 3 行） |

**错误码**：`SEND-041` / `SEND-042` / `SEND-043` / `SEND-044`（投递验证族，沿用既有编号）

---

## 二、根因（Execution-Chain Traceability）

```
send_msg 响应 resp.content
  └─ Response{cmd,message,body{...}}            ← body 的 field 100 在本仓 .proto 中**未定义**
       （实测定证：Response_pb2.Response.body oneof 仅 500/609/610）
  └─ client_im 只读 resp_json['message'] == 'OK'  ← ✂️ 证据链在此被截断
  └─ (bool, str) 二元契约向上传，**证据无处安放**
  └─ dispatch/dm_dispatch 只能「自称 ok」→ 无条件写 marker
  └─ 探针只查 text LIKE '%投递验证%' ⇒ healthy
```

**根因 = 归因链在第 2 跳截断**：生产链路既没有「解析出服务端消息号」的实现，
也没有「把消息号从底层传到写标记处」的契约。修复必须**同时补齐这两件**。

**关键上游事实（Open-Source Provenance）**：上游 `zhinjs/douyin-im` 的 `isMessageDelivered()`
判据为 `statusCode==0 && server_message_id 非空非 0 && check_code != 8610`，
且 `check_code` 语义：`8101=已投递 / 10502=审核中 / 8610=安全检查未通过`。
本仓 `scripts/diag/verify_text_send_delivery.py` 已实现该协议（宽容字节解析），
但**生产链路从未引用它** —— 同一协议两份实现且只有一份是对的。

---

## 三、修法（按数据流自下而上）

| # | 文件 | 改动 |
|---|---|---|
| 1 | **新建** `backend/services/send_response.py` | 投递响应**唯一解析器 + 唯一判定出口**：`parse_send_response()` / `delivery_verdict(raw, http_ok)` → `{delivered, state, server_message_id, status, check_code, reason}`；`state ∈ delivered/blocked/review/unknown`。把诊断脚本里已验证的宽容解析**收敛为唯一实现**（SSOT） |
| 2 | `dy_apis/client_im.py` `send_msg` | 改读 `delivery_verdict(resp.content)`：有消息号才成功；**只回 OK 无消息号 ⇒ 判未投递**（新增 `AUTH-032` 日志）；成功时返回第三元素 `_v`（结构化判定）。旧调用方按 `(bool, str)` 解包仍安全 |
| 3 | `daemon/recv_daemon.py` | `SendBody`/`SendByUidBody` 增 `server_message_id`（向后兼容，跨解释器证据通路）；`/send`、`/send_by_uid` 解析三元组并在成功后调 `_mark_send_delivery()`；新增 `_mark_send_delivery()`（证据优先级：调用方回传 sid → 本进程解析 verdict → 两者皆无**不写**） |
| 4 | `core/sender.py` `send_by_uid` 兜底直发分支 | 持有 `_verdict` ⇒ 写标记（`source="sender_direct"`） |
| 5 | `core/dispatch.py` `_do_send` | **删除盲标记块**（`if not _routed` 内无条件 `status_code=0, check_code=0`） |
| 6 | `services/dm_dispatch.py` `_send_one` | **删除盲标记块**（同上） |
| 7 | `services/delivery_verify.py` | 重写：`verdict=` 参数路径；**无 `server_message_id` 即拒绝写入**（`SEND-041`）；marker 的 `msg_id` 改 **`verify:<sid>` 独立命名空间**（避开与真实消息行的唯一索引冲突 + 重复标记幂等）；`msg_type='delivery_marker'` + `extra.marker=True` 供读侧二次判定 |
| 8 | `api/messages.py` `get_conversation` | 过滤子句补三条并集：`msg_type <> 'delivery_marker'` / `text NOT LIKE '[投递验证]%'` / `msg_id NOT LIKE 'verify:%'` |
| 9 | `services/probe.py` `probe_send_delivery` | 只认**带 `server_message_id`** 的标记（`json_extract(extra,'$.server_message_id') <> ''`）；新增 metrics `real_msg_rows / marker_rows / marker_without_msgid`；真实消息数扣除标记行（不再虚增） |
| 10 | `scripts/diag/verify_text_send_delivery.py` | `_fields`/`parse_send_response` 改为委托唯一实现（消重） |
| 11 | `test_capability_probe.py:365` | 顺手修 P3-3：`self.assertNotEqual(...) if not r["evidence"] else None`（断言永不执行）→ 真断言 |

---

## 四、Live Verification（离线实测，本机真实运行）

命令：`cd DYAutoDM_v2/backend && DY_APP_ROOT=$LOCALAPPDATA/Temp/fixP0 python -m unittest test_delivery_verify`

```
Ran 14 tests in 0.743s
OK
```

**新增 `test_delivery_verify.py`（14 例，含 4 条负控）**：

| 用例 | 断言 | 负控意义 |
|---|---|---|
| `test_delivered_with_server_message_id` | sid=7621372915, code=8101 → `delivered=True` | 正向 |
| `test_ok_without_message_id_is_not_delivered` | **只回 `message='OK'`** → `delivered=False, state=unknown` | 🔴 **修复前此形态判成功** |
| `test_safety_block_is_blocked` | code=8610 → `blocked` | 守卫真会触发（修复前恒不触发） |
| `test_under_review_is_not_delivered` | code=10502 → `review`，不算投递 | |
| `test_http_failure_is_blocked` | http_ok=False → `blocked` | |
| `test_zero_message_id_treated_as_absent` | sid=0 → 视为无证据 | |
| `test_no_evidence_writes_nothing` | 旧式无条件调用（`status_code=0,check_code=0`）→ **不写标记**，库内 0 行 | 🔴 **修复前恒写** |
| `test_evidence_from_verdict_writes_marker` | 写 marker，`msg_id='verify:999'`，`msg_type='delivery_marker'` | |
| `test_marker_idempotent` | 连调 3 次 → 仍 **1 行** | 🔴 修复前 3 行（无上限堆积） |
| `test_marker_does_not_collide_with_real_message_row` | `msg_id='555'` 与 `'verify:555'` **并存** | |
| `test_blind_marker_is_not_healthy` | 盲标记（extra 无 sid）→ **degraded** | 🔴 修复前报 healthy（假成功本体） |
| `test_marker_with_sid_is_healthy` | 带 sid 标记 → healthy，且 `real_msg_rows=1`（不虚增） | |
| `test_no_send_at_all_is_unknown` | 无发送 → unknown（不冒充健康） | |
| `test_marker_filtered_out` | 聊天详情：`真实消息` 在、`[投递验证]` **不在** | 🔴 修复前泄漏到聊天框 |

静态检查：`compileall` + `import` 在 **Python 3.14.6（真实 prod）与 3.11.9（打包解释器）双通过**。

---

## 五、诚实标注（未验证/未闭环部分）

1. 🔴 **端到端未验证（用户已明示本轮不真发）**：以上全部为**离线**证据（构造响应字节 → 解析 → 判定 → 落库 → 探针/渲染读回）。**「服务端确实返回 `server_message_id`」这一步未在真实发送中观测过** —— 本轮未发出一封私信（风控红线 + 养号期）。
2. 🔴 **生产解析器可能取不到证据（重要，须实机取证）**：
   `delivery_verdict()` 的宽容解析只对 **CPython ≥3.13** 生效（3.13 起 `bytes[j]` 返回 `int`）。
   本机 **3.14.6** 可跑；但**打包解释器实测为 3.11.9**，其 `bytes[j]` 返回 `bytes` ⇒
   宽容解析会抛 `TypeError`，被 `client_im` 的守卫捕获后**降级**为旧的 `message=='OK'` 语义
   （不崩溃，但能力退化）。⇒ **当前唯一可用证据通路 = 调用方回传 `server_message_id`**
   （`SendBody.server_message_id`，任何解释器都可用）。**待实机确认**：
   真实响应是否含 `server_message_id`、以及**哪个解释器在跑 sidecar**（见 §五-3）。
3. ⚠️ **解释器不确定性（须查证，会改变结论面）**：
   本仓同时存在 Python **3.11 / 3.13? / 3.14 / 3.15**。旁证冲突：
   H-8 曾记「`get_type_hints` 3.11 必炸 ⇒ 3.14」；而本轮实测打包 `daemon/recv_daemon.py`
   的 `.pyc` 头为 `3.11 (3495)`。**未查证**：`build_all.py` 用哪个解释器冻结 sidecar。
   ⇒ 下一步判据：实机发一条私信，看日志是否出现 `AUTH-032`（= 解析到、但无消息号）
   或 `降级（解析器不可用）`（= 解释器 <3.13）。**两者都是可归因的确定结论**。
4. ⚠️ 若**全部**发送路径都不经 recv_daemon（例如某条直连分支），标记仍不会产生 ⇒ 探针诚实报 `degraded`（这是设计意图，不是缺陷）。
5. ⚠️ `probe_send_delivery` 的 `delivery_verified` 语义已由「标记条数」改为「**带服务端消息号的标记条数**」，
   历史库中若存在旧式盲标记，会被计入 `marker_without_msgid` 并在 `reasons` 中点名。
6. ⚠️ 本报告**未**改版本号（批末统一 `+0.01`）；**未** `git add/commit`。

---

## 六、实机验证步骤（交给用户，本轮未执行）

```bash
# 前置：清残留进程走 /quit；隔离环境 C:\temp\dyautodm_design
# ① 真实发送 1 条私信（任何既有入口：调度器/手动回复）
# ② 期望在 recv 日志看到三选一（可归因）：
grep -E "AUTH-032|AUTH-029|降级（解析器不可用）" C:\temp\dyautodm_design\logs\recv_daemon_*.log
# ③ 期望标记只在有消息号时落库：
#    sqlite3 <db> "select msg_type,msg_id,substr(text,1,30),json_extract(extra,'$.server_message_id')
#                  from dm_messages where msg_type='delivery_marker' order by id desc limit 5"
# ④ 探针：
#    python -c "import services.probe as P; print(P.probe_send_delivery('<账号>')['state'])"
#    期望：有消息号 → healthy；只回 OK → degraded（且 reasons 点名「无投递直接证据」）
```
