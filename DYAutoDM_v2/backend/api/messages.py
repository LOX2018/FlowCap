"""私信会话路由

取代原版 WebBridge.getConversations / getConversation / sendDm。
转发到 recv_daemon 的 HTTP 接口（每账号专属端口）。

2026-09-02 新增:加密原图解析(/origin_image/...)——把 dm_messages.extra 里
的 (skey, origin_url) 解密出真原图,小图本地静态、大图走图床,前端可直接 <img>。
"""
from __future__ import annotations

import time
import asyncio
import urllib.parse
import urllib.request
import json
from pathlib import Path

# 顶层 import 确保 PyInstaller onefile 能追踪到 origin_image_resolver
# (函数体内动态 import 不会被静态分析,导致 onefile 缺少该模块)
from auto_dm import origin_image_resolver as _origin_image_resolver
# 2026-09-17：IM 视频（CENC 解密 + 下载）——同样顶层 import，保证 PyInstaller 能追踪
from services import im_video as _im_video
from services import merged_forward as _mf
from services.verdicts import is_placeholder_name
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from loguru import logger

from auto_dm import accounts as acct_core
from config import settings as _app_settings
from database import get_db

router = APIRouter()

# 会话摘要日志去重：仅当（总数, 关联昵称数, 未读合计）变化时打印，避免前端
# 每 5s 轮询导致「私信拉取」日志刷屏。键为 account。
_last_summary: dict[str, tuple] = {}


class SendDmRequest(BaseModel):
    account: str
    conv_id: str
    text: str
    # 发送通道：'ws'=私信守护 HTTP API（默认，稳定）；'wp'=抖音网页版 chat 页 IM SDK
    # 2026-09-05 新增。两通道并存，默认走 ws（更可靠），wp 作为网页通道备用。
    channel: str = "ws"
    # 2026-09-28：发送来源。本端点由用户在 UI 显式触发 ⇒ 默认 "manual"；
    # 由 recv_daemon 的物理闸门据此决定是否套用手动豁免（不拦用户显式操作）。
    # 若把它当作编程式绕过调度器的直发通道，请显式传 source="dispatch" 等（受严格管控）。
    source: str = "manual"


class SendImageRequest(BaseModel):
    account: str
    conv_id: str
    # 图片二进制 base64（≤20MB 原始大小）
    image_b64: str
    filename: str = "image.jpg"
    # 2026-09-28：图片目前恒为用户显式发送 ⇒ 默认 "manual"。
    source: str = "manual"


class RequestDmBody(BaseModel):
    name: str
    comment: str = ""


def _recv_url(account: str, path: str) -> str | None:
    """构造该账号 recv_daemon 的 HTTP URL（端口稳定哈希，与守护进程一致）。"""
    port = acct_core.recv_daemon_port(account)
    return f"http://127.0.0.1:{port}{path}"


def _bcc_url(account: str, path: str) -> str:
    """构造该账号 BCC(browser_daemon) 的 HTTP URL。

    端口用 acct_core.browser_daemon_port（独立稳定哈希，与 recv_daemon 不同）。
    2026-09-06 懒加载：调用前先 ensure_bcc —— BCC 不随启动拉起（用户架构
    决策），WP 发送/更新会话首次使用时自动拉起（onefile 冷启动 ~15s）。
    """
    # 2026-09-13：走统一调度入口 + 用户显式豁免冷静期。
    # 此前直接调 ensure_bcc 未豁免 → 用户在 backend 启动 30s 内点
    # 「更新会话」必然拿不到 BCC（实测：日志 SYS-002 冷静期拦截）。
    #
    # 2026-09-17 修补（OCR 审查 CRITICAL）：`ensure_browser` 拿到 lease_id
    # **即持租约**（其 docstring 明确「用完必须 release_lease()」）。原实现
    # 只用返回的 port 拼 URL，从不释放 → 每次 WP 发送/更新会话都占住租约
    # 直到 BCC 侧 TTL 到期，会把其它调用方（capture / 发送）挡在门外。
    # 现改为：本处只需探活拿 port，取完后立即释放租约（短 TTL 兜底）。
    _st = None
    _lease_id = ""
    try:
        from services.browser_gate import (ensure_browser as _eb,
                                          release_lease as _rl,
                                          PURPOSE_USER as _PU)
        _st = _eb(account, purpose=_PU, wait=True, ttl=30.0)
        _lease_id = (_st or {}).get("lease_id") or ""
        if _st is not None:
            st = _st
            if not st.get("ok"):
                raise RuntimeError(f"BCC 未就绪: {st.get('msg')}")
            port = acct_core.browser_daemon_port(account)
            return f"http://127.0.0.1:{port}{path}"
    except Exception as _e:
        logger.debug(f"[bcc-url] gate 调用异常（回退 ensure_bcc）: {_e}")
    finally:
        # 无论成功与否都释放：本函数只借用租约定位端口，不需要长期持有
        if _lease_id:
            try:
                _rl(account, _lease_id, holder="bcc_url")
            except Exception as _re:
                logger.debug(f"[bcc-url] 释放租约失败（TTL 兜底）: {_re}")
    if _st is None:
        # 调度器不可用时退回原路径（仍豁免冷静期）
        _st = acct_core.ensure_bcc(account, skip_cooldown=True)
    st = _st
    if not st.get("ok"):
        raise RuntimeError(f"BCC 未就绪: {st.get('msg')}")
    port = acct_core.browser_daemon_port(account)
    return f"http://127.0.0.1:{port}{path}"

def _http_get_json(url: str, timeout: float = 5.0) -> dict:
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        import json
        return json.loads(resp.read().decode("utf-8"))


def _http_post_json(url: str, payload: dict, timeout: float = 8.0) -> dict:
    import json
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _fmt_ts(ts: float | None) -> str:
    try:
        if ts:
            # 2026-09-05:返回完整 ISO 时间 YYYY-MM-DD HH:MM:SS,
            # 前端用它做日期分隔线 + 每条消息显示。
            return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(ts)))
    except Exception:
        pass
    return ""


def _front_type(msg_type: str) -> str:
    """把抖音消息类型归一化成前端 MsgBubble 已知类型（2026-09-16 实机修复）。

    背景：WS 实时路径落库 msg_type 是**数字字符串**（"7"=文本、"5"=表情包、
    "17"=语音、"27"=图片、"8"=分享视频、"50001"=已读回执）；补拉/历史路径
    落库的是语义串（"text"/"image"/...）。前端 MsgBubble 只认后者，
    收到数字串会落入**兜底分支**渲染成「分享的视频」卡片（title 空白）。
    这里统一归一：数字映射到语义串，其它原样透传。
    """
    mapping = {
        "7": "text",
        "5": "sticker",
        "17": "voice",
        "27": "image",
        "8": "video",
        "50001": "read_receipt",
    }
    if msg_type is None:
        return "text"
    return mapping.get(str(msg_type), str(msg_type) or "text")


def _map_message(m: dict) -> dict:
    """把 recv_daemon 的 message 字段映射成前端期望结构。

    recv_daemon to_dict(): role/the/me、text、msg_type、ts
    前端 messages.tsx: dir(in/out)、type、text、time
    """
    role = (m.get("role") or "them")
    direction = "in" if role == "them" else "out"
    return {
        "dir": direction,
        # 2026-09-16 实机修复：WS 实时路径落库的是**数字** msg_type（如 "7"=文本），
        # 补拉/历史路径落库的是字符串 "text"。前端 MsgBubble 只认
        # text/voice/sticker/image，收到 "7" 会落入**兜底分支**渲染成
        # 「分享的视频」卡片（m.title 空白 → 界面显示"分享视频（该信息非真实存在）"）。
        # 这里把抖音消息类型归一化成前端已知类型：
        #   7=文本, 5=表情包, 17=语音, 27=图片, 8=分享视频, 50001=已读回执
        # （与 auto_dm/conversation_capture.py 的映射保持一致）
        "type": _front_type(m.get("msg_type") or m.get("type") or "text"),
        "text": m.get("text") or "",
        "time": _fmt_ts(m.get("ts")),
    }


