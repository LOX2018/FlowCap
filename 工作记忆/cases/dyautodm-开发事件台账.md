# DYAutoDM 开发事件台账（事故 / 根因 / 实测结论）

> **来源**：从 skill `software-development/dyautodm-development` 迁移（2026-09-17）。
> **迁移原因**：skill 只存「思路 + 判据」；具体事件叙事（事故经过、根因、实测结论）归知识库。
> **性质**：以下为**完整原文**（保真，未改写）；skill 内已留压缩索引与信标。

---

## 2.3 peer_uid 提取失效 → 「N 张本账号卡片」污染（2026-09-07 根因修复）

**症状**：会话列表出现大量显示为本账号昵称的卡片（实测 77 个会话中 32 个），
`dm_conversations.peer_id` 全部 = my_uid。其余会话昵称正常。

**根因**：`parse_init_protobuf(raw, my_uid)` 的对端提取是
`peer_uid = uid_b if uid_a == my_uid else uid_a`。当 `my_uid` 失效
（`auth.get_uid()` 偶发返回空/None，`str()` 后成 `""` 或 `"None"`，**不抛异常所以
外层 except 不触发**）→ 条件恒 False → **恒取 uid_a** → 抖音 conv_id 里 uid_a 常是
本账号 → peer_uid=自己 → peer_name 回填本账号昵称。另 45 条正确的，是恰好 conv_id
里 uid_a 是对端（顺序不同）——**同账号内两种 conv_id 顺序并存**，所以表现为「部分污染」。

**检测基准**：`SELECT COUNT(*) FROM dm_conversations WHERE account=? AND peer_id=<my_uid>`
（>0 即污染；修复后应为 0）。

**修复（4 处，`conversation_capture.py`）**——顺序即防御层次：
1. **my_uid 自愈**（`parse_init_protobuf` 开头）：my_uid 无效时从首包全部 conv_id
   统计 uid 频次，**出现次数 ≥ 会话数×0.9 的即本账号**（自己与所有人聊天，必然出现在
   每个 conv_id 中）。自测 77 会话 → 本账号命中 77 次，100% 正确。
2. **第二道防线**（主解析 + `_fallback_regex` 两处同样的提取式）：算出的
   `peer_uid == my_uid` → 置 None，降级裸 UID，**绝不用「自己」冒充对端**。
   两处都要改，`_fallback_regex` 是主解析失败时的兜底路径，漏改则污染依旧。
3. **写库订正**：写 `dm_conversations` 前用 conv_id 重解析真实对端覆盖错误 peer_uid；
   peer_uid 为空时昵称置空（不写「自己」）。
4. **存量一次性订正**（`conn.commit()` 后）：扫全表 `peer_id=my_uid` 的记录，
   用 conv_id 改回真实对端，昵称降级为 UID 占位（下次 BCC 截获真实昵称时回填）。

**铁律合规**：全程零主动请求，只复用首包 bytes 与已截获的 BCC 昵称字典，
未调用任何 `bulk_user_info`/`get_im_user_info` 等死代码接口。

**对照实验方法**：遇到「昵称全是同一个人」先按 peer_name 分组计数
（`GROUP BY peer_name`）——若只有一部分是本账号昵称，说明不是 hook 整体错配，
而是 peer_uid 提取在特定 conv_id 顺序下走错分支，据此定位到 my_uid 而非 hook 脚本。


---

## 9.3 ① STS 凭证的坑

- **必须用 `public_image_config`（v1）**；`public_image_config_v2` 的 session_token 报
  `Invalid session token, sequence is broken`（v1/v2 token 结构不同）
- 接口必须带 a_bogus + 完整设备参数（缺失报 status_code=8 用户未登录）
- ⚠️ **鉴权强绑定 BCC 页面会话**：页面掉线后 cookie 本身仍有效（query/user 可通），
  但该接口直连仍报未登录 → 发图前提 = BCC chat 页登录态（08 §24.1 铁律）


---

## 9.5 端到端实测通过（2026-09-06 00:42-00:54，最终定论）

