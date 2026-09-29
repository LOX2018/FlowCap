# -*- coding: utf-8 -*-
"""真机验证：用 Camoufox（无头 + 临时 profile）打开真实抖音登录页，
捕获 DOM，保存快照，并在真实 DOM 上跑同一套自适应定位逻辑。

严格只读：不输入、不点击、不提交；不写任何真实账号 profile。
"""
import asyncio, os, sys, json, tempfile, time, traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

LOGIN_URL = "https://www.douyin.com/?modal_id=login"
RISK_KW = ["安全风险", "已阻止此次访问", "访问过于频繁", "验证", "滑块"]

SELS = {
    "panel": "#douyin-login-new-id",
    "scan_comp": "#douyin_login_comp_scan_code",
    "btn_id": "#douyin_login_comp_btn_id",
    "qr_container": "#animate_qrcode_container",
    "qr_img": "#animate_qrcode_container img",
    "input_normal": 'input[name="normal-input"]',
    "input_button": 'input[name="button-input"]',
    "any_tel_input": 'input[type="tel"]',
    "qr_canvas_or_img": "#animate_qrcode_container img, #animate_qrcode_container canvas",
}


async def main(out_dir: str):
    os.makedirs(out_dir, exist_ok=True)
    prof = os.path.join(tempfile.gettempdir(), "_cam_spike_profile")
    os.makedirs(prof, exist_ok=True)
    from camoufox.async_api import AsyncCamoufox

    report = {"url": LOGIN_URL, "profile": prof}
    ctx_mgr = AsyncCamoufox(
        persistent_context=True,
        user_data_dir=prof,
        headless=True,
        locale="zh-CN",
        os="windows",
        i_know_what_im_doing=True,
    )
    ctx = await ctx_mgr.__aenter__()
    try:
        page = await ctx.new_page()
        await page.set_viewport_size({"width": 1600, "height": 1000})
        t0 = time.time()
        await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
        # 等登录面板出现（最多 30s）
        appeared = False
        for _ in range(60):
            try:
                if await page.locator(SELS["panel"]).count() > 0:
                    appeared = True
                    break
            except Exception:
                pass
            await asyncio.sleep(0.5)
        # 再给渲染一点时间（二维码等）
        await asyncio.sleep(2.5)
        report["panel_appeared"] = appeared
        report["wait_s"] = round(time.time() - t0, 1)
        report["final_url"] = page.url
        report["title"] = await page.title()

        # 逐个选择器探测
        present = {}
        for name, sel in SELS.items():
            try:
                present[name] = await page.locator(sel).count()
            except Exception as e:
                present[name] = f"ERR {e!r}"
        report["selectors"] = present

        # 抓整页 HTML + 风控关键词
        html = await page.content()
        report["html_len"] = len(html)
        report["risk_hits"] = [k for k in RISK_KW if k in html]
        snap = os.path.join(out_dir, "live_login.html")
        with open(snap, "w", encoding="utf-8") as f:
            f.write(html)
        report["snapshot"] = snap

        # 若面板未出现，dump 可见文本前 500 字（帮助判断是不是风控页）
        try:
            body_txt = (await page.locator("body").inner_text())[:500]
            report["body_preview"] = body_txt
        except Exception:
            report["body_preview"] = ""

        # 截图（便于人工核对，agent 不看图，仅留证）
        png = os.path.join(out_dir, "live_login.png")
        try:
            await page.screenshot(path=png, full_page=False)
            report["screenshot"] = png
        except Exception as e:
            report["screenshot"] = f"ERR {e!r}"

    finally:
        try:
            await ctx_mgr.__aexit__(None, None, None)
        except Exception as e:
            report["close_err"] = repr(e)

    rp = os.path.join(out_dir, "live_report.json")
    with open(rp, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "_live")
    try:
        asyncio.run(main(out))
    except Exception:
        traceback.print_exc()
        sys.exit(1)
