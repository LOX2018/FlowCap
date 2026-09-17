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

### 3.7 v0.43.57~0.43.60 已处置（并发/静默覆盖/校验有效性）

**① `load_env` 跨账号凭证污染（实测复现）**

`load_env()` 无锁执行 `load_dotenv(env_path, override=True)` —— 这会**写进程级
`os.environ`**，随后从 `os.getenv` 读 cookie 构造 auth。多账号并发时：
A 写 environ → B 写 environ（覆盖）→ A 读 → **A 拿到 B 的 cookie**。

> **实测**：旧模型下 **A 账号读到 `COOKIES_B`**（污染成立）；加锁后 A→COOKIES_A、B→COOKIES_B。
> 修复：模块级 `RLock` 串行化临界区；**注明全局单例仍是残余面**（彻底治理需架构改动）。

**② `wp_recv` 非 dict 事件中断整循环**

`ev.get("kind")` 在 per-event `try` **之外** → BCC 返回畸形 payload 时
AttributeError 打断**整个入库循环**，其后正常事件全丢。修复：纳入守卫 + 非 dict 跳过。

**③ `browser_args` 重复定义（静默死代码）**

`utils/fingerprint.py` **两次定义** `browser_args`（语义还不同：前者不含
`--window-size`、后者含）→ Python 以后者为准，前者**永不执行**。

> 实测删掉后 `browser_args()` 仍返回 7 项含 `--window-size`（**行为不变**）；
> **全库 AST 扫描确认无其它顶层函数重复定义**。

**④ 迁移标志提前置位（永久跳过迁移）**

`_migrate_v1()` 在迁移体执行**之前**就 `set_kv(_MIGRATED_KEY, True)` →
中途失败则下次启动直接 return，**旧配置永久迁不进来**。修复：成功后置位。

**⑤ 校验脚本自身的有效性（门禁形同虚设）**

| 位置 | 缺陷 |
|---|---|
| `verify_isolation.py` | 只打印 PASS/FAIL **无退出码** → CI 里 FAIL>0 也判成功 |
| `verify_live_restart_hotswap.py` | `... or "enabled" in lc` **恒真式** → 该 check 永不失败 |

> **实测**：`verify_isolation` 注入 10 项失败后**退出码 = 1**（原恒为 0）。

**⑥ `kill_all` 的 PID 回收窗口（TOCTOU）**

锁内 `clear()` 后**锁外**逐个 `_pid_running` 判定再 `taskkill /F /T` ——
期间 PID 若被回收给新进程，会**连带杀掉无关进程树**。

> **实测**：旧实现**确实会对已 unregister 的 pid 下手**（缺陷复现）；
> 新实现跳过已注销者、且新登记的 pid 未被误杀。4/4 通过。

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
5. **校验脚本本身也要校验**：多个"验证脚本"其实**恒通过/跑不起来**
   （见 3.4），这类缺陷比业务 bug 更危险——它给出**虚假的安全感**。
6. **"原文仍在"不等于"未修复"**：用 `existing_code` 做批量预筛只能判断
   "该文本是否还在文件里"，不能替代语义核实（很多条目是同段多报或已被
   等价改写）。本会话据此把 197 条 HIGH 预筛为 122/75，仍需逐条判。
7. **自己的改动也要复核**：本会话 `auth_helper.py` 的改动经复核会引入回归
   （`_dotenv_values` 不写 `os.environ`，下游依赖 environ）→ **完整回退**。
   教训：改"进程级副作用"时，必须确认**下游是否依赖该副作用**。

---

## 5. 第二轮处置（v0.43.58 ~ v0.43.64，2026-09-17 晚）

### 5.1 校验/门禁类脚本缺陷（v0.43.59、v0.43.61）

