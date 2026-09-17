# -*- coding: utf-8 -*-
"""MCP 工具集 —— 把既有业务能力暴露给 AI 客户端。

## 本分支规则（design/better-douyin，用户 2026-09-14 授权「全解除」）

见 `docs/BRANCH_RULES_design_better_douyin.md`。**主分支铁律在本分支不生效**：

- 允许注册**主动查询平台**的工具（含用户资料/昵称）—— 照源项目的做法；
- 允许读取**远端内容**（推荐流/搜索/作品/合集等），不限于本地 SQLite。

保留的底线（与风控无关）：**凭证/密钥绝不出现在工具输出或审计里**。

## 分级

- **READ**：读库或读平台（无副作用）。默认可用。
- **WRITE**：向平台发起写操作（发送/点赞/收藏/关注）。需 `allow_write_actions=true`
  **且**一次性确认票据（`require_confirmation`）。
"""
from __future__ import annotations

from typing import Any

from .registry import READ, WRITE, Tool, register


# ---------------- 只读工具 ----------------

def _list_accounts() -> list[dict[str, Any]]:
    """列出已登记账号（仅名称与是否已配置，不返回任何凭据）。"""
    try:
        from auto_dm.accounts import list_accounts
        return [{"name": n} for n in list_accounts()]
    except Exception:
        return []


def _list_conversations(account: str = "", limit: int = 50) -> list[dict[str, Any]]:
    """读本地会话列表（纯 SQLite，不触发任何网络请求）。

    排序口径对齐既有实现：有消息的优先，其次按时间。**不返回凭据**。
    """
    from database import exec_query

    limit = max(1, min(int(limit or 50), 500))
    if account:
        rows = exec_query(
            """SELECT c.account, c.conv_id, c.peer_id, c.peer_name, c.unread,
                      c.last_ts,
                      (SELECT COUNT(1) FROM dm_messages m
                        WHERE m.account=c.account AND m.conv_id=c.conv_id
                          AND m.msg_type <> '50001') AS n
                 FROM dm_conversations c
                WHERE c.account=?
                ORDER BY COALESCE(n,0) DESC, c.last_ts DESC, c.conv_id ASC
                LIMIT ?""", (account, limit))
    else:
        rows = exec_query(
            """SELECT c.account, c.conv_id, c.peer_id, c.peer_name, c.unread,
                      c.last_ts,
                      (SELECT COUNT(1) FROM dm_messages m
                        WHERE m.account=c.account AND m.conv_id=c.conv_id
                          AND m.msg_type <> '50001') AS n
                 FROM dm_conversations c
                ORDER BY COALESCE(n,0) DESC, c.last_ts DESC, c.conv_id ASC
                LIMIT ?""", (limit,))
    return [{
        "account": r.get("account"),
        "conv_id": r.get("conv_id"),
        "peer_name": r.get("peer_name") or "",
        "unread": int(r.get("unread") or 0),
        "message_count": int(r.get("n") or 0),
        "last_ts": float(r.get("last_ts") or 0),
    } for r in rows]


def _read_messages(account: str, conv_id: str, limit: int = 30) -> list[dict[str, Any]]:
    """读某会话最近消息（纯本地库）。

    ⚠️ 过滤 `msg_type=50001`（「对方已读」回执无 msg_id，属脏数据，
    见 `dyautodm-dev-guards` §九·乙）。
    """
    from database import exec_query

    limit = max(1, min(int(limit or 30), 200))
    rows = exec_query(
        """SELECT role, text, msg_type, ts FROM dm_messages
            WHERE account=? AND conv_id=? AND msg_type <> '50001'
            ORDER BY ts DESC LIMIT ?""", (account, conv_id, limit))
    rows.reverse()
    return [{
        "role": r.get("role"),
        "text": r.get("text") or "",
        "msg_type": r.get("msg_type") or "text",
        "ts": float(r.get("ts") or 0),
    } for r in rows]


def _overview() -> dict[str, Any]:
    """汇总统计（纯本地库）。"""
    from database import exec_query
    out: dict[str, Any] = {}
    try:
        out["conversations"] = int(
            (exec_query("SELECT COUNT(1) AS n FROM dm_conversations")[0] or {}).get("n") or 0)
        out["messages"] = int(
            (exec_query("SELECT COUNT(1) AS n FROM dm_messages")[0] or {}).get("n") or 0)
        out["accounts"] = len(_list_accounts())
    except Exception as e:
        out["error"] = type(e).__name__
    return out


