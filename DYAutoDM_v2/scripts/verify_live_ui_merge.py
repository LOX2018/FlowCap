# -*- coding: utf-8 -*-
"""真实渲染验证：直播页「评论统计卡」融合 + 高价值关键词按钮 + 账号卡靠右。

背景（用户 2026-09-29 指令）：
  ① 把 AI 自动回复开关、以及「生效的自动私信配置」并入 `[data-od-id="comment-stats"]`；
  ② 高价值关键词的按钮方案融入 `[data-od-id="live-room-configs"]`；
  ③ `[data-od-id="live-acct-select"]` 的动作段靠右分布。

本脚本走**真实渲染**（vite + 真实组件 + 预览 harness 的 mock api），判据全部读页面内
真实 DOM 与几何量（getBoundingClientRect），不靠截图、不靠 console 猜测：

  M1 comment-stats 卡存在，且卡头 actions 内出现 AI 开关（[ai-reply-toggle]）
  M2 正文宿主 [comment-stats-configs] 内出现整块 [live-auto-dm]（AI 横幅 + 概览 + 词库）
  M3 该配置块位于**表格上方**（几何：configs.top < table.top）
  M4 「高价值关键词」按钮与「管理策略」并列，点击弹出权重表弹窗
  N1 账号卡动作段靠右：换行场景（窄视口）下仍贴右（gapLeft 明显 > gapRight）
      —— 该判据是 ml-auto 修复的**可证伪**门禁（去掉 ml-auto 会变红）

跑法：python scripts/verify_live_ui_merge.py
"""
import os
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FE = os.path.join(ROOT, "frontend")
PORT = 5198
BASE = f"http://127.0.0.1:{PORT}"

PASS = FAIL = 0
FAILED = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        FAILED.append(name)
        print(f"  FAIL {name}  {detail}")


env = dict(os.environ)
proc = subprocess.Popen(
    ["npx", "vite", "--port", str(PORT), "--strictPort"],
    cwd=FE, env=env,
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    shell=(os.name == "nt"),
)

ready = False
for _ in range(60):
    time.sleep(1)
    try:
        with urllib.request.urlopen(BASE + "/preview-pages.html", timeout=3) as r:
            if r.status == 200:
                ready = True
                break
    except Exception:
        pass

if not ready:
    proc.kill()
    print("FAIL: vite 未在 60s 内就绪")
    sys.exit(1)
print(f"vite 就绪：{BASE}\n")

GAPS_JS = """(() => {
  const el = document.querySelector('[data-od-id="live-acct-tabs"]');
  if (!el) return null;
  const wrap = el.parentElement;                 // Section 的 actions 包装层（ml-auto 在此）
  const head = wrap.parentElement;               // Section head（flex flex-wrap justify-between）
  const titleBlk = head.firstElementChild;       // 标题/说明块
  const wr = wrap.getBoundingClientRect();
  const tr = titleBlk.getBoundingClientRect();
  const hr = head.getBoundingClientRect();
  return {
    hasMlAuto: wrap.className.includes('ml-auto'),
    wrapTop: wr.top, wrapRight: wr.right, wrapLeft: wr.left,
    headLeft: hr.left, headRight: hr.right,
    gapLeft: wr.left - hr.left,
    gapRight: hr.right - wr.right,
    wrapped: wr.top >= (tr.bottom - 2),          // 换行：动作组另起一行
    headClass: head.className,
  };
})()"""

try:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1360, "height": 960})
        page.goto(BASE + "/preview-pages.html?p=live", wait_until="domcontentloaded")
        # 等 React 渲染 + 各 useQuery 落定
        page.wait_for_timeout(2800)

        # ── M1/M2/M3：评论统计卡的融合 ─────────────────────────────────
        has_card = page.evaluate(
            "!!document.querySelector('[data-od-id=\"comment-stats\"]')")
        check("M1a comment-stats 卡已渲染", has_card, "未找到评论统计卡")

        check("M1b 卡头 actions 内出现 AI 回复开关（portal 生效）",
              page.evaluate(
                  "!!document.querySelector('[data-od-id=\"comment-stats-actions\"] "
                  "[data-od-id=\"ai-reply-toggle\"]')"),
              "AI 开关未进 comment-stats 卡头")
        check("M1c 旧独立卡 [live-ai-reply] 已移除",
              page.evaluate("!document.querySelector('[data-od-id=\"live-ai-reply\"]')"),
              "旧 AI 卡仍存在")

        check("M2a 正文宿主 [comment-stats-configs] 存在",
              page.evaluate("!!document.querySelector('[data-od-id=\"comment-stats-configs\"]')"),
              "未找到配置宿主")
        check("M2b 宿主内出现整块 [live-auto-dm]（自动私信配置）",
              page.evaluate(
                  "!!document.querySelector('[data-od-id=\"comment-stats-configs\"] "
                  "[data-od-id=\"live-auto-dm\"]')"),
              "自动私信配置未并入")
        check("M2c 配置块内含 AI 文案生效横幅 / 私信词库标题",
              page.evaluate("""(() => {
                const h = document.querySelector('[data-od-id="comment-stats-configs"]');
                if (!h) return false;
                const t = h.innerText || '';
                return t.includes('生效的自动私信配置') || t.includes('私信词库') || t.includes('AI 文案');
              })()"""),
              "配置块内容缺失")

        geom = page.evaluate("""(() => {
          const cfg = document.querySelector('[data-od-id="comment-stats-configs"]');
          const tbl = document.querySelector('[data-od-id="comment-stats"] table');
          if (!cfg || !tbl) return null;
          return { cfgTop: cfg.getBoundingClientRect().top,
                   tblTop: tbl.getBoundingClientRect().top };
        })()""")
        check("M3 配置块位于评论表格**上方**",
              isinstance(geom, dict) and geom["cfgTop"] < geom["tblTop"],
              f"geom={geom}")

        # ── M4：高价值关键词按钮 + 弹窗 ────────────────────────────────
        check("M4a 「高价值关键词」按钮与「管理策略」并列（同在直播间区 actions）",
              page.evaluate("""(() => {
                const b = document.querySelector('[data-od-id="live-high-value-keywords"]');
                if (!b) return false;
                const area = b.closest('[data-od-id="live-input"]');
                if (!area) return false;
                return !!area.querySelector('[data-od-id="live-room-configs"]');
              })()"""),
              "按钮不存在或不在直播间区")
        check("M4b 弹窗初始不渲染",
              page.evaluate("!document.querySelector('[data-od-id=\"high-value-keywords-modal\"]')"),
              "弹窗默认就存在")
        page.evaluate("document.querySelector('[data-od-id=\"live-high-value-keywords\"]').click()")
        page.wait_for_timeout(900)
        check("M4c 点击后弹出权重表弹窗（存在 + 含标题文案）",
              page.evaluate("""(() => {
                const m = document.querySelector('[data-od-id="high-value-keywords-modal"]');
                if (!m) return false;
                return (m.innerText || '').includes('高价值关键词权重');
              })()"""),
              "弹窗未出现或缺标题")
        page.evaluate("""(() => {
          const m = document.querySelector('[data-od-id="high-value-keywords-modal"]');
          const x = m && m.querySelector('button[aria-label="关闭"]');
          if (x) x.click();
        })()""")
        page.wait_for_timeout(500)
        check("M4d 点关闭后弹窗消失",
              page.evaluate("!document.querySelector('[data-od-id=\"high-value-keywords-modal\"]')"),
              "弹窗未关闭")

        # ── N1：账号卡动作段靠右（换行场景）────────────────────────────
        g_wide = page.evaluate(GAPS_JS)
        print("    [几何 · 1360px] ", g_wide)
        check("N1a 账号卡动作包装层带 ml-auto（修复已在位）",
              isinstance(g_wide, dict) and g_wide.get("hasMlAuto"),
              f"g={g_wide}")

        page.set_viewport_size({"width": 620, "height": 960})
        page.wait_for_timeout(700)
        g_narrow = page.evaluate(GAPS_JS)
        print("    [几何 ·  620px] ", g_narrow)
        if isinstance(g_narrow, dict) and g_narrow.get("wrapped"):
            check("N1b 换行时动作段仍贴右（gapLeft 明显 > gapRight）",
                  g_narrow["gapLeft"] > g_narrow["gapRight"] + 40,
                  f"gapLeft={g_narrow['gapLeft']:.0f} gapRight={g_narrow['gapRight']:.0f}")
        else:
            # 未换行时同样应贴右
            check("N1b 未换行时动作段贴右（gapRight 很小）",
                  isinstance(g_narrow, dict) and g_narrow["gapRight"] < 40,
                  f"g={g_narrow}")

        browser.close()
except Exception as e:
    check("UI 层可运行", False, f"{type(e).__name__}: {e}")
finally:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except Exception:
        proc.kill()

print(f"\nPASS={PASS} FAIL={FAIL}")
if FAILED:
    print("失败项：")
    for x in FAILED:
        print("  -", x)
sys.exit(1 if FAIL else 0)