def _enrich_with_db_nicknames(account: str, convs: list[dict]) -> list[dict]:
    """用 SQLite 中已关联的昵称/头像补全 recv_daemon 内存里的数字 UID 会话。

    08 方案：昵称/头像由公司级 capture_all(with_browser=True) 经 BCC 截获后写入
    dm_conversations 表（peer_id=对端数字 UID，peer_name=昵称）。

    关键：recv_daemon 内存里 peer_id/peer_name 常取成自己（首包解析时 uid_a/uid_b
    顺序问题），但 conv_id 形如 0:1:uid_a:uid_b 里对端 UID 100% 可靠。
    因此本函数从 conv_id 提取对端 UID（排除 my_uid），再用该 UID 匹配数据库
    peer_id 字段取昵称/头像，确保前端不再刷屏数字 UID。
    """
    try:
        from dy_apis.login_api import DYLoginApi
        from auto_dm import accounts as acc
        from database import get_db
        # 取 my_uid 用于从 conv_id 排除自身、提取对端 UID
        my_uid = None
        try:
            env_path = acc.env_path_of(account)
            if env_path:
                auth = DYLoginApi._load_auth_from_env(env_path)
                my_uid = str(auth.get_uid())
        except Exception:
            pass

        conn = get_db()
        rows = conn.execute(
            "SELECT peer_id, peer_name, avatar FROM dm_conversations "
            "WHERE account=? AND peer_name IS NOT NULL AND peer_name != '' AND peer_name != peer_id",
            (account,),
        ).fetchall()
        if not rows:
            return convs
        # peer_id -> (nickname, avatar) 映射
        # 2026-09-25 H-25：污染行（peer_id == 本号 uid）的 peer_name/avatar 实为
        # **本号自己的身份**（根因见 recv_daemon._extract_peer_uid），绝不可复用给
        # 别的会话 —— 从映射中排除，切断存量污染在读侧的二次扩散。
        _self = ""
        try:
            from services.conv_identity import my_uid as _my_uid
            _self = str(_my_uid(account) or "")
        except Exception:
            _self = ""
        db_map = {str(r["peer_id"]): (r["peer_name"], r["avatar"])
                  for r in rows
                  if not (_self and str(r["peer_id"]) == _self)}

        def _extract_peer_uid(cid: str):
            """从 conv_id 0:1:uid_a:uid_b 提取对端 UID（排除 my_uid）。

            2026-09-25 H-25：委托 `services.conv_identity.peer_uid`（唯一真相源）。
            删除原「无 my_uid 兜底取 uid_b」——该兜底在 my_uid 缺失时返回的
            uid_b 恰是**本号自己**（conv_id 实为 `0:1:<对端>:<本号>`），会把
            本号昵称/头像当作对端信息复用给别的会话（与 recv_daemon 同根因）。
            my_uid 未知时不猜（返回 b 的弱语义由 conv_identity 统一承担，
            调用方 `_enrich_with_db_nicknames` 仅用其结果查库，不写身份字段）。
            """
            try:
                from services.conv_identity import peer_uid as _peer_uid
            except Exception:
                return None
            # my_uid 未知时不猜（conv_identity 会弱返回 b，而 b 常是本号自己）
            if not my_uid:
                return None
            return _peer_uid(cid, my_uid)

        for c in convs:
            cid = c.get("conv_id")
            peer_uid = _extract_peer_uid(cid)
            if not peer_uid:
                continue
            info = db_map.get(peer_uid)
            if not info:
                continue
            db_name, db_avatar = info
            cur_name = c.get("peer_name") or c.get("name")
            cur_name = str(cur_name) if cur_name is not None else ""
            # 当前是数字 UID（无昵称）→ 用库值覆盖
            if (not cur_name) or cur_name.isdigit() or cur_name == str(c.get("peer_id") or ""):
                if db_name:
                    c["peer_name"] = db_name
                    c["name"] = db_name
            cur_avatar = c.get("avatar")
            if (not cur_avatar) and db_avatar:
                c["avatar"] = db_avatar
    except Exception as e:
        logger.debug(f"[私信拉取] 数据库昵称补全失败（跳过）: {e}")
    return convs


def _map_conversation(c: dict) -> dict:
    return {
        "conv_id": c.get("conv_id"),
        "name": c.get("peer_name") or c.get("peer_id") or c.get("conv_id") or "会话",
        "unread": c.get("unread") or 0,
        "messages": [_map_message(m) for m in (c.get("messages") or [])],
        "avatar": c.get("avatar") or "",
    }


def _fmt_hm(ts) -> str:
    """float 秒级时间戳 -> HH:MM 字符串（前端气泡时间展示）。

    ts 为 0/None/非法值时返回空串，前端自行兜底 nowHM()。
    """
    try:
        v = float(ts or 0)
        if v <= 0:
            return ""
        import time as _t
        return _t.strftime("%H:%M", _t.localtime(v))
    except Exception:
        return ""


@router.get("/conversations")
async def list_conversations(account: str):
    """会话列表（08 方案：私信页纯读 SQLite 权威源）

    昵称/头像由 backend 启动时 capture_all(with_browser=True) 经 BCC 截获后写入
    SQLite（dm_conversations.peer_name/avatar），recv_daemon 仅负责 WS 实时增量
    （新消息、未读数），不在内存里维护昵称。这里直接读库，保证展示层和落库一致。
    守护未启动时不抛连接错误，返回 recvDaemonDown 由前端决定拉起。

    日志策略：拉取/读库为高频轮询（前端每 5s 一次），为避免刷屏，
    仅在「会话总数 / 关联昵称数 / 未读合计」任一发生变化时才打印摘要日志一次。
    """
    logger.debug(f"[私信拉取] 账号「{account}」读库请求（高频轮询，变化时才记日志）")
    try:
        conn = get_db()
        rows = conn.execute(
            # 排序规则（2026-08-29 实测修正）：
            # 1) 有真实消息的会话优先（n_msgs > 0 排前面）。
            #    实测 222 个会话里 180 个是空会话，且其 last_ts 反而更大
            #    （被 WS 回执/同步刷新），若纯按 last_ts 排序，空会话会霸占
            #    列表顶部，用户点前面几个永远是「暂无消息」。
            # 2) 再按 last_ts DESC（有消息的按活跃度；空会话之间也按此）。
            # 3) 二级键 conv_id 保证顺序确定：last_ts 大量并列时，
            #    仅按 last_ts 排序不稳定（依赖内部扫描顺序），会让前端 5s
            #    轮询拿到的顺序每次都变（会话「乱跳」+ 选中态错位）。
            # 注：统计消息数时排除 50001 回执（不落库后已无，但历史库可能有）。
            "SELECT c.conv_id,c.peer_id,c.peer_name,c.short_id,c.last_ts,c.unread,c.avatar,"
            "       c.conv_type "
            "FROM dm_conversations c "
            "LEFT JOIN (SELECT conv_id, COUNT(*) n FROM dm_messages "
            "           WHERE account=? AND msg_type <> '50001' GROUP BY conv_id) m "
            "  ON m.conv_id = c.conv_id "
            "WHERE c.account=? "
            "ORDER BY COALESCE(m.n, 0) DESC, c.last_ts DESC, c.conv_id ASC",
            (account, account),
        ).fetchall()
        if not rows:
            # 库里还没有会话：若守护未启动则提示前端拉起，否则返回空
            port = acct_core.recv_daemon_port(account)
            if not acct_core._port_open(port, timeout=0.3):
                logger.warning(f"[MSG-001] " + f"[私信拉取] 账号「{account}」库空且守护未运行(port={port})")
                return {"ok": True, "conversations": [], "recvDaemonDown": True}
            return {"ok": True, "conversations": []}
        convs = []
        # peer_id → 昵称 映射：recv_daemon 独有会话（conv_id 格式与 capture_all 不同）
        # 可能 peer_name 为空/数字 UID，但其 peer_id 与已关联昵称的会话相同，
        # 用 peer_id 复用昵称，保证展示层不出现裸 UID。
        nickname_by_peer = {}
        for r in rows:
            pn = r["peer_name"]
            if not is_placeholder_name(pn, peer_id=r["peer_id"]):
                nickname_by_peer[str(r["peer_id"])] = pn
        for r in rows:
            cid = r["conv_id"]
            peer_id = r["peer_id"]
            peer_name = r["peer_name"]
            name = peer_name or nickname_by_peer.get(str(peer_id)) or peer_id or cid or "会话"
            # 2026-09-17：会话类型（1=单聊 2=群聊；DB 由 capture 写入，上游同口径）。
            # 存量行可能为 NULL/0 → 按 conv_id 是否纯数字回退判定（群聊 conv_id 为数字串）。
            ct = r["conv_type"] if "conv_type" in r.keys() else None
            if not ct:
                # 2026-09-18：收敛到 conv_identity.conv_type（唯一实现，勿再内联重写）
                from services.conv_identity import conv_type as _conv_type
                ct = _conv_type(cid)
            convs.append({
                "conv_id": cid,
                "name": name,
                "peer_id": peer_id,
                "peer_name": peer_name,
                "unread": r["unread"] or 0,
                "avatar": r["avatar"] or "",
                "conv_type": int(ct),
                "is_group": int(ct) == 2,
                "messages": [],
            })
        unread_total = sum((c.get("unread") or 0) for c in convs)
        named = sum(1 for c in convs if not is_placeholder_name(c["name"]))
        # 变化检测：仅当总数/关联数/未读合计变化时才打印（避免每 5s 轮询刷屏）
        _key = (len(convs), named, unread_total)
        if _last_summary.get(account) != _key:
            _last_summary[account] = _key
            logger.info(
                f"[私信拉取] 账号「{account}」读库 {len(convs)} 个会话"
                f"（已关联昵称 {named}，未读合计 {unread_total}）"
            )
        return {"ok": True, "conversations": convs}
    except Exception as e:
        logger.warning(f"[MSG-002] " + f"[私信拉取] 账号「{account}」读库异常: {e}")
        return {"ok": False, "conversations": [], "error": str(e)}


