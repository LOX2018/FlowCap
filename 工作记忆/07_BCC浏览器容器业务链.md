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


---

## 【2026-09-12 根治】「查看登录态」不再另起实例 —— BCC 就地切可见（`/show`）

### 用户需求原话
> 「我喜欢打开浏览器的目的是**浏览器能够直观地看到登录态**」

### 旧实现的致命设计冲突
`POST /api/accounts/{name}/open-browser` 会**另起一个有头 Chromium 实例**指向同一 profile，
与常驻的无头 BCC **抢 SingletonLock**。实测事故链（2026-09-12 13:43 现场）：

| 时间 | 事件 | 后果 |
|---|---|---|
| 12:43:43 | BCC 容器启动（复用 profile） | — |
| 12:43:57 | `AUTH-050` + `BCC-014`（裸探活 uid 与历史会话不符） | 判为不可信 |
| 12:43:58 | `BCC-016` **拒绝写入 .env**（幽灵 uid，0 命中） | 凭证回写被拦 |
| 12:44:18 | `[bcc] 滚动轮次 1: 新点击=0 累计昵称=0` | **页面根本没登录态** |
| 12:47:40 | 用户手动打开浏览器 → Cookies 被更新 | 又反抢 profile，BCC 更不可用 |

**关键认知**：`BCC-016` 门禁**不是 bug**（它是防凭证污染的安全设计，见 §幽灵 uid 门禁）。
真正的缺陷是**"想看一眼"这个诉求被迫付出 profile 冲突的代价**。

### 根治方案：同一实例就地切换可见性
Playwright 无法运行中切 headless，因此切换 = **重启 context**（约 2~3s），
但**实例、profile、保活链路全部不变**：

- `daemon/browser_daemon.py`：
  · `BrowserContainer.__init__` 新增 `self._headless: bool = True`
  · `_launch()` 改用 `headless=self._headless`（原为硬编码 `headless=True`）
  · 新增 `async def set_visible(visible, url="") -> dict`：
    在 `_lock` 内关旧 context → 设 `_headless` → `_launch()` 重建（可选导航）
  · 新增 `POST /show` 端点
- `api/accounts.py`：
  · `open-browser` **重写**：先确保 BCC 在跑（懒加载 `ensure_bcc(skip_cooldown=True)`），
    再 POST BCC 的 `/show {"visible": true}`。**不再 `_quit_browser_daemon`、不再另起线程**。
  · 新增 `POST /{name}/hide-browser` → `/show {"visible": false}`
- `frontend/src/api/client.ts`：新增 `hideFingerprintBrowser()`（`openFingerprintBrowser` 签名不变）

### 实测验证（源码态 BCC，端口 10042）
| 调用 | 返回 | 耗时 |
|---|---|---|
| `visible=true` | `{"ok":true,"headless":false,"changed":true}` | 3.2s |
| `visible=false` | `{"ok":true,"headless":true,"changed":true}` | 2.3s |
| `visible=true`（复切） | `{"ok":true,"headless":false,"changed":true}` | 2.3s |

**决定性佐证**：切为可见后 `[bcc] 滚动轮次 1..14: 累计昵称=169`（持续增长）
→ 页面**真实已登录**；而旧路径同一账号是 `累计昵称=0`。

### 坑（必看）
1. **FastAPI 把 `visible: bool = True` 当 query 参数**，前端发 JSON body 会被**静默忽略**
   → 切回无头失效（实测返回 `headless:false`）。修法：端点加 `req: Request`，
   显式 `json.loads(await req.body())` 覆盖，body 优先、兼容 query。
2. **改源码后必须用源码态启动 BCC 验证**：`binaries/` 下的 BCC 是**旧二进制**
   （09-08 打包），新端点恒 404。开发态启动：
   `PYTHONPATH=<backend绝对路径> DY_APP_ROOT=... python daemon/browser_daemon.py --account <名> --port <端口>`
3. `_headless` **不读环境变量**（`DY_BCC_HEADLESS_MODE` 已废弃）：历史上 `disguise`
   （有头+移屏外）模式因 profile 残留屏外坐标等副作用被移除，此处不复活它。


## 【2026-09-12 二次根治】set_visible 切换两个作用域 bug（NameError 误判 TargetClosed）

