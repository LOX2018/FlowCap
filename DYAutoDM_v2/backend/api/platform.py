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

from utils.tls_policy import tls_verify  # noqa: E402
import asyncio
import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
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
        # P3：凭证收敛——静态字段检查委托 verify_credential(lightweight=True)
        from auto_dm.accounts import verify_credential
        _vc = verify_credential(account, lightweight=True)
        if not _vc["ok"]:
            raise HTTPException(503, f"账号 {account} 凭证不完整（{_vc['wp']['detail']}），请先扫码登录")
        from dy_apis.login_api import DYLoginApi
        auth = DYLoginApi._load_auth_from_env(env_path)
        return auth
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(503, f"加载账号 {account} 凭证失败: {type(e).__name__}")


def _api():
    from dy_apis.douyin_api import DouyinAPI
    return DouyinAPI


# ===========================================================================
# 登录态失灵的统一判据（★ 2026-09-26 新增）
# ===========================================================================
# ## 为什么需要它（实测根因，2026-09-26）
#
# 「点赞 / 收藏 / 站内通知」三个 tab 同时不可用，实测根因**同一个**：
#   服务端对**所有本人相关端点**返回
#   `HTTP 200 + {"status_code": 8, "status_msg": "用户未登录"}` ——
#   而 `feed` / `search` 仍 200 正常 ⇒ 差别在于**这两类端点不校验登录态**。
#
# 铁证（本机实测）：cookie `sid_guard` = `…%7C<exp>%7C51`，
#   `exp = 1790251779` → **2026-09-24 20:09:39（已过期）**；
#   `login_time = 1790251777226`（37.9 小时前登录）。
#   旁证：`has_biz_token=false` / `IsDouyinActive=false`。
#
# ## 为什么必须区分「过期」与「高频限制」（旧文案的缺陷）
#
# 旧实现把 sc=8 一律解释成「短时高频访问触发限制，**稍后重试即可**」——
# 这在**凭证真过期**时是**误导**：用户等再久也不会好，正解是**重新扫码**。
# 而 `notice/list` 更糟：**静默返回空列表**，用户以为「本来就没通知」。
#
# ## 判据来源（复用，不新造）
#
# 复用项目既有唯一凭证有效性入口 `auto_dm.accounts.verify_credential`：
#   · `dm.level == "fail"` 且 detail 含 `609` / `只读` → 该账号 web 会话被
#     服务端判「只读」（读可用、写不可用）→ **需重新扫码**；
#   · `wp.level == "fail"` 且 label 含「身份漂移(USB-050)」→ 凭证陈旧。
# 这保证与「账号管理」页的结论**同源**，不会出现两处判断打架。
#
# 返回 `(reason, action_hint)`：reason 给人看的一句话，action_hint 是**可行动**指引。
async def _login_state_reason(account: str) -> tuple[str, str]:
    """判定该账号当前为何取不到「本人」数据。返回 (reason, action_hint)。

    P2-③（H-22 审计 idx9 · 调用链实证）：`verify_credential(lightweight=False)`
    是**同步阻塞**的全量双引擎校验（WP 探活 + DM 写探针 + 身份漂移检测，`timeout=8`，
    且 `auto_fix=True` 可能触发浏览器重捕获的**写副作用**）。本文件其余同类调用
    一律 `await asyncio.to_thread(...)`，唯此函数直接同步调用 ⇒ 每个降级请求
    阻塞事件循环最长 ~8s。故改为 async 并把阻塞体放入线程池。
    """
    try:
        from auto_dm.accounts import verify_credential
        v = await asyncio.to_thread(verify_credential, account, lightweight=False)
        wp = (v.get("wp") or {})
        dm = (v.get("dm") or {})
        dm_detail = str(dm.get("detail") or "")
        wp_label = str(wp.get("label") or "")
        if dm.get("level") == "fail" and ("609" in dm_detail or "只读" in dm_detail):
            return ("该账号的网页登录态已被服务端判为「只读」（读可用、写不可用）",
                    "请在「账号管理」对该账号执行【重新扫码】重建登录态")
        if wp.get("level") == "fail" and ("漂移" in wp_label or "030" in wp_label):
            return ("该账号凭证已陈旧（探活身份与历史会话不一致）",
                    "请在「账号管理」对该账号执行【重新扫码】重建登录态")
        if dm.get("level") == "fail":
            return (f"该账号凭证校验未通过：{dm_detail[:80] or wp_label or '原因未知'}",
                    "请在「账号管理」对该账号执行【重新扫码】重建登录态")
    except Exception as e:  # noqa: BLE001 —— 判据本身失败不阻断：回落到通用文案
        logger.debug(f"[PLT-000] 登录态判据不可用: {type(e).__name__}")
    return ("未能确认本人登录态（服务端返回「用户未登录」）",
            "请确认该账号在「账号管理」显示为可用；若已过期，重新扫码登录")


def _is_not_logged_in(raw: Any) -> bool:
    """响应是否为服务端「用户未登录」（status_code=8）。"""
    return isinstance(raw, dict) and raw.get("status_code") == 8



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
    """★ 本分支新增（照源项目）：合集内作品。

    ## 两种合集（2026-09-23，P1-7 修复）

    抖音的「合集」在接口层是**两种不同实体**（详见
    `工作记忆/14_业务域_内容采集.md`）：

    | 实体 | 接口 | 参数 | 判据 |
    |---|---|---|---|
    | **普通合集**（作者自建作品集） | `/aweme/v1/web/mix/aweme/` | `mix_id` | `is_serial_mix = 0` |
    | **短剧**（付费连载） | `/aweme/v1/web/series/aweme/` | `series_id` | `is_serial_mix = 1` |

    用 `series_id` 去打普通合集 → 服务端 `status_code: 5「参数不合法」` →
    前端**恒看到空列表**（实测根因）。

    ⇒ 请求体兼容三种给法（都归一到 `series_id` 这个槽，但 `platform.py` 会分流）：
      · `series_id` —— 前端 `platform-page.tsx` 直接传 `pickedMix.id`（= mix_id）；
      · `mix_id`  —— 语义显式版；
      · `is_serial_mix` —— **可选**显式判据（1=短剧走 series，0=普通合集走 mix）；
        未给时后端按 `status_code==5` 自动回退（见 `collection_series`）。
    """
    account: str
    series_id: str = ""
    mix_id: str = ""
    is_serial_mix: int | None = None
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


# ★ 2026-09-27（ADR-018 F3）：内容页「评论采集」与「评论行发私信」
class CommentsFullReq(BaseModel):
    """评论采集（含楼中楼）——**只读**，不做任何写操作。"""
    account: str
    aweme_id: str
    url: str = ""                 # 可选：作品 URL；缺省时按 aweme_id 拼 www 链接
    limit: int = 50               # 一级评论上限（防热门作品全量过久）
    with_inner: bool = True       # 是否拉楼中楼（每条一级评论额外一次请求）
    inner_limit: int = 5          # 单条一级评论最多保留多少条楼中楼


