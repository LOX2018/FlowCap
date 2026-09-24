# -*- coding: utf-8 -*-
"""T4 案例库清洗：按 item_id 逐条删 19 条问题条目（禁 LIKE 模糊删）。

用户 2026-09-24 授权「删」。判据（T4_report §2.2 + 父会话实读复核 19/19）：
  phone_in_q 5 / system_msg 2 / test_msg 6 / platform_rule_in_a 4 / single_char_q 2
删除方式：走**应用自身 API** `services.reply_kb.delete_item(id)`（= UI 的删法，逐条），
         绝不使用 SQL LIKE / 整表覆盖。删后自证：19 条清零 + 其余 80 条逐条比对无丢失。
"""
from __future__ import annotations

import json
import os
import sys

# 19 条问题条目（按 id 逐条，禁模糊）
SUSPECT = {
    1789813412419: "phone_in_q",
    1789813412425: "phone_in_q",
    1789829184302: "phone_in_q",
    1790042261580: "phone_in_q",
    1790042261591: "phone_in_q",
    1789829184289: "system_msg",
    1789829184290: "system_msg",
    1789829184286: "test_msg",
    1789829184287: "test_msg",
    1789829184294: "test_msg",
    1789829184297: "test_msg",
    1789962440926: "test_msg",
    1790075820534: "test_msg",
    1790042261583: "platform_rule_in_a",
    1790042261605: "platform_rule_in_a",
    1790075820522: "platform_rule_in_a",
    1790133547923: "platform_rule_in_a",
    1790042261584: "single_char_q",
    1790042261602: "single_char_q",
}


def main() -> int:
    os.environ["DY_APP_ROOT"] = r"C:\temp\dyautodm_design"
    os.environ["DY_MEMBER"] = "m17db0f8209156f26"
    sys.path.insert(0, os.path.abspath(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "backend")))

    from services import reply_kb

    before = reply_kb.list_items()
    before_ids = {it["id"] for it in before}
    clean_ids = before_ids - set(SUSPECT)
    print(f"删除前：{len(before)} 条；待删 {len(SUSPECT)}；应保留 {len(clean_ids)}")

    # 逐条删（每条单独调用，逐条记录结果）
    del_ok, del_miss = [], []
    for iid in SUSPECT:
        try:
            ok = reply_kb.delete_item(int(iid))
            (del_ok if ok else del_miss).append(iid)
            print(f"  delete_item({iid}) -> {ok}   # {SUSPECT[iid]}")
        except Exception as e:
            del_miss.append(iid)
            print(f"  delete_item({iid}) -> EXC {type(e).__name__}: {e}")

    after = reply_kb.list_items()
    after_ids = {it["id"] for it in after}
    remain = set(SUSPECT) & after_ids
    lost = clean_ids - after_ids

    print("\n=== 删后自证 ===")
    print(f"条目数：{len(before)} -> {len(after)}（期望 {len(clean_ids)}）")
    print(f"19 条问题仍残留：{sorted(remain) if remain else '无 ✅'}")
    print(f"clean 条目丢失：{sorted(lost) if lost else '无 ✅'}")
    print(f"逐条删除成功 {len(del_ok)}/19；失败 {len(del_miss)} {del_miss}")

    ok_all = (not remain) and (not lost) and len(after) == len(clean_ids) and not del_miss
    print("\n清洗判定:", "PASS" if ok_all else "FAIL")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
