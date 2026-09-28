# BCC 替换路线评估与记录 —— 提案（⏸ 待实施）

> **任务**：用户 2026-09-28「BCC 体系稳定性太差，想重构为 Electron 架构」→ 评估可行性 → 改判为「**先记录，稍后实施**」，并追问「有没有更成熟的方案」→ 用户裁定「**禁止商业/付费指纹浏览器**」。
> **分支**：`design/better-douyin`　|　**日期**：2026-09-28　|　**版本**：未动（记录态）
> **性质**：架构级评估 + 待实施路线**记录**。**本文件为纯记录，未改动任何源码、未 commit。**
> **状态**：⏸ 待实施。台账登记：`工作记忆/00_交接卡待办台账.md` **M-25**。
> **可用解空间（裁定后）**：**只剩路线 B（降维剥离）**。路线 A ❌（无效）、路线 C 🚫（禁止）。

---

## 一、目标与判定

**目标不是「换技术栈」，是「让 BCC 那类故障不再发生」。** 因此一切按「对 BCC 失败类是否有效」判定，而非按技术先进性。

**必须先澄清的前提**：BCC（`backend/daemon/browser_daemon.py:2`，Browser Context Container）**不是桌面壳**，是每账号一个的 Python 常驻 sidecar：内部跑 FastAPI、按 `crc32(账号名)` 稳定哈希占端口（`auto_dm/accounts.py:196-232`）、持有该账号唯一指纹浏览器 profile，后端经**回环 HTTP** 调它。

BCC 被自建的原因（`工作记忆/07_反爬对抗.md:18-22`）：重构前各模块各自 `launch_persistent_context` 直开 Playwright，多进程抢同一 profile 目录 ⇒ 锁冲突 + 崩溃。v0.29.0（2026-08-19）改为「每账号常驻 sidecar 持有唯一 context，串行执行」。

### 三条路线判定

| 路线 | 技术可行性 | 对 BCC 稳定性 | 判定 |
|---|---|---|---|
| **A. Electron 重平台** | 可行 | **零** | ❌ 不采用 |
| **B. 去掉浏览器常驻容器**（上游「无浏览器」等价物） | 可行，且我方已具备大半 | **正收益**（删掉失败类本身） | ✅ 推荐先试（分阶段） |
| **C. 换商业指纹浏览器 + Local API** | 可行 | 正收益（厂商持生命周期） | 🚫 **用户裁定禁止**（2026-09-28；`项目说明.md` §九 铁律 9）—— 见 §五 留档 |

---

## 二、路线 A：Electron 重平台 —— ❌ 不采用

### 2.1 换壳买不到 BCC 稳定性

Tauri 壳对 BCC 的**全部**作为 = 末尾一个看门狗（`src-tauri/src/sidecar.rs:351-435`，退避重启上限 5 次）。换壳（Tauri → Electron）触及的是窗口 + 进程监督，与 BCC 无关。

成本侧实测：前端 Tauri 耦合仅 **13 处 / 4 文件**（6 处动态 import + 10 处 `invoke`，集中于 `frontend/src/api/sidecar.ts`、`client.ts`、`App.tsx`、`components/topbar.tsx`）⇒ 换壳本身不难，但**收益为零**；且后端主体（191 文件 / **75,994 行**）必须保留，Electron 无法替代 FastAPI 业务层，重平台后变成 **Node + Python + 外部浏览器三运行时**，比现状多一层 IPC 与失败面。

### 2.2 「用 Electron 内嵌 Chromium 取代 BCC 的浏览器」—— 不可行

- Electron 只能内嵌其自带 stock Chromium，**无任何受支持方式**把第三方指纹内核（Camoufox / fingerprint-chromium）作为 BrowserWindow 托管；
- 放弃指纹 ⇒ 触犯铁律「内核不可用时直接报错，绝不静默回退原生 Playwright」（`backend/vbrowser.py:20-25`），并有历史事故：`d78f100` headless 环境跳变 ⇒ 「安全风险阻止访问」+ **双账号强制下线**；
- 保留指纹内核 ⇒ 仍旧只能经 CDP/Playwright 驱动外部进程，与现状同构，只是多一个 Electron 当控制器。

### 2.3 佐证：失败类与浏览器引擎无关

反复复发的失败类（重启风暴 / profile 陈旧锁与双实例 / 永久负缓存 / 租约 TTL 与真实耗时错配 / PyInstaller 入口与模块身份 / 进程清扫自伤）**全部由「存在一个常驻浏览器进程 + 我方自写其生命周期」这一前提衍生**，而非 Chromium/Firefox 本身不稳。ADR-023 §9.1/§9.2 自曝两处新缺陷（可重试 ⇒ 重启风暴；清扫函数杀掉调用者）即为该方法论的直接反证：**这是工程纪律问题，不是技术栈问题**。

---

## 三、路线 B：去掉浏览器常驻容器 —— ✅ 推荐先试

### 3.1 上游证据（`vendor/douyin_spider_upstream/`）

上游 = `cv-cat/DouYin_Spider`（commit `4479ea784b`，2026-09-20 快照；3198★，`pushed_at=2026-09-27`）。**全仓不启动浏览器**：对 `playwright|selenium|pyppeteer|camoufox|undetected|webdriver|chromedriver|remote-debugging` 的 grep 仅命中 2 处 fixture 布尔值（`dy_apis/login_api.py:1132` / `:2185` 的 `"webdriver": False`）。

| 能力 | 上游做法（无浏览器） | 证据 |
|---|---|---|
| 请求签名 | 纯 Python（a_bogus / X-Bogus / secsdk / dtrait 外壳 / bd-ticket / AWS4 SigV4）；**只有 2 个 VMP 包走离线 Node `vm`** | `utils/ab_pure.py`、`xbogus_pure.py`、`secsdk_web_sign.py`、`bd_ticket.py`、`dtrait.py`、`imagex_sign.py` |
| `__ac_signature` / challenge | 离线 Node `vm` 重放页面 VMP，DOM/navigator 用**抓一次的冻结常量**塑形 | `utils/acrawler.py:1-13,129-138`；`acrawler_runtime/run_ac_node.js:1-2,940-959` |
| 传输层 | `curl_cffi` 冒充 Chrome TLS/HTTP2（`impersonate=chrome151→chrome150`） | `utils/http_client.py:2-24` |
| 扫码登录 | **纯 HTTP**：`get_qrcode/` → `check_qrconnect/`，二维码 URL 直接打印 | `dy_apis/login_api.py:2626-2700`；docstring `login_api.py:1-13`「纯算，不依赖浏览器自动化」 |
| 直播弹幕 | **原始 WebSocket** `webcast100-ws-web-hl.douyin.com/webcast/im/push/v2/` + X-Bogus + protobuf | `dy_live/server.py:133-199` |
| 私信发送 | **HTTP** `POST imapi.douyin.com/v1/message/send`，protobuf + a_bogus + bd-ticket | `dy_apis/douyin_api.py:2277-2290` |
| 私信接收 | **WebSocket** `frontier-im.douyin.com/ws/v2`，`pbbp2` 子协议 | `dy_apis/douyin_recv_msg.py:16-42,106-122` |
| 进程模型 | **单进程 Python CLI，无守护、无端口、无监督** | `main.py:1-9`；`Dockerfile` `CMD ["python","main.py"]` |