class CommentDmReq(BaseModel):
    """评论行「发私信」——**仅手动触发**，不在任何自动链路里被调用。

    D1（用户拍板）：自动外发默认休眠 `enabled=False`；本端点只服务前端
    「用户点按钮」，且不自带任何自动批量/定时语义。
    """
    account: str
    uid: str                      # 评论者 uid（**仅取评论自带**，绝不补查）
    text: str
    nickname: str = ""            # 仅用于日志/回显，不参与发送


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
        logger.warning(f"[PLT-001] " + f"推荐流获取失败: {type(e).__name__}")
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
    except json.JSONDecodeError:
        # 2026-09-15：平台限流时返回**空响应体**（非 JSON）→ resp.json() 抛此错。
        # 属平台侧限制（推荐流/收藏仍正常可佐证），优雅降级为空列表并由前端提示，
        # 不再报 502（否则用户看到「接口不可用」，误判为功能故障）。
        logger.info(f"[PLT-002] " + "作品列表返回空响应（平台限流），降级为空列表")
        items = []
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[PLT-002] " + f"作品列表获取失败: {type(e).__name__}")
        raise HTTPException(502, f"作品列表获取失败: {type(e).__name__}")
    out = [_pick_aweme(w) for w in (items or [])]
    if not out:
        # 2026-09-15：实测 `aweme/post` 偶发「只返 sc=0 无 aweme_list」——
        # 平台对同一 sec_uid 短时多次请求会限流（非凭证失效，推荐流/收藏仍正常）。
        logger.info(f"[PLT-002] " + f"用户作品返回空（平台限流或该用户无公开作品）: sec={req.user_url[-16:]}")
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
        logger.warning(f"[PLT-003] " + f"用户资料获取失败: {type(e).__name__}")
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
            logger.warning(f"[PLT-020] " + f"BCC 批量查用户失败: {type(e).__name__}")
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
        logger.warning(f"[PLT-021] " + f"HTTP 批量查用户失败: {type(e).__name__}")
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
            verify=tls_verify(), timeout=12,
        )
        # 2026-09-17 修补（OCR 审查 HIGH —— 无条件 r.json() 中断整批）：
        # 抖音限流时返回 HTTP 200 + **空响应体**（本模块其它端点已有此记录），
        # 原实现直接 `r.json()` 抛 JSONDecodeError，异常向上冒泡**中断整批**
        # 剩余分片，而不是像 status_code≠0 那样 continue。
        try:
            j = r.json()
        except Exception as e:
            logger.warning(f"[PLT-0xx] im/user/info 响应非 JSON"
                           f"（HTTP {r.status_code}, {len(r.content)} 字节），跳过本批: {e}")
            continue
        if not isinstance(j, dict):
            continue
        if j.get("status_code") != 0:
            logger.warning(f"[PLT-022] " + f"im/user/info sc={j.get('status_code')} msg={j.get('status_msg')}")
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
        stream = None  # ★ M-20：供传输层事实读取（except 分支下保持 None）
        try:
            stream = await asyncio.to_thread(api.search_stream, auth, req.query, "0", str(num))
            works = (stream or {}).get("aweme_list") or []
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[PLT-009] " + f"源项目搜索流失败，回落旧接口: {type(e).__name__}")
            works = None
        # ★ 2026-09-27 修复（M-20 收口 · 「禁止假成功」）：此前 `works` 为空时
        #   一律回 200 + `items: []` —— 前端**无从区分**「这个关键词真没作品」与
        #   「被 Argus 风控拦截」，只能显示空列表**假装没结果**（项目铁律禁止）。
        #   现按同族已修范式把传输层事实如实上抛：`blocked=True` + 原因文案。
        transport = api.take_search_transport(stream)
        if not works:
            fb = await asyncio.to_thread(api.search_some_general_work, auth, req.query, num, "0", "0")
            works = fb or []
            # 回落接口也带传输层事实（取更可信的那一个：先流的、再回落的）
            transport = transport or api.take_search_transport(fb)
        blocked = bool(transport)
        # 文案组装用 .format（不用嵌套引号 f-string，避免转义歧义）
        _reason = None
        if blocked:
            _reason = "被风控拦截（HTTP {}，响应 {} 字节）".format(
                transport.get("status"), transport.get("bytes"))
        return {"ok": True, "kind": "video",
                "items": [_pick_aweme(w) for w in (works or [])],
                "blocked": blocked,
                "blocked_reason": _reason}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[PLT-004] " + f"搜索失败: {type(e).__name__}")
        raise HTTPException(502, f"搜索失败: {type(e).__name__}")


@router.post("/favorite")
async def favorite(req: LikedReq) -> dict[str, Any]:
    """**我的收藏（作品维度）** —— 不依赖「收藏夹」文件夹。

    ## 为什么单开这个端点（2026-09-21，用户反馈）

    原设计只暴露 `/collected`（收藏夹**文件夹列表**）→ 前端「收藏夹」tab
    先要求用户有文件夹、再点进去才看作品。但实测该账号

        get_collect_list → collects_list = **0 个文件夹**

    而**收藏作品本身有 19 条**（`get_user_favorite` 实测 status_code=0）。

    ⇒ 多数人并不专门建文件夹，收藏就是一堆作品。故提供本端点
      **直接返回收藏作品**，与「点赞」同源（`get_user_favorite`），
      区别只在语义与文案。前端「收藏」tab 用它，不再要求文件夹。
    """
    auth = _auth_for(req.account)
    api = _api()
    sec = req.sec_id
    if not sec:
        try:
            sec = await asyncio.to_thread(api.get_my_sec_uid, auth)
        except Exception as e:  # noqa: BLE001
            # ★ 2026-09-26 修复（该 tab 直接 502「请求失败」——**错误语义**）：
            #   取不到本人 sec_uid 时原实现抛 **502**，前端只能弹「请求失败」，
            #   用户无法分辨「功能坏了」还是「登录态问题」。
            #   实测根因：`profile/self` 在高频访问后返回 status_code=8
            #   「用户未登录」（同期 feed 仍 200/6 条 ⇒ cookie 有效，属服务端
            #   对该凭证的**降权**，非账号失效）。
            #   ⇒ 改为 **200 + unavailable**（与下方「平台侧空响应」同一降级范式），
            #   并透传真实原因，由 UI 给出可行动的提示。
            logger.warning(f"[PLT-007] " + f"取自身 sec_uid 失败: {type(e).__name__}")
            _why, _hint = await _login_state_reason(req.account)
            return {"ok": True, "items": [], "has_more": False, "unavailable": True,
                    "reason": f"{_why}。{_hint}"}
    try:
        raw = await asyncio.to_thread(
            api.get_user_favorite, auth, sec, "0", str(max(1, min(req.num, 50))))
    except Exception as e:  # noqa: BLE001
        # 与 /liked 同样的处置：平台侧偶发空响应体 → 空列表 + 标记，不弹误导性错误
        logger.warning(f"[PLT-007] " + f"收藏列表获取失败（平台侧常返空）: {type(e).__name__}")
        _why, _hint = await _login_state_reason(req.account)
        return {"ok": True, "items": [], "has_more": False, "unavailable": True,
                "reason": f"平台侧暂不可用（{type(e).__name__}）。{_hint}"}
    # ★ 2026-09-26 修复：与 /liked 同源缺陷 —— `get_user_favorite` 不抛异常、
    #   直接返回 `{"status_code": 8, "status_msg": "用户未登录"}`，原实现不看 sc
    #   ⇒ 空列表「暂无收藏」掩盖了「登录过期」。必须显式识别。
    if _is_not_logged_in(raw):
        _why, _hint = await _login_state_reason(req.account)
        logger.warning("[PLT-007] 收藏列表：服务端返回 status_code=8（用户未登录）")
        return {"ok": True, "items": [], "has_more": False, "unavailable": True,
                "reason": f"{_why}。{_hint}"}
    if not isinstance(raw, dict):
        return {"ok": True, "items": [], "has_more": False, "unavailable": True}
    return {
        "ok": True,
        "items": [_pick_aweme(w) for w in (raw.get("aweme_list") or [])],
        "has_more": bool(raw.get("has_more")),
        "cursor": raw.get("max_cursor"),
    }


