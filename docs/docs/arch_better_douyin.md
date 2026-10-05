# better-douyin 架构逆向情报（v1.1.18）

> **性质**：第三方闭源发行版的二进制情报分析。
> **来源**：官方 Release `v1.1.18`（SHA-256 已与官方 `checksums.sha256` 逐字节核对通过）。
> **协议**：Better Douyin Non-Commercial License —— 本文件**只记录架构事实与字符串证据**，
> 不含任何提取的代码、密钥、Cookie、Token。仅作本项目技术参考，禁止外部分发或商业使用。
> **落点**：本分支为全新架构试验田，允许突破既有铁律。
> **建立日期**：2026-09-14

---

## 一、进程与产物形态

| 项 | 值 |
|---|---|
| 安装包 | `better-douyin-v1.1.18-windows-x64-installer.exe`（9,512,253 B，NSIS 自解压） |
| SHA-256 | `8eb54b40a9bec90250e14cb51918eda61fe7b7e65bb07c3b45d84a671e194d6f` ✅ 与官方一致 |
| macOS 便携包 | `...-macos-arm64-portable.zip`，仅 8 条目 |
| 应用主二进制 | `better-douyin`（14,645,776 B，Mach-O arm64） |
| **伴生二进制** | **`douyin-dl`（8,004,592 B，Mach-O arm64）— 独立后端进程** |
| Bundle ID | `com.anyujia.better-douyin` |
| 运行时 | **Tauri 2.10.3**（`tauri-2.10.3/src/async_runtime.rs`），Rust + WebKit |

**关键发现：双进制架构** —— 桌面壳 `better-douyin` 负责 UI/窗口/命令分发，另有一个
**独立的 `douyin-dl` 后端进程**承载平台业务。前端资源内嵌于主二进制（Tauri 默认）。

---

## 二、真实 Rust 源码架构（从 rustc panic 路径还原）

二进制中保留了编译期的 `src/<模块>.rs` 字符串，**等于还原了完整模块树**：

### `src/api/` —— 平台接口层（16 个模块）
```
client.rs                    client_im.rs              client_im_friends.rs
client_collection.rs         client_im_history.rs      client_im_messages.rs
client_collection_folders.rs client_im_parse.rs        client_notice.rs
client_comments.rs           client_relations.rs       client_user.rs
client_content.rs            client_video.rs           im_proto.rs
client_feed.rs
```

### `src/commands/` —— Tauri 命令层（18 个模块）
```
ai.rs            content_collection.rs   content_relations.rs   creator_monitor.rs
config.rs        content_comments.rs     content_user.rs        download_files_cmd.rs
content_feed.rs  content_video.rs        downloads.rs           friends.rs
history.rs       login.rs                mcp.rs                 notices.rs
system.rs        update_cmd.rs
```

### `src/downloader/` —— 下载子系统（11 个模块）
```
batch.rs         completion.rs    control.rs      downloaded_cache.rs  events.rs
filename.rs      image_media.rs   media_group.rs  media_request.rs
media_transfer.rs quality.rs      requeue?(streaming.rs)  streaming.rs  tasks.rs
```

### 其他
- `src/download_payload.rs`、`src/im_listener.rs`、`src/mcp.rs`、`src/history/mod.rs`

**架构判读**：分层非常清楚 —— `api/`（协议）→ `commands/`（Tauri 边界）→
`downloader/`（独立流水线），IM、下载、MCP 各自成子系统。

---

## 三、抖音接口清单（实测提取）

### 内容侧（`www.douyin.com` | `www-hj.douyin.com` 双域名）
```
/aweme/v1/web/query/user                    /aweme/v1/web/user/profile/other/
/aweme/v1/web/user/profile/self/            /aweme/v1/web/user/following/list/
/aweme/v1/web/aweme/post/                   /aweme/v1/web/aweme/detail/
/aweme/v1/web/multi/aweme/detail/           /aweme/v1/web/general/search/stream/
/aweme/v1/web/discover/search/              /aweme/v1/web/tab/feed
/aweme/v2/web/module/feed                   /aweme/v1/web/aweme/favorite/
/aweme/v1/web/aweme/listcollection/         /aweme/v1/web/aweme/collect/
/aweme/v1/web/mix/listcollection/           /aweme/v1/web/mix/listcollection/mix_infos
/aweme/v1/web/collects/list/                /aweme/v1/web/collects/video/list/
/aweme/v1/web/series/aweme/                 /aweme/v1/web/comment/list/
/aweme/v1/web/comment/list/reply/           /aweme/v1/web/comment/digg
/aweme/v1/web/comment/publish               /aweme/v1/web/commit/item/digg/
/aweme/v1/web/commit/follow/user/           /aweme/v1/web/notice/notice_list_v2
/aweme/v1/play/media                        /aweme/v1/playwm
/service/2/abtest_config/
```

