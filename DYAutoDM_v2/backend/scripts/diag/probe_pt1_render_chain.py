"""验证：aweme_type=68（图文）经 _pick_aweme → extract_media 是否能取到地址。"""
import json
import os
import sys

sys.path.insert(0, os.getcwd())

from api.platform import _pick_aweme           # noqa: E402
from downloader import media_request as MR      # noqa: E402

# 按探针实测的真实形态构造（aweme_type=68，有 images，video 壳存在但 duration=0）
real = {
    "aweme_id": "7692345607121705450",
    "desc": "公司总想以最小的代价解决问题",
    "aweme_type": 68,
    "author": {"nickname": "n", "uid": "1", "sec_uid": "s"},
    "statistics": {},
    "video": {"duration": 0, "play_addr": {"url_list": ["https://v/x.mp4", "https://v/y.mp4"]}},
    "images": [
        {"url_list": ["https://p/img1.jpg"], "width": 1080, "height": 1440},
        {"url_list": ["https://p/img2.jpg"], "width": 1080, "height": 1440},
    ],
}

item = _pick_aweme(real)
media = item.get("media") or {}
print("=== _pick_aweme 产出的 media ===")
print("  media 键:", sorted(media.keys()))
print("  media.images 条数:", len(media.get("images") or []))

print("=== extract_media(media) 判定 ===")
m = MR.extract_media(media)
print("  type:", m.get("type"))
print("  images:", m.get("images"))
print("  videos 档位:", list((m.get("videos") or {}).keys()))

print("=== pick_quality('origin') ===")
picked = MR.pick_quality(m, "origin")
print("  结果:", repr(picked)[:80])
print("  " + ("❌ 取不到 ⇒ 502" if not picked else "✅ 有地址"))