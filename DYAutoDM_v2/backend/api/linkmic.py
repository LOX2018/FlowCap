"""直播间连麦路由（接口直调版，2026-09-10）。

与直播监听数据链路同构：走 dy_apis 直连接口（账号凭证 + msToken + a_bogus 签名），
**不经过 BCC 浏览器**（申请连麦是低频单次写操作，与 diggLiveRoom/sendMsgInRoom
同范式，签名基础设施 generate_a_bogus / auth.msToken 项目内已齐备）。

端点（挂 /api/live/linkmic）：
  POST /apply    申请连麦（room_id + anchor_id；响应含排队位次 waiting_list_offset）
  GET  /status   状态：waiting_list.total_count（排队人数）+ list/v2（连线者）
                 + check_audience_linkers（action/sleep_second 轮询节奏）
  POST /leave    退出连麦

依赖：account → .env 凭证（common_util.load_env(env_path) 加载该账号）。
真实 room_id 与 anchor_id 由前端/调用方传入（解析房间号后可得）。
"""
from fastapi import APIRouter, HTTPException
from loguru import logger
from pydantic import BaseModel

from database import get_kv_json, set_kv_json
import asyncio
import time

router = APIRouter()


def _auth_for(account: str):
    """加载指定账号凭证 → dy_auth（与直播监听引擎**同一加载器**）。

    ⚠️ 2026-10-01：原用 `utils.common_util.load_env` —— 那是**弱载入器**
    （`perepare_auth(cookies, "", "")` 且**不还原** `DY_PRIVATE_KEY` 的字面量 `\\n`），
    会让写接口在 `SigningKey.from_pem` 处抛 `Empty string does not encode a sequence`。
    同族缺陷已在 `api/live.py::_auth_for` / `api/platform.py::_auth_for` 修过，
    本处为**第三处残留**，一并收敛到唯一真源（SSOT）。
    """
    try:
        from auto_dm import accounts as acct_core
        env_path = acct_core.env_path_of(account)
        if not env_path:
            raise HTTPException(404, f"账号 {account} 未登记")
        from dy_apis.login_api import DYLoginApi
        return DYLoginApi._load_auth_from_env(env_path)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(503, f"加载账号 {account} 凭证失败: {type(e).__name__}: {e}")


def _room_ids(account: str, room_id: str | None, anchor_id: str | None):
    """room_id/anchor_id 缺省时从运行时配置补齐（resolve 流程写入）。"""
    cfg = get_kv_json("config", {}) or {}
    rid = room_id or cfg.get("live_id") or ""
    aid = anchor_id or cfg.get("anchor_id") or ""
    if not rid:
        raise HTTPException(400, "缺少 room_id（请先解析直播间）")
    return str(rid), str(aid or "")


class ApplyBody(BaseModel):
    account: str
    room_id: str | None = None      # 真实 room_id（非 URL 短号）
    anchor_id: str | None = None    # 主播 uid
    link_type: str = "2"            # 2=语音连线（实测值）
    apply_type: str = "0"           # 0=主动申请
    # ── 2026-09-29（H-12 用户指令）：申请连麦改走 **DOM 页面原生路径** ──────────
    #   接口直调 `/webcast/linkmic_audience/apply/` 需 msToken+a_bogus 签名，
    #   签名失配会被服务端拒（实测 10011）；页面原生流程**天然带签名**且能正确
    #   驱动「选麦克风 → 确定」对话框。故首选 DOM，接口直调降级保留。
    method: str = "dom"             # "dom"（默认，页面原生）| "api"（接口直调，降级）
    room_url: str | None = None     # 直播间 URL（缺省由 room_id 拼；DOM 路径需要）
    raw_js: str | None = None       # 覆盖默认 DOM 脚本（便于不重打包即可调参）


class RoomBody(BaseModel):
    account: str
    room_id: str | None = None


def _my_uid(account: str) -> str:
    """自己 uid：读 uid_probe 调度器缓存（零网络请求，与 /api/accounts 同源）。"""
    try:
        from services.uid_probe import get_uid as _uid_get
        return str(_uid_get(account) or "")
    except Exception:
        return ""


