# -*- coding: utf-8 -*-
"""数据库字段现状盘点（★ 2026-10-03，只读）。

用途：为「字段整理优化」提供**实测基线**，而不是凭源码定义推测。

## 为什么必须实测（项目铁律）

- 活跃库由**登录态**决定（`database._db_path()` 有会员态时走分库），
  凭路径猜会读错库；本脚本**两个库都读**并如实标注。
- `_init_tables` 的 `CREATE TABLE IF NOT EXISTS` 对旧库是 **no-op** ⇒
  **源码里定义的列 ≠ 实际库里的列**（新增列靠 `_migrate_schema` 的 ALTER）。
  ⇒ 真实列集合只能靠 `PRAGMA table_info` 读出来。

## 只读保证

只 `SELECT` / `PRAGMA`，不 CREATE/ALTER/DELETE/INSERT。
"""
import os
import sqlite3
import sys

ROOTS = [
    ("部署根(运行时)", r"C:\temp\dyautodm_design\data\dyautodm.db"),
    ("部署根·会员库", r"C:\temp\dyautodm_design\members"),
]

SKIP = ("sqlite_",)


def dump(db_path: str, label: str) -> None:
    if not os.path.exists(db_path):
        print(f"[{label}] 不存在: {db_path}")
        return
    print(f"\n{'=' * 74}\n[{label}] {db_path}")
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except Exception as e:
        print(f"  打开失败: {e}")
        return
    try:
        cur = conn.cursor()
        tabs = [r[0] for r in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        print(f"  表数: {len(tabs)}")
        for t in tabs:
            if any(t.startswith(s) for s in SKIP):
                continue
            cols = list(cur.execute(f'PRAGMA table_info("{t}")'))
            n = cur.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
            idx = [r[0] for r in cur.execute(
                f"SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=? "
                f"AND name NOT LIKE 'sqlite_%'", (t,))]
            print(f"\n  ── {t}  (行数={n})")
            print(f"     索引: {', '.join(idx) if idx else '(无)'}")
            width = max((len(c[1]) for c in cols), default=4)
            for c in cols:
                cid, name, ctype, notnull, dflt, pk = c[:6]
                flags = []
                if pk:
                    flags.append("PK")
                if notnull:
                    flags.append("NOT NULL")
                print(f"     {name:<{width}}  {ctype:<8} "
                      f"{(dflt if dflt is not None else ''):<8} {' '.join(flags)}")
            # 索引覆盖判据：只看单列/复合索引的**左前缀**列
            covered = set()
            for i in idx:
                names = [r[2] for r in cur.execute(f'PRAGMA index_info("{i}")')]
                if names:
                    covered.add(names[0])   # 左前缀才视为被覆盖
            naked = [c[1] for c in cols
                     if c[1] not in covered and not c[5]]   # 排除 PK
            if n > 0 and naked:
                print(f"     [无索引左前缀] {', '.join(naked)}")
    finally:
        conn.close()


def main() -> None:
    for label, p in ROOTS:
        if os.path.isdir(p):
            for mid in sorted(os.listdir(p)):
                db = os.path.join(p, mid, "data", "dyautodm.db")
                if os.path.exists(db):
                    dump(db, f"{label}/{mid}")
        else:
            dump(p, label)


if __name__ == "__main__":
    main()
