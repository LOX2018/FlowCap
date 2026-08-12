# coding=utf-8
"""自动私信模块配置（融合 DYchajian 业务逻辑到 DouYin_Spider 底座）。

设计原则：
- 私信目标来源双路兜底：
  1) 直播 WebSocket 弹幕（WebcastChatMessage）——消息自带 user.id(数字uid)/sec_uid/nickname，
     直接可用，无需查询，最稳（这是原 DYchajian 卡在 get_user_info 的根因解法）。
  2) 中控台浏览器采集（web_probe）——保留 DYchajian 原有能力作补充来源。
- 私信发送统一走 dy_apis.douyin_api 的 create_conversation + send_msg（imapi 私有接口），
  优先用弹幕消息自带的数字 uid，彻底跳过 get_user_info 风控接口。
"""

import random

# 直播房间号（网页直播地址 https://live.douyin.com/<LIVE_ID>）
LIVE_ID = '992931212705'
# 直播间链接（用户在 GUI「直播间链接」输入框填写的原始地址，可为空）。
# 点「解析房间号」会从中提取并写入 LIVE_ID；保存时会一并写回本文件。
LIVE_URL = ''

# 采集开关：是否启用弹幕监听 / 中控台采集（两者去重合并，避免同一人重复私信）。
# 详见 gui.py「采集配置」与 core.py 跨来源去重逻辑。
ENABLE_DANMAKU = True
ENABLE_CONSOLE = True

# 直播间未开播时的轮询间隔（秒）：启动监听前若检测到未开播，
# 将每 LIVE_POLL_INTERVAL 秒复查一次 room_status，开播后自动开始监听。
LIVE_POLL_INTERVAL = 30

# 运行期登录态心跳间隔（秒）：WS 监听建立后，每 WS_HEARTBEAT_INTERVAL 秒用 get_my_uid 探活一次，
# 一旦登录态失效（cookie 过期/账号被挤下线）即自动触发重新扫码（rescan_and_rebuild），
# 避免“弹幕悄悄全部被加密、私信签名被 KICK”却无人察觉。
WS_HEARTBEAT_INTERVAL = 300

# 启动自动私信时是否【强制重新扫码、完全不读取磁盘旧凭证】。
# True  = 每次点“启动自动私信”都打开浏览器重新扫码，忽略任何已存在的 .env 凭证
#         （最干净，杜绝旧 cookie/签名导致的昵称加密、私信 KICK；代价是每次都要扫码）。
# False = 走凭证新鲜度判断：凭证完整且较新则跳过扫码（快速启动，复用已登录/守护保活的凭证），
#         仅过期/缺失才重扫。
FORCE_RESCAN_ON_START = False

# 私信接收守护配置（独立常驻进程 auto_dm.recv_daemon，多账户隔离）。
# 接收通道是 frontier-im.douyin.com 的私有长连接，与发送（imapi.douyin.com）同源但独立，
# 因此单独跑一个进程最稳：GUI 关闭也不丢消息，各账号私信按 conversation_id 隔离。
RECV_DAEMON_HOST = "127.0.0.1"
RECV_DAEMON_PORT = 9912

# 私信内容（词库形式）：发送时从 DM_MESSAGE_POOL 中随机抽取一条，降低机器批量感。
# 兼容旧字段 DM_MESSAGE（单条）：当词库为空或随机失败时使用。
DM_MESSAGE_POOL = ['你好，欢迎留言咨询唐律师工伤,留个方式，唐律下播后帮你分析', '老乡 看到你在唐律师直播间咨询工伤问题，我是他的助理，你可以留个☎️方式，我们帮你看下等级和赔偿 [握手]', '唐律还在直播，我是助理，可以留个联系.方式，唐律下播后帮你分析']
# 每条词库的启用状态（与 DM_MESSAGE_POOL 等长）。False=停用，不参与随机抽取。
# 若长度与 POOL 不匹配，pick_dm_message 会自动按"缺省启用"补齐。
DM_MESSAGE_ENABLED = [True, True, True]
DM_MESSAGE = DM_MESSAGE_POOL[0] if DM_MESSAGE_POOL else '你好'