@router.get("/conversation")
async def get_conversation(account: str, conv_id: str):
    """会话详情（纯读库 + 标记已读）

    2026-09-02:同时把 dm_messages.extra 里存的图片 (skey, origin_url)
    走 origin_image_resolver 解密 → 前端可直接 <img> 的 image_url。
    同一 msg_id 解析一次,后续走进程内缓存(30 天)。
    """
    logger.info(f"[私信拉取] 拉取账号「{account}」会话详情 conv_id={conv_id}")
    try:
        conn = get_db()
        row = conn.execute(
            "SELECT conv_id,peer_id,peer_name,short_id,last_ts,unread,avatar "
            "FROM dm_conversations WHERE account=? AND conv_id=?",
            (account, str(conv_id)),
        ).fetchone()
        if row is None:
            return {"ok": False, "conversation": {}}
        # 过滤回执/系统类消息:msg_type=50001 是「对方已读」回执,
        # recv_daemon 经 WS 反复写入且 msg_id 为 NULL(唯一索引管不到),
        # 实测单个会话能堆积上千条(全库 1938 条),会把真实聊天记录挤掉。
        # 聊天框只展示真实对话内容,故在此过滤。
        # 另:排除解析噪音 "[未知媒体] ..."(空媒体对象,非真实消息)。
        # 2026-09-02 同时读 extra(JSON 含 skey/origin_url),供前端按需解密。
        # 2026-09-04 过滤系统引导消息(msg_type=7 且 msg_id IS NULL,
        # 如"微信"/"在哪个地区受伤的"快捷回复建议,非真实聊天)
        # 和[分享视频]脏数据(WS 错误解析产生的噪音)
        msgs = conn.execute(
            "SELECT msg_id, role, text, msg_type, extra, ts FROM dm_messages "
            "WHERE account=? AND conv_id=? AND msg_type <> '50001' "
            "AND NOT (msg_type = '7' AND msg_id IS NULL) "
            # 2026-09-23（审计 P0-1）：排除投递验证标记，禁止其泄漏进用户聊天框。
            # 标记行（msg_type='delivery_marker' / msg_id 前缀 'verify:' / text 前缀
            # '[投递验证]'）是探针证据，不是用户消息 —— 三条判据取并集，任一条命中即排除。
            "AND msg_type <> 'delivery_marker' "
            "AND text NOT LIKE '[投递验证]%' "
            "AND IFNULL(msg_id,'') NOT LIKE 'verify:%' "
            # 2026-09-06 过滤抖音「未发过消息的陌生会话」系统占位提示
            # （用户实测：对方回复你或互关之前，可发送一条文字消息...）；
            # 此前 sender 来自陌生人被当真实消息入库污染聊天记录
            "AND text NOT LIKE '%对方回复你或互关之前%' "
            "AND text NOT LIKE '%请礼貌发言%' "
            "AND text NOT LIKE '%自觉遵守%' "
            "AND text NOT LIKE '[未知媒体]%' "
            # 2026-09-25（H-25）：系统提示/系统消息/未知类型是**抖音系统通知或
            # 解析噪音**，不是聊天内容 —— 实测该账号混入 18 条，会被当会话内容
            # 显示。与 AI 读侧 `_HISTORY_NOISE_SQL` 口径一致（同一语义不得两处漂移）。
            "AND text NOT LIKE '[系统提示]%' "
            "AND text NOT LIKE '[系统消息]%' "
            "AND text NOT LIKE '[未知类型%' "
            # 2026-09-16：只滤「空分享」（裸 [分享视频]，WS 解析噪音无 ID）；
            # 带 ID 的 "[分享视频] 视频ID x" 是真实视频分享（08 §16.4 实测），
            # 应正常展示。此前 NOT LIKE '[分享视频]%' 把真实分享也滤掉了
            # （设计漂移）。空分享的准确特征是「整条文本就是 [分享视频]」。
            "AND text <> '[分享视频]' "
            "AND text NOT LIKE 'https://www.iesdouyin.com/share/%' "  # 群聊分享链接脏数据
            # 2026-09-17：优先按服务端单调序号 created_at_us（extra 内）排序，
            # 缺失时回退 ts —— 同秒消息此前会并列导致顺序抖动。
            # 关键：兜底值必须**量级对齐**（ts 秒 ×1e6 = 微秒），否则缺失 f4 的行
            # 会与有 f4 的行按不同量级混排（实测 ts=1/4 会排到 created_at_us=100 之前）。
            "ORDER BY CASE WHEN json_extract(NULLIF(extra,''),'$.created_at_us')"
            " IS NOT NULL"
            " THEN CAST(json_extract(NULLIF(extra,''),'$.created_at_us') AS INTEGER)"
            " ELSE CAST(ts * 1000000 AS INTEGER) END ASC, ts ASC",
            (account, str(conv_id)),
        ).fetchall()
        out_messages = []
        for seq_i, m in enumerate(msgs, start=1):
            msg_id = m["msg_id"]
            # 2026-09-17（E1/E2）：下发消息序号（与 `/render/png` 的 seq 同口径
            # —— 同过滤 + 同排序）。前端据此做**逐条/选区**渲染，零错位。
            _seq = seq_i
            extra_raw = m["extra"] or "{}"
            image_url = None
            try:
                ex = json.loads(extra_raw) if extra_raw.startswith("{") else {}
            except Exception:
                ex = {}
            skey = ex.get("skey")
            origin_url = ex.get("origin_url")
            # 2026-09-25（H-25 统一落库契约）：缩略图（内联 base64 / 图床短链）
            # 由 extra.thumb 派生下发 —— text 已不再承载字节，前端优先用它渲染。
            thumb_url = ex.get("thumb") or None
            if skey and origin_url and _origin_image_resolver is not None:
                try:
                    res = _origin_image_resolver.resolve(
                        account=account, msg_id=str(msg_id or ""),
                        skey=skey, origin_url=origin_url,
                    )
                    if res.get("ok"):
                        image_url = res["url"]
                        if image_url.startswith("/"):
                            image_url = f"http://127.0.0.1:{_app_settings.backend_port}{image_url}"
                except Exception as e:
                    logger.debug(f"[私信拉取] 解密图片失败 msg_id={msg_id}: {e}")
            # 2026-09-17：引用回复（f18）随消息输出，供前端渲染引用区块。
            # 仅透传，不做任何补查（消息自带；昵称红线不涉及）。
            _reply = ex.get("reply") if isinstance(ex.get("reply"), dict) else None
            # 2026-09-17：语音转写文本（转写结果由 transcribe 接口写入 extra）。
            _trans = ex.get("transcription")
            # 2026-09-17：撤回标志（f11 字段级判据）。此前前端只能靠正文占位串
            # （`Recall Content Hided`）猜，现由服务端字段直接判定。
            _recalled = bool(int(ex.get("is_recalled") or 0))
            # 2026-09-17：IM 视频要素（**只透传，不取址**）。
            # 视频消息的 `tkey` 需另一跳换签名地址，属外呼——放在用户**点播**时
            # 才做（`POST /video/resolve`），此处绝不预取，避免浏览会话就狂发请求。
            _video = ex.get("video") if isinstance(ex.get("video"), dict) else None
            if _video:
                _video = {
                    "tkey": _video.get("tkey") or "",
                    "skey": _video.get("skey") or "",
                    "duration": _video.get("duration"),
                    "poster": _video.get("poster"),
                    # 只有少数分享卡自带 url；通常为空 → 前端点播时后端再取址
                    "url": _video.get("url") or "",
                }
            out_messages.append({
                "role": m["role"],
                "seq": _seq,
                "text": m["text"],
                "msg_type": m["msg_type"],
                "dir": "out" if m["role"] == "me" else "in",
                "type": _front_type(m["msg_type"] or "text"),
                "time": _fmt_ts(m["ts"]),  # 2026-09-05:改为完整时间,前端做日期分割线
                "msg_id": msg_id,
                "image_url": image_url,  # 前端 <img src> 直接用,None 则降级到缩略图
                # 2026-09-25（H-25）：契约内缩略图（extra.thumb → 数据URI/短链）。
                # 修复前它嵌在 text 里（`[图片] data:image/webp;base64,...`），
                # 污染 AI prompt / 导出 / 审计；现改为结构化下发。
                "thumb_url": thumb_url,
                # 2026-09-05 新增：消息来源通道。
                # wp_recv 落库时写 extra.source="wp"；WS 通道无该字段 → 兜底 "ws"。
                "source": ex.get("source") or "ws",
                "reply": _reply,
                "transcription": str(_trans) if _trans else None,

                "recalled": _recalled,
                # 2026-09-17：视频要素（tkey/skey/时长/封面）；None = 非视频消息
                "video": _video,
                # 2026-09-17：原始秒级时间戳（供前端跳转定位；time 是格式化串）
                "ts": float(m["ts"] or 0),
            })
        # 字段同时给两套命名,兼容前端不同消费点:
        #   role/msg_type —— 后端原生命名
        #   dir/type/time —— 前端 messages.tsx 的 Msg 接口命名
        #     dir: role=me -> out(我发),否则 in(对方发),与气泡左右布局对应
        #     time: HH:MM 字符串(ts 为 float 秒级时间戳)
        conv = {
            "conv_id": row["conv_id"],
            "name": row["peer_name"] or row["peer_id"] or row["conv_id"] or "会话",
            "peer_id": row["peer_id"],
            "peer_name": row["peer_name"],
            "unread": row["unread"] or 0,
            "avatar": row["avatar"] or "",
            "messages": out_messages,
        }
        # 标记已读
        try:
            conn.execute(
                "UPDATE dm_conversations SET unread=0 WHERE account=? AND conv_id=?",
                (account, str(conv_id)),
            )
            conn.commit()
        except Exception:
            pass
        logger.info(f"[私信拉取] 账号「{account}」会话 {conv_id} 详情: name={conv['name']}, messages={len(conv['messages'])} 条")
        return {"ok": True, "conversation": conv}
    except Exception as e:
        logger.warning(f"[MSG-003] " + f"[私信拉取] 账号「{account}」会话详情异常: {e}")
        return {"ok": False, "conversation": {}}


