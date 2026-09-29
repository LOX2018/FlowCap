# -*- coding: utf-8 -*-
"""正确编码下重取真实登录面板结构 + 定位 tab 文案的真实位置。只读。"""
import os
from lxml import html as LH

HERE = os.path.dirname(os.path.abspath(__file__))
snap = os.path.join(HERE, "_live", "live_login.html")

parser = LH.HTMLParser(encoding="utf-8")
doc = LH.parse(snap, parser)
root = doc.getroot()

def dtext(el):
    parts = []
    if el.text and el.text.strip(): parts.append(el.text.strip())
    for ch in el.iterchildren():
        if ch.tail and ch.tail.strip(): parts.append(ch.tail.strip())
    return " ".join(parts)

# 1) tab 文案到底在哪
for needle in ["扫码登录", "验证码登录", "登录其他账号", "一键登录", "获取验证码"]:
    hits = [el for el in root.iter() if isinstance(el.tag, str) and needle in (dtext(el) or "") or (isinstance(el.tag,str) and needle in (el.text or ""))]
    print(f"== 文案 {needle!r}: 命中 {len(hits)}")
    for el in hits[:4]:
        print(f"    <{el.tag}> id={el.get('id')!r} class={(el.get('class') or '')[:36]!r} direct={dtext(el)!r}")

# 2) 登录面板真实结构（登录相关 id 元素 + 其直接文本/属性）
print("\n== 登录相关元素一览（id 含 login/comp/input/qrcode/btn/captcha）==")
for el in root.iter():
    if not isinstance(el.tag, str): continue
    eid = el.get("id") or ""
    if any(k in eid.lower() for k in ("login","qrcode","scan","btn_id","comp","verify","captcha","input")):
        attrs = {a: el.get(a) for a in ("name","placeholder","type","maxlength") if el.get(a)}
        print(f"  <{el.tag:7}> id={eid:40} text={dtext(el)[:24]!r} {attrs}")

# 3) 二维码容器里到底是什么（img/canvas/svg）
q = root.xpath('//*[@id="animate_qrcode_container"]')
if q:
    el = q[0]
    print("\n== #animate_qrcode_container 后代标签统计 ==")
    from collections import Counter
    c = Counter(ch.tag for ch in el.iter() if isinstance(ch.tag, str))
    print("  ", dict(c))
