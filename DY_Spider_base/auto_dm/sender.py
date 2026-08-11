# coding=utf-8
"""私信发送层（融合 DouYin_Spider 底座）。

关键点：优先使用直播间弹幕消息【自带】的数字 uid（ChatMessage.user.id），
直接走 imapi 私有接口 create_conversation + send_msg 直发，
彻底跳过原 DYchajian 卡死的 get_user_info（aweme/v1/web/user/profile/other/ 风控）环节。

若只拿到 sec_uid 而没有数字 uid，才 fallback 到 douyin_api.get_user_info 查询
（带重试，失败则放弃该目标，不影响其它）。
"""

import time
from loguru import logger
from dy_apis.douyin_api import DouyinAPI


def send_by_uid(auth, user_id, content, max_retry=2):
    """按数字 uid 直发私信。返回 (bool ok, str reason)。

    :param auth: 已 prepare 的 DouyinAuth（含 web_protect/keys 签名）
    :param user_id: 数字 uid（int/str）。来自弹幕消息 user.id，无需查询。
    """
    if user_id is None:
        return False, "user_id 为空"
    try:
        user_id = int(user_id)
    except Exception:
        return False, f"user_id 非数字: {user_id}"

    # ---- 诊断：确认 auth 签名三件套是否有效（排查 bad argument type）----
    _tk = getattr(auth, 'ticket', None) or ''
    logger.debug(f"[diag] auth.ticket={'有' if _tk else '空'}(前30={_tk[:30]!r}) "
                 f"ts_sign={'有' if getattr(auth,'ts_sign',None) else '空'} "
                 f"client_cert={'有' if getattr(auth,'client_cert',None) else '空'} "
                 f"private_key={'有' if getattr(auth,'private_key',None) else '空'} "
                 f"cookie={'有' if getattr(auth,'cookie',None) else '空'}")
    if not (getattr(auth, "ticket", None) and getattr(auth, "client_cert", None)
            and getattr(auth, "private_key", None)):
        logger.error("[auth] 私信签名三件套缺失(ticket/client_cert/private_key)。"
                     "请删除 .env 中的 DY_TICKET/DY_TS_SIGN/DY_CLIENT_CERT/DY_PRIVATE_KEY "
                     "后重启完成一次扫码登录。")
        return False, "签名三件套缺失(ticket/client_cert/private_key)，需重新扫码"

    for attempt in range(1, max_retry + 1):
        try:
            conversation_id, short_id, ticket = DouyinAPI.create_conversation(auth, user_id)
        except Exception as e:
            msg = str(e)
            if "INVALID_REQUEST" in msg or "KICK" in msg:
                # create_conversation 走 imapi 私有网关（带 web_protect 四件套签名），
                # 预检（对自身 uid）通过即证明签名有效。此处被 KICK 几乎不是“没打开私信对话框/
                # 签名缺失”，而是账号级私信风控（陌生目标反 spam）或私信频控/被限制。
                logger.error(
                    f"[私信被风控] create_conversation 被抖音拒绝(uid={user_id}): {msg}\n"
                    f"       说明：签名四件套有效（预检已通过），此处 KICK 多为账号级私信风控\n"
                    f"       （向陌生观众批量私信触发反 spam）或私信频控/被限制。\n"
                    f"       建议：降低发送频率、换号/养号，或确认该账号能否手动给该用户发私信。")
                return False, f"私信被风控(INVALID_REQUEST/KICK): {msg}"
            logger.warning(f"create_conversation 失败(第{attempt}次) uid={user_id}: {e}")
            if attempt == max_retry:
                return False, f"create_conversation 失败: {e}"
            time.sleep(1)
            continue
        try:
            ok = DouyinAPI.send_msg(auth, conversation_id, short_id, ticket, content)
        except Exception as e:
            logger.warning(f"send_msg 失败(第{attempt}次) uid={user_id}: {e}")
            if attempt == max_retry:
                return False, f"send_msg 失败: {e}"
            time.sleep(1)
            continue
        if ok:
            return True, "ok"
        logger.warning(f"send_msg 返回 False(第{attempt}次) uid={user_id}")
        if attempt == max_retry:
            return False, "send_msg 返回 False"
    return False, "未知失败"


def send_by_secuid(auth, sec_uid, content, max_retry=1):
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


def send_target(auth, target, content):
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
        logger.warning(f"[私信] 发送失败「{nickname}」: {reason}")
    return ok, reason
