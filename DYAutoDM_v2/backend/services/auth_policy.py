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

# ============================================================================
# 🔴 2026-10-01 实测订正（部署环境 C:\temp\dyautodm_design，v0.46.2）
# ============================================================================
# 初版按「推荐流/搜索可匿名」分类，部署环境实测**证伪**。实测对照：
#
#   | 端点                                  | 匿名结果                  |
#   |---------------------------------------|---------------------------|
#   | /aweme/v1/web/tab/feed/               | HTTP 200 / 0 字节 ❌      |
#   | /aweme/v1/web/general/search/single/  | sc=2483（风控拒绝）❌     |
#   | /aweme/v1/web/search/user/            | HTTP 404 Janus ❌         |
#   | /aweme/v1/web/search/live/            | HTTP 404 Janus ❌         |
#   | www.iesdouyin.com 移动评论端点         | 34KB / 10 条 ✅（仅此）   |
#   | https://www.douyin.com/ 首页           | 72KB ✅（网络正常）       |
#
# 判据：首页与 iesdouyin 均通 ⇒ 网络/本机未被封，是**端点本身要求登录态**。
# 故推荐流与搜索一律移回「必须凭证」，只保留实测真通过的匿名端点。
# ⛔ 禁止把「匿名但恒返回空」当成匿名可用 —— 那是本项目的**假成功**红线。

# 只读公开数据接口（可匿名）—— 仅限实测真通过的
ANON_ENDPOINTS = {
    # 评论预览（iesdouyin 移动端点；实测 34KB/10 条，零凭证 ✅）
    "/api/crawl/comments/anon-preview",
    "/aweme/v1/web/comment/list/anon",
    # ── 2026-10-02 实测订正：直播探活**必须匿名**（★ 你的设想成立）──
    # 实测（部署环境，web_rid=291891133640）：
    #   匿名 GET live.douyin.com/<web_rid>
    #   → HTTP 200 / 934KB / 解析 roomId=7691345987004009258 / status=2（直播中）
    #   → 服务端下发 ttwid(127字符) ⇒ 真匿名设备标识
    #   → 与历史实测 roomId 完全一致 ✅
    # ★ 知识库 ENG-023 已登记：开播检测**必须**匿名进房取 room_status；
    #   带凭证在降权账号下会返回错误 status='4'（误判下播），匿名反而更准。
    #   （项目既有实现 `core/live_hook.py:_anon_live_info` 本就是匿名探活）
    "/api/live/resolve",
    "/webcast/room/info/anon",
}

# 必须凭证的接口（写操作或登录门禁）
CREDENTIAL_ENDPOINTS = {
    # ── 2026-10-01 实测订正：以下「只读」端点实测匿名取不到，移回凭证 ──
    # 推荐流（实测 HTTP 200 / 0 字节）
    "/api/platform/feed",
    "/aweme/v1/web/tab/feed/",
    # 搜索（综合 sc=2483；用户/直播 404 Janus）
    "/api/platform/search",
    "/api/crawl/search",
    "/api/live/rooms/discover",
    "/aweme/v1/web/general/search/single/",
    "/aweme/v1/web/general/search/stream/",
    "/aweme/v1/web/search/user/",
    "/aweme/v1/web/search/live/",
    # 直播探活 / 房间状态：实测匿名可用，已移入 ANON_ENDPOINTS（见上）
    # ⛔ 贡献榜（实测匿名不可用）：room_id+anchor_id+sec_uid 齐全仍 HTTP 200/0 字节
    "/webcast/ranklist/audience/",
    "/aweme/v1/web/rank/list/",
    # 媒体取址（未经匿名实测通过，保守取凭证侧）
    "/api/platform/media/resolve",
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
    """判断接口是否可匿名。

    🔴 严格匹配规则（防止误匹配）：
    - 精确匹配：endpoint == anon_ep
    - 前缀匹配：endpoint.startswith(anon_ep + "/") 或 endpoint == anon_ep
    - 禁止 /api/platform/feed/sensitive 被误判为 /api/platform/feed 的匿名
    """
    for anon_ep in ANON_ENDPOINTS:
        if endpoint == anon_ep:
            return True
        if endpoint.startswith(anon_ep + "/"):
            return True
    return False


def _is_credential_endpoint(endpoint: str) -> bool:
    """判断接口是否必须凭证。

    🔴 严格匹配规则（防止误匹配）：
    - 精确匹配：endpoint == cred_ep
    - 前缀匹配：endpoint.startswith(cred_ep + "/") 或 endpoint == cred_ep
    """
    for cred_ep in CREDENTIAL_ENDPOINTS:
        if endpoint == cred_ep:
            return True
        if endpoint.startswith(cred_ep + "/"):
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
    """判断接口是否必须凭证（供路由层快速判断）。

    🔴 匿名优先：若已判定可匿名，则不再算作「必须凭证」
    （否则 `/api/crawl/comments/anon-preview` 会同时命中匿名与凭证两套前缀，
    造成调用方歧义）。
    """
    ep = endpoint.rstrip("/")
    if _is_anon_endpoint(ep):
        return False
    return _is_credential_endpoint(ep)