**判定：这是「把问题删掉」而非「把问题搬家」。** 没有常驻浏览器进程，就没有 profile 抢占、陈旧 `parent.lock`、可见性切换被风控、PyInstaller 入口/模块身份、租约 TTL 错配、重启风暴。

**附带治好两处正在流血的伤口**：① 抖音登录页 DOM 锚点失效（ADR-023 §3 D3/D4：`input[name="normal-input"]` 不存在、扫码 Tab 点不到）—— 纯 HTTP 登录**对 DOM 漂移免疫**；② HC-14 / H-27① 的「凭证 3 小时失效」（BCC 的 `parent.lock` + 可见性切换 180s 冷却与该链路强相关）。

### 3.2 我方已具备的资产（成本比看上去低得多）

- **纯算签名栈已全在**：`backend/utils/{ab_pure,xbogus_pure,secsdk_web_sign,bd_ticket,mstoken,strdata_pure,fingerprint,tls_policy}.py` + `dy_util.generate_a_bogus/generate_csrf_token` + `builder/params.py:with_a_bogus`；
- **`curl_cffi` 已声明且在requirements**：`backend/requirements.txt:73`，注释「抖音接口统一走 curl_cffi（冒充 Chrome 的 TLS/HTTP2 指纹）」；
- **已有现成绕行开关**：`backend/api/platform.py:265` `use_bcc: bool = True  # True=BCC 容器内 fetch；False=纯 HTTP 直连`，`:467` 注明明写纯 HTTP 分支是「**照源项目**」实现 ⇒ 团队已为取用户信息建好过一条无浏览器通道并抄的就是上游；
- **私信收发的主通道本来就与浏览器无关**：recv-daemon 已连 `frontier-im.douyin.com`（同上游 recv）；发送的 ws 通道无浏览器，**只有 wp 兜底通道走 DOM**；
- **唯一真缺口**：离线 Node `vm` 运行器（算 `__ac_signature` 与 challenge 模板），目前**只存在于** `vendor/douyin_spider_upstream/utils/acrawler_runtime/`（4 文件 / 188KB），我方无自有实现。

### 3.3 代价与风险（不粉饰）

| # | 项 | 说明 |
|---|---|---|
| R1 | **设备指纹从「每账号一个 profile」→「一份常量库」** | 多账号**相关性封禁**风险上升；须为每账号派生独立设备画像（上游是单机单用户工具，**未做**这件事） |
| R2 | **昵称获取前提要重谈** | BCC 的被动 hook（复用前端自发 `im/user/info`，**零主动请求**，属风控级铁律）会消失；`platform.py:467` 的纯 HTTP 路径存在，但破坏了原始假设。注意铁律原文限定的是「**批量**查昵称」，单点查询早已有 HTTP 通道 |
| R3 | **新增显式部署依赖 Node ≥18** | 现 `backend/requirements.txt` 与部署清单**均未声明**；上游作短命子进程用（`acrawler.py:129-138`，15s 超时），非常驻服务 |
| R4 | **合规：只能取协议事实，不能搬代码** | 上游 `license = null`（`GET /repos/cv-cat/DouYin_Spider/license` → **HTTP 404**，2026-09-28 实测）⇒ 按伯尔尼公约 = 保留所有权利。可借鉴**端点路径/参数名/字段形态/流程顺序**（事实性协议信息），**不可复制表达层代码**。利好：签名栈我方已自研完成 |
| R5 | **wp DOM 兜底通道消失** | 发送目前是 ws ⇄ wp 互备、一条失败自动降级。去掉 wp 会改变发送可靠性语义 —— 须实测 ws 单通道的失败率是否可接受 |
| R6 | **中控台采集 / 加密媒体页内解析 / cookie 读写回写 / 环境审计** | 这几项仍需评估去向（部分已有非浏览器等价物，未有结论） |

### 3.4 ⚠️ 决定性未知 —— **2026-09-28 订正：原表述有误**（保留原判 + 实证推翻）

> **本节初版写的「未知 = 无浏览器是否触发抖音风控」是错的。** 读 `d78f100` / `c409b90` 全文后订正如下。

**`d78f100` 原文根因（2026-09-13，双账号强制下线）**：
> 「只读审计证据：账号均未配 `DY_PROXY`、chrome 实为 `--no-proxy-server` 直连、出口 IP 171.218.232.73(成都 CN) → **排除 IP/代理**；张老师 BCC **带 `--headless`**、尚进**有头**但两账号都弹 → **共因 = 环境跳变**。」
> 「新铁律：关键是『**登录环境与运行环境一致**』；凭证字段齐全 ≠ 有效。」

`c409b90` 的实施说明更明确：**「open-browser/扫码用 `headless=False`（有头）而守护用 `headless=True` → 同一 profile 两种环境 = 环境跳变」**。

⇒ **真实根因不是「没有浏览器」，是「同一个 profile 上出现两种浏览器环境」。** 无浏览器请求并**不**产生 profile 环境跳变。

**且「无浏览器可用」在本仓已是既成事实（本轮实测，非推断）**：

| 能力 | 现用通道 | 依赖浏览器？ | 证据 |
|---|---|---|---|
| **私信发送（主通道）** | `channel='ws'` → `DouyinAPI.send_msg`（imapi 私有接口） | **否** | `api/messages.py:1469-1470`「**主通道**…有 ACK、落库含 skey、**不依赖浏览器**」；`core/sender.py:247` |
| **私信接收** | `wss://frontier-im.douyin.com/ws/v2` 长连接 | **否** | `daemon/recv_daemon.py:851`（`RecvChannel` 线程 + `websocket-client`） |
| **直播链接解析** | `link_resolve.resolve_live_id`（纯 Python X-Bogus） | **否**（BCC `/resolve_url` 是替代/兜底） | `core/auto_dm.py:694`；`api/live.py:284` |
| **昵称（合规来源）** | 被动 hook `capture_userinfo_map` / `/userinfo_idb` | **是**（页面 IndexedDB / 页内截获） | 昵称铁律 `项目说明.md:202`；`dy_apis/login_api.py:877` |

