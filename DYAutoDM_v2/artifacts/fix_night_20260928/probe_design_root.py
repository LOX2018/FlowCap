# -*- coding: utf-8 -*-
"""一次性探测脚本（不属于 unittest，不会被 discover 收集）。

用途：对 design 真实数据根 C:\\temp\\dyautodm_design 做**递归指纹快照**
（所有 .db 文件的 path / size / mtime_ns / sha256），并**只读**导出被怀疑的
kv_store 键（ai_agents / ai_account_agent）内容。

严格只读：不写、不删、不改 design 根内任何文件。禁止 LIKE 模糊删。
用法：
    python probe_design_root.py --out before.json
    python probe_design_root.py --out after.json
    python probe_design_root.py --diff before.json after.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys

DESIGN_ROOT = r"C:\temp\dyautodm_design"
SUSPECT_KEYS = ("ai_agents", "ai_account_agent", "ai_reply_config",
                "ai_reply_knowledge_base", "ai_reply_blacklist")


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def snapshot(root: str) -> dict:
    if not os.path.isdir(root):
        return {"root": root, "exists": False, "files": {}, "kv": {}, "errors": []}
    files = {}
    errors = []
    for dirpath, dirnames, filenames in os.walk(root):
        for name in filenames:
            if not name.lower().endswith(".db"):
                continue
            p = os.path.join(dirpath, name)
            try:
                st = os.stat(p)
                files[os.path.relpath(p, root)] = {
                    "size": st.st_size,
                    "mtime_ns": st.st_mtime_ns,
                    "sha256": sha256_of(p),
                }
            except Exception as e:                      # 只读，出错只记录
                errors.append(f"{p}: {type(e).__name__}: {e}")
    return {"root": root, "exists": True, "files": files, "kv": dump_kv(root),
            "errors": errors}


def dump_kv(root: str) -> dict:
    """只读导出各 .db 里的可疑 kv_store 键值（用于比对是否冒出 ag* 测试数据）。"""
    out = {}
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if not name.lower().endswith(".db"):
                continue
            p = os.path.join(dirpath, name)
            try:
                # 只读模式打开，杜绝任何写副作用（含 -wal/-journal 落盘）
                uri = "file:{}?mode=ro".format(p.replace("\\", "/").replace("?", "%3f"))
                conn = sqlite3.connect(uri, uri=True, timeout=5)
                try:
                    rows = conn.execute(
                        "SELECT key, value FROM kv_store WHERE key IN ({})".format(
                            ",".join("?" * len(SUSPECT_KEYS))), SUSPECT_KEYS).fetchall()
                    if rows:
                        out[os.path.relpath(p, root)] = {k: v for k, v in rows}
                finally:
                    conn.close()
            except Exception:
                continue
    return out


def diff(a: dict, b: dict) -> dict:
    fa, fb = a.get("files", {}), b.get("files", {})
    added = sorted(set(fb) - set(fa))
    removed = sorted(set(fa) - set(fb))
    changed = []
    for k in sorted(set(fa) & set(fb)):
        x, y = fa[k], fb[k]
        if x["sha256"] != y["sha256"] or x["size"] != y["size"]:
            changed.append({"path": k, "size": [x["size"], y["size"]],
                            "mtime_ns": [x["mtime_ns"], y["mtime_ns"]],
                            "sha256_changed": x["sha256"] != y["sha256"]})
        elif x["mtime_ns"] != y["mtime_ns"]:
            changed.append({"path": k, "size": [x["size"], y["size"]],
                            "mtime_ns": [x["mtime_ns"], y["mtime_ns"]],
                            "sha256_changed": False, "mtime_only": True})
    ka, kb = a.get("kv", {}), b.get("kv", {})
    kv_changed = {}
    for dbpath in sorted(set(ka) | set(kb)):
        for key in sorted(set(ka.get(dbpath, {})) | set(kb.get(dbpath, {}))):
            va = ka.get(dbpath, {}).get(key)
            vb = kb.get(dbpath, {}).get(key)
            if va != vb:
                kv_changed.setdefault(dbpath, {})[key] = {
                    "before_head": (va or "")[:400],
                    "after_head": (vb or "")[:400],
                }
    return {"files_total_before": len(fa), "files_total_after": len(fb),
            "added": added, "removed": removed, "changed": changed,
            "kv_changed": kv_changed,
            "identical": not (added or removed or changed or kv_changed)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", help="写入快照 JSON")
    ap.add_argument("--diff", nargs=2, metavar=("BEFORE", "AFTER"), help="比对两份快照")
    ap.add_argument("--root", default=DESIGN_ROOT)
    args = ap.parse_args()

    if args.diff:
        with open(args.diff[0], encoding="utf-8") as f:
            before = json.load(f)
        with open(args.diff[1], encoding="utf-8") as f:
            after = json.load(f)
        d = diff(before, after)
        print(json.dumps(d, ensure_ascii=False, indent=2))
        print("\nIDENTICAL =", d["identical"])
        return 0

    snap = snapshot(args.root)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(snap, f, ensure_ascii=False, indent=1)
        print(f"[snapshot] root={snap['root']} exists={snap['exists']} "
              f"db_files={len(snap.get('files', {}))} kv_dbs={len(snap.get('kv', {}))} "
              f"-> {args.out}")
        if snap.get("errors"):
            print("[snapshot] errors:", snap["errors"][:5])
    else:
        print(json.dumps(snap, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
