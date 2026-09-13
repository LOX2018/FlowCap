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

from fastapi import FastAPI, Request
from pydantic import BaseModel
from loguru import logger

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
# logger.warning("BCC-006", "描述") 的描述被丢弃（运行日志只剩代码）。
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
class ContainerBusy(Exception):
    def __init__(self, holder: str = "scan_login"):
        self.holder = holder
        super().__init__(f"容器被独占操作占用: {holder}")


_scan_exclusive = {"holder": None}  # None=空闲；否则是独占操作名


def _is_busy() -> bool:
    return _scan_exclusive["holder"] is not None


@app.exception_handler(ContainerBusy)
async def _container_busy_handler(request, exc: ContainerBusy):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=200, content={
        "ok": False, "busy": exc.holder,
        "msg": f"容器正被 {exc.holder} 独占（扫码/重登录），请稍后重试"})

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
CAP_USERINFO_HOOK_JS = r"""
(() => {
  if (window.__CAP_USERINFO__) return 'already';
  window.__CAP_USERINFO__ = { map: {} };
  const origFetch = window.fetch.bind(window);
  window.fetch = function(u, o) {
    const p = origFetch(u, o);
    try {
      const url = (typeof u === 'string') ? u : (u && u.url) || '';
      if (/im\/user\/info/.test(url)) {
        p.then(r => r.clone().json().catch(()=>null)).then(obj => {
          try {
            for (const it of (obj && obj.data) || []) {
              const su = it.sec_uid || it.sec_user_id;
              if (su) window.__CAP_USERINFO__.map[su] = {
                nickname: it.nickname || '',
                avatar: (it.avatar_thumb && it.avatar_thumb.url_list && it.avatar_thumb.url_list[0]) || '',
                uid: it.uid != null ? String(it.uid) : ''
              };
            }
          } catch(e) {}
        }).catch(()=>{});
      }
    } catch(e) {}
    return p;
  };
  const origXHR = window.XMLHttpRequest;
  window.XMLHttpRequest = function() {
    const x = new origXHR();
    const o = x.open; x.open = function(m,u,...r){ x.__u=u; x.__m=m; return o.call(x,m,u,...r); };
    const ob = x.send; x.send = function(d){ return ob.call(x,d); };
    x.addEventListener('load', function(){
      try {
        if (x.__u && /im\/user\/info/.test(x.__u)) {
          const obj = JSON.parse(x.responseText || '{}');
          for (const it of (obj.data) || []) {
            const su = it.sec_uid || it.sec_user_id;
            if (su) window.__CAP_USERINFO__.map[su] = {
              nickname: it.nickname || '',
              avatar: (it.avatar_thumb && it.avatar_thumb.url_list && it.avatar_thumb.url_list[0]) || '',
              uid: it.uid != null ? String(it.uid) : ''
            };
          }
        }
      } catch(e) {}
    });
    return x;
  };
  return 'captured';
})()
"""

# ---------------------------------------------------------------------------
# WP 通道私信消息 hook（2026-09-05 新增）
# 抖音网页版 douyin.com/chat 的私信收发会走两类请求：
#   - HTTP: imapi.douyin.com/v1/message/get_message_by_init（首包 250KB，全量会话）
#           imapi.douyin.com/v1/message/get_by_conversation（cmd 301，逐会话历史）
#           （2026-09-05 修正：原写 www.douyin.com/aweme/v1/web/... 是错的，
#             实测 404 Unsupported path(Janus)；真实接口在 imapi.douyin.com，
#             见知识库 08 §33.1 / §34.2）
#   - WebSocket: 实时推送新私信
# 这里被动 hook 这两类，把原始帧 raw 推入 window.__CAP_WP_MESSAGE__.events，
# 由后端 wp_recv 轮询读取后统一解析（不在页面内解析，保持 hook 极简、低侵入）。
# 风控边界：纯被动监听，绝不主动发请求、绝不遍历用户信息（昵称红线 08 §13）。
CAP_WP_MESSAGE_HOOK_JS = r"""(() => {
  if (window.__CAP_WP_MESSAGE__) return 'already';
  window.__CAP_WP_MESSAGE__ = { events: [] };
  const push = (kind, url, body) => {
    try {
      const ev = { kind, url, body, ts: Date.now() };
      const arr = window.__CAP_WP_MESSAGE__.events;
      arr.push(ev);
      if (arr.length > 500) arr.splice(0, arr.length - 500); // 上限防爆
    } catch(e) {}
  };
  // 2026-09-05 修正: 正则用 new RegExp(字符串) 构造。
  // 曾经的 bug: 直接写正则字面量 /.../ 且跨行 -> JS 语法错误 ->
  // 整个 init script 静默失败, window.__CAP_WP_MESSAGE__ 从未创建。
  // 匹配 imapi.douyin.com 的真实私信接口（知识库 08 §33.1 实证,
  // 不是 www.douyin.com/aweme/v1/web/... 那条, 后者实测 404 Janus）。
  const IMAPI_RE = new RegExp(
    '/(v1|v2)/.*(' +
    ['get_message_by_init', 'get_by_conversation', 'get_user_message',
     'get_info_list', 'mark_read', 'message/send', 'conversation/create',
     'conversation/info'].join('|') +
    ')'
  );
  // ---- fetch hook ----
  const origFetch = window.fetch.bind(window);
  window.fetch = function(u, o) {
    const p = origFetch(u, o);
    try {
      const url = (typeof u === 'string') ? u : (u && u.url) || '';
      if (IMAPI_RE.test(url)) {
        p.then(r => r.clone().text().catch(()=>null)).then(t => {
          if (t) push('http', url, t.slice(0, 400000));
        }).catch(()=>{});
      }
    } catch(e) {}
    return p;
  };
  // ---- XMLHttpRequest hook ----
  const origXHR = window.XMLHttpRequest;
  window.XMLHttpRequest = function() {
    const x = new origXHR();
    const o = x.open; x.open = function(m, u, ...r){ x.__u = u; x.__m = m; return o.call(x, m, u, ...r); };
    const ob = x.send; x.send = function(d){ return ob.call(x, d); };
    x.addEventListener('load', function(){
      try {
        if (x.__u && IMAPI_RE.test(x.__u)) {
          push('http', x.__u, (x.responseText || '').slice(0, 400000));
        }
      } catch(e) {}
    });
    return x;
  };
  // ---- WebSocket hook（双向：send 发出 + message 收进）----
  const OrigWS = window.WebSocket;
  window.WebSocket = function(u, p) {
    const ws = (typeof p === 'string') ? new OrigWS(u, p) : new OrigWS(u);
    const origAdd = ws.addEventListener.bind(ws);
    ws.addEventListener = function(ev, cb) {
      if (ev === 'message') {
        return origAdd(ev, (e) => {
          try {
            // 2026-09-06 全局治理：二进制帧不再丢弃为 '<binary>' 占位。
            // 抖音 IM 实时推送大量走 protobuf 二进制帧，之前 100% 丢失，
            // WP 通道只能靠 HTTP 轮询被动补消息。现在转 base64 上抛，
            // 后端侧按前缀 'B64:' 识别（wp_recv.parse_ws_frame 对非 JSON
            // 帧本来就只记 debug 跳过，不会误解析；需要实时性时再解码）。
            let d;
            if (typeof e.data === 'string') {
              d = e.data;
            } else if (e.data instanceof Blob) {
              d = 'B64:';  // Blob 异步读取复杂度高，先标记等待后续 FileReader 支持
            } else if (e.data instanceof ArrayBuffer) {
              const bytes = new Uint8Array(e.data);
              let bin = '';
              for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
              d = 'B64:' + btoa(bin);
            } else {
              d = String(e.data);
            }
            if (/im|message|conversation/i.test(d) || d.startsWith('B64:')) push('ws', String(u), d.slice(0, 400000));
          } catch(e2) {}
          return cb(e);
        });
      }
      return origAdd(ev, cb);
    };
    return ws;
  };
  return 'captured';
})()
"""



def _app_version() -> str:
    """本守护进程的构建版本（读 exe 同级 version.json；失败=unknown）。

    2026-09-13：与 backend /api/version 配套，解决「前端新/后端旧」无校验缺口。
    sidecar 与桌面端分别构建，必须能自查版本，避免部署未生效却无人察觉。
    """
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