⇒ **「先做尖刺证明无浏览器可行」这个前提本身已被部分证伪** —— 三条钱路里已有两条（发送、接收）长期无浏览器运行。**真正剩下的未知只有一个，且更窄**（见 3.4.1）。

#### 3.4.1 订正后的唯一未知（这才是要尖刺的）

**若把「登录」从真浏览器扫码改为纯协议（vendor 路径），该账号的「登录环境」= 无浏览器；此后但凡再用同一账号的常驻浏览器（被动 hook 抓昵称、采集、`/show` 查看）—— 是否构成一次新的「环境跳变」，从而触发 step-up / UID 漂移？**

- 这**直接命中** `d78f100` 立的铁律「**登录环境与运行环境一致**」，而不是「必须有无浏览器」。
- 已知的**硬约束（不受尖刺影响）**：只要昵称铁律不改（昵称唯一来源 = BCC 被动 hook），**浏览器就不能完全去掉** —— 这是路线 B 的**天花板**，必须写清（见附录 B 第 14/15 行）。
- **附带的可测项**：我方纯 HTTP 路径 `dy_apis/client.py:140-163` 用的是**裸 `requests`，不做 TLS/JA3 伪装**；`requirements.txt:69-73` 那句「抖音接口统一走 curl_cffi」与实现不符（全仓仅 `auto_dm/login_api_vendor.py` 用 curl_cffi）。是否需要补，属可对照测量的工程问题，不是风控未知。

⇒ **要尖刺的是 3.4.1 那个问句**（附录 A · E3），**不是**「无浏览器行不行」（E1 已经把这一问用事实结掉了）。


---

## 四、建议的实施顺序（分阶段，每阶段可独立回滚）

**原则**：按「收益 / 风险比」排序，**不动登录、发送、接收三条钱路的核心**，从「现在就是坏的」那条开始。

1. **阶段 0（零风险剥离 + 尖刺）—— 完整规格见附录 A**：
   - **E1（零风险，先做）**：机械核验「哪些能力已无浏览器」并**停用两条已无用途/已判红线的 BCC 路由**（`/resolve_url` 有纯 Python 替代；`/user_info` 服务于已废弃的主动批量查昵称）—— 不动钱路、不碰风控、不需真机。
   - **E2（可选，低风险真机）**：对**已无浏览器**的 ws 发送 / WS 接收做量级与失败率读数（R5 的去留判据）。
   - **E3（高风险，需你拍板 + 在场）**：3.4.1 那个「登录环境一致性」问句。**未获你明确拍板前不执行。**
2. **阶段 1（登录，收益最高）**：`get_qrcode/check_qrconnect` 纯 HTTP 扫码 + 我方自行渲染二维码。改动面小（2 个端点 + 前端出码），且**免疫 DOM 漂移**、顺带解 HC-14/H-27① 的凭证链路。
3. **阶段 2**：昵称读取改走既有无浏览器通道（`platform.py:467` 分支），评估 R2。
4. **阶段 3**：发送 ws 单通道可靠性实测（R5），决定 wp DOM 通道去留。
5. **阶段 4**：直播采集、加密媒体、cookie 回写、环境审计逐项剥离（R6）。

**止损律**：任一阶段实测被风控打回 ⇒ 记录反证并停止该支线，不叠加更多补丁。

---

## 五、路线 C：换商业指纹浏览器 —— 🚫 **用户已裁定禁止（2026-09-28）**，本节转为留档

> **裁定原文**：「**禁止使用商业指纹浏览器**，如果要付费我还费劲自己做产品干嘛，不如买月卡了」
> **落点**：`项目说明.md` §九 铁律 **第 9 条**（新增）。
> **效力**：路线 C **出局**，不再是「待重审」。本节保留是为了**避免后人重新提案**——它记录的是「已经取证过的结论」，不是可行性选项。

### 5.1 结论与我方评估史

「换 VirtualBrowser / Ant-Browser」**用户 2026-09-13 已问过，结论「不换」**（`工作记忆/06_凭证与签名.md:369-379`，附 GitHub API 实测提交频率）：

| 项目 | 90 天提交 | 内核 | 说明 |
|---|---|---|---|
| `adryfish/fingerprint-chromium`（当时在用） | 1 | 开源 | 最活跃的免费指纹内核 |
| `Virtual-Browser/VirtualBrowser` | **0** | **闭源** | 仅开管理界面；内核更新更少 |
| `Wtcity22/Open-Anti-Browser` | 2 | 用 adryfish 同款 | **仅多一层管理界面** |
| `Kaliiiiiiiiii-Vinyzu/patchright` | **22** | 驱动层 | ✅ 这才是「更新更活跃」的方向 |

同处并立的**铁律**：问「换更活跃的内核」时**先用 GitHub API 查真实提交频率，不要凭印象**。方法论要点：**「内核本身具备指纹能力，问题在没传参数，不在内核」**（`:336`）。

**因此：把「换商业产品」当作 BCC 稳定性解法，在本项目已被实测否决过一次。**

### 5.2 补充事实（本轮新增取证）

