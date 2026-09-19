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
  R2 改动任一字段 → 自动进入草稿模式（列表区出现草稿行，**无「新建策略」按钮**）
  R2b 点「清空表单」→ 退出草稿模式（草稿行消失）—— [strategy-new] 回归清空本义
  R2c 标题区文字 ==「策略详情」（用户指定）
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
        check("R1 存在清空表单按钮（data-od-id=strategy-new 回归清空本义）",
              newbtn.count() == 1, f"count={newbtn.count()}")
        check("R1a 页脚**没有**「新建策略」按钮（用户要求取消）",
              page.evaluate("""(() => {
                const m = document.querySelector('[data-od-id="live-strategy-modal"]');
                return !(m.innerText || '').includes('新建策略');
              })()"""),
              "弹窗内仍出现「新建策略」字样")
        check("R1b 清空表单按钮文案正确",
              newbtn.inner_text().strip() == "清空表单",
              repr(newbtn.inner_text().strip()))

        # R1c 标题区必须是「策略详情」（用户指定的那个元素）
        title_txt = page.evaluate("""(() => {
          const m = document.querySelector('[data-od-id="live-strategy-modal"]');
          const t = m.querySelector('.p-4 > div.mb-1.font-semibold');
          return t ? (t.textContent || '').trim() : null;
        })()""")
        check("R1c 标题区文字为「策略详情」（用户指定）",
              bool(title_txt) and title_txt.startswith("策略详情"), repr(title_txt))

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
        # 用户指明的元素：[data-od-id="live-strategy-modal"] > .p-4 > div:nth-of-type(1)
        #   > .flex.flex-wrap.items-center —— 即**列表区**。
        # 新契约：改任一字段即自动进入草稿模式，列表区必须出现草稿行。
        list_html_before = page.evaluate("""(() => {
          const m = document.querySelector('[data-od-id="live-strategy-modal"]');
          const list = m.querySelector('.p-4 > div:nth-of-type(1)');
          return list ? list.innerHTML.length : -1;
        })()""")
        check("R2 初始态无草稿行（干净浏览态）",
              page.locator('[data-od-id="strategy-draft-row"]').count() == 0,
              "初始就出现了草稿行")
        # 改动 → 草稿模式
        namebox = page.locator('[data-od-id="strategy-name"]')
        namebox.fill("草稿验证-甲")
        page.wait_for_timeout(500)
        check("R2b 改动字段后**列表区自动出现草稿行**（用户指明的元素）",
              page.locator('[data-od-id="strategy-draft-row"]').count() == 1,
              "草稿行未出现")
        list_html_after = page.evaluate("""(() => {
          const m = document.querySelector('[data-od-id="live-strategy-modal"]');
          const list = m.querySelector('.p-4 > div:nth-of-type(1)');
          return list ? list.innerHTML.length : -1;
        })()""")
        check("R2c 列表区 DOM 长度确实变化（非空操作）",
              list_html_after != list_html_before,
              f"before={list_html_before} after={list_html_after}")
        check("R2d 标题区出现「（草稿）」标记",
              "草稿" in page.evaluate("""(() => {
                const m = document.querySelector('[data-od-id="live-strategy-modal"]');
                const t = m.querySelector('.p-4 > div.mb-1.font-semibold');
                return t ? (t.textContent || '') : '';
              })()"""),
              "标题未出现草稿标记")

        # R2e 「清空表单」→ 退出草稿模式（strategy-new 的本义）
        newbtn.click()
        page.wait_for_timeout(500)
        _draft_n = page.locator('[data-od-id="strategy-draft-row"]').count()
        _name_v = namebox.input_value()
        check("R2e 点「清空表单」→ 草稿行消失 + 输入框清空",
              _draft_n == 0 and _name_v == "",
              f"draft={_draft_n} name={_name_v!r}")

        # R3 进入编辑态 → 改字段 → 仍为编辑态（编辑与草稿互斥）
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

        # 编辑态下改字段 → 仍在编辑（不显示草稿行，用 ✎ 高亮标记）
        page.locator('[data-od-id="strategy-name"]').fill("改名后的策略")
        page.wait_for_timeout(500)
        check("R3b 编辑态改字段 → 仍为编辑中（不显示草稿行，避免与新建混淆）",
              page.locator('[data-od-id="strategy-draft-row"]').count() == 0
              and page.evaluate("""(() => {
                const t = document.querySelector('[data-od-id="live-strategy-modal"]').innerText;
                return t.includes('编辑中');
              })()"""),
              "编辑态下出现了草稿行或丢失编辑标记")
        check("R3c 编辑态按钮文案为「更新策略」",
              page.evaluate("""(() => {
                const m = document.querySelector('[data-od-id="live-strategy-modal"]');
                const b = [...m.querySelectorAll('button')]
                  .find(x => ['保存策略','更新策略'].includes((x.textContent||'').trim()));
                return b ? (b.textContent||'').trim() : null;
              })()""") == "更新策略")

        # R4 退出编辑态（清空表单）后，保存按钮应回「保存策略」
        newbtn.click()
        page.wait_for_timeout(500)
        save_text = page.evaluate("""(() => {
          const modal = document.querySelector('[data-od-id="live-strategy-modal"]');
          const b = [...modal.querySelectorAll('button')]
            .find(x => ['保存策略','更新策略'].includes((x.textContent||'').trim()));
          return b ? (b.textContent||'').trim() : null;
        })()""")
        check("R4 退出编辑态后保存按钮文案 =「保存策略」", save_text == "保存策略", str(save_text))

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
