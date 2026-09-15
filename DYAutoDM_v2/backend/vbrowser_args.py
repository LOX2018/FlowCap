"""指纹浏览器 —— 启动参数常量（Chromium 开关 / 假媒体设备）

## 为什么独立（2026-09-15 大单文件打散）

原 `vbrowser.py`（1369 行）内联了约 53 行 Chromium 启动参数与假媒体设备列表。
抽出后便于审计指纹相关开关（风控敏感项集中可见）。

## 说明
**纯搬移**——每个开关、每个参数逐字节不变。
"""

_FAKE_MEDIA_ARGS = [
    "--use-fake-device-for-media-stream",
    "--allow-file-access-from-files",
    "--mute-audio",
    "--deny-permission-prompts",
]

_CHROME_ARGS = [
    # ⚠️ 2026-09-13 实测移除 --disable-gpu：
    #   原为「无头稳定性」保留，但它使内核的 GPU/WebGL 指纹伪装失效，实测
    #   WebGL renderer 变成 "ANGLE (Microsoft, Microsoft Basic Render Driver ...)"
    #   ——Windows 软件渲染兜底值，正常用户浏览器绝不会是这个值，是极强的
    #   自动化/虚拟机特征（对照：去掉本参数后为真实 "AMD Radeon(TM) Graphics"）。
    #   现所有启动均已转为「真有头+最小化」，不再需要它换稳定。
    #   如需回退：设环境变量 DY_DISABLE_GPU=1。
    "--disable-dev-shm-usage",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-background-networking",
    "--disable-extensions",
    "--disable-sync",
] + _FAKE_MEDIA_ARGS

# 2026-09-09 变更（用户要求）：【废弃】「真有头 + 窗口移出屏幕」伪装模式。
#
# 原设计（2026-09-06）：BCC「无头」改用真有头窗口移到屏幕外（-32000,-32000），
#   理由是抖音风控能识别 Chromium headless，需保持有头特征。
#
# 废弃原因：
#   1) 副作用严重 —— 持久化 profile 会把屏外坐标写进 Preferences，导致后续
#      可见启动（扫码登录 / 查看模式）窗口恢复在桌面外，用户无法扫码。
#      为兜底不得不额外维护 _ensure_window_visible() 归位逻辑。
#   2) 实测证伪必要性 —— 2026-09-08 双账号实测：纯 native 无头下
#      小助理 ws 发送成功、张老师 wp 发送成功，无头完全可用（见下 native 分支注释）。
#   3) 用户明确要求删除全部移屏外设定。
#
# 现在 headless 一律走纯 Playwright headless（native），不再有任何屏外窗口。
# _HEADLESS_DISGUISE_ARGS 保留为空列表仅为兼容旧引用，调用方加它是无操作。
_HEADLESS_DISGUISE_ARGS: list = []

# 窗口归位判定（2026-09-06 二次修正，实机数据）：
# 伪装模式写 --window-position=-32000，但 Chromium 会把窗口钳制到虚拟桌面
# 边界，实测 profile 里残留的 window_placement.left = -26214（四川工伤张老师
# 实测）/ -1268（尚进工伤小助理，窗口主体在屏外仅边角在屏内）。旧阈值
# 30000 > 26214，把屏外遗留误判成「多显示器正常负坐标」直接跳过归位
# → 可见启动窗口仍落在桌面外。
# 新判据 = EnumDisplayMonitors 拿真实全部显示器工作区 +「与任一工作区
# 可见相交 ≥200px」。跨屏窗口只要主体落在任何一块真实屏幕上都放行；
# -26214 / -1268 两类伪装遗留与所有真实屏幕相交均 <200px，正确归位。
import ctypes as _ctypes
from ctypes import wintypes as _wintypes


