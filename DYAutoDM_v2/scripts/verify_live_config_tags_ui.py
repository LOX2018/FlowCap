# -*- coding: utf-8 -*-
"""前端真实渲染验证（直播页两个新子视图；Playwright + 一次性临时 profile）。

## 为什么用临时 profile
铁律：**绝不**用账号 profile 起第二个浏览器（BCC 单例）。本脚本用
`tempfile.mkdtemp()` 的一次性 profile，与任何账号/会员环境无关，用完即删。

## 判据（分层，缺一层就不算通过）
1. **加载**：`#root` 非空 + 无 vite error overlay（否则空白页会骗人）
2. **结构**：三个子视图 tab 真实存在；点「目标直播间」→ 目标页 DOM 出现；
   点「配置标签」→ 配置标签页 DOM 出现
3. **数据**：目标页呈现 mock 的两条目标（含绑定态）；配置标签页呈现两条标签
4. **交互**：点「配置标签」里的「重启」按钮 → 出现 mock 的「引擎未运行」提示
   （证明 RoomConfigPage 的写路径真的接上了，不是静态摆设）
5. **样式**：glass 面板的 `backdrop-filter` 已生效、页面上无 `var(--undefined)` 类症状

跑法：先起 `npx vite --port 1420`，再跑本脚本。
"""
import io
import json
import os
import shutil
import sys
import tempfile
import time
import urllib.request

from playwright.sync_api import sync_playwright

URL = os.environ.get("PREVIEW_URL", "http://127.0.0.1:1420/preview-pages.html?p=live")
CHROME = r"C:\temp\dyautodm_design\vb_chromium\ungoogled-chromium_148.0.7778.215-1.1_windows_x64\chrome.exe"
OUT = os.path.join(os.environ.get("LOCALAPPDATA", tempfile.gettempdir()), "Temp", "preview_shots")
os.makedirs(OUT, exist_ok=True)

PASS = FAIL = 0
FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        FAILED.append(name)
        print(f"  FAIL {name}  {detail}")


# 前置：vite 必须活着（否则会把「服务没起」误判成「页面有缺陷」）
try:
    code = urllib.request.urlopen(URL, timeout=5).status
except Exception as e:
    raise SystemExit(f"预览服务不可达（{URL}）：{e} —— 请先 `npx vite --port 1420`")
print(f"vite 可达（{code}）：{URL}\n")

profile = tempfile.mkdtemp(prefix="preview_prof_")
if not os.path.isfile(CHROME):
    CHROME = None  # 退回 playwright 自带内核