# ---------------------------------------------------------------------------
# 加密原图解析(2026-09-02)
# ---------------------------------------------------------------------------
# 抖音 IM 图片是 AES-256-GCM 加密(MEMORY + 08 §三十五):
#   skey = resource_url.skey(64 hex = 32 字节),origin_url = 远程加密链
# 后端从数据库读 extra,拉密文 + AESGCM 解密 → 按字节阈值分流:
#   小图(默认 ≤ 32KB)→ 本地静态目录 <app_root>/data/origin_images/
#   大图(> 32KB)→ 走 image_host(tucdn.wpon.cn,实测 0.3s 下载,见 08 末)
# 同一 (skey, origin_url) 进程内 30 天缓存,二次访问 0 请求。
# 风控边界:HTTP GET 不带账号 cookie,只靠图片 URL 自带签名参数;
# 频次 = 首访唯一,后续全缓存。零主动批量,零复用凭证。
# ---------------------------------------------------------------------------
class ImVideoReq(BaseModel):
    """IM 视频：下载 + CENC 解密（2026-09-17 新增；09-17 二次修订支持按消息取用）。

    **两种用法**（优先按消息）：
      · `account` + `msg_id` —— 【推荐】后端自己从 `extra.video` 取要素；
        缺 URL 时经 BCC 页面上下文用 `tkey` 换签名地址。前端只需知道"哪条消息"。
      · `url` + `skey` —— 直给要素（少数调用方已知地址时用），跳过取址。

    ⚠️ 关键契约（2026-09-17 实测订正）：抖音 IM 视频消息**不自带播放地址**，
    只有 `tkey`（tos_key）+ `skey`；地址必须**另一跳**换取（`batch_play_info`）。
    """
    account: str = ""
    conv_id: str = ""
    msg_id: str = ""
    url: str = ""
    skey: str = ""
    tkey: str = ""
    force: bool = False


@router.post("/video/resolve")
async def resolve_im_video(body: ImVideoReq):
    """下载并 CENC 解密 IM 视频 → 返回可直接播放/下载的本机 url（v0.43.83 扩展）。

    **两步链路**（对照上游 `extractor/video_downloader.py`）：
      ① 取址：消息里的 `tkey` → `POST /aweme/v1/web/maya/story/batch_play_info/v1/`
         → 签名 CDN 地址（**在账号自己的 BCC 页面上下文发**，复用登录态）；
      ② 取用：下载密文 → CENC 原地解密（`skey`）→ faststart 重封装 → 落缓存。

    设计取舍（对照铁律 §一·2）：
      · 只有**第 ① 步**需要登录态，故走 BCC 页面上下文；
        第 ② 步是 CDN 直链（自带签名、不带 cookie），与图片链路同款，
        **不经 BCC、不复制任何凭证到异域**。
      · 取址按**单条消息触发**（用户点播），不做批量预取；
        `batch_play_info` 每批 ≤10 由 `cenc_video.BATCH_SIZE` 约束。

    ⚠️ 缓存命中时**零外呼**：已解密过的视频直接回本机 url。
    """
    url = (body.url or "").strip()
    skey = (body.skey or "").strip()
    tkey = (body.tkey or "").strip()
    account = (body.account or "").strip()
    msg_id = (body.msg_id or "").strip()
    if not (url and skey) and not (account and msg_id) and not (tkey and skey):
        raise HTTPException(400, "需要 account+msg_id，或 url+skey，或 tkey+skey")

    # 需要取址（有 tkey 但没 url）时才拉 BCC；缓存命中/直给 url 时零外呼
    exec_js = None
    if tkey and not url:
        try:
            _u = _bcc_url(account, "/exec_js") if account else None
        except Exception:  # noqa: BLE001
            _u = None
        if _u:
            def exec_js(js: str, arg=None, _url=_u):
                r = _http_post_json(_url, {"js": js, "arg": arg, "timeout": 20},
                                    timeout=25.0)
                return r.get("result") if r.get("ok") else None

    try:
        res = await asyncio.to_thread(
            _im_video.resolve_by_message,
            account, msg_id, tkey=tkey, skey=skey, url=url,
            exec_js=exec_js, force=body.force)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[MSG-044] " + f"视频解析异常: {type(e).__name__}: {e}")
        raise HTTPException(502, f"视频解析异常: {type(e).__name__}")
    if not res.get("ok"):
        raise HTTPException(502, res.get("error") or "视频解析失败")
    name = ""
    if res.get("path"):
        name = Path(res["path"]).name
        res["url"] = f"http://127.0.0.1:{_app_settings.backend_port}/api/messages/video/{name}"
    res.pop("path", None)     # 不外泄本机绝对路径
    return res


@router.get("/video/{filename}")
async def serve_im_video(filename: str, request: Request):
    """返回已解密的视频明文文件（**支持 Range**，供 <video> 拖动播放）。

    filename 仅允许 [A-Za-z0-9_.-]，杜绝路径穿越。
    """
    safe = "".join(c for c in filename if c.isalnum() or c in "._-")
    if safe != filename or not safe:
        raise HTTPException(400, "filename 非法")
    fpath = _im_video._cache_dir() / safe
    if not fpath.exists() or not fpath.is_file():
        raise HTTPException(404, "视频不存在或已清理")
    try:
        _im_video.touch_local(safe)
    except Exception:
        pass
    size = fpath.stat().st_size
    rng = request.headers.get("range") or request.headers.get("Range")
    if rng and rng.startswith("bytes="):
        # 只支持单区间（浏览器 <video> 的实际用法）
        try:
            spec = rng.split("=", 1)[1].split(",")[0].strip()
            a, _, b = spec.partition("-")
            start = int(a) if a else 0
            end = int(b) if b else size - 1
            start = max(0, min(start, size - 1))
            end = max(start, min(end, size - 1))
        except Exception:
            start, end = 0, size - 1
        length = end - start + 1
        with open(fpath, "rb") as f:
            f.seek(start)
            chunk = f.read(length)
        return Response(content=chunk, status_code=206, media_type="video/mp4",
                        headers={"Content-Range": f"bytes {start}-{end}/{size}",
                                 "Accept-Ranges": "bytes",
                                 "Content-Length": str(length)})
    return FileResponse(str(fpath), media_type="video/mp4",
                        headers={"Accept-Ranges": "bytes"})


class OriginImageResolveRequest(BaseModel):
    account: str
    msg_id: str | None = None
    skey: str
    origin_url: str


@router.post("/origin_image/resolve")
async def resolve_origin_image(body: OriginImageResolveRequest):
    """按 (msg_id, skey, origin_url) 触发解密 → 返回可内嵌的 url。

    通常前端无需主动调本端点(会话详情接口已自动解析),本端点用于:
      - 历史消息的旧 extra 为空时手动补触发
      - 测试 / 调试
    """
    try:
        from auto_dm import origin_image_resolver
        res = origin_image_resolver.resolve(
            account=body.account, msg_id=body.msg_id or "",
            skey=body.skey, origin_url=body.origin_url,
        )
        # 把相对路径补成完整 URL,前端 <img> 可直接用
        if res.get("ok") and res.get("url", "").startswith("/"):
            res["url"] = f"http://127.0.0.1:{_app_settings.backend_port}{res['url']}"
        return res
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.get("/origin_image/stats")
async def origin_image_stats():
    """调试:解密缓存命中统计。

    ⚠️ 必须注册在 /origin_image/{filename} **之前** —— FastAPI 按注册顺序
    匹配路由,"stats" 会被 {filename} 通配吃掉(实测 2026-09-03:GET
    /origin_image/stats 返回 404「图片不存在」)。
    """
    try:
        from auto_dm import origin_image_resolver as _oir
        return {"ok": True, "stats": _oir._stats_snapshot()}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.get("/origin_image/{filename}")
async def serve_origin_image(filename: str):
    """返回本地解密的原图文件。前端 <img src=/api/messages/origin_image/xxx>。

    安全:filename 仅允许 [A-Za-z0-9_.-],杜绝 .. 路径穿越。
    """
    # 白名单过滤
    safe = "".join(c for c in filename if c.isalnum() or c in "._-")
    if safe != filename or not safe:
        raise HTTPException(status_code=400, detail="filename 非法")
    try:
        from auto_dm import origin_image_resolver as _oir
        cache_dir = _oir._origin_cache_dir()
    except Exception:
        try:
            from auto_dm import accounts as _acc
            cache_dir = Path(_acc.app_root()) / "data" / "origin_images"
        except Exception:
            raise HTTPException(status_code=500, detail="路径解析失败")
    fpath = cache_dir / safe
    if not fpath.exists() or not fpath.is_file():
        raise HTTPException(status_code=404, detail="图片不存在或已清理")
    # 命中即刷新 mtime(TTL 清理以 mtime 判龄,确保"最近看过"的图不被回收)
    try:
        from auto_dm import origin_image_resolver as _oir
        _oir.touch_local(safe)
    except Exception:
        pass
    # 按扩展名给 mime
    ext = fpath.suffix.lower()
    mime = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png", ".webp": "image/webp",
        ".gif": "image/gif", ".heic": "image/heic",
    }.get(ext, "application/octet-stream")
    return FileResponse(str(fpath), media_type=mime, filename=safe)


