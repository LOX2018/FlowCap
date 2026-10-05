"""元素选择模式（Element Inspector）实机验证 —— 真实浏览器驱动。

验证目标（设计契约）：
  1. 工具按钮存在，点击进入选择模式（提示条 + 高亮框）。
  2. ★选择模式下点击元素 → 只选中、**onClick 不被触发**（计数不变），
     且面板产出结构性取址（data-od-id / CSS / XPath / 组件链）。
  3. 面板自身可交互（不被拦截）。
  4. 退出（Esc / 按钮）后点击恢复正常。
  5. 复制产出完整报告。

用一次性临时 profile（绝不用账号 profile），退出即删。
每项检查独立捕获异常 —— 失败要**报出**，不能让脚本崩在第一个断言上。
"""
import os
import re
import shutil
import sys
import tempfile

from patchright.sync_api import sync_playwright

CHROME = (r"C:\temp\flowcap_design\vb_chromium"
          r"\ungoogled-chromium_148.0.7778.215-1.1_windows_x64\chrome.exe")
# 目标页可覆盖：默认 dev server；生产构建验证时传 production bundle 地址
URL = os.environ.get("EI_URL", "http://127.0.0.1:1420/preview-inspector.html")

results = []


def check(name, fn):
    """fn() -> (ok, detail)；异常记为 FAIL 并给出异常文本。"""
    try:
        ok, detail = fn()
    except Exception as e:  # noqa: BLE001
        ok, detail = False, f"EXC {type(e).__name__}: {str(e)[:160]}"
    results.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + ("  | " + str(detail) if detail else ""))


