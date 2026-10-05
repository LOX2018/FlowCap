# -*- coding: utf-8 -*-
"""L-6 门禁：`scripts/build_all.py::_run_shell` 的 shell 语义（2026-09-26 实机闭环）。

## 背景
台账 L-6 曾记「`build_all.py` 的 `_run_shell`（Win 需 `shell=True`）**已修未验证**」。
本门禁把它从「已修未验证」推进到**实机验证 + 机械判据**。

## 为什么必须 `shell=True`（Windows）
`npm` / `npx` 在 Windows 上是 **`.cmd` 批处理**，不是可执行文件。
`subprocess.run(["npx", ...], shell=False)` 会抛 `FileNotFoundError`（找不到 npx）。
只有经 shell 才能解析 `.cmd`。

## 判据（R1~R4，全部实机跑，非静态断言）
  R1  `_run_shell` 用 `shell=True` 调用 subprocess（AST 判定，防退回 shell=False）
  R2  真实 `.cmd` 文件可被解析执行，且其退出码**如实回传**（实机：rc==3）
  R3  非零退出码**不得被吞**（实机：rc==7）
  R4  带 `log_path` 时日志真实落盘且尾部输出可读（实机）
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent          # FlowCap/
_SCRIPTS = _ROOT / "scripts"
_BUILD = _SCRIPTS / "build_all.py"

sys.path.insert(0, str(_SCRIPTS))


class TestL6RunShell(unittest.TestCase):

    def test_r1_uses_shell_true(self):
        """R1：源码层必须 `shell=True`（否则 Windows 上 .cmd 无法解析）。"""
        tree = ast.parse(_BUILD.read_text(encoding="utf-8"))
        fn = next((n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name == "_run_shell"), None)
        self.assertIsNotNone(fn, "build_all._run_shell 未找到")
        calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
                 and getattr(n.func, "attr", "") == "run"]
        self.assertTrue(calls, "_run_shell 内未发现 subprocess.run 调用")
        ok = []
        for c in calls:
            for kw in c.keywords:
                if kw.arg == "shell" and isinstance(kw.value, ast.Constant):
                    ok.append(kw.value.value is True)
        self.assertTrue(ok and all(ok),
                        f"subprocess.run 的 shell 参数须恒为 True，实测={ok}")

    def _run_shell(self, cmd, cwd, log=None, label=""):
        import importlib
        mod = importlib.import_module("build_all")
        return mod._run_shell(cmd, Path(cwd), Path(log) if log else None, label=label)

    def test_r2_cmd_file_resolves_and_code_returned(self):
        """R2（实机）：真实 `.cmd` 可解析 + 退出码如实回传。"""
        with tempfile.TemporaryDirectory(prefix="l6_") as d:
            cmd = Path(d) / "t.cmd"
            cmd.write_text("@echo off\r\necho CMD_RESOLVED\r\nexit /b 3\r\n",
                           encoding="utf-8")
            rc = self._run_shell(str(cmd), d)
        self.assertEqual(rc, 3, ".cmd 未被 shell 解析（Windows 上 npx 会失败的同一根因）")

    def test_r3_nonzero_exit_not_swallowed(self):
        """R3（实机）：非零退出码必须透传，不得吞成 0。"""
        with tempfile.TemporaryDirectory(prefix="l6_") as d:
            rc = self._run_shell("exit 7", d)
        self.assertEqual(rc, 7, "非零退出码被吞 —— 构建失败会被误报为成功")

    def test_r3b_negative_control_plain_exe_also_works(self):
        """R3b 负控：普通内建命令仍返回 0（证明上面不是恒非零）。"""
        with tempfile.TemporaryDirectory(prefix="l6_") as d:
            rc = self._run_shell("echo ok", d)
        self.assertEqual(rc, 0)

    def test_r4_log_path_written(self):
        """R4（实机）：带 log_path 时日志真实落盘。"""
        with tempfile.TemporaryDirectory(prefix="l6_") as d:
            lg = Path(d) / "a.log"
            rc = self._run_shell("echo L6_LOGGED", d, log=lg, label="l6")
            self.assertEqual(rc, 0)
            self.assertTrue(lg.exists(), "log_path 未落盘")
            self.assertIn("L6_LOGGED", lg.read_text(encoding="utf-8", errors="replace"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
