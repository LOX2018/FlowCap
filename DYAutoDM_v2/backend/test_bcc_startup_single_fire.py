# -*- coding: utf-8 -*-
"""BCC 生命周期单次触发守卫（防「startup 跑两次」回归）。

## 这一条守的是什么（真实事故，2026-09-23）

`daemon/bcc_routes.py` 曾用 `@router.on_event("startup")` 定义 BCC 生命周期，
而 `browser_daemon.py` 用 `app.include_router(router)` 挂载 ⇒ **handler 跑两遍**：

| 后果 | 表现 |
|---|---|
| 两个 `BrowserContainer` 抢同一 profile | 第二个 Camoufox 启动失败（`BCC-058`） |
| `_state["container"]` 被后建的坏容器覆盖 | `/status` 恒 `alive:false` |
| keepalive 线程 ×2 + 每轮重建 | 用户所见「Camoufox 正在运行 / 反复激活」 |

上游机理（FastAPI 0.141.1 `fastapi/routing.py`，已读源码坐实）：
`include_router` 既 `add_event_handler("startup", h)` 注册 router 的 handler，
又把 router 的默认 lifespan 合并进来，而后者同样执行 `router._startup()`。

⇒ 定式：**路由可以 include，生命周期不行**（必须 `app.add_event_handler`）。

## 判据

真跑一次 uvicorn lifespan，数 `BrowserContainer.start()` 的调用次数：
**必须恰好 1**。且必须证明它**会变红** —— 见 `test_guard_would_catch_double_fire`。
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest

_BACKEND = os.path.dirname(os.path.abspath(__file__))

# A-8 / M-17 隔离根单一化：子进程 DY_APP_ROOT 兜底一律用**一次性临时目录**，
# 禁止回落源码树（旧兜底 _BACKEND）。先 makedirs 再赋值。范式见 test_uid_sink_ext.py:17-39。
_ROOT = tempfile.mkdtemp(prefix="bcc_startup_fire_")
os.makedirs(_ROOT, exist_ok=True)

_PROBE = r'''
import sys, os, time, threading, importlib.util, tempfile
BACKEND = r"{backend}"
sys.path.insert(0, BACKEND)
if not os.environ.get("DY_APP_ROOT"):
    _p = tempfile.mkdtemp(prefix="bcc_startup_probe_")
    os.makedirs(_p, exist_ok=True)
    os.environ["DY_APP_ROOT"] = _p

import uvicorn
uvicorn.run = lambda *a, **k: None       # 防 main() 真起服务
sys.argv = ["bcc.exe", "--account", "GUARD_PROBE", "--port", "{port}",
            "--allow-any-port", "--force-duplicate"]

spec = importlib.util.spec_from_file_location(
    "__main__", os.path.join(BACKEND, "daemon", "browser_daemon.py"))
m = importlib.util.module_from_spec(spec)
sys.modules["__main__"] = m
spec.loader.exec_module(m)

# ---- 打桩：容器启动不碰浏览器，只记次数 ----
CALLS = {{"start": 0, "keepalive": 0}}

async def _fake_start(self):
    CALLS["start"] += 1
    self._started = True
    self._context = object()          # 让 status()/shutdown 走到分支
    self._profile_dir = "GUARD"
    self._backend = "camoufox"

async def _fake_capture(self, *a, **k):
    return {{}}

def _fake_keepalive(self, stop_ev):
    CALLS["keepalive"] += 1
    stop_ev.wait()

m.BrowserContainer.start = _fake_start
m.BrowserContainer.capture_userinfo_map = _fake_capture
m.BrowserContainer.run_keepalive = _fake_keepalive

if "{inject_double}" == "1":
    # 事故形态注入：把 handler 再挂一次 → 必须被守卫抓到
    for _h in list(m.app.router.on_startup):
        m.app.router.add_event_handler("startup", _h)

cfg = uvicorn.Config(m.app, host="127.0.0.1", port={port}, log_level="error")
srv = uvicorn.Server(cfg)
t = threading.Thread(target=srv.run, daemon=True)
t.start()
time.sleep(4)
srv.should_exit = True
t.join(timeout=10)

print("STARTUP_CALLS:" + str(CALLS["start"]))
print("KEEPALIVE_THREADS:" + str(CALLS["keepalive"]))
'''


def _run(inject_double: bool, port: int):
    code = _PROBE.format(backend=_BACKEND, port=port,
                         inject_double="1" if inject_double else "0")
    env = dict(os.environ)
    env.setdefault("DY_APP_ROOT", _ROOT)
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, cwd=_BACKEND, env=env, timeout=180,
    )


class TestBccStartupSingleFire(unittest.TestCase):
    """lifespan 必须只进一次 —— 否则多容器抢同一 profile。"""

    def test_startup_fires_exactly_once(self):
        proc = _run(inject_double=False, port=19971)
        out = (proc.stdout or "") + (proc.stderr or "")
        self.assertIn(
            "STARTUP_CALLS:", out,
            f"探针未产出判据\nstdout={proc.stdout[-1500:]}\nstderr={proc.stderr[-1500:]}",
        )
        calls = int(out.split("STARTUP_CALLS:")[1].splitlines()[0].strip())
        self.assertEqual(
            calls, 1,
            "BCC lifespan 进入了 %d 次（应为 1）。多容器会抢同一 profile → "
            "BCC-058 Camoufox 启动失败 + /status 恒 alive:false + 「反复激活」。"
            "生命周期必须用 app.add_event_handler 挂载，禁止 @router.on_event。" % calls,
        )
        ka = int(out.split("KEEPALIVE_THREADS:")[1].splitlines()[0].strip())
        self.assertEqual(ka, 1, f"keepalive 线程起了 {ka} 个（应为 1）。")

    def test_guard_would_catch_double_fire(self):
        """负控：注入「再挂一次」后必须变红 —— 证明守卫不是恒真装饰。

        （`mechanical-gate-verification`：门禁必须证明它会拒绝。）
        """
        proc = _run(inject_double=True, port=19972)
        out = (proc.stdout or "") + (proc.stderr or "")
        self.assertIn("STARTUP_CALLS:", out, f"注入态探针异常\n{out[-1200:]}")
        calls = int(out.split("STARTUP_CALLS:")[1].splitlines()[0].strip())
        self.assertGreater(
            calls, 1,
            "注入双跑后 STARTUP_CALLS 仍为 %d —— 说明该守卫无法发现事故形态（假门禁）。" % calls,
        )

    def test_source_contract_no_router_lifecycle(self):
        """源码契约：bcc_routes.py 不得用 @router.on_event 定义生命周期。"""
        p = os.path.join(_BACKEND, "daemon", "bcc_routes.py")
        with open(p, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertNotIn(
            '@router.on_event("startup"', src,
            "bcc_routes.py 又用 @router.on_event 定义生命周期了 —— "
            "include_router 会让它跑两次。",
        )
        self.assertIn(
            "async def startup()", src,
            "bcc_routes.py 缺少导出的 startup()（供 app.add_event_handler 挂载）。",
        )


if __name__ == "__main__":
    unittest.main()
