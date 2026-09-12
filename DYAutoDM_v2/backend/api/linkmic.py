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
import time

router = APIRouter()


def _auth_for(account: str):
    """加载指定账号凭证 → dy_auth（与直播监听引擎同源）。"""
    try:
        from auto_dm import accounts as acct_core
        env_path = acct_core.env_path_of(account)
        if not env_path:
            raise HTTPException(404, f"账号 {account} 未登记")
        import utils.common_util as common_util
        return common_util.load_env(env_path)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(503, f"加载账号 {account} 凭证失败: {e}")


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


@router.post("/apply")
async def apply_linkmic(body: ApplyBody) -> dict:
    """申请连麦（接口直调，与直播监听同链路）。"""
    from dy_apis.douyin_api import DouyinAPI
    auth = _auth_for(body.account)
    room_id, anchor_id = _room_ids(body.account, body.room_id, body.anchor_id)
    if not anchor_id:
        raise HTTPException(400, "缺少 anchor_id（主播 uid）")
    # anchor_id 缺失时从直播间页抓（get_live_info 返回 room_info 含 user_id）
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
    logger.info(f"[linkmic] apply account={body.account} room={room_id} "
                f"code={code} prompts={data.get('prompts')} queue={data.get('waiting_list_offset')}")
    # 记录状态供前端轮询
    st = get_kv_json("linkmic_state", {}) or {}
    st[body.account] = {
        "phase": "applied" if ok else "apply_failed",
        "room_id": room_id,
        "linkmic_id_str": data.get("linkmic_id_str"),
        "queue_no": data.get("waiting_list_offset"),
        "auto_join": data.get("auto_join"),
        "prompts": data.get("prompts"),
        "ts": int(time.time()),
    }
    set_kv_json("linkmic_state", st)
    return {"ok": ok, "status_code": code, "data": data,
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
