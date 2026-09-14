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


## 【2026-09-13 三Bug 修复】BCC 切换死循环闪退 / 二次双击不唤醒 / 日志吞掉描述

### 用户反馈
> 「双击浏览器唤醒可观测态出现闪退，关闭浏览器后二次双击没有唤醒浏览器。
> 运行日志的报错信息，只有报错代码，没有描述该代码对应的报错说明」

### 一、日志只有代码没描述（根因：loguru 参数语义误用）
**机制**：loguru 把**第一个位置参数当格式模板**（等价 str.format 的模板），
第二个参数仅作 `{}` 占位符填充值。因此项目按「统一报错代码体系」写的
```python
logger.warning("BCC-006", f"[bcc] context/page 失活，重启: {e}")
```
**描述被静默丢弃**，运行日志只剩一行 `BCC-006` —— 排查价值归零。
**全项目 345 处**这种写法（45 个文件：browser_daemon 35、ai_reply 28、
recv_daemon 23、main 21、login_api 20 …）。

**修法（零侵入，一处生效）**：新增 `backend/utils/code_logger.py`
`install_code_logger_patch()`，包装 logger 各级方法，检测「纯错误码 + 后续描述」
时自动合并为 `CODE | 描述`。在 3 个日志配置点（main / browser_daemon /
recv_daemon）的 `logger.remove()` 之前安装。**未改 345 处调用点**，
`{}` 占位符正常用法不受影响，幂等可重复调用。

**⚠️ 连带教训**：正因为这个 bug，此前所有「崩溃只看到代码看不到异常」的排查
都被它挡住。**修日志是诊断下一步的前提**，遇到"日志信息不足"先修可观测性。

### 二、双击唤醒闪退 = _ensure_alive 缺切换期保护（3.6s 死循环）
**根因链**：
```
双击 → set_visible：关旧 context → _context=None → _switching=True
     → 后台 _do_switch_background 重建 context（实测耗时 ~17.6s）
   ↓ 同期前端 / WP 轮询经 _exec → _ensure_alive()
   ↓ _context is None → 立刻 raise "context 已关闭"
   ↓ 误判「失活」→ 触发重启 → 与后台 _launch 抢 profile → TargetClosed
   ↓ 再次误判 → 【3.6 秒一轮死循环】（BCC-006 刷屏 = 用户看到的"闪退"）
```
`run_keepalive` 早就有此保护（跳过探活），但 **`_exec → _ensure_alive`
这条路径漏了** —— 同一保护必须覆盖**所有**探活入口。

**修复**：
- `_ensure_alive` 加「切换中/冷却期」保护（等待重建，不判失活）
- `_exec` 在 `_switching` 时快速失败（`ContainerBusy`），不继续执行拿 None context
- 新增**切换卡死看门狗**：`_switching` 超 900s 强制复位（`_switch_started_at`），
  防切换异常导致窗口永久无法唤醒

### 三、二次双击无反应 = set_visible 只 goto 不探活
用户手动关掉可见窗口后，`_context` 仍非 None 但**页面已失效**；
旧逻辑在「已是目标模式」分支只尝试 `goto` → 静默失败（BCC-038）→ 窗口永不回来。
**修复**：该分支先探活 context/page，失效则**落到重建流程**真正唤醒窗口。

### 铁律（新增）
1. **同一保护必须覆盖全部同类入口**：`run_keepalive` 有、`_exec/_ensure_alive`
   没有 = 等于没有。加保护时先 grep 出**所有**调用该状态的路径。
2. **手动关窗后 `_context` 非 None 不代表可用**：任何"已是目标态"的短路分支
   都必须先探活再复用，否则无法自愈。
3. **状态标志要配看门狗**：`_switching` 这类"进行中"标志一旦因异常未复位，
   会让功能永久失效（窗口再也唤不醒）；必须带超时强制复位。


## 【2026-09-13 v0.43.4】BCC 单例硬守卫 + 致命态熔断（调度器不再可绕过）

### 用户铁律（本次的出发点）
> 「任何操作前先核对 BCC 状态；**只能有一个 BCC**，统一交给调度器切换。
>  如果每次都能越过调度器，那就说明这个机制就是一个废物。」

