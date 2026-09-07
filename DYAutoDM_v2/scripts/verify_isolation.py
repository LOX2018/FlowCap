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

# 模拟调试版注入
dd.TEST_WHITELIST_ON = True
dd._TEST_WHITELIST = {
    "尚进工伤小助理": {"4175297014664416"},
    "四川工伤张老师": {"316276709526638"},
}

d = dd.DmDispatcher()
d.pool._my_uid_of = lambda a: "316276709526638" if a == "尚进工伤小助理" else "4175297014664416"

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
# 尚进 -> 四川张老师（白名单内）
r = d.submit_by_uid("尚进工伤小助理", "4175297014664416", "采集首发1", "dispatch")
check("第1次 放行", r.accepted, f"task={r.task_id}")
r = d.submit_by_uid("尚进工伤小助理", "4175297014664416", "采集首发2", "dispatch")
check("第2次 放行", r.accepted, f"task={r.task_id}")
r = d.submit_by_uid("尚进工伤小助理", "4175297014664416", "采集首发3", "dispatch")
check("第3次 被限流(2/分钟)", (not r.accepted) and "首发达限" in r.error,
      f"error={r.error[:44]}")
print("  → 关键：首发**能走到限流环节**，降权/冷静链路可测")

# ---- 2. 发给非测试账号（应拒绝）----
print("\n--- 2. 发给非测试账号（必须拒绝）---")
r = d.submit_by_uid("尚进工伤小助理", "8888888888", "误发测试", "dispatch")
check("首发->真人 拒绝", (not r.accepted) and "白名单" in r.error,
      f"error={r.error[:40]}")
r2 = d.submit("尚进工伤小助理", "0:1:316276709526638:8888888888", "误发", "manual")
check("熟客->真人 拒绝", (not r2.accepted) and "白名单" in r2.error,
      f"error={r2.error[:40]}")

# ---- 3. 私信中心/AI = 熟客（conv_id 有历史）----
print("\n--- 3. 私信中心/AI（submit，熟客会话）---")
q_before = d.quota_of("尚进工伤小助理").snapshot()["stranger_today"]
r = d.submit("尚进工伤小助理", "0:1:316276709526638:4175297014664416",
             "熟客回复", "manual")
check("熟客会话 放行", r.accepted, f"task={r.task_id}")
q_after = d.quota_of("尚进工伤小助理").snapshot()["stranger_today"]
check("熟客不占首发额度", q_after == q_before, f"{q_before} -> {q_after}")

# ---- 4. 反向（四川张老师 -> 尚进）----
print("\n--- 4. 反向互发 ---")
r = d.submit_by_uid("四川工伤张老师", "316276709526638", "反向首发", "dispatch")
check("张->尚进 放行", r.accepted, f"task={r.task_id}")

# ---- 5. 发给自己（任何来源都拒绝）----
print("\n--- 5. 发给自己（必须拒绝）---")
r = d.submit_by_uid("尚进工伤小助理", "316276709526638", "发给自己", "dispatch")
check("发给自己 拒绝", (not r.accepted) and "本账号" in r.error,
      f"error={r.error[:40]}")

print("\n" + "=" * 62)
print(f"结果: PASS={ok_n}  FAIL={fail_n}")
print("=" * 62)