def main():
    tmp = tempfile.mkdtemp(prefix="ei_verify_")
    ctx = None
    try:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=tmp,
                executable_path=CHROME,
                headless=True,
                args=["--disable-gpu", "--no-first-run", "--no-default-browser-check"],
                ignore_default_args=["--no-sandbox"],
            )
            try:
                ctx.grant_permissions(["clipboard-read", "clipboard-write"])
            except Exception:  # noqa: BLE001
                pass
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)))
            page.goto(URL, wait_until="networkidle")
            page.wait_for_timeout(700)

            check("页面本身渲染（标题）",
                  lambda: (page.locator("#demo-title").count() == 1, page.title()))
            check("工具按钮渲染",
                  lambda: (page.locator('[data-od-id="debug-inspector-toggle"]').count() == 1, ""))

            # ★入口形态：全局顶层悬浮（左下角、浮于业务元素之上）
            check("入口为全局悬浮（position:fixed 且左下角）",
                  lambda: (lambda cs, r: (
                      cs["position"] == "fixed" and r["left"] < 200 and r["bottom"] > 300,
                      f"pos={cs['position']} left={round(r['left'])} bottom={round(r['bottom'])}"
                  ))(page.eval_on_selector('[data-od-id="debug-inspector-toggle"]',
                                           "el => getComputedStyle(el)"),
                      page.eval_on_selector('[data-od-id="debug-inspector-toggle"]',
                                            "el => el.getBoundingClientRect()")))
            check("★入口浮于业务元素之上（命中测试命中自己）",
                  lambda: (page.eval_on_selector(
                      '[data-od-id="debug-inspector-toggle"]',
                      "el => { const r = el.getBoundingClientRect();"
                      " const hit = document.elementFromPoint(r.x + r.width/2, r.y + r.height/2);"
                      " return !!hit && (hit === el || el.contains(hit)); }"), ""))

            # 进入选择模式
            page.click('[data-od-id="debug-inspector-toggle"]')
            page.wait_for_timeout(300)
            check("进入选择模式：提示条 + 激活态",
                  lambda: (page.locator(".ei-hint").count() == 1
                           and page.locator(".ei-fab-on").count() == 1, ""))

            page.hover("#danger-btn")
            page.wait_for_timeout(250)
            check("悬停高亮框显示",
                  lambda: (page.eval_on_selector(".ei-box", "el => getComputedStyle(el).display") != "none",
                           ""))

            # ★核心契约：选择模式点击不触发功能
            before = page.inner_text("#click-count")
            page.click("#danger-btn")
            page.wait_for_timeout(300)
            after = page.inner_text("#click-count")
            check("★选择模式下点击不触发功能（计数不变）",
                  lambda: (before == after, f"{before} -> {after}"))

            check("结果面板出现",
                  lambda: (page.locator(".ei-panel").count() == 1, ""))
            if page.locator(".ei-panel").count() == 1:
                ptext = page.inner_text(".ei-panel")
                check("面板含 data-od-id 选择器",
                      lambda: ('[data-od-id="demo-danger"]' in ptext, ptext[:150].replace("\n", " / ")))
                check("面板含 React 组件链段（或明确标注不可用）",
                      lambda: (("组件链" in ptext)
                               and ("Demo" in ptext or "不可用" in ptext), ""))
                # 压缩名（如 `ep`/`t`）绝不许出现在组件链里 —— 误导性信息
                chain = []
                try:
                    chain = re.findall(r"组件链\n(.+)\n上级结构", ptext)[0]
                except Exception:  # noqa: BLE001
                    pass
                check("组件链不含压缩后的无意义名",
                      lambda: (not any(len(t) <= 3 and t.islower() for t in chain.split(" > ")),
                               chain))
                check("面板识别选中元素（危险按钮）",
                      lambda: ("危险按钮" in ptext, page.inner_text(".ei-panel-el")))
                check("面板含 XPath",
                      lambda: ("XPath" in ptext and "//" in ptext, ""))
            stext = ""
            check("面板可切到「状态」页签",
                  lambda: (page.locator(".ei-tab", has_text="状态").count() == 1, ""))
            if page.locator(".ei-tab", has_text="状态").count() == 1:
                page.locator(".ei-tab", has_text="状态").first.click()
                page.wait_for_timeout(250)
                stext = page.inner_text(".ei-body")
                check("状态页显示类名/标签（不编造）",
                      lambda: ("class" in stext and "tag" in stext, stext[:120].replace("\n", " / ")))
                page.locator(".ei-tab", has_text="选择器").first.click()
                page.wait_for_timeout(200)
            check("结构页可渲染",
                  lambda: (page.locator(".ei-tab", has_text="结构").count() == 1, ""))

            # 键盘 ← 上移一层
            page.keyboard.press("ArrowLeft")
            page.wait_for_timeout(250)
            check("← 上移到父元素（面板元素标签变化）",
                  lambda: (page.inner_text(".ei-panel-el") != "button.demo-danger",
                           page.inner_text(".ei-panel-el")))

            # 重新选中按钮 + 复制
            page.click("#danger-btn")
            page.wait_for_timeout(250)
            page.click(".ei-foot .ei-act")
            page.wait_for_timeout(400)
            clip = page.evaluate("() => navigator.clipboard.readText()")
            check("复制产出完整报告（选择器 + 组件链段）",
                  lambda: ("demo-danger" in clip and "组件链" in clip, clip.splitlines()[:1]))

            # 面板自身交互：清空选择
            page.click(".ei-x")
            page.wait_for_timeout(250)
            check("面板 × 可清空选择（面板交互未被拦截）",
                  lambda: (page.locator(".ei-panel").count() == 0, ""))

            # Esc 退出
            page.keyboard.press("Escape")
            page.wait_for_timeout(300)
            check("Esc 退出选择模式",
                  lambda: (page.locator(".ei-hint").count() == 0
                           and page.locator(".ei-fab-on").count() == 0, ""))

            # ★退出后恢复
            b2 = page.inner_text("#click-count")
            page.click("#danger-btn")
            page.wait_for_timeout(300)
            a2 = page.inner_text("#click-count")
            check("★退出后点击恢复正常（计数 +1）",
                  lambda: (int(a2) == int(b2) + 1, f"{b2} -> {a2}"))

            check("无页面级 JS 异常（pageerror）",
                  lambda: (len(errs) == 0, errs[:2]))

            ctx.close()
            ctx = None
    finally:
        if ctx is not None:
            try:
                ctx.close()
            except Exception:  # noqa: BLE001
                pass
        shutil.rmtree(tmp, ignore_errors=True)

    total = len(results)
    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\n=== RESULT {passed}/{total} PASS ===")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