| 文件 | 缺陷 | 验证 |
|---|---|---|
| `scripts/verify_isolation.py` | 只打印 PASS/FAIL，**无退出码** → CI 里 FAIL>0 也判成功 | 注入 10 项失败后退出码 **1**（原恒 0） |
| `scripts/verify_live_restart_hotswap.py` | `... or "enabled" in lc` 是**恒真式** | 改为精确断言 `'"enabled": bool('` |
| `scripts/verify_nickname_link.py` | JS 实体取自 `browser_daemon.py`（只 import 常量）→ **IndexError，脚本从未跑起来** | 改取 `browser_daemon_js.py`，现 **PASS=17 FAIL=0** |
| 同上 | 两条断言查错文件 / 匹配**已被修正的旧写法** → 假失败 | 改为校验当前正确实现（含"空 sender→me"反断言） |
| `backend/scripts/verify_capture_parse.py` | `assert` 做**分支环境守卫** → `python -O` 下被剥离 | 改显式 `if + raise SystemExit` |
| `backend/scripts/verify_api_split.py` | 同款 assert 守卫（**同类第 2 处**） | 同上 |
| `scripts/check_version_sync.py` | 五处全部提取不到 → `sorted(vals)[0]` **IndexError** | 空集时返回 **1** + 缺失文件列表 |
| `scripts/wiki_query.py` | `--read` 无参 → 未捕获 IndexError | 打印用法并返回 2 |
| `scripts/verify_uid_sink.py` | 硬编码 `C:\Users\LOX\...` → 换机 ImportError | 改用 `__file__` 相对定位 |
| `scripts/gen_api_client.sh` | `curl -s` 对 404/500 仍返回 0 → 垃圾写进 schema | 加 `-fsS --max-time` + JSON 内容复核 |
| `scripts/chk_refresh.sh` | `cd` 失败静默；`$f` 为空当参数；**路径指向主分支环境** | 显式判空 + `DY_APP_ROOT` 显式指定 |

### 5.2 `.gitignore` 吞掉持久验证资产（v0.43.62）—— **本轮最高价值**

`_*.py` 规则本意只忽略「临时脚本」，但静默吞掉三个**被 ADR 明文引用**的门禁脚本：

```
backend/daemon/_verify_send_gate_cache.py
backend/daemon/_verify_conv_identity.py
backend/daemon/_verify_lease_lock.py
```

**实测**：`git log --all -- <path>` 三者均为 **0 次提交** → 从未入库，只存在于
开发机工作区；`docs/adr_conv_identity_single_source.md:72/147` 直接引用它们
→ **ADR 引用断链**，换机/克隆即永久失去验证能力。

修复：加 `!**/_verify_*.py` 等例外；**关键回归测试**——
`git rm --cached <脚本>` 后 `git check-ignore` 仍**不忽略**（证明例外真生效），
临时脚本（`_triage_all.py` 等）**仍被忽略**（设计意图保持）。

> 附带排除报告原例误报：`logs/` 否定规则**实测有效**（`git check-ignore -v` 无输出），
> `_build_version.py` 入库靠的是"已跟踪文件不受 gitignore 影响"，非例外规则。

### 5.3 静默失效成一类（v0.43.64）

**① `AutoDM.shutdown` 定义两次** —— 后者（仅 `if is_running: stop()`）覆盖前者
（含 `_finish_history_task("stopped")`）→ 非运行态历史任务收尾**永不执行**。
AST 断言：修复后定义数 **1**，保留版含 `_finish_history_task`。

**② `api/logs.py` 路径穿越** —— 实测 `logs/"../../secret.txt"` 逃逸、
`logs/"C:\Windows\win.ini"` **整体替换**为绝对路径 → 可读任意文件。
修复：纯文件名校验 + `resolve()` 后父目录必须等于 `LOG_DIR`（双重）。

**③ `/start` check-then-act 竞态 + 丢弃 Task** —— `STARTING` 在被调度协程内才赋值
→ 并发两次都见 IDLE；`create_task` 返回值被丢 → 异常无人取回，路由仍报
`ok=True`（**假成功**）。修复：`asyncio.Lock` 串行化 + 保留任务引用 +
`add_done_callback` 记录异常。

