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
    "RECV": ["接收守护", "WS 断连(无 ping 30s 被掐) / msg 解析 / 方向判定", "每 30s Connection lost=ping_interval 缺失；KICK 时 protobuf 解析失败实为 JSON 风控响应"],
    "SEND": ["发送链路", "统一闸门限速 / 通道回退 / KICK 风控", "rate_limited=8s 闸门属预期；KICK=账号信誉；必须 DB 落库 role=me 才算成功"],
    "SYS": ["系统与启动", "daemon 拉起 / 路由挂载 / 配置", "BCC frozen exe 必须带 DY_APP_ROOT；并行拉起 ~15s"],
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
                     "占位不入库；③ WS 必须带 ping 保活（否则 30s 被服务端掐断）。",
        "chain": "recv_daemon 建 WS → 服务端推 → _handle → 落 dm_messages → （B机制）"
                 "回填会话昵称 → 前端轮询读库。",
        "verify": "日志 WS 断连计数（稳态 2 分钟应 0）；DB 新消息 ts 增量；"
                  "/status 的 connected/conv_count。",
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
    "SEND-037": {"meaning": "[调度] dm_dispatch 接入失败，已放弃发送（不再回退直发绕过风控闸门）", "file": "core/dispatch.py", "line": 0},
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
    "LIVE-006": {"meaning": "昵称加密] 检测到昵称加密且 sec_uid 为空（累计  次）。        这是监测账号凭证/会话异常的典型表现。        请对该监测账号执行【重", "file": "core/live_hook.py", "line": 308},
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
}

SPECIAL = {}  # 特码覆盖: code -> (常见原因, 建议处置)；未覆盖回退域默认
