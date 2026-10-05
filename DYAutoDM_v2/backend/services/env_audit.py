# -*- coding: utf-8 -*-
"""环境泄漏监测探针（v0.43.88）—— 移植三个开源检测器的核心检测逻辑。

## 设计契约

**design**：把 rebrowser-bot-detector（CDP/自动化泄漏）、CreepJS（谎言检测/
跨信号交叉比对）、liarjs（JS 层 vs 档案层交叉比对）三者的核心检测逻辑
移植为**内置 JS 探针**，在 BCC 容器内被动执行，实时检测环境是否泄露。

**contract**（铁律）：
1. **零外网请求**：探针纯本地 JS 计算，绝不访问 creepjs/pixelscan 等在线
   测试站（访问行为本身就是风控暴露 + IP 泄漏给第三方）。检测逻辑内嵌，
   结果只进本项目日志与 /env_audit 端点。
2. **零主动抖音请求**：探针在已登录的 chat 页上下文里只读
   navigator/screen/WebGL 等本地 API，不发任何网络请求（昵称红线同源）。
3. **不触碰业务页面状态**：只在常驻 chat 页 evaluate 只读表达式，
   不导航、不点 DOM、不写 window 业务对象（只读 window 上探针专属键）。
4. **告警不阻断**：发现泄漏只记 BCC-064+ 告警日志与 /env_audit 结果，
   绝不自动改环境/重启浏览器（与 env_baseline 同纪律）。

## 检测项（对应开源项目）

rebrowser-bot-detector（github.com/rebrowser/rebrowser-bot-detector）：
  - runtimeEnableLeak 同族 → CDP 自动化痕迹（此处验 patchright 生效性）
  - navigator.webdriver / HeadlessChrome UA / 缺 plugins / 缺 languages
CreepJS（github.com/abrahamjuliot/creepjs，MIT）：
  - 原生函数篡改检测（toString 应含 [native code]）
  - 跨信号矛盾（UA 平台 vs navigator.platform vs WebGL vendor vs 时区）
liarjs（github.com/liarjsdev/liarjs）：
  - JS 层指纹 vs 项目档案层（env_baseline/HTTP 层声明）交叉比对——
    这部分在 Python 侧做（JS 取回真值 → 与 utils/fingerprint 档案对比）
  - Worker 与主线程值不一致（deviceandbrowserinfo 的 worker 检查思路）

**为什么检测项在 Python 侧比对**：项目档案（fingerprint_profile）在
Python，浏览器真值由探针取回后两侧对齐——与 v0.43.14「单源化」验收
脚本（verify_fp_single_source）同一套判据，转为常驻监测。
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# 浏览器侧探针（async 函数字符串，经 BCC /exec_js 形态 evaluate 执行）
# 返回 dict：js_view = 浏览器真实自报值（不做任何伪装判断，判断在 Python）
# ---------------------------------------------------------------------------
ENV_AUDIT_JS = r"""
async () => {
  const out = {};
  try {
    // ---- navigator 基本面（rebrowser / sannysoft 检测项）----
    out.webdriver = navigator.webdriver === undefined ? null : !!navigator.webdriver;
    out.userAgent = navigator.userAgent || "";
    out.platform = navigator.platform || "";
    out.languages = (navigator.languages || []).join(",");
    out.language = navigator.language || "";
    out.pluginsCount = (navigator.plugins || {length:0}).length || 0;
    out.hardwareConcurrency = navigator.hardwareConcurrency || 0;
    out.deviceMemory = navigator.deviceMemory === undefined ? null : navigator.deviceMemory;
    out.maxTouchPoints = navigator.maxTouchPoints || 0;
    // HeadlessChrome UA 泄漏（rebrowser/sannysoft）
    out.headlessInUA = /HeadlessChrome/i.test(navigator.userAgent || "");

    // ---- 自动化痕迹（rebrowser 检测思路：注入对象/序列）----
    out.automationKeys = (() => {
      const hits = [];
      for (const k of Object.keys(window)) {
        if (/^(cdc_|__driver|__selenium|__webdriver|__fxdriver|calledSelenium|\$webdriver)/i.test(k)) hits.push(k);
      }
      try {
        if (document.documentElement) {
          for (const a of document.documentElement.getAttributeNames?.() || []) {
            if (/selenium|webdriver|driver/i.test(a)) hits.push("html@" + a);
          }
        }
      } catch (e) {}
      return hits.slice(0, 10);
    })();
    // window.chrome 存在性（真实 Chrome 有；patchright 下不应被隐藏）
    out.hasWindowChrome = !!window.chrome;

    // ---- 原生函数篡改（CreepJS toString 检查思路）----
    const nativeOk = (fn) => {
      try { return Function.prototype.toString.call(fn).includes("[native code]"); }
      catch (e) { return null; }
    };
    out.nativeChecks = {
      toString: nativeOk(Function.prototype.toString),
      getParameter: null, WebGLRenderingContext_getParameter: null, // 下面填
      createElement: nativeOk(document.createElement),
      getContext: nativeOk(HTMLCanvasElement.prototype.getContext),
      querySelector: nativeOk(Document.prototype.querySelector),
      defineProperty: nativeOk(Object.defineProperty),
      getUserMedia: navigator.mediaDevices ? nativeOk(navigator.mediaDevices.getUserMedia) : null,
    };
    try {
      out.nativeChecks.WebGLRenderingContext_getParameter =
        nativeOk(WebGLRenderingContext.prototype.getParameter);
    } catch (e) {}
    // 被篡改的计数（null 视为无法检测，不计）
    try {
      out.tamperedCount = Object.values(out.nativeChecks)
        .filter(v => v === false).length;
    } catch (e) { out.tamperedCount = -1; }

    // ---- 屏幕与视口（liarjs：screen/inner/avail 三者相等 = 自动化特征）----
    out.screen = { w: screen.width, h: screen.height,
                   aw: screen.availWidth, ah: screen.availHeight,
                   cd: screen.colorDepth,
                   outerW: window.outerWidth, outerH: window.outerHeight,
                   innerW: window.innerWidth, innerH: window.innerHeight,
                   dpr: window.devicePixelRatio };

    // ---- 时区与语言（liarjs：时区 vs 语言 vs 档案交叉在 Python 做）----
    try { out.timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || ""; }
    catch (e) { out.timezone = ""; }
    out.timezoneOffset = new Date().getTimezoneOffset();

    // ---- WebGL / GPU（CreepJS 重点；软件渲染=最强破绽）----
    try {
      const c = document.createElement("canvas");
      const gl = c.getContext("webgl") || c.getContext("experimental-webgl");
      if (gl) {
        const dbg = gl.getExtension("WEBGL_debug_renderer_info");
        out.webgl = {
          vendor: gl.getParameter(gl.VENDOR) || "",
          renderer: gl.getParameter(gl.RENDERER) || "",
          dbgVendor: dbg ? (gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL) || "") : "",
          dbgRenderer: dbg ? (gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) || "") : "",
          version: gl.getParameter(gl.VERSION) || "",
        };
        // 软件渲染特征（实测 9-13：--disable-gpu 下出现 Microsoft Basic Render Driver）
        const r = (out.webgl.dbgRenderer || out.webgl.renderer || "").toLowerCase();
        out.softRender = /swiftshader|basic render|software|llvmpipe/.test(r);
      } else { out.webgl = null; out.softRender = null; }
    } catch (e) { out.webgl = null; out.softRender = null; }

    // ---- Canvas 噪声稳定性（同号两次绘制应全同——种子指纹的确定性判据）----
    try {
      const draw = () => {
        const c = document.createElement("canvas"); c.width = 200; c.height = 50;
        const ctx = c.getContext("2d");
        ctx.textBaseline = "top"; ctx.font = "16px 'Arial'";
        ctx.fillStyle = "#f60"; ctx.fillRect(122, 1, 60, 20);
        ctx.fillStyle = "#069"; ctx.fillText("env-audit,\u4e16\u754c", 2, 2);
        ctx.strokeStyle = "rgba(102,204,0,0.7)"; ctx.arc(50, 25, 18, 0, 6.29, false); ctx.stroke();
        return c.toDataURL();
      };
      const a = draw(), b = draw();
      out.canvasStable = (a === b);
      // 简单 hash（不传全量 dataURL，避免日志膨胀）
      let h = 0; for (let i = 0; i < a.length; i += 13) { h = ((h << 5) - h + a.charCodeAt(i)) | 0; }
      out.canvasHash = String(h);
    } catch (e) { out.canvasStable = null; out.canvasHash = ""; }

    // ---- Web Worker 交叉（liarjs/deviceandbrowserinfo 思路：worker 值应与主线程一致）----
    try {
      const wk = await new Promise((resolve) => {
        let settled = false;
        const to = setTimeout(() => { if (!settled) { settled = true; resolve(null); } }, 1500);
        try {
          const blob = new Blob(["self.onmessage=e=>{postMessage({ua:self.navigator.userAgent,cores:self.navigator.hardwareConcurrency})}"], {type:"application/javascript"});
          const w = new Worker(URL.createObjectURL(blob));
          w.onmessage = (e) => { if (!settled) { settled = true; clearTimeout(to); w.terminate(); resolve(e.data); } };
          w.onerror = () => { if (!settled) { settled = true; clearTimeout(to); resolve(null); } };
          w.postMessage("x");
        } catch (e) { if (!settled) { settled = true; clearTimeout(to); resolve(null); } }
      });
      out.worker = wk ? { ua: wk.ua || "", cores: wk.cores || 0 } : null;
    } catch (e) { out.worker = null; }

    // ---- Audio 指纹稳定性（CreepJS 检测项；同库两次应全同）----
    try {
      const Ctx = window.OfflineAudioContext || window.webkitOfflineAudioContext;
      if (Ctx) {
        const run = () => new Promise((resolve) => {
          const ctx = new Ctx(1, 5000, 44100);
          const osc = ctx.createOscillator(); osc.type = "triangle"; osc.frequency.value = 10000;
          const comp = ctx.createDynamicsCompressor();
          osc.connect(comp); comp.connect(ctx.destination); osc.start(0);
          ctx.startRendering().then(buf => {
            let s = 0; const ch = buf.getChannelData(0);
            for (let i = 4500; i < 5000; i++) s += Math.abs(ch[i]);
            resolve(String(s.toFixed(6)));
          }).catch(() => resolve(null));
        });
        const a1 = await run(), a2 = await run();
        out.audioStable = (a1 !== null && a1 === a2);
        out.audioHash = a1 || "";
      } else { out.audioStable = null; out.audioHash = ""; }
    } catch (e) { out.audioStable = null; out.audioHash = ""; }

    // ---- Client Hints（fingerprint.py 声明的 sec_ch_ua 应与 UA 版本自洽——JS 侧取 UA-Data 若可用）----
    try {
      if (navigator.userAgentData) {
        out.uaData = { brands: (navigator.userAgentData.brands || []).map(b => b.brand + "|" + b.version).slice(0, 5),
                       platform: navigator.userAgentData.platform || "" };
      } else { out.uaData = null; }
    } catch (e) { out.uaData = null; }

    // ---- 权限/通知不一致检测（sannysoft：Notification.permission vsUA 判型）----
    try {
      if (typeof Notification !== "undefined") {
        out.notificationPermission = Notification.permission;
      } else { out.notificationPermission = null; }
    } catch (e) { out.notificationPermission = null; }

    out.ok = true;
  } catch (e) {
    out.ok = false; out.error = String(e && e.message || e);
  }
  return out;
}
"""

# ---------------------------------------------------------------------------
# T5-b（2026-09-23）：两世界可见性探针
#
# ## 为什么需要
#
# `browser_daemon` 的 CAP_*_HOOK_JS 经 `add_init_script` 注入后，读取侧
# （`page.evaluate("() => window.__CAP_WP_MESSAGE__ ...")`）在**默认世界**执行。
# 若注入落在**另一个 JS 世界**（patchright 的隔离世界），页面主世界看得到、
# 默认世界却读不到 —— 表现为「注入成功却读到空」，且**不报错**（假成功）。
#
# 本探针同时验两件事：
#   ① 页面**主世界**能看到 hook 建的 `window.__CAP_*` 键（注入确实执行了）；
#   ② **默认世界**（读取侧）也能读到同一键（跨世界可见）。
# 二者不一致即判 red（这是「注入成功却读到空」复发时的唯一机械信号）。
#
# 注：patchright 的 `add_init_script` 走 `install_inject_route`
# （document 请求 `patchrightInitScript=True` 回退），设计上就是注入**主世界**；
# 此探针是把该设计前提钉成可验证判据，防回归。
# ---------------------------------------------------------------------------
TWO_WORLD_PROBE_JS = r"""
() => {
  // 在**页面主世界**读取 hook 是否真的建了键（不依赖读取侧世界）。
  const keys = ['__CAP_USERINFO__', '__CAP_WP_MESSAGE__'];
  const out = {};
  for (const k of keys) {
    try { out[k] = !!(window[k]); } catch (e) { out[k] = false; }
  }
  out.isMainWorld = (window === window.top);
  return out;
}
"""


def compare_two_world_visibility(main_world: dict, default_world: dict) -> dict:
    """比对「页面主世界」与「默认（读取）世界」对 CAP_* 键的可见性。

    参数：
      main_world    —— 在页面主世界取到的 {__CAP_USERINFO__: bool, ...}
                       （即 TWO_WORLD_PROBE_JS 的输出）
      default_world —— 在读取侧默认世界取到的同一组键的可见性

    判定（全部走机械判据，返回 leaks）：
      · 主世界**没有**任何 CAP_* 键 → 注入根本没执行（BCC-070 fatal）
      · 主世界有、默认世界无 → **跨世界不可见**（正是「注入成功却读到空」，BCC-070 fatal）
      · 两世界一致 → ok
    """
    keys = ("__CAP_USERINFO__", "__CAP_WP_MESSAGE__")
    leaks: list[dict] = []

    def _add(code, severity, detail):
        leaks.append({"code": code, "severity": severity, "detail": detail})

    main_visible = [k for k in keys if (main_world or {}).get(k)]
    default_visible = [k for k in keys if (default_world or {}).get(k)]

    if not main_visible:
        _add("BCC-070", "fatal",
             "页面主世界看不到任何 CAP_* hook 键 —— init script 未生效"
             "（注入失败或未执行）")
    for k in main_visible:
        if k not in default_visible:
            _add("BCC-070", "fatal",
                 f"{k} 在页面主世界可见、但默认（读取）世界不可见 —— "
                 f"跨世界不可见，读取侧将永远读到空（注入落进了隔离世界）")
    fatal = [x for x in leaks if x["severity"] == "fatal"]
    return {"ok": len(fatal) == 0, "leaks": leaks,
            "main_visible": main_visible, "default_visible": default_visible}

# ---------------------------------------------------------------------------
# Python 侧比对：浏览器真值(js_view) vs 项目档案(expected)
# 返回 leaks: [{code, severity, detail}]; all_ok: bool
# ---------------------------------------------------------------------------
def compare_with_profile(account: str, js_view: dict) -> dict:
    from utils.fingerprint import fingerprint_profile, kernel_version

    p = fingerprint_profile(account)
    leaks: list[dict] = []

    def _add(code: str, severity: str, detail: str) -> None:
        leaks.append({"code": code, "severity": severity, "detail": detail})

    # ---- 档案期望值 ----
    exp_cores = int(p.get("_cores") or 0)
    exp_tz = p.get("_timezone_arg") or ""
    exp_lang = p.get("_lang_arg") or ""
    exp_ua = p.get("ua") or ""
    exp_w = int(p.get("_window_w") or 0)
    exp_h = int(p.get("_window_h") or 0)
    # 2026-09-18 审查修复（LOW→实修）：`exp_os` 原先算出却从未使用（dead assignment），
    # 暗示着一个不存在的检查。现补上 `navigator.platform` vs 档案平台的比对。
    # ⚠️ 错误码用 BCC-068（「浏览器环境与项目档案不一致」），**不用 BCC-066**
    # （其语义是「原生函数被篡改」，写错就是 DSSCC 语义漂移）。
    # ⚠️ UA / 内核版本比对**已由下方 E9 覆盖**，此处绝不重复判定（否则同一缺陷刷两条告警）。
    #
    # ★ 2026-09-26 修复（ADR-016 D3-A 实机验证时抓出的**检测器误报**）：
    #   原判据把 `_platform_arg`（= "windows"，是**给内核的启动参数**
    #   `--fingerprint-platform=windows` 的枚举值）当成 `navigator.platform` 的期望值，
    #   但 `navigator.platform` 在 Windows 上的**真实值是 "Win32"**
    #   ⇒ 恒报 `期望 windows / 实际 Win32`（**假阳性**，每次审计固定刷一条）。
    #   实测证据：这是**同一台真实机器**的 Camoufox 自报值，且内核 webdriver=false、
    #   tamperedCount=0（无篡改）—— 说明浏览器没错，是**期望值取错源**。
    #   修法：建立「内核平台枚值 → navigator.platform 真值」的**显式映射**。
    _PLATFORM_REAL = {"windows": "win32", "macos": "macintel", "linux": "linux x86_64"}
    exp_os = _PLATFORM_REAL.get((p.get("_platform_arg") or "").lower(), "")
    exp_brand = p.get("_brand_arg") or "Chrome"
    exp_ver = (p.get("browser_version") or "").split(".")[0]
    kernel_v = (kernel_version() or "").split(".")[0]
    if exp_os and js_view.get("platform") and exp_os != str(js_view["platform"]).lower():
        _add("BCC-068", "warn", f"navigator.platform 与档案不一致: "
             f"期望 {exp_os} / 实际 {js_view['platform']}")
    # exp_ver / exp_brand 由下方 E9 消费（UA 与内核比对），保留以备将来扩展
    _ = (exp_ver, exp_brand)

    # ---- E1: navigator.webdriver / HeadlessChrome（rebrowser/sannysoft）----
    if js_view.get("webdriver") is True:
        _add("BCC-065", "fatal", "navigator.webdriver=true —— 自动化标志暴露")
    if js_view.get("headlessInUA"):
        _add("BCC-065", "fatal", "UA 含 HeadlessChrome —— 无头特征暴露")

    # ---- E2: 自动化注入对象（rebrowser）----
    ak = js_view.get("automationKeys") or []
    if ak:
        _add("BCC-065", "fatal", f"检测到自动化注入对象/属性: {ak}")

    # ---- E3: 原生函数被篡改（CreepJS toString）----
    tam = js_view.get("tamperedCount")
    if isinstance(tam, int) and tam > 0:
        bad = [k for k, v in (js_view.get("nativeChecks") or {}).items() if v is False]
        _add("BCC-066", "warn", f"{tam} 个原生函数被篡改: {bad}")

    # ---- E4: 软件渲染（实测 9-13 最强破绽）----
    if js_view.get("softRender") is True:
        _add("BCC-067", "fatal",
             f"WebGL 软件渲染特征: {js_view.get('webgl', {}).get('dbgRenderer') or ''} "
             "（GPU 伪装失效，检查是否被注入 --disable-gpu）")

    # ---- E5: 时区 vs 档案（liarjs 交叉）----
    tz = js_view.get("timezone") or ""
    if exp_tz and tz and tz != exp_tz:
        _add("BCC-068", "warn", f"时区不一致: 档案={exp_tz} 浏览器={tz}")

    # ---- E6: 语言 vs 档案 ----
    lang = js_view.get("language") or ""
    if exp_lang and lang and not lang.startswith(exp_lang.split("-")[0]):
        _add("BCC-068", "warn", f"主语言不一致: 档案={exp_lang} 浏览器={lang}")

    # ---- E7: CPU 核心数 vs 档案（单源化验收判据）----
    cores = int(js_view.get("hardwareConcurrency") or 0)
    if exp_cores and cores and cores != exp_cores:
        _add("BCC-068", "warn", f"CPU 核心数不一致: 档案={exp_cores} 浏览器={cores}")

    # ---- E8: screen vs 档案 viewport（单源化：viewport 决定 screen）----
    scr = js_view.get("screen") or {}
    if exp_w and scr.get("w") and (int(scr["w"]) != exp_w or int(scr.get("h") or 0) != exp_h):
        _add("BCC-068", "warn",
             f"screen 不一致: 档案 viewport={exp_w}x{exp_h} 浏览器={scr.get('w')}x{scr.get('h')}")
    # liarjs：三者全等 = 自动化特征
    if scr.get("w") and scr.get("aw") and scr.get("innerW") \
            and scr["w"] == scr["aw"] == scr["innerW"] and scr.get("h") == scr.get("ah") == scr.get("innerH"):
        _add("BCC-069", "warn",
             "screen/avail/inner 三者全等 —— 真实屏幕不可能，属自动化特征")

    # ---- E9: UA vs 档案 / 内核版本 ----
    ua = js_view.get("userAgent") or ""
    if exp_ua and ua and ua != exp_ua:
        # 允许 patchright 等的极小差异？不——档案 UA 就该是内核真实 UA，全比对
        _add("BCC-068", "warn", f"UA 不一致: 档案={exp_ua[:60]}… 浏览器={ua[:60]}…")
    # 浏览器声明的版本应与内核一致（kernel_version 单源）
    m = None
    try:
        import re as _re
        m = _re.search(r"Chrome/(\d+)\.", ua)
    except Exception:
        m = None
    if m and kernel_v and m.group(1) != kernel_v:
        _add("BCC-068", "warn", f"UA Chrome/{m.group(1)} 与内核 {kernel_v} 不一致")

    # ---- E10: Worker 与主线程不一致（liarjs/deviceandbrowserinfo）----
    w = js_view.get("worker")
    if isinstance(w, dict) and w.get("cores") and cores and int(w["cores"]) != cores:
        _add("BCC-069", "warn",
             f"Worker 硬件核心数({w['cores']}) 与主线程({cores}) 不一致 —— 跨线程值分叉")

    # ---- E11: canvas / audio 稳定性（种子指纹确定性判据）----
    if js_view.get("canvasStable") is False:
        _add("BCC-069", "warn", "canvas 指纹两次绘制不一致（同 context 内应恒定）")
    if js_view.get("audioStable") is False:
        _add("BCC-069", "warn", "audio 指纹两次渲染不一致（同 context 内应恒定）")

    # ---- E12: UA-Data 缺失（fingerprint-chromium 已知缺口，v0.43.14 记录过）----
    if js_view.get("uaData") is None:
        # 信息级：不判失败（内核本来就未伪装 Client Hints，v0.43.14 已记录）
        leaks.append({"code": "BCC-069", "severity": "info",
                      "detail": "navigator.userAgentData 为 null（内核未伪装 Client Hints，已知缺口）"})

    fatal = [x for x in leaks if x["severity"] == "fatal"]
    warn = [x for x in leaks if x["severity"] == "warn"]
    return {"ok": len(fatal) == 0,
            "leaks": leaks,
            "fatal_count": len(fatal),
            "warn_count": len(warn),
            "info_count": len(leaks) - len(fatal) - len(warn),
            "account": account,
            "js_view": js_view}
