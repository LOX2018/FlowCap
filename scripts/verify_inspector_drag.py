"""元素选择模式 · 悬浮入口拖动行为验证（真实鼠标驱动，dev server）。

验证（设计契约）：
  1. 短按（无位移）→ 切换选择模式（不被拖动逻辑吃掉）
  2. **长按拖动 → 按钮跟随移动，松手后位置被记住（localStorage）**
  3. 拖动结束**不触发** onClick（模式未被误切换）
  4. 刷新页面后位置仍保留（记忆生效）
  5. 双击 → 复位到默认左下角（并清除记忆）
  6. 拖动 / 记忆全程不产生 pageerror

用一次性临时 profile（绝不碰账号 profile）。
"""
import os
import shutil
import sys
import tempfile

from patchright.sync_api import sync_playwright

CHROME = (r"C:\temp\flowcap_design\vb_chromium"
          r"\ungoogled-chromium_148.0.7778.215-1.1_windows_x64\chrome.exe")
URL = os.environ.get("EI_URL", "http://127.0.0.1:1420/preview-inspector.html")

results = []


def check(name, fn):
    try:
        ok, detail = fn()
    except Exception as e:  # noqa: BLE001
        ok, detail = False, f"EXC {type(e).__name__}: {str(e)[:160]}"
    results.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + ("  | " + str(detail) if detail else ""))


def box(page):
    return page.eval_on_selector('[data-od-id="debug-inspector-toggle"]',
                                 "el => { const r = el.getBoundingClientRect();"
                                 " return {x: r.x, y: r.y, w: r.width, h: r.height}; }")


def main():
    tmp = tempfile.mkdtemp(prefix="ei_drag_")
    ctx = None
    try:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=tmp, executable_path=CHROME, headless=True,
                args=["--disable-gpu", "--no-first-run", "--no-default-browser-check"],
                ignore_default_args=["--no-sandbox"])
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)))
            page.goto(URL, wait_until="networkidle")
            page.wait_for_timeout(700)

            b0 = box(page)
            check("初始位置在左下角（默认）",
                  lambda: (b0["x"] < 200 and b0["y"] > 300,
                           f"x={round(b0['x'])} y={round(b0['y'])}"))

            # ── 1) 短按 → 切换模式（不被拖动吞掉） ──
            cx, cy = b0["x"] + b0["w"] / 2, b0["y"] + b0["h"] / 2
            page.mouse.move(cx, cy)
            page.mouse.down()
            page.wait_for_timeout(60)          # < 长按阈值
            page.mouse.up()
            page.wait_for_timeout(300)
            check("① 短按 → 进入选择模式（提示条出现）",
                  lambda: (page.locator(".ei-hint").count() == 1, ""))

            # 退出，避免选择模式吞掉后续鼠标事件（面板会挡住拖动测试）
            page.keyboard.press("Escape")
            page.wait_for_timeout(250)

            # ── 2) 长按拖动 → 跟随移动 ──
            b = box(page)
            sx, sy = b["x"] + b["w"] / 2, b["y"] + b["h"] / 2
            page.mouse.move(sx, sy)
            page.mouse.down()
            page.wait_for_timeout(300)         # > 160ms 长按阈值 → 进入拖动
            page.mouse.move(sx + 420, sy - 300, steps=12)
            page.wait_for_timeout(80)
            mid = box(page)
            check("② 长按拖动 → 按钮跟随移动",
                  lambda: (mid["x"] > b["x"] + 200 and mid["y"] < b["y"] - 120,
                           f"({round(b['x'])},{round(b['y'])}) -> ({round(mid['x'])},{round(mid['y'])})"))
            page.mouse.up()
            page.wait_for_timeout(350)
            after = box(page)
            check("③ 松手后位置稳定（未回弹）",
                  lambda: (abs(after["x"] - mid["x"]) <= 3 and abs(after["y"] - mid["y"]) <= 3,
                           f"x={round(after['x'])} y={round(after['y'])}"))

            # ── 3) 拖动未误触 onClick ──
            check("④ 拖动结束不触发 onClick（未进入选择模式）",
                  lambda: (page.locator(".ei-hint").count() == 0, ""))

            # ── 4) 位置已写入 localStorage ──
            stored = page.evaluate("() => localStorage.getItem('dy.inspector.pos')")
            check("⑤ 位置写入 localStorage（仅坐标）",
                  lambda: (stored is not None and '"x"' in stored, stored))

            # ── 5) 刷新后位置保留 ──
            page.reload(wait_until="networkidle")
            page.wait_for_timeout(600)
            rel = box(page)
            check("⑥ 刷新后位置仍保留（记忆生效）",
                  lambda: (abs(rel["x"] - after["x"]) <= 3 and abs(rel["y"] - after["y"]) <= 3,
                           f"x={round(rel['x'])} y={round(rel['y'])}"))

            # ── 6) 双击复位 ──
            rx, ry = rel["x"] + rel["w"] / 2, rel["y"] + rel["h"] / 2
            page.mouse.move(rx, ry)
            page.mouse.dblclick(rx, ry)
            page.wait_for_timeout(500)
            res = box(page)
            cleared = page.evaluate("() => localStorage.getItem('dy.inspector.pos')")
            check("⑦ 双击 → 复位到默认左下角且清除记忆",
                  lambda: (res["x"] < 200 and res["y"] > 300 and cleared is None,
                           f"x={round(res['x'])} y={round(res['y'])} stored={cleared}"))

            # ── 7) 复位后短按仍可用 ──
            cx2, cy2 = res["x"] + res["w"] / 2, res["y"] + res["h"] / 2
            page.mouse.move(cx2, cy2)
            page.mouse.down()
            page.wait_for_timeout(60)
            page.mouse.up()
            page.wait_for_timeout(300)
            check("⑧ 复位后短按仍能进入选择模式",
                  lambda: (page.locator(".ei-hint").count() == 1, ""))

            check("无页面级 JS 异常（pageerror）",
                  lambda: (len(errs) == 0, errs[:2]))

            # ── 8) 长按但**不移动** → 判定为长按（不算短按），不应切换模式 ──
            page.mouse.move(cx2, cy2)
            page.mouse.down()
            page.wait_for_timeout(320)   # 超过长按阈值，但不产生位移
            page.mouse.up()
            page.wait_for_timeout(300)
            check("⑨ 长按不移动 → 不误切换模式（仍处于选择模式）",
                  lambda: (page.locator(".ei-hint").count() == 1, ""))

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
