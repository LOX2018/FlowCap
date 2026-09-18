# -*- coding: utf-8 -*-
"""列出 PyInstaller 产物（onedir 的 exe）中**真正打包的 Python 模块名**。

代码在 exe 的 CArchive → PYZ 两层归档里，grep 二进制一律假阴性；
本脚本把 PYZ 解到临时文件后读取，给出权威判据（模块在/不在）。

用法:
    python scripts/diag/list_archive_modules.py <exe路径> [关键词...]
"""
from __future__ import annotations

import os
import sys
import tempfile

from PyInstaller.archive.readers import CArchiveReader
from PyInstaller.loader.pyimod01_archive import ZlibArchiveReader


def list_pyz_names(exe: str) -> list[str]:
    arc = CArchiveReader(exe)
    names: list[str] = []
    entries = list(arc.toc.items()) if hasattr(arc.toc, "items") else list(arc.toc)
    for name, _entry in entries:
        sname = str(name)
        if not sname.endswith(".pyz"):
            continue
        data = arc.extract(name)
        if isinstance(data, str):
            data = data.encode("latin-1", "replace")
        tmp = os.path.join(tempfile.mkdtemp(prefix="pyz_"), "a.pyz")
        with open(tmp, "wb") as f:
            f.write(data)
        z = ZlibArchiveReader(tmp)
        toc = z.toc
        names.extend(str(n) for n in (toc.keys() if hasattr(toc, "keys") else toc))
        try:
            os.remove(tmp)
            os.rmdir(os.path.dirname(tmp))
        except OSError:
            pass
    return names


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    exe = sys.argv[1]
    keys = sys.argv[2:]
    names = list_pyz_names(exe)
    print(f"归档 {exe}")
    print(f"  模块数 = {len(names)}")
    if keys:
        print("\n关键词匹配:")
        miss = 0
        for k in keys:
            norm = k.replace(".", "_")
            hit = [n for n in names if n.replace(".", "_") == norm]
            miss += 0 if hit else 1
            print(f"  {'OK  ' if hit else 'MISS'} {k:34s} -> {hit[:3]}")
        return 1 if miss else 0
    for n in sorted(names):
        print("   ", n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
