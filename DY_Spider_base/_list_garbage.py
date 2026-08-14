# -*- coding: utf-8 -*-
"""列出 DY_Spider_base 下的乱码/残留文件，写入文件避免 GBK 打印。"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
out = os.path.join(HERE, "_garbage_list.txt")

lines = []
for name in os.listdir(HERE):
    p = os.path.join(HERE, name)
    if os.path.isfile(p):
        # 用字节形式记录文件名，避免编码问题
        lines.append("FILE bytes=%r  size=%d" % (name.encode('utf-8', 'backslashreplace'), os.path.getsize(p)))

with open(out, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
print("wrote", out)
