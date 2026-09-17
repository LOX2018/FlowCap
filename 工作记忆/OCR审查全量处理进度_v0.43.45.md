# OCR 全量审查 · 处理进度与处置记录

> 报告源：`D:\SJ  agent\DYchajian-review-design-better-douyin-{detailed.md,json}`
> 生成 2026-09-17 16:30 | open-code-review v1.12.4 | 355 文件 / 1484 条
> 处置原则：**逐条实测甄别**（机器报告≠真实缺陷，抽样误报率约 30%）

---

## 1. 范围甄别

| 范围 | 条数 | 处置 |
|---|---:|---|
| `DYAutoDM_v2/*`（产品代码） | **1312** | ✅ 纳入 |
| `_ext_repos/DouYin_Spider-master` | 142 | ❌ 第三方对照库 |
| 仓库根工具脚本 / docs | 30 | ⚠️ 低优先 |

产品代码严重度：**CRITICAL 38 / HIGH 197 / MEDIUM 590 / LOW 487**
类别：bug 644 / maintainability 408 / security 77 / performance 58 / 其它 143

## 2. CRITICAL 38 条 —— **已全部定性**

### 2.1 已修复（20 条，分三批）

**v0.43.44（14 项）**

| 文件 | 缺陷 | 验证 |
|---|---|---|
| `services/pro_kb.py` | logger 未导入（**上轮回归**） | 语法 |
| `vbrowser_window.py` | 拆分丢 ctypes 导入 | 语法 |
| `features.py` | 多传参数致 TypeError | 比对签名 |
| `core/auto_dm.py:773` | `config` 未定义 → NameError | 属性存在性核实 |
| `builder/auth.py` | 空 cookie 覆盖有效凭证 | 读实现 |
| `test_send_gate_config.py` | 6 条断言错 | **6/6 通过**（原 4fail+2err） |
| `mcp/tools.py` ×5 | 参数名与底层签名不符 | 比对 13 个方法签名 |
| `dy_apis/client_video.py` | 丢 `@staticmethod` | 装饰器+语义验证 |
| `dy_apis/client_live.py` | 同上 | 同上 |
| `dy_apis/client_user.py` | **硬编码他人 sec_uid** | 全库扫无残留 |
| `api/messages.py:78` | lease 未释放 | 签名核实 |

**v0.43.45（6 项）**

| 文件 | 缺陷 | 验证 |
|---|---|---|
| 全库 17 文件 | **63 处 `verify=False`** → 统一 `tls_verify()` | **真实域名 verify=True 握手正常** |
| `api/mcp.py` ×3 | `/restart`、`GET/POST /audit` 漏鉴权 | 7/7 端点校验核实 |
| `frontend/accounts-page.tsx` | 双 setRole 并发覆盖 | `tsc --noEmit` 0 |
| `frontend/AgentSection.tsx` | effect 覆盖新建 draft | 同上 |
| `frontend/platform-page.tsx` | queryKey 与请求参数不一致 | 同上 |

### 2.2 判定为误报（18 条，**不改**）

| 文件 | OCR 主张 | 实测结论 |
|---|---|---|
| `utils/data_util.py:12` | 正则空分支失效 | **实跑证明非法字符被正确去除** |
| `core/live_hook.py:275` | `frame.messagesList` 字段错 | 用的就是 `response.messagesList`，与 `dy_live/server.py:63` 一致 |
| `login_capture.py:149` | None 除法 | 已有 `is not None` 保护 |
| `scripts/verify_api_split.py:42` | check 有 positional-only | 三参均为普通参数 |
| `test_model_hub_v2.py` ×2 | 断言与实现不符 | 断言已是 `["sem"]` |
| `scripts/build_sidecar.py:268` | 注入标记丢失 | 标记在 :1169/:1172 存在，缺标记时也会 warn |
| `scripts/probe_img_*.py` ×4 | 缺 `import os` | 实际已 import |
| `src-tauri/lib.rs:34` | Mutex guard 跨 await | 调用为同步，未跨 await 点 |
| `api/logs.py` | open 竞态 | 已有 exists()/is_file() 前置，属低危建议 |
| `frontend/tsconfig.json:4` | 键名拼写错误 | 键名本身正确（实际为 `useDefineForClassFields`） |

## 3. 处理中（HIGH 197 条）

### 3.0 v0.43.46 已处置（commit `a7166ae`）—— 三条 NameError 存活性缺陷

