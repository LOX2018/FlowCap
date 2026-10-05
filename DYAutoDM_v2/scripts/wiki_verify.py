"""用 LLM Wiki API 读回页面，确认新章节确实在知识库里。"""
import os
import sys
import json
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wiki_query import read_page  # noqa: E402

PAGES = [
    "wiki/08_私信列表与会话详情捕获方案.md",
]

# 要确认存在的章节标题关键词
WANT = [
    "二十七",
    "二十八",
    "二十九",
    "三十",
    "三十一",
    "shell:allow-open",
    "openExternal",
    "emptyOutDir",
]

for p in PAGES:
    d = read_page(p)
    if "error" in d:
        print("ERR:", d)
        continue
    c = d.get("content", "")
    print(f"=== {p} ===")
    print(f"长度: {len(c):,} 字符")
    print()
    import re

    heads = re.findall(r"^## .+$", c, re.M)
    print(f"章节数: {len(heads)}")
    for h in heads[-6:]:
        print(f"  {h}")
    print()
    print("关键内容校验:")
    for w in WANT:
        n = c.count(w)
        print(f"  {w:22s}: {n:3d}  {'OK' if n else '-- 缺失'}")
