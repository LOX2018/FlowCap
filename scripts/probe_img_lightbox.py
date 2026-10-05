"""【真机实测·最后一步】点开私信图片的「查看大图」弹层，抓那一刻的图片请求。

前几轮已经确认：
  1. 消息体里的 [原图] URL 在真机里加载失败（3/3 error，0×0）
  2. 会话列表只加载头像（aweme-avatar），不加载私信图片
  3. 点进会话后，消息流里**仍然不加载私信图片**
     （DOM 里只有 UI 图标 132/168/36px）

推论：抖音 IM 消息流里显示的**就是缩略图**（inline_pic，160px 级），
      原图是**用户点开大图弹层时才按需请求**的。

本脚本做最后一步：真的去点开一张私信图片的大图弹层，
抓那一刻抖音请求的图片 URL + 实际尺寸。

若能抓到大图 URL → 说明原图可按需获取，可据此实现「点击看原图」。
若抓不到（比如弹层里也只有缩略图）→ 确认原图不可得。

铁律：走项目 vbrowser.launch_async；跑前停应用（profile 锁）。
"""
import asyncio
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path as _P
import os

ROOT = _P(os.environ.get("DY_REPO_ROOT", r"C:\Users\LOX\Desktop\DYchajian"))
sys.path.insert(0, str(ROOT / "backend"))

DB = _P(os.environ.get("FLOWCAP_APP_ROOT", r"C:\temp\flowcap_test")) / "data" / "flowcap.db"
PROFILE = str(_P(os.environ.get("FLOWCAP_APP_ROOT", r"C:\temp\flowcap_test"))
               / "auto_dm" / "accounts" / (os.environ.get("DY_TEST_ACCOUNT", "") or "") / "profile")
SEL_ITEM = ".conversationConversationItemwrapper"


def pick_img_convs():
    """找确实含图片消息的会话 peer_id（按图片数降序）。"""
    con = sqlite3.connect(str(DB))
    rows = con.execute("""
        SELECT c.peer_id, COUNT(*) n
        FROM dm_messages m JOIN dm_conversations c ON m.conv_id = c.conv_id
        WHERE m.text LIKE '[图片]%'
        GROUP BY c.peer_id ORDER BY n DESC LIMIT 5
    """).fetchall()
    con.close()
    return [(r[0], r[1]) for r in rows]


async def main():
    from auto_dm import config as _cfg
    from auto_dm.vbrowser import launch_async, should_use_vb

    targets = pick_img_convs()
    print("含图片的会话（peer_id, 图片数）:")
    for p, n in targets:
        print(f"  {p}: {n} 张")
    print()

    prof = _P(PROFILE)
    _vb, _vb_mode = should_use_vb(_cfg)
    pw, browser, ctx, backend = await launch_async(
        _vb_mode, _cfg, headless=True, user_data_dir=str(prof), force=False)
    print(f"浏览器启动成功 (backend={backend})\n")

    try:
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()

        snaps = []

        def on_resp(resp):
            try:
                u = resp.url
                if not any(k in u for k in ("douyinpic", "amemv", "bytecdn")):
                    return
                ct = (resp.headers or {}).get("content-type", "")
                if not ct.startswith("image/"):
                    return
                snaps.append({"status": resp.status, "url": u, "ctype": ct})
            except Exception:
                pass

        page.on("response", on_resp)

        await page.goto("https://www.douyin.com/chat?isPopup=1",
                        wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(10000)

        items = await page.query_selector_all(SEL_ITEM)
        print(f"会话列表项: {len(items)} 个")

        # 逐个点会话，找含图片的（图片元素通常带 class 含 image/msg-image）
        found = False
        for idx in range(min(len(items), 8)):
            try:
                items = await page.query_selector_all(SEL_ITEM)
                if idx >= len(items):
                    break
                await items[idx].click(timeout=4000)
                await page.wait_for_timeout(3500)

                # 找消息里的图片元素
                imgs = await page.evaluate(r"""
                () => {
                  const out = [];
                  document.querySelectorAll('img').forEach((im) => {
                    const s = im.currentSrc || im.src || '';
                    if (/aweme-avatar|iesdouyin|bytednsdoc/.test(s)) return;
                    const r = im.getBoundingClientRect();
                    if (r.width > 30 && r.height > 30) {
                      out.push({src: s.slice(0,200), w: Math.round(r.width),
                                h: Math.round(r.height),
                                nw: im.naturalWidth, nh: im.naturalHeight});
                    }
                  });
                  return out;
                }""")
                if imgs:
                    print(f"\n>>> 会话 #{idx} 找到 {len(imgs)} 张消息图片")
                    for im in imgs[:5]:
                        print(f"    显示 {im['w']}×{im['h']} "
                              f"(原始 {im['nw']}×{im['nh']})")
                        print(f"      {im['src'][:120]}")

                    # 点第一张，看是否弹出大图
                    snaps.clear()
                    clicked = await page.evaluate(r"""
                    () => {
                      const ims = [...document.querySelectorAll('img')].filter(
                        (im) => {
                          const s = im.currentSrc || im.src || '';
                          if (/aweme-avatar|iesdouyin|bytednsdoc/.test(s))
                            return false;
                          const r = im.getBoundingClientRect();
                          return r.width > 30 && r.height > 30;
                        });
                      if (!ims.length) return false;
                      ims[0].click();
                      return true;
                    }""")
                    if clicked:
                        print("\n    已点击第一张图片，等待大图弹层...")
                        await page.wait_for_timeout(8000)

                        big = [s for s in snaps
                               if s["status"] == 200
                               and "aweme-avatar" not in s["url"]]
                        print(f"\n    点击后新增图片请求: {len(snaps)} "
                              f"（非头像 {len(big)}）")
                        for s in big[:6]:
                            m = re.search(r"~tplv-[A-Za-z0-9\-_:.]*", s["url"])
                            print(f"      [{s['status']}] {s['ctype']}  "
                                  f"{m.group() if m else '(无tplv)'}")
                            print(f"        {s['url'][:125]}")

                        if big:
                            print("\n    >>> 抓到大图请求！原图可按需获取")
                        else:
                            print("\n    >>> 点击后没有新的图片请求"
                                  "（弹层可能复用已加载的缩略图）")
                    found = True
                    break
            except Exception as e:
                print(f"  会话 #{idx} 处理失败: {type(e).__name__}: {e}")
                continue

        if not found:
            print("\n[!] 前 8 个会话里都没找到消息图片元素")
            print("    可能图片在更下面的会话，或需要更长的渲染时间")

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
