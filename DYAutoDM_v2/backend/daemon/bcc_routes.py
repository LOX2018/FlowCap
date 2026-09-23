# coding=utf-8
"""BCC FastAPI 路由（从 browser_daemon.py 抽出）

本文件存放 browser_daemon.py 中所有 FastAPI 路由、Pydantic 模型、
生命周期事件、中间件、异常处理器。

每个 handler 函数内部做 lazy import（避免循环导入 browser_daemon ↔ bcc_routes）。
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from daemon.bcc_lease import ContainerBusy

router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic 模型
# ---------------------------------------------------------------------------

class UserInfoBody(BaseModel):
    sec_uids: list[str]


class ResolveBody(BaseModel):
    url: str


class ScanBody(BaseModel):
    force: bool = False
    timeout: int = 300


class WaitBody(BaseModel):
    wait: int = 15
    # 2026-09-14 v0.43.11：跨调用窗口租约透传（调度器租约 §3.6「更新会话全程」）。
    # 调用方（conversation_capture）先从 gate 取得租约，把 lease_id 带进来，
    # _exec 识别为重入并复用 —— 否则同一个自己会被自己刚拿的租约挡在门外
    # （实测 BCC-047 → BCC-030 → 昵称 0 个）。
    lease_id: str = ""


class UidsBody(BaseModel):
    uids: list[str]


class LeaseBody(BaseModel):
    holder: str = ""
    purpose: str = "auto"
    prio: int = 2
    ttl: float = 0.0
    lease_id: str = ""


class CookieBody(BaseModel):
    """2026-09-14 v0.43.11：跨调用窗口租约透传（与 WaitBody 同源）。"""
    lease_id: str = ""


class ExecJsBody(BaseModel):
    """页面内执行 JS（仅用于**只读**取数，如把图片导出为 base64）。"""

    # 必须是「单表达式」形式的 async 箭头函数字符串，
    # 形如 "async (arg) => { ... return x }"；Playwright 会把它编译成函数。
    js: str
    arg: object = None
    timeout: int = 30  # 秒


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


class WpSendBody(BaseModel):
    account: str
    conv_id: str
    text: str


class EnvAuditBody(BaseModel):
    internal: bool = False


# ---------------------------------------------------------------------------
# 生命周期
# ---------------------------------------------------------------------------


# ════════════════════════════════════════════════════════════════════════════
# 🔴 2026-09-23【生命周期必须挂 app，不能挂 router】—— 防「startup 跑两次」
#
# 事故（本次实证，v0.44.53 阻断级）：本模块曾用 **APIRouter 的 on_event 装饰器**
# 定义 BCC 生命周期，而 `browser_daemon.py` 用 `include_router` 挂载它
# ⇒ **同一个 handler 被跑两遍**，导致：
#     · 两个 `BrowserContainer` 先后抢同一 profile（第二个撞 camofox 启动失败
#       BCC-058「Failed to launch the browser process」）
#     · `_state["container"]` 被**后建的坏容器**覆盖 → `/status` 恒 alive:false
#     · keepalive 线程 ×2 + 每轮重建 → 用户所见「Camoufox 正在运行、反复激活」
#
# 上游机理（FastAPI 0.141.1，`fastapi/routing.py`，已读源码坐实）：
#   `include_router()` 对传入 router 做**两件**与生命周期相关的事：
#     ① `for handler in router.on_startup: self.add_event_handler("startup", handler)`
#     ② `self.lifespan_context = _merge_lifespan_context(self.lifespan_context,
#                                                        router.lifespan_context)`
#   而 `APIRouter.__init__` 里 `lifespan_context = _DefaultLifespan(self)`，
#   其 `__aenter__` 正是 `await self._router._startup()` —— 于是 ① 注册的 handler
#   与 ② 合并进来的 router 默认 lifespan **各自跑一次** ⇒ `_startup()` ×2。
#   （重构前 handler 直接写 `@app.on_event(...)`，不经过 include_router，故只跑一次。）
#
# 定式：**路由可以 include，生命周期不行**。`on_startup/on_shutdown` 一律
# 用 `app.add_event_handler(...)` 挂在**最终应用对象**上（见 browser_daemon.py），
# 本模块只导出普通 async 函数 `startup()` / `shutdown()`。
#
# 回归守卫：`backend/test_bcc_startup_single_fire.py`
# （真跑一次 lifespan，断言 container.start() 恰好 1 次；注入双跑必须变红）
# ════════════════════════════════════════════════════════════════════════════
async def startup() -> None:
    from daemon.browser_daemon import _state, BrowserContainer
    from loguru import logger
    import threading

    logger.info(f"browser_daemon(BCC) 启动 account={_state['account']} port={_state['port']}")
    container = BrowserContainer(account=_state["account"])
    _state["container"] = container
    try:
        await container.start()
    except Exception as e:
        logger.error(f"[BCC-028] " + f"[bcc] 浏览器容器启动失败（后续接口会自愈）: {e}")
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
        container._prewarm_running = True
        try:
            # BrowserContainer 在 start() 里存了自己的 loop（self._loop）
            loop = getattr(container, "_loop", None)
            if not loop:
                return
            # 2026-09-14 v0.43.11：internal=True —— 预热是容器**自身**的后台
            # 线程，不参与租约仲裁（只走 _lock 串行）。原实现走 _exec 默认
            # 租约（prio=2），被业务租约（gate:auto ttl=180s）连续拒绝 →
            # BCC-047 刷屏、预热永远跑不完（实测 10:25:04~10:25:17 连续 4 次）。
            fut = _aio.run_coroutine_threadsafe(
                container.capture_userinfo_map(wait=15, internal=True), loop)
            data = fut.result(timeout=300)
            logger.info(f"[bcc] 昵称缓存预热完成：{len(data)} 个")
        except Exception as e:
            logger.warning(f"[BCC-029] " + f"[bcc] 昵称缓存预热失败（不影响功能）: {e}")
        finally:
            # ⚠️ 必须 finally：函数体内有 `return`（loop 缺失早退）与异常路径，
            # 用 try/except 而不带 finally 会让 _prewarm_running 永久卡 True，
            # 后续所有业务请求都会白等 40s（本会话实测踩到）。
            container._prewarm_running = False

    threading.Thread(target=_prewarm, daemon=True).start()


async def shutdown() -> None:
    from daemon.browser_daemon import _state

    if _state["keepalive_stop"]:
        _state["keepalive_stop"].set()
    c = _state.get("container")
    if c:
        try:
            if c._backend in ("exe", "camoufox") and c._context is not None:
                if c._backend == "camoufox":
                    from vbrowser_camoufox import close_camoufox_context
                    await close_camoufox_context(c._context)
                else:
                    await c._context.close()
            if c._pw is not None:
                await c._pw.stop()
        except Exception:
            pass




# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------


@router.get("/status")
async def status() -> dict:
    from daemon.browser_daemon import _state

    c = _state.get("container")
    if not c:
        return {"alive": False, "account": _state["account"]}
    return c.status()


@router.get("/lease_status")
async def lease_status() -> dict:
    """只读：当前租约状态（谁在用浏览器、还剩多久）。

    零副作用、零行为变化 —— 调度器（services.browser_gate）与前端据此判断
    能否立刻拿到浏览器，而不是像此前那样"猜"（gate 曾读一个从不存在的
    /status.exclusive 字段，导致独占分支恒为假成功）。
    """
    from daemon.bcc_lease import _lease_status

    return {"ok": True, "lease": _lease_status(),
            "prios": {"0": "用户显式(ttl<=300s)",
                      "1": "业务自动(ttl<=180s)",
                      "2": "后台保活(ttl<=30s)"}}


@router.post("/lease")
async def lease_acquire(body: LeaseBody) -> dict:
    """申请浏览器租约（调度器统一仲裁入口）。

    规则（见 docs/调度器租约设计细节.md §3）：
      · **不抢占**：已被他人持有则立即返回 busy + retry_after，绝不打断
        正在执行的浏览器操作（DOM 流程/context 重建不可中断）。
      · **同 lease_id 重入**复用现有租约（不自己和自己冲突）。
      · **ttl 超该优先级上限**按上限授予（P0=300s/P1=180s/P2=30s），
        防"低优先级长期霸占"把调度器架空。
      · **惰性 TTL**：到期未 release 会在下次读取时自动回收（BCC-046），
        持有者崩溃不会造成永久独占。
    """
    from daemon.bcc_lease import _lease_acquire, _lease_status, LEASE_PRIO_NAME

    r = _lease_acquire(body.holder or "anonymous", body.purpose,
                       body.prio, body.ttl, body.lease_id)
    if not r.get("ok"):
        return {"ok": False, "busy": r.get("busy"),
                "busy_prio": r.get("busy_prio"),
                "retry_after": r.get("retry_after"),
                "msg": (f"浏览器正被 {r.get('busy')}"
                        f"（{LEASE_PRIO_NAME.get(r.get('busy_prio'), '?')}）使用，"
                        f"约 {r.get('retry_after')}s 后可用")}
    return {"ok": True, "lease_id": r["lease_id"],
            "expires_at": r["expires_at"], "renew": r.get("renew", False),
            "lease": _lease_status()}


@router.post("/lease/renew")
async def lease_renew(body: LeaseBody) -> dict:
    """续租。累计时长不得超过该优先级上限（防续租绕过 TTL 上限）。"""
    from daemon.bcc_lease import _lease_renew

    r = _lease_renew(body.lease_id, body.ttl)
    return r


@router.post("/lease/release")
async def lease_release(body: LeaseBody) -> dict:
    """释放租约（必须 lease_id 匹配，防误释放他人租约）。"""
    from daemon.bcc_lease import _lease_release

    r = _lease_release(body.lease_id, body.holder)
    return r


@router.post("/cookie")
async def refresh_cookie(body: CookieBody | None = None) -> dict:
    """读实时 cookie 返回 + 写回 .env。"""
    from daemon.browser_daemon import _state

    c = _state.get("container")
    if not c:
        return {"ok": False, "msg": "容器未启动"}
    return await c.refresh_cookie_to_env(
        lease_id=(body.lease_id if body else ""))


@router.post("/user_info")
async def user_info(body: UserInfoBody) -> dict:
    """浏览器页面内 fetch 批量查 sec_user_ids → 昵称/头像。"""
    from daemon.browser_daemon import _state

    c = _state.get("container")
    if not c:
        return {"ok": False, "msg": "容器未启动", "data": {}}
    out = await c.bulk_user_info(body.sec_uids)
    return {"ok": True, "data": out}


@router.post("/capture_userinfo")
async def capture_userinfo(body: WaitBody) -> dict:
    """被动 hook 截前端自己发的 im/user/info 响应（零主动请求、零风控）。
    复用本容器常驻浏览器，不另开浏览器、不抢 profile。
    """
    from daemon.browser_daemon import _state
    from loguru import logger

    c = _state.get("container")
    if not c:
        return {"ok": False, "msg": "容器未启动", "data": {}}
    try:
        out = await c.capture_userinfo_map(wait=body.wait or 15,
                                           lease_id=body.lease_id or "")
    except Exception as e:
        logger.warning(f"[BCC-030] " + f"[bcc] /capture_userinfo 失败: {e}")
        return {"ok": False, "msg": str(e), "data": {}}
    return {"ok": True, "data": out}


@router.post("/userinfo_idb")
async def userinfo_idb(body: WaitBody | None = None) -> dict:
    """★ 读 IndexedDB `<uid>_user` 库拿全量用户昵称/头像（2026-09-15 实机落地）。

    为什么需要（桥接的正解，取代失败的「文本桥 / 位置对齐」）：
      DOM 会话项**无 uid**，而本库记录 `value.uid` 是**数字**，
      与首包 conv_id 推出的 `peer_uid` **同一体系** → 直接相等比对。
      实测：首包 peer_uid ∩ IDB uid = **44/44 = 100%**。

    风控：纯读页面自有 IndexedDB，**零网络请求**。

    用法：调用方先确保 chat 页已加载并**滚动点击完全部会话**
    （前端才会把用户信息写入 IDB），再调本端点。
    """
    from daemon.browser_daemon import _state, CAP_IDB_USERINFO_JS
    from loguru import logger

    c = _state.get("container")
    if not c:
        return {"ok": False, "msg": "容器未启动", "total": 0, "users": {}}
    # 租约取—传—释（2026-09-15）：本端点是**用户显式动作**触发的读取，
    # 用 PURPOSE_USER 取租约并把 lease_id 透传给 exec_js —— 否则会被
    # 调用方（capture_all）已持有的租约挡在门外（实测 BCC-056 自我死锁）。
    _lid = (getattr(body, "lease_id", "") or "") if body else ""
    _got = ""
    if not _lid:
        try:
            from services.browser_gate import ensure_browser, PURPOSE_USER
            g = ensure_browser(_state.get("account") or "", purpose=PURPOSE_USER,
                               holder="userinfo_idb", ttl=300.0)
            if g.get("ok"):
                _got = g.get("lease_id") or ""
                _lid = _got
        except Exception:  # noqa: BLE001
            pass
    try:
        # 2026-09-16 v0.43.39：传入本账号 uid（运行时取，绝不硬编码）——
        # JS 的 IndexedDB 兜底按 `<uid>_user` 约定定位库，必须用真实值。
        _myuid = ""
        try:
            from services import conv_identity as _cid
            _myuid = _cid.my_uid(_state.get("account") or "")
        except Exception:
            _myuid = ""
        try:
            r = await c.exec_js(CAP_IDB_USERINFO_JS, _myuid, timeout=60,
                                lease_id=_lid, holder="userinfo_idb")
        except TypeError:
            # 兼容：exec_js 未升级为支持 lease_id 时退回原调用
            r = await c.exec_js(CAP_IDB_USERINFO_JS, _myuid, timeout=60)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[BCC-056] " + f"[bcc] /userinfo_idb 失败: {e}")
        return {"ok": False, "msg": str(e), "total": 0, "users": {}}
    finally:
        if _got:
            try:
                from services.browser_gate import release_lease
                release_lease(_state.get("account") or "", _got, holder="userinfo_idb")
            except Exception:  # noqa: BLE001
                pass
    r = r or {}
    n = int(r.get("total") or 0)
    logger.info(f"[bcc] IndexedDB 用户信息：{n} 条（库 {r.get('db')}）")
    return {"ok": True, "total": n, "db": r.get("db"), "users": r.get("users") or {}}


@router.post("/user_info_by_uids")
async def user_info_by_uids(body: UidsBody) -> dict:
    """用数字 UID 主动 fetch im/user/info 批量查昵称/头像（比被动 hook 更可靠）。

    抖音 im/user/info 接口同时支持 user_ids 与 sec_user_ids 两种入参。
    会话列表只有数字 peer_uid（首包解析 100% 可靠），用 user_ids 直查
    避免依赖前端自发展示会话（被动 hook 会超时/缺口）。
    返回 {uid: {nickname, avatar}}。
    """
    from daemon.browser_daemon import _state
    from loguru import logger

    c = _state.get("container")
    if not c:
        return {"ok": False, "msg": "容器未启动", "data": {}}
    try:
        out = await c.bulk_user_info_by_uid(body.uids)
    except Exception as e:
        logger.warning(f"[BCC-031] " + f"[bcc] /user_info_by_uids 失败: {e}")
        return {"ok": False, "msg": str(e), "data": {}}
    return {"ok": True, "data": out}


@router.post("/resolve_url")
async def resolve_url(body: ResolveBody) -> dict:
    """浏览器打开链接 → 跟随跳转 → 抠 live_id。"""
    from daemon.browser_daemon import _state

    c = _state.get("container")
    if not c:
        return {"ok": False, "live_id": None, "msg": "容器未启动"}
    return await c.resolve_url(body.url)


@router.post("/exec_js")
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
    from daemon.browser_daemon import _state
    from loguru import logger

    c = _state.get("container")
    if not c:
        return {"ok": False, "msg": "容器未启动", "result": None}
    try:
        res = await c.exec_js(body.js, body.arg, timeout=body.timeout)
        return {"ok": True, "msg": "", "result": res}
    except Exception as e:
        logger.warning(f"[BCC-032] " + f"[bcc] /exec_js 失败: {e}")
        return {"ok": False, "msg": str(e), "result": None}


@router.post("/linkmic_run")
async def linkmic_run(body: LinkmicRunBody) -> dict:
    from daemon.browser_daemon import _state
    from daemon.bcc_lease import ContainerBusy as _ContainerBusy
    from loguru import logger

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
                raise ValueError(f"action={body.action} 缺 js")
            page.set_default_timeout(body.timeout * 1000)
            return await page.evaluate(body.js)
        if body.action == "status":
            page.set_default_timeout(30_000)
            return await page.evaluate(body.js)
        raise ValueError(f"未知 action: {body.action}")

    try:
        # 2026-09-17 修补（OCR 审查 HIGH）：原实现直接 `async with c._lock:` +
        # `c._ensure_alive()`，**完全绕过租约仲裁与 _is_busy() 快速失败** ——
        # 后果：①scan_login 独占窗口内（context 已关）本端点仍会拿锁并调
        # _ensure_alive，正是本文件注释所述「误判死活→与后台 _launch 抢
        # profile→死循环」的配方；②不写 _scan_exclusive，与 BCC 自家调度
        # 体系不一致，其它端点看不到它的独占。
        # 现改走统一入口 _exec（自动纳入租约 + 串行 + 自愈）。
        # 注意保持返回结构不变：_exec 返回的是 _do() 的裸结果，故此处再包装。
        res = await c._exec(_do, holder="linkmic", prio=1, ttl=180.0)
        return {"ok": True, "msg": "", "result": res}
    except _ContainerBusy as e:
        return {"ok": False, "msg": f"浏览器忙（{e}），请稍后重试",
                "error": f"browser_busy: {e}", "result": None}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[BCC-040] [bcc] /linkmic_run action={body.action} 失败: {e}")
        # 2026-09-17：同时给 msg 与 error 两个字段，兼容既有外部调用方
        # （原实现在「缺 js」「未知 action」时只返回 error 字段）。
        return {"ok": False, "msg": str(e), "error": str(e), "result": None}


@router.post("/wp_messages")
async def wp_messages() -> dict:
    """拉取 BCC 被动 hook 截到的 WP 通道私信事件（读后清空）。

    2026-09-05 新增。事件来源：CAP_WP_MESSAGE_HOOK_JS 监听 chat 页的
    im 相关 HTTP 响应与 WebSocket 帧，raw 推入 window.__CAP_WP_MESSAGE__.events。
    后端 wp_recv 轮询本接口取回后统一解析。

    风控边界：纯被动读取已截获的事件，不主动发起任何请求。
    """
    from daemon.browser_daemon import _state
    from loguru import logger

    c = _state.get("container")
    if not c:
        return {"ok": False, "msg": "容器未启动", "events": [], "count": 0}
    try:
        events = await c.capture_wp_messages()
        return {"ok": True, "msg": "", "events": events, "count": len(events)}
    except Exception as e:
        logger.warning(f"[BCC-033] " + f"[bcc] /wp_messages 失败: {e}")
        return {"ok": False, "msg": str(e), "events": [], "count": 0}


@router.post("/wp_send")
async def wp_send(body: WpSendBody) -> dict:
    """WP 通道发送文本私信（chat 页 DOM 流程）。

    2026-09-06 重写：wp_send_text 改用 DOM 流程（搜索→点开→编辑器→Enter），
    废弃探测式 IM SDK 调用（从未成功过）。
    2026-09-06 补账号一致性校验（§24.9 事故④a 同源）：请求的 account 必须
    与本容器账号一致，防止端口错乱时把消息发到别的账号会话里。
    """
    from daemon.browser_daemon import _state
    from loguru import logger

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
        logger.warning(f"[BCC-034] " + f"[bcc] /wp_send 失败: {e}")
        return {"ok": False, "msg": str(e), "result": None}


@router.post("/env_audit")
async def env_audit(body: EnvAuditBody | None = None) -> dict:
    """实时环境泄漏监测（v0.43.88，移植 rebrowser/CreepJS/liarjs 检测逻辑）。

    纯本地只读 JS 探针，零外网请求。供前端/后端随时调取即时快照；
    keepalive 另按低频周期自动跑并告警。返回 {ok, fatal_count,
    warn_count, leaks:[...], js_view}。
    """
    from daemon.browser_daemon import _state
    from daemon.bcc_lease import ContainerBusy as _ContainerBusy
    from loguru import logger

    c = _state.get("container")
    if not c:
        return {"ok": False, "msg": "容器未启动", "leaks": [], "js_view": {}}
    try:
        out = await c.env_audit_snapshot(
            internal=bool(body.internal) if body else False)
        return {"ok": True, **out}
    except _ContainerBusy as e:
        return {"ok": False, "busy": str(e), "leaks": [], "js_view": {}}
    except Exception as e:
        logger.warning(f"[BCC-064] " + f"[bcc] /env_audit 失败: {e}")
        return {"ok": False, "msg": str(e), "leaks": [], "js_view": {}}


@router.post("/scan_login")
async def scan_login(body: ScanBody) -> dict:
    """扫码登录/刷新凭证。"""
    from daemon.browser_daemon import _state

    c = _state.get("container")
    if not c:
        return {"ok": False, "msg": "容器未启动"}
    return await c.scan_login(force=body.force, timeout=body.timeout)


@router.post("/refresh")
async def refresh(force: bool = False) -> dict:
    """兼容旧接口（= scan_login force=False）。"""
    from daemon.browser_daemon import _state

    c = _state.get("container")
    if not c:
        return {"ok": False, "msg": "容器未启动"}
    return await c.scan_login(force=force)


@router.post("/show")
async def show(req: Request, visible: bool = True, url: str = "") -> dict:
    """切换容器可见性（默认切到有头可见）。

    供前端「查看登录态」按钮使用：把常驻 BCC 容器**就地切为有头可见**，
    而不是另起一个浏览器抢同一 profile。用户看到的窗口就是 BCC 自己，
    保活/凭证回写链路不中断。

    visible=false 恢复纯无头（省资源、防风控暴露）。
    url 可选：切换后导航到指定页面（默认保持当前页）。
    """
    import json

    from daemon.browser_daemon import _state
    from loguru import logger

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
    except Exception as e:  # noqa: BLE001
        # 2026-09-17 修补（OCR 审查 HIGH）：原为 `except Exception: pass` ——
        # 与上方注释描述的 bug **完全同类**：body 畸形/被中间件消费时，
        # visible=false 被静默忽略，set_visible(True) 照跑，「切回无头」失效
        # 且**无任何日志**（排查时完全看不到线索）。现至少记录告警。
        logger.warning(f"[BCC-061] [show] 解析 body 失败，将按 query 参数"
                       f"（visible={visible}）处理: {type(e).__name__}: {e}")
    return await c.set_visible(bool(visible), url or "")


@router.post("/quit")
async def quit_() -> dict:
    import os
    import threading

    from daemon.browser_daemon import _state

    c = _state.get("container")
    if c and c._backend in ("exe", "camoufox") and c._context is not None:
        try:
            if c._backend == "camoufox":
                from vbrowser_camoufox import close_camoufox_context
                await close_camoufox_context(c._context)
            else:
                await c._context.close()
        except Exception:
            pass
    threading.Timer(0.5, lambda: os._exit(0)).start()
    return {"ok": True}