- 直调 `send_image()`：①-⑥ 全链路成功，**对方在抖音实收确认**（用户亲眼验证）
- `recv_daemon /send_image` 端点：发送 ok + DB 落库验证通过
  （`dm_messages #35026 role=me msg_type=image extra={skey,oid,…}`）
- sidecar 修改必须重打包并覆盖 `<测试目录>/binaries/`（旧 binaries 404 无 /send_image，
  部署铁律 10 再证）
- **profile 锁不阻塞发送**：BCC 占用 profile 时 `refresh_cookie_from_profile`
  自动跳过（vbrowser exitCode=21，SingletonLock 冲突），`.env` 里的凭证直接可用；
  不要因为刷新失败就中止发送流程


---

## 9.8 UID 身份错乱：web user_uid 轮换 ≠ imapi 会话 uid（2026-09-06；**凭证监控修复 1-3 已落地**）

**症状**：前端切账号后「更新会话」返回 0 会话；`get_message_by_init` 首包仅 50B
（`{"BaseResp":{},"Header":{},"Method":0,"Service":0}` = **imapi 认为请求无效的特征空壳**）；
`create_conversation`/cmd610 均 INVALID_REQUEST——但详情接口仍能读到 DB 旧消息。

**根因**：`get_uid()` 走 `query/user` 的 `user_uid` 字段，该值**可能被抖音轮换**
（实测同账号 00:41 返回 `316…` 且 imapi 全链路正常；01:28 应用重启重新握手后返回
`2609…`，imapi 全部拒绝）。即 web 侧 `user_uid` 与 imapi 会话体系绑定的 uid **不是
永远同步的同一个值**。

**诊断手法**：
1. 对比时间线：出错前 imapi 成功时用的 uid vs 出错后 `get_uid()` 返回值
2. 用旧 uid 强行构造 cmd610/609 请求，验证 imapi 认哪个 uid
3. `query/user` 的 `last_time` 可判断 user_uid 轮换发生的时刻
4. cookie 里不含明文 uid（uid_tt 是 hex），无法本地核对

**✅ 凭证监控修复（2026-09-06 已实现+重打包+部署测试目录 binaries，修复 4 按用户指示暂不动）**：
1. `refresh_cookie_to_env`（browser_daemon.py）写 .env 前双门禁：新 cookie 必须网络探活出 uid
   （`probe_auth.uid=None` 强制不吃缓存）+ uid 与 `_last_uid` 一致；任一失败**拒绝写入并告警**，
   保住 .env 里最后一份好凭证；成功后回写 `_last_uid` 作漂移基线
2. `run_keepalive`（browser_daemon.py）uid 与上次比对：漂移 → error 日志（不再打"登录态正常"绿标）
   + 自动触发 `scan_login(force=False)` 重扫
3. `verify_account`（accounts.py）探活 uid 与该账号 `dm_conversations.conv_id` 的 uid 段
   （`0:1:<a>:<b>` split(":")）交叉验证：漂移 → wp 引擎判 **fail「uid 漂移（身份存疑）」**
   并提示重新扫码；新账号（无历史会话）自动跳过不误伤
4. BCC `/cookie` 账号一致性校验（`_bcc_alive` 只查端口 alive、手动 `--port` 可绕过端口哈希）
   → **后已全部修复（同日二批提交）**：④a `_bcc_alive` 除 alive 外校验 /status 的 `account`
   与请求账号一致，不匹配按未运行处理并告警（拒绝跨账号取 cookie）；④b `browser_daemon.main`
   启动时校验 `--port` 与 `browser_daemon_port(account)` 哈希一致，不一致 SystemExit(2) 拒启
   （调试逃生口 `--allow-any-port`）；④c keepalive 新增 `_page_login_state_sync()` 页面级
   登录态检查（见 §9.9）。至此四处缺陷全部闭环。

**修复效果**：同类事故（登录态失效→失效 cookie 覆盖 .env→uid 轮换→全功能静默崩）
再发生时，前端会明确报「uid 漂移，请重新扫码」而非静默失败；好凭证不会被坏凭证冲掉。
注意：修复 3 依赖 DB 已有该账号历史会话；全新账号首次登录场景由门禁 1/2 兜底。