均为「代码写了但从未生效」的**静默失效**型（被 `except: pass` 或调用方降级成日志）：

| 位置 | 缺陷 | 真实后果 |
|---|---|---|
| `auto_dm/accounts.py:287` | `io.open` 但**未导入 io** → NameError | 用户"停止 BCC"标记**永不落盘** → 重启后自动拉起，违背用户显式意图 |
| `auto_dm/accounts.py:1076` | `_probe(env_path, timeout)` **无 name 形参** → NameError | uid 缓存**永不更新** → 调度器"零打网"设计目标完全未达成 |
| `login_api.py:206` | `dyGenerateInitData` **无 env_path** → NameError | 该函数 **100% 失败** → 缺签名四件套的账号登录链必挂 |

同批其它修复：
- `login_api.py` `generateSecretPhoneNum/Code` 全仓不存在 → **fail-closed**（显式 NotImplementedError，不臆造实现）
- `login_api.py` 硬编码 `x-tt-passport-csrf-token`/`trace-id` → 改从 `auth.cookie` 动态取
- `builder/header.py:with_csrf` → CSRF 失败不再把 `None` 写进请求头 + 补 `return self`
- `database.py:exec_modify` → `lastrowid or rowcount` 按 SQL 首关键字分流（SQLite 的 lastrowid 仅对 INSERT 有意义）
- `api/accounts.py` proxy-test → 消除进程级环境变量竞态（**跨账号代理串味**）+ 线程池避免阻塞事件循环
- `vbrowser.py:probe_egress_ip_direct` → 增 `mode`/`node` 显式形参
- 前端 `UnifiedConfigSection` **init 死锁** → 切标签不再把 A 的配置写进 B（回归测试 8/8，含旧逻辑缺陷对照）
- 前端 `UnifiedConfigSection` `setQueryData` scope 闭包 → 随 mutation 参数传递
- 前端 `app-store.ts` localStorage 白名单 → 防脏值致界面卡死
- `dy_apis/douyin_recv_msg.py:on_message` 无异常保护 → try/except + 安全 URL 取值（**实测 6 类畸形输入全安全降级**）
- **loguru 跨行双参数又修 20 处 / 12 文件**（上轮脚本只处理单行，漏掉的）

## 3. 处理中（HIGH 197 条）

**进度（v0.43.50 时点）**：HIGH 已定性 **110 条 / 235 条高危**；security 类 25 条**全部定性完毕**（真缺陷 3 / 已修 7 / 误报 13 / 待 agent 2）。

| 状态 | 条数 | 说明 |
|---|---:|---|
| 真缺陷已修 | 3 | `mstoken.py` 缓存跨账号串用、`player-kernel.ts` 原型污染、`scripts/track_upstream.py` 全局关 TLS |
| 前轮已修 | 7 | TLS 族 5 条 + `api/mcp.py` 2 条 + `api/accounts.py` 1 条 |
| 误报 | 13 | `browser_daemon.py` **11 条**（9 条同行 OWASP 模板刷屏 + 2 条把浏览器 fetch 误判 SQL 注入）、`frontend/package.json`（实测官方 npm 有 1.x 线且镜像 integrity 与官方**完全一致**）|
| 待 agent | 2 | `api/notify.py`、`crawl-page.tsx` |

### 3.6 v0.43.54~0.43.56 已处置（loguru 收敛 + 前端/数据层）

**① loguru「错误码当格式模板」（24 文件）**

loguru 签名是 `logger.<level>(message, *args)`：**仅当 message 含 `{}` 时才用
`*args`，否则 `*args` 被静默丢弃**。项目大量写成
`logger.warning("BCC-003", f"[open-browser] ...")` → 错误码作为 message（无占位符）
→ 后面的 f-string **整段丢弃**，日志只剩裸错误码，排障信息全丢。

修复：新增幂等脚本 `scripts/_fix_loguru_code_prefix.py` 收敛为单串
`logger.<level>(f"[CODE] 正文")`。**24 文件修复，二次运行 0 处**。

> 实测输出：`[BCC-003] [open-browser] 账号 acctA 发现并清理 3 个持有 profile 的孤儿浏览器进程（端口 12345 已死但锁未释放）`
> —— 错误码与正文同时保留。

**② 5 个文件缺 `import os`**