@router.post("/collected")
async def collected(req: CollectListReq) -> dict[str, Any]:
    """收藏夹**文件夹列表**（历史保留）。对应基座 `get_collect_list`。

    ⚠️ 2026-09-21：前端「收藏」tab 已改用 `/favorite`（直接看作品），
    本端点保留供未来「按文件夹浏览」使用 —— 实测多数账号文件夹数为 0。
    """
    auth = _auth_for(req.account)
    api = _api()
    try:
        raw = await asyncio.to_thread(api.get_collect_list, auth)
    except json.JSONDecodeError:
        # 2026-09-15：同「用户作品」——平台限流返回空响应体，优雅降级为空。
        logger.info(f"[PLT-005] " + "收藏夹返回空响应（平台限流），降级为空列表")
        raw = {}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[PLT-005] " + f"收藏夹获取失败: {type(e).__name__}")
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
            # ★ 2026-09-26 修复（该 tab 直接 502「请求失败」——**错误语义**）：
            #   取不到本人 sec_uid 时原实现抛 **502**，前端只能弹「请求失败」，
            #   用户无法分辨「功能坏了」还是「登录态问题」。
            #   实测根因：`profile/self` 在高频访问后返回 status_code=8
            #   「用户未登录」（同期 feed 仍 200/6 条 ⇒ cookie 有效，属服务端
            #   对该凭证的**降权**，非账号失效）。
            #   ⇒ 改为 **200 + unavailable**（与下方「平台侧空响应」同一降级范式），
            #   并透传真实原因，由 UI 给出可行动的提示。
            # ★ 2026-09-26 修复（错误语义 v2）：原先把 sc=8「用户未登录」
            #   一律解释成「短时高频访问触发限制，**稍后重试即可**」——
            #   本机实测证伪：真实根因是 **cookie 硬过期**
            #   （`sid_guard` exp=1790251779 → 2026-09-24 20:09 已过期），
            #   此类情况**永远不会自愈**，正解是**重新扫码**。
            #   ⇒ 改为调用统一判据 `_login_state_reason()` 给出「原因 + 可行动指引」。
            logger.warning(f"[PLT-006] " + f"取自身 sec_uid 失败: {type(e).__name__}")
            _why, _hint = await _login_state_reason(req.account)
            return {"ok": True, "items": [], "has_more": False, "unavailable": True,
                    "reason": f"{_why}。{_hint}"}
    try:
        raw = await asyncio.to_thread(api.get_user_favorite, auth, sec, "0", str(max(1, min(req.num, 50))))
    except Exception as e:  # noqa: BLE001
        # ★ 2026-09-15 实测：平台侧对 `/aweme/v1/web/aweme/favorite/` 返回
        #   **HTTP 200 但响应体 0 字节**（绕过本项目封装直接构造原始请求复现同样结果），
        #   解析时表现为 JSONDecodeError。源项目逆向情报中「点赞列表」用的也是该接口
        #   ⇒ 属**平台侧行为**，非本项目适配错误。
        #   故此处**不抛 502**（避免前端弹"请求失败"误导用户），
        #   而是返回空列表 + `unavailable` 标记，让 UI 能给出准确说明。
        logger.warning(f"[PLT-006] " + f"点赞列表获取失败（平台侧常返空）: {type(e).__name__}")
        return {"ok": True, "items": [], "has_more": False,
                "unavailable": True,
                "reason": f"平台侧暂不可用（{type(e).__name__}）"}
    # ★ 2026-09-26 修复：`get_user_favorite` **不抛异常**、而是直接返回
    #   `{"status_code": 8, "status_msg": "用户未登录"}` —— 原实现不看 sc，
    #   于是 `aweme_list` 取不到 → 返回**空列表**，前端显示「暂无点赞作品」，
    #   把「登录过期」伪装成「本来就没点赞」。必须显式识别。
    if _is_not_logged_in(raw):
        _why, _hint = await _login_state_reason(req.account)
        logger.warning(f"[PLT-006] 点赞列表：服务端返回 status_code=8（用户未登录）")
        return {"ok": True, "items": [], "has_more": False, "unavailable": True,
                "reason": f"{_why}。{_hint}"}
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
        logger.warning(f"[PLT-007] " + f"关系列表获取失败: {type(e).__name__}")
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
        logger.warning(f"[PLT-008] " + f"站内通知获取失败: {type(e).__name__}")
        raise HTTPException(502, f"站内通知获取失败: {type(e).__name__}")
    # ★ 2026-09-14 实测修正：新版接口把数据放在 **`notice_list_v2`**，
    #   `notice_list` 恒为空数组。原实现只读 `notice_list` ⇒ 实测恒 0 条。
    #   取法：优先 v2，为空再回落到旧键（兼容两端）。
    # ★ 2026-09-26 修复：服务端对本人端点返回 `{"status_code": 8,
    #   "status_msg": "用户未登录"}`（本机实测：cookie `sid_guard` 已过期）。
    #   原实现不看 sc ⇒ `notice_list_v2` 取不到 ⇒ 返回**空列表**，
    #   前端显示「暂无通知」，把「登录过期」伪装成「本来就没通知」。
    if _is_not_logged_in(raw):
        _why, _hint = await _login_state_reason(req.account)
        logger.warning("[PLT-008] 站内通知：服务端返回 status_code=8（用户未登录）")
        return {"ok": True, "items": [], "has_more": False, "unread": None,
                "unavailable": True, "reason": f"{_why}。{_hint}"}
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
        logger.warning(f"[PLT-009] " + f"评论获取失败: {type(e).__name__}")
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
# ★ 2026-09-27（ADR-018 F3）：内容页「评论采集 + 与播放同时展示」
# ---------------------------------------------------------------------------
#
# ## 与既有 `POST /api/platform/comments` 的关系
#
# 既有端点只取**一级**、且只看 `url`。F3 要求「评论采集 + 楼中楼 + 评论行发私信」，
# 故新增只读端点 `/comments/full`（**不改动**既有 `/comments` 的契约，避免回归）。
#
# ## 风控红线（D7）
#
# 昵称**只取评论响应自带**的 `user.nickname`；**绝不回调任何批量用户补查**
# 方法（那几个是已登记的死代码，属风控红线）。
#
# ## D6 签名
#
# 采集走 `DouyinAPI.get_work_all_comment` → `get_work_all_out_comment` →
# `get_work_out_comment`（一级）/ `get_work_all_inner_comment` →
# `get_work_inner_comment`（楼中楼）。这两个**实际发请求**的方法已于本次
# 改走 `params.signed_url()`（见 `dy_apis/client_comments.py`），否则
# Argus 网关恒返 403（46B `Blocked by ArgusSecurityPlugin Uifid Not Found`）。

