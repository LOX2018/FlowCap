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

logger.remove()
logger.add(
    sys.stderr,
    level="INFO",
    colorize=False,
    enqueue=True,
    format="{time:HH:mm:ss} | {level: <8} | {message}",
)

app = FastAPI(title="browser-container")

# 全局状态
_state: dict[str, Any] = {
    "account": "",
    "port": 0,
    "started_at": time.time(),
    "container": None,  # BrowserContainer 实例
    "keepalive_thread": None,
    "keepalive_stop": None,
}


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
#   - HTTP: /aweme/v1/web/im/notice/get_message_by_init（全量会话 + 最近消息）
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
  // ---- fetch hook ----
  const origFetch = window.fetch.bind(window);
  window.fetch = function(u, o) {
    const p = origFetch(u, o);
    try {
      const url = (typeof u === 'string') ? u : (u && u.url) || '';
      if (/im\/(notice\/)?get_message_by_init|web\/im\/conversation|im\/message\/send/.test(url)) {
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
        if (x.__u && /im\/(notice\/)?get_message_by_init|web\/im\/conversation|im\/message\/send/.test(x.__u)) {
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
            const d = typeof e.data === 'string' ? e.data : '<binary>';
            if (/im|message|conversation/i.test(d)) push('ws', String(u), d.slice(0, 400000));
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



class BrowserContainer:
    """常驻持有该账号 profile 的唯一 Playwright context。

    所有浏览器操作通过 submit(coro) 入队，内部 asyncio.Lock 串行执行，杜绝并发抢锁。
    context/page 失活时 _ensure_alive 自愈重启。
    """

    def __init__(self, account: str) -> None:
        self.account = account
        self._lock = asyncio.Lock()
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None
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
        if not self._profile_dir or not os.path.isdir(self._profile_dir):
            raise RuntimeError(f"[bcc] profile 目录不存在: {self._profile_dir}")
        _vb, _vb_mode = should_use_vb(_cfg)
        # 常驻浏览器容器默认无头：捕获链路（capture_userinfo_map 被动 hook 截前端自发
        # im/user/info）经实机验证（有头/无头均 44/44）无头完全可行，且零窗口更稳。
        # 扫码登录走独立 get_login_auth(headless=False)，需可见 UI，不在此处。
        self._pw, self._browser, self._context, self._backend = await launch_async(
            _vb_mode, _cfg, headless=True, user_data_dir=self._profile_dir, force=False)
        self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()
        # V16 踩坑：add_init_script 必须在 goto 前注入，否则前端已发完 im/user/info 再注入就截不到
        await self._context.add_init_script(CAP_USERINFO_HOOK_JS)
        await self._context.add_init_script(CAP_WP_MESSAGE_HOOK_JS)  # 2026-09-05 WP
        # 直接打开 chat 页（前端才会自发调 im/user/info）
        try:
            await self._page.goto("https://www.douyin.com/chat?isPopup=1", wait_until="domcontentloaded", timeout=20000)
        except Exception as e:
            logger.warning(f"[bcc] 打开 chat 页失败（不阻塞，后续接口自愈）: {e}")

    async def _ensure_alive(self) -> None:
        """context/page 失活时重启。在 _lock 内调用。"""
        try:
            if self._context is None or not self._context.pages:
                raise RuntimeError("context 已关闭")
            # 探测 page 是否能 evaluate
            if self._page is None or self._page.is_closed():
                self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()
            await self._page.evaluate("1")
        except Exception as e:
            logger.warning(f"[bcc] context/page 失活，重启: {e}")
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
            await self._launch()

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
        """在 _lock 内执行浏览器操作（自愈 + 串行）。coro_factory 是无参 callable 返回 coroutine。"""
        async with self._lock:
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
                    logger.warning(f"[bcc] 批量查昵称 evaluate 失败: {e}")
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
                    logger.warning(f"[bcc] 批量查昵称(uid) evaluate 失败: {e}")
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

    async def wp_send_text(self, conv_id: str, text: str, timeout: int = 30) -> dict:
        """在 chat 页上下文里发文本私信（WP 通道发送）。

        2026-09-05 新增。与 WS 通道（DouyinAPI.send_msg）并存。

        实现策略 —— **探测优先**：
        抖音 chat 页把 IM SDK 挂在某个全局对象上，但具体名字/方法签名
        未文档化且会随前端发版变化。这里按候选顺序尝试调用，
        全部失败时返回页面里所有疑似 IM 全局对象的清单，
        供人工在 DevTools 里确认真实调用方式（见计划 Task 6.5）。

        风控说明：发送是用户主动触发的单次操作，不是批量行为，
        且复用页面已有登录态，不额外登录、不遍历用户信息。
        
        返回 {"ok": bool, "via": str, "result": ...} / {"ok": False, "candidates": [...]}
        """
        import json as _json

        js = r"""
        async (args) => {
          const convId = args.conv_id;
          const text = args.text;
          const found = [];
          // 先扫描全局对象，收集疑似 IM SDK（无论调用成功与否都返回，便于排查）
          for (const k of Object.keys(window)) {
            if (/im|chat|message|conversation/i.test(k)) {
              try {
                const v = window[k];
                if (v && typeof v === 'object') {
                  const methods = Object.keys(v).filter(m => typeof v[m] === 'function');
                  const sendish = methods.filter(m => /send|create|post/i.test(m));
                  if (sendish.length) found.push({ obj: k, sendMethods: sendish.slice(0, 20) });
                }
              } catch(e) {}
            }
          }
          // 候选调用：常见命名 + 常见签名，逐个尝试
          const candidates = [
            ['webImService', (s) => s.sendText({conversation_id: convId, text: text})],
            ['webImService', (s) => s.sendText(convId, text)],
            ['DY_IM',        (s) => s.sendText({conversation_id: convId, text: text})],
            ['__IM_SDK__',   (s) => s.sendText(convId, text)],
            ['imSdk',        (s) => s.sendText({conversation_id: convId, text: text})],
          ];
          for (const [name, fn] of candidates) {
            const sdk = window[name];
            if (!sdk) continue;
            try {
              const r = await fn(sdk);
              if (r) return { ok: true, via: name, result: r, candidates: found };
            } catch(e) {
              return { ok: false, via: name, error: String(e), candidates: found };
            }
          }
          return {
            ok: false,
            error: '未找到可用的 IM SDK 发送方法（需 Task 6.5 实测确认）',
            candidates: found
          };
        }
        """
        try:
            res = await self.exec_js(js, arg={"conv_id": conv_id, "text": text},
                                     timeout=timeout)
            return res if isinstance(res, dict) else {"ok": False, "result": res}
        except Exception as e:
            logger.warning(f"[bcc] wp_send_text 失败: {e}")
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
                logger.warning(f"[bcc] 读取 hook 结果失败: {e}")
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
                logger.warning(f"[bcc] 读 wp message 失败: {e}")
                return []
            evs = evs or []
            if evs:
                logger.info(f"[bcc] 取回 WP 私信事件 {len(evs)} 条")
            return evs
        return await self._exec(_do)


    async def resolve_url(self, url: str) -> dict:
        """浏览器打开链接 → 跟随跳转 → 抠 live_id（替代 link_resolve._browser_resolve）。

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
            await self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
            live_id = None
            final_url = None
            for _ in range(20):
                await self._page.wait_for_timeout(1000)
                u = self._page.url
                if "live.douyin.com" in u and "/user/" not in u:
                    lid = _extract(u)
                    if lid:
                        live_id = lid
                        final_url = u
                        break
            if not live_id:
                u = self._page.url
                # 用户主页里找直播间入口
                try:
                    links = await self._page.eval_on_selector_all(
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
            api = DYLoginApi()
            auth = await api.get_login_auth(
                headless=False, env_path=env_path, force=force,
                landing_url="https://www.douyin.com/chat?isPopup=1")
            ok = bool(auth and getattr(auth, "cookie", None))
            # 重启容器 context
            await self._launch()
            return {"ok": ok, "uid": getattr(auth, "uid", None) if auth else None}
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
                return DouyinAPI.get_my_uid(auth)
        except Exception:
            pass
        return None

    async def refresh_cookie_to_env(self) -> dict:
        """读实时 cookie，写回 .env。返回 {ok, cookie_count, sessionid?}。"""
        from auto_dm import accounts as _acc
        from dy_apis.login_api import DYLoginApi
        env_path = _acc.env_path_of(self.account)
        if not env_path:
            return {"ok": False, "msg": "账号 .env 未登记"}
        # 先加载 auth（拿签名四件套），再用实时 cookie 覆盖 cookie 字段
        auth = DYLoginApi._load_auth_from_env(env_path)
        cks = await self.get_cookies()
        if not (cks.get("sessionid") or cks.get("sid_tt")):
            return {"ok": False, "msg": "profile 内无登录态"}
        auth.cookie = cks
        auth.cookie_str = "; ".join(f"{k}={v}" for k, v in cks.items())
        try:
            DYLoginApi().save_credential(auth, env_path)
        except Exception as e:
            logger.warning(f"[bcc] 写回 .env 失败: {e}")
        self._last_refresh = time.time()
        return {"ok": True, "cookie_count": len(cks),
                "sessionid": cks.get("sessionid", "")[:12],
                "cookies": "; ".join(f"{k}={v}" for k, v in cks.items()),
                "cookie_dict": cks}

    def run_keepalive(self, stop_ev: threading.Event, interval: int = 300) -> None:
        """每 interval 秒探活一次；uid 探活失败时调 scan_login 刷新。"""
        logger.info(f"[bcc] 保活心跳启动，间隔 {interval}s")
        while not stop_ev.is_set():
            if stop_ev.wait(interval):
                break
            try:
                uid = self._load_uid_from_env()
                if uid:
                    self._last_uid = uid
                    logger.debug(f"[bcc] 登录态正常(uid={uid})")
                else:
                    logger.warning("[bcc] 登录态失效，自动刷新凭证…")
                    # 在子线程调 async scan_login：投递到主 loop
                    if self._loop:
                        fut = asyncio.run_coroutine_threadsafe(
                            self.scan_login(force=False), self._loop)
                        try:
                            fut.result(timeout=120)
                        except Exception as e:
                            logger.warning(f"[bcc] 自动刷新凭证失败: {e}")
            except Exception as e:
                logger.warning(f"[bcc] 探活异常: {e}")
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
        logger.error(f"[bcc] 浏览器容器启动失败（后续接口会自愈）: {e}")
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
            logger.warning(f"[bcc] 昵称缓存预热失败（不影响功能）: {e}")

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
        logger.warning(f"[bcc] /capture_userinfo 失败: {e}")
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
        logger.warning(f"[bcc] /user_info_by_uids 失败: {e}")
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
        logger.warning(f"[bcc] /exec_js 失败: {e}")
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
        logger.warning(f"[bcc] /wp_messages 失败: {e}")
        return {"ok": False, "msg": str(e), "events": [], "count": 0}


class WpSendBody(BaseModel):
    account: str
    conv_id: str
    text: str


@app.post("/wp_send")
async def wp_send(body: WpSendBody) -> dict:
    """WP 通道发送文本私信（调 chat 页 IM SDK）。

    2026-09-05 新增。与 WS 通道（recv_daemon /send）并存，
    由后端 api/messages.py 的 channel 字段路由过来。
    """
    c = _state.get("container")
    if not c:
        return {"ok": False, "msg": "容器未启动", "result": None}
    try:
        res = await c.wp_send_text(body.conv_id, body.text)
        return {"ok": res.get("ok", False), "msg": res.get("error", ""), "result": res}
    except Exception as e:
        logger.warning(f"[bcc] /wp_send 失败: {e}")
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
    args = parser.parse_args()

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