# ── DOM 页面原生申请连麦（H-12，2026-09-29 用户指令）───────────────────────
#  实测要点（工作记忆/12 §5.5，禁止重犯）：
#   · 按钮文案是「申请连线」（部分直播间可能「申请连麦」）——**两者都匹配**；
#   · 按钮 **进房后约 24s 才出现** ⇒ 默认等约 40s，勿只等 8~9s；
#   · class 是**动态哈希**（勿用固定 class）⇒ 用 `#BottomLayout` 作用域 + button + 文本；
#   · 点后弹「请选择麦克风输入」对话框（Fake 设备）⇒ 选设备 + 点「确定」；
#   · 需**虚拟麦克风**（已在 vbrowser_camoufox 注入 Firefox media prefs 实现）。
_LINKMIC_DOM_JS = r"""
async (arg) => {
  const out = {steps: {}};
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));
  const txt = (el) => ((el && (el.innerText || el.textContent)) || '').trim();
  const clickReal = (el) => { try { el.scrollIntoView({block:'center'}); } catch(e){}
                              el.click(); };
  // ① 等「申请连线 / 申请连麦」按钮出现（进房后约 24s；给 40s）
  let btn = null;
  for (let i = 0; i < 80; i++) {
    const btns = Array.from(document.querySelectorAll(
      '#BottomLayout button, #BottomLayout [role="button"], button'));
    btn = btns.find(b => /申请连线|申请连麦/.test(txt(b)));
    if (btn) break;
    await sleep(500);
  }
  out.steps.found = !!btn;
  if (!btn) { out.error = '未找到「申请连线」按钮（该主播可能未开连麦，或已在连线中）'; return out; }
  out.buttonText = txt(btn);
  try { const r = btn.getBoundingClientRect();
        out.buttonBox = [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)]; } catch(e){}
  clickReal(btn);
  out.steps.clicked = true;
  await sleep(2500);
  // ② 选麦克风设备 + 确定（Fake 设备对话框；多轮兜底）
  for (let k = 0; k < 4; k++) {
    out.steps['dialog_round_' + k] = (() => {
      const cand = Array.from(document.querySelectorAll('div,span,li,button,p'))
        .filter(e => { const r = e.getBoundingClientRect();
          return r.width > 0 && r.height > 0 && /麦克风|确定|确认|同意|申请/.test(txt(e)); });
      const dev = cand.find(e => /麦克风|Fake|Audio/i.test(txt(e)) && txt(e).length < 40);
      if (dev) { clickReal(dev); return 'device:' + txt(dev).slice(0,20); }
      const ok = cand.find(e => /^(确定|确认|同意)$/.test(txt(e)));
      if (ok) { clickReal(ok); return 'confirm:' + txt(ok); }
      return 'none';
    })();
    await sleep(1500);
  }
  // ③ 只有**确实确认到设备/点了确定**才算成功；四轮全 none 视为失败（禁假成功）
  const rounds = Object.keys(out.steps)
    .filter(k => k.indexOf('dialog_round_') === 0)
    .map(k => out.steps[k]);
  out.confirmed = rounds.some(s => /^(device:|confirm:)/.test(s || ''));
  out.ok = out.confirmed;
  if (!out.confirmed) {
    out.error = '已点击「申请连线」但四轮均未选中麦克风设备/未点确定'
      + '（可能未弹出对话框，或页面结构变化）——申请未确认';
  }
  return out;
}
"""


def _run_linkmic_dom(account: str, room_url: str,
                     raw_js: str | None = None) -> dict:
    """经 BCC `/linkmic_run` 在**直播间页面上下文**执行 DOM 申请流程。

    为什么必须走 BCC（工作记忆/12 §5.5）：`/exec_js` 硬限制在 /chat 页，
    无法驻留直播间；连麦必须在直播间页做交互。

    ⚠️ 本函数全程**同步阻塞 IO**（`_bcc_alive` 走 requests.get、`_bcc_post`
    timeout=120，DOM 流程还要等按钮 ~40s）⇒ **禁止在事件循环里直接 await/调用**，
    必须由调用方 `await asyncio.to_thread(_run_linkmic_dom, ...)` 下放线程
    （否则冻结整个事件循环数十秒，实测 /api/live/stream 3s 轮询全部排队）。
    原为 `async def` 但内部无任何 await ⇒ 加 async 是**假异步**，故改回 `def`。
    """
    from dy_apis.login_api import _bcc_post, _bcc_alive
    if not _bcc_alive(account):
        return {"ok": False, "via": "dom",
                "error": f"BCC 未运行（账号 {account}）——请先打开该账号浏览器"}
    js = raw_js or _LINKMIC_DOM_JS
    res = _bcc_post(account, "/linkmic_run",
                    {"action": "apply", "room_url": room_url, "js": js,
                     "timeout": 90}, timeout=120)
    if not isinstance(res, dict):
        return {"ok": False, "via": "dom", "error": f"BCC 返回异常: {res!r}"}
    if not res.get("ok"):
        return {"ok": False, "via": "dom",
                "error": res.get("msg") or res.get("error") or "BCC /linkmic_run 失败"}
    result = res.get("result")
    if isinstance(result, dict) and result.get("ok"):
        return {"ok": True, "via": "dom", "result": result}
    err = result.get("error") if isinstance(result, dict) else ""
    return {"ok": False, "via": "dom", "result": result,
            "error": err or "DOM 流程未成功（详见 result）"}