### 事故：手工起第二个 BCC → 快闪风暴
日志铁证（2026-09-13 20:46:57—20:48:07）：
```
20:46:57 BCC-028 | [bcc] 浏览器容器启动失败：账号 尚进工伤小助理 无 .env（索引未登记？）
20:47:01 BCC-033 | [bcc] /wp_messages 失败: [bcc] 账号 尚进工伤小助理 无 .env（索引未登记？）
… 每 3.7 秒一条，连续 20 轮 …
20:48:07 BCC-033 | （最后一条）
```
**`无 .env（索引未登记？）` 只在缺会员环境变量时出现** → 即「手工启动」的特征
（应用自己拉起时 backend 会传 `DY_MEMBER`/`DY_MEMBER_KEY`）。

机制：缺会员态 → 读不到 `.env.enc` → `_launch` 抛错 → `_ensure_alive` 判
「context 失活」→ **每 3.7 秒重启一次** = 用户所见「窗口一闪一闪」。
同期 `20:43:07` 应用自己那个 BCC 的 `BCC-006 context 已关闭`，
时间点与手工实例拉起重合 —— 抢 profile 导致常驻容器 context 被挤掉。

**风控后果**：同一账号 profile 被两个 BCC 抢锁 + 每 3.7 秒重启 = 极强风控信号。

### 根因（链路溯源）：单例只靠调用方自觉
`browser_daemon.main()` 此前**只有**「端口哈希校验」（`--port` 必须等于
`browser_daemon_port(account)`），**没有**「该账号已有实例在跑」的守卫。
⇒ 任何路径（含人工调试/脚本）都能再起一个指向同一账号 profile 的 BCC，
⇒ **调度器可被绕过，形同虚设**（这正是用户说「是废物」的技术根因）。

### 修法：把单例判断做进**二进制自身**（自证，不靠调用方）
`main()` 端口校验之后新增硬守卫，两层判据（任一命中即拒绝启动）：
| 层 | 判据 | 说明 |
|---|---|---|
| ① 端口层 | 该账号哈希端口被监听 **且** `/status.account` 与之相符 | 只判端口被占不够（可能是别的进程），必须核对账号 |
| ② profile 层（根本） | profile 目录存在 `SingletonLock` / `lockfile` | 浏览器所有权最终体现在该目录，与端口无关，更根本 |

拒绝启动返回 **`SystemExit(3)`**，并打印可操作提示：
「请把操作交给调度器 `services.browser_gate.ensure_browser(account, purpose)`」
逃生口：`--force-duplicate`（显式调试，仅极端场景）。

### 配套：致命态熔断 `BCC-043`
凭证不可用（`env_path` 为空）属**不可自愈**条件 —— 重启一万次也一样。
原逻辑会每 3~4 秒重启（快闪风暴）。现改为：
- 首次遇致命态 → `logger.error("BCC-043", ...)` + 设 `_fatal_until = now + 1800`（30 分钟）
- 熔断期内 `_ensure_alive` **只告警不重启**，并给出可操作提示
- 把「读不到凭证」的常见原因写进提示：① 应用未以会员身份运行（子进程缺
  `DY_MEMBER`/`DY_MEMBER_KEY`）② 账号未登记进索引 ③ 账号已被删除

### 验证（零风险：monkeypatch `uvicorn.run`，绝不真起第二个 BCC）
| 检查 | 结果 |
|---|---|
| A. 判据命中正在运行的账号 | ✅ `port=11231, account=尚进工伤小助理` |
| B. 对不存在账号不误报 | ✅ 返回 `None` |
| C. `main()` 第二次启动同账号 | ✅ **`SystemExit(3)` 拒绝启动** |
| D. `--force-duplicate` 逃生口 | ✅ 可放行到 uvicorn |

### 铁律（新增）
1. **单例门禁必须做在二进制/资源层自证**，不能只靠调用方自觉 ——
   调用方一多，任何一个漏传都能破坏单例（`allow_launch` 后门、手工脚本同理）。
2. **不可自愈的错误必须熔断**，绝不能进重启循环（重启风暴 = 风控信号）。
3. **浏览器所有权判据选「profile 锁文件」**（唯一物理资源，与端口/进程名无关），
   比端口/进程判断更根本。
4. 调试用它人的账号 profile 前，先核对 BCC 状态并**交给调度器**；
   绝不再手工起第二个实例。

## 【2026-09-13 v0.43.4】浏览器租约（Lease）——调度器变实，消除「吉祥物」

### 用户定位（本次出发点）
> 「我不希望调度器只是个吉祥物，调度器在本项目中是非常非常重要的模块」

