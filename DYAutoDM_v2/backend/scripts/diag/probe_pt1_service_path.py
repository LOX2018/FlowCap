"""复现服务端 `/api/platform/search` 的**完整代码路径**（含回落逻辑）。

直接调用 `api.platform.search(SearchReq(...))`，与运行实例走同一段代码，
避免"探针测的是我拼的链路、服务跑的是另一段"这一类假结论。
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.getcwd())

from api.platform import SearchReq, search  # noqa: E402


async def main():
    acct = os.environ.get("DY_PROBE_ACCOUNT", "小助理")
    query = os.environ.get("DY_PROBE_QUERY", "工伤")

    # 对照：pt=0 基线 / pt=1 复现 / pt=7 对照
    for pt, tag in (("0", "基线-不限"), ("1", "复现-一天内"), ("7", "对照-一周内")):
        try:
            r = await search(SearchReq(account=acct, query=query,
                                       kind="video", num=20, publish_time=pt))
            items = r.get("items") or []
            ids = [it.get("aweme_id") for it in items]
            has_media = sum(1 for it in items if (it.get("media") or {}).get("video")
                            or (it.get("media") or {}).get("images"))
            print(f"[{tag}] pt={pt}: ok={r.get('ok')} items={len(items)} "
                  f"带media={has_media} blocked={r.get('blocked')}")
            print(f"    aweme_ids[:5]={ids[:5]}")
        except Exception as e:  # noqa: BLE001
            print(f"[{tag}] pt={pt}: EXC {type(e).__name__}: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))