# coding=utf-8
"""统一凭证决策层 —— 所有路由通过本模块获取凭证或匿名。

## 设计原则

**统一调度**：不在每个路由里各自判断"要不要凭证"，而是由本模块集中管理。
**按步骤拆分**：同一条业务链路，能匿名的步骤用匿名，必须凭证的步骤才用凭证。
**降低风控暴露**：只读公开数据走匿名，写操作和登录门禁接口才用凭证。

## 接口分类

| 分类 | 凭证需求 | 示例 |
|---|---|---|
| **只读公开数据** | 可匿名 | 推荐流、搜索、探活、热度、贡献榜 |
| **写操作** | 必须凭证 | 发私信、点赞、关注、评论、弹幕 |
| **本人数据** | 必须凭证 | 收藏、通知、私信历史 |
| **评论列表** | 必须凭证 | 实测匿名返回 0 字节（登录门禁） |

## 使用方式

```python
from services.auth_policy import get_auth_for

# 获取凭证（如果需要）或 None（匿名）
auth = get_auth_for("/api/platform/feed", account)
if auth is None:
    # 匿名请求
    result = DouyinAPI.get_feed_anon(count="20")
else:
    # 带凭证请求
    result = DouyinAPI.get_feed(auth, count="20")
```
"""
from __future__ import annotations

from loguru import logger
from typing import Optional, Any

# ============================================================================
# 接口凭证策略表（SSOT）
# ============================================================================

# 只读公开数据接口（可匿名）
ANON_ENDPOINTS = {
    # 推荐流
    "/api/platform/feed",
    "/aweme/v1/web/tab/feed/",
    # 搜索
    "/api/platform/search",
    "/api/crawl/search",
    "/aweme/v1/web/general/search/single/",
    "/aweme/v1/web/search/user/",
    "/aweme/v1/web/search/live/",
    # 直播探活
    "/api/live/resolve",
    "/aweme/v1/web/room/info/",
    # 媒体取址
    "/api/platform/media/resolve",
    # 评论预览（已有匿名端点）
    "/api/crawl/comments/anon-preview",
}

# 必须凭证的接口（写操作或登录门禁）
CREDENTIAL_ENDPOINTS = {
    # 评论列表（登录门禁）
    "/api/platform/comments",
    "/api/platform/comments/full",
    "/api/crawl/comments",
    "/api/crawl/comments/batch",
    "/aweme/v1/web/comment/list/",
    # 私信发送
    "/api/messages/send",
    "/api/messages/send_image",
    "/api/messages/wp_send",
    "/api/messages/request",
    "/api/crawl/dm",
    "/api/crawl/batch",
    "/api/platform/comments/dm",
    "/aweme/v1/web/im/send/msg/",
    # 写操作
    "/api/platform/action/digg",
    "/api/platform/action/collect",
    "/api/live/danmaku",
    "/api/live/like",
    "/aweme/v1/web/commit/item/digg/",
    "/aweme/v1/web/commit/follow/user/",
    "/aweme/v1/web/aweme/collect/",
    "/aweme/v1/web/comment/digg/",
    "/aweme/v1/web/comment/publish/",
    "/aweme/v1/web/room/chat/",
    "/aweme/v1/web/room/like/",
    # 本人数据
    "/api/platform/liked",
    "/api/platform/favorite",
    "/api/platform/notifications",
    "/api/platform/collected",
    "/api/platform/collection/items",
    "/api/platform/collection/mixes",
    "/api/platform/collection/series",
    "/aweme/v1/web/user/favorite/",
    "/aweme/v1/web/user/collection/",
    "/aweme/v1/web/notice/list/",
    "/aweme/v1/web/user/profile/self/",
    # 用户信息（需登录态）
    "/api/platform/user/info",
    "/api/platform/user/works",
    "/aweme/v1/web/user/profile/other/",
    "/aweme/v1/web/im/user/info/",
    # 关系列表
    "/api/platform/relation/list",
    "/aweme/v1/web/user/follower/list/",
    "/aweme/v1/web/user/following/list/",
    # 私信会话
    "/aweme/v1/web/im/conversation/list/",
    "/aweme/v1/web/im/conversation/history/",
    # 连麦
    "/api/live/linkmic/apply",
    "/api/live/linkmic/status",
    "/api/live/linkmic/leave",
    "/aweme/v1/web/linkmic_audience/apply/",
    "/aweme/v1/web/linkmic_audience/waiting_list/",
    "/aweme/v1/web/linkmic_audience/list/v2/",
    "/aweme/v1/web/linkmic_audience/leave/",
    # 账号管理
    "/api/accounts",
    "/api/accounts/self-check",
    "/api/accounts/{name}/check",
    "/api/accounts/{name}/scan",
    "/api/accounts/{name}/sms-login",
    "/api/accounts/{name}/update-login",
    # BCC 浏览器路由
    "/cookie",
    "/user_info",
    "/capture_userinfo",
    "/user_info_by_uids",
    "/resolve_url",
    "/exec_js",
    "/linkmic_run",
    "/wp_messages",
    "/wp_send",
    "/scan_login",
    "/refresh",
}


