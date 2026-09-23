# coding=utf-8
"""浏览器容器守护进程（Browser Context Container, BCC）

取代旧版 CredentialKeeper 的"临时开浏览器抓凭证"模式：
- 启动时 launch_persistent_context 持有该账号 profile 的【唯一】浏览器 context，
  整个进程只此一个 Playwright browser，所有浏览器任务排队串行执行（asyncio.Lock）。
- 暴露 HTTP API 给 backend / recv-daemon / link_resolve / web_probe 调用，
  调用方不再各自 launch_persistent_context（消除抢 profile 锁的根因）。
- context/page 失活时自愈重启（profile 锁丢失 / 崩溃后自动恢复）。
- 凭证保活（CredentialKeeper）作为内部心跳任务：周期性探活 + cookie 失效时调
  /scan_login 自我刷新（不再单独开浏览器）。

运行方式（Tauri sidecar，沿用 dyautodm-browser-daemon exe 名）：
    dyautodm-browser-daemon --account X --port P

HTTP API：
    GET  /status             健康检查（context/page 存活、当前登录 uid、profile 路径）
    POST /cookie             读实时 cookie 返回 + 写回 .env（给 recv-daemon 用）
    POST /user_info          浏览器页面内 fetch 批量查 sec_user_ids → 昵称/头像
    POST /resolve_url        浏览器打开链接 → 跟随跳转 → 抠 live_id（替代 link_resolve）
    POST /scan_login         扫码登录/刷新凭证（force=True 重新扫码）
    POST /refresh            兼容旧接口（= /scan_login force=False）
    POST /quit               优雅退出
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import threading
import time
from typing import Any

from fastapi import FastAPI
from loguru import logger

from daemon.bcc_lease import (
    ContainerBusy,
    _lease_lock, _is_busy,
    _scan_exclusive_get, _scan_exclusive_set,
    _lease_reset, _lease_current, _lease_status,
    _lease_acquire, _lease_renew, _lease_release, _lease_owned_by,
    LEASE_PRIO_TTL_LIMIT, LEASE_PRIO_NAME,
)

from daemon.bcc_login import BccLoginMixin
from daemon.bcc_capture import BccCaptureMixin
from daemon.bcc_audit import BccAuditMixin


# ════════════════════════════════════════════════════════════════════════════
# 🔴 2026-09-23【模块身份归一 —— 防「同进程双份模块」】
#
# 本文件被 PyInstaller 以 **`__main__`** 身份启动，而 `daemon/bcc_routes.py`
# （P3-5 Step 2 路由抽取后新增）用 `from daemon.browser_daemon import _state`
# 读全局状态 ⇒ 同一份源码在**同一进程内被加载两次**：
#     ① `sys.modules["__main__"]`              ← main() 往里写 account/port 的那份
#     ② `sys.modules["daemon.browser_daemon"]` ← bcc_routes 的 import 触发的那份
#
# 实测后果（2026-09-23，v0.44.52 阻断级）：
#   · startup 事件读到**空** `_state`（account="" / port=0）
#     → `BrowserContainer(account="")` → `env_path` 为空
#     → `BCC-051`「凭证文件不可用」→ **浏览器容器永远起不来**
#     （/status 恒 alive:false；BCC 日志只留这一条错误）
#   · 第二份模块重新执行本文件的模块级 `logger.remove()`
#     → **删掉 main() 刚加的文件 sink** → BCC 当天日志恒 0 字节
#       （文件已创建、却一行不写；这正是本案最难定位的表象）
#
# 定式：凡「以 __main__ 运行、又被同进程按包名 import」的入口，都必须在
# **任何反向 import 发生之前**把自身登记为规范模块名。先到先得，故用 setdefault：
#   · 以包名正常 import（`python -m daemon.browser_daemon` 等）→ 已是自己，不动它；
#   · 以 __main__ 启动 → 补上规范名，消除第二份实例。
# 事故复现与修复验证见 `backend/test_bcc_module_identity_guard.py`
# （注入 DIFF 形态必须变红；修复后为 SAME）。
# ════════════════════════════════════════════════════════════════════════════
sys.modules.setdefault("daemon.browser_daemon", sys.modules[__name__])


# 2026-09-13：标记「本进程是浏览器守护」——供 services.browser_gate 豁免
# 自身启动告警（否则 BCC 拉自己的容器会误报 BCC-042 环境分叉）。
os.environ.setdefault("DY_BROWSER_DAEMON", "1")

# 无控制台模式下 sys.stdout/stderr 可能为 None
if sys.stdout is not None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if sys.stderr is not None:
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from vbrowser import app_root

_ROOT = app_root()
_DAEMON_DIR = os.path.join(_ROOT, "auto_dm")

# 错误码日志补丁：loguru 会把第一个位置参数当格式模板，导致
# logger.warning(f"[BCC-006] " + "描述") 的描述被丢弃（运行日志只剩代码）。
# 此处安装兼容层，让「码 + 描述」正常输出（一处生效，覆盖全项目 345 处调用）。
try:
    from utils.code_logger import install_code_logger_patch as _inst_code_log
    _inst_code_log()
except Exception as _e_code_log:  # 补丁失败绝不阻塞启动
    import sys as _sys_cl
    print(f"[code_logger] 补丁安装失败（不影响运行）: {_e_code_log}", file=_sys_cl.stderr)

logger.remove()
logger.add(
    sys.stderr,
    level="INFO",
    colorize=False,
    enqueue=True,
    format="{time:HH:mm:ss} | {level: <8} | {message}",
)

app = FastAPI(title="browser-container")


# ============================================================================
# P2-B（2026-09-06 第五轮治理，09 台账 5.3）：独占期 busy 快速失败
# ----------------------------------------------------------------------------
# scan_login 要先关闭容器 context 独占 profile 完成扫码，再重启容器——
# 独占窗口内（可达 300s）其他浏览器端点若照常排队，会干等到 HTTP 超时，
# 上层（发送降级/AI 回复）还白白重试。现用 _scan_exclusive 标志 +
# ContainerBusy 异常 + 全局异常处理器：独占期内其他端点立即返回
# {ok:false, busy:"scan_login"}，FastAPI HTTP 层不阻塞、不排队。
# ============================================================================
# 后写者的 lease_id 生效，先写者从此无法 release（_lease_release 返回 not_holder），
# 该租约要等 TTL（最高 600s）才被惰性回收，期间全部业务被 403/busy 挡回。
# 用 **threading.RLock**（不是 asyncio.Lock）：因为 keepalive 跑在子线程、
# 且 `_lease_acquire` 内部会再调 `_lease_current`（可重入），RLock 最合适。
# 定义必须早于 _is_busy / _lease_* 的使用点。




# ════════════════════════════════════════════════════════════════════════════
# 浏览器租约（Lease）—— 2026-09-13 调度器体系 S2
#
# ## 为什么需要（根因）
#   原设计里 services/browser_gate.py 声称"要求 BCC 让出 profile"，但：
#     ① 它读的 st["exclusive"] 来自 /status，而 /status **从不返回该字段**
#        （实测只返回 alive/account/profile/uid/last_refresh/logged_in/version）
#        → j.get("exclusive") 恒为 None → if not st["exclusive"] 恒为真
#     ② 全仓没有任何代码写入 /status.exclusive
#     ③ BCC 内部真正的独占标志是私有 _scan_exclusive，未对外暴露
#   ⇒ gate 的"独占"分支永远只打一行日志，然后 return ok=True（静默假成功）。
#
# ## 正确抽象：租约（而不是"让出"）
#   BCC 始终是 profile 的唯一所有者（独立进程，跨进程"交出 profile"不可能实现）。
#   业务操作向 BCC **申请租约**，BCC 仲裁授予；操作结束 release，或 TTL 到期自动释放。
#
# ## 与既有 _scan_exclusive 的关系
#   _scan_exclusive 是"scan_login 独占"的临时标志，保留兼容；
#   租约成为**统一入口**，scan_login 也走租约（同时置 _scan_exclusive 以兼容旧检查）。
#
# ## 优先级与 TTL 硬上限（防"P2 霸占"把调度器架空）
#   P0 用户显式（更新会话/打开浏览器/扫码）  TTL ≤ 300s
#   P1 业务自动（AI 回复/私信发送/凭证刷新） TTL ≤ 180s
#   P2 后台保活（keepalive/昵称预热/uid轮询）TTL ≤  30s
#
# ## 不做真抢占
#   浏览器操作大多不可中断（DOM 流程、context 重建），抢占会导致状态不一致。
#   用「P2 限时 + 快速失败 + retry_after」解决"低优先级霸占"。
# ════════════════════════════════════════════════════════════════════════════
# 2026-09-14 v0.43.11：P0 300 -> 600s。实机实测「更新会话」整轮可达 302s+
# （滚动 10+ 轮 × 每轮 30~40s），300s 上限导致租约中途被 BCC-046 强制回收，
# 释放时 lease_id 已不匹配（ok=False）。放宽到 600s 覆盖真实耗时。





# 全局状态
_state: dict[str, Any] = {
    "account": "",
    "port": 0,
    "started_at": time.time(),
    "container": None,  # BrowserContainer 实例
    "keepalive_thread": None,
    "keepalive_stop": None,
}

# 可见性切换冷却期时长（秒）。有头指纹内核冷启动实测约 2~3 分钟，取 180s 兜底。
# 模块级常量（勿放 __init__ 局部——set_visible/_do_switch_background 等
# 多个方法都要引用，局部作用域会 NameError）。
_SWITCH_COOLDOWN_SEC = 180


# 模块级 hook 脚本：截 im/user/info 响应（必须在 context 创建后、goto 前 add_init_script 注入）
# V16 踩坑：evaluate 注入太晚（前端已发完 im/user/info），必须 add_init_script 在 goto 前
# ---------------------------------------------------------------------------
# 2026-09-14 v0.43.9：DOM 滚动抓取（替代已失效的 im/user/info hook）
#
# 取证（2026-09-14 实机，非推断）：
#   1. 全量 hook fetch+XHR（不预设接口名）+ 滚动 + 点击会话 → 捕获 **0 条**响应。
#      即抖音前端不再为会话列表发任何网络请求，数据在首包 + 首次渲染缓存里。
#      ⇒ 「等 im/user/info」是等一个不存在的请求，字段改没改都无意义。
#   2. 会话列表是虚拟列表：DOM 同时只渲染 12~14 项，但**滚动会换内容**。
#      实测滚动 10 轮 → 累计抓到 **45 个昵称 + 头像**（DOM 直读，零网络请求）。
#   3. DOM 的 title 文本形如 "昵称<换行>时间"，需按首行取纯昵称。
# ---------------------------------------------------------------------------
# 2026-09-15 修复（**入口 crash**）：本文件同时被当作三种身份加载 ——
#   ① PyInstaller **入口脚本**（`__package__` 为空）→ 相对导入必崩；
#   ② `daemon.browser_daemon` 包内模块（有父包）；
#   ③ 打包后 JS 常量以 `daemon.browser_daemon_js` 形式在 PYZ 内。
# 原写成 `from .browser_daemon_js import ...`（相对导入）→ 在①下必抛
#   `ImportError: attempted relative import with no known parent package`
#   → **BCC sidecar 启动即崩，二进制完全不可用**（实测部署 exe 报此错）。
# 正解：多级兼容导入，逐级回退（任一级成功即可）。
#   注意：`browser_daemon_js.py` 位于 `backend/daemon/` 下，PyInstaller 以
#   `daemon.browser_daemon_js` 收录（顶层名 `browser_daemon_js` 收集不到）。
try:  # ② 包内模块模式
    from .browser_daemon_js import (
        CAP_DOM_SWEEP_JS, CAP_DOM_SCROLL_JS, CAP_USERINFO_HOOK_JS, CAP_WP_MESSAGE_HOOK_JS,
        CAP_IDB_USERINFO_JS,
    )
except ImportError:  # ① 入口脚本模式（PyInstaller / python xxx.py）
    try:
        from daemon.browser_daemon_js import (
            CAP_DOM_SWEEP_JS, CAP_DOM_SCROLL_JS, CAP_USERINFO_HOOK_JS, CAP_WP_MESSAGE_HOOK_JS,
            CAP_IDB_USERINFO_JS,
        )
    except ImportError:
        import os as _os
        import sys as _sys
        _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
        from browser_daemon_js import (
            CAP_DOM_SWEEP_JS, CAP_DOM_SCROLL_JS, CAP_USERINFO_HOOK_JS, CAP_WP_MESSAGE_HOOK_JS,
            CAP_IDB_USERINFO_JS,
        )

def _app_version() -> str:
    """本守护进程的构建版本（读 exe 同级 version.json；失败=unknown）。

    2026-09-13：与 backend /api/version 配套，解决「前端新/后端旧」无校验缺口。
    sidecar 与桌面端分别构建，必须能自查版本，避免部署未生效却无人察觉。
    """
    try:
        from _build_version import BUILD_VERSION as _bv
        if _bv:
            return str(_bv)
    except Exception:
        pass
    try:
        import json as _json
        base = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False)
                                              else __file__))
        for rel in ("version.json", os.path.join("..", "version.json"),
                    os.path.join("..", "..", "version.json")):
            fp = os.path.normpath(os.path.join(base, rel))
            if os.path.isfile(fp):
                with open(fp, encoding="utf-8") as f:
                    v = (_json.load(f) or {}).get("version")
                if v:
                    return str(v)
    except Exception:
        pass
    return "unknown"


def _cred_refresh_mode() -> str:
    """读取「凭证更新方式」（两套路径共存，由用户配置）。

    取值：
      observe（默认）—— 观测态静默更新：保活心跳读实时 cookie + 页面最新签名
                        写回 .env，不弹窗、零打扰，适合无人值守。
      popup          —— 仅弹窗激活更新：检测到页面需重激活时弹指纹浏览器，
                        请用户手动点一下，适合习惯人工确认的账号。
      both           —— 观测优先；确认页面登录态失效才弹窗。

    取值优先级：统一配置中心(app_config.general.cred_refresh_mode)
              → 环境变量 DY_CRED_REFRESH_MODE → 默认 observe。
    """
    try:
        from services import app_config
        v = app_config.get("general", "cred_refresh_mode", None)
        if v:
            return str(v)
    except Exception:
        pass
    return (os.environ.get("DY_CRED_REFRESH_MODE") or "observe").strip() or "observe"


class BrowserContainer(BccLoginMixin, BccCaptureMixin, BccAuditMixin):
    """常驻持有该账号 profile 的唯一 Playwright context。

    所有浏览器操作通过 submit(coro) 入队，内部 asyncio.Lock 串行执行，杜绝并发抢锁。
    context/page 失活时 _ensure_alive 自愈重启。
    """

    def __init__(self, account: str) -> None:
        self.account = account
        # 可见模式开关（2026-09-12 用户需求根治；2026-09-13 风控语义修正）：
        #   False(默认) = 无头请求 → vbrowser 层转为「真有头+窗口最小化」
        #                 （有头特征与扫码/查看一致，杜绝环境跳变；最小化不
        #                 污染 profile，也不打扰用户）
        #   True  = 有头可见（窗口就是本容器，用户可直接查看登录态；
        #           同一实例继续保活+回写凭证，**不与"打开浏览器"抢 profile**）
        # 由 POST /show 动态切换（重启 context 生效），不读环境变量。
        # ⚠️ 2026-09-14【用户重新拍板】默认真无头（纯 native headless）。
        #    本节历史上曾写「纯 headless 会被抖音识别→登录态强制下线，故实际启动
        #    恒有头」——该结论未在新形态下复现；且用户明确要求「非业务需要（需要
        #    观测）默认以无头形式运行，观测态才有头」。**启动层不得偷偷改有头**：
        #    调用方传 headless=True 就必须得到真无头，否则调用方意图与实现不一致。
        #    转有头有两个正当入口：① POST /show 动态切换；② 双击「打开指纹浏览器」
        #    （login_api 的 get_login_auth / open_browser 走 headless=False）。
        self._headless: bool = True
        self._lock = asyncio.Lock()
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None
        # P2-A：独立导航 tab（resolve_url 专用，不占用常驻 chat 页）
        self._nav_page = None
        # ENG-020：观测专用导航 tab（exec_js / capture_wp_messages 用，
        # 使「只读取数」不再把主 page 导航走）
        self._diag_page = None
        self._backend = ""  # "exe" / "cdp"
        self._profile_dir = ""
        self._started = False
        self._last_uid: Any = None
        self._last_refresh: float = 0.0
        # 昵称缓存：(采集时间戳, {sec_uid: {...}})。配 _prewarm 使用，
        # 避免每次「更新会话」都重跑 176s 的滚动捕获（08 §三十七）。
        self._userinfo_cache: tuple | None = None
        # 2026-09-17 修补（OCR 审查 HIGH）：context 代次。每次 _launch 成功
        # （context 重建）后 +1，用于让「上下文相关缓存」在重建后立即失效
        # （昵称缓存历史上是永不失效的僵尸值，见 capture_userinfo_via_browser）。
        self._context_generation: int = 0
        # 2026-09-14 v0.43.11：预热进行中标记。capture_userinfo_map 据此决定
        # 是否「稍等一下预热」（预热不占租约，只占 _lock，见 _exec internal）。
        self._prewarm_running: bool = False
        # _loop 由 FastAPI startup 持有，submit 用它把协程投递到主事件循环
        self._loop: asyncio.AbstractEventLoop | None = None
        # 2026-09-12 切换冷却期：set_visible 无头↔有头重启 context 后，给新
        # context 一段加载窗口。此期间 _ensure_alive / keepalive 探活只告警、
        # 绝不强杀重启 —— 否则刚加载一半的页面被误杀，抖音会弹「环境异常」
        # （实测：13:52 切有头 → 13:54 探活误判失效 → BCC-006 重启 → 页面
        # 加载中断 → 抖音异常页）。
        self._switch_cool_until: float = 0.0
        # 切换中标志：_launch 在后台任务执行，期间探活/业务调用短暂失败只告警。
        self._switching: bool = False
        # 切换起始时间（看门狗用：_switching 卡死超时后强制复位，保证窗口能被重新唤醒）
        self._switch_started_at: float = 0.0

    async def start(self) -> None:
        """启动浏览器 context（持有 profile 锁）。失败抛 RuntimeError。"""
        if self._started:
            return
        self._loop = asyncio.get_event_loop()
        await self._launch()
        self._started = True
        logger.info(f"[bcc] 浏览器容器启动成功 account={self.account} profile={self._profile_dir}")

    async def _launch(self, headless: bool | None = None) -> None:
        from auto_dm import accounts as _acc
        from auto_dm import config as _cfg
        from auto_dm.vbrowser import should_use_vb, launch_async
        env_path = _acc.env_path_of(self.account)
        if not env_path:
            # ⚠️ 2026-09-13 实测事故：凭证读不到时 _launch 抛错 → _ensure_alive
            # 判定「context 失活」→ 每 3~4 秒重启一次（日志 BCC-006 刷屏），
            # 用户看到的就是「浏览器窗口一闪一闪」（快闪）。
            # 这是**不可自愈**的条件：env 路径拿不到，重启一万次也一样。
            # 必须熔断并给出可操作提示，绝不进入重启风暴。
            _msg = (f"账号「{self.account}」的凭证文件不可用（env_path 为空）——"
                    f"常见原因：① 应用未以会员身份运行（子进程缺 DY_MEMBER/"
                    f"DY_MEMBER_KEY，读不到 .env.enc）；② 账号未登记进索引；"
                    f"③ 账号已被删除。请从应用界面启动浏览器守护，"
                    f"或在账号管理页重新登记该账号。")
            logger.error(f"[BCC-051] " + f"[bcc] {_msg}（已熔断，不再自动重启）")
            # 置长熔断：让 _ensure_alive 在较长时间内只告警不重启
            try:
                self._fatal_until = time.time() + 1800   # 30 分钟
            except Exception:
                pass
            raise RuntimeError(f"[bcc] {_msg}")
        self._env_path = env_path
        self._profile_dir = _acc.profile_dir_of(env_path)
        if not self._profile_dir:
            raise RuntimeError(f"[bcc] 无法推导 profile 目录: account={self.account}")
        # 2026-09-13：浏览器环境被清空后（用户要求摧毁旧环境、全新扫码），
        # profile 目录不存在属正常 → 首次启动自动创建全新环境，不再报错。
        # 单 profile 铁律不变：仍是该账号独占的那一个目录，不新建临时目录。
        if not os.path.isdir(self._profile_dir):
            try:
                os.makedirs(self._profile_dir, exist_ok=True)
                logger.info(
                    f"[bcc] profile 目录不存在，已创建全新环境: {self._profile_dir}"
                    "（全新环境：需扫码登录建立登录态）")
            except Exception as e:
                raise RuntimeError(
                    f"[bcc] profile 目录创建失败: {self._profile_dir} ({e})") from e
        # 统一调度（BCC 作为 profile 唯一持有者）：重建 context 前先确保旧进程
        # 完全退出（SingletonLock 消失），否则新 launch 会 TargetClosed
        # （close()+stop() 异步，chromium 进程未退净即启动新 context 的竞态）。
        # 所有 _launch 调用点统一走这里，无需各处手动处理。
        await self._wait_profile_released()
        _vb, _vb_mode = should_use_vb(_cfg)
        # 常驻浏览器容器默认无头请求（vbrowser 层转为真有头+最小化）：捕获链路
        # （capture_userinfo_map 被动 hook 截前端自发 im/user/info）经实机验证
        # 有头/无头均 44/44；但 2026-09-13 实证纯 headless 会被抖音识别触发登录态
        # 强制下线，故统一转有头最小化（风控对齐，§24.10 复发修复）。
        # 扫码登录走独立 get_login_auth(headless=False)，需可见 UI，不在此处。
        # ⚠️ 2026-09-13 快闪修复：**重建一律按「最小化」启动**。
        # 原实现在这里传 self._headless，而 set_visible(True) 会把它置为
        # False（并持久保留）→ 之后任何 BCC-006 context 失活触发的重建
        # 都会**再创建一个可见窗口**；而 launch_persistent_context
        # (headless=False) 是「先建可见窗口、再 minimize」→ 中间的时间差
        # 就是用户看到的「快闪」（实测 22:09:02 / 22:10:20 两次重建各闪一次）。
        # 重建是**异常自愈路径**，不该顺带弹窗；用户要可见态时走 /show（只改
        # 窗口状态、不重建 context）。
        # 回退开关：DY_BCC_RESTORE_VISIBLE_ON_RELAUNCH=1（恢复旧行为，仅调试）。
        _restore_vis = (str(os.environ.get(
            "DY_BCC_RESTORE_VISIBLE_ON_RELAUNCH", "")).strip() == "1")
        # ════════════════════════════════════════════════════════════════════
        # 2026-09-19 修正（v0.43.98）：区分「自愈重建」与「用户显式可见性重建」。
        #
        # 原逻辑把所有重建都按「最小化/无头」启动（防快闪），用户点「打开浏览器」
        # 时若需要重建 context，也会被强制拉成无头 —— 在纯 native headless 下
        # 等价于「永远出不了窗口」（实测：setWindowBounds 返回 True 但 OS 可见
        # 窗口数 = 0）。
        # 现改为：调用方显式传 headless 时以它为准（用户显式路径）；未传时
        # 沿用旧的「按无头重建」自愈语义，防快闪行为不变。
        # ════════════════════════════════════════════════════════════════════
        if headless is None:
            _launch_headless = self._headless if _restore_vis else True
            if not _restore_vis and not self._headless:
                logger.info(
                    "[bcc] 容器重建：按最小化启动（不继承上次可见态，避免窗口快闪）；"
                    "需要查看登录态请走 /show")
        else:
            _launch_headless = bool(headless)
            logger.info(f"[bcc] 容器重建（显式指定可见性）: headless={_launch_headless}")
        self._pw, self._browser, self._context, self._backend = await launch_async(
            _vb_mode, _cfg, headless=_launch_headless, user_data_dir=self._profile_dir,
            force=False, account=self.account)
        self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()
        # 2026-09-17 修补（OCR 审查 HIGH）：context 已重建 → **bump 代次**，
        # 并作废与上下文绑定的昵称缓存（原缓存永不失效，跨重建仍被复用 →
        # 归属失效的旧昵称被持续回填，见 capture_userinfo_via_browser 处说明）。
        self._context_generation = getattr(self, "_context_generation", 0) + 1
        self._userinfo_cache = None
        logger.debug(f"[bcc] context 代次 → {self._context_generation}"
                     f"（昵称缓存已作废）")
        # V16 踩坑：add_init_script 必须在 goto 前注入，否则前端已发完 im/user/info 再注入就截不到
        #
        # 2026-09-17 修补（OCR 审查 HIGH —— 重复注入）：
        # 原实现每次 `_launch` 都无条件 add_init_script 两个脚本，而 `_launch`
        # 会被 `_do_switch_background` / `scan_login` / `_ensure_alive` 反复调用。
        # 若 context 未真正重建（同一 context 复用），脚本会在同一页面上**累计
        # 重复注入** → 两个脚本各自包裹 window.fetch/XMLHttpRequest，内层包装
        # 被外层覆盖，先注入者再也观察不到请求（WP 私信通道静默失效）。
        # 现先清空再注入，保证「每个 context 恰好一套」。
        # ════════════════════════════════════════════════════════════════════
        # 2026-09-20 v0.44.0【Camoufox 模式禁止 JS 注入】
        #
        # 这两个 hook 通过 add_init_script 改写 window.fetch / XMLHttpRequest /
        # WebSocket，属「可被 JS 检查发现」的痕迹 —— 正是抖音在**交互时刻**
        # 弹「安全风险…已阻止此次访问」的嫌疑成因（实测：带注入时点「验证码
        # 登录」即弹窗）。
        #
        # Camoufox 的指纹注入在 **C++ 实现层**，本就无需 JS 注入，且**注入会
        # 抵消它的反检测优势**。故该模式下跳过全部 init script。
        #
        # 影响（如实记录）：Camoufox 模式下 WP 私信通道的 JS 劫持不可用，
        # 需改用 Playwright 原生 WebSocket 事件（非页面注入，无痕迹）。
        # ════════════════════════════════════════════════════════════════════
        if self._backend == "camoufox":
            logger.info(f"[bcc] {self.account} 内核=Camoufox → 跳过 JS 注入"
                        f"（C++ 层指纹注入，注入反而留下可检痕迹）")
        else:
            try:
                await self._context.clear_init_scripts()
            except Exception as e:  # noqa: BLE001
                logger.debug(f"[bcc] clear_init_scripts 不可用（不影响本次注入）: {e}")
            await self._context.add_init_script(CAP_USERINFO_HOOK_JS)
            await self._context.add_init_script(CAP_WP_MESSAGE_HOOK_JS)  # 2026-09-05 WP
        # 直接打开 chat 页（前端才会自发调 im/user/info）
        try:
            await self._page.goto("https://www.douyin.com/chat?isPopup=1", wait_until="domcontentloaded", timeout=20000)
        except Exception as e:
            logger.warning(f"[BCC-005] " + f"[bcc] 打开 chat 页失败（不阻塞，后续接口自愈）: {e}")

    async def _ensure_alive(self) -> None:
        """context/page 失活时重启。在 _lock 内调用。

        ⚠️ 2026-09-13 修复「双击唤醒闪退 + 之后无法唤醒」：
        切换期间（_switching）_context 被置 None、_launch 在后台重建 context，
        此时若前端/WP 轮询经 _exec 调到这里，会立刻抛「context 已关闭」→
        误判失活 → 触发重启 → 与后台 _launch 抢 profile → 失败 → 再次误判 →
        **3.6 秒一轮的死循环**（日志 BCC-006 刷屏，浏览器闪退、二次双击无响应）。
        run_keepalive 早有此保护（L1417），但 _exec → _ensure_alive 这条路径没有，
        现补齐：切换中/冷却期一律「等切换完成」而不是判定失活。
        """
        # 切换卡死看门狗：_switching 卡住超过阈值（如 _launch 抛错未复位、
        # 或后台任务被取消）会让窗口永久无法唤醒。冷启动最坏 2~8 分钟，
        # 取 15 分钟阈值，超时强制复位让后续双击能重新走重建流程。
        if self._switching and self._switch_started_at:
            if time.time() - self._switch_started_at > 900:
                logger.error(f"[BCC-006] " + f"[bcc] {self.account} 可见性切换卡死超 900s，强制复位 "
                    f"_switching（否则窗口将永久无法唤醒）")
                self._switching = False
                self._switch_started_at = 0.0
        # 2026-09-13：致命态熔断（凭证不可用等不可自愈错误）——
        # 原逻辑会每 3~4 秒重启一次，用户看到窗口「快闪」。
        # 熔断期内只告警不重启，避免重启风暴与风控暴露。
        _ft = getattr(self, "_fatal_until", 0.0)
        if _ft and time.time() < _ft:
            remain = int(_ft - time.time())
            logger.warning(f"[BCC-043] " + f"[bcc] {self.account} 处于致命态熔断中（剩余 {remain // 60} 分钟），"
                f"不再自动重启容器；请先解决凭证/索引问题（见启动日志 BCC-043）")
            raise RuntimeError(
                f"[bcc] 容器处于致命态熔断（{remain // 60} 分钟）：凭证不可用，"
                f"请从应用界面启动或重新登记账号")
        # 切换中/冷却期保护：等后台 _launch 完成，绝不在此期间判失活重启
        if self._switching or time.time() < self._switch_cool_until:
            remain = int(self._switch_cool_until - time.time())
            phase = "切换中(_launch后台)" if self._switching else "切换冷却期"
            logger.debug(
                f"[bcc] {self.account} {phase}({max(remain, 0)}s)，"
                f"等待 context 重建完成（跳过失活判定，防重启死循环）")
            return
        try:
            if self._context is None or not self._context.pages:
                raise RuntimeError("context 已关闭")
            # 探测 page 是否能 evaluate
            if self._page is None or self._page.is_closed():
                self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()
            await self._page.evaluate("1")
        except Exception as e:
            # 切换冷却期：context 刚重启完，页面可能还在加载（有头冷启动可达
            # 2~3 分钟）。此时探活失败是正常的，绝不能强杀重启 —— 否则刚加载
            # 一半的页面被杀，抖音会弹「环境异常」（实测 13:52 切换事故）。
            # _switching 时 _launch 在后台跑，context 尚在重建，同样只告警。
            if self._switching or time.time() < self._switch_cool_until:
                remain = int(self._switch_cool_until - time.time())
                phase = "切换中(_launch后台)" if self._switching else "切换冷却期"
                logger.warning(f"[BCC-006] " + f"[bcc] context 探活失败（{phase}，{max(remain,0)}s "
                    f"后恢复强杀）: {e} —— 页面加载中，跳过重启，等冷却结束")
                return
            # ═══════════════════════════════════════════════════════════════
            # 2026-09-20 v0.43.99【第二道闸 · 有头观测态禁止静默重建】
            #
            # 有头可见 = **用户正在观测/操作**的窗口（扫码、输手机号）。此时
            # 任何一次 context 重建都会：
            #   · 把用户眼前的窗口销毁重开（正在输入的手机号/正在扫的码全丢）；
            #   · 在抖音侧记一次「全新环境」访问 —— 恰在 step-up 校验时刻，
            #     直接触发「安全风险…已阻止此次访问」。
            # 故有头态下探活失败只告警、等待用户操作完成，绝不自动重建。
            # （无头态仍保留自愈重建：那是无人值守的后台形态，不会打断交互。）
            # ═══════════════════════════════════════════════════════════════
            if not self._headless:
                logger.warning(f"[BCC-055] " + f"[bcc] {self.account} 处于【有头观测态】，"
                    f"探活失败也**不自动重建** —— 重建会销毁用户眼前窗口并在抖音侧"
                    f"记一次全新环境访问，正是在扫码/输手机号时触发"
                    f"「安全风险阻止访问」的成因。请用户完成操作后手动处理: {e}")
                return
            logger.warning(f"[BCC-006] " + f"[bcc] context/page 失活，重启: {e}")
            try:
                if self._backend in ("exe", "camoufox") and self._context is not None:
                    if self._backend == "camoufox":
                        from vbrowser_camoufox import close_camoufox_context
                        await close_camoufox_context(self._context)
                    else:
                        await self._context.close()
                if self._pw is not None:
                    await self._pw.stop()
            except Exception:
                pass
            self._pw = None
            self._browser = None
            self._context = None
            self._page = None
            self._nav_page = None  # P2-A：context 已重建，导航 tab 引用作废
            self._diag_page = None  # ENG-020：同理，观测 tab 引用一并作废
            # ═══════════════════════════════════════════════════════════════
            # 2026-09-20 v0.43.99【致命修复 · 环境跳变风暴】
            #
            # 原逻辑「重建一律按无头启动」（防快闪），在 v0.43.98 引入「切可见
            # = 真正重建为有头」后产生致命组合：
            #   ① 用户切可见 → _headless=False，容器真的重建成【有头】
            #   ② 自愈重建（_ensure_alive）走 _launch() 不传参 → 恒 headless=True
            #      → 把【有头】悄悄重建回【无头】，与 _headless=False 状态不符
            #   ③ 用户/校验再切可见 → 又重建回【有头】
            #   ⇒ 无头↔有头反复横跳。实测 40 分钟内 context 代次 6 次。
            #
            # 每一次 context 重建在抖音眼里都是一次「全新环境」访问；短间隔 +
            # 形态横跳 = 风控判「环境异常」的教科书特征 —— 这正是「扫码授权时
            # /输手机号时弹『安全风险已阻止访问』」的成因（拦截发生在 step-up
            # 校验时刻，故首屏渲染看不出来）。
            #
            # 修复：自愈重建**继承当前可见性意图**（用户要可见就按可见重建），
            # 消除形态横跳；快闪防护改为「有头时不静默重建」。
            # ═══════════════════════════════════════════════════════════════
            logger.info(
                f"[bcc] {self.account} context 失活自愈：按当前可见性意图重建"
                f"（headless={self._headless}，避免无头↔有头横跳触发风控）")
            await self._launch(headless=self._headless)
            return

    async def _wait_profile_released(self, timeout: float = 10.0) -> None:
        """等待 profile 的锁 / 占用进程消失（旧实例完全退出）。

        切换可见性时 close()+stop() 是异步的，旧进程可能还没退，新
        launch_persistent_context 立即启动会 TargetClosed。这里轮询等旧占用消失；
        超时则继续（不再等，避免永久卡死）。

        Chromium 锁文件命名随内核/版本变化：官方 Chromium 用 SingletonLock，
        ungoogled-chromium 实测是 lockfile（2026-09-12 现场核实）。两者都查。

        ## 2026-09-20 v0.44.11 修复：本函数对 Camoufox **曾经是空操作**

        实机取证实录：
          · Camoufox 是 **Firefox 内核**，锁文件是 `parent.lock`——不在上述
            Chromium 名字列表里，于是 `if not locks: return` **立即返回**，
            「等旧进程退净」这条保护对 Camoufox **完全失效**（静默形同虚设）；
          · 且 Firefox 退出后 `parent.lock` **不会删除**（实测 close 后仍在），
            故**不能**靠「锁文件消失」判断 Firefox 进程已退 —— 若简单把名字
            加进列表，反而会在每次重建时白等满 timeout（性能回归）。

        正解（仅对 Camoufox 生效，Chromium 路径**一行不动**）：
        用 psutil 按 **本账号 profile 路径** 匹配进程，等其全部退出。进程退出
        才是跨内核唯一可靠的判据。psutil 不可用时回落到原锁文件轮询。
        """
        if not self._profile_dir:
            return
        # —— Camoufox/Firefox：按**进程退出**等待（不看锁文件）——
        # ⚠️ 判定用**配置真源**而非只看 self._backend：`_launch` 里本函数在
        #    `launch_async(...)` 之前调用，**首次启动时 self._backend 尚未赋值**
        #    （仍是 None/旧值），只看它会漏判。配置是显式真源（显式配置原则）。
        _is_camoufox = getattr(self, "_backend", "") == "camoufox"
        if not _is_camoufox:
            try:
                from auto_dm import config as _cfg
                from vbrowser_camoufox import camoufox_enabled
                _is_camoufox = bool(camoufox_enabled(_cfg))
            except Exception:
                _is_camoufox = False
        if _is_camoufox:
            _needle = str(self._profile_dir).replace("\\", "/").lower()

            def _owner_alive():
                """返回 True=仍有占用进程 / False=已全部退出 / None=无法判定。"""
                try:
                    import psutil
                except Exception:
                    return None
                try:
                    for _p in psutil.process_iter(["pid", "cmdline"]):
                        try:
                            _cl = " ".join(_p.info.get("cmdline") or [])
                        except Exception:
                            continue
                        if _needle and _needle in _cl.replace("\\", "/").lower():
                            return True
                except Exception:
                    return None
                return False

            _st = _owner_alive()
            if _st is False:
                return
            if _st is True:
                _deadline = time.time() + timeout
                while time.time() < _deadline:
                    _st = _owner_alive()
                    if _st is False:
                        logger.info(f"[bcc] {self.account} profile 旧进程已退净"
                                    f"（Camoufox 进程判据），可安全启动新 context")
                        return
                    if _st is None:
                        break
                    await asyncio.sleep(0.3)
                if _st is not False:
                    logger.warning(
                        f"[bcc] {self.account} profile 仍被旧进程占用 "
                        f"{timeout:.0f}s 未退净，继续启动（可能仍冲突）")
                    return
            # _st is None（psutil 不可用）→ 落到下方锁文件轮询兜底
        lock_files = [os.path.join(self._profile_dir, n)
                      for n in ("SingletonLock", "lockfile", "parent.lock", ".parentlock")]
        locks = [p for p in lock_files if os.path.exists(p)]
        if not locks:
            return
        deadline = time.time() + timeout
        while time.time() < deadline:
            locks = [p for p in lock_files if os.path.exists(p)]
            if not locks:
                logger.info(f"[bcc] {self.account} profile 锁已释放，可安全启动新 context")
                return
            await asyncio.sleep(0.3)
        logger.warning(
            f"[bcc] {self.account} profile 锁 {timeout:.0f}s 未释放（{locks}），"
            f"继续启动（可能仍冲突）")


    async def submit(self, coro):
        """把协程投递到主事件循环，串行执行（_lock 保证同一时刻只有一个浏览器操作）。"""
        if self._loop is None:
            raise RuntimeError("[bcc] 容器未启动")
        # 如果调用方在另一个线程（FastAPI 路由跑在主 loop，但保活心跳在子线程），
        # 需要切回主 loop 执行浏览器操作
        if asyncio.get_event_loop() is not self._loop:
            fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
            return await asyncio.wrap_future(fut)
        return await coro

    async def _exec(self, coro_factory, holder: str = "",
                   purpose: str = "auto", prio: int = 2,
                   ttl: float = 0.0, lease_id: str = "",
                   internal: bool = False):
        """在 _lock 内执行浏览器操作（租约 + 自愈 + 串行）。

        coro_factory 是无参 callable 返回 coroutine。

        ## 租约（2026-09-13 S2）
        所有走本函数的端点**自动纳入租约调度**——这是最小侵入的接入点：
        无需逐个改造 11 个端点。未显式声明 holder 的调用方按 **P2 后台保活**
        保守授权（ttl≤30s）。
          · 同 holder / 同 lease_id 重入 → 复用租约（不自己和自己冲突）
          · 已被他人持有 → 立即失败（不抢占），调用方按 retry_after 重试

        P2-B：scan_login 独占窗口内（context 已关、扫码中）快速失败，
        避免调用方排队干等到 HTTP 超时。注意 _launch 自身不走本检查
        （scan_login 的 _do 内部会调 _launch）。

        ## internal（2026-09-14 v0.43.11 新增）
        **容器自身的内部线程**（_prewarm 昵称预热、保活回写）不是外部业务
        调用方，**不参与租约仲裁**。

        为什么必须区分（实测：prewarm 被业务租约永久饿死）：
          租约是按账号单槽位的，外部业务一持租（如「更新会话」capture_all
          ttl=300s），prewarm（prio=2 后台保活）每次申请都被拒 → BCC-047
          刷屏，预热永远跑不完 → 业务拿不到预热成果，只能自己重跑。

        ## ⚠️ 但「让位」= 放弃，不是排队（本会话实测修正）
        初版实现让 internal 线程「排队等 _lock」——实机证明**更糟**：
        预热与业务在同一把 _lock 上交替抢占，业务每轮从 6~8s 恶化到 30~45s，
        整轮捕获冲破 300s 客户端超时（同一账号、同一页面，仅此一处差异）。
        正解：**业务持租期间，内部线程直接放弃本次**（不排队、不抢占）。
        预热本来就是「锦上添花」——业务自己的捕获同样会写 `_userinfo_cache`，
        跳过预热没有任何损失。
        """
        # 2026-09-17 修补（OCR 审查 HIGH）：原为 `if _is_busy(): raise
        # ContainerBusy(_scan_exclusive["holder"])` —— 两次**非原子**读之间有
        # TOCTOU 窗口：_is_busy() 为真后、第二个下标读取前，若独占方已释放，
        # _scan_exclusive["holder"] 变成 None，异常信息就成了 ContainerBusy(None)。
        # 现改为一次加锁读，取到的值即判据。
        _ex = _scan_exclusive_get()
        if _ex is not None:
            raise ContainerBusy(_ex)
        if internal:
            # 内部线程让位（两重）：
            #  ① 已有业务租约在持 → 直接放弃（不排队，见上文实测）
            #  ② 连 `bcc-internal` 自己也只算「保活级」——预热绝不与业务争
            _cur = _lease_current()
            _cur_holder = str((_cur or {}).get("holder") or "")
            if _cur_holder and _cur_holder not in ("bcc-internal", "prewarm", "keepalive"):
                logger.debug(
                    f"[lease] 内部线程({holder}) 让位：{_cur_holder} 正持租约")
                raise ContainerBusy(f"yield_to:{_cur_holder}")
        # 租约门（_lease_acquire 内部处理重入复用）
        _lid = lease_id
        _need_release = False
        if internal:
            # 内部线程：不碰租约，仅 _lock 串行（见 docstring「internal」）
            _is_reentry = True
        else:
            _cur_l = _lease_current()
            # 重入判据**只有**显式 lease_id 匹配 —— _lock 非重入，_exec 不可能嵌套，
            # 因此任何"同 holder"都不是重入，而是并发冲突（必须拒绝）。
            _is_reentry = bool(lease_id and _cur_l
                               and _cur_l["lease_id"] == lease_id)
        if not _is_reentry:
            _r = _lease_acquire(holder or "bcc-internal", purpose, prio,
                                ttl, lease_id)
            if not _r.get("ok"):
                raise ContainerBusy(_r.get("busy") or "busy")
            _lid = _r["lease_id"]
            _need_release = not _r.get("renew")
        try:
            # 2026-09-14 v0.43.11：`_lock` 获取策略按用途区分 —— 这是「让位」的
            # 真正落点（比租约判据更根本，因为 `_lock` 才是物理串行化资源）。
            #
            #  · 业务请求（internal=False）：**无限等 `_lock`**。它在等的是
            #    「上一个浏览器操作做完」，这正是串行化的本意。若这里直接
            #    `ContainerBusy` 快速失败，业务会被一次预热/保活瞬间挡回
            #    （曾实测：业务刚发起就撞上预热持锁 → CAP-007
            #     「容器被独占操作占用: bcc-internal」→ 昵称 0 个、耗时 2.1s）。
            #  · 内部线程（internal=True）：**限时 3s 抢锁，抢不到就放弃**。
            #    预热/保活是锦上添花，绝不占用业务的等待时间，也绝不排队
            #    （排队会让两者交替抢占，实测把业务每轮从 6~8s 拖到 30~45s）。
            if internal:
                try:
                    await asyncio.wait_for(self._lock.acquire(), timeout=3.0)
                except asyncio.TimeoutError:
                    logger.debug(
                        f"[lease] 内部线程({holder}) 放弃本次：_lock 被业务占用")
                    raise ContainerBusy("lock_busy_yield")
            else:
                await self._lock.acquire()
            try:
                # 切换期（_launch 后台重建 context）快速失败：此时 _context 为
                # None，继续执行只会拿到 None 崩溃或误判失活触发重启死循环
                # （2026-09-13 BCC-006 刷屏事故）。调用方按「容器忙」重试即可。
                if self._switching:
                    raise ContainerBusy("切换可见性中（context 重建），请稍后重试")
                await self._ensure_alive()
                return await coro_factory()
            finally:
                self._lock.release()
        finally:
            # 单次调用型端点：用完即释放（跨调用窗口由调用方显式 lease_id）
            if _need_release and _lid:
                _lease_release(_lid)

    # -------------------- 业务方法（在 _lock 内执行）--------------------

    async def get_cookies(self, lease_id: str = "",
                          internal: bool = False) -> dict:
        """读取实时 cookie。返回 {name: value}。

        lease_id：跨调用窗口租约透传（调用方已持租约时必须带上，否则被拒）。
        internal：容器自身后台线程（保活回写）→ 不参与租约仲裁，只走 _lock。
        """
        async def _do():
            cks = await self._context.cookies()
            return {c["name"]: c["value"] for c in cks}
        if internal:
            return await self._exec(_do, holder="keepalive", internal=True)
        return await self._exec(_do, holder="get_cookies",
                                purpose="auto", prio=1, ttl=300.0,
                                lease_id=lease_id)

    async def goto(self, url: str, wait_until: str = "domcontentloaded", timeout: int = 25000) -> str:
        async def _do():
            await self._page.goto(url, wait_until=wait_until, timeout=timeout)
            return self._page.url
        return await self._exec(_do)

    async def evaluate(self, script: str, arg: Any = None) -> Any:
        async def _do():
            return await self._page.evaluate(script, arg)
        return await self._exec(_do)

    async def bulk_user_info(self, sec_uids: list[str]) -> dict:
        """浏览器页面内 fetch im/user/info 批量查昵称/头像（对齐 douyin.com/chat 实机）。

        必须先 goto douyin.com/chat（同 origin 才能相对 fetch）。返回
        {sec_uid: {"nickname": str, "avatar": str}}。
        """
        api_url = ("/aweme/v1/web/im/user/info/?device_platform=webapp&aid=6383&channel=channel_pc_web"
                   "&pc_client_type=1&update_version_code=170400&version_code=170400&version_name=17.4.0"
                   "&cookie_enabled=true&browser_language=zh-CN&browser_platform=Win32&browser_name=Mozilla"
                   "&browser_version=5.0&browser_online=true&os_name=Windows&os_version=10&platform=PC"
                   "&downlink=10&effective_type=4g&round_trip_time=100")

        async def _do():
            # 确保在 douyin.com/chat（im/user/info 需要私信上下文）
            if "/chat" not in self._page.url:
                await self._page.goto("https://www.douyin.com/chat?isPopup=1", wait_until="domcontentloaded", timeout=25000)
                await self._page.wait_for_timeout(2500)
            out = {}
            batch = 6
            import urllib.parse as _up
            for i in range(0, len(sec_uids), batch):
                chunk = sec_uids[i:i + batch]
                body = "sec_user_ids=" + _up.quote(json.dumps(chunk))
                js = (
                    "(async () => {"
                    f"  const r = await fetch({json.dumps(api_url)}, {{"
                    "    method: 'POST',"
                    "    headers: {'content-type': 'application/x-www-form-urlencoded; charset=UTF-8'},"
                    f"    body: {json.dumps(body)},"
                    "    credentials: 'include'"
                    "  });"
                    "  return await r.json();"
                    "})()"
                )
                try:
                    result = await self._page.evaluate(js)
                except Exception as e:
                    logger.warning(f"[BCC-007] " + f"[bcc] 批量查昵称 evaluate 失败: {e}")
                    continue
                items = (result or {}).get("data") or []
                for u in items:
                    sec = u.get("sec_uid") or ""
                    if not sec:
                        continue
                    avt = (u.get("avatar_small") or {}).get("url_list") or []
                    out[sec] = {
                        "nickname": u.get("nickname") or "",
                        "avatar": avt[0] if avt else "",
                    }
                await self._page.wait_for_timeout(300)
            return out
        return await self._exec(_do)

    async def bulk_user_info_by_uid(self, uids: list[str]) -> dict:
        """用数字 UID 主动 fetch im/user/info 批量查昵称/头像（比被动 hook 更可靠）。

        抖音 im/user/info 接口同时支持 user_ids 与 sec_user_ids 参数。
        会话列表只有数字 peer_uid（首包解析 100% 可靠），用 user_ids 直查
        避免依赖前端自发展示会话（被动 hook 会超时/缺口）。
        返回 {uid: {"nickname", "avatar"}}。
        """
        api_url = (
            "/aweme/v1/web/im/user/info/?device_platform=webapp&aid=6383&channel=channel_pc_web"
            "&pc_client_type=1&update_version_code=170400&version_code=170400&version_name=17.4.0"
            "&cookie_enabled=true&browser_language=zh-CN&browser_platform=Win32&browser_name=Mozilla"
            "&browser_version=5.0&browser_online=true&os_name=Windows&os_version=10&platform=PC"
            "&downlink=10&effective_type=4g&round_trip_time=100"
        )

        async def _do():
            # 确保在 douyin.com/chat（im/user/info 需要私信上下文）
            if "/chat" not in self._page.url:
                await self._page.goto("https://www.douyin.com/chat?isPopup=1", wait_until="domcontentloaded", timeout=25000)
                await self._page.wait_for_timeout(2500)
            out = {}
            batch = 6
            import urllib.parse as _up
            for i in range(0, len(uids), batch):
                chunk = uids[i:i + batch]
                body = "user_ids=" + _up.quote(json.dumps(chunk))
                js = (
                    "(async () => {"
                    f"  const r = await fetch({json.dumps(api_url)}, {{"
                    "    method: 'POST',"
                    "    headers: {'content-type': 'application/x-www-form-urlencoded; charset=UTF-8'},"
                    f"    body: {json.dumps(body)},"
                    "    credentials: 'include'"
                    "  });"
                    "  return await r.json();"
                    "})()"
                )
                try:
                    result = await self._page.evaluate(js)
                    logger.info(f"[bcc] 批量查昵称(uid) 响应: {str(result)[:500]}")
                except Exception as e:
                    logger.warning(f"[BCC-008] " + f"[bcc] 批量查昵称(uid) evaluate 失败: {e}")
                    continue
                items = (result or {}).get("data") or []
                for u in items:
                    uid = str(u.get("uid") or "")
                    if not uid:
                        continue
                    avt = (u.get("avatar_small") or {}).get("url_list") or []
                    out[uid] = {
                        "nickname": u.get("nickname") or "",
                        "avatar": avt[0] if avt else "",
                    }
                await self._page.wait_for_timeout(300)
            return out

        return await self._exec(_do)

    async def _ensure_nav_tab(self, url: str = "https://www.douyin.com/chat?isPopup=1"):
        """返回一个**位于抖音 /chat 的独立导航 tab**（懒创建，绝不用主 page）。

        🔴 2026-09-21（ENG-020）新增 —— 统一原语，替代「把主 page goto 走」的旧做法。

        背景（实机取证）：`exec_js` / `capture_wp_messages` 原先在「页面不在 /chat」时
        直接 `self._page.goto("/chat")`。这属于**观测动作产生副作用**：
          - 主 page 停在 `/jingxuan` 等重 SPA 页面时，该次 goto 会被挂住 ——
            实测每轮正好挂满 25.0s 超时（日志：14:57:00→14:57:25、15:02:26→15:02:51…），
            调用方（页面级登录态探针）据此判「页面失效」→ 保活回写被跳过
            → `.env` 凝固在坏会话（ENG-020 根因）；
          - 探针不该改变被观测对象的位置。
        正解沿用项目既有先例 `resolve_url`（2026-09-06 同款修复：主 chat 页全程不动）：
        用**同 context 的独立 tab**（同指纹、同 cookie、同代理出口）。

        注：WP/昵称 hook 均为 **context 级** `add_init_script`（见 `_launch`），
        对本 tab 同样生效 ⇒ wp_recv 通过本 tab 仍能取到被动截获的事件。
        """
        page = getattr(self, "_diag_page", None)
        try:
            if page is not None and not page.is_closed() and "/chat" in (page.url or ""):
                return page
        except Exception:  # noqa: BLE001
            page = None
        try:
            if page is None or page.is_closed():
                page = await self._context.new_page()
                self._diag_page = page
            await page.goto(url, wait_until="domcontentloaded", timeout=25000)
            await page.wait_for_timeout(1200)
            return page
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[bcc] 独立导航 tab 不可用（{type(e).__name__}），回落主 page")
            return None

    async def exec_js(self, js: str, arg=None, timeout: int = 30,
                      lease_id: str = "", holder: str = "exec_js"):
        """在抖音页面上下文里执行 JS（**只读取数**用途）。

        2026-08-31 新增，用于取私信原图：远程链是抖音私有加密格式，
        后端/普通 <img> 都解不开，但**抖音前端自己能解码渲染**
        （用户在网页上看得到图），所以在页面上下文里
        fetch → canvas → toDataURL 是唯一可行路径。

        js 必须是「单表达式」形式的 async 箭头函数字符串，例如：
            "async (url) => { const r = await fetch(url); ... return b64 }"
        Playwright 会把它编译成函数再调用。

        **风控边界**：本方法只执行传入的 JS，自身不发起请求。
        不得用于遍历/批量查询用户信息（昵称红线）。

        lease_id（2026-09-15）：**必须由调用方透传**，否则会被调用方自己
        刚拿到的租约挡在门外 —— 实测 `/userinfo_idb` 报
        `BCC-056 容器被独占操作占用: capture_all`（capture_all 持有租约，
        本方法未透传 → 自我死锁，与知识库 §〇·戊 同族）。
        """
        async def _do():
            page = self._page
            if "/chat" not in (page.url or ""):
                # 🔴 2026-09-21（ENG-020）：**不再把主 page 导航走** —— 改用独立
                # 导航 tab 取抖音域上下文（同域 fetch + 登录态 + 前端解密），
                # 主 page 全程不动。原实现的 goto 会在页面停于重 SPA 页时挂满 25s
                # 超时，把页面级探针打成「页面失效」（见 _ensure_nav_tab docstring）。
                page = await self._ensure_nav_tab() or page
            page.set_default_timeout(timeout * 1000)
            return await page.evaluate(js, arg)

        return await self._exec(_do, holder=holder, lease_id=lease_id or "")

    async def wp_send_text(self, conv_id: str, text: str, timeout: int = 60) -> dict:
        """在 chat 页上下文里发文本私信（WP 通道发送）。

        2026-09-06 重写：废弃「探测式 IM SDK 调用」（页面全局从未有
        webImService 等候选对象，实测恒失败），改用 **DOM 流程** ——
        2026-09-06 上午实测验证通过（真有头+无头各一次，对方实收）：
          搜索会话 → 点开 → 编辑器填字（execCommand insertText）→ Enter 发送。
        与 wp_send_image 的 8 步流程同源（知识库 08 §24.9/§24.10）。

        风控说明：发送是用户主动触发的单次操作，且复用页面已有登录态。

        返回 {"ok": bool, "via": "dom", "result": ...} / {"ok": False, "error": ...}
        """
        # ① 从 conv_id 提取对端 uid，再由 DB 拿 peer_name（DOM 搜索需要昵称）
        peer_name = None
        try:
            from database import get_db
            _conn = get_db()
            _row = _conn.execute(
                "SELECT peer_name FROM dm_conversations WHERE account=? AND conv_id=?",
                (self.account, conv_id)).fetchone()
            if _row and _row[0]:
                peer_name = str(_row[0])
        except Exception:
            pass
        if not peer_name:
            # conv_id 兜底：0:1:<uid_a>:<uid_b> 取非自身 uid 段当昵称占位
            parts = str(conv_id).split(":")
            if len(parts) == 4:
                my = str(getattr(self, "_last_uid", "") or "")
                peer_name = parts[3] if parts[2] == my else parts[2]
            else:
                return {"ok": False, "error": f"无法确定会话对象（conv_id={conv_id[:30]}）"}

        js = r"""
        async (args) => {
          const sleep = ms => new Promise(r => setTimeout(r, ms));
          const kw = args.peer_name;
          const text = args.text;
          // ② 搜索会话
          const inputs = Array.from(document.querySelectorAll('input'));
          const search = inputs.find(i => /搜索|查找/.test(i.placeholder || ''));
          if (!search) return { ok: false, error: '页面无搜索框（可能未登录/未在 chat 页）' };
          search.focus();
          const setter = Object.getOwnPropertyDescriptor(
            window.HTMLInputElement.prototype, 'value').set;
          setter.call(search, kw);
          search.dispatchEvent(new Event('input', { bubbles: true }));
          await sleep(2500);
          // ③ 点开会话
          const items = Array.from(
            document.querySelectorAll('.conversationConversationItemwrapper'));
          const tgt = items.find(el => (el.innerText || '').includes(kw));
          if (!tgt) return { ok: false, error: '搜索结果中无「' + kw + '」会话' };
          ['mousedown', 'mouseup', 'click'].forEach(ev => {
            tgt.dispatchEvent(new MouseEvent(ev, { bubbles: true, cancelable: true,
                                                   view: window, button: 0 }));
          });
          await sleep(3000);
          // ④ 编辑器填字 + Enter 发送
          const editor = document.querySelector(
            '.messageEditorinputArea, [class*=editor-kit-container]');
          if (!editor) return { ok: false, error: '聊天编辑器未出现（会话未打开成功）' };
          editor.focus();
          document.execCommand('insertText', false, text);
          await sleep(600);
          editor.dispatchEvent(new KeyboardEvent('keydown', {
            key: 'Enter', code: 'Enter', keyCode: 13, which: 13,
            bubbles: true, cancelable: true }));
          await sleep(3000);
          // ⑤ 发送判定：编辑器内容清空 = 消息已发出（抖音行为）
          // 注意：抖音编辑器清空后残留零宽空格 \u200b，必须剔除再判
          const editor2 = document.querySelector(
            '.messageEditorinputArea, [class*=editor-kit-container]');
          const rest = editor2
            ? (editor2.innerText || '').replace(/\u200b/g, '').trim()
            : null;
          const cleared = rest !== null && rest === '';
          return { ok: !!cleared, via: 'dom',
                   error: cleared ? '' : '编辑器内容未清空，发送可能未成功' };
        }
        """
        try:
            res = await self.exec_js(
                js, arg={"peer_name": peer_name, "text": text}, timeout=timeout)
            if isinstance(res, dict) and res.get("ok"):
                logger.info(f"[bcc] wp_send_text 成功(DOM) -> {peer_name}: {text[:20]}")
            else:
                logger.warning(f"[BCC-009] " + f"[bcc] wp_send_text 失败: "
                               f"{res.get('error') if isinstance(res, dict) else res}")
            return res if isinstance(res, dict) else {"ok": False, "error": str(res)}
        except Exception as e:
            logger.warning(f"[BCC-010] " + f"[bcc] wp_send_text 失败: {e}")
            return {"ok": False, "error": str(e)}



    async def capture_wp_messages(self) -> list[dict]:
        """读取 BCC hook 截到的 WP 通道私信事件（读后清空）。

        2026-09-05 新增。CAP_WP_MESSAGE_HOOK_JS 已被动把 HTTP 响应 / WS 帧
        raw 推入 window.__CAP_WP_MESSAGE__.events，这里取回并清空，
        由后端 wp_recv 统一解析（页面内不做解析，保持 hook 极简）。

        返回 [{kind: 'http'|'ws', url, body, ts}, ...]。
        """
        async def _do():
            page = self._page
            if "/chat" not in (page.url or ""):
                # 🔴 2026-09-21（ENG-020）：改用独立导航 tab —— 原实现把主 page
                # goto 到 /chat，既挂满 25s 超时（主 page 停在重 SPA 页时），
                # 又让「页面在哪」变成随机事件，令页面级探针与 WP 通道互相踩。
                # hook 是 context 级 add_init_script，独立 tab 同样生效。
                page = await self._ensure_nav_tab() or page
            try:
                evs = await page.evaluate(
                    "() => window.__CAP_WP_MESSAGE__ ? window.__CAP_WP_MESSAGE__.events : []")
                # 取回后立即清空，避免下次重复处理
                await page.evaluate(
                    "() => { if (window.__CAP_WP_MESSAGE__) window.__CAP_WP_MESSAGE__.events = []; }")
            except Exception as e:
                logger.warning(f"[BCC-012] " + f"[bcc] 读 wp message 失败: {e}")
                return []
            evs = evs or []
            if evs:
                logger.info(f"[bcc] 取回 WP 私信事件 {len(evs)} 条")
            return evs
        return await self._exec(_do)


    async def resolve_url(self, url: str) -> dict:
        """浏览器打开链接 → 跟随跳转 → 抠 live_id（替代 link_resolve._browser_resolve）。

        2026-09-06 第五轮治理 P2-A（09 台账 5.2A2）：改走**独立导航 tab**。
        原实现用常驻主 page 直接 goto —— 会把 chat 页导航走，解析期间
        被动昵称 hook、WP 发送、页面级登录态探测全部失效（直播功能与私信
        功能互踩）。现在：懒创建 nav tab（同 context 同指纹同登录态），
        在 nav tab 里导航+等待跳转，结束后导回 about:blank 释放资源，
        **主 chat 页全程不动**。nav tab 的创建/导航仍在 _exec 锁内串行。

        返回 {live_id, final_url, source}。
        """
        import re
        # 复用 link_resolve 的提取正则（避免循环 import，本地复制）
        _LIVE_RE = re.compile(r"live\.douyin\.com/([^?/\s\"']+)")

        def _extract(u):
            if not u:
                return None
            m = _LIVE_RE.search(u)
            return m.group(1) if m else None

        async def _do():
            # 懒创建导航 tab（与主 chat 页同 context：同指纹、同 cookie、同代理出口）
            page = getattr(self, "_nav_page", None)
            try:
                if page is None or page.is_closed():
                    page = await self._context.new_page()
                    self._nav_page = page
            except Exception as e:
                logger.warning(f"[BCC-013] " + f"[bcc] 导航 tab 创建失败（退回主 page）: {e}")
                page = self._page
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                live_id = None
                final_url = None
                for _ in range(20):
                    await page.wait_for_timeout(1000)
                    u = page.url
                    if "live.douyin.com" in u and "/user/" not in u:
                        lid = _extract(u)
                        if lid:
                            live_id = lid
                            final_url = u
                            break
                if not live_id:
                    u = page.url
                    # 用户主页里找直播间入口
                    try:
                        links = await page.eval_on_selector_all(
                            "a[href*='live.douyin.com']",
                            "els => els.map(e => e.href)")
                    except Exception:
                        links = []
                    for link in (links or []):
                        lid = _extract(link)
                        if lid:
                            live_id = lid
                            final_url = link
                            break
                return {"live_id": live_id, "final_url": final_url,
                        "source": "browser_container" if live_id else "browser_failed"}
            finally:
                # 导航 tab 用完即复位，不残留直播间页面（省资源、避免后台自动刷新弹幕 WS）
                #
                # 2026-09-17 修补（OCR 审查 HIGH）：原实现只 `goto("about:blank")`
                # **不 close()**，每次 resolve_url 都留一个空 tab —— 空页虽已断开
                # 弹幕 WS，但仍占一个 renderer 进程位/句柄，长时间运行会累积。
                # 现改为真正关闭并把引用置空，下次按需懒创建。
                if page is not self._page:
                    try:
                        await page.close()
                    except Exception:
                        # close 失败（如页面已在关闭中）时退回复位，至少不留直播页
                        try:
                            await page.goto("about:blank", wait_until="commit",
                                            timeout=5000)
                        except Exception:
                            pass
                    if getattr(self, "_nav_page", None) is page:
                        self._nav_page = None
        return await self._exec(_do)

    def status(self) -> dict:
        from auto_dm import accounts as _acc
        from services import member_ctx
        env_path = getattr(self, "_env_path", None) or _acc.env_path_of(self.account)
        alive = self._started and self._context is not None
        uid = self._last_uid
        if not uid:
            try:
                uid = self._load_uid_from_env()
            except Exception:
                uid = None
        return {
            "alive": alive,
            "account": self.account,
            "profile": self._profile_dir,
            "uid": uid,
            "last_refresh": int(self._last_refresh),
            "logged_in": bool(env_path and member_ctx.env_exists(env_path)),
            # 2026-09-13：上报自身版本，供桌面端比对「前端新/后端旧」
            "version": _app_version(),
            # 2026-09-13 S2：暴露租约状态（browser_gate 此前读的 "exclusive"
            # 字段**从未存在**，导致其独占分支恒为假成功——此处补齐真值源）
            "lease": _lease_status(),
            # 兼容旧的 exclusive 读取（值为当前 holder，空闲为 None）
            "exclusive": (_lease_status() or {}).get("holder"),
        }

# 本机鉴权（2026-09-17 审查 P2-4 修补）
# ----------------------------------------------------------------------------
# 背景：BCC 仅监听 127.0.0.1（见 __main__ 的 uvicorn.run），属本机信任边界；
# 但**同机任意本地进程**可无凭据调用全部 19 个端点 —— 包括 /exec_js（在已登录
# 抖音页面执行任意 JS）、/wp_send（发私信）、/cookie、/scan_login、/quit，
# 等价于拿到该账号的完整浏览器操作权。
#
# 现引入可选的本机令牌：
#   - 未设 DY_BCC_TOKEN → 维持现状（本机信任，向后兼容既有调用方）；
#   - 已设置 → 除 /status 外，全部端点要求 `X-BCC-Token` 头，用
#     secrets.compare_digest 恒定时间比对（防时序侧信道）。
# 令牌由启动方（backend / Tauri sidecar）通过环境变量注入。
_BCC_TOKEN = os.environ.get("DY_BCC_TOKEN", "") or ""


@app.middleware("http")
async def _bcc_token_guard(request, call_next):
    """可选的本机令牌校验（未配置 DY_BCC_TOKEN 时放行，保持向后兼容）。"""
    if not _BCC_TOKEN:
        return await call_next(request)
    path = request.url.path
    # 探活 exempt：启动方需在持令牌前就能探测 BCC 是否就绪
    if path in ("/status", "/health") or request.method == "OPTIONS":
        return await call_next(request)
    import secrets
    if not secrets.compare_digest(request.headers.get("x-bcc-token", ""),
                                  _BCC_TOKEN):
        logger.warning(f"[BCC-060] " + f"[bcc] 未授权访问被拒: {path}")
        from fastapi.responses import JSONResponse
        return JSONResponse({"ok": False, "msg": "unauthorized"},
                            status_code=401)
    return await call_next(request)


# 注册 bcc_routes 路由（2026-09-22 路由抽取 P3-5 Step 2）
from daemon.bcc_routes import router as _bcc_router
app.include_router(_bcc_router)

# 🔴 2026-09-23【生命周期挂 app，不挂 router —— 防 startup 跑两次】
# 详见 daemon/bcc_routes.py 顶部「生命周期必须挂 app」注释块：
# `include_router` 既把 router.on_startup 的 handler `add_event_handler` 到本 app，
# 又把 router 的**默认 lifespan** 合并进来，而后者同样会跑 router._startup()
# ⇒ `@router.on_event("startup")` 的 handler 会被调用两次。
# 故生命周期一律在此显式挂到 app 上（只注册一次，且与 include_router 无关）。
try:
    from daemon.bcc_routes import startup as _bcc_startup, shutdown as _bcc_shutdown
    app.router.add_event_handler("startup", _bcc_startup)
    app.router.add_event_handler("shutdown", _bcc_shutdown)
except Exception as _e_life:  # pragma: no cover - 导入失败必须立刻可见
    logger.error(f"[BCC-028] [bcc] 生命周期 handler 挂载失败: {_e_life}")
    raise

# ContainerBusy 异常处理器（APIRouter 不支持 exception_handler，必须挂在 app 上）
@app.exception_handler(ContainerBusy)
async def _container_busy_handler(request, exc: ContainerBusy):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=200, content={
        "ok": False, "busy": exc.holder,
        "msg": f"容器正被 {exc.holder} 独占（扫码/重登录），请稍后重试"})


# ----------------------------------------------------------------------------
# 主入口
# ----------------------------------------------------------------------------
# ───────────────────────────────────────────────────────────────────────────
# 单例检测（2026-09-13）：启动前确认该账号没有第二个 BCC / 浏览器在跑
# ───────────────────────────────────────────────────────────────────────────
# ════════════════════════════════════════════════════════════════════════════
# 人类行为模式（2026-09-13 用户风控要求）
#
# 用户原话：「点击的时候要符合不规律感，固定点击位置和频率容易被判定为脚本
#            导致封控」。
#
# 原理：脚本特征 = **确定性**。固定坐标（元素正中心）、固定间隔（400ms）、
#   固定滚动步长（整屏）三者叠加即成指纹。人则是「有抖动、有停顿、有回退」。
#
# 实现要点：
#   · 间隔用**对数正态**（多数短、偶尔长停顿）——比均匀分布更接近真人节奏
#     （真人打字/浏览的停顿是长尾的，均匀随机仍会被统计识别）
#   · 点击点避开正中心与边缘（中心是脚本最爱，边缘易误点）
#   · 滚动不总是整屏（真人会滚多滚少、偶尔回看）
#   · 鼠标分步移动而非瞬移（teleport 是 Playwright click 的默认行为）
#
# 回退：DY_HUMAN_PATTERN=off（调试复现用，恢复确定性行为）
# ════════════════════════════════════════════════════════════════════════════
def _human_on() -> bool:
    return str(os.environ.get("DY_HUMAN_PATTERN", "on")).strip().lower() != "off"


def _human_gap(base: float = 0.4, spread: float = 0.6) -> float:
    """人类化停顿：对数正态分布，多数接近 base，偶尔长停顿。

    base=0.4s 时典型取值 0.2~1.2s，长尾可达 2~3s（像人在看内容）。
    下限 0.12s（比这更快就明显是机器）。
    """
    import random as _r
    if not _human_on():
        return base
    # 对数正态：median=base, sigma 控制离散度
    v = _r.lognormvariate(0.0, spread) * base
    return max(0.12, min(v, base * 8.0))


def _human_scroll_ratio() -> float:
    """滚动步长比例（0.65~0.95 屏；真人很少每次整屏）。"""
    import random as _r
    return 1.0 if not _human_on() else _r.uniform(0.65, 0.95)


async def _human_click(page, element, timeout: int = 2000) -> bool:
    """人类化点击：元素内随机取点 + 分步移动鼠标 + 抖动延时。

    返回是否点击成功。失败**不抛异常**（与原 it.click 的容错语义一致）。
    """
    try:
        box = await element.bounding_box()
        if not box or box.get("width", 0) < 8 or box.get("height", 0) < 8:
            await element.click(timeout=timeout)
            return True
        import random as _r
        if not _human_on():
            await element.click(timeout=timeout)
            return True
        # 取点：避开正中心 20% 区域与 15% 边缘（在"舒适区"内随机）
        w, h = box["width"], box["height"]
        cx = box["x"] + w * _r.uniform(0.32, 0.68)
        cy = box["y"] + h * _r.uniform(0.32, 0.68)
        # 偶尔偏向侧边（真人点文字不太会精确居中）
        if _r.random() < 0.3:
            cx = box["x"] + w * _r.uniform(0.18, 0.82)
        # 分步移动（3~5 步），模拟轨迹；步间微停
        steps = _r.randint(3, 5)
        cur = await page.evaluate("() => ({x: window.__lmx || 0, y: window.__lmy || 0})")
        sx, sy = cur.get("x", 0), cur.get("y", 0)
        for i in range(1, steps + 1):
            t = i / steps
            mx = sx + (cx - sx) * t + _r.uniform(-2.5, 2.5)
            my = sy + (cy - sy) * t + _r.uniform(-2.5, 2.5)
            try:
                await page.mouse.move(mx, my)
            except Exception:
                pass
            await page.wait_for_timeout(_r.uniform(0.012, 0.05))
        await page.mouse.click(cx, cy)
        # 记录光标位置，供下次轨迹连续（真实鼠标不会跳回原点）
        try:
            await page.evaluate(f"() => {{ window.__lmx = {cx}; window.__lmy = {cy}; }}")
        except Exception:
            pass
        return True
    except Exception:
        # 坐标点击失败 → 退回元素点击（保证功能不因人类化而降级）
        try:
            await element.click(timeout=timeout)
            return True
        except Exception:
            return False


def _detect_existing_bcc(account, port):
    """检测同一账号是否已有 BCC 在运行；有则返回描述串（用于拒绝启动）。

    两层判据，任一命中即视为「已有实例」（宁可拦错也不许并存）：
      ① **端口层**：该账号哈希端口已被监听，且 /status 回的是同一账号。
         （只判端口被占不够——可能是别的进程；必须核对 /status.account）
      ② **profile 层（根本）**：该账号 profile 目录存在 Chromium 锁文件
         （SingletonLock / lockfile）。浏览器所有权最终体现在该目录上，
         有人持锁就说明有活着的浏览器；比端口判据更根本、与端口无关。
    """
    # ① 端口层
    try:
        from auto_dm import accounts as _acc
        if _acc._port_open(port, timeout=0.3):
            _who = ""
            try:
                import json as _json
                import urllib.request as _ur
                with _ur.urlopen("http://127.0.0.1:%d/status" % port,
                                 timeout=4) as _r:
                    _j = _json.loads(_r.read().decode("utf-8", "replace"))
                _who = str(_j.get("account") or "")
            except Exception:
                _who = ""
            if not _who or _who == account:
                return "port=%d%s" % (port, (", account=%s" % _who) if _who else "")
    except Exception:
        pass
    # ② profile 层
    try:
        from auto_dm import accounts as _acc
        _env = _acc.env_path_of(account)
        _prof = _acc.profile_dir_of(_env) if _env else ""
        if _prof and os.path.isdir(_prof):
            for _n in ("SingletonLock", "lockfile"):
                if os.path.exists(os.path.join(_prof, _n)):
                    return "profile 被占用（%s 存在 %s）" % (_prof, _n)
    except Exception:
        pass
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="浏览器容器守护进程（BCC）")
    parser.add_argument("--account", required=True, help="账号名")
    parser.add_argument("--port", type=int, required=True, help="HTTP 控制端口")
    parser.add_argument(
        "--allow-any-port", action="store_true",
        help="允许非哈希端口启动（仅调试用；正常启动一律走端口哈希校验）")
    parser.add_argument(
        "--force-duplicate", action="store_true",
        help="允许同账号第二个 BCC 并存（仅极端调试；正常一律走调度器 services.browser_gate）")
    args = parser.parse_args()

    # 2026-09-06 P1 修复（知识库 08 §24.9 事故 ④）：端口必须与
    # browser_daemon_port(account) 哈希一致。手动 --port 启动绕过哈希
    # 会制造双 BCC 并存（同一账号两个端口各挂一个容器，cookie/保活各自为政，
    # _bcc_alive 的账号校验也会因端口错乱而失灵）。不一致默认拒绝启动。
    from auto_dm import accounts as _acc
    _expected_port = _acc.browser_daemon_port(args.account)
    if args.port != _expected_port and not args.allow_any_port:
        print(f"[bcc] 拒绝启动：--port {args.port} 与账号「{args.account}」的"
              f"哈希端口 {_expected_port} 不一致。"
              f"端口错乱会导致 cookie 串号/双容器并存。"
              f"（确属调试需要请加 --allow-any-port）")
        raise SystemExit(2)

    # ═══════════════════════════════════════════════════════════════════════
    # 2026-09-13【单例硬守卫】落实用户铁律：
    #   「任何操作前先核对 BCC 状态；只能有一个 BCC，统一交给调度器切换」
    #
    # 为什么要做在二进制里（而不是只靠调用方自觉）：
    #   main() 此前只有「端口哈希校验」，没有「该账号已有实例在跑」的守卫
    #   —— 单例全靠调用方自觉，调度器因此可被任意路径绕过。
    #   实测事故：手工起的第二个 BCC 与常驻 BCC 抢同一 profile，且缺会员态
    #   导致 _launch 抛错 → 每 3~4 秒重启一次（用户所见「快闪」）。
    #   现在改为**二进制自证**：自己确认没有第二个实例，否则拒绝启动。
    #
    # 逃生口：--force-duplicate（显式调试用，会打 BCC-044 审计）。
    # ═══════════════════════════════════════════════════════════════════════
    if not args.force_duplicate:
        _dup = _detect_existing_bcc(args.account, args.port)
        if _dup:
            print(f"[bcc] 拒绝启动：账号「{args.account}」已有一个 BCC 在运行"
                  f"（{_dup}）。"
                  f"单账号只允许一个 BCC 常驻 —— 请把操作交给调度器："
                  f"services.browser_gate.ensure_browser(account, purpose)，"
                  f"由它复用/切换现有容器，绝不再起第二个。"
                  f"（确属极端调试需要请加 --force-duplicate）")
            raise SystemExit(3)

    _state["account"] = args.account
    _state["port"] = args.port

    try:
        from datetime import datetime
        log_dir = os.path.join(_ROOT, "logs")
        os.makedirs(log_dir, exist_ok=True)
        log_file = os.path.join(log_dir, f"browser_daemon_{datetime.now().strftime('%Y%m%d')}.log")
        logger.add(log_file, level="DEBUG", encoding="utf-8",
                   format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} - {message}",
                   retention="15 days")
    except Exception:
        pass

    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")


if __name__ == "__main__":
    main()