def _errcode(code: str) -> dict[str, Any]:
    """查统一报错体系里某个错误码的设计契约（只读）。"""
    try:
        from errcode import lookup
        return lookup(code) or {"code": code, "found": False}
    except Exception as e:
        return {"code": code, "found": False, "error": type(e).__name__}


# ---------------- 写入工具（默认不可达） ----------------

def _send_dm(account: str, conv_id: str, text: str) -> dict[str, Any]:
    """向指定会话发送私信。

    ⚠️ 必须满足全部条件才允许：
      1) 配置 `allow_write_actions=true`
      2) 配置 `require_confirmation=true` 时持一次性确认票据
      3) **既有发送闸门仍生效** —— 这里不绕过 `recv_daemon` 的限速闸门，
         仅调用既有服务层，避免出现第二条发送旁路（发送是最高频风控面）。
    """
    if not account or not conv_id or not text:
        return {"ok": False, "message": "account / conv_id / text 均为必填"}
    # 走既有发送服务，绝不自行直发（禁止新增 imapi 直发调用点）
    try:
        from services.browser_gate import ensure_browser  # noqa: F401
    except Exception:
        pass
    return {
        "ok": False,
        "message": "写工具已登记但未接线：发送必须经既有发布闸门，需单独评审后启用",
        "note": "本占位确保注册表/票据链路可测，不产生真实发送行为",
    }


def _refresh_write_confirm(account: str = "") -> dict[str, Any]:
    """内部用：为写操作签发确认票据（由管理面调用，不直接暴露给外部客户端）。"""
    from .registry import issue_ticket
    return issue_ticket("send_dm", {"account": account})


# ---------------- 只读工具（★ 本分支新增：平台读） ----------------

def _platform_call(account: str, method: str, **kwargs) -> Any:
    """通用平台读调用（本分支全解除后可注册主动查询工具）。"""
    from auto_dm import accounts as acct_core
    from dy_apis.login_api import DYLoginApi
    from dy_apis.douyin_api import DouyinAPI
    env_path = acct_core.env_path_of(account)
    if not env_path:
        raise RuntimeError(f"账号 {account} 未登记")
    auth = DYLoginApi._load_auth_from_env(env_path)
    if not auth or not getattr(auth, "cookie", None):
        raise RuntimeError(f"账号 {account} 凭证为空")
    fn = getattr(DouyinAPI, method)
    return fn(auth, **kwargs)


def _search_user(account: str, keyword: str, count: int = 20) -> Any:
    """搜索用户（照源项目 A2：按昵称/抖音号/UID 搜索）。

    2026-09-17 修补（OCR 审查 CRITICAL）：底层签名为
    `search_some_user(auth, query, num, **kwargs)`。原调用传 `keyword=/count=`
    会落进 `**kwargs`，而必填的 `query`/`num` 缺失 → 必然
    `TypeError: missing required positional argument`。改为参数名对齐。
    """
    return _platform_call(account, "search_some_user",
                          query=keyword, num=str(count))


def _user_works(account: str, user_url: str) -> Any:
    """用户作品列表（照源项目 A4）。"""
    return _platform_call(account, "get_user_all_work_info", user_url=user_url)


def _user_info(account: str, user_url: str) -> Any:
    """用户资料（照源项目 A2）。"""
    return _platform_call(account, "get_user_info", user_url=user_url)


def _user_info_batch(account: str, sec_uids: list[str]) -> Any:
    """★ 主动批量查用户资料（照源项目；本分支全解除后允许）。

    走实测有效的 `POST im/user/info` + `sec_user_ids` JSON数组串。
    """
    from api.platform import _im_user_info_by_sec, _auth_for
    return _im_user_info_by_sec(_auth_for(account), sec_uids)


def _recommend_feed(account: str, count: int = 20, refresh_index: str = "2") -> Any:
    """推荐流（照源项目 A5；refresh_index 切精选/推荐）。"""
    return _platform_call(account, "get_feed", count=str(count), refresh_index=refresh_index)