### IM 侧（`imapi.douyin.com` + WS）
```
https://imapi.douyin.com/v1/message/send
https://imapi.douyin.com/v1/message/get_by_conversation
https://imapi.douyin.com/v1/message/get_user_message
https://imapi.douyin.com/v2/conversation/create
/aweme/v1/web/im/user/info/                 ← 用户资料（含昵称）
/aweme/v1/web/im/spotlight/relation/        ← 关系
/aweme/v1/web/im/user/active/status/        ← 在线状态
/aweme/v1/web/im/upload/config/v2           ← 图片上传配置
/aweme/v1/web/im/...recent_interactions
wss://frontier-im.douyin.com/ws/v2          ← ★ IM 长连接
```

### 认证
```
/passport/safe/get_identity_security_token
/passport_auth_status   （sid_guard / sid_tt）
```

---

## 四、签名与风控机制（核心情报）

### 4.1 双通道签名
```
① Web 接口通道：a_bogus
   字符串证据：
     "a_bogus"
     "Douyin signed relation request prepared: method= query_keys= query_hash= a_bogus_present= uifid_len="
     "Douyin signed relation request rejected: method="
     "x-secsdk-web-signature"  "x-secsdk-web-expire"  "x-secsdk-csrf"
     "failed to inject relation signer probe:"
   → **一个"签名器注入"（relation signer probe）**，即把签名逻辑注入到某个 JS 环境执行

② IM 接口通道：protobuf + 票据
   字符串证据：
     "Douyin IM protobuf request blocked before send: path= missing_browser_identity="
     "Douyin IM protobuf request rejected: path= elapsed_ms= ticket_guard_result_present= passport_gateway_present="
     "bd-ticket"   "bd_ticket"
     "IM protobuf request completed: method=POST url="
   → 发送前有**票据守卫（ticket guard）+ passport 网关校验**，缺失 browser identity 直接拦截
```

### 4.2 浏览器身份指纹（实测提取的取值逻辑）
从二进制的内嵌 JS 中还原出**身份三元组**的读取逻辑：
```js
verify_fp: readCookieValue("s_v_web_id") || readCookieValue("verifyFp")
           || readResourceQueryValue(["verifyFp","fp"])
ms_token:  readCookieValue("msToken") || readResourceQueryValue(["msToken"])
           || url.searchParams.get("msToken")
判定条件： if (!verifyFp && !msToken) return;   // 二者皆无则放弃
```
- 命中来源：**Cookie** 与 **资源 URL 查询串**两条通路（双通道取指纹）
- 另有 `window.__dyRelationImIdentityListeners` / `window.__dyRelationDtraitListeners`
  —— **监听页面对 `__dyRelationImIdentity` / `__dyRelationDtrait` 的赋值**，
  即**被动 hook 前端自身的身份/签名产出**，而非自算。

### 4.3 User-Agent 指纹池（多套伪装）
```
桌面 Edge 151:  Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) ... Edg/151.0.0.0
Windows Edge:   Not=A?Brand";v="99", "Microsoft Edge";v="151", "Chromium";v="151"
iOS Safari:     Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) ... Mobile/15E148
Firefox 117:    Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/117.0
移动端字段:      device_platform/aid/channel_pc_web/pc_client_type/cookie_enabled
```

### 4.4 IM WebSocket 协议细节
```
端点:   wss://frontier-im.douyin.com/ws/v2
扩展:   permessage-deflate; client_max_window_bits
子协议: pbbp2
请求头: Pragma / Cache-Control
```
**连接管理日志（可复用为设计参考）**：
```
"Ignoring stale IM listener ensure request"
"Douyin IM WebSocket session changed; restarting listener"
"Douyin IM WebSocket listener exited; reconnecting in ..."
"Douyin IM websocket duplicate ignored: conversation= message"
"Douyin IM websocket message: conversation= text_len= initial_sync="
"zero_messages / empty_decoded_messages / unrecognized_frame"   ← 空帧判别
字段:  account_sec_uid / account_generation / listener_epoch
```
**方向判定（与 FlowCap 铁律同构）**：
```
"sender_uid"  "current_uid"  "is_outgoing"  "direction: out"
```

