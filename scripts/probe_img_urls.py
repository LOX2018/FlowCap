"""探查私信图片消息在原始帧里的**全部** URL 字段与尺寸参数。

目的：解决「点开只能看到缩略图，看不到原图」。

已知（此前）：
  - inline_pic        : base64 WebP 缩略图（实测 160×213，2~5KB）
  - resource_url.origin_url_list[0] : 远程链（抖音私有加密，浏览器不可解码）
  - 远程链都带 `~tplv-x-get:thumb.image` 后缀 —— 这是**缩略图变换参数**，
    暗示同一资源可能存在其他尺寸变体。

本次要回答：
  1. 图片消息里除了 inline_pic / origin_url_list，还有哪些 URL 字段？
  2. 有没有 **非 thumb** 的原图 URL（如 ~tplv-obj.image、.jpeg 无后缀等）？
  3. tplv 参数有哪些取值？改参数能否拿到全尺寸？

用法（需 BCC 已启动且缓存有数据）：
    python scripts/probe_img_urls.py
"""
import json
import re
import sys
from collections import Counter
from pathlib import Path

RAW = Path(r"C:\temp\flowcap_test\ws_capture\init_raw.bin")
if not RAW.exists():
    print(f"[!] 找不到原始帧: {RAW}")
    sys.exit(1)

raw = RAW.read_bytes()
print(f"原始帧: {len(raw):,} 字节\n")

# ---- 1. 所有出现的 URL key 名 ----
print("=" * 60)
print("1. 图片相关 JSON key 统计")
print("=" * 60)
keys = [
    "inline_pic", "origin_url", "origin_url_list", "url_list",
    "static_url", "animate_url", "download_url", "thumb_url",
    "medium_url", "large_url", "uri", "url_prefix",
]
for k in keys:
    n = raw.count(k.encode())
    if n:
        print(f"  {k:22s}: {n}")

# ---- 2. tplv 变换参数取值 ----
print()
print("=" * 60)
print("2. tplv 变换参数取值（判断是否有原图变体）")
print("=" * 60)
tplv = Counter()
for m in re.finditer(rb"~tplv-[A-Za-z0-9\-_:.]+\.image", raw):
    tplv[m.group().decode("ascii", "ignore")] += 1
for v, n in tplv.most_common(20):
    print(f"  {n:4d}  {v}")

# ---- 3. 抽取一条完整图片消息的 JSON ----
print()
print("=" * 60)
print("3. 完整图片消息结构（第一条）")
print("=" * 60)


def balanced(s: bytes, start: int) -> bytes | None:
    """从 start（必须是 '{'）开始取括号平衡的 JSON 片段。"""
    if start < 0 or start >= len(s) or s[start:start + 1] != b"{":
        return None
    depth, i, n, instr, esc = 0, start, len(s), False, False
    while i < n:
        ch = s[i:i + 1]
        if instr:
            if esc:
                esc = False
            elif ch == b"\\":
                esc = True
            elif ch == b'"':
                instr = False
        else:
            if ch == b'"':
                instr = True
            elif ch == b"{":
                depth += 1
            elif ch == b"}":
                depth -= 1
                if depth == 0:
                    return s[start:i + 1]
        i += 1
    return None


# 找带 resource_url 的对象
idx = raw.find(b'"resource_url"')
found = None
while idx != -1:
    # 往前找最近的 '{'
    st = raw.rfind(b"{", max(0, idx - 3000), idx)
    if st != -1:
        frag = balanced(raw, st)
        if frag and b"resource_url" in frag:
            found = frag
            break
    idx = raw.find(b'"resource_url"', idx + 1)

if not found:
    print("  [!] 未找到完整 resource_url 对象")
else:
    try:
        obj = json.loads(found.decode("utf-8", "ignore"))
    except Exception as e:
        obj = None
        print(f"  [!] JSON 解析失败: {e}")
        print(f"  原始片段: {found[:600]!r}")
    if obj:
        # 打印结构，但把长 base64 截断
        def trunc(o, limit=80):
            if isinstance(o, dict):
                return {k: trunc(v, limit) for k, v in o.items()}
            if isinstance(o, list):
                return [trunc(v, limit) for v in o[:3]]
            if isinstance(o, str) and len(o) > limit:
                return o[:limit] + f"...<{len(o)}chars>"
            return o
        print(json.dumps(trunc(obj, 60), ensure_ascii=False, indent=2)[:2500])

# ---- 4. 统计所有图片 URL 的 host 与路径形态 ----
print()
print("=" * 60)
print("4. 图片 URL 路径形态（看有没有非 thumb 的原图）")
print("=" * 60)
urls = set()
for m in re.finditer(rb"https?://[A-Za-z0-9\-.]*douyinpic\.com/[^\s\"'\\]+", raw):
    try:
        u = m.group().decode("utf-8", "ignore")
    except Exception:
        continue
    urls.add(u)
print(f"  去重后 URL 数: {len(urls)}\n")
for u in sorted(urls)[:12]:
    # 提取关键后缀
    mm = re.search(r"~tplv-[A-Za-z0-9\-_:.]+\.image", u)
    suffix = mm.group() if mm else "(无 tplv 后缀)"
    print(f"  {suffix}")
    print(f"    {u[:110]}")