def _search_work(account: str, keyword: str, count: int = 20) -> Any:
    """搜索作品（照源项目 A2）。

    2026-09-17 修补（OCR 审查 CRITICAL）：底层签名为
    `search_some_general_work(auth, query, num, sort_type, publish_time, ...)`。
    原调用传 `keyword=/count=` → 必填 `query`/`num` 缺失而抛 TypeError。
    """
    return _platform_call(account, "search_some_general_work",
                          query=keyword, num=str(count))


def _collected_list(account: str) -> Any:
    """收藏夹列表（照源项目 A4）。"""
    return _platform_call(account, "get_collect_list")


def _collection_items(account: str, count: int = 18) -> Any:
    """收藏夹内的作品（★ 本分支新增，实测 sc=0）。"""
    return _platform_call(account, "get_aweme_list_collection", num=str(count))


def _collection_mixes(account: str, count: int = 20) -> Any:
    """收藏的合集列表（★ 本分支新增，实测 sc=0）。"""
    return _platform_call(account, "get_mix_list_collection", count=str(count))


def _user_favorite(account: str, sec_id: str, count: int = 18) -> Any:
    """某用户的收藏（基座既有方法）。"""
    return _platform_call(account, "get_user_favorite", sec_id=sec_id, num=str(count))


def _work_comments(account: str, aweme_id: str, count: int = 30) -> Any:
    """作品评论列表（照源项目 A/评论面）。

    2026-09-17 修补（OCR 审查 CRITICAL）：底层签名为
    `get_work_all_comment(auth, url, **kwargs)` —— 它接受**作品 URL**，
    既没有 aweme_id 参数也没有 num。原调用把两者塞进 kwargs，导致必填的
    `url` 缺失 → TypeError。此处把 aweme_id 归一化为作品页 URL。
    """
    url = aweme_id if str(aweme_id).startswith("http") \
        else f"https://www.douyin.com/video/{aweme_id}"
    return _platform_call(account, "get_work_all_comment", url=url,
                          count=str(count))


def _notice_list(account: str, count: int = 20) -> Any:
    """站内通知列表（照源项目 C3）。"""
    return _platform_call(account, "get_notice_list", count=str(count))


def _follower_list(account: str, sec_id: str, count: int = 20) -> Any:
    """粉丝列表（照源项目 A2）。

    2026-09-17 修补（OCR 审查 CRITICAL）：底层签名为
    `get_user_follower_list(auth, user_id, sec_id, ...)` —— `user_id` 是
    **必填位置参数**，仅给 sec_id 会抛 TypeError。此处 user_id 未知时
    传空串（接口按 sec_id 定位，实测可返回），并保留 sec_id。
    """
    return _platform_call(account, "get_user_follower_list",
                          user_id="", sec_id=sec_id, count=str(count))


def _following_list(account: str, sec_id: str, count: int = 20) -> Any:
    """关注列表（照源项目 A2）。

    2026-09-17 修补（OCR 审查 CRITICAL）：同 `_follower_list`，
    `get_user_following_list(auth, user_id, sec_id, ...)` 的 user_id 必填。
    """
    return _platform_call(account, "get_user_following_list",
                          user_id="", sec_id=sec_id, count=str(count))


def _media_stats() -> dict:
    """媒体代理缓存统计（★ 本分支新增，对应源项目 media_proxy_cache）。"""
    from services import media_proxy
    return media_proxy.stats()


def _automation_config() -> dict:
    """读自动化频率配置（★ 本分支新增，照源项目 auto_* 模型）。"""
    from services import app_config
    sec = app_config.SECTIONS.get("automation") or {}
    return {k: app_config.get("automation", k) for k in (sec.get("fields") or {})}


# ---------------- 注册 ----------------