**④ `dy_apis` 一类**：`safe_json` 限流降级为 `{}` 后下游**无守卫下标**
（`notice_list_v2`/`followers`/`data`）→ KeyError；多处 `while True` **无轮数上限**。

**⑤ `get_live_info` 返回形状** —— 失败返**三元组** `(None,None,None)`，而调用方
全按「dict 或 None」判（元组为真值 → 过 falsy 检查后抛 TypeError）→ 统一 `None`；
顺带删除残留调试 `print(res)`。

**⑥ 其他**：`douyin_recv_msg` 的 `A or B and C` 优先级（ConnectionRefusedError
无视 `auto_reconnect`）+ `type()==` 漏子类；`mcp/tools.py` 把 `(name, env_path)`
元组塞进 `name` 并泄露 env_path；`kb_maintain` 的 loguru 双参数吞**全部正文**；
`strdata_pure.build_fingerprint()` 不带 account → 每账号**同一份**上报指纹；
`vbrowser.py` **硬编码 ipapi.is 密钥** → 改 `DY_IPAPI_KEY`。

### 5.4 复核后**回退**的改动（重要）

`auth_helper.py` 曾把 `load_dotenv(env_path, override=True)` 改为
`_dotenv_values(env_path)`。复核发现：**下游 `common_util.load_env()` 依赖
environ 已被写入**（原注释即言明"确保 …能读到 DY_COOKIES"），
`_dotenv_values` 只返回 dict、不写 environ → **我引入回归** → 已 `git checkout`
完整回退，未进入本批提交。真正的修法应是在 `load_env` 的 `_env_lock` 临界区内
把该 dict 显式写入 environ（或维持现状）。

### 5.5 本批验证记录（v0.43.64）

```
backend 全量语法    175 个 .py，失败 0
单元测试            8/8 OK（app_config/send_gate/delay_sentinel/
                            killall/mstoken/dup_keys/config_isolation/model_hub_v2）
门禁脚本            3/3 ALL PASS（lease_lock 结论：缺陷真实且修复有效）
前端                npx tsc --noEmit 退出码 0
版本门禁            五处齐平 0.43.64
硬编码路径复扫       0 处
AST 精确断言        AutoDM.shutdown 定义数 == 1（含 _finish_history_task）
```

### 5.6 剩余

- HIGH 约 **90 条**未处理（`start_dev.ps1` 的 `Get-Process` 通配符/`CommandLine`
  属性、`src-tauri/lib.rs` 句柄竞态与去重、前端 ~20 条、`notify/gateway` open 模式、
  `member_ctx.destroy_session`、`member_store` 空表回写、`uid_probe.shutdown` 空转、
  `conv_identity` 回退 uid、`pro_kb`/`reply_kb`、`api/tasks` 字段名、
  `dy_live/server` 无退避重连、`mcp/server` 401 未 drain body 等）
- MEDIUM 590 / LOW 487 **完全未动**

### 5.7 部署校验（v0.43.64 → 0.43.65）—— **测试抓出我自己的不完整修复**

#### A. 打包链（design/better-douyin）

要点：**`build_sidecar.py` 用 `sys.executable`**，必须用 **Python 3.14**
（`C:\Users\LOX\AppData\Local\Programs\Python\Python314\python.exe`，装 PyInstaller 6.22）
显式调用；直接用 shell 的 `python`（Hermes venv 3.11）会 `No module named PyInstaller`。

```bash
PY314="/c/Users/LOX/AppData/Local/Programs/Python/Python314/python.exe"
"$PY314" scripts/build_sidecar.py --onedir      # 三份 sidecar + dedupe（共享 _internal）
export PATH="/c/.../stable-x86_64-pc-windows-msvc/bin:$PATH"
npx tauri build --no-bundle                      # 前端 dist（注入 __APP_VERSION__）+ 桌面端
python scripts/deploy.py                         # 校验 + 部署到 C:\temp\dyautodm_design
```

