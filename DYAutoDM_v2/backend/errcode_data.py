"""错误码 —— 纯数据（域定义 / 错误码表 / 设计契约）

## 为什么独立（2026-09-15 大单文件打散）

原 `errcode.py`（673 行）把 570 行**纯数据字典**与查询/格式化逻辑混在一起。
按「数据与逻辑分离」抽出本模块，`errcode.py` 只保留函数（lookup/ec_*/all_codes 等）。

## 说明
**纯搬移**——所有字典逐字节不变，仅换宿主文件。
"""
from __future__ import annotations

DOMAIN_INFO = {
    "ACC": ["账号管理", "扫码/校验/凭证文件/账号索引（kv_store）", "先看是否 uid 漂移；账号列表空查 kv_store[accounts_index]"],
    "AI": ["AI获客回复", "知识库导入/语义缓存/embedding/回复生成", "三级漏斗阈值 0.40；PyInstaller 漏 python-multipart 则 8000 不启"],
    "AUTH": ["凭证与签名", "cookie 快照失效 / user_uid 轮换 / 签名三件套", "uid 探活走 services/uid_probe.py 统一调度；禁止复用凭证直发请求"],
    "BCC": ["浏览器容器", "daemon 未拉起(10061) / 滚动超时 / SingletonLock(exitCode 21) / 半登录态", "10061=ensure_daemons_for 缺失；ReadTimeout=首包大滚动超时；绝不强杀，走 /quit"],
    "CAP": ["会话捕获", "BCC 未起时序(warmup 0 命中) / 首包空壳(uid 轮换) / 昵称关联", "首包 50B 空壳=imapi 拒绝=uid 轮换特征"],
    "CRAWL": ["数据采集", "搜索 2483 请先登录 / verify_check 拦截", "搜用户被 verify_check 拦截属抖音风控，非代码问题"],
    "DB": ["数据库", "迁移失败 / 落库异常", "删除必须精确逐条，禁止 LIKE 模糊删"],
    "ENG": ["自动私信引擎", "凭证预检竞态(BCC 回写中间态) / 直播间状态查询", "引擎报凭证失效先原样重发一次 start（回写竞态）"],
    "IMG": ["图片链路", "解密(skey/HEIC) / 本地托管 / 图床降级", "图床偶发 502/SSL 超时，默认本地托管属预期"],
    "LIVE": ["直播监听", "心跳探活 / 直播间解析 / 直播 WS", "TaskConfig 字段 liveUrl/acct；状态查 /api/live/stream"],
    "MEM": ["会员体系", "会员 DB 初始化 / 登录迁移", "初始化失败不阻塞登录属预期降级"],
    "MSG": ["私信读取", "纯读库接口异常 / 守护未运行", "私信页纯读 SQLite，绝不触发网络捕获（铁律）"],
    "NTY": ["IM通知指令", "通知通道 / 指令解析", "启动失败不影响主流程属预期"],
    "HUB": ["模型中心", "提供商密钥 / 模型拉取 / 链路解析", "测密钥/拉模型走提供商上的按钮；链路空=回落旧配置"],
    "RECV": ["接收守护", "WS 长连接稳态(L0-L3) / msg 解析 / 方向判定 / 重连追赶", "30s 定时断连=曾用 ping_interval+ping_timeout（已在 v0.43.36 移除，属客户端自杀非服务端掐断）；KICK 时 protobuf 解析失败实为 JSON 风控响应"],
    "SEND": ["发送链路", "统一闸门限速 / 通道回退 / KICK 风控", "rate_limited=8s 闸门属预期；KICK=账号信誉；必须 DB 落库 role=me 才算成功"],
    "SYS": ["系统与启动", "daemon 拉起 / 路由挂载 / 配置", "BCC frozen exe 必须带 DY_APP_ROOT；并行拉起 ~15s"],
    "PROBE": ["能力探针", "M1 探针缺失 / 三态判定 / 覆盖率与基线 / 探针假健康", "探针只读本地事实（DB+本项目日志），零网络零浏览器；报 healthy 必须带 evidence；三态禁止二态"],
    "TSK": ["任务历史", "历史任务读写 / 导出", "读失败多为文件占用，重试即可"],
    "MISC": ["未分类", "", "按消息里的 [tag] 定位模块"],
}

# ════════════════════════════════════════════════════════════════════════════
# 域级「设计契约」（2026-09-13 用户要求：报错必须回归设计理念，不能只看现状）
#
# 为什么：只有「发生了什么」→ 排查会在终端现状打转；
#        必须同时给出「本该是什么」→ 才能判断偏在哪、从哪一环断的。
#
# 字段（对应调试六步闭环）：
#   intent    该模块**应该做什么**（设计意图 / 一句话契约）
#   invariant 必须成立的**不变式**（破了即 bug，与实现无关）
#   chain     正常**链路**（源头 → 传播 → 终端）——排查从源头开始
#   verify    **实机**判据（命令/现象/DB 证据；禁止纯代码推断定论）
# ════════════════════════════════════════════════════════════════════════════
DOMAIN_DESIGN = {
    # 直播监听域（2026-09-21 ENG-018 补齐：此前该域整体缺设计契约，
    # 域内全部码在 contract_gaps() 审计里恒报缺 design）
    "LIVE": {
        "intent": "监听目标直播间：建连直播流（WS）收弹幕/礼物/进场/热度，"
                  "把弹幕转成私信目标交给调度中心。",
        "invariant": "① 直播流建连**不依赖账号登录态**（平台允许匿名观看）；"
                     "② 凭证的职责是「部分直播间昵称加密时的解密权」——"
                     "有解密权才收得到真实 uid/nickname/sec_uid；"
                     "③ 无解密权时**降级而非中断**：继续收弹幕但明确告知昵称脱敏；"
                     "④ 「有 cookie」不是可用性判据，必须是服务端承认的登录态。",
        "chain": "TaskConfig(live_url/acct) → AutoDM._run → probe_live_identity → "
                 "check_room_live → LiveChatHook(auth_, session_ok) → "
                 "DouyinLive.start_ws → WS 帧 → on_message → dispatch.submit",
        "verify": "① 弹幕日志的 uid 是否为 111111、sec_uid 是否为空；"
                  "② scripts/diag/diag_live_cred_ab.py —— 同房间只换 cookie 的 "
                  "A/B 判型；③ GET /api/live/stream 的 alive/feed。",
    },

    "BCC": {
        "intent": "每账号唯一浏览器所有者：常驻持有该账号 profile，对外只通过 HTTP "
                  "端点提供「读凭证/截昵称/发送/切换可见性」；绝不新开第二个实例"
                  "（抢 profile → 环境跳变 → 风控）。",
        "invariant": "① 同账号同刻最多 1 个 BCC 持有 profile；② 浏览器动作串行"
                     "（单 _lock + 租约）；③ 可见性切换/自愈重建不得产生用户可见"
                     "副作用（不弹窗、不闪窗）。",
        "chain": "调用方(更新会话/发送/扫码) → services.browser_gate（唯一门禁）→ "
                 "租约仲裁 → BrowserContainer._exec → Playwright context → 抖音页面。"
                 "源头是「谁在申请浏览器」，不是「页面为何异常」。",
        "verify": "Get-CimInstance Win32_Process 看 browser-daemon 数量恒为 1；"
                  "curl :11231/status 看 alive/uid/lease；chrome 主实例命令行有无 "
                  "--headless（有无头判据）。",
    },
    "CAP": {
        "intent": "会话捕获 = 把抖音的会话/消息/昵称/头像如实写进本地库，供私信页纯读。"
                  "昵称/头像唯一来源 = BCC 被动 hook 截前端自发 im/user/info"
                  "（零主动请求，昵称关联风控红线）。",
        "invariant": "① 昵称链路绝不主动批量查询用户信息；② peer_uid 必须来自首包 "
                     "conv_id（0:1:my:peer），绝不用「自己」冒充对端；"
                     "③ 首包(2043)与 cmd301 两条解析路径逐字段对齐。",
        "chain": "点「更新会话」→ api/messages.refresh → capture_all → "
                 "①.env 凭证 → ②BCC 就绪(gate) → ③首包 HTTP(parse_init_protobuf) → "
                 "④cmd301 补全 → ⑤BCC 滚动截昵称 → ⑥写 dm_conversations/dm_messages。"
                 "源头是「按钮是否真的拿到了浏览器与凭证」。",
        "verify": "curl :8000/api/messages/conversations 看 peer_name 是否仍等于 "
                  "peer_id（等于=昵称未关联）；日志「滚动轮次 N: 新点击=X 累计昵称=Y」"
                  "——Y 恒 0 即 hook 未生效；DB SELECT COUNT(*) FROM dm_conversations "
                  "WHERE peer_name<>peer_id。",
    },
    "AUTH": {
        "intent": "凭证唯一真相在账号 .env（会员空间为 .env.enc）；探活 uid 与账号历史 "
                  "conv_id 一致才算凭证有效。",
        "invariant": "UID 漂移 = 凭证失效（用户铁律）——必须触发重新捕获，"
                     "不能「不缓存 uid 继续用旧凭证」。",
        "chain": "扫码/BCC 保活回写 → .env → services.uid_probe（唯一探活出口）→ "
                 "query/user → 与历史 conv_id 交叉校验 → 消费方读缓存。",
        "verify": "curl :8000/api/accounts 看 level；uid_probe.stats()；"
                  "SELECT DISTINCT conv_id FROM dm_conversations WHERE account=?。",
    },
    "SEND": {
        "intent": "所有私信发送过统一闸门（per-account 限速 + 风控配额），"
                  "任何旁路直发都算缺陷。",
        "invariant": "① 发送成功必须有 DB 落库 role=me 佐证；② dm_dispatch 不可用时"
                     "宁可不发，绝不绕过闸门直发。",
        "chain": "调用方(直播/采集/AI/手动) → recv_daemon /send*（闸门）→ imapi "
                 "create_conversation → message/send → 响应 KICK/INVALID_REQUEST → DB 落库。",
        "verify": "DB SELECT role,msg_type,ts FROM dm_messages WHERE conv_id=? ORDER BY ts "
                  "DESC LIMIT 3 必须见 role=me；日志看 rate_limited/KICK 原文"
                  "（KICK 是账号信誉，非代码）。",
    },
    "SYS": {
        "intent": "启动/守护/路由的基础设施层：保证 backend、recv_daemon、BCC 在应用"
                  "可用前就绪，且进程可被正确清理（不残留孤儿）。",
        "invariant": "① BCC 只走 /quit 优雅退出，绝不强杀；② sidecar 二进制必须在"
                     "应用根目录（禁止 binaries/）；③ 版本五处一致（tauri.conf 为真源）。",
        "chain": "主程序(Tauri) → backend spawn → daemon_launcher → ensure_daemons_for "
                 "→ 各 sidecar。",
        "verify": "curl :8000/api/version 比对 :11231/status.version；进程路径核对。",
    },
    "RECV": {
        "intent": "私信接收守护：WS 长连接把新消息实时落库，是「新消息/AI 回复/未读」"
                  "的数据源。",
        "invariant": "① 方向只能用 sender UID 判定；② msg_type=7 且 msg_id 为空的系统"
                     "占位不入库；③ 【v0.43.36 更正】WS 保活在**应用层**（PushFrame "
                     "payloadType=hb），**绝不用** websocket-client 的 ping_interval/"
                     "ping_timeout —— frontier-im 不回 Pong，那两个参数会造成 30s "
                     "定时自杀；④ 生命周期由 daemon/ws_link.py 单循环掌管，回调只置"
                     "状态、绝不自行重连；⑤ 重连后必须按节流补拉（WS 不重推历史）。",
        "chain": "WSLink 建 WS（每次重连重载凭证）→ 应用层 hb 保活 → 服务端推 → "
                 "_handle → 落 dm_messages → （B机制）回填会话昵称 → 前端轮询读库；"
                 "断连 → 退避重连 → 建连成功回调触发追赶补拉。",
        "verify": "日志 WS 断连计数（稳态 2 分钟应 0）；DB 新消息 ts 增量；"
                  "/status 的 connected/conv_count **与 link{connects,hb_sent,"
                  "last_rx_age,backoff_stage}**；"
                  "回归脚本 backend/daemon/verify_ws_link.py 与 verify_ws_ab.py。",
    },

    # 能力探针域（2026-09-21 P2 落地：此前 M1 探针 0 个，见 工作记忆/02_效果定义与探针.md）
    "PROBE": {
        "intent": "能力探针（M1）：回答「现在这个能力行不行？」，输出**三态**"
                  "（healthy/degraded/failed/unknown）+ 覆盖率 + 置信度 + 硬证据 + 基线差值（M7）。",
        "invariant": "① 禁止二态：部分成功必须判 degraded 并带覆盖率；② 报 healthy 时 "
                     "evidence 不得为空（无证据不得报健康）；③ 探针**只读本地事实**"
                     "（SQLite + 本项目日志），绝不发起网络请求、绝不触碰/启动浏览器；"
                     "④ 无法判定时判 unknown，绝不用 0 或推断值冒充 healthy。",
        "chain": "触发（GET /api/probe/run）→ services/probe.run_probes → 各能力探针 → "
                 "只读 DB（dm_conversations/dm_messages）+ 只读 logs → 判定 → kv 基线比对（M7）。"
                 "源头是「本地事实是否有该能力的近期成功记录」，不是「代码写得对不对」。",
        "verify": "curl :8000/api/probe/run（401=已注册）；"
                  "人为制造失效（如改名 DB 路径/断网）后 probe 必须先于用户报 degraded/failed；"
                  "对照 02_效果定义与探针.md §2 的覆盖率阈值。",
    },
}

