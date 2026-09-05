"""验证：抖音私信图片的远程加密链，能否在 BCC 页面（抖音域）内导出原图。

背景（2026-08-31）：
  - inline_pic        : 消息体内嵌 base64 WebP **缩略图**（160×213，2~5KB）
  - resource_url 四组 : thumb/medium/large/origin，**实测全部加密**
                        （熵 7.999，无图片魔数），后端与普通 <img src>
                        都显示破损图标
  - 但抖音前端自己**能**解密渲染（用户在网页上看得到图）

假设：在**抖音页面上下文**里（同域 + 完整登录态 + 前端解密逻辑），
      fetch 远程链 → canvas.toDataURL() → 得到可用的原图 base64。

本脚本通过 BCC 新增的 `POST /exec_js` 接口，在页面内跑验证 JS
（**只测 1 张图**，确认可行后再决定是否全量使用）。

用法（BCC 必须已启动）：
    python scripts/probe_img_origin.py [图片URL]
不传 URL 则自动从数据库取一条远程链图片。
"""
import json
import sqlite3
import sys
import urllib.error
import urllib.request
from pathlib import Path

DB = Path(r"C:\temp\dyautodm_test\data\dyautodm.db")
BCC = "http://127.0.0.1:10074"

# 页面内执行：三路并进去试，看哪条能拿到真图
JS = r"""
async (url) => {
  const out = {url: url.slice(0, 80) + '...', steps: []};

  // ---- 路 A：fetch 拿原始 bytes，看 Content-Type 与魔数 ----
  try {
    const r = await fetch(url, {credentials: 'include'});
    const buf = await r.arrayBuffer();
    const u8 = new Uint8Array(buf.slice(0, 16));
    out.fetch = {
      status: r.status,
      type: r.headers.get('content-type'),
      len: buf.byteLength,
      magic: Array.from(u8).map(b => b.toString(16).padStart(2, '0')).join(' '),
    };
  } catch (e) {
    out.fetchErr = String(e);
  }

  // ---- 路 B：<img> 直接加载（抖音域内走前端自己的解密） ----
  try {
    const img = new Image();
    img.crossOrigin = 'anonymous';
    const ok = await new Promise((resolve) => {
      const t = setTimeout(() => resolve(false), 15000);
      img.onload = () => { clearTimeout(t); resolve(true); };
      img.onerror = () => { clearTimeout(t); resolve(false); };
      img.src = url;
    });
    out.img = {loaded: ok, w: img.naturalWidth || 0, h: img.naturalHeight || 0};

    if (ok && img.naturalWidth > 0) {
      // canvas 导出（可能因跨域被 taint，故 try）
      try {
        const cv = document.createElement('canvas');
        cv.width = img.naturalWidth;
        cv.height = img.naturalHeight;
        cv.getContext('2d').drawImage(img, 0, 0);
        const d = cv.toDataURL('image/webp', 0.92);
        out.canvas = {bytes: d.length, dims: [cv.width, cv.height],
                      head: d.slice(0, 40)};
      } catch (e) {
        out.canvasErr = String(e);
      }
    }
  } catch (e) {
    out.imgErr = String(e);
  }

  // ---- 路 C：fetch bytes → Blob → createImageBitmap → canvas ----
  // （绕开 <img> 的跨域 taint，Blob 是同源的）
  try {
    const r = await fetch(url, {credentials: 'include'});
    const blob = await r.blob();
    out.blob = {type: blob.type, size: blob.size};
    const bmp = await createImageBitmap(blob).catch(e => null);
    if (bmp) {
      const cv = document.createElement('canvas');
      cv.width = bmp.width;
      cv.height = bmp.height;
      cv.getContext('2d').drawImage(bmp, 0, 0);
      const d = cv.toDataURL('image/webp', 0.92);
      out.bitmap = {dims: [bmp.width, bmp.height], bytes: d.length,
                    head: d.slice(0, 40)};
      out.dataUrl = d;  // 只在成功时回传（可能很大）
    } else {
      out.bitmapErr = 'createImageBitmap 返回 null（无法解码）';
    }
  } catch (e) {
    out.blobErr = String(e);
  }

  return out;
}
"""


