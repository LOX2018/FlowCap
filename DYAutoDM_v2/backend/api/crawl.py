# coding=utf-8
"""数据采集路由：关键词搜索视频/用户 + 评论采集 + 评论转私信截流

整合 GitHub cxiniao/- (抖音截流私信获客系统) 的功能规格与基座 DY_Spider_base
在 V2 中已继承的真实接口链路：
  - DouyinAPI.search_some_general_work  关键词搜视频（排序/发布时间/时长筛选）
  - DouyinAPI.search_some_user          关键词搜用户
  - DouyinAPI.get_work_out_comment      作品一级评论（带游标，可限量）
  - core.sender.send_by_uid             私信直发（优先 recv_daemon 统一发送闸门）

风控约束（铁律）：
  - 采集一律复用已有账号 .env 凭证被动签名，与手动网页浏览同源同指纹；
  - 搜索/评论结果自带的 nickname/uid 直接使用，绝不补发任何批量用户信息查询。

DouyinAPI 是同步 requests 实现，路由内一律 asyncio.to_thread 包裹，
避免阻塞事件循环（与 sender.py 的做法一致）。
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from fastapi import APIRouter, HTTPException
from loguru import logger
from pydantic import BaseModel

from database import exec_modify, exec_query
from services import high_value_keywords as _hv

router = APIRouter()


# ---------------------------------------------------------------------------
# 请求模型
# ---------------------------------------------------------------------------
class CrawlSearchRequest(BaseModel):
    account: str
    query: str
    kind: str = "video"          # video | user
    # ★ 2026-09-30 接线：以下四项**留空 = 用采集策略**（`crawl_policies`，按账号标签
    #   scope 解析）；显式传值则覆盖策略。空串是「未指定」而非「值 = 空」。
    sort_type: str = ""          # 排序 0 综合 / 1 最多点赞 / 2 最新发布（仅 video）
    publish_time: str = ""       # 发布时间 0 不限 / 1 一天内 / 7 一周内 / 180 半年内
    filter_duration: str = ""    # 视频时长 '' 不限 / 0-1 / 1-5 / 5-10000
    num: int = 0                 # 搜索条数（0 = 用策略 num；上限 50 由策略侧 clamp）
    policy_id: str = ""          # 指定采集策略 id（空 = 用该账号绑定/全局默认）


class CrawlCommentsRequest(BaseModel):
    account: str
    aweme_id: str
    limit: int = 100             # 一级评论上限，防止热评视频全量拉取过久
    count: int = 0               # 单页条数（0=用配置中心值；实测 5~50）


class CrawlCommentsBatchRequest(BaseModel):
    """多作品批量采集评论（★ 2026-09-30 方案1，v0.45.125）。

    🔴 `extra="forbid"`（★ 2026-10-03 新增，**必读**）：
    Pydantic 默认**静默丢弃**未知字段。实测旧 sidecar（0.46.22）对
    `start_date` 返回 200 且**不生效** —— 用户填了日期却被静默忽略，
    表现为「日期筛选没用」而毫无报错，正是本项目「禁止假成功」红线。
    ⇒ 显式禁止多余字段：前端比后端新时，**响亮地 422**，而不是假装成功。
    （同模型被 `/comments/batch/cancel` 复用，cancel 不发日期字段，无影响。）

    只**采集**，不发送（与 `/batch` 的「采集+私信」语义分开，避免误触发写操作）。
    刻意**串行 + 间隔**（配置中心 `crawl.batch_interval`），不开并发。

    ★ 2026-10-02 改造：
      · `min_score` > 0 时，只保留关键词权重得分 >= min_score 的评论（高价值过滤）。
      · 只保留有效 UID（uid 非空）的评论。
      · 支持通过 `/comments/batch/cancel` 终止正在进行的批量采集。

    ★ 2026-10-03 新增：
      · `start_date` / `end_date`：只采集指定日期范围内的评论（YYYY-MM-DD）。
    """
    model_config = {"extra": "forbid"}
    account: str
    aweme_ids: list[str] = []
    limit: int = 100             # 每作品评论上限
    count: int = 0               # 单页条数（0=配置中心值）
    min_score: int = 0           # 高价值关键词最低得分（0=不过滤）
    start_date: str = ""         # 评论日期范围起（YYYY-MM-DD，空=不限）
    end_date: str = ""           # 评论日期范围止（YYYY-MM-DD，空=不限）
    # ★ 2026-10-03：指定采集策略 id（空 = 用该账号绑定/全局默认策略）。
    #   此前 `policy_id` **只有 `/search` 端点认**，批量采集完全无视它 ⇒
    #   悬浮窗里选策略毫无效果（假成功）。现接入，与搜索端点同源
    #   （`_resolve_policy_params`），策略层才真正覆盖采集链路。
    policy_id: str = ""
    # ★ 2026-10-03（用户指令）：本次显式选的高价值标签（方案A）。
    #   空 = 沿用账号在 crawl 板块的绑定标签。
    #   ⚠️ 本模型是 `extra="forbid"`（见类 docstring）—— 缺这个字段时
    #   前端传 tag_id 会直接 **400**，不是静默忽略。
    tag_id: str = ""


class CrawlDmRequest(BaseModel):
    account: str
    uid: str
    text: str


class CrawlBatchRequest(BaseModel):
    """批量截流任务：采集评论区 → 按关键词筛选 → 逐条批量私信（走统一发送闸门）。"""
    account: str
    aweme_id: str
    text: str
    keyword: str = ""           # 可选：评论内容含该关键词才发（空=全部）
    limit: int = 100            # 评论拉取上限
    count: int = 0              # 单页条数（0=配置中心值）
    max_send: int = 0           # 可选：最多发 N 条（0=不限）
    interval: float = 0.0       # 可选：每条之间额外间隔秒（默认由闸门节流）


# ---------------------------------------------------------------------------
# 凭证与辅助
# ---------------------------------------------------------------------------
def _load_auth(account: str):
    """按账号名加载 .env 凭证为 DouyinAuth（与 messages.py 同一链路）。"""
    from dy_apis.login_api import DYLoginApi
    from auto_dm import accounts as acc

    env_path = acc.env_path_of(account)
    if not env_path:
        raise HTTPException(400, f"账号 {account} 不存在")
    # P3：凭证收敛——无需显式判 cookie，委托 verify_credential(lightweight=True) 做完整静态检查
    from auto_dm.accounts import verify_credential
    _vc = verify_credential(account, lightweight=True)
    if not _vc["ok"]:
        raise HTTPException(400, f"账号 {account} 凭证不完整（{_vc['wp']['detail']}），请先在账号管理页扫码登录")
    auth = DYLoginApi._load_auth_from_env(env_path)
    return auth


def _unwrap(env) -> list:
    """拆开 `features` 封装层的信封，取出列表载荷。

    🔴 2026-10-02（架构契约修复）：`features.*` 统一返回
    `{"ok": bool, "data": [...], "error": str?}` **信封**；而本模块的
    `_map_video` / `_map_user` 期望**裸列表**（原实现直连 `DouyinAPI`，
    后者才返回裸列表）。v0.46.3「搜索不可匿名，移回凭证」那次把调用改成
    直连，连带让 features 封装层被绕过 —— `test_features_wiring` 的哨兵
    （直连即抛）正是为抓这个而存在。

    现回到「走封装层」的设计意图，故必须在此**显式拆信封**：

      · `ok=False` ⇒ **fail-closed**，抛 502（绝不把失败吞成空列表 = 假成功，
        项目铁律）。原直连实现里底层异常会自然冒泡，语义一致。
      · `ok=True` 但 `data` 非 list ⇒ 视为结构异常，同样 fail-closed。
    """
    if isinstance(env, dict):
        if not env.get("ok"):
            raise RuntimeError(env.get("error") or "features 调用失败")
        data = env.get("data")
    else:
        data = env                      # 兼容直接返回列表的旧桩
    if data is None:
        return []
    if not isinstance(data, list):
        raise RuntimeError(f"features 返回结构异常：{type(data).__name__}")
    return data


def _map_video(w: dict) -> dict:
    """搜索结果条目 -> 前端视频卡字段（与旧 crawl.tsx 契约兼容）。"""
    a = w.get("aweme_info") or w
    st = a.get("statistics") or {}
    author = a.get("author") or {}
    video = a.get("video") or {}
    # 封面解析顺序：origin_cover (1080p 原图,体积大) > cover (默认封面,通常 ~720p) > dynamic_cover。
    # 旧字段 cover_url_list 已在 17.x 接口里被弃用为空；用顶层 cover dict.url_list。
    cover = ""
    try:
        for ck in ("origin_cover", "cover", "dynamic_cover"):
            c = video.get(ck)
            if not c:
                continue
            urls = c.get("url_list") if isinstance(c, dict) else None
            if urls:
                cover = urls[-1]
                break
    except Exception:
        pass
    return {
        "awemeId": a.get("aweme_id") or "",
        "title": (a.get("desc") or "").strip(),
        "plays": st.get("play_count") or 0,
        "likes": st.get("digg_count") or 0,
        "cmts": st.get("comment_count") or 0,
        "shares": st.get("share_count") or 0,
        "collects": st.get("collect_count") or 0,
        "createTime": a.get("create_time") or 0,
        "durationMs": video.get("duration") or 0,
        "cover": cover,
        "nickname": author.get("nickname") or "",
        "secUid": author.get("sec_uid") or "",
        "uid": str(author.get("uid") or author.get("user_id") or ""),
    }


def _map_user(u: dict) -> dict:
    """搜索结果条目 -> 前端用户行字段。"""
    info = u.get("user_info") or u
    return {
        "uid": str(info.get("uid") or info.get("user_id") or ""),
        "secUid": info.get("sec_uid") or "",
        "nickname": info.get("nickname") or "",
        "signature": info.get("signature") or "",
        "fans": info.get("follower_count") or 0,
        "follow": info.get("following_count") or 0,
        "works": info.get("aweme_count") or 0,
    }


def _day_range_ts(start_date: str, end_date: str, tz_hours: int = 8) -> tuple[int, int]:
    """把 `YYYY-MM-DD` 日期范围解析成 Unix 秒区间（含首尾两端当日）。

    🔴 时区铁律：日切按**本地时区**（默认 +08:00），与 `overview._day_start_ts` /
    `_crawl_stats_sync` 的 `tz`（夹取 [-12,14]）同约定。
    此前用 `timezone.utc` 会让「今天」的边界偏 8 小时 —— 早上 8 点前的评论被算到昨天。

    ⚠️ 契约：解析失败**不抛**，返回哨兵 `(0, 0)`（`hi == 0` 即「日期非法」），
    由调用方决定是回 400 还是忽略；绝不因一个畸形日期让整条采集链路 500。
    """
    try:
        off = max(-12, min(int(tz_hours), 14))
    except Exception:
        off = 8
    from datetime import datetime, timedelta, timezone

    tzinfo = timezone(timedelta(hours=off))
    lo, hi = 0, 2 ** 31 - 1
    try:
        if start_date:
            lo = int(datetime.strptime(str(start_date).strip(), "%Y-%m-%d")
                     .replace(tzinfo=tzinfo).timestamp())
        if end_date:
            hi = int(datetime.strptime(str(end_date).strip(), "%Y-%m-%d")
                     .replace(tzinfo=tzinfo).timestamp()) + 86399  # 含当日 23:59:59
    except Exception:
        return 0, 0  # 哨兵：调用方按 `hi == 0` 判「日期非法」
    return lo, hi


def _map_comment(c: dict) -> dict:
    """评论条目 -> 前端字段。uid/nickname 均为评论数据自带，无额外请求。"""
    u = c.get("user") or {}
    return {
        "cid": c.get("cid") or "",
        "awemeId": c.get("aweme_id") or "",
        "text": c.get("text") or "",
        "nickname": u.get("nickname") or "",
        "secUid": u.get("sec_uid") or "",
        "uid": str(u.get("uid") or u.get("user_id") or ""),
        "digg": c.get("digg_count") or 0,
        "ip": c.get("ip_label") or "",
        "ts": c.get("create_time") or 0,
        "replyTotal": c.get("reply_comment_total") or 0,
    }


# ---------------------------------------------------------------------------
# 采集效率参数（统一配置中心 `crawl` 分区，SSOT）
# ---------------------------------------------------------------------------
# 设计依据（2026-09-30 方案1，v0.45.125）：
#   · `count` 原硬编码 5 条/页 ⇒ 采 100 条发 20 次请求（实测耗时主因）；
#     实测服务端支持 20~50 条/页（count=20→0.41s、count=50→0.51s）。
#   · 多作品批量**刻意串行 + 间隔**：同端点高频并发是账号级限流高发区
#     （知识库 2483 封禁 / cmd609 只读态先例）。
# 取值一律走配置中心（可观测、可调、可回滚），代码不写死。
_CRAWL_FALLBACK = {"comment_page_count": 20, "batch_interval": 1.5,
                   "batch_max_works": 20, "batch_min_score": 0}


def _crawl_cfg(key: str, account: str = "", scope_override: str | None = None):
    """读 `crawl` 分区配置；**优先按标签 scope**，读不到回退默认值。

    ★ 2026-09-30 接线：此前只读全局（`scope=None`）⇒ 「采集策略」与标签的
    采集板块**都没有消费者**（`crawl_policy` 模块头自记的待办）。现按
    `config_tag.scope_of(account, "crawl")` 取该账号在采集板块应使用的标签 scope，
    与 `dm_dispatch` 的既有范式一致（标签是**指引**，参数仍由 app_config 按 scope 隔离存储）。

    ★ 2026-10-03 增 `scope_override`（方案A：本次运行的临时标签覆盖）：
    悬浮窗让用户显式选一个标签时，该标签**优先于**账号默认绑定，用于
    「按标签试跑一批」的临时切换。取值优先级：
      ① `scope_override`（本次显式选，**最高**）
      ② `config_tag.scope_of(account, "crawl")`（账号在采集板块的绑定标签）
      ③ 全局 `app_config.get("crawl", key)`（无 scope）
      ④ `_CRAWL_FALLBACK` 缺省
    ⚠️ 显式传 `scope_override=""`（空串）视为**不覆盖**，回落 ②；
       传 None 亦然。只有非空标签 id 才算覆盖。

    与 `errcode_data.py` 多处「读配置失败按默认处理」的项目约定一致：
    配置中心/标签异常**不得**让采集功能整体不可用。
    """
    if scope_override:
        try:
            from services import app_config as _ac
            v = _ac.get("crawl", key, None, scope=scope_override)
            if v is not None:
                return v
            logger.debug(f"[crawl] 覆盖标签 scope={scope_override} 无 crawl.{key}，"
                         f"继续按账号绑定回落")
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[crawl] 按覆盖标签读 crawl.{key} 失败，继续回落: {e}")
    if account:
        try:
            from services import config_tag
            scope = config_tag.scope_of(account, "crawl")
            if scope:
                from services import app_config as _ac
                v = _ac.get("crawl", key, None, scope=scope)
                if v is not None:
                    return v
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[crawl] 按标签读 crawl.{key} 失败，回落全局: {e}")
    try:
        from services import app_config
        v = app_config.get("crawl", key, None)
        if v is not None:
            return v
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[crawl] 读配置 crawl.{key} 失败，改用默认值: {e}")
    return _CRAWL_FALLBACK[key]


def _page_count(body_count=None, account: str = "") -> int:
    """确定本次**评论每页条数**（请求体 > 该账号标签/全局配置 > 缺省 20）。

    ⚠️ 与「采集策略」的 `num`（**搜索条数**上限，`crawl_policy`）是**两个量**，
    不可混用：本项管评论分页大小，`num` 管一次搜索取回多少个作品。
    """
    if body_count:
        try:
            return max(5, min(int(body_count), 50))
        except (TypeError, ValueError):
            pass
    return max(5, min(int(_crawl_cfg("comment_page_count", account)), 50))


def _fetch_work_comments(auth, aweme_id: str, limit: int, count: int) -> list[dict]:
    """**唯一**的一级评论取数实现（翻页限量，同步）。

    ★ 2026-09-30 收敛（v0.45.125）：此前 `/comments`（经 `features.work_comments`）
    与 `/batch`（直连 `DouyinAPI.get_work_out_comment`）是**两套实现** ——
    正是 `cases/2026-09-28_内容中心评论区恒加载_末级链路分叉修复` 记录的
    「同一业务末级取数链路分叉」反模式。现统一走本函数（经 features 封装层）。

    本函数是**同步 requests** 实现，调用方必须用 `asyncio.to_thread` 包裹
    （本模块约定：禁止在事件循环里直接跑同步 HTTP）。
    """
    url = f"https://www.douyin.com/video/{aweme_id}"
    max_pages = max(1, (limit + count - 1) // count + 1)
    comments: list[dict] = []
    cursor = "0"
    for _ in range(max_pages):
        import features
        r = features.work_comments(auth, url, cursor, count)
        if not r.get("ok"):
            # 失败必须上抛（外层转 502）；绝不把「采集失败」降级成「没有评论」。
            raise RuntimeError(f"评论采集失败: {r.get('error')}")
        res = r.get("data")
        batch = res.get("comments") if isinstance(res, dict) else None
        if not batch:
            break
        comments.extend(batch)
        if len(comments) >= limit or not res.get("has_more"):
            break
        cursor = str(res.get("cursor") or len(comments))
    return comments[:limit]



async def _save_history(account: str, kind: str, keyword: str, target: str,
                        items: list[dict]) -> None:
    """采集结果落库 crawl_history（失败仅告警，不阻断采集本身）。"""
    try:
        exec_modify(
            "INSERT INTO crawl_history(account,kind,keyword,target,result_count,"
            "payload,ts) VALUES(?,?,?,?,?,?,?)",
            (account, kind, keyword, target, len(items),
             json.dumps(items, ensure_ascii=False), time.time()),
        )
    except Exception as e:
        logger.warning(f"[CRAWL-001] " + f"[crawl] 采集历史落库失败（不影响本次结果）: {e}")


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------
def _resolve_policy_params(account: str, policy_id: str = "") -> dict:
    """解析该账号应使用的**采集策略参数包**（★ 2026-09-30 接线）。

    优先级（与 `config_tag.scope_of` 同构）：
      ① 显式 `policy_id`（若存在）
      ② 该账号在**采集板块**绑定的标签 scope 下的策略（`scope_of(account,"crawl")`
         指向的策略记录本身存于 kv `crawl_policies`，按 id 取）
      ③ **全局默认策略**：`crawl_policies` 中 `is_default=True` 的一条
      ④ 都没有 ⇒ `{}`（调用方回落端点默认值 —— 零回归）

    诚实边界：策略层目前是**全局命名空间**（kv `crawl_policies` 不按标签分区）；
    本函数负责把「账号 → 标签 scope → 策略」这条指引链打通，使标签的采集板块
    真正有消费者。策略记录**只存参数、不发请求**（沿用 `crawl_policy` 原有约束）。
    """
    try:
        from api import crawl_policy as _cp
        data = _cp._load_all()
    except Exception as e:  # noqa: BLE001 —— 策略不可用不得让采集失败
        logger.debug(f"[crawl] 采集策略不可用，回落端点默认: {e}")
        return {}
    if not isinstance(data, dict) or not data:
        return {}

    pid = str(policy_id or "").strip()
    if not pid and account:
        try:
            from services import config_tag
            scope = config_tag.scope_of(str(account).strip(), "crawl")
            if scope and scope in data:
                pid = scope
        except Exception:  # noqa: BLE001
            pid = ""
    if not pid:
        for k, v in data.items():
            if isinstance(v, dict) and v.get("is_default"):
                pid = k
                break
    item = data.get(pid)
    return dict(item) if isinstance(item, dict) else {}


def _search_blocked_reason(account: str, auth: Any) -> str | None:
    """空结果时复核「是风控拦截，还是真没结果」。

    ★ v0.46.18：这是「凭证有效 ≠ 搜索域有权」这条判据的**唯一落地处**。
    返回 None 表示「搜索域有权，确实无结果」（正常空）；
    返回文案表示「被平台风控拦截」（前端据此显示 blocked，不再显示空列表）。

    为什么用 `force=True`：本次搜索**刚刚**拿到空结果，若命中 60s 失败缓存
    会把「上一次的风控」误当成本次结论；必须真打一次才能归因到本次请求。
    同理，若探针这次**通**了（有结果），说明搜索域正常，那就是真没结果。
    """
    try:
        from services import search_probe
        r = search_probe.probe_search_domain(account, auth=auth, force=True)
    except Exception as e:  # noqa: BLE001 —— 复核失败不得让采集整体失败
        logger.debug(f"[crawl] 搜索域复核异常 account={account}: {e}")
        return None
    if r.get("level") in ("warn", "fail"):
        return str(r.get("label") or "被平台风控拦截")
    return None


@router.post("/search")
async def crawl_search(body: CrawlSearchRequest):
    """关键词搜索（video=综合搜索 / user=用户搜索）。

    复用基座签名链路（a_bogus 纯算签名），凭证与手动浏览同源。

    ★ 2026-09-30 接线：排序/时段/时长/条数**留空即取采集策略**（按账号标签 scope），
    显式传值则覆盖 —— 策略成为采集参数的**唯一可复用入口**。
    """
    q = (body.query or "").strip()
    if not q:
        raise HTTPException(400, "请输入搜索关键词")
    if body.kind not in ("video", "user"):
        raise HTTPException(400, f"不支持的采集类型: {body.kind}")

    # 策略解析（失败一律回落端点默认，绝不让采集整体不可用）
    pol = _resolve_policy_params(body.account, body.policy_id)
    sort_type = (body.sort_type or pol.get("sort_type") or "0")
    publish_time = (body.publish_time or pol.get("publish_time") or "0")
    filter_duration = body.filter_duration or pol.get("filter_duration") or ""
    num = int(body.num or pol.get("num") or 20)
    num = max(1, min(num, 60))

    from services.auth_policy import get_auth_for
    auth = get_auth_for("/api/crawl/search", body.account)
    if auth is None:
        # 实测搜索不可匿名（sc=2483 / 404 Janus）⇒ fail-closed
        raise HTTPException(
            503, "采集搜索需登录态凭证（实测匿名被风控拒绝）；请先完成账号登录")

    from features import search_user as _fsu, search_work as _fsw

    try:
        if body.kind == "video":
            env = await asyncio.to_thread(
                _fsw, auth, q, num, sort_type, publish_time, filter_duration,
            )
            raw = _unwrap(env)
            items = [_map_video(w) for w in raw if (w or {}).get("aweme_info")]
        else:
            env = await asyncio.to_thread(
                _fsu, auth, q, num,
            )
            raw = _unwrap(env)
            items = [_map_user(u) for u in raw]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[CRAWL-002] " + f"[crawl] 搜索失败 account={body.account} q={q}: {e}")
        raise HTTPException(502, f"搜索失败: {e}") from e

    # ★ v0.46.18「禁止假成功」：空结果必须区分「真没搜到」与「被平台风控拦截」。
    #   实测（2026-10-02）平台对搜索域下发业务层风控时，响应是
    #   **HTTP 200 + status_code=0 + data=[] + search_nil_info.search_nil_type=
    #   "verify_check"** —— 传输层完全正常，`_transport` 为 None，
    #   所以下面这层「读传输层事实」的旧逻辑**完全测不到**它，
    #   结果就是「命中 0 条」（把风控说成没结果）。
    #   ⇒ 空结果时用 `services.search_probe` 复核搜索域权限，如实回 blocked。
    #   为什么用探针而不是直接读 `search_nil_info`：`client_search.search_some_general_work`
    #   尚未把该字段带出（直播那条线正在改），而**本文件不与那条线冲突**。
    blocked_reason: str | None = None
    if not items:
        blocked_reason = await asyncio.to_thread(_search_blocked_reason, body.account, auth)
    if blocked_reason:
        logger.warning(f"[CRAWL-004] [crawl] 搜索被风控拦截 account={body.account} "
                       f"kind={body.kind} q={q}: {blocked_reason}")
        # 风控时不落历史：空结果不是一次有效采集，写进去会污染统计
        return {"ok": True, "items": [], "total": 0,
                "blocked": True, "blocked_reason": blocked_reason}

    await _save_history(body.account, body.kind, q, "", items)
    logger.info(f"[crawl] 搜索完成 account={body.account} kind={body.kind} "
                f"q={q} 命中={len(items)}")
    return {"ok": True, "items": items, "total": len(items)}


@router.post("/comments")
async def crawl_comments(body: CrawlCommentsRequest):
    """采集作品一级评论（带游标限量拉取，防止热门视频全量过久）。

    评论数据自带 user.uid / user.nickname（主动抓取零额外请求），
    前端可直接对评论作者一键私信，实现评论截流。
    """
    aweme_id = (body.aweme_id or "").strip()
    if not aweme_id:
        raise HTTPException(400, "缺少作品 ID")
    limit = max(1, min(int(body.limit or 100), 300))
    count = _page_count(body.count, body.account)

    auth = _load_auth(body.account)

    try:
        raw = await asyncio.to_thread(_fetch_work_comments, auth, aweme_id, limit, count)
    except Exception as e:
        logger.error(f"[CRAWL-003] " + f"[crawl] 评论采集失败 account={body.account} aweme={aweme_id}: {e}")
        raise HTTPException(502, f"评论采集失败: {e}") from e

    items = [_map_comment(c) for c in raw]
    await _save_history(body.account, "comment", "", aweme_id, items)
    logger.info(f"[crawl] 评论采集完成 account={body.account} aweme={aweme_id} "
                f"共 {len(items)} 条（每页 {count}）")
    return {"ok": True, "items": items, "total": len(items)}


@router.post("/comments/batch")
async def crawl_comments_batch(body: CrawlCommentsBatchRequest):
    """多作品批量采集一级评论（★ 2026-09-30 方案1，v0.45.125）。

    ## 为什么是「串行 + 间隔」而不是并发
    同一端点（`comment/list`）的高频并发请求是**账号级限流**的高发区
    （知识库先例：搜索 2483 封禁、cmd609 只读态）。故本实现**刻意串行**，
    两个作品之间停顿 `crawl.batch_interval`（默认 1.5s，配置中心可调）。
    这是「慢一点但可跑完」对「快一点但可能废号」的取舍。

    ## 失败隔离
    单个作品失败**不**中断整批：该作品记 `status="failed"` 并继续下一个；
    每作品成功数进 `per_work`，总数进 `total_comments`。**不**把失败静默成 0 条。

    ★ 2026-10-02 改造：
      · `min_score` > 0 时，只保留关键词权重得分 >= min_score 的评论（高价值过滤）。
      · 只保留有效 UID（uid 非空）的评论。
      · 支持通过 `/comments/batch/cancel` 终止正在进行的批量采集。
    """
    ids: list[str] = []
    seen: set[str] = set()
    for x in (body.aweme_ids or []):
        s = str(x or "").strip()
        if s and s not in seen:
            seen.add(s)
            ids.append(s)
    if not ids:
        raise HTTPException(400, "缺少作品 ID 列表")
    max_works = max(1, int(_crawl_cfg("batch_max_works", body.account)))
    if len(ids) > max_works:
        raise HTTPException(
            400, f"单次批量最多 {max_works} 个作品（当前 {len(ids)}），请分批")
    interval = max(0.0, float(_crawl_cfg("batch_interval", body.account)))
    limit = max(1, min(int(body.limit or 100), 300))
    count = _page_count(body.count, body.account)
    # ★ 2026-10-03（用户指令）：批量采集接入高价值关键词过滤 ——
    #   **只保留关键词命中的评论，其余全部丢弃**。
    #
    #   为何此前形同虚设：前端恒传 `min_score: 0`，而本行只读 body ⇒
    #   `min_score > 0` 永假 ⇒ 过滤分支从未执行（假成功）。
    #
    #   契约与**批量私信**端点（`/dm/batch`，见下方同款实现）**完全一致**：
    #     ① 配置中心 `crawl.batch_min_score` 是**唯一权威来源**；
    #     ② 请求体 `min_score` 仅作「本次覆盖」，**0 视为不覆盖**
    #        （否则前端每次传 0 又把门槛踩回不过滤 —— 正是要消灭的缺陷）；
    #     ③ 本次显式选的标签 `tag_id` **优先于**账号默认绑定（方案A）。
    _tag_scope = (getattr(body, "tag_id", "") or "").strip() or None
    _cfg_min = _crawl_cfg("batch_min_score", body.account, _tag_scope)
    try:
        _cfg_min = int(_cfg_min)
    except (TypeError, ValueError):
        _cfg_min = 0
    try:
        _req_min = int(body.min_score or 0)
    except (TypeError, ValueError):
        _req_min = 0
    min_score = max(0, _cfg_min) if _req_min <= 0 else max(0, _req_min)
    if _tag_scope:
        logger.info(f"[crawl] 批量采集按本次选中标签取门槛: tag={_tag_scope} "
                    f"配置={_cfg_min} → 生效={min_score} account={body.account}")
    # ★ 2026-10-03 拆分：策略的**评论上限**读 `comment_limit`（新字段），
    #   **不再读 `num`** —— `num` 的语义是「搜索条数」，两者不可混用
    #   （`_page_count` docstring 早已明写该约定，此前误接 num 违反之）。
    #   约定：仅在请求体**未显式**给 limit 时生效（显式值优先）。
    _pol = _resolve_policy_params(body.account, body.policy_id)
    if _pol:
        try:
            p_climit = int(_pol.get("comment_limit") or 0)
        except (TypeError, ValueError):
            p_climit = 0
        if p_climit > 0 and not body.limit:
            limit = max(1, min(p_climit, 300))
        logger.info(f"[crawl] 批量采集按策略取参数: policy={body.policy_id or '(默认)'} "
                    f"comment_limit={p_climit} limit={limit} account={body.account}")
    # ★ 2026-10-03：日期范围（本地时区日切；非法日期回 400，不静默忽略）
    lo_ts, hi_ts = _day_range_ts(body.start_date, body.end_date)
    if hi_ts == 0 and (body.start_date or body.end_date):
        raise HTTPException(
            400, "日期格式非法，须为 YYYY-MM-DD（如 2026-10-01）")
    if lo_ts > hi_ts:
        raise HTTPException(400, "起始日期不能晚于结束日期")

    auth = _load_auth(body.account)

    # ★ 2026-10-02：取消标志（按账号隔离）
    cancel_key = f"batch_cancel:{body.account}"
    _batch_cancel_flags.discard(cancel_key)

    per_work: list[dict] = []
    total = 0
    # ★ 2026-10-04：被高价值过滤掉的条数（回传给前端，让「0 条」可诊断）
    batch_filtered = 0
    cancelled = False

    # ★ 2026-10-04（用户定调）：**按作品数量分配采集策略** ——
    #   单作品与批量是**同一条路**，只是策略参数不同，不存在「两条实现」。
    #     · 1 个作品：翻页取满（`limit` 生效），节流 0（无需串行等待）；
    #     · ≥2 个作品：按 `batch_interval` 串行节流，避免连打被风控。
    #   ⚠️ 判据写在配置中心（`crawl.batch_interval`），此处不做硬编码分支
    #      ——「策略」是数据，不是 if/else。
    _n = len(ids)
    if _n <= 1:
        interval = 0.0
        logger.info(f"[crawl] 采集策略=单作品（翻页取满 limit={limit}）"
                    f" account={body.account}")
    else:
        logger.info(f"[crawl] 采集策略=批量（{_n} 个作品，节流 {interval}s）"
                    f" account={body.account}")
    for i, aweme_id in enumerate(ids):
        # 检查取消标志
        if cancel_key in _batch_cancel_flags:
            cancelled = True
            logger.info(f"[crawl] 批量采集被用户终止 account={body.account} "
                        f"已完成 {i}/{len(ids)}")
            break
        if i:
            await asyncio.sleep(interval)   # ★ 串行节流（配置中心可调）
        try:
            raw = await asyncio.to_thread(
                _fetch_work_comments, auth, aweme_id, limit, count)
            items = [_map_comment(c) for c in raw]
            # ★ 2026-10-02：只保留有效 UID
            items = [c for c in items if c.get("uid")]
            # ★ 2026-10-03：日期范围过滤（时区与日切统计一致，见 _day_range_ts）
            if lo_ts <= hi_ts:
                items = [c for c in items if lo_ts <= (c.get("ts") or 0) <= hi_ts]
            # ★ 2026-10-02：高价值关键词过滤
            if min_score > 0:
                scope = _hv_scope(body.account)
                _before = len(items)
                items = [c for c in items
                         if _hv.score_text(c.get("text", ""), scope) >= min_score]
                # 🔴 2026-10-04：过滤后**必须留痕**，否则「采到 0 条」
                #   无法区分「作品真没评论」与「被过滤光了」（后者是配置问题，
                #   用户会误判为采集坏了 —— 实测连着 7 批 n=0 就是这么来的）。
                #   全滤掉且原集合非空 ⇒ WARN + 记入响应，让 UI/日志可诊断。
                if _before and not items:
                    logger.warning(
                        f"[CRAWL-008] [crawl] 高价值过滤后为 0 条"
                        f"（过滤前 {_before} 条，门槛={min_score}，"
                        f"标签={getattr(body, 'tag_id', '') or '(账号绑定)'}"
                        f"）⇒ 该标签的关键词表可能为空或不匹配，"
                        f"并非采集失败。如需全采请不选标签或把门槛设为 0")
                batch_filtered += (_before - len(items))
            total += len(items)
            per_work.append({"aweme_id": aweme_id, "status": "ok",
                             "count": len(items), "items": items})
            await _save_history(body.account, "comment_batch", "", aweme_id, items)
            logger.info(f"[crawl] 批量采集 {i + 1}/{len(ids)} aweme={aweme_id} "
                        f"共 {len(items)} 条")
        except Exception as e:  # noqa: BLE001 —— 单作品失败不拖垮整批
            per_work.append({"aweme_id": aweme_id, "status": "failed",
                             "count": 0, "items": [], "error": str(e)})
            logger.error(f"[CRAWL-006] " + f"[crawl] 批量采集单作品失败 "
                         f"aweme={aweme_id}: {e}")

    ok_works = sum(1 for w in per_work if w["status"] == "ok")
    logger.info(f"[crawl] 批量采集完成 account={body.account} "
                f"作品 {ok_works}/{len(ids)} 成功，共 {total} 条评论"
                f"{'（已终止）' if cancelled else ''}")
    return {"ok": True, "works": len(ids), "ok_works": ok_works,
            "total_comments": total, "per_work": per_work,
            # ★ 2026-10-04：被高价值过滤掉的条数。前端据此提示
            #   「本批 0 条是因为过滤，不是采集失败」——否则用户无法区分。
            "filtered": batch_filtered,
            "cancelled": cancelled}


# ★ 2026-10-02：批量采集取消标志集（按账号隔离）
_batch_cancel_flags: set[str] = set()


def _hv_scope(account: str) -> str | None:
    """获取账号的高价值关键词标签 scope（与 crawl 配置同链路）。"""
    try:
        from services import config_tag
        return config_tag.scope_of(account, "crawl") or None
    except Exception:
        return None


@router.post("/comments/batch/cancel")
async def crawl_comments_batch_cancel(body: CrawlCommentsBatchRequest):
    """终止正在进行的批量采集（★ 2026-10-02）。

    设置取消标志，批量采集循环会在下一个作品开始前检查并退出。
    """
    cancel_key = f"batch_cancel:{body.account}"
    _batch_cancel_flags.add(cancel_key)
    logger.info(f"[crawl] 批量采集终止请求 account={body.account}")
    return {"ok": True, "message": "终止信号已发送，采集将在当前作品完成后停止"}


class CrawlAnonPreviewRequest(BaseModel):
    """匿名评论预览（★ 2026-09-30 C 方案探针）。

    **不需要 account**：走零凭证移动端点，可在未登录会话下预览。
    结果只用于「哪些视频评论值得采」，**不能**直接私信（无数字 uid）。
    """
    aweme_ids: list[str] = []
    count: int = 0               # 单页条数（0=用配置中心值；该端点实际被忽略）
    limit: int = 0               # 只预览前 N 个作品（0=全部，上限=anon_preview_max_works）


@router.post("/comments/anon-preview")
async def crawl_comments_anon_preview(body: CrawlAnonPreviewRequest):
    """匿名预览多作品评论（★ 2026-09-30 C 方案「探针」）。

    ## 定位（诚实边界，勿当采集主力）
    走 `iesdouyin.com` 移动端点：**零凭证**、**零账号风控面**，但每个作品
    只返回**同一批 ≤20 条热门预览**、**不可翻页**、**无数字 uid**（不可私信）。
    用途：搜索后零风险地先看「哪些视频有评论值得全量采」。

    ## 为什么默认并发（★ 2026-09-30 修订）
    本端点是**零凭证**（不携带任何账号 cookie）⇒ 请求**不落在账号风控面**上，
    只是匿名 IP 的普通请求。故默认**有界并发**（`crawl.anon_preview_concurrency`）
    以求「搜索一返回就尽快拿到预览」，而非串行等待。并发上限可配；设为 1 即回退串行。

    ## 失败隔离
    单个作品失败不影响整批（记 `status="failed"`，继续下一个）。
    """
    ids: list[str] = []
    seen: set[str] = set()
    for x in (body.aweme_ids or []):
        s = str(x or "").strip()
        if s and s not in seen:
            seen.add(s)
            ids.append(s)
    if not ids:
        raise HTTPException(400, "缺少作品 ID 列表")
    max_works = max(1, int(_crawl_cfg("anon_preview_max_works")))
    ids = ids[:max_works]
    if body.limit and body.limit > 0:
        ids = ids[:int(body.limit)]
    interval = max(0.0, float(_crawl_cfg("anon_preview_interval")))
    conc = max(1, min(int(_crawl_cfg("anon_preview_concurrency")), 12))
    sem = asyncio.Semaphore(conc)
    import features

    async def _one(aid: str) -> dict:
        async with sem:
            try:
                r = await asyncio.to_thread(features.work_comments_anon, aid)
                # ★ 失败必须显式上抛 ⇒ 记 failed；**绝不**把「取不到」记成
                #   「ok 且 0 条」（本项目禁止的「把失败静默成空」，A4 门禁守护）。
                if not r.get("ok"):
                    raise RuntimeError(f"匿名预览失败: {r.get('error')}")
                res = r.get("data")
                raw = (res.get("comments") if isinstance(res, dict) else None) or []
                items = [_map_comment(c) for c in raw]
                return {"aweme_id": aid, "status": "ok", "count": len(items),
                        "items": items}
            except Exception as e:  # noqa: BLE001 —— 单作品失败不拖垮整批
                logger.warning(f"[CRAWL-007] [crawl] 匿名预览失败 aweme={aid}: {e}")
                return {"aweme_id": aid, "status": "failed", "count": 0,
                        "items": [], "error": str(e)}

    # 并发抓取；间隔仅当 conc==1（串行）时生效 —— 并发下由信号量限流，无需再停
    per_work: list[dict] = []
    if conc == 1 and interval > 0:
        for i, aid in enumerate(ids):
            if i:
                await asyncio.sleep(interval)
            per_work.append(await _one(aid))
    else:
        per_work = list(await asyncio.gather(*[_one(a) for a in ids]))

    total = sum(w["count"] for w in per_work)
    ok_works = sum(1 for w in per_work if w["status"] == "ok")
    logger.info(f"[crawl] 匿名预览完成 作品 {ok_works}/{len(ids)} 成功，"
                f"共 {total} 条（**仅供预览，不可翻页/无私信 uid**）")
    return {"ok": True, "anonymous": True, "works": len(ids),
            "ok_works": ok_works, "total_comments": total, "per_work": per_work}


@router.post("/dm")
async def crawl_dm(body: CrawlDmRequest):
    """对评论/搜索结果中的用户直发私信（评论截流）。

    复用 core.sender.send_by_uid：优先 recv_daemon 统一发送闸门
    （三源一配额），守护不可达时兜底本进程 imapi 直发。
    """
    uid = (body.uid or "").strip()
    text = (body.text or "").strip()
    if not uid or not text:
        raise HTTPException(400, "缺少目标 uid 或私信文案")

    auth = _load_auth(body.account)
    try:
        auth.account_name = body.account
    except Exception:
        pass

    from core.sender import send_by_uid

    try:
        ok, reason = await asyncio.to_thread(send_by_uid, auth, uid, text)
    except Exception as e:
        logger.error(f"[CRAWL-004] " + f"[crawl] 私信发送异常 account={body.account} uid={uid}: {e}")
        raise HTTPException(502, f"私信发送异常: {e}") from e

    logger.info(f"[crawl] 私信发送 account={body.account} uid={uid} ok={ok} reason={reason}")
    return {"ok": bool(ok), "reason": reason}


class CrawlDmBatchRequest(BaseModel):
    """批量私信（★ 2026-10-02）：从已采集评论中筛选候选并逐条发送。

    与 `/batch` 的区别：`/batch` 会重新采集评论，本端点接收前端已采集的
    评论数据，只负责筛选 + 发送。筛选逻辑（高价值关键词）在后端执行，
    避免前端与后端两套筛选逻辑的契约漂移。

    🔴 `extra="forbid"`（★ 2026-10-03）：与 `CrawlCommentsBatchRequest` 同理 ——
    Pydantic 默认 `extra='ignore'` 会**静默丢弃**前端多发的字段（如 `tag_id`），
    表现为「选了标签/填了条数却毫无效果」且无任何报错。显式禁止 ⇒ 版本错配
    响亮地 422，符合项目「禁止假成功」红线。
    """
    model_config = {"extra": "forbid"}
    account: str
    # ★ 2026-10-03（用户指令「私信复用标签」）：`text` 改为**可选** ——
    #   留空即由本端点按标签取 `send.dm_pool` 首条（见下方取值优先级）。
    #   ⚠️ 此前是 `text: str`（必填）⇒ 前端删掉输入框后不传该字段会 **422**，
    #   复用标签的分支永远走不到。默认值给 "" 而非 None，与 `(body.text or "")`
    #   的既有读法一致。
    text: str = ""
    items: list[dict] = []  # [{uid, nickname, text}, ...]
    min_score: int = 0      # 高价值关键词最低得分（0=不过滤）
    max_send: int = 0       # 最多发 N 条（0=不限）
    interval: float = 0.0   # 每条之间额外间隔秒
    # ★ 2026-10-03（方案A）：本次显式选定的标签 id，作为**临时覆盖**优先于
    # 账号在采集板块的默认绑定（`config_tag.scope_of(account,"crawl")`）。
    # 空串 = 不覆盖，沿用账号绑定。UI 语义见前端 CrawlFloatingPanel。
    tag_id: str = ""


@router.post("/dm/batch")
async def crawl_dm_batch(body: CrawlDmBatchRequest):
    """批量私信：从已采集评论中按高价值关键词筛选候选，逐条发送（★ 2026-10-02）。

    筛选逻辑在后端执行（SSOT），发送走统一发送闸门（core.sender.send_by_uid）。

    ★ 2026-10-03（用户指令）：私信文案**复用标签**，前端不再单独输入。
      文案取值优先级（SSOT，唯一真源 = 标签的 `send.dm_pool`）：
        ① 请求体 `text` 非空 —— 显式覆盖（保留，兼容程序化调用）；
        ② 本次显式选的标签 `tag_id` 的 `send.dm_pool` **第一行**；
        ③ 账号在 send 板块绑定标签的 `send.dm_pool` 第一行；
        ④ 仍取不到 ⇒ **400 并说清原因**（fail-closed，不静默发空文案）。
      `dm_pool` 的既有语义是「每行一条、随机选用」（见
      `app_config_schema.py`），此处取第一行是**确定性**取舍：
      批量发送同一批人用不同文案会降低送达率，也不好事后核对。
    """
    text = (body.text or "").strip()
    if not text:
        # 复用标签的私信词库（前端已不再提供输入框）
        # ⚠️ 用 `app_config.get` 而非自造 helper：它**内建回落链**
        #   「标签 scope → 全局 → 环境变量 → schema 默认」（app_config.py:196），
        #   不必自己再实现一遍（我第一版写了不存在的 `_crawl_cfg_send`）。
        _tag = (getattr(body, "tag_id", "") or "").strip()
        try:
            from services import app_config as _ac
            _pool = str(_ac.get("send", "dm_pool", "", scope=_tag or None) or "")
        except Exception as _e:  # noqa: BLE001 —— 取不到就当空，落到 400
            logger.warning(f"[CRAWL-007] [crawl] 读标签私信词库失败: "
                           f"{type(_e).__name__}: {_e}")
            _pool = ""
        # ⚠️ dm_pool 是「每行一条」文本：先去掉 CR，再按 LF 切，
        #   否则 Windows 的 CRLF 会让末行残留回车（发出去带空白）。
        # 用 chr(13)/chr(10) 而非字面量：heredoc 传输会把转义序列真的展开成
        #   真实换行，插进字符串字面量里会直接语法错（本次已踩两次）。
        lines = [ln.strip() for ln in _pool.replace(chr(13), "").split(chr(10))
                 if ln.strip()]
        if lines:
            text = lines[0]
            logger.info(f"[crawl] 私信文案取自标签词库 dm_pool: "
                        f"tag={_tag or '(账号绑定)'} → 首条 {len(text)} 字")
    if not text:
        raise HTTPException(
            400, "私信词库为空：请在配置中心为该标签的「私信词库」填一条，"
                 "或直接传 text")
    if not body.items:
        raise HTTPException(400, "缺少评论数据")

    auth = _load_auth(body.account)
    try:
        auth.account_name = body.account
    except Exception:
        pass

    from core.sender import send_by_uid

    # ★ 2026-10-02：高价值关键词筛选（门槛 > 0 时生效）
    #
    # 🔴 SSOT 归位（2026-10-02 审计发现契约漂移）：此前门槛只认**请求体**
    # `min_score`，而前端恒传 `min_score: 0`（注释写「后端读取配置中心」
    # 但后端从未读）⇒ 用户在配置中心把门槛调高，批量采集会过滤、批量私信
    # **一条都不过滤**。同一语义（高价值门槛）两条路径两套实现 = 契约漂移。
    #
    # 现行契约：配置中心 `crawl.batch_min_score` 是**唯一权威来源**；
    # 请求体 `min_score` **仅作为「本次覆盖」**，且**0 视为不覆盖**
    # （否则前端每次传 0 又会把门槛踩回不过滤 —— 正是本次要消灭的缺陷）。
    # ★ 2026-10-03（方案A）：本次显式选的标签 **优先于** 账号默认绑定。
    # 空串 = 不覆盖，沿用 `_crawl_cfg` 原有的账号绑定回落链。
    _tag_scope = (body.tag_id or "").strip() or None
    if _tag_scope:
        logger.info(f"[crawl] 批量私信按本次选中标签取参数: "
                    f"tag={_tag_scope} account={body.account}")
    _cfg_min = _crawl_cfg("batch_min_score", body.account, _tag_scope)
    try:
        _cfg_min = int(_cfg_min)
    except (TypeError, ValueError):
        _cfg_min = 0
    try:
        _req_min = int(body.min_score or 0)
    except (TypeError, ValueError):
        _req_min = 0
    min_score = max(0, _cfg_min) if _req_min <= 0 else max(0, _req_min)
    if min_score != _cfg_min:
        logger.info(f"[crawl] 批量私信门槛由本次请求覆盖: "
                    f"配置={_cfg_min} → 生效={min_score} account={body.account}")
    hv_scope = _tag_scope or _hv_scope(body.account)
    candidates: list[dict] = []
    seen_uid: set[str] = set()
    for item in body.items:
        uid = str(item.get("uid") or "").strip()
        ctext = str(item.get("text") or "").strip()
        if not uid or uid in seen_uid:
            continue
        if min_score > 0:
            score = _hv.score_text(ctext, hv_scope)
            if score < min_score:
                continue
        seen_uid.add(uid)
        candidates.append({"uid": uid, "nickname": item.get("nickname") or "", "text": ctext})

    if body.max_send > 0:
        candidates = candidates[:body.max_send]

    sent_ok = 0
    sent_fail = 0
    rate_limited = 0
    results: list[dict] = []
    for cand in candidates:
        try:
            ok, reason = await asyncio.to_thread(send_by_uid, auth, cand["uid"], text)
            if ok:
                sent_ok += 1
            elif "rate_limited" in str(reason):
                rate_limited += 1
                reason = "rate_limited"
            else:
                sent_fail += 1
            results.append({"uid": cand["uid"], "nickname": cand["nickname"], "ok": bool(ok), "reason": reason})
        except Exception as e:
            sent_fail += 1
            results.append({"uid": cand["uid"], "nickname": cand["nickname"], "ok": False, "reason": str(e)})
        if body.interval > 0:
            await asyncio.sleep(body.interval)

    logger.info(f"[crawl] 批量私信完成 account={body.account} "
                f"候选={len(candidates)} 成功={sent_ok} 失败={sent_fail} 限流={rate_limited}")
    return {
        "ok": True,
        "candidates": len(candidates),
        "sent_ok": sent_ok,
        "sent_fail": sent_fail,
        "rate_limited": rate_limited,
        "results": results,
    }


@router.post("/batch")
async def crawl_batch(body: CrawlBatchRequest):
    """批量截流任务（采集评论区 → 按关键词筛选 → 逐条批量私信）。

    复用 core.sender.send_by_uid（统一发送闸门，三源一配额，闸门自动限速，
    配额耗尽返回 rate_limited）。keyword 空则对全部评论作者发；非空则仅发
    评论 text 含该关键词的用户。max_send 可限制单次最多发几条。
    """
    aweme_id = (body.aweme_id or "").strip()
    text = (body.text or "").strip()
    if not aweme_id or not text:
        raise HTTPException(400, "缺少作品 ID 或私信文案")

    auth = _load_auth(body.account)
    try:
        auth.account_name = body.account
    except Exception:
        pass

    from core.sender import send_by_uid

    kw = (body.keyword or "").strip()
    limit = max(1, min(int(body.limit or 100), 300))
    max_send = max(0, int(body.max_send or 0))
    interval = max(0.0, float(body.interval or 0.0))
    count = _page_count(body.count, body.account)

    try:
        raw = await asyncio.to_thread(_fetch_work_comments, auth, aweme_id, limit, count)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[CRAWL-005] " + f"[crawl] 批量评论采集失败 account={body.account} aweme={aweme_id}: {e}")
        raise HTTPException(502, f"评论采集失败: {e}") from e

    # 提取候选（uid 非空 + 关键词筛选 + 高价值关键词过滤）
    candidates: list[dict] = []
    seen_uid: set[str] = set()
    hv_min_score = max(0, int(_crawl_cfg("batch_min_score", body.account)))
    hv_scope = _hv_scope(body.account)
    for c in raw:
        u = c.get("user") or {}
        uid = str(u.get("uid") or u.get("user_id") or "")
        ctext = (c.get("text") or "").strip()
        if not uid or uid in seen_uid:
            continue
        if kw and kw not in ctext:
            continue
        # ★ 2026-10-02：高价值关键词过滤（min_score > 0 时生效）
        if hv_min_score > 0:
            score = _hv.score_text(ctext, hv_scope)
            if score < hv_min_score:
                continue
        seen_uid.add(uid)
        candidates.append({"uid": uid, "nickname": u.get("nickname") or "", "text": ctext})

    if max_send:
        candidates = candidates[:max_send]

    sent_ok = 0
    sent_fail = 0
    rate_limited = 0
    results: list[dict] = []
    for cand in candidates:
        ok, reason = await asyncio.to_thread(send_by_uid, auth, cand["uid"], text)
        if ok:
            sent_ok += 1
        elif "rate_limited" in str(reason):
            rate_limited += 1
            reason = "rate_limited"
        else:
            sent_fail += 1
        results.append({"uid": cand["uid"], "nickname": cand["nickname"], "ok": bool(ok), "reason": reason})
        if interval > 0:
            await asyncio.sleep(interval)

    await _save_history(body.account, "batch", "", aweme_id, candidates)
    logger.info(f"[crawl] 批量截流完成 account={body.account} aweme={aweme_id} "
                f"候选={len(candidates)} 成功={sent_ok} 失败={sent_fail} 限流={rate_limited}")
    return {
        "ok": True,
        "candidates": len(candidates),
        "sent_ok": sent_ok,
        "sent_fail": sent_fail,
        "rate_limited": rate_limited,
        "results": results,
    }


@router.get("/history")
async def crawl_history(limit: int = 50):
    """最近采集记录（不含 payload 全量，仅摘要）。"""
    limit = max(1, min(limit, 200))
    rows = exec_query(
        "SELECT id,account,kind,keyword,target,result_count,ts FROM crawl_history "
        "ORDER BY id DESC LIMIT ?",
        (limit,),
    )
    for r in rows:
        r["ts"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["ts"])) if r.get("ts") else ""
    return {"ok": True, "items": rows}


# ════════════════════════════════════════════════════════════════
# 采集专项统计（ADR-033，2026-10-03）
# ════════════════════════════════════════════════════════════════
#
# ## 为什么单独建端点，而不是复用 /api/overview/funnel
#
# `/overview/funnel` 是**全局业务漏斗**（含私信/线索/账号），口径是「全站今日」。
# 采集页要的是**采集域自己的明细**：按关键词排行、按 kind 拆分、近 N 日趋势、
# 顶级作品榜。两者维度不同，混在一起会让任一方都读不清。
#
# ## 风控契约（铁律，不可破坏）
#
# 只读本地 SQLite（`crawl_history` / `dm_uid_sink`），**零网络零浏览器**，
# 不触发任何捕获 / 昵称查询 / 浏览器动作。可安全高频轮询。
#
# ## 口径铁律（禁止假成功）
#
# · `crawl_history.result_count` 是该次采集**返回的条数**，不是「去重后人数」，
#   两者不可混标（UI 必须写明是「条」）。
# · `kind='comment'` 的 target 是 aweme_id（不是关键词）；排行只统计
#   keyword 非空的视频/用户类采集，避免把 aweme_id 当关键词塞进榜单。
# · 日切按本地时区，与 overview._day_start_ts 同约定（tz 夹取 [-12,14]）。


def _crawl_stats_sync(tz_hours: int = 8, days: int = 7) -> dict:
    """在**非事件循环**线程里做全部同步 SQLite 读。"""
    from database import exec_query

    try:
        off = max(-12, min(int(tz_hours), 14))
    except Exception:
        off = 8
    days = max(1, min(int(days), 30))
    now = time.time()
    # 本日 00:00（本地时区）
    today_start = now + off * 3600
    today_start = today_start - (today_start % 86400) - off * 3600
    window_start = today_start - (days - 1) * 86400

    # ── ① 累计总量（含全部历史，不受窗口限制）──
    tot = exec_query(
        "SELECT COUNT(*) AS runs, COALESCE(SUM(result_count),0) AS results "
        "FROM crawl_history"
    )[0] or {}
    total_runs = int(tot.get("runs") or 0)
    total_results = int(tot.get("results") or 0)

    # ── ② 今日（本地时区）──
    td = exec_query(
        "SELECT COUNT(*) AS runs, COALESCE(SUM(result_count),0) AS results "
        "FROM crawl_history WHERE ts >= ? AND ts < ?",
        (today_start, today_start + 86400),
    )[0] or {}
    today_runs = int(td.get("runs") or 0)
    today_results = int(td.get("results") or 0)

    # ── ③ 按 kind 拆分（全量 + 窗口内）──
    kinds_total: dict[str, dict] = {}
    for r in exec_query(
        "SELECT kind, COUNT(*) AS n, COALESCE(SUM(result_count),0) AS rc "
        "FROM crawl_history GROUP BY kind"
    ):
        k = str(r.get("kind") or "unknown")
        kinds_total[k] = {"runs": int(r.get("n") or 0), "results": int(r.get("rc") or 0)}

    # ── ④ 关键词排行（只统计 keyword 非空的 video/user 类）──
    kw_rows = exec_query(
        "SELECT keyword, COUNT(*) AS runs, COALESCE(SUM(result_count),0) AS rc, "
        "       MAX(ts) AS last_ts "
        "FROM crawl_history "
        "WHERE keyword IS NOT NULL AND TRIM(keyword) <> '' "
        "  AND kind IN ('video','user') "
        "GROUP BY keyword "
        "ORDER BY rc DESC, runs DESC LIMIT 20"
    )
    top_keywords = [
        {
            "keyword": str(r.get("keyword") or ""),
            "runs": int(r.get("runs") or 0),
            "results": int(r.get("rc") or 0),
            "last_ts": float(r.get("last_ts") or 0),
        }
        for r in kw_rows
    ]

    # ── ⑤ 近 N 日趋势（按本地日切，逐日聚合；缺日补 0）──
    #    用 ts+off*3600 归入本地日，避免 UTC 切日错位。
    trend_rows = exec_query(
        "SELECT CAST((ts + ?) / 86400 AS INTEGER) * 86400 AS day_key, "
        "       COUNT(*) AS n, COALESCE(SUM(result_count),0) AS rc "
        "FROM crawl_history WHERE ts >= ? GROUP BY day_key ORDER BY day_key",
        (off * 3600, window_start),
    )
    by_day = {int(r["day_key"]): (int(r["n"] or 0), int(r["rc"] or 0)) for r in trend_rows}
    trend: list[dict] = []
    for i in range(days):
        ds = window_start + i * 86400
        key = int((ds + off * 3600) // 86400) * 86400
        n, rc = by_day.get(key, (0, 0))
        trend.append({
            "date": time.strftime("%Y-%m-%d", time.gmtime(ds + off * 3600 + 12 * 3600)),
            "runs": n,
            "results": rc,
        })

    # ── ⑥ 采集域沉淀（dm_uid_sink.source='crawl'）—— 采集真正带来的人 ──
    #    单位 = **人**（按 peer_uid 去重），与 result_count 的「条」不同。
    sink_total = 0
    sink_sent = 0
    try:
        s = exec_query("SELECT COUNT(*) AS n FROM dm_uid_sink WHERE source='crawl'")[0] or {}
        sink_total = int(s.get("n") or 0)
        s2 = exec_query(
            "SELECT COUNT(*) AS n FROM dm_uid_sink "
            "WHERE source='crawl' AND sent_ts IS NOT NULL"
        )[0] or {}
        sink_sent = int(s2.get("n") or 0)
    except Exception:
        # source 列在极旧库可能不存在 —— 降级为 0，不伪造
        pass

    # ── ⑦ 最近采集记录（复用 /history 的摘要口径）──
    recent: list[dict] = []
    for r in exec_query(
        "SELECT id,account,kind,keyword,target,result_count,ts FROM crawl_history "
        "ORDER BY id DESC LIMIT 20"
    ):
        recent.append({
            "id": r.get("id"),
            "account": r.get("account") or "",
            "kind": r.get("kind") or "",
            "keyword": r.get("keyword") or "",
            "target": r.get("target") or "",
            "result_count": int(r.get("result_count") or 0),
            "ts": time.strftime(
                "%Y-%m-%d %H:%M:%S", time.localtime(r["ts"])
            ) if r.get("ts") else "",
        })

    return {
        "ok": True,
        "tz": off,
        "days": days,
        "date": time.strftime("%Y-%m-%d", time.gmtime(now + off * 3600)),
        "total": {"runs": total_runs, "results": total_results},
        "today": {"runs": today_runs, "results": today_results},
        "kinds": kinds_total,
        "top_keywords": top_keywords,
        "trend": trend,
        # 采集域捕获池：单位「人」，与上面的「条」严格区分
        "sink": {"total": sink_total, "sent": sink_sent},
        "recent": recent,
    }


@router.get("/stats")
async def crawl_stats(tz: int = 8, days: int = 7):
    """采集专项统计（ADR-033）。只读本地库，零网络零浏览器。

    `tz`  日切时区，默认 +8；`days` 趋势窗口天数，默认 7（夹取 1..30）。
    同步 IO 走 to_thread，不阻塞事件循环。
    """
    return await asyncio.to_thread(_crawl_stats_sync, tz, days)
