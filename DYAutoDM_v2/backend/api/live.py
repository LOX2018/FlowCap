"""直播监听路由

取代原版 WebBridge.getLiveStream / sendDanmaku / doLike 等。
关键改进：用 WebSocket 推送实时弹幕，替代 2s 轮询。
"""
from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect, HTTPException
from pydantic import BaseModel
from loguru import logger
from models.live import LiveStreamResponse, LiveMessage, DanmakuRequest, DmTemplateRequest, RankUser
from database import get_kv_json, set_kv_json
from config import settings

router = APIRouter()


class ResolveRequest(BaseModel):
    url: str


@router.get("/stream")
async def get_stream(request: Request) -> LiveStreamResponse:
    """直播流快照（用于初次加载）。

    从引擎持有 AutoDM.live (LiveChatHook) 读取实时直播流数据：
    room_status（room_title/主播昵称/room_id）、feed（实时弹幕流）、heat_curve、
    online_count / likes、当前监听状态。引擎未运行或无 live 时返回 alive=False。

    V2 任务容器：附加 engineState/statusMsg/dmRunning/dmPaused，前端切页回读真实引擎状态。
    """
    adm = getattr(request.app.state, "adm", None)
    live = getattr(adm, "live", None) if adm else None
    engine_state = getattr(adm, "state", None)
    engine_value = engine_state.value if engine_state else "idle"
    status_msg = getattr(adm, "status_msg", "") or ""
    if live is None:
        return LiveStreamResponse(
            alive=False,
            engineState=engine_value,
            statusMsg=status_msg,
            dmRunning=getattr(adm, "is_running", False),
            dmPaused=engine_value == "paused",
            rankReason="未启动监听",
        )

    room = getattr(live, "room_status", None) or {}
    room_info = room.get("room_info") if isinstance(room, dict) else {}
    room_stats = getattr(live, "room_stats", None) or {}
    # 直播间标题：AutoDM 启动时已把 check_room_live 返回的 title 存到 self.room_title
    room_title = getattr(adm, "room_title", "") or str(room_info.get("title") or "")

    # 运行态判断：与 AutoDM._listen_line_active 共用同一判据（ENG-015）。
    # 原判据依赖 `live.ws`，而 ws 在 WS 握手窗口内恒为 None → 监听已开始
    # 但尚未连上的 1~2s 内被判成「未运行」，前端徽章显示「未连接」。
    running = adm is not None and adm._listen_line_active()

    # 弹幕流：feed_snapshot 返回 [{type,nickname,content,ts,epoch},...]，映射成 LiveMessage
    feed_items = live.feed_snapshot(limit=50) if hasattr(live, "feed_snapshot") else []
    messages = [
        LiveMessage(
            uid=str(x.get("uid", "") or ""),
            nickname=str(x.get("nickname", "") or ""),
            content=str(x.get("content", "") or ""),
            ts=int(x.get("epoch", 0) or 0),
        )
        for x in feed_items
    ]

    # 热度曲线：heat_snapshot 返回 [[epoch, online, likes],...]，取 online 作为热度点
    heat = live.heat_snapshot() if hasattr(live, "heat_snapshot") else []
    heat_curve = [int(h[1]) for h in heat if isinstance(h, (list, tuple)) and len(h) > 1]

    # 贡献榜（2026-09-30）：由 LiveChatHook 后台轮询上游榜单写入。
    # 空态给**如实**说明：running 的引擎若榜单拉取失败（缺登录态/无 room_info/上游
    # 结构变动），front 显示原因，绝不假装「无贡献者」。
    _raw_rank = getattr(live, "contribution_rank", None) or []
    _rank = [RankUser(**r) for r in _raw_rank if isinstance(r, dict)]
    _rank_reason = getattr(live, "_rank_reason", "") or ""
    if not _rank and not _rank_reason:
        _rank_reason = "running" if running else "未启动"
    elif not _rank and _rank_reason == "idle":
        _rank_reason = "拉取中"

    return LiveStreamResponse(
        alive=running,
        room_id=getattr(adm, "live_id", None) or str(room_info.get("room_id") or ""),
        online_count=int(room_stats.get("online", 0) or 0),
        messages=messages,
        heat_curve=heat_curve,
        likes=int(room_stats.get("likes", 0) or 0),
        listening=running,
        roomTitle=room_title,
        liveUrl=f"https://live.douyin.com/{getattr(adm, 'live_id', '') or ''}",
        engineState=engine_value,
        statusMsg=status_msg,
        dmRunning=getattr(adm, "is_running", False),
        dmPaused=engine_value == "paused",
        contribution_rank=_rank,
        rankReason=_rank_reason,
    )


