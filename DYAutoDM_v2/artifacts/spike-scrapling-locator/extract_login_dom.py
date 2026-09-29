# -*- coding: utf-8 -*-
"""从真实捕获的登录页 DOM 快照里，提取登录相关子树的【真实结构】，供定义定位目标。
只读、不猜。"""
import os, sys
from lxml import html as LH

HERE = os.path.dirname(os.path.abspath(__file__))
snap = os.path.join(HERE, "_live", "live_login.html")
doc = LH.parse(snap)
root = doc.getroot()

def dtext(el):
    parts = []
    if el.text and el.text.strip():
        parts.append(el.text.strip())
    for ch in el.iterchildren():
        if ch.tail and ch.tail.strip():
            parts.append(ch.tail.strip())
    return " ".join(parts)

def dump(el, depth=0, maxdepth=6, buf=None):
    if buf is None: buf = []
    if depth > maxdepth: return buf
    pad = "  " * depth
    cls = (el.get("class") or "")[:40]
    line = f"{pad}<{el.tag} id={el.get('id')!r} class={cls!r}"
    t = dtext(el)
    if t: line += f" text={t[:40]!r}"
    for a in ("name", "placeholder", "type", "maxlength"):
        if el.get(a): line += f" {a}={el.get(a)!r}"
    buf.append(line)
    for ch in el.iterchildren():
        dump(ch, depth+1, maxdepth, buf)
    return buf

print("== 页面里出现的登录相关 id ==")
for el in root.iter():
    if not isinstance(el.tag, str): continue
    eid = el.get("id") or ""
    if any(k in eid.lower() for k in ("login", "qrcode", "scan", "btn_id", "comp", "verify", "captcha")):
        print(f"  <{el.tag}> id={eid!r} class={(el.get('class') or '')[:40]!r} direct_text={dtext(el)[:40]!r}")

for anchor in ["douyin-login-new-id", "douyin_login_comp_btn_id",
               "douyin_login_comp_scan_code", "animate_qrcode_container"]:
    els = root.xpath(f'//*[@id="{anchor}"]')
    print(f"\n===== 子树: #{anchor} (命中 {len(els)}) =====")
    if els:
        for ln in dump(els[0], maxdepth=5):
            print(ln[:200])