`scripts/probe_img_*.py` ×4 + 脚本扫出的 `_live_send_test.py` 在模块级用
`os.environ` 但从未 `import os` → 直接运行即 `NameError`。
新增 `scripts/_fix_missing_import_os.py` 批量补齐。

**③ 前端 4 处逻辑错**

| 位置 | 缺陷 |
|---|---|
| `reply-kb.tsx` | `disabled={!q && !a}` 用 `&&` → **两个都空才禁用**，填一半就能提交 |
| `message-shared.tsx` | `EMOJI_MAP[m[1]]` 未校验自有键 → `[constructor]` 命中原型链函数并渲染 |
| `live-shared.tsx` | 取 `r.start_ts` —— 后端 `_records_from_adm` **无该字段** → 时间列恒空 |
| `dy_apis/client_search.py` | 裸 `json.loads` + `res_json["data"]` 无守卫 → 限流空响应即抛（同族已用 `safe_json`） |

**④ `delay_range` 用默认值当哨兵**

`if (not delay_range or delay_range == [40, 65]) and self.delay:` ——
`[40,65]` **恰是字段类默认值** → 显式传 `[40,65]` 与"未提供"无法区分。
修复：字段默认改 `None` 作哨兵。

> **真实 `TaskConfig` 解析 4/4 正确**；旧实现在"显式 [40,65] + delay=10,20"时会**覆盖成 [10,20]**（缺陷复现）。
> 回归保护：只传 `delay` 时仍解析（`test_delay_alias_still_works`）。

**⑤ MCP 非对象 JSON 崩溃**

`json.loads("[1,2]")` 返回 list → `req.get("id")` 抛 AttributeError，
且异常在 try 之外 → **服务端崩溃/断连**。修复：按 JSON-RPC 2.0 规范回 `-32600`。

**误报登记（勿改）**：`message-viewer.tsx`「effect 早退前挂载」→ 注释明写
「必须放在 early return 之前，否则 hooks 数量不一致导致 React 崩溃」；
`LeadsSection.tsx`「PageProps 无 push」→ `PageProps` 确实有 push（client.ts:1661）。

### 3.5 v0.43.51~0.43.53 已处置（服务层/API/前端 agent 批次）

**① `app_config._coerce` 的 select 校验恒判非法（长期基线失败的真根因）**

```python
opts = meta.get("options") or []
if opts and v not in opts: return None     # ← v 是字符串，opts 是 dict 列表
```

`options` 是 **dict 列表**（`[{"value":"observe","label":"..."}]`），`v` 是裸值字符串 →
`v not in opts` **恒为 False** → **所有 select 字段的值被判非法并丢弃**。

> **这是 `test_app_config` 长期失败的真实根因**（此前被当作"既有基线失败"忽略）。
> 修复后 **15 tests 由 FAILED(1) → OK**。影响面：select 类配置（如凭证更新方式）
> **保存后读不回来**，一直显示 schema 默认值。

**② `/prokb/import/confirm` 非原子清空（数据丢失）**

`clear_items()` 在 `bulk_add()` **之前**无条件执行 → 写入抛错即"旧库已清空 + 新库半截"，最坏全库归零。修复：**快照 → 写入 → 失败回滚**。

**③ 通知 JSON 手搓转义（消息发不出）**

钉钉 `msgParam`、飞书 `content` 用 `%` 拼接手搓 JSON，仅转义 `"` 和 `\n`。

> **实测：旧实现 4/8 通过**（含反斜杠 → `Invalid \escape`；含 `\r\n\t` → `Invalid control character`）；**新实现 8/8 且原文未被改写**。

**④ `member_ctx` 三路径不同源（账号目录/数据空间分叉）**

`accounts_root()` 用 `member_space_root()` 做守卫却**二次解析** member_id；
`db_path()` 缺会话文件回退。

> **实测：新实现 4/4 场景三路径必然同源；旧实现在 `current_member_id()` 两次调用间变化时确实分叉**（space=M2 但 accounts=M1）。

**⑤ `app_config._save` 静默吞异常（假成功）**

裸 `except: pass`，注释称"消费方仍能读到本次值"——**该注释是错的**（`get()` 每次从 DB 重新加载，无内存副本）。修复：记 `[CFG-011]` 日志 + 返回 bool + API 层转 HTTP 500。

> **实测**：注入 `OSError` 时返回 `False` 且记日志（原实现无返回值、无日志）。

