"""真实浏览器验证：配置中心切 Tab 是否还有横向跳脱（2026-10-04）。

判据（机械化）：
  A. 切换各 tab 前后，PageContainer 的左边缘 x 是否恒定（允许 ±0.5px）
  B. main 的 clientWidth 在各 tab 下是否恒定
  C. Card（右侧内容区）的左边缘 x 与宽度在各 tab 下是否恒定
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
    pg.wait_for_timeout(1500)

    def measure():
        return pg.evaluate("""() => {
          const main = document.querySelector('main');
          const pc = document.querySelector('main > div > div[style*="max-width"]');
          const card = document.querySelector('main [class*="card-surface"], main .rounded-\\\\[var\\\\(--radius-md\\\\)\\\\]');
          const r = (el) => el ? {x: +el.getBoundingClientRect().x.toFixed(1), w: +el.getBoundingClientRect().width.toFixed(1)} : null;
          return {
            mainClientW: main ? main.clientWidth : null,
            mainScrollW: main ? main.scrollWidth : null,
            mainHasVScroll: main ? main.scrollHeight > main.clientHeight : null,
            pageContainer: r(pc),
            card: r(card),
            docScrollW: document.documentElement.scrollWidth,
            docClientW: document.documentElement.clientWidth,
          };
        }""")

    print("=== 初始（通用配置）===")
    base = measure()
    print(json.dumps(base, ensure_ascii=False))

    results = {}
    for tab in TABS:
        btn = pg.query_selector(f'nav button:has-text("{tab}")')
        if not btn:
            print(f"  跳过（找不到）: {tab}")
            continue
        btn.click()
        pg.wait_for_timeout(700)
        m = measure()
        results[tab] = m
        pcx = m["pageContainer"]["x"] if m["pageContainer"] else None
        pcw = m["pageContainer"]["w"] if m["pageContainer"] else None
        print(f"  {tab:<16} main.clientW={m['mainClientW']} 滚动={m['mainHasVScroll']}  PC.x={pcx} PC.w={pcw}  card.x={m['card']['x'] if m['card'] else None} card.w={m['card']['w'] if m['card'] else None}  doc溢出={m['docScrollW']-m['docClientW']}")

    # 判据
    print("\n=== 判据 ===")
    xs = [m["pageContainer"]["x"] for m in results.values() if m["pageContainer"]]
    ws = [m["pageContainer"]["w"] for m in results.values() if m["pageContainer"]]
    cxs = [m["card"]["x"] for m in results.values() if m["card"]]
    cws = [m["card"]["w"] for m in results.values() if m["card"]]
    mainws = [m["mainClientW"] for m in results.values()]

    def span(v): return (max(v) - min(v)) if v else None
    print(f"PageContainer.x 极差 = {span(xs):.1f}px  (应 ≤ 0.5)")
    print(f"PageContainer.w 极差 = {span(ws):.1f}px  (应 ≤ 0.5)")
    print(f"Card.x 极差 = {span(cxs):.1f}px  (应 ≤ 0.5)")
    print(f"Card.w 极差 = {span(cws):.1f}px  (应 ≤ 0.5)")
    print(f"main.clientWidth 极差 = {span(mainws):.1f}px  (应 = 0)")
    ok = span(xs) <= 0.5 and span(ws) <= 0.5 and span(cxs) <= 0.5 and span(cws) <= 0.5 and span(mainws) == 0
    print(f"\n{'✅ 无跳脱' if ok else '❌ 仍跳脱'}")
    b.close()
    sys.exit(0 if ok else 1)