@router.post("/apply")
async def apply_linkmic(body: ApplyBody) -> dict:
    """申请连麦 —— **DOM 页面原生优先**（H-12，2026-09-29 用户指令），接口直调降级。

    为什么改（实测，工作记忆/12 §5.5）：接口直调 `/webcast/linkmic_audience/apply/`
    需 msToken+a_bogus 签名，签名失配被服务端拒（实测 10011）；而**页面原生流程**
    天然带签名，且能正确驱动「选麦克风 → 确定」对话框。故默认走 DOM。
    保留 `method="api"` 供显式选择 / 排查。
    """
    from dy_apis.douyin_api import DouyinAPI
    auth = _auth_for(body.account)
    room_id, anchor_id = _room_ids(body.account, body.room_id, body.anchor_id)
    room_url = (body.room_url or f"https://live.douyin.com/{room_id}").strip()

    def _store(phase: str, extra: dict) -> None:
        st = get_kv_json("linkmic_state", {}) or {}
        st[body.account] = {"phase": phase, "room_id": room_id, "ts": int(time.time()),
                            "method": body.method, **extra}
        set_kv_json("linkmic_state", st)

    # ── 路径 A：DOM 页面原生（默认）───────────────────────────────────
    if str(body.method).lower() != "api":
        # C-2 修复（OCR[7] MED）：`_run_linkmic_dom` 全程同步阻塞 IO
        # （requests + DOM 等按钮 ~40s），若在事件循环里直接调用会冻结整个
        # 循环数十秒（连 /api/live/stream 3s 轮询都排队）。下放线程执行，
        # 返回值契约不变（仍为 dict）。
        dom = await asyncio.to_thread(_run_linkmic_dom,
                                      body.account, room_url, body.raw_js)
        if dom.get("ok"):
            r = dom.get("result") or {}
            _store("applied", {"via": "dom", "button": r.get("buttonText"),
                               "steps": r.get("steps")})
            logger.info(f"[linkmic] apply(DOM) account={body.account} room={room_id} "
                        f"button={r.get('buttonText')!r} steps={r.get('steps')}")
            return {"ok": True, "via": "dom", "status_code": 0, "data": r,
                    "error": None}
        # DOM 失败 → 记录并**降级**接口直调（保留原能力，避免整体不可用）
        logger.warning(f"[linkmic] apply(DOM) 未成功，降级接口直调: {dom.get('error')}")
        _store("apply_dom_failed", {"via": "dom", "error": str(dom.get("error"))[:200]})

    # ── 路径 B：接口直调（降级 / 显式选择）────────────────────────────
    # 2026-09-17 修补（OCR 审查 HIGH —— 补全分支被早退守卫变成死代码）：
    # 原实现在此先 `if not anchor_id: raise HTTPException(400)`，
    # 使下方「从直播间页抓 anchor_id」的补全逻辑**永不可达**（注释明写其意图），
    # 而 ApplyBody.anchor_id 标注为可选 → 与「可自动补全」的设计相悖。
    # 现删除该早退，让补全逻辑真正执行；补全失败仍由末尾守卫返回 400。
    if not anchor_id:
        try:
            info = DouyinAPI.get_live_info(auth, room_id)
            if isinstance(info, dict):
                anchor_id = str(info.get("user_id") or "")
                # 顺手缓存真实 room_id 映射
                if info.get("room_id"):
                    cfg = get_kv_json("config", {}) or {}
                    cfg["live_id"] = str(info.get("room_id"))
                    set_kv_json("config", cfg)
        except Exception as e:
            logger.warning(f"[linkmic] get_live_info 补 anchor_id 失败: {e}")
    if not anchor_id:
        raise HTTPException(400, "无法确定主播 uid（anchor_id）")
    try:
        res = DouyinAPI.linkmicApply(auth, room_id, anchor_id,
                                     apply_type=body.apply_type,
                                     link_type=body.link_type)
    except Exception as e:
        raise HTTPException(502, f"申请连麦请求失败: {e}")
    data = (res or {}).get("data") or {}
    code = (res or {}).get("status_code")
    ok = code == 0
    logger.info(f"[linkmic] apply(API) account={body.account} room={room_id} "
                f"code={code} prompts={data.get('prompts')} queue={data.get('waiting_list_offset')}")
    # 记录状态供前端轮询
    _store("applied" if ok else "apply_failed", {
        "via": "api",
        "linkmic_id_str": data.get("linkmic_id_str"),
        "queue_no": data.get("waiting_list_offset"),
        "auto_join": data.get("auto_join"),
        "prompts": data.get("prompts"),
    })
    return {"ok": ok, "via": "api", "status_code": code, "data": data,
            "error": None if ok else (data.get("message") or data.get("prompts") or str(code))}


