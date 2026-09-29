# -*- coding: utf-8 -*-
"""闭环验证：Scrapling 定位(离线) -> 绝对XPath -> 真实 Camoufox/Playwright 元素
证明：定位到的 XPath 在真实页面上解析为【可见、有几何框】的控件（即真正可点击）。
严格只读：不点击、不输入、不提交。
"""
import asyncio, os, sys, json, tempfile, traceback
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from locator import AdaptiveLocator, LOGIN_TARGETS

LOGIN_URL = "https://www.douyin.com/?modal_id=login"


async def main():
    prof = os.path.join(tempfile.gettempdir(), "_cam_spike_profile2")
    os.makedirs(prof, exist_ok=True)
    from camoufox.async_api import AsyncCamoufox

    db = os.path.join(tempfile.mkdtemp(prefix="brg_"), "e.sqlite")
    loc = AdaptiveLocator(storage_file=db)
    report = {}

    ctx_mgr = AsyncCamoufox(persistent_context=True, user_data_dir=prof,
                            headless=True, locale="zh-CN", os="windows",
                            i_know_what_im_doing=True)
    ctx = await ctx_mgr.__aenter__()
    try:
        page = await ctx.new_page()
        await page.set_viewport_size({"width": 1600, "height": 1000})
        await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
        for _ in range(60):
            if await page.locator("#douyin-login-new-id").count() > 0:
                break
            await asyncio.sleep(0.5)
        await asyncio.sleep(2.5)

        html = await page.content()
        loc.seed(html, LOGIN_TARGETS)      # 播种（真实页面）
        print(f"真实页面 DOM 长度={len(html)}")

        for t in LOGIN_TARGETS:
            r = loc.locate(html, t)
            item = {"ok": r.ok, "strategy": r.strategy, "xpath": r.xpath}
            if r.ok and r.xpath:
                lc = page.locator(r.xpath)
                try:
                    item["count"] = await lc.count()
                    item["visible"] = await lc.first.is_visible()
                    box = await lc.first.bounding_box()
                    item["box"] = {k: round(v, 1) for k, v in (box or {}).items()}
                    item["text"] = (await lc.first.inner_text())[:20]
                except Exception as e:
                    item["err"] = repr(e)
            report[t.key] = item
            print(json.dumps({t.key: item}, ensure_ascii=False))
    finally:
        try:
            await ctx_mgr.__aexit__(None, None, None)
        except Exception:
            pass

    merged = {}
    for k, v in report.items():
        b = v.get("box")
        if b:
            merged.setdefault((b.get("x"), b.get("y"), b.get("width"), b.get("height")), []).append(k)
    dup = {k2: v2 for k2, v2 in merged.items() if len(v2) > 1}
    print("\n== 汇总 ==")
    print("  定位成功:", sum(1 for v in report.values() if v["ok"]), "/", len(report))
    print("  可见且有框:", sum(1 for v in report.values() if v.get("visible") and v.get("box")), "/", len(report))
    print("  几何框重复(应为空):", dup)
    out = os.path.join(HERE, "_live", "bridge_report.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    json.dump(report, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        traceback.print_exc(); sys.exit(1)
