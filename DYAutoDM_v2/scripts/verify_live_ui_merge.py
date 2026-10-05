# -*- coding: utf-8 -*-
"""真实渲染验证：直播页评论统计卡（AI 开关留卡头 / 计数下行 / 删配置块）+ 高价值关键词 + 动作靠右。

对应用户 2026-09-29 第二轮指令（三张标注图）：
  图一：账号切换段 与 策略下拉+三按钮 两处动作组都要**贴卡片最右缘**；
  图二：「已回复/留资/错误」三个计数从卡头**下移到正文统计行**（AI 状态与开关留在卡头）；
  图三：「生效的自动私信配置」**整块删除**（含配置内容）。

判据全部读页面内真实 DOM 与几何量（getBoundingClientRect）：

  A1 comment-stats 卡头 actions 内出现 AI 状态+开关（[live-ai-reply-controls]/[ai-reply-toggle]）
  A2 卡头**不含**「已回复/留资/错误」计数（已下移）
  A3 [live-ai-counts] 存在，且**不在**卡头内、而在正文统计行内（含「条弹幕记录」）
  R1 「生效的自动私信配置」整块已删除：[live-auto-dm]/[comment-stats-configs]/
     [live-ai-dm-state] 全部不存在，页面文案也不含「生效的自动私信配置」
  M4 「高价值关键词」按钮与「管理策略」并列，点开弹窗、关闭即消失
  N1 账号动作组 与 策略动作组 右边缘对齐（都贴卡片内容右缘，两者差 < 6px）

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
    cwd=FE, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
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

try:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1360, "height": 960})
        page.goto(BASE + "/preview-pages.html?p=live", wait_until="domcontentloaded")
        page.wait_for_timeout(2800)

        # ── A / R：评论统计卡 ──────────────────────────────────────────
        check("A1a comment-stats 卡已渲染",
              page.evaluate("!!document.querySelector('[data-od-id=\"comment-stats\"]')"), "")
        check("A1b 卡头 actions 内出现 AI 开关（[ai-reply-toggle]）",
              page.evaluate("!!document.querySelector('[data-od-id=\"comment-stats-actions\"] "
                            "[data-od-id=\"ai-reply-toggle\"]')"),
              "AI 开关未在卡头")
        check("A1c 卡头 actions 内出现 AI 状态胶囊（[live-ai-reply-controls]）",
              page.evaluate("!!document.querySelector('[data-od-id=\"comment-stats-actions\"] "
                            "[data-od-id=\"live-ai-reply-controls\"]')"),
              "AI 状态未在卡头")
        check("A2 卡头**不含**「已回复/留资/错误」计数（已下移）",
              page.evaluate("""(() => {
                const h = document.querySelector('[data-od-id="comment-stats"] > div:nth-of-type(1)');
                return !!h && !(h.innerText || '').includes('已回复');
              })()"""),
              "卡头仍含「已回复」计数")

        check("A3a [live-ai-counts] 存在",
              page.evaluate("!!document.querySelector('[data-od-id=\"live-ai-counts\"]')"), "")
        check("A3b 计数**不在**卡头内（确实下移到正文）",
              page.evaluate("""(() => {
                const c = document.querySelector('[data-od-id="live-ai-counts"]');
                const h = document.querySelector('[data-od-id="comment-stats-actions"]');
                return !!c && !!h && !h.contains(c);
              })()"""),
              "计数仍在卡头")
        check("A3c 计数位于正文统计行（向上 6 层内可见「条弹幕记录」）",
              page.evaluate("""(() => {
                const c = document.querySelector('[data-od-id="live-ai-counts"]');
                if (!c) return false;
                let n = c;
                for (let i = 0; i < 6 && n; i++) {
                  if ((n.innerText || '').includes('条弹幕记录')) return true;
                  n = n.parentElement;
                }
                return false;
              })()"""),
              "计数不在统计行内")

        check("R1a [live-auto-dm] 整块已删除",
              page.evaluate("!document.querySelector('[data-od-id=\"live-auto-dm\"]')"),
              "仍存在自动私信配置块")
        check("R1b [comment-stats-configs] 宿主已删除",
              page.evaluate("!document.querySelector('[data-od-id=\"comment-stats-configs\"]')"),
              "仍存在配置宿主")
        check("R1c [live-ai-dm-state] 已删除 + 文案不含「生效的自动私信配置」",
              page.evaluate("""(() => {
                if (document.querySelector('[data-od-id="live-ai-dm-state"]')) return false;
                return !((document.body.innerText || '').includes('生效的自动私信配置'));
              })()"""),
              "仍存在 AI 文案横幅或标题文案")

        # ── E：卡副标题清理（A1/A2 删除；B1/B2 保留）───────────────────────
        check("E1 账号卡 description 已删除（'新手教程'式说明）",
              page.evaluate("""(() => {
                const p = document.querySelector('[data-od-id="live-acct-select"] '
                  + '> div:nth-of-type(1) > div.flex.flex-wrap.items-center > div.min-w-0 > p');
                return !p;
              })()"""),
              "账号卡仍存在 description <p>")
        check("E2 直播间卡 description 已删除",
              page.evaluate("""(() => {
                const p = document.querySelector('[data-od-id="live-input"] '
                  + '> div:nth-of-type(1) > div > div.min-w-0 > p');
                return !p;
              })()"""),
              "直播间卡仍存在 description <p>")
        check("E3 实时信息流 description 保留（B1 范围图例）",
              page.evaluate("""(() => {
                const p = document.querySelector('[data-od-id="live-feed"] '
                  + '> div:nth-of-type(1) > div > div.min-w-0 > p');
                return !!p && (p.textContent || '').includes('弹幕');
              })()"""),
              "feed 范围图例被误删")
        check("E4 房间热度 description 保留（B2 动态读数，含「人在线」）",
              page.evaluate("""(() => {
                const p = document.querySelector('[data-od-id="heat-chart"] '
                  + '> div:nth-of-type(1) > div.flex.flex-wrap.items-center > div.min-w-0 > p');
                return !!p && (p.textContent || '').includes('人在线');
              })()"""),
              "heat 在线人数读数被误删")

        # ── M4：高价值关键词按钮 + 弹窗 ────────────────────────────────
        check("M4a 「高价值关键词」按钮与「管理策略」并列（同在直播间区）",
              page.evaluate("""(() => {
                const b = document.querySelector('[data-od-id="live-high-value-keywords"]');
                if (!b) return false;
                const area = b.closest('[data-od-id="live-input"]');
                return !!area && !!area.querySelector('[data-od-id="live-room-configs"]');
              })()"""),
              "按钮不存在或不在直播间区")
        check("M4b 弹窗初始不渲染",
              page.evaluate("!document.querySelector('[data-od-id=\"high-value-keywords-modal\"]')"), "")
        page.evaluate("document.querySelector('[data-od-id=\"live-high-value-keywords\"]').click()")
        page.wait_for_timeout(900)
        check("M4c 点击后弹出权重表弹窗（含标题文案）",
              page.evaluate("""(() => {
                const m = document.querySelector('[data-od-id="high-value-keywords-modal"]');
                return !!m && (m.innerText || '').includes('高价值关键词权重');
              })()"""),
              "弹窗未出现或缺标题")
        page.evaluate("""(() => {
          const m = document.querySelector('[data-od-id="high-value-keywords-modal"]');
          const x = m && m.querySelector('button[aria-label="关闭"]');
          if (x) x.click();
        })()""")
        page.wait_for_timeout(500)
        check("M4d 点关闭后弹窗消失",
              page.evaluate("!document.querySelector('[data-od-id=\"high-value-keywords-modal\"]')"), "")

        # ── N1：两组动作都贴卡片最右缘 ─────────────────────────────────
        geom = page.evaluate("""(() => {
          const acct = document.querySelector('[data-od-id="live-acct-tabs"]');
          const acctCard = document.querySelector('[data-od-id="live-acct-select"]');
          const acctHead = acctCard && acctCard.querySelector(':scope > div:nth-of-type(1)');
          const area = document.querySelector('[data-od-id="live-input"]');
          const inp = area && area.querySelector('div.flex.shrink-0.items-center');
          const inpHead = area && area.querySelector(':scope > div:nth-of-type(1)');
          if (!acct || !inp || !acctHead || !inpHead) return null;
          const R = (e) => e.getBoundingClientRect();
          return {
            acctRight: R(acct).right, acctHeadRight: R(acctHead).right,
            acctCardRight: R(acctCard).right, acctCardLeft: R(acctCard).left,
            inpRight: R(inp).right, inpHeadRight: R(inpHead).right,
            inpCardRight: R(area).right, inpCardLeft: R(area).left,
            acctHasMlAuto: !!(acct.parentElement && acct.parentElement.className.includes('ml-auto')),
            inpHasMlAuto: !!(inp.className || '').includes('ml-auto'),
          };
        })()""")
        print("    [几何 · 1360px] ", geom)
        check("N1a 两组动作存在且动作包装层带 ml-auto（Section 靠右根因修复在位）",
              isinstance(geom, dict) and geom.get("acctHasMlAuto") and geom.get("inpHasMlAuto"),
              f"g={geom}")
        check("N1b 账号动作组与策略动作组右边缘对齐（差 < 3px）",
              isinstance(geom, dict) and abs(geom["acctRight"] - geom["inpRight"]) < 3,
              f"acctRight={geom and geom.get('acctRight')} inpRight={geom and geom.get('inpRight')}")
        check("N1c 两组都贴卡片内容右缘（落在右侧 25px 带内）",
              isinstance(geom, dict)
              and geom["acctRight"] > geom["acctCardRight"] - 25
              and geom["inpRight"] > geom["inpCardRight"] - 25,
              f"acct={geom and geom.get('acctRight')}/{geom and geom.get('acctCardRight')} "
              f"inp={geom and geom.get('inpRight')}/{geom and geom.get('inpCardRight')}")

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