@router.websocket("/ws")
async def live_ws(ws: WebSocket):
    """WebSocket 推送实时弹幕（替代前端 2s 轮询）

    前端连接后，后端有新弹幕就推送：
    {type: 'message', data: LiveMessage}
    {type: 'heat', data: int}
    """
    await ws.accept()
    try:
        while True:
            # TODO: 从 LiveChatHook 订阅消息推送
            msg = await ws.receive_text()
            # 处理前端发来的指令（如发弹幕）
    except WebSocketDisconnect:
        pass


class DanmakuSendBody(DanmakuRequest):
    """发送弹幕请求体（向后兼容扩展）。

    ## 为什么不在 models/live.py 里改（2026-09-28 N4）

    `DanmakuRequest` 原本只有 `content` —— 真实发送必需 `account`（取凭证）
    与 `room_id`（定位直播间），二者只有在这里补齐才能让端点真正接上
    `dy_apis` 写接口。本文件以**继承扩展**方式补字段：

      · 旧调用方（只发 `{"content": "..."}`）仍能通过校验 ⇒ 向后兼容；
      · `account` 缺省时**不得**静默成功 —— 由端点按 fail-closed 明确报
        `ok=False`（见 `send_danmaku`），这是本条修复的核心。
    """

    account: str | None = None
    room_id: str | None = None


class LikeSendBody(BaseModel):
    """直播间点赞请求体（2026-09-30 新增）。

    与 `DanmakuSendBody` 同契约：缺 `account` 时回落「当前账号」，
    仍取不到即 fail-closed（`reason=no_account`）——绝不静默沿用空账号。
    """

    count: int = 1
    account: str | None = None
    room_id: str | None = None


def _auth_for(account: str):
    """加载指定账号凭证 → dy_auth（与直播/采集/平台链路**同一加载器**）。

    ## 🔴 2026-10-01 修复（写接口「异常」的真根因）

    原实现调 `utils.common_util.load_env(env_path)`，它是**弱载入器**，与项目标准
    载入器 `DYLoginApi._load_auth_from_env` 有两处致命差异（实测）：

    | 项 | `common_util.load_env` | `DYLoginApi._load_auth_from_env` |
    |---|---|---|
    | 签名还原 | `perepare_auth(cookies, "", "")` ← **传空 web_protect/keys** | `perepare_auth(cookies, web_protect, keys)` |
    | 私钥形态 | `_src['DY_PRIVATE_KEY']` **原样** | `_decode_private_key(...)` **还原换行** |

    后果（用户实测日志）：
    ```
    [LIVE-002] [danmaku] 发送异常 …: Empty string does not encode a sequence
    [LIVE-042] [like]    发送异常 …: Empty string does not encode a sequence
    ```
    `DY_PRIVATE_KEY` 在存储态是**字面量 `\\n`**（`_encode_private_key` 产物），未还原即
    丢给 `SigningKey.from_pem` ⇒ PEM 解析失败。**只有写接口**会走到解析私钥这一步
    （`with_bd` → `generate_bd_ticket_client_data`），只读接口用 `with_bd_readonly` 不碰私钥
    ⇒ 这正是「只读能用、写就异常」的那一环。

    ## 同族缺陷（本项目已修过同一处，这里是**残留**）

    `api/platform.py:54-70` 于 2026-09-14 实测发现「`load_env` 返回的 `.cookie` 恒为空
    ⇒ 所有 platform 请求不带凭证」并**已改**用 `DYLoginApi._load_auth_from_env`。
    本文件与 `api/linkmic.py:35` 是**同族未收敛**的两处 ⇒ 本次一并收敛（SSOT）。
    """
    try:
        from auto_dm import accounts as acct_core
        env_path = acct_core.env_path_of(account)
        if not env_path:
            raise HTTPException(404, f"账号 {account} 未登记")
        # 唯一真源：与探活/直播/平台链路同一加载器（完整还原签名与私钥换行）
        from dy_apis.login_api import DYLoginApi
        return DYLoginApi._load_auth_from_env(env_path)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(503, f"加载账号 {account} 凭证失败: {type(e).__name__}: {e}")


