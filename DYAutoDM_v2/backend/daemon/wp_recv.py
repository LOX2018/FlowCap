# coding=utf-8
"""WP 通道私信接收循环（抖音网页版 douyin.com/chat）。

2026-09-05 新增。与 WS 通道（recv_daemon / frontier-im 长连接）并存。

链路：
    chat 页前端自发请求 / WS 推送
        ↓ CAP_WP_MESSAGE_HOOK_JS 被动 hook（browser_daemon 注入）
    window.__CAP_WP_MESSAGE__.events
        ↓ 本模块轮询 BCC /wp_messages 取回（读后清空）
    解析（JSON / protobuf 兜底）
        ↓ 落库 dm_messages（extra.source='wp'）
    前端私信中心展示

去重（关键）：
    WS 与 WP 是两条独立通道，同一条真实消息会被两边各收到一次。
    dm_messages 表自身无 UNIQUE 约束（只有普通 INDEX），
    故这里用 (account, conv_id, ts, text) 组合做**应用层去重**：
    落库前先查同 (account, conv_id) 且 |ts 差| < 2s 且 text 相同的记录，
    已存在则跳过。这样 WS 先落、WP 后到会被拦掉，反之亦然。

    为什么不建 UNIQUE 索引：
      - 现有表无 client_msg_id 列，该值藏在 extra JSON 里，建列需迁移；
      - 历史数据已存在重复，直接建 UNIQUE 会失败。
    应用层去重可立即生效且无需迁移，代价是「同一秒内发两条完全相同的文本」
    会被误合并 —— 私信场景可接受。

风控边界（昵称红线 08 §13）：
    本模块只解析「已被动截获」的事件，绝不主动发起任何请求，
    绝不遍历/批量查询用户信息。昵称沿用消息自带的 sender_nickname。

运行方式（由 main.py 以 asyncio task 拉起）：
    await run_wp_recv_loop(account_name)
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from loguru import logger

# 轮询间隔（秒）。实时性 vs 浏览器压力权衡：
# 太快(<2s)会频繁 evaluate 打断 chat 页；太慢(>10s)私信延迟明显。
POLL_INTERVAL = 3.0

# 去重时间窗（秒）：WS 与 WP 收到同一消息的时间差通常 < 1s，
# 放宽到 2s 容忍时钟/处理延迟。
DEDUP_WINDOW_SEC = 2.0

# 单条消息文本落库上限（防止异常大帧撑爆 DB）
MAX_TEXT_LEN = 4000


def _bcc_port(account: str) -> int:
    """该账号 BCC(browser_daemon) 的 HTTP 端口。

    注意：不是 recv_daemon_port + N，而是有独立稳定分配函数
    （auto_dm/accounts.py: browser_daemon_port，基于账号名哈希）。
    """
    from auto_dm import accounts as acc

    return acc.browser_daemon_port(account)


def _db():
    from database import get_db

    return get_db()


def _already_exists(conn, account: str, conv_id: str, ts: float, text: str) -> bool:
    """(account, conv_id, ts±2s, text) 组合去重判定。"""
    try:
        row = conn.execute(
            "SELECT 1 FROM dm_messages WHERE account=? AND conv_id=? "
            "AND text=? AND ABS(ts - ?) < ? LIMIT 1",
            (account, conv_id, text, ts, DEDUP_WINDOW_SEC),
        ).fetchone()
        return row is not None
    except Exception:
        return False


def _ensure_conv(conn, account: str, conv_id: str, peer_id: Any,
                 peer_name: str | None) -> None:
    """确保会话骨架存在（INSERT OR IGNORE，不覆盖已有昵称）。"""
    try:
        conn.execute(
            "INSERT OR IGNORE INTO dm_conversations(account,conv_id,peer_id,"
            "peer_name,short_id,last_ts,unread) VALUES(?,?,?,?,?,?,?)",
            (account, conv_id, peer_id, peer_name, None, 0, 0),
        )
    except Exception:
        pass


def parse_http_init(body: str, my_uid: str) -> list[dict]:
    """解析 get_message_by_init 的 HTTP 响应（全量会话 + 最近消息）。

    响应可能是 JSON（网页端常见）或 protobuf 二进制。
    - JSON:  {"data": {...}} 结构
    - protobuf: 复用 auto_dm.conversation_capture.parse_init_protobuf

    返回 [{conv_id, peer_uid, role, text, msg_type, ts, client_msg_id, sender_nickname}, ...]
    """
    out: list[dict] = []
    if not body:
        return out

    # 1) 先试 JSON
    try:
        obj = json.loads(body)
        data = obj.get("data") or obj
        # 会话列表：data.conversation_list / data.conversations
        convs = data.get("conversation_list") or data.get("conversations") or []
        for c in convs:
            conv_id = str(c.get("conversation_id") or c.get("conversation_short_id") or "")
            if not conv_id:
                continue
            peer_uid = str(c.get("peer_uid") or c.get("to_user_id") or "")
            for m in (c.get("messages") or c.get("message_list") or []):
                out.append(_norm_msg(m, conv_id, peer_uid, my_uid))
        if out:
            return out
    except Exception:
        pass

    # 2) 兜底：protobuf（网页端若返回二进制）
    try:
        from auto_dm.conversation_capture import parse_init_protobuf

        convs = parse_init_protobuf(body, my_uid)
        for c in convs:
            conv_id = c.get("conversation_id") or ""
            peer_uid = str(c.get("peer_uid") or "")
            for m in (c.get("messages") or []):
                out.append(_norm_msg(m, conv_id, peer_uid, my_uid))
    except Exception as e:
        logger.debug(f"[wp_recv] protobuf 兜底解析失败: {e}")
    return out


def _norm_msg(m: dict, conv_id: str, peer_uid: str, my_uid: str) -> dict:
    """把不同来源的消息对象归一成统一结构。

    :param my_uid: 本机账号 UID（用于方向判定）
    """
    # 08 §13.5 铁律：方向只能用 sender UID 判断，不可用消息类型推断。
    # sender == 自己 UID → 我发(me)；否则对方发(them)。
    #
    # 2026-09-06 全局治理（跨通道方向判定统一）：
    # 原写法 `sender and my_uid and sender == my_uid` 在 sender 为空时会
    # 落到 else → them，与 conversation_capture:602 的「空 sender 归 me」
    # （自动欢迎语/自动回复本系统产生）判定相反 —— 同一条消息走 WS 和走
    # WP 会得到不同 role，前端展示方向错乱。
    # 统一为：sender 为空 → me（与 capture 一致）；有 sender → 按 UID 比对。
    sender = str(m.get("sender") or m.get("sender_id") or m.get("from_user_id") or "")
    if not sender:
        role = "me"
    elif my_uid and str(sender) == str(my_uid):
        role = "me"
    else:
        role = "them"
    # 时间戳：兼容秒 / 毫秒
    raw_ts = m.get("create_time") or m.get("ts") or m.get("created_at") or 0
    try:
        raw_ts = float(raw_ts)
    except Exception:
        raw_ts = 0.0
    ts = raw_ts / 1000.0 if raw_ts > 1e11 else (raw_ts or time.time())
    text = m.get("text") or m.get("content") or ""
    if isinstance(text, dict):
        text = text.get("text") or json.dumps(text, ensure_ascii=False)
    return {
        "conv_id": conv_id,
        "peer_uid": peer_uid or sender,
        "role": role,
        "text": (text or "")[:MAX_TEXT_LEN],
        "msg_type": str(m.get("msg_type") or m.get("type") or "text"),
        "ts": ts,
        # 2026-09-06 全局治理（跨通道 ID 空间归一化）：
        # WS 通道落库用【服务端 msg_id】，WP 通道若只用 client_msg_id
        # 则两者不在同一 ID 空间，uniq_dmmsg 无法跨通道去重。
        # 这里优先取服务端 msg_id（页面数据通常两者都带），client_msg_id
        # 仅作兜底并在 extra 里保留，供发送侧溯源。
        "msg_id": str(m.get("msg_id") or m.get("server_msg_id")
                      or m.get("client_msg_id") or ""),
        "client_msg_id": str(m.get("client_msg_id") or m.get("msg_id") or ""),
        "sender_nickname": m.get("sender_nickname") or "",
    }


def parse_ws_frame(body: str, my_uid: str) -> list[dict]:
    """解析 WS 推送帧（实时新私信）。

    网页端 WS 帧多是 JSON 包装（内含 protobuf base64 或直接字段）。
    策略：先按 JSON 解析，取不到就原样丢弃（记 debug 日志供后续排查），
    绝不猜测 protobuf schema。
    """
    out: list[dict] = []
    # 2026-09-06 全局治理：页面 hook 现已上抛二进制帧（B64: 前缀 base64）。
    # 抖音 IM 二进制帧是 protobuf 且 schema 未逆向，当前仍不解析（绝不猜测），
    # 但与 '<binary>' 占位不同：B64 帧至少保留了完整原始数据，可离线排查。
    if not body:
        return out
    if body == "<binary>":
        return out
    if body.startswith("B64:"):
        logger.debug(f"[wp_recv] WS 二进制帧（B64，{len(body)-4} B），protobuf 解析未启用，跳过")
        return out
    try:
        obj = json.loads(body)
    except Exception:
        # 非 JSON（可能是纯 protobuf 二进制字符串）—— 无法安全解析，跳过
        logger.debug(f"[wp_recv] WS 帧非 JSON，跳过（{len(body)} 字节）")
        return out

    # 常见结构：{messages: [...]} / {data: {messages: [...]}}
    msgs = obj.get("messages") or (obj.get("data") or {}).get("messages") or []
    if isinstance(msgs, dict):
        msgs = [msgs]
    for m in msgs:
        if not isinstance(m, dict):
            continue
        conv_id = str(m.get("conversation_id") or m.get("conversation_short_id") or "")
        if not conv_id:
            continue
        out.append(_norm_msg(m, conv_id, m.get("peer_uid") or "", my_uid))
    return out


def _my_uid_of(account: str) -> str:
    """读账号自己的 UID（用于方向判定）。失败返回空串。"""
    try:
        from auto_dm import accounts as acc
        from dy_apis.login_api import DYLoginApi

        env_path = acc.env_path_of(account)
        if not env_path:
            return ""
        auth = DYLoginApi._load_auth_from_env(env_path)
        return str(auth.get_uid() or "")
    except Exception as e:
        logger.debug(f"[wp_recv] 读 my_uid 失败: {e}")
        return ""


def process_events(account: str, events: list[dict]) -> int:
    """解析并落库一批 hook 事件，返回新增消息条数。"""
    if not events:
        return 0
    my_uid = _my_uid_of(account)
    n_new = 0
    try:
        conn = _db()
    except Exception as e:
        logger.warning(f"[wp_recv][{account}] 数据库连接失败: {e}")
        return 0

    for ev in events:
        kind = ev.get("kind")
        body = ev.get("body") or ""
        try:
            if kind == "http":
                msgs = parse_http_init(body, my_uid)
            elif kind == "ws":
                msgs = parse_ws_frame(body, my_uid)
            else:
                continue
        except Exception as e:
            logger.debug(f"[wp_recv][{account}] 解析事件失败 kind={kind}: {e}")
            continue

        for m in msgs:
            if not m["text"]:
                continue
            try:
                if _already_exists(conn, account, m["conv_id"], m["ts"], m["text"]):
                    continue  # WP/WS 重复，跳过
                _ensure_conv(conn, account, m["conv_id"], m["peer_uid"],
                             m.get("sender_nickname") or m["peer_uid"])
                extra = {
                    "source": "wp",
                    "client_msg_id": m["client_msg_id"],
                    "sender_nickname": m.get("sender_nickname") or "",
                }
                # 2026-09-06 全局并发治理（双通道重复落库核心修复）：
                # WP 通道此前只把 client_msg_id 塞进 extra JSON，不写 msg_id 列
                # —— uniq_dmmsg(account,conv_id,msg_id) 唯一索引完全管不到它，
                # 只能靠 fallback 索引（role+text+毫秒 ts）兜底；而 WS 回声与
                # WP 轮询写入同一条消息时 ts 有毫秒级差异，fallback 也失效，
                # 导致同一条消息重复入库（前端看到两条一样的）。
                # 修正：client_msg_id 写入 msg_id 列，让 WS/WP 双通道同一条
                # 消息命中同一个唯一索引 → 真正去重。
                conn.execute(
                    "INSERT OR IGNORE INTO dm_messages("
                    "account,conv_id,role,text,msg_type,extra,ts,msg_id)"
                    " VALUES(?,?,?,?,?,?,?,?)",
                    (account, m["conv_id"], m["role"], m["text"],
                     m["msg_type"], json.dumps(extra, ensure_ascii=False), m["ts"],
                     m.get("msg_id") or m["client_msg_id"] or None),
                )
                conn.execute(
                    "UPDATE dm_conversations SET last_ts=?,unread=unread+? "
                    "WHERE account=? AND conv_id=?",
                    (m["ts"], 1 if m["role"] == "them" else 0, account, m["conv_id"]),
                )
                n_new += 1
            except Exception as e:
                logger.debug(f"[wp_recv][{account}] 落库失败: {e}")
    try:
        conn.commit()
    except Exception:
        pass
    if n_new:
        logger.info(f"[wp_recv][{account}] WP 通道新增 {n_new} 条消息")
    return n_new


async def poll_once(account: str) -> int:
    """拉一次 BCC /wp_messages 并处理，返回新增条数。"""
    import httpx

    port = _bcc_port(account)
    try:
        async with httpx.AsyncClient(timeout=10) as cli:
            r = await cli.post(f"http://127.0.0.1:{port}/wp_messages", json={})
        if r.status_code != 200:
            return 0
        payload = r.json()
        if not payload.get("ok"):
            return 0
        events = payload.get("events") or []
    except Exception as e:
        # BCC 未启动/重启中：静默（debug 级），避免刷屏
        logger.debug(f"[wp_recv][{account}] 拉取失败 (port={port}): {e}")
        return 0
    return process_events(account, events)


async def run_wp_recv_loop(account: str, interval: float = POLL_INTERVAL) -> None:
    """长跑轮询循环，异常自动重试（不退出）。

    2026-09-06 适配 BCC 懒加载（用户架构决策：启动不拉 BCC）：
    BCC 未运行时轮询空转（debug 级静默，poll_once 已兜底），
    不主动拉起 BCC —— 用户点「更新会话」/WP 发送时 ensure_bcc 才拉起，
    之后本循环自动恢复取数。
    """
    logger.info(f"[wp_recv][{account}] 启动 WP 通道轮询（BCC port={_bcc_port(account)}，"
                f"BCC 未运行时空转等待，懒加载后自动恢复）")
    while True:
        try:
            await poll_once(account)
        except asyncio.CancelledError:
            logger.info(f"[wp_recv][{account}] 轮询已停止")
            raise
        except Exception as e:
            logger.warning(f"[wp_recv][{account}] 轮询异常: {e}")
        await asyncio.sleep(interval)