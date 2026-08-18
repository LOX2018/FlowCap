# 工作记忆 2026-08-19（V2 版本 0.19.0→0.20.0）

## 改动背景
用户实测反馈 2 大类 BUG：
1. **任务容器缺失**：直播监听「开启自动私信」启动后切换页面再回来，页面无法回读任务信息、
   显示「等待启动」；任务中心点击该任务也看不到实质状态。希望把启动后的任务封装进一个
   「任务容器」，任务中心点进任务直接读该容器的进程；任务中心只有「进入任务」按钮，
   需补充暂停/启动按钮，并加「复用」按钮。
2. **词库随机抽取失效 + 发送结果无反馈**：私信词库随机抽取失效，恒用同一文案被抖音监测；
   日志显示发送成功但实质未发出（空文案被平台返回 OK）；抖音私信隔离机制导致部分发送提示
   「需互关」「发送频繁」却无结果反馈。希望在私信发送链路对每条发送都有结果反馈。

## 根因与修复

### 1. 任务容器（前后端都改）
根因（三个叠加）：
- `backend/api/live.py get_stream` 只靠 WS 是否连接判 `alive`，且不返回引擎状态；引擎在
  启动中/等待开播/暂停时前端误显示「等待启动」。
- `core/dispatch.py` 队列首次自然变空触发 `AutoDM._on_dispatch_idle` → 直接置 STOPPED，
  引擎被误停，任务实际已死。
- 历史任务只存结果快照，没有配置快照，「复用」无从谈起。

前端改动（live.tsx / tasks.tsx / client.ts / App.tsx）：
- `live.tsx`：新增 engineState/statusMsg 字段消费；用 `engineBusy`（starting/running/paused/
  stopping）替代原 `running=alive` 判定开始/暂停/继续/停止按钮态与状态文案（启动中/等待开播/
  暂停/监听中/未运行）。挂载时调 `/api/tasks/current` 回读上一次任务配置（直播间/词库/间隔/
  抖动/上限/账号）自动预填。ReviewMode 详情面板与表格 tooltip 展示失败原因。
- `tasks.tsx`：运行中任务行加「进入任务」按钮（保留暂停/继续/停止）；历史任务每行加「复用」，
  把 history.config 快照经 `goReuse` 预填到直播监听页，toast 提示核对后再点「开始自动私信」。
- `client.ts`：新增 `getCurrentTask()`、`CurrentTask/TaskConfigSnapshot/ReusePayload` 类型，
  `TaskHistoryItem.config`、PageProps 加 `goReuse/reusePayload`；`saveDmPool` 类型放宽为
  `{text,enabled}[]`（此前 enabled 派发到词库被丢弃）。

后端改动（auto_dm / tasks / live / models / tasks_history）：
- `core/auto_dm.py`：
  - 新增 `_apply_config`：把 TaskConfig 落到运行时字段并把启用态合并进 `dm_template`。
  - 新增 `_make_pick_dm_message`：从**已启用**词库 `random.choice` 随机抽取（修复恒取
    `config.dm_pool[0]`）。
  - 新增 `_snapshot_config` / `snapshot()`：任务容器统一快照（engine_state/status_msg/config/
    live/counts/records），直播页与任务中心共用同一数据源。
  - `start()`：写历史任务时携带 config 快照；启动失败（state=IDLE）历史任务标「stopped」。
  - 修复 `_on_dispatch_idle`：监听 WS 还活着时队列短暂空属于正常间隙，不再误置 STOPPED。
- `api/tasks.py`：新增 `GET /api/tasks/current` 返回 `adm.snapshot()`。
- `api/live.py` + `models/live.py`：`/api/live/stream` 增加 `engineState/statusMsg/dmRunning/
  dmPaused` 字段。
- `tasks_history.py`：`start_task` 增加 `config` 快照参数并落盘（复用数据源）。

