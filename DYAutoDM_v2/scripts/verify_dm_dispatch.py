"""真机验证：私信调度器（打包后跑）。

验证项（对应用户要求）：
  1. 调度器模块能被导入（PyInstaller 打包完整性）
  2. 测试白名单已生效（调试版专属）
  3. 白名单外的目标被拒绝（防误发真人）
  4. 会话整理池归一化正确
  5. 陌生人首发限流生效
"""
import sys

sys.path.insert(0, r"C:\Users\LOX\Desktop\DYchajian\DYAutoDM_v2\backend")

ok_count = 0
fail_count = 0


def check(name, cond, extra=""):
    global ok_count, fail_count
    if cond:
        ok_count += 1
        print(f"  [PASS] {name} {extra}")
    else:
        fail_count += 1
        print(f"  [FAIL] {name} {extra}")


print("=" * 60)
print("私信调度器 真机验证（打包后）")
print("=" * 60)

# 1. 导入
try:
    import services.dm_dispatch as dd
    check("模块导入", True)
except Exception as e:
    check("模块导入", False, str(e))
    sys.exit(1)

# 2. 白名单是否生效（调试版应为 True）
print("\n--- 测试白名单（调试版专属）---")
check("TEST_WHITELIST_ON", dd.TEST_WHITELIST_ON is True,
      f"= {dd.TEST_WHITELIST_ON}")
check("白名单内容", bool(dd._TEST_WHITELIST), f"{dd._TEST_WHITELIST}")

# 3. 入池校验：白名单内放行 / 白名单外拒绝
print("\n--- 入池校验 ---")
d = dd.DmDispatcher()
my = "100000000000001"   # 合成本账号 uid（通用产品，不硬编码真实账号）
d.pool._my_uid_of = lambda a: my

# A -> B（白名单内，应放行）
r1 = d.submit("测试账号A", f"0:1:{my}:200000000000002", "测试", "manual")
check("白名单内放行", r1.accepted, f"error={r1.error[:40]}")

# A -> 其他真人（应拒绝）
r2 = d.submit("测试账号A", f"0:1:{my}:8888888888", "测试", "manual")
check("白名单外拒绝", (not r2.accepted) and "白名单" in r2.error,
      f"error={r2.error[:40]}")

# 4. 会话整理
print("\n--- 会话整理池 ---")
p = dd.ConvPool()
check("me:对方 -> 对方", p._peer_from_conv(f"0:1:{my}:555", my) == "555")
check("对方:me -> 对方", p._peer_from_conv(f"0:1:777:{my}", my) == "777")
check("me:me -> None", p._peer_from_conv(f"0:1:{my}:{my}", my) is None)

# 5. 陌生人首发限流
print("\n--- 陌生人首发限流 ---")
q = dd.AccountQuota("限流测试")
c1 = q.can_stranger_first()[0]
q.note_stranger_sent()
c2 = q.can_stranger_first()[0]
q.note_stranger_sent()
c3 = q.can_stranger_first()[0]
check("第1次允许", c1)
check("第2次允许", c2)
check("第3次拒绝(达2/分钟)", not c3)

# 6. 频控降权 + 冷静
print("\n--- 频控降权 + 冷静 ---")
q2 = dd.AccountQuota("降权测试")
for _ in range(6):
    q2.on_result(True)
w0 = q2.weight()
q2.on_result(False, "发送过于频繁，被平台频控拦截")
w1 = q2.weight()
check("降权生效", w1 < w0, f"{w0:.2f} -> {w1:.2f}")
check("进入冷静期", q2.snapshot()["cooldown_left_sec"] > 0,
      f"{q2.snapshot()['cooldown_left_sec']}s")
check("冷静期暂停首发", not q2.can_stranger_first()[0])
check("权重不低于0.3", w1 >= 0.3, f"w={w1:.2f}")

print("\n" + "=" * 60)
print(f"结果: PASS={ok_count}  FAIL={fail_count}")
print("=" * 60)
