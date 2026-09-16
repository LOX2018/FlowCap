# coding=utf-8
"""私信发送层（迁移自 DY_Spider_base/auto_dm/sender.py）

关键点：优先使用直播间弹幕消息【自带】的数字 uid（ChatMessage.user.id），
直接走 imapi 私有接口 create_conversation + send_msg 直发，
彻底跳过原 DYchajian 卡死的 get_user_info（aweme/v1/web/user/profile/other/ 风控）环节。

若只拿到 sec_uid 而没有数字 uid，才 fallback 到 douyin_api.get_user_info 查询
（带重试，失败则放弃该目标，不影响其它）。

重构变化（相对旧版）：
- 业务逻辑完整保留（DouyinAPI 是同步的，不做异步化以免破坏签名/风控链路）
- 提供 async wrapper `send_target_async` 供 asyncio 调度使用
- 返回值不变：(ok: bool, reason: str)
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Tuple

from loguru import logger

from dy_apis.douyin_api import DouyinAPI


# 触发自动重捕获的失败原因关键字（仅"凭证失效类"，排除账号级风控 KICK）
_RECAP_MARKERS = ("签名三件套缺失", "INVALID_REQUEST")


# ============================================================================
# 失败原因结构化分类（2026-09-08，用户要求：失败弹窗须区分
# 「调度堵塞 / 凭证失效 / 账号风控 / 频控限流 / 参数错误 / 其他」）
# ----------------------------------------------------------------------------
# 只做**纯文本归类**（零网络、零副作用），供前端弹窗给出可操作建议。
# 规则顺序即优先级：越具体的排在越前面（先判风控/凭证，再判堵塞）。
# ============================================================================
FAIL_KINDS = {
    "credential": "凭证失效",
    "risk": "账号风控",
    "ratelimit": "频控限流",
    "blocked": "调度堵塞",
    "param": "参数错误",
    "network": "网络异常",
    "other": "其他原因",
}

# 每类的用户可读建议（前端弹窗直接展示，给用户下一步动作）
FAIL_ADVICE = {
    "credential": "该账号私信签名已失效。请到「账号」页面点【重新扫码】重新抓取签名后重试。",
    "risk": "抖音对该账号的私信行为判定为风控（多见于向陌生用户频繁首发）。建议：降低发送频率、暂停该账号 30 分钟以上，或换账号发送。",
    "ratelimit": "已达发送频率上限（统一闸门限流）。建议等待冷却结束再发，不要手动连续重发，否则会加重限流。",
    "blocked": "发送队列/调度被占满或任务排队超时。建议：暂停当前监听任务，等队列消化后再启动；若持续出现请重启后端。",
    "param": "发送参数不合法（目标 uid 或文案为空/格式错误）。请检查该目标的会话数据是否完整。",
    "network": "网络或守护进程不可达。请检查后端与 recv_daemon 是否在运行。",
    "other": "未能归类的失败。请展开原始原因或查看后端日志定位。",
}


def classify_fail(reason: str) -> str:
    """把原始失败原因归类为 FAIL_KINDS 的 key（纯文本匹配，零副作用）。"""
    r = (reason or "").strip()
    low = r.lower()
    if not r:
        return "other"
    # ① 账号级风控：KICK / 反 spam（**必须最先判**）
    #    后端 sender.py 注释明确：KICK 多为账号级私信风控（反 spam），
    #    不是签名失效。而该文案同时含 INVALID_REQUEST，若先判凭证会误归。
    if "KICK" in r.upper() or "风控" in r or "spam" in low or "被限制" in r:
        return "risk"
    # ② 凭证失效：签名缺失 / 服务端拒签名（此时已排除 KICK）
    if ("签名三件套缺失" in r or "需重新扫码" in r
            or "INVALID_REQUEST" in r or "unauthorized" in low
            or "登录态" in r or "cookie" in low and "失效" in r):
        return "credential"
    # ③ 频控限流：闸门 rate_limited / 频繁 / 冷却期 / 上限
    if ("rate_limited" in low or "频繁" in r or "冷静期" in r
            or "冷却" in r or "上限" in r or "限流" in r):
        return "ratelimit"
    # ④ 调度堵塞：队列满 / 排队超时 / 调度器忙 / 守护不可达时的排队失败
    if ("堵塞" in r or "队列" in r or "queue" in low or "超时" in r
            or "timeout" in low or "busy" in low or "调度" in r):
        return "blocked"
    # ⑤ 参数错误
    if ("为空" in r or "非数字" in r or "缺失" in r and "账号" in r
            or "uid" in low and "解析" in r or "会话整理" in r):
        return "param"
    # ⑥ 网络/连接
    if ("不可达" in r or "connection" in low or "连接" in r
            or "network" in low or "HTTP 5" in r):
        return "network"
    return "other"


def explain_fail(reason: str) -> dict:
    """返回结构化失败说明：{kind, label, advice, raw}（供前端弹窗展示）。"""
    kind = classify_fail(reason)
    return {
        "kind": kind,
        "label": FAIL_KINDS.get(kind, "其他原因"),
        "advice": FAIL_ADVICE.get(kind, ""),
        "raw": reason or "",
    }


# ============================================================================
# 统一发送闸门客户端（2026-09-06 第五轮治理 P1-B，09 台账 5.3）
# ----------------------------------------------------------------------------
# 直播 dispatch 的私信发送原先是本进程 imapi 直发，与 recv_daemon /send
# （手动 + AI 回复）互不知晓，同账号三源同时活跃时发送频率叠加 = 频率风控面。
# 现改为：优先 HTTP 调 recv_daemon /send_by_uid（闸门在那边，三源一配额）；
# recv_daemon 不可达（守护未启动/网络异常）才兜底本进程直发——功能不丢，
# 但闸门失效时会打 warning 提示。
# ============================================================================
def _send_via_recv_daemon(auth: Any, user_id: int, content: str,
                          timeout: float = 30.0):
    """尝试经 recv_daemon /send_by_uid 直发。返回 (handled, ok, reason)。

    handled=False 表示守护不可达（调用方应兜底直发）；
    handled=True 时 ok/reason 是最终结果。
    """
    account = getattr(auth, "account_name", None)
    if not account:
        return False, False, "auth 未标记 account_name"
    try:
        from auto_dm.accounts import recv_daemon_port
    except Exception as e:
        return False, False, f"导入失败: {e}"
    try:
        port = recv_daemon_port(account)
    except Exception:
        port = None
    if not port:
        return False, False, "recv_daemon 端口解析失败"
    import requests as _rq
    try:
        r = _rq.post(f"http://127.0.0.1:{port}/send_by_uid",
                     json={"account": account, "peer_uid": int(user_id),
                           "text": content},
                     timeout=timeout)
        d = r.json() or {}
    except Exception as e:
        return False, False, f"recv_daemon 不可达: {e}"
    # 守护可达：无论成功/限流都是最终结果，不再兜底
    if d.get("ok"):
        return True, True, "ok"
    return True, False, d.get("error") or d.get("msg") or "send_by_uid 返回失败"


def _maybe_auto_recapture(auth: Any, reason: str) -> None:
    """发送失败且属于凭证失效类时，best-effort 触发自动重捕获（不阻塞）。

    2026-09-08：同时发 IM 告警（凭证失效是最高优先级事件，必须让人知道）。
    节流 10 分钟由 notify.events 内部负责 —— 发送链路会反复重试，不节流会刷屏。
    """
    if not any(m in reason for m in _RECAP_MARKERS):
        return
    name = getattr(auth, "account_name", None) or None
    # 先告警（即使后续重捕获失败，人也已经收到通知）
    try:
        from notify import events as _ev

        _ev.cred_expired(name or "(未知账号)", reason, auto_fixing=True)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[notify] 凭证失效告警跳过: {e}")
    try:
        from auto_dm.accounts import auto_recapture

        auto_recapture(name)
    except Exception as e:
        logger.warning(f"[SEND-009] " + f"[recap] 触发自动重捕获失败: {e}")


def send_by_uid(auth: Any, user_id: Any, content: str, max_retry: int = 2) -> Tuple[bool, str]:
    """按数字 uid 直发私信。返回 (bool ok, str reason)。

    :param auth: 已 prepare 的 DouyinAuth（含 web_protect/keys 签名）
    :param user_id: 数字 uid（int/str）。来自弹幕消息 user.id，无需查询。
    """
    if user_id is None:
        return False, "user_id 为空"
    if not content or not str(content).strip():
        logger.error(f"[SEND-010] " + "[私信] 文案为空，拒绝发送（避免日志显示成功但实际未发送）")
        return False, "文案为空，拒绝发送"
    try:
        user_id = int(user_id)
    except Exception:
        return False, f"user_id 非数字: {user_id}"

    # ---- 诊断：确认 auth 签名三件套是否有效 ----
    _tk = getattr(auth, "ticket", None) or ""
    logger.debug(
        f"[diag] auth.ticket={'有' if _tk else '空'}(前30={_tk[:30]!r}) "
        f"ts_sign={'有' if getattr(auth, 'ts_sign', None) else '空'} "
        f"client_cert={'有' if getattr(auth, 'client_cert', None) else '空'} "
        f"private_key={'有' if getattr(auth, 'private_key', None) else '空'} "
        f"cookie={'有' if getattr(auth, 'cookie', None) else '空'}"
    )
    if not (
        getattr(auth, "ticket", None)
        and getattr(auth, "client_cert", None)
        and getattr(auth, "private_key", None)
    ):
        logger.error("AUTH-021", 
            "[auth] 私信签名三件套缺失(ticket/client_cert/private_key)。"
            "请删除 .env 中的 DY_TICKET/DY_TS_SIGN/DY_CLIENT_CERT/DY_PRIVATE_KEY "
            "后重启完成一次扫码登录。"
        )
        _maybe_auto_recapture(auth, "签名三件套缺失(ticket/client_cert/private_key)")
        return False, "签名三件套缺失(ticket/client_cert/private_key)，需重新扫码"

    # ---- P1-B：优先经 recv_daemon /send_by_uid（统一发送闸门，三源一配额）----
    _handled, _ok, _reason = _send_via_recv_daemon(auth, user_id, content)
    if _handled:
        if _ok:
            logger.info(f"[私信] 发送结果: 目标「{user_id}」=成功 文案前20字={content[:20]!r}")
            return True, "ok"
        if "rate_limited" in str(_reason):
            logger.warning(f"[SEND-011] " + f"[私信] 发送被闸门限流 uid={user_id}: {_reason}")
            return False, _reason
        logger.warning(f"[SEND-012] " + f"[私信] 经 recv_daemon 直发失败 uid={user_id}: {_reason}")
        return False, _reason
    # 守护不可达：兜底本进程直发（此时无统一闸门，仅本条自身的重试间隔）
    logger.warning("SEND-013", f"[私信] recv_daemon 不可达（{_reason}），兜底本进程直发——"
                   f"发送闸门失效，注意频率风控")

    for attempt in range(1, max_retry + 1):
        try:
            conversation_id, short_id, ticket = DouyinAPI.create_conversation(auth, user_id)
        except Exception as e:
            msg = str(e)
            if "INVALID_REQUEST" in msg or "KICK" in msg:
                # create_conversation 走 imapi 私有网关（带 web_protect 四件套签名），
                # 预检（对自身 uid）通过即证明签名有效。此处被 KICK 几乎不是"没打开私信对话框/
                # 签名缺失"，而是账号级私信风控（陌生目标反 spam）或私信频控/被限制。
                logger.error("SEND-014", 
                    f"[私信被风控] create_conversation 被抖音拒绝(uid={user_id}): {msg}\n"
                    f"       说明：签名四件套有效（预检已通过），此处 KICK 多为账号级私信风控\n"
                    f"       （向陌生观众批量私信触发反 spam）或私信频控/被限制。\n"
                    f"       建议：降低发送频率、换号/养号，或确认该账号能否手动给该用户发私信。"
                )
                _maybe_auto_recapture(auth, f"INVALID_REQUEST: {msg}")
                return False, f"私信被风控(INVALID_REQUEST/KICK): {msg}"
            logger.warning(f"[SEND-015] " + f"create_conversation 失败(第{attempt}次) uid={user_id}: {e}")
            if attempt == max_retry:
                return False, f"create_conversation 失败: {e}"
            time.sleep(1)
            continue
        try:
            ok, detail = DouyinAPI.send_msg(auth, conversation_id, short_id, ticket, content)
        except Exception as e:
            logger.warning(f"[SEND-016] " + f"send_msg 失败(第{attempt}次) uid={user_id}: {e}")
            if attempt == max_retry:
                return False, f"send_msg 失败: {e}"
            time.sleep(1)
            continue
        if ok:
            logger.info(f"[私信] 发送结果: 目标「{user_id}」=成功 文案前20字={content[:20]!r}")
            return True, "ok"
        logger.warning(f"[SEND-017] " + f"send_msg 返回 {detail!r}(第{attempt}次) uid={user_id}")
        if attempt == max_retry:
            return False, detail if detail else "send_msg 返回 False"
        time.sleep(1)
    return False, "未知失败"


def send_by_secuid(auth: Any, sec_uid: str, content: str, max_retry: int = 1) -> Tuple[bool, str]:
    """仅持有 sec_uid 时的 fallback：先 get_user_info 拿 uid 再发。"""
    try:
        info = DouyinAPI.get_user_info(auth, f"https://www.douyin.com/user/{sec_uid}")
        user = (info or {}).get("user") or {}
        uid = user.get("uid")
        if uid is None:
            return False, "get_user_info 无 user.uid"
        return send_by_uid(auth, uid, content, max_retry=max_retry)
    except Exception as e:
        return False, f"get_user_info 异常: {e}"


def send_target(auth: Any, target: dict, content: str) -> Tuple[bool, str]:
    """统一入口：target = {'user_id':..., 'sec_uid':..., 'nickname':...}。

    优先用 user_id 直发；缺失时 fallback 到 sec_uid 查询。
    """
    nickname = target.get("nickname", "")
    user_id = target.get("user_id")
    sec_uid = target.get("sec_uid")
    if user_id:
        ok, reason = send_by_uid(auth, user_id, content)
    elif sec_uid:
        ok, reason = send_by_secuid(auth, sec_uid, content)
    else:
        return False, "既无 user_id 也无 sec_uid"
    if ok:
        logger.info(f"[私信] 已发送给「{nickname}」(uid={user_id or sec_uid})")
    else:
        logger.warning(f"[SEND-018] " + f"[私信] 发送失败「{nickname}」: {reason}")
    return ok, reason


async def send_target_async(auth: Any, target: dict, content: str) -> Tuple[bool, str]:
    """send_target 的 async wrapper。

    用 asyncio.to_thread 包装同步调用，避免阻塞 FastAPI 事件循环。
    DispatchCenter 在 asyncio 上下文中调用本函数。
    """
    return await asyncio.to_thread(send_target, auth, target, content)