- **Mode B（CDP 接管商业产品）现为不可达死代码**：`backend/auto_dm/config.py:123` `VB_MODE = "exe"`；全仓无 `VB_MODE="cdp"`；且 `should_use_vb` 在读 `VB_MODE` **之前**就返回 `"camoufox"`（`vbrowser.py:1208-1220`）⇒ 8 个生产调用点恒走 Camoufox。`工作记忆/06_凭证与签名.md:33` 记明「当前 exe 模式无此服务」。
- **`vb_chromium/` 已物理删除**（commit `81b5cfa`，2026-09-20，用户拍板，Camoufox 成为唯一内核）。即：**重新引入商业产品 = 重新引入 mode B 那类「依赖厂商本地服务」的架构，而它当时是被否决的。**
- 商业产品名单探测（2026-09-28 实测，命令：`grep -rn -iE 'AdsPower|BitBrowser|Multilogin|GoLogin|VMLogin|MuLogin|Incogniton|紫鸟|比特浏览器'`）：
  - `backend/` + `frontend/` **源码：零命中** ⇒ 生产代码从未接过任何商业指纹浏览器。
  - 仓内 3 处**非源码**提及（全部指向同一方向，非评估结论）：
    1. `docs/_第四分析源_LLMWiki深度研究汇要.md:1356`（§1.3 指纹浏览器与环境隔离：列 `MostLogin`/`AdsPower` 为「内核级伪装 + 物理隔离 + 网络匹配」专业方案）；同文件 §4 并列**结构性风险**（见下）。
    2. **`_ext_repos/douyin-jiliu-src/README.md:101,329`** —— ⚠️ **本轮新发现：同类项目的现成先例**。这是本仓参照集里**另一个抖音私信自动化项目**，其「多账号管理」明确写「**比特浏览器集成**：支持多个抖音账号同时在线」，FAQ 更写明「系统会**自动从比特浏览器中提取账号的证书信息并缓存 24 小时**」⇒ **该同类项目恰恰选了路线 C**（厂商 Local API 托管 profile + 抽凭证复用），且已把它做成产品。这**不能证明路线 C 对抖音更优**（无实测对比），但**证伪了「路线 C 不切实际」这一直觉** —— 它在这类产品里是**主流做法**。该快照在 `工作记忆/10_上游源项目情报.md:466,536` 已登记（且该处自注「URL 待补」）。
    3. `原型.html:1877-2525`（UI 原型里的假数据：`BitBrowser · Profile #3`、`AdsPower · 环境 2`）⇒ 说明该路线**在你的产品设想里出现过**，只是从未进入技术选型。

  同文件 §4 对本路线给出的**结构性风险（值得单独记）**：
  > **指纹碰撞** —— 「大量用户使用相同的指纹模板会导致平台将该模板标记为『已知指纹浏览器』」；**行为指纹** —— 「环境隔离只能解决『你是谁』，不能解决『你做什么』，高频规律的自动化仍会被行为模型识别」。

  ⇒ 对本产品（**卖给多个用户、每人跑自己的账号矩阵**）含义特定：**所有客户共用同一家商业厂商的内核与模板** ⇒ 一台被标记，可能**连带标记整批客户**；而自建 Camoufox + 逐账号指纹种子（`06_凭证与签名.md:349-353`）在**指纹隔离度**上反而更可控。这是除 5.3 三条之外的**第四条**阻塞项。

  ⇒ 结论：这批产品**从未做过选型评估**（无 ADR、无实测），与 5.1 的两家不是同一层级；但仓内已有**同类项目采用它的先例** + **一份指向它的风险清单**。

### 5.3 若要重审：必须先答的三个阻塞项

工业界做 Windows 多账号自动化确实是「用厂商 Local API 而非自rolled」——**这条路的技术成熟度高于我方自建**（厂商专职维护 profile 生命周期 / 内核更新 / 指纹一致性，并公开 Local API + CDP）。但本项目有三处硬阻塞，**属业务决策而非技术决策**：

1. **按用户付费 + 厂商云账号** vs 产品定位「**凭证不上传任何第三方服务器**、本地加密隔离」+ 会员体系每会员独立数据空间（`项目说明.md:274-275`；`backend/services/member_store.py:4-23`）。每个终端用户都要自装并授权一个第三方收费客户端。
2. **抖音风控行为未经验证**：这批产品主场是 Facebook/Amazon 跨境电商多账号，对**抖音**是否优于 Camoufox **无任何证据**。（同类项目 `douyin-jiliu-src` 用了比特浏览器，但同样**没有**与内核方案的风控对比数据。）
3. **失去自补能力**：我方当前深度改动 patchright / camoufox 内部；交给闭源厂商后不可补。
4. **风险耦合（本轮新增，见 5.2 §4）**：商业方案的多客户**共用同一内核模板** ⇒ 指纹碰撞会把「一个客户被测出」放大为「一批客户被测出」；本产品是**多用户分发的矩阵工具**，与「自己一个人用商业浏览器」的风险面不同。我方自建的逐账号指纹种子（`fingerprint_seed_of`）在隔离度上更可控。

5. **产品自杀（用户裁定，最高优先）**：本产品自身就是要卖的 —— **再为每个终端用户引入一份第三方月卡，等于自断产品价值**（用户原话：「如果要付费我还费劲自己做产品干嘛，不如买月卡了」）。这一条否决的是**「付费第三方」这个形态本身**，不只否决具体某家厂商。

### 5.4 由此推出的边界（本轮新增，替代原「重审触发条件」）

| 可选 | 禁止 |
|---|---|
| 自持指纹内核（Camoufox / adryfish fingerprint-chromium 等**免费开源**内核） | 任何**按用户收费**的指纹浏览器 / profile 托管服务 |
| 自研或复用**开源**容器治理底座 | 把 BCC 的 profile 生命周期治理**外包给厂商云** |
| 复用**已有**开源依赖（patchright / playwright / psutil / `curl_cffi` 等） | 引入任何**新的付费依赖**承担核心能力 |

> **注意「开源」≠「免费商用」**：若未来考虑某开源 base，仍须先查许可证（本项目已有先例：上游 `cv-cat/DouYin_Spider` `license=null` ⇒ 只能借鉴协议事实，见 M-24）。
>
> **若真要了解同类项目怎么做**（不引入，仅供设计参考）：`_ext_repos/douyin-jiliu-src/README.md:101,329` 就在本地，可只读其「抽证书 → 缓存 24h → 发送」的**流程形态**（协议事实层），不引代码。

### 5.5 路线 C 出局后的剩余空间 —— 含一个「似是而非」的选项（必须点破）

用户仍想「换掉现有 BCC」。C 被禁后，形式上还剩第四种做法：

| 选项 | 内容 | 判定 |
|---|---|---|
| **D. 同形态重写 BCC 治理层** | 保留 Camoufox + 保留常驻 sidecar，只重写治理逻辑（换语言/换框架/换锁实现） | ⚠️ **单独做无收益** —— 见下 |

**为什么 D 是「似是而非」**：ADR-023 已定位的 5 个缺陷**全部出在自撰治理逻辑**，与形态和语言无关：

| 缺陷 | 若「重写一遍」是否会复发 |
|---|---|
| 负缓存无出口（失败即钉死） | **会** —— 这是设计模式问题，换语言照样踩 |
| 锁判据作用域错（机器级 vs 档案级） | **会** —— 判据设计问题 |
| 自愈调用顺序颠倒 | **会** —— 契约问题 |
| 「可重试」缺速率上限 ⇒ 重启风暴 | **会** —— 安全属性问题 |
| 进程清扫按命令行文本匹配 ⇒ 杀自己 | **会** —— 判据设计问题 |

⇒ **换实现不消除缺陷，只是把它们用另一种方式写一遍。** 真正降低 BCC 失效面的动作只有一个方向：**减少「必须常驻浏览器」的职责面**（= 路线 B）。

