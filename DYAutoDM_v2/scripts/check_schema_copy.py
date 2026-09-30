# -*- coding: utf-8 -*-
"""R15 门禁：配置中心字段文案的「简洁性」（用户 2026-09-30 定的 UI 文案铁律）。

## 为什么

用户原话：「55 个字的说明也很长啊，给普通用户留这么长的说明干什么，如果非要提示，
只需告诉他们这个选项是干嘛的就行了」。

hint 若过长，会：① 把字段在 flex 布局里撑宽（配置页分布崩坏，见 CASE-2026-09-30-UI）；
② 对用户零信息量（讲「为什么 / 怎么做 / 边界」属开发信息）。

## 判据（R15-1~R15-3）

  R15-1  字段 `hint` 长度 ≤ 18 字（超出即 FAIL，点名 section.field）
  R15-2  `label`（字段 + section）长度 ≤ 18 字
  R15-3  `hint` 不得含内部开发信息（`/api`、`BCC`、`源项目`、`**` markdown 等 —— R13 未覆盖的 schema 侧）

## 自检

  python scripts/check_schema_copy.py            # 度量，超限 exit 1
  python scripts/check_schema_copy.py --selftest # 负控：注入超长 hint 必须变红
"""
from __future__ import annotations

import argparse
import ast
import os
import re
import sys

HINT_MAX = 18
LABEL_MAX = 18
_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "backend", "services", "app_config_schema.py")
_INTERNAL = re.compile(r"/api/|\bBCC\b|源项目|better-douyin|hardcoded|\*\*|\.env\b|ADR-\d")


def _sections(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    for n in tree.body:
        if isinstance(n, ast.AnnAssign) and getattr(getattr(n, "target", None), "id", None) == "SECTIONS":
            return ast.literal_eval(n.value)
        if isinstance(n, ast.Assign) and any(getattr(t, "id", None) == "SECTIONS" for t in n.targets):
            return ast.literal_eval(n.value)
    raise RuntimeError("SECTIONS 未找到")


def check(path: str) -> list[str]:
    secs = _sections(path)
    bad: list[str] = []
    for sk, sv in secs.items():
        sl = sv.get("label") or ""
        if len(sl) > LABEL_MAX:
            bad.append(f"[R15-2] section {sk} label {len(sl)}>{LABEL_MAX}: {sl}")
        for fk, fv in (sv.get("fields") or {}).items():
            lab = fv.get("label") or ""
            if len(lab) > LABEL_MAX:
                bad.append(f"[R15-2] {sk}.{fk} label {len(lab)}>{LABEL_MAX}: {lab}")
            h = fv.get("hint")
            if h is None:
                continue
            if len(h) > HINT_MAX:
                bad.append(f"[R15-1] {sk}.{fk} hint {len(h)}>{HINT_MAX}: {h[:40]}…")
            m = _INTERNAL.search(h)
            if m:
                bad.append(f"[R15-3] {sk}.{fk} hint 含开发信息「{m.group(0)}」: {h[:40]}…")
    return bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true", help="注入超长 hint，期望 FAIL")
    args = ap.parse_args()

    target = _SRC
    if args.selftest:
        import tempfile
        src = open(_SRC, encoding="utf-8").read()
        # 在任意一个 hint 上注入超长内容 + 开发信息
        src = re.sub(r'"hint": "([^"]*)"',
                     lambda m: '"hint": "%s"' % ("X" * 30 + " /api/foo"),
                     src, count=1)
        fd, target = tempfile.mkstemp(suffix=".py"); os.close(fd)
        with open(target, "w", encoding="utf-8") as f:
            f.write(src)

    bad = check(target)
    print(f"check_schema_copy  阈值: hint≤{HINT_MAX} / label≤{LABEL_MAX}")
    if bad:
        print(f"  [FAIL] 命中 {len(bad)} 条：")
        for b in bad[:20]:
            print("    -", b)
        if args.selftest:
            print("  [OK] 负控：注入后确实变红 ✓")
            return 0
        return 1
    print("  [PASS] 全部字段文案合规")
    if args.selftest:
        print("  [FAIL] 负控：注入后仍绿 ⇒ 判据失效 ✗")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
