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