### 2. 词库随机 + 发送结果反馈（前后端都改）
- 前端 `live.tsx` 启动/保存词库只提交**已启用**项，并保留 enabled 传给后端。
- 后端 `douyin_api.send_msg`：改返回 `(bool, detail)`；**空文案本地拦截拒绝发送**（修复「日志
  显示成功但实质未发送」）；失败原因经 `_classify_send_fail` 分类：
  需互关 / 发送频繁被频控 / 隐私限制 / 风控 KICK / 用户不存在 / 静默拦截(OK 未投递) 等。
- `core/sender.py`：消费新返回值，空文案拦截；每条发送日志输出成功/失败+文案+原因。
- `core/dispatch.py`：成功/失败日志含文案与失败原因（逐条结果反馈）。
- `daemon/recv_daemon.py` `/send`：适配元组返回值，把详细原因回给私信中心手动发送。

## 验证
- 前端 `npm run build`（tsc -b + vite 449 modules）0 TS 错误。
- 后端 `py_compile` 全部改动文件通过。
- `python -m unittest discover -s tests_integration`：19 项全过（1 跳过需真实账号）。
- 任务容器/随机抽取/配置合并 用真实 Python 冒烟测试通过（禁用项不出现在抽取池）。

## 版本号 +0.01：0.19.0→0.20.0
4 文件：Cargo.toml / tauri.conf.json / package.json / frontend/package.json。

## 打包（默认跳过 NSIS/MSI，仅部署便携版）
- **打包约定**：除非用户强调需要正式版（安装包），否则一律不构建 NSIS/MSI，直接编译主
  exe + 3 个 sidecar 并部署便携版即可（Rust 主程序约 3 分钟，NSIS 400MB 压缩才需 5-10 分钟）。
- **重要环境坑**：
  - `build.ps1` 的 `Set-VersionInFile` 替换只写 `${1}+newVal`、把结尾引号 `$2` 掉了，会把
    JSON/Toml 破坏成 `"version": "0.20.0,`，且 Cargo 模式会误伤 `rust-version` 和依赖的
    `version = "2.0"`。已修复为 `'${1}'+$newVal+'${2}'`，Cargo 模式加 `(?m)^` 锚定行首
    `version`（避开 `rust-version`/依赖）。本次损坏的 4 文件已手工修复。
  - build_sidecar.ps1 在非交互环境可能掉进 Python 3.14 REPL 无限循环；已验证直接
    `python scripts/build_sidecar.py` 可正常打包 3 个 sidecar。