# ---- 全量代码注册表（由扫描脚本生成，meaning 取自原日志文本）----
ERRCODES = {
    "ACC-001": {"meaning": "scan] 账号  扫码异常:", "file": "api/accounts.py", "line": 194},
    "ACC-002": {"meaning": "self-check] 账号  校验异常:", "file": "api/accounts.py", "line": 392},
    "ACC-003": {"meaning": "check] 账号  校验异常:", "file": "api/accounts.py", "line": 438},
    "ACC-004": {"meaning": "accounts] add_account 失败:", "file": "api/accounts.py", "line": 650},
    "ACC-005": {"meaning": "accounts] remove_account 失败:", "file": "api/accounts.py", "line": 660},
    "ACC-006": {"meaning": "accounts] 保存账号索引失败:", "file": "auto_dm/accounts.py", "line": 120},
    "ACC-007": {"meaning": "verify] 账号  uid 漂移：探活 uid= 不存在于该账号  条历史会话中，凭证身份存疑（疑似身份被轮换/替换）。", "file": "auto_dm/accounts.py", "line": 434},
    "ACC-008": {"meaning": "verify] 账号  自动重捕触发失败:", "file": "auto_dm/accounts.py", "line": 523},
    "ACC-009": {"meaning": "账号] 清空凭证失败 :", "file": "auto_dm/accounts.py", "line": 730},
    "ACC-010": {"meaning": "recap] 账号  发起自动重捕获失败:", "file": "auto_dm/accounts.py", "line": 1042},
    "ACC-011": {"meaning": "recap] 账号  停止凭证守护失败（可能未运行）:", "file": "auto_dm/accounts.py", "line": 1056},
    "ACC-012": {"meaning": "recap] 账号  自动重新捕获异常:", "file": "auto_dm/accounts.py", "line": 1065},
    "ACC-013": {"meaning": "recap-profile] 账号  停止凭证守护失败（可能未运行）:", "file": "auto_dm/accounts.py", "line": 1124},
    "ACC-014": {"meaning": "recap-profile] 账号  读取被风控验证页污染，已拒绝写盘，指纹浏览器保持打开请手动处理验证码:", "file": "auto_dm/accounts.py", "line": 1137},
    "ACC-015": {"meaning": "recap-profile] 账号  从 profile 读取凭证失败:", "file": "auto_dm/accounts.py", "line": 1143},
    "ACC-016": {"meaning": "recap-profile] 账号  重读异常:", "file": "auto_dm/accounts.py", "line": 1151},
    "ACC-022": {"meaning": "recap] 账号  未取得 profile 所有权锁（降级不加锁，存在多实例风险）:", "file": "auto_dm/accounts.py", "line": 0},
    "ACC-023": {"meaning": "recap-profile] 账号  未取得 profile 所有权锁（降级不加锁，存在多实例风险）:", "file": "auto_dm/accounts.py", "line": 0},
    "ACC-024": {"meaning": "scan] 账号  未取得 profile 所有权锁（降级不加锁，存在多实例风险）:", "file": "api/accounts.py", "line": 0},
    "ACC-025": {"meaning": "open-browser] 账号  凭证失效 → 暂停全部任务并打开有头浏览器供观测/重新授权", "file": "api/accounts.py", "line": 0},
    "ACC-026": {"meaning": "open-browser] 账号  引擎暂停失败（不阻塞打开浏览器）:", "file": "api/accounts.py", "line": 0},
    # 2026-09-22 校正（D-05）：原此处与文件尾部一条**同键**六段契约冲突（后写覆盖 → 丢 meaning），
    # 且尾部那条缺 meaning → lookup() 的 c["meaning"] 抛 KeyError → all_codes()/`/api/errcodes` 500。
    # 现六段契约统一归 CODE_DESIGN（单一宿主），此处只留 meaning/file/line 三字段。
    "ACC-017": {"meaning": "捕获分析] 报告落盘失败:", "file": "login_capture.py", "line": 227},
    "AI-001": {"meaning": "ai-kb-import] 解析异常:", "file": "api/ai.py", "line": 155},
    "AI-002": {"meaning": "ai] 语义缓存重建失败:", "file": "api/ai.py", "line": 201},
    "AI-003": {"meaning": "ai] 语义缓存重建异常:", "file": "api/ai.py", "line": 203},
    "AI-004": {"meaning": "ai] kv 读失败 :", "file": "services/ai_reply.py", "line": 80},
    "AI-005": {"meaning": "ai] embeddings :", "file": "services/ai_reply.py", "line": 319},
    "AI-006": {"meaning": "ai] embeddings 返回数不符: /", "file": "services/ai_reply.py", "line": 324},
    "AI-007": {"meaning": "ai] embeddings 调用失败:", "file": "services/ai_reply.py", "line": 330},
    "AI-008": {"meaning": "ai] AI 请求失败:", "file": "services/ai_reply.py", "line": 505},
    "AI-009": {"meaning": "ai] AI API :", "file": "services/ai_reply.py", "line": 532},
    "AI-010": {"meaning": "ai] AI 返回为空:", "file": "services/ai_reply.py", "line": 544},
    "AI-011": {"meaning": "ai] 检测到思考过程被截断输出（finish=length），丢弃改兜底", "file": "services/ai_reply.py", "line": 551},
    "AI-012": {"meaning": "ai] AI API :", "file": "services/ai_reply.py", "line": 574},
    "AI-013": {"meaning": "ai] AI 返回为空:", "file": "services/ai_reply.py", "line": 585},
    "AI-014": {"meaning": "ai] 视觉 API :", "file": "services/ai_reply.py", "line": 624},
    "AI-015": {"meaning": "ai] 视觉请求失败:", "file": "services/ai_reply.py", "line": 635},
    "AI-016": {"meaning": "ai] 线索写入失败:", "file": "services/ai_reply.py", "line": 690},
    "AI-017": {"meaning": "ai] 监听 tick 异常:", "file": "services/ai_reply.py", "line": 803},
    "AI-018": {"meaning": "ai] 单条处理异常:", "file": "services/ai_reply.py", "line": 835},
    "AI-019": {"meaning": "ai] 回复疑似思考过程残留，丢弃改兜底:", "file": "services/ai_reply.py", "line": 924},
    "AI-020": {"meaning": "ai] 图片解密失败:", "file": "services/ai_reply.py", "line": 959},
    "AI-021": {"meaning": "ai] 图片解密未成功:", "file": "services/ai_reply.py", "line": 962},
    "AI-022": {"meaning": "ai] inline base64 解码失败:", "file": "services/ai_reply.py", "line": 982},
    "AI-023": {"meaning": "ai] 入池被拒:", "file": "services/ai_reply.py", "line": 1038},
    "AI-024": {"meaning": "ai] 调度入池异常:", "file": "services/ai_reply.py", "line": 1042},
    "AI-025": {"meaning": "ai] WS 通道失败:", "file": "services/ai_reply.py", "line": 1052},
    "AI-026": {"meaning": "ai] WP 通道失败:", "file": "services/ai_reply.py", "line": 1060},
    "AI-027": {"meaning": "ai] WP 通道异常:", "file": "services/ai_reply.py", "line": 1062},
    "AUTH-001": {"meaning": "auth] 无法导入基座 DYLoginApi，跳过签名补全:", "file": "auth_helper.py", "line": 38},
    "AUTH-002": {"meaning": "auth] 请先安装依赖：pip install aiohttp（基座 login_api 需要）", "file": "auth_helper.py", "line": 39},
    "AUTH-003": {"meaning": "auth] 加载  失败:", "file": "auth_helper.py", "line": 51},
    "AUTH-004": {"meaning": "auth] BCC /scan_login 返回 ok 但 .env 无 cookie，退回直开浏览器", "file": "auth_helper.py", "line": 88},
    "AUTH-005": {"meaning": "auth] BCC /scan_login 返回失败: ，退回直开浏览器", "file": "auth_helper.py", "line": 90},
    "AUTH-006": {"meaning": "auth] BCC /scan_login 异常，退回直开浏览器:", "file": "auth_helper.py", "line": 92},
    "AUTH-007": {"meaning": "auth] 获取登录凭证失败:", "file": "auth_helper.py", "line": 123},
    "AUTH-008": {"meaning": "auth] 未能解析自身 uid（create_conversation 将失败，请确认登录 cookie 含 uid_tt/sid_tt）", "file": "auth_helper.py", "line": 179},
    "AUTH-009": {"meaning": "auth] 导入依赖失败:", "file": "auth_helper.py", "line": 218},
    "AUTH-010": {"meaning": "auth] 当前账号无可用的登录态，请在「账号管理」完成登录。", "file": "auth_helper.py", "line": 243},
    "AUTH-011": {"meaning": "auth] 凭证年龄=s(> s)，视为非实时会话，强制重扫以避免弹幕昵称被加密。", "file": "core/auto_dm.py", "line": 179},
    "AUTH-012": {"meaning": "auth] 未检测到 DY_COOKIES，将自动打开浏览器扫码登录获取。", "file": "core/auto_dm.py", "line": 209},
    "AUTH-013": {"meaning": "auth] 私信签名三件套缺失(ticket/client_cert/private_key)。        请删除 .env 中 DY_TICKET/DY", "file": "core/auto_dm.py", "line": 229},
    "AUTH-014": {"meaning": "auth] 无法获取自身 uid（cookie 可能已失效），请对该账号执行【重新扫码】后再启动。", "file": "core/auto_dm.py", "line": 239},
    "AUTH-015": {"meaning": "auth] 私信签名预检被拒（create_conversation 返回 INVALID_REQUEST/KICK）。        建议点【重新扫码】重新", "file": "core/auto_dm.py", "line": 249},
    "AUTH-016": {"meaning": "auth] 登录态校验失败（create_conversation 报未登录），cookie 可能已失效。", "file": "core/auto_dm.py", "line": 254},
    "AUTH-017": {"meaning": "auth] 私信签名预检异常:", "file": "core/auto_dm.py", "line": 258},
    "AUTH-018": {"meaning": "auth] 监测账号未获取到登录 cookie，无法监听。", "file": "core/auto_dm.py", "line": 442},
    "AUTH-019": {"meaning": "auth] 发送账号未获取到登录 cookie，无法发私信。", "file": "core/auto_dm.py", "line": 463},
    "AUTH-020": {"meaning": "auth] 发送账号私信签名缺失（DY_TICKET/DY_PRIVATE_KEY 为空）。请在该账号下点重新扫码完成一次登录。", "file": "core/auto_dm.py", "line": 468},
    "AUTH-021": {"meaning": "auth] 私信签名三件套缺失(ticket/client_cert/private_key)。请删除 .env 中的 DY_TICKET/DY_TS_SIG", "file": "core/sender.py", "line": 192},
    "AUTH-022": {"meaning": "im] 分页拉取会话列表第  页失败:", "file": "dy_apis/douyin_api.py", "line": 1930},
    "AUTH-023": {"meaning": "im] get_im_user_info uid= status=", "file": "dy_apis/douyin_api.py", "line": 2089},
    "AUTH-024": {"meaning": "私信] 文案为空，拒绝发送（避免日志显示成功但实际未发送）", "file": "dy_apis/douyin_api.py", "line": 2119},
    "AUTH-025": {"meaning": "私信] 文案长度  超过 500 字，可能被平台截断，仅前 500 字发送", "file": "dy_apis/douyin_api.py", "line": 2123},
    "AUTH-026": {"meaning": "私信发送 HTTP :", "file": "dy_apis/douyin_api.py", "line": 2146},
    "AUTH-027": {"meaning": "私信发送被抖音拒绝（JSON 响应）：decision= | full=", "file": "dy_apis/douyin_api.py", "line": 2159},
    "AUTH-028": {"meaning": "私信发送响应 protobuf 解析失败:  | raw[:120]=", "file": "dy_apis/douyin_api.py", "line": 2165},
    "AUTH-029": {"meaning": "私信发送失败 conversation_id= resp_json=", "file": "dy_apis/douyin_api.py", "line": 2173},
    "AUTH-030": {"meaning": "bcc-client] 端口  上运行的 BCC 属于账号「」而非「」（端口被占用/手动启动绕过哈希），按未运行处理，拒绝跨账号取 cookie。", "file": "dy_apis/login_api.py", "line": 51},
    "AUTH-031": {"meaning": "auth] 生成初始数据：新开标签页打开私信落地页()失败:", "file": "dy_apis/login_api.py", "line": 140},
    "AUTH-032": {"meaning": "auth] 新开标签页打开私信落地页()失败（将继续重试）:", "file": "dy_apis/login_api.py", "line": 263},
    "AUTH-033": {"meaning": "风控] 检测到验证码/风控验证页（）。【指纹浏览器保持打开】，请在其中手动完成验证码/滑块验证，完成后程序将自动继续抓取凭证；无需重新扫码。", "file": "dy_apis/login_api.py", "line": 307},
    "AUTH-034": {"meaning": "auth] 打开抖音首页失败(第次):", "file": "dy_apis/login_api.py", "line": 363},
    "AUTH-035": {"meaning": "auth] 已登录会话签名未就绪，降级为重新扫码", "file": "dy_apis/login_api.py", "line": 403},
    "AUTH-036": {"meaning": "auth] BCC /cookie 返回失败: ，退回直开浏览器", "file": "dy_apis/login_api.py", "line": 600},
    "AUTH-037": {"meaning": "auth] profile 刷新 cookie：打开 chat 页失败，退回原凭证", "file": "dy_apis/login_api.py", "line": 626},
    "AUTH-038": {"meaning": "auth] profile 刷新 cookie：profile 内无登录态（无 sessionid）", "file": "dy_apis/login_api.py", "line": 640},
    "AUTH-039": {"meaning": "auth] BCC /user_info 返回失败: ，退回直开浏览器", "file": "dy_apis/login_api.py", "line": 689},
    "AUTH-040": {"meaning": "auth] 批量查昵称：打开 chat 页失败", "file": "dy_apis/login_api.py", "line": 713},
    "AUTH-041": {"meaning": "auth] 批量查昵称：未落在 douyin.com 域，跳过", "file": "dy_apis/login_api.py", "line": 716},
    "AUTH-042": {"meaning": "auth] 批量查昵称 evaluate 失败:", "file": "dy_apis/login_api.py", "line": 741},
    "AUTH-043": {"meaning": "auth] 浏览器批量查昵称失败:", "file": "dy_apis/login_api.py", "line": 757},
    "AUTH-044": {"meaning": "auth] 已有凭证但校验失败，将重新扫码:", "file": "dy_apis/login_api.py", "line": 792},
    "AUTH-045": {"meaning": "auth] 登录 cookie 存在但私信签名缺失，将重新扫码以抓取 web_protect/keys", "file": "dy_apis/login_api.py", "line": 794},
    "AUTH-046": {"meaning": "风控] （指纹浏览器保持打开，请在其中手动处理验证码/滑块）", "file": "dy_apis/login_api.py", "line": 807},
    "AUTH-047": {"meaning": "auth] 新开标签页打开私信落地页()失败（将继续重试）:", "file": "dy_apis/login_api.py", "line": 898},
    "AUTH-048": {"meaning": "auth] 打开抖音首页失败(第次):", "file": "dy_apis/login_api.py", "line": 908},
    "AUTH-049": {"meaning": "风控] 检测到验证码/风控验证页（）。【指纹浏览器保持打开】，请在其中手动完成验证码/滑块验证，完成后程序将自动继续读取凭证；在您验证通过、页面离开风控页之前", "file": "dy_apis/login_api.py", "line": 946},
    "AUTH-050": {"meaning": "uid-probe] 账号「」探活 uid= 与该账号历史会话不一致 —— 判为陈旧/不可信，不缓存（真实 uid 以 conv_id 为准）", "file": "services/uid_probe.py", "line": 193},
    "AUTH-051": {"meaning": "uid-probe] 账号「」探活失败（s 内不再重试）", "file": "services/uid_probe.py", "line": 202},
    "BCC-001": {"meaning": "open-browser] 账号  打开指纹浏览器异常:", "file": "api/accounts.py", "line": 221},
    "BCC-002": {"meaning": "open-browser] 停止守护  失败（可能已退出）:", "file": "api/accounts.py", "line": 472},
    "BCC-003": {"meaning": "open-browser] 账号  发现并清理  个持有 profile 的孤儿浏览器进程（端口  已死但锁未释放）", "file": "api/accounts.py", "line": 481},
    "BCC-004": {"meaning": "open-browser] 清理孤儿 profile 持有进程失败:", "file": "api/accounts.py", "line": 541},
    "BCC-005": {"meaning": "bcc] 打开 chat 页失败（不阻塞，后续接口自愈）:", "file": "daemon/browser_daemon.py", "line": 331},
    "BCC-006": {"meaning": "bcc] context/page 失活，重启:", "file": "daemon/browser_daemon.py", "line": 343},
    "BCC-007": {"meaning": "bcc] 批量查昵称 evaluate 失败:", "file": "daemon/browser_daemon.py", "line": 439},
    "BCC-008": {"meaning": "bcc] 批量查昵称(uid) evaluate 失败:", "file": "daemon/browser_daemon.py", "line": 497},
    "BCC-009": {"meaning": "bcc] wp_send_text 失败:", "file": "daemon/browser_daemon.py", "line": 630},
    "BCC-010": {"meaning": "bcc] wp_send_text 失败:", "file": "daemon/browser_daemon.py", "line": 634},
    "BCC-011": {"meaning": "bcc] 读取 hook 结果失败:", "file": "daemon/browser_daemon.py", "line": 766},
    "BCC-012": {"meaning": "bcc] 读 wp message 失败:", "file": "daemon/browser_daemon.py", "line": 800},
    "BCC-013": {"meaning": "bcc] 导航 tab 创建失败（退回主 page）:", "file": "daemon/browser_daemon.py", "line": 839},
    "BCC-014": {"meaning": "bcc] 账号「」裸探活 uid= 与历史会话不一致，判为不可信（拒绝返回）", "file": "daemon/browser_daemon.py", "line": 978},
    "BCC-015": {"meaning": "bcc] 拒绝写入 .env：新 cookie 探活失败（无 uid），保留既有凭证。疑似 profile 登录态失效，请重新扫码。", "file": "daemon/browser_daemon.py", "line": 1064},
    "BCC-016": {"meaning": "bcc] 拒绝写入 .env：探活 uid= 与该账号「」历史会话不一致（幽灵 uid，0 命中）。profile 登录态疑似失效/残留他人凭证，请重新扫码登", "file": "daemon/browser_daemon.py", "line": 1077},
    "BCC-017": {"meaning": "bcc] 拒绝写入 .env：uid 漂移！old= new=。疑似账号身份被轮换/替换，保留既有凭证并告警。", "file": "daemon/browser_daemon.py", "line": 1096},
    "BCC-018": {"meaning": "bcc] 写回 .env 失败:", "file": "daemon/browser_daemon.py", "line": 1107},
    "BCC-019": {"meaning": "bcc] uid 漂移！old= new=，凭证身份存疑，触发自动刷新…", "file": "daemon/browser_daemon.py", "line": 1150},
    "BCC-020": {"meaning": "bcc] uid 漂移后自动刷新失败:", "file": "daemon/browser_daemon.py", "line": 1161},
    "BCC-021": {"meaning": "bcc] 页面仍需重激活（conv=），scan_login 已熔断（连续失败  次）， 分钟内不再自动重启浏览器，请在指纹浏览器完成扫码登录", "file": "daemon/browser_daemon.py", "line": 1172},
    "BCC-022": {"meaning": "bcc] 页面级登录态失效（conv= rel=），uid= 仍有效但页面需重新激活，触发 scan_login…", "file": "daemon/browser_daemon.py", "line": 1178},
    "BCC-023": {"meaning": "bcc] 页面重激活失败:", "file": "daemon/browser_daemon.py", "line": 1189},
    "BCC-024": {"meaning": "bcc] scan_login 连续失败  次，熔断  分钟。session 疑似服务端已失效，自动登录救不回，请在指纹浏览器重新扫码；期间仅告警不重启浏览器", "file": "daemon/browser_daemon.py", "line": 1193},
    "BCC-025": {"meaning": "bcc] 登录态失效，自动刷新凭证…", "file": "daemon/browser_daemon.py", "line": 1236},
    "BCC-026": {"meaning": "bcc] 自动刷新凭证失败:", "file": "daemon/browser_daemon.py", "line": 1244},
    "BCC-027": {"meaning": "bcc] 探活异常:", "file": "daemon/browser_daemon.py", "line": 1246},
    "BCC-028": {"meaning": "bcc] 浏览器容器启动失败（后续接口会自愈）:", "file": "daemon/browser_daemon.py", "line": 1282},
    "BCC-029": {"meaning": "bcc] 昵称缓存预热失败（不影响功能）:", "file": "daemon/browser_daemon.py", "line": 1312},
    "BCC-030": {"meaning": "bcc] /capture_userinfo 失败:", "file": "daemon/browser_daemon.py", "line": 1370},
    "BCC-031": {"meaning": "bcc] /user_info_by_uids 失败:", "file": "daemon/browser_daemon.py", "line": 1390},
    "BCC-032": {"meaning": "bcc] /exec_js 失败:", "file": "daemon/browser_daemon.py", "line": 1437},
    "BCC-033": {"meaning": "bcc] /wp_messages 失败:", "file": "daemon/browser_daemon.py", "line": 1458},
    "BCC-034": {"meaning": "bcc] /wp_send 失败:", "file": "daemon/browser_daemon.py", "line": 1488},
    "BCC-035": {"meaning": "vbrowser] 窗口归位检查失败（不阻塞启动）:", "file": "vbrowser.py", "line": 160},
    "BCC-036": {"meaning": "vbrowser] 窗口归位检查失败（不阻塞启动）:", "file": "vbrowser.py", "line": 194},
    "BCC-037": {"meaning": "vbrowser] 账号  （按无代理继续）", "file": "vbrowser.py", "line": 382},
    "BCC-038": {"meaning": "vbrowser] 账号未配 DY_PROXY，但检测到 Windows 系统代理指向已死端口——本次启动加 --no-proxy-server 直连（不动系", "file": "vbrowser.py", "line": 396},
    "BCC-039": {"meaning": "vbrowser] DY_APP_ROOT 指向的目录不存在，忽略:", "file": "vbrowser.py", "line": 482},
    "BCC-040": {"meaning": "vbrowser] 调用启动 API 失败（服务未启动？）:", "file": "vbrowser.py", "line": 542},
    "BCC-041": {"meaning": "vbrowser] 启动环境失败:", "file": "vbrowser.py", "line": 545},
    "BCC-042": {"meaning": "vbrowser] 响应缺少 debuggingPort:", "file": "vbrowser.py", "line": 549},
    "BCC-043": {"meaning": "web_probe] 未配置 WEB_PROBE_ROOM_URL，跳过中控台采集", "file": "web_probe.py", "line": 96},
    "BCC-044": {"meaning": "web_probe] 中控台采集无法启动（已禁用原生 Playwright，不采集）：", "file": "web_probe.py", "line": 108},
    "BCC-045": {"meaning": "web_probe] 扫描异常:", "file": "web_probe.py", "line": 126},
    "BCC-046": {"meaning": "[lease] 租约超时强制释放（持有者未 release）", "file": "daemon/browser_daemon.py", "line": 0},
    "BCC-047": {"meaning": "[lease] 租约冲突被拒（holder=, prio=）", "file": "daemon/browser_daemon.py", "line": 0},
    "BCC-048": {"meaning": "[lease] renew 超过该优先级 TTL 上限被拒", "file": "daemon/browser_daemon.py", "line": 0},
    "BCC-049": {"meaning": "[lease] 持有者不匹配或未持租约", "file": "daemon/browser_daemon.py", "line": 0},
    "BCC-050": {"meaning": "[bcc] 单例守卫命中：该账号已有 BCC 在运行，拒绝启动第二个", "file": "daemon/browser_daemon.py", "line": 0},
    "BCC-051": {"meaning": "[bcc] 致命态熔断：凭证不可用（env_path 为空），不再自动重启", "file": "daemon/browser_daemon.py", "line": 0},
    "BCC-052": {"meaning": "[bcc] context 失活自愈：已重建容器（按最小化启动，窗口不再快闪）", "file": "daemon/browser_daemon.py", "line": 0},
    "BCC-053": {"meaning": "[bcc] 可见性切换完成后 OS 层未检测到可见窗口（切换未达成，状态回退为无头）", "file": "daemon/browser_daemon.py", "line": 0},
    "BCC-054": {"meaning": "[gate] 获取账号 profile 所有权超时（已有其它操作持有，放弃本次独占）", "file": "services/browser_gate.py", "line": 0},
    "BCC-055": {"meaning": "[bcc] 有头观测态下探活失败 —— 不自动重建（防销毁用户窗口+新环境访问触发风控）", "file": "daemon/browser_daemon.py", "line": 0},
    "BCC-057": {"meaning": "[bcc] 有头观测态下不触发自动重扫（防销毁用户窗口+新环境访问触发风控）", "file": "daemon/browser_daemon.py", "line": 0},
    "BCC-058": {"meaning": "[vbrowser] Camoufox 内核启动失败，已回退 Chromium", "file": "vbrowser.py", "line": 0},
    "BCC-070": {"meaning": "[vbrowser] Camoufox 已启用但浏览器内核不可用（camoufox fetch 未安装）", "file": "vbrowser.py", "line": 0},
    "BCC-063": {"meaning": "[bcc] 出口环境漂移：当前出口 IP 与登录基线不一致（登录环境与运行环境不一致，先重扫建立新基线）", "file": "daemon/browser_daemon.py", "line": 0},
"BCC-064": {"meaning": "[bcc] 环境泄漏监测发现异常（rebrowser/CreepJS/liarjs 检测逻辑内置探针）", "file": "daemon/browser_daemon.py", "line": 0},
"BCC-065": {"meaning": "[bcc] 自动化痕迹暴露（navigator.webdriver=true / HeadlessChrome UA / 注入对象）", "file": "services/env_audit.py", "line": 0},
"BCC-066": {"meaning": "[bcc] 原生函数被篡改（toString 不含 [native code]）", "file": "services/env_audit.py", "line": 0},
"BCC-067": {"meaning": "[bcc] WebGL 软件渲染特征（GPU 伪装失效，检查 --disable-gpu）", "file": "services/env_audit.py", "line": 0},
"BCC-068": {"meaning": "[bcc] 浏览器环境与项目档案不一致（时区/语言/核心数/screen/UA）", "file": "services/env_audit.py", "line": 0},
"BCC-069": {"meaning": "[bcc] 环境一致性弱点（screen 三值全等/worker 分叉/canvas 或 audio 不稳定/Client Hints 缺失）", "file": "services/env_audit.py", "line": 0},
    "SEND-037": {"meaning": "[调度] dm_dispatch 接入失败，已放弃发送（不再回退直发绕过风控闸门）", "file": "core/dispatch.py", "line": 0},
    "SEND-038": {"meaning": "[调度] gen_dm_message（AI 文案）异常，回落词库", "file": "core/dispatch.py", "line": 0},
    "SEND-039": {"meaning": "[调度] AI 文案接线判定失败，回落词库", "file": "core/auto_dm.py", "line": 0},
    "SEND-040": {"meaning": "[调度] AI 文案生成失败，回落词库", "file": "core/auto_dm.py", "line": 0},
    # 2026-09-22 搬迁（D-05）：ACC-017~ACC-021 的六段契约原错放在本字典内部 ——
    # ① ACC-017 与其单行记录同键冲突（后写覆盖 → 丢 meaning → lookup()/all_codes() 抛 KeyError）；
    # ② 其余 4 条只有六段而无 meaning；③ 六段契约的单一宿主应为 CODE_DESIGN（见该字典尾部）。
"CAP-001": {"meaning": "refresh][] browser_daemon 未拉起，昵称关联可能失效", "file": "api/messages.py", "line": 735},
    "CAP-002": {"meaning": "refresh][] 更新会话失败:", "file": "api/messages.py", "line": 749},
    "CAP-003": {"meaning": "capture] my_uid 无效()，从  个 conv_id 自愈推断本账号 UID=（出现  次）", "file": "auto_dm/conversation_capture.py", "line": 526},
    "CAP-004": {"meaning": "capture][301] HTTP  len= cid=", "file": "auto_dm/conversation_capture.py", "line": 759},
    "CAP-005": {"meaning": "capture][301] 拉取失败 cid=:", "file": "auto_dm/conversation_capture.py", "line": 764},
    "CAP-006": {"meaning": "capture][301] 解析失败:", "file": "auto_dm/conversation_capture.py", "line": 793},
    "CAP-007": {"meaning": "capture] 调 BCC /capture_userinfo 失败:", "file": "auto_dm/conversation_capture.py", "line": 1037},
    "CAP-008": {"meaning": "capture][] 加载凭证失败:", "file": "auto_dm/conversation_capture.py", "line": 1087},
    "CAP-009": {"meaning": "capture][] 首包解析失败:", "file": "auto_dm/conversation_capture.py", "line": 1104},
    "CAP-010": {"meaning": "capture][] 301 补全失败 :", "file": "auto_dm/conversation_capture.py", "line": 1211},
    "CAP-011": {"meaning": "capture][] 长会话补全失败（降级仅首包）:", "file": "auto_dm/conversation_capture.py", "line": 1218},
    "CAP-012": {"meaning": "capture][] 浏览器昵称捕获失败（降级仅首包）:", "file": "auto_dm/conversation_capture.py", "line": 1226},
    "CAP-013": {"meaning": "capture][] 存量污染订正失败:", "file": "auto_dm/conversation_capture.py", "line": 1389},
    "CAP-014": {"meaning": "capture][] 兜底补全失败:", "file": "auto_dm/conversation_capture.py", "line": 1427},
    "CAP-015": {"meaning": "capture][] 写库失败:", "file": "auto_dm/conversation_capture.py", "line": 1429},
    "CAP-017": {"meaning": "capture][] 释放跨调用窗口租约失败（等 TTL 回收）:", "file": "auto_dm/conversation_capture.py", "line": 0},
    "CRAWL-001": {"meaning": "crawl] 采集历史落库失败（不影响本次结果）:", "file": "api/crawl.py", "line": 170},
    "CRAWL-002": {"meaning": "crawl] 搜索失败 account= q=:", "file": "api/crawl.py", "line": 206},
    "CRAWL-003": {"meaning": "crawl] 评论采集失败 account= aweme=:", "file": "api/crawl.py", "line": 249},
    "CRAWL-004": {"meaning": "crawl] 私信发送异常 account= uid=:", "file": "api/crawl.py", "line": 282},
    "CRAWL-005": {"meaning": "crawl] 批量评论采集失败 account= aweme=:", "file": "api/crawl.py", "line": 339},
    "DB-001": {"meaning": "db] task_history.json 迁移失败（不影响使用）:", "file": "database.py", "line": 266},
    "DB-002": {"meaning": "db] config.json 迁移失败:", "file": "database.py", "line": 288},
    "DB-003": {"meaning": "db] accounts.json 迁移失败:", "file": "database.py", "line": 307},
    "DB-004": {"meaning": "db] /dm_history.json 迁移失败:", "file": "database.py", "line": 354},
    "DB-005": {"meaning": "db] 数据库初始化失败:", "file": "main.py", "line": 279},
    "ENG-001": {"meaning": "直播间状态] get_live_info 返回空，保守视为已开播以免误阻断监听", "file": "core/auto_dm.py", "line": 64},
    "ENG-002": {"meaning": "直播间状态] 查询异常（保守视为已开播）:", "file": "core/auto_dm.py", "line": 75},
    "ENG-003": {"meaning": "history] 记录历史任务失败:", "file": "core/auto_dm.py", "line": 406},
    "ENG-004": {"meaning": "引擎] 启动超时（60s 仍在 STARTING），可能等待开播中", "file": "core/auto_dm.py", "line": 417},
    "ENG-005": {"meaning": "直播间状态] 未开播，进入轮询等待（每 30s 复查）", "file": "core/auto_dm.py", "line": 498},
    "ENG-006": {"meaning": "引擎] 运行异常:", "file": "core/auto_dm.py", "line": 536},
    "ENG-007": {"meaning": "引擎] 启动未成功:", "file": "core/auto_dm.py", "line": 541},
    "ENG-008": {"meaning": "history] 退出收尾历史任务失败（不影响关闭）:", "file": "core/auto_dm.py", "line": 650},
    "ENG-009": {"meaning": "history] 更新历史任务失败:", "file": "core/auto_dm.py", "line": 672},
    "ENG-013": {"meaning": "引擎] 热更被拒：引擎未运行（state=）", "file": "core/auto_dm.py", "line": 0},
    "ENG-014": {"meaning": "引擎] 热更失败：dispatch 未初始化", "file": "core/auto_dm.py", "line": 0},
    "IMG-001": {"meaning": "图床][] 上传失败（降级内联）:", "file": "auto_dm/image_host.py", "line": 184},
    "IMG-002": {"meaning": "origin_image] 写本地失败 :", "file": "auto_dm/origin_image_resolver.py", "line": 326},
    "IMG-003": {"meaning": "origin_image] 图床上传模块导入/调用失败:", "file": "auto_dm/origin_image_resolver.py", "line": 339},
    "LIVE-001": {"meaning": "resolve] 配置落盘失败（不影响本次解析）:", "file": "api/live.py", "line": 154},
    "LIVE-002": {"meaning": "心跳] 登录态探活异常（将自动重新扫码）:", "file": "core/live_hook.py", "line": 216},
    "LIVE-003": {"meaning": "心跳] 登录态失效（get_my_uid 无返回，cookie 可能过期/账号被挤下线）。        自动触发重新扫码以恢复监测账号有效会话，避免弹幕昵称", "file": "core/live_hook.py", "line": 219},
    "LIVE-004": {"meaning": "心跳] 自动重新扫码失败（请手动点【重新扫码】）:", "file": "core/live_hook.py", "line": 244},
    "LIVE-005": {"meaning": "心跳] 未绑定控制器，无法自动重扫，请手动重新扫码。", "file": "core/live_hook.py", "line": 246},
    "LIVE-006": {"meaning": "昵称加密] 检测到昵称加密且 sec_uid 为空（累计  次）。        本会话账号侧判据：        该现象有两种可能来源，仅凭帧内数据不可区分：         ① 监测账号无解密权（凭证被服务端降权）—— 处置见 LIVE-035；         ② 该直播间开启「隐藏观众信息」（房间级开关）——            验证法：换一个已知有解密权的账号进同一房间，若同样脱敏即属此类。", "file": "core/live_hook.py", "line": 0},
    "LIVE-007": {"meaning": "live_hook item error:", "file": "core/live_hook.py", "line": 345},
    "LIVE-008": {"meaning": "live_hook on_message error:", "file": "core/live_hook.py", "line": 347},
    "LIVE-009": {"meaning": "resolve] 跟随重定向失败:", "file": "link_resolve.py", "line": 99},
    "LIVE-010": {"meaning": "resolve] X-Bogus 签名失败:", "file": "link_resolve.py", "line": 127},
    "LIVE-011": {"meaning": "resolve] X-Bogus 签名失败:", "file": "link_resolve.py", "line": 139},
    "LIVE-012": {"meaning": "resolve] reflow 请求失败:", "file": "link_resolve.py", "line": 152},
    "LIVE-013": {"meaning": "resolve] reflow 响应解析失败（可能未开播/接口变更）:", "file": "link_resolve.py", "line": 160},
    "LIVE-014": {"meaning": "resolve] get_live_info 补全 sec_uid 失败:", "file": "link_resolve.py", "line": 202},
    "LIVE-015": {"meaning": "resolve] 主页 HTML 补全 room_id 失败:", "file": "link_resolve.py", "line": 219},
    "LIVE-016": {"meaning": "resolve] BCC /resolve_url 返回失败: ，退回直开浏览器", "file": "link_resolve.py", "line": 262},
    "LIVE-017": {"meaning": "resolve] BCC /resolve_url 异常，退回直开浏览器:", "file": "link_resolve.py", "line": 264},
    "LIVE-018": {"meaning": "resolve] 浏览器解析不可用（已禁用原生 Playwright，跳过浏览器解析）：", "file": "link_resolve.py", "line": 276},
    "LIVE-019": {"meaning": "resolve] 用户  当前未在直播或无法解析房间", "file": "link_resolve.py", "line": 307},
    "LIVE-020": {"meaning": "resolve] 浏览器解析失败:", "file": "link_resolve.py", "line": 309},
    "LIVE-021": {"meaning": "room-config] 热更失败:", "file": "api/live_config.py", "line": 0},
    "LIVE-034": {"meaning": "live-ws] 会话态未知（未探测或探测失败），按「有 cookie」继续", "file": "core/live_hook.py", "line": 0},
    "LIVE-035": {"meaning": "live-identity] 监测账号无直播昵称解密权 —— 弹幕昵称将被脱敏（uid=111111）", "file": "auto_dm/accounts.py", "line": 0},
    "LIVE-036": {"meaning": "live-identity] 登录态探测失败（结论未知，不据此降级）", "file": "auto_dm/accounts.py", "line": 0},
    "LIVE-037": {"meaning": "live-ws] 带凭证进房未获 room_id/异常，回落匿名进房", "file": "dy_live/server.py", "line": 0},


    "MEM-001": {"meaning": "member] 会员 DB 初始化失败:", "file": "api/member.py", "line": 72},
    "MEM-002": {"meaning": "member] .env 迁移异常（不阻塞登录）:", "file": "api/member.py", "line": 79},
    "MEM-003": {"meaning": "member] 登录后守护拉起失败（不影响登录）:", "file": "api/member.py", "line": 90},
    "MEM-004": {"meaning": "member] 加密迁移失败（保留明文）: :", "file": "services/member_ctx.py", "line": 305},
    "MEM-005": {"meaning": "member] 注册表读取失败（返回空表，避免误删）:", "file": "services/member_store.py", "line": 97},
    "MEM-006": {"meaning": "member] 会员已删除（含数据空间）:  ()", "file": "services/member_store.py", "line": 308},
    "MSG-001": {"meaning": "私信拉取] 账号「」库空且守护未运行(port=)", "file": "api/messages.py", "line": 263},
    "MSG-002": {"meaning": "私信拉取] 账号「」读库异常:", "file": "api/messages.py", "line": 301},
    "MSG-003": {"meaning": "私信拉取] 账号「」会话详情异常:", "file": "api/messages.py", "line": 410},
    "NTY-001": {"meaning": "notify] 配置读取失败:", "file": "api/notify.py", "line": 44},
    "NTY-002": {"meaning": "notify] 指令执行失败:", "file": "api/notify.py", "line": 150},
    "NTY-003": {"meaning": "notify] 启动失败（不影响主流程）:", "file": "api/notify.py", "line": 304},
    "NTY-004": {"meaning": "notify] adm 注入失败（指令执行将不可用）:", "file": "main.py", "line": 297},
    "NTY-005": {"meaning": "notify] 模块挂载失败（不影响主流程）:", "file": "main.py", "line": 432},
    "NTY-006": {"meaning": "notify:] 发送异常(/):", "file": "notify/channels.py", "line": 109},
    "NTY-007": {"meaning": "notify:] 发送最终失败:", "file": "notify/channels.py", "line": 112},
    "NTY-008": {"meaning": "notify:wecom] 部分接收人无效:", "file": "notify/channels.py", "line": 289},
    "NTY-009": {"meaning": "cmd-parse] LLM 解析失败，回落规则:", "file": "notify/cmd_parser.py", "line": 157},
    "NTY-010": {"meaning": "notify] 渠道 () 初始化失败:", "file": "notify/notifier.py", "line": 53},
    "NTY-011": {"meaning": "notify] 派发异常:", "file": "notify/notifier.py", "line": 100},
    "NTY-012": {"meaning": "notify]  推送失败:", "file": "notify/notifier.py", "line": 175},
    "RECV-001": {"meaning": "recv][] my_uid 发生轮换： → （已自动更新方向判定基准）", "file": "daemon/recv_daemon.py", "line": 165},
    "RECV-002": {"meaning": "recv][] 数据库加载会话失败:", "file": "daemon/recv_daemon.py", "line": 211},
    "RECV-003": {"meaning": "recv][] 数据库加载会话详情失败:", "file": "daemon/recv_daemon.py", "line": 327},
    "RECV-004": {"meaning": "recv][] 消息持久化失败:", "file": "daemon/recv_daemon.py", "line": 416},
    "RECV-005": {"meaning": "recv][] 消息解析异常:", "file": "daemon/recv_daemon.py", "line": 483},
    "RECV-006": {"meaning": "recv][] WS 错误:", "file": "daemon/recv_daemon.py", "line": 488},
    "RECV-007": {"meaning": "recv][] 启动前移捕获失败（忽略）:", "file": "daemon/recv_daemon.py", "line": 757},
    "RECV-008": {"meaning": "recv] 数据库初始化失败:", "file": "daemon/recv_daemon.py", "line": 772},
    "RECV-009": {"meaning": "recv] 启动捕获注册失败:", "file": "daemon/recv_daemon.py", "line": 789},
    "RECV-010": {"meaning": "recv] 账号  启动失败:", "file": "daemon/recv_daemon.py", "line": 791},
    "RECV-011": {"meaning": "recv][] 加载凭证失败:", "file": "daemon/recv_daemon.py", "line": 850},
    "RECV-012": {"meaning": "recv][] get_message_by_init 返回  字节（非全量，疑似凭证失效/限频）：", "file": "daemon/recv_daemon.py", "line": 857},
    "RECV-013": {"meaning": "recv][] get_message_by_init 失败:", "file": "daemon/recv_daemon.py", "line": 863},
    "RECV-014": {"meaning": "recv][] 刷新实时 cookie 失败（沿用 .env）:", "file": "daemon/recv_daemon.py", "line": 1022},
    "RECV-015": {"meaning": "recv][] 会话 peer_id 已订正:  -> （conv_id 重解析）", "file": "daemon/recv_daemon.py", "line": 1056},
    "RECV-016": {"meaning": "recv][] 发送闸门限流：等待 s 仍未放行（最小间隔 s），快速失败", "file": "daemon/recv_daemon.py", "line": 1073},
    "RECV-017": {"meaning": "recv][] 回复失败原因:", "file": "daemon/recv_daemon.py", "line": 1090},
    "RECV-018": {"meaning": "recv][] 回复失败:", "file": "daemon/recv_daemon.py", "line": 1093},
    "RECV-019": {"meaning": "recv][] 发送闸门限流(by_uid)：等待 s 未放行", "file": "daemon/recv_daemon.py", "line": 1124},
    "RECV-020": {"meaning": "recv][] 直发 uid= 失败:", "file": "daemon/recv_daemon.py", "line": 1142},
    "RECV-021": {"meaning": "recv][] 直发 uid= 异常:", "file": "daemon/recv_daemon.py", "line": 1145},
    "RECV-022": {"meaning": "recv][] 图片发送失败:", "file": "daemon/recv_daemon.py", "line": 1228},
    "RECV-023": {"meaning": "recv][] 图片发送异常:", "file": "daemon/recv_daemon.py", "line": 1231},
    "RECV-024": {"meaning": "wp_recv][] 数据库连接失败:", "file": "daemon/wp_recv.py", "line": 259},
    "RECV-025": {"meaning": "wp_recv][] 轮询异常:", "file": "daemon/wp_recv.py", "line": 360},
    "RECV-030": {"meaning": "recv][] WS 建连异常:", "file": "daemon/ws_link.py", "line": 160},
    "RECV-031": {"meaning": "recv][] 建连后回调失败:", "file": "daemon/ws_link.py", "line": 257},
    "RECV-032": {"meaning": "recv][] 秒未收到任何下行帧，判定半开连接，主动重连", "file": "daemon/ws_link.py", "line": 308},
    "RECV-033": {"meaning": "recv][] 心跳发送失败（第  次）:", "file": "daemon/ws_link.py", "line": 333},
    "RECV-034": {"meaning": "recv][] 重连后追赶补拉失败（不影响收消息）:", "file": "daemon/recv_daemon.py", "line": 854},
    "RECV-037": {"meaning": "recv][] 用户回调异常:", "file": "daemon/ws_link.py", "line": 224},
    "SEND-001": {"meaning": "send][]  通道失败（），自动回退  通道", "file": "api/messages.py", "line": 629},
    "SEND-002": {"meaning": "调度] 硬停止：已清空待发队列，不再发送任何私信", "file": "core/dispatch.py", "line": 106},
    "SEND-003": {"meaning": "调度] on_idle 异常:", "file": "core/dispatch.py", "line": 273},
    "SEND-004": {"meaning": "调度] 停止中，丢弃待发私信「」", "file": "core/dispatch.py", "line": 283},
    "SEND-005": {"meaning": "调度] pick_dm_message 异常:", "file": "core/dispatch.py", "line": 304},
    "SEND-006": {"meaning": "调度] 私信未入池（账号= 目标=）:", "file": "core/dispatch.py", "line": 326},
    "SEND-007": {"meaning": "调度] dm_dispatch 接入失败，回退直发:", "file": "core/dispatch.py", "line": 329},
    "SEND-008": {"meaning": "私信发送结果] 目标「」=失败    原因:     文案:", "file": "core/dispatch.py", "line": 365},
    "SEND-009": {"meaning": "recap] 触发自动重捕获失败:", "file": "core/sender.py", "line": 159},
    "SEND-010": {"meaning": "私信] 文案为空，拒绝发送（避免日志显示成功但实际未发送）", "file": "core/sender.py", "line": 171},
    "SEND-011": {"meaning": "私信] 发送被闸门限流 uid=:", "file": "core/sender.py", "line": 207},
    "SEND-012": {"meaning": "私信] 经 recv_daemon 直发失败 uid=:", "file": "core/sender.py", "line": 209},
    "SEND-013": {"meaning": "私信] recv_daemon 不可达（），兜底本进程直发——发送闸门失效，注意频率风控", "file": "core/sender.py", "line": 212},
    "SEND-014": {"meaning": "私信被风控] create_conversation 被抖音拒绝(uid=):         说明：签名四件套有效（预检已通过），此处 KICK 多为账号级", "file": "core/sender.py", "line": 224},
    "SEND-015": {"meaning": "create_conversation 失败(第次) uid=:", "file": "core/sender.py", "line": 232},
    "SEND-016": {"meaning": "send_msg 失败(第次) uid=:", "file": "core/sender.py", "line": 240},
    "SEND-017": {"meaning": "send_msg 返回 (第次) uid=", "file": "core/sender.py", "line": 248},
    "SEND-018": {"meaning": "私信] 发送失败「」:", "file": "core/sender.py", "line": 285},
    "SEND-019": {"meaning": "img-send] ⑤ batch_build_image HTTP", "file": "dy_apis/image_sender.py", "line": 423},
    "SEND-020": {"meaning": "img-send] ⑤ batch_build_image 失败（忽略）:", "file": "dy_apis/image_sender.py", "line": 432},
    "SEND-021": {"meaning": "img-send] ⑥ 发送失败 conversation_id= resp_json=", "file": "dy_apis/image_sender.py", "line": 531},
    "SEND-022": {"meaning": "img-send] 读取图片尺寸失败（退化 800x600）:", "file": "dy_apis/image_sender.py", "line": 551},
    "SEND-023": {"meaning": "img-send] ④ SourceMd5() 与本地 md5() 不一致，以本地为准", "file": "dy_apis/image_sender.py", "line": 591},
    "SEND-024": {"meaning": "dm-dispatch][] 回执命中频控 → 权重降至 ，强制冷静  分钟（第  次，半衰期 h 后自动恢复）", "file": "services/dm_dispatch.py", "line": 354},
    "SEND-025": {"meaning": "uid-sink] 落库失败（仅内存生效）:", "file": "services/dm_dispatch.py", "line": 487},
    "SEND-026": {"meaning": "dm-dispatch]", "file": "services/dm_dispatch.py", "line": 722},
    "SEND-027": {"meaning": "dm-dispatch]", "file": "services/dm_dispatch.py", "line": 731},
    "SEND-028": {"meaning": "dm-dispatch]", "file": "services/dm_dispatch.py", "line": 744},
    "SEND-029": {"meaning": "dm-dispatch]", "file": "services/dm_dispatch.py", "line": 749},
    "SEND-030": {"meaning": "dm-dispatch][] 陌生人首发被限流:", "file": "services/dm_dispatch.py", "line": 767},
    "SEND-031": {"meaning": "dm-dispatch]", "file": "services/dm_dispatch.py", "line": 819},
    "SEND-032": {"meaning": "dm-dispatch]", "file": "services/dm_dispatch.py", "line": 824},
    "SEND-033": {"meaning": "dm-dispatch][] 陌生人首发被限流(uid直发):", "file": "services/dm_dispatch.py", "line": 846},
    "SEND-034": {"meaning": "dm-dispatch] 发送异常 task=:", "file": "services/dm_dispatch.py", "line": 909},
    "SEND-035": {"meaning": "dm-dispatch] ，已拒绝发送", "file": "services/dm_dispatch.py", "line": 927},
    "SEND-036": {"meaning": "dm-dispatch] 发送失败 task=:", "file": "services/dm_dispatch.py", "line": 967},
    "SYS-001": {"meaning": "daemon-launcher] 未找到 browser_daemon 二进制，跳过 BCC 拉起", "file": "auto_dm/daemon_launcher.py", "line": 98},
    "SYS-002": {"meaning": "daemon-launcher] BCC 未拉起（）——冷静期内或二进制缺失，属预期，不强制拉起", "file": "auto_dm/daemon_launcher.py", "line": 113},
    "SYS-003": {"meaning": "daemon-launcher] 拉起 browser_daemon 失败:", "file": "auto_dm/daemon_launcher.py", "line": 117},
    "SYS-004": {"meaning": "daemon-launcher] 未找到 recv_daemon 二进制，跳过", "file": "auto_dm/daemon_launcher.py", "line": 123},
    "SYS-005": {"meaning": "daemon-launcher] 拉起 recv_daemon 失败:", "file": "auto_dm/daemon_launcher.py", "line": 135},
    "SYS-006": {"meaning": "daemon-launcher] ensure_daemons_for 异常（不影响使用）:", "file": "auto_dm/daemon_launcher.py", "line": 137},
    "SYS-007": {"meaning": "feature]  失败:", "file": "features.py", "line": 19},
    "SYS-008": {"meaning": "startup] 未找到 dyautodm-browser-daemon 二进制，跳过 BCC 拉起", "file": "main.py", "line": 165},
    "SYS-009": {"meaning": "startup] 拉起 browser_daemon 失败:", "file": "main.py", "line": 177},
    "SYS-010": {"meaning": "startup] 未找到 dyautodm-recv-daemon 二进制，跳过 recv_daemon 拉起", "file": "main.py", "line": 186},
    "SYS-011": {"meaning": "startup] 拉起  的 recv_daemon 失败:", "file": "main.py", "line": 197},
    "SYS-012": {"meaning": "startup] 自动拉起 daemon 失败（不影响使用）:", "file": "main.py", "line": 223},
    "SYS-013": {"meaning": "nickname]  昵称关联失败（WS B 机制兜底）:", "file": "main.py", "line": 244},
    "SYS-014": {"meaning": "nickname] 昵称关联流程失败:", "file": "main.py", "line": 246},
    "SYS-015": {"meaning": "warmup] 账号校验缓存预热失败（不影响使用）:", "file": "main.py", "line": 267},
    "SYS-016": {"meaning": "history] 启动收尾悬空任务失败（不影响使用）:", "file": "main.py", "line": 286},
    "SYS-017": {"meaning": "uid-probe] 预热启动失败（不影响使用）:", "file": "main.py", "line": 315},
    "SYS-018": {"meaning": "origin_image] 启动 TTL 清理失败（不影响使用）:", "file": "main.py", "line": 323},
    "SYS-019": {"meaning": "startup] 会员态判断失败，按未登录处理:", "file": "main.py", "line": 335},
    "SYS-020": {"meaning": "startup] WP 接收循环启动失败（不影响 WS 通道）:", "file": "main.py", "line": 353},
    "SYS-021": {"meaning": "startup] AI 自动回复初始化失败（不影响主流程）:", "file": "main.py", "line": 362},
    "TSK-001": {"meaning": "tasks] 读取历史任务失败:", "file": "api/tasks.py", "line": 48},
    "TSK-002": {"meaning": "tasks] 读取任务容器失败:", "file": "api/tasks.py", "line": 65},
    "TSK-003": {"meaning": "tasks] 配置落盘失败（不影响本次保存）:", "file": "api/tasks.py", "line": 173},
    "TSK-004": {"meaning": "tasks] 导出失败:", "file": "api/tasks.py", "line": 256},
    "HUB-001": {"meaning": "model_hub] kv 读取失败:", "file": "services/model_hub.py", "line": 0},
    "HUB-002": {"meaning": "model_hub] kv 写入失败:", "file": "services/model_hub.py", "line": 0},
    "HUB-003": {"meaning": "model_hub] v1 配置迁移失败（不影响运行）:", "file": "services/model_hub.py", "line": 0},
    "AI-030": {"meaning": "ai] model_hub 链路解析失败（用旧配置兜底）:", "file": "services/ai_reply.py", "line": 0},
    "AI-031": {"meaning": "ai] 链路候选失败，切下一个:", "file": "services/ai_reply.py", "line": 0},
    "AI-032": {"meaning": "ai] 视觉候选失败，切下一个:", "file": "services/ai_reply.py", "line": 0},
    "AI-033": {"meaning": "ai] 语义候选失败，切下一个:", "file": "services/ai_reply.py", "line": 0},
    "AI-034": {"meaning": "ai] 语义缓存模型不一致，本次跳过语义级:", "file": "services/ai_reply.py", "line": 0},
    "AUTH-053": {"meaning": "[auth] 抓取到的会话未被服务端承认（重读仍未获承认）", "file": "dy_apis/login_api.py", "line": 0},
    "AUTH-054": {"meaning": "[auth] 凭证加密写盘失败，已拒绝明文降级（凭证未写入）", "file": "dy_apis/login_api.py", "line": 0},
    "MEM-007": {"meaning": "[member] 拒绝写入/读取凭证：主密钥不可用（明文 .env 已废弃，绝不明文落盘）", "file": "services/member_ctx.py", "line": 0},
    "BCC-071": {"meaning": "[bcc] 新 cookie 的会话未被服务端承认（profile/self status_code≠0），不写入 .env", "file": "daemon/browser_daemon.py", "line": 0},
    "BCC-072": {"meaning": "[bcc] 保活回写连续两次会话未获承认，会话需人工重新登录", "file": "daemon/browser_daemon.py", "line": 0},
    "BCC-073": {"meaning": "[bcc] 页面级登录态探针取不到证据（独立 tab 亦不可用/超时）—— 结论未知，不得据以判定页面失效或跳过凭证回写（ENG-020）", "file": "daemon/browser_daemon.py", "line": 0},
}

