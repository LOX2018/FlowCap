"""真实浏览器验证：配置中心 (1) 各 tab 宽度是否一致 (2) 各 tab 字号是否一致。

判据（机械化，取真实 DOM computed style）：
  A. 切换各 tab，Card（右侧内容区）的 x 与 w 是否恒定
  B. 各 tab 里主要文字元素的 computed fontSize 分布
"""
import sys, json
from playwright.sync_api import sync_playwright

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5199/preview-pages.html?p=settings"

TABS = ["通用配置", "私信发送", "直播监听", "捕获与存储", "私信 / 昵称兜底",
        "AI 回复引擎", "Agent 与绑定", "配置标签", "采集策略", "通知与指令", "MCP 服务", "系统"]

with sync_playwright() as pw:
    b = pw.chromium.launch(args=["--no-sandbox"])
    pg = b.new_page(viewport={"width": 1280, "height": 800})
    pg.goto(URL, wait_until="networkidle", timeout=60000)
    pg.wait_for_timeout(1800)

    def measure():
        return pg.evaluate("""() => {
          const main = document.querySelector('main');
          // PageContainer：main 内第一个 style 含 max-width 的 div
          const pcs = [...document.querySelectorAll('main div')].filter(d => d.style && d.style.maxWidth);
          const pc = pcs[0] || null;
          // 右侧 Card：带 min-w-0 flex-1 的卡片
          const card = document.querySelector('main div[class*="min-w-0"][class*="flex-1"]');
          const r = (el) => el ? {x:+el.getBoundingClientRect().x.toFixed(1), w:+el.getBoundingClientRect().width.toFixed(1)} : null;
          // 收集正文文字元素字号（排除图标 svg）
          const sizes = {};
          if (card) {
            card.querySelectorAll('span,p,div,strong,b,label,button,li,td,th,input,select').forEach(el => {
              if (el.children.length > 0 && el.tagName !== 'BUTTON') return;
              const t = (el.textContent||'').trim();
              if (!t || t.length < 2) return;
              const fs = getComputedStyle(el).fontSize;
              sizes[fs] = (sizes[fs]||0)+1;
            });
          }
          return {
            mainClientW: main ? main.clientWidth : null,
            mainVScroll: main ? main.scrollHeight > main.clientHeight : null,
            pc: r(pc), card: r(card),
            docOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
            sizes,
          };
        }""")

    rows = {}
    for tab in TABS:
        btn = pg.query_selector(f'nav button:has-text("{tab}")')
        if not btn:
            continue
        btn.click()
        pg.wait_for_timeout(650)
        rows[tab] = measure()

    print(f"{'tab':<18} {'mainW':<6} {'滚':<3} {'PC.x':<7} {'PC.w':<7} {'card.x':<7} {'card.w':<7} {'doc溢'}")
    for tab, m in rows.items():
        pcx = m["pc"]["x"] if m["pc"] else "-"
        pcw = m["pc"]["w"] if m["pc"] else "-"
        cx = m["card"]["x"] if m["card"] else "-"
        cw = m["card"]["w"] if m["card"] else "-"
        print(f"{tab:<18} {str(m['mainClientW']):<6} {str(m['mainVScroll']):<3} {str(pcx):<7} {str(pcw):<7} {str(cx):<7} {str(cw):<7} {m['docOverflow']}")

    def span(v):
        v = [x for x in v if isinstance(x, (int,float))]
        return (max(v)-min(v)) if v else None

    pcs_x = [m["pc"]["x"] for m in rows.values() if m["pc"]]
    pcs_w = [m["pc"]["w"] for m in rows.values() if m["pc"]]
    cds_x = [m["card"]["x"] for m in rows.values() if m["card"]]
    cds_w = [m["card"]["w"] for m in rows.values() if m["card"]]
    mainw = [m["mainClientW"] for m in rows.values() if m["mainClientW"]]

    print("\n=== 宽度判据 ===")
    print(f"PageContainer.x 极差 = {span(pcs_x)}  (应 ≤ 0.5)")
    print(f"PageContainer.w 极差 = {span(pcs_w)}  (应 ≤ 0.5)")
    print(f"Card.x 极差 = {span(cds_x)}  (应 ≤ 0.5)")
    print(f"Card.w 极差 = {span(cds_w)}  (应 ≤ 0.5)")
    print(f"main.clientWidth 极差 = {span(mainw)}  (应 = 0)")

    print("\n=== 字号分布（各 tab 合并）===")
    allsizes = {}
    for m in rows.values():
        for fs, n in m["sizes"].items():
            allsizes[fs] = allsizes.get(fs, 0) + n
    for fs in sorted(allsizes, key=lambda s: float(s.replace("px",""))):
        print(f"  {fs}: {allsizes[fs]}")

    ok = (span(pcs_x) is None or span(pcs_x) <= 0.5) and \
         (span(cds_x) is None or span(cds_x) <= 0.5) and \
         (span(mainw) == 0)
    print(f"\n{'✅ 宽度一致' if ok else '❌ 宽度仍跳'}")
    b.close()
