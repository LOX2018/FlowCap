"""最小对照探针：定位「一天内(pt=1) 返回 0 条」的真因。

只打 3 次上游（pt=0 基线 / pt=1 复现 / pt=7 对照），取**原始响应事实**
（status_code / data_len / has_more / search_nil_info），不做任何推断。

用法（在 backend 目录下）：
    PYTHONPATH=<backend> python <此文件>
"""
import json
import os
import sys

sys.path.insert(0, os.getcwd())

from dy_apis.douyin_api import DouyinAPI  # noqa: E402
from services.auth_policy import get_auth_for  # noqa: E402


def probe(auth, query, publish_time, tag):
    out = {"tag": tag, "publish_time": publish_time}
    try:
        js = DouyinAPI.search_general_work(
            auth, query,
            sort_type="0", publish_time=publish_time,
            offset="0", filter_duration="",
        )
        out["transport"] = (js or {}).get("_transport")
        out["status_code"] = (js or {}).get("status_code")
        data = (js or {}).get("data")
        out["data_len"] = len(data) if isinstance(data, list) else None
        out["has_more"] = (js or {}).get("has_more")
        out["search_nil_info"] = (js or {}).get("search_nil_info")
        works = [w for w in (data or []) if isinstance(w, dict) and w.get("aweme_info")]
        out["works_with_aweme_info"] = len(works)
        out["top_keys"] = sorted(js)[:10] if isinstance(js, dict) else type(js).__name__
    except Exception as e:  # noqa: BLE001
        out["exception"] = f"{type(e).__name__}: {e}"
    return out


def main():
    acct = os.environ.get("DY_PROBE_ACCOUNT", "小助理")
    query = os.environ.get("DY_PROBE_QUERY", "工伤")
    try:
        auth = get_auth_for("/api/platform/search", acct)
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"fatal": f"取凭证失败: {type(e).__name__}: {e}"},
                         ensure_ascii=False))
        return 1
    if not auth:
        print(json.dumps({"fatal": f"无凭证: {acct}"}, ensure_ascii=False))
        return 1

    rows = []
    for pt, tag in (("0", "基线-不限"), ("1", "复现-一天内"), ("7", "对照-一周内")):
        rows.append(probe(auth, query, pt, tag))

    print(json.dumps({"account": acct, "query": query, "rows": rows},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