# ════════════════════════════════════════════════════════════════════════════
# 码级「设计契约 + 偏离 + 溯源链」（2026-09-13 用户要求）
#
# 域级说明"这个域该怎样"；这里说明"这一条报错偏在哪、从哪断的、怎么实机验证"。
# 字段：design(本该做什么) contract(被破的不变式) deviation(实际差异)
#       chain(源头→传播→终端) root(为何在这一环断) verify(实机判据)
#
# ⚠️ 铁律：新增错误码必须至少填 design + verify（无设计契约的报错不许提交）。
# ════════════════════════════════════════════════════════════════════════════
CODE_DESIGN = {
    "ENG-013": {
        "design": "「重启标签」的设计语义（用户 2026-09-15 定调）：把变更后的配置内容"
                  "补进**正在运行**的监听任务，只改配置、不中断监听（不重建 WS、"
                  "不重扫凭证、不清队列）。",
        "contract": "热更只在 RUNNING/PAUSED 态有意义；非运行态必须显式拒绝并提示"
                    "「先开始自动私信」，绝不假装生效。",
        "deviation": "热更被拒：引擎不在 RUNNING/PAUSED",
        "chain": "前端「重启」→ POST /api/live/room-configs/{id}/restart → "
                 "_apply_to_task_kv（写 kv 供下次启动）→ adm.apply_runtime_config",
        "root": "任务未启动 / 已停止 / 启动失败回退 IDLE —— 属调用时序问题，不是配置错",
        "verify": "curl :8000/api/tasks/current 看 engine_state；日志 grep '热更被拒'。",
    },
    "BCC-070": {
        "design": "Camoufox 已成为**唯一**浏览器内核（用户 2026-09-20 拍板彻底移除 vb_chromium）。"
                  "启动层必须把「内核可用」当作硬前置：Camoufox 未安装时宁可显式失败，"
                  "也不得静默回退 Chromium —— 静默降级会让「内核没切过去」变成无声事实"
                  "（本项目已实测踩坑）。",
        "contract": "camoufox_enabled(cfg) 为真时，should_use_vb 只校验 Camoufox 可用性"
                    "（模块可导入 + installed_verstr() 成功），**不再要求 vb_chromium/.../chrome.exe 存在**；不可用即抛 BCC-070，绝不返回可继续的假成功。",
        "deviation": "should_use_vb() 抛 RuntimeError: Camoufox 已启用但浏览器不可用",
        "chain": "browser_daemon._launch / login_api×5 / link_resolve / web_probe → should_use_vb(cfg) → camoufox_enabled → installed_verstr",
        "root": "camoufox 包已装但未执行 `camoufox fetch`（浏览器本体缺失）；"
                "或产物未打入 camoufox hidden-import（打包态 ModuleNotFoundError）。",
        "verify": "源码态: python -c 'from camoufox.pkgman import installed_verstr; print(installed_verstr())' 应打印内核版本；实机: 启动后日志应出现 '[vbrowser] 内核=Camoufox'，且无 BCC-070。",
    },
    "BCC-064": {
        "design": "把 rebrowser-bot-detector / CreepJS / liarjs 的检测逻辑"
                  "移植为内置只读 JS 探针（零外网请求、零第三方上传），"
                  "keepalive 低频巡检 + /env_audit 即时快照，环境泄漏在"
                  "被风控判罚之前暴露。",
        "contract": "探针零外网请求、零主动抖音请求、不导航不点 DOM；"
                    "告警不阻断（不重启浏览器不改环境）；"
                    "internal=True 让位于业务租约。",
        "deviation": "探针执行失败或发现泄漏项",
        "chain": "run_keepalive → env_audit_snapshot → ENV_AUDIT_JS → "
                 "compare_with_profile → BCC-064 告警",
        "root": "见具体泄漏项（065 自动化痕迹 / 066 篡改 / 067 软渲染 / "
                "068 档案不一致 / 069 一致性弱点）。探针执行失败通常是 "
                "context 正在重建，等下轮即可。",
        "verify": "curl -X POST :<bcc_port>/env_audit 读回完整 leaks 与 "
                  "js_view；fatal 项必须人工处置后复测归零。",
    },
    "BCC-065": {
        "design": "自动化标志绝不可暴露（webdriver/HeadlessChrome/注入对象）"
                  "—— rebrowser-bot-detector 的 runtimeEnableLeak/webdriver "
                  "同族检测，主流反bot 必测项。",
        "contract": "patchright 接管驱动层后这些值必须干净；fatal 级。",
        "deviation": "webdriver=true / UA 含 HeadlessChrome / window 上有 "
                     "cdc_/__webdriver 等注入键",
        "chain": "ENV_AUDIT_JS 基本面采集 → compare_with_profile E1/E2",
        "root": "驱动层回退到原生 playwright（检查 DY_PW_BACKEND）或 "
                "启动参数被改",
        "verify": "chrome 主进程实参应无 --disable-component-update 且有 "
                  "--disable-blink-features=AutomationControlled（patchright "
                  "判据）；复跑 /env_audit 应归零。",
    },
    "BCC-066": {
        "design": "原生函数必须保持 [native code]（CreepJS toString 篡改检测）"
                  "—— JS 注入式伪装会被原型检查拆穿，内核级伪装不应触发。",
        "contract": "patchright + fingerprint-chromium 下 tamperedCount=0。",
        "deviation": "原生函数 toString 暴露 JS 源码",
        "chain": "ENV_AUDIT_JS nativeChecks → compare_with_profile E3",
        "root": "有 JS 层 monkey-patch 伪装在跑（stealth 插件类）；本项目 "
                "架构是内核级伪装，出现即说明混入了 JS 层补丁",
        "verify": "排查 add_init_script 是否注入了额外伪装脚本；复跑归零。",
    },
    "BCC-067": {
        "design": "WebGL 必须呈现真实显卡（fingerprint-chromium 种子伪装）"
                  "—— 软件渲染兜底值是 2026-09-13 实测的最强破绽。",
        "contract": "dbgRenderer 不得含 swiftshader/basic render/software。",
        "deviation": "WebGL renderer 为软件渲染字符串",
        "chain": "ENV_AUDIT_JS webgl 采集 → compare_with_profile E4",
        "root": "--disable-gpu 被注入（DY_DISABLE_GPU=1？）或内核伪装失效",
        "verify": "chrome 实参无 --disable-gpu；WebGL renderer 显示真实显卡；",
    },
    "BCC-068": {
        "design": "浏览器层真值必须与项目档案（utils/fingerprint 单源）恒等"
                  "—— v0.43.14 单源化的常驻监测形态（verify_fp_single_source "
                  "的一次性脚本转为 keepalive 周期检测）。",
        "contract": "时区/语言/核心数/screen/UA 与 fingerprint_profile(account) "
                    "逐项一致；UA Chrome 版本与 kernel_version() 一致。",
        "deviation": "任一项不一致",
        "chain": "ENV_AUDIT_JS 采集 → compare_with_profile E5-E9",
        "root": "单源化接线被绕过（launch_args/viewport_for 未走）或档案派生 "
                "逻辑被改",
        "verify": "复跑 verify_fp_single_source.py；/env_audit 归零。",
    },
    "BCC-069": {
        "design": "环境内部一致性（liarjs/CreepJS 思路）：screen 三值不全等、"
                  "worker 与主线程同值、canvas/audio 同 context 恒定。",
        "contract": "真实用户浏览器不可能出现三值全等/跨线程分叉/同库不稳。",
        "deviation": "任一一致性弱点命中",
        "chain": "ENV_AUDIT_JS worker/canvas/audio 采集 → compare_with_profile "
                 "E10/E11/E12",
        "root": "viewport 未设（Playwright 默认 1280x720）或驱动层状态异常；"
                "Client Hints 缺失为内核已知缺口（info 级，v0.43.14 已记录）",
        "verify": "排查 vbrowser 是否注入 viewport_for(account)；复跑归零。",
    },
    "BCC-063": {
        "design": "同一账号的出口环境（出口 IP+代理模式）必须与登录时恒等"
                  "（知识库 9.30 铁律）。env_baseline 在登录成功时记录基线，"
                  "run_keepalive 低频比对（30min 节流），环境跳变在被强制"
                  "下线之前暴露，而不是事后从弹窗反推。",
        "contract": "比对走 probe_egress_ip_direct（零浏览器副作用）；"
                    "探测失败/无基线/节流窗口内 = 未比对，绝不告警；"
                    "只告警不重启（重启是更强风控信号）。",
        "deviation": "当前出口 IP 与基线 IP 不一致",
        "chain": "run_keepalive → env_baseline.compare_baseline → "
                 "probe_egress_ip_direct → 与基线比对",
        "root": "出口环境与登录时分叉（系统代理开关/换节点/配置改动未重扫）。"
                "注意：若刚改过代理配置，基线应已被 save_proxy 清除，不应误报。",
        "verify": "curl 该账号 proxy-test 看当前出口；读 kv_store "
                  "env_baseline_<账号> 看基线；两条不一致即坐实。"
                  "处置=重扫或全链路同改环境。",
    },
    "ENG-014": {
        "design": "引擎 RUNNING 时 dispatch（DispatchCenter）必须已构造完成，"
                  "热更经它生效。",
        "contract": "热更有且只有一个真源 = dispatch；拿不到 dispatch 就是失败，"
                    "不得只改 adm 字段假装成功。",
        "deviation": "热更失败：dispatch 未初始化",
        "chain": "apply_runtime_config → self.dispatch 为 None → 返回 ok=False",
        "root": "引擎刚进 RUNNING 但 _run 尚未建 dispatch，或启动中途失败（状态未回退）",
        "verify": "curl :8000/api/tasks/current 看 has_task/engine_state；"
                  "日志 grep ENG-014 与紧邻的引擎启动日志。",
    },
    "ENG-015": {
        "design": "引擎任务收尾只应在监听线确实结束后发生。WS 握手窗口（已发起连接、尚未连上）" \
"属于监听线的活跃生命周期，此期间队列空是必然的正常中间态，不得据此收尾。" ,
        "contract": "① 存活判据必须覆盖『已发起→已确认』完整区间，不得用 live.ws is not None" \
"（ws 由 run_forever() 内部赋值，握手窗口内恒为 None）；② 该判据在" \
"_on_dispatch_idle / snapshot() / /api/live/stream 三处必须同源；③ 是否" \
"继续关心『队列空』由上层状态机决定，通知方不得自行解绑。" ,
        "deviation": "引擎刚进 RUNNING、WS 尚未连上时就被收尾成 STOPPED；此后弹幕捕获 / AI 生成 /" \
"私信发送照常工作，但 engineState 恒为 stopped，前端徽章与" \
"『暂停/继续/停止监听』控件全部失效。" ,
        "chain": "AutoDM.start → _run 建 dispatch → dispatch._loop 队列空超时 → on_idle →" \
"_on_dispatch_idle（判据用 ws is None → 判为监听已死）→ state=STOPPED →" \
"snapshot/engineState 下发 stopped → 前端控件全灰" ,
        "root": "两处连坐：① DispatchCenter._loop 触发 on_idle 后自行 self.on_idle = None，" \
"把上层状态机语义硬编码进通知方，唯一的触发机会被握手窗口吃掉；" \
"② 存活判据用 live.ws is not None，而 ws 在 run_forever() 内才赋值，" \
"握手窗口内恒 None。" ,
        "verify": "① python -m unittest test_engine_idle_guard -v（10 项全绿；回退判据后应红 3 项）；" \
"② 实机：启动直播监听，日志中『私信收尾』必须晚于『[live-ws] 连接已建立』；" \
"③ curl :8000/api/tasks/current 的 engine_state 应为 running；" \
"④ 前端徽章显示『直播引擎监听中』且暂停/继续/停止按钮可用。" ,
    },
    "ENG-017": {
        "design": "直播流连接应**独立于账号凭证**：抖音直播支持匿名观看，凭证的真正职责是" \
"「部分直播间昵称解密」与「私信发送」。因此凭证失效时，监听线必须继续" \
"（降级为只听不发），而不是整条链路中断。" ,
        "contract": "① 进房（room_id/ttwid/开播状态）与 WS 建连不得依赖登录态，无凭证走匿名；" \
"② 凭证缺失只关闭发送能力（enable_send=False），不得 return / 置 IDLE；" \
"③ 不得把 None 传给 DouyinAPI.get_live_info（其内部 auth_.cookie 会崩）；" \
"④ 有凭证时行为与改造前逐字一致（零回归）。" ,
        "deviation": "凭证失效/缺失时，_run 在 AUTH-018/019/020 处直接 return 并置 IDLE，" \
"状态显示「监测登录失败 / 发送登录失败」，前端连直播流都建不起来 ——" \
"与「直播可匿名观看」的平台事实矛盾。" ,
        "chain": "AutoDM._run → 构造 monitor_auth/auth → 凭证判据失败 → return + IDLE →" \
"LiveChatHook 从未被创建 → /api/live/stream alive=false → 前端无任何直播数据" ,
        "root": "把「昵称解密/发送所需的凭证」与「直播流建连能力」耦合在同一条判据上：" \
"实现方按「没有凭证就不能干活」直译，而平台实际允许匿名建连。" \
"另有一处实现坑：DouyinAPI.get_live_info(auth_=None) 会 AttributeError，" \
"匿名路径必须自建请求或改走 LiveChatHook._anon_live_info()。" ,
        "verify": "① python -m unittest test_live_anon_decouple（10 项，含回退验证）；" \
"② python scripts/diag/verify_anon_live_decouple.py —— 用假失效凭证实测：" \
"匿名进房拿 room_id/status/ttwid + 匿名 WS 握手成功并收到帧（实测 3/3 通过）；" \
"③ 有凭证账号启动任务，行为与改造前一致（仍能发送）。" ,
    },
    "ENG-016": {
        "design": "软停止（stop(hard=False)）的语义是「停止接收新目标，存量队列发完即收尾」。" \
"收尾必须在存量发完后**立即**发生，不能被尚未开始的随机延迟窗口拖住。" ,
        "contract": "① 等待发送时刻的 sleep 必须可被停止信号打断；② 停止后被丢弃的目标" \
"必须同步清理 pending（否则收尾判据永远不成立）；③ wait_done 与 on_idle" \
"的收尾条件都依赖 pending 为空，二者不得被同一条记录同时堵死。" ,
        "deviation": "点「停止监听」后界面长时间无变化：实测 stop 后状态卡在 STOPPING，" \
"耗时 = 该记录的随机延迟窗口（50s 实测 / delay 上限可达 ~120s）才转 STOPPED。" ,
        "chain": "AutoDM.stop(hard=False) → dispatch.stop_soft() → _loop 仍在" \
"`await asyncio.sleep(item.send_at - now)` → 该记录 pending 未清 →" \
"on_idle 判据 `not self.pending` 不成立、wait_done 的 while 永真 →" \
"无人推进状态 → state 恒为 STOPPING → 前端徽章/按钮无变化" ,
        "root": "两点叠加：① _loop 无条件 sleep 到 send_at，软停止（_accept_new=False 但" \
"_stopped 未置）不打断它；② 该记录在软停止后必然走不到发送（闸门拒收），" \
"却仍要睡满才在 _do_send 里 pop pending —— 收尾被它独占阻塞。" ,
        "verify": "① python scripts/diag/diag_soft_stop_state.py —— 复现脚本：修复前" \
"50s 才落 stopped，修复后 2.5s 内落 stopped；" \
"② 实机：点「停止监听」，观察 ≤3s 内 engine_state 变 stopped、" \
"前端徽章与按钮立即更新；③ 日志出现「软停止：丢弃尚未到发送时刻的目标」。" ,
    },
    "LIVE-021": {
        "design": "「直播间配置管理」是房间级配置的**唯一可写入口**；点「重启」应把"
                  "配置内容热更进正在运行的任务，且四种结果都要如实下发（已生效 / "
                  "引擎未运行 / 属换任务语义 / 热更异常）。",
        "contract": "热更失败必须透出到前端，禁止『保存了但没生效』的静默假成功"
                    "（用户会以为配置已按新值运行）。",
        "deviation": "热更抛出异常（配置载荷异常 / 引擎状态非法 / 内部错误）",
        "chain": "restart_room_config → _RuntimeCfg(cfg) → adm.apply_runtime_config → "
                 "dispatch.apply_runtime",
        "root": "配置项类型/范围非法（如 delay 字符串不可解析、词库结构异常）或"
                "引擎方法内部异常；属数据面问题，非前端展示问题",
        "verify": "POST /api/live/room-configs/{id}/restart 的响应里 "
                  "restart.ok=false 且 reason 非空；日志 grep LIVE-021 看原始异常。",
    },

    "LIVE-034": {
        "design": "监听线存活期间，「凭证是否可用」必须有明确判据。"
                  "但该判据不能是「有 cookie」——实测张老师 cookie 70 个字段齐全"
                  "（sessionid/sid_tt/ttwid/uid_tt 全在），服务端仍判其未登录。",
        "contract": "三态且诚实降级：True=已确认被服务端承认 / False=已确认未承认 /"
                    " None=取不到证据 → 保留原行为并留痕，绝不擅自降级。",
        "deviation": "会话态探测无结论时调用方无法区分「没探测」与「探测失败」。",
        "chain": "AutoDM._run → probe_live_identity → LiveChatHook.session_ok → "
                 "_has_credential()",
        "root": "探测结果未传递或被探测异常吞掉（异常一律转可读原因，不冒泡）。",
        "verify": "日志出现 LIVE-034 时，检查 upstream 是否真跑过 probe_live_identity。",
    },
    "LIVE-035": {
        "design": "凭证的职责是「部分直播间昵称加密时的解密权」。账号无解密权时，"
                  "直播帧只下发脱敏数据（uid=111111 + 昵称 `威***` + sec_uid 空），"
                  "**与完全不带 cookie 的真匿名逐字相同**。",
        "contract": "解密权是**合取**判据（2026-09-22 H-3 返工。此前只用 ① 且以"
                    "「无解密权」上报，把「会话活性被拒」误等于「身份漂移/无解密权」）："
                    "① 会话被服务端承认 —— 判据 = 主站 user/profile/self/（status_code=0 + sec_uid）；"
                    "② 身份未漂移 —— 判据 = 探活 uid ∈ 该账号历史 conv_id（AUTH-050）；"
                    "③ 二者覆盖**两类不同失效**，结论互不代替（live_session_state 自身边界）；"
                    "④ 任一条取不到证据（None）不得据此降级（诚实三态）；"
                    "⑤ 不得因为「有 cookie」就宣称具备解密权；"
                    "⑥ 权威出口 = accounts.uid_identity_verdict() 四元组 (state, reason, label, detail)，"
                    "reason ∈ {ok, not_logged_in, uid_drift, no_credential, unknown}。",
        "deviation": "实测：同一房间、同一时刻，张老师 cookie → uid=111111/sec_uid 空；"
                     "尚进 cookie → uid=63676672247/sec_uid=MS4wLjABAAAA…；"
                     "无 cookie → uid=111111（与张老师完全一致）。"
                     "另一类失效样本（2026-09-14/17 实测）：探活 uid 与历史 conv_id 不符（AUTH-050），"
                     "该账号同样无解密权，但成因与「会话被拒」不同。",
        "chain": "账号 .env cookie →① 主站 profile/self（status_code=8 用户未登录）"
                 "或 ② query/user 探活 uid ∉ 历史 conv_id → "
                 "uid_identity_verdict 合取判定 → AutoDM._live_session_ok 三态 → "
                 "LiveChatHook.session_ok → 直播 WS 帧 User.id=111111 / sec_uid 空 / "
                 "desensitized_nickname 有值",
        "root": "两类根因，**必须分别判定、分别上报**："
                "（a）账号会话未被服务端承认（cookie 存在但登录态无效），"
                "或账号处于「只读态」（独立证据：imapi cmd609 建会话被拒）；"
                "（b）身份漂移 —— 探活 uid 不在该账号历史 conv_id 中（AUTH-050），"
                "常由 profile 残留他人登录态 / 凭证回写污染导致。",
        "verify": "① 弹幕日志 uid=111111 且 LIVE-006 计数上升；"
                  "② python scripts/diag/diag_live_cred_ab.py 三组对照；"
                  "③ uid_identity_verdict 返回 False 且 reason ∈ "
                  "{not_logged_in（detail 带 status_code=8）, uid_drift（detail 带 AUTH-050）}；"
                  "④ 健康样本（会话被承认 + 身份一致）→ 返回 True/ok（两侧都要验）。",
    },
    "LIVE-036": {
        "design": "登录态探测是「能力判定」的输入，探测自身失败不等于能力缺失。",
        "contract": "探测异常必须转为可读原因并**不据此降级**（诚实三态之 None），"
                    "否则「机制坏了」会被伪装成「账号不行」。",
        "deviation": "网络/风控导致 profile/self 请求异常。",
        "chain": "probe_live_identity → requests.get(profile/self) → except → (False, 原因)",
        "root": "外网异常或响应形状变化；判定真值应由下一次探测刷新。",
        "verify": "detail 含「结论未知」即表示未定论；此时 AutoDM 不置 session_ok=False。"
                  "（H-3 后：uid_identity_verdict 返回 (None, 'unknown', …) 亦属此态，"
                  "消费方按既有行为继续，不得据此降级。）",
    },
    "LIVE-037": {
        "design": "带凭证取直播间信息；若服务端把该会话当未登录，则回落匿名路径。",
        "contract": "回落必须显式留痕（不得静默），且不得用非登录会话的 ttwid 建连。",
        "deviation": "实测张老师带 cookie 请求直播间页，响应体 1158.9KB，"
                     "页面渲染「请登录」文案 4 处（与无 cookie 完全一致）。",
        "chain": "start_ws → _room_info_with_credential → DouyinAPI.get_live_info → "
                 "无 room_id/异常 → _anon_live_info",
        "root": "DouyinAPI.get_live_info 依赖 res.cookies['ttwid']，"
                "在服务端未承认该会话时可能取不到或返回结构异常。",
        "verify": "日志出现 LIVE-037 后应紧跟 LIVE-033，且最终仍能建连（收帧数>0）。",
    },
    "BCC-005": {
        "design": "打开 chat 页是 BCC 一切能力（昵称 hook/发送/页面探活）的前置；"
                  "失败应降级为后续接口自愈，不阻塞容器启动。",
        "deviation": "chat 页未打开 → 页面停在 about:blank/首页",
        "chain": "BCC 启动 → vbrowser.launch_async(profile) → page.goto(chat)",
        "root": "网络/代理/页面加载超时，或 profile 未登录被重定向",
        "verify": "GET :11231/status 后 POST /exec_js {js:'location.href'} 看是否含 "
                  "/chat；页面 convItems 数 >0。",
    },
    "BCC-006": {
        "design": "容器 context/page 应持续可用；失活才重建（自愈），重建不得产生"
                  "用户可见副作用。",
        "contract": "稳态下 context 不应反复失活——频繁重建=有外力在破坏它。",
        "deviation": "单位时间重建次数异常（实测曾 155 次/日；故障期每 3~4s 一次 → "
                     "用户可见「窗口快闪」）",
        "chain": "源头候选：① 另一进程抢同一 profile（如手工起的第二个 BCC）"
                 "② set_visible 重建 context ③ _ensure_alive 误判 "
                 "④ 浏览器被外部关闭 → 终端：context.pages 为空报「已关闭」",
        "root": "先查是不是有第二个实例/别的进程在动同一个 profile，再看探活判据是否"
                "过严（页面加载中被误判失活）",
        "verify": "① Get-CimInstance Win32_Process 看 browser-daemon 是否 =1；"
                  "② 日志 BCC-052 计数（每次重建一条）；③ profile 锁文件"
                  "（SingletonLock/lockfile）是否存在。",
    },
    "BCC-028": {
        "design": "_launch 是容器唯一启动路径，应能在任何入口下都拿到该账号的 .env"
                  "（含会员加密 .env.enc）。",
        "deviation": "报「账号 X 无 .env（索引未登记？）」——不是索引坏，而是凭证读不到",
        "chain": "BCC 启动 → _launch → accounts.env_path_of(account) → "
                 "services.member_ctx（会员态解密）",
        "root": "① 进程缺 DY_MEMBER/DY_MEMBER_KEY（手工启动最常见）② accounts_index "
                "未登记 ③ 账号已删",
        "verify": "python -c \"from services import member_ctx as m; "
                  "print(m.member_space_root(), m.is_member_env('<env_path>'))\"；"
                  "手工启动必须带 DY_APP_ROOT + DY_MEMBER + DY_MEMBER_KEY。",
    },
    "BCC-041": {
        "design": "services.browser_gate 是浏览器唯一门禁：自动路径复用 BCC，离线则先"
                  "拉起 BCC，拿不到就显式失败（绝不新开实例）。",
        "contract": "门禁失败时调用方必须停止依赖浏览器的步骤，而不是继续跑"
                    "（否则产生假成功）。",
        "deviation": "门禁返回 not-ready，但调用方仍继续 → 后续 CAP-007 连接失败、"
                     "昵称 0 个，前端却显示「更新完成」",
        "chain": "调用方 → gate.ensure_browser → bcc_state(端口/status) → "
                 "ensure_daemons_for(拉起)",
        "root": "实测两类：① 启动冷静期(DY_BCC_LAZY_DELAY=30s)拦住了用户显式操作；"
                "② 二进制缺失/端口未就绪",
        "verify": "看 gate 日志的 skip_cooldown= 与 purpose；curl :8000/api/version "
                  "确认 backend 已起多久（<30s 则命中冷静期）。",
    },
    "BCC-051": {
        "design": "凭证不可用属不可自愈条件，容器应熔断退出重启循环并给出可操作提示。",
        "deviation": "进入熔断（30 分钟内不再自动重启）",
        "chain": "_ensure_alive → _launch(env 不可用) → 致命态标记",
        "root": "同 BCC-028（凭证读不到）——必须解决根因，重启无用",
        "verify": "同 BCC-028 的会员态核查。",
    },
    "BCC-052": {
        "design": "context 失活自愈：重建一律按最小化启动，不继承上次可见态"
                  "（避免窗口快闪）。",
        "deviation": "发生了一次容器重建（信息级，用于观测重建频率）",
        "chain": "_ensure_alive 探活失败 → close → _launch(headless=True)",
        "root": "重建本身是自愈行为；若频率高，回到 BCC-006 查外力",
        "verify": "统计当日 BCC-052 次数；配合 Win32_Process 看实例数是否曾 >1。",
    },
    "BCC-053": {
        "design": "可见性切换的**完成判据是 OS 层真实可见窗口**，不是 CDP 返回值。"
                  "纯 native headless 下 Browser.setWindowBounds('normal') 恒返回"
                  "成功但无真实窗口，故「已切为有头」必须经 OS 枚举确认；"
                  "无法确认即如实报未达成，绝不谎报成功。"
                  "（2026-09-20 v0.44.11：判据改为**带重试轮询**——实测窗口从 "
                  "launch 返回到可见约 12s，单次检查必然误报；轮询上限 90s。）",
        "contract": "切换为可见后，该容器内核进程在 OS 层必须存在 "
                    "IsWindowVisible=True 且类名为浏览器顶层窗口"
                    "（Chromium=Chrome_WidgetWin / Camoufox=MozillaWindowClass）"
                    "的顶层窗口；窗口在轮询窗口内出现即算达成。",
        "deviation": "轮询耗尽（最长 90s）仍未见可见窗口 → 切换未真正达成"
                     "（用户侧表现为「提示已打开却看不到浏览器」）。"
                     "检测函数据取不到时**不判失败**，走诚实降级（不清空状态）。",
        "chain": "POST /show(visible=true) → set_visible → _do_switch_background"
                 " → _launch(headless=False) → _wait_window_visible()"
                 "（轮询）→ _window_really_visible()",
        "root": "① 内核仍被以 native headless 启动（窗口被最小化/移出屏幕）；"
                "② 多实例抢 profile 导致重建失败；"
                "③【2026-09-20 实测真因】PID 兜底静默失败：旧实现按固定进程名"
                "过滤，而真实内核进程名会漂移 → 进程列表恒空 → 判不可见；"
                "异常又被 `except: pass` 吞掉，全程无日志。",
        "verify": "1) EnumWindows 枚举该内核 PID 的可见窗口数（headless=0 / "
                  "headed=1，已实测）；2) 查内核主进程实参是否含 --headless；"
                  "3) Win32_Process 确认同 profile 无第二个实例；"
                  "4) 若日志出现「未能解析到本容器进程 PID」= PID 兜底失效，"
                  "按 cmdline 片段（member_id + _camoufox）核对进程名是否漂移。",
    },
    "BCC-054": {
        "design": "同一账号同一时刻只能有一个 profile 所有者；任何『交出/取回 "
                  "profile 所有权』的整段操作（停 BCC \u2192 独占使用 \u2192 还回 BCC）"
                  "必须在同一把按账号维度的互斥锁内完成，而不是只锁各自的单步。",
        "contract": "锁覆盖『停 \u2192 用 \u2192 还』全过程；不同账号的锁互相独立，"
                    "不牺牲跨账号并发。",
        "deviation": "申请独占时锁已被其它操作持有且超过超时阈值 \u2192 放弃本次独占"
                     "（绝不与之抢 profile）。",
        "chain": "重捕/扫码 \u2192 ProfileOwnership(account) \u2192 _quit_browser_daemon"
                 " \u2192 独占使用 \u2192 ensure_daemons_for",
        "root": "旧实现里『停 BCC』与『拉 BCC』分属两模块、无共享锁，"
                "而另起 chromium 的旧路径夹在中间 \u2192 两实例重叠持有同一 profile"
                "（实测 TargetClosedError）。",
        "verify": "1) Win32_Process 统计同一 profile 的 chrome 主进程数，"
                  "恒 \u22641；2) 并发跑重捕+校验，日志不应出现 BCC-054；"
                  "3) 锁按账号维度：两账号并发互不阻塞（耗时不叠加）。",
    },
    "CAP-001": {
        "design": "「更新会话」必须依赖 BCC 才能截昵称；拿不到 BCC 时必须让用户知道，"
                  "而不是静默降级。",
        "deviation": "browser_daemon 未拉起 → 昵称关联必失效（仅告警，用户无感）",
        "chain": "refresh → ensure_daemons_for → BCC 端口",
        "root": "启动冷静期拦截 / 二进制缺失 / 端口被占",
        "verify": "同 BCC-041；并在前端确认是否提示了「未拿到浏览器」。",
    },
    "CAP-007": {
        "design": "capture_userinfo_via_browser 经 BCC 被动 hook 截昵称"
                  "（零主动请求、零风控）。",
        "deviation": "调 BCC /capture_userinfo 连接失败（端口不通）",
        "chain": "capture_all → capture_userinfo_via_browser → "
                 "requests.post(:11231/capture_userinfo)",
        "root": "BCC 根本没起来（上一环 gate 失败未被阻断）——不是捕获逻辑有 bug",
        "verify": "Get-CimInstance Win32_Process 过滤 browser-daemon 数量是否为 0；"
                  "curl :11231/status。",
    },
    "CAP-016": {
        "design": "所有浏览器启动路径收敛到 browser_gate 统一入口（杜绝环境分叉）。",
        "deviation": "统一入口未就绪（拿不到浏览器）",
        "chain": "capture_all → gate.ensure_browser → BCC 状态",
        "root": "与 BCC-041 同源；注意此处不 return 会继续跑出假成功",
        "verify": "日志紧跟的 CAP-007（连接失败）即证据链下一环。",
    },
    "CAP-017": {
        "design": "「更新会话全程」是跨调用窗口租约（设计文档 §3.6）："
                  "capture_all 取得租约 → lease_id 透传到 BCC /cookie 与 "
                  "/capture_userinfo → 结束时 release。",
        "contract": "租约必须在调用方 finally 释放；持租期间不得被自己的下游请求判为冲突。",
        "deviation": "租约释放失败（BCC 不在线 / lease_id 不匹配 / 已被 TTL 回收）",
        "chain": "messages.refresh_conversations(finally) → release_active_lease → "
                 "gate.release_lease → BCC /lease/release",
        "root": "BCC 进程已退出（端口不通）或租约已被 BCC-046 惰性 TTL 回收；"
                "不阻断主流程，但该账号浏览器要等 TTL 结束才可被其它业务取用。",
        "verify": "curl -s :<bcc_port>/lease_status 应为空闲（holder=None）；"
                  "日志 grep '已释放跨调用窗口租约'。",
    },
    "SYS-002": {
        "design": "守护拉起应幂等，且可被用户显式操作随时触发。",
        "deviation": "BCC 未拉起（冷静期内或二进制缺失）",
        "chain": "ensure_daemons_for → ensure_bcc → 冷静期判定",
        "root": "启动冷静期(30s)拦截——原设计为防启动期自动路径乱拉，但用户显式操作"
                "也被一并拦住（实测事故）",
        "verify": "对照 backend 启动时间与用户点击时间间隔（<30s 即命中）；"
                  "echo $DY_BCC_LAZY_DELAY。",
    },
    "AUTH-050": {
        "design": "探活 uid 必须与账号历史 conv_id 的 uid 段一致（身份判据）。",
        "contract": "UID 漂移 = 凭证失效（用户铁律）",
        "deviation": "探活得到的 uid 不出现在任何历史 conv_id 中",
        "chain": "uid_probe.get_uid(force) → query/user → 与 dm_conversations.conv_id "
                 "交叉校验",
        "root": "① 凭证已失效（登录态被踢/环境跳变）② profile 里登录的是他人（幽灵 uid）",
        "verify": "SELECT DISTINCT conv_id FROM dm_conversations WHERE account=? 手工比对"
                  "探活 uid；0 命中 → 触发重新扫码。",
    },
    "SEND-037": {
        "design": "dm_dispatch 是发送的唯一风控闸门；不可用时宁可不发。",
        "contract": "任何直发旁路都算缺陷（发送是最高频风控面）。",
        "deviation": "dm_dispatch 接入失败，本次已放弃发送（不再回退直发）",
        "chain": "dispatch._do_send → dm_dispatch.submit → 失败分支",
        "root": "调度器内部异常（非账号风控）",
        "verify": "日志前后是否有 SEND-006/007；DB 无 role=me 新增即为放弃成功。",
    },
    "SEND-038": {
        "design": "私信文案来源 = AI 生成优先、词库回落；生成回调抛异常不得影响发送。",
        "contract": "回调异常只降级文案来源，绝不中断本次发送、绝不污染风控闸门。",
        "deviation": "gen_dm_message 抛异常 → 已静默降级为词库文案",
        "chain": "dispatch._do_send → gen_dm_message(target) → 异常分支",
        "root": "AI 侧异常（配置/网络/Agent 解析），非发送侧问题",
        "verify": "日志出现 SEND-038 且同条记录 content 非空（取自词库）即为预期降级。",
    },
    "SEND-039": {
        "design": "直播/采集的私信文案是否接 AI，由「账号绑定 Agent + scopes 含 live + enabled 且非 kb_only」判定。",
        "contract": "判定失败必须回退词库（调用方零感知）；kb_only 档位绝不允许 AI 参与。",
        "deviation": "接线判定抛异常 → 已回落词库",
        "chain": "AutoDM._make_gen_dm_message → agent_of/resolve_config → 异常分支",
        "root": "Agent 模块不可导入或 kv 读取异常",
        "verify": "日志 SEND-039 + 发送仍成功（content 来自词库）= 预期降级。",
    },
    "SEND-040": {
        "design": "AI 生成的直播私信文案必须过现有护栏（validate_reply/违禁词/长度）后才可发送。",
        "contract": "生成失败或被护栏拦下 → 返回空串 → 调度器回落词库，绝不发空文案、绝不发泄漏文本。",
        "deviation": "生成/护栏环节抛异常 → 已回落词库",
        "chain": "AutoDM._gen → ai_reply.generate_dm_for_live → 异常分支",
        "root": "模型链路不可用 / 网关不可达 / 输出被护栏拒绝",
        "verify": "日志 SEND-040；对照 live-ai 成功行判断是「未接线」还是「生成失败」。",
    },
    "ACC-022": {
        "design": "重捕整段操作（停守护 → 独占 profile → 拉回 BCC）必须在"
                  "按账号维度的所有权锁内完成。",
        "contract": "同一账号任一时刻只有一个 profile 所有者。",
        "deviation": "未能取得所有权锁 → 降级为无锁执行（有双实例风险）",
        "chain": "_do_auto_recapture → ProfileOwnership → _quit_browser_daemon",
        "root": "browser_gate 不可导入或锁获取异常",
        "verify": "grep 日志 ACC-022；正常情况应恒不出现。",
    },
    "ACC-023": {
        "design": "从 profile 读凭证的整段操作同样必须在所有权锁内完成。",
        "contract": "同一账号任一时刻只有一个 profile 所有者。",
        "deviation": "未能取得所有权锁 → 降级为无锁执行（有双实例风险）",
        "chain": "_do_recapture_from_profile → ProfileOwnership → _quit_browser_daemon",
        "root": "browser_gate 不可导入或锁获取异常",
        "verify": "grep 日志 ACC-023；正常情况应恒不出现。",
    },
    "ACC-024": {
        "design": "扫码整段操作（停守护 → 独占 profile → 拉回 BCC）必须在"
                  "所有权锁内完成；否则与并发拉起 BCC 撞车 → 抢锁 → "
                  "扫码页加载异常/授权回执读不到。",
        "contract": "同一账号任一时刻只有一个 profile 所有者。",
        "deviation": "未能取得所有权锁 → 降级为无锁执行（有双实例风险）",
        "chain": "_do_scan → ProfileOwnership → _quit_browser_daemon → enrich_auth",
        "root": "browser_gate 不可导入或锁获取异常",
        "verify": "grep 日志 ACC-024；正常情况应恒不出现。",
    },
    "ACC-025": {
        "design": "凭证失效时打开有头浏览器是**正当且被引导**的观测入口，"
                  "同时必须暂停全部任务（无效凭证下继续发送=风控暴露）。",
        "contract": "凭证失效 ⇒ 任务暂停 + 有头观测放行；"
                    "凭证有效且任务在跑 ⇒ 拒绝有头。",
        "deviation": "检测到凭证失效，已按契约暂停任务并放行有头观测（信息级）",
        "chain": "POST /open-browser → verify_account(wp!=ok) → adm.pause() → BCC /show",
        "root": "凭证失效本身（UID 漂移 / 签名过期 / 登录态被下线）",
        "verify": "1) 日志出现 ACC-025 且引擎 state 已暂停；"
                  "2) 之后扫码成功 → 凭证回写 → 任务可恢复。",
    },
    "ACC-026": {
        "design": "暂停引擎是凭证失效时的保护动作，失败不得阻断"
                  "「打开有头浏览器观测」这条用户显式路径。",
        "contract": "暂停失败只告警，仍放行观测。",
        "deviation": "引擎暂停调用抛异常",
        "chain": "POST /open-browser → adm.pause() 异常",
        "root": "引擎状态机处于不可暂停态 / 事件循环不可用",
        "verify": "人工确认引擎状态；若未暂停应手动在界面停止。",
    },
    "BCC-055": {
        "design": "有头可见 = 用户正在观测/操作的窗口。此时任何 context 重建都会"
                  "销毁用户眼前窗口，并在抖音侧记一次『全新环境』访问 —— 恰在"
                  "step-up 校验（扫码/输手机号）时刻触发『安全风险阻止访问』。",
        "contract": "有头态下探活失败只告警，**绝不自动重建**；无头态保留自愈。",
        "deviation": "有头态探活失败，已跳过自动重建（保护用户交互与环境稳定）",
        "chain": "_ensure_alive 探活失败 → 有头态判定 → return（不重建）",
        "root": "v0.43.98 起切可见=真实重建；若叠加自愈重建会造成无头↔有头横跳",
        "verify": "有头期间 context 代次应保持不变；日志出现 BCC-055 而非 BCC-052。",
    },
    "BCC-057": {
        "design": "自动重扫（scan_login）会关闭并重建 context；有头观测态下必须与"
                  "之互斥，否则打断授权并在抖音侧新增『新环境』记录。",
        "contract": "有头态下不触发自动重扫；由用户在打开的窗口中完成登录。",
        "deviation": "检测到登录态失效但处于有头态 → 跳过自动重扫",
        "chain": "run_keepalive → BCC-025 → 有头态判定 → continue",
        "root": "后台自动动作与用户交互争抢同一 profile/context",
        "verify": "有头期间不应出现 scan_login / context 代次增长。",
    },
    "BCC-058": {
        "design": "内核可切换（Chromium / Camoufox）由配置显式决定；Camoufox 启动失败"
                  "必须回退 Chromium，绝不因换内核导致浏览器整体不可用。",
        "contract": "DY_BROWSER_KERNEL=camoufox 启用；其它值（含缺省）走 Chromium。",
        "deviation": "尝试以 Camoufox 启动但失败（未安装/geoip 缺失/profile 冲突等）",
        "chain": "launch_async/launch_sync → camoufox_enabled → launch_camoufox_* → 异常",
        "root": "Camoufox 未安装或依赖缺失（需 pip install camoufox[geoip]）",
        "verify": "日志是否出现 BCC-058 并成功回退；pip show camoufox 确认安装。",
    },
    "AUTH-053": {
        "design": "扫码/重捕得到的凭证应是被服务端承认的、当前生效的会话，而不是扫码瞬间的旧快照。",
        "contract": "抓取到的 cookie 必须通过 user/profile/self/ 被服务端承认（status_code==0 且含 MS4wLjABAAAA）。",
        "deviation": "扫码完成后立即读 context.cookies()，得到的是被服务端拒绝的轮换前会话（profile/self status_code=8）。",
        "chain": "scan_login/recapture → context.cookies() → save_credential → .env（写入即快照）",
        "root": "抖音在扫码完成后会再换发一次会话（passport 换发窗口），快照采在换发前 ⇒ 永远落后一站。",
        "verify": "抓取后用 profile/self 复验该 cookie；日志出现 AUTH-053 即为未获承认。",
    },
    "BCC-071": {
        "design": "BCC 保活回写只能写入「被服务端承认的」会话；旧旧会话绝不写入 .env。",
        "contract": "写盘前必须同时过「身份一致性」（门禁1.5）与「会话活性」（profile/self）两道门禁，二者互不代替。",
        "deviation": "读到的 cookie 探活到正确 uid，但 profile/self 返 status_code=8（服务端不承认该会话）。",
        "chain": "run_keepalive → refresh_cookie_to_env → get_cookies(profile) → 门禁1.5 → 门禁1.6(profile/self) → save_credential",
        "root": "query/user 容忍陈旧会话→门禁1.5 放行；而 profile/self 严格→会话被拒。先就地刷新页面复验。",
        "verify": "日志出现 BCC-071 后是否出现 BCC-072；手动核对 .env 与 profile 的 sessionid 家族是否一致。",
    },
    "BCC-072": {
        "design": "自愈回路（保活回写）不得静默失效；持续两次拿不到被承认的会话必须响亮上报。",
        "contract": "保活回写被 BCC-071 拒掉时应立即重试一轮（不等 30min 节流），两次仍失败则明确告警交人工。",
        "deviation": "会话未获承认 → .env 不被刷新 → 下一轮探活仍坏 → 自锁（不恢复）。",
        "chain": "门禁1.5/1.6 拒写 → keepalive 当轮跳过 → 下一轮（30min）→ 坏会话持续",
        "root": "回写失败后无立即重试与明确告警 → 故障不被发现。",
        "verify": "日志出现 BCC-072 即该账号 profile 登录态需人工重新登录（不自动重扫）。",
    },
    "MEM-007": {
        "design": "凭证唯一存储形态是 Fernet 加密的 <env_path>.enc；主密钥不可用（未登录）时读写凭证都必须显式失败。",
        "contract": "member_ctx.write_env_file / parse_env_dict 不得回落到明文；明文 .env 即使存在也不被读取。",
        "deviation": "旧实现主密钥不可用时回落明文读/写（明文凭证静默落盘或静默被读取）。",
        "chain": "调用方 → member_ctx.write_env_file/parse_env_* → master_key() 为空 → 旧实现走明文分支",
        "root": "明文兼容分支与主密钥门禁缺失，使「未登录」被当成「可用明文模式」。",
        "verify": "无主密钥进程内调用 write_env_file：抛错；调用 parse_env_dict 且仅有明文 .env：返回空（不读明文）。",
    },
    "AUTH-054": {
        "design": "凭证必须加密落盘（<path>.enc）；加密不可用时应显式失败并上报，绝不静默写出明文凭证。",
        "contract": "save_credential 的任何写盘路径都不允许产生明文 .env；失败必须响亮（error 级）且不推进任何基线。",
        "deviation": "加密写盘失败（主密钥缺失/磁盘故障）时，旧实现存在降级为明文 set_key 的路径。",
        "chain": "scan_login/refresh_cookie_to_env → save_credential → member_ctx.write_env_file → 异常",
        "root": "明文降级路径未被根除（DY_ALLOW_PLAINTEXT_ENV 逃生口 + is_member_env 为假时的 set_key 分支）。",
        "verify": "在无主密钥进程内保存凭证：应报 AUTH-054 且磁盘不出现明文 .env。",
    },
    "ACC-017": {
        "design": "重捕整段操作（停守护 → 独占 profile → 拉回 BCC）必须在按账号维度的所有权锁内完成。",
        "contract": "同一账号任一时刻只有一个 profile 所有者。",
        "deviation": "未能取得所有权锁 → 降级为无锁执行（有双实例风险）",
        "chain": "_do_auto_recapture → ProfileOwnership → _quit_browser_daemon",
        "root": "browser_gate 不可导入或锁获取异常",
        "verify": "grep 日志 ACC-022；正常情况应恒不出现。",
    },
    "ACC-018": {
        "design": "从 profile 读凭证的整段操作同样必须在所有权锁内完成。",
        "contract": "同一账号任一时刻只有一个 profile 所有者。",
        "deviation": "未能取得所有权锁 → 降级为无锁执行（有双实例风险）",
        "chain": "_do_recapture_from_profile → ProfileOwnership → _quit_browser_daemon",
        "root": "browser_gate 不可导入或锁获取异常",
        "verify": "grep 日志 ACC-023；正常情况应恒不出现。",
    },
    "ACC-019": {
        "design": "扫码整段操作（停守护 → 独占 profile → 拉回 BCC）必须在"
                  "所有权锁内完成；否则与并发拉起 BCC 撞车 → 抢锁 → "
                  "扫码页加载异常/授权回执读不到。",
        "contract": "同一账号任一时刻只有一个 profile 所有者。",
        "deviation": "未能取得所有权锁 → 降级为无锁执行（有双实例风险）",
        "chain": "_do_scan → ProfileOwnership → _quit_browser_daemon → enrich_auth",
        "root": "browser_gate 不可导入或锁获取异常",
        "verify": "grep 日志 ACC-024；正常情况应恒不出现。",
    },
    "ACC-020": {
        "design": "凭证失效时打开有头浏览器是**正当且被引导**的观测入口，"
                  "同时必须暂停全部任务（无效凭证下继续发送=风控暴露）。",
        "contract": "凭证失效 ⇒ 任务暂停 + 有头观测放行；凭证有效且任务在跑 ⇒ 拒绝有头。",
        "deviation": "检测到凭证失效，已按契约暂停任务并放行有头观测（信息级）",
        "chain": "POST /open-browser → verify_account(wp!=ok) → adm.pause() → BCC /show",
        "root": "凭证失效本身（UID 漂移 / 签名过期 / 登录态被下线）",
        "verify": "1) 日志出现 ACC-020 且引擎 state=PAUSED；"
                  "2) 之后扫码成功 → 凭证回写 → 任务可恢复。",
    },
    "ACC-021": {
        "design": "暂停引擎是凭证失效时的保护动作，失败不得阻断"
                  "「打开有头浏览器观测」这条用户显式路径。",
        "contract": "暂停失败只告警，仍放行观测。",
        "deviation": "引擎暂停调用抛异常",
        "chain": "POST /open-browser → adm.pause() 异常",
        "root": "引擎状态机处于不可暂停态 / 事件循环不可用",
        "verify": "人工确认引擎状态；若未暂停应手动在界面停止。",
    },
}

SPECIAL = {}  # 特码覆盖: code -> (常见原因, 建议处置)；未覆盖回退域默认