### 根因三层证据（为什么 gate 一直是空壳）
```python
# services/browser_gate.py L112-120 —— PURPOSE_EXCLUSIVE 分支全文
    if purpose == PURPOSE_AUTO:
        logger.info(f"[gate] 复用 BCC 容器 (... exclusive={st['exclusive']})")
    else:
        if not st["exclusive"]:                       # ① 读 st['exclusive']
            logger.info("...需先令 BCC 让出 profile")   # ② 只打日志
    return {"ok": True, ...}                          # ③ 直接返回成功
```
| 证据 | 事实 |
|---|---|
| ① 字段来源 | `st['exclusive']` ← `bcc_state()` ← `/status` 的 `exclusive` |
| ② **实测 /status** | 只返回 `alive/account/profile/uid/last_refresh/logged_in/version`——**无 exclusive** ⇒ `j.get("exclusive")` **恒为 None** |
| ③ 写入方 | 全仓**无任何代码**写 `/status.exclusive`；BCC 内部真标志是私有 `_scan_exclusive`，未对外暴露 |

⇒ `if not st["exclusive"]` **恒为真** → 永远走「只打日志」分支 → 永远 `ok=True`。
**完整空壳，且静默**（比报错更糟：调用方以为拿到独占，实际什么都没发生）。

### 为什么设计成「租约」而不是「让出 profile」
`gate` 想做「让 BCC 让出 profile」——但 BCC 是**独立进程**、profile 由它持有，
**跨进程「交出 profile」不可能实现**（所以只能打日志假装成功）。

正确抽象：**BCC 始终是唯一所有者**，业务操作向它**申请租约**，
BCC 仲裁授予；结束 release，或 TTL 到期自动回收。

### 实现（browser_daemon.py）
| 件 | 说明 |
|---|---|
| `_lease` 结构 | `holder/purpose/prio/lease_id/acquired_at/ttl/expires_at/renew_count` |
| `_lease_current()` | **惰性 TTL**：读时判 `now > expires_at` 即回收（BCC-046）。无需后台定时器，**持有者崩溃不会永久独占** |
| `_lease_acquire()` | 重入判据**只有显式 lease_id 匹配**；被他人持有 → 拒绝（不抢占）+ `retry_after` |
| `_lease_renew()` | 累计时长超 prio 上限 → 拒绝（BCC-048，防续租绕过上限） |
| `_lease_release()` | 必须 lease_id 匹配（BCC-049，防误释放他人租约） |
| 端点 | `GET /lease_status`、`POST /lease`、`/lease/renew`、`/lease/release` |
| `/status` | 新增 `lease` 子对象 + `exclusive` 真值（补齐 gate 缺失的真值源） |

### 优先级与 TTL 硬上限
| prio | 名称 | 用途 | TTL 上限 | 拿不到时 |
|---|---|---|---|---|
| 0 | 用户显式 | 更新会话/打开浏览器/扫码 | 300s | 短等重试 |
| 1 | 业务自动 | AI 回复/私信发送/凭证刷新 | 180s | 短等 → 降级 |
| 2 | 后台保活 | keepalive/昵称预热/uid 轮询 | **30s** | 直接跳过本轮 |

**超限按上限授予**（不是拒绝请求），**renew 累计超限则拒绝**——防「P2 持 10 分钟」把调度器架空。

### 关键设计取舍：**不做真抢占**
浏览器操作大多**不可中断**（DOM 流程、context 重建）——真抢占会打断执行中操作，
导致状态不一致。用「P2 限时 30s + 快速失败 + retry_after」解决「低优先级霸占」，
语义最干净，且与既有 `_scan_exclusive`（scan_login 独占期内其他**立即失败**，不抢占）一致。

### 最小侵入接入：租约做进 `_exec`
`_exec` 是 11 个端点共用的执行入口 → 在它里面加租约门，**11 个端点自动纳入调度**，
无需逐个改造。跨调用窗口（scan_login：关 context → 扫码 → 重启）才显式取租约。

⚠️ **重入判据只有「显式 lease_id 匹配」**：`_lock` 非重入 ⇒ `_exec` 不可能嵌套，
所以任何「同 holder」都**不是**重入，而是**并发冲突**（必须拒绝）。
（若按 holder 判重入，两个并发请求都叫 `bcc-internal` 时，第二个会错误复用第一个的租约。）

### gate 变实（S2.5）
`ensure_browser()` 改为**真取租约**；拿不到返回 `ok=False` + `retry_after`（不再假 `ok=True`）。
新增 `acquire_lease()` / `release_lease()` / `lease_status()` 供业务侧调用。

### 绕过口收紧（S2.7）
`core/dispatch.py` 的 `dm_dispatch 接入失败 → 回退直发` **已移除**，改为放弃发送 +
`SEND-037`。理由：直发绕过统一风控闸门（2次/分钟、30次/天 + 频控降权冷静），
**发送是最高频风控面**——宁可「这次不发」，也不破坏闸门。