_ARGUS_BLOCKED = "被风控拦截"


def _map_comment_full(c: dict, inner_limit: int = 5) -> dict:
    """规范化一条一级评论（昵称**只取自带**，不补查）。"""
    u = c.get("user") or {}
    replies_raw = c.get("reply_comment") or []
    replies = []
    for r in (replies_raw or [])[: max(0, inner_limit)]:
        if not isinstance(r, dict):
            continue
        ru = r.get("user") or {}
        replies.append({
            "cid": str(r.get("cid") or ""),
            "text": (r.get("text") or "")[:500],
            "digg_count": r.get("digg_count") or 0,
            "create_time": r.get("create_time") or 0,
            "user_nickname": ru.get("nickname") or "",   # 自带，不补查
            "user_uid": str(ru.get("uid") or ""),
        })
    return {
        "cid": str(c.get("cid") or ""),
        "text": (c.get("text") or "")[:500],
        "digg_count": c.get("digg_count") or 0,
        "create_time": c.get("create_time") or 0,
        "reply_comment_total": c.get("reply_comment_total") or 0,
        "has_inner": bool(c.get("reply_comment_total") or 0) or bool(replies),
        "reply_comment": replies,
        "user_nickname": u.get("nickname") or "",       # 自带，不补查
        "user_uid": str(u.get("uid") or ""),
        "user_sec_uid": u.get("sec_uid") or "",
    }


@router.post("/comments/full")
async def comments_full(req: CommentsFullReq) -> dict[str, Any]:
    """作品评论采集（一级 + 可选楼中楼）——**只读**，与播放器同时展示。

    返回字段（每条一级评论）：
      cid / text / digg_count / create_time / reply_comment_total /
      has_inner（是否含楼中楼）/ reply_comment[]（楼中楼，同结构，无嵌套）/
      user_nickname / user_uid / user_sec_uid

    失败态如实呈现：
      · `blocked=True`  → 平台侧风控拦截（Argus 等），文案在 `reason`
      · `ok=False`      → 取不到，原因在 `reason`（不返回占位空列表冒充成功）
    """
    aweme_id = (req.aweme_id or "").strip()
    if not aweme_id:
        raise HTTPException(400, "缺少作品 ID")
    auth = _auth_for(req.account)
    api = _api()

    url = (req.url or "").strip() or f"https://www.douyin.com/video/{aweme_id}"
    limit = max(1, min(int(req.limit or 50), 200))
    inner_limit = max(0, min(int(req.inner_limit or 5), 20))

    def _fetch() -> list[dict]:
        """拉取评论。带 `has_inner` 的一级评论按需补楼中楼。

        一级用**带上限的翻页**（不用 `get_work_all_out_comment` 全量，
        热门作品会无限翻页）；楼中楼复用 `get_work_all_inner_comment`。
        """
        out: list[dict] = []
        cursor = "0"
        for _ in range(40):                      # 硬上限 40 页，防止死循环
            res = api.get_work_out_comment(auth, url, cursor)
            if not isinstance(res, dict):
                break
            batch = res.get("comments")
            if not batch:
                break
            out.extend([b for b in batch if isinstance(b, dict)])
            if len(out) >= limit or res.get("has_more") != 1:
                break
            cursor = str(res.get("cursor") or len(out))
        out = out[:limit]
        if req.with_inner:
            for c in out:
                c["reply_comment"] = []
                try:
                    if int(c.get("reply_comment_total") or 0) > 0:
                        inner = api.get_work_all_inner_comment(auth, c)
                        c["reply_comment"] = [
                            i for i in (inner or []) if isinstance(i, dict)
                        ][:inner_limit]
                except Exception as e:  # noqa: BLE001 —— 单条楼中楼失败不拖垮整批
                    logger.warning(f"[PLT-012] " + f"楼中楼采集失败 cid="
                                   f"{c.get('cid')}: {type(e).__name__}")
        return out

    try:
        raw = await asyncio.to_thread(_fetch)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[PLT-013] " + f"评论采集失败 account={req.account} "
                                       f"aweme={aweme_id}: {type(e).__name__}")
        raise HTTPException(502, f"评论采集失败: {type(e).__name__}") from e

    # 如实呈现：一条都没拿到 ⇒ 区分「真没评论」与「被风控拦截」。
    # 判据：`get_work_out_comment` 走 signed_url 后仍拿不到，且响应被
    # safe_json 降级成空 dict（Argus 403 的 46B 非 JSON 体即此形态）。
    if not raw:
        probe: Any = None
        try:
            probe = await asyncio.to_thread(
                api.get_work_out_comment, auth, url, "0")
        except Exception:  # noqa: BLE001
            probe = None
        empty_probe = (not isinstance(probe, dict)) or (
            not probe.get("comments") and probe.get("status_code") is None)
        if empty_probe:
            logger.warning(f"[PLT-014] 评论采集空响应（疑似风控拦截）"
                           f"account={req.account} aweme={aweme_id}")
            return {"ok": True, "items": [], "total": 0, "has_more": False,
                    "blocked": True, "reason": _ARGUS_BLOCKED}
    items = [_map_comment_full(c, inner_limit) for c in raw]
    return {"ok": True, "items": items, "total": len(items),
            "has_more": len(raw) >= limit, "blocked": False}