**9.8.1 幽灵 uid 门禁 1.5：写入方探活必须过历史会话一致性（2026-09-08 事故修复）**

**事故链**（用户看到的现象是「BCC 浏览器常驻且反复拉取/重启」）：账号 profile 里登录的
不是本人（残留他人的凭证）→ 保活 `refresh_cookie_to_env` 首次写入时 `_uid_at_last_env_write`
基线为 None → **门禁 2 直接放行** → 他人 uid 写进 `.env` → keepalive 探活到该 uid ≠ 历史
uid → 判「uid 漂移」→ `scan_login` 重启浏览器 → 页面仍是那个账号 → 无限循环。

**铁律：任何要写入 `.env` 或决定重启浏览器的 uid，必须先过「历史会话一致性」校验**——
探活 uid 必须出现在该账号 `dm_conversations.conv_id` 的 uid 段（`0:1:<a>:<b>` split(":")）中：
- 0 命中 = 幽灵 uid（他人凭证 / 陈旧缓存）→ **拒绝写 .env、拒绝作为漂移基线、不采信**
- 门禁 2 的「首次写入基线为 None」不能作为放行理由，一致性校验是**独立于基线的硬门禁**
- `uid_probe` 的 fallback 裸探活路径同样必须过校验，否则一致性校验被绕过（本次事故
  正是 fallback 裸调 `get_my_uid(auth)` 绕过了校验）

**定位幽灵 uid 的方法**：用 `.env` 当前凭证实打 `query/user` 得到 uid，再
`SELECT COUNT(*) FROM dm_conversations WHERE account=? AND conv_id LIKE '%<uid>%'`——
0 命中即为幽灵，说明 `.env` 已被污染，此时**先停 BCC**（走 `/quit`，勿强杀）再让用户重登。

**注意 uid 缓存误判**：`get_my_uid` 对 cookie 里的 `uid_tt` 有短路（纯数字直接返回、
hex 不短路），且进程内 `_uid_probe_cache` 会返回上一次的值——日志里看到
「uid=本人」可能是**过期缓存**，要用 `force_probe=True` 或清缓存实测才作数。

**⚠️ 消费侧缺口：门禁 1.5 只拦「写入 .env」，拦不住已污染的 uid 被消费。**
实证：某账号 `get_uid()` 返回陈旧值（与该账号 `dm_conversations.conv_id` 里的 uid 段
不一致），该值被直接用于 `create_conversation` 签名 → 服务端返回 `INVALID_REQUEST`
（cmd 609）→ **发送被拦，但账号管理页仍显示 `level=ok`「凭证齐全」**。

**铁律：账号页绿灯 ≠ 可发送。** 判定「这个账号能不能发」必须实测两件事：
1. `get_uid()` 是否出现在该账号历史 `conv_id` 的 uid 段（`0:1:<a>:<b>`；
   `SELECT conv_id FROM dm_conversations WHERE account=?`）；
2. 直接对目标 `create_conversation` 试一次（不是只看 `/api/accounts` 的 level）。

**A/B 对照是唯一可信归因手段**：同时用两个账号跑同一份代码，一个 `{"ok":true}`、
另一个 `INVALID_REQUEST` ⇒ 问题在**账号身份/凭证**，不在实现，别去改发送代码。
注意：**为失败账号补拉 BCC（`POST /api/accounts/{name}/ensure-bcc`）不一定能解决**
该症状——实测补拉 BCC 后仍 `INVALID_REQUEST`，因为根因是探活 uid 本身陈旧，
不是缺少 cookie 刷新通道。**排查顺序：先比 uid 一致性，再去怀疑 BCC 缺失。**

