# -*- coding: utf-8 -*-
"""发送闸门统一 + 缓存统一 验证（v0.43.40）。

跑法：
    set FLOWCAP_APP_ROOT=<部署目录>
    set DY_MEMBER=<会员 id>          # 实机段需要
    set DY_TEST_ACCOUNT=<账号名>     # 实机段需要；缺省跳过
    python <此脚本>

覆盖：
  1. TTLCache 原语（set/get/过期/负缓存/prefix失效/prune/snapshot）
  2. 缓存统一：uid_probe.session_uid 委托 conv_identity（TTL 一处）
  3. 调度器仲裁：AccountQuota.can_send 的最小间隔裁决 + note_sent
  4. 兜底闸门：recv_daemon._cfg_min_interval 显著宽于配置值
"""
import os
import sys
import time

sys.path.insert(0, r"C:\Users\LOX\Desktop\DYchajian\backend")

FAIL = []


def check(name, cond, extra=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name} {extra}")
    if not cond:
        FAIL.append(name)


print("=== [1] TTLCache 原语 ===")
from services.ttl_cache import TTLCache

c = TTLCache(default_ttl=1.0)
c.set("a", 1)
check("set/get 命中", c.get("a") == 1)
check("未命中返回 default", c.get("nope", "d") == "d")
c.set("s", "v", ttl=0.3)
time.sleep(0.4)
check("过期后失效", c.get("s") is None)
# 负缓存：值可为 None，peek 能区分「未缓存」与「缓存 None」
c.set("neg", None, ttl=10)
f1, v1 = c.peek("neg")
f2, v2 = c.peek("never")
check("负缓存：缓存 None 命中", (f1 is True, v1) == (True, None))
check("负缓存：未缓存不命中", f2 is False)
# get_or_set
calls = {"n": 0}
def _f():
    calls["n"] += 1
    return "X"
check("get_or_set 首次计算", c.get_or_set("gos", _f) == "X" and calls["n"] == 1)
check("get_or_set 二次命中不算", c.get_or_set("gos", _f) == "X" and calls["n"] == 1)
# prefix 失效 & prune
c.set("p:1", 1, ttl=100); c.set("p:2", 2, ttl=100); c.set("q:1", 3, ttl=100)
check("invalidate_prefix", c.invalidate_prefix("p:") == 2 and c.get("q:1") == 3)
c.set("tmp", 1, ttl=0.2)
time.sleep(0.3)
check("prune 清过期", c.prune() >= 1)
snap = c.snapshot()
check("snapshot 结构", "total" in snap and "live" in snap, str(snap))

print("\n=== [2] 缓存统一：session_uid 委托 conv_identity ===")
from services import uid_probe as UP
from services import conv_identity as CI
# uid_probe 不应再持有 _valid_session
check("uid_probe 已无 _valid_session", not hasattr(UP, "_valid_session"))
# session_uid 应等于 conv_identity.my_uid（同一来源）
acct = os.environ.get("DY_TEST_ACCOUNT", "").strip()
if acct:
    a = UP.session_uid(acct)
    b = CI.my_uid(acct)
    check("session_uid == conv_identity.my_uid", a == b, f"{a!r}=={b!r}")
    # invalidate 应联动清理 conv_identity 缓存
    UP.invalidate(acct)
    check("invalidate 联动已清 conv_identity 缓存",
          CI._my_uid_cache.peek(acct)[0] is False)
else:
    print("  [SKIP] 未设 DY_TEST_ACCOUNT，跳过实机段")

print("\n=== [3] 调度器仲裁：AccountQuota.can_send 最小间隔 ===")
from services.dm_dispatch import AccountQuota

q = AccountQuota("test-acct")
ok, why, wait = q.can_send(0.0)
check("首次 can_send 放行", ok is True, f"why={why!r}")
q.note_sent()
ok2, why2, wait2 = q.can_send(5.0)
check("note_sent 后立即再取被拦", ok2 is False, f"why={why2!r} wait={wait2:.1f}")
check("返回剩余等待 >0", wait2 > 0)
ok3, _, _ = q.can_send(0.0)
check("min_interval=0 时不拦", ok3 is True)

print("\n=== [4] 兜底闸门宽于调度器配置值 ===")
# 直接读 recv_daemon 的兜底函数（不启动服务）
os.environ.setdefault("DY_SEND_MIN_INTERVAL", "8")
import importlib
RD = None
try:
    sys.path.insert(0, r"C:\Users\LOX\Desktop\DYchajian\backend\daemon")
    RD = importlib.import_module("daemon.recv_daemon")
except Exception as e:
    print(f"  [SKIP] 无法导入 recv_daemon（{type(e).__name__}: {e}）")
if RD is not None:
    _mi = RD._cfg_min_interval()
    check("兜底闸门 >=2s 且 <=配置值（宽于调度器）", 2.0 <= _mi <= 8.0,
          f"_cfg_min_interval()={_mi}")

print("\n" + "=" * 50)
if FAIL:
    print(f"FAILED: {len(FAIL)} 项 -> {FAIL}")
    sys.exit(1)
print("ALL PASS")
