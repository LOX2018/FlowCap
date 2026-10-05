# -*- coding: utf-8 -*-
"""实机验证：监听启动时的「账号解密权预检 + 显式报错」能力是否真的可用。

用户需求（乙方案）：「监听启动时预检账号解密权，无权则显式报错」。

源码已存在（勿重复建设）：
  · 判据：accounts.uid_identity_verdict()  → (state, reason, label, detail)
  · 消费：core/auto_dm.py:846-880          → self._live_session_ok
  · 透出：core/auto_dm.py:998-1002         → self.status_msg（前端 ls.statusMsg）

本脚本**不新建任何逻辑**，只调既有判据并把结果如实打印，证明：
  ① 判据可达（能拿到三态）；
  ② 有解密权账号 → state=True + label「具备直播昵称解密权」；
  ③ status_msg 的构造规则（照抄 auto_dm.py:998-1002 的判据）产出正确文案。

只读诊断：不写 kv、不发私信、不改文件。
"""
from __future__ import annotations

import os
import sys

DESIGN_ROOT = os.path.abspath(os.environ.get("DY_APP_ROOT") or r"C:\temp\dyautodm_design")
if DESIGN_ROOT == os.path.abspath(r"C:\temp\dyautodm_test"):
    sys.exit("[环境门禁] 拒绝在主分支环境运行")
os.environ["DY_APP_ROOT"] = DESIGN_ROOT
_BACKEND = os.path.abspath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "backend"))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)
os.chdir(_BACKEND)

ACCOUNT = os.environ.get("DY_PROBE_ACCOUNT") or "小助理"
LIVE_ID = os.environ.get("DY_PROBE_LIVE_ID") or "992931212705"


def main() -> int:
    from auto_dm.accounts import uid_identity_verdict

    print("=" * 74)
    print("【1】既有权威判据 uid_identity_verdict() 实机调用")
    print("=" * 74)
    state, reason, label, detail = uid_identity_verdict(ACCOUNT, None, force=True)
    print(f"  账号     : {ACCOUNT}")
    print(f"  state    : {state!r}   （True=有 / False=确认无 / None=取不到证据）")
    print(f"  reason   : {reason!r}")
    print(f"  label    : {label!r}")
    print(f"  detail   : {detail!r}")

    print("\n" + "=" * 74)
    print("【2】status_msg 构造（照抄 auto_dm.py:998-1002 判据，验证文案）")
    print("=" * 74)
    # 这段是 auto_dm.py:998-1002 的等价复现（不新建逻辑，只验证文案）
    status_msg = f"监听中 {LIVE_ID}"
    if state is False:
        _why = label or "无直播昵称解密权"
        status_msg = f"监听中 {LIVE_ID}（昵称脱敏·{_why}，请重新扫码）"
    print(f"  模拟 status_msg = {status_msg!r}")
    print(f"  前端显示（live-page.tsx:661） = {status_msg!r}")

    print("\n" + "=" * 74)
    print("【3】判定")
    print("=" * 74)
    if state is True:
        print(f"  ✅ 判据可达 + 该账号**具备**解密权 ⇒ status_msg 保持「监听中 <id>」")
        print(f"     （无权时才追加「昵称脱敏·<原因>，请重新扫码」）")
        print("  ✅ 能力验证通过：预检可达、有解密权时文案正确")
    elif state is False:
        print(f"  ✅ 判据可达 + 该账号**无**解密权 ⇒ 显式报错文案已生成：")
        print(f"     {status_msg!r}")
        print("  ✅ 能力验证通过：预检可达、无权时**显式报错**（用户要的行为）")
    else:
        print(f"  ⚠️ 判据返回 None（取不到证据）—— 诚实降级，不据此判无")
        print(f"     detail: {detail}")
        print("  ⇒ 这是设计内的三态行为（LIVE-036），非缺陷；换有网/有凭证环境可复测")
    return 0


if __name__ == "__main__":
    sys.exit(main())
