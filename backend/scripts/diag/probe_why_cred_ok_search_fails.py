# coding=utf-8
"""🔍 只读探针：为什么「凭证有效」与「搜索失效」能同时为真（2026-10-02）

根因假设：项目的「凭证有效」判据走的是 **/aweme/v1/web/query/user/**（身份探针），
而搜索走 **/general/search/**。平台的风控是**按端点/按业务域**下的，
不是按账号一刀切 ⇒ 身份端点仍通、搜索端点被 verify_check。

本探针对同一份 auth 并发打四类端点，逐个记录：
  status_code / status_msg / transport / search_nil_info / 实际条目数
判据：四类端点的结论必须**分别**呈现，不得合并成单一「凭证有效」。
只发 GET，不写任何业务数据。
"""
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))

from services.auth_policy import get_auth_for  # noqa: E402
from dy_apis.douyin_api import DouyinAPI  # noqa: E402

ACCOUNT = "小助理"
QUERY = "工伤"


def _norm(js):
    """把任意端点响应压成同一形状，便于横向比较。"""
    if not isinstance(js, dict):
        return {"shape": type(js).__name__}
    data = js.get("data")
    nil = js.get("search_nil_info")
    out = {
        "status_code": js.get("status_code"),
        "status_msg": js.get("status_msg"),
        "transport": js.get("_transport"),
        "nil_type": (nil or {}).get("search_nil_type") if isinstance(nil, dict) else None,
        "data_len": len(data) if isinstance(data, list) else None,
    }
    u = js.get("user")
    if isinstance(u, dict) and u:
        out["uid"] = u.get("uid")
        out["nickname"] = u.get("nickname")
    out["user_uid"] = js.get("user_uid")
    return out


def _run(label, api, fn, *args, **kwargs):
    try:
        return {"probe": label, **_norm(fn(*args, **kwargs))}
    except Exception as e:  # noqa: BLE001
        return {"probe": label, "exception": f"{type(e).__name__}: {e}"}


def main():
    auth = get_auth_for("/api/crawl/search", ACCOUNT)
    if auth is None:
        print(json.dumps({"fatal": "无凭证"}, ensure_ascii=False))
        return 1

    probes = [
        # ① 身份探针 —— 项目的「凭证有效」判据就走这里
        _run("identity_query_user(get_my_uid)", "query/user",
             lambda: {"user_uid": DouyinAPI.get_my_uid(auth, force_probe=True)}),
        # ② 身份端点（另一个）
        _run("user_profile_self", "user/profile/self",
             lambda: DouyinAPI.get_user_info(auth, "")),
        # ③ 搜索端点 —— 用户报障的那条
        _run("search_general_single", "general/search/single",
             lambda: DouyinAPI.search_general_work(auth, QUERY, "0", "0", "0", "", "", "")),
        # ④ 直播搜索端点
        _run("search_live", "live/search",
             lambda: DouyinAPI.search_live(auth, QUERY, "0", "25")),
    ]

    verdict = {
        "account": ACCOUNT,
        "probes": probes,
        "reading": (
            "若 identity_query_user 有 uid，而 search_* 为 nil_type=verify_check ⇒ "
            "「凭证有效」只覆盖身份域，不代表搜索域有权（平台按端点风控，非按账号一刀切）"
        ),
    }
    print(json.dumps(verdict, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
