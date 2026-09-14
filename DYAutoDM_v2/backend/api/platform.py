# coding=utf-8
"""平台内容路由 —— 把基座已有的平台能力接成 HTTP 路由（对标 better-douyin 的内容面）。

## 设计来源

对标 better-douyin 的 `src/api/client_content.rs` / `client_user.rs` /
`client_collection.rs` / `client_relations.rs`，把「内容浏览」能力暴露给前端。

## 关键事实（实测）

本项目**基座早已具备**这些能力（`dy_apis/douyin_api.py` 40+ 方法），
此前只是没有被接成路由与页面 —— 本次补上「最后一公里」。

## 风控约束（**铁律，不得违反**）

1. **复用账号既有凭证被动签名**（`load_env` 读 .env → DouyinAPI 自带
   msToken / a_bogus / bd-ticket-guard），与「手动网页浏览」同源同指纹；
2. **绝不批量补查用户信息**（昵称风控红线）——响应里自带的 nickname/uid 直接用；
   `get_im_user_info` / `bulk_user_info` 类方法**绝不调用**；
3. **主动请求须可节流**：本路由是「用户显式动作触发」（打开页面/点赞），
   不做后台轮询；前端须避免高频自动刷新。
4. DouyinAPI 是**同步 requests** 实现 → 一律 `asyncio.to_thread` 包裹，
   避免阻塞事件循环（与 `api/crawl.py` 一致）。
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException
from loguru import logger
from pydantic import BaseModel

router = APIRouter()


# ---------------------------------------------------------------------------
# 公共依赖
# ---------------------------------------------------------------------------

def _auth_for(account: str):
    """加载指定账号凭证 → auth（与直播/采集链路同源）。

    只读 .env 里的凭证字段，不做任何网络请求。
    """
    if not account:
        raise HTTPException(400, "缺少 account")
    try:
        from auto_dm import accounts as acct_core
        env_path = acct_core.env_path_of(account)
        if not env_path:
            raise HTTPException(404, f"账号 {account} 未登记")
        import utils.common_util as common_util
        return common_util.load_env(env_path)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(503, f"加载账号 {account} 凭证失败: {type(e).__name__}")


def _api():
    from dy_apis.douyin_api import DouyinAPI
    return DouyinAPI


def _pick_aweme(w: dict) -> dict:
    """裁剪作品字段（只保留前端需要的，避免把巨量原始 JSON 透传）。"""
    if not isinstance(w, dict):
        return {}
    author = w.get("author") or {}
    stats = w.get("statistics") or {}
    video = w.get("video") or {}
    cover = (video.get("cover") or {})
    urls = cover.get("url_list") or []
    return {
        "aweme_id": str(w.get("aweme_id") or ""),
        "desc": (w.get("desc") or "")[:200],
        "create_time": w.get("create_time") or 0,
        "cover": urls[0] if urls else "",
        "author_uid": str(author.get("uid") or ""),
        "author_sec_uid": author.get("sec_uid") or "",
        "author_nickname": author.get("nickname") or "",   # 响应自带，不补查
        "digg_count": stats.get("digg_count") or 0,
        "comment_count": stats.get("comment_count") or 0,
        "share_count": stats.get("share_count") or 0,
        "play_count": stats.get("play_count") or 0,
        "duration": video.get("duration") or 0,
    }


def _pick_user(u: dict) -> dict:
    """裁剪用户字段。"""
    if not isinstance(u, dict):
        return {}
    avatar = u.get("avatar_thumb") or {}
    urls = avatar.get("url_list") or []
    return {
        "uid": str(u.get("uid") or ""),
        "sec_uid": u.get("sec_uid") or "",
        "nickname": u.get("nickname") or "",
        "signature": (u.get("signature") or "")[:200],
        "avatar": urls[0] if urls else "",
        "follower_count": u.get("follower_count") or 0,
        "following_count": u.get("following_count") or 0,
        "aweme_count": u.get("aweme_count") or 0,
        "total_favorited": u.get("total_favorited") or 0,
    }


# ---------------------------------------------------------------------------
# 请求模型
# ---------------------------------------------------------------------------

class FeedReq(BaseModel):
    account: str
    count: int = 20


class UserWorksReq(BaseModel):
    account: str
    user_url: str                 # 用户主页 URL 或 sec_uid
    limit: int = 50


class UserInfoReq(BaseModel):
    account: str
    user_url: str


class SearchReq(BaseModel):
    account: str
    query: str
    kind: str = "video"           # video | user
    num: int = 20


class CollectListReq(BaseModel):
    account: str


class LikedReq(BaseModel):
    account: str
    sec_id: str = ""              # 空=查自己（由后端取 my_sec_uid）
    num: int = 18


class RelationReq(BaseModel):
    account: str
    user_id: str
    sec_id: str
    count: int = 20
    kind: str = "follower"        # follower | following


class NoticeReq(BaseModel):
    account: str
    count: int = 10
    group: str = "700"            # 700=全部通知组


class CommentsReq(BaseModel):
    account: str
    url: str
    limit: int = 20


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------

@router.post("/feed")
async def get_feed(req: FeedReq) -> dict[str, Any]:
    """推荐流。对应基座 `get_feed`。"""
    auth = _auth_for(req.account)
    api = _api()
    try:
        # 基座真实签名：get_feed(auth, count='20', refresh_index='2')
        raw = await asyncio.to_thread(api.get_feed, auth,
                                      str(max(1, min(req.count, 50))), "2")
    except Exception as e:  # noqa: BLE001
        logger.warning("PLT-001", f"推荐流获取失败: {type(e).__name__}")
        raise HTTPException(502, f"推荐流获取失败: {type(e).__name__}")
    items = raw.get("aweme_list") if isinstance(raw, dict) else []
    return {"ok": True, "items": [_pick_aweme(w) for w in (items or [])],
            "has_more": bool(raw.get("has_more")) if isinstance(raw, dict) else False}


@router.post("/user/works")
async def user_works(req: UserWorksReq) -> dict[str, Any]:
    """用户作品列表。对应基座 `get_user_all_work_info`（自带翻页）。"""
    auth = _auth_for(req.account)
    api = _api()
    try:
        items = await asyncio.to_thread(api.get_user_all_work_info, auth, req.user_url)
    except Exception as e:  # noqa: BLE001
        logger.warning("PLT-002", f"作品列表获取失败: {type(e).__name__}")
        raise HTTPException(502, f"作品列表获取失败: {type(e).__name__}")
    out = [_pick_aweme(w) for w in (items or [])]
    return {"ok": True, "items": out[: max(1, req.limit)], "total": len(out)}


@router.post("/user/info")
async def user_info(req: UserInfoReq) -> dict[str, Any]:
    """用户资料。对应基座 `get_user_info`。

    ⚠️ 响应自带 nickname/uid —— **不补查、不批量**（昵称风控红线）。
    """
    auth = _auth_for(req.account)
    api = _api()
    try:
        raw = await asyncio.to_thread(api.get_user_info, auth, req.user_url)
    except Exception as e:  # noqa: BLE001
        logger.warning("PLT-003", f"用户资料获取失败: {type(e).__name__}")
        raise HTTPException(502, f"用户资料获取失败: {type(e).__name__}")
    u = (raw or {}).get("user") if isinstance(raw, dict) else None
    return {"ok": True, "user": _pick_user(u or {})}


@router.post("/search")
async def search(req: SearchReq) -> dict[str, Any]:
    """搜索作品 / 用户。对应基座 `search_some_general_work` / `search_some_user`。"""
    auth = _auth_for(req.account)
    api = _api()
    num = max(1, min(req.num, 50))
    try:
        if req.kind == "user":
            users = await asyncio.to_thread(api.search_some_user, auth, req.query, num)
            return {"ok": True, "kind": "user",
                    "items": [_pick_user(u) for u in (users or [])]}
        works = await asyncio.to_thread(api.search_some_general_work, auth, req.query, num, "0", "0")
        return {"ok": True, "kind": "video",
                "items": [_pick_aweme(w) for w in (works or [])]}
    except Exception as e:  # noqa: BLE001
        logger.warning("PLT-004", f"搜索失败: {type(e).__name__}")
        raise HTTPException(502, f"搜索失败: {type(e).__name__}")


@router.post("/collected")
async def collected(req: CollectListReq) -> dict[str, Any]:
    """收藏夹列表。对应基座 `get_collect_list`。"""
    auth = _auth_for(req.account)
    api = _api()
    try:
        raw = await asyncio.to_thread(api.get_collect_list, auth)
    except Exception as e:  # noqa: BLE001
        logger.warning("PLT-005", f"收藏夹获取失败: {type(e).__name__}")
        raise HTTPException(502, f"收藏夹获取失败: {type(e).__name__}")
    # 基座返回结构可能是 {collects_list:[…]} 或 list，两种都兼容
    items = raw.get("collects_list") if isinstance(raw, dict) else raw
    out = []
    for c in (items or []):
        if not isinstance(c, dict):
            continue
        out.append({
            "collects_id": str(c.get("collects_id") or ""),
            "name": c.get("collects_name") or c.get("name") or "",
            "count": c.get("total_number") or c.get("count") or 0,
        })
    return {"ok": True, "items": out}


@router.post("/liked")
async def liked(req: LikedReq) -> dict[str, Any]:
    """点赞视频列表。对应基座 `get_user_favorite`（sec_id 空则查自己）。"""
    auth = _auth_for(req.account)
    api = _api()
    sec = req.sec_id
    if not sec:
        try:
            sec = await asyncio.to_thread(api.get_my_sec_uid, auth)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"获取自身 sec_uid 失败: {type(e).__name__}")
    try:
        raw = await asyncio.to_thread(api.get_user_favorite, auth, sec, "0", str(max(1, min(req.num, 50))))
    except Exception as e:  # noqa: BLE001
        logger.warning("PLT-006", f"点赞列表获取失败: {type(e).__name__}")
        raise HTTPException(502, f"点赞列表获取失败: {type(e).__name__}")
    items = raw.get("aweme_list") if isinstance(raw, dict) else []
    return {"ok": True, "items": [_pick_aweme(w) for w in (items or [])],
            "has_more": bool(raw.get("has_more")) if isinstance(raw, dict) else False}


@router.post("/relation/list")
async def relation_list(req: RelationReq) -> dict[str, Any]:
    """关注 / 粉丝列表。对应基座 follower / following。"""
    auth = _auth_for(req.account)
    api = _api()
    count = str(max(1, min(req.count, 50)))
    try:
        if req.kind == "following":
            raw = await asyncio.to_thread(api.get_user_following_list, auth,
                                          req.user_id, req.sec_id, "0", count)
        else:
            raw = await asyncio.to_thread(api.get_user_follower_list, auth,
                                          req.user_id, req.sec_id, "0", count)
    except Exception as e:  # noqa: BLE001
        logger.warning("PLT-007", f"关系列表获取失败: {type(e).__name__}")
        raise HTTPException(502, f"关系列表获取失败: {type(e).__name__}")
    items = raw.get("followers") or raw.get("followings") or []
    return {"ok": True, "items": [_pick_user(u) for u in (items or [])],
            "total": raw.get("total") if isinstance(raw, dict) else None}


@router.post("/notice/list")
async def notice_list(req: NoticeReq) -> dict[str, Any]:
    """站内通知。对应基座 `get_notice_list`。"""
    auth = _auth_for(req.account)
    api = _api()
    try:
        raw = await asyncio.to_thread(api.get_notice_list, auth, "0", "0",
                                      str(max(1, min(req.count, 50))), req.group)
    except Exception as e:  # noqa: BLE001
        logger.warning("PLT-008", f"站内通知获取失败: {type(e).__name__}")
        raise HTTPException(502, f"站内通知获取失败: {type(e).__name__}")
    items = raw.get("notice_list") if isinstance(raw, dict) else []
    out = []
    for n in (items or []):
        if not isinstance(n, dict):
            continue
        out.append({
            "notice_id": str(n.get("notice_id") or n.get("nid_str") or ""),
            "type": n.get("type") or n.get("type_label") or "",
            "content": (n.get("content") or n.get("text") or "")[:300],
            "create_time": n.get("create_time") or 0,
            "is_read": bool(n.get("is_read") or n.get("has_read")),
        })
    return {"ok": True, "items": out,
            "unread": raw.get("unread_count") if isinstance(raw, dict) else None}


@router.post("/comments")
async def comments(req: CommentsReq) -> dict[str, Any]:
    """作品评论（一级）。对应基座 `get_work_out_comment`。"""
    auth = _auth_for(req.account)
    api = _api()
    try:
        raw = await asyncio.to_thread(api.get_work_out_comment, auth, req.url, "0")
    except Exception as e:  # noqa: BLE001
        logger.warning("PLT-009", f"评论获取失败: {type(e).__name__}")
        raise HTTPException(502, f"评论获取失败: {type(e).__name__}")
    items = raw.get("comments") if isinstance(raw, dict) else []
    out = []
    for c in (items or [])[: max(1, req.limit)]:
        if not isinstance(c, dict):
            continue
        u = c.get("user") or {}
        out.append({
            "cid": str(c.get("cid") or ""),
            "text": (c.get("text") or "")[:500],
            "digg_count": c.get("digg_count") or 0,
            "create_time": c.get("create_time") or 0,
            "reply_comment_total": c.get("reply_comment_total") or 0,
            "user_nickname": u.get("nickname") or "",     # 自带，不补查
            "user_uid": str(u.get("uid") or ""),
            "user_sec_uid": u.get("sec_uid") or "",
        })
    return {"ok": True, "items": out,
            "has_more": bool(raw.get("has_more")) if isinstance(raw, dict) else False}


# ---------------------------------------------------------------------------
# 写操作（点赞/收藏/关注）—— 单独分组，前端须显式确认
# ---------------------------------------------------------------------------

class DiggReq(BaseModel):
    account: str
    aweme_id: str
    action: str = "1"             # 1=点赞 0=取消


class CollectReq(BaseModel):
    account: str
    aweme_id: str
    action: str = "1"             # 1=收藏 0=取消


class FollowReq(BaseModel):
    account: str
    user_id: str
    sec_id: str = ""
    action: str = "1"             # 1=关注 0=取消


@router.post("/action/digg")
async def action_digg(req: DiggReq) -> dict[str, Any]:
    """点赞 / 取消点赞。对应基座 commit/item/digg 链路。

    ⚠️ 写操作：前端必须由用户显式点击触发，不得自动批量执行。
    """
    auth = _auth_for(req.account)
    api = _api()
    try:
        # 基座真实方法：digg(auth, aweme_id, digg_type) -> bool
        #   digg_type: '1'=点赞 '0'=取消
        ok = await asyncio.to_thread(api.digg, auth, req.aweme_id, req.action)
    except Exception as e:  # noqa: BLE001
        logger.warning("PLT-010", f"点赞失败: {type(e).__name__}")
        raise HTTPException(502, f"点赞失败: {type(e).__name__}")
    return {"ok": bool(ok), "action": req.action}


@router.post("/action/collect")
async def action_collect(req: CollectReq) -> dict[str, Any]:
    """收藏 / 取消收藏。对应基座 `collect_aweme`。"""
    auth = _auth_for(req.account)
    api = _api()
    try:
        raw = await asyncio.to_thread(api.collect_aweme, auth, req.aweme_id, req.action)
    except Exception as e:  # noqa: BLE001
        logger.warning("PLT-011", f"收藏失败: {type(e).__name__}")
        raise HTTPException(502, f"收藏失败: {type(e).__name__}")
    return {"ok": True, "raw_status": (raw or {}).get("status_code") if isinstance(raw, dict) else None}


@router.post("/action/follow")
async def action_follow(req: FollowReq) -> dict[str, Any]:
    """关注 / 取关。"""
    auth = _auth_for(req.account)
    api = _api()
    # 实测：基座（dy_apis/douyin_api.py）**没有关注写方法**
    #   —— 只有 get_user_follower_list / get_user_following_list（读）。
    #   故此处**显式返回 501**，绝不假装成功（"流程走完 != 结果正确"）。
    fn = getattr(api, "commit_follow", None) or getattr(api, "follow_user", None)
    if fn is None:
        logger.info("PLT-012", "关注写操作未实现：基座无 follow 方法")
        raise HTTPException(501, "关注写操作尚未实现：基座未提供 follow 方法"
                                 "（如需启用，需先在 dy_apis 补 commit/follow/user 链路）")
    try:
        raw = await asyncio.to_thread(fn, auth, req.user_id, req.sec_id, req.action)
    except Exception as e:  # noqa: BLE001
        logger.warning("PLT-012", f"关注失败: {type(e).__name__}")
        raise HTTPException(502, f"关注失败: {type(e).__name__}")
    return {"ok": True, "raw_status": (raw or {}).get("status_code") if isinstance(raw, dict) else None}
