# coding=utf-8
"""平台内容路由 —— 把基座已有的平台能力接成 HTTP 路由（对标 better-douyin 的内容面）。

## 设计来源

对标 better-douyin 的 `src/api/client_content.rs` / `client_user.rs` /
`client_collection.rs` / `client_relations.rs`，把「内容浏览」能力暴露给前端。

## 关键事实（实测）

本项目**基座早已具备**这些能力（`dy_apis/douyin_api.py` 40+ 方法），
此前只是没有被接成路由与页面 —— 本次补上「最后一公里」。

## 风控约束（**本分支 `design/better-douyin` 已全解除**）

> ⚠️ **本分支规则见 `docs/BRANCH_RULES_design_better_douyin.md`**。
> 用户 2026-09-14 明确授权「**全解除**」主分支铁律，采用照源项目（better-douyin）的接口方式。
> 下文原约束（保留作历史留痕）**在本分支不再生效**。

**本分支现行做法**（对齐源项目实测情报 `docs/reverse_interface_spec.md`）：
1. 凭证复用 + 主动签名请求（`msToken` / `a_bogus` / `bd-ticket-guard`）——照源项目；
2. **允许主动批量查询用户信息**（源项目直接请求 `/aweme/v1/web/im/user/info/`）：
   二通道 —— ① BCC 容器内 fetch（`/user_info`、`/user_info_by_uids`）；
   ② 纯 HTTP 直连（`dy_apis.get_im_user_info`）。
3. 频率控制照源项目模型：`auto_max_actions_per_run` + 扫描间隔 + 互动门槛，
   **不用**主分支的全局发送闸门；
4. DouyinAPI 是**同步 requests** 实现 → 一律 `asyncio.to_thread` 包裹，
   避免阻塞事件循环（与 `api/crawl.py` 一致）。

---

### 历史留痕：主分支铁律原文（本分支不生效）
1. ~~复用账号既有凭证被动签名~~；
2. ~~绝不批量补查用户信息；`get_im_user_info` / `bulk_user_info` 类方法绝不调用~~；
3. ~~主动请求须可节流、不做后台轮询~~；
"""
from __future__ import annotations

