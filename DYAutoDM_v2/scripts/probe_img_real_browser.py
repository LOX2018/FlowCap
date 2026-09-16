"""【真机实测】抖音私信图片远程链，在指纹浏览器里到底能不能显示？

背景：用户质疑「点击缩略图后显示的原图是哪来的？不管加密不加密总得
在前端显示吧？」—— 这个质疑是对的，我之前的结论可能有误。

**此前的测试有个致命缺陷**：在 BCC 页面测时我设了
`img.crossOrigin = 'anonymous'`，这会强制走 CORS 检查，
CDN 不返回 CORS 头就失败。**但普通 `<img src>`（不带 crossOrigin）
根本不检查 CORS** —— 很可能能正常加载并显示。

本脚本用**项目自己的指纹浏览器**（vbrowser.launch_async，与 BCC 同一套），
在真实抖音页面上下文里，用**普通 <img>（不带 crossOrigin）**加载远程链，
看能否真的显示出来。

铁律：必须走项目 vbrowser.launch_async，禁止裸 Playwright（会 exitCode=21）。
      跑之前必须停应用（BCC 独占 profile 锁）。

用法：
    1. 先停 DYAutoDM 应用（释放 profile 锁）
    2. python scripts/probe_img_real_browser.py
"""
import asyncio
import re
import sqlite3
import sys
from pathlib import Path as _P

ROOT = _P(os.environ.get("DY_REPO_ROOT", r"C:\Users\LOX\Desktop\DYchajian\DYAutoDM_v2"))
BACKEND = ROOT / "backend"
DB = _P(os.environ.get("DY_APP_ROOT", r"C:\temp\dyautodm_test")) / "data" / "dyautodm.db"
ACCOUNT = os.environ.get("DY_TEST_ACCOUNT", "").strip()

sys.path.insert(0, str(BACKEND))

# 关键：不用 crossOrigin，模拟普通 <img> 加载
JS_LOAD = r"""
async (url) => {
  const out = {};
  const img = new Image();
  // 注意：不设 crossOrigin —— 普通 <img> 不检查 CORS
  const ok = await new Promise((resolve) => {
    const t = setTimeout(() => resolve('timeout'), 20000);
    img.onload = () => { clearTimeout(t); resolve('loaded'); };
    img.onerror = () => { clearTimeout(t); resolve('error'); };
    img.src = url;
  });
  out.result = ok;
  out.w = img.naturalWidth || 0;
  out.h = img.naturalHeight || 0;
  out.complete = img.complete;

  // 如果能显示，尝试 canvas 导出（可能因跨域 taint 失败，但至少确认能显示）
  if (ok === 'loaded' && img.naturalWidth > 0) {
    try {
      const cv = document.createElement('canvas');
      cv.width = img.naturalWidth; cv.height = img.naturalHeight;
      cv.getContext('2d').drawImage(img, 0, 0);
      out.canvasOk = true;
      out.dims = [cv.width, cv.height];
    } catch (e) {
      out.canvasOk = false;
      out.canvasErr = String(e).slice(0, 100);
    }
  }
  return out;
}
"""


def pick_urls() -> list[tuple[str, str]]:
    """从库里挑测试 URL：优先 [原图]（无 tplv 尺寸后缀 = 原图）。"""
    con = sqlite3.connect(str(DB))
    rows = con.execute(
        "SELECT text FROM dm_messages WHERE text LIKE '%data:image%' LIMIT 40"
    ).fetchall()
    con.close()

    out = []
    seen = set()
    for (t,) in rows:
        for m in re.finditer(r"\[原图\]\s*(https?://\S+)", t):
            u = m.group(1).rstrip("\\")
            # 取无 tplv 尺寸后缀的（= 原图）
            if "~tplv-x-get:.image" in u and u not in seen:
                seen.add(u)
                out.append(("原图(无tplv后缀)", u))
        for m in re.finditer(r"~tplv-x-get:thumb\.image[^\s\"']*", t):
            pass
    return out[:3]


async def main():
    from auto_dm import accounts as acc
    from auto_dm.vbrowser import launch_async

    urls = pick_urls()
    if not urls:
        print("[!] 库里没找到 [原图] URL")
        return

    print(f"待测试图片: {len(urls)} 张\n")

    # 用项目自己的指纹浏览器启动（与 BCC 同配置）
    # profile 目录：直接用测试环境的已知路径
    # （accounts.env_path_of 走项目根 DB，与测试环境 C:\temp\dyautodm_test 不同）
    prof = _P(r"C:\temp\dyautodm_test\auto_dm\accounts") / ACCOUNT / "profile"
    print(f"profile: {prof}")
    if not prof.exists():
        print(f"[!] profile 不存在: {prof}")
        return

    # 签名与 BCC 一致：launch_async(vb_mode, cfg, headless, user_data_dir, force)
    from auto_dm import config as _cfg
    from auto_dm.vbrowser import should_use_vb

    _vb, _vb_mode = should_use_vb(_cfg)
    print(f"指纹模式: vb={_vb} mode={_vb_mode}")

    pw, browser, ctx, backend = await launch_async(
        _vb_mode, _cfg, headless=True, user_data_dir=str(prof), force=False)
    print(f"浏览器启动成功 (backend={backend})\n")

    try:
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        # 导航到抖音（需要有登录态/域上下文）
        await page.goto("https://www.douyin.com/chat?isPopup=1",
                        wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(5000)
        print(f"当前页面: {page.url}\n")

        any_ok = False
        for label, url in urls:
            print("=" * 66)
            print(f"{label}")
            print(f"  {url[:100]}...")
            print("-" * 66)
            try:
                r = await page.evaluate(JS_LOAD, url)
            except Exception as e:
                print(f"  [!] 执行失败: {e}")
                continue

            res = r.get("result")
            print(f"  加载结果: {res}")
            print(f"  实际尺寸: {r.get('w')} × {r.get('h')}")
            if res == "loaded" and r.get("w", 0) > 0:
                any_ok = True
                print(f"  >>> 图片**能显示**！")
                if r.get("canvasOk"):
                    print(f"      canvas 导出也成功: {r.get('dims')}")
                elif r.get("canvasErr"):
                    print(f"      canvas 导出失败(跨域污染，但不影响显示): "
                          f"{r.get('canvasErr')}")
            print()

        print("=" * 66)
        if any_ok:
            print(">>> 结论：远程链**能**在浏览器里正常显示！")
            print("    之前「加密/破损图标」的结论不成立。")
            print("    失败原因是 crossOrigin 强制 CORS 检查，不是加密。")
            print("    → 前端可直接 <img src=远程链> 显示原图。")
        else:
            print(">>> 结论：真机浏览器里也加载失败（确实加密或需鉴权）")
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
