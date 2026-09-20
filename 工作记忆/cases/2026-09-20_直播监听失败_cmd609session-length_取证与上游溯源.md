# 2026-09-20 直播监听失败：cmd 609 `unexepcted session length` 取证与处置建议

> 归属：DYAutoDM_v2 · design/better-douyin 分支
> 触发：用户报「直播监听失败」，并要求「检查源项目接口是否更新 github.com/cv-cat/DouYin_Spider」
> 性质：**取证 + ADR 建议**（本记录**不做修复**，因为根因在服务端校验契约，不在本仓代码）
> 状态：**已归因（账号级·会话只读）——修复待用户对张老师执行重新授权**

---

## 一、现象（本机实机日志，非推断）

日志：`DYAutoDM_v2/src-tauri/logs/run_20260920_210954.log`（22:04 两次启动失败）

```
22:04:26 | INFO  | [engine] 引擎启动中 live_url=404381082027
22:04:26 | INFO  | [live-ai] 私信文案已接入 AI 生成（账号=四川工伤张老师 agent=未绑定/用全局 档位=rag scopes=['dm','live','crawl']）
22:04:26 | INFO  | [auth] 凭证有效，跳过扫码
22:04:27 | ERROR | [AUTH-017] 私信签名预检异常: create_conversation 响应缺少 create_conversation_v2_body
                              message='unexepcted session length'
                              resp_json={'cmd': 609, 'sequence_id': '10140', 'message': 'unexepcted session length', 'body': {}}
22:04:27 | WARN  | [ENG-007] [engine] 启动未成功: 发送账号凭证失效
22:04:27 | INFO  | [引擎] 私信收尾：共捕获 0 条，实际成功发送 0 条，整体停止（状态=stopped）
```

### 附带确认（本轮同日志）
- ✅ **dm_pool 契约 422 已消失**（引擎真的进入了启动流程）；
- ✅ **AI 文案接线已生效**（`[live-ai]` 判定通过，scopes 含 live）；
- ❌ 被 **`create_conversation` 语义级拒绝**挡住 → 引擎按设计放弃启动。

错误串 `unexepcted session length`（含服务端原文拼写错误）在**本仓与上游仓全量 grep 均 0 命中**
⇒ 它是**抖音服务端原样返回的 message**，不是任何一侧代码的产物。

---

## 二、上游溯源（Open-Source Provenance）

上游 `cv-cat/DouYin_Spider` 仓库元数据：`pushed_at = 2026-09-19T17:36:53Z`（= CST 09-20 01:36），
`updated_at = 2026-09-20T12:55Z` —— **确实有新提交**。

| 上游提交 | 时间(CST) | 改动文件 | 主题 |
|---|---|---|---|
| `4479ea78` | 09-20 01:36 | `dy_apis/douyin_api.py` | Merge PR #91 feat/send-live-comment |
| `251075ec` | 09-20 01:26 | `dy_apis/douyin_api.py` | feat: align live room comment sending |
| `df52357d` | 09-20 01:33 | `dy_apis/douyin_api.py` | fix: align work comment publishing |
| `41ed6c52` | 09-19 14:36 | `douyin_api.py` / `dy_live/pk.py` / `PK.proto` … | feat: live PK rank APIs |

**关键结论：这 4 个提交全部不涉及 `create_conversation`（私信建会话）。**
逐行读过 `git diff 1712dad..4479ea7 -- dy_apis/douyin_api.py`（+63/−16）：只改
`sendMsgInRoom`（直播弹幕）与 `publish_comment`（作品评论）。

双方 `create_conversation` 实现**逐行等价**（同为 imapi `/v2/conversation/create`、
同 `HeaderType.PROTOBUF`、同 `referer='https://www.douyin.com/'`、**均不叠加 bd-ticket-guard**）。
`builder/proto.py` diff 后**功能等价**（唯一差异是 `VERSION_CODE`，两侧同为 `360000`）。

⇒ **上游并未对这条链路做接口变更**；本仓也**不是"抄漏了"**。

---

## 三、上游暴露的同类新要求（这才是"改版"的具体内容）

上游 commit `df52357d` 新增的两道校验（原文）：

