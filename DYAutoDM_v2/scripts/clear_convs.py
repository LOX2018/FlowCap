#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""清空会话与消息数据（干净环境重新捕获验证）。

保留：kv_store（账号索引/配置/昵称缓存开关）、tasks、crawl_history
清空：dm_conversations、dm_messages、ai_leads、dm_uid_sink（并重置其自增 id）

## 铁律（本文件 2026-09-15 重写时加固）

1. **分支环境隔离固化为代码，不靠记忆**：
   本分支（design/better-douyin）环境 = `C:\\temp\\dyautodm_design`；
   主分支环境 `C:\\temp\\dyautodm_test` **绝不写入**（历史事故：曾误用主分支环境致污染）。
   目标指向被禁环境 → **直接拒绝**，除非显式 `--allow-foreign-root`。
2. **DB 路径不写死**：复用官方解析器 `services.member_ctx.db_path()` /
   `database.get_db()`，避免「路径写错却以为清空了」。
3. **清空前自动备份**（`*.bak.<时间戳>`），且**默认是预演**，必须显式 `--yes` 才执行。
4. **进程占用时拒绝执行**（BCC/recv_daemon 持有 DB 会致写入交错）。

用法：
    # 预演（默认，只打印不删）
    python scripts/clear_convs.py
    # 真正清空（自动备份）
    python scripts/clear_convs.py --yes
    # 显式指定环境（否则用 DY_APP_ROOT 或本分支默认）
    python scripts/clear_convs.py --app-root C:\\temp\\dyautodm_design --yes