**折中选项**：路线 B 可**只剥离一部分**（例如先剥「昵称 / 用户信息 / 发送通道」，保留登录与采集在 BCC）—— 这样不必一次性推翻，剥掉哪部分，哪部分的常驻失败类就消失。这是**风险最低的「换 BCC」实际做法**。

---

## 六、事实库（可复核索引）

| 结论 | 证据位置 |
|---|---|
| BCC 定义与自建原因 | `backend/daemon/browser_daemon.py:2`；`工作记忆/07_反爬对抗.md:18-22` |
| BCC 规模与守卫 | BCC + 浏览器 + 采集核心 ≈ **11,300 行 / 19 文件**；守卫 `test_bcc_*` 等 **1,391 行** |
| 壳对 BCC 的全部作为 | `src-tauri/src/sidecar.rs:351-435`（`BCC_MAX_RESTARTS=5`） |
| 前端 Tauri 耦合面 | 13 处 / 4 文件（`sidecar.ts` / `client.ts` / `App.tsx` / `topbar.tsx`） |
| 后端规模 | 191 文件 / 75,994 行（非测试） |
| 指纹铁律 | `backend/vbrowser.py:20-25`；`项目说明.md:260-266` |
| 现有纯算签名栈 | `backend/utils/{ab_pure,xbogus_pure,secsdk_web_sign,bd_ticket,mstoken,strdata_pure,fingerprint,tls_policy}.py` |
| `curl_cffi` 已声明 | `backend/requirements.txt:73` |
| 已有 BCC 绕行开关 | `backend/api/platform.py:265,467`（`use_bcc=False`） |
| 无头被风控史 | `d78f100`、`c409b90`；`backend/auto_dm/config.py:49-58` |
| 上游无浏览器（逐能力） | 见 §3.1 表；vendor 快照 `vendor/PROVENANCE.md` |
| 上游 LICENSE 缺失 | `GET /repos/cv-cat/DouYin_Spider/license` → **HTTP 404**（2026-09-28 实测）；`license=null` |
| 商业方案已否决（两家自建管理界面） | `工作记忆/06_凭证与签名.md:369-379` |
| 商业方案（工业级）从未评估 + 同类项目先例 | `_ext_repos/douyin-jiliu-src/README.md:101,329`（比特浏览器集成 / 抽证书缓存 24h）；`docs/_第四分析源_LLMWiki深度研究汇要.md:1356`+§4（指纹碰撞风险）；`原型.html:1877-2525`；`工作记忆/10_上游源项目情报.md:466,536` |
| mode B 不可达 / `vb_chromium` 已删 | `backend/auto_dm/config.py:123`；`backend/vbrowser.py:1208-1220`；commit `81b5cfa` |
| 登录 DOM 锚点已失效 | `docs/adr/ADR-023-credential-update-routing-and-bcc-lazy-retry.md` §3 D3/D4 |
| 重启风暴/自伤反证 | 同上 §9.1/§9.2 |

**未取得证据项（不推断）**：① 上游 `create_v2` 真实发布成功率；② 上游方案在**我方账号/IP 环境**下的风控表现；③ 各商业指纹浏览器对抖音的实测优劣（现已禁止，不再取数）。

---

## 附录 A：阶段 0 尖刺设计（可执行规格）

> 用户令「处理吧」（2026-09-28）。本附录是**规格**，不是执行记录 —— 除 E1 外均未执行。
> **原则**：把「未知」压到最小再去碰账号。**能用事实结掉的问句，不做实验。**

### A.1 E1 —— 零风险机械核验（**先做这个，不碰账号**）

**目的**：把「哪些能力已无浏览器」从**说法**变成**可复跑读数**，并摘掉两条已无用途的 BCC 路由。

| 步骤 | 命令 / 动作 | 硬判据 |
|---|---|---|
| E1-1 | 复核发送主通道无浏览器：`grep -n "channel='ws'" backend/api/messages.py`（或读 `:1467-1470`） | 存在「主通道…**不依赖浏览器**」原文 |
| E1-2 | 复核接收无浏览器：`grep -n "frontier-im" backend/daemon/recv_daemon.py` | 命中 `wss://frontier-im.douyin.com/ws/v2` |
| E1-3 | 复核昵称合规来源唯一：`grep -n "AUTH-043" backend/dy_apis/login_api.py` | 主动批量查询**带废弃警告**；被动路径存在 |
| E1-4 | 复核 `/user_info` **零生产调用方**（两个调用点分别在零调用的 `/user/info/batch` 与已废弃零调用的 `bulk_user_info_via_browser` 内） | `grep -rn '"/user_info"' backend/` + 逐调用点回溯，**每个都落在零调用方函数内**（已实测成立，见附录 B #7）（2026-09-28 实测） | 
| E1-5 | 查明 `/resolve_url` 的**兜底语义**（实测：它位于 `link_resolve._browser_resolve` 内，而 `_browser_resolve` 本身是 reflow 主引擎失败后的备用路径 —— 即「**兜底的兜底**」；BCC 失败还要退回直开浏览器） | 判据 = 能明确写出「去掉 BCC 后 reflow 失败时的失败链是什么」；**这不是 PASS/FAIL，是设计输入**（见附录 B #11） |
| E1-6 | **摘死端点**：`/user/info/batch`（`api/platform.py:456`）**全仓零调用**且 `use_bcc` 默认 `True` ⇒ 处置它（删除，或把默认改 `False`），使 #7/#10 两条 BCC 路由失去唯一入口 | 改后 `python -m unittest discover` **零回归**；路由数 **20 → 20**（**BCC 路由代码保留、零调用**，便于回滚） |

**E1 的失败模式**：若 E1-4/E1-5 查出**除已废弃路径外**还有生产调用方 ⇒ **停手**，把该调用方登记清楚再谈（不得为「省一个路由」而砍活功能）。

**E1 成本**：零风控风险、零真机、零账号状态变更；改动面 ≤ 3 个文件。

### A.2 E2（可选）—— 已无浏览器通道的量级读数

**目的**：回答 R5（能否去掉 wp DOM 兜底），用**已经在跑**的通道取数，不引入新行为。

| 项 | 规格 |
|---|---|
| 观测对象 | ws 单通道发送的**失败率**（按 `core/sender.py` 的 `FAIL_KINDS` 分类）+ 接收 WS 的**停推/重连频率** |
| 数据来源 | **既有日志/落库**（`run_*.log`、发送结果表），**不新增探针、不发测试消息** |
| 硬判据 | 判**通过** = ws 单通道失败率 ≤ 同期 wp 兜底实际救回率（即「去掉兜底不多丢消息」）；判**失败** = 兜底确实是必要安全网 ⇒ R5 结论为「保留 wp」 |
| 止损 | 数据不足以判定 ⇒ **记「数据不足」**，不得以推断代替（本项目已明令禁止） |

