"""Dump 一条图片消息的完整 resource_url 结构（四个尺寸字段）。

上一步 probe_img_urls.py 发现：原始帧里 thumb_url / medium_url /
large_url / origin_url **四个尺寸字段各有 44 个**，此前只用了
inline_pic + origin_url_list[0]，漏了 medium/large。

本脚本 dump 完整结构，确认四者的 URL 与 tplv 参数对应关系。
"""
import json
import re
import sys
from pathlib import Path

RAW = Path(r"C:\temp\dyautodm_test\ws_capture\init_raw.bin")
raw = RAW.read_bytes()
print(f"原始帧: {len(raw):,} 字节\n")

# 找 thumb_url 所在的对象
idx = raw.find(b'"thumb_url"')
print(f"thumb_url 首次出现位置: {idx}\n")


def balanced(s: bytes, start: int) -> bytes | None:
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


# 打印 thumb_url 前后 1200 字节原始文本（最可靠）
print("=" * 70)
print("thumb_url 附近原始文本（前 300 / 后 900 字节）")
print("=" * 70)
seg = raw[max(0, idx - 300): idx + 900]
txt = seg.decode("utf-8", "ignore")
print(txt)
