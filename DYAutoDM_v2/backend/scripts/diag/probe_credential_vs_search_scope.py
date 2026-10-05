# coding=utf-8
"""🔍 只读探针：凭证有效性 vs 搜索权限 是否独立（2026-10-02）

背景：用户报告「凭证校验显示凭证有效，但视频搜索失效」。
本探针用**同一份 auth**，对不同端点分组探测，证明二者正交：

  组 A 身份类（凭证有效性能证明什么）
      - /aweme/v1/web/user/profile/self/  —— uid 探针走的就是这个
      - 推荐流 /aweme/v1/web/tab/feed/
  组 B 搜索类（verify_check 风控）
      - /aweme/v1/web/general/search/single/
      - /aweme/v1/web/general/search/stream/
      - /aweme/v1/web/live/search/

判读：若 A 组全通、B 组被 verify_check ⇒ 二者正交，
      「凭证有效」不蕴含「搜索有权」，前端文案必须分开。
本探针只发 GET，不写任何业务数据。
"""
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]  # scripts/diag/x.py -> backend/
sys.path.insert(0, str(BACKEND))

from services.auth_policy import get_auth_for  # noqa: E402
from dy_apis.douyin_api import DouyinAPI  # noqa: E402

ACCOUNT = "小助理"
QUERY = "工伤"


def probe_identity(auth):
    """组 A：身份类端点。凭证有效 ⇒ 这里应该通。"""
    out = {"group": "A_identity", "endpoint": "user/profile/self"}
    try:
        js = DouyinAPI.get_user_info(auth, "")
        out["ok"] = bool(js)
        out["user_nickname"] = (js.get("user") or {}).get("nickname") if isinstance(js, dict) else None
        out["uid"] = (js.get("user") or {}).get("uid") if isinstance(js, dict) else None
        out["keys"] = list(js)[:6] if isinstance(js, dict) else type(js).__name__
    except Exception as e:  # noqa: BLE001
        out = {**out, "exception": f"{type(e).__name__}: {e}"}
    return out


def probe_feed(auth):
    """组 A：推荐流。与搜索同属内容读取，平台风控口径可能不同。"""
    out = {"group": "A_identity", "endpoint": "tab/feed"}
    try:
        js = DouyinAPI.get_feed(auth, "20", "2")
        data = (js or {}).get("data") if isinstance(js, dict) else None
        out["status_code"] = (js or {}).get("status_code") if isinstance(js, dict) else None
        out["data_len"] = len(data) if isinstance(data, list) else None
    except Exception as e:  # noqa: BLE001
        out = {**out, "exception": f"{type(e).__name__}: {e}"}
    return out


def probe_search_single(auth):
    """组 B：视频搜索 single。"""
    out = {"group": "B_search", "endpoint": "general/search/single"}
    try:
        js = DouyinAPI.search_general_work(auth, QUERY, "0", "0", "0", "", "", "")
        out["transport"] = (js or {}).get("_transport")
        out["status_code"] = (js or {}).get("status_code") if isinstance(js, dict) else None
        data = (js or {}).get("data") if isinstance(js, dict) else None
        out["data_len"] = len(data) if isinstance(data, list) else None
        out["nil_type"] = ((js or {}).get("search_nil_info") or {}).get("search_nil_type") \
            if isinstance(js, dict) else None
    except Exception as e:  # noqa: BLE001
        out = {**out, "exception": f"{type(e).__name__}: {e}"}
    return out


def probe_search_stream(auth):
    """组 B：视频搜索 stream。"""
    out = {"group": "B_search", "endpoint": "general/search/stream"}
    try:
        st = DouyinAPI.search_stream(auth, QUERY, "0", "10")
        out["transport"] = st.get("_transport")
        out["status_code"] = st.get("status_code")
        out["aweme_list_len"] = len(st.get("aweme_list") or [])
        out["raw_len"] = len(st.get("raw") or [])
    except Exception as e:  # noqa: BLE001
        out = {**out, "exception": f"{type(e).__name__}: {e}"}
    return out


def probe_search_live(auth):
    """组 B：直播搜索。已知今天实测同为 verify_check。"""
    out = {"group": "B_search", "endpoint": "live/search"}
    try:
        js = DouyinAPI.search_live(auth, QUERY, "0", "25")
        out["transport"] = (js or {}).get("_transport") if isinstance(js, dict) else None
        out["status_code"] = (js or {}).get("status_code") if isinstance(js, dict) else None
        data = (js or {}).get("data") if isinstance(js, dict) else None
        out["data_len"] = len(data) if isinstance(data, list) else None
        out["nil_type"] = ((js or {}).get("search_nil_info") or {}).get("search_nil_type") \
            if isinstance(js, dict) else None
    except Exception as e:  # noqa: BLE001
        out = {**out, "exception": f"{type(e).__name__}: {e}"}
    return out


def probe_credential_shape(auth):
    """组 C：凭证本身的时间字段（是否真的过期）。"""
    out = {"group": "C_credential", "endpoint": "cookie fields"}
    try:
        ck = getattr(auth, "cookie", {}) or {}
        out["cookie_keys"] = sorted(ck)[:20]
        out["has_sessionid"] = "sessionid" in ck
        out["has_sessionid_ss"] = "sessionid_ss" in ck
        out["has_ttwid"] = "ttwid" in ck
        out["has_passport_csrf_token"] = "passport_csrf_token" in ck
        out["msToken_present"] = bool(getattr(auth, "msToken", None))
    except Exception as e:  # noqa: BLE001
        out = {**out, "exception": f"{type(e).__name__}: {e}"}
    return out


def main():
    auth = get_auth_for("/api/crawl/search", ACCOUNT)
    if auth is None:
        print(json.dumps({"fatal": "无凭证，无法探测"}, ensure_ascii=False, indent=2))
        return 1
    report = {
        "account": ACCOUNT,
        "note": "同���份 auth 打 A/B/C 三组端点，验证『凭证有效』与『搜索权限』是否正交",
        "probes": [
            probe_credential_shape(auth),
            probe_identity(auth),
            probe_feed(auth),
            probe_search_single(auth),
            probe_search_stream(auth),
            probe_search_live(auth),
        ],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