def register_all() -> int:
    """注册全部工具，返回注册数量（幂等）。"""
    tools = [
        Tool(name="list_accounts",
             level=READ,
             summary="列出已登记账号（仅名称，不含任何凭据）",
             handler=_list_accounts),
        Tool(name="list_conversations",
             level=READ,
             summary="读本地会话列表（纯 SQLite，零网络请求）",
             handler=_list_conversations,
             params={"account": "账号名，留空=全部", "limit": "返回条数，默认50"},
             audit_fields=("account",)),
        Tool(name="read_messages",
             level=READ,
             summary="读某会话最近消息（纯本地库，自动过滤回执脏数据）",
             handler=_read_messages,
             params={"account": "账号名", "conv_id": "会话 id", "limit": "条数，默认30"},
             audit_fields=("account", "conv_id")),
        Tool(name="overview",
             level=READ,
             summary="本地数据汇总统计",
             handler=_overview),
        Tool(name="lookup_errcode",
             level=READ,
             summary="查错误码的设计契约（design/contract/chain/root/verify）",
             handler=_errcode,
             params={"code": "错误码，如 BCC-046"},
             audit_fields=("code",)),
        Tool(name="send_dm",
             level=WRITE,
             summary="向指定会话发送私信（默认关闭；需显式开启+确认票据）",
             handler=_send_dm,
             params={"account": "账号名", "conv_id": "会话 id", "text": "消息正文"},
             audit_fields=("account", "conv_id", "text")),

        # ===== ★ 本分支新增（照源项目功能面，见 docs/logic_replication_plan.md）=====
        Tool(name="search_user", level=READ,
             summary="搜索用户（昵称/抖音号/UID）",
             handler=_search_user,
             params={"account": "账号名", "keyword": "关键词", "count": "条数"},
             audit_fields=("account", "keyword")),
        Tool(name="search_work", level=READ,
             summary="搜索作品",
             handler=_search_work,
             params={"account": "账号名", "keyword": "关键词", "count": "条数"},
             audit_fields=("account", "keyword")),
        Tool(name="user_info", level=READ,
             summary="用户资料（按主页 URL）",
             handler=_user_info,
             params={"account": "账号名", "user_url": "用户主页 URL"},
             audit_fields=("account",)),
        Tool(name="user_info_batch", level=READ,
             summary="批量查用户资料（按 sec_uid；照源项目做法）",
             handler=_user_info_batch,
             params={"account": "账号名", "sec_uids": "sec_uid 数组"},
             audit_fields=("account",)),
        Tool(name="user_works", level=READ,
             summary="用户作品列表",
             handler=_user_works,
             params={"account": "账号名", "user_url": "用户主页 URL"},
             audit_fields=("account",)),
        Tool(name="recommend_feed", level=READ,
             summary="推荐流（refresh_index 切精选/推荐）",
             handler=_recommend_feed,
             params={"account": "账号名", "count": "条数", "refresh_index": "2=推荐 1=精选"},
             audit_fields=("account",)),
        Tool(name="collected_list", level=READ,
             summary="收藏夹列表",
             handler=_collected_list,
             params={"account": "账号名"}, audit_fields=("account",)),
        Tool(name="collection_items", level=READ,
             summary="收藏夹内的作品",
             handler=_collection_items,
             params={"account": "账号名", "count": "条数"}, audit_fields=("account",)),
        Tool(name="collection_mixes", level=READ,
             summary="收藏的合集列表",
             handler=_collection_mixes,
             params={"account": "账号名", "count": "条数"}, audit_fields=("account",)),
        Tool(name="user_favorite", level=READ,
             summary="某用户的收藏（作品维度）",
             handler=_user_favorite,
             params={"account": "账号名", "sec_id": "用户 sec_uid", "count": "条数"},
             audit_fields=("account",)),
        Tool(name="work_comments", level=READ,
             summary="作品评论列表",
             handler=_work_comments,
             params={"account": "账号名", "aweme_id": "作品 id", "count": "条数"},
             audit_fields=("account", "aweme_id")),
        Tool(name="notice_list", level=READ,
             summary="站内通知列表（点赞/评论/关注）",
             handler=_notice_list,
             params={"account": "账号名", "count": "条数"}, audit_fields=("account",)),
        Tool(name="follower_list", level=READ,
             summary="粉丝列表",
             handler=_follower_list,
             params={"account": "账号名", "sec_id": "用户 sec_uid", "count": "条数"},
             audit_fields=("account",)),
        Tool(name="following_list", level=READ,
             summary="关注列表",
             handler=_following_list,
             params={"account": "账号名", "sec_id": "用户 sec_uid", "count": "条数"},
             audit_fields=("account",)),
        Tool(name="media_stats", level=READ,
             summary="媒体代理缓存统计（命中率/磁盘占用）",
             handler=_media_stats),
        Tool(name="automation_config", level=READ,
             summary="读自动化频率配置（单轮上限/间隔/门槛/关键词）",
             handler=_automation_config),
    ]
    for t in tools:
        register(t)
    return len(tools)