- sidecar 3 个：src-tauri/binaries/\*.exe 已重打（0:39-0:40，含本次后端改动）。
- 主程序：`src-tauri/target/release/dyautodm-v2.exe` v0.20.0（tsc 后 exe 已内嵌新前端 dist）。
- 部署（未做 NSIS/MSI 安装包，测试不需要）：
  - `dist\DYAutoDM_v2_0.20.0.exe` + `dist\binaries\` 3 sidecar
  - `C:\temp\dyautodm_test\DYAutoDM_v2_0.20.0.exe` + `binaries\` 3 sidecar

## 本机真实 python：C:\Users\LOX\AppData\Local\Programs\Python\Python314\python.exe
Rust/tauri 构建：`$env:PATH = "C:\Users\LOX\.cargo\bin;" + $env:PATH` 后 `npx tauri build`
（主 exe 2m53s 编译完成；NSIS 400MB 压缩约需 5-10min，测试场景可跳过，直接用主 exe + binaries）。

## 需用户实测
- ① 直播监听：启动自动私信后切走再切回，应回读直播间/词库/参数并显示真实引擎状态
  （启动中/等待开播/暂停/监听中），不再显示「等待启动」。
- ② 任务中心：运行中任务有「进入任务/暂停/继续/停止」；历史任务每行有「复用」，
  点复用跳到直播监听页自动预填配置。
- ③ 私信词库：随机抽已启用文案（禁用项不会被发）；保存词库后 enabled 状态持久化。
- ④ 发送反馈：运行日志每条私信打印 成功/失败+文案+原因；失败记录在查阅模式/表格
  tooltip 显示原因（需互关/频繁/隐私…）；空文案不发，不再「显示成功但没收到」。

---

# 会话 0.20.0→0.21.0（同日第二轮，任务中心监控/查阅模式数据源）

## 改动背景
用户实测 3 个问题：
1. 直播监听页「停止监听」是软停止（关 WS 但存量私信继续发），但任务中心把该任务当成
   已结束直接归入历史任务，没有入口去「停止存量私信」（原运行行随 ov.running=False 消失）。
2. 直播监听页「实时评论统计列表 → 进入查阅模式」未写入数据（实时无记录时空白）；
   历史任务希望双击行也能进入查阅模式。
3. 账号管理页「上次运行记录」应读取历史任务（查阅模式同源）的数据，而不是只看运行时。

## 改动详情

### 1. 任务中心保留软停止任务行 + 停止存量入口
- `backend/api/overview.py`：`running` 从 `state=="running"` 改为 `adm.is_running`
  （含 starting/stopping）；新增 `engineState/statusMsg` 字段。
- `frontend/src/api/client.ts`：Overview 增加 `engineState?/statusMsg?`。
- `frontend/src/pages/tasks.tsx`：运行行状态 Pill 细分（启动中…/运行中/已暂停/私信收尾中，
  stopping 时显示 statusMsg）；stopping 态操作列改为「进入任务 + 停止存量」
  （调 `/api/engine/stop` 硬停，立即终止剩余私信），paused/运行中 保持 暂停/继续/停止。

### 2. 查阅模式数据源 + 历史任务双击
- `frontend/src/pages/live.tsx`：新增 `recordsToRows(src)` 公共映射；「进入查阅模式」按钮
  改为 `openReview()`——实时 rows 有数据直接开，否则回读 `/api/tasks/current` 容器记录
  （无记录提示）。历史任务/容器记录走同一映射，杜绝空白。
- `frontend/src/pages/tasks.tsx`：历史任务 `<tr>` 加 `onDoubleClick={() => gotoTask(h)}`
  与 title 提示，双击即进入查阅模式。

### 3. 账号「上次运行记录」读历史任务
- `backend/api/accounts.py`：重写 `_build_last_run`——优先 `tasks_history.list_history()`
  中该账号最新一条（config 快照→直播间；records 快照→comments/dmSent/dmSuccess/dmFail；
  start_ts/end_ts→time/duration；totalRuns=该账号历史条数；running 任务 duration=进行中）；
  无历史退回 `_last_run_runtime()`（并修正 dmFail=FAIL 计数，不再依赖 sent-success 差值）。
- 账号管理页阅读模式展示的正是历史任务/查阅模式同一份数据。

## 验证
- 前端 `npm run build` 0 TS 错误；后端 py_compile 通过；19 项集成测试全过。
- `npx tauri build --no-bundle` 仅产主 exe（跳过 NSIS/MSI，符合既定打包约定）。

## 版本号 +0.01：0.20.0→0.21.0（4 文件：Cargo.toml/tauri.conf.json/package.json/frontend/package.json）
sidecar 3 个重打（含 accounts/overview 改动）；主 exe v0.21.0（内嵌新前端）。
部署：`dist\` + `C:\temp\dyautodm_test\` 各一份 `DYAutoDM_v2_0.21.0.exe` + `binaries\` 3 sidecar。

## 需用户实测
- ① 直播监听点「停止监听」后，任务中心仍显示「私信收尾中」任务，可点「停止存量」立即终止；
  队列发完自然收尾为历史任务。
- ② 实时评论统计列表「进入查阅模式」无实时数据时会自动读任务容器/历史记录，不再空白；
  任务中心历史任务行双击直接进查阅模式。
- ③ 账号管理「上次运行记录」= 该账号最近一次历史任务的实录（房间/时间/条数/成功/失败）。

---

# 会话 0.21.0→0.22.0（同日第三轮，前端数据读取加速 + 私信守护自动恢复 + 解析加速）

## 改动背景
用户反馈：账号管理读后端约 4s、私信中心拉会话弹「urlopen error [WinError10061] 拒绝连接」、
解析房间号接近 3s。并希望「前端启动时后端主动推送数据，别每次切页才去拉」，
参考 https://github.com/YGR1996/utlived-app 的直播流地址解析层优化本地 URL 解析。

## 改动详情

### 1. 前端数据：App 常驻共享轮询（等效「启动即拉、数据常热、切页读缓存」）
- `frontend/src/main.tsx`：默认 staleTime 5s→20s，切页 20s 内直接读缓存不重拉。
- `frontend/src/App.tsx`：App 常驻轮询并持有缓存——`accounts(30s)` / `live-tasks(5s)` /
  `live-stream(3s)` / `current-task(5s)` / `task-history(10s)`（App 永不卸载，页面退出
  后这些查询仍持续刷新）。页面用**相同 queryKey 纯读**，不再各自发请求：
  - live.tsx：`live-tasks`/`accounts`/`live-stream` 移除 refetchInterval。
  - accounts.tsx：`accounts` 移除 8s 轮询（操作后仍手动 refetch）。
  - settings.tsx / overview.tsx / messages.tsx：账号查询统一改 `["accounts"]` 读缓存。
  - overview.tsx 顺手修复：原 `d.ok?d.accounts` 解包 bug（getAccounts 返回数组），
    账号总览宫格一直是空的。
  - tasks.tsx：历史任务由本地 setInterval 改为共享 `["task-history"]`；清空后
    invalidateQueries 刷新。

### 2. 账号管理 4s → 秒出
- `backend/main.py`：lifespan 后台线程 `_warm_verify_cache`，启动即并行跑一遍
  api.accounts._cached_verify（写入 TTL 缓存），首屏打开账号页不再现场网络探活。

### 3. 私信中心 urlopen 10061 弹窗 → 自动恢复
- `backend/api/messages.py`：`/conversations`（及 `/conversation`）先探守护端口，
  端口未开/连接拒绝/404 返回 `{ok:true, conversations:[], recvDaemonDown:true}`
  不再抛原始 WinError。
- `frontend/src/pages/messages.tsx`：检测 recvDaemonDown → 只提示一次并自动调
  `startRecvDaemon(账号, port)`（sidecar 拉起），下一轮轮询自动拉到真实会话；
  去掉每 3s 的“正在拉取…/拉取到 N 个会话”toast 噪音（改 5s 轮询、成功仅记日志）。

### 4. 解析房间号 3s → 毫秒
- `backend/link_resolve.py`（对齐 utlived-app 本地抠 URL）：
  - 快速路径：纯数字/web_rid 或已是 `live.douyin.com/<id>` 直接返回，0 网络请求（实测 0.1ms）。
  - 新增 TTL 缓存（5min，thread-safe，≤200 条 LRU）：短链/用户主页等慢解析结果秒回。

## 验证
- 前端 build 0 TS 错；后端 py_compile 过；19 项集成测试全过（3.75s）。
- link_resolve 快速路径 0.1ms。

## 版本号 +0.01：0.21.0→0.22.0（4 文件）
sidecar 3 个重打（含 messages/main/link_resolve 改动）；`npx tauri build --no-bundle`
产主 exe（跳过 NSIS/MSI，符合打包约定）。部署 dist\ + C:\temp\dyautodm_test\ 各一份
`DYAutoDM_v2_0.22.0.exe` + binaries\ 3 sidecar。

## 需用户实测
- ① 各页面切换应秒开（数据已在内存缓存，不再每页重拉）；账号管理首屏不再 4s。
- ② 私信中心：守护未运行时出现「正在自动启动」提示并自动恢复，不再报 10061 连接失败。
- ③ 解析房间号：live.douyin.com/<id> / 纯房号秒回；短链第二次起走缓存秒回。

---

# 会话 0.22.0→0.23.0（同日第四轮，修复私信 422 + 启动闪屏）

## 改动背景
用户反馈两点：
1. 双击 exe 后约 3s 才读到后端数据（PyInstaller 后端冷启动无法消除），希望先唤醒后端再展示页面。
2. 私信中心仍拉不到会话：日志显示 recv_daemon 已起来但 `/conversations` 返回 HTTP 422。

## 改动详情

### 1. 私信中心 422 根因与修复
- 根因：`backend/daemon/recv_daemon.py` 的 `GET /conversations`、`GET /conversation`
  路由声明为 `account: str`（FastAPI 必填 query），但前转发层 `backend/api/messages.py`
  构造 `http://127.0.0.1:<port>/conversations` 时漏带 `?account=`，导致 422。