#### A.2.1 E2 执行记录（2026-09-28，只读取数，**未新增探针、未发测试消息**）

**结论：`数据不足` —— R5 无法用既有数据判定。** 逐来源取证如下（三条来源全部不含通道维度）：

| # | 来源 | 命令 / 实测 | 结果 |
|---|---|---|---|
| 1 | 发送侧日志（失败率分子） | `grep -rn "SEND-001" logs/ backend/logs/ dist/logs/ src-tauri/logs/` | **仅 3 条**（全在 `backend/logs/run_20260912_1209*.log`）。且当时 `utils/code_logger` 补丁（2026-09-13 才加）**未安装** ⇒ loguru 把首参当模板，描述被吞 ⇒ 留存行**只剩代码 `SEND-001`**，读不出「哪条通道失败」「回退是否成功」 |
| 2 | 发送侧日志（分母） | `grep -rhoE "\[dm-dispatch\] *[^"]{0,40}" …` | `入池` 共 **9 条**（6×四川工伤张老师 + 3×尚进工伤小助理），**全部是「测试白名单」放行的手动发送** ⇒ 分母过小，任何比率都无统计意义 |
| 3 | 落库（通道归属） | `dm_messages` 建表语句 `database.py:186-197` | **无通道列**。`extra.source` 仅 `daemon/wp_recv.py:336` 主动打标 `"wp"`；WS 通道**不打标**（读侧 `api/messages.py:534-535` 兜底为 `"ws"`）⇒ 落库侧无法反推真实通道归属 |
| 4 | 落库（真实设计库实测） | 只读查 `C:\temp\dyautodm_design\members\m17db0f8209156f26\data\dyautodm.db` | `dm_messages` 共 **914 行**；`extra.source` 分布 = `{None: 885, "recv_daemon": 29}` ⇒ **零条 `"wp"`**（注：`recv_daemon` 是投递标记行的来源标签 `recv_daemon.py:1816/1818`，**不是通道**）。顶层 `C:\temp\dyautodm_design\data\dyautodm.db` 为 0 行 |
| 5 | 接收侧（停推/重连频率） | `grep -rn "WP 通道新增" …`（`wp_recv.py:374` 的唯一 INFO）；`grep -rhoE "RECV-0[0-9]{2}" …` | **两者均零命中**。`logs/recv_daemon_20260923.log` 仅 528 B 且内容为测试临时根 `hc10_v*` 的 `BCC-039` 告警 ⇒ 无生产接收侧日志留存 |
| 6 | 失败分类维度 | `core/sender.py:38-48` `FAIL_KINDS` | 只做「失败原因归类」（credential/risk/ratelimit/blocked/param/network/other），**不含通道维度** |

**副产品（结构事实，可直接作 R5 输入，无需再取数）**：

- `wp_send_dm` 全仓**只有 1 个调用方** = `/send`（`api/messages.py:1489`）内 `_try("wp")` 的兜底分支 ⇒ **wp 发送的唯一职责是「手动发送的兜底」**；自动获客/自动回复链（`services/dm_dispatch`）本就**只走 ws**，wp 不参与。
- `wp_recv` 落库前有 `_already_exists` 去重（`daemon/wp_recv.py:331`）⇒ **WS 先到则 wp 必然跳过**；真实库「零条 `wp`」与此语义一致（wp 从未贡献过独占消息）。
- ⇒ 即「wp 通道」的实际爆炸半径 = ① 手动发送的兜底；② 一个从未贡献独占消息的接收循环。这**缩小**了 R5 的后果面，但**不构成**「可去 wp」的判据（仍缺失败率读数）。

**要判 R5 的前置（本轮未实施，属新增行为）**：需先在 `/send` 的 `_try` 收口处补一个**只读**结构化埋点（记 `first`/`second`/最终通道 + 结果 + 错误类），跑够样本后再取数；否则只能取保守结论「**保留 wp 作手动发送兜底**」。按 A.2 止损律，此处**以「数据不足」结案，不以推断代替**。

### A.3 E3（高风险）—— 唯一真尖刺：登录环境一致性（3.4.1）

> ⚠️ **本实验会把一个真实账号的「登录环境」从真浏览器改为纯协议。可能触发 step-up / UID 漂移 / 凭证失效。** 未获用户明确拍板 + 在场，不执行。**绝不使用主力账号。**

#### A.3.0 E3 前置侦察（2026-09-28，只读，未触碰账号）—— **成本估计需下调**

**发现：纯协议扫码登录的实现已在本仓，但零调用方、从未验证。**

| 符号 | 位置 | 实现 | 调用方（全仓实测） |
|---|---|---|---|
| `dyGenerateQRcode(auth)` | `backend/dy_apis/login_api.py:1238` | `api="get_qrcode/"`，**plain `requests.get`**（非 `curl_cffi`，无 JA3/JA4 伪装） | **0** |
| `dyCheckQrCodeLogin(auth, token)` | 同文件 `dyGenerateQRcode` 之后 | `api='check_qrconnect/'`，**plain `requests.get`** | **0** |
| `qrcodeMain(env_path, on_qrcode, …)` | 同文件 `:1482` | 编排 get_qrcode → 本地 `qrcode` 库出码 → 轮询 → 回调 `on_qrcode({'token','qrcode_index_url'})`；**已带出码回调** | **0**（仅 `:1657` 一行注释掉的 `loop.run_until_complete(...)`） |

- **证据**：`grep -rn "dyGenerateQRcode\|dyCheckQrCodeLogin\|qrcodeMain" backend/` ⇒ 除定义处与那行注释外**零命中**。
- **历史**：`git log -S "qrcodeMain" / -S "dyGenerateQRcode"` ⇒ 两者**最后一次改动都是 `af5934b`**（Tauri 2 + FastAPI Sidecar 架构重构）⇒ 属**架构重构时带过来的遗留**，此后从未接线。
- **对成本的影响**：§四 阶段 1 原估「改动面小（2 个端点 + 前端出码）」—— 现证实**更小**：出码/轮询/回调**已实现**，工作降级为「接线 + 前端渲染 + 验证」。
- ⚠️ **不可据此认为「登录已经能用」**：`零调用 ⇒ 从未验证`；且与上游关键差异在 **TLS 指纹**（上游 `curl_cffi`，本处 plain `requests`）⇒ **风控表现恰恰是 E3 要出的那个结论**，不得前置假定。
- ⇒ E3 步骤 1 的措辞随之精确化：不是「照 vendor 现写一套协议登录」，而是「**先接线仓内这套从未验证的纯协议扫码**（或 vendor 的 `curl_cffi` 版），拿到凭证后按 A.3 表逐项观测」。**选哪一套本身是 E3 的第一个设计决策**（plain `requests` vs `curl_cffi` 决定「环境跳变」的成因解释力）。

