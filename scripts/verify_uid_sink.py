"""验证 UID 沉淀池：同 UID 多次弹幕只保留一次（跨重启持久化）。"""
import os
import sys

# 2026-09-17 修补（OCR 审查 HIGH —— 硬编码机器绝对路径）：
# 原为 `sys.path.insert(0, r"C:\Users\LOX\Desktop\DYchajian\backend")`
# —— 绑死某台机器与用户名，换机/CI 上必然 ImportError。
# 改为按**本脚本位置**推导仓库根（脚本在 <repo>/scripts/ 下）。
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, "backend"))
import services.dm_dispatch as dd

ok_n = fail_n = 0


def check(name, cond, extra=""):
    global ok_n, fail_n
    if cond:
        ok_n += 1
        print(f"  [PASS] {name} {extra}")
    else:
        fail_n += 1
        print(f"  [FAIL] {name} {extra}")


print("=" * 60)
print("UID 沉淀池验证（同 UID 去重 + 持久化）")
print("=" * 60)

sink = dd.UidSink()
ACC = "沉淀测试账号"

# 1. 首次应放行
ok, reason = sink.should_send(ACC, "111111")
check("首次 UID 放行", ok, f"reason={reason}")

# 2. 标记已发送
sink.mark_sent(ACC, "111111", nickname="张三", source="live")
check("mark_sent 无异常", True)

# 3. 同 UID 再次 → 应拦截（模拟同一个人刷多条弹幕）
ok2, reason2 = sink.should_send(ACC, "111111")
check("同 UID 二次拦截", not ok2, f"reason={reason2[:40]}")

# 4. 不同 UID 不受影响
ok3, _ = sink.should_send(ACC, "222222")
check("不同 UID 放行", ok3)

# 5. 不同账号的同 UID 互不干扰（key 隔离）
ok4, _ = sink.should_send("另一账号", "111111")
check("跨账号隔离", ok4, "(A发过不影响B)")

# 6. 持久化：新建实例（模拟进程重启）仍记得
sink2 = dd.UidSink()
ok5, reason5 = sink2.should_send(ACC, "111111")
check("重启后仍拦截(持久化)", not ok5, f"reason={reason5[:36]}")

# 7. 统计
st = sink2.stats(ACC)
check("统计可读", st.get("total", 0) >= 1, f"{st}")

print("\n" + "=" * 60)
print(f"结果: PASS={ok_n}  FAIL={fail_n}")
print("=" * 60)