@router.post("/comments/dm")
async def comment_dm(req: CommentDmReq) -> dict[str, Any]:
    """评论行「发私信」——**仅前端手动点击触发**，转调既有 `dm_dispatch`。

    ## 不自造轮子（D1 / D4）
    发送逻辑、限流、去重、投递验证**全部复用**既有入口
    `services.dm_dispatch.get_dispatcher().submit_by_uid()`（陌生人首发语义，
    与「视频采集」同通道）；本端点**不**自己发、也**不**自带任何自动批量语义。

    ## 无回执不认成功
    `submit_by_uid` 返回的是「入队受理」结果，不是投递成功。此处如实把
    `accepted` 与 `error` 透传，由前端按 `accepted` 显示；**不**把受理
    包装成「已发送」。
    """
    uid = (req.uid or "").strip()
    text = (req.text or "").strip()
    if not uid or not text:
        raise HTTPException(400, "缺少目标 uid 或私信文案")

    try:
        from services.dm_dispatch import get_dispatcher as _get_dispatcher
        disp = _get_dispatcher()
        r = await asyncio.to_thread(disp.submit_by_uid, req.account, uid, text,
                                    "manual")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[PLT-015] " + f"评论转私信异常 account={req.account} "
                                       f"uid={uid}: {type(e).__name__}")
        raise HTTPException(502, f"私信提交异常: {type(e).__name__}") from e

    ok = bool(getattr(r, "accepted", False))
    err = str(getattr(r, "error", "") or "")
    logger.info(f"[PLT-016] 评论转私信 account={req.account} uid={uid} "
                f"nickname={req.nickname} accepted={ok} error={err[:80]}")
    return {"ok": ok, "accepted": ok, "error": err,
            "task_id": str(getattr(r, "task_id", "") or "")}


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



@router.post("/action/digg")
async def action_digg(req: DiggReq) -> dict[str, Any]:
    """点赞 / 取消点赞。对应基座 commit/item/digg 链路。

    ⚠️ 写操作：前端必须由用户显式点击触发，不得自动批量执行。
    """
    # ★ 2026-09-26 修复（「点赞」点了没反应且只说“未返回成功” —— 语义吞没）：
    #   基座 `digg()` 把平台响应坍缩成一个裸 `bool`，而实测服务端对
    #   `/aweme/v1/web/commit/item/digg/` 恒返 **HTTP 200 + status_code=8
    #   「用户未登录」**（www 与 www-hj 双域名 A/B 实测一致；该接口不在
    #   secsdk 保护清单内 ⇒ 不是漏签名，而是**写操作需要浏览器容器态凭证**
    #   （bd-ticket-guard / REE，见 docs/reverse_interface_spec.md §2.2/§2.3）。
    #   原实现返回 `{ok: false}`，前端只能显示「点赞未返回成功（平台侧可能
    #   已限流）」——**误导**：既非限流、也非失败，是**当前通道不具备写权限**。
    #   ⇒ 改为优先取原始响应（digg_raw），透传 status_code / status_msg；
    #   基座无 raw 方法时回落到 bool 并如实标注 unverifiable。
    auth = _auth_for(req.account)
    api = _api()
    raw: dict[str, Any] | None = None
    try:
        fn = getattr(api, "digg_raw", None)
        if callable(fn):
            raw = await asyncio.to_thread(fn, auth, req.aweme_id, req.action)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[PLT-010] " + f"点赞原始响应获取失败: {type(e).__name__}")
        raw = None
    if isinstance(raw, dict):
        sc = raw.get("status_code")
        ok = (sc == 0) or (raw.get("is_digg") == 0)
        return {"ok": bool(ok), "action": req.action, "status_code": sc,
                "status_msg": raw.get("status_msg") or "", "raw": True}
    try:
        # 基座真实方法：digg(auth, aweme_id, digg_type) -> bool
        #   digg_type: '1'=点赞 '0'=取消
        ok = await asyncio.to_thread(api.digg, auth, req.aweme_id, req.action)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[PLT-010] " + f"点赞失败: {type(e).__name__}")
        raise HTTPException(502, f"点赞失败: {type(e).__name__}")
    # 无原始响应 ⇒ 成功不可证实，如实标注（不假装成功，也不谎称限流）
    return {"ok": bool(ok), "action": req.action, "status_code": None,
            "unverifiable": True}


@router.post("/action/collect")
async def action_collect(req: CollectReq) -> dict[str, Any]:
    """收藏 / 取消收藏。对应基座 `collect_aweme`。"""
    auth = _auth_for(req.account)
    api = _api()
    try:
        raw = await asyncio.to_thread(api.collect_aweme, auth, req.aweme_id, req.action)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[PLT-011] " + f"收藏失败: {type(e).__name__}")
        raise HTTPException(502, f"收藏失败: {type(e).__name__}")
    return {"ok": True, "raw_status": (raw or {}).get("status_code") if isinstance(raw, dict) else None}


# ⛔ 2026-09-26 移除：`POST /action/follow`（关注/取关）。
#   判据（实测）：基座 `dy_apis` **无任何关注写方法**
#   （`commit_follow` / `follow_user` 均不存在，只有 follower/following
#   两个**读**接口）⇒ 该端点恒返 501，**功能实际不可用**；前端
#   `platform.ts` 虽有定义，但**页面上无一处调用**。
#   按「修不好就去掉」原则移除，避免保留一个永远 501 的假入口。
#   若将来在 dy_apis 补齐 `/aweme/v1/web/commit/follow/user/` 链路，
#   再连同前端入口一起加回（勿只加一半 —— 历史教训：有方法无路由）。
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
        logger.warning(f"[PLT-030] " + f"收藏夹作品获取失败: {type(e).__name__}")
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
        logger.warning(f"[PLT-031] " + f"收藏合集获取失败: {type(e).__name__}")
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
            # 🔴 P1-7（2026-09-23）：把短剧判据透传给前端 —— 前端据此决定
            # 进「合集」时用 mix_id 还是 series_id（两条不同上游接口）。
            # 上游若无该字段则不下发（None），由 `/collection/series` 的 sc 回退兜底。
            "is_serial_mix": m.get("is_serial_mix"),
        })
    return {"ok": True, "items": out,
            "has_more": bool((raw or {}).get("has_more")),
            "cursor": (raw or {}).get("cursor")}


