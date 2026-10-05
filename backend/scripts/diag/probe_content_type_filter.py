"""验证 content_type 是否真过滤（防「假接线」）。

对比 content_type=''（不限） vs '2'（图文），看**原始响应**里
`aweme_type` 与 `images` 的分布 —— 而不是靠 media 壳判定
（图文作品也带 video 壳，据此判定会把图文误判成视频）。
"""
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.getcwd())

from dy_apis.douyin_api import DouyinAPI  # noqa: E402
from services.auth_policy import get_auth_for  # noqa: E402


def shape_of(info: dict) -> str:
    """按上游字段判定形态（不靠 media 壳）。"""
    has_img = bool(info.get("images"))
    at = info.get("aweme_type")
    if has_img:
        return f"图文(images,type={at})"
    if (info.get("video") or {}).get("play_addr"):
        return f"视频(type={at})"
    return f"其他(type={at})"


def probe(auth, query, content_type, tag):
    js = DouyinAPI.search_general_work(
        auth, query, sort_type="0", publish_time="0",
        offset="0", filter_duration="", content_type=content_type,
    )
    data = (js or {}).get("data") or []
    infos = [w.get("aweme_info") for w in data
             if isinstance(w, dict) and w.get("aweme_info")]
    dist = Counter(shape_of(i) for i in infos)
    print(f"[{tag}] content_type={content_type!r} → data_len={len(data)} "
          f"作品={len(infos)}")
    for k, v in dist.most_common():
        print(f"      {v:>3}  {k}")
    return len(infos), dist


def main():
    acct = os.environ.get("DY_PROBE_ACCOUNT", "小助理")
    query = os.environ.get("DY_PROBE_QUERY", "美食")
    auth = get_auth_for("/api/platform/search", acct)
    if auth is None:
        print(json.dumps({"fatal": "无凭证"}, ensure_ascii=False))
        return 1
    probe(auth, query, "", "不限")
    print()
    probe(auth, query, "2", "只要图文")
    return 0


if __name__ == "__main__":
    sys.exit(main())