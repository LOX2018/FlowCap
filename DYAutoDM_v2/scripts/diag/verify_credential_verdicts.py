# -*- coding: utf-8 -*-
"""引擎校验判据 实机验证（v0.44.17）。

## 要证明什么
修复前：张老师（609 被服务端拒、发送全废）在「引擎校验」里显示
        `dm: ok / 私信守护正常` —— 谎报。
修复后：同样账号必须显示 `dm: fail / 私信凭证不可写`，且 detail 点明
        「只读态、需重新扫码」；而尚进（可写）必须显示 ok。

这是「用真实账号的真实服务端响应」验证判据，不是静态检查。

## 用法
    python scripts/diag/verify_credential_verdicts.py
"""
from __future__ import annotations

import json
import os
import sys

DESIGN_ROOT = os.path.abspath(os.environ.get("DY_APP_ROOT") or r"C:\temp\dyautodm_design")
_FORBIDDEN = {os.path.abspath(r"C:\temp\dyautodm_test")}
BACKEND = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "backend"))


def main() -> int:
    if DESIGN_ROOT in _FORBIDDEN:
        sys.exit("[环境门禁] DY_APP_ROOT 指向主分支环境，拒绝运行")
    os.environ["DY_APP_ROOT"] = DESIGN_ROOT
    sys.path.insert(0, BACKEND)
    os.chdir(BACKEND)
    with open(os.path.join(DESIGN_ROOT, "members", ".session.json"), encoding="utf-8") as f:
        sess = json.load(f)
    os.environ["DY_MEMBER"] = sess["member_id"]
    os.environ["DY_MEMBER_KEY"] = sess["master_key"]

    acc_root = os.path.join(DESIGN_ROOT, "members", sess["member_id"], "auto_dm", "accounts")
    names = sorted(a for a in os.listdir(acc_root)
                   if os.path.isdir(os.path.join(acc_root, a)))
    from auto_dm import accounts as acc

    print(f"数据根 = {DESIGN_ROOT}")
    print(f"账号   = {names}\n")
    verdicts = {}
    for name in names:
        print("=" * 74)
        print("账号：", name)
        # 模拟用户点「引擎校验」：dm_loopback=True → probe_im_write(force=True)
        v = acc.verify_account(name, dm_loopback=True, auto_fix=False)
        wp, dm = v["wp"], v["dm"]
        print(f"  wp : {wp['level']:>7} | {wp['label']}")
        print(f"        {wp['detail'][:150]}")
        print(f"  dm : {dm['level']:>7} | {dm['label']}")
        print(f"        {dm['detail'][:200]}")
        print(f"  综合 ok = {v['ok']}")
        verdicts[name] = (wp["level"], dm["level"])
    print("\n" + "=" * 74)
    print("【汇总】", json.dumps(verdicts, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