**⚠️ 铁律升级（2026-09-13 用户强调）：UID 漂移 = 凭证失效。** 探活 uid 与账号
历史 conv_id 不一致（AUTH-050）不是「uid 数据陈旧」而是**凭证已失效**——必须触发
重新捕获（scan_login / auto-recapture），不能仅「不缓存 uid 继续用旧凭证」。
凭证字段「看起来新」（ticket / web_protect / keys 齐全）≠ 有效；**UID 与账号匹配
才是有效判据**。2026-09-13 双账号强制下线链：headless 环境跳变 → 登录态被强制下线
→ AUTH-050 漂移 → 发送 INVALID_REQUEST(cmd 609) + WS token 失效停推送（详见
dev-guards §十七）。凡见「凭证齐全但发送 INVALID_REQUEST / WS 停推」，先验 uid
一致性再谈其它。


---

## 9.10 首包时间字段（日期分割线只剩一条的真正根因，2026-09-06）

**症状**：新账号（小助理）「更新会话」后 27 条消息 ts 全部挤在同一分钟，
前端日期分割线只有一条；而老账号（张老师，WS 实时落库）分割线正常。

**根因**：`_parse_message_text` 返回的 `createdAt` 取自 content JSON 的
`obj.get("createdAt")`——**只有图片消息才有**；文本消息没有 → `ts` 全部
fallback 到入库 `time.time()`。黑盒解析首包实证：**message 对象
protobuf field 10 = create_time 毫秒**（与 DB 历史时间完全吻合），但从未被读取。

**修复**：新增 `_parse_message_create_time(b)`（field 10，毫秒范围校验
2020-2030 防误配，秒级兑底），优先级：field10 → content.createdAt → 入库时间。
已用保存的首包验证：时间恢复真实历史（08-12/08-16/08-17/08-19/09-06 五个日期）。
⚠️ 已入库的错误 ts 无法自动恢复（源数据当时就没存）；需清该账号会话数据重拉。

**方法论**：protobuf 黑盒定位字段——递归 parse 找 varint，按毫秒/秒时间戳
范围过滤（`1577836800000 <= v <= 1893456000000`），命中字段的还原时间与
DB 已知历史时间比对即可实锤字段语义。


---

## 9.12 无头伪装模式（2026-09-06 ✅ 已闭环：提交+重打包+部署+复测通过）

**用户需求澄清**（用户原话：“我要的是直接调用指纹浏览器，而不是新开”）：BCC 的
“无头”必须与账号管理“双击打开指纹浏览器”同链路同环境，只是无头状——不要被理解成
改视口参数（曾误改 viewport 对齐，被用户纠正“不对”，已回滚）。

**机制依据**：双击打开（`open_douyin_home(headless=False)`）与 BCC
（`launch_async(headless=True)`）本就是同一函数/内核/profile，唯一差异是
headless 标志；而 Chromium headless 模式会被抖音风控识别 → 同一 profile
有头正常、BCC 半登录态（§9.9 事故根因）。

**实现**（vbrowser.py，已提交+重打包+部署测试目录 binaries）：
- `_HEADLESS_DISGUISE_ARGS = ["--window-position=-32000,-32000", "--window-size=1440,900"]`
- `launch_async`/`launch_sync`：`headless=True` 时追加伪装参数并以
  `headless=False`（恒真有头）启动，日志打“无头请求已转为 真有头+窗口移屏外”
- 对抖節 100% 有头特征；对用户等效无头（窗口在屏外看不见）

**⚠️ 归因修正（2026-09-13 复发实证推翻）**：09-06 曾下结论「半登录态根因是
session 生命周期而非 headless 模式本身，伪装模式非必需，09-08 改回 native 纯无头
可用」——**该结论 09-13 被双账号同时强制下线证伪**：BCC 纯 headless 长期运行会被
抖音识别（同 profile 下用户手动有头登录态正常、BCC 无头触发 step-up 重验证 →
弹「安全风险阻止访问」→ 登录态强制下线 → UID 漂移 → 凭证失效）。短期实测
（00:42 无头发图成功）只能证明单次可用，不能证明长期常驻安全。

**最终形态（c409b90 落地，取代 disguise/native 二选一）**：`launch_async` /
`launch_sync` 收到 `headless=True` 请求一律转为 **真有头 + 窗口最小化**——
`headless=False` 启动 + CDP `Browser.setWindowBounds({windowState:"minimized"})`
（`_minimize_window` / `_minimize_window_sync`）。有头特征与扫码/查看完全一致
（杜绝环境跳变），最小化不改变 JS 可检测特征且不污染 profile（-32000 屏外坐标
方案 09-09 已废弃）。`DY_BCC_HEADLESS_MODE` 不再使用。