def _room_id_for(room_id: str | None):
    """取「直播域写接口可用的房间号」——**权威优先**。

    ## 两套标识（2026-09-30 厘清，这是本域最容易踩的概念坑）

    | 标识 | 示例 | **谁必须用它** |
    |---|---|---|
    | `web_rid`（URL 短号） | `992931212705` | **URL / referer**（`https://live.douyin.com/<web_rid>` 才是可访问的直播间页） |
    | `room_id`（真实房间号） | `7688251038101556006` | **直播域 API**（`/webcast/room/{chat,like}/` 等一律只认它） |

    二者**都是真实需要**，不存在哪个「已经不需要」——所以是**两个字段**，不是二选一。
    此前链路把它们混用（`config.live_id` 一处被写 web_rid、一处被写真实 room_id ⇒ 语义污染），
    本函数按其**权威来源**取数：

    ① `config.live_room_id`（**权威**：真实 room_id，`link_resolve` / 监听启动时落库）；
    ② `config.live_id`（**兼容旧数据**；既可能存 web_rid 也可能存真实 room_id）；
    ③ 调用方显式传入的 `room_id`。

    取到的值**不保证**已是真实 room_id（②③ 可能是 web_rid）⇒ 仍由
    `DouyinAPI._live_chat_room_id()` 做最终归一化/校验（那里是唯一出口）。
    本函数只解决「从哪个字段取」，不重复实现归一化（SSOT）。
    """
    if room_id and str(room_id).strip():
        return str(room_id).strip()
    cfg = get_kv_json("config", {}) or {}
    rid = str(cfg.get("live_room_id") or cfg.get("live_id") or "").strip()
    if not rid:
        raise HTTPException(400, "缺少房间号（请先解析直播间）")
    return rid