| 项 | 规格 |
|---|---|
| **唯一问句** | 纯协议登录后，该 token 被抖音绑定到「无浏览器环境」；**此后再用同一账号的常驻浏览器**（被动抓昵称 / 采集 / `/show`）是否构成环境跳变 ⇒ step-up / 漂移？ |
| **前置** | ① 用户指定**测试账号**（非张老师/尚进等主力）；② 记录实验前基线：`uid_identity_verdict()` 的 4 元组 + `uid` 原值 + 当日发送成功率；③ 全程 `git` 干净或在独立分支；④ 明确回滚动作（改回真浏览器扫码）已写好 |
| **步骤** | 1) 用 vendor 纯协议路径登录该测试账号，**记录凭证产出**；2) **不做任何浏览器动作**，先静置观测（对齐本项目既有「静置 70s 零增长」式读数习惯）；3) 再用**同一账号**拉起常驻浏览器做一次**被动抓昵称**（`/userinfo_idb`）；4) 观测 ≥72h |
| **硬判据 · PASS** | ① 登录成功且凭证可用；② 观测窗内 `uid_identity_verdict()` 恒为 `state=True`（**无 AUTH-050 UID 漂移**）；③ 无「安全风险阻止访问」类拦截；④ 接收 WS 无 `INVALID_REQUEST` 停推；⑤ 发送成功率不低于实验前基线 |
| **硬判据 · FAIL** | 命中任一：`state≠True` / UID 漂移 / 出现「安全风险/已阻止此次访问」/ WS 停推 ⇒ **环境一致性铁律被破坏** ⇒ 登录路径**必须留在真浏览器**（路线 B 阶段 1 出局，其余剥离不受影响） |
| **硬判据 · INVALID（不算失败，但不出结论）** | 登录本身就被协议层拦下（拿不到凭证）⇒ 只证明「协议登录不可用」，**不构成**对「环境一致性」的判断；记录后停止，不得把 INVALID 写成 PASS 或 FAIL |
| **红线** | 不发任何消息 / 不发布内容 / 不点赞关注；不强杀进程（停进程走 `/quit`）；不写真实 design 数据根；不把实验账号换成主力账号 |

### A.4 阶段 0 完成标准（可勾选）

- [x] E1 六项判据全部达成，且**全量测试零回归** —— E1-1~E1-4 逐条复跑命中、E1-5 产出设计输入（`/resolve_url` 是「兜底的兜底」⇒ 移出零风险集）、E1-6 已落地（默认翻转为 `False`，未删路由，便于回滚）。**回归对比见下**。
- [x] E2 的 R5 结论 = **如实记「数据不足」**（三条来源均无通道维度；详见 A.2.1）
- [x] E3 **明确记为「用户未拍板，未执行」**（未触碰任何真实账号）；**前置侦察已完成**（A.3.0：仓内已有零调用的纯协议扫码实现 ⇒ 成本下调，但**从未验证**，风控表现仍待 E3 出结论）
- [x] 三处记录同步：本附录 + 台账 **M-25** + `工作记忆/07_反爬对抗.md`（BCC 职责表）

#### A.4.1 E1 回归对比（2026-09-28，基线用 `git worktree add <tmp> aaaf172 --detach` 取干净 HEAD）

| 组 | 测试数 | 结果 | skipped |
|---|---|---|---|
| 基线（干净 HEAD `aaaf172`，独立 worktree） | **1096** | `FAILED (failures=12, errors=2)` | 5 |
| E1 改后（工作区，含 `use_bcc=False` + 新增门禁） | **1103** | `FAILED (failures=12, errors=2)` | 0 |

**逐项比对结论：失败集一字不差（14 个标识符完全相同）⇒ E1 零回归。** 1103 − 1096 = **7** = 新增门禁 `test_bcc_duty_surface_e1.py` 的 7 个用例；两侧 skipped 差 5 = `TestUpstreamParity`（`test_upstream_write_align_t1_t2.py:774` 的 `@skipUnless(os.path.isfile(_UPSTREAM), …)`）—— `_UPSTREAM` 指向 **git submodule** `_ext_repos/DouYin_Spider_git/dy_apis/douyin_api.py`，新 worktree 未 `submodule update --init` ⇒ 基线跑时缺失而触发跳过（主仓该文件 EXISTS）。**该 5 项跳过与 E1 无关**。

14 项失败**全部为既有债**（两侧同现，非本轮引入）：

| 模块 | 数量 | 形态 | 是否顺序相关 |
|---|---|---|---|
| `test_upstream_write_align_t1_t2.TestT1SendMsgInRoom` | 8 F + 2 E | `spy.call_args` 为 `None` / `KeyError: 'api'` ⇒ 打桩未被调用 | ✅ 顺序相关（只跑这 5 个模块时**全绿**，全量里红） |
| `test_ai_agent.TestZeroRegression` | 2 F | 跨测试类残留数据（M-17 已登记） | ✅ 顺序相关 |
| `test_p2_live_guards.TestDeleteStrategyOrdering` | 1 F | 解绑失败却回 `ok=True` | ✅ 顺序相关（子集跑**绿**，全量红） |
| `test_ai_client_method_structure` G6 | 1 F | 全仓 AST 扫描命中 `test_task_scheduler_gates.py:699`（`_make_test()` 内嵌 `_t()`） | ❌ **非顺序相关** —— 5 模块子集里也红 |
| `test_h29_message_schema` G7 | 1 F | 真实设计库「仍有 103/914 行未标注 kind」= 活体数据漂移 | ❌ **非顺序相关** —— 子集里也红 |

⇒ **新增登记需求**：`test_upstream_write_align_t1_t2`（10 项）与 `test_p2_live_guards`（1 项）属**此前未登记的 M-17 同族顺序债**；G6/G7 两项为非顺序相关的既有红。本轮**只登记不改**（超出 E1 范围，且 G7 依赖活体数据需先行裁决）。

**基线工作区的一个坑（本身即缺陷，见 M-26）**：`backend/test_browser_visibility_guard.py:21` 硬编码 `BE = r"C:\Users\LOX\Desktop\DYchajian\DYAutoDM_v2\backend"` 并 `sys.path.insert(0, BE)` ⇒ 在**任何非该绝对路径的仓库副本**里跑 `unittest discover`，`_find_tests` 会因模块被从别的目录导入而直接 `ImportError` 中止（实测：基线首跑仅 2536 B 即崩，`test_capability_probe module incorrectly imported from …`）。基线运行前已把该副本的该行改为 `os.path.dirname(os.path.abspath(__file__))`（**只改基线副本，未改主仓**）。