**⑥ 其他**：`api/ai.py` scheduler 参数被绑成 query（body 提交被忽略，`stop_scheduler` 不可达）→ 新增 `SchedulerBody`。

### 3.4 v0.43.49~0.43.50 已处置

- **`utils/mstoken.py` 缓存跨账号串用（真缺陷）** —— `_cache` 是**单条**模块级 dict，
  不含 ttwid 键 → 多账号下 A 的 msToken 被 B 复用（msToken 与 ttwid 配套，串用致签名失败/风控）。
  修复：改 `{ttwid: {...}}` + 容量上限。**验证 5/5**（含旧实现缺陷对照：旧实现确实把 A 的 token 串给 B）。
- **`player-kernel.ts` 原型污染（真缺陷）** —— `registerKernel` 未校验 name，
  传 `__proto__` 会改写 Object 原型。**实测：旧实现 `REGISTRY["__proto__"]` 变成函数**。
  修复：拒绝危险键 + `defineProperty` 写入。（该函数**全仓无调用方**，属防御性加固）
- **`scripts/track_upstream.py` 全局关 TLS（真缺陷）** —— `CERT_NONE` + 携带
  `Authorization: Bearer <token>`。修复：默认开启校验 + `DY_UPSTREAM_INSECURE=1` 显式降级。
  **实测：开启校验下真实请求 GitHub API 返回 200**（不破坏功能）。

> **本轮 TLS 收敛闭环**：全仓 `verify=False` 与 `CERT_NONE` **已清零**（剩余命中均为注释/脚本自身说明）。

### 3.3 v0.43.48 已处置（commit `abe0fa7`）—— 私信链路 + 捕获链路

**① 「陌生人首发」配额泄漏（发送闸门被自身拒绝路径绕空）**

`submit`(L849) / `submit_by_uid`(L927) 把 `note_stranger_sent()`（预占额度）
放在 `QUEUE_MAX` 容量检查**之前**，队列满时直接 return **不归还**额度
→ 每次被拒白吃一个额度（2/分钟、30/天），批量场景下额度被**拒绝路径**快速耗空，
之后真实可发的目标被 `can_stranger_first` 误拒。

修复：容量检查提到预占之前。

> **验证（`test_stranger_quota_leak.py` 新旧顺序对照）**：
> 旧实现 10 次被拒即**耗空额度**（used=2 达上限）；新实现 **used=0**。
> 另 3 项回归保护通过（正常入池仍预占 / 额度上限仍生效 / 失败归还仍有效）——**闸门未被改废**。

**② 昵称缓存僵尸值（永不失效）**

`capture_userinfo_via_browser` 的命中判据只有 `(now-ts) < ttl`，而**每次调用都会
走到该行并复用**（时间窗不断后滚）⇒ 只要 10 分钟内有任意调用，缓存**永不失效**。
BCC 重启/切账号（context 重建）后仍返回归属失效的旧昵称。

修复：引入 **context 代次**（`_launch` 成功 +1），代次不符即作废重采；
`_cache_unpack` 兼容旧二元组格式。

**③ 弹幕帧静默丢弃**

`live_hook.on_message` 整帧解码（ParseFromString/gzip/ack）共用单个 except，
任一步异常即**整帧弹幕全丢**，仅留一行 warning（无计数、无重试）。

修复：增加连续失败计数，连续 5 次触发 `rescan_and_rebuild()` 自愈（`[LIVE-009]`）。

**④ 其他**

| 位置 | 缺陷 | 修复 |
|---|---|---|
| `_launch` | 重复 `add_init_script` → 两 hook 互相覆盖（WP 通道静默失效） | 注入前 `clear_init_scripts()` |
| `/send_image` | `image_b64` 无长度上限（docstring 只写 ≤20MB） | Pydantic `max_length` + 解码后字节数复核 |
| `/conversation` | 裸透传 `msg_type`（"7"/"27"）→ 前端渲染空白卡片 | 走 `_front_type` 归一化（10/10 用例验证） |
| `core/sender.py` | loguru 双参（**跨行写法**，前轮脚本漏掉） | 改单串 f-string |

### 3.2 v0.43.47 已处置（commit `7d3f2ff`）—— 租约竞态 + schema 重复键

**① 租约跨线程竞态（本轮最有价值，实测硬证据）**