⚠️ **但这不是本次症状的全部根因**：同一天实测证实**出口 IP 分叉是更致命的共因**
（手动打开走系统代理、BCC 被强制直连 ⇒ IP 在两国间跳）。只修 headless 会得到
「改完依旧弹窗」的假失败——**排查顺序：先量出口 IP（§9.30），再验 headless 特征**。

⚠️ `vbrowser.py` 被 backend / browser-daemon / recv-daemon **三个 exe 共同依赖**，
改它必须三个全部重打（单打规则 §1.2 的例外）。

**验证“无头请求已转有头”是否真的生效**（日志不能作为证据，要看进程实参）：
```powershell
# 看指纹浏览器主实例的启动参数里还有没有 --headless
Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" |
  Where-Object { $_.CommandLine -like '*user-data-dir*' -and $_.CommandLine -like '*accounts*' } |
  Select-Object ProcessId,CommandLine
# 判定：出现 --headless = 未生效（跑的是旧二进制）；无 --headless + 有窗口标题 = 已生效
```
日志侧对应两条标记：`无头请求已转为 真有头+窗口最小化` + `窗口已最小化到任务栏`。
⚠️ CDP `Browser.setWindowBounds(minimized)` 对**无头 context 是 no-op**，
所以“最小化”只能证明“已是有头”，不能反过来证明——必须看进程实参才算数。

**✅ 复测已通过**（2026-09-06 14:31，用户重新扫码后）：伪装模式 chat 页
conv=12、无一键登录字样 → 登录态完全正常。**A/B 对照教训**：复测前曾遇
伪装模式与纯有头**都**半登录 → 那次是 profile session 服务端失效（与伪装无关），
区分方法是先用纯有头打开同一 profile 对照。

> 注：2026-09-13 起不再区分伪装/native——无头请求一律转「真有头+窗口最小化」
> （见本段上方归因修正）。历史 A/B 对照仍有效：同代码双账号一好一坏 ⇒ 归因到
> 账号身份/凭证而非实现。


---

## 9.17.1 前端关闭不连带走 sidecar：Tauri 清理逻辑拿不到句柄（孤儿进程根因）

**症状**：主窗口关闭后 `recv_daemon` / `backend` 仍以孤儿进程存活数小时，占端口与
profile；下次启动日志显示「已在运行，跳过」或端口冲突，表现为「上次的进程还在」。

**根因（取证结论，两层）**：
1. Tauri `lib.rs` 的 `on_window_event(CloseRequested)` 清理逻辑**本身是对的**
   （`state.backend.take().kill_tree()` + 遍历 `state.daemons`），但 `AppState` **只由
   `start_backend` / `start_recv_daemon` 这些 command 填充**。前端若从未 `invoke` 它们，
   `backend` 恒 `None`、`daemons` 恒空 → **清理时杀的是空气**。
2. `backend` 进程内的 `_auto_start_daemons()` 用 `subprocess.Popen`
   （`CREATE_NEW_PROCESS_GROUP`）spawn 守护——**Windows 不会因父进程退出而级联回收**，
   父进程一死即成孤儿，这与 Tauri 有没有句柄无关。

**取证顺序**：
1. 全仓 grep 前端 `invoke(` 调用点，确认 Tauri 句柄到底有没有被填过；
2. 查孤儿进程 `ParentProcessId` 指向的进程是否还在（取不到 = 孤儿）；
3. 比 `CreationDate` 与本次启动时间（更早 = 上次遗留）。

**已实施方案（backend 自管台账，不依赖前端也不依赖 Tauri）**：
- 新增 `backend/daemon_registry.py`：跨模块共享的 pid 台账（`register` / `unregister` /
  `kill_all`，进程内线程安全）。**所有 spawn sidecar 的位置都要 `register`** —— 项目里
  有多条 spawn 路径（`main.py._spawn_sidecar`、`auto_dm/daemon_launcher.py._spawn_sidecar`），
  漏登记一路就漏一路孤儿。