### 4.5 图片解密（IM 表情/图片）
```
"media proxy failed to decrypt IM image, returning raw response:"
"media proxy failed to read encrypted image body:"  "cacheable image"
"aes_chunk_size"  "524288"        ← 512KB 分块
policy-set / check,thumb,medium,large
cache_v2 / "cipher_v2" / PolicyParams
"imagex" / "media_urls" / "live_photos" / "live_photo_urls"
image_urls / video_url / preview_addr / dash_addr
"x-amz-content-sha256"             ← S3 风格签名（媒体下载）
```
→ IM 图片是**AES 分块加密**，由**本地 media proxy** 解密后再交给前端渲染。

---

## 五、`douyin-dl` 后端二进制（决定性发现）

### 5.1 它不是 Python —— 是 Rust 编译的第二个二进制

`douyin-dl`（8,004,592 B）**不是打包的 Python**（无 PyInstaller/Nuitka/_MEIPASS/libpython 痕迹），
而是**与主壳同一 Cargo 工程的另一个 bin target**：

```
私有仓库路径（二进制残留，实测样本）：
  /Users/runner/work/better-douyin-private/better-douyin-private/src-tauri/src/...
CI 构建环境：GitHub Actions runner (macOS, aarch64-apple-darwin)
```

**这确认了公开仓库的 `Open Shell` 说法是真实的**：真实工程名为
`better-douyin-private`，从未公开。公开仓库只是抽出的 UI 壳。

### 5.2 CLI 入口定义（实测原文）

```
二进制名:  douyin-dl
源文件:    src/bin/douyin-dl.rs
说明:      "Better Douyin MCP server"
           "Starts the Better Douyin MCP server over stdio.
            Other automation entrypoints are intentionally not exposed."
子命令:    serve  →  "Serve MCP over stdio"
协议:      MCP JSON-RPC over stdio，protocolVersion 2025-03-26
```

> **设计要点**：它把 MCP 作为**独立二进制**发布，且**刻意只暴露 MCP 一个入口**
> （"Other automation entrypoints are intentionally not exposed"）——
> 用进程边界 + 单一入口收紧自动化攻击面。这是它的安全设计选择。

### 5.3 独有模块（主壳中不存在）

```
src/media_proxy_cache.rs      ← 媒体代理缓存
src/media_utils_download_items.rs
src/downloader/request_policy.rs   ← 请求策略（限流/节流中枢）
src/downloader/downloader.rs
src/automation.rs             ← 自动化执行器（含 confirm=true / ACTION_FAILED）
src/download_files.rs
src/mcp.rs
```

### 5.4 ★ TicketGuard 机制（对抗 403 风控的核心）

这是它 v1.1.18 宣称"修复 403 风控"的真实实现。实测提取的**完整参数面**：

```
Cookie 侧：
  uid_tt / uid_tt_ss
  bd_ticket_guard_client_data / bd_ticket_guard_client_data_v2
  bd-ticket-guard-client-data
  bd-ticket-guard-ree-public-key        ← ★ REE 公钥（服务端下发）
  bd-ticket-guard-web-sign-type
  bd-ticket-guard-version
  bd-ticket-guard-web-version
  bd-ticket-guard-iteration-version

签名构造：
  ticket=  path=  timestamp=  ts_sign   ← ★ 三字段签名载荷
  签名输入 = ticket, path, timestamp

响应校验：
  bd-ticket-guard-result                ← 守卫结果回传校验
  HTTP 403, TicketGuard                 ← 403 时归因到 TicketGuard
  "TicketGuard EC"                      ← 使用 EC（椭圆曲线）算法分支
  invalid ticket keys length
  get_payload_public_key / get_payload_public_key_ec
  encoded_public_key / derive_public_key / derived key
```

**机制判读**：
1. 服务端通过 Cookie 下发 **REE 公钥**（`bd-ticket-guard-ree-public-key`）
2. 客户端用 **EC（椭圆曲线）** 对 `ticket + path + timestamp` 三元组做 `ts_sign`
3. 每个请求路径都要绑定 path 参与签名（**path 级签名，防重放**）
4. 服务端返回 `bd-ticket-guard-result` 校验结果，失败即 403 并标记 TicketGuard

> **这是抖音近年新增的风控层。对照结论：本项目已完整实现且更深入 —— 见 §6 核对结论。**

### 5.5 本项目 TicketGuard 实现对照（实机验证）

better-douyin 的二进制只能给出**参数面**；本项目 `backend/utils/bd_ticket.py`（66 行）
给出了**可运行的完整算法**，实测自测 `verify : PASS`：