- 修复：`_recv_url(account, "/conversations?account="+quote(account))`；
  `/conversation` 同样补 `account` 与 `conv_id` 双参数。

### 2. 启动 3s → 启动闪屏（先唤醒后端、就绪后再展示界面）
- 后端冷启动（PyInstaller onefile 解压 ~68MB + 导入）约 3s 属必要耗时，无法再压。
- `frontend/src/App.tsx`：新增 `BootSplash` 全屏闪屏（品牌 DY 标 + 转圈 + “正在唤醒
  后端引擎并准备数据…”），`ready=overview 首帧就绪` 前盖住主界面，就绪后闪屏让位
  ——双击 exe 立即有画面，数据到齐才进入主界面，消除白屏/“未连接”观感。
- `frontend/src/styles/global.css`：新增 `.spinner` 与 `@keyframes spin`。

## 验证
- 前端 build 0 TS 错；后端 py_compile 过；19 项集成测试全过。
- 版本 0.22.0→0.23.0（4 文件）；sidecar 3 个重打；`npx tauri build --no-bundle` 产主 exe。
- 部署 dist\ + C:\temp\dyautodm_test\ `DYAutoDM_v2_0.23.0.exe` + binaries\ 3 sidecar。

## 需用户实测
- ① 私信中心：守护未运行会自动拉起，运行中能拉到真实会话（不再 422）。
- ② 双击 exe：先出「抖音数据控制台」启动闪屏，数据就绪后切入主界面。

