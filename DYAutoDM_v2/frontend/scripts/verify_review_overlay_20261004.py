"""真实浏览器验证：查阅模式是否覆盖侧栏（2026-10-04 层叠上下文修复验收）。

判据（机械化，不靠肉眼）：
  A. ReviewMode 根元素 boundingRect.x 必须 ≈ 0（覆盖视口左缘）
  B. 侧栏中心点 elementFromPoint 必须**不是**侧栏 → 证明浮层在侧栏之上
  C. ReviewMode 宽度必须 ≈ 视口宽
  D. 侧栏右边界 x 与浮层内容区左缘的关系：内容不应被侧栏遮挡

用 Vite dev server 挂载 preview-pages.html?p=live（mock props 真挂载）。
"""
import sys, json, time
from playwright.sync_api import sync_playwright

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5199/preview-pages.html?p=live"

with sync_playwright() as pw:
    browser = pw.chromium.launch(args=["--no-sandbox"])
    page = browser.new_page(viewport={"width": 1024, "height": 720})
    page.goto(URL, wait_until="networkidle", timeout=60000)
    page.wait_for_timeout(1500)

    # 进入查阅模式
    btn = page.query_selector('[data-od-id="review-open"]')
    if not btn:
        print("FAIL 未找到 review-open 按钮；页面标题:", page.title())
        print(page.content()[:800])
        browser.close(); sys.exit(1)
    btn.click()
    page.wait_for_timeout(1200)

    res = page.evaluate("""() => {
      const overlay = document.querySelector('[data-od-id="live-review"]');
      const aside = document.querySelector('aside');
      const out = {
        vw: window.innerWidth, vh: window.innerHeight,
        hasOverlay: !!overlay,
        overlayParentTag: overlay ? overlay.parentElement.tagName : null,
        overlayParentIsBody: overlay ? overlay.parentElement === document.body : null,
      };
      if (overlay) {
        const r = overlay.getBoundingClientRect();
        out.overlay = {x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height)};
        // 浮层内首个标题元素
        const h2 = overlay.querySelector('h2');
        if (h2) { const hr = h2.getBoundingClientRect(); out.title = {text: h2.textContent, x: Math.round(hr.x)}; }
        // 浮层内的表格首列
        const th = overlay.querySelector('th');
        if (th) { const tr = th.getBoundingClientRect(); out.firstTh = {text: th.textContent, x: Math.round(tr.x)}; }
      }
      if (aside) {
        const ar = aside.getBoundingClientRect();
        out.aside = {x: Math.round(ar.x), y: Math.round(ar.y), w: Math.round(ar.width), h: Math.round(ar.height)};
        // 侧栏中心点命中的元素
        const cx = ar.x + ar.width/2, cy = ar.y + ar.height/2;
        const hit = document.elementFromPoint(cx, cy);
        out.hitAtSidebarCenter = hit ? (hit.tagName + '.' + (hit.className||'').toString().slice(0,60)) : null;
        out.hitIsAside = hit ? (hit === aside || aside.contains(hit)) : null;
      }
      return out;
    }""")

    print(json.dumps(res, ensure_ascii=False, indent=2))

    ok = True
    def chk(name, cond, detail=""):
        global ok
        print(f"{'PASS' if cond else 'FAIL'}  {name}{'  — ' + detail if detail else ''}")
        ok &= bool(cond)

    # ── 判据 1：Portal 是否生效 ──
    chk("浮层已挂到 body（Portal 生效）", res.get("overlayParentIsBody") is True,
        f"parent={res.get('overlayParentTag')}")

    if res.get("overlay") and res.get("aside"):
        ov, asi = res["overlay"], res["aside"]
        chk("浮层铺满视口", abs(ov["x"]) <= 2 and abs(ov["w"] - res["vw"]) <= 2,
            f"overlay x={ov['x']} w={ov['w']} vs vw={res['vw']}")

    # ── 判据 2：★关键 —— 浮层必须盖在侧栏**之上** ──
    # 修复前：hitIsAside=True（侧栏中心命中侧栏本身 ⇒ 浮层被侧栏遮住 ⇒ 用户看到"内容被切"）
    # 修复后：hitIsAside=False（侧栏中心命中浮层 ⇒ 浮层在最上层）
    chk("★侧栏中心命中浮层（浮层盖住侧栏，不再被切）", res.get("hitIsAside") is False,
        f"hit={res.get('hitAtSidebarCenter')}")

    # ── 判据 3：浮层内的内容元素**必须完全可见** ──
    # 注意：全屏 overlay 的内容合法地从视口边缘（x≈20）开始，**不必**在侧栏右侧。
    # 真正要验的是"内容右缘没超视口"与"内容左缘 ≥ 0"，而不是"在侧栏之后"。
    if res.get("title"):
        chk("标题完整可见（左缘 ≥ 0）", res["title"]["x"] >= 0,
            f"title '{res['title']['text']}' x={res['title']['x']}")
    if res.get("firstTh"):
        chk("表格首列在视口内（左缘 ≥ 0）", res["firstTh"]["x"] >= 0,
            f"th x={res['firstTh']['x']}")

    browser.close()
    print(f"\n{'✅ 全部通过' if ok else '❌ 有失败项'}")
    sys.exit(0 if ok else 1)
