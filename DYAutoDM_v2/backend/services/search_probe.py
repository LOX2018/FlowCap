# coding=utf-8
"""搜索域探针（2026-10-02 新增，v0.46.18）。

背景
----
`auto_dm.accounts.verify_credential` 是项目「唯一凭证有效性决策入口」，
但它回答的问题只有一半：`uid_probe` 走 `/aweme/v1/web/query/user/`
（身份域）+ `.env` 签名四件套静态齐全性（本地读文件），**从不请求任何
搜索端点**。

实测（2026-10-02，同一份 auth、同一时刻，四端点横向对照）：

  ============================  ==========================================
  端点                          结论
  ============================  ==========================================
  /aweme/v1/web/query/user/     ✅ user_uid=316276709526638（平台认这个登录态）
  /aweme/v1/web/user/profile/   ⚠️ status_code=0 + status_msg="blocked"
    self/                          + user={} 空
  /aweme/v1/web/general/search/ ❌ search_nil_info.search_nil_type=
    single/                        "verify_check"（HTTP 200，非 Argus 403）
  /aweme/v1/web/live/search/    ❌ 同上，verify_check
  ============================  ==========================================

⇒ 抖音的风控是**按业务域下发**的，不是按账号一刀切。身份域调用稀疏
（5 分钟一次）阈值低、一直放行；搜索域高频 + 强反爬，账号被单独加
`verify_check`。所以「凭证有效」与「搜索失效」**同时为真**，不是矛盾。

设计
----
**本模块是搜索域的探针出口**，与 `services/uid_probe`（身份域）对称：

  · 只读、只发一个 GET 搜索请求，**不写任何业务数据**。
  · 带 TTL 缓存（默认 300s 成功 / 60s 失败），避免各调用方各打各的
    —— 与 uid_probe 同理：搜索域的规律性探测本身也是风控信号。
  · **如实上报业务层风控事实**（`search_nil_info.search_nil_type`），
    绝不用「空列表」冒充「没搜到」（项目铁律：禁止假成功）。

为什么不并入 verify_credential 的 wp 字段
------------------------------------------
`wp`/`dm` 是**私信引擎**的前置守卫（铁律 #35：守护只在凭证有效时启动），
其语义是「身份可识别 + 签名材料齐全」。把搜索域结论塞进去会：
  ① 改变私信引擎的启动判据（搜索被限不该阻止守护运行）；
  ② 重演 `accounts.py:726` 注释已记录过的「端口在 ≠ 凭证有效」事故。
⇒ 独立字段、独立模块、**不改** 现有 wp/dm 任何语义。
"""

from __future__ import annotations

import threading
import time
from typing import Any, Optional

from loguru import logger

# 缓存键：按 (账号, 端点域) 隔离，避免不同账号共用一条结论
_LOCK = threading.Lock()
_CACHE: dict[str, tuple[float, dict]] = {}

# 与 uid_probe 对称的 TTL：成功 300s / 失败 60s
# 失败 TTL 短，是因为 verify_check 多为临时风控，需要较快感知到解除
TTL_OK = 300.0
TTL_FAIL = 60.0

# 探针关键词：用高频中性词，避免「探针本身」触发额外风控
PROBE_QUERY = "咖啡"


def _cache_key(account: str, domain: str) -> str:
    return f"{account}::{domain}"


def _read_cache(key: str, ttl_ok: float) -> Optional[dict]:
    with _LOCK:
        hit = _CACHE.get(key)
    if not hit:
        return None
    ts, val = hit
    ttl = ttl_ok if val.get("ok") else TTL_FAIL
    if (time.time() - ts) > ttl:
        return None
    return dict(val)


def _write_cache(key: str, val: dict) -> None:
    with _LOCK:
        _CACHE[key] = (time.time(), dict(val))


def clear_cache() -> None:
    """清空缓存（测试 / 凭证变更后用）。"""
    with _LOCK:
        _CACHE.clear()


