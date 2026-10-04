"""验证假设：「502 = 把图文当成视频解析」

证据链：图文作品（aweme_type=68）
  · 有 `images`（图文真实载体）
  · **没有** `video.play_addr`（走视频解析取址 ⇒ 空 ⇒ 502）

对照三个 content_type，看卡片形态与「按视频取址能否拿到地址」。
"""
import os
import sys
from collections import Counter

sys.path.insert(0, os.getcwd())

from dy_apis.douyin_api import DouyinAPI  # noqa: E402
from services.auth_policy import get_auth_for  # noqa: E402


def probe(auth, query, content_type, tag):
    js = DouyinAPI.search_general_work(
        auth, query, sort_type="0", publish_time="0",
        offset="0", filter_duration="", content_type=content_type)
    infos = [w.get("aweme_info") for w in (js or {}).get("data") or []
             if isinstance(w, dict) and w.get("aweme_info")]
    stat = Counter()
    for i in infos:
        vid = (i.get("video") or {}).get("play_addr") or {}
        has_play = bool(vid.get("url_list"))
        has_img = bool(i.get("images"))
        if has_img:
            stat["图文(有images)"] += 1
            if has_play:
                stat["  └ 图文却带play_addr"] += 1
        elif has_play:
            stat["视频(有play_addr)"] += 1
        else:
            stat["无图无址 ⇒ 点开必502"] += 1
    print(f"[{tag}] content_type={content_type!r} 作品={len(infos)}")
    for k, v in stat.most_common():
        print(f"      {v:>3}  {k}")


def main():
    acct = os.environ.get("DY_PROBE_ACCOUNT", "小助理")
    query = os.environ.get("DY_PROBE_QUERY", "美食")
    auth = get_auth_for("/api/platform/search", acct)
    if auth is None:
        print("FATAL 无凭证"); return 1
    probe(auth, query, "", "不限(用户日常所见)")
    print()
    probe(auth, query, "2", "只要图文")
    return 0


if __name__ == "__main__":
    sys.exit(main())