import asyncio
import json
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

    ## 2026-09-14 修复（本分支实测定位）

    原实现用 `common_util.load_env(env_path)`，实测其返回的 `.cookie` 恒为**空 dict**
    （真实账号下：`len(auth.cookie) == 0`），而 `DYLoginApi._load_auth_from_env`
    同一账号返回 **68 个 cookie**。后果：**所有 platform 端点发出的请求都不带凭证**
    → 服务端返回 `"url doesn't match"` / `sc=5`，表现为"接口调用失败"。

    ⇒ 改用与探活/直播链路**同一加载器** `DYLoginApi._load_auth_from_env`（实测有效）。
    """
    if not account:
        raise HTTPException(400, "缺少 account")
    try:
        from auto_dm import accounts as acct_core
        env_path = acct_core.env_path_of(account)
        if not env_path:
            raise HTTPException(404, f"账号 {account} 未登记")
        from dy_apis.login_api import DYLoginApi
        auth = DYLoginApi._load_auth_from_env(env_path)
        if not auth or not getattr(auth, "cookie", None):
            raise HTTPException(503, f"账号 {account} 凭证为空（未登录或 .env 缺字段）")
        return auth
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(503, f"加载账号 {account} 凭证失败: {type(e).__name__}")


def _api():
    from dy_apis.douyin_api import DouyinAPI
    return DouyinAPI



def _trim_video(video: dict) -> dict:
    """裁剪 video 子树：只保留前 2 档 bit_rate（播放所需的 play_addr），
    去掉 25 档全量，避免列表接口单作品 90KB 的传输开销。

    2026-09-15 性能优化（实测 video 全量 92779 字符 → 前 2 档 20270 字符）。
    """
    if not isinstance(video, dict):
        return video
    out = dict(video)
    brl = out.get("bit_rate")
    if isinstance(brl, list) and len(brl) > 2:
        out["bit_rate"] = brl[:2]
    return out


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
        # ★ 播放所需最小字段（2026-09-14 实测：列表对象自带播放地址，
        #   但整对象 125 键过臃肿；此处只带 downloader/media_request 提取所需的
        #   `video` 子树 + 封面/时长，供前端点击播放时原样回传给 /media/resolve）
        "media": {
            "aweme_id": str(w.get("aweme_id") or ""),
            "desc": (w.get("desc") or "")[:200],
            "duration": video.get("duration") or 0,
            # 2026-09-15 性能优化：只保留前 2 档 bit_rate（高清+标清，播放足够）。
            # 全量 video 子树含 25 档×3 url ≈ 90KB/作品，一页 10 个 ≈ 900KB，
            # 裁剪后仅 ≈20KB/作品（减到 1/5）。media_request 只需 play_addr，
            # 前 2 档已覆盖 hd/h264 两档清晰度。
            "video": _trim_video(video),
            "images": w.get("images") or None,
            "author": {"nickname": author.get("nickname") or "",
                       "sec_uid": author.get("sec_uid") or ""},
        },
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


class UsersInfoReq(BaseModel):
    """★ 本分支新增（全解除）：主动批量查询用户资料（昵称/头像）。

    照源项目 better-douyin 的做法：直接请求 `/aweme/v1/web/im/user/info/`。
    主分支铁律原为「绝不调用」（昵称风控红线），本分支已按用户授权解除。
    """
    account: str
    uids: list[str] = []            # 数字 UID（会话 peer_uid，首包解析 100% 可靠）
    sec_uids: list[str] = []        # sec_uid（用户主页 URL 尾段）
    use_bcc: bool = True            # True=BCC 容器内 fetch；False=纯 HTTP 直连


class SearchReq(BaseModel):
    account: str
    query: str
    kind: str = "video"           # video | user
    num: int = 20


class CollectListReq(BaseModel):
    account: str
    cursor: str = "0"
    count: int = 20


class CollectionItemsReq(BaseModel):
    """★ 本分支新增（照源项目）：收藏夹内的作品。"""
    account: str
    max_cursor: str = "0"
    count: int = 18


class MixListReq(BaseModel):
    """★ 本分支新增（照源项目）：收藏的合集列表。"""
    account: str
    cursor: str = "0"
    count: int = 20


class SeriesAwemeReq(BaseModel):
    """★ 本分支新增（照源项目）：合集内作品。"""
    account: str
    series_id: str
    cursor: str = "0"
    count: int = 20


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
    # ★ 2026-09-15：`get_feed` 已改用源项目接口 `/aweme/v1/web/tab/feed/`
    #   （源项目方案），返回**标准 `aweme_list`** —— 优先直接读它。
    #   （保留 cards 兼容：万一基座回退到老接口 `/module/feed/` 仍可工作，
    #    该路径下 `card["aweme"]` 是 JSON 字符串，需二次解析。）
    items: list[dict] = []
    if isinstance(raw, dict):
        if isinstance(raw.get("aweme_list"), list):
            items = raw["aweme_list"]
        if not items:
            for card in (raw.get("cards") or []):
                if not isinstance(card, dict):
                    continue
                aw = card.get("aweme")
                if isinstance(aw, str):
                    try:
                        aw = json.loads(aw)
                    except Exception:  # noqa: BLE001
                        aw = None
                if isinstance(aw, dict):
                    items.append(aw)
    return {"ok": True, "items": [_pick_aweme(w) for w in items],
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
    if not out:
        # 2026-09-15：实测 `aweme/post` 偶发「只返 sc=0 无 aweme_list」——
        # 平台对同一 sec_uid 短时多次请求会限流（非凭证失效，推荐流/收藏仍正常）。
        logger.info("PLT-002", f"用户作品返回空（平台限流或该用户无公开作品）: sec={req.user_url[-16:]}")
    return {"ok": True, "items": out[: max(1, req.limit)], "total": len(out)}


@router.post("/user/info")
async def user_info(req: UserInfoReq) -> dict[str, Any]:
    """用户资料。对应基座 `get_user_info`。

    本分支（全解除）：响应自带的 nickname/uid 直接用；如需补查可走
    `/user/info/batch`（主动批量查询，见该端点）。
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


