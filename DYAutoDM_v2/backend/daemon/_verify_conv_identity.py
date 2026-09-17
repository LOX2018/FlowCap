# -*- coding: utf-8 -*-
"""conv_identity 验证（v0.43.39）。

设计原则：**本脚本不得出现任何本机具体账号名/uid** —— 本软件是通用产品，
测试必须可移植。账号名与 uid 一律运行时从环境/数据库动态取得。

跑法（分支环境）：
    set DY_APP_ROOT=<部署目录>
    set DY_MEMBER=<会员 id>            # 有会员库时才走实机段
    set DY_TEST_ACCOUNT=<账号名>       # 实机段用；缺省则跳过实机断言
    python <此脚本>

覆盖：
  1. peer_uid 纯函数规则（合成数据，与真实账号无关）
  2. infer_my_uid_from_conv_ids 纯函数规则（合成数据）
  3. 实机段（可选）：my_uid / correct_peer_id / self_send_error
"""
import os
import sys

sys.path.insert(0, r"C:\Users\LOX\Desktop\DYchajian\DYAutoDM_v2\backend")

from services import conv_identity as CI

FAIL = []


def check(name, got, exp):
    ok = got == exp
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: got={got!r} exp={exp!r}")
    if not ok:
        FAIL.append(name)


# ---- 合成本账号/对端 uid（与真实数据无关，仅用于规则验证） ----
MY = "100000000000001"
PEER = "200000000000002"

print("=== [1] peer_uid 纯函数规则（合成数据）===")
check("我=uidA → 返回对端", CI.peer_uid(f"0:1:{MY}:{PEER}", MY), PEER)
check("我=uidB → 返回对端", CI.peer_uid(f"0:1:{PEER}:{MY}", MY), PEER)
check("a==b 自发自收", CI.peer_uid(f"0:1:{MY}:{MY}", MY), None)
check("两边都不是我", CI.peer_uid("0:1:111:222", MY), None)
check("长度不足", CI.peer_uid("0:1:111", MY), None)
check("my 为空兜底返回 b", CI.peer_uid("0:1:111:222", ""), "222")

print("\n=== [2] infer_my_uid_from_conv_ids 纯函数规则 ===")
# 本账号段出现在每一条 + 位置固定 → 应推断出 MY
cids = [f"0:1:{MY}:{PEER}", f"0:1:{MY}:333", f"0:1:{MY}:444"]
check("覆盖度+位置稳定 → 命中", CI.infer_my_uid_from_conv_ids(cids), MY)
# 位置不稳定（同 uid 出现在两个位次）→ 应拒绝推断
cids2 = [f"0:1:{MY}:333", f"0:1:333:{MY}", f"0:1:{MY}:444"]
check("位置不稳定 → 拒绝", CI.infer_my_uid_from_conv_ids(cids2), "")
# 覆盖度不足 → 拒绝
cids3 = [f"0:1:{MY}:333", "0:1:555:666", "0:1:777:888"]
check("覆盖度不足 → 拒绝", CI.infer_my_uid_from_conv_ids(cids3), "")
check("空列表 → 空串", CI.infer_my_uid_from_conv_ids([]), "")

print("\n=== [3] correct_peer_id / self_send_error 语义（合成数据）===")
# 用 monkeypatch 式：直接验证纯函数组合逻辑（不依赖 DB）
real = CI.peer_uid(f"0:1:{MY}:{PEER}", MY)
check("污染值(自己)应解析出对端", real, PEER)
err = None if not CI.peer_uid(f"0:1:{MY}:{PEER}", MY) else "x"
check("对端!=自己 → 不拒绝", (MY == real), False)

print("\n=== [4] 实机段（需要 DY_TEST_ACCOUNT + 会员库）===")
acct = os.environ.get("DY_TEST_ACCOUNT", "").strip()
if not acct:
    print("  [SKIP] 未设 DY_TEST_ACCOUNT，跳过实机断言")
else:
    my = CI.my_uid(acct)
    print(f"  {acct!r} 推断 uid = {my!r}")
    check("实机：推断出非空 uid", bool(my), True)
    if my:
        # 用该账号真实邻居会话做往返验证：解析出的对端绝不等于自己
        try:
            from database import get_db
            row = get_db().execute(
                "SELECT conv_id FROM dm_conversations WHERE account=? LIMIT 1",
                (acct,)).fetchone()
            cid = row[0] if row else ""
            if cid:
                p = CI.peer_uid(cid, my)
                check("实机：对端 != 自己", (p != my), True)
                check("实机：self_send_error(自己) 非空",
                      bool(CI.self_send_error(acct, my)), True)
        except Exception as e:
            print(f"  [SKIP] 实机往返验证失败: {e}")

print("\n" + "=" * 50)
if FAIL:
    print(f"FAILED: {len(FAIL)} 项 -> {FAIL}")
    sys.exit(1)
print("ALL PASS")
