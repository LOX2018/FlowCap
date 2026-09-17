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
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
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


class SendImageRequest(BaseModel):
    account: str
    conv_id: str
    # 图片二进制 base64（≤20MB 原始大小）
    image_b64: str
    filename: str = "image.jpg"


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
        db_map = {str(r["peer_id"]): (r["peer_name"], r["avatar"]) for r in rows}

        def _extract_peer_uid(cid: str):
            """从 conv_id 0:1:uid_a:uid_b 提取对端 UID（排除 my_uid）。"""
            if not cid:
                return None
            parts = cid.split(":")
            if len(parts) >= 4:
                uid_a, uid_b = parts[2], parts[3]
                if my_uid and uid_a == my_uid:
                    return uid_b
                if my_uid and uid_b == my_uid:
                    return uid_a
                # 无 my_uid 兜底：取与 my_uid 不同的那个；都不等则取 uid_b
                return uid_b
            return None

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
            "SELECT c.conv_id,c.peer_id,c.peer_name,c.short_id,c.last_ts,c.unread,c.avatar "
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
            if pn and pn != r["peer_id"] and not str(pn).isdigit():
                nickname_by_peer[str(r["peer_id"])] = pn
        for r in rows:
            cid = r["conv_id"]
            peer_id = r["peer_id"]
            peer_name = r["peer_name"]
            name = peer_name or nickname_by_peer.get(str(peer_id)) or peer_id or cid or "会话"
            convs.append({
                "conv_id": cid,
                "name": name,
                "peer_id": peer_id,
                "peer_name": peer_name,
                "unread": r["unread"] or 0,
                "avatar": r["avatar"] or "",
                "messages": [],
            })
        unread_total = sum((c.get("unread") or 0) for c in convs)
        named = sum(1 for c in convs if c["name"] and not str(c["name"]).isdigit())
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
            # 2026-09-06 过滤抖音「未发过消息的陌生会话」系统占位提示
            # （用户实测：对方回复你或互关之前，可发送一条文字消息...）；
            # 此前 sender 来自陌生人被当真实消息入库污染聊天记录
            "AND text NOT LIKE '%对方回复你或互关之前%' "
            "AND text NOT LIKE '%请礼貌发言%' "
            "AND text NOT LIKE '%自觉遵守%' "
            "AND text NOT LIKE '[未知媒体]%' "
            # 2026-09-16：只滤「空分享」（裸 [分享视频]，WS 解析噪音无 ID）；
            # 带 ID 的 "[分享视频] 视频ID x" 是真实视频分享（08 §16.4 实测），
            # 应正常展示。此前 NOT LIKE '[分享视频]%' 把真实分享也滤掉了
            # （设计漂移）。空分享的准确特征是「整条文本就是 [分享视频]」。
            "AND text <> '[分享视频]' "
            "AND text NOT LIKE 'https://www.iesdouyin.com/share/%' "  # 群聊分享链接脏数据
            "ORDER BY ts ASC",
            (account, str(conv_id)),
        ).fetchall()
        out_messages = []
        for m in msgs:
            msg_id = m["msg_id"]
            extra_raw = m["extra"] or "{}"
            image_url = None
            try:
                ex = json.loads(extra_raw) if extra_raw.startswith("{") else {}
            except Exception:
                ex = {}
            skey = ex.get("skey")
            origin_url = ex.get("origin_url")
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
            out_messages.append({
                "role": m["role"],
                "text": m["text"],
                "msg_type": m["msg_type"],
                "dir": "out" if m["role"] == "me" else "in",
                "type": _front_type(m["msg_type"] or "text"),
                "time": _fmt_ts(m["ts"]),  # 2026-09-05:改为完整时间,前端做日期分割线
                "msg_id": msg_id,
                "image_url": image_url,  # 前端 <img src> 直接用,None 则降级到缩略图
                # 2026-09-05 新增：消息来源通道。
                # wp_recv 落库时写 extra.source="wp"；WS 通道无该字段 → 兜底 "ws"。
                "source": ex.get("source") or "ws",
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
    logger.warning("SEND-001", 
        f"[send][{body.account}] {first.upper()} 通道失败（{errors.get(first)}），"
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