"""
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]      # DYAutoDM_v2/
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

# ── 分支环境隔离（与 scripts/deploy.py 同源规则）─────────────────────────────
DEFAULT_APP_ROOT = r"C:\temp\dyautodm_design"
FORBIDDEN_ROOTS = (r"C:\temp\dyautodm_test",)

CLEAR_TABLES = ["dm_messages", "dm_conversations", "ai_leads", "dm_uid_sink"]
KEEP_TABLES = ["kv_store", "tasks", "crawl_history"]


def _norm(p) -> str:
    return str(p).replace("/", "\\").rstrip("\\").lower()


def branch_guard(app_root: str, allow_foreign: bool = False) -> bool:
    """拒绝把本分支操作打到被禁（主分支）环境。"""
    for bad in FORBIDDEN_ROOTS:
        if _norm(app_root) == _norm(bad):
            if allow_foreign:
                print(f"  ⚠️ --allow-foreign-root 已显式放行被禁环境：{app_root}")
                return True
            print(f"\n[隔离门禁] ❌ 拒绝执行：目标指向主分支环境 {app_root}")
            print(f"  本分支（design/better-douyin）环境 = {DEFAULT_APP_ROOT}")
            print("  如确要操作该目录，请显式加 --allow-foreign-root。")
            return False
    return True


def resolve_db_path(app_root: str) -> Path:
    """复用官方解析器拿 DB 路径（不写死）。

    2026-09-15 实测坑：`member_ctx.db_path()` 依赖 `DY_MEMBER`（member_id）——
    脚本里没设时返回 None，回退到**非会员路径** `<app_root>/data/dyautodm.db`
    （该文件不存在/为空），于是脚本「以为清空了实际没清」。
    真实库在 `<app_root>/members/<member_id>/data/dyautodm.db`。
    故这里先按 daemon 的启动方式补 DY_MEMBER（读 `<app_root>/members/.session.json`）。
    """
    os.environ["DY_APP_ROOT"] = app_root
    # 补 member_id（与 daemon 启动一致；不写死具体 id）
    sess = Path(app_root) / "members" / ".session.json"
    if sess.is_file() and not os.environ.get("DY_MEMBER"):
        try:
            import json as _json
            data = _json.loads(sess.read_text(encoding="utf-8"))
            mid = data.get("member_id") or data.get("id") or ""
            if mid:
                os.environ["DY_MEMBER"] = str(mid)
            mk = data.get("master_key") or data.get("key") or ""
            if mk and not os.environ.get("DY_MEMBER_KEY"):
                os.environ["DY_MEMBER_KEY"] = str(mk)
        except Exception as e:
            print(f"  [warn] 读取 members/.session.json 失败: {e}")

    try:
        from services import member_ctx          # type: ignore
        p = member_ctx.db_path()
        if p:
            p = Path(p)
            return p if p.is_absolute() else Path(app_root) / p
        print("  [warn] member_ctx.db_path() 返回 None（缺 member_id）")
    except Exception as e:
        print(f"  [warn] member_ctx.db_path() 不可用: {e}")

    # 直接扫 members/*/data/dyautodm.db（最可靠，不依赖 member_ctx）
    cands = sorted(Path(app_root).glob("members/*/data/dyautodm.db"))
    if len(cands) == 1:
        return cands[0]
    if len(cands) > 1:
        raise SystemExit(f"[中止] 发现多个会员库，请显式指定/清理：{cands}")
    # 最后兜底
    return Path(app_root) / "data" / "dyautodm.db"


def _counts(conn, tables):
    out = {}
    for t in tables:
        try:
            out[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        except Exception as e:
            out[t] = f"err:{e}"
    return out


def daemons_running() -> list:
    """检测本分支 daemon 进程（持有 DB 时不许清）。失败则不阻塞（仅告警）。"""
    try:
        import subprocess
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq dyautodm-browser-daemon-x86_64-pc-windows-msvc.exe"],
            capture_output=True, text=True, timeout=10).stdout
        hits = [l for l in out.splitlines()
                if "browser-daemon" in l or "recv-daemon" in l or "dyautodm-" in l.lower()]
        return hits
    except Exception:
        return []


def main() -> int:
    ap = argparse.ArgumentParser(description="清空会话/消息数据（默认预演）")
    ap.add_argument("--app-root", default=os.environ.get("DY_APP_ROOT") or DEFAULT_APP_ROOT)
    ap.add_argument("--yes", action="store_true", help="确认执行（默认只预演）")
    ap.add_argument("--allow-foreign-root", action="store_true",
                    help="显式放行被禁环境（一般不要用）")
    ap.add_argument("--no-backup", action="store_true", help="不备份（不推荐）")
    args = ap.parse_args()

    print("=" * 70)
    print(f"[清库] 应用根目录 = {args.app_root}")
    print("=" * 70)
    if not branch_guard(args.app_root, args.allow_foreign_root):
        return 9

    occupied = daemons_running()
    if occupied:
        print("\n[警告] 检测到 dyautodm daemon 进程，可能持有 DB：")
        for l in occupied[:5]:
            print("   ", l.strip()[:110])
        print("   请先 POST /quit 优雅停止，否则清空可能交错或读不到最新数据。")

    db = resolve_db_path(args.app_root)
    print(f"\n[清库] DB 路径 = {db}")
    if not db.exists():
        print("  ❌ DB 不存在，中止")
        return 6

    conn = sqlite3.connect(str(db))
    before = _counts(conn, CLEAR_TABLES)
    kept = _counts(conn, KEEP_TABLES)
    print("\n=== 将清空 ===")
    for t, n in before.items():
        print(f"  {t:22s} {n}")
    print("=== 保留（不动） ===")
    for t, n in kept.items():
        print(f"  {t:22s} {n}")

    if not args.yes:
        conn.close()
        print("\n(预演结束；确认无误后加 --yes 执行)")
        return 0

    # 备份
    if not args.no_backup:
        ts = time.strftime("%Y%m%d_%H%M%S")
        for suffix in ("", "-wal", "-shm"):
            src = Path(str(db) + suffix)
            if src.exists():
                dst = Path(f"{src}.bak.{ts}")
                try:
                    shutil.copy2(src, dst)
                    print(f"  已备份 {dst.name} ({dst.stat().st_size} B)")
                except Exception as e:
                    print(f"  ⚠️ 备份 {src.name} 失败: {e}")

    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception:
        pass
    for t in CLEAR_TABLES:
        try:
            conn.execute(f"DELETE FROM {t}")
        except Exception as e:
            print(f"  ⚠️ 清空 {t} 失败: {e}")
    try:
        conn.execute("DELETE FROM sqlite_sequence")
    except Exception:
        pass
    conn.commit()
    after = _counts(conn, CLEAR_TABLES)
    conn.close()

    # 重开复核（确认落盘）
    conn2 = sqlite3.connect(str(db))
    verify = _counts(conn2, CLEAR_TABLES)
    conn2.close()
    print("\n=== 清空后（复核）===")
    for t in CLEAR_TABLES:
        print(f"  {t:22s} {verify[t]}")
    ok = all(v == 0 for v in verify.values() if isinstance(v, int))
    print(f"\n{'[OK] 已清空（保留配置与采集历史）' if ok else '[警告] 部分表未清干净，请检查'}")
    return 0 if ok else 7


if __name__ == "__main__":
    sys.exit(main())
