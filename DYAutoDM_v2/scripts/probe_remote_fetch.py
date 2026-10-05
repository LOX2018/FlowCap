"""最直接验证：抖音私信图片的远程链，用**普通浏览器请求**能不能拿到图片？

此前的结论「远程链是私有加密，浏览器报破损图标」可能站不住 ——
在 BCC 页面测试时我设了 `img.crossOrigin = 'anonymous'`，这会强制走
CORS 检查，CDN 不返回 CORS 头就失败。**但普通 <img src>（不带
crossOrigin）根本不检查 CORS**，很可能能正常加载。

本脚本模拟普通浏览器请求（带完整 Referer / UA），看：
  - HTTP 状态码
  - Content-Type
  - 前 16 字节魔数（判断是不是真图片）

魔数对照：
  ff d8 ff      -> JPEG
  89 50 4e 47   -> PNG
  52 49 46 46   -> RIFF（WEBP）
  47 49 46 38   -> GIF
"""
import re
import sqlite3
import sys
import urllib.request
from pathlib import Path

DB = Path(r"C:\temp\dyautodm_test\data\dyautodm.db")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

MAGIC = {
    "ffd8ff": "JPEG",
    "89504e47": "PNG",
    "52494646": "RIFF/WebP",
    "47494638": "GIF",
}


def probe(url: str, label: str, referer: str | None = None) -> bool:
    headers = {"User-Agent": UA, "Accept": "image/avif,image/webp,image/*,*/*;q=0.8"}
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read(4096)
            ctype = r.headers.get("Content-Type", "")
            status = r.status
    except Exception as e:
        print(f"  [{label}] 请求失败: {type(e).__name__}: {e}")
        return False

    magic = data[:16].hex()
    kind = "未知"
    for k, v in MAGIC.items():
        if magic.startswith(k):
            kind = v
            break

    ok = kind != "未知"
    flag = "OK" if ok else "!!"
    print(f"  [{label}] HTTP {status}  type={ctype}")
    print(f"         魔数: {magic[:32]}")
    print(f"         {flag} 判定: {kind}")
    return ok


def main():
    con = sqlite3.connect(str(DB))
    rows = con.execute(
        "SELECT text FROM dm_messages "
        "WHERE text LIKE '[图片]%' AND text NOT LIKE '%data:image%' LIMIT 2"
    ).fetchall()
    # 同时取一条「内联缩略图」消息里粘着的 [原图] URL
    rows2 = con.execute(
        "SELECT text FROM dm_messages WHERE text LIKE '%data:image%' LIMIT 1"
    ).fetchall()
    con.close()

    urls = []
    for (t,) in rows:
        u = t.replace("[图片]", "").strip()
        urls.append(("远程链(无inline_pic)", u))
    for (t,) in rows2:
        m = re.search(r"\[原图\]\s*(https?://\S+)", t)
        if m:
            urls.append(("内联图的[原图]URL", m.group(1)))

    if not urls:
        print("[!] 库里没有可测的远程链")
        sys.exit(1)

    any_ok = False
    for label, url in urls:
        print("=" * 66)
        print(f"{label}")
        print(f"  {url[:110]}...")
        print("-" * 66)
        # 1) 裸请求（不带 Referer）
        if probe(url, "裸请求"):
            any_ok = True
        print()
        # 2) 带抖音 Referer（模拟页面内加载）
        if probe(url, "带 douyin Referer", "https://www.douyin.com/"):
            any_ok = True
        print()

    print("=" * 66)
    if any_ok:
        print(">>> 结论：远程链**能**拿到真图片")
        print("    之前「私有加密/破损图标」的结论不成立 ——")
        print("    失败原因是页面内 fetch 的 CORS，不是加密。")
        print("    前端可直接 <img src=远程链> 显示原图。")
    else:
        print(">>> 结论：远程链确实拿不到图片（加密或需鉴权）")
        print("    点击后无原图可显示，只能用缩略图放大。")


if __name__ == "__main__":
    main()