@router.post("/user/info/batch")
async def users_info_batch(req: UsersInfoReq) -> dict[str, Any]:
    """★ 本分支新增（全解除）：**主动批量查询**用户昵称/头像。

    照源项目 better-douyin 的做法 —— 它直接请求 `/aweme/v1/web/im/user/info/`。
    主分支铁律原为「`get_im_user_info`/`bulk_user_info` 绝不调用」（昵称风控红线），
    本分支按用户 2026-09-14 授权「全解除」已解禁。

    双通道：
      ① `use_bcc=True`（默认）→ 经 BCC 容器内 fetch（沿用账号常驻浏览器环境，
         与源项目的"浏览器内 fetch"同思路，且天然带 origin/credentials）；
      ② `use_bcc=False` → 纯 HTTP 直连（`dy_apis.get_im_user_info`，照源项目
         `reqwest` 直连的方式，需自带 msToken/a_bogus 签名）。

    返回 `{ok, data: {uid|sec_uid: {nickname, avatar, sec_uid, uid}}}`。
    """
    if not req.uids and not req.sec_uids:
        raise HTTPException(400, "uids 与 sec_uids 至少提供一个")
    _auth_for(req.account)  # 校验账号已登记（BCC 通道也需凭证）

    # ── 通道 ①：BCC 容器内 fetch ──
    if req.use_bcc:
        try:
            from api.messages import _bcc_url  # 复用既有 BCC 寻址（含 ensure_bcc 拉起）
            import urllib.request as _ur

            async def _post(path: str, payload: dict) -> dict:
                """POST 到该账号 BCC 的 path（_bcc_url 返回完整 URL）。"""
                url = await asyncio.to_thread(_bcc_url, req.account, path)
                body = json.dumps(payload).encode()
                r = await asyncio.to_thread(
                    _ur.urlopen,
                    _ur.Request(url, data=body,
                                headers={"Content-Type": "application/json"}),
                    60)
                return json.loads(r.read().decode("utf-8", "replace"))

            out: dict[str, Any] = {}
            # 数字 uid 优先（首包 peer_uid 100% 可靠）
            if req.uids:
                j = await _post("/user_info_by_uids", {"uids": req.uids})
                out.update(j.get("data") or {})
            if req.sec_uids:
                j = await _post("/user_info", {"sec_uids": req.sec_uids})
                out.update(j.get("data") or {})
            return {"ok": True, "channel": "bcc", "data": out,
                    "count": len(out)}
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning("PLT-020", f"BCC 批量查用户失败: {type(e).__name__}")
            # 不静默降级：明确告知哪条通道失败（本分支允许第二条通道重试）
            raise HTTPException(502, f"BCC 批量查用户失败: {type(e).__name__}")

    # ── 通道 ②：纯 HTTP 直连（照源项目 reqwest 直连）──
    auth = _auth_for(req.account)
    out: dict[str, Any] = {}
    try:
        if req.sec_uids:
            # ★ 实测结论（docs/reverse_interface_spec.md §三）：该接口
            #   ① 只认 **POST**；② 参数名是 **sec_user_ids**；③ 值必须是
            #   **JSON 数组字符串**（裸串会得 sc=5「参数不合法」）。
            #   实测：POST sec_user_ids=["MS4wLjABAAAA..."] → sc=0 且返回
            #   {nickname, uid, avatar_small, sec_uid}（uid 可与 peer_uid 关联）。
            got = await asyncio.to_thread(_im_user_info_by_sec, auth, req.sec_uids)
            out.update(got)
        if req.uids:
            # 数字 uid 走基座既有方法（GET + to_user_id，作兜底）
            api = _api()
            for uid in req.uids:
                info = await asyncio.to_thread(api.get_im_user_info, auth, str(uid))
                if info:
                    out[str(uid)] = info
        return {"ok": True, "channel": "http", "data": out, "count": len(out)}
    except Exception as e:  # noqa: BLE001
        logger.warning("PLT-021", f"HTTP 批量查用户失败: {type(e).__name__}")
        raise HTTPException(502, f"HTTP 批量查用户失败: {type(e).__name__}")


