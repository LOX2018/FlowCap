# -*- coding: utf-8 -*-
"""真实渲染验证：策略弹窗「＋ 新建策略」点击必须可见生效。

背景（用户实测反馈）：
  ① `[data-od-id="strategy-new"]` 点击后**没有任何变化**；
  ② 该按钮应在 `live-strategy-modal` 页脚、且排在「保存/更新策略」按钮**左侧**。

根因（执行链追踪）：弹窗每次打开时 useEffect 已把表单置为 EMPTY_DRAFT + editing=null，
而原「新建」做的是同一件事 ⇒ 默认态点击是幂等空操作，用户看不到任何变化。

本脚本走**真实渲染**（vite dev + 真实组件 + 按真实 API 契约拦截），
判据全部读页面内真实 DOM，不靠截图、不靠 console 猜测：

  R1 按钮存在于弹窗页脚，且位于「保存/更新策略」左侧
  R2 默认态点击 → 触发 push 反馈 + **列表区出现草稿行**（用户指明的位置必须变）
  R2b 再次点击 → 取消新建（草稿行消失、按钮文案回退）—— 连点也必须有可见变化
  R3 点「编辑」进入编辑态（标题出现「编辑中」）→ 再点「新建」
     ⇒ 标题回到「新建」、表单被清空、列表中编辑标记消失
  R4 编辑态点击「新建」后，保存按钮文案由「更新策略」变为「保存策略」

跑法：python scripts/verify_live_strategy_ui.py   （自动起/停 vite，用 5199 端口）
"""
import os
import re
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FE = os.path.join(ROOT, "frontend")
PORT = 5199
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


# ── 起 vite dev ────────────────────────────────────────────────────────────
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
    print("FAIL: vite dev 未在 60s 内就绪")
    sys.exit(1)
print(f"vite dev 就绪： {BASE}\n")

try:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()

        # ① 按**真实契约形状**拦截 API（组件 import 的是真实 api 单例，mock props 不生效）
        strategies = [
            {"id": "lc_demo1", "name": "标准-快速", "max_target": 50, "interval": 45,
             "delay": "40,80", "acct": "主账号_A", "auto_link_mic": True,
             "link_mic_mode": "audio", "dm_pool": []},
            {"id": "lc_demo2", "name": "保守-慢速", "max_target": 8, "interval": 300,
             "delay": "300,600", "acct": None, "auto_link_mic": False,
             "link_mic_mode": "audio", "dm_pool": []},
        ]

        def route_handler(route):
            # 只拦策略接口；其余 /api 一律放行（广撒网拦截会打爆启动门禁 → 页面空白）
            if route.request.method == "GET":
                route.fulfill(status=200, content_type="application/json",
                              body=__import__("json").dumps({"ok": True, "items": strategies}))
            else:
                route.fulfill(status=200, content_type="application/json",
                              body='{"ok":true,"config":{"id":"lc_demo1","name":"x"}}')

        page.route("**/api/live/config-tags**", route_handler)

        # 页面内的 toast 计数探针（真实 app 的 push 会 setToasts → 渲染 toast）
        page.goto(BASE + "/preview-pages.html", wait_until="domcontentloaded")
        page.wait_for_timeout(2500)

        # 切到直播页
        page.evaluate("""(() => {
          const btns = [...document.querySelectorAll('button,a')]
            .filter(b => (b.textContent||'').trim() === '直播');
          btns.forEach(b => b.click());
        })()""")
        page.wait_for_timeout(1500)
        # 直播页默认即「单账户」视图（viewMode 初值 "single"），策略入口直接可见
        has_entry = page.evaluate("!!document.querySelector('[data-od-id=\"live-room-configs\"]')")
        check("R-1 单账户视图下策略入口可见（默认视图，无需切换）", has_entry,
              "未找到策略入口")
        # 打开策略弹窗
        opened = page.evaluate("""(() => {
          const b = document.querySelector('[data-od-id="live-room-configs"]');
          if (!b) return false;
          b.click();
          return true;
        })()""")
        page.wait_for_timeout(1200)

        modal = page.locator('[data-od-id="live-strategy-modal"]')
        check("R0 策略弹窗已打开", opened and modal.count() > 0,
              f"opened={opened} modal={modal.count()}")

        newbtn = page.locator('[data-od-id="strategy-new"]')
        check("R1 存在「＋ 新建策略」按钮", newbtn.count() == 1, f"count={newbtn.count()}")

        # R1b 位于页脚、且在保存按钮左侧（用真实几何 x 坐标判定）
        geo = page.evaluate("""(() => {
          const modal = document.querySelector('[data-od-id="live-strategy-modal"]');
          const nb = modal.querySelector('[data-od-id="strategy-new"]');
          const save = [...modal.querySelectorAll('button')]
            .find(b => ['保存策略','更新策略'].includes((b.textContent||'').trim()));
          if (!nb || !save) return null;
          const nbR = nb.getBoundingClientRect(), sR = save.getBoundingClientRect();
          return { nbX: nbR.x, saveX: sR.x, nbY: nbR.y, saveY: sR.y, sameRow: Math.abs(nbR.y-sR.y) < 8 };
        })()""")
        check("R1b 新建按钮在保存按钮左侧（真实几何）",
              bool(geo) and geo["nbX"] < geo["saveX"] and geo["sameRow"],
              str(geo))

        # R2 默认态点击 → handler 必须真的给出反馈。
        # 注意：预览 harness 的 push = console.log（不渲染 toast），
        # 所以这里捕获 push 调用本身；真实 app 的 push 会 setToasts 渲染（App.tsx:572），
        # 真机 toast 另由 verify_live_strategy_toast.py（computer_use 目视）确认。
        page.evaluate("""(() => {
          window.__pushed = [];
          if (!window.__pushWrapped) {
            window.__pushWrapped = true;
            const _orig = console.log;
            console.log = (...a) => { window.__pushed.push(a.join(' ')); _orig(...a); };
          }
        })()""")
        # ⚠️ 用户指明的元素：[data-od-id="live-strategy-modal"] > .p-4 > div:nth-of-type(1)
        #    > .flex.flex-wrap.items-center —— 即**列表区**。默认态点击必须让它变化。
        list_html_before = page.evaluate("""(() => {
          const m = document.querySelector('[data-od-id="live-strategy-modal"]');
          const list = m.querySelector('.p-4 > div:nth-of-type(1)');
          return list ? list.innerHTML.length : -1;
        })()""")
        newbtn.click()
        page.wait_for_timeout(600)
        pushed = page.evaluate("window.__pushed || []")
        check("R2 默认态点击触发反馈（push 被调用）",
              any("新建策略" in str(x) for x in pushed),
              f"pushed={pushed}")
        # R2b 列表区（用户指的那个元素）必须出现草稿行
        draft_row = page.locator('[data-od-id="strategy-draft-row"]')
        check("R2b 点击后**列表区出现草稿行**（用户指明的元素确实变化）",
              draft_row.count() == 1, f"draft_row={draft_row.count()}")
        list_html_after = page.evaluate("""(() => {
          const m = document.querySelector('[data-od-id="live-strategy-modal"]');
          const list = m.querySelector('.p-4 > div:nth-of-type(1)');
          return list ? list.innerHTML.length : -1;
        })()""")
        check("R2c 列表区 DOM 长度确实变化（非空操作）",
              list_html_after != list_html_before,
              f"before={list_html_before} after={list_html_after}")

        # R2d 再次点击 → 取消新建（也必须有可见变化）
        newbtn.click()
        page.wait_for_timeout(500)
        check("R2d 再点一次取消新建 → 草稿行消失（连点非空操作）",
              page.locator('[data-od-id="strategy-draft-row"]').count() == 0,
              "草稿行仍在")
        # 回到新建态，供后续用例
        newbtn.click()
        page.wait_for_timeout(500)

        # R3 进入编辑态 → 点新建 → 必须回到新建态
        page.evaluate("""(() => {
          const modal = document.querySelector('[data-od-id="live-strategy-modal"]');
          const edit = [...modal.querySelectorAll('button')]
            .find(b => (b.textContent||'').trim() === '编辑');
          edit.click();
        })()""")
        page.wait_for_timeout(600)
        in_edit = page.evaluate("""(() => {
          const t = document.querySelector('[data-od-id="live-strategy-modal"]').innerText;
          return t.includes('编辑中');
        })()""")
        check("R3a 点「编辑」后进入编辑态（标题含「编辑中」）", in_edit, "未进入编辑态")

        name_val_edit = page.locator('[data-od-id="strategy-name"]').input_value()
        newbtn.click()
        page.wait_for_timeout(600)
        back_to_new = page.evaluate("""(() => {
          const t = document.querySelector('[data-od-id="live-strategy-modal"]').innerText;
          return !t.includes('编辑中');
        })()""")
        name_val_new = page.locator('[data-od-id="strategy-name"]').input_value()
        check("R3b 编辑态点「新建」→ 退出编辑态（可见变化 ✅）", back_to_new,
              "仍在编辑态 —— 点击无变化")
        check("R3b2 同时列表区出现草稿行",
              page.locator('[data-od-id="strategy-draft-row"]').count() == 1,
              "草稿行未出现")
        check("R3c 编辑态点「新建」→ 表单被清空", name_val_new == "",
              f"编辑时={name_val_edit!r} 点新建后={name_val_new!r}")

        # R4 保存按钮文案随之变化
        save_text = page.evaluate("""(() => {
          const modal = document.querySelector('[data-od-id="live-strategy-modal"]');
          const b = [...modal.querySelectorAll('button')]
            .find(x => ['保存策略','更新策略'].includes((x.textContent||'').trim()));
          return b ? (b.textContent||'').trim() : null;
        })()""")
        check("R4 新建态下保存按钮文案 =「保存策略」", save_text == "保存策略", str(save_text))

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
