"""清空会话与消息表，用于干净环境重新捕获验证。

保留：kv_store（账号索引、配置）、tasks
清空：dm_conversations、dm_messages
"""
import sqlite3
import sys

DB = r"C:\temp\dyautodm_test\data\dyautodm.db"

confirm = "--yes" in sys.argv
if not confirm:
    print("!! 需要 --yes 才会执行清空")
    sys.exit(1)

con = sqlite3.connect(DB)

print("=== 清空前 ===")
for t in ("dm_conversations", "dm_messages", "kv_store", "tasks"):
    try:
        n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"  {t}: {n}")
    except Exception as e:
        print(f"  {t}: 读取失败 {e}")

n_img = con.execute(
    "SELECT COUNT(*) FROM dm_messages WHERE text LIKE '[图片]%'").fetchone()[0]
n_hosted = con.execute(
    "SELECT COUNT(*) FROM dm_messages WHERE text LIKE '%i.ibb.co%'").fetchone()[0]
print(f"  其中图片消息: {n_img}，图床链接: {n_hosted}")
print()

con.execute("DELETE FROM dm_conversations")
con.execute("DELETE FROM dm_messages")
# 重置自增，让新数据 id 从 1 开始，便于区分新旧
try:
    con.execute("DELETE FROM sqlite_sequence WHERE name IN "
                "('dm_conversations','dm_messages')")
except Exception:
    pass
con.commit()

print("=== 清空后 ===")
for t in ("dm_conversations", "dm_messages", "kv_store", "tasks"):
    try:
        n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"  {t}: {n}")
    except Exception as e:
        print(f"  {t}: 读取失败 {e}")

con.close()
print("\n[OK] 会话与消息已清空，账号索引与配置保留")