**`deploy.py` 的三道门禁实测有效**（本轮被它拦下过一次）：
① 主程序 exe **资源版本** == tauri.conf 版本（改版本号不重建 → 直接拒绝）；
② sidecar `_build_version.py` == 期望版本；③ 平铺 exe 与子目录 exe md5 一致
（`--onedir` 只写子目录，残留平铺旧 exe 会被判"产物自相矛盾"）。

> 坑：`bash -c "cmd 2>&1 | tail -n"` 之后 `$?` 是 **tail** 的退出码 → 会误报成功。
> 必须 `cmd > log 2>&1; s=$?` 逐步取码（本轮据此才发现 PyInstaller 那次是**真失败**）。

#### B. 部署实例实测（v0.43.64 产物，`frozen:true`）

| 项 | 结果 |
|---|---|
| `/api/version` | `{"backend":"0.43.64","pid":4812,"frozen":true}` ✅ |
| `/api/ready` | `{"ok":true,"daemons_ready":true,"accounts":1}` ✅ |
| 版本门禁（同版本 0.43.64） | 非 409 ✅ |
| 版本门禁（旧版本 0.43.1） | **409** + `version_mismatch` 结构体 ✅ |
| 路径穿越守卫（进程内直调，6 用例） | **6/6 PASS**（`../../`、反斜杠、绝对路径、含目录段全部拒绝，无内容泄露；正常名可读） |
| `build_fingerprint(account)` | 不同账号指纹**不同** ✅（原实现恒相同） |
| `kb_maintain._log` | 正文完整保留 ✅（原只剩裸码） |
| `vbrowser` IPAPI 端点 | 由 `DY_IPAPI_KEY` 决定、源码密钥已移除 ✅ |
| `client_notice` 键守卫 | `{}`/`None` → `[]` 不抛；对照旧写法同输入抛 KeyError ✅ |
| `douyin_recv_msg` 重连条件 | `auto_reconnect and isinstance(...)`；无 `type(x)==Y` ✅ |

#### C. 🔴 关键教训：我又一次"修了但没修透"

`api/engine.py` 的 `/start` 并发竞态，我第一版只加了 `asyncio.Lock`。
**用真实函数 + 复刻真实时序的 stub 引擎做并发测试，当场测出 `starts=2`（未修复）**：

```
请求A 进锁 → 见 IDLE → create_task(start) → 释放锁（状态仍是 IDLE，因为
                                                   STARTING 在协程内才赋值）
请求B 进锁 → 仍是 IDLE → 又 create_task  ← 重复拉起
```

即**锁只串行化了"检查"，没有把"状态已翻转"纳入临界区**。修正：在锁内
`create_task` 后**让出控制权**（`await asyncio.sleep(0)` 轮询）直到状态翻离
IDLE/STOPPED 或任务结束，再释放锁 → 第二个请求命中 `already`。

复测：并发 2 次 `starts=1 already=1`；并发 3 次 `starts=1 already=2`。

> **方法论**：并发类修复必须用**贴近真实时序**的桩做对照实验
> （状态在被调度的协程内变更、有 `await` 让出点）。仅"看起来加了锁"不算修好。
> 这条同时推翻了我 v0.43.64 提交里"已修复"的结论 —— 已在本轮订正并重打包。

--- 

## 6. 第三轮处置（v0.43.66 ~ v0.43.69）

### 6.1 静默漏配 / 静默覆盖 / 被忽略入参 成一类（v0.43.66）