### 错误码（BCC 域 046+，避开 web_probe 占用的 043-045）
| 码 | 含义 |
|---|---|
| `BCC-046` | 租约超时强制释放（持有者未 release） |
| `BCC-047` | 租约冲突被拒（busy） |
| `BCC-048` | renew/TTL 超上限被拒 |
| `BCC-049` | 持有者不匹配 / 未持租约 |
| `BCC-050` | 单例守卫命中（已有 BCC 在跑，拒绝启动） |
| `BCC-051` | 致命态熔断（凭证不可用，不再自动重启） |
| `SEND-037` | dm_dispatch 接入失败，放弃发送（不再回退直发） |

### 验证（33 项，源码级 + monkeypatch，不碰运行中 BCC）
```
初态空闲 OK / P1成功 OK / 冲突被拒不抢占 OK / 重入复用 OK /
P2 600s→30s 截断 OK / 惰性TTL回收 OK / release 需 id 匹配 OK /
renew 超限被拒 OK / 端点注册 OK / 错误码注册 OK
==> 全部通过
```
另：单例硬守卫 monkeypatch 验证——判据命中运行中账号 OK、不误报 OK、
第二次启动 `SystemExit(3)` OK、`--force-duplicate` 逃生口 OK。

### 铁律（新增）
1. **调度器不能是壳**：调度逻辑必须真的在调度器里，且**读到的状态必须是真值源**。
   「读一个从不存在的字段」会静默退化成假成功——比报错更危险。
2. **跨进程资源不能承诺「让出」**：所有者始终是那个进程，正确抽象是**租约**。
3. **不可中断的资源不做抢占**：用「优先级 + TTL 上限 + 快速失败 + retry_after」。
4. **TTL 必须惰性回收**：持有者崩溃/忘 release 不能造成永久独占。
5. **绕过必须响**：静默降级（假成功 / 悄悄直发）是机制腐化的根因。

## 【2026-09-13 v0.43.5】窗口快闪根治 + 交互行为人类化（去脚本特征）

### 用户反馈
> 「BCC 依旧快闪，更新会话应该采用无头 BCC，且点击的时候要符合不规律感，
>   固定点击位置和频率容易被判定为脚本导致封控」

### 问题1：快闪真因（证据链 22:08-22:10）
```
22:08:35 BCC 启动（无头请求 → 真有头+窗口最小化）
22:08:40 /show(visible=True) → self._headless = False   ← 用户点「打开浏览器」
22:09:02 BCC-006 context 死亡 → _launch(headless=False) → 重建可见窗口 ⇒ 闪
22:10:20 BCC-006 再次死亡 → 再次重建 ⇒ 又闪
```
**根因**：`set_visible(True)` 会把 `self._headless = False` **持久保留**，
而 `_launch` 里传的是 `headless=self._headless` → 之后任何 `BCC-006` 失活触发的
**重建都会再创建一个可见窗口**。而 `launch_persistent_context(headless=False)`
是「**先创建可见窗口、再 minimize**」→ 中间的时间差就是用户看到的**快闪**。
（同期 `BCC-029 昵称缓存预热失败: context has been closed` 印证重建时旧 context 已关。）

**修法（browser_daemon._launch）**：
1. **重建一律按「最小化」启动**（不继承上次可见态）——重建是**异常自愈路径**，
   不该顺带弹窗；用户要可见态时走 `/show`（只改窗口状态，不重建 context）。
2. 重建后**同步 `self._headless = True`** —— 否则残留 `False` 会让下一次
   `set_visible(True)` 误判「已是目标模式」而**跳过窗口恢复**（用户点了却看不到窗口）。
3. 新增观测码 `BCC-052`（context 失活自愈：已重建容器、按最小化启动）。
4. 回退开关 `DY_BCC_RESTORE_VISIBLE_ON_RELAUNCH=1`（仅调试）。

### 问题2：固定点击位置/频率 = 脚本指纹
**原理**：脚本特征 = **确定性**。固定坐标（元素正中心）+ 固定间隔（400ms）
+ 固定滚动步长（整屏）三者叠加即成指纹。真人则是「有抖动、有停顿、有回退」。