> 评论发布是 bd-ticket-guard 的强校验写接口。调用方从同一个浏览器会话抓到的短时凭据
> 可以通过 kwargs 显式传入；**之前这些参数被静默忽略**，导致请求沿用旧 `.env` 或缺少 dtrait，
> 最终被服务端以二次验证/空响应拒绝。
>
> 评论发布需要**与当前 Cookie 同会话的 ticket/ts_sign**；请重新导出配套浏览器凭据，**不能混用旧 .env**。
> 评论发布需要同一浏览器会话的 **`dtrait_blob`/`profile` 或 `session_dtrait`**；**只提供 Cookie 会被风控拦截**。

上游 `utils/dtrait.py` 文件头（**权威说明**）：

> `x-tt-session-dtrait` 头的构造（纯算）。
> 抖音 web 的**高风控接口（评论、私信等）要求带该头，缺失会被 passport 判定为需要二次身份验证**
> （响应头 `X-Tt-Verify-Passport-Decision`，`verify_scene=comment`）。

上游 `builder/auth.py:994` `ticket_matches_session()` 则处理「ticket/ts_sign 与 cookie 是否同一次登录」。

---

## 四、本机实测（设计分支环境，零网络、零浏览器）

脚本读会员空间 `members/m17db0f8209156f26/auto_dm/accounts/<账号>/.env`（经 `member_ctx.parse_env_dict` 解密），
**只打印键名与长度，绝不打印任何秘密值**：

| 账号 | cookie 长度 | ticket | ts_sign | client_cert | private_key | `bd_ticket_guard_ts_sign_id` | ts_sign 匹配 | **dtrait_blob** | **session_dtrait** |
|---|---|---|---|---|---|---|---|---|---|
| 四川工伤张老师 | 6184 | 49 | 133 | 92 | 240 | 存在(20) | ✅ True | **缺失** | **缺失** |
| 尚进工伤小助理 | 6550 | 49 | 133 | 92 | 240 | 存在(20) | ✅ True | **缺失** | **缺失** |

### 由此**排除**与**确认**的假设

- ❌ **排除**「ts_sign 与 cookie 不同会话」：实测 `ts_sign.startswith(bd_ticket_guard_ts_sign_id) == True`。
- ✅ **确认** `dtrait_blob` / `session_dtrait` **两个账号都缺**。
- ✅ **确认**本仓**完全没有 dtrait 实现**：`utils/` 下无 `dtrait.py` / `dtrait_features.py`；
  `grep -rln dtrait DYAutoDM_v2/backend` 只命中 `builder/header.py` 与 `dy_apis/client_im.py` 的**注释**（说明"缺 dtrait 不阻断"），**没有构造器**。
  `builder/auth.py` 仅 88 行，**无 `session_dtrait_header()` / `dtrait_evidence()`**（上游 1298 行）。

---

## 五、诚实边界（不得当作已定论）