| 文件 | 缺陷 | 实测验证 |
|---|---|---|
| `services/reply_kb.py` | 阈值用**衰减后**分数比较（衰减恒 ≤1 → 阈值被悄悄抬高）→ 旧的但高相关条目**永不命中**；`best_raw` 只在循环内赋值（无用） | raw=1.000 / decay(50d)=0.368 < 0.85 → 旧判据不命中；新判据(raw≥0.85)命中；`find_match` 实调返回正确 |
| `services/member_store.py` | `_load_registry()` 读失败返回**空表**，写路径拿到后**写回** → 一次瞬时读错误即**清空全部会员** | 半截 JSON + `register_member` → 返回 `ok:False` 且**文件字节前后一致** |
| `services/member_ctx.py` | `destroy_session(token)` **无条件**删落盘会话 → 陈旧 token 登出会清掉当前有效会话 | — |
| `services/pro_kb.py` | `_embed` 内部 `texts[:20]` 截断，而调用方传全量 → 向量数≠入参数 → **静默 (None,0.0)，查重整体失效** | — |
| `database.py` | ① 会员一致性守卫自身异常被裸 `except: pass` 吞（守卫失效不可观测）② 建连后**立即**发布全局 `_conn`，迁移失败留下半初始化连接 | — |
| `api/tasks.py` | ① `cfg.dm_pool` 已压成 `list[str]`，写回**抹掉 enabled 标记** ② Excel 导出读 `capture_ts`/`send_ts`，而字段名是 `captured_at`/`sent_at` → 两列**恒空** | `_fmt_ts` 实测 |
| `notify/gateway.py` | `check_intent` 未处理 open 模式 → **open 模式下每条消息仍被拒** | — |
| `notify/cmd_parser.py` | `params` 来自 **LLM 输出**，`or {}` 不保证 dict → 非 dict 会 AttributeError | — |
| `api/overview.py` | 在 `async def` 里**串行**调 2 次同步 `connect_ex`（各 0.3s）→ 阻塞事件循环 | — |
| `api/platform.py` | `r.json()` 无条件 → 限流空体抛错并**中断整批** | — |
| `api/linkmic.py` | 早退守卫使「抓 anchor_id」补全逻辑**永不可达**（注释明写其意图） | — |
| `core/auto_dm.py` | `rescan_and_rebuild(account_name)` 内两条 TODO 占位 → **入参被完全忽略** | — |
| `utils/code_logger.py` | 重构消息后把 loguru 的 `exception=True` 当模板值传 → **异常堆栈丢失** | — |

### 6.2 前端（v0.43.67）

| 文件 | 缺陷 | 验证 |
|---|---|---|
| `src/api/client.ts` | `{headers, ...init}` 展开顺序 → `init.headers` **整体覆盖**默认头（含 X-Member-Token）→ 静默 401 | vite 构建通过 |
| `src/api/platform.ts` | 自带封装缺 401 处理（与 client.ts 不一致）+ 缺 X-App-Version | 同上 |
| `MemberGate.tsx` | ① 无 try/catch → 校验抛错则**永久卡在"正在检查登录态…"** ② 依赖 `[onLogin]` 不稳定（App 内联箭头 + 3s 轮询）→ effect 反复执行 | 同上 |
| `AiRuntimeSection.tsx` | 用 `enabled` 决定 start/stop，而按钮文案由 `running` 驱动 → **"启动"按钮执行停止** | 同上 |
| `pro-kb.tsx` | `applyMut` 静默吞 `ok:false`；`restoreMut` **无条件**报"已恢复" | 同上 |
| `scroll-area.tsx` | 垂直 Scrollbar 缺 `h-full` → 轨道/Thumb 布局失效 | 同上 |
| `player-playback-bar.tsx` | 只有 `onPointerUp` 复位 drag → 指针取消时**永久卡住**，进度条冻结 | 同上 |

### 6.3 脚本与开发工具（v0.43.68 / 0.43.69）

| 文件 | 缺陷 | 验证 |
|---|---|---|
| `scripts/clear_convs.py` | 备份失败仅警告，仍继续 `DELETE`+`commit` → **无备份的不可逆删除** | monkeypatch `copy2` 抛错 → 返回码 **2** 且未删任何数据 |
| `start_dev.ps1` ×3 | `Get-Process` 对象**无 `CommandLine` 属性**；`-Name` **不支持通配符** → 进程清理失效/脚本中途报错 | PowerShell `[Parser]::ParseFile` → **PARSE_OK** |
| `api/logs.py::_read_tail` | 全量解析整个 20MB 日志再切片 | 50000 行 → 返回 500 行、**0.013s**、首末行正确 |