### 症状
`/show` 切有头：HTTP 秒回 `{switching:true}`（不再超时，这部分首次修复成功），
但每次后台切换后必报 `BCC-006`；且每次伴随 2 次「复用固定 profile」日志。
曾误判为 TargetClosed（profile 锁竞争），实际是作用域 NameError。

### 根因（两个，均为作用域 bug）
1. **`_SWITCH_COOLDOWN_SEC` 定义在 `__init__` 局部**，`set_visible`/`_do_switch_background`
   引用直接 NameError → 切换后崩在冷却期赋值行。Error 堆栈坐实：
   `NameError: name '_SWITCH_COOLDOWN_SEC' is not defined`（browser_daemon.py:449）。
2. **`_wait_profile_released` 只查 `SingletonLock`**，但 ungoogled-chromium 实测
   锁文件是 **`lockfile`**（2026-09-12 现场核实）→ 永远检测不到锁 → 竞态未挡住，
   launch 撞 TargetClosed。

### 修复
- `_SWITCH_COOLDOWN_SEC` 提为**模块级常量**（勿放 `__init__` 局部）。
- `_wait_profile_released` 同时查 `SingletonLock` 和 `lockfile`。
- 该等待收敛到 `_launch()` 内部统一调用（所有重建 context 路径生效：
  start / _ensure_alive / set_visible / scan_login）。

### 实测验证（源码态 + 部署二进制）
4 轮往返切换（有头→无头→有头→无头）全部成功：
- 每次 `/show` 秒回 switching:true，无超时
- **BCC-006: 0 次**（此前每次切换必报）
- 每次切换 2-3s 完成，进入 180s 切换冷却期，探活只告警不强杀
- lockfile 正确识别，锁释放等待生效

### 铁律
- **新引入的模块级常量/可复用常量绝不放 `__init__` 局部**，多方法引用即 NameError。
- **Chromium 锁文件名随内核变化**：官方用 SingletonLock，ungoogled-chromium 用 lockfile，
  判断 profile 是否被占用要两者都查，不能盲等单一名。


## 【2026-09-12 代理根治】前端代理配置真实生效 + 账号未配代理强制直连（IP 隔离）

### 用户需求原话
> 「代理功能本来就是需要的，因为涉及到访问 IP 隔离的问题，只是我目前需要访问
> 一些国外的网站，所以节点是国外的，在后续正式投入运行的时候，是需要针对账号
> 环境设置国内 IP 隔离的」

### 根因（代理配置失效 → 误走系统代理）
前端账号管理页「代理配置」入口是**占位实现**，配置不生效：
- onTest 用 `Math.random()` 假随机测连接（accounts.tsx）
- onSave 只弹 toast「已保存」，不调后端、不写 .env
- 后端只有 proxy-status 读接口，无保存接口
→ 账号 .env 的 DY_PROXY 永远为空 → 指纹浏览器默认跟随系统代理
（satelite 10808 国外节点，ProxyEnable=1）→ 抖音检测到代理特征+出口IP
与账号不符 → 弹「安全风险阻止访问」（首页+私信都弹）。

### 修复（三层）
1. **后端 `POST /{name}/proxy` 保存接口**：接收 {type,host,port,user,pass}，
   组装 DY_PROXY 写 .env.enc（write_env_file 自动加密），direct 清除。
   socks 不带认证（Chromium 限制），http(s) 带认证并 quote 特殊字符。
2. **前端真实化**：saveProxy/proxyStatus API + onTest/onSave 改调后端
   （不再假随机），保存后 invalidateQueries 刷新列表。
3. **vbrowser 直连保护**：账号未配 DY_PROXY 时**无论系统代理死活都强制
   --no-proxy-server**（不再跟随系统代理/satelite）；配了代理按账号 IP 隔离。
   同时移除 _FAKE_MEDIA_ARGS 的 --use-fake-ui-for-media-stream
   （ungoogled 内核提示不受支持的命令行标记，--deny-permission-prompts 已够）。

### 实测验证（部署二进制）
- 保存 socks5/http+认证 → .env.enc 正确写入、parse_proxy_env 读回正确、
  _mask_proxy 脱敏正确；direct/None/'' 清除都返回未配置。