- 清扫时机两处：① FastAPI `lifespan` 的 shutdown 段；② **`atexit` 兜底** —— uvicorn 被
  强杀 / 信号退出时 lifespan 可能根本不执行，只挂 lifespan 会漏。
- **BCC 必须排除**：`browser_daemon` spawn 后立即 `unregister` —— 它是常驻保活容器
  （「守住所不退则凭证有效」的依据），不随 backend 退出清扫。铁律：不强杀 BCC。
- 验证方法（不必真起 sidecar）：起 3 个长睡子进程，2 个登记 / 1 个注销 → `kill_all()` 后
  登记的必须死、注销的必须活。

**⚠️ 源码态启动 recv_daemon 必须设 `PYTHONPATH`**：脚本以 `daemon/` 为 `sys.path[0]`，
`python daemon/recv_daemon.py` 会 `ModuleNotFoundError: No module named 'vbrowser'`
（`vbrowser.py` 在 `backend/` 下）。正确形态：
```bash
cd backend && PYTHONPATH="<backend绝对路径>" DY_APP_ROOT=<app_root> PYTHON_BASIC_REPL=1 \
  python daemon/recv_daemon.py --accounts "<账号名>" --port <端口>
```
参数是 `--accounts`（复数，`--account` 只是兼容别名）；**`--port` 是必填参数**
（漏传直接 `error: the following arguments are required: --port` 退出，日志只有一段
argparse usage——别把这当成「守护起不来」的疑难问题）；启动后 `curl /status` 应见
`connected:true` + `conv_count>0`。

**注意**：PyInstaller onefile 会解压出 `_MEIxxxx` 孙子进程，`taskkill /T` 未必可靠——
不要把「有句柄」当成「一定杀得掉」。清理时仍须遵守 §9.17：BCC 走 `/quit`，绝不强杀。


---

## 9.24 直播监听启动与「凭证失效」误报（2026-09-08）

**启动路径**：直播监听没有独立的 start 路由，走引擎——`POST /api/engine/start`，
`TaskConfig` 字段是 `liveUrl` / **`acct`**（不是 `account`）/ `enableDanmaku` 等；
`live_url` 为空会返回 400。已保存的直播间在 SQLite `kv_store["config"]` 的 `live_url`。
状态读 `GET /api/live/stream`（**不是 `/api/live/status`，该路由不存在，返回 404**）。

**⚠️ 启动报「发送账号凭证失效」先看是不是竞态**：引擎 `start` 内部
`_verify_credential`（私信签名三件套 → `get_my_uid` → `create_conversation`）任一失败
都会置 IDLE。若同一时刻 BCC 保活正在回写 `.env`，引擎读到的是**回写瞬间的中间态凭证**
→ `create_conversation` 被服务端拒（报 INVALID_REQUEST/KICK）→ 误判凭证失效。
**判定方法**：用引擎同款路径 `AutoDM._build_one_auth(env, False, 0)` 构造 auth 单机复跑
三件套+uid+create_conversation（不要用自己的 `_load_auth_from_env`，对象不同结论不可比）。
单机全通而引擎失败 = 竞态，**原样重发一次 `/api/engine/start` 即通过**（实测第二次
预检直接 ok，监听正常启动）。只有单机也失败才是真凭证失效，才需要重新扫码。


---

## 9.26 会员加密凭证读取失效：`is_member_env` 只靠路径前缀（事故修复）

**症状（极具迷惑性）**：指纹浏览器登录态正常、**网页端私信能正常发送**，但账号管理页
校验持续异常——wp 引擎 `fail`「凭证未就绪」、`uid=None`，并不断触发无谓的「自动重捕获」
（弹浏览器 / 读 profile / 写回，全是空转）。**功能正常而校验失败 = 读取链路坏，不是凭证坏。**

