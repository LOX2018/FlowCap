"""决定性探针：用**真实 pt=1 数据**跑完整取址链路，打印具体失败点。

回答：502「无可用地址」到底在哪一环丢失。
链路：search_general_work(pt=1) → _extract_aweme_list → _pick_aweme
      → media_resolve 的等价逻辑 → pick_quality → _media_host_ok
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.getcwd())

from api.platform import (  # noqa: E402
    _extract_aweme_list, _pick_aweme, _media_host_ok, MediaResolveReq,
    media_resolve,
)
from dy_apis.douyin_api import DouyinAPI          # noqa: E402
from services.auth_policy import get_auth_for     # noqa: E402
from downloader import media_request as MR        # noqa: E402


async def main():
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
    lst = _extract_aweme_list(js)
    print(f"搜索(pt=1) → 剥壳后 {len(lst)} 条")

    for idx, w in enumerate(lst[:3]):
        item = _pick_aweme(w)
        media = item.get("media") or {}
        print(f"\n--- 第 {idx} 条 aweme_id={item.get('aweme_id')} ---")
        v = media.get("video") or {}
        print(f"  media.video 键: {sorted(v.keys())}")
        print(f"  media.images 条数: {len(media.get('images') or [])}")

        # 前端实际回传的就是 media
        r = await media_resolve(MediaResolveReq(
            account=acct, aweme_id=item.get("aweme_id"), raw=media))
        url = r.get("url") or ""
        print(f"  media_resolve.url = {url[:70]!r}")
        print(f"  type = {r.get('type')!r}  主机白名单 = {_media_host_ok(url) if url else 'N/A'}")
        print("  " + ("✅ 可取址" if url else "❌ 无可用地址 ⇒ 502（此处复现）"))

        # 若失败，进一步定位是哪一环
        if not url:
            m = MR.extract_media(media)
            print(f"    extract_media.type = {m.get('type')!r}")
            print(f"    videos 档位 = {list((m.get('videos') or {}).keys())}")
            print(f"    images 条数 = {len(m.get('images') or [])}")
            print(f"    pick_quality('origin') = {MR.pick_quality(m, 'origin')!r}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))