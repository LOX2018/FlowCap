# -*- coding: utf-8 -*-
"""在【真实捕获的抖音登录页 DOM】上验证：① 直接定位 ② 人为改版后的自适应重定位。"""
import os, re, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from locator import AdaptiveLocator, LOGIN_TARGETS

snap = os.path.join(HERE, "_live", "live_login.html")
html = open(snap, encoding="utf-8").read()
print(f"真实 DOM 长度 = {len(html)}")
for probe in ["douyin-login-new-id", "douyin_login_comp_scan_code",
              "douyin_login_comp_btn_id", "animate_qrcode_container",
              "normal-input", "button-input", "扫码登录", "验证码登录"]:
    print(f"  真实DOM 含 {probe!r}: {probe in html}  (x{html.count(probe)})")

db = os.path.join(tempfile.mkdtemp(prefix="real_"), "e.sqlite")
loc = AdaptiveLocator(storage_file=db)
print("\n播种(真实DOM):", loc.seed(html, LOGIN_TARGETS))

print("\n== ① 真实 DOM 直接定位 ==")
for t in LOGIN_TARGETS:
    r = loc.locate(html, t)
    if r.ok:
        print(f"  OK   {t.key:11} strat={r.strategy:10} attrs={dict(getattr(r.element,'attrib',{}))}")
    else:
        print(f"  MISS {t.key:11} {r.reason}  last={r.attempts[-2:]}")

# ── 人为改版：把真实 DOM 里的 name/id/class 全重写（模拟抖音改版）──
drift = html
drift = re.sub(r'name="(normal-input|button-input)"', r'name="dn_\1_v2"', drift)
drift = re.sub(r'id="(douyin-login-new-id|douyin_login_comp_scan_code|douyin_login_comp_btn_id)"',
               r'id="z-\1-x"', drift)
drift = re.sub(r'class="([^"]{0,60})"',
               lambda m: 'class="h%04d"' % (sum(map(ord, m.group(1))) % 9973), drift)
print(f"\n改版后 DOM 长度 = {len(drift)}；旧锚点是否仍字面存在: "
      f"normal-input={('name=\"normal-input\"' in drift)} "
      f"comp_scan_code={('douyin_login_comp_scan_code' in drift)}")

print("\n== ② 改版后自适应重定位（存储来自真实 DOM 指纹）==")
hits = 0
for t in LOGIN_TARGETS:
    r = loc.locate(drift, t)
    if r.ok:
        hits += 1
        print(f"  OK   {t.key:11} strat={r.strategy:12} attrs={dict(getattr(r.element,'attrib',{}))}")
    else:
        print(f"  MISS {t.key:11} {r.reason}")
print(f"\n结果：{hits}/{len(LOGIN_TARGETS)} 在【真实 DOM 人为改版后】仍被定位到")
