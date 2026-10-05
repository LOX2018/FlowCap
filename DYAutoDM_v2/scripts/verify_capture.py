"""捕获完成后统计：会话数、消息数、图片形态、图床覆盖率。

清空后重新捕获，用来干净地验证图床链路。
"""
import sqlite3

DB = r"C:\temp\dyautodm_test\data\dyautodm.db"
con = sqlite3.connect(DB)

print("=== 总量 ===")
for t in ("dm_conversations", "dm_messages"):
    try:
        n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"  {t}: {n}")
    except Exception as e:
        print(f"  {t}: {e}")

print("\n=== 消息类型分布 ===")
for r in con.execute(
    "SELECT msg_type, COUNT(*) FROM dm_messages GROUP BY msg_type ORDER BY 2 DESC LIMIT 10"
):
    print(f"  {r[0]}: {r[1]}")

n_img = con.execute(
    "SELECT COUNT(*) FROM dm_messages WHERE text LIKE '[图片]%'").fetchone()[0]
print(f"\n=== 图片消息: {n_img} 条 ===")

if n_img:
    for label, pat in [("图床链 i.ibb.co", "%i.ibb.co%"),
                       ("内联 base64", "%data:image%"),
                       ("抖音远程链", "%douyinpic%")]:
        n = con.execute(
            "SELECT COUNT(*) FROM dm_messages WHERE text LIKE '[图片]%' "
            "AND text LIKE ?", (pat,)).fetchone()[0]
        pct = n * 100 // n_img if n_img else 0
        print(f"  {label:18s} {n:4d}  ({pct}%)")

    print("\n=== 前 3 条图片消息 ===")
    for r in con.execute(
        "SELECT id, substr(text,1,110) FROM dm_messages "
        "WHERE text LIKE '[图片]%' ORDER BY id LIMIT 3"
    ):
        print(f"  id={r[0]}")
        print(f"    {r[1]}")

# 图床在守护日志里的统计
print("\n=== 守护进程图床日志（最近 8 条）===")
import glob
import os
logdir = r"C:\temp\dyautodm_test\logs"
hits = []
for fp in glob.glob(os.path.join(logdir, "*.log")):
    try:
        with open(fp, encoding="utf-8", errors="replace") as f:
            for ln in f:
                if "[imgbb]" in ln:
                    hits.append(ln.strip())
    except Exception:
        continue
for h in hits[-8:]:
    print("  " + h[:120])
print(f"\n  imgbb 日志总条数: {len(hits)}")