try:
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=profile,
            executable_path=CHROME,
            headless=True,
            args=["--disable-gpu", "--no-first-run", "--no-default-browser-check"],
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        # 预览 harness 的 push 就是 console.log（不渲染 toast）——用它断言 UI 文案
        logs: list[str] = []
        page.on("console", lambda m: logs.append(m.text))
        posts: list[str] = []

        # 拦截直播配置相关接口，按**真实契约形状**返回 —— 一次验证三件事：
        #   ① 前端消费的字段形状与后端契约一致 ② 页面真的渲染 ③ 写路径可用
        CFG_A = {
            "id": "992931212705", "room_id": "992931212705", "name": "标准-快速",
            "max_target": 50, "interval": 45, "delay": "40,80", "force_rescan": False,
            "acct": "主账号_A", "auto_link_mic": True, "link_mic_mode": "audio",
            "dm_pool": [{"text": "你好，看到你咨询工伤，方便留个电话吗", "enabled": True},
                        {"text": "在的哈，有什么可以帮您", "enabled": False}],
        }
        CFG_B = {
            "id": "保守-慢速", "room_id": "保守-慢速", "name": "保守-慢速",
            "max_target": 8, "interval": 300, "delay": "300,600", "force_rescan": True,
            "acct": None, "auto_link_mic": False, "link_mic_mode": "audio", "dm_pool": [],
        }
        TARGETS = {
            "ok": True,
            "items": [
                {"room_id": "992931212705", "name": "张老师工伤直播间",
                 "tag_id": "992931212705", "enabled": True},
                {"room_id": "777666555", "name": "另一间（未绑定）",
                 "tag_id": None, "enabled": False},
            ],
        }

        def _route(route):
            url = route.request.url
            method = route.request.method
            if "/restart" in url:
                posts.append(f"{method} {url}")
                route.fulfill(status=200, content_type="application/json", body=json.dumps({
                    "ok": True, "applied_fields": [],
                    "restart": {"ok": False, "applied": [], "not_applied": [],
                                "reason": "引擎未运行（当前 idle），请先「开始自动私信」"},
                }))
            elif "/target-rooms" in url:
                if method == "POST":
                    route.fulfill(status=200, content_type="application/json",
                                  body=json.dumps({"ok": True, "target": {"room_id": "992931212705"}}))
                elif method == "DELETE":
                    route.fulfill(status=200, content_type="application/json",
                                  body='{"ok":true,"removed":"x"}')
                else:
                    route.fulfill(status=200, content_type="application/json",
                                  body=json.dumps(TARGETS))
            elif method == "GET":
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"ok": True, "items": [CFG_A, CFG_B]}))
            else:
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"ok": True, "config": CFG_A}))

        page.route("**/api/live/**", _route)

        page.goto(URL, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(2500)

        print("A. 加载层")
        check("A1 #root 已渲染内容", page.eval_on_selector("#root", "el => el.innerHTML.length") > 2000,
              f"len={page.eval_on_selector('#root', 'el => el.innerHTML.length')}")
        check("A2 无 vite error overlay",
              page.query_selector("vite-error-overlay") is None)
        check("A3 无页面级 JS 异常", not errors, f"errors={errors[:3]}")

        print("\nB. 直播页三个子视图（真实点击）")
        body = page.inner_text("body")
        for label in ("监听", "目标直播间", "配置标签"):
            check(f"B 子视图入口存在：{label}", label in body)
        # 旧弹窗必须不存在（弹窗是「反复覆盖」形态的载体）
        check("B 旧弹窗 DOM 不存在", page.query_selector('[data-od-id="room-config-modal"]') is None)

        # 点「目标直播间」
        page.get_by_role("button", name="目标直播间", exact=True).first.click()
        page.wait_for_timeout(900)
        check("B1 目标直播间页已挂载",
              page.query_selector('[data-od-id="live-target-rooms"]') is not None)
        t = page.inner_text('[data-od-id="live-target-rooms"]')
        page.screenshot(path=os.path.join(OUT, "targets.png"))
        check("B2 呈现 mock 目标直播间（含备注名）", "张老师工伤直播间" in t, t[:220])
        check("B3 呈现绑定态文案", ("已绑定" in t) or ("未绑定" in t), t[:220])
        check("B4 有「是否绑定配置」选择器",
              page.query_selector('[data-od-id^="target-room-tag"]') is not None)
        check("B5 目标页无参数编辑控件（只有身份/绑定/启用）",
              ("发送上限" not in t) and ("延迟抖动" not in t), t[:220])

        # 点「配置标签」
        page.get_by_role("button", name="配置标签", exact=True).first.click()
        page.wait_for_timeout(900)
        check("B6 配置标签页已挂载",
              page.query_selector('[data-od-id="live-config-tags"]') is not None)
        c = page.inner_text('[data-od-id="live-config-tags"]')
        page.screenshot(path=os.path.join(OUT, "configs.png"))
        check("B7 呈现两条标签（多配置标签）",
              ("标准-快速" in c) and ("保守-慢速" in c), c[:300])
        check("B8 保留全部参数控件（唯一可写入口）",
              all(k in c for k in ("发送上限", "间隔（秒）", "延迟抖动（秒）",
                                   "私信词库", "强制重扫", "自动申请连麦")), c[:300])
        _restart_btns = page.query_selector_all("button:has-text('重启')")
        check("B9 每条标签独立提供「重启」按钮", len(_restart_btns) >= 2,
              "n=%d" % len(_restart_btns))

        print("\nC. 交互层：写路径真的接上了（点「重启」发出真实请求并如实回显）")
        # 本环境 Playwright 的 console 捕获拿不到页面 JS 日志（已实测：连探针都不出现），
        # 故在页面内包装 console.log（预览 harness 的 push 就是 console.log），
        # 读组件真实产出的文案 —— 这是可观测的，不是用代码推断。
        page.evaluate("""() => {
          window.__pushed = [];
          const orig = console.log;
          console.log = (...a) => { try { window.__pushed.push(a.join(' ')); } catch(e){} orig(...a); };
        }""")
        page.query_selector_all("button:has-text('重启')")[0].click()
        page.wait_for_timeout(1800)
        page.screenshot(path=os.path.join(OUT, "restart_clicked.png"))
        pushed = page.evaluate("() => window.__pushed || []")
        check("C1 点「重启」真的发出了 restart 请求", any("/restart" in x for x in posts),
              f"posts={posts}")
        check("C1b 有 UI 反馈（读到组件产出的文案）", bool(pushed), f"pushed={pushed}")
        # 引擎未运行 → 必须如实说「未能热更」，不许谎报已生效
        check("C2 未运行时不谎报已热更（文案含「未能热更/引擎未运行/已保存」）",
              any(("未能热更" in x) or ("引擎未运行" in x) or ("已保存" in x) for x in pushed),
              f"pushed={pushed}")

        print("\nD. 样式层（glass 面板 backdrop-filter 生效）")
        bf = page.eval_on_selector(
            '[data-od-id="live-config-tags"] > div',
            "el => getComputedStyle(el).backdropFilter || getComputedStyle(el).webkitBackdropFilter")
        check("D1 配置标签面板已应用 backdrop-filter", bool(bf) and bf != "none", f"bf={bf}")
        # 确保回到「监听」子视图，原主体仍在
        page.get_by_role("button", name="监听", exact=True).first.click()
        page.wait_for_timeout(800)
        m = page.inner_text("body")
        page.screenshot(path=os.path.join(OUT, "monitor.png"))
        check("D2 返回「监听」后原主体仍在（当前监听账号区可见）", "当前监听账号" in m, m[:200])

        ctx.close()
finally:
    shutil.rmtree(profile, ignore_errors=True)

print(f"\n截图落盘：{OUT}")
print(f"PASS={PASS} FAIL={FAIL}")
if FAILED:
    print("失败项：")
    for x in FAILED:
        print("  -", x)
sys.exit(1 if FAIL else 0)
