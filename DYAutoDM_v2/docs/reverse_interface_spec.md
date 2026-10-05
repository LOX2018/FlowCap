# douyin-dl 逆向情报：真实接口方式（方案 B）

> **性质**：闭源 `douyin-dl`（8,004,592 B，Mach-O arm64）字符串级逆向。
> **来源**：`better-douyin v1.1.18` macOS 便携包（SHA-256 已与官方 checksums 核对）。
> **协议**：Better Douyin Non-Commercial License —— 本文件**只记录架构事实与字符串证据**，
> 不含任何提取的代码、密钥、Cookie、Token。禁止外部分发或商业使用。
> **落点**：`design/better-douyin` 分支（用户 2026-09-14 定调：接口方式全按源项目，突破主分支铁律）。
> **方法**：Python 自建提取器（`D:\bdy_reverse\extract_strings.py` + `probe_anchors.py`），
> 不依赖 GNU strings。**全部为实测提取，非推断。**

---

## 一、真实接口清单（提取自二进制常量）

### 1.1 双域名策略
```
主域名:   https://www.douyin.com
备用域名: https://www-hj.douyin.com     ← ★ 同一接口两域名并行
IM域名:   https://imapi.douyin.com      ← IM 业务独立域名
WS:       wss://frontier-im.douyin.com/ws/v2
```

### 1.2 内容侧接口（实测完整清单）
```
# —— 主域名 www.douyin.com ——
/aweme/v1/web/query/user
/aweme/v1/web/user/profile/self/
/aweme/v1/web/aweme/post/
/aweme/v1/web/aweme/detail/
/aweme/v1/web/multi/aweme/detail/
/aweme/v1/web/general/search/stream/
/aweme/v1/web/discover/search/
/aweme/v1/web/tab/feed
/aweme/v2/web/module/feed
/aweme/v1/web/notice/                 (+ notice_list_v2 / notice_list)
/aweme/v1/web/im/user/info/           ← 昵称（本项目红线，见§六）
/aweme/v1/web/im/upload/config/v2
/aweme/v1/web/comment/list/
/aweme/v1/web/comment/list/reply/
/aweme/v1/web/comment/digg
/aweme/v1/web/comment/publish
/aweme/v1/web/commit/item/digg/
/aweme/v1/web/commit/follow/user/
/aweme/v1/web/aweme/favorite/
/aweme/v1/web/aweme/collect/
/aweme/v1/web/aweme/listcollection/
/aweme/v1/web/mix/listcollection/
/aweme/v1/web/mix/listcollection/mix_infos
/aweme/v1/web/series/aweme/
/aweme/v1/web/collects/list/
/aweme/v1/web/collects/video/list/
/aweme/v1/play/media     /aweme/v1/playwm
/service/2/abtest_config/
/passport/safe/get_identity_security_token
/passport_auth_status
```

> **读列表 vs 读详情走不同域名**（实测字符串相邻关系）：
> `comment/list` → **www-hj**；`comment/publish` → **www**（主）
> `aweme/post`（用户作品）→ **www**；`listcollection`（收藏）→ **www-hj**
>
> ⚠️ **2026-09-14 修正**：此前本文件曾写「写操作用 www」，**该判断有误**。
> 原始字符串证据（`all_strings.txt`）显示源项目二进制中**互动写操作同样走 www-hj**：
> ```
> https://www-hj.douyin.com/aweme/v1/web/commit/item/digg/     ← 点赞
> https://www-hj.douyin.com/aweme/v1/web/commit/follow/user/   ← 关注
> https://www-hj.douyin.com/aweme/v1/web/comment/digg          ← 评论点赞
> https://www-hj.douyin.com/aweme/v1/web/aweme/collect/        ← 收藏
> https://www-hj.douyin.com/aweme/v1/web/comment/list/         ← 评论列表
> https://www-hj.douyin.com/aweme/v1/web/comment/list/reply/   ← 评论回复
> https://www-hj.douyin.com/aweme/v1/web/im/user/active/status/ ← 在线状态
> https://www-hj.douyin.com/aweme/v1/web/im/spotlight/relation/ ← 关系
> https://www-hj.douyin.com/aweme/v1/web/series/aweme/          ← 合集作品
> https://www-hj.douyin.com/aweme/v1/web/mix/listcollection/    ← 收藏合集
> https://www-hj.douyin.com/aweme/v1/web/aweme/favorite/        ← 我的收藏
> https://www-hj.douyin.com/aweme/v1/web/aweme/listcollection/  ← 收藏夹作品
> ```
> 即 **www-hj 承载"互动与列表"类接口（读+写皆有）**，`www` 承载
> 搜索/详情/主页/发布等（`search/item`、`aweme/detail`、`comment/publish`）。