- 账号未配代理时 BCC 日志出现：
  `[vbrowser] 账号未配 DY_PROXY，强制 --no-proxy-server 直连（防误走系统代理/satelite…）`
- 无「不受支持的命令行标记」警告（use-fake-ui 移除生效）。
- exec_js 正常、chrome 进程正常、直连出口 IP 23.191.200.205。

### 铁律
- **前端占位实现必须杜绝**：测试/保存要真调后端，禁止 Math.random() 假模拟。
- **指纹浏览器默认跟随系统代理**（Chromium 行为）→ 账号必须显式配 DY_PROXY
  或强制 --no-proxy-server，否则代理特征泄露被抖音拦截。
- **账号代理 = IP 隔离的载体**：国内账号配国内节点，国外访问配国外节点，
  保存后即时生效，不匹配=环境异常。


---

## 【2026-09-13 方案】UID 漂移 = 凭证失效（待实施 · 明日）

> 用户强调（2026-09-13）：**UID 漂移 = 凭证失效**。
> 探活 uid 与该账号历史 conv_id 不一致（AUTH-050）即是凭证失效的铁证，
> 不能仅当作「uid 数据陈旧」而继续用失效凭证干活。

### 一、现状缺口（实测坐实）

`services/uid_probe.py:193`（AUTH-050）：
```python
logger.warning("AUTH-050", "...判为陈旧/不可信，不缓存")
return None          # ← 只不缓存 uid；调用方"走兜底"继续用失效凭证
```
- 把漂移当**数据层不可信**，没当**账号层凭证失效** → 继续发消息、继续用 WS token。

### 二、今日故障链（凭此定性）

```
UID 漂移（AUTH-050 反复告警，03:36 仍在报）→ 代码继续用失效凭证
   ↓
发消息 → create_conversation INVALID_REQUEST（cmd 609）← 凭证失效铁证
   ↓（静置 28 分钟不恢复 → 非临时限流，是账号/凭证持久失效）
WS 的 token(sessionid) 同效失效 → 推送停止 → 对方消息收不到 → AI 不触发
```

**关键纠正**：凭证字段「看起来新」（ticket / web_protect / keys 齐全）
**≠ 有效**；**UID 与账号匹配才是有效判据**。曾被字段齐全误导。

### 三、改造目标

UID 漂移 → 判定凭证失效 → 触发重新捕获 → 捕获成功前**不发消息**（避免
INVALID_REQUEST 刷屏 + 加重风控）→ 成功后恢复。

### 四、改动点（明日实施）

| # | 位置 | 改动 |
|---|---|---|
| 1 | `services/uid_probe.py:193` | AUTH-050 除 return None 外，**标记该账号凭证失效**（写入失效态，如 kv_store `cred_invalid_<account>` + 时间戳） |
| 2 | `services/dm_dispatch.py`（send 闸门） | 发送前检查失效标记：命中则**直接拒绝并返回"凭证失效待重捕获"**，不发请求（杜绝 INVALID_REQUEST 刷屏） |
| 3 | `daemon/recv_daemon.py` | 收到失效标记 → 停止用旧 token 的 WS 重连，等待新凭证 |
| 4 | `daemon/browser_daemon.py` | 已有 BCC-016 拒绝回写（保留）；补充：漂移时**主动触发 auto_recapture** |
| 5 | 重捕获入口 | `auto_dm.accounts.auto_recapture(name)` / `POST /api/accounts/{name}/auto-recapture`；成功后**清除失效标记**并重建 WS |

### 五、实施后验证步骤（按顺序，硬验证）

1. 制造/等待一次 UID 漂移（或手动置失效标记模拟）
2. 确认：**不再有 INVALID_REQUEST 刷屏**（发送被闸门拦住）
3. 确认：触发 auto_recapture（日志出现重新捕获）
4. 重捕获成功 → 失效标记清除
5. 手动发消息 → **DB 落库 role=them** ✅（硬证据，非日志推断）
6. AI 生成回复 → 目标在白名单内 → **发送成功并落库 role=me** ✅

### 六、教训（防复发）

- **日志只显示错误码不显示详情**：loguru 的 `logger.warning("CODE", detail)`
  会把 detail 当 format 参数吞掉 → 排查时**先落文件抓堆栈**（本次靠
  `recv005_trace.log` 才抓到 `AttributeError: msg_id`）。
