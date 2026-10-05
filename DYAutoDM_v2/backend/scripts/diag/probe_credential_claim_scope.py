# coding=utf-8
"""🔍 只读探针：判定「凭证有效」这个结论的**作用域**（2026-10-02）

用户质疑：凭证校验显示有效，为何搜索失效？
本探针把「凭证有效」拆成三个**强度不同**的断言，逐个验证：

  L1 凭证结构完整  —— cookie 里有 sessionid / msToken / bd_ticket（纯本地，零网络）
  L2 会话被平台认可 —— user/profile/self 返回真实 user（uid/nickname）
  L3 业务功能有权 —— 搜索端点返回真实 data

判读：L1 ∧ L2 成立而 L3 不成立 ⇒ 「凭证有效」这个结论**只覆盖到 L2**，
      不能外推为「功能可用」。这正是 UI 误导用户的根源。
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


def _dump_profile(auth):
    """把 profile/self 响应**完整**打出来（含 status_msg / user 子树键）。"""
    out = {"group": "L2_identity", "endpoint": "user/profile/self"}
    try:
        js = DouyinAPI.get_user_info(auth, "")
        if not isinstance(js, dict):
            return {**out, "shape": type(js).__name__}
        out["status_code"] = js.get("status_code")
        out["status_msg"] = js.get("status_msg")
        out["has_user_key"] = "user" in js
        u = js.get("user")
        if isinstance(u, dict):
            out["user_is_empty_dict"] = (len(u) == 0)
            out["uid"] = u.get("uid")
            out["nickname"] = u.get("nickname")
            out["sec_uid_present"] = bool(u.get("sec_uid"))
            out["user_keys"] = sorted(u)[:15]
        else:
            out["user_type"] = type(u).__name__
        out["extra"] = js.get("extra")
        out["all_keys"] = sorted(js)
    except Exception as e:  # noqa: BLE001
        out = {**out, "exception": f"{type(e).__name__}: {e}"}
    return out


def _dump_feed(auth):
    out = {"group": "L3_function", "endpoint": "tab/feed"}
    try:
        js = DouyinAPI.get_feed(auth, "20", "2")
        if not isinstance(js, dict):
            return {**out, "shape": type(js).__name__}
        out["status_code"] = js.get("status_code")
        out["status_msg"] = js.get("status_msg")
        data = js.get("data")
        out["data_type"] = type(data).__name__
        out["data_len"] = len(data) if isinstance(data, list) else None
        out["all_keys"] = sorted(js)[:10]
    except Exception as e:  # noqa: BLE001
        out = {**out, "exception": f"{type(e).__name__}: {e}"}
    return out


def _dump_search(auth):
    out = {"group": "L3_function", "endpoint": "general/search/single"}
    try:
        js = DouyinAPI.search_general_work(auth, QUERY, "0", "0", "0", "", "", "")
        if not isinstance(js, dict):
            return {**out, "shape": type(js).__name__}
        out["status_code"] = js.get("status_code")
        data = js.get("data")
        out["data_len"] = len(data) if isinstance(data, list) else None
        nil = js.get("search_nil_info")
        out["nil_type"] = (nil or {}).get("search_nil_type") if isinstance(nil, dict) else None
        out["transport"] = js.get("_transport")
    except Exception as e:  # noqa: BLE001
        out = {**out, "exception": f"{type(e).__name__}: {e}"}
    return out


def main():
    auth = get_auth_for("/api/crawl/search", ACCOUNT)
    if auth is None:
        print(json.dumps({"fatal": "无凭证"}, ensure_ascii=False))
        return 1

    ck = getattr(auth, "cookie", {}) or {}
    verdict = {
        "account": ACCOUNT,
        "L1_credential_structure": {
            "sessionid": "sessionid" in ck,
            "sessionid_ss": "sessionid_ss" in ck,
            "ttwid": "ttwid" in ck,
            "passport_csrf_token": "passport_csrf_token" in ck,
            "msToken": bool(getattr(auth, "msToken", None)),
            "cookie_count": len(ck),
        },
    }
    verdict["L2_identity"] = _dump_profile(auth)
    verdict["L3_function_feed"] = _dump_feed(auth)
    verdict["L3_function_search"] = _dump_search(auth)
    print(json.dumps(verdict, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