**根因链**：
```
member_space_root() 依赖 current()（进程内内存会话）
   ↓ 独立进程 / 子进程 / 会话未注入 → 返回 None
is_member_env(env_path) 恒 False
   ↓ parse_env_dict 走明文分支
磁盘上实际存在的 <env_path>.enc 被无视
   ↓ credentials_complete → "账号 .env 不存在" → wp fail → 重捕获循环
```
凭证本身完全正常（实测：解密后 cookies/ticket/ts_sign/client_cert/private_key/
web_protect/keys 七键齐全，探活 uid 正确）。

**修复（`services/member_ctx.py`，双判据 + 三层回退）**：
1. `is_member_env` 改**双判据**，任一成立即按会员加密凭证处理：
   - ① 路径位于会员数据空间内（原语义保留）
   - ② **文件事实**：存在 `<env_path>.enc` **且** 主密钥可用 → 以磁盘事实为准，
     不再依赖路径归属与进程会话态（**根治**：路径错配、会话丢失都不再致命）
2. `master_key()` 加第三层回退：内存会话 → 环境变量 `DY_MEMBER_KEY` →
   **盘上 `members/.session.json`**（带 mtime 缓存，避免每次读盘）
3. `member_space_root()` / `accounts_root()` 的 member_id 同样加盘上会话回退——
   否则会拼出 `<members>/auto_dm/accounts`（**空 id 目录**，路径错假的另一表现）

**铁律：判断加密凭证是否存在，以「文件事实 + 密钥可用」为准，不以路径归属为准。**

**三步排查套路**（区分「凭证真坏」还是「读不到」）：
```python
from services import member_ctx
print(member_ctx.member_space_root())   # None ⇒ 会话态没注入（读不到的根因）
print(member_ctx.is_member_env(env))    # False 但 .enc 存在 ⇒ 判据失效
member_ctx.parse_env_dict(env)          # {} ⇒ 解密/路径有问题
# 注入会员态再对比，能实锤「凭证是好的，只是读不到」：
os.environ["DY_MEMBER"] = sess["member_id"]; os.environ["DY_MEMBER_KEY"] = sess["master_key"]
```

**⚠️ 调 API 自查时鉴权头是 `x-member-token`，不是 `Authorization: Bearer`**
（`main.py:472` 读 `request.headers.get("x-member-token")`）。用 Bearer 会 401
`{"detail":"未登录或会话已过期"}`，curl 拿到空输出——别据此以为接口没实现。
```bash
TOKEN=$(python -c "import json;print(json.load(open(r'C:/temp/dyautodm_test/members/.session.json',encoding='utf-8'))['token'])")
curl -s http://127.0.0.1:8000/api/accounts -H "x-member-token: $TOKEN"
```


---

## 10.2 cookie 失效正确修复路径：recapture_from_profile（不重扫码）

**症状**：搜索接口报 2483「请先登录」/ `profile/self` 返回 status=8「用户未登录」；账号管理页「凭证齐全」是绿的——**不是「账号管理误报」**，是它校验的是 imapi 签名四件套（私信体系），与网页 cookie（搜索/评论用）是两套独立凭证，**互不代表**。

**根因**：`.env` 里 `DY_COOKIES` 是扫码那一刻的**静态快照**；浏览器 profile 里的 cookie 持续自动刷新。服务端轮换 sessionid 后快照即失效，profile 仍活。

**判定口诀**：搜索单独 2483 → 先 curl `profile/self` 看是否 status=8「用户未登录」；是则从 profile 重读凭证（路径见下），别先怀疑签名/参数。

**正确修复**（不重扫码、不丢登录态）：走 V2 自带 `dy_apis.login_api.read_auth_from_profile`（API 路由 `/accounts/recapture-profile`）——从已登录 profile 直接读取实时凭证，调 `save_credential` 写回 .env。读不到干净凭证不写盘（uid 门禁内置，详见 §9.8）。

**两坑**：
1. 从源码目录跑会 `_path_normpath(None)` 崩——`app_root()` 解析到源码根找不到账号；必须前置 `DY_APP_ROOT=C:\temp\dyautodm_test`（部署/测试目录）
2. 重读前必须 profile 锁空闲（chrome=0），否则触发浏览器抱死。**绝不强杀 BCC/浏览器**（§九.17 铁律）——如已锁，走 `browser-singleton-lock` skill 的「精确匹配 user-data-dir」清理