class BrowserContainer:
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
        # ⚠️ 2026-09-13：纯 headless 会被抖音识别导致登录态强制下线（§24.10
        #    复发实证），故本字段只作「用户请求的可见性」，实际启动恒有头。
        self._headless: bool = True
        self._lock = asyncio.Lock()
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None
        # P2-A：独立导航 tab（resolve_url 专用，不占用常驻 chat 页）
        self._nav_page = None
        self._backend = ""  # "exe" / "cdp"
        self._profile_dir = ""
        self._started = False
        self._last_uid: Any = None
        self._last_refresh: float = 0.0
        # 昵称缓存：(采集时间戳, {sec_uid: {...}})。配 _prewarm 使用，
        # 避免每次「更新会话」都重跑 176s 的滚动捕获（08 §三十七）。
        self._userinfo_cache: tuple | None = None
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

    async def _launch(self) -> None:
        from auto_dm import accounts as _acc
        from auto_dm import config as _cfg
        from auto_dm.vbrowser import should_use_vb, launch_async
        env_path = _acc.env_path_of(self.account)
        if not env_path:
            raise RuntimeError(f"[bcc] 账号 {self.account} 无 .env（索引未登记？）")
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
        self._pw, self._browser, self._context, self._backend = await launch_async(
            _vb_mode, _cfg, headless=self._headless, user_data_dir=self._profile_dir,
            force=False, account=self.account)
        self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()
        # V16 踩坑：add_init_script 必须在 goto 前注入，否则前端已发完 im/user/info 再注入就截不到
        await self._context.add_init_script(CAP_USERINFO_HOOK_JS)
        await self._context.add_init_script(CAP_WP_MESSAGE_HOOK_JS)  # 2026-09-05 WP
        # 直接打开 chat 页（前端才会自发调 im/user/info）
        try:
            await self._page.goto("https://www.douyin.com/chat?isPopup=1", wait_until="domcontentloaded", timeout=20000)
        except Exception as e:
            logger.warning("BCC-005", f"[bcc] 打开 chat 页失败（不阻塞，后续接口自愈）: {e}")

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
                logger.error(
                    "BCC-006",
                    f"[bcc] {self.account} 可见性切换卡死超 900s，强制复位 "
                    f"_switching（否则窗口将永久无法唤醒）")
                self._switching = False
                self._switch_started_at = 0.0
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
                logger.warning(
                    "BCC-006", f"[bcc] context 探活失败（{phase}，{max(remain,0)}s "
                    f"后恢复强杀）: {e} —— 页面加载中，跳过重启，等冷却结束")
                return
            logger.warning("BCC-006", f"[bcc] context/page 失活，重启: {e}")
            try:
                if self._backend == "exe" and self._context is not None:
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
            await self._launch()

    async def set_visible(self, visible: bool, url: str = "") -> dict:
        """切换容器可见性：把无头容器重启为有头可见（或反向）。

        根治「打开浏览器」与「BCC 保活」抢同一 profile 的设计冲突：
          - 旧路径：open-browser 另起一个**有头** Chromium 实例指向同一 profile
            → 与常驻无头 BCC 抢 SingletonLock，后启动者拿到失效页面
            （实测：BCC 12:43 启动后探活 uid 与历史不符 → BCC-016 拒绝回写凭证）。
          - 新路径：**不另起实例**，直接让本容器以有头模式重启 context。
            用户看到的窗口就是 BCC 自己，登录态真实、且保活/回写链路不中断。

        重启 context 是必要的：Playwright 无法在运行中切换 headless。
        重启期间 _lock 串行，业务调用会排队（约 3~6s），不影响凭证。
        """
        async with self._lock:
            target = not bool(visible)
            if self._headless == target and self._context is not None:
                # 已是目标模式：先确认 context/page 真的还活着再只导航。
                # ⚠️ 2026-09-13 修复「关闭浏览器后二次双击没反应」：
                # 用户手动关掉可见窗口后，_context 仍非 None 但页面已失效，
                # 旧逻辑只尝试 goto 就静默失败（BCC-038），窗口永不回来。
                # 现改为：探活失败即视为需要重建 → 落到下方重建分支唤醒窗口。
                alive = False
                try:
                    if self._context.pages:
                        _pg = self._page if (self._page is not None
                                             and not self._page.is_closed()) \
                            else self._context.pages[0]
                        await _pg.evaluate("1")
                        alive = True
                except Exception as e:  # noqa: BLE001
                    logger.info(
                        f"[bcc] {self.account} 页面已失效（{type(e).__name__}），"
                        f"需重建 context 唤醒窗口")
                    alive = False
                if alive:
                    if url and self._page is not None and not self._page.is_closed():
                        try:
                            await self._page.goto(url, wait_until="domcontentloaded",
                                                   timeout=20000)
                        except Exception as e:  # noqa: BLE001
                            logger.warning("BCC-038", f"[bcc] {self.account} 导航失败: {e}")
                    return {"ok": True, "headless": target, "changed": False}
                # 不 alive → 继续走下面的重建流程（会真正把窗口唤醒）
            logger.info(
                f"[bcc] {self.account} 切换浏览器可见性: "
                f"headless={self._headless} -> {target}")
            # 关旧 context（释放 profile 内窗口，但保持 profile 目录不动）
            try:
                if self._backend == "exe" and self._context is not None:
                    await self._context.close()
                if self._pw is not None:
                    await self._pw.stop()
            except Exception:
                pass
            self._pw = None
            self._browser = None
            self._context = None
            self._page = None
            self._nav_page = None
            self._headless = target
            # 切换改为后台执行：有头指纹内核冷启动 + 页面加载可能远超 HTTP 调用方
            # 超时（实测冷启动 2~8 分钟）。若同步 await self._launch()，/show 请求会
            # 在 60~180s 超时，前端误报「打开失败」，且 BCC 主循环被阻塞卡死。
            # 改后台任务：立即返回「切换中」，launch 完成后设冷却期保护探活不误杀。
            self._switching = True
            self._switch_started_at = time.time()
            asyncio.get_event_loop().create_task(
                self._do_switch_background(target, url))
            mode_str = "有头可见" if visible else "纯无头"
            return {"ok": True, "headless": target, "changed": True,
                    "switching": True,
                    "msg": f"正在切换为{mode_str}，"
                           f"窗口就绪后自动完成（冷启动约 1~3 分钟）"}

    async def _do_switch_background(self, target: bool, url: str = "") -> None:
        """后台执行可见性切换的 _launch 部分。失败只告警、不卡死容器。"""
        try:
            # _launch 内部已统一调用 _wait_profile_released（BCC 作为 profile
            # 唯一持有者的调度：重建 context 前先等旧进程完全退出，防 TargetClosed）。
            await self._launch()
            self._switch_cool_until = time.time() + _SWITCH_COOLDOWN_SEC
            logger.info(
                f"[bcc] {self.account} 可见性切换完成(headless={target})，"
                f"进入 {_SWITCH_COOLDOWN_SEC}s 切换冷却期（探活只告警不强杀）")
            if url and self._page is not None and not self._page.is_closed():
                try:
                    await self._page.goto(url, wait_until="domcontentloaded",
                                           timeout=20000)
                except Exception as e:  # noqa: BLE001
                    logger.warning("BCC-039",
                        f"[bcc] {self.account} 切换后导航失败: {e}")
        except Exception as e:
            logger.error("BCC-006",
                f"[bcc] 可见性切换失败(切换为{'有头' if target else '无头'}): {e} —— "
                f"将在冷却期后由探活自愈（不自动重启，防误杀）", exc_info=True)
        finally:
            self._switching = False
            self._switch_started_at = 0.0

    async def _wait_profile_released(self, timeout: float = 10.0) -> None:
        """等待 profile 的锁文件消失（chromium 进程完全退出）。

        切换可见性时 close()+stop() 是异步的，chromium 进程可能还没退，
        新 launch_persistent_context 立即启动会 TargetClosed。这里轮询
        profile 下的锁文件消失；超时则继续（不再等，避免永久卡死）。

        Chromium 锁文件命名随内核/版本变化：官方 Chromium 用 SingletonLock，
        ungoogled-chromium 实测是 lockfile（2026-09-12 现场核实）。两者都查。
        """
        if not self._profile_dir:
            return
        lock_files = [os.path.join(self._profile_dir, n)
                      for n in ("SingletonLock", "lockfile")]
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

    async def _exec(self, coro_factory):
        """在 _lock 内执行浏览器操作（自愈 + 串行）。coro_factory 是无参 callable 返回 coroutine。

        P2-B：scan_login 独占窗口内（context 已关、扫码中）快速失败，
        避免调用方排队干等到 HTTP 超时。注意 _launch 自身不走本检查
        （scan_login 的 _do 内部会调 _launch）。
        """
        if _is_busy():
            raise ContainerBusy(_scan_exclusive["holder"])
        async with self._lock:
            # 切换期（_launch 后台重建 context）快速失败：此时 _context 为
            # None，继续执行只会拿到 None 崩溃或误判失活触发重启死循环
            # （2026-09-13 BCC-006 刷屏事故）。调用方按「容器忙」重试即可。
            if self._switching:
                raise ContainerBusy("切换可见性中（context 重建），请稍后重试")
            await self._ensure_alive()
            return await coro_factory()

    # -------------------- 业务方法（在 _lock 内执行）--------------------

    async def get_cookies(self) -> dict:
        """读取实时 cookie。返回 {name: value}。"""
        async def _do():
            cks = await self._context.cookies()
            return {c["name"]: c["value"] for c in cks}
        return await self._exec(_do)

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
                    logger.warning("BCC-007", f"[bcc] 批量查昵称 evaluate 失败: {e}")
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
                    logger.warning("BCC-008", f"[bcc] 批量查昵称(uid) evaluate 失败: {e}")
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

    async def exec_js(self, js: str, arg=None, timeout: int = 30):
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
        """

        async def _do():
            if "/chat" not in (self._page.url or ""):
                # 取图需要抖音域上下文（同域 fetch + 登录态 + 前端解密）
                await self._page.goto(
                    "https://www.douyin.com/chat?isPopup=1",
                    wait_until="domcontentloaded", timeout=25000)
                await self._page.wait_for_timeout(1500)
            self._page.set_default_timeout(timeout * 1000)
            return await self._page.evaluate(js, arg)

        return await self._exec(_do)

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
                logger.warning("BCC-009", f"[bcc] wp_send_text 失败: "
                               f"{res.get('error') if isinstance(res, dict) else res}")
            return res if isinstance(res, dict) else {"ok": False, "error": str(res)}
        except Exception as e:
            logger.warning("BCC-010", f"[bcc] wp_send_text 失败: {e}")
            return {"ok": False, "error": str(e)}


    async def capture_userinfo_map(self, wait: int = 15) -> dict:
        """被动 hook 截前端自己发的 im/user/info 响应（零主动请求、零风控）。

        复用本容器已持有的常驻浏览器 context/page（不另开浏览器、不抢 profile）。
        在 _lock 内执行，与 bulk_user_info/resolve_url 串行无冲突。
        返回 {sec_uid: {"nickname": str, "avatar": str, "uid": str}}。

        2026-09-01 新增**进程内缓存**（08 §三十七，修复「更新会话 220s」）：
          本容器启动后 _prewarm 线程会后台跑一次完整捕获（176s，日志
          「昵称缓存预热完成：N 个」）。若之后 backend 调 /capture_userinfo
          又从头滚一遍，预热就白做了 —— 这正是 220s 的主因。
          这里把成功结果缓存 DY_USERINFO_CACHE_SEC 秒（默认 600），
          命中则直接返回，让「更新会话」在预热完成后几乎零等待。
          设为 0 可关闭（每次都真跑，用于调试）。
        """
        # ── 缓存命中检查（在 _lock 外，避免不必要的串行等待）──
        try:
            _ttl = int(os.environ.get("DY_USERINFO_CACHE_SEC", "600"))
        except Exception:
            _ttl = 600
        if _ttl > 0 and self._userinfo_cache:
            _ts, _data = self._userinfo_cache
            if _data and (time.time() - _ts) < _ttl:
                logger.info(
                    f"[bcc] 复用昵称缓存（{len(_data)} 个，"
                    f"{time.time() - _ts:.0f}s 前采集），跳过滚动")
                return _data

        async def _do():
            # hook 已在 _launch 中 add_init_script 注入，直接等前端发 im/user/info
            if "/chat" not in self._page.url:
                await self._page.goto("https://www.douyin.com/chat?isPopup=1",
                                       wait_until="domcontentloaded", timeout=25000)
                await self._page.wait_for_timeout(2000)
            # 等待前端首发 im/user/info（首屏会话）
            await self._page.wait_for_timeout(wait * 1000)

            # 2026-09-01 优化：**跳过已点击过的会话**。
            # 原实现每轮都把当前可见的 12 项全点一遍（含前几轮已点过的），
            # 但重复点击不会触发新的 im/user/info（前端已有缓存），
            # 是纯浪费：实测 40 轮 × (12 项 × 400ms) 仅点击就占数十秒。
            _clicked = set()

            async def _click_all():
                items = await self._page.query_selector_all(
                    ".conversationConversationItemwrapper")
                n_new = 0
                for idx, it in enumerate(items):
                    # 用会话文本做稳定标识（DOM 元素会随滚动重建，下标不可靠）
                    try:
                        key = (await it.inner_text())[:40]
                    except Exception:
                        key = f"__idx{idx}"
                    if key in _clicked:
                        continue
                    _clicked.add(key)
                    try:
                        await it.click(timeout=2000)
                        n_new += 1
                        # 仅在【真的点了新会话】时才等待，跳过已点的不付等待成本
                        await self._page.wait_for_timeout(400)
                    except Exception:
                        pass
                return n_new

            # 平滑逐屏滚动 + 每屏点进每个可见会话（覆盖懒加载的全部会话）
            # 抖音私信列表是增量懒加载：大跨度 scrollTop=scrollHeight 跳跃会让
            # 中间大量会话不进入可视区 → 前端不为它们发 im/user/info → 缺口。
            # 故必须一屏一屏平滑往下滚，让每个会话都真正渲染、触发其 im/user/info。
            #
            # 2026-08-31 优化：加**提前退出**。
            # 实测冷启动跑满 40 轮要 152~162s，是「更新会话」179s 的 90%。
            # 但昵称往往在前几屏就已截全（222 会话实测截到 278 个），
            # 后面 30 多轮全是空转。这里连续 3 轮无新增就停，省下大量时间。
            _stall = 0
            _prev = -1
            import time as _time

            _t0 = _time.time()
            for _round in range(40):  # 上限 40 屏防死循环
                _n_new = await _click_all()
                # 先看当前已截获数量，判断是否还在增长
                try:
                    _cur = await self._page.evaluate(
                        "() => window.__CAP_USERINFO__ "
                        "? Object.keys(window.__CAP_USERINFO__.map || {}).length : 0")
                except Exception:
                    _cur = -1
                # 可观测性（08 §三十七）：每轮打印耗时/累计/新增，
                # 让 176s 的黑盒变成能定位的明细，避免下次又靠猜。
                logger.info(
                    f"[bcc] 滚动轮次 {_round + 1}: 新点击={_n_new} "
                    f"累计昵称={_cur} 用时={_time.time() - _t0:.1f}s")
                if _cur >= 0 and _prev >= 0 and _cur <= _prev:
                    _stall += 1
                    if _stall >= 3:
                        logger.info(
                            f"[bcc] 昵称无新增（连续 {_stall} 轮，当前 {_cur} 个），"
                            f"提前结束滚动（第 {_round} 轮，用时 "
                            f"{_time.time() - _t0:.1f}s）")
                        break
                else:
                    _stall = 0
                _prev = _cur

                # 平滑滚下一屏（一次一个 clientHeight，不跳到底）
                moved = await self._page.evaluate(
                    "() => { const el = document.querySelector("
                    "'.conversationConversationListwrapper'); "
                    "if (!el) return false; "
                    "const before = el.scrollTop; "
                    "el.scrollTop = before + el.clientHeight; "
                    "return el.scrollTop > before; }")
                await self._page.wait_for_timeout(1200)  # 等该屏渲染 + 触发 im/user/info
                # 到底判定：已滚到接近底部 或 高度不再增长
                at_bottom = await self._page.evaluate(
                    "() => { const el = document.querySelector("
                    "'.conversationConversationListwrapper'); "
                    "if (!el) return true; "
                    "return el.scrollTop + el.clientHeight >= el.scrollHeight - 4; }")
                if at_bottom:
                    break
            # 到底后再点一轮，确保末屏会话也点进（触发其 im/user/info）
            await _click_all()
            try:
                cap = await self._page.evaluate(
                    "() => window.__CAP_USERINFO__ ? window.__CAP_USERINFO__.map : {}")
            except Exception as e:
                logger.warning("BCC-011", f"[bcc] 读取 hook 结果失败: {e}")
                cap = {}
            cap = cap or {}
            # 写入进程内缓存（供后续 /capture_userinfo 直接命中，
            # 避免预热完成后又重复跑一遍 176s 的滚动）
            if cap:
                self._userinfo_cache = (time.time(), cap)
                logger.info(
                    f"[bcc] 昵称捕获完成：{len(cap)} 个，"
                    f"总耗时 {_time.time() - _t0 + wait:.1f}s（已缓存）")
            return cap
        return await self._exec(_do)

    async def capture_wp_messages(self) -> list[dict]:
        """读取 BCC hook 截到的 WP 通道私信事件（读后清空）。

        2026-09-05 新增。CAP_WP_MESSAGE_HOOK_JS 已被动把 HTTP 响应 / WS 帧
        raw 推入 window.__CAP_WP_MESSAGE__.events，这里取回并清空，
        由后端 wp_recv 统一解析（页面内不做解析，保持 hook 极简）。

        返回 [{kind: 'http'|'ws', url, body, ts}, ...]。
        """
        async def _do():
            if "/chat" not in (self._page.url or ""):
                await self._page.goto("https://www.douyin.com/chat?isPopup=1",
                                       wait_until="domcontentloaded", timeout=25000)
                await self._page.wait_for_timeout(2000)
            try:
                evs = await self._page.evaluate(
                    "() => window.__CAP_WP_MESSAGE__ ? window.__CAP_WP_MESSAGE__.events : []")
                # 取回后立即清空，避免下次重复处理
                await self._page.evaluate(
                    "() => { if (window.__CAP_WP_MESSAGE__) window.__CAP_WP_MESSAGE__.events = []; }")
            except Exception as e:
                logger.warning("BCC-012", f"[bcc] 读 wp message 失败: {e}")
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
                logger.warning("BCC-013", f"[bcc] 导航 tab 创建失败（退回主 page）: {e}")
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
                if page is not self._page:
                    try:
                        await page.goto("about:blank", wait_until="commit", timeout=5000)
                    except Exception:
                        pass
        return await self._exec(_do)

    async def scan_login(self, force: bool = False, timeout: int = 300) -> dict:
        """扫码登录/刷新凭证。force=True 忽略现有凭证重新扫码。

        委托 DYLoginApi.get_login_auth（复用其扫码 + 风控守卫 + 凭证落盘逻辑），
        但在【本容器的 context】里执行——不开新浏览器，避免抢锁。
        """
        from dy_apis.login_api import DYLoginApi
        from auto_dm import accounts as _acc
        env_path = _acc.env_path_of(self.account)
        if not env_path:
            return {"ok": False, "msg": "账号 .env 未登记"}

        # DYLoginApi.get_login_auth 内部会 launch_async（开浏览器）——我们需要让它复用
        # 本容器的 context。但该函数当前不支持外部注入 context，最简方案：临时关闭
        # 本容器 context，让 DYLoginApi 独占 profile 完成扫码，完成后重启容器。
        async def _do():
            # P2-B：标记独占窗口。从 context 关闭到 _launch 完成期间，
            # 其他浏览器操作走 _exec 时会快速失败而非排队干等
            _scan_exclusive["holder"] = "scan_login"
            try:
                # 关闭本容器 context，让 DYLoginApi 独占 profile
                try:
                    if self._backend == "exe" and self._context is not None:
                        await self._context.close()
                    if self._pw is not None:
                        await self._pw.stop()
                except Exception:
                    pass
                self._pw = None
                self._browser = None
                self._context = None
                self._page = None
                self._nav_page = None  # P2-A：context 已关闭，导航 tab 引用作废
                api = DYLoginApi()
                auth = await api.get_login_auth(
                    headless=False, env_path=env_path, force=force,
                    landing_url="https://www.douyin.com/chat?isPopup=1")
                ok = bool(auth and getattr(auth, "cookie", None))
                # 重启容器 context
                await self._launch()
                return {"ok": ok, "uid": getattr(auth, "uid", None) if auth else None}
            finally:
                _scan_exclusive["holder"] = None
        return await self._exec(_do)

    # -------------------- 健康与保活 --------------------

    def status(self) -> dict:
        from auto_dm import accounts as _acc
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
            "logged_in": bool(env_path and os.path.exists(env_path)),
            # 2026-09-13：上报自身版本，供桌面端比对「前端新/后端旧」
            "version": _app_version(),
        }

    def _load_uid_from_env(self) -> Any:
        from auto_dm import accounts as _acc
        from dy_apis.login_api import DYLoginApi
        from dy_apis.douyin_api import DouyinAPI
        env_path = _acc.env_path_of(self.account)
        if not env_path:
            return None
        try:
            auth = DYLoginApi._load_auth_from_env(env_path)
            if auth and auth.cookie:
                # 2026-09-07：UID 探活统一由 services.uid_probe 调度。
                # keepalive 只读取调度结果（此前此处每轮独立打 query/user）。
                try:
                    from services.uid_probe import get_uid as _uid_get
                    _u = _uid_get(self.account)
                    if _u is not None:
                        return _u
                except Exception:
                    pass
                # 2026-09-08 加固：fallback 裸探活同样要过历史一致性校验。
                # 事故实证：张老师 .env 被保活回写污染为 4175 的凭证后，
                # 裸探活稳定返回 4175（幽灵 uid，历史会话 0 命中），导致
                # keepalive 每轮判「uid 漂移」→ 无限 scan_login 重启循环。
                # 这里复用 uid_probe 的一致性判据：不一致即视为不可信，
                # 返回 None（让上游走兜底），绝不把幽灵 uid 当有效身份。
                try:
                    from services.uid_probe import (
                        _uid_consistent_with_history as _uid_ok)
                    _fallback_uid = DouyinAPI.get_my_uid(auth)
                    if _fallback_uid and _uid_ok(self.account, _fallback_uid):
                        return _fallback_uid
                    if _fallback_uid:
                        logger.warning("BCC-014", 
                            f"[bcc] 账号「{self.account}」裸探活 uid={_fallback_uid} "
                            f"与历史会话不一致，判为不可信（拒绝返回）")
                    return None
                except Exception:
                    return DouyinAPI.get_my_uid(auth)
        except Exception:
            pass
        return None

    def _page_login_state_sync(self) -> dict:
        """页面级登录态检查（keepalive 用，同步包装 async exec_js）。

        2026-09-06 P1 修复（WP 通道失效事故）：query/user 在「半登录态」
        （页面显示"一键登录"待激活）下依然返回 uid，导致 keepalive 误报
        "登录态正常"，而页面内一切操作（WP 发送/昵称捕获）实际已失效。
        页面级判据（知识库 08 §24.1 铁律）：convItems>0 且无「一键登录/扫码」。
        """
        import asyncio as _aio

        async def _probe():
            try:
                res = await self.exec_js(
                    "() => ({"
                    " conv: document.querySelectorAll('.conversationConversationItemwrapper').length,"
                    " rel: /一键登录|扫码登录|二维码失效/.test(document.body.innerText || ''),"
                    " url: location.href.slice(0, 60)})", timeout=15)
                if isinstance(res, dict):
                    return res
            except Exception as e:
                logger.debug(f"[bcc] 页面登录态探测异常: {e}")
            return None

        try:
            loop = self._loop or asyncio.get_event_loop()
            fut = _aio.run_coroutine_threadsafe(_probe(), loop) \
                if loop.is_running() else _aio.ensure_future(_probe())
            return fut.result(timeout=25) or {}
        except Exception as e:
            logger.debug(f"[bcc] 页面登录态探测失败: {e}")
            return {}

    async def _read_page_sign(self) -> dict:
        """从当前页面读取 security-sdk 的最新签名数据（web_protect / keys）。

        返回 {"web_protect": str, "keys": str}；任一缺失返回 {}（视为失败）。
        在 _lock 内执行（浏览器串行铁律）。

        背景：抖音私信走 imapi.douyin.com 私有网关，靠 protobuf 体内的
        ticket/ts_sign/sdk_cert（均由 security-sdk 的 web_protect/keys 派生）
        鉴权，与 cookie 是**两套独立时效**。只更新 cookie 不更新签名，
        网关会返回 cmd=609 INVALID_REQUEST。
        """
        async def _do():
            page = self._page
            if page is None or page.is_closed():
                return {}
            keys_str = None
            wp_str = None
            # 与 login_api 的读取姿势对齐：轻滚动 + 多次重试（SDK 异步生成）
            for _ in range(4):
                try:
                    keys_str = await page.evaluate(
                        'localStorage["security-sdk/s_sdk_crypt_sdk"]')
                    wp_str = await page.evaluate(
                        'localStorage["security-sdk/s_sdk_sign_data_key/web_protect"]')
                except Exception:
                    return {}
                if keys_str and wp_str:
                    break
                try:
                    await page.mouse.wheel(0, 600)
                except Exception:
                    pass
                await asyncio.sleep(1.5)
            if not (keys_str and wp_str):
                return {}
            return {"web_protect": wp_str, "keys": keys_str}

        return await self._exec(_do)

    async def refresh_cookie_to_env(self) -> dict:
        """读实时 cookie，写回 .env。返回 {ok, cookie_count, sessionid?}。

        2026-09-06 P0 修复（知识库 08 §24.9 uid 轮换事故）：写回前先校验
        新 cookie 的身份一致性 —— 用新 cookie 做 uid 探活，与 .env 既有 uid
        （self._last_uid / .env 中 conv_id 归属）比对：
          - 探活失败（拿不到 uid）→ 拒绝写入，保住 .env 里最后一份好凭证；
          - uid 与上次不一致（漂移）→ 拒绝写入并告警（疑似登录态被替换/轮换）。
        仅探活成功且 uid 一致才允许覆盖，避免好凭证被坏凭证冲掉。
        """
        from auto_dm import accounts as _acc
        from dy_apis.login_api import DYLoginApi
        from dy_apis.douyin_api import DouyinAPI
        env_path = _acc.env_path_of(self.account)
        if not env_path:
            return {"ok": False, "msg": "账号 .env 未登记"}
        # 先加载 auth（拿签名四件套），再用实时 cookie 覆盖 cookie 字段
        auth = DYLoginApi._load_auth_from_env(env_path)
        cks = await self.get_cookies()
        if not (cks.get("sessionid") or cks.get("sid_tt")):
            return {"ok": False, "msg": "profile 内无登录态"}

        # ---- P0 门禁 1：新 cookie 必须能探活出 uid（登录态有效的基本判据）----
        # 2026-09-06 优化：探活结果缓存 60s（类级），避免每条消息发送前的
        # /cookie 刷新都做一次 query/user 网络探活（实测增加 0.5-1s 延迟）。
        # 缓存键 = uid 值本身；60s 内已探活过同一 uid 直接复用。
        probe_auth = DYLoginApi._load_auth_from_env(env_path)
        probe_auth.cookie = cks
        probe_auth.cookie_str = "; ".join(f"{k}={v}" for k, v in cks.items())
        probe_auth.uid = None  # 强制走网络探活，不吃 auth 缓存
        now_ts = time.time()
        cached = getattr(BrowserContainer, "_uid_probe_cache", None)
        new_uid = None
        if cached and now_ts - cached[0] < 60:
            new_uid = cached[1]  # 60s 内探活过，复用
        else:
            try:
                # P1-C：force_probe=True —— 本门禁写入 .env 前必须真探活，
                # 绝不能吃进程级探活缓存（否则可能把陈旧登录态当有效写入）
                new_uid = DouyinAPI.get_my_uid(probe_auth, force_probe=True)
            except Exception:
                new_uid = None
            BrowserContainer._uid_probe_cache = (now_ts, new_uid)
        if not new_uid:
            logger.warning("BCC-015", 
                f"[bcc] 拒绝写入 .env：新 cookie 探活失败（无 uid），"
                f"保留既有凭证。疑似 profile 登录态失效，请重新扫码。")
            return {"ok": False, "msg": "新 cookie 探活失败（登录态无效），已保留原凭证"}

        # ---- P0 门禁 1.5（2026-09-08）：探活 uid 必须与该账号历史会话一致 ----
        # 事故实证：张老师 profile 残留 4175 凭证（登录态失效后遗留），
        # 首次回写（基线 None 放行）把 4175 写进 .env → 后续所有探活全
        # 返回 4175 → keepalive 每轮「uid 漂移」→ 无限 scan_login 重启。
        # 幽灵 uid 在该账号历史 conv_id 中 0 命中，此门禁永远拦得住。
        try:
            from services.uid_probe import _uid_consistent_with_history as _uid_ok
            if not _uid_ok(self.account, new_uid):
                logger.error("BCC-016", 
                    f"[bcc] 拒绝写入 .env：探活 uid={new_uid} 与该账号「{self.account}」"
                    f"历史会话不一致（幽灵 uid，0 命中）。profile 登录态疑似"
                    f"失效/残留他人凭证，请重新扫码登录本账号。")
                return {"ok": False,
                        "msg": f"探活 uid={new_uid} 与历史会话不一致，"
                               f"疑似残留凭证，已拒绝写入（请重新扫码）"}
        except Exception:
            pass

        # ---- P0 门禁 2：uid 与既有值一致性（漂移 = 身份被替换/轮换）----
        # 2026-09-06 全局治理：原比对 self._last_uid，但 run_keepalive 在
        # uid 漂移时会【先更新 _last_uid 再触发 scan_login】（browser_daemon
        # 1006 行注释「记录新值」），等 refresh_cookie_to_env 再跑时 _last_uid
        # 已经是新值，门禁 2 永远命中不了 —— 自我作废。
        # 改用独立基线 _uid_at_last_env_write：只在【成功写入 .env】时更新，
        # 不受 keepalive 漂移检测影响，两条链路互不干扰。
        old_uid = getattr(self, "_uid_at_last_env_write", None)
        if old_uid and str(old_uid) != str(new_uid):
            logger.error("BCC-017", 
                f"[bcc] 拒绝写入 .env：uid 漂移！old={old_uid} new={new_uid}。"
                f"疑似账号身份被轮换/替换，保留既有凭证并告警。")
            return {"ok": False,
                    "msg": f"uid 漂移({old_uid}→{new_uid})，已保留原凭证，请重新扫码确认"}

        # ---- 2026-09-12 根治：签名必须随 cookie 一起刷新 ----
        # 旧实现只覆盖 auth.cookie，而 auth 的 ticket/ts_sign/web_protect 来自
        # **_load_auth_from_env（即 .env 里的旧值）** —— 于是写回的是
        # 「新 cookie + 旧签名」。抖音 IM 网关按 protobuf 体内签名鉴权，
        # 签名过期即返回 cmd=609 INVALID_REQUEST（实测张老师 create_conversation
        # 必失败，而 cookie 完全有效，极易误判为「账号被风控」）。
        # 修复：顺手从**当前页面**读一次最新的 security-sdk 签名数据，
        # 成功才一并写回；读不到则保持原签名（不阻塞、不写残缺凭证）。
        try:
            _fresh = await self._read_page_sign()
            if _fresh:
                auth.web_protect_str = _fresh.get("web_protect") or auth.web_protect_str
                auth.keys_str = _fresh.get("keys") or getattr(auth, "keys_str", "")
                # perepare_auth 会重新派生 ticket/ts_sign/ree_public_key 等
                try:
                    auth.perepare_auth("", auth.web_protect_str, auth.keys_str)
                except Exception:
                    pass
                _has_sdk = bool(getattr(auth, "ticket", None) or
                                getattr(auth, "ts_sign", None))
                logger.info(
                    f"[bcc] 观测态写回：已同步页面最新签名"
                    f"（web_protect={'有' if auth.web_protect_str else '无'}, "
                    f"keys={'有' if auth.keys_str else '无'}, "
                    f"ticket={'有' if _has_sdk else '无'}）")
            else:
                logger.debug("[bcc] 观测态写回：未读到页面签名，沿用既有签名")
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[bcc] 观测态写回：读取页面签名失败（沿用既有）: {e}")

        auth.cookie = cks
        auth.cookie_str = "; ".join(f"{k}={v}" for k, v in cks.items())
        try:
            DYLoginApi().save_credential(auth, env_path)
        except Exception as e:
            logger.warning("BCC-018", f"[bcc] 写回 .env 失败: {e}")
        self._last_refresh = time.time()
        self._last_uid = new_uid
        # 2026-09-06 全局治理：独立基线，只在成功写入 .env 时更新（见上方门禁 2 注释）
        self._uid_at_last_env_write = new_uid
        return {"ok": True, "cookie_count": len(cks), "uid": str(new_uid),
                "sessionid": cks.get("sessionid", "")[:12],
                "cookies": "; ".join(f"{k}={v}" for k, v in cks.items()),
                "cookie_dict": cks}

    def run_keepalive(self, stop_ev: threading.Event, interval: int = 300) -> None:
        """每 interval 秒探活一次；uid 探活失败或漂移时触发刷新/告警。

        2026-09-06 P0 修复（知识库 08 §24.9 uid 轮换事故）：不再只判
        "能拿到 uid = 正常"。uid 与上次比对，漂移视为凭证异常
        （疑似身份被轮换/替换），记 error 并触发 scan_login 重扫，
        而非打"登录态正常"绿标。

        2026-09-06 P0 熔断（知识库 08 §24.13）：实测发现恶性循环——
        半登录态 -> scan_login 重启浏览器 -> 仍半登录（session 服务端已死，
        自动登录救不回）-> 5 分钟后又来。每 5 分钟一次完整浏览器重启 =
        极强风控信号。修复：scan_login 连续失败 2 次即熔断，退避 30 分钟；
        期间只记日志告警（人工扫码后自然恢复），不再自动重启浏览器。
        """
        # 凭证更新方式（2026-09-12 用户需求：两套更新路径共存、由用户自选）：
        #   observe = 观测态静默更新（读实时 cookie+页面签名写回 .env，不弹窗）
        #   popup   = 仅弹窗激活更新（检测到需重激活时弹指纹浏览器请用户点）
        #   both    = 观测优先，确认页面登录态失效才弹窗（默认兼容）
        cred_mode = _cred_refresh_mode()
        logger.info(
            f"[bcc] 保活心跳启动，间隔 {interval}s，凭证更新方式={cred_mode}"
            f"（{'观测态静默/弹窗激活/两者兼容' if cred_mode=='both' else cred_mode}）")
        scan_fail_count = 0          # 连续 scan_login 失败计数
        SCAN_BREAKER_LIMIT = 2       # 连续失败 N 次 -> 熔断
        SCAN_BACKOFF_SEC = 1800      # 熔断退避 30 分钟
        breaker_until = 0.0          # 熔断截止时间戳
        # 2026-09-06（2.1a）：保活回写 .env 的节流时间戳。
        # 初始值设成「刚启动」以便启动后第一个探活周期就同步一次新鲜凭证
        # （原实现从不回写，.env 长期停留在旧凭证）。
        last_cookie_sync = 0.0
        while not stop_ev.is_set():
            if stop_ev.wait(interval):
                break
            now = time.time()
            in_breaker = now < breaker_until
            # 切换中/切换冷却期：set_visible 无头↔有头重启 context 后，新页面还在
            # 加载，探活会误判失效并触发 BCC-006 强杀（→ 页面加载中断 → 抖音「环境
            # 异常」）。此期间跳过整个探活周期，只记 debug；切换完成冷却期结束自然恢复。
            if self._switching or now < self._switch_cool_until:
                remain = int(self._switch_cool_until - now)
                phase = "切换中(_launch后台)" if self._switching else "切换冷却期"
                logger.debug(
                    f"[bcc] {phase}({max(remain,0)}s)，跳过探活（防误杀新 context）")
                continue
            try:
                uid = self._load_uid_from_env()
                prev_uid = getattr(self, "_last_uid", None)
                if uid and prev_uid and str(uid) != str(prev_uid):
                    # uid 漂移：身份被替换/轮换，凭证不可信
                    logger.error("BCC-019", 
                        f"[bcc] uid 漂移！old={prev_uid} new={uid}，"
                        f"凭证身份存疑，触发自动刷新…")
                    self._last_uid = uid  # 记录新值，后续漂移检测以新值为基线
                    if self._loop and not in_breaker:
                        fut = asyncio.run_coroutine_threadsafe(
                            self.scan_login(force=False), self._loop)
                        try:
                            fut.result(timeout=120)
                            scan_fail_count = 0
                        except Exception as e:
                            logger.warning("BCC-020", f"[bcc] uid 漂移后自动刷新失败: {e}")
                            scan_fail_count += 1
                elif uid:
                    # 2026-09-06 P1：uid 探活通过 ≠ 页面登录态有效。
                    # 「半登录态」（页面显示一键登录待激活）下 query/user 仍返回
                    # uid，但页面内 WP 发送/昵称捕获已全部失效（实测）。
                    page_state = self._page_login_state_sync()
                    if page_state.get("rel") or not page_state.get("conv"):
                        if in_breaker:
                            # 熔断中：只告警，绝不重启浏览器（防风控恶性循环）
                            remain = int(breaker_until - now)
                            logger.warning("BCC-021", 
                                f"[bcc] 页面仍需重激活（conv={page_state.get('conv')}），"
                                f"scan_login 已熔断（连续失败 {scan_fail_count} 次），"
                                f"{remain // 60} 分钟内不再自动重启浏览器，"
                                f"请在指纹浏览器完成扫码登录")
                            continue
                        logger.warning("BCC-022", 
                            f"[bcc] 页面级登录态失效（conv={page_state.get('conv')} "
                            f"rel={page_state.get('rel')}），uid={uid} 仍有效但页面需重新激活，"
                            f"凭证更新方式={cred_mode}"
                            + ("（observe：不弹窗，等用户自行激活）"
                               if cred_mode == "observe" else "，触发 scan_login…"))
                        # observe 模式：用户明确选择「不弹窗」，只在日志/前端提示，
                        # 绝不自动重启浏览器（避免风控暴露）。both/popup 才走 scan_login。
                        if cred_mode == "observe":
                            continue
                        if self._loop:
                            fut = asyncio.run_coroutine_threadsafe(
                                self.scan_login(force=False), self._loop)
                            try:
                                fut.result(timeout=120)
                                scan_fail_count = 0
                            except Exception as e:
                                logger.warning("BCC-023", f"[bcc] 页面重激活失败: {e}")
                                scan_fail_count += 1
                            if scan_fail_count >= SCAN_BREAKER_LIMIT:
                                breaker_until = time.time() + SCAN_BACKOFF_SEC
                                logger.error("BCC-024", 
                                    f"[bcc] scan_login 连续失败 {scan_fail_count} 次，"
                                    f"熔断 {SCAN_BACKOFF_SEC // 60} 分钟。"
                                    f"session 疑似服务端已失效，自动登录救不回，"
                                    f"请在指纹浏览器重新扫码；期间仅告警不重启浏览器")
                    else:
                        self._last_uid = uid
                        scan_fail_count = 0
                        logger.debug(f"[bcc] 登录态正常(uid={uid}, "
                                     f"conv={page_state.get('conv')})")
                        # 2026-09-06（2.1a 保活回写）：原实现「保活心跳」
                        # 只做探活 + scan_login，**从不把浏览器 profile 里的
                        # 新鲜 cookie 回写 .env** —— 导致 .env 长期停留在
                        # 上次扫码/POST /cookie 时的旧凭证，而 BCC 手里其实
                        # 一直有更新鲜的（浏览器会自动续期）。recv_daemon 等
                        # 消费者读 .env 拿到的就是陈旧凭证。
                        #
                        # 修复：登录态正常时【节流】回写（默认 30 分钟一次，
                        # DY_BCC_COOKIE_SYNC_SEC 可调；设为 0 可关闭）。
                        # 只在「页面登录态确认正常」时写，绝不覆盖有效凭证。
                        try:
                            _sync_sec = int(os.environ.get(
                                "DY_BCC_COOKIE_SYNC_SEC", "1800"))
                        except Exception:
                            _sync_sec = 1800
                        # 2026-09-12：popup 模式明确要求「只弹窗激活更新」，
                        # 故不做静默回写（把更新时机交给用户手动激活）。
                        if cred_mode == "popup":
                            logger.debug(
                                "[bcc] 凭证更新方式=popup，跳过观测态静默回写"
                                "（等待用户弹窗激活）")
                        elif _sync_sec > 0 and (now - last_cookie_sync) >= _sync_sec:
                            last_cookie_sync = now
                            try:
                                if self._loop:
                                    fut = asyncio.run_coroutine_threadsafe(
                                        self.refresh_cookie_to_env(), self._loop)
                                    r = fut.result(timeout=60)
                                    if r and r.get("ok"):
                                        logger.info(
                                            f"[bcc] 保活回写：已将 profile 新鲜凭证"
                                            f"同步至账号 .env（uid={uid}）")
                                    else:
                                        logger.debug(
                                            f"[bcc] 保活回写跳过："
                                            f"{(r or {}).get('msg', '无更新')}")
                            except Exception as e:
                                logger.debug(f"[bcc] 保活回写失败（不影响运行）: {e}")
                else:
                    # 2026-09-13 止血：此处原为「探活拿不到 uid → 直接 scan_login」
                    # 且【从不累加 scan_fail_count、从不置 breaker_until】——
                    # 熔断逻辑写了却未接线，导致无限循环：
                    #   探活失败 → scan_login 抢占 profile → 容器 context 失活
                    #   → BCC-006 重启 → 又探活失败 → …（实测单日 155 次失活、
                    #   78 次自动刷新、90 次 AUTH-050，肉眼所见即「浏览器频繁自启」）
                    # 反复重启 + 反复刷新凭证对抖音是极强风控信号，必须熔断。
                    logger.warning("BCC-025", "[bcc] 登录态失效，自动刷新凭证…")
                    if in_breaker:
                        remain = int(breaker_until - now)
                        logger.warning("BCC-021",
                            f"[bcc] 探活仍拿不到有效 uid，scan_login 已熔断"
                            f"（连续失败 {scan_fail_count} 次），{remain // 60} 分钟内"
                            f"不再自动重启浏览器，请在指纹浏览器完成扫码登录")
                        continue
                    # 在子线程调 async scan_login：投递到主 loop
                    if self._loop:
                        fut = asyncio.run_coroutine_threadsafe(
                            self.scan_login(force=False), self._loop)
                        try:
                            fut.result(timeout=120)
                            scan_fail_count += 1
                        except Exception as e:
                            scan_fail_count += 1
                            logger.warning("BCC-026", f"[bcc] 自动刷新凭证失败: {e}")
                        # 关键：探活失败路径同样要触发熔断（原缺失）
                        if scan_fail_count >= SCAN_BREAKER_LIMIT:
                            breaker_until = time.time() + SCAN_BACKOFF_SEC
                            logger.error("BCC-024",
                                f"[bcc] 探活/scan_login 连续失败 {scan_fail_count} 次，"
                                f"熔断 {SCAN_BACKOFF_SEC // 60} 分钟（防浏览器频繁重启"
                                f"引发风控）。session 疑似服务端已失效，自动登录救不回，"
                                f"请在指纹浏览器重新扫码；期间仅告警不重启浏览器")
            except Exception as e:
                logger.warning("BCC-027", f"[bcc] 探活异常: {e}")
        logger.info("[bcc] 保活心跳退出")


# ----------------------------------------------------------------------------
# FastAPI 路由
# ----------------------------------------------------------------------------
class UserInfoBody(BaseModel):
    sec_uids: list[str]


class ResolveBody(BaseModel):
    url: str


class ScanBody(BaseModel):
    force: bool = False
    timeout: int = 300


class WaitBody(BaseModel):
    wait: int = 15


class UidsBody(BaseModel):
    uids: list[str]


@app.on_event("startup")
async def _startup() -> None:
    logger.info(f"browser_daemon(BCC) 启动 account={_state['account']} port={_state['port']}")
    container = BrowserContainer(account=_state["account"])
    _state["container"] = container
    try:
        await container.start()
    except Exception as e:
        logger.error("BCC-028", f"[bcc] 浏览器容器启动失败（后续接口会自愈）: {e}")
    # 保活心跳（后台线程）
    stop_ev = threading.Event()
    _state["keepalive_stop"] = stop_ev
    t = threading.Thread(target=container.run_keepalive, args=(stop_ev,), daemon=True)
    _state["keepalive_thread"] = t
    t.start()

    # 2026-08-31：昵称缓存预热。
    # 实测：BCC 冷启动首次 capture_userinfo 要 **152~162 秒**
    #   （页面导航 + 首屏渲染 + 40 轮滚动触发全部 im/user/info），
    #   而缓存热之后只要 **23 秒**。
    # 更新会话的总耗时从 179s 里 BCC 独占 162s（90%），用户明确抱怨慢。
    # 这里在启动后**后台**跑一次预热（不阻塞 BCC 启动、不影响接口可用性），
    # 之后用户点「更新会话」时缓存已热，昵称捕获降到 20~30 秒。
    def _prewarm():
        import asyncio as _aio

        # 等浏览器与登录态稳定（保活线程已启动）
        threading.Event().wait(20)
        try:
            # BrowserContainer 在 start() 里存了自己的 loop（self._loop）
            loop = getattr(container, "_loop", None)
            if not loop:
                return
            fut = _aio.run_coroutine_threadsafe(
                container.capture_userinfo_map(wait=15), loop)
            data = fut.result(timeout=300)
            logger.info(f"[bcc] 昵称缓存预热完成：{len(data)} 个")
        except Exception as e:
            logger.warning("BCC-029", f"[bcc] 昵称缓存预热失败（不影响功能）: {e}")

    threading.Thread(target=_prewarm, daemon=True).start()


@app.on_event("shutdown")
async def _shutdown() -> None:
    if _state["keepalive_stop"]:
        _state["keepalive_stop"].set()
    c = _state.get("container")
    if c:
        try:
            if c._backend == "exe" and c._context is not None:
                await c._context.close()
            if c._pw is not None:
                await c._pw.stop()
        except Exception:
            pass


@app.get("/status")
async def status() -> dict:
    c = _state.get("container")
    if not c:
        return {"alive": False, "account": _state["account"]}
    return c.status()


@app.post("/cookie")
async def refresh_cookie() -> dict:
    """读实时 cookie 返回 + 写回 .env。"""
    c = _state.get("container")
    if not c:
        return {"ok": False, "msg": "容器未启动"}
    return await c.refresh_cookie_to_env()


@app.post("/user_info")
async def user_info(body: UserInfoBody) -> dict:
    """浏览器页面内 fetch 批量查 sec_user_ids → 昵称/头像。"""
    c = _state.get("container")
    if not c:
        return {"ok": False, "msg": "容器未启动", "data": {}}
    out = await c.bulk_user_info(body.sec_uids)
    return {"ok": True, "data": out}


@app.post("/capture_userinfo")
async def capture_userinfo(body: WaitBody) -> dict:
    """被动 hook 截前端自己发的 im/user/info 响应（零主动请求、零风控）。
    复用本容器常驻浏览器，不另开浏览器、不抢 profile。
    """
    c = _state.get("container")
    if not c:
        return {"ok": False, "msg": "容器未启动", "data": {}}
    try:
        out = await c.capture_userinfo_map(wait=body.wait or 15)
    except Exception as e:
        logger.warning("BCC-030", f"[bcc] /capture_userinfo 失败: {e}")
        return {"ok": False, "msg": str(e), "data": {}}
    return {"ok": True, "data": out}


@app.post("/user_info_by_uids")
async def user_info_by_uids(body: UidsBody) -> dict:
    """用数字 UID 主动 fetch im/user/info 批量查昵称/头像（比被动 hook 更可靠）。

    抖音 im/user/info 接口同时支持 user_ids 与 sec_user_ids 两种入参。
    会话列表只有数字 peer_uid（首包解析 100% 可靠），用 user_ids 直查
    避免依赖前端自发展示会话（被动 hook 会超时/缺口）。
    返回 {uid: {nickname, avatar}}。
    """
    c = _state.get("container")
    if not c:
        return {"ok": False, "msg": "容器未启动", "data": {}}
    try:
        out = await c.bulk_user_info_by_uid(body.uids)
    except Exception as e:
        logger.warning("BCC-031", f"[bcc] /user_info_by_uids 失败: {e}")
        return {"ok": False, "msg": str(e), "data": {}}
    return {"ok": True, "data": out}


@app.post("/resolve_url")
async def resolve_url(body: ResolveBody) -> dict:
    """浏览器打开链接 → 跟随跳转 → 抠 live_id。"""
    c = _state.get("container")
    if not c:
        return {"ok": False, "live_id": None, "msg": "容器未启动"}
    return await c.resolve_url(body.url)


class ExecJsBody(BaseModel):
    """页面内执行 JS（仅用于**只读**取数，如把图片导出为 base64）。"""

    # 必须是「单表达式」形式的 async 箭头函数字符串，
    # 形如 "async (arg) => { ... return x }"；Playwright 会把它编译成函数。
    js: str
    arg: object = None
    timeout: int = 30  # 秒


@app.post("/exec_js")
async def exec_js(body: ExecJsBody) -> dict:
    """在抖音页面上下文里执行 JS 并返回结果。

    2026-08-31 新增，用途：**取私信原图**。

    背景：私信图片的远程链（resource_url.*）实测为抖音私有加密格式，
    后端与普通 <img src> 都无法解码（显示破损图标）。但抖音**前端自己
    能解密渲染**（用户在网页上看得到图），所以在**页面上下文**里
    （同域 + 完整登录态 + 前端解密逻辑）fetch → canvas 导出 base64，
    是拿到原图的唯一可行路径。

    **风控边界（红线）**：本接口只是执行调用方传入的 JS，
    自身不发起任何请求。昵称/用户信息的批量查询仍然禁止 ——
    只允许用于**只读取数**（图片导出等），不得用于遍历用户信息。
    """
    c = _state.get("container")
    if not c:
        return {"ok": False, "msg": "容器未启动", "result": None}
    try:
        res = await c.exec_js(body.js, body.arg, timeout=body.timeout)
        return {"ok": True, "msg": "", "result": res}
    except Exception as e:
        logger.warning("BCC-032", f"[bcc] /exec_js 失败: {e}")
        return {"ok": False, "msg": str(e), "result": None}


class LinkmicRunBody(BaseModel):
    """连麦链路执行（2026-09-10 新增）。

    action:
      goto    —— 导航到直播间页（room_url 必填），并在页面稳定后返回
      apply   —— 在直播间页执行申请连麦 DOM 流程（js 由 backend api/linkmic.py 提供）
      status  —— 查询连麦状态（waiting_list + list/v2）
      mute    —— 闭麦（track.enabled=false，0 输入）
      leave   —— 退出连麦

    风控边界：与 /exec_js 一致，本接口只执行调用方传入的页面 JS；
    连麦申请/闭麦是用户主动单次操作（对应真人点按钮），非批量行为。
    """

    action: str
    js: str = ""
    room_url: str = ""
    timeout: int = 120


@app.post("/linkmic_run")
async def linkmic_run(body: LinkmicRunBody) -> dict:
    c = _state.get("container")
    if not c:
        return {"ok": False, "msg": "容器未启动", "result": None}

    async def _do():
        page = c._page
        # goto：先导航（exec_js 硬限制 /chat，连麦必须驻留直播间页 —— 知识库 05 §5.5）
        if body.action == "goto":
            await page.goto(body.room_url, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(20000)
            return {"url": page.url, "title": await page.title()}
        # 其他 action：若当前不在直播间页，先导航
        if "live.douyin.com" not in (page.url or ""):
            await page.goto(body.room_url or "https://live.douyin.com/",
                            wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(15000)
        if body.action in ("apply", "mute", "leave"):
            if not body.js:
                return {"ok": False, "error": f"action={body.action} 缺 js"}
            page.set_default_timeout(body.timeout * 1000)
            return await page.evaluate(body.js)
        if body.action == "status":
            page.set_default_timeout(30_000)
            return await page.evaluate(body.js)
        return {"ok": False, "error": f"未知 action: {body.action}"}

    try:
        async with c._lock:
            await c._ensure_alive()
            res = await _do()
        return {"ok": True, "msg": "", "result": res}
    except Exception as e:
        logger.warning("BCC-040", f"[bcc] /linkmic_run action={body.action} 失败: {e}")
        return {"ok": False, "msg": str(e), "result": None}


@app.post("/wp_messages")
async def wp_messages() -> dict:
    """拉取 BCC 被动 hook 截到的 WP 通道私信事件（读后清空）。

    2026-09-05 新增。事件来源：CAP_WP_MESSAGE_HOOK_JS 监听 chat 页的
    im 相关 HTTP 响应与 WebSocket 帧，raw 推入 window.__CAP_WP_MESSAGE__.events。
    后端 wp_recv 轮询本接口取回后统一解析。

    风控边界：纯被动读取已截获的事件，不主动发起任何请求。
    """
    c = _state.get("container")
    if not c:
        return {"ok": False, "msg": "容器未启动", "events": [], "count": 0}
    try:
        events = await c.capture_wp_messages()
        return {"ok": True, "msg": "", "events": events, "count": len(events)}
    except Exception as e:
        logger.warning("BCC-033", f"[bcc] /wp_messages 失败: {e}")
        return {"ok": False, "msg": str(e), "events": [], "count": 0}


class WpSendBody(BaseModel):
    account: str
    conv_id: str
    text: str


@app.post("/wp_send")
async def wp_send(body: WpSendBody) -> dict:
    """WP 通道发送文本私信（chat 页 DOM 流程）。

    2026-09-06 重写：wp_send_text 改用 DOM 流程（搜索→点开→编辑器→Enter），
    废弃探测式 IM SDK 调用（从未成功过）。
    2026-09-06 补账号一致性校验（§24.9 事故④a 同源）：请求的 account 必须
    与本容器账号一致，防止端口错乱时把消息发到别的账号会话里。
    """
    c = _state.get("container")
    if not c:
        return {"ok": False, "msg": "容器未启动", "result": None}
    if body.account and body.account != c.account:
        return {"ok": False,
                "msg": f"账号不匹配：请求 {body.account}，容器是 {c.account}",
                "result": None}
    try:
        res = await c.wp_send_text(body.conv_id, body.text)
        return {"ok": res.get("ok", False), "msg": res.get("error", ""), "result": res}
    except Exception as e:
        logger.warning("BCC-034", f"[bcc] /wp_send 失败: {e}")
        return {"ok": False, "msg": str(e), "result": None}


@app.post("/scan_login")
async def scan_login(body: ScanBody) -> dict:
    """扫码登录/刷新凭证。"""
    c = _state.get("container")
    if not c:
        return {"ok": False, "msg": "容器未启动"}
    return await c.scan_login(force=body.force, timeout=body.timeout)


@app.post("/refresh")
async def refresh(force: bool = False) -> dict:
    """兼容旧接口（= scan_login force=False）。"""
    c = _state.get("container")
    if not c:
        return {"ok": False, "msg": "容器未启动"}
    return await c.scan_login(force=force)


@app.post("/show")
async def show(req: Request, visible: bool = True, url: str = "") -> dict:
    """切换容器可见性（默认切到有头可见）。

    供前端「查看登录态」按钮使用：把常驻 BCC 容器**就地切为有头可见**，
    而不是另起一个浏览器抢同一 profile。用户看到的窗口就是 BCC 自己，
    保活/凭证回写链路不中断。

    visible=false 恢复纯无头（省资源、防风控暴露）。
    url 可选：切换后导航到指定页面（默认保持当前页）。
    """
    c = _state.get("container")
    if not c:
        return {"ok": False, "msg": "容器未启动（请先拉起 BCC）"}
    # 2026-09-12 修复：FastAPI 把 visible 当 **query 参数**（非 body），
    # 前端用 JSON body 传参时会被静默忽略 → 永远按默认 True 执行，
    # 「切回无头」失效（实测：POST body {"visible": false} 返回 headless=false）。
    # 这里显式读 body 覆盖（body 优先，兼容 query 调用）。
    try:
        raw = await req.body()
        if raw:
            _b = json.loads(raw.decode("utf-8", "replace"))
            if isinstance(_b, dict):
                if "visible" in _b:
                    visible = bool(_b["visible"])
                if _b.get("url"):
                    url = str(_b["url"])
    except Exception:
        pass
    return await c.set_visible(bool(visible), url or "")


@app.post("/quit")
async def quit_() -> dict:
    for ch in []:
        pass
    c = _state.get("container")
    if c and c._backend == "exe" and c._context is not None:
        try:
            await c._context.close()
        except Exception:
            pass
    threading.Timer(0.5, lambda: os._exit(0)).start()
    return {"ok": True}


# ----------------------------------------------------------------------------
# 主入口
# ----------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="浏览器容器守护进程（BCC）")
    parser.add_argument("--account", required=True, help="账号名")
    parser.add_argument("--port", type=int, required=True, help="HTTP 控制端口")
    parser.add_argument(
        "--allow-any-port", action="store_true",
        help="允许非哈希端口启动（仅调试用；正常启动一律走端口哈希校验）")
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