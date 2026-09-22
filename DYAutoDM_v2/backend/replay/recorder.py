# -*- coding: utf-8 -*-
"""回放样本记录器 —— fixtures 的**唯一写入通道**。

用它而不是手 cp，是为了保证三件事同时发生：
  ① 字节落盘；② sha256/size 进 manifest；③ 样本不可变（再次录制必须显式 `--update`）。

## 何时该录（契约铁律，勿滥用）

只在**一次真实实拉已确认契约成立**之后录一次。样本一旦冻结即视为"世界的一个快照"，
后续所有回归都跑它。**不允许**用回放证明"抖音侧契约仍然有效"——
那必须重新实拉（见 `replay/__init__.py` 的边界表）。

## 用法

    # 从已有的真实样本文件录制（最常用）
    python -m replay.recorder --from <path> --name init_packet \
        --desc "get_message_by_init 首包（cmd2043 包裹）" \
        --provenance "2026-09-06 实拉，测试根备份回收" \
        --account-uid 316276709526638 \
        --expect convs=44 --expect short_id_coverage=44

    # 有意更新一个已存在的样本（会提示并覆盖）
    python -m replay.recorder --update <name> --from <new path>

    # 只列清单
    python -m replay.recorder --list
"""

import argparse
import datetime
import json
import os
import shutil
import sys

if __package__ in (None, ""):  # 允许 `python replay/recorder.py`
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from replay import loader  # type: ignore
else:
    from . import loader


def _parse_expect(items):
    """--expect k=v（v 自动转 int/float/str）→ dict"""
    out = {}
    for it in items or []:
        if "=" not in it:
            raise SystemExit(f"--expect 需要 k=v 形式，收到 {it!r}")
        k, v = it.split("=", 1)
        for cast in (int, float):
            try:
                out[k.strip()] = cast(v)
                break
            except ValueError:
                continue
        else:
            out[k.strip()] = v
    return out


def record(src: str, name: str, desc: str, provenance: str, account_uid=None,
           expected=None, filename=None, update: bool = False) -> dict:
    if not os.path.exists(src):
        raise SystemExit(f"源样本不存在：{src}")
    with open(src, "rb") as f:
        blob = f.read()
    if not blob:
        raise SystemExit(f"源样本为空：{src}")

    m = loader._read_manifest()
    if name in m and not update:
        raise SystemExit(
            f"样本 {name!r} 已存在（sha256={m[name].get('sha256')}）。\n"
            f"若确为有意更新，请加 --update。"
        )

    ext = os.path.splitext(src)[1] or ".bin"
    rel = f"{name}/{name}{ext}" if not filename else f"{name}/{filename}"
    dst = os.path.join(loader.fixtures_dir(), rel)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copyfile(src, dst)

    with open(dst, "rb") as f:
        saved = f.read()

    entry = {
        "file": rel,
        "sha256": loader.sha256_of(saved),
        "size": len(saved),
        "desc": desc,
        "provenance": provenance,
        "recorded_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    if account_uid:
        entry["account_uid"] = str(account_uid)
    if expected:
        entry["expected"] = expected
    if name in m:
        entry["previous_sha256"] = m[name].get("sha256")
    m[name] = entry
    loader.write_manifest(m)
    return entry


def main(argv=None):
    ap = argparse.ArgumentParser(description="回放样本记录器（fixtures 唯一写通道）")
    ap.add_argument("--from", dest="src", help="源样本路径")
    ap.add_argument("--name", help="样本名（manifest 键）")
    ap.add_argument("--desc", default="", help="样本说明")
    ap.add_argument("--provenance", default="", help="来源（何时何地实拉）")
    ap.add_argument("--account-uid", default=None)
    ap.add_argument("--expect", action="append", help="期望读数 k=v，可重复")
    ap.add_argument("--filename", default=None, help="落盘文件名（默认沿用源扩展名）")
    ap.add_argument("--update", action="store_true", help="覆盖已存在的样本")
    ap.add_argument("--list", action="store_true", help="列出清单后退出")
    a = ap.parse_args(argv)

    if a.list:
        m = loader._read_manifest()
        if not m:
            print("（清单为空）")
            return 0
        for k in sorted(m):
            e = m[k]
            print(f"{k:24s} {e['size']:>9d}B  {e['sha256'][:16]}  {e.get('desc','')}")
        return 0

    if not (a.src and a.name):
        ap.error("需要 --from 与 --name（或用 --list）")

    e = record(a.src, a.name, a.desc, a.provenance,
               account_uid=a.account_uid, expected=_parse_expect(a.expect),
               filename=a.filename, update=a.update)
    print(json.dumps(e, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
