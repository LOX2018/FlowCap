# -*- coding: utf-8 -*-
"""订正存量：把库里已落库的「内部 biz 串」占位文本改为中文说明。
   —— 只针对 text 里含已知内部 biz 串的行，精确匹配，绝不模糊删。

默认预演；--yes 才执行；执行前自动备份 DB。
"""
import os, sys, sqlite3, shutil, time, re

D = r"C:\temp\flowcap_design"
os.environ.setdefault("FLOWCAP_APP_ROOT", D)
sys.path.insert(0, r"C:\Users\LOX\Desktop\DYchajian\backend")
os.chdir(D)
DB = os.path.join(D, "members", "m17db0f8209156f26", "data", "flowcap.db")
APPLY = "--yes" in sys.argv

# biz 串 → 中文说明（与 conversation_capture._system_notice_text 保持一致）
MAP = {
    "aweme_im_api_gateway_confirm_stranger_message":
        "[系统提示] 陌生人消息确认（该会话由抖音网关自动确认）",
    "aweme_im_consecutive_chat_notice":
        "[系统提示] 对方已久未回复，此为连续聊天提醒",
    "aweme_im_user_follow_action_hello_msg":
        "[系统提示] 互相关注后可开始聊天",
}

c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
print(f"{'[APPLY]' if APPLY else '[DRY-RUN]'} DB={DB}\n")

total = 0
plan = []
for biz, label in MAP.items():
    rows = list(c.execute(
        "SELECT id, text FROM dm_messages WHERE text LIKE ?", (f"%{biz}%",)))
    for r in rows:
        # 保留原尾号（#123456）
        m = re.search(r'\(#(\d+)\)|（#(\d+)）', r["text"])
        tail = (m.group(1) or m.group(2)) if m else None
        new = f"{label}（#{tail}）" if tail else label
        plan.append((r["id"], r["text"], new))
        total += 1

print(f"=== 待订正 {total} 行 ===")
for i, old, new in plan:
    print(f"  id={i}\n    旧: {old}\n    新: {new}")

if not APPLY:
    print("\n(预演结束；加 --yes 执行)")
    sys.exit(0)

if not plan:
    print("无需订正")
    sys.exit(0)

ts = time.strftime("%Y%m%d_%H%M%S")
for sfx in ("", "-wal", "-shm"):
    src = DB + sfx
    if os.path.exists(src):
        shutil.copy2(src, f"{src}.bak.{ts}")
        print(f"  已备份 {os.path.basename(src)}.bak.{ts}")

for i, old, new in plan:
    c.execute("UPDATE dm_messages SET text=? WHERE id=? AND text=?", (new, i, old))
c.commit()
print("\n=== 复核 ===")
for biz in MAP:
    n = c.execute("SELECT COUNT(*) FROM dm_messages WHERE text LIKE ?",
                  (f"%{biz}%",)).fetchone()[0]
    print(f"  仍含 {biz}: {n}")
for biz, label in MAP.items():
    n = c.execute("SELECT COUNT(*) FROM dm_messages WHERE text LIKE ?",
                  (f"%{label[:12]}%",)).fetchone()[0]
    print(f"  已改为 {label[:14]}…: {n}")
c.close()
print("\n★ 订正完成（存量 0 内部串）")