1. `create_conversation` 的请求体两侧等价 ⇒ 我**无法仅凭代码**证明该 609 的确切死因；
2. 上游**没有**针对私信建会话的接口变更记录；
3. 上游自己也没解决私信失效：
   - [#75 私信功能 不可用 并导致账号登录信息被清退](https://github.com/cv-cat/DouYin_Spider/issues/75)（**open**，2026-08-17）
     症状同型：建会话后发送报 `Wire format was corrupt` + **账号被踢下线**；作者无回复，评论者亦未解决；
   - [#64 私信对方收不到](https://github.com/cv-cat/DouYin_Spider/issues/64)（**open**，2026-07-05，0 评论）。
4. **本次失败账号 = 四川工伤张老师** —— 该账号本轮会话中**另有** `AUTH-050 uid 漂移` 记录
   （早前会话）；而本次日志显示本账号 uid 探活为 `3887506227210423`（与历史 conv_id 一致，非漂移）。
   故**不能**断言"只是账号失效"，也**不能**断言"与账号无关"——需一次实证才能归因（见下）。

---

## 六、待用户拍板的分叉（不擅自改代码）

用户的判断（原话）：「肯定是接口变了！抖音改版了」。据此，本记录给出两条互斥路线：

| 路线 | 做法 | 代价 | 何时选 |
|---|---|---|---|
| **A. 归因实验（推荐先做）** | 用【尚进工伤小助理】发一封信，观察是否同样 609 | 一次真实发送；若为全局性则无额外风险，若为账号级则消耗一次 | 任何时候；**这是唯一能区分「账号级失效」与「协议级失效」的手段** |
| **B. 补 dtrait 能力** | 移植上游 `utils/dtrait.py` + `dtrait_features.py` + `auth.session_dtrait_header()`，拿到该账号浏览器会话的设备档案后按 path 现算头 | 大（上游 auth.py 1298 行）；且需取得**同一次浏览器会话**的 dtrait 素材，长度/别处 hook 的记录**不能替代**（上游明确） | 若 A 证明是**协议级**（尚进也 609） |

**不做的事**：不凭"改版"这个判断直接大改协议层——那会重演本项目已记录的
「围绕现有代码打转、把未验证的推测当结论」的失败模式（见 skill §〇·丙′）。

### 建议的最小验证（路线 A 的具体判据）

```
1. 启动：C:\temp\dyautodm_design\dev.ps1
2. 直播页选策略（词库非空）→ 账号选【尚进工伤小助理】→ 开始自动私信
3. 只读核查（不干预）：
   - 若日志再次出现 [AUTH-017] + 'unexepcted session length'  ⇒ 协议级 → 走 B
   - 若预检通过、引擎进入 RUNNING/监听中                       ⇒ 账号级 → 张老师重新扫码即可
```

---

## 七、附：本记录产出的可复用判据

1. **错误串全 grep 0 命中（本仓 + 上游仓）= 它为服务端原文**，不是任何一侧代码生成的 ⇒ 优先按
   「服务端新校验」而非「我方代码 bug」处理；
2. **判断"上游是否改了", 必须 diff 到具体文件与行**，而不是看 `pushed_at` 变新：
   本次上游确有更新，但**改的是直播弹幕与作品评论**，与私信建会话无关；
3. **判定"我方是否缺某能力"要 grep 全仓**：本次 `grep -rln dtrait` 只命中**注释**
   （"缺 dtrait 不阻断"），若只看命中的文件就下结论会误判为"已有实现"；
4. **实测优先于推断**：我先假设过「ts_sign 与 cookie 失配」，实测 `True` 后**撤回**该假设，
   并把实测数据（而非假设）写进本记录。


---

## 八、归因结案（2026-09-20 深夜 · 实机 A/B 判别实验）

> 本节**推翻** §六 的「路线 B（补 dtrait）」作为本例根因，并给出可直接复现的判别判据。
> 新增工具：`DYAutoDM_v2/scripts/diag/diag_im_create_conversation.py`（一步归因，不猜）。

### 8.1 实验设计（比 §六 路线 A 更省、更强）

不做「换账号跑一次直播」的线上实验（那会真实发出一条消息），改为**离线直接复跑建会话链路**：
`create_conversation` 只**建/取会话**、**不投递任何消息**，可安全用作「写会话能力」探针。
两个账号、同一份代码、同一台机器、同一时刻，逐项对比：

| 账号 | ① 探活 uid | 与历史 conv_id 一致 | ② 610 读会话列表 | ③ 609 建会话 |
|---|---|---|---|---|
| 四川工伤张老师 | 3887506227210423 | ✅ True（291 条 conv_id） | ✅ 成功 | ❌ **609 unexepcted session length** |
| 尚进工伤小助理 | 316276709526638 | ✅ True（83 条 conv_id） | ✅ 成功 | ✅ 成功（conv 已建） |

**失败面范围**：张老师对 **self / 小助理 / 2645858429702251（日志里 AI 发送连续失败的 peer）**
**三个目标全部 609**；小助理对同样三个目标**全部成功**。

### 8.2 逐条排除（每条都有实测，非推断）

| 假设 | 实测 | 结论 |
|---|---|---|
| 协议级变更（抖音改版，需补 dtrait） | 同代码同机器，**小助理成功** ⇒ 与代码/协议无关 | ❌ 排除为**本例根因** |
| 请求体/信封构造差异 | 两账号请求序列化后逐字段一致（827B vs 825B，仅 uid 位数差）；`create_conversation`/`builder/proto.py` 与上游逐行等价 | ❌ 排除 |
| 上游近期改动波及本链路 | 上游 4 次提交只改直播弹幕/作品评论，**未碰 create_conversation** | ❌ 排除 |
| `ts_sign` 与 cookie 不同会话 | 两账号均 `True`（`ts_sign.startswith(bd_ticket_guard_ts_sign_id)`） | ❌ 排除 |
| cookie 键缺失（第三方工具同型文档称 `sessionid` 缺失） | 两账号 `sessionid/sessionid_ss/sid_guard/uid_tt/ttwid` **全部齐全** | ❌ 排除 |
| 旧 `.env` 凭证 vs 浏览器实时会话不同步 | 张老师 profile 的 `sessionid/sessionid_ss/bd_ticket_guard_ts_sign_id` 与 `.env` **逐一相同**（仅 `ttwid/odin_tt/bd_sso_hi3jfd/download_guide` 不同） | ❌ 与 609 无关（见下） |
| 把 profile 值替换进 cookie 即可修复 | **替换后仍 609**（判别实验，直接否证） | ❌ 排除 |
| uid 漂移（AUTH-050，早前日志 1149448411428128） | 本次实测 uid=3887506227210423，**与历史 conv_id 一致**，非漂移 | ❌ 排除（漂移系更早的独立事故） |

### 8.3 根因（Root Cause）

**账号级**：张老师的 web 会话在服务端被判定为「可读、不可写」——探活(身份读)、610(会话读) 均正常，
唯独 609(建会话=IM 写操作) 被以 `unexepcted session length` 拒绝；同代码下另一账号完全正常。
**不是**本仓代码缺陷、**不是**上游接口变更、**不是**协议缺 dtrait/签名素材（同端点同素材另一账号成功）。

> 术语澄清（避免后续误判）：早期会话里说的「账号级失效」若理解为「连读都读不了」是**不准确**的。
> 本例的账号级失效是**只读态**：读通过、写被拒。所以「探活能过 ⇒ 凭证有效」在**写会话**这一层**不成立**。

### 8.4 修复（A 类改动，需用户在本机 UI 操作一次）

**用户动作**：对**四川工伤张老师**执行【重新扫码】/重新授权，重新捕获**同一次浏览器会话**的
cookie + 四件套（ticket / ts_sign / client_cert / private_key）。
**不做**：不改协议层、不补 dtrait、不动 `create_conversation`（均无证据支持，且 A/B 已否证）。

**设计缺陷（建议后续修，非本次根因）**：`_verify_credential` 的反面
——`get_login_auth` 的浅校验只做 `get_my_uid`（身份**读**）就判「凭证有效，跳过扫码」，
**不校验写会话能力** ⇒ 「能读不能写」的会话会被判定为有效，直到引擎启动才在 609 处失败。
建议：把 `get_login_auth` 的浅校验升级为**与 `_verify_credential` 同档**（加一次 create_conversation），
使「登录态陈旧/只读」在授权阶段即被识别，而不是拖到启动监听时。

### 8.5 验收判据（重新授权后逐条过）

1. `python scripts/diag/diag_im_create_conversation.py` —— 张老师由「失败」转「成功」（工具自动给判定）；
2. 引擎日志出现 `[auth] 私信签名预检通过（uid=…）`，随后 `监听中`；
3. 真实发送后 `dm_messages` 落 `role=me`（**仅日志不足以宣称成功**，必须落库硬证）。

### 8.6 本记录新增的可复用判据

1. **IM 写链路（609/建会话）必须「跨两个账号 A/B」判型**：单账号失败无法区分「账号 vs 协议」，
   而 A/B 一次即可定论；用 `create_conversation`（不投递消息）做探针最省、最安全。
2. **「探活通过」只证明身份读，不证明写能力**：读(cmd 610/query-user)与写(cmd 609/imapi)是两条服务端通路，
   结论不可互相代替。
3. **照抄第三方工具的错误表要核实前置条件**：`douyin-im-export` 把 `unexpected session length` 归因
   「sessionid cookie 缺失」，而本例两账号 cookie 齐全 ⇒ 该表只覆盖「缺 cookie」一种形态，
   不可当作充分判据。
4. **`.env` 与 profile 的 cookie 可能部分不同步（如 `ttwid`），但需以判别实验证明确有因果**，
   不能见差异就断定是根因（本次替换实验直接否证）。
