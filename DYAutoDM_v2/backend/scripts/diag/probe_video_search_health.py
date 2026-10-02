# coding=utf-8
"""🔍 只读探针：视频搜索失效定位（2026-10-02）

目的：区分三种失败
  A. 传输层被 Argus 拦截（HTTP 403 / 非 200）
  B. HTTP 200 但业务层要求验证（search_nil_info）
  C. 端点真的正常、只是关键词无结果

同时对比 /general/search/single/ 与 /general/search/stream/ 两条路径。
本探针**只发 GET 搜索请求**，不写任何业务数据。
"""
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]  # scripts/diag/x.py -> backend/
sys.path.insert(0, str(BACKEND))

from services.auth_policy import get_auth_for  # noqa: E402
from dy_apis.douyin_api import DouyinAPI  # noqa: E402

ACCOUNT = "小助理"
QUERIES = ["咖啡", "露营"]


def _transport_of(obj):
    if isinstance(obj, dict):
        return obj.get("_transport")
    return None


def probe_single(q, auth):
    """老接口 /aweme/v1/web/general/search/single/"""
    out = {}
    try:
        js = DouyinAPI.search_general_work(auth, q, "0", "0", "0", "", "", "")
        out["endpoint"] = "single"
        out["http_status"] = None
        out["transport"] = _transport_of(js)
        out["status_code"] = (js or {}).get("status_code")
        data = (js or {}).get("data")
        out["data_len"] = len(data) if isinstance(data, list) else None
        out["has_more"] = (js or {}).get("has_more")
        out["nil_info"] = (js or {}).get("search_nil_info")
        out["keys"] = list(js)[:8] if isinstance(js, dict) else type(js).__name__
        works = [w for w in (data or []) if isinstance(w, dict) and w.get("aweme_info")]
        out["works"] = len(works)
    except Exception as e:  # noqa: BLE001
        out = {"endpoint": "single", "exception": f"{type(e).__name__}: {e}"}
    return out


def probe_stream(q, auth):
    """源项目方案 /aweme/v1/web/general/search/stream/"""
    out = {}
    try:
        st = DouyinAPI.search_stream(auth, q, "0", "10")
        out["endpoint"] = "stream"
        out["transport"] = st.get("_transport")
        out["status_code"] = st.get("status_code")
        out["aweme_list_len"] = len(st.get("aweme_list") or [])
        out["raw_len"] = len(st.get("raw") or [])
        out["nil_info"] = st.get("search_nil_info")
        out["keys"] = list(st)[:8]
    except Exception as e:  # noqa: BLE001
        out = {"endpoint": "stream", "exception": f"{type(e).__name__}: {e}"}
    return out


def probe_through_api(q, auth):
    """走 features 封装层（前端 /api/crawl/search 真实路径）"""
    out = {}
    try:
        from features import search_work
        env = search_work(auth, q, 20, "0", "0", "")
        out["endpoint"] = "features.search_work"
        out["ok"] = bool(isinstance(env, dict) and env.get("ok"))
        out["error"] = (env or {}).get("error") if isinstance(env, dict) else None
        data = (env or {}).get("data") if isinstance(env, dict) else None
        out["data_len"] = len(data) if isinstance(data, list) else None
        out["carries_transport"] = bool(
            isinstance(data, list) and getattr(data, "last_transport", None))
        out["transport"] = getattr(data, "last_transport", None) if isinstance(data, list) else None
    except Exception as e:  # noqa: BLE001
        out = {"endpoint": "features.search_work",
               "exception": f"{type(e).__name__}: {e}"}
    return out


def main():
    auth = get_auth_for("/api/crawl/search", ACCOUNT)
    if auth is None:
        print(json.dumps({"fatal": "无凭证，无法探测"}, ensure_ascii=False, indent=2))
        return 1
    report = {"account": ACCOUNT, "probes": []}
    for q in QUERIES:
        for fn in (probe_through_api, probe_single, probe_stream):
            report["probes"].append({"query": q, **fn(q, auth)})
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