def bcc_exec(js: str, arg=None, timeout: int = 60) -> dict:
    req = urllib.request.Request(
        f"{BCC}/exec_js",
        data=json.dumps({"js": js, "arg": arg, "timeout": timeout}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout + 30) as r:
        return json.loads(r.read().decode())


def main():
    # 取测试 URL
    if len(sys.argv) > 1:
        url = sys.argv[1]
    else:
        con = sqlite3.connect(str(DB))
        row = con.execute(
            "SELECT text FROM dm_messages "
            "WHERE text LIKE '[图片]%' AND text NOT LIKE '%data:image%' "
            "LIMIT 1").fetchone()
        con.close()
        if not row:
            print("[!] 库里没有远程链图片，请手动传 URL")
            sys.exit(1)
        url = row[0].replace("[图片]", "").strip()

    print(f"测试图片 URL:\n  {url[:120]}...\n")

    # 先确认 BCC 活着
    try:
        with urllib.request.urlopen(f"{BCC}/status", timeout=10) as r:
            st = json.loads(r.read().decode())
        print(f"BCC 状态: {json.dumps(st, ensure_ascii=False)[:160]}\n")
    except urllib.error.URLError as e:
        print(f"[!] BCC 未启动或不可达: {e}")
        print("    请先启动应用")
        sys.exit(1)

    # 执行
    print("=" * 66)
    print("页面内执行结果")
    print("=" * 66)
    try:
        resp = bcc_exec(JS, url, timeout=90)
    except Exception as e:
        print(f"[!] 调用 /exec_js 失败: {e}")
        sys.exit(1)

    if not resp.get("ok"):
        print(f"[!] 执行失败: {resp.get('msg')}")
        sys.exit(1)

    res = resp.get("result") or {}

    print("\n--- 路 A：fetch 原始 bytes ---")
    if res.get("fetch"):
        f = res["fetch"]
        print(f"  HTTP {f['status']}  type={f['type']}  {f['len']:,} 字节")
        print(f"  魔数: {f['magic']}")
        magic = f["magic"].replace(" ", "")
        if magic.startswith(("ffd8ff", "89504e47", "52494646", "47494638")):
            print("  >>> 是标准图片格式（未加密！）")
        else:
            print("  >>> 非标准图片魔数（加密或需解密）")
    else:
        print(f"  失败: {res.get('fetchErr')}")

    print("\n--- 路 B：<img> 加载 + canvas ---")
    if res.get("img"):
        im = res["img"]
        print(f"  loaded={im['loaded']}  {im['w']}×{im['h']}")
        if res.get("canvas"):
            c = res["canvas"]
            print(f"  canvas 成功: {c['dims'][0]}×{c['dims'][1]}, "
                  f"{c['bytes']:,} 字符")
            print(f"  dataUrl 头部: {c['head']}")
        elif res.get("canvasErr"):
            print(f"  canvas 失败: {res['canvasErr']}")
    else:
        print(f"  失败: {res.get('imgErr')}")

    print("\n--- 路 C：fetch → Blob → createImageBitmap → canvas ---")
    if res.get("blob"):
        b = res["blob"]
        print(f"  Blob: type={b['type']}  {b['size']:,} 字节")
    if res.get("bitmap"):
        bm = res["bitmap"]
        print(f"  >>> 解码成功！{bm['dims'][0]}×{bm['dims'][1]}, "
              f"base64 {bm['bytes']:,} 字符")
        print(f"  dataUrl 头部: {bm['head']}")
        print("\n  [结论] 路 C 可行 —— 能拿到真原图")
    elif res.get("bitmapErr"):
        print(f"  createImageBitmap 失败: {res['bitmapErr']}")
    else:
        print(f"  失败: {res.get('blobErr')}")

    # 保存结果供后续分析
    out_f = Path(__file__).parent / "_img_origin_probe.json"
    out_f.write_text(json.dumps(res, ensure_ascii=False, indent=2),
                     encoding="utf-8")
    print(f"\n完整结果已存: {out_f}")


if __name__ == "__main__":
    main()