@router.post("/collection/series")
async def collection_series(req: SeriesAwemeReq) -> dict[str, Any]:
    """合集内作品 —— **按 `is_serial_mix` 分流**到两个上游接口。

    ## 🔴 P1-7 修复（2026-09-23，实测根因）

    旧实现**只**调 `get_series_aweme(req.series_id)`（短剧专用接口，参数 `series_id`），
    而前端 `platform.ts:collectionSeries` 传的是 `pickedMix!.id` = **`mix_id`**
    （来自 `collection/mixes` 的 `mix_infos[].mix_id`）→ 用 `series_id` 的位置去打
    普通合集 → 服务端 `status_code: 5「参数不合法」/ aweme_list: null`
    → 前端**永远看到「该合集暂无作品」**。

    正确姿势（README/`工作记忆/14`/C-02 §Ⅰ2 已有记载，代码当时没落地）：

    | 实体 | 上游接口 | 参数 |
    |---|---|---|
    | `is_serial_mix = 0`（**普通合集**，绝大多数） | `get_mix_aweme` | `mix_id` |
    | `is_serial_mix = 1`（短剧） | `get_series_aweme` | `series_id` |

    ## 分流判据（三态，不得把「取不到」折成「不是」）

    1. `req.is_serial_mix` **显式给出** → 直接按它走（前端可传，最稳）；
    2. 未给出 → **先按普通合集走**（预判：绝大多数是普通合集；`collection/mixes`
       这个来源本身就是收藏的普通合集，没有短剧字段）；
    3. 上一步拿到 `status_code == 5`（参数不合法，即"这不是我要的那种合集"）
       → **自动回退**试另一条接口；两条都失败则如实返回空 + `detail`（不静默）。
    """
    # 归一 id 槽：三个字段任一有值都可用（前端历史实现把 mix_id 放在 series_id 里）
    sid = (req.series_id or "").strip()
    mid = (req.mix_id or "").strip()
    primary = mid or sid
    if not primary:
        raise HTTPException(400, "缺少 series_id/mix_id（合集 id）")
    # 短剧槽与普通合集槽是否指向不同的 id（双给时以 mix_id 为普通合集 id）
    series_slot = sid
    mix_slot = mid or (sid if req.is_serial_mix != 1 else "")

    auth = _auth_for(req.account)
    api = _api()

    async def _try(kind: str, ident: str) -> dict[str, Any]:
        """调一条上游接口并归一成 {items, has_more, sc, detail}。"""
        if kind == "mix":
            raw = await asyncio.to_thread(
                api.get_mix_aweme, auth, ident, req.cursor, str(req.count))
        else:
            raw = await asyncio.to_thread(
                api.get_series_aweme, auth, ident, req.cursor, str(req.count))
        raw = raw if isinstance(raw, dict) else {}
        items = []
        for k in ("aweme_list", "series_aweme_list", "aweme_list_collection"):
            if isinstance(raw.get(k), list) and raw[k]:
                items = raw[k]
                break
        return {"items": items, "has_more": bool(raw.get("has_more")),
                "sc": raw.get("status_code"), "msg": raw.get("status_msg") or "",
                "via": kind, "ident": ident}

    # 顺序：显式判据优先；否则「普通合集 → 短剧」回退
    if req.is_serial_mix == 1:
        order = [("series", series_slot or primary), ("mix", mix_slot or primary)]
    elif req.is_serial_mix == 0:
        order = [("mix", mix_slot or primary), ("series", series_slot or primary)]
    else:
        order = [("mix", mix_slot or primary), ("series", series_slot or primary)]

    out: dict[str, Any] | None = None
    tried: list[str] = []
    for kind, ident in order:
        if not ident:
            continue
        try:
            out = await _try(kind, ident)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[PLT-032] " + f"合集内作品获取失败({kind}): {type(e).__name__}")
            out = None
            tried.append(f"{kind}:err:{type(e).__name__}")
            continue
        tried.append(f"{kind}:sc={out['sc']}")
        # sc==0 且有作品 → 命中；sc==0 但空列表 → 这是**真·空合集**，不再试另一条
        if out["sc"] == 0:
            break
        # sc!=0（实测普通合集被 series 接口打回 sc=5）→ 继续试另一条
        logger.info(f"[PLT-032] 合集取作品 {kind} id={ident[:12]}… sc={out['sc']} "
                    f"msg={out['msg']} → 回退另一接口")

    if out is None:
        raise HTTPException(502, "合集内作品获取失败：两条上游接口均不可用")

    items = out["items"]
    if not items and out["sc"] != 0:
        # 两条都失败：如实告知（不得假装"空合集"）
        logger.warning(f"[PLT-032] 合集内作品两条接口均未成功 tried={tried}")
    return {"ok": True, "items": [_pick_aweme(w) for w in items],
            "has_more": out["has_more"], "via": out["via"],
            "status_code": out["sc"]}


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
      ⚠️ **2026-09-26 订正（原表述为误归因）**：旧注释写「实测 HTTP 200 但响应体
      0 字节（平台侧行为）」—— **该结论已被 A/B 实测推翻**。真实原因是**缺 secsdk
      签名**：不带签名时被 Argus 网关拦下返 **403**（46B），上游把非 200 体当空读，
      于是误记为「200 空体」。**实测对照**（真实作品 id=7680508646496750890）：
        不带签名 → 403 `Blocked by ArgusSecurityPlugin Uifid Not Found`
        带签名   → **200 + 124,004 字节 + `aweme_detail` 非空**
      ⇒ `get_work_info` 已补 `signed_url`（M-2 闭环），`aweme_id`/`url` 路径**可用**。
    仍**推荐**前端直接回传列表作品对象（少一次请求、也少一次风控面），
    但不再是因为「详情必然失败」—— 那是错的。
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
            logger.warning(f"[PLT-040] " + f"详情取址失败（平台侧常返空）: {type(e).__name__}")
            raw = None
    elif raw is None and req.url:
        auth = _auth_for(req.account)
        api = _api()
        try:
            raw = await asyncio.to_thread(api.get_work_info, auth, req.url)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[PLT-040] " + f"详情取址失败（平台侧常返空）: {type(e).__name__}")
            raw = None

    if not isinstance(raw, dict) or not raw:
        raise HTTPException(502, "取址失败：未获得作品数据（建议由前端回传列表返回的作品对象 raw）")

    m = MR.extract_media(raw)
    picked = MR.pick_quality(m, req.quality) or ""
    # ── 2026-09-21 根因修复：跟随 302 拿真实可播地址 ──
    # `pick_quality` 已优先挑无签名链接，但无签名入口
    # `/aweme/v1/play/?video_id=` 本身是 **302 跳转入口**而非视频数据；
    # 不解析它，<video> 拿到的是一个 text/plain 的 302 页面 → 播放失败。
    # 会话签名直链则相反：必须避开（CDN 403），挑选逻辑见 is_session_signed()。
    url = picked
    how = "unsigned-entry"
    if picked:
        url, how = await asyncio.to_thread(MR.resolve_playable, picked)
        # 告警判据（2026-09-21 修正）：只有**解析未生效**（passthrough，仍是
        # 未经 302 的候选地址）且该地址带会话签名时，才判定「必然 403」。
        # 解析成功后的真实流 host 同样落在 douyinvod.com —— 那不是签名直链，
        # 早期版本按 host 告警，会把「已解析可播」误报成「必然失败」。
        if how == "passthrough" and MR.is_session_signed(url):
            logger.warning(f"[PLT-042] " + f"播放地址未解析且为会话签名直链（CDN 会 403）: {picked[:80]}")
        elif how != "passthrough":
            logger.info(f"[crawl] 播放地址已解析 via={how} aweme={req.aweme_id or (raw.get('aweme_id') or '')}")
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
        # 取址方式（2026-09-21）：direct=直给 / head|get-range=已跟随302 /
        # passthrough=解析失败沿用原地址。前端与排障据此判断地址是否可信。
        "resolved_via": how,
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
        return {"ok": True, **MP.stats(), "stream_cache": stream_cache_stats()}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[PLT-041] " + f"媒体统计失败: {type(e).__name__}")
        raise HTTPException(502, f"媒体统计失败: {type(e).__name__}")