**实现（`browser_daemon`，模块级 `_human_*` 系列）**：
| 函数 | 作用 | 关键设计 |
|---|---|---|
| `_human_gap(base, spread)` | 点击/滚动间停顿 | **对数正态**分布。真人停顿是**长尾**的（多数短、偶尔长），均匀随机仍可被统计识别。实测 base=0.4 时：中位 0.40s、p90 0.84s、长尾 2.62s |
| `_human_click(page, el)` | 人类化点击 | 元素内随机取点（避开正中心 20% 与 15% 边缘）+ **分步鼠标轨迹**（3~5 步、步间微停、坐标抖动）——Playwright 默认 click 是**瞬移+居中**，是典型脚本特征 |
| `_human_scroll_ratio()` | 滚动步长 | 0.65~0.95 屏随机，而非恒整屏 |
| 会话顺序 | 每轮 `random.shuffle` | 脚本总从第一个开始，**顺序确定性**也是特征 |

**回退开关**：`DY_HUMAN_PATTERN=off` → 恢复确定性行为（调试复现用）。

**实测分布（3000 次取样）**：
```
_human_gap      : min=0.12 中位=0.40 均值=0.48 p90=0.84 max=2.62  unique=2922
_human_scroll   : 0.650~0.950  unique=1993
DY_HUMAN_PATTERN=off 时：_human_gap 恒 0.4、ratio 恒 1.0（回退有效）
```

### 铁律（新增）
1. **异常自愈路径不得产生用户可见副作用**（重建不该弹窗/闪窗）。
2. **界面可见性是「用户请求态」而非「持久态」** —— 重建后应回到默认不可见；
   否则用户态会被异常路径继承并放大（本例：一次 /show 导致每次重建都弹窗）。
3. **与外部平台交互的行为必须非确定性**：坐标、间隔、步长、顺序四者都要抖动；
   间隔用**长尾分布**（对数正态）而非均匀分布。
4. 人类化改造必须**保留功能降级路径**（`_human_click` 失败回退元素点击），
   去脚本化不得让功能可用性变差。

## 【2026-09-13 v0.43.6】BCC 改为「随启动拉起」—— 懒加载架构的服役终止

### 用户决策
> 「之前规范 BCC 不随启动拉起，是为了优化性能，避免启动过慢，现有架构是否还会
>   存在这种问题」→ 量化后确认**不会了** → 「启动就拉 BCC（4 秒，消掉 6 个结构性问题）」

### 决定性证据（前提已消失）
| | 当初立规范时（09-06） | 现在 |
|---|---|---|
| 打包形态 | onefile（每次解包 114MB 到 %TEMP%） | **onedir 免解压**（09-08 已改造） |
| sidecar 启动 | **13.24s** | **1.10s** |
| 内存常驻 | ~90MB | ~9.5MB |
| **BCC 冷启动** | 15s+（当初的理由） | **实测 4 秒**（22:08:35 → 22:08:39） |

git `d0b9163`(09-06) 原文：「BCC 只服务 WP 通道/会话更新预热，启动不该为可能不用的
功能预付 **15s+** 成本与风控暴露」——那个 15s 是 onefile 时代的数字。

### 全流程捕获实证（7335 帧 @8Hz，15 分钟）
```
BCC /status 成功帧数        : 0 / 7335   （全程 URLError）
browser-daemon 进程         : 从未出现
chrome（指纹浏览器）        : 从未出现（776 帧的 chrome 是用户自己的浏览器）
前端显示                    : 「更新完成：会话 44（消息 25），耗时 5.8s」← 假成功
```
捕获器：`scripts/diag/capv2.py`（纯 Win32 API，8Hz；调 PowerShell 会拖到 0.4Hz）

### 根因链
```
启动路径分叉：
  · 未登录 → _prealign_on_startup(skip_cooldown=True) → 拉全部守护
  · 已登录 → _auto_start_daemons() → BCC 被 DY_BCC_ON_START=0 **跳过**
应用正常运行期就是「已登录」⇒ 每次启动 BCC 都不被拉起。

用户点「更新会话」→ _bcc_url → ensure_bcc（未豁免冷静期）
  → 撞 DY_BCC_LAZY_DELAY=30s → SYS-002「冷静期内不自动拉 BCC」
  → gate 委托调度器又撞同一冷静期 → BCC-041「未能就绪」
  → **CAP-016 未就绪但代码不 return** → CAP-007 连不上 11231
```

### 修法
1. **BCC 随启动拉起**：`DY_BCC_ON_START` 默认 0 → **1**（=0 可显式关闭）；
   与 recv_daemon 并行 spawn（两者无依赖）。
2. **冷静期默认 0**：BCC 启动即拉后，冷静期无存在必要（保留环境变量供回退）。
3. **用户显式操作永远豁免**：新增 `PURPOSE_USER` + `_SKIP_COOLDOWN_BY_PURPOSE`
   表（USER/EXCLUSIVE→True，AUTO→False），`ensure_browser(skip_cooldown=…)` 透传。
