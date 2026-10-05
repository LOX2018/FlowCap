# -*- coding: utf-8 -*-
"""产物归属证明：把新功能的特征串从 PyInstaller 产物的 PYZ 归档里读回来。

为什么必须这样做（铁律）：
  - onedir 的 exe 只有 ~28MB，业务代码在 exe 内 **CArchive → PYZ** 两层归档里；
    用 grep 搜 exe / `_internal` **必然假阴性**（连 `_detect_existing_bcc` 这种
    唯一函数名都搜不到）——历史多次据此误判「修复没打进产物」。
  - 唯一可靠的静态判据是**解归档**：CArchive → 取出 .pyz → ZlibArchive 取模块
    code object → `marshal.dumps` 还原字节 → 在其中断言特征串。

用法：
    python scripts/diag/prove_live_ai_wiring_in_artifact.py [exe路径]
退出码 0 = 全部断言通过（说明本次改动确实进了这份产物）。
"""
from __future__ import annotations

import marshal
import os
import sys
import tempfile

from PyInstaller.archive.readers import CArchiveReader
from PyInstaller.loader.pyimod01_archive import ZlibArchiveReader

# 新功能的唯一特征串（每一项都只可能来自本次改动，见 v0.44.14 提交）
CHECKS: dict[str, list[str]] = {
    "core.auto_dm": ["_make_gen_dm_message", "live-ai", "SEND-039", "SEND-040",
                     "target_acct"],
    # ⚠️ 断言原语必须与**编译产物的真实形态**对齐（本脚本开发中踩过）：
    #   - `inspect.isawaitable(...)` 在字节码里是 co_names 的两个独立名字
    #     ("inspect" / "isawaitable")，**不存在** "inspect.isawaitable" 字面量；
    #   - `List[Union[str, dict]]` 的类型标注在**无 `from __future__ import
    #     annotations`** 时会被**求值成对象**，也不存在该字面量。
    #   所以按「只会因本次改动而出现的名字」断言。
    "core.dispatch": ["gen_dm_message", "isawaitable", "SEND-038"],
    # `Union` = 声明被放宽的标记（旧代码 `list[str]` 没有 Union 的痕跡）；
    # 不要断言 `Dict`（本实现用的是内置 `dict`，不存在该名字 —— 写错会假红）。
    "models.task": ["Union"],
    "services.ai_reply": ["generate_dm_for_live"],
}


def _open_pyz(exe: str) -> ZlibArchiveReader:
    """CArchive → 取 .pyz 条目 → 落临时文件 → 返回 ZlibArchiveReader。"""
    arc = CArchiveReader(exe)
    entries = list(arc.toc.items()) if hasattr(arc.toc, "items") else list(arc.toc)
    for name, _entry in entries:
        if not str(name).endswith(".pyz"):
            continue
        data = arc.extract(name)
        if isinstance(data, str):
            data = data.encode("latin-1", "replace")
        d = tempfile.mkdtemp(prefix="pyz_")
        tmp = os.path.join(d, "a.pyz")
        with open(tmp, "wb") as f:
            f.write(data)
        return ZlibArchiveReader(tmp)
    raise RuntimeError("exe 的 CArchive 里没有 .pyz 条目")


def _module_bytes(z: ZlibArchiveReader, modname: str) -> bytes | None:
    """取模块 code object 并 marshal 成字节（名字两种写法都试）。"""
    cands = [modname, modname.replace(".", "_"), modname.replace(".", "/")]
    for cand in cands:
        try:
            code = z.extract(cand)
        except Exception:
            code = None
        if code is not None:
            return marshal.dumps(code)
    return None


def main() -> int:
    root = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        "..", ".."))
    if len(sys.argv) > 1:
        exe = sys.argv[1]
    else:
        exe = os.path.join(root, "src-tauri", "binaries",
                           "dyautodm-backend-x86_64-pc-windows-msvc.exe")
    print(f"[info] 产物 exe : {exe}")
    print(f"[info] mtime    : {__import__('time').strftime('%Y-%m-%d %H:%M:%S', __import__('time').localtime(os.path.getmtime(exe)))}")
    if not os.path.exists(exe):
        print("[FAIL] exe 不存在")
        return 1

    z = _open_pyz(exe)
    names = z.toc.keys() if hasattr(z.toc, "keys") else z.toc
    print(f"[info] PYZ 模块数 : {len(list(names))}")

    fails: list[str] = []
    for mod, needles in CHECKS.items():
        blob = _module_bytes(z, mod)
        if blob is None:
            fails.append(f"{mod}: 取不到模块")
            print(f"  [FAIL] {mod}: 取不到模块")
            continue
        for n in needles:
            ok = n.encode("utf-8") in blob
            print(f"  [{'PASS' if ok else 'FAIL'}] {mod:18s} 含 {n!r:26s} (模块字节 {len(blob)})")
            if not ok:
                fails.append(f"{mod}: 缺 {n}")

    print()
    if fails:
        print("产物归属证明失败：")
        for f in fails:
            print("  -", f)
        return 1
    print("产物归属证明通过：本次改动确实进了这份产物。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