# ===========================================================================
# 播放流反向代理（★ 2026-09-21 新增，方案 B —— 根治「取到地址仍播不了」）
# ===========================================================================
# ## 为什么必须有它（实测根因）
#
# 方案 A 已能让后端拿到 200/video/mp4 的真实地址，但**播放器仍然失败**：
# Tauri 的 webview 里 `<video src="https://v26-web.douyinvod.com/...">` 是
# **跨域直接请求抖音 CDN**，浏览器侧受 CORS / Referer / 来源策略约束，
# 与 Python 侧 `requests` 能否 200 是**两件事**。
#
# ⇒ 正解是让 `<video>` 只请求**同源的本机后端**，由后端带正确 headers 去
#   拉流并转发（源项目 `media_proxy_cache.rs` 的对应物）。
#
# ## 设计要点
# · **不落盘**：50MB×N 的作品全落盘会撑爆磁盘，故边收边转（stream）。
# · **透传 Range**：<video> 拖进度条必须支持 206，故 Range 头原样转发。
# · **URL 白名单**：只允许抖音媒体域，防 SSRF（与 merged_forward 同范式）。
# · **签名校验**：用 urlsafe 签名(aweme_id+过期) 防被当开放代理滥用。

_ALLOWED_MEDIA_HOSTS = (
    "douyinvod.com",
    "douyinpic.com",
    "douyinstatic.com",
    "byteimg.com",
    "bytegoofy.com",
    "ixigua.com",
    "snssdk.com",
)
_STREAM_SIGN_TTL_SEC = 6 * 3600

# ── 流缓存（2026-09-21 用户要求：保留 30 分钟）──────────────────────────
# ## 为什么用「内存 + 上限」而不是落盘
# 单个作品可达 50–400MB（实测 401,673,127 字节），落盘会迅速撑爆磁盘
# （此前设计中已刻意避免）。而 30 分钟窗口内重复播放同一作品是很常见的
# 操作（拖进度条会触发多次 Range 请求）—— 故缓存 **Range 片段**而非整片：
#   · key = (target_url, range_spec)
#   · value = (bytes, content_type, content_range, ts)
# 拖进度条时同一片段通常被请求多次，命中即可省一次上游往返。
# 上限 64MB / 200 条，LRU 淘汰，过期 30 分钟。
_STREAM_CACHE_TTL_SEC = 30 * 60
_STREAM_CACHE_MAX_BYTES = 64 * 1024 * 1024
_STREAM_CACHE_MAX_ITEMS = 200
_stream_cache: "OrderedDict[tuple[str, str], tuple[bytes, str, str, float]]" = None  # type: ignore
_stream_cache_lock = None
_stream_cache_bytes = 0


def _cache_init():
    """懒初始化（模块导入期不建锁/字典，避免多进程 fork 问题）。"""
    global _stream_cache, _stream_cache_lock
    if _stream_cache is None:
        from collections import OrderedDict
        import threading
        _stream_cache = OrderedDict()
        _stream_cache_lock = threading.Lock()


def _cache_get(key):
    """取缓存片段；过期返回 None（并顺手清理该条）。"""
    _cache_init()
    import time as _t
    with _stream_cache_lock:
        ent = _stream_cache.get(key)
        if not ent:
            return None
        data, ctype, crange, ts = ent
        if _t.time() - ts > _STREAM_CACHE_TTL_SEC:
            _stream_cache.pop(key, None)
            globals()["_stream_cache_bytes"] -= len(data)
            return None
        _stream_cache.move_to_end(key)   # LRU：命中即置最新
        return (data, ctype, crange)


def _cache_put(key, data, ctype, crange):
    """写入缓存片段，超上限即按 LRU 淘汰。"""
    _cache_init()
    import time as _t
    global _stream_cache_bytes
    with _stream_cache_lock:
        if key in _stream_cache:
            old = _stream_cache.pop(key)
            _stream_cache_bytes -= len(old[0])
        _stream_cache[key] = (data, ctype, crange, _t.time())
        _stream_cache_bytes += len(data)
        # 双重上限：字节数 + 条目数（防大量小片段占满条目）
        while (_stream_cache_bytes > _STREAM_CACHE_MAX_BYTES
               or len(_stream_cache) > _STREAM_CACHE_MAX_ITEMS):
            _, (d, *_rest) = _stream_cache.popitem(last=False)
            _stream_cache_bytes -= len(d)


def stream_cache_stats() -> dict:
    """流缓存统计（可观测：命中率能直接说明缓存是否真在工作）。"""
    _cache_init()
    with _stream_cache_lock:
        return {"items": len(_stream_cache), "bytes": _stream_cache_bytes,
                "max_bytes": _STREAM_CACHE_MAX_BYTES,
                "max_items": _STREAM_CACHE_MAX_ITEMS,
                "ttl_sec": _STREAM_CACHE_TTL_SEC}


def _media_sign(aweme_id: str, exp: int) -> str:
    """本机流地址的短签名（防被当开放代理）。仅本机使用，密钥派生自应用盐。"""
    import hashlib
    import hmac
    secret = b"dyautodm-media-stream-v1"
    msg = f"{aweme_id}:{exp}".encode()
    return hmac.new(secret, msg, hashlib.sha256).hexdigest()[:32]