def get_auth_for(endpoint: str, account: str = "") -> Optional[Any]:
    """根据接口路径决定返回凭证或 None（匿名）。

    Args:
        endpoint: 接口路径（如 "/api/platform/feed"）
        account: 账号名（匿名时可不传）

    Returns:
        DouyinAuth 对象（需凭证）或 None（匿名）
    """
    # 规范化路径（去掉尾部斜杠）
    ep = endpoint.rstrip("/")

    # 判断是否可匿名
    if _is_anon_endpoint(ep):
        logger.debug(f"[AUTH-POLICY] 匿名: {ep}")
        return None

    # 需要凭证的接口
    if _is_credential_endpoint(ep):
        logger.debug(f"[AUTH-POLICY] 凭证: {ep} account={account}")
        return _load_credential(account)

    # 未知接口：保守策略，使用凭证
    logger.warning(f"[AUTH-POLICY] 未知接口，保守使用凭证: {ep}")
    return _load_credential(account)


def _is_anon_endpoint(endpoint: str) -> bool:
    """判断接口是否可匿名。"""
    # 精确匹配
    if endpoint in ANON_ENDPOINTS:
        return True

    # 前缀匹配（如 /api/accounts/{name}/check）
    for anon_ep in ANON_ENDPOINTS:
        if endpoint.startswith(anon_ep):
            return True

    return False


def _is_credential_endpoint(endpoint: str) -> bool:
    """判断接口是否必须凭证。"""
    # 精确匹配
    if endpoint in CREDENTIAL_ENDPOINTS:
        return True

    # 前缀匹配
    for cred_ep in CREDENTIAL_ENDPOINTS:
        if endpoint.startswith(cred_ep):
            return True

    return False


def _load_credential(account: str) -> Any:
    """加载账号凭证。"""
    if not account:
        logger.warning("[AUTH-POLICY] 需要凭证但未指定账号")
        return None

    try:
        from auto_dm import accounts as acct_core
        env_path = acct_core.env_path_of(account)
        if not env_path:
            logger.warning(f"[AUTH-POLICY] 账号 {account} 未登记")
            return None

        from dy_apis.login_api import DYLoginApi
        auth = DYLoginApi._load_auth_from_env(env_path)
        return auth
    except Exception as e:
        logger.error(f"[AUTH-POLICY] 加载凭证失败: {e}")
        return None


def is_anonymous(endpoint: str) -> bool:
    """判断接口是否可匿名（供路由层快速判断）。"""
    return _is_anon_endpoint(endpoint.rstrip("/"))


def is_credential(endpoint: str) -> bool:
    """判断接口是否必须凭证（供路由层快速判断）。"""
    return _is_credential_endpoint(endpoint.rstrip("/"))