@router.post("/origin_image/sweep")
async def sweep_origin_images():
    """手动触发 TTL 清理(删除过期 / 超容的本地原图)。

    ⚠️ 必须注册在 /origin_image/{filename} **之前**(FastAPI 按注册顺序匹配)。
    正常无需手动调用 —— backend 启动 60s 后自动清理一次,
    之后 resolve() 每次写入新图时也会顺手检查(1 小时节流)。
    """
    try:
        from auto_dm import origin_image_resolver as _oir
        return {"ok": True, "sweep": _oir.sweep(force=True)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


class DbTransferReq(BaseModel):
    """数据库导入 / 导出 / 迁移（2026-09-17 新增）。

    action: export | import | migrate
    fmt:    json（默认，**脱敏**）| sqlite（整库物理复制，需 include_secrets=True）
    mode:   merge（默认，保留目标库独有行）| replace（清空目标表后写入）
    """
    action: str
    fmt: str = "json"
    mode: str = "merge"
    path: str = ""                    # 导出目标 / 导入来源
    include_secrets: bool = False     # 仅 sqlite 格式必须显式开启


@router.post("/db/transfer")
async def db_transfer(body: DbTransferReq) -> dict:
    """整库导出 / 导入 / 迁移（本地文件，不联网）。

    ⚠️ 安全契约（详见 `services/db_transfer.py`）：
      · `json` 导出**默认脱敏**（内容级扫描键名，清空 api_key/token/secret 等）；
      · `sqlite` 导出含账号与密钥，**必须显式** `include_secrets=True`；
      · 导入/迁移前会**自动备份**目标库为 `<db>.bak.<时间戳>`，失败可回滚。
    """
    action = (body.action or "").strip().lower()
    if action not in ("export", "import", "migrate"):
        raise HTTPException(422, "action 只能是 export/import/migrate")
    if not (body.path or "").strip():
        raise HTTPException(422, "path 必填")
    try:
        from services import db_transfer as _t
        if action == "export":
            res = await asyncio.to_thread(
                _t.export_db, body.path, fmt=body.fmt,
                include_secrets=body.include_secrets)
        elif action == "import":
            res = await asyncio.to_thread(_t.import_db, body.path, mode=body.mode)
        else:
            res = await asyncio.to_thread(_t.migrate, body.path, mode=body.mode)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[MSG-040] " + f"DB 转移失败: {type(e).__name__}: {e}")
        raise HTTPException(502, f"DB 转移失败: {type(e).__name__}")
    return {"ok": True, "action": action, **res}


class ChatlabExportReq(BaseModel):
    """ChatLab 导出（文件）或导入知识库。"""
    account: str
    conv_id: str = ""
    dest_dir: str = ""                # 导出目录（action=export 时必填）
    fmt: str = "jsonl"                # json | jsonl
    action: str = "export"            # export | to_kb
    target: str = "reply"             # to_kb 时：reply | pro
    max_items: int = 50


@router.post("/export/chatlab")
async def export_chatlab(body: ChatlabExportReq) -> dict:
    """导出 ChatLab 格式，或把会话问答导入知识库（**只读聊天表**）。

    · `action=export`  → 生成 ChatLab JSON/JSONL 文件（`dest_dir` 必填）
    · `action=to_kb`   → 抽取「对方提问 → 我方作答」配对写入 `reply_kb`/`pro_kb`
    """
    if not body.account:
        raise HTTPException(422, "account 必填")
    try:
        from services import chatlab_export as _ce
        if body.action == "export":
            if not (body.dest_dir or "").strip():
                raise HTTPException(422, "dest_dir 必填")
            res = await asyncio.to_thread(
                _ce.export_chatlab, body.account, body.conv_id, body.dest_dir,
                fmt=body.fmt)
        elif body.action == "to_kb":
            res = await asyncio.to_thread(
                _ce.export_to_kb, body.account, body.conv_id,
                target=body.target, max_items=body.max_items)
        elif body.action == "to_kb_preview":
            # 2026-09-18（E8）：预览问答对 —— **零写入**，只抽取供前端勾选。
            # 计划铁律：必须预览后再入库（避免把噪音批量灌进知识库）。
            from database import get_db as _gdb
            def _preview():
                if body.conv_id:
                    _conv, msgs = _ce._load_msgs(_gdb(), body.account, body.conv_id)
                else:
                    msgs = list(_gdb().execute(
                        "SELECT msg_id, role, text, msg_type, extra, ts FROM dm_messages "
                        "WHERE account=? AND msg_type <> '50001' ORDER BY ts ASC",
                        (body.account,)).fetchall())
                pairs = _ce.extract_qa_pairs(msgs)[:body.max_items]
                return {"ok": True, "pairs": pairs, "count": len(pairs),
                        "target": body.target}
            res = await asyncio.to_thread(_preview)
        else:
            raise HTTPException(422, "action 只能是 export/to_kb")
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[MSG-041] " + f"ChatLab 导出失败: {type(e).__name__}: {e}")
        raise HTTPException(502, f"ChatLab 导出失败: {type(e).__name__}")
    return {"ok": True, **res}


@router.post("/render/html")
async def render_chat_html(body: dict) -> dict:
    """把消息区间渲染成**自包含 HTML 长图**（本地渲染，内容不出机器）。

    请求体：`{account, conv_id, start_seq?, end_seq?, theme?, title?, subtitle?,
              self_uid?, width?, scale?}`
    返回 `{ok, html, chars, theme}`；HTML 中消息文本已强制转义（防注入）。

    注：上游直出 PNG（无头浏览器截图）。我方先产出 HTML（可浏览器打印/另存），
    服务端直出 PNG 的接法是在 BCC 容器里 `page.screenshot()`——属下一阶段。
    """
    account = str(body.get("account") or "").strip()
    conv_id = str(body.get("conv_id") or "").strip()
    if not account or not conv_id:
        raise HTTPException(422, "account 与 conv_id 必填")
    try:
        from services import chat_render as _cr
        html = await asyncio.to_thread(
            _cr.render_html, account, conv_id,
            body.get("start_seq"), body.get("end_seq"),
            theme=str(body.get("theme") or "dark"),
            title=str(body.get("title") or ""),
            subtitle=str(body.get("subtitle") or ""),
            self_uid=str(body.get("self_uid") or ""),
            width=int(body.get("width") or _cr.DEFAULT_WIDTH),
            scale=float(body.get("scale") or _cr.DEFAULT_SCALE),
        )
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[MSG-042] " + f"长图渲染失败: {type(e).__name__}")
        raise HTTPException(502, f"长图渲染失败: {type(e).__name__}")
    return {"ok": True, "html": html, "chars": len(html),
            "theme": str(body.get("theme") or "dark")}


class ChatlabDownloadReq(BaseModel):
    """ChatLab 导出并下载（2026-09-17 乙方案）。

    桌面应用没有「服务端路径」语义：调用方**不必**给 `dest_dir`，
    后端落到默认导出目录（`<app_root>/exports/chatlab`）并返回下载直链，
    前端带令牌 fetch 成 Blob 触发浏览器保存。
    """
    account: str
    conv_id: str
    fmt: str = "jsonl"          # json | jsonl


@router.post("/export/chatlab/download")
async def export_chatlab_download(body: ChatlabDownloadReq) -> dict:
    """导出到**默认导出目录**并返回下载直链（不要求调用方传路径）。"""
    if not body.account or not body.conv_id:
        raise HTTPException(422, "account 与 conv_id 必填")
    try:
        from services import chatlab_export as _ce
        res = await asyncio.to_thread(
            _ce.export_chatlab, body.account, body.conv_id, "", fmt=body.fmt)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[MSG-050] " + f"ChatLab 导出失败: {type(e).__name__}: {e}")
        raise HTTPException(502, f"ChatLab 导出失败: {type(e).__name__}")
    fn = res.get("filename") or Path(res.get("path") or "").name
    res.pop("path", None)                      # 不外泄本机绝对路径
    res["url"] = f"http://127.0.0.1:{_app_settings.backend_port}" \
                 f"/api/messages/export/chatlab/file/{fn}"
    return res


@router.get("/export/chatlab/file/{filename}")
async def download_chatlab_file(filename: str):
    """下载已导出的 ChatLab 文件（文件名白名单 + 导出目录内校验）。"""
    from services import chatlab_export as _ce
    p = _ce._safe_export_file(filename)
    if p is None:
        raise HTTPException(404, "导出文件不存在")
    mime = "application/json; charset=utf-8"
    return FileResponse(str(p), media_type=mime, filename=filename)


@router.post("/render/png")
async def render_chat_png(body: dict) -> Response:
    """把消息区间渲染成 **PNG 长图**（Pillow 原生绘制，**不经 BCC / 无浏览器**）。

    请求体：`{account, conv_id, start_seq?, end_seq?, theme?, title?, subtitle?,
              width?, scale?, as_base64?}`
    返回：默认 `image/png` 二进制；`as_base64=true` 时返回 JSON
    `{ok, data_uri, bytes, width, height}`。

    设计取舍（用户 2026-09-17 明确要求）：**不走 BCC**。
    可选路径对比见 `services/chat_render_png.py` 模块头 ——
    Pillow 原生绘制零新依赖、零浏览器、纯离线，代价是排版由我们自测（已单测覆盖）。
    """
    from fastapi import Response as _Resp
    account = str(body.get("account") or "").strip()
    conv_id = str(body.get("conv_id") or "").strip()
    if not account or not conv_id:
        raise HTTPException(422, "account 与 conv_id 必填")
    kw = dict(
        start_seq=body.get("start_seq"), end_seq=body.get("end_seq"),
        theme=str(body.get("theme") or "dark"),
        title=str(body.get("title") or ""),
        subtitle=str(body.get("subtitle") or ""),
        width=int(body.get("width") or 520),
        scale=float(body.get("scale") or 2.0),
    )
    try:
        from services import chat_render_png as _png
        data = await asyncio.to_thread(_png.render_png, account, conv_id, **kw)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[MSG-043] " + f"PNG 渲染失败: {type(e).__name__}: {e}")
        raise HTTPException(502, f"PNG 渲染失败: {type(e).__name__}")
    name = f"chat_{conv_id.replace(':','_')[:40]}.png"
    if body.get("as_base64"):
        import base64 as _b64
        return {"ok": True, "bytes": len(data),
                "data_uri": "data:image/png;base64," + _b64.b64encode(data).decode("ascii")}
    return _Resp(content=data, media_type="image/png",
                 headers={"Content-Disposition": f'inline; filename="{name}"'})


@router.get("/open/conversations")
async def open_conversations(account: str, search: str = "", page: int = 1,
                             page_size: int = 50) -> dict:
    """**开放 API（只读）**：会话列表（照上游 `GET /api/conversations`）。

    只返回公开字段（不含 short_id/extra 等内部键）。
    """
    if not account:
        raise HTTPException(422, "account 必填")
    from database import get_db
    conn = get_db()
    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 50), 200))
    where = "account = ?"
    params: list = [account]
    if (search or "").strip():
        where += " AND (peer_name LIKE ? OR conv_id LIKE ?)"
        pat = f"%{search.strip()}%"
        params += [pat, pat]
    total = conn.execute(
        f"SELECT COUNT(*) n FROM dm_conversations WHERE {where}", tuple(params)
    ).fetchone()["n"]
    rows = conn.execute(
        f"SELECT conv_id, peer_name, last_ts, unread FROM dm_conversations "
        f"WHERE {where} ORDER BY last_ts DESC LIMIT ? OFFSET ?",
        tuple(params) + (page_size, (page - 1) * page_size)).fetchall()
    return {"ok": True, "total": int(total or 0), "page": page,
            "page_size": page_size,
            "items": [{"conv_id": r["conv_id"],
                       "name": r["peer_name"] or r["conv_id"],
                       "last_ts": float(r["last_ts"] or 0),
                       "unread": int(r["unread"] or 0)} for r in rows]}