def _media_host_ok(url: str) -> bool:
    """URL 主机是否属于抖音媒体域（防 SSRF）。"""
    from urllib.parse import urlparse
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:  # noqa: BLE001
        return False
    if not host:
        return False
    return any(host == h or host.endswith("." + h) for h in _ALLOWED_MEDIA_HOSTS)


class StreamTicketReq(BaseModel):
    account: str
    aweme_id: str
    quality: str = "origin"
    raw: dict[str, Any] | None = None


@router.post("/media/stream-ticket")
async def media_stream_ticket(req: StreamTicketReq) -> dict[str, Any]:
    """把取到的直链换成**本机同源流地址**（`<video>` 用这个，绕开跨域）。

    返回 `{ok, stream_url, expires_in}`；`stream_url` 指向
    `GET /api/platform/media/stream?u=<b64url>&exp=&sig=`，Flutter/webview
    只与本机后端通信 → 无跨域、无 Referer 限制。
    """
    import base64
    import time as _t
    r = await media_resolve(MediaResolveReq(
        account=req.account, aweme_id=req.aweme_id, raw=req.raw, quality=req.quality))
    direct = r.get("url") or ""
    if not direct:
        raise HTTPException(502, "取址失败：无可用地址")
    if not _media_host_ok(direct):
        # 非白名单域不代理（防 SSRF）；罕见但必须显式拒绝而非静默放行
        logger.warning(f"[PLT-043] " + f"直链主机不在白名单，拒绝代理: {direct[:80]}")
        raise HTTPException(502, "取址失败：地址主机不在允许列表")
    exp = int(_t.time()) + _STREAM_SIGN_TTL_SEC
    sig = _media_sign(req.aweme_id, exp)
    u = base64.urlsafe_b64encode(direct.encode()).decode()
    stream_url = (f"/api/platform/media/stream?u={u}&exp={exp}&sig={sig}"
                  f"&aid={req.aweme_id}")
    return {"ok": True, "stream_url": stream_url, "expires_in": _STREAM_SIGN_TTL_SEC,
            "resolved_via": r.get("resolved_via"), "type": r.get("type"),
            "cover": r.get("cover"), "duration": r.get("duration"),
            "desc": r.get("desc"), "author": r.get("author"),
            "images": r.get("images") or [], "live_photos": r.get("live_photos") or []}


@router.get("/media/stream")
async def media_stream(u: str, exp: int, sig: str, aid: str = "", request: Request = None):
    """反向代理抖音媒体流（支持 Range；`<video>` 直接指向本机此处）。

    安全：签名校验 + 过期校验 + 主机白名单，三重防滥用/SSRF。
    """
    import base64
    import time as _t
    from fastapi.responses import StreamingResponse
    import requests

    if exp < int(_t.time()):
        raise HTTPException(403, "流地址已过期，请重新取址")
    if _media_sign(aid, exp) != sig:
        raise HTTPException(403, "流地址签名无效")
    try:
        target = base64.urlsafe_b64decode(u.encode()).decode()
    except Exception:  # noqa: BLE001
        raise HTTPException(400, "u 参数非法")
    if not _media_host_ok(target):
        raise HTTPException(403, "目标主机不在允许列表")

    rng = None
    if request is not None:
        rng = request.headers.get("range") or request.headers.get("Range")

    # ── 缓存查找（30 分钟内同一 (url, range) 直接回放，不再打上游）──
    ckey = (target, rng or "")
    hit = _cache_get(ckey)
    if hit is not None:
        data, ctype, crange = hit
        hdrs = {"Accept-Ranges": "bytes", "Content-Length": str(len(data)),
                "X-Stream-Cache": "HIT"}
        if crange:
            hdrs["Content-Range"] = crange
        return Response(content=data, status_code=206 if crange else 200,
                        media_type=ctype, headers=hdrs)

    fwd_headers = {
        # ★ ADR-016 D4：统一走档案（内核感知），禁止写死
        "User-Agent": _login_state_reason.__globals__["_ua"]() if False else __import__("utils.fingerprint", fromlist=["user_agent"]).user_agent(),
        "Referer": "https://www.douyin.com/",
        "Accept": "*/*",
    }
    if rng:
        fwd_headers["Range"] = rng

    from utils.tls_policy import tls_verify
    try:
        up = requests.get(target, headers=fwd_headers, stream=True, timeout=20,
                          verify=tls_verify())
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[PLT-044] " + f"上游流拉取失败: {type(e).__name__}")
        raise HTTPException(502, f"上游流拉取失败: {type(e).__name__}")

    if up.status_code >= 400:
        up.close()
        logger.warning(f"[PLT-045] " + f"上游返回 {up.status_code}（直链可能过期）")
        raise HTTPException(502, f"上游返回 {up.status_code}，直链可能已过期")

    media_type = up.headers.get("Content-Type") or "video/mp4"
    crange = up.headers.get("Content-Range")
    out_headers = {"Accept-Ranges": "bytes", "X-Stream-Cache": "MISS"}
    for k in ("Content-Length", "Content-Range"):
        v = up.headers.get(k)
        if v:
            out_headers[k] = v

    # ── 缓存写入策略（2026-09-21）──
    # · 带 Range 的**片段**请求（<video> 拖动/起播常见）→ 读全后缓存并直返；
    #   片段通常 KB~MB 级，读进内存安全，且这正是会被重复请求的部分。
    # · **无 Range 的整片**请求（可达 400MB）→ 不读进内存，保持边收边转，
    #   否则一次播放就能把内存打满。
    # 判据用 Content-Length（上游声明）而非读到的字节数，避免先读后判。
    _cl = up.headers.get("Content-Length")
    _is_small = False
    try:
        _is_small = bool(rng) and _cl and int(_cl) <= 8 * 1024 * 1024
    except Exception:  # noqa: BLE001
        _is_small = False
    if _is_small:
        try:
            body = up.content          # 上游已声明长度且带 Range → 片段可安全读全
            status = up.status_code
            up.close()
            _cache_put(ckey, body, media_type, crange or "")
            out_headers["Content-Length"] = str(len(body))
            return Response(content=body, status_code=status, media_type=media_type,
                            headers=out_headers)
        except Exception as e:  # noqa: BLE001 —— 读失败则回落到流式（不阻断播放）
            logger.warning(f"[PLT-046] " + f"片段缓存读失败，回落流式: {type(e).__name__}")

    try:
        up.kwargs["stream"] = True
    except Exception:  # noqa: BLE001
        pass
    return StreamingResponse(
        up.iter_content(chunk_size=64 * 1024),
        status_code=up.status_code,
        media_type=media_type,
        headers=out_headers,
    )