### 1.3 IM 侧接口
```
https://imapi.douyin.com/v1/message/send
https://imapi.douyin.com/v1/message/get_by_conversation
https://imapi.douyin.com/v1/message/get_user_message
https://imapi.douyin.com/v2/conversation/create
wss://frontier-im.douyin.com/ws/v2       ← 长连接（pbbp2 + protobuf）
```

---

## 二、签名与安全网关（接口方式核心）

### 2.1 双通道签名
```
① Web 通道：a_bogus + x-bogus
   日志锚点: "Douyin signed relation request prepared: method= query_keys= query_hash= a_bogus_present= uifid_len="
   响应头:   x-secsdk-web-signature  /  x-secsdk-web-expire
   错误:     "HTTP error: web signature query was not prepared"

② CSRF 三件套: x-secsdk-csrf-request / x-secsdk-csrf-version / x-secsdk-csrf-token
   另有: x-ware-csrf-token / X-Ware-Csrf-Token
```

### 2.2 TicketGuard（403 风控对抗）
```
Cookie 侧参数:
  uid_tt / uid_tt_ss
  bd_ticket_guard_client_data / bd_ticket_guard_client_data_v2
  bd-ticket-guard-client-data
  bd-ticket-guard-ree-public-key      ← REE 公钥（服务端下发）
  bd-ticket-guard-web-sign-type / -version / -web-version / -iteration-version

签名构造（实测字符串）:
  ticket=  path=  timestamp=          ← 三字段载荷
  ts_sign  req_content  req_sign       ← 产出字段
  ticket,path,timestamp                ← 签名输入三元组

报文侧:
  bd-ticket-guard-result               ← 守卫结果（响应校验）
  bd_passport_security_gateway
```

### 2.3 ★ 安全网关错误标识（提取到的新情报）
```
RELATION_SECURITY_GATEWAY: HTTP 403      ← 关系类接口（关注/收藏）的独立网关
（日志形态）"... ID RELATION_SECURITY_GATEWAY: HTTP 403 Cookie ..."
```
> **重要**：关系类操作（collect/follow/digg）有**独立的 `RELATION_SECURITY_GATEWAY`**，
> 403 时明确归因，而非笼统"请求失败"。这是**关系操作用户级风控**的直接证据。

### 2.4 浏览器身份三元组（HTTP 层伪造）
```js
// 实测还原的取值逻辑（内嵌 JS）
verify_fp: readCookieValue("s_v_web_id") || readCookieValue("verifyFp")
           || readResourceQueryValue(["verifyFp","fp"])
ms_token:  readCookieValue("msToken") || readResourceQueryValue(["msToken"])
           || url.searchParams.get("msToken")
if (!verifyFp && !msToken) return;    // 二者皆无则放弃
// 另有: generatedsigner / uifid / webid / UIFID / __druidClientInfo
```