---

## 附录 B：BCC 职责剥离清单（20 路由逐项，本轮实测）

> 数据源：`backend/daemon/bcc_routes.py` 全量 `@router` 扫描（**实测 20 个**）+ 逐路由调用方 grep。
> **「可剥」= 已有非浏览器等价物或该路由已无用途；「不可剥」= 依赖浏览器被动观测或真浏览器交互。**

| # | 路由 | 职责 | 依赖浏览器 | 现有非浏览器等价物 / 调用方 | 判定 |
|---|---|---|---|---|---|
| 1 | `GET /status` | 就绪/探活 | 是（容器自身） | — | **不可剥**（容器治理） |
| 2 | `GET /lease_status` | 租约查询 | 是 | `bcc_lease.py`（BCC 内部机制） | **不可剥**（随容器存亡） |
| 3 | `POST /lease` | 申请租约 | 是 | 同上 | **不可剥** |
| 4 | `POST /lease/renew` | 续租 | 是 | 同上 | **不可剥** |
| 5 | `POST /lease/release` | 释放租约 | 是 | 同上 | **不可剥** |
| 6 | `POST /cookie` | 刷新 cookie | 是 | 纯算签名栈（`secsdk_web_sign`/`bd_ticket`/`mstoken`）**存在**，未接线 | **待评估**（有可剥基础） |
| 7 | `POST /user_info` | **主动批量查昵称/头像** | 是 | 纯 HTTP 等价物存在（`_im_user_info_by_sec`，`api/platform.py:535`，2026-09-14 真机 `status_code=0`）。**且实测该路由零生产调用方**：两个调用点 `api/platform.py:499`（在 `/user/info/batch` 端点内，**该端点全仓零调用**，前端只调单数 `/api/platform/user/info`）与 `dy_apis/login_api.py:908`（在 `bulk_user_info_via_browser` 内，**已标 AUTH-043 风控红线且零调用**） | ✅ **应剥**（**E1-4**；零调用方 ⇒ 停机风险≈0） |
| 8 | `POST /capture_userinfo` | 被动 hook 抓用户资料 | **是（被动观测）** | 无等价物 | **不可剥**（昵称铁律依赖） |
| 9 | `POST /userinfo_idb` | 纯读页面 IndexedDB（零网络） | **是** | 无等价物 | **不可剥**（昵称铁律依赖） |
| 10 | `POST /user_info_by_uids` | 按数字 uid 批量查 | 是 | 同上 `_im_user_info_by_sec` / `get_im_user_info` | ✅ **应剥**（同 #7，与 `/user/info/batch` 同一死端点） |
| 11 | `POST /resolve_url` | 打开链接抠 live_id | 是 | **唯一调用点** `link_resolve.py:288`（在 `_browser_resolve` 内）；而 `_browser_resolve` 是 reflow 主引擎失败后的备用路径（`:276`「转备用」），且 BCC 失败还会退回直开浏览器（`LIVE-016/017`） | ⚠️ **可剥，但需先裁决兜底语义** —— 去掉后失败链变成「reflow 失败 → 直开浏览器（**抢锁**，即 BCC 当初要解决的问题）或直接失败」；另注：该路由**每次调用留一个不 close 的空 tab**（`browser_daemon.py:1423`） |
| 12 | `POST /exec_js` | 页面内执行 JS | **是** | 无等价物 | **不可剥**（4 个调用点在 `api/messages.py:638/1209/1290/1375`，**须逐个查明用途**） |
| 13 | `POST /linkmic_run` | 连麦 | 是 | 无 | **待查明**（本轮未取到调用方证据） |
| 14 | `POST /wp_messages` | 拉被动截获的 WP 私信事件 | **是（被动）** | 接收主通道已是 `frontier-im` WS（`recv_daemon.py:851`） | **可剥候选**（wp_messages 是 v0.44 前的收件路径；须确认是否仍有生产消费者） |
| 15 | `POST /wp_send` | chat 页 DOM 发送 | **是** | **主通道 `DouyinAPI.send_msg` 已不依赖浏览器**（`api/messages.py:1470`） | **可剥候选**（= R5，须 E2 取数决定去留） |
| 16 | `POST /env_audit` | 环境泄漏探针 | **是** | 无（本身就是浏览器探针） | **不可剥**（真浏览器在线才有意义） |
| 17 | `POST /scan_login` | 真浏览器扫码 | **是** | vendor 纯协议登录适配层**已存在但零生产调用**（`auto_dm/login_api_vendor.py`；审计 `artifacts/AUDIT_2026-09-26_H22…:218` 记「当前无调用方」） | **待 E3 裁决** |
| 18 | `POST /refresh` | = `scan_login` 兼容口 | 是 | 同 #17 | **待 E3 裁决** |
| 19 | `POST /show` | 切换容器可见性 | **是** | 无 | **不可剥**（且是「环境一致」铁律的调节阀，改动需极慎） |
| 20 | `POST /quit` | 优雅退出 | 是 | 无（且**铁律要求**停进程只能走它） | **不可剥** |

**计数核对（20 = 11 + 7 + 2）**：

| 类别 | 条数 | 路由 |
|---|---|---|
| **不可剥**（容器治理 / 铁律依赖 / 无等价物） | **11** | #1 #2 #3 #4 #5 #8 #9 #12 #16 #19 #20 |
| **可剥 / 待裁决** | **7** | #7 #10 #11 #14 #15 #17 #18 |
| **待评估 / 待查明** | **2** | #6 `cookie`、#13 `linkmic_run` |

**结论（剥离空间的天花板）**：20 个路由里 **11 个结构性不可剥**（容器治理 6 + 昵称铁律依赖 2 + `exec_js`/`env_audit`/`show` 3），
真正可剥的窗口 = **7 条**，其中**零风险即可摘的是 #7 / #10 两条**（都因宿主端点为死端点而**零生产调用方**）；
**#11 需先裁决兜底语义**；#14/#15 需 E2 取数；#17/#18 需 E3 裁决。

⇒ **即使把七条全剥掉，BCC 仍需存在**（#8 / #9 / #12 / #16 / #19 / #20 + 容器治理 6 项 ⇒ 至少 11 项）。
**「换掉 BCC」的准确含义只能是「把它从 20 项职责缩到 13 项（其中 #6/#13 仍待评估）」**，而不是「不再需要它」。
这一点必须在任何后续提案里如实写清 —— 否则又是在承诺一个做不到的目标。