`_lease` / `_scan_exclusive` 是模块级 dict，读写分布在 **FastAPI 主 loop** 与
**`run_keepalive` 子线程**两条线程上，**全程无锁**；`_lease_acquire` 是
「读 → 判空 → 写」三步非原子 → 并发可同时通过判空 → **双写覆盖**，
后写者 lease_id 生效，先写者无法 release，租约要等 TTL（最高 600s）才被回收，
**期间全部业务被 403/busy 挡回**。

修复：引入 `threading.RLock`（非 asyncio.Lock —— keepalive 在子线程且
`_lease_acquire` 内部可重入），acquire/renew/release/`_scan_exclusive` 全部进临界区；
另修 `_is_busy()` + 下标读的 TOCTOU。

> **验证（`daemon/_verify_lease_lock.py`，30 轮 × 32 线程对照实验）**：
> 旧实现 **30/30 轮复现**「多人同时持有租约」（单轮最多 **7 个线程**）；
> 新实现 **30/30 轮零竞争**。

**② `app_config_schema.py` 字典重复键静默覆盖（实测 10 个）**

`SECTIONS["automation"]["fields"]` 内 10 个键被重复定义，Python 字面量
**last-wins 且无告警** → 前一份（`scan_interval` 5~86400、`max_actions` 1~500）被静默丢弃。

修复：删除被覆盖的前一份，保留与 `automation_engine.py` clamp 一致的权威组；
新增防回归测试 `test_no_dup_dict_keys.py`（AST 扫描，重复键即失败 + 校验权威值未变）。

> 实测：重复键清零；`scan_interval_seconds=30/10/300`、`max_actions_per_run=5/1/50`
> 与 engine 逐项一致 —— **删重复键未改变生效值**。

**③ 其他 3 条静默失真**

| 位置 | 缺陷 | 后果 |
|---|---|---|
| `/show` 端点 | `except Exception: pass` 丢弃整个 body | `visible=false` 被忽略，「切回无头」失效且**无日志** |
| `refresh_cookie_to_env` | 写 .env 失败仅 warning 却返回 `ok=True` | 调用方按成功记账，实际 .env 仍是旧凭证；漂移门禁基线被误推进 |
| `/linkmic_run` | 裸 `c._lock` + `_ensure_alive()` | **绕过租约仲裁与 `_is_busy()` 快速失败**，scan_login 独占期可抢 profile |
| `_nav_page` | 用完只 `goto("about:blank")` 不 `close()` | 每次 resolve_url 留一个空 tab，占 renderer 句柄，长期累积 |

### 3.1 已派 agent 甄别（HIGH）

- `browser_daemon.py`（11）、`app_config_schema.py`（10）、`api/logs.py`（3）
- `dy_apis/client_comments|relations|douyin_recv_msg|login_api`（14）
- `database.py`/`ws_link.py`/`api/accounts.py`/`api/mcp.py`/`auto_dm/accounts.py`/`builder/header.py`（15）
- 前端 `player-media-stage`/`UnifiedConfigSection`/`app-store`（9）
- 私信链路 `recv_daemon`/`dm_dispatch`/`core/sender`/`api/messages`（11）— 进行中
- 捕获链路 `conversation_capture`/`browser_daemon L900-2829`/`core/live_hook`（进行中）

**已自行处理的安全类 HIGH**：
- `api/mcp.py` 鉴权缺口（已修）
- `verify=False` 系列 5 条（已由 TLS 策略统一收敛）
- `browser_daemon.py:2579` 的 9 条「未实现 XSS/CSRF/泄密…」——**同一行刷屏的泛化模板**，
  非逐项真实缺陷，判为低质生成不作逐条处理

## 4. 方法论要点

1. **机器报告必须逐条实测**：CRITICAL 误报 18/38 ≈ 47%；
   若照单全收，`data_util`/`live_hook` 等**本来正确的代码会被改坏**。
2. **回归自查**：`pro_kb.py` logger 是我 v0.43.42 修补时引入的，
   说明自动化审查能抓出人工修复的副作用。
3. **收敛优于逐点**：64 处 `verify=False` 不是逐个改，而是引入统一策略模块，
   一次性收敛 + 保留显式开关。
4. **修复必须实测**：
   - TLS：真跑抖音域名验证 `verify=True` 不会破坏生产
   - 断言类：跑测试看是否真通过
   - 装饰器类：AST 确认装饰器已挂上
