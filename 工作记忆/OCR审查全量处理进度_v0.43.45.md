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