### 2.5 UA / sec-ch-ua 池（实测提取原文）
```
① Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko)
   Chrome/151.0.0.0 Safari/537.36 Edg/151.0.0.0
   sec-ch-ua: "Not=A?Brand";v="99", "Microsoft Edge";v="151", "Chromium";v="151"
② Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/117.0
③ Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15
   (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1
④ "Microsoft Edge";v="125", "Chromium";v="125", "Not.A/Brand";v="24"
⑤ "Not?A_Brand";v="24", "Chromium";v="", "Google Chrome";v=""
平台: os_name / os_version / engine_name / engine_version / browser_name / browser_version
      MacIntel / Win32 / Linux x86_64 / "Windows" / "Mac OS X" / "macOS"
```
**HTTP 层指纹字段**（与浏览器层对应，实测）：
```
screen_width / dy_swidth      screen_height / dy_sheight
cpu_core_num / device_web_cpu_core
device_memory / device_web_memory_size
fp / UIFID / uifid / webid / s_v_web_id
```

---

## 三、★ 频率控制实况（风控核心，**决定性发现**）

### 3.1 源项目**没有**通用请求节流器
实测搜索 `rate_limit` / `backoff` / `throttle` / `min_interval` / `Retry-After`(仅HTTP标准头) →
**0 命中**。**不存在**类似本项目 `browser_gate` / `_send_gate_acquire` 那样的全局闸门。

### 3.2 唯一的频率控制是**创作者监控的检查间隔**
```
user_interval_seconds          ← 每个监控目标的检查间隔
max_new_downloads_per_check
（前端侧约束）
CREATOR_MONITOR_MIN_INTERVAL_MINUTES     = 10
CREATOR_MONITOR_MAX_INTERVAL_MINUTES     = 24*60 = 1440
CREATOR_MONITOR_DEFAULT_INTERVAL_MINUTES = 60
```

### 3.3 ★ AI 自动化节流参数（完整清单，实测提取）
```
auto_send_delay_ms              ← 自动发送延迟（毫秒）
auto_send_max_chars             ← 单条最大字数
auto_scan_interval_seconds      ← 扫描间隔（秒）
auto_max_actions_per_run        ← 单轮最大动作数   ← ★ 关键风控参数
auto_require_context            ← 需上下文才动作
auto_min_digg_count / auto_min_comment_count / auto_min_play_count   ← 互动门槛
auto_match_keywords / auto_exclude_keywords                          ← 关键词准入/排除
auto_private_match_keywords / auto_private_exclude_keywords          ← 私信专用
auto_comment_* / auto_like_* / auto_collect_*                        ← 分动作关键词
auto_monitor_notices / friends / comments / feed                     ← 监控源开关
auto_send_comments / auto_send_private_messages                      ← 发送开关
auto_follow_back_on_new_follower / auto_like / auto_collect
auto_return_shared_media / _allow_images / _allow_videos
auto_return_shared_max_size_mb / auto_return_shared_max_media_count
```

> **判读**：源项目的风控策略是 **「按动作限速 + 关键词准入 + 互动门槛 + 单轮上限」**，
> 而非本项目的「全局发送闸门 + 指纹浏览器隔离」。
> **两者的风控哲学不同**：源项目靠"少做+精确做"，本项目靠"环境可信+闸门"。

### 3.4 重试机制
```
src/downloader/retry.rs          ← 独立重试模块（字符串证据）
"retrying"  /  "is-retry"  /  "retry-after"(HTTP标准头)
```
**无指数退避参数暴露**（`backoff` 0 命中）。

---

## 四、架构事实（源码模块树，从 panic 路径还原）

### 4.1 `src/api/`（16 模块，实测路径）
```
client.rs                    client_im.rs            client_im_friends.rs
client_collection.rs         client_im_history.rs    client_im_messages.rs
client_collection_folders.rs client_im_parse.rs      client_notice.rs
client_comments.rs           client_relations.rs     client_user.rs
client_content.rs            client_video.rs         im_proto.rs
client_feed.rs
```