### 6.4 本轮判为**误报/不改**的（有据）

- `tokens.css` 磨砂："frost 反而更弱" —— 基础 `.glass-premium` 本身有 `blur(48px)`，
  `html:not([attr])` 那条只是"未开启时降到 14px"，注释与实现一致。
- `origin_image_resolver.py:348` `if size <= threshold or not force_hosted` ——
  该条件**逻辑正确**（默认走本地；显式 `force_hosted` 时大图才上图床）。
- `utils/dy_util.py:168`：函数返回的是**元组** `(csrf_token_1, csrf_token_2)`，
  `[0]` 取到 `None`（非崩溃），且调用方 `builder/header.py:44-48` 已有 `if tok` 守卫。
- `dy_apis/douyin_recv_msg.py` 的 14 处 `print` —— 该模块**无任何生产调用方**
  （遗留独立 WS 客户端），非链路缺陷。
- `api/crawl.py::_map_user` 字段名 —— 属**推测性**断言（"请核对真实字段名"），
  代码读的是抖音标准字段名，按「以事实为主」**不改**（需真实凭证请求才能证实）。
- `utils/bd_ticket.py::verify_req_sign` pub_hex 前缀假设 —— 仅 `__main__` 自测使用，
  非生产路径。
- `services/uid_probe.py::shutdown` 空转 —— 收尾清理正确（`Event.set()`），非缺陷。
- `services/conv_identity.py:80-83` —— 报告所述缺陷**已由 v0.43.39+ 的
  `uid_probe.get_uid` 自身交叉验证修复**（不返回不一致 uid）。

### 6.5 剩余

- HIGH 仍有一批 **需真机/真实凭证**才能判定的项（`api/crawl.py` 字段名、
  部分"限流行为"类断言），以及 `browser_daemon.py` 的 10 条
  （已在 5.x 逐条定性为 OWASP 模板刷屏 + SQL 误判 → **全部误报**）。
- MEDIUM 590 / LOW 487 未动（报告本身误报率高，建议按"同类收敛"而非逐条）。

---

## 7. MEDIUM 聚类处置（v0.43.70 ~ v0.43.71）

### 7.0 方法：**先按模式聚类，再对当前代码实测**

590 条 MEDIUM / 241 文件 → 用正则把 `content` 归入 14 个"缺陷模式"桶，
再**直接对当前代码做 AST 扫描**看该模式还剩多少（不信报告直接数）：

| 模式 | 报告条数 | 当前代码实测 |
|---|---:|---|
| 静默吞异常 | 55 | 283 处（**多数是有意的 best-effort 清理**，逐条改风险大收益低） |
| loguru 双参数 | 58 | **15 处真实**（→ 本轮修完） |
| 并发/竞态 | 74 | 多为"未加锁"提示，需逐个判断 |
| 安全/校验 | 43 | **6 处真实** + 6 类已缓解/架构性 |
| 可变默认参数 | 2 | **0 处**（早已清零） |

### 7.1 loguru 误用 printf 风格（v0.43.70）—— **含我自己引入的 1 处**

**实测三态**（这是关键证据，不靠印象）：

```
logger.warning("[db] ...: %s", "真实错误内容")   → 输出 '[db] ...: %s'      参数丢失 ❌
logger.warning("SEC-UID-002", "详情")            → 输出 'SEC-UID-002'        详情丢失 ❌
logger.warning("[db] ...: {}", "真实错误内容")   → 输出 '[db] ...: 真实错误内容' ✅
```

**15 处**（4 文件）：`database.py`（**我 v0.43.66 写的**）、`tasks_history.py`、
`dy_apis/client_user.py`×3（`SEC-UID-00X` 错误码+详情）、
`dy_apis/login_api.py`×10（`%s`/`%d` 与 cookie 统计）。

修后抽验输出：`[history] 拒绝执行：非法列名 'x; DROP'（白名单=['a','b']）` ✅

