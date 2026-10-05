"""按 tab 分别统计「标题类元素」（font-weight>=600）的字号与字体族，定位 IM 页差异。"""
import sys, json
from playwright.sync_api import sync_playwright

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5199/preview-pages.html?p=settings"
TABS = ["通用配置", "私信发送", "直播监听", "捕获与存储", "私信 / 昵称兜底",
        "AI 回复引擎", "Agent 与绑定", "配置标签", "采集策略", "通知与指令", "MCP 服务"]

with sync_playwright() as pw:
    b = pw.chromium.launch(args=["--no-sandbox"])
    pg = b.new_page(viewport={"width": 1280, "height": 800})
    pg.goto(URL, wait_until="networkidle", timeout=60000)
    pg.wait_for_timeout(1800)

    for tab in TABS:
        btn = pg.query_selector(f'nav button:has-text("{tab}")')
        if not btn: continue
        btn.click()
        pg.wait_for_timeout(600)
        r = pg.evaluate("""() => {
          const card = document.querySelector('main div[class*="min-w-0"][class*="flex-1"]');
          if (!card) return null;
          const out = [];
          card.querySelectorAll('*').forEach(el => {
            const cs = getComputedStyle(el);
            const w = parseInt(cs.fontWeight) || 400;
            if (w < 600) return;
            // 只取直接含文本的
            const t = [...el.childNodes].filter(n => n.nodeType===3).map(n=>n.textContent).join('').trim();
            if (!t || t.length < 2) return;
            out.push({
              t: t.slice(0, 22),
              fs: cs.fontSize,
              fw: cs.fontWeight,
              ff: cs.fontFamily.split(',')[0].replace(/"/g,''),
              tag: el.tagName,
            });
          });
          return out;
        }""")
        print(f"\n=== {tab} ===")
        seen = set()
        for e in (r or []):
            key = (e["fs"], e["ff"])
            mark = "" if key in seen else "  ←"
            seen.add(key)
            print(f"  {e['fs']:<9} fw={e['fw']:<4} ff={e['ff']:<20} <{e['tag']}> {e['t']}{mark}")
    b.close()
