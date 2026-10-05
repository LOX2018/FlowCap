"""pt=1 单条结果的**原始结构**取证（回答：为什么取不到播放地址）。

只打 1 次上游，dump 该作品的关键字段是否具备可播条件：
  - aweme_info 是否存在
  - video 子树 / video.play_addr.url_list 是否非空
  - bit_rate 档位数
  - aweme_type（图文/视频/特殊卡）
"""
import json
import os
import sys

sys.path.insert(0, os.getcwd())

from dy_apis.douyin_api import DouyinAPI  # noqa: E402
from services.auth_policy import get_auth_for  # noqa: E402


def main():
    acct = os.environ.get("DY_PROBE_ACCOUNT", "小助理")
    query = os.environ.get("DY_PROBE_QUERY", "工伤")
    auth = get_auth_for("/api/platform/search", acct)
    if auth is None:
        print(json.dumps({"fatal": "无凭证"}, ensure_ascii=False))
        return 1

    js = DouyinAPI.search_general_work(
        auth, query, sort_type="0", publish_time="1",
        offset="0", filter_duration="",
    )
    data = (js or {}).get("data") or []
    out = {"data_len": len(data), "items": []}
    for i, w in enumerate(data):
        info = w.get("aweme_info") if isinstance(w, dict) else None
        rec = {
            "idx": i,
            "shell_type": w.get("type") if isinstance(w, dict) else None,
            "has_aweme_info": bool(info),
        }
        if isinstance(info, dict):
            v = info.get("video") or {}
            pa = (v.get("play_addr") or {}).get("url_list") or []
            br = v.get("bit_rate") or []
            rec.update({
                "aweme_id": str(info.get("aweme_id") or ""),
                "desc": (info.get("desc") or "")[:40],
                "aweme_type": info.get("aweme_type"),
                "has_video_shell": bool(v),
                "play_addr_urls": len(pa),
                "play_addr_first_empty": (pa[0] == "" if pa else None),
                "bit_rate_count": len(br),
                "has_images": bool(info.get("images")),
                "duration": v.get("duration"),
            })
        out["items"].append(rec)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())