@router.post("/danmaku")
async def send_danmaku(body: DanmakuSendBody):
    """发送直播间弹幕 —— **真实调用 dy_apis**，不再是恒 ok:true 的空壳。

    ## 修复说明（2026-09-28 N4 / 审计项 A-1）

    原实现 `# TODO: 迁移 sendDanmaku 逻辑` + `return {"ok": True, "content": ...}`
    —— 弹幕**根本没发出去**，前端 `live-page.tsx` 却弹出「弹幕已发送 · xxx」，
    属用户可见的**假成功端点**。基座 `dy_apis.client_live.LiveMixin.sendMsgInRoom`
    已于 v0.44.54 对齐上游 `251075e` 且项目内暂无生产调用方，故此处按
    `api/linkmic.py` 的写接口范式**接真**（而非删除端点）。

    ⚠️ 红线：本函数只在凭证与房间号齐备时才发出真实请求；任一缺失即
    fail-closed 返回 `ok=False`，**绝不**在无依据时空报成功。
    """
    content = (body.content or "").strip()
    if not content:
        return {"ok": False, "error": "弹幕内容为空", "reason": "empty_content", "sent": False}

    # 🔴 2026-09-30：**显式配置门**（用户「显式配置原则」+「新能力默认休眠」）。
    # `/webcast/room/chat/` 是**写接口，触风控红线**；此前只要凭证齐备就无条件外发。
    # 现改为：不显式打开 `live.danmaku_enabled` 就**不外发**（默认 False）。
    # 判据：不改任何配置时，本端点行为 == 关闭态（可断言的零回归）。
    try:
        from services import app_config as _ac
        _enabled = bool(_ac.get("live", "danmaku_enabled", False))
    except Exception as e:  # noqa: BLE001
        # 读不到配置 ⇒ 无法证明「已启用」⇒ fail-closed（绝不静默放行外发）
        logger.warning(f"[LIVE-040] [danmaku] 读取 danmaku_enabled 失败（按未启用处理）: {e}")
        _enabled = False
    if not _enabled:
        return {"ok": False, "sent": False, "reason": "danmaku_disabled",
                "error": "发送弹幕未启用（默认休眠）",
                "hint": "设置 → 直播 → 「发送弹幕」，开启后重试"}

    account = (body.account or "").strip()
    if not account:
        # 向后兼容兜底：旧调用方不带 account 时取「当前账号」；
        # 仍取不到 ⇒ fail-closed（绝不静默 ok:true）。
        try:
            from auto_dm.accounts import current_name
            account = (current_name() or "").strip()
        except Exception:
            account = ""
    if not account:
        return {"ok": False, "error": "未指定账号且无当前账号（无法加载发送凭证）",
                "reason": "no_account", "sent": False}

    try:
        room_id = _room_id_for(body.room_id)
    except HTTPException as e:
        return {"ok": False, "error": str(e.detail), "reason": "no_room_id", "sent": False}

    try:
        auth = _auth_for(account)
    except HTTPException as e:
        return {"ok": False, "error": str(e.detail), "reason": "credential_unavailable",
                "sent": False, "account": account}
    if auth is None:
        return {"ok": False, "error": f"账号 {account} 凭证为空",
                "reason": "credential_empty", "sent": False, "account": account}

    # 🔴 2026-09-30：kv `config.live_id` / 前端输入框里的是 **web_rid（URL 短号）**，
    # 而 `/webcast/room/chat/` 要的是**真实 room_id**（实测 992931212705 →
    # 7688251038101556006，二者不同）。此前直接把 web_rid 当 room_id 发 ⇒
    # 上游业务失败且响应里没有可读错误码（静默失败 = 用户看到的「发了没反应」）。
    # 这里经基座**唯一**归一化入口升级（取不到则原样回退，绝不编造）。
    # 位置必须在凭证就绪之后 —— 该探测是带凭证的真实请求。
    from dy_apis.douyin_api import DouyinAPI
    room_id = DouyinAPI._live_chat_room_id(auth, room_id)

    # 真实发送：DouyinAPI 由 LiveMixin 提供 sendMsgInRoom（staticmethod）。
    # 返回 safe_json(res) → dict，status_code==0 为业务成功。
    try:
        res = DouyinAPI.sendMsgInRoom(auth, room_id, content)
    except HTTPException as e:
        return {"ok": False, "error": str(e.detail), "reason": "request_rejected",
                "sent": False, "account": account, "roomId": room_id}
    except Exception as e:
        logger.warning(f"[LIVE-002] [danmaku] 发送异常 account={account} room={room_id}: {e}")
        return {"ok": False, "error": f"发送弹幕失败: {e}", "reason": "exception",
                "sent": False, "account": account, "roomId": room_id}

    data = (res or {}).get("data") or {}
    code = (res or {}).get("status_code")
    ok = code == 0
    logger.info(f"[LIVE-003] [danmaku] account={account} room={room_id} code={code} ok={ok}")
    if ok:
        return {"ok": True, "sent": True, "content": content, "account": account,
                "roomId": room_id, "statusCode": code, "data": data, "error": None}
    return {"ok": False, "sent": False, "content": content, "account": account,
            "roomId": room_id, "statusCode": code, "data": data,
            "error": data.get("message") or data.get("prompts") or f"上游返回 status_code={code}",
            "reason": "upstream_failed"}