@router.get("/status")
async def linkmic_status(account: str, room_id: str | None = None) -> dict:
    """连麦状态：排队人数 + 连线者 + 轮询节奏。

    连线判定（按知识库 05 §5.8.1 实测）：
      - waiting_list.total_count 回落且 list/v2 出现主播 = 已进入连线
      - list/v2 含自己 uid 是最直接信号（可能延迟出现，不能单靠）
      - check_audience_linkers.action 变化也是信号
    """
    from dy_apis.douyin_api import DouyinAPI
    auth = _auth_for(account)
    room_id, _ = _room_ids(account, room_id, None)
    my_uid = _my_uid(account)
    out: dict = {"room_id": room_id, "my_uid": my_uid}
    try:
        wl = DouyinAPI.linkmicWaitingList(auth, room_id)
        out["waiting_total"] = ((wl or {}).get("data") or {}).get("total_count")
    except Exception as e:
        out["waiting_err"] = str(e)[:100]
    try:
        ls = DouyinAPI.linkmicList(auth, room_id)
        users = ((ls or {}).get("data") or {}).get("user") or []
        out["linkers"] = [{"id": str((u.get("user") or {}).get("id", "")),
                           "nickname": (u.get("user") or {}).get("nickname", "")}
                          for u in users[:8]]
        out["me_in_linkers"] = any(x["id"] == my_uid for x in out["linkers"]) if my_uid else None
    except Exception as e:
        out["linkers_err"] = str(e)[:100]
    try:
        ck = DouyinAPI.linkmicCheck(auth, room_id)
        out["check"] = (ck or {}).get("data") or {}
    except Exception as e:
        out["check_err"] = str(e)[:100]
    # 已连线判定：连线者列表非空（至少含主播）→ 等待队列清空
    linked = bool(out.get("linkers")) and out.get("waiting_total") == 0
    out["linked"] = linked
    # 更新状态缓存
    st = get_kv_json("linkmic_state", {}) or {}
    if account in st:
        st[account]["linked"] = linked
        st[account]["last_status_ts"] = int(time.time())
        set_kv_json("linkmic_state", st)
    return {"ok": True, "status": out}


@router.post("/leave")
async def linkmic_leave(body: RoomBody) -> dict:
    """退出连麦。"""
    from dy_apis.douyin_api import DouyinAPI
    auth = _auth_for(body.account)
    room_id, _ = _room_ids(body.account, body.room_id, None)
    try:
        res = DouyinAPI.linkmicLeave(auth, room_id)
    except Exception as e:
        raise HTTPException(502, f"退出连麦请求失败: {e}")
    ok = (res or {}).get("status_code") == 0
    logger.info(f"[linkmic] leave account={body.account} room={room_id} ok={ok}")
    st = get_kv_json("linkmic_state", {}) or {}
    if body.account in st:
        st[body.account]["phase"] = "left"
        st[body.account]["ts"] = int(time.time())
        set_kv_json("linkmic_state", st)
    return {"ok": ok, "data": (res or {}).get("data") or {}}
