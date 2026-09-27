# -*- coding: utf-8 -*-
"""负控：证明 test_f1_d1_d3.py 的门禁**真会拦**（不是恒绿的摆设）。

## 为什么必须跑负控

只证「门禁现在全绿」= 什么都没证 —— 恒真的断言也会全绿。必须证明：
**把被守护的行为改坏 → 该断言变红**。本项目定义：无负控的门禁 = 未验证。

## 负控清单（每个都做「篡改 → 断言必红 → 还原」）

  N1  把 `scope_of` 的房间级判定**去掉** ⇒ G1/G2 必红
  N2  把悬空引用改成「直接返回 id」（不校验）⇒ G3 必红
  N3  把非法枚举改成「静默回落默认值」（不拒）⇒ G6 必红
  N4  把 `tag_id` 重新塞回 `_normalize_room` 的残留清理名单 ⇒ G9 必红

## 判据

每个负控必须**真的让目标断言失败**。若篡改后仍全绿 ⇒ 该门禁对这项行为**无覆盖**，
本脚本以非零退出码报错（防「门禁看起来在守、实际没守」）。
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

_BACKEND = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable


def _run(pattern: str) -> tuple[int, str]:
    """跑门禁（-k 过滤到目标断言），返回 (exit_code, 输出尾部)。"""
    r = subprocess.run(
        [PY, "-m", "unittest", "test_f1_d1_d3", "-k", pattern, "-v"],
        cwd=_BACKEND, capture_output=True, text=True, timeout=300,
    )
    return r.returncode, (r.stdout + r.stderr)[-1200:]


def _patch_and_expect_red(path: str, old: str, new: str, pattern: str, label: str) -> bool:
    """篡改 path 中的 old→new，跑 pattern，期望**变红**，然后还原。返回是否通过。

    ★ 行尾自适应：本仓混用 CRLF（services/config_tag.py）与 LF（api/*.py）——
    负控脚本若只写死一种，会在另一种文件上「锚点命中 0 次」而**静默失效**
    （本项目实测踩到：N1/N2 因此假失败）。故统一把锚点按目标文件的行尾转换。
    """
    raw = open(path, encoding="utf-8", newline="").read()
    if raw.count("\r\n") and "\n" not in raw.replace("\r\n", ""):
        old = old.replace("\n", "\r\n")
        new = new.replace("\n", "\r\n")
    if raw.count(old) != 1:
        print(f"  ❌ [{label}] 锚点命中 {raw.count(old)} 次（应为 1）—— 负控脚本自身失效")
        return False
    open(path, "w", encoding="utf-8", newline="").write(raw.replace(old, new, 1))
    try:
        code, out = _run(pattern)
    finally:
        open(path, "w", encoding="utf-8", newline="").write(raw)   # 无条件还原
    if code == 0:
        print(f"  ❌ [{label}] 篡改后门禁**仍绿** ⇒ 该断言无覆盖（假门禁）")
        return False
    # 确认真的是「断言失败」，不是「导入错误」等噪声
    if "AssertionError" not in out:
        print(f"  ⚠️ [{label}] 变红但不是断言失败（可能语法/导入错）—— 计为失败")
        print("     " + out.strip().splitlines()[-1][:150])
        return False
    print(f"  ✅ [{label}] 篡改后如期变红（AssertionError）→ 已还原")
    return True


def main() -> int:
    ct = os.path.join(_BACKEND, "services", "config_tag.py")
    cp = os.path.join(_BACKEND, "api", "crawl_policy.py")
    results = []

    print("N1 · 去掉 scope_of 的房间级判定 ⇒ G1 必红")
    results.append(_patch_and_expect_red(
        ct,
        '    rt = str(room_tag or "").strip()\n'
        '    if rt and (get_tag(rt) or {}).get("name"):\n'
        '        return rt\n',
        '    rt = ""\n',
        "test_g1_room_beats_section_beats_account",
        "N1 房间级判定被移除"))

    print("N2 · 悬空引用改成直接返回 id（不做存在性校验）⇒ G3 必红")
    results.append(_patch_and_expect_red(
        ct,
        '    rt = str(room_tag or "").strip()\n'
        '    if rt and (get_tag(rt) or {}).get("name"):\n'
        '        return rt\n',
        '    rt = str(room_tag or "").strip()\n'
        '    if rt:\n'
        '        return rt\n',
        "test_g3_dangling_room_tag_falls_back",
        "N2 悬空引用未校验"))

    print("N3 · 非法枚举静默回落默认值（不拒）⇒ G6 必红")
    results.append(_patch_and_expect_red(
        cp,
        '            v = pick(f, clearable=True, default=_ENUM_DEFAULTS.get(f, ""))\n'
        '            if v not in allowed:\n'
        '                return {"ok": False,\n'
        '                        "error": f"字段 {f} 取值 {v!r} 非法（可选：{sorted(x for x in allowed if x) or \'空\'}）"}',
        '            v = pick(f, clearable=True, default=_ENUM_DEFAULTS.get(f, ""))\n'
        '            if v not in allowed:\n'
        '                v = _ENUM_DEFAULTS.get(f, "")',
        "test_g6_illegal_enum_rejected_not_silently_rewritten",
        "N3 非法枚举被静默改写"))

    print("N4 · 把 tag_id 塞回 _normalize_room 的残留清理名单 ⇒ G9 必红")
    results.append(_patch_and_expect_red(
        os.path.join(_BACKEND, "api", "live_rooms.py"),
        '    for junk in ("force_rescan", "forceRescan"):',
        '    for junk in ("force_rescan", "forceRescan", "tag_id"):',
        "test_g9_read_path_does_not_strip_tag_id",
        "N4 读取出口吞掉 tag_id"))

    print()
    print("=" * 64)
    ok = sum(1 for x in results if x)
    print(f"负控结果：{ok}/{len(results)} 通过")
    if ok == len(results):
        print(f"✅ {len(results)} 项负控全部如期变红 ⇒ 门禁对这些行为**确有覆盖**")
        # 收尾复核：负控全部还原后，门禁应回到全绿
        code, _ = _run("test_")
        print("✅ 还原复核：门禁回到", "全绿" if code == 0 else "❌ 未全绿（还原不干净）")
        return 0 if code == 0 else 1
    print("❌ 有负控未如期变红 ⇒ 存在假门禁，必须修断言")
    return 1


if __name__ == "__main__":
    sys.exit(main())