@router.post("/like")
async def like_room(body: LikeSendBody):
    """给直播间点赞（`/webcast/room/like/`）—— **真实调用 dy_apis**。

    ## 设计契约（2026-09-30 立）

    - **前置**：`live.like_enabled=true`（**默认 False**，显式配置门）；
      否则 `ok=False, reason=like_disabled`，**零出站**。
    - **入参**：`count` 正整数（上限见 `live.like_max`，默认 1000）；
      `account`/`room_id` 缺省可回落（与 `/danmaku` 同一套回落契约）。
    - **房间号**：`room_id` 缺省来自 kv `config.live_id`，那是 **web_rid（URL 短号）**；
      直播域写接口要的是**真实 room_id** ⇒ 经基座唯一入口
      `DouyinAPI._live_chat_room_id` 归一化后使用。
    - **成功判据**：上游 `status_code == 0`；其余一律 fail-closed。
    - **红线**：写接口触风控；本项目「非必要不主动互动」，故默认休眠。

    与 `/danmaku` 的字段分层刻意保持一致（`ok`/`sent`/`reason`/`statusCode`），
    前端只需一套错误呈现逻辑。
    """
    # ① 显式配置门（默认休眠 —— 写接口不配置不外发）
    try:
        from services import app_config as _ac
        _enabled = bool(_ac.get("live", "like_enabled", False))
        _max = int(_ac.get("live", "like_max", 1000) or 1000)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[LIVE-041] [like] 读取 like_enabled 失败（按未启用处理）: {e}")
        _enabled, _max = False, 1000
    if not _enabled:
        return {"ok": False, "sent": False, "reason": "like_disabled",
                "error": "点赞未启用（默认休眠）",
                "hint": "设置 → 直播 → 「点赞」，开启后重试"}

    # ② count 校验（越界显式拒绝，**不静默夹取**）
    # 🔴 实测踩坑（门禁抓到的真实缺陷，不是用例错）：原写 `int(body.count or 1)`
    #    ⇒ `0` 是 falsy，被 `or 1` **静默吃成 1** —— 用户传 0 却发出 1 次点赞。
    #    改用显式三态：None（未传）⇒ 默认 1；其余一律 `int()`，非法即 0 ⇒ 走拒绝分支。
    raw_count = body.count
    if raw_count is None:
        count = 1
    else:
        try:
            count = int(raw_count)
        except (TypeError, ValueError):
            count = 0
    if count < 1 or count > _max:
        return {"ok": False, "sent": False, "reason": "bad_count",
                "error": f"点赞次数须在 1~{_max} 之间（收到 {body.count!r}）"}

    # ③ 账号（缺省回落当前账号；仍取不到 ⇒ fail-closed）
    account = (body.account or "").strip()
    if not account:
        try:
            from auto_dm.accounts import current_name
            account = (current_name() or "").strip()
        except Exception:
            account = ""
    if not account:
        return {"ok": False, "sent": False, "reason": "no_account",
                "error": "未指定账号且无当前账号（无法加载发送凭证）"}

    # ④ 房间号（缺省回落 kv config.live_id）
    try:
        room_id = _room_id_for(body.room_id)
    except HTTPException as e:
        return {"ok": False, "sent": False, "reason": "no_room_id", "error": str(e.detail)}

    # ⑤ 凭证
    try:
        auth = _auth_for(account)
    except HTTPException as e:
        return {"ok": False, "sent": False, "reason": "credential_unavailable",
                "error": str(e.detail), "account": account}
    if auth is None:
        return {"ok": False, "sent": False, "reason": "credential_empty",
                "error": f"账号 {account} 凭证为空", "account": account}

    from dy_apis.douyin_api import DouyinAPI
    room_id = DouyinAPI._live_chat_room_id(auth, room_id)

    # ⑥ 真实调用（唯一出站点）
    try:
        res = DouyinAPI.diggLiveRoom(auth, room_id, str(count))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[LIVE-042] [like] 发送异常 account={account} room={room_id}: {e}")
        return {"ok": False, "sent": False, "reason": "exception",
                "error": f"点赞失败: {e}", "account": account, "roomId": room_id}

    data = (res or {}).get("data") or {}
    code = (res or {}).get("status_code")
    ok = code == 0
    logger.info(f"[LIVE-043] [like] account={account} room={room_id} count={count} code={code} ok={ok}")
    if ok:
        return {"ok": True, "sent": True, "count": count, "account": account,
                "roomId": room_id, "statusCode": code, "data": data, "error": None}
    return {"ok": False, "sent": False, "count": count, "account": account,
            "roomId": room_id, "statusCode": code, "data": data,
            "error": data.get("message") or data.get("prompts") or f"上游返回 status_code={code}",
            "reason": "upstream_failed"}


@router.post("/dm-template")
async def set_dm_template(body: DmTemplateRequest):
    """配置私信模板与发送参数 —— **真落盘** kv_store `config`，不再是空壳。

    ## 修复说明（2026-09-28 N4 / 审计项 A-1）

    原实现 `# TODO: 写入 config` + `return {"ok": True}` —— 前端「保存模板」
    点了永远成功，配置**从不落盘**。落点复用既有 `database.set_kv_json("config", ...)`
    （与 `POST /resolve`、`POST /api/tasks/save-config` 同一处），并同步
    `settings` 运行时单例；落盘失败/读回不一致一律 `ok=False`。

    ⚠️ 已知未接项（登记，不在本次范围）：本域**读路径**未接 —— 前端模板
    回读仍走 `GET /api/tasks/current` 的 `dmPool`（其数据源是
    `adm.dm_template` / `settings.dm_pool`），不读本端点写的 `dm_template`
    键。故本次仅保证「写进去且能读回原文」，读路径打通留待后续批次。
    """
    pool = [str(t) for t in (body.pool or [])]
    payload = {
        "pool": pool,
        "delay_range": list(body.delay_range or (40, 65)),
        "interval": float(body.interval or 0),
        "max_target": int(body.max_target or 0),
    }
    # 运行时生效：同步 settings 单例，使 adm/引擎侧立即看到新词库。
    try:
        settings.dm_pool = pool
        settings.delay_range = tuple(payload["delay_range"])
        settings.interval = payload["interval"]
        settings.max_target = payload["max_target"]
    except Exception as e:
        logger.warning(f"[LIVE-004] [dm-template] 写入运行时 settings 失败: {e}")

    try:
        data = get_kv_json("config", {}) or {}
        data["dm_template"] = payload
        set_kv_json("config", data)
    except Exception as e:
        logger.warning(f"[LIVE-005] [dm-template] 配置落盘失败: {e}")
        return {"ok": False, "error": f"配置落盘失败: {e}", "reason": "persist_failed",
                "saved": False}

    # 落盘自证：写回后必须能**读回原文**，否则视为失败（防空转）。
    try:
        back = get_kv_json("config", {}) or {}
        if (back.get("dm_template") or {}).get("pool") != pool:
            return {"ok": False, "error": "配置落盘后读回校验不一致",
                    "reason": "readback_mismatch", "saved": False}
    except Exception as e:
        return {"ok": False, "error": f"配置落盘读回校验失败: {e}",
                "reason": "readback_failed", "saved": False}

    return {"ok": True, "saved": True, "count": len(pool), "dmTemplate": payload}


