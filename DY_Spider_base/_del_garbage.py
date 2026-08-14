# -*- coding: utf-8 -*-
"""删除两个 0 字节的乱码残留文件（之前 PowerShell 补 BOM 失败的产物）。"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
removed = []
for name in os.listdir(HERE):
    p = os.path.join(HERE, name)
    if os.path.isfile(p) and os.path.getsize(p) == 0:
        # 0 字节且文件名含 \ufffd（乱码）的残留
        if "\ufffd" in name:
            os.remove(p)
            removed.append(repr(name.encode("utf-8", "backslashreplace")))
print("removed:", removed)