def pick_dm_message():
    """从【已启用】词库中随机抽取一条；全部停用或词库为空时退回 DM_MESSAGE。"""
    pool = DM_MESSAGE_POOL
    if DM_MESSAGE_ENABLED:
        active = [t for i, t in enumerate(pool) if i < len(DM_MESSAGE_ENABLED) and DM_MESSAGE_ENABLED[i]]
        # 若 ENABLED 比 POOL 短，多出的项默认视为启用
        active += [t for i, t in enumerate(pool) if i >= len(DM_MESSAGE_ENABLED)]
    else:
        active = pool
    if active:
        return random.choice(active)
    return DM_MESSAGE

# 最多私信多少人（达到后停止）
MAX_TARGET = 3

# 每次发送间隔（秒，防风控）
SEND_INTERVAL = 60.0

# 捕获到评论/弹幕后，延迟多少秒再发送私信（模拟真人“看到弹幕→过一会再私信”的节奏，降低风控识别）。
# 支持随机抖动：设为 (min, max) 元组时，每条目标随机取 [min, max] 区间的整数秒；
# 设为单个数字时退化为固定延迟。延迟计时从“捕获时刻”算起，非入队时刻。
SEND_DELAY_SEC = (50, 120)

# 是否启用直播间弹幕来源
ENABLE_LIVE_CHAT = True

# 是否启用中控台浏览器采集来源（补充/兜底，非必需，已不推荐）。
# 注意：直播间“评论区/公屏”与“弹幕”在抖音后端是同一 WebSocket 流(WebcastChatMessage)，
# 已由 live_hook 完整接收，无需本模块。本 DOM 扫描依赖 WEB_PROBE_ROOM_URL 且选择器易失效，
# 平时建议保持 False。
ENABLE_WEB_PROBE = False

# 是否启用私信发送（False 时仅采集不发送，用于调试）
ENABLE_SEND = True

# 中控台浏览器采集相关（ENABLE_WEB_PROBE=True 时生效）
# 中控台登录页 / 房间页 URL（按你的实际使用中控台填写）
WEB_PROBE_ROOM_URL = ""          # 例如 https://creator.douyin.com/... 或直播中控台地址
WEB_PROBE_USER_DATA_DIR = "pw_profile_probe"
WEB_PROBE_HEADLESS = False

# ---- 指纹浏览器内核后端（可选，默认关闭）----
# 设为 True 时，3 处浏览器使用点（扫码抓签名 / 中控台采集 / 跳转解析）改用指纹内核，
# 由 VB_MODE 决定具体内核：
#   "exe"  -> fingerprint-chromium 的 chrome.exe（推荐：无需装 GUI 客户端，源码级指纹伪装）
#   "cdp"  -> VirtualBrowser/Ant-Browser 本地服务 + connect_over_cdp 接管
# 【硬性约束】只用指纹浏览器，禁止使用原生 Playwright：本开关必须保持 True，
# 指纹内核不可用（exe 内核缺失 / cdp 服务不可达）时直接报错，绝不回退原生内核。
USE_VIRTUAL_BROWSER = True
VB_MODE = "exe"
# exe 模式：fingerprint-chromium 编译产物 chrome.exe 的相对路径
VB_CHROME_EXE = "vb_chromium/ungoogled-chromium_148.0.7778.215-1.1_windows_x64/chrome.exe"
# cdp 模式：VirtualBrowser/Ant-Browser 本地服务地址（客户端启动后默认监听 9000）
VB_API_BASE = "http://localhost:9000"
# cdp 模式：在客户端中提前创建好的环境 ID（worker-id）
VB_ENV_ID = 1
# 启动环境 / 连接 CDP 的超时（秒）
VB_LAUNCH_TIMEOUT = 30
