# -*- coding: utf-8 -*-
"""BCC 内核不可用 → 致命态熔断 守卫（防「内核缺失 → 3s 一轮重建风暴」回归）。

## 这一条守的是什么（真实事故，2026-09-24）

Camoufox 内核缓存被上游 `camoufox.pkgman.camoufox_path()` 自毁清空
（`INSTALL_DIR` 非空但 `.0.5_FLAG` 缺失 → `shutil.rmtree(INSTALL_DIR)`），
于是 `vbrowser.should_use_vb(cfg)` 抛 `[BCC-070]`。

原实现把它当普通异常：`_ensure_alive → _launch` 每 3.05s 重建一次 context，
实测 **136 次 BCC-006 / 138 次 BCC-070**（15:01~15:07 连续刷屏），
且每次重建在抖音侧都是一次「全新环境」访问 —— 不可自愈的条件被无限重试。

## 判据（正控 + 负控，缺一不可）

| 场景 | 期望 | 为什么必须有 |
|---|---|---|
| `should_use_vb` 抛 `[BCC-070]` | `_fatal_until` **被设置**（≈now+1800） | 正控：熔断真的生效 |
| `should_use_vb` 抛无关异常 | `_fatal_until` **不被设置** | 负控：熔断是**内核专属**，不是无差别吞错 |

负控的意义：若把熔断写成 `except Exception: self._fatal_until = ...`，正控仍会绿，
但会把「一切启动失败」都变成 30 分钟静默熔断（真实缺陷被掩盖）。故负控必须存在。

## 为什么用子进程

加载 `browser_daemon.py` 模块级代码会执行 `logger.remove()` / 建 FastAPI app，
在本进程内做会污染其它测试模块的 loguru sink 与 `sys.modules`。子进程隔离 = 零污染。
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest

_BACKEND = os.path.dirname(os.path.abspath(__file__))
_ENTRY = os.path.join(_BACKEND, "daemon", "browser_daemon.py")

# A-8 / M-17 隔离根单一化：子进程的 FLOWCAP_APP_ROOT 兜底一律用**一次性临时目录**，
# 禁止回落源码树（旧兜底 _BACKEND）。vbrowser.app_root() 对不存在的根会忽略并
# 回落仓库相对路径 data/，故必须先 makedirs 再赋值。范式见 test_uid_sink_ext.py:17-39。
_ROOT = tempfile.mkdtemp(prefix="bcc_kernel_breaker_")
os.makedirs(_ROOT, exist_ok=True)

_PROBE = r'''
import asyncio, importlib.util, os, sys, tempfile, time

BACKEND = r"{backend}"
ENTRY = r"{entry}"
sys.path.insert(0, BACKEND)
if not os.environ.get("FLOWCAP_APP_ROOT"):
    _p = tempfile.mkdtemp(prefix="bcc_kernel_probe_")
    os.makedirs(_p, exist_ok=True)
    os.environ["FLOWCAP_APP_ROOT"] = _p

# 入口文件末尾 `if __name__ == "__main__": main()` —— 以 __main__ 加载会真跑它，
# 故 stub uvicorn.run + 给合法 argv（与 test_bcc_module_identity_guard 同法）。
import uvicorn
uvicorn.run = lambda *a, **k: None
sys.argv = ["browser_daemon.exe", "--account", "GATE_PROBE", "--port", "19999",
            "--allow-any-port", "--force-duplicate"]
spec = importlib.util.spec_from_file_location("__main__", ENTRY)
m = importlib.util.module_from_spec(spec)
sys.modules["__main__"] = m
spec.loader.exec_module(m)

# —— 把 _launch 的外部依赖压到最小（不碰真 profile、不起真浏览器）——
from auto_dm import accounts as _acc
TMP = tempfile.mkdtemp(prefix="kernel_gate_probe_")
_acc.env_path_of = lambda name: os.path.join(TMP, "accounts", name, ".env")
_acc.profile_dir_of = lambda env_path: TMP

import auto_dm.vbrowser as VB


def _run_case(exc_text):
    c = m.BrowserContainer(account="GATE_PROBE")

    async def _noop_release():
        return None
    c._wait_profile_released = _noop_release   # 不扫 psutil / 不等进程

    def _boom(*a, **k):
        raise RuntimeError(exc_text)
    VB.should_use_vb = _boom

    try:
        asyncio.run(c._launch())
    except Exception:
        pass
    return float(getattr(c, "_fatal_until", 0.0) or 0.0)


_now = time.time()
_kernel = _run_case("[BCC-070] Camoufox 已启用但浏览器不可用（official/stable is not installed）")
_other = _run_case("some unrelated launch failure")

print("KERNEL_FATAL:" + ("SET" if _kernel > _now else "NOTSET"))
print("KERNEL_DELTA:" + str(int(_kernel - _now)))
print("OTHER_FATAL:" + ("SET" if _other > _now else "NOTSET"))
'''


class TestBccKernelUnavailableBreaker(unittest.TestCase):
    """内核缺失必须立刻熔断；无关启动失败不得被熔断吞掉。"""

    @classmethod
    def setUpClass(cls):
        if not os.path.isfile(_ENTRY):
            raise unittest.SkipTest(f"入口不存在: {_ENTRY}")

    def _probe(self) -> str:
        code = _PROBE.format(backend=_BACKEND, entry=_ENTRY)
        env = dict(os.environ)
        env["FLOWCAP_APP_ROOT"] = _ROOT
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, cwd=_BACKEND, env=env, timeout=180,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        self.assertIn(
            "KERNEL_FATAL:", out,
            f"探针未产出判据（子进程异常）\nstdout={proc.stdout[-3000:]}\n"
            f"stderr={proc.stderr[-3000:]}",
        )
        return out

    def test_kernel_unavailable_sets_fatal_breaker(self):
        """正控：BCC-070 → 熔断被设置，且约为 30 分钟。"""
        out = self._probe()
        self.assertIn("KERNEL_FATAL:SET", out,
                      f"内核不可用未触发熔断（重建风暴会复发）\n{out[-2500:]}")
        delta = int([l for l in out.splitlines()
                     if l.startswith("KERNEL_DELTA:")][0].split(":")[1])
        self.assertGreaterEqual(delta, 1500,
                                f"熔断时长过短({delta}s)，不足以防重建风暴")
        self.assertLessEqual(delta, 1900, f"熔断时长异常({delta}s)")

    def test_unrelated_failure_does_not_arm_breaker(self):
        """负控：无关异常不得被熔断 —— 否则真实缺陷被 30 分钟静默掩盖。"""
        out = self._probe()
        self.assertIn("OTHER_FATAL:NOTSET", out,
                      f"无关启动失败也被熔断了（熔断范围过宽，会掩盖真缺陷）\n{out[-2500:]}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