- **验证必须硬**：本次曾把「AI 生成回复」误报为「AI 已回复」，实际
  SEND-029 白名单拦截 / INVALID_REQUEST 发送失败 → 抖音端根本没收到。
  铁律：**DB 落库 + 实际收到**才算数。
- **测试白名单是安全设计**：`_TEST_WHITELIST` 只允许测试账号互发，
  SEND-029 拒绝发给真实客户属**正确行为**，不是 bug。


## 【2026-09-13 根因复现】"安全风险阻止访问" + 登录态强制下线 = headless 环境跳变复发

### 用户报告（原话）
> 「又出现了，您正在尝试访问的网站存在安全风险...已阻止此次访问。而且这次是
> 两个账号都有出现，很明显是因为环境的问题导致。登录态被强制下线，所以凭证不可用」

### 现场证据（只读审计，2026-09-13）
| 项 | 实测值 |
|---|---|
| 账号 .env | **无 DY_PROXY**（两账号都未配） |
| 系统代理 | ProxyEnable=1 → 127.0.0.1:10808（satelite 国外节点，端口**活着**） |
| chrome 实参 | `--no-proxy-server`（vbrowser 强制直连，未走系统代理） |
| 直连出口 IP | 171.218.232.73（成都 CN）→ **非 IP 问题** |
| 张老师 BCC (PID 10188) | 🔴 **带 `--headless`** |
| 尚进 BCC (PID 10124) | 有头 |
| 弹窗 | **两账号都弹**（首页+私信）→ 共因＝环境，不是单账号问题 |

### 根因（与 §24.10 完全一致）
**Chromium headless 被抖音风控识别 → 触发 step-up 重验证 → 登录态被强制
下线（弹「安全风险阻止访问」）→ UID 漂移（AUTH-050）→ 凭证实际失效。**

时间线回顾：
- 2026-09-06 发现此根因 → 改「真有头 + 窗口移屏外」→ **风控对齐成功**（实测闭环）。
- 2026-09-09 因移屏外副作用（屏外坐标写进 Preferences，污染后续可见启动）
  被废弃 → **改回纯 headless（native）** → **埋雷**。
- 2026-09-08 曾以「纯无头下小助理 ws / 张老师 wp 均发送成功」证伪必要性，
  但那是**短期可用**；本次（09-13）双账号同时被强制下线，证明 headless
  长期仍会被识别 → **§24.10 的结论依然成立，废弃决定需回滚**。

### 故障链
```
BCC headless 启动 → 抖音识别无头特征 → 环境跳变
   ↓ step-up 重验证 → 登录态强制下线（弹「安全风险」）
   ↓ UID 漂移 AUTH-050 → 凭证失效（字段看似齐全但已失效）
   ↓ 发送 INVALID_REQUEST(cmd 609) + WS token 失效停推送
   ↓ 对方消息收不到 → 不落库 → AI 不触发 → 用户看不到回复
```

### 待实施方案（明日，三选一）
- **A. 恢复「真有头+窗口移屏外」**：09-06 已验证有效；副作用靠
  `_ensure_window_visible()` 归位兜底（该函数仍在代码中）。
- **B. 有头 + 正常坐标（推荐）**：`headless=False`，窗口放正常坐标
  （不污染 profile），有头特征完整。**需用户确认窗口可见是否可接受**。
- **C. CDP 接管用户手动打开的指纹浏览器**（`VB_MODE="cdp"`）：环境
  100% 一致，最根治；需浏览器带 `--remote-debugging-port` 常驻。

### 核心洞见（新增铁律）
**关键不是「有头/无头」，而是「登录时的环境与运行时环境必须一致」。**
扫码时有头、运行无头 → 环境跳变 → 触发风控降级。要么全程有头，要么全程无头
（但无头已被识别），**结论：必须全程有头**。

### 待补铁律
- **无头可用的短期实证不能推翻长期风控结论**：09-08 的「无头也能发」只代表
  当时未被判风险；09-13 双账号强制下线证明 headless 终会被识别。
- **凭证字段齐全 ≠ 凭证有效**：ticket/web_protect/keys 都在，但 UID 漂移
  已证明身份不匹配 → 凭证实际失效（与「UID 漂移=凭证失效」铁律一致）。
