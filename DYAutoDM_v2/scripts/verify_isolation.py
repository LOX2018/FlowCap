"""验证隔离机制：首发 vs 熟客 都能测到（白名单不过度拦截）。

核心断言：
  1. 视频采集/直播监听（submit_by_uid，陌生人首发）：
     白名单**放行** → 能走到限流 → 第3次被 2/分钟 拦住（可测频控/降权）
  2. 私信中心/AI（submit，熟客）：白名单放行，不计首发额度
  3. 任何来源发给「非测试账号」：一律拒绝
"""
import sys

sys.path.insert(0, r"C:\Users\LOX\Desktop\DYchajian\DYAutoDM_v2\backend")
import services.dm_dispatch as dd

# 模拟调试版注入（合成账号/uid，与任何真实账号无关 —— 本软件为通用产品）
ACC_A = "测试账号A"
ACC_B = "测试账号B"
UID_A = "100000000000001"      # A 的本账号 uid
UID_B = "200000000000002"      # B 的本账号 uid（= A 的对端）
dd.TEST_WHITELIST_ON = True
dd._TEST_WHITELIST = {
    ACC_A: {UID_B},            # A 允许发给 B
    ACC_B: {UID_A},            # B 允许发给 A
}

d = dd.DmDispatcher()
d.pool._my_uid_of = lambda a: UID_A if a == ACC_A else UID_B

ok_n = fail_n = 0


def check(name, cond, extra=""):
    global ok_n, fail_n
    if cond:
        ok_n += 1
        print(f"  [PASS] {name} {extra}")
    else:
        fail_n += 1
        print(f"  [FAIL] {name} {extra}")


print("=" * 62)
print("隔离机制验证：首发 vs 熟客（调试白名单）")
print("=" * 62)

# ---- 1. 视频采集 / 直播监听 = 陌生人首发（uid 直发）----
print("\n--- 1. 视频采集/直播监听（submit_by_uid，陌生人首发）---")
# A -> B（白名单内）
r = d.submit_by_uid(ACC_A, UID_B, "采集首发1", "dispatch")
check("第1次 放行", r.accepted, f"task={r.task_id}")
r = d.submit_by_uid(ACC_A, UID_B, "采集首发2", "dispatch")
check("第2次 放行", r.accepted, f"task={r.task_id}")
r = d.submit_by_uid(ACC_A, UID_B, "采集首发3", "dispatch")
check("第3次 被限流(2/分钟)", (not r.accepted) and "首发达限" in r.error,
      f"error={r.error[:44]}")
print("  → 关键：首发**能走到限流环节**，降权/冷静链路可测")

# ---- 2. 发给非测试账号（应拒绝）----
print("\n--- 2. 发给非测试账号（必须拒绝）---")
r = d.submit_by_uid(ACC_A, "8888888888", "误发测试", "dispatch")
check("首发->真人 拒绝", (not r.accepted) and "白名单" in r.error,
      f"error={r.error[:40]}")
r2 = d.submit(ACC_A, f"0:1:{UID_A}:8888888888", "误发", "manual")
check("熟客->真人 拒绝", (not r2.accepted) and "白名单" in r2.error,
      f"error={r2.error[:40]}")

# ---- 3. 私信中心/AI = 熟客（conv_id 有历史）----
# 注意：熟客/陌生人由【本地 dm_messages 是否有历史消息】判定，
# 不是由来源决定。测试 DB 里该 conv_id 无历史 → 会被判为陌生人首发，
# 从而**正常受限流约束**（这是正确行为）。
# 因此"熟客不占额度"这一项，必须先造一条历史消息再测，否则测的是首发。
print("\n--- 3. 私信中心/AI（submit）---")
CID = f"0:1:{UID_A}:{UID_B}"
ACC = ACC_A

# 3a) 无历史 -> 判为陌生人首发（受限流约束，属正确行为）
is_stranger = dd.DmDispatcher._is_stranger_first(ACC, CID)
print(f"  当前 conv_id 判定: {'陌生人首发' if is_stranger else '熟客会话'}"
      f"（由本地历史消息决定，非来源决定）")

# 3b) 造一条历史消息 -> 变成熟客，再验证不占首发额度
made_history = False
try:
    import sqlite3
    from database import get_db
    conn = get_db()
    conn.execute(
        "INSERT OR IGNORE INTO dm_messages("
        "account,conv_id,role,text,msg_type,ts,msg_id) "
        "VALUES(?,?,'them','历史消息','text',?,?)",
        (ACC, CID, __import__("time").time(), "test_history_1"))
    conn.commit()
    made_history = True
except Exception as e:
    print(f"  [warn] 造历史消息失败: {e}")

if made_history:
    now_stranger = dd.DmDispatcher._is_stranger_first(ACC, CID)
    check("有历史后判定为熟客", now_stranger is False,
          f"is_stranger={now_stranger}")
    q_before = d.quota_of(ACC).snapshot()["stranger_today"]
    r = d.submit(ACC, CID, "熟客回复", "manual")
    check("熟客会话 放行", r.accepted, f"error={r.error[:36]}")
    q_after = d.quota_of(ACC).snapshot()["stranger_today"]
    check("熟客不占首发额度", q_after == q_before, f"{q_before} -> {q_after}")
    # 清理测试历史
    try:
        conn.execute("DELETE FROM dm_messages WHERE msg_id='test_history_1'")
        conn.commit()
    except Exception:
        pass

# ---- 4. 反向（B -> A）----
print("\n--- 4. 反向互发 ---")
r = d.submit_by_uid(ACC_B, UID_A, "反向首发", "dispatch")
check("B->A 放行", r.accepted, f"task={r.task_id}")

# ---- 5. 发给自己（任何来源都拒绝）----
print("\n--- 5. 发给自己（必须拒绝）---")
r = d.submit_by_uid(ACC_A, UID_A, "发给自己", "dispatch")
check("发给自己 拒绝", (not r.accepted) and "本账号" in r.error,
      f"error={r.error[:40]}")

print("\n" + "=" * 62)
print(f"结果: PASS={ok_n}  FAIL={fail_n}")
print("=" * 62)