def _im_user_info_by_sec(auth, sec_uids: list[str]) -> dict[str, Any]:
    """★ 本分支新增：用 sec_uid 主动查用户资料（**实测有效的正确姿势**）。

    实测（2026-09-14，真实账号 + 真实 sec_uid）：
      · `POST /aweme/v1/web/im/user/info/`，body `sec_user_ids=<JSON数组串>`
        → `status_code=0`，返回 `data: [{nickname, uid, sec_uid, avatar_small, ...}]`
      · 同接口 GET + `to_user_id` → `"url doesn't match"`（无效）
      · `sec_user_ids` 传裸串（非 JSON 数组）→ `sc=5「参数不合法」`（无效）

    照源项目 better-douyin 的做法（它直接请求该接口，见 `reverse_interface_spec.md`）。
    返回 `{sec_uid: {nickname, avatar, uid, sec_uid}}`。
    """
    import requests
    from builder.header import HeaderBuilder, HeaderType

    api = "/aweme/v1/web/im/user/info/"
    h = HeaderBuilder().build(HeaderType.POST)
    headers = h.get()
    headers["content-type"] = "application/x-www-form-urlencoded; charset=UTF-8"
    headers["referer"] = "https://www.douyin.com/"

    out: dict[str, Any] = {}
    batch = 20
    for i in range(0, len(sec_uids), batch):
        chunk = [s for s in sec_uids[i:i + batch] if s]
        if not chunk:
            continue
        r = requests.post(
            f"{getattr(_api(), 'douyin_url', 'https://www.douyin.com')}{api}",
            headers=headers, cookies=auth.cookie,
            data={"sec_user_ids": json.dumps(chunk)},
            verify=False, timeout=12,
        )
        j = r.json()
        if j.get("status_code") != 0:
            logger.warning("PLT-022",
                           f"im/user/info sc={j.get('status_code')} msg={j.get('status_msg')}")
            continue
        for u in (j.get("data") or []):
            sec = u.get("sec_uid") or ""
            avt = (u.get("avatar_small") or {}).get("url_list") or []
            if not avt:
                avt = (u.get("avatar_thumb") or {}).get("url_list") or []
            key = sec or str(u.get("uid") or "")
            if not key:
                continue
            out[key] = {
                "nickname": u.get("nickname") or "",
                "avatar": avt[0] if avt else "",
                "uid": str(u.get("uid") or ""),
                "sec_uid": sec,
            }
    return out


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
        # ★ 2026-09-15：视频搜索改用**源项目方案** `/general/search/stream/`（实测 10 条、
        #   真实作者可读）；失败则回落到原 `search_some_general_work`（老接口），保证可用。
        works = None
        try:
            stream = await asyncio.to_thread(api.search_stream, auth, req.query, "0", str(num))
            works = (stream or {}).get("aweme_list") or []
        except Exception as e:  # noqa: BLE001
            logger.warning("PLT-009", f"源项目搜索流失败，回落旧接口: {type(e).__name__}")
            works = None
        if not works:
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
        # ★ 2026-09-15 实测：平台侧对 `/aweme/v1/web/aweme/favorite/` 返回
        #   **HTTP 200 但响应体 0 字节**（绕过本项目封装直接构造原始请求复现同样结果），
        #   解析时表现为 JSONDecodeError。源项目逆向情报中「点赞列表」用的也是该接口
        #   ⇒ 属**平台侧行为**，非本项目适配错误。
        #   故此处**不抛 502**（避免前端弹"请求失败"误导用户），
        #   而是返回空列表 + `unavailable` 标记，让 UI 能给出准确说明。
        logger.warning("PLT-006", f"点赞列表获取失败（平台侧常返空）: {type(e).__name__}")
        return {"ok": True, "items": [], "has_more": False,
                "unavailable": True,
                "reason": f"平台侧暂不可用（{type(e).__name__}）"}
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
    # ★ 2026-09-14 实测修正：新版接口把数据放在 **`notice_list_v2`**，
    #   `notice_list` 恒为空数组。原实现只读 `notice_list` ⇒ 实测恒 0 条。
    #   取法：优先 v2，为空再回落到旧键（兼容两端）。
    items = None
    if isinstance(raw, dict):
        items = raw.get("notice_list_v2")
        if not items:
            items = raw.get("notice_list")
    out = []
    for n in (items or []):
        if not isinstance(n, dict):
            continue
        out.append({
            "notice_id": str(n.get("notice_id") or n.get("nid_str")
                             or n.get("nid") or ""),
            "type": n.get("type") or n.get("type_label") or "",
            # ★ 2026-09-14 实测修正：`notice_list_v2` 没有 `content` 字段，
            #   文案在 `digg.aweme.desc`；作者昵称在 `digg.aweme.author.nickname`。
            #   原实现只读 `content` ⇒ v2 下**全部通知显示为空**。
            "content": (
                n.get("content") or n.get("text")
                or ((n.get("digg") or {}).get("aweme") or {}).get("desc")
                or ""
            )[:300],
            "nickname": (
                n.get("nickname")
                or ((((n.get("digg") or {}).get("aweme") or {}).get("author") or {})
                    .get("nickname"))
                or ""
            ),
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


# ===========================================================================
# 合集群（★ 本分支新增，照源项目 better-douyin）
# ===========================================================================
# 源项目实测接口（docs/reverse_interface_spec.md §1.2）：
#   /aweme/v1/web/aweme/listcollection/     收藏夹内的作品（实测 sc=0 ✅）
#   /aweme/v1/web/mix/listcollection/       收藏的合集列表（实测 sc=0 ✅）
#   /aweme/v1/web/series/aweme/             合集内作品
# 基座原本无这三个方法，本分支补齐（逻辑层复现源项目功能）。


@router.post("/collection/items")
async def collection_items(req: CollectionItemsReq) -> dict[str, Any]:
    """收藏夹内的作品列表。对应基座 `get_aweme_list_collection`。

    实测（2026-09-14 真实账号）：`status_code=0`，返回 `aweme_list`。
    """
    auth = _auth_for(req.account)
    api = _api()
    try:
        raw = await asyncio.to_thread(
            api.get_aweme_list_collection, auth, req.max_cursor, str(req.count))
    except Exception as e:  # noqa: BLE001
        logger.warning("PLT-030", f"收藏夹作品获取失败: {type(e).__name__}")
        raise HTTPException(502, f"收藏夹作品获取失败: {type(e).__name__}")
    items = (raw or {}).get("aweme_list") if isinstance(raw, dict) else []
    return {"ok": True, "items": [_pick_aweme(w) for w in (items or [])],
            "has_more": bool((raw or {}).get("has_more")),
            "cursor": (raw or {}).get("cursor")}


@router.post("/collection/mixes")
async def collection_mixes(req: MixListReq) -> dict[str, Any]:
    """收藏的**合集**列表。对应基座 `get_mix_list_collection`。

    实测（2026-09-14 真实账号）：`status_code=0`，返回 `mix_infos`。
    """
    auth = _auth_for(req.account)
    api = _api()
    try:
        raw = await asyncio.to_thread(
            api.get_mix_list_collection, auth, str(req.count), req.cursor)
    except Exception as e:  # noqa: BLE001
        logger.warning("PLT-031", f"收藏合集获取失败: {type(e).__name__}")
        raise HTTPException(502, f"收藏合集获取失败: {type(e).__name__}")
    items = (raw or {}).get("mix_infos") if isinstance(raw, dict) else []
    out = []
    for m in (items or []):
        m = m or {}
        stat = m.get("statistics") or {}
        out.append({
            "mix_id": m.get("mix_id"),
            "mix_name": m.get("mix_name") or m.get("name"),
            "desc": m.get("desc"),
            "cover": ((m.get("cover_url") or {}).get("url_list") or [None])[0],
            "item_total": stat.get("total") or m.get("item_total"),
            "play_vv": stat.get("play_vv") or m.get("play_vv"),
            "update_time": m.get("update_time") or m.get("create_time"),
        })
    return {"ok": True, "items": out,
            "has_more": bool((raw or {}).get("has_more")),
            "cursor": (raw or {}).get("cursor")}


@router.post("/collection/series")
async def collection_series(req: SeriesAwemeReq) -> dict[str, Any]:
    """合集内的作品列表。对应基座 `get_series_aweme`。"""
    if not req.series_id:
        raise HTTPException(400, "缺少 series_id")
    auth = _auth_for(req.account)
    api = _api()
    try:
        raw = await asyncio.to_thread(
            api.get_series_aweme, auth, req.series_id, req.cursor, str(req.count))
    except Exception as e:  # noqa: BLE001
        logger.warning("PLT-032", f"合集内作品获取失败: {type(e).__name__}")
        raise HTTPException(502, f"合集内作品获取失败: {type(e).__name__}")
    items = []
    if isinstance(raw, dict):
        for k in ("aweme_list", "series_aweme_list", "aweme_list_collection"):
            if isinstance(raw.get(k), list):
                items = raw[k]
                break
    return {"ok": True, "items": [_pick_aweme(w) for w in items],
            "has_more": bool((raw or {}).get("has_more"))}


# ===========================================================================
# 媒体取址（★ 本分支新增，供播放器 / 下载使用）
# ===========================================================================
# 设计来源：源项目 `src/downloader/media_request.rs` + `media_group.rs`
# （方案 B 逆向情报）与 `src/media_proxy_cache.rs`。
# 本项目对应实现：`backend/downloader/media_request.py`（提取媒体）
#                + `backend/services/media_proxy.py`（唯一解密 + 缓存）。
#
# 为什么需要本端点：
#   列表接口（/feed、/user/works、/collection/items …）只返回**封面**与统计，
#   不含播放地址（实测 `AwemeItem` 无 url 字段）。播放器要播、下载器要下，
#   都必须先按 aweme_id **取详情 → 提取媒体地址**。


class MediaResolveReq(BaseModel):
    account: str
    """下列三种入参任选其一（**优先 aweme，其次 raw**，最后 url）：
      · `raw`      —— ★**推荐**：列表接口已返回的完整作品对象（实测 125 键，自带 play_addr）
      · `aweme_id` —— 仅 id 时后端自行取详情（**实测平台侧返回空**，见下）
      · `url`      —— 作品链接
    """
    aweme_id: str = ""
    url: str = ""
    ## 列表接口返回的完整作品对象（★ 实测：自带 play_addr，无需再请求）
    raw: dict[str, Any] | None = None
    quality: str = "origin"


@router.post("/media/resolve")
async def media_resolve(req: MediaResolveReq) -> dict[str, Any]:
    """解析可播放的媒体地址（供播放器 / 下载）。

    返回与前端 `PlayerMedia` 契约对齐：
      `{ok, type, url, images[], live_photos[], cover, duration, desc, aweme_id, qualities[]}`

    ## 实测结论（2026-09-14，决定本端点的入参设计）

    · **列表类接口**（`/feed`、`/user/works`、`/search`、`/collection/items`、`/liked`）
      返回的作品对象**自带播放地址**：实测 125 键、含 `video.play_addr`、45 个 URL。
    · **作品详情接口**（`get_work_info`，即 `/aweme/v1/web/aweme/detail/`）
      实测 **HTTP 200 但响应体 0 字节**（平台侧行为，与写操作族同源）。
    ⇒ 因此**推荐前端直接把列表返回的作品对象原样回传**（`raw`），
      避免再发一次必然失败的详情请求（少一次请求也更安全）。
      `aweme_id` / `url` 路径保留作 fallback，若平台侧恢复则可用。
    """
    from downloader import media_request as MR

    raw: dict[str, Any] | None = req.raw if isinstance(req.raw, dict) else None

    # ① 优先用前端回传的作品对象（实测自带地址，零额外请求）
    if raw is None and req.aweme_id:
        auth = _auth_for(req.account)
        api = _api()
        try:
            raw = await asyncio.to_thread(
                api.get_work_info, auth, f"https://www.douyin.com/video/{req.aweme_id}")
        except Exception as e:  # noqa: BLE001
            logger.warning("PLT-040", f"详情取址失败（平台侧常返空）: {type(e).__name__}")
            raw = None
    elif raw is None and req.url:
        auth = _auth_for(req.account)
        api = _api()
        try:
            raw = await asyncio.to_thread(api.get_work_info, auth, req.url)
        except Exception as e:  # noqa: BLE001
            logger.warning("PLT-040", f"详情取址失败（平台侧常返空）: {type(e).__name__}")
            raw = None

    if not isinstance(raw, dict) or not raw:
        raise HTTPException(502, "取址失败：未获得作品数据（建议由前端回传列表返回的作品对象 raw）")

    m = MR.extract_media(raw)
    url = MR.pick_quality(m, req.quality) or ""
    summary = MR.summarize(m)
    # 2026-09-15：前端回传的是 _pick_aweme 的裁剪对象（媒体在 media 子树、
    # author 亦在 media.author），故作者信息需兼容两种形态读取。
    inner = raw.get("media") if isinstance(raw.get("media"), dict) else {}
    author = raw.get("author") or inner.get("author") or {}
    return {
        "ok": True,
        "aweme_id": str(raw.get("aweme_id") or req.aweme_id or ""),
        "type": m.get("type"),
        "url": url,
        "images": m.get("images") or [],
        "live_photos": m.get("live_photos") or [],
        "cover": m.get("cover") or "",
        "duration": m.get("duration") or 0,
        "desc": (raw.get("desc") or inner.get("desc") or "")[:200],
        "author": {
            "nickname": (author.get("nickname") or ""),
            "avatar": (((author.get("avatar_thumb") or {}).get("url_list") or [""])[0]),
            "sec_uid": (author.get("sec_uid") or ""),
        },
        "qualities": summary.get("video_qualities") or [],
    }


@router.post("/media/stats")
async def media_stats() -> dict[str, Any]:
    """媒体代理缓存统计（照源项目 `media_proxy_cache.rs` 的语义）。"""
    from services import media_proxy as MP
    try:
        return {"ok": True, **MP.stats()}
    except Exception as e:  # noqa: BLE001
        logger.warning("PLT-041", f"媒体统计失败: {type(e).__name__}")
        raise HTTPException(502, f"媒体统计失败: {type(e).__name__}")