@router.post("/resolve")
async def resolve_live(body: ResolveRequest):
    """解析直播间链接/房间号，写回配置（迁移自原版 WebBridge.resolveLive）。

    1) 调用 link_resolve.resolve_live_id 解析出 web_rid（真实直播间号）；
    2) 写入运行时 settings（live_url 字段，供 engine.start 等后续读取）；
    3) 落盘 data/config.json（等价原版 _write_config_file 写回 LIVE_ID/LIVE_URL）。
    """
    from link_resolve import resolve_live_id
    import json
    from pathlib import Path

    raw = (body.url or "").strip()
    if not raw:
        return {"ok": False, "error": "链接为空"}
    try:
        from auto_dm.accounts import current_name
        _acct = current_name()
    except Exception:
        _acct = None
    try:
        live_id, _src = resolve_live_id(raw, account_name=_acct)
    except Exception as e:
        return {"ok": False, "error": f"解析失败: {e}"}
    if not live_id:
        return {"ok": False, "error": "未能从链接中解析出直播间号"}

    # 运行时生效：写入 settings 单例（原版 C.LIVE_ID / C.LIVE_URL）
    settings.live_url = raw

    # 🔴 2026-09-30：**同时落两个字段**（两套标识都有真实用途，见 `_room_id_for` 的说明）——
    #   · `live_id`      = web_rid（URL 短号）：`https://live.douyin.com/<web_rid>` 才是
    #                      可访问的直播间页，也是写接口 referer 的正确取值；
    #   · `live_room_id` = **真实 room_id**：直播域 API（发弹幕 / 点赞等）只认它。
    # 只落 web_rid 会让写接口拿短号当 room_id 用（上游静默失败）——这正是本轮根因，
    # 故在**解析侧**就把权威 room_id 一并落库（写接口无需每次多打一次网去探测）。
    # 探测失败**不影响解析结果**（写接口侧仍有 `_live_chat_room_id` 兜底归一化）。
    _real_room_id = ""
    try:
        from dy_apis.douyin_api import DouyinAPI
        _auth = None
        if _acct:
            try:
                _auth = _auth_for(_acct)
            except Exception:
                _auth = None
        if _auth is not None:
            _info = DouyinAPI.get_live_info(_auth, live_id)
            if isinstance(_info, dict):
                _real_room_id = str(_info.get("room_id") or "").strip()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[resolve] 真实 room_id 探测失败（不影响解析）: {e}")

    # 落盘到 SQLite kv_store（替代 config.json）
    try:
        from database import get_kv_json, set_kv_json
        data = get_kv_json("config", {}) or {}
        data["live_url"] = raw
        data["live_id"] = live_id                       # web_rid（短号，URL 用）
        if _real_room_id:
            data["live_room_id"] = _real_room_id        # 真实 room_id（API 用）
        set_kv_json("config", data)
    except Exception as e:
        logger.warning(f"[LIVE-001] " + f"[resolve] 配置落盘失败（不影响本次解析）: {e}")

    return {"ok": True, "liveId": live_id, "liveRoomId": _real_room_id, "liveUrl": raw}