**自作探针骨架**（避免走 HTTP API 留窗口）：
```python
import asyncio, os
os.environ["DY_APP_ROOT"] = r"C:\temp\dyautodm_test"
async def go():
    api = login_api.DouyinLoginAPI()
    auth = await api.read_auth_from_profile(name="尚进工伤小助理")
    print("uid:", auth.uid, "cookies:", len(auth.cookies))
    api.save_credential(auth)  # 写回 .env（带 uid 门禁）
asyncio.run(go())
```

**效果**：搜索接口 status=0 返回真实数据（10 条视频 + 20 条评论含昵称/uid/IP 属地），采集闭环全通。验证后必须把「`DY_COOKIES` 是静态快照、profile 持续刷新」这一认知同步到 `工作记忆/11_数据采集与评论截流.md`。


---

## 10.3 纯前端改完不重新构建 = 永远看不到效果（2026-09-07 教训）

**症状**：修改 `frontend/src/pages/*.tsx` 后，刷新应用窗口 → 改动未生效（典型表现：账号下拉仍空/旧文案仍显示）。

**根因**：Tauri 主程序把前端 bundle 嵌入 exe（`src-tauri/target/release/dyautodm-v2.exe`），**dev 模式没有热更**。开发期间改 `.tsx` 文件并保存，浏览器也不会自动刷新——文件改了但 exe 仍是旧 bundle。**这是 Tauri 嵌入模式与浏览器 dev server 的本质区别**，与 backend/edge 缓存无关。

**强制流程（任何前端改动后必走）**：
1. `npx vite build`（产 `frontend/dist/`，~2s）
2. **`export PATH="/c/Users/LOX/.rustup/toolchains/stable-x86_64-pc-windows-msvc/bin:$PATH"`**（每个新 shell 都要，cargo 在 .rustup 不在 .cargo\bin，缺 PATH 报 `cargo metadata ... program not found`）
3. `cd src-tauri 父目录 && npx tauri build --no-bundle`（测试阶段**不打 NSIS/MSI 安装包**，用户明确每个 400MB+ 纯浪费），~3min
4. 部署主 exe：`cp -f src-tauri/target/release/dyautodm-v2.exe C:\temp\dyautodm_test\DYAutoDM_v2_<version>.exe`
5. **从部署目录启动**：`powershell -Command "Start-Process -FilePath 'C:\temp\dyautodm_test\DYAutoDM_v2_<ver>.exe' -WorkingDirectory 'C:\temp\dyautodm_test'"`——MSYS 下 `cmd //c start` 不可靠（弹个 shell 横幅就退出），必须 PowerShell Start-Process 且 `-WorkingDirectory` 指向部署目录（app_root 解析依赖 cwd）（不是从源码 release 启动！）

**判定口诀**：用户报"前端改完没变化" → 第一动作**不是再读代码**，是 `stat src-tauri/target/release/dyautodm-v2.exe` 看时间戳；早于改动时间 = 漏了 build 步骤，重跑 1-4。

**⚠️ 启动目录铁律（2026-09-07 反复踩坑）**：**一律从 `C:\temp\dyautodm_test\` 启动，绝不从 `src-tauri/target/release/` 启动**。后者是源码构建目录，**没有 `vb_chromium`**（它只存在于部署目录 `C:\temp\dyautodm_test\vb_chromium\`），且 `app_root()` 解析到源码根 → 打开指纹浏览器报「vb_chromium 内核不存在」+ sidecar 加载的是源码目录旧 backend（18:37 改的封面解析，17:16 的 exe 根本不带）。完整部署顺序：先 `build_one('main.py','dyautodm-backend')` 打 sidecar → `cp` 到 `binaries/` → `npx tauri build --no-bundle` 打主 exe → `cp` 主 exe 到部署目录 → `cd C:\temp\dyautodm_test && ./DYAutoDM_v2_<ver>.exe`。