| 项 | better-douyin（二进制推断） | FlowCap（`bd_ticket.py`，实测） |
|---|---|---|
| 曲线 | "TicketGuard **EC**" 分支 | **NIST P-256**（`ecdsa.NIST256p`） |
| 哈希 | 未知 | **SHA-256** |
| 编码 | 未知 | **DER**（`sigencode_der`） |
| 签名载荷 | `ticket= path= timestamp=` | `f"ticket={ticket}&path={api}&timestamp={ts}"` ✅ 同构 |
| 客户端数据结构 | `ts_sign` / `req_content` / `req_sign` | 完全相同 + `timestamp` |
| 编码传输 | 未知 | `base64.urlsafe_b64encode` |
| REE 公钥 | `bd-ticket-guard-ree-public-key`（服务端下发） | `get_ree_key()` 生成 `04‖X‖Y` base64 ✅ |
| 落点 | — | `builder/header.py:19,21`、`login_api.py:1130,1195-1197` |

**字段级覆盖率实测：6/6 完全覆盖**（ticket / path / timestamp / ts_sign / req_content / req_sign）

> **修正记录**：本文档初稿曾判定本项目存在"TicketGuard 缺口"，经实机核对
> **该判断错误，已撤回** —— 本项目该层比 better-douyin 更完整。


### 5.6 Passport 网关

```
/passport/safe/get_identity_security_token
/passport_jssdk_version  /passport_jssdk_type  is_from_ttaccountsdk
PassportGateway（命名模块）
passport_csrf_token / passport_csrf_token_default
identity_security_token / identity_security_device_id / identity_security_aid
x-tt-passport-trace-id / x-tt-passport-csrf-token
```
→ IM 发送链路需先过 **Passport 网关**取 identity security token，再进 imapi。

### 5.7 其他提取要点

```
CSRF 三件套：x-secsdk-csrf-request / x-secsdk-csrf-version / x-secsdk-csrf-token
x-ware-csrf-token / X-Ware-Csrf-Token
媒体：x-amz-content-sha256 + AWS4-HMAC-SHA256 / cn-north-1 / vod / aws4_request
      直传 VOD（火山引擎），S3 风格签名
图片解密：cipher_v2 / PolicyParams / policy-set / check,thumb,medium,large
          aes_chunk_size = 524288 (512KB)
身份正则：user_unique_id=(\d+)  webid=(\d+)
方向字段：sender_uid / from_uid / senderId / current_uid / is_outgoing
下载落盘：bakconfig.json / found / .downloaded / download_record.json
          friend_chat_state.json / history.json / failed-downloads.json
自动化：confirm=true / ACTION_FAILED
```

---

## 六、与 FlowCap 的架构对照

| 维度 | better-douyin | FlowCap | 判读 |
|---|---|---|---|
| 桌面壳 | Tauri 2.10.3 + Rust，**双进制**（主壳 + douyin-dl 后端） | Tauri 2 + Python sidecar 后端 | **思路同构** |
| UI | React 19 + Vite + Tailwind 4 + Zustand | React + Vite | 可参考其组件组织 |
| 签名获取 | 注入 signer probe + 监听 `__dyRelationImIdentity`（**被动 hook 前端**） | 指纹浏览器 + 前端 hook | ★ **同源思路，互相印证** |
| IM 通道 | `wss://frontier-im.douyin.com/ws/v2`（pbbp2 + protobuf） | BCC hook + WS 骨架 | 可对照协议细节 |
| 方向判定 | `sender_uid === current_uid` | 铁律：仅用 sender UID | ✅ 完全一致 |
| 昵称来源 | `/aweme/v1/web/im/user/info/` + 本地 `uidNameCache` | 铁律：仅 BCC 被动 hook，**禁主动批量查** | ⚠️ **路线分歧，本项目不采纳** |
| **TicketGuard** | 二进制仅可见**参数名**（`bd-ticket-guard-*`、`ts_sign`、EC 分支） | **`backend/utils/bd_ticket.py` 完整实现**：NIST **P-256** + SHA-256 + DER 签名；构造 `ticket=&path=&timestamp=` → `ts_sign`/`req_content`/`req_sign` → base64url；`ree-public-key` 回传 | ✅ **本项目更完整** |
| a_bogus | web 通道自算签名 | `backend/utils/ab_pure.py` 已实现 | ✅ 对等 |
| CSRF | x-secsdk-csrf 三件套 | `generate_csrf_token`（`builder/header.py`） | ✅ 对等 |
| 图片 | 本地 media proxy AES 解密 | p*-sign AES-256-GCM | 同题 |
| 下载 | 独立 `downloader/` 11 模块 | downloader 子系统 | 可参考流水线切分 |
| MCP | 本机 HTTP + Bearer + 只读默认 + 写确认；独立二进制 + 单 stdio 入口 | 尚未建设 | **可借鉴其权限模型** |
| 错误体系 | 未见统一错误码体系（日志为主） | 有统一报错体系 | **本项目更强** |

