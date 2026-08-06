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
