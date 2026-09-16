"""【真机实测·决定性】点进会话，观察抖音自己如何加载私信图片。

前两轮结论：
  1. 消息体里的远程链（[原图] URL）在真机里加载失败（3/3 error，0×0）
  2. 会话列表页只加载头像（aweme-avatar 168×168），没加载私信图片

本轮：真正的验证 —— **点进一个含图片的会话**，观察：
  - 抖音自己请求了哪些图片 URL？
  - 这些 URL 与消息体里的 [原图] URL 是同一套吗？
  - 加载出来的图片实际多大？（判断是不是原图）

这能一次性回答「点击缩略图后显示的原图是哪来的」。

铁律：走项目 vbrowser.launch_async；跑前停应用（profile 锁）。
"""
import asyncio
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path as _P

ROOT = _P(os.environ.get("DY_REPO_ROOT", r"C:\Users\LOX\Desktop\DYchajian\DYAutoDM_v2"))
sys.path.insert(0, str(ROOT / "backend"))

DB = _P(os.environ.get("DY_APP_ROOT", r"C:\temp\dyautodm_test")) / "data" / "dyautodm.db"
PROFILE = str(_P(os.environ.get("DY_APP_ROOT", r"C:\temp\dyautodm_test"))
               / "auto_dm" / "accounts" / (os.environ.get("DY_TEST_ACCOUNT", "") or "") / "profile")

# 会话列表项选择器（与 BCC capture_userinfo_map 里用的一致）
SEL_ITEM = ".conversationConversationItemwrapper"


def pick_target():
    """挑一个含图片的会话 peer_id（用于日志提示，实际点第一个会话）。"""
    con = sqlite3.connect(str(DB))
    row = con.execute(
        "SELECT peer_id FROM dm_conversations "
        "WHERE peer_id IS NOT NULL AND peer_id != '' LIMIT 1").fetchone()
    con.close()
    return row[0] if row else None


async def main():
    from auto_dm import config as _cfg
    from auto_dm.vbrowser import launch_async, should_use_vb

    peer = pick_target()
    print(f"目标会话 peer_id: {peer}\n")

    prof = _P(PROFILE)
    if not prof.exists():
        print(f"[!] profile 不存在: {prof}")
        return

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

        # 点进第一个会话（会渲染它的历史消息，含图片）
        items = await page.query_selector_all(SEL_ITEM)
        print(f"会话列表项: {len(items)} 个")
        if not items:
            print("[!] 没找到会话项")
            return

        snaps.clear()  # 只统计点进会话之后的请求
        await items[0].click(timeout=5000)
        print("已点进第一个会话，等待消息与图片加载...")
        await page.wait_for_timeout(15000)

        # 滚一下触发懒加载
        await page.evaluate(
            "() => { const el = document.querySelector("
            "'.messageContentParent, .chat-message-list, [class*=messageList]');"
            " if (el) el.scrollTop = el.scrollHeight; }")
        await page.wait_for_timeout(6000)

        print(f"\n点进会话后捕获图片响应: {len(snaps)} 个\n")
        print("=" * 74)
        ok = [s for s in snaps if s["status"] == 200]
        print(f"  200 OK: {len(ok)} / {len(snaps)}\n")

        kinds = Counter()
        for s in ok:
            # 区分头像 vs 私信图片
            if "aweme-avatar" in s["url"]:
                kinds["头像 aweme-avatar"] += 1
            else:
                m = re.search(r"~tplv-[A-Za-z0-9\-_:.]*", s["url"])
                kinds[f"私信图片 {m.group() if m else '(无tplv)'}"] += 1
        print("  分类:")
        for k, n in kinds.most_common(10):
            print(f"    {k:44s}: {n}")

        # 重点：非头像的私信图片
        imgs = [s for s in ok if "aweme-avatar" not in s["url"]]
        print()
        print("=" * 74)
        print(f"私信图片（非头像）: {len(imgs)} 个")
        print("=" * 74)
        for s in imgs[:8]:
            m = re.search(r"~tplv-[A-Za-z0-9\-_:.]*", s["url"])
            print(f"  [{s['status']}] {s['ctype']}  {m.group() if m else '(无tplv)'}")
            print(f"     {s['url'][:135]}")

        if not imgs:
            print("  （未捕获到非头像的私信图片）")

        # DOM 里实际显示的图片尺寸
        res = await page.evaluate(r"""
        () => {
          const out = [];
          document.querySelectorAll('img').forEach((im) => {
            const src = im.currentSrc || im.src || '';
            if (im.naturalWidth > 0 && !/aweme-avatar/.test(src)) {
              out.push({src: src.slice(0,150), w: im.naturalWidth,
                        h: im.naturalHeight});
            }
          });
          return out.slice(0, 20);
        }""")
        print()
        print("=" * 74)
        print(f"DOM 里渲染的私信图片: {len(res)} 个")
        print("=" * 74)
        for im in res[:10]:
            print(f"  {im['w']}×{im['h']}")
            print(f"    {im['src'][:125]}")

        if res:
            mx = max(res, key=lambda x: x["w"] * x["h"])
            print(f"\n  最大一张: {mx['w']}×{mx['h']}")
            if mx["w"] > 400 or mx["h"] > 400:
                print("  >>> 存在大图（>400px）—— 抖音确实会加载原图")
            else:
                print("  >>> 全是小图（<=400px）—— 抖音会话里也只显示缩略图")

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
