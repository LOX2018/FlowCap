# -*- coding: utf-8 -*-
"""离线可复现验证（合成页，结构对齐 2026-09-29 实机真实 DOM）。
断言：降级链命中 + 改版后自适应重定位 + **误命中必须为 0** + 校验器负控。

跑法：python verify_offline.py （解释器需装 scrapling base 包）
"""
import os, sys, traceback
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from locator import AdaptiveLocator, LOGIN_TARGETS, validate

# 基线：结构与真实 DOM 一致（tab=span.C6OZQwMA 无 id；提交按钮=div#douyin_login_comp_btn_id）
BASE = """
<html><body>
  <div id="nav"><span>首页</span><span>登录</span><button>登录</button></div>
  <div id="login-full-panel-AbC123">
   <div id="douyin-login-new-id" class="douyin_login_new_class">
    <div class="tabbar">
      <span class="C6OZQwMA">扫码登录</span>
      <span class="C6OZQwMA">验证码登录</span>
    </div>
    <div id="douyin_login_comp_scan_code" class="pE9ZOPEo">
      <div id="animate_qrcode_container" class="J2iCN0Aj"><svg><image/></svg></div>
    </div>
    <div id="douyin_login_comp_mobile_code" class="Bl7zNBFy">
      <input id="normal-input" name="normal-input" type="tel" maxlength="50" placeholder="请输入手机号">
      <input id="button-input" name="button-input" type="tel" maxlength="6" placeholder="请输入验证码">
      <div id="douyin_login_comp_btn_id" class="r7j70rK2">登录</div>
    </div>
   </div>
  </div>
</body></html>
"""

# 改版 A：哈希类名全变、id 全改、name 改、tab 文案加空格（语义在）
DRIFT_A = """
<html><body>
  <div id="nav"><span>首页</span><span>登录</span><button>登录</button></div>
  <div id="login-full-panel-XyZ789">
   <div class="login-wrap-9f">
    <div class="tabbar-x">
      <span class="x9Fa2"> 扫码登录 </span>
      <span class="x9Fa2"> 验证码登录 </span>
    </div>
    <div id="z-scan-x" class="q1">
      <div id="z-qr-x"><svg><image/></svg></div>
    </div>
    <div id="z-mobile-x" class="q2">
      <input id="z-phone-x" name="mobile-2" type="tel" maxlength="50" placeholder="请输入手机号">
      <input id="z-code-x" name="grid-verify-y" type="tel" maxlength="6" placeholder="请输入验证码">
      <div id="z-btn-x" class="btn-9f">登录</div>
    </div>
   </div>
  </div>
</body></html>
"""

# 干扰页：导航栏与搜索区都含「登录」/无关 input，不得误命中
TRAP = """
<html><body>
  <div id="nav"><span>首页</span><span>登录</span><button>登录</button></div>
  <div class="search"><input name="kw" placeholder="搜索"> <button>搜索</button></div>
  <div id="login-full-panel-Zz">
   <div class="login-wrap">
    <div class="tabbar-y"><span class="k1">扫码登录</span><span class="k1">验证码登录</span></div>
    <input name="m9" type="tel" maxlength="50" placeholder="请输入手机号">
    <input name="c9" type="tel" maxlength="6" placeholder="请输入验证码">
    <div class="btn-x">登录</div>
   </div>
  </div>
</body></html>
"""

def run(tag, html, loc, expect):
    print(f"\n===== {tag} =====")
    okc = 0
    for t in LOGIN_TARGETS:
        r = loc.locate(html, t)
        exp = expect.get(t.key, True)
        flag = "OK " if r.ok else "MISS"
        good = (r.ok == exp)
        okc += 1 if good else 0
        extra = f" attrs={dict(getattr(r.element,'attrib',{}))}" if r.ok else f" ({r.reason})"
        print(f"  [{'✓' if good else '✗'}] {flag} {t.key:11} strat={r.strategy or '-':10}{extra[:120]}")
    return okc, len(LOGIN_TARGETS)

def main():
    import tempfile
    db = os.path.join(tempfile.mkdtemp(prefix="spk2_"), "e.sqlite")
    loc = AdaptiveLocator(storage_file=db)
    print("播种(基线):", loc.seed(BASE, LOGIN_TARGETS))

    r = {}
    r["base"]  = run("基线页（全部应命中）", BASE, loc, {})
    r["driftA"] = run("改版 A（id/name/class 全漂移，应全靠文本/自适应命中）", DRIFT_A, loc, {})
    r["trap"]  = run("干扰页（导航栏同名『登录』，不得误命中）", TRAP, loc, {})

    # 负控：校验器必须拒掉「同名但错对象/错域」的元素
    print("\n===== 负控：校验器判别力 =====")
    from scrapling.parser import Selector
    s = Selector(TRAP)
    nav_btn = s.css("#nav button")
    t_sub = [t for t in LOGIN_TARGETS if t.key == "btn_submit"][0]
    t_phone = [t for t in LOGIN_TARGETS if t.key == "input_phone"][0]
    if nav_btn:
        g, w = validate(nav_btn[0], t_phone)
        print(f"  导航栏 button 冒充手机框 -> {g} ({w})  [期望 False]")
    nav_span = s.css("#nav span")
    if nav_span:
        g, w = validate(nav_span[0], t_sub)
        print(f"  导航栏『登录』span 冒充提交按钮 -> {g} ({w})  [期望 False]")
    search = s.css('input[name="kw"]')
    if search:
        g, w = validate(search[0], t_phone)
        print(f"  搜索框 冒充手机框 -> {g} ({w})  [期望 False]")

    print("\n== 汇总（期望全部命中，且误命中 0）==")
    for k, (c, n) in r.items():
        print(f"  {k:7} {c}/{n}")
    tot = sum(c for c, n in r.values())
    n = sum(n for c, n in r.values())
    print(f"  合计 {tot}/{n}")
    print("VERDICT:", "PASS" if tot == n else "FAIL")

if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc(); sys.exit(1)
