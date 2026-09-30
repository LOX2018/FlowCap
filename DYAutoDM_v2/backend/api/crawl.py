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

router = APIRouter()


# ---------------------------------------------------------------------------
# 请求模型
# ---------------------------------------------------------------------------
class CrawlSearchRequest(BaseModel):
    account: str
    query: str
    kind: str = "video"          # video | user
    # 排序 0 综合 / 1 最多点赞 / 2 最新发布（仅 video）
    sort_type: str = "0"
    # 发布时间 0 不限 / 1 一天内 / 7 一周内 / 180 半年内（仅 video）
    publish_time: str = "0"
    # 视频时长 '' 不限 / 0-1 / 1-5 / 5-10000
    filter_duration: str = ""
    num: int = 20


class CrawlCommentsRequest(BaseModel):
    account: str
    aweme_id: str
    limit: int = 100             # 一级评论上限，防止热评视频全量拉取过久
    count: int = 0               # 单页条数（0=用配置中心值；实测 5~50）


class CrawlCommentsBatchRequest(BaseModel):
    """多作品批量采集评论（★ 2026-09-30 方案1，v0.45.125）。

    只**采集**，不发送（与 `/batch` 的「采集+私信」语义分开，避免误触发写操作）。
    刻意**串行 + 间隔**（配置中心 `crawl.batch_interval`），不开并发。
    """
    account: str
    aweme_ids: list[str] = []
    limit: int = 100             # 每作品评论上限
    count: int = 0               # 单页条数（0=配置中心值）


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
                   "batch_max_works": 20}


def _crawl_cfg(key: str):
    """读 `crawl` 分区配置；读不到（配置中心不可用/旧环境）回退默认值。

    与 `errcode_data.py` 中多处「读配置失败按默认处理」的项目约定一致：
    配置中心异常**不得**让采集功能整体不可用。
    """
    try:
        from services import app_config
        v = app_config.get("crawl", key, None)
        if v is not None:
            return v
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[crawl] 读配置 crawl.{key} 失败，改用默认值: {e}")
    return _CRAWL_FALLBACK[key]


def _page_count(body_count=None) -> int:
    """确定本次采集的单页条数（请求体 > 配置中心 > 缺省 20）。"""
    if body_count:
        try:
            return max(5, min(int(body_count), 50))
        except (TypeError, ValueError):
            pass
    return max(5, min(int(_crawl_cfg("comment_page_count")), 50))


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
@router.post("/search")
async def crawl_search(body: CrawlSearchRequest):
    """关键词搜索（video=综合搜索 / user=用户搜索）。

    复用基座签名链路（a_bogus 纯算签名），凭证与手动浏览同源。
    """
    q = (body.query or "").strip()
    if not q:
        raise HTTPException(400, "请输入搜索关键词")
    if body.kind not in ("video", "user"):
        raise HTTPException(400, f"不支持的采集类型: {body.kind}")
    num = max(1, min(int(body.num or 20), 60))

    auth = _load_auth(body.account)

    from dy_apis.douyin_api import DouyinAPI

    try:
        if body.kind == "video":
            raw = await asyncio.to_thread(
                DouyinAPI.search_some_general_work, auth, q, num,
                body.sort_type, body.publish_time, body.filter_duration,
            )
            items = [_map_video(w) for w in (raw or []) if w.get("aweme_info")]
        else:
            # 经 features 基座封装层调用（返回 {"ok":bool,"data":...}）。
            # ok=False 必须按原有 502 语义上抛，绝不把「采集失败」降级成「没有结果」。
            import features
            res = await asyncio.to_thread(features.search_user, auth, q, num)
            if not res.get("ok"):
                raise HTTPException(502, f"搜索失败: {res.get('error')}")
            raw = res.get("data") or []
            items = [_map_user(u) for u in raw]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[CRAWL-002] " + f"[crawl] 搜索失败 account={body.account} q={q}: {e}")
        raise HTTPException(502, f"搜索失败: {e}") from e

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
    count = _page_count(body.count)

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
    max_works = max(1, int(_crawl_cfg("batch_max_works")))
    if len(ids) > max_works:
        raise HTTPException(
            400, f"单次批量最多 {max_works} 个作品（当前 {len(ids)}），请分批")
    interval = max(0.0, float(_crawl_cfg("batch_interval")))
    limit = max(1, min(int(body.limit or 100), 300))
    count = _page_count(body.count)

    auth = _load_auth(body.account)

    per_work: list[dict] = []
    total = 0
    for i, aweme_id in enumerate(ids):
        if i:
            await asyncio.sleep(interval)   # ★ 串行节流（配置中心可调）
        try:
            raw = await asyncio.to_thread(
                _fetch_work_comments, auth, aweme_id, limit, count)
            items = [_map_comment(c) for c in raw]
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
                f"作品 {ok_works}/{len(ids)} 成功，共 {total} 条评论")
    return {"ok": True, "works": len(ids), "ok_works": ok_works,
            "total_comments": total, "per_work": per_work}


@router.post("/dm")
async def crawl_dm(body: CrawlDmRequest):
    """对评论/搜索结果中的用户直发私信（评论截流）。

    复用 core.sender.send_by_uid：优先走 recv_daemon 统一发送闸门
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
    count = _page_count(body.count)

    try:
        raw = await asyncio.to_thread(_fetch_work_comments, auth, aweme_id, limit, count)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[CRAWL-005] " + f"[crawl] 批量评论采集失败 account={body.account} aweme={aweme_id}: {e}")
        raise HTTPException(502, f"评论采集失败: {e}") from e

    # 提取候选（uid 非空 + 关键词筛选）
    candidates: list[dict] = []
    seen_uid: set[str] = set()
    for c in raw:
        u = c.get("user") or {}
        uid = str(u.get("uid") or u.get("user_id") or "")
        ctext = (c.get("text") or "").strip()
        if not uid or uid in seen_uid:
            continue
        if kw and kw not in ctext:
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