@router.get("/open/messages")
async def open_messages(account: str, conv_id: str, start_seq: int | None = None,
                        end_seq: int | None = None, self_uid: str = "") -> dict:
    """**开放 API（只读）**：消息区间（不含 skey/origin_url 等敏感 extra 字段）。"""
    if not account or not conv_id:
        raise HTTPException(422, "account 与 conv_id 必填")
    try:
        from services import chat_render as _cr
        res = await asyncio.to_thread(
            _cr.messages_for_view, account, conv_id, start_seq, end_seq,
            self_uid=self_uid)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"读取失败: {type(e).__name__}")
    return {"ok": True, **res}


@router.get("/open/messages/by-date")
async def open_messages_by_date(account: str, conv_id: str, date: str,
                                tz: int = 8) -> dict:
    """**开放 API（只读）**：某自然日的全部消息（照上游 `by-date`）。"""
    if not account or not conv_id or not date:
        raise HTTPException(422, "account / conv_id / date 必填")
    try:
        from services import chat_render as _cr
        res = await asyncio.to_thread(_cr.by_date, account, conv_id, date, tz)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"读取失败: {type(e).__name__}")
    return {"ok": True, **res}


@router.get("/open/stats/daily")
async def open_stats_daily(account: str, conv_id: str, tz: int = 8) -> dict:
    """**开放 API（只读）**：逐日消息量 + 汇总视图（照上游 `stats/daily`）。"""
    if not account or not conv_id:
        raise HTTPException(422, "account 与 conv_id 必填")
    try:
        from services import dm_search as _s, chat_render as _cr
        daily = await asyncio.to_thread(_s.daily_stats, account, conv_id, tz)
        res = _cr.export_stats(daily)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"统计失败: {type(e).__name__}")
    return {"ok": True, **res}


class DmSearchReq(BaseModel):
    """全库/会话内消息检索（2026-09-17 新增，纯读库）。

    对应上游开放 API 的 `GET /api/search`；我方作为 POST 端点复用既有鉴权体系。
    条件至少要给一个（关键词 / conv_id / 时间区间 / 媒体类型），否则 422。
    """
    account: str
    q: str = ""
    conv_id: str | None = None
    start_time: float | None = None
    end_time: float | None = None
    media_type: str | None = None      # image | video | media
    page: int = 1
    page_size: int = 50


@router.post("/search")
async def dm_search(body: DmSearchReq) -> dict:
    """在全库或指定会话内检索消息（文本 / 时间区间 / 媒体类型）。

    设计契约：
      · **只读**：纯 SELECT，无任何写操作；
      · 时间区间为半开 `[start_time, end_time)`（相邻日期不重叠）；
      · 过滤口径与聊天页一致（回执/脏数据不返回，避免「搜到却点不开」）；
      · 不触网、不查用户信息（不涉昵称红线）。
    """
    if body.media_type and body.media_type not in ("image", "video", "media"):
        raise HTTPException(422, "media_type 只能是 image/video/media")
    try:
        from services import dm_search as _s
        res = await asyncio.to_thread(
            _s.search_messages, body.account,
            query=body.q, conv_id=body.conv_id,
            start_time=body.start_time, end_time=body.end_time,
            media_type=body.media_type, page=body.page,
            page_size=body.page_size,
        )
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[MSG-031] " + f"消息检索失败: {type(e).__name__}")
        raise HTTPException(502, f"消息检索失败: {type(e).__name__}")
    return {"ok": True, **res}


@router.get("/conversation/daily")
async def conversation_daily(account: str, conv_id: str, tz: int = 8) -> dict:
    """会话逐日消息量（供日历/月份跳转定位某天首条消息）。

    对应上游开放 API 的 `.../stats/daily`。**只读**。
    """
    try:
        from services import dm_search as _s
        res = await asyncio.to_thread(_s.daily_stats, account, conv_id, tz)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[MSG-032] " + f"逐日统计失败: {type(e).__name__}")
        raise HTTPException(502, f"逐日统计失败: {type(e).__name__}")
    return {"ok": True, **res}


@router.post("/voice/transcribe")
async def voice_transcribe(body: dict):
    """语音消息转写（2026-09-17 新增，对照上游 douyin-chat-export v2.0.0）。

    请求体：`{account: str, conv_id?: str, limit?: int}`

    风控边界（铁律 §一 / §二）：
      · 识别请求在**账号自己的 BCC 容器页面上下文**里发出
        （`fetch(..., {credentials:'include'})`）—— 复用账号常驻浏览器登录态，
        **绝不**在后端用 cookie 拼 requests 直发；
      · 只处理**语音消息**（消息体自带 uri），不查昵称、不遍历用户信息；
      · 每批 ≤10 条（上游实测上限），已有转写的消息跳过。

    返回 `{ok, requested, succeeded, skipped, reason}`。
    """
    account = str(body.get("account") or "").strip()
    conv_id = str(body.get("conv_id") or "").strip()
    # 2026-09-17（E6）：单条转写 —— 语音气泡上的「转写」按钮精确到条
    msg_id = str(body.get("msg_id") or "").strip()
    if not account:
        raise HTTPException(400, "account 必填")
    try:
        limit = int(body.get("limit") or 30)
    except Exception:
        limit = 30
    limit = max(1, min(limit, 60))
    if msg_id:
        limit = 1          # 单条模式：只可能命中一条

    # 取该账号 BCC 地址（含 ensure_bcc 拉起；与 /wp_send 同一寻址方式）
    try:
        url = _bcc_url(account, "/exec_js")
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"BCC 不可用: {type(e).__name__}",
                "requested": 0, "succeeded": 0, "skipped": 0,
                "reason": "bcc-unavailable"}

    def _exec_js(js: str, arg=None):
        """在 BCC 页面上下文执行 JS（同步，供 voice_transcribe 注入）。"""
        r = _http_post_json(url, {"js": js, "arg": arg, "timeout": 45},
                            timeout=60.0)
        if not r.get("ok"):
            raise RuntimeError(r.get("msg") or "exec_js 失败")
        return r.get("result")

    def _run():
        from services.voice_transcribe import transcribe_pending
        return transcribe_pending(account, conv_id=conv_id,
                                  exec_js=_exec_js, limit=limit, msg_id=msg_id)

    try:
        res = await asyncio.to_thread(_run)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[MSG-021] " + f"语音转写失败: {type(e).__name__}")
        return {"ok": False, "error": type(e).__name__, "requested": 0,
                "succeeded": 0, "skipped": 0, "reason": "exception"}
    logger.info(f"[MSG-020] " + f"语音转写 account={account} conv={conv_id or '-'} "
                f"→ {res}")
    return res


