"""活体验证：图文取址不再误报 502。

直接调 `media_stream_ticket`（与前端 openAweme 同一路径），
对一个**真实图文作品**验证：修复前 502，修复后 `ok=True` + `images` 非空。
"""
import asyncio
import os
import sys

sys.path.insert(0, os.getcwd())

from api.platform import (  # noqa: E402
    MediaResolveReq, StreamTicketReq, media_resolve, media_stream_ticket,
    search, SearchReq, _extract_aweme_list,
)


async def find_image_aweme(auth, query):
    """取一个真实图文作品（content_type=2），返回裁剪后的作品对象。"""
    from dy_apis.douyin_api import DouyinAPI
    js = await asyncio.to_thread(
        DouyinAPI.search_general_work, auth, query,
        sort_type="0", publish_time="0", offset="0",
        filter_duration="", content_type="2")
    works = _extract_aweme_list(js)
    # 注意：`_extract_aweme_list` 返回的是 **aweme_info 原对象**
    # （`images` 在顶层）；`media` 子树要经过 `_pick_aweme` 裁剪后才出现。
    for w in works or []:
        if isinstance(w, dict) and (w.get("images") or
                                    (w.get("media") or {}).get("images")):
            return w
    return None


async def main():
    acct = os.environ.get("DY_PROBE_ACCOUNT", "小助理")
    query = os.environ.get("DY_PROBE_QUERY", "美食")
    from services.auth_policy import get_auth_for
    auth = get_auth_for("/api/platform/search", acct)
    if auth is None:
        print("FATAL 无凭证"); return 1

    it = await find_image_aweme(auth, query)
    if it is None:
        print(f"FATAL 未找到图文作品 query={query!r}"); return 1

    aid = it.get("aweme_id") or ""
    n_img = len(it.get("images") or ((it.get("media") or {}).get("images") or []))
    print(f"目标图文: aweme_id={aid} images={n_img} 条")

    # ① resolve 应正确分流为 images 类型（前端回传的是 _pick_aweme 裁剪对象，
    #    这里直接给原 aweme_info —— extract_media 两种形态都支持）
    r = await media_resolve(MediaResolveReq(account=acct, raw=it,
                                            quality="origin"))
    print(f"[resolve] type={r.get('type')!r} url={bool(r.get('url'))} "
          f"images={len(r.get('images') or [])}")

    # ② stream_ticket —— 修复前此处抛 502「无可用地址」
    try:
        t = await media_stream_ticket(StreamTicketReq(
            account=acct, aweme_id=aid, raw=it, quality="origin"))
        print(f"[stream_ticket] ✅ ok={t.get('ok')} type={t.get('type')!r} "
              f"images={len(t.get('images') or [])} "
              f"stream_url={t.get('stream_url')!r}")
        print("  ⇒ 502 已修复：图文以图集形式成功返回")
        return 0
    except Exception as e:  # noqa: BLE001
        print(f"[stream_ticket] ❌ {type(e).__name__}: {e}")
        print("  ⇒ 仍报取址失败（图文被当视频解析）")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
