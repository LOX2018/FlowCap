# -*- coding: utf-8 -*-
"""防回归：loguru 日志调用不得使用 printf 风格 / 双参数形态（2026-09-17）。

## 为什么需要（实测确立）

loguru 的 `logger.<level>(message, *args, **kwargs)` 把首个位置参数当作
**`str.format` 模板**，其余位置参数是**格式化值**。因此：

  `logger.warning("...: %s", e)`     → 模板里没有 `{}` → `e` 被**丢弃**，
                                        日志原样打印字面量 `"...: %s"`
  `logger.warning("SEC-UID-002", "详情")` → 只打印 `SEC-UID-002`，详情丢失

这与本项目早期用标准 logging / printf 风格的历史写法混淆，
实测（本文件 test_printf_style_args_are_dropped）确认参数**确实丢失**。
历史上因此丢过错误码正文、异常详情、cookie 统计等大量诊断信息。

## 判据

遍历 backend 下全部 `.py` 的 AST，找出形如
`logger.<level>(<str 常量>, ...其余位置参数)` 且**常量里不含 `{}`** 的调用。

- 含 `{}` 的一律放行（loguru 正确写法）
- 只传 1 个参数的一律放行（无格式化意图）
- 关键字参数不算（`logger.opt(...)` 等另论）
- 豁免：`logger.<level>(f"...")` 属 f-string（不是常量，天然不匹配）

运行：`python test_no_loguru_printf_style.py`
"""
from __future__ import annotations

import ast
import io
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.abspath(__file__))
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "build", "dist",
             "_ext_repos", "tests_integration"}
LEVELS = {"debug", "info", "success", "warning", "error", "critical", "exception"}


def _iter_py():
    me = os.path.basename(__file__)
    for dp, dn, fs in os.walk(ROOT):
        dn[:] = [d for d in dn if d not in SKIP_DIRS]
        for f in fs:
            # 跳过本文件：其中含**反例演示**行（故意用 printf 风格证明参数会丢），
            # 不是需要修复的缺陷。
            if f.endswith(".py") and f != me:
                yield os.path.join(dp, f)


def find_violations() -> list[tuple[str, int, str]]:
    out = []
    for p in _iter_py():
        try:
            src = open(p, encoding="utf-8").read()
            tree = ast.parse(src)
        except Exception:
            continue
        for n in ast.walk(tree):
            if not isinstance(n, ast.Call):
                continue
            fn = n.func
            if not (isinstance(fn, ast.Attribute) and fn.attr in LEVELS):
                continue
            if not (isinstance(fn.value, ast.Name) and fn.value.id == "logger"):
                continue
            pos = [a for a in n.args if not isinstance(a, ast.Starred)]
            if len(pos) < 2:
                continue
            first = pos[0]
            if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
                continue          # f-string / 变量 → 不判定
            if "{}" in first.value:
                continue          # 正确写法
            out.append((os.path.relpath(p, ROOT), n.lineno, first.value[:60]))
    return out


def test_no_printf_style_loguru() -> None:
    bad = find_violations()
    assert not bad, (
        "发现 loguru printf 风格/双参数调用（参数会被静默丢弃）：\n"
        + "\n".join(f"  {p}:{l}  {msg!r}" for p, l, msg in bad)
        + "\n请改为单串 f-string 或 `{}` 占位符。"
    )


class TestLoguruStyle(unittest.TestCase):
    """以 unittest 形式暴露（直接 `python -m unittest <本模块>` 可跑）。"""

    def test_no_printf_style_loguru(self) -> None:
        bad = find_violations()
        self.assertFalse(
            bad,
            "发现 loguru printf 风格/双参数调用（参数会被静默丢弃）：\n"
            + "\n".join(f"  {p}:{l}  {msg!r}" for p, l, msg in bad)
            + "\n请改为单串 f-string 或 `{}` 占位符。",
        )

    def test_printf_style_args_are_dropped(self) -> None:
        """实证 loguru 语义：printf 风格下参数**确实**丢失（锁定认知，防误判）。"""
        from loguru import logger

        buf = io.StringIO()
        sid = logger.add(buf, format="{message}")
        try:
            logger.warning("VALUE: %s", "SENTINEL-42")
            logged = buf.getvalue()
        finally:
            logger.remove(sid)
        self.assertNotIn("SENTINEL-42", logged, "loguru 语义已变，需重新评估本规则")
        self.assertIn("%s", logged, "预期打印字面量 %s")


if __name__ == "__main__":
    v = find_violations()
    if v:
        print(f"✗ 发现 {len(v)} 处 loguru printf 风格/双参数：")
        for p, l, msg in v:
            print(f"   {p}:{l}  {msg!r}")
        sys.exit(1)
    print("✓ 无 loguru printf 风格/双参数调用")
    unittest.main(verbosity=2)