4. **用户操作路径全走 PURPOSE_USER**：`_bcc_url`、`refresh_conversations`、
   账号页守护启动按钮（实测原漏 3 处）。

### 消掉的 6 个结构性问题
| # | 问题 | 现状 |
|---|---|---|
| ① | 三处路径对「该不该拉 BCC」结论相反 | 统一为「启动就拉」 |
| ② | 30s 冷静期拦住用户显式操作 | 默认 0 + USER 永远豁免 |
| ③ | 拿不到 BCC 不 return（假成功） | BCC 启动即就绪，不再发生 |
| ④ | 5 个入口语义重叠 | 拉起时机统一，入口不再需要各自判断 |
| ⑤ | skip_cooldown 隐式契约（漏传即 bug） | 改为按 purpose 自动推断 |
| ⑥ | 「BCC 是否可用」是运行期不确定状态 | 启动时确定 |

### 铁律（新增）
1. **「性能取舍」必须标注其前提条件，并随前提失效而重新评估**。
   本次的 4 秒 vs 6 个结构性问题——前提（onefile 13s）一消失，规范就该退役。
2. **节流机制（冷静期/限速/退避）永远不得拦用户显式操作**。
   自动路径可以被节流，用户点了按钮就是要用。
3. **拿不到依赖时必须显式失败，不得继续跑出「假成功」**。
   前端显示「更新完成」而昵称 0 个，比直接报错更糟（用户无从察觉）。

## 【2026-09-13 v0.43.6】错误码体系升级为「设计契约驱动」

### 用户要求（原话）
> 「报错除了代号指引方向还需要强调回归设计理念该模块是规划设想是如何的，
>   不能老是围绕现有代码打转而忘了目标。所有debug都需要先回归设计理念在分析链路，
>   依照链路从源头还是分析问题，不能聚焦终端现状，所有的核查和修复都需以实机为主。」

### 缺口
旧 `ERRCODES[code] = {meaning, file, line}` 的 meaning 取自**原日志文本**
（如「[bcc] context/page 失活，重启: …」）——**只有现状，没有契约**；
域级 cause/action 又太粗（同域几十码共用一句）⇒ 指不到具体设计意图。

### 升级（六段，与六步闭环一一对应）
| 字段 | 六步闭环 | 内容 |
|---|---|---|
| `design`   | Step1 回归设计理念 | 该模块应该做什么 |
| `contract` | Step1 补充 | 必须成立的不变式 |
| `deviation`| Step2 定义当前状态 | 实际与预期的**具体差异** |
| `chain`    | Step3 链路溯源 | **源头 → 传播 → 终端** |
| `root`     | Step4 根因分析 | 为什么在**这一点**断裂 |
| `verify`   | Step5 实机验证 | **实机**核查命令/判据 |

域级 `DOMAIN_DESIGN[域] = {intent, invariant, chain, verify}`（6 个关键域：
BCC/CAP/AUTH/SEND/SYS/RECV）；码级缺契约时自动回落域级（design 永不为空）。
实现：`backend/errcode.py`；`GET /api/errcodes/{code}` 已透出全部新字段。
审计：`contract_gaps()` 列出缺契约的码。

### 铁律
1. 新增错误码**必须填 `design`**——没有设计契约的报错不允许提交。
2. 排查任何报错**先读 design/contract，再沿 chain 从源头查**，禁止只看终端现象。
3. 所有结论**必须过 `verify`（实机判据）**，纯代码分析不得作为定论。
---

## 三十六、🔴 跨调用窗口租约未接线 →「更新会话」自我死锁（2026-09-14 v0.43.11 根治）

### 症状（用户报告）

「更新会话」hook 失败、捕获入库失败、**前端只显示数字 UID**（78 个会话全裸数字）。

### 实测证据（同一轮运行，两份日志对照）

`logs/run_20260914_100993.log`（backend 侧）：

```
10:25:04 [gate][尚进工伤小助理] 取得租约 (port=11231, purpose=auto, prio=1, lease_id=83a780e866e2)
10:25:04 AUTH-036 | BCC /cookie 返回失败: 容器正被 gate:auto 独占 → 退回直开浏览器
10:25:05 [capture] 首包解析出 44 个会话，含消息的 11 个
10:25:05 [capture] 经 BCC 截到昵称数: 0          ← ★ 矛盾点
10:25:06 [capture] 写库完成：会话 44，昵称命中 uid关联=0 sec_uid关联=0 未命中=44/44
10:25:06 [refresh] 更新会话完成：会话 44（消息 25），耗时 1.6s   ← 1.6s = 假成功
```