### 4.2 `src/downloader/`（11 模块）
```
batch.rs  completion.rs  control.rs  downloaded_cache.rs  events.rs
filename.rs  image_media.rs  media_group.rs  media_request.rs
media_transfer.rs  quality.rs  retry.rs  streaming.rs  tasks.rs
request_policy.rs          ← 实测存在（"请求策略"）
```

### 4.3 其他关键模块
```
src/media_proxy_cache.rs     ← 媒体代理缓存
src/net_session.rs           ← 会话/凭证（含 hash= 日志）
src/im_listener.rs           ← IM 长连接
src/mcp.rs                   ← MCP 权限模型
src/automation.rs            ← 自动化执行器
src/history/mod.rs
```

### 4.4 MCP 配置字段（实测）
```
close_prompt_shown  preferred_port  allow_write_actions
require_confirmation  log_retention
```
```
CONFIRMATION_REQUIRED   confirm=true   ACTION_FAILED
```

### 4.5 配置文件与状态（实测）
```
config.json  better-douyin(目录)  tmp/
friend_chat_state.json  friend_chat_state_.json
history.json  failed-downloads.json
structures: RelationSignerConfig  AccountConfig  CreatorMonitorTarget
            CreatorMonitorConfig  FailedDownloadEntry  LocalWriteError
```

---

## 五、IM WebSocket 协议（实测）

```
端点:    wss://frontier-im.douyin.com/ws/v2
子协议:  pbbp2
扩展:    permessage-deflate; client_max_window_bits
方向字段: sender_uid / from_uid / senderId / current_uid / is_outgoing
         (本项目铁律"仅用 sender UID 判方向" ✅ 与源项目同构)
会话字段: conversation_id / conversationType / short_id / index_in_conversation
消息字段: msg_type / seq_id / server_message_id / serverId / ack_
空帧判别: zero_messages / empty_decoded_messages / unrecognized_frame
世代号:  account_generation / listener_epoch  ← 作废过期监听请求
```

---

## 六、与本项目铁律的冲突点（**必须由用户裁定**）

| 项 | 源项目做法 | 本项目铁律 | 冲突级别 |
|---|---|---|---|
| **昵称来源** | 主动请求 `/aweme/v1/web/im/user/info/` | 🔴 禁主动批量查，仅 BCC 被动 | **最高** |
| **请求出口** | 纯 HTTP + 签名伪造（无浏览器） | 指纹浏览器 BCC 代发 | **高** |
| **频率控制** | `auto_max_actions_per_run` + 扫描间隔 | 全局发送闸门（8s 最小间隔） | 中 |
| **401/403 处理** | `RELATION_SECURITY_GATEWAY` 归因 | AUTH-050 UID 漂移→重捕获 | 中 |
| **凭证** | Cookie 读 + identity token | BCC 保活回写 + 门禁 | 中 |
| **无头策略** | 无关（无浏览器） | 默认真无头（保留项） | 无 |

---

## 七、方法论沉淀

| 手法 | 说明 |
|---|---|
| **不依赖 GNU strings** | Windows/MSYS 环境缺 strings/objdump；用 Python 扫描可打印串，**同样有效**（提取 15,883 条唯一串） |
| **锚点上下文切片** | 相邻字符串在同 struct/日志格式串里，取 ±260B 窗口可还原**字段归属关系**（如 `ticket=&path=&timestamp=` 后面紧跟 `ts_sign`） |
| **源码模块树免费获得** | Rust release 保留 panic 路径 `src/*.rs` → 完整模块树 |
| **配置字段名即是功能清单** | `auto_*` 一串字段名 = AI 自动化功能全集（比读代码快） |
| **"不存在"也是情报** | `rate_limit`/`backoff` 0 命中 → 源项目没有通用节流器，这本身是重要结论 |

---

*本文件为 `design/better-douyin` 分支的接口方式基准，属技术情报记录，非代码资产。*
*提取工具：`D:\bdy_reverse\extract_strings.py`、`D:\bdy_reverse\probe_anchors.py`（仓库外，不入库）。*
