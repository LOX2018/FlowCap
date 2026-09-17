# coding=utf-8
"""基座功能统一封装层。

把 ccv-cat/Douyin_Spider 基座 dy_apis.douyin_api.DouyinAPI 的全部公开能力
封装成简洁、带异常处理、返回结构化结果的函数，供 GUI / 命令行统一调用。

所有函数都接收已 prepare 的 auth（含 web_protect/keys 签名或普通 cookie）。
大部分 www.douyin.com 接口需要 msToken/a_bogus（基座自动处理），仅私信走 imapi。
"""

from loguru import logger
from dy_apis.douyin_api import DouyinAPI


def _safe(fn_name, auth, *args, **kwargs):
    try:
        return {"ok": True, "data": getattr(DouyinAPI, fn_name)(auth, *args, **kwargs)}
    except Exception as e:
        logger.warning(f"[SYS-007] " + f"[feature] {fn_name} 失败: {e}")
        return {"ok": False, "error": str(e)}


# ---------------- 用户 ----------------
def user_info(auth, user_url):
    return _safe("get_user_info", auth, user_url)


def user_works(auth, user_url, max_cursor="0"):
    return _safe("get_user_work_info", auth, user_url, max_cursor)


def user_all_works(auth, user_url):
    return _safe("get_user_all_work_info", auth, user_url)


def user_favorite(auth, sec_id, max_cursor="0", num="18"):
    return _safe("get_user_favorite", auth, sec_id, max_cursor, num)


# ---------------- 作品 ----------------
def work_info(auth, url):
    return _safe("get_work_info", auth, url)


# ---------------- 评论 ----------------
def work_comments(auth, url, cursor="0"):
    return _safe("get_work_out_comment", auth, url, cursor)


def work_all_comments(auth, url):
    return _safe("get_work_all_out_comment", auth, url)


def publish_comment(auth, aweme_id, content, reply_id=""):
    """发布评论 / 回复（reply_id 为空则是发布到作品，否则回复某条评论）。"""
    return _safe("publish_comment", auth, aweme_id, content, reply_id)


# ---------------- 搜索 ----------------
def search_user(auth, query, num=20):
    return _safe("search_some_user", auth, query, num)


def search_work(auth, query, num=16, sort_type="0", publish_time="0"):
    return _safe("search_some_general_work", auth, query, num, sort_type, publish_time)


def search_live(auth, query, num=20):
    return _safe("search_some_live", auth, query, num)


# ---------------- 粉丝 / 关注 ----------------
def follower_list(auth, user_id, sec_id, num=20):
    return _safe("get_some_user_follower_list", auth, user_id, sec_id, num)


def following_list(auth, user_id, sec_id, num=20):
    return _safe("get_some_user_following_list", auth, user_id, sec_id, num)


# ---------------- 直播 ----------------
def live_info(auth, live_id):
    return _safe("get_live_info", auth, live_id)


def live_production(auth, url, room_id, author_id, offset="0"):
    return _safe("get_live_production", auth, url, room_id, author_id, offset)


def live_all_production(auth, url):
    return _safe("get_all_live_production", auth, url)


def live_digg(auth, room_id, count="1"):
    """直播间点赞。"""
    return _safe("diggLiveRoom", auth, room_id, count)


def live_send_msg(auth, room_id, content):
    """直播间发弹幕。"""
    return _safe("sendMsgInRoom", auth, room_id, content)


# ---------------- 互动（点赞 / 收藏）----------------
def digg_work(auth, aweme_id, digg_type="1"):
    return _safe("digg", auth, aweme_id, digg_type)


def collect_work(auth, aweme_id, action="1"):
    return _safe("collect_aweme", auth, aweme_id, action)


def collect_list(auth):
    return _safe("get_collect_list", auth)


# ---------------- 消息 / 推荐 ----------------
def notice_list(auth, num=20):
    return _safe("get_some_notice_list", auth, num)


def feed(auth, count="20"):
    return _safe("get_feed", auth, count)


# ---------------- 私信 ----------------
def conversation_list(auth, to_user_id, conversation_short_id):
    # 2026-09-17 修补（OCR 审查 CRITICAL）：`get_conversation_list`
    # 的定义是 `(auth, conversation_short_id=0, **kwargs)` —— auth 之后**只接受
    # 一个**位置参数（分页游标）。原调用多传了 to_user_id，会抛
    # `TypeError: too many positional arguments`。
    # 若目标是「按对端 uid 取会话」，用的是别的方法；此处只透传游标。
    if to_user_id:
        logger.warning(f"[FEAT-001] " + f"[features] conversation_list 收到 to_user_id={to_user_id}，"
            f"但底层 get_conversation_list 只按 conversation_short_id 分页，已忽略")
    return _safe("get_conversation_list", auth, conversation_short_id)