**新增防回归测试** `backend/test_no_loguru_printf_style.py`：
- `test_no_printf_style_loguru`：AST 全库扫描（首参为不含 `{}` 的常量且有第 2 位置参）
- `test_printf_style_args_are_dropped`：**实证 loguru 语义**（锁定认知，如果
  哪天 loguru 改了行为这条会失败，提醒重新评估）
- **反向验证**：在受控副本注入 `logger.error("CODE-1","detail")` → 退出码 1 且
  **准确点出该文件**；正向 2 tests OK。

> 注意：扫描器必须**跳过自己**（文件内含故意写错的反例演示行）。

### 7.2 安全加固 6 项（v0.43.71）

| 文件 | 缺陷 | 实测 |
|---|---|---|
| `api/logs.py` `/write` | `body.text` 原样写日志：无上限 + 含 `\n` 可**伪造日志行**（日志注入） | 加 4000 字上限 + 换行/制表收敛为空格 |
| `utils/bd_ticket.py` | `ticket`/`api` 直接插进 `k=v&k=v` 待签串 → 含 `&`/`=` 可**注入额外键值对** | `"a&b"`/`"a=b"` → **ValueError**；正常路径不变 |
| `auto_dm/origin_image_resolver.py` | 缓存文件名用未净化的 `msg_id` → 含 `/`、`..` 可写出目录 | `'../../evil'→'evil'`、`'a/b'→'ab'`、`'..'→''`（回落 sha） |
| `api/ai.py` | 临时名 `pro_kb_import_{int(time.time())}`：1 秒内同名碰撞 + 可预测 | 改 `tempfile.mkstemp`（原子+随机名） |
| `dy_apis/login_api.py` `_safe_repr` | 只按 dict **键名**遮蔽；裸 cookie 串**原样打印** | 裸串 → `<masked len=55>`；普通文本不受影响 |
| `mcp/config.py` `check_token` | 从不检查 `enabled` → 关闭 MCP 后带 token 仍可调用 | 按其 docstring 补 `enabled` 判定 |

### 7.3 MEDIUM security 里判为**已缓解/架构性**的

- **`verify=False` 系列（~6 条，`client_video`/`image_sender`/`dy_util`/
  `client_live`/`conversation_capture`/`mstoken`）→ 误报**：v0.43.45 已全库
  收敛为 `tls_verify()`，**实测默认返回 `True`**（仅 `DY_TLS_INSECURE=1` 时 False），
  非注释的 `verify=False` 残留为 **0**。报告是基于收敛前的代码。
- `api/member.py:254` **跨会员删除**（号称可删他人）→ **高估**：需
  `body.password` 且 `delete_member` 内部走 `verify_password`，无口令拿不到；
  删除**他人**需知道他人口令，非"越权"。但 `/delete` 未校验 `memberId` 归属，
  属**纵深防御**可补（未改：需先与用户确认多会员运维语义）。
- `mcp/config.py` 的 token 用 `secrets.token_urlsafe(32)`、`compare_digest` 恒定时间比较
  → 令牌本体安全；本轮只补 enabled 门禁。
- `login_capture.py:80` 存密钥**末 8 字符**"指纹"→ 约 6×10⁻¹⁵ 命中明文，
  且注释明写意图 → 可接受。
- `_safe_repr` 的**普通文本不遮蔽**属设计（只遮蔽敏感），本轮只补"裸敏感串"分支。

### 7.4 剩余（MEDIUM）

- `静默吞异常` 283 处：**不宜机械全改**（大量是有意的 best-effort 清理，
  如关闭 context、删临时文件失败）；应只在"吞掉的是关键失败"处补日志 ——
  已在 v0.43.47/49/66 按此原则逐个处理过若干处。
- `并发/竞态` 74 条：需逐个判断是否真有共享可变状态。
- `frontend` 174 条 MEDIUM：多为可维护性/样式，低优先。
- LOW 487：未动。