def _classify(nil_type: Optional[str], transport: Optional[dict],
              data_len: Optional[int]) -> tuple[str, str]:
    """把原始响应压成 (level, label)。

    level 语义（**与身份域 wp 的 ok 不是一回事**）：
      ok    —— 搜索域有权，data 非空
      empty —— 搜索域有权，data 为空（该关键词真的没结果，**不是**故障）
      warn  —— 业务层要求风控验证（verify_check）⇒ 平台侧限制
      fail  —— 传输层被拦（Argus 403 / 非 200）
      error —— 探针自身异常（网络、凭证缺失等）
    """
    if transport and int(transport.get("status") or 0) != 200:
        st = transport.get("status")
        by = transport.get("bytes")
        return "fail", f"被风控拦截（HTTP {st}，{by} 字节）"
    nt = str(nil_type or "")
    if nt and nt != "normal":
        return "warn", f"平台要求风控验证（search_nil_type={nt}）"
    if data_len:
        return "ok", f"搜索域可用（命中 {data_len} 条）"
    return "empty", "搜索域有权但该关键词无结果"


def probe_search_domain(account: str, auth: Any = None,
                        query: str = PROBE_QUERY,
                        force: bool = False) -> dict:
    """探测某账号的**搜索域权限**（只读，一个 GET）。

    Args:
        account: 账号名（缓存键 + 凭证定位）
        auth:    已加载的凭证；为 None 时按 account 现取
        query:   探针关键词（默认中性词，勿用业务关键词以免污染业务统计）
        force:   跳过缓存（仅测试 / 用户显式「重新校验」用）

    Returns:
        ``{ok: bool, level: str, label: str, detail: str, domain: str,
           nil_type: str|None, data_len: int|None, transport: dict|None,
           from_cache: bool}``

        ⚠️ ``ok`` 的含义是「**搜索域可用**」，不是「凭证有效」。
        身份域结论见 ``services.uid_probe``。
    """
    domain = "search"
    key = _cache_key(account, domain)
    if not force:
        cached = _read_cache(key, TTL_OK)
        if cached is not None:
            cached["from_cache"] = True
            return cached

    out: dict[str, Any] = {
        "ok": False, "level": "error", "label": "探针异常",
        "detail": "", "domain": domain, "nil_type": None,
        "data_len": None, "transport": None, "from_cache": False,
    }

    try:
        if auth is None:
            from services.auth_policy import get_auth_for
            auth = get_auth_for("/api/crawl/search", account)
        if auth is None:
            out.update(level="error", label="无凭证",
                       detail="账号未登记或凭证加载失败")
            _write_cache(key, out)
            return out

        from dy_apis.douyin_api import DouyinAPI
        js = DouyinAPI.search_general_work(auth, query, "0", "0", "0", "", "", "")
        if not isinstance(js, dict):
            out.update(detail=f"响应结构异常：{type(js).__name__}")
            _write_cache(key, out)
            return out

        transport = js.get("_transport")
        nil = js.get("search_nil_info")
        nil_type = (nil or {}).get("search_nil_type") if isinstance(nil, dict) else None
        data = js.get("data")
        data_len = len(data) if isinstance(data, list) else 0

        level, label = _classify(nil_type, transport, data_len)
        out.update(ok=(level == "ok" or level == "empty"),
                   level=level, label=label, detail=label,
                   nil_type=nil_type, data_len=data_len, transport=transport)
    except Exception as e:  # noqa: BLE001 —— 探针不得让调用方崩
        out.update(detail=f"{type(e).__name__}: {e}")
        logger.warning(f"[SEARCH-PROBE-001] 搜索域探针异常 account={account}: {e}")

    _write_cache(key, out)
    return out


def get_search_permission(account: str, auth: Any = None,
                          force: bool = False) -> dict:
    """``probe_search_domain`` 的语义化别名（读侧命名对齐 uid_probe.get_uid）。"""
    return probe_search_domain(account, auth=auth, force=force)