---

# 会话 0.23.0→0.24.0（同日第五轮，私信会话列表 API 兜底）

## 改动背景
用户日志：422 已消失（守护已启用并返回 `{"ok":true,"conversations":[]}`），
但私信中心会话列表仍为空 —— 守护刚启动/WS 长连接同步帧未到，收件箱无会话。

## 根因与修复
- 根因：`recv_daemon` 的会话仅来自 WS 同步帧/dm_history 落盘；守护启动几秒内（或
  WS 未同步/被风控）收件箱为空 → `/conversations` 空列表。
- 修复（`backend/daemon/recv_daemon.py`）：
  - 新增 `_pull_conversations_api(ib)`：收件箱为空时直接调
    `DouyinAPI.get_conversation_list(auth)`（IM API，与账号校验的 dm 探活同源、可靠），
    建立会话骨架（conversation_id/short_id）并落盘 dm_history.json。
  - `/conversations`：空时先 API 拉取再加返回。
  - `/conversation`：未见过的会话先 API 拉取再查，点开的会话必定存在。
  - 名称/消息仍由 WS 新消息与历史补全（骨架占位用 conv_id，随后即被真实昵称覆盖）。

## 验证
- py_compile 通过；19 项集成测试全过。
- 版本 0.23.0→0.24.0（4 文件）；sidecar 3 个重打；主 exe --no-bundle。
- 部署 dist\ + C:\temp\dyautodm_test\ `DYAutoDM_v2_0.24.0.exe` + binaries\ 3 sidecar。
  （重要：私信中心依赖的新逻辑在 recv-daemon sidecar，必须连同 dyautodm-recv-daemon
  exe 一起替换；C:\temp 已同步。）

## 需用户实测
- ② 私信中心：守护自动拉起后下一次轮询即可看到真实会话列表（由 IM API 直拉兜底），
  不再空白；进入会话后随 WS 新消息补全昵称与内容。

---

# 会话 0.24.0→0.25.0（同日第六轮，历史任务「运行中」悬空修复）

## 改动背景
用户日志：一条早就停止的任务（2026-08-19 01:23:16）在任务中心仍标注为「运行中」。

## 根因与修复
- 根因：`_run` 的 finally 进入 STOPPING 分支后没有启动 `_wait_dispatch_done`，
  且 `_on_dispatch_idle` 对 STOPPING 态提前 return（`if self.state == STOPPING: return`），
  导致直播自然关播（WS 断联）后队列发空，但历史任务永远收不了尾（「运行中」悬空）。