`logs/browser_daemon_20260914.log`（BCC 侧，同一时刻）：

```
10:25:04.422 [lease] gate:auto 获得租约（prio=1 业务自动, ttl=180s, id=83a780e866e2）
10:25:05.998 BCC-047 | [lease] bcc-internal(prio=2) 被拒：当前 gate:auto(prio=1) 持有，剩余 178.4s
10:25:05.998 BCC-030 | [bcc] /capture_userinfo 失败: 容器被独占操作占用: gate:auto
10:25:12.957 [bcc] DOM 末屏补充：累计昵称=37
10:25:12.961 [bcc] 昵称捕获完成：37 个，总耗时 161.5s（已缓存）   ← ★ BCC 明明抓到了 37 个
```

DB 硬证据（会员分库）：

```sql
select count(*) from dm_conversations;                                     -- 78
select count(*) from dm_conversations where peer_name is null
   or peer_name='' or peer_name=peer_id;                                   -- 78 / 78 全裸 UID
select count(*) from dm_conversations where peer_name not glob '[0-9]*';   -- 0
```

### 设计契约（被违反的那一条）

`docs/调度器租约设计细节.md` §3.6 把租约分成两类用法：

| 类型 | 场景 | 做法 |
|---|---|---|
| 单次调用 | capture_userinfo / exec_js / cookie | 走 `_exec` 自动租约（零改造） |
| **跨调用窗口** | **更新会话全程** | **显式 `POST /lease` + `release`，`_exec` 检测到「已持有」则复用（同 lease_id）** |

**实机核查：第二类从来只是一句规划，接线从未实现。**

```bash
$ grep -rn "release_lease" backend --include=*.py
backend/services/browser_gate.py:129  # docstring
backend/services/browser_gate.py:141  # def release_lease
backend/services/browser_gate.py:165  # docstring「用完必须 release_lease()」
# ← 全仓零调用点
```

### 根因：自我死锁（三层断裂，缺一不可）

```
capture_all 第 1135 行：ensure_browser(purpose=AUTO) → 拿到 lease_id=83a780e866e2（gate:auto, prio=1）
        ↓
   lease_id 只存进局部变量 _g，**从未向下传**
        ↓
capture_all 第 1296 行：capture_userinfo_via_browser(name)   ← 签名 (name, wait, max_age)，无 account/lease
        ↓
capture_userinfo_via_browser 第 1080 行：POST {"wait": 15}   ← 请求体无 lease_id（WaitBody 只有 wait 字段）
        ↓
BCC _exec 第 988 行：_is_reentry = bool(lease_id and ...)  ← lease_id="" → 判为「并发冲突」
        ↓
_lease_acquire(bcc-internal, prio=2) 被拒 → ContainerBusy → BCC-030
        ↓
昵称 0 个 → peer_name 回填 peer_id → messages.py:163 name = peer_name or ... → 前端只显示数字
```

**即：同一个进程先给自己拿了租约，5 秒后自己的下游请求被这把租约挡在门外。**
`_exec` 的重入判据「只认显式 lease_id」本身是对的（`_lock` 非重入，同 holder 确实是并发冲突），
**但前提是调用方必须把 lease_id 传下来** —— 而它没传。

### 并发发现的结构性缺陷（同轮一并对齐）

| # | 缺陷 | 证据 | 修法 |
|---|---|---|---|
| B | **两套缓存互不共享 + 时序错配**：BCC `_prewarm` 花 161.5s 写入 BCC 进程内缓存，backend 侧的 `_userinfo_cache` 是另一份；backend 请求在 10:25:05、预热完成在 10:25:12（差 7s） | 上述两份日志 | BCC 侧缓存命中检查先于租约；业务请求若见 `_prewarm_running` 先等（≤40s）；客户端超时 240→300s |
| C | **早退判据读已废弃的 hook 计数器**：`_cur` 取自 `window.__CAP_USERINFO__.map`（v0.43.9 已实证恒 0）→ `_cur <= _prev` 恒真 → 第 3 轮必 break | 同日志：`滚动轮次3: 累计昵称=0` 但 `DOM 抓取: 累计=29` | 判据改用 `_dom_total`（唯一有效来源）；hook 计数降级为仅打印 |
| D | **DOM 无 uid → 关联退化为「顺序软对齐」** | `browser_daemon.py:1400` 写死 `"uid": ""` | 保留现状（v0.43.9 已记录风险）；后续点会话补 sec_uid |
| E | **用途分类用错**：`PURPOSE_AUTO`（prio=1/ttl≤180s）而滚动实测 161.5s | 日志 `BCC-046 租约超时强制回收` 10:23:11/10:23:51/10:24:31/10:25:04 连爆 4 次 | 改 `PURPOSE_USER`（prio=0/ttl≤300s）—— 这是用户点按钮的路径 |
| F | **拿不到资源仍继续跑**（假成功结构） | `messages.py:749` 只 `warning` 不 return；`errcode.CAP-016.root` 自己写着「注意此处不 return 会继续跑出假成功」 | 改为 raise 显式失败 + 向上报 |

### 修复（v0.43.11，六处接线）

1. **租约 lift 到「更新会话全程」**：`capture_all` 用 `PURPOSE_USER` + `holder="capture_all"` + `ttl=300` 取租约，
   登记进模块级 `_ACTIVE_LEASE[account]`，`lease_id` 经 `refresh_cookie_via_owner(lease_id=…)` 与
   `capture_userinfo_via_browser(lease_id=…)` 两路下传。
2. **BCC 端点接受 lease_id**：`WaitBody` / 新增 `CookieBody` 加 `lease_id` 字段；
   `capture_userinfo_map` / `get_cookies` / `refresh_cookie_to_env` 透传给 `_exec(lease_id=…)`，
   `_exec` 据此判定重入复用。
3. **释放接线**：新增 `conversation_capture.release_active_lease(account)`（幂等、绝不抛），
   由 `api/messages.refresh_conversations` 的 `finally` 调用 —— 设计文档 §3.6 的最终落地。
4. **内部线程豁免租约**：`_exec` 新增 `internal` 参数。`_prewarm` 与保活回写走 `internal=True`，
   只走 `_lock` 串行、不参与租约仲裁 —— 「让位」的正确形态是排队而不是被拒后彻底放弃。
5. **早退判据改 `_dom_total`**：DOM 抓取块移到判据之前；hook 计数仅打印。
6. **显式失败**：BCC 未就绪时 `raise RuntimeError`（不再静默跑出「1.6s 假成功」）。

### 铁律（新增）

1. **跨调用窗口租约必须「取—传—释」三件套齐全**：只 acquire 不传 lease_id = 自我死锁；
   只传不 release = 该账号浏览器被自己占满 TTL，其它业务（发送/WP/保活）全部拿不到。
2. **容器自身后台线程不参与业务租约仲裁**：否则业务一持租（180s），预热永远跑不完，
   而业务又拿不到预热成果 → 只能每次重跑 160s（互为死锁）。
3. **早退/终止判据绝不能读已废弃的数据源**：抖音改版会让某个计数器恒 0，
   判据必须用**当前有效**的统计量（本项目 = DOM 累计数）。
4. **用户点按钮的路径一律 `PURPOSE_USER`**（prio=0/ttl≤300s），不得沿用 `PURPOSE_AUTO`。

### 实机验证判据

```bash
# 1) 源头：BCC 侧应出现重入复用，而不是被拒
grep -E "lease|昵称捕获完成" logs/browser_daemon_$(date +%Y%m%d).log | tail
#   期望：无 BCC-047「被拒」；有「昵称捕获完成：N 个」且 N 接近会话数（本项目 44）

# 2) backend 侧应收到非 0
grep "经 BCC 截到昵称数" logs/run_*.log | tail -3        # 期望 > 0

# 3) 落库硬证据
sqlite3 members/m7963938bc99a25a9/data/dyautodm.db  "select count(*) from dm_conversations where peer_name not glob '[0-9]*';"   # 期望 > 0

# 4) 租约已释放（不是等 TTL）
curl -s http://127.0.0.1:11231/lease_status      # 期望 holder=None
grep "已释放跨调用窗口租约" logs/run_*.log

# 5) 源码级回归（不启第二个 BCC）
python scripts/verify_lease_wiring.py            # 期望 PASS=31 FAIL=0
```

### 方法论记录

- **「BCC 抓到 37 个、backend 收到 0 个」是定位一切的关键矛盾点** —— 它把排查范围从
  「捕获逻辑（DOM/hook）」直接锁定到「**两个进程之间的租约协商**」。只看 backend 日志会误判为捕获失效。
- **两份日志必须同刻对照**：单看任何一份都能自洽（BCC 说成功、backend 说失败），
  只有并排才能看到「同一把租约 83a780e866e2」。
