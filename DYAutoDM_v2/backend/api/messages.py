"""私信会话路由

取代原版 WebBridge.getConversations / getConversation / sendDm。
转发到 recv_daemon 的 HTTP 接口（每账号专属端口）。
"""
from __future__ import annotations

import time
import urllib.parse
import urllib.request
import json
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from loguru import logger

from auto_dm import accounts as acct_core
from database import get_db

router = APIRouter()

# 会话摘要日志去重：仅当（总数, 关联昵称数, 未读合计）变化时打印，避免前端
# 每 5s 轮询导致「私信拉取」日志刷屏。键为 account。
_last_summary: dict[str, tuple] = {}


class SendDmRequest(BaseModel):
    account: str
    conv_id: str
    text: str


class RequestDmBody(BaseModel):
    name: str
    comment: str = ""


def _recv_url(account: str, path: str) -> str | None:
    """构造该账号 recv_daemon 的 HTTP URL（端口稳定哈希，与守护进程一致）。"""
    port = acct_core.recv_daemon_port(account)
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
            return time.strftime("%H:%M:%S", time.localtime(float(ts)))
    except Exception:
        pass
    return ""


def _map_message(m: dict) -> dict:
    """把 recv_daemon 的 message 字段映射成前端期望结构。

    recv_daemon to_dict(): role/the/me、text、msg_type、ts
    前端 messages.tsx: dir(in/out)、type、text、time
    """
    role = (m.get("role") or "them")
    direction = "in" if role == "them" else "out"
    return {
        "dir": direction,
        "type": m.get("msg_type") or m.get("type") or "text",
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
            "SELECT conv_id,peer_id,peer_name,short_id,last_ts,unread,avatar "
            "FROM dm_conversations WHERE account=? ORDER BY last_ts DESC",
            (account,),
        ).fetchall()
        if not rows:
            # 库里还没有会话：若守护未启动则提示前端拉起，否则返回空
            port = acct_core.recv_daemon_port(account)
            if not acct_core._port_open(port, timeout=0.3):
                logger.warning(f"[私信拉取] 账号「{account}」库空且守护未运行(port={port})")
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
        logger.warning(f"[私信拉取] 账号「{account}」读库异常: {e}")
        return {"ok": False, "conversations": [], "error": str(e)}


@router.get("/conversation")
async def get_conversation(account: str, conv_id: str):
    """会话详情（纯读库 + 标记已读）"""
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
        msgs = conn.execute(
            "SELECT role,text,msg_type,ts FROM dm_messages "
            "WHERE account=? AND conv_id=? ORDER BY ts ASC",
            (account, str(conv_id)),
        ).fetchall()
        conv = {
            "conv_id": row["conv_id"],
            "name": row["peer_name"] or row["peer_id"] or row["conv_id"] or "会话",
            "peer_id": row["peer_id"],
            "peer_name": row["peer_name"],
            "unread": row["unread"] or 0,
            "avatar": row["avatar"] or "",
            "messages": [{"role": m["role"], "text": m["text"], "msg_type": m["msg_type"]} for m in msgs],
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
        logger.warning(f"[私信拉取] 账号「{account}」会话详情异常: {e}")
        return {"ok": False, "conversation": {}}


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
    """手动发送私信（转发到 recv_daemon /send）"""
    try:
        url = _recv_url(body.account, "/send")
        if url is None:
            return {"ok": False, "error": "端口分配失败"}
        d = _http_post_json(url, {
            "account": body.account,
            "conv_id": body.conv_id,
            "text": body.text,
        })
        return d
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"ok": False, "error": "账号私信守护未运行"}
        return {"ok": False, "error": f"私信守护返回 {e.code}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


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
        # 捕获依赖 BCC（browser_daemon）常驻浏览器；先幂等拉起（端口已开则跳过）
        launched = ensure_daemons_for(account)
        if use_browser and not launched.get("browser"):
            logger.warning(f"[refresh][{account}] browser_daemon 未拉起，昵称关联可能失效")

        n_conv, n_msg = capture_all(account, with_browser=use_browser)
        elapsed = round(_time.time() - t0, 1)
        logger.info(f"[refresh][{account}] 更新会话完成：会话 {n_conv}（消息 {n_msg}），耗时 {elapsed}s")
        return {
            "ok": True,
            "n_conv": n_conv,
            "n_msg": n_msg,
            "elapsed": elapsed,
        }
    except Exception as e:
        logger.warning(f"[refresh][{account}] 更新会话失败: {e}")
        return {
            "ok": False,
            "error": str(e),
            "elapsed": round(_time.time() - t0, 1),
        }
