# 调试案例归档：私信发送 create_conversation 返回 INVALID_REQUEST（所有已存在会话发不出）

**日期**：2026-09-19
**版本**：v0.43.96（设计分支 dyautodm_design，运行实例 DYAutoDM_v2_0.43.96.exe）
**严重度**：fatal（手动发送 + AI 自动回复全链路发送层失效，两个账号实测均失败）
**状态**：Root Cause 已定位，修复方案已提出，待用户拍板实施（架构级改动）

---

## 一、设计意图（Design-First）

`daemon/recv_daemon.py` 的 `POST /send` 端点契约：
- **前置条件**：调用方传入 `account` + `conv_id`（形态 `0:1:uidA:uidB`）+ `text`，端点经统一发送闸门后，用该账号凭证把消息发给对端。
- **预期行为**：对已存在会话应「回复」（复用既有会话），仅对全新陌生会话才「创建会话」。发送成功则在 `dm_messages` 落 `role=me` 记录。
- **数据契约**：`send_msg(auth, conversation_id, conversation_short_id, ticket, content)` 三要素（conversation_id/short_id/ticket）必须来自抖音；其中 ticket 只能由 `create_conversation` 返回或会话帧提供。

## 二、观测偏差（Observed Deviation）

- **偏差类型**：resource / protocol（抖音协议层拒绝）
- **偏差点**：`recv_daemon.py:1565` 与 `1617`（`/send` 与 `/send_by_uid`）
- **偏差描述**：
  - 调用 `/api/messages/send`（channel=ws）→ 经 `dm_dispatch.submit` → recv_daemon `/send` → 无条件 `DouyinAPI.create_conversation(auth, peer_id)`。
  - 抖音返回 `{'cmd':609,'message':'INVALID_REQUEST','body':{}}`（业务错误，非 HTTP 错误）。
  - 异常被 `client_im.create_conversation` 抛 RuntimeError，经 `[RECV-018]` 捕获返回 `ok=False`。
  - **DB 硬证**：`dm_messages` 中该会话在 22:28 之后无任何 `role=me` 新记录 → 确属真拒发，非"发了但日志误报"。
  - 两个账号（四川工伤张老师、尚进工伤小助理）对已存在会话均失败，证明与具体账号无关，是端点逻辑缺陷。

## 三、错误码（DSSCC）

```
ERR-SEND-CREATE-INVALID-REQUEST
severity=fatal
scope=recv_daemon /send 与 /send_by_uid（所有已存在会话）
```

## 四、执行链追溯（Execution-Chain Traceability）

```
用户/AI worker
  → api/messages.py /send (channel=ws)
    → services/dm_dispatch.submit(account, conv_id, text, "manual"/"ai", 0)
      → per-account 串行调度器 _send_one
        → recv_daemon /send (port=recv_daemon_port(account))
          → DouyinAPI.create_conversation(auth, int(peer_id))   ← 无条件调用
            → 抖音 imapi.douyin.com/v2/conversation/create
              → 返回 {'cmd':609,'message':'INVALID_REQUEST'}  (会话已存在，拒绝重复创建)
          → [RECV-018] 回复失败: create_conversation 响应缺少 create_conversation_v2_body ...
```

**关键发现**：`git log -S create_conversation` 证实 `/send` 自 2026-08-14（af5934b）起**一直无条件先 create 再 send**，并非 0.43.96 新引入回归——属**长期设计缺陷**，只是此前被测会话在抖音侧尚未存在（首次 create 成功）掩盖了问题；现会话已存在即暴露。

## 五、根因分析（RCA）

1. **直接原因**：抖音 `create_conversation` 接口对**已存在会话**返回 `INVALID_REQUEST`（抖音协议规则：不能重复创建已有会话）。
2. **架构原因（根）**：系统**没有缓存已存在会话的 `conversation_id/short_id/ticket`** 供 `send_msg` 直发复用：
   - `dm_conversations` 表只存 `conv_id`（`0:1:uidA:uidB` 本地形态）、`peer_id`，`short_id` 多数 None，`conversation_id`（抖音长数字）与 `ticket` **完全未持久化**。
   - WS 会话同步帧（`_sync_conversations`）虽拿到抖音长数字 `conversation_id` 与 `short_id`，但**仅建内存骨架、未落 DB**，且内存 key 用长数字而 `/send` 传入的是 `0:1:uidA:uidB`，两者查不到同一会话。
   - 因此 `/send` 拿不到直发三要素，退化为"每次都 create"，撞上抖音对已存在会话的拒绝。
3. **文档归因错误（doc-rot）**：`工作记忆/03_私信收发链路.md` 行 72 把 `INVALID_REQUEST` 归类为 `credential`（凭证失效），建议"重新扫码"。**实测否定该归因**：连接正常、WS 心跳正常、两账号均失败、仅 create 步骤失败——与凭证无关。该误归因会误导后续排障（让人重扫而非修代码），须修正。

## 六、实机验证（Live Verification）

