"""【真机实测】观察抖音页面自己加载私信图片时，用的是什么 URL、多大的图。

上一轮结论：消息体里的远程链（resource_url.* / [原图] URL）在真机浏览器里
**加载失败**（3/3 error，0×0）。

那抖音网页自己是怎么把图显示出来的？只有两种可能：
  A. 用了**另一套 URL**（消息体里没带，前端另行构造/请求）
  B. 压根没显示原图，只显示 inline_pic 缩略图放大

本脚本在真机页面里：
  1. 监听所有图片类网络请求（response 事件）
  2. 导航到 chat 页，滚一屏让会话渲染
  3. 打印实际加载成功的图片 URL 与尺寸

铁律：走项目 vbrowser.launch_async；跑前停应用（profile 锁）。
"""
import asyncio
import sys
from pathlib import Path as _P
import os

ROOT = _P(os.environ.get("DY_REPO_ROOT", r"C:\Users\LOX\Desktop\DYchajian"))
sys.path.insert(0, str(ROOT / "backend"))

PROFILE = str(_P(os.environ.get("FLOWCAP_APP_ROOT", r"C:\temp\flowcap_test"))
               / "auto_dm" / "accounts" / (os.environ.get("DY_TEST_ACCOUNT", "") or "") / "profile")

# 在页面里记录所有图片加载情况（用 PerformanceObserver + 遍历 img）
JS_COLLECT = r"""
() => {
  const out = [];
  // 页面里所有 <img>
  document.querySelectorAll('img').forEach((im) => {
    if (im.naturalWidth > 0) {
      out.push({
        src: (im.currentSrc || im.src || '').slice(0, 160),
        w: im.naturalWidth, h: im.naturalHeight,
      });
    }
  });
  // 背景图（CSS background-image）
  const bgs = [];
  document.querySelectorAll('*').forEach((el) => {
    const bg = getComputedStyle(el).backgroundImage;
    if (bg && bg.startsWith('url(') && /douyinpic|amemv|bytecdn|ixigua/.test(bg)) {
      const u = bg.slice(5, -2).replace(/^["']|["']$/g, '');
      bgs.push(u.slice(0, 160));
    }
  });
  return {imgs: out.slice(0, 40), bgs: [...new Set(bgs)].slice(0, 20)};
}
"""


async def main():
    from auto_dm import config as _cfg
    from auto_dm.vbrowser import launch_async, should_use_vb

    prof = _P(PROFILE)
    if not prof.exists():
        print(f"[!] profile 不存在: {prof}")
        return

    _vb, _vb_mode = should_use_vb(_cfg)
    print(f"指纹模式: vb={_vb} mode={_vb_mode}")

    pw, browser, ctx, backend = await launch_async(
        _vb_mode, _cfg, headless=True, user_data_dir=str(prof), force=False)
    print(f"浏览器启动成功 (backend={backend})\n")

    try:
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()

        # 记录所有图片响应
        img_responses = []

        def on_resp(resp):
            try:
                u = resp.url
                if not any(k in u for k in
                           ("douyinpic", "amemv", "bytecdn", "ixigua", "tplv")):
                    return
                ct = (resp.headers or {}).get("content-type", "")
                img_responses.append({
                    "status": resp.status,
                    "url": u[:170],
                    "ctype": ct,
                })
            except Exception:
                pass

        page.on("response", on_resp)

        await page.goto("https://www.douyin.com/chat?isPopup=1",
                        wait_until="domcontentloaded", timeout=30000)
        print("已导航到 chat 页，等待前端加载图片...")
        await page.wait_for_timeout(12000)

        # 滚一屏触发懒加载
        await page.evaluate("window.scrollBy(0, 600)")
        await page.wait_for_timeout(4000)

        print(f"\n捕获到图片类响应: {len(img_responses)} 个\n")
        print("=" * 72)
        print("网络层：实际成功加载的图片")
        print("=" * 72)
        ok = [r for r in img_responses if r["status"] == 200]
        bad = [r for r in img_responses if r["status"] != 200]
        print(f"  200 OK: {len(ok)}   非200: {len(bad)}\n")

        # 按 URL 里的 tplv 参数分类
        import re
        from collections import Counter
        kinds = Counter()
        for r in ok:
            m = re.search(r"~tplv-[A-Za-z0-9\-_:.]*", r["url"])
            kinds[m.group() if m else "(无tplv)"] += 1
        print("  按 tplv 参数分类:")
        for k, n in kinds.most_common(10):
            print(f"    {k:34s}: {n}")

        print("\n  Content-Type 分布:")
        for ct, n in Counter(r["ctype"] for r in ok).most_common(6):
            print(f"    {ct:28s}: {n}")

        print("\n  样例 URL（前 6 个 200 的）:")
        for r in ok[:6]:
            print(f"    [{r['status']}] {r['ctype'][:20]}")
            print(f"      {r['url'][:130]}")

        if bad:
            print(f"\n  非 200 样例（前 3）:")
            for r in bad[:3]:
                print(f"    [{r['status']}] {r['url'][:120]}")

        # DOM 里的实际 img
        res = await page.evaluate(JS_COLLECT)
        print()
        print("=" * 72)
        print(f"DOM 层：成功渲染的 <img>（{len(res['imgs'])} 个）")
        print("=" * 72)
        for im in res["imgs"][:12]:
            print(f"  {im['w']}×{im['h']}  {im['src'][:100]}")

        if res["bgs"]:
            print(f"\n  背景图（{len(res['bgs'])} 个）:")
            for b in res["bgs"][:5]:
                print(f"    {b[:110]}")

    finally:
        try:
            await ctx.close()
        except Exception:
            pass
        try:
            await pw.stop()
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(main())