> **核对结论（实测，非推测）**：本项目在**签名/风控层与 better-douyin 处于同一水平或更强**
> —— TicketGuard 已有可运行的 P-256 实现（`bd_ticket.py` 自带 `__main__` 自测），
> 而 better-douyin 的二进制反而只能看到参数面。**不存在"TicketGuard 缺口"**。

---

## 七、可借鉴要点（按价值排序）

1. **★ TicketGuard 缺失警报**：本项目凭证体系**未见** `bd-ticket-guard-*` 参数处理。
   若触及 403/风控，这是首要排查对象（详见 5.4）。
2. **IM 会话恢复机制**：`account_generation` + `listener_epoch` + "stale listener ensure"
   —— 用**世代号**判别并作废过期的监听请求，避免跨会话窗口串号。
3. **empty-frame 判别**：`zero_messages / empty_decoded_messages / unrecognized_frame`
   三分法，比"消息数为 0 就报错"更精确。
4. **媒体代理层**：把"加密取回 → 本地解密 → 喂前端"收敛到一个 proxy
   （`media_proxy_cache.rs`），前端只拿明文 URL，契约定为 `mediaProxyUrl()`。
5. **MCP 权限模型**：只读默认 + `allow_write_actions` + `require_confirmation`
   + Token 轮换即时失效 + 日志脱敏；并以**独立二进制 + 单一 stdio 入口**收紧面。
6. **下载流水线切分**：`media_request`（取址）→ `media_transfer`（传输）→
   `quality`（选质）→ `tasks`（状态机）→ `completion`（收尾）+ `request_policy`（限流中枢）。
7. **双域名策略**：`www.douyin.com` 与 `www-hj.douyin.com` 并行使用。

---

## 八、明确不采纳项

| 项 | 原因 |
|---|---|
| `/aweme/v1/web/im/user/info/` 拉昵称 | **触碰本项目风控红线**（用户铁律：仅被动 hook，禁主动批量查） |
| 自算 `a_bogus` 签名 | 长期消耗战；本项目走指纹浏览器 hook 路线 |
| 注入式 signer probe | 与本项目 BCC 容器路线冲突 |
| 任何提取的代码/密钥 | 许可禁止；本文件仅记录事实 |

---

## 九、证据与局限

**已验证**：
- Release 包 SHA-256 与官方一致（`8eb54b40…194d6f`）
- 双进制、Rust 模块树、私有仓库路径：二进制字符串实测
- 接口清单、签名标记（含 TicketGuard 全参数面）、指纹逻辑、WS 协议参数：二进制实测提取

**未验证 / 局限**：
- Rust 侧**无法反编译回源码**（非字节码）；本文是**字符串级情报**，非源码还原。
- 内嵌前端 JS 是 minified 产物，仅提取到片段（身份读取逻辑因保留可读代码，可信度高）。
- **未在真机运行该应用**，实际运行时行为未实测。
- TicketGuard 的**算法细节**（EC 曲线参数、REE 公钥格式、ts_sign 具体编码）**未知**，
  仅知参数名与流程骨架。
- 提取的**接口/参数名是 2026-09 快照**，抖音侧随时可能变更。

---

## 十、本次逆向的方法论沉淀

| 手法 | 说明 |
|---|---|
| NSIS 包解包 | NSIS 自解压**不含**标准 7z 尾签名（`37 7A BC AF 27 1C` 未命中），需另寻途径；**改用 macOS 便携 zip**（Tauri 产物同构且结构干净）更高效 |
| 交叉平台取证 | **拿某平台的包分析另一平台的产物** —— macOS portable zip 只有 8 条目，却因此暴露了双进制结构 |
| 字符串即架构 | Rust release 二进制保留编译器 panic 路径（`src/*.rs`），**等于免费获得模块树** |
| 比 Python 更易 | 若为 PyInstaller 打包，`.pyc` 可反编译回近似源码；Rust 只能到字符串级 —— **但字符串级已足够还原架构与协议** |
| 定位敏感串 | 以 `bd-ticket-guard`、`src/`、`wss://`、`a_bogus` 为锚点做上下文切片，比全量 grep 高效 |

---

*本文件为分支设计基准，属技术情报记录，非代码资产。*