class MergeForwardReq(BaseModel):
    """合并转发正文解析 / 补抓（2026-09-17 新增）。

    · `account` + `msg_id`：从 dm_messages.extra.merge 读卡片内容（本地优先）；
    · `content`：也可直接传卡片 content 对象（调试/前端回传）；
    · `fetch=True` 且本地无完整正文、卡片带 upload_key_list 时，
      **经 BCC 页面上下文**取资源链接（同域、复用登录态，不转发 cookie），
      再按白名单域**不带 cookie** 下载并用 AES-GCM 解密。
    """
    account: str = ""
    msg_id: str = ""
    content: dict | None = None
    fetch: bool = False


class NicknameFallbackReq(BaseModel):
    """昵称兜底（2026-09-17 新增，**默认关闭**，见 services/nickname_fallback.py）。

    `dry_run=True` 只列出候选，不发任何请求（用于先看会查到谁）。
    """
    account: str
    limit: int | None = None
    dry_run: bool = False


@router.get("/nickname_fallback/status")
async def nickname_fallback_status():
    """兜底开关与限速状态（用于确认它是否处于关闭态）。"""
    from services import nickname_fallback as NF
    return NF.status()


@router.post("/nickname_fallback/run")
async def nickname_fallback_run(body: NicknameFallbackReq):
    """执行一次昵称兜底（受配置/间隔/单次/每日上限约束；**默认关闭**）。

    风控边界：请求在账号自己的 BCC 页面上下文发出（复用登录态），
    后端不直发 cookie；只补「库里没有昵称」的会话，已有昵称绝不覆盖。
    """
    account = (body.account or "").strip()
    if not account:
        raise HTTPException(400, "account 必填")
    from services import nickname_fallback as NF

    allow, why = NF.rate_limit_check()
    if not allow and why != "ok":
        # dry_run 也要先过开关（关闭态一律拒绝，避免「用 dry run 试探」）
        return {"ok": False, "reason": why, "candidates": 0, "queried": 0,
                "updated": 0, "skipped": 0, "limit_info": NF._limit_info()}

    def _exec_js(js: str, arg=None):
        url = _bcc_url(account, "/exec_js")
        r = _http_post_json(url, {"js": js, "arg": arg, "timeout": 45}, timeout=60.0)
        if not r.get("ok"):
            raise RuntimeError(r.get("msg") or "exec_js 失败")
        return r.get("result")

    def _run():
        return NF.run_fallback(account, _exec_js, limit=body.limit,
                               dry_run=body.dry_run)

    try:
        res = await asyncio.to_thread(_run)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[MSG-048] " + f"昵称兜底异常: {type(e).__name__}")
        return {"ok": False, "reason": f"exception:{type(e).__name__}",
                "candidates": 0, "queried": 0, "updated": 0, "skipped": 0}
    logger.info(f"[MSG-049] " + f"昵称兜底 account={account} dry={body.dry_run} → {res}")
    return res


@router.post("/merge_forward/resolve")
async def resolve_merge_forward(body: MergeForwardReq):
    """解析合并转发卡片；必要时补抓远端正文（**单条触发**，默认不抓）。"""
    MF = _mf
    content: dict | None = body.content if isinstance(body.content, dict) else None
    stored_bodies = None
    if content is None and body.account and body.msg_id:
        try:
            row = get_db().execute(
                "SELECT extra FROM dm_messages WHERE account=? AND msg_id=?",
                (body.account, body.msg_id)).fetchone()
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"读库失败: {type(e).__name__}")
        if row is None:
            raise HTTPException(404, "消息不存在")
        try:
            ex = json.loads(row["extra"] or "{}")
        except Exception:
            ex = {}
        content = (ex or {}).get("merge") if isinstance(ex, dict) else None
        stored_bodies = (ex or {}).get("merge_bodies") if isinstance(ex, dict) else None
        if not isinstance(content, dict) or not content:
            raise HTTPException(422, "该消息不是合并转发卡片（extra.merge 为空）")

    if not isinstance(content, dict) or not content:
        raise HTTPException(422, "缺少 content（或 account+msg_id）")
    if not MF.is_merge_forward(content):
        raise HTTPException(422, "content 不是合并转发卡片（aweType != 13600）")

    # ① 本地正文优先（inline_content 已含在 content 内；stored_bodies 为上次抓取结果）
    bodies = stored_bodies if MF.complete(content, stored_bodies) else None
    if bodies is None and MF.complete(content, content.get("inline_content")):
        bodies = content["inline_content"]

    local_map: dict[str, dict] = {}
    if bodies is None and body.account:
        ids = [i for i in MF.expected_ids(content)][:MF.MAX_MESSAGES]
        if ids:
            try:
                q = ",".join("?" * len(ids))
                rows = get_db().execute(
                    f"SELECT msg_id, text, extra, msg_type, ts FROM dm_messages "
                    f"WHERE account=? AND msg_id IN ({q})",
                    (body.account, *ids)).fetchall()
                for r in rows:
                    try:
                        _ex = json.loads(r["extra"] or "{}")
                    except Exception:
                        _ex = {}
                    local_map[str(r["msg_id"])] = {
                        "text": r["text"], "aweType": str((_ex or {}).get("aweType") or ""),
                        "msg_type": r["msg_type"]}
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[MSG-045] " + f"合并转发本地正文查询失败: {e}")

    fetched = False
    fetch_err = ""
    # ② 远端补抓（仅当本地/inline 都没有，且用户显式要求 fetch）
    if bodies is None and local_map and len(local_map) == len(MF.expected_ids(content)):
        pass      # 本地已能凑齐，无需网络
    elif bodies is None and body.fetch:
        if not MF.upload_keys(content):
            fetch_err = "no-upload-keys"
        else:
            try:
                url = _bcc_url(body.account, "/exec_js")
            except Exception as e:  # noqa: BLE001
                raise HTTPException(502, f"BCC 不可用: {type(e).__name__}")
            chain = [{"msg_id": int(str(body.msg_id).removeprefix("srv_") or 0),
                      "conv_id": int(str(content.get("__short_id") or 0) or 0)}] \
                if body.msg_id else None

            def _exec_js(js: str, arg=None):
                r = _http_post_json(url, {"js": js, "arg": arg, "timeout": 45},
                                    timeout=60.0)
                if not r.get("ok"):
                    raise RuntimeError(r.get("msg") or "exec_js 失败")
                return r.get("result")

            async def _request(path: str, b: dict) -> dict:
                res = await asyncio.to_thread(
                    _exec_js, _mf.WEB_FETCH_JS,
                    [MF.WEB_OBJECT_URL_PATH, json.dumps(b, ensure_ascii=False)])
                if not isinstance(res, dict) or res.get("status") != 200:
                    return {}
                try:
                    payload = json.loads(res.get("body") or "{}")
                except Exception:
                    return {}
                return payload

            async def _download(u: str, max_bytes: int) -> bytes:
                return await asyncio.to_thread(_im_video._http_get, u, 60)

            try:
                bodies = await MF.fetch_uploaded_bodies(
                    content, _request, _download, access_chain=chain)
                fetched = True
            except Exception as e:  # noqa: BLE001
                fetch_err = type(e).__name__
                logger.warning(f"[MSG-046] " + f"合并转发补抓失败: {fetch_err}")
            # 抓到的正文落库（下次零请求）；仅在与索引完全一致时写入
            if fetched and body.account and body.msg_id:
                try:
                    conn = get_db()
                    row = conn.execute(
                        "SELECT extra FROM dm_messages WHERE account=? AND msg_id=?",
                        (body.account, body.msg_id)).fetchone()
                    if row is not None:
                        _ex = json.loads(row["extra"] or "{}")
                        _ex["merge_bodies"] = bodies
                        conn.execute(
                            "UPDATE dm_messages SET extra=? WHERE account=? AND msg_id=?",
                            (json.dumps(_ex, ensure_ascii=False), body.account,
                             body.msg_id))
                        conn.commit()
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"[MSG-047] " + f"合并转发正文落库失败: {e}")

    st = MF.status(content, bodies)
    return {"ok": True, "fetched": fetched, "fetch_error": fetch_err,
            "source": ("inline" if MF.complete(content, content.get("inline_content"))
                       else ("stored" if MF.complete(content, stored_bodies)
                             else ("fetched" if fetched
                                   else ("local" if local_map else "missing")))),
            "text": MF.render_text(content, bodies, local_map),
            **st}


@router.post("/request")
async def request_dm(body: RequestDmBody, request: Request):
    """新建会话（手动把某昵称/评论入队，走与直播间捕获同一条延迟发送队列）

    对齐前端 messages.tsx 的 requestDm(name) 调用。
    委托 adm.dispatch.submit（与原版 web_bridge.requestDm 一致）。
    """
    adm = request.app.state.adm
    if adm is None or adm.dispatch is None:
        return {"ok": False, "msg": "引擎尚未启动（无调度中心）"}
    target = {
        "user_id": None,
        "sec_uid": None,
        "nickname": body.name,
        "comment": body.comment or "",
    }
    try:
        ok = adm.dispatch.submit(target)
    except Exception as e:
        return {"ok": False, "msg": f"入队失败: {e}"}
    if ok:
        return {"ok": True, "msg": f"已为「{body.name}」创建私信会话（进入延迟发送队列）"}
    return {"ok": False, "msg": f"「{body.name}」已在队列中或已达上限"}


