# -*- coding: utf-8 -*-
"""BCC 模块身份守卫（防「__main__ ↔ 包名」双份模块回归）。

## 这一条守的是什么（真实事故，2026-09-23）

BCC sidecar 以 `__main__` 身份运行 `daemon/browser_daemon.py`，而
`daemon/bcc_routes.py` 里有 `from daemon.browser_daemon import _state`
（P3-5 Step 2 路由抽取后新增）。若不做模块身份归一，同一文件会被**再加载一份**
`daemon.browser_daemon` 实例，于是：

| 后果 | 表现 |
|---|---|
| 两套模块级 `_state` | startup 读到空 `_state`（`account=""` / `port=0`） |
| `BrowserContainer(account="")` | `env_path` 为空 → `BCC-051` → **容器永远起不来** |
| 第二份模块再跑一遍模块级 `logger.remove()` | 删掉 `main()` 刚加的文件 sink → **BCC 日志恒 0 字节** |

⇒ 判据：`__main__._state` 与 `daemon.browser_daemon._state` 必须是**同一个对象**。

## 为什么用子进程

加载 `browser_daemon.py` 模块级代码会执行 `logger.remove()` / 建 FastAPI app，
在本进程内做会污染其它测试模块的 loguru sink 与 `sys.modules`（本项目
`replayable-regression-verification` §三·甲 已实证此类跨模块互染）。
子进程隔离 = 零污染，且能真实模拟 exe 入口。
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest

_BACKEND = os.path.dirname(os.path.abspath(__file__))
_ENTRY = os.path.join(_BACKEND, "daemon", "browser_daemon.py")

# A-8 / M-17 隔离根单一化：子进程 FLOWCAP_APP_ROOT 兜底一律用**一次性临时目录**，
# 禁止回落源码树（旧兜底 _BACKEND）。先 makedirs 再赋值（vbrowser.app_root()
# 忽略不存在的根 → 回落仓库 data/）。范式见 test_uid_sink_ext.py:17-39。
_ROOT = tempfile.mkdtemp(prefix="bcc_module_identity_")
os.makedirs(_ROOT, exist_ok=True)

_PROBE = r'''
import sys, os, importlib.util, tempfile
BACKEND = r"{backend}"
ENTRY = r"{entry}"
sys.path.insert(0, BACKEND)
if not os.environ.get("FLOWCAP_APP_ROOT"):
    _p = tempfile.mkdtemp(prefix="bcc_module_identity_probe_")
    os.makedirs(_p, exist_ok=True)
    os.environ["FLOWCAP_APP_ROOT"] = _p
# 入口文件末尾有 `if __name__ == "__main__": main()` —— 以 __main__ 加载会真跑它。
# stub uvicorn.run 防真起服务；给合法 argv 防 argparse 退出；--force-duplicate +
# --allow-any-port 防单例守卫 SystemExit（本机可能真有 BCC 在跑）。
import uvicorn
uvicorn.run = lambda *a, **k: None
sys.argv = ["browser_daemon.exe", "--account", "GUARD_PROBE", "--port", "19999",
            "--allow-any-port", "--force-duplicate"]
spec = importlib.util.spec_from_file_location("__main__", ENTRY)
m = importlib.util.module_from_spec(spec)
sys.modules["__main__"] = m
spec.loader.exec_module(m)
# main() 已把 args 写入 _state；这里再显式赋一个可辨识值
m._state["account"] = "GUARD_PROBE_ACCOUNT"
m._state["port"] = 19999
# ↓ 这一行就是事故的触发点（bcc_routes.py 里同样形态的 import）
from daemon.browser_daemon import _state as _s2
print("RESULT:" + ("SAME" if _s2 is m._state else "DIFF"))
print("ACCT:" + repr(_s2["account"]))
'''

# 后端入口（main.py）同类探针：请求期 api/accounts.py 会 `from main import app`
_PROBE_MAIN = r'''
import sys, os, importlib.util, tempfile
BACKEND = r"{backend}"
ENTRY = r"{entry}"
sys.path.insert(0, BACKEND)
if not os.environ.get("FLOWCAP_APP_ROOT"):
    _p = tempfile.mkdtemp(prefix="bcc_module_identity_probe_")
    os.makedirs(_p, exist_ok=True)
    os.environ["FLOWCAP_APP_ROOT"] = _p
import uvicorn
uvicorn.run = lambda *a, **k: None
sys.argv = ["main.exe", "--port", "19998"]
spec = importlib.util.spec_from_file_location("__main__", ENTRY)
m = importlib.util.module_from_spec(spec)
sys.modules["__main__"] = m
spec.loader.exec_module(m)          # 模块级建 app（lifespan 不会跑，不起守护）
import api.accounts as A
# 触发请求期那一行：from main import app
import main as M2
print("RESULT:" + ("SAME" if M2.app is m.app else "DIFF"))
print("HASADM:" + str(hasattr(getattr(m.app, "state", None), "adm")))
'''



class TestBccModuleIdentityGuard(unittest.TestCase):
    """BCC 的 `__main__` 与 `daemon.browser_daemon` 必须是同一份模块。"""

    @classmethod
    def setUpClass(cls):
        if not os.path.isfile(_ENTRY):
            raise unittest.SkipTest(f"入口不存在: {_ENTRY}")

    def test_main_and_package_name_are_same_module(self):
        """回归判据：跨模块 `from daemon.browser_daemon import _state` 必须命中同一对象。

        事故形态下此断言**必然失败**（DIFF + account=""）。修复后应为 SAME。
        """
        code = _PROBE.format(backend=_BACKEND, entry=_ENTRY)
        env = dict(os.environ)
        env["FLOWCAP_APP_ROOT"] = _ROOT
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, cwd=_BACKEND, env=env, timeout=120,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        self.assertIn(
            "RESULT:", out,
            f"探针未产出判据（子进程异常）\nstdout={proc.stdout[-2000:]}\n"
            f"stderr={proc.stderr[-2000:]}",
        )
        self.assertIn(
            "RESULT:SAME", out,
            "BCC 模块身份分叉：`from daemon.browser_daemon import _state` 拿到了"
            "第二份模块实例 —— 会导致 startup 读到空 _state（account=''）→"
            "BrowserContainer 起不来（BCC-051）+ BCC 日志恒 0 字节。\n"
            f"实测输出：{out[-800:]}",
        )
        self.assertIn(
            'ACCT:\'GUARD_PROBE_ACCOUNT\'', out,
            "跨模块读到的 _state 不是入口写入的那一份（account 不匹配）。",
        )

    def test_entry_declares_canonical_module_alias(self):
        """源码契约：入口必须显式声明「进程内只允许一份本模块」的归一动作。

        这是机械防复发——单靠行为断言在「恰好没触发反向 import」时会假绿。
        """
        with open(_ENTRY, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn(
            'sys.modules.setdefault("daemon.browser_daemon"',
            src,
            "入口缺少模块身份归一（sys.modules 规范名登记）。"
            "凡「以 __main__ 运行、又被同进程按包名 import」的入口都必须显式声明。",
        )

    # ------------------------------------------------------------------
    # 同一缺陷类的第二个受害点：backend 入口 main.py
    # ------------------------------------------------------------------

    def test_backend_main_and_package_name_are_same_module(self):
        """`from main import app` 必须命中 __main__ 那份 app（否则引擎实例取不到）。

        实证事故形态：第二份 `main` 模块 → 第二个 app，`app.state` 无 `adm`
        → `api/accounts._get_adm()` 返回 None → 「引擎实例不可用」。
        """
        entry = os.path.join(_BACKEND, "main.py")
        if not os.path.isfile(entry):
            self.skipTest(f"入口不存在: {entry}")
        code = _PROBE_MAIN.format(backend=_BACKEND, entry=entry)
        env = dict(os.environ)
        env["FLOWCAP_APP_ROOT"] = _ROOT
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, cwd=_BACKEND, env=env, timeout=180,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        self.assertIn(
            "RESULT:", out,
            f"探针未产出判据（子进程异常）\nstdout={proc.stdout[-1500:]}\n"
            f"stderr={proc.stderr[-1500:]}",
        )
        self.assertIn(
            "RESULT:SAME", out,
            "backend 入口模块身份分叉：`from main import app` 拿到第二份 app —— "
            "`app.state.adm` 不存在 → `_get_adm()` 返回 None → 「引擎实例不可用」。"
            f"\n实测输出：{out[-600:]}",
        )

    def test_backend_main_entry_declares_canonical_alias(self):
        """源码契约：main.py 必须有规范名登记（机械防复发）。"""
        entry = os.path.join(_BACKEND, "main.py")
        if not os.path.isfile(entry):
            self.skipTest(f"入口不存在: {entry}")
        with open(entry, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn(
            'sys.modules.setdefault("main"',
            src,
            "backend 入口缺少模块身份归一 —— api/accounts.py 与 api/notify.py "
            "会在请求期 `from main import app`，无归一即产生第二个 app。",
        )



if __name__ == "__main__":
    unittest.main()