- 环境：设计分支 dyautodm_design，启动 DYAutoDM_v2_0.43.96.exe（backend PID 17216），张老师 recv 连（port 12687，conv_count=280）、小助理 recv 连（port 12726，conv_count=83）。
- AI 链路验证：`/api/ai/config` 补 `api_key`（FreeLLM unified key）+ `enabled=true` → WORKER running=True → `/api/ai/test` 返回 glm-5.2 真实回复「你好！请问有什么我可以帮你的吗？」✅ AI 识别与生成全通。
- 发送验证：
  - 小助理→张老师会话 `0:1:316276709526638:3887506227210423`：`/api/messages/send` 返回 `两条通道均失败：WS=create_conversation...INVALID_REQUEST`。
  - 张老师→小助理会话 `0:1:3887506227210423:316276709526638`：同样 INVALID_REQUEST。
  - DB 硬核：两会话 22:28 后均无新 `role=me` 记录。
- 网关验证：FreeLLM 31415 需入站带 `unified_api_key`（否则 /v1/models 401）；补 key 后 models/chat 全通（glm-5.2 实测返回内容）。

## 七、修复实施（v0.43.97，2026-09-19 落地）

按「整体对齐源项目 2026 协议」实施，禁止局部补丁。改动清单：

| # | 文件 | 改动 | 对应根因 |
|---|---|---|---|
| 1 | `backend/builder/proto.py` | `build_normal_request` 对齐 2026 信封：去顶层 `token`、去 `ts_sign`、去 `sdk_cert`；`sdk_version=0.1.8`、`build_number=0d50935:feat/pc-im-groupB`、`version_code=360000`；`browser_name='Mozilla'`、`browser_version=UA去Mozilla/`；proto `referer=douyin.com/jingxuan`、`timezone_name=Asia/Shanghai`；去 `webid`/`fp`（旧字段） | 根因① |
| 2 | `backend/builder/proto.py` | `build_send_message_request` 对齐 2026：去顶层 `reuqest_sign`；新 ext 结构（`s:mentioned_users`/`s:client_message_id`/`s:stime=<ms>.<5随机>`）；新增 `identity_security_token`/`device_id` 参数写入 header map + `identity_security_aid=''` | 根因② |
| 3 | `backend/builder/header.py` | `with_bd` 对齐 2026：补 `bd-ticket-guard-web-sign-type`；`web-version` 走 `ticket_guard_version`（ts.1→1 其余→2）；移除旧 `iteration-version` 头；ticket 一致性校验（能力存在则强校验）；新增 `with_bd_readonly` | 根因② |
| 4 | `backend/utils/bd_ticket.py` | 新增 `ticket_guard_version(ts_sign)` | 依赖 |
| 5 | `backend/dy_apis/client_im.py` | 新增 `get_identity_security_token()`（调 `/passport/safe/get_identity_security_token/`，带 with_bd）；`send_msg` 加 `headers.with_bd('/v1/message/send')` + identity token 流程 + 2026 params 顺序（msToken→a_bogus→verifyFp→fp） | 根因② |
| 6 | `backend/services/conv_identity.py` | `infer_my_uid_from_conv_ids` 判据修正：去掉「位置数 ≤ 1」限制，改为「idx2/idx3 合计覆盖度最高者」 | 根因③ |
| 7 | `scripts/build_sidecar.py` | 补 `qrcode` hidden-import（探活回退必需） | 根因③附带 |
| 8 | `scripts/verify_im_protocol_2026.py`（新增） | 14 项协议不变量静态守卫，防回退旧信封 | 防回归 |
| 9 | 版本 6 处 | 0.43.96 → **0.43.97**（门禁 `check_version_sync.py` 通过） | 铁律·版本递增 |

### 已验证（实机/静态，均可复现）

- **静态守卫** `scripts/verify_im_protocol_2026.py`：14/14 PASS；**反向证明有效**（注入旧 `request.token` 立刻 FAIL，移除后 PASS）。
- **信封冒烟**：`build_normal_request(a, 609)` → `token=''`、`ts_sign=''`、`sdk_cert=''`、`sdk_version=0.1.8`、`version_code=360000` ✅
- **send 冒烟**：`msg_type=7`、无 `reuqest_sign`、header map 含 `{"token":"..."}`/device_id/aid ✅
- **with_bd 冒烟**：5 头齐全，`web-version=2`（ts.2 正确）、`web-sign-type=0`（ECDSA） ✅
- **my_uid 判据**（真实 DB 280/83 会话）：张老师→`3887506227210423`、小助理→`316276709526638`，均 PASS ✅
- **peer 解析**：修复后张老师账号对会话 `0:1:316276709526638:3887506227210423` 解析 peer=小助理（正确），修复前恒解析成自己 ✅
- **打包产物**：`qrcode` 已进 recv-daemon PYZ 归档（`list_archive_modules` 实测 OK）✅

### 待完成（部署实机验证）

- Tauri 主程序重建（`npx tauri build --no-bundle`）→ `scripts/deploy.py` 部署 → 重启设计环境 →
  **实机复测：小助理经 WS 给张老师发消息，确认 AI 回复真落 `role=me`**（最终验收判据）。

## 附：遗留（测试态，未改源码）
- 为实机验证 AI 链路，临时给设计分支运行态 AI 全局配置补了 `api_key`（FreeLLM unified key）并 `enabled=true`。
- 工作区有**并发写者**改动（`frontend/package.json` 加 `vite-plugin-singlefile`、`package-lock.json` 的 braces 等），本会话**未覆盖**，仅在 version 字段上做递增。