@router.post("/send")
async def send_dm(body: SendDmRequest):
    """手动发送私信，按 channel 路由到 WS 或 WP 通道，失败自动降级。

    2026-09-05：新增双通道。
      - channel='ws'（默认，**主通道**）：转发 recv_daemon /send，走
        DouyinAPI.send_msg HTTP API。有 ACK、落库含 skey、不依赖浏览器。
      - channel='wp'（**备用通道**）：转发 BCC /wp_send，在 chat 页上下文
        走 DOM 流程发送。无 ACK，依赖浏览器常驻。

    2026-09-06 全局治理（2.4 自动降级）：
      用户明确「WS 优先级比 WP 高」。此前两通道是硬分支——选了 wp 就
      只用 wp，BCC 一挂发送直接失败，无任何回退。
      现在引入自动降级：
        - channel='ws'（或 'auto'）失败 → 自动回退 wp
        - channel='wp' 失败 → 自动回退 ws
        - 两个都失败才返回失败，并带上两条通道的错误原因
      前端无需改动即可受益（默认 ws 自动获得 wp 兜底）。
    """
    fallback_enabled = body.channel in ("ws", "wp", "auto")
    first = "wp" if body.channel == "wp" else "ws"
    second = "ws" if first == "wp" else "wp"

    async def _try(ch: str) -> dict:
        if ch == "wp":
            return await wp_send_dm(body)
        # 2026-09-07：私信发送统一调度（架构重构）。
        # WS 通道先经「会话整理池」归一化/去重/校验，再由 per-account
        # 串行调度器出队发送——解决多板块并发时同会话乱序/重复、
        # 以及 peer_id 被污染导致"发给自己"的事故。
        # 注：wp 通道走 BCC 页内 DOM（实时交互），不进队列，保持原链路。
        try:
            from services.dm_dispatch import submit as _dm_submit
            r = await asyncio.to_thread(
                _dm_submit, body.account, body.conv_id, body.text,
                "manual", 0)
            if r.accepted:
                # 入池成功 = 已受理，但**尚未发出**（异步调度）。
                # 同步等待发送结果（最多 90s）后再返回，避免前端误判
                # "已发送"（用户明确要求：验证后才算成功）。
                from services.dm_dispatch import get_dispatcher as _get_disp
                import time as _t
                disp = _get_disp()
                deadline = _t.time() + 90
                while _t.time() < deadline:
                    st = await asyncio.to_thread(disp.task_status, r.task_id)
                    if not st or st.get("status") in ("done", "failed"):
                        if st and st.get("status") == "done":
                            return {"ok": True, "task_id": r.task_id,
                                    "queued": True}
                        return {"ok": False,
                                "error": (st or {}).get("error") or "调度发送失败"}
                    await asyncio.sleep(0.5)
                return {"ok": False, "error": "调度发送超时（90s）",
                        "task_id": r.task_id}
            # 入池被拒（会话无效/重复/队列满）→ 交给降级逻辑走 wp
            return {"ok": False,
                    "error": r.error or "入池被拒",
                    "pool_rejected": True}
        except Exception as e:
            return {"ok": False, "error": f"调度入池异常: {e}"}

    errors = {}
    try:
        d = await _try(first)
        if d and d.get("ok"):
            # 首次尝试成功；若发生过降级则标注实际通道
            if d.get("channel") is None:
                d["channel"] = first
            return d
        errors[first] = (d or {}).get("error") or (d or {}).get("msg") or "未知失败"
    except urllib.error.HTTPError as e:
        errors[first] = ("账号私信守护未运行" if e.code == 404
                         else f"私信守护返回 {e.code}")
    except Exception as e:
        errors[first] = str(e)

    if not fallback_enabled:
        return {"ok": False, "error": errors.get(first), "channel": first}

    # 首次失败 → 自动回退备用通道
    logger.warning(f"[SEND-001] " + f"[send][{body.account}] {first.upper()} 通道失败（{errors.get(first)}），"
        f"自动回退 {second.upper()} 通道")
    try:
        d = await _try(second)
        if d and d.get("ok"):
            d["channel"] = second
            d["fallback_from"] = first
            return d
        errors[second] = (d or {}).get("error") or (d or {}).get("msg") or "未知失败"
    except Exception as e:
        errors[second] = str(e)

    return {
        "ok": False,
        "error": f"两条通道均失败：{first.upper()}={errors.get(first)}；"
                 f"{second.upper()}={errors.get(second)}",
        "channel": None,
        "errors": errors,
    }


@router.post("/send_image")
async def send_image_dm(body: SendImageRequest):
    """发送图片私信：转发 recv_daemon /send_image（后端直发全链路 ①-⑥）。"""
    try:
        url = _recv_url(body.account, "/send_image")
        if url is None:
            return {"ok": False, "error": "端口分配失败"}
        d = _http_post_json(url, {
            "account": body.account,
            "conv_id": body.conv_id,
            "image_b64": body.image_b64,
            "filename": body.filename or "image.jpg",
            "source": body.source,
        })
        return d
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"ok": False, "error": "账号私信守护未运行"}
        return {"ok": False, "error": f"私信守护返回 {e.code}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.post("/wp_send")
async def wp_send_dm(body: SendDmRequest):
    """WP 通道发送（抖音网页版 chat 页），转发到 BCC /wp_send。

    与 /send?channel=wp 等价，单独暴露便于前端显式指定。
    BCC 未就绪时返回明确错误（不静默降级到 WS，避免用户以为发成功）。
    """
    try:
        url = _bcc_url(body.account, "/wp_send")
        d = _http_post_json(url, {
            "account": body.account,
            "conv_id": body.conv_id,
            "text": body.text,
        }, timeout=30.0)  # 页面内调用较慢，放宽超时
        return d
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"ok": False, "error": "浏览器容器(BCC)未运行，WP 通道不可用"}
        return {"ok": False, "error": f"BCC 返回 {e.code}"}
    except Exception as e:
        return {"ok": False, "error": f"WP 通道发送失败: {e}"}


# ---------------------------------------------------------------------------
# 更新会话（按需触发前移捕获）
# ---------------------------------------------------------------------------
# 私信页「更新会话」按钮调用：经 BCC 一次性拉取会话列表 + 会话详情（含聊天记录），
# 写库后前端 5s 轮询自动刷新，无需本接口返回数据。
#
# 与「引擎校验」的职责边界（2026-08-29 收敛）：
#   - 引擎校验：只判守护凭证(wp) + 私信守护活性(dm)，不跑捕获（轻量、可高频）。
#   - 更新会话：真正跑 capture_all（重、涉及浏览器与网络），只在用户点按钮时触发。
#
# 风控边界：capture_all 的昵称来源仍是 BCC 被动截获前端自发 im/user/info，
# 后端零主动批量查昵称；聊天记录走首包(2043) + 长会话 cmd 301 补全。
# ---------------------------------------------------------------------------
class RefreshConvsRequest(BaseModel):
    account: str
    with_browser: bool = True


@router.post("/{account}/refresh")
async def refresh_conversations(account: str, body: RefreshConvsRequest | None = None):
    """按需触发前移捕获：拉取会话列表 + 会话详情（聊天记录）并写库。

    返回 {ok, n_conv, n_msg, elapsed, error}。
    """
    import time as _time
    from auto_dm.daemon_launcher import ensure_daemons_for
    from auto_dm.conversation_capture import capture_all

    t0 = _time.time()
    use_browser = body.with_browser if body else True
    try:
        # 2026-09-06 全局调用链治理（事件循环阻塞）：
        # ensure_daemons_for（可能拉起浏览器进程）+ capture_all（含浏览器
        # 操作，数秒~数十秒）都是同步重型调用。在 async 路由里同步执行会
        # 阻塞 uvicorn 事件循环，期间所有其他 API（含 3s/5s 高频轮询）全部
        # 排队 → 前端整体卡死。改 run_in_executor 丢线程池。
        def _do_refresh():
            # 用户点「更新会话」→ 豁免启动冷静期
            launched = ensure_daemons_for(account, skip_cooldown=True)
            if use_browser and not launched.get("browser"):
                # 2026-09-14 v0.43.11：拿不到浏览器必须**显式失败并向上报**，
                # 不再静默继续跑出「假成功」（契约见 errcode.CAP-016.root：
                # 「注意此处不 return 会继续跑出假成功」）。
                raise RuntimeError(
                    "浏览器守护(BCC)未能就绪，昵称/头像无法捕获；"
                    "请稍后重试或在账号管理页启动该账号的浏览器守护"
                    f"（{launched.get('msg') or '端口未开'}）")
            return capture_all(account, with_browser=use_browser)

        loop = asyncio.get_running_loop()
        try:
            n_conv, n_msg = await loop.run_in_executor(None, _do_refresh)
        finally:
            # 跨调用窗口租约必须在「更新会话全程」结束时释放（含异常路径），
            # 否则该账号浏览器只能等 BCC 侧 TTL（≤300s）回收
            # ——期间其它业务（发送/WP/保活）全部拿不到租约。
            try:
                from auto_dm.conversation_capture import release_active_lease
                release_active_lease(account)
            except Exception:
                pass
        elapsed = round(_time.time() - t0, 1)
        logger.info(f"[refresh][{account}] 更新会话完成：会话 {n_conv}（消息 {n_msg}），耗时 {elapsed}s")
        return {
            "ok": True,
            "n_conv": n_conv,
            "n_msg": n_msg,
            "elapsed": elapsed,
        }
    except Exception as e:
        logger.warning(f"[CAP-002] " + f"[refresh][{account}] 更新会话失败: {e}")
        return {
            "ok": False,
            "error": str(e),
            "elapsed": round(_time.time() - t0, 1),
        }
