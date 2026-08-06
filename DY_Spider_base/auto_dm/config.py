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

# 直播房间号（网页直播地址 https://live.douyin.com/<LIVE_ID>）
LIVE_ID = '992931212705'

# 私信内容
DM_MESSAGE = '你好'

# 最多私信多少人（达到后停止）
MAX_TARGET = 2

# 每次发送间隔（秒，防风控）
SEND_INTERVAL = 30.0

# 是否启用直播间弹幕来源
ENABLE_LIVE_CHAT = True

# 是否启用中控台浏览器采集来源（补充）
ENABLE_WEB_PROBE = True

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
# 设为 False（或内核不可用）时自动回退到原生 Playwright，行为不变。
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