- 修复（`core/auto_dm.py` `_on_dispatch_idle`）：移除 STOPPING 提前 return 守卫，
  RUNNING/STOPPING/PAUSED 且 WS 已关、队列已空时统一收尾（`_finish_history_task`）。
- 已存数据修复（`tasks_history.py`）：`_load` 时自动检测多任务「运行中」→ 只保留最新一条，
  其余标为「已停止」（`_fix_stuck_tasks`）。`_lock` 改为 `RLock` 避免重入死锁。

## 验证
- py_compile 通过；19 项集成测试全过；_fix_stuck_tasks 冒烟（多 running→保留最新）。
- 版本 0.24.0→0.25.0（4 文件）；sidecar 3 个重打；主 exe --no-bundle 产。
- 部署 dist\ + C:\temp\dyautodm_test\ 0.25.0。

## 需用户实测
- ② 历史任务：之前悬空的「运行中」任务应自动变为「已停止」；以后所有任务结束时
  都能正确收尾，不再出现永驻「运行中」。

---

# 会话 0.25.0→0.26.0（同日第七轮，JSON→SQLite 全面迁移）

## 改动背景
用户要求：数据尽可能走数据库，而非 JSON 文件。对全项目做数据存储审计。

## 全项目数据存储审计结果
| # | 存储点 | 格式 | 建议 |
|---|-------|------|------|
| 1 | task_history.json | JSON | 已迁移 SQLite ✓ |
| 2 | dm_history.json | JSON | 已迁移 SQLite ✓ |
| 3 | config.json | JSON | **本次迁移** → kv_store 表 |
| 4 | accounts.json | JSON | **本次迁移** → kv_store 表 |
| 5 | .env | dotenv | 保留（dotenv 直读+敏感凭证+多进程） |
| 6 | logs/run_*.log | 文本 | 保留（loguru 轮转+非结构化） |
| 7 | exports/*.xlsx|*.csv | 导出 | 保留（用户产物） |
| 8 | 下载 jpg/mp4/txt/info.json | 各种 | 保留（用户产物） |
| 9 | .daemon_alive | flag | 保留（进程间通信标记） |
| 10 | 浏览器 profile | 目录 | 保留（引擎要求） |

## 改动详情

### 1. 新增 SQLite kv_store 通用键值表
- `database.py`：新增 `kv_store(key,value)` 表 + `get_kv/get_kv_json/set_kv/set_kv_json` helper。
- `_migrate_json`：首次运行时自动把 `config.json` → kv_store("config")，
  `accounts.json` → kv_store("accounts_index")。

### 2. 运行时配置 config.json → SQLite
- `api/live.py` resolve：`set_kv_json("config", merged)` 替代 `cfg_path.write_text(json.dumps(...))`。
- `api/tasks.py` save_config：同上。

### 3. 账号索引 accounts.json → SQLite
- `auto_dm/accounts.py`：`_load_index` → `get_kv_json("accounts_index")`，
  `_save_index` → `set_kv_json("accounts_index", idx)`。

### 4. 前端历史任务分页
- `api/tasks.py /history`：新增 `limit`/`offset` query 参数 + `total` 返回。
- `client.ts getTaskHistory`：支持分页参数。
- `tasks.tsx`：每页 50 条 + 「加载更多」按钮（显示已显示/总数）。
- `App.tsx`：共享轮询用 `["task-history", 0]`。

## 验证
- py_compile + 19 项集成测试全过。
- SQLite CRUD 冒烟（start/finish/list/count/clear + kv get/set）通过。
- 前端 build 0 TS 错。
- 版本 0.25.0→0.26.0；sidecar 3 个重打；主 exe --no-bundle。
- 部署 dist\ + C:\temp\dyautodm_test\ 0.26.0。

## 需用户实测
- ① 历史任务：不再有 200 条上限，翻页「加载更多」；旧 JSON 数据首次启动自动迁移到 SQLite。
- ② 配置保存/解析房间号：写回 SQLite kv_store，不再写 config.json。
- ③ 账号增删/切换：写回 SQLite，不再写 accounts.json。