"""
验证:前端拿到的 image_url 是否真能渲染(可访问 + 有效图片格式)。
并生成一个本地预览 HTML,人眼可直接确认「原图」真的显示出来了。
"""
import json
import os
import urllib.parse
import urllib.request

ACCT = "四川工伤张老师"
CID = "0:1:103242153689:3887506227210423"

BASE = "http://127.0.0.1:8000"
url = (f"{BASE}/api/messages/conversation?account="
       + urllib.parse.quote(ACCT) + "&conv_id=" + urllib.parse.quote(CID))

print(f"GET /api/messages/conversation ...")
with urllib.request.urlopen(url, timeout=60) as r:
    d = json.loads(r.read().decode())

conv = d.get("conversation") or {}
msgs = conv.get("messages") or []
imgs = [m for m in msgs if m.get("image_url")]
print(f"会话「{conv.get('name')}」  消息 {len(msgs)} 条  带 image_url {len(imgs)} 条\n")

MAGIC = {
    b"\xff\xd8\xff": "JPEG",
    b"\x89PNG": "PNG",
    b"GIF8": "GIF",
    b"RIFF": "WEBP",
}

ok = 0
rows = []
for m in imgs:
    u = m["image_url"]
    try:
        with urllib.request.urlopen(urllib.request.Request(u, method="GET"),
                                    timeout=20) as r:
            data = r.read()
            ct = r.headers.get("Content-Type", "") or ""
        fmt = "未知"
        for magic, name in MAGIC.items():
            if data[:len(magic)] == magic:
                fmt = name
                break
        good = fmt in ("JPEG", "PNG", "GIF", "WEBP")
        if good:
            ok += 1
        rows.append((u, len(data), ct, fmt, good))
        print(f"  {'✓' if good else '✗'} {os.path.basename(u)[:40]:40s} "
              f"{len(data):>8,}B  {ct:12s} {fmt}")
    except Exception as e:
        rows.append((u, 0, "", f"失败 {e}", False))
        print(f"  ✗ {u}  失败: {e}")

print(f"\n=== 可渲染 {ok}/{len(imgs)} ===")

# 生成预览页
figs = "\n".join(
    f'<figure><img src="{u}" loading="eager">'
    f'<figcaption>{os.path.basename(u)}<br>{fmt} · {sz:,}B</figcaption></figure>'
    for u, sz, ct, fmt, good in rows
)
html = f"""<!doctype html><meta charset="utf-8">
<title>私信原图渲染验证 v0.33.2</title>
<style>
body{{background:#0f1320;color:#e6e9f0;font-family:system-ui;padding:20px;margin:0}}
h1{{font-size:16px;color:#4ade80;margin:0 0 10px}}
.sum{{background:#1a2035;padding:10px 14px;border-radius:8px;margin-bottom:16px;font-size:13px;line-height:1.7}}
code{{background:#0b0f1c;padding:1px 5px;border-radius:3px;color:#7dd3fc}}
figure{{display:inline-block;margin:8px;vertical-align:top;max-width:430px}}
img{{max-width:420px;max-height:540px;border-radius:8px;border:1px solid #2a3350;display:block;background:#1a2035}}
figcaption{{font-size:11px;color:#8b93ad;margin-top:5px;font-family:ui-monospace;word-break:break-all}}
</style>
<h1>✅ 私信原图渲染验证 — DYAutoDM v0.33.2</h1>
<div class="sum">
会话「{conv.get('name')}」 · 消息 {len(msgs)} 条 · 图片 {len(imgs)} 张 · <b>可渲染 {ok}/{len(imgs)}</b><br>
图片来源：后端 <code>origin_image_resolver</code> 拉密文 → AES-256-GCM 解密 → HEIC 转 JPEG
→ 经 <code>/api/messages/origin_image/</code> 提供给前端 <code>&lt;img&gt;</code> 直接渲染。
</div>
{figs}
"""
out = r"C:\Users\LOX\Desktop\DYchajian\origin_image_verify.html"
with open(out, "w", encoding="utf-8") as f:
    f.write(html)
print(f"预览页: {out}")
