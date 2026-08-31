# V2 BCC（浏览器容器）业务链（2026-08-25 整理，结合代码核实）

> 筛选自 `变更说明.md` 与 `browser_daemon.py` / `login_api.py` / `auth_helper.py` / `link_resolve.py` 逐行核实，符合当前 V2 架构（0.29.0）。

## 一、BCC 是什么（术语澄清）

**BCC = Browser Context Container（浏览器容器）**，不是邮件盲抄送。见 `变更说明.md`（2026-08-19, v0.29.0）：

- 重构前：每个要操作浏览器的模块各自 `launch_persistent_context` **直开 Playwright**，多进程同时操作同一 profile 目录 → 锁冲突、崩溃。
- 重构后：`browser_daemon` 重写为**每账号一个常驻 sidecar，持有唯一 Playwright context**，对外暴露 HTTP API；所有需要操作浏览器的模块改为 **HTTP 调 BCC，串行执行浏览器任务**，不再抢 profile 锁。
- 容器治理由 Rust `SidecarManager` 负责：spawn 前 TCP 端口探测，spawn 后 supervisor 监听退出事件，崩溃按指数退避自动重启（1s→2s→4s→8s→10s，上限 5 次）。

## 二、BCC 暴露的端点（browser_daemon.py，已核实）

| 端点 | 行号 | 用途 |
|---|---|---|
| `GET /status` | 464 | 就绪探测（alive / 探活） |
| `POST /cookie` | 472 | 刷新 cookie |
| `POST /user_info` | 481 | 批量查用户信息 |
| `POST /resolve_url` | 491 | 解析直播链接 |
| `POST /scan_login` | 500 | 扫码登录抓凭证 |
| `POST /refresh` | 509 | 强制重扫码 |
| `POST /quit` | 518 | 退出 |

> `/status` `/refresh` `/quit` 属**容器治理接口**（启停/保活/探活），不算业务链。

## 三、BCC 形式的 4 条业务链（核心）

"BCC 形式"= 原本各自直开浏览器的环节，被改写成 **HTTP 调 BCC** 的链路。调用方统一经 `login_api._bcc_port` / `_bcc_alive` / `_bcc_post`（第 25-52 行）先探活再请求，BCC 不可达则**降级直开浏览器**（用"优先"而非"强制"）。

| # | 业务环节 | BCC 端点 | 调用方 | 代码位置（已核实） |
|---|---|---|---|---|
| 1 | **扫码登录抓凭证** | `POST /scan_login` | `auth_helper.enrich_auth` | `auth_helper.py:74-76` → `_bcc_post(name,"/scan_login")` |
| 2 | **刷新 cookie** | `POST /cookie` | `login_api.refresh_cookie_from_profile` | `login_api.py:512-513` → `_bcc_post(name,"/cookie")` |
| 3 | **批量查用户信息** | `POST /user_info` | `login_api.bulk_user_info_via_browser` | `login_api.py:598-599` → `_bcc_post(name,"/user_info")` |
| 4 | **解析直播链接** | `POST /resolve_url` | `link_resolve._browser_resolve` | `link_resolve.py:255-257` → `_bcc_post(name,"/resolve_url")` |

### 协作细节（已核实）
- `scan_login`：BCC 会先关闭自身 context 让 `DYLoginApi` 独占扫码，再重开（见 `auth_helper.py` 第 62 行注释）。
- `bulk_user_info_via_browser`：纯 `requests` 调 `get_user_info`（profile API）会被拒/限频，故用**浏览器页面 fetch** 批量查（对齐 douyin.com/chat 实机），稳定返回昵称+头像（见 `recv_daemon.py` 第 671-673 行注释，同一机制也用于会话昵称补全）。
- 调用方模式统一：
  ```
  N 个调用方(backend / recv-daemon / link_resolve / web_probe)
        │  HTTP POST 127.0.0.1:{bcc_port}
        ▼
  browser_daemon(BCC, 每账号 1 个) ──持有唯一 Playwright context,串行执行──▶ 抖音页面
  ```

## 四、反例：哪些核心链**不是** BCC 形式（刻意绕开浏览器）

| 链路 | 实现 | 是否走 BCC |
|---|---|---|
| 弹幕监听 | `LiveChatHook` 直播间 WS + 中控台采集 | 否（纯 WebSocket） |
| 延迟私信调度 | `DispatchCenter` asyncio 优先级队列 | 否（纯 asyncio） |
| 私信发送 | `sender.send_by_uid` → `imapi` 私有网关 `create_conversation`/`send_msg` | 否（**刻意跳过浏览器**，绕开 get_user_info 风控卡死） |
| 私信接收 | `recv_daemon` 连 `frontier-im.douyin.com/ws/v2` | 否（纯 WS） |
| 直播热度/统计解析 | 数据解析 | 否 |
| SQLite 存储 / 历史任务 | 本地 DB | 否 |

**结论**：BCC 只承载"必须与抖音网页/扫码 UI 交互"的 4 类动作（登录、cookie、查信息、解链接）；监听、私信收发、调度等高频核心链路反而刻意绕开浏览器，走抖音私有 API/WS，既稳又快。

## 五、铁律（踩坑固化）

1. **BCC 是"优先"非"强制"**：每个调用方先 `_bcc_alive` 探活，不可达则降级直开浏览器，不静默失败。
2. **单 profile 铁律同样约束 BCC**：BCC 持有该账号唯一 context，重扫/查看/守护按优先级抢占，绝不临时新建目录。
3. **刷新 cookie 用 profile 实时值**：`_pull_conversations_api` / `_build_auth`（recv_daemon）调 `refresh_cookie_from_profile` 从 profile 读实时 cookie，避免 .env cookie 过期导致 imapi 返回空响应。
4. **批量查信息必须走浏览器**：纯 requests 调 profile API 被拒/限频，必须用 BCC 页面 fetch（sec_user_ids 批量）。
5. **BCC 崩溃自愈**：Rust SidecarManager 指数退避重启（上限 5 次），前端 `sidecar.ts` 轮询 `/status` 至 `alive=true` 才置 `browserDaemonAlive`。

6. **hook 注入时序铁律已落地（V16 坑，08 §九）**：`context.add_init_script(CAP_USERINFO_HOOK_JS)`
   必须在 `_launch` 的 `goto` **之前**调用，且直接 `goto douyin.com/chat?isPopup=1`（不能先停
   首页）；超时放宽至 120s。否则前端已发完 `im/user/info` 后 hook 才注入 → 截到 0 条。落地实测
   被动截 81 项，`parse_init_protobuf` 解出的 44 会话 `peer_uid ↔ im/user/info.uid` 桥接 44/44。
