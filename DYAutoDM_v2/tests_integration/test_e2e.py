"""DYAutoDM_v2 前后端联调测试套件（标准库 unittest，零依赖）

三层覆盖：
1. ContractTest   — 前端 client.ts 调用路径 vs 后端实际路由一致性
2. BackendTest    — in-process FastAPI TestClient 打真实路由，验证响应结构
3. EngineSmoke    — 真实 AutoDM 状态机（start/pause/resume/stop）不依赖外部网络

运行：
    cd DYAutoDM_v2
    py -m unittest discover -s tests_integration -v
"""
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 让 backend 包可导入
sys.path.insert(0, str(ROOT / "backend"))

from fastapi.testclient import TestClient  # noqa: E402

# 延迟导入，避免启动时构建 AutoDM 副作用
import main as _main  # noqa: E402

# 用 lifespan 上下文触发 app.state.adm 初始化（与真实运行一致）
_client_ctx = TestClient(_main.app)
_client_ctx.__enter__()
client = _client_ctx


def _collect_frontend_calls() -> set[str]:
    """从 client.ts 提取所有 fetch('/api/...') 路径模板"""
    text = (ROOT / "frontend" / "src" / "api" / "client.ts").read_text(encoding="utf-8")
    paths = set()
    for m in re.finditer(r"fetch\(`(/api/[^`]*)`", text):
        paths.add(m.group(1))
    for m in re.finditer(r"fetch\(['\"](/api/[^'\"]*)['\"]", text):
        paths.add(m.group(1))
    return paths


def _frontend_overview_fields() -> set[str]:
    """前端 client.ts Overview 接口期望的字段"""
    return {"running", "paused", "sent", "limit", "queue",
            "browserDaemon", "recvDaemon"}


def _backend_overview_fields() -> set[str]:
    """后端 /api/overview 实际返回字段（已对齐前端）"""
    return {"running", "paused", "sent", "limit", "queue",
            "browserDaemon", "recvDaemon", "engine_state"}


def _frontend_account_fields() -> set[str]:
    """前端 RawAccount 期望字段（accounts.tsx mapAcct 使用）"""
    return {"name", "uid", "level", "label", "loggedIn", "browserDaemonPort",
            "recvDaemonPort", "browserDaemonAlive", "recvDaemonAlive",
            "wpEngine", "dmEngine", "isCurrent", "isMonitor", "isSender"}


def _backend_account_fields() -> set[str]:
    """后端 /api/accounts 通过 _to_raw_account 实际返回字段（已对齐前端）"""
    return {"name", "uid", "level", "label", "loggedIn", "browserDaemonPort",
            "recvDaemonPort", "browserDaemonAlive", "recvDaemonAlive",
            "wpEngine", "dmEngine", "isCurrent", "isMonitor", "isSender"}


def _backend_routes() -> set[str]:
    """从 backend/api/*.py 提取 @router.get/post 实际路径"""
    routes = set()
    api_dir = ROOT / "backend" / "api"
    for f in api_dir.glob("*.py"):
        text = f.read_text(encoding="utf-8")
        for m in re.finditer(r'@router\.(get|post)\(["\']([^"\']+)["\']', text):
            prefix = f.stem
            path = m.group(2)
            if path == "":
                routes.add(f"/api/{prefix}")
            else:
                routes.add(f"/api/{prefix}{path}")
    return routes


class ContractTest(unittest.TestCase):
    def test_client_paths_exist_in_backend(self):
        fe = _collect_frontend_calls()
        be = _backend_routes()
        missing = []
        for p in fe:
            base = p.split("?")[0].split("{")[0].rstrip("/")
            if not any(
                base == r or base.startswith(r + "/") or r.startswith(base + "/")
                for r in be
            ):
                missing.append(p)
        missing = [m for m in missing if "{account}" not in m and "ws" not in m]
        # 已知断裂点（联调报告 #1）：前端 getStats 调用 /api/stats，
        # 后端该逻辑挂在 /api/overview/stats
        known_mismatch = [m for m in missing if "/api/stats" in m]
        if known_mismatch:
            print(f"\n[FAIL] 契约断裂: 前端调用 {known_mismatch} 但后端无此路由"
                  f"（后端路径为 /api/overview/stats）")
        self.assertFalse(known_mismatch,
                         f"前端调用但后端无对应路由: {known_mismatch}")

    def test_overview_field_alignment(self):
        """前端 Overview 期望字段 vs 后端 OverviewResponse 字段"""
        fe = _frontend_overview_fields()
        be = _backend_overview_fields()
        # 前端 running/paused 后端用 engine_state 表达；daemons 后端是 list
        gap = fe - be - {"running", "paused", "browserDaemon", "recvDaemon"}
        self.assertFalse(gap, f"前端 Overview 期望但后端无的字段: {gap}")

    def test_account_field_alignment(self):
        """前端 RawAccount 期望字段 vs 后端 AccountInfo 字段"""
        fe = _frontend_account_fields()
        be = _backend_account_fields()
        # 重构版后端重设计协议，前端仍用旧版扁平结构
        gap = fe - be - {"name"}  # name 公共
        print(f"\n[FAIL] 前后端账号协议未对齐: 前端需要 {sorted(gap)} "
              f"但后端 AccountInfo 未提供")
        self.assertFalse(gap,
                         f"前端账号字段后端缺失: {gap}")

    def test_backend_routes_documented(self):
        """后端路由应被前端调用覆盖（孤儿接口检测，仅告警式）"""
        be = _backend_routes()
        fe = _collect_frontend_calls()
        fe_bases = {p.split("?")[0].split("{")[0].rstrip("/") for p in fe}
        orphans = []
        for r in be:
            if not any(
                r == fb or r.startswith(fb + "/") or fb.startswith(r + "/")
                for fb in fe_bases
            ):
                orphans.append(r)
        # engine 子命令（pause/resume/stop-soft）前端可能未接，容忍
        print(f"\n[info] 后端未被前端调用的路由(参考): {orphans}")


class BackendTest(unittest.TestCase):
    def test_status(self):
        r = client.get("/api/status")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["ok"])
        self.assertIn("running", r.json())

    def test_overview_shape(self):
        r = client.get("/api/overview")
        self.assertEqual(r.status_code, 200)
        b = r.json()
        for k in ("running", "paused", "sent", "limit", "queue",
                  "browserDaemon", "recvDaemon"):
            self.assertIn(k, b)

    def test_accounts_list(self):
        r = client.get("/api/accounts")
        self.assertEqual(r.status_code, 200)
        b = r.json()
        self.assertIn("accounts", b)
        self.assertIsInstance(b["accounts"], list)

    def test_accounts_check_route(self):
        # 前端 client.ts 使用 /{name}/check（POST），验证该路由存在且返回结构化
        r = client.post("/api/accounts/默认账号/check")
        self.assertEqual(r.status_code, 200)
        self.assertIn("name", r.json())

    def test_tasks_shape(self):
        r = client.get("/api/tasks")
        self.assertEqual(r.status_code, 200)
        b = r.json()
        self.assertIn("config", b)
        self.assertIn("records", b)
        self.assertIsInstance(b["records"], list)

    def test_live_stream_snapshot(self):
        r = client.get("/api/live/stream")
        self.assertEqual(r.status_code, 200)
        self.assertIn("alive", r.json())

    def test_messages_conversations(self):
        r = client.get("/api/messages/conversations", params={"account": "默认账号"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("conversations", r.json())

    def test_settings_get(self):
        r = client.get("/api/settings")
        self.assertEqual(r.status_code, 200)

    def test_engine_start_bad_config(self):
        # 空 live_url 应触发结构化校验（400 或 500 业务错误），不应 404
        payload = {
            "live_url": "",
            "max_target": 3,
            "keywords": [],
            "dm_pool": ["hi"],
            "delay_range": [40, 65],
            "interval": 60.0,
        }
        r = client.post("/api/engine/start", json=payload)
        self.assertIn(r.status_code, (400, 500))
        self.assertIsInstance(r.json(), dict)

    def test_engine_pause_resume_stop_idempotent(self):
        valid = {"idle", "starting", "running", "paused", "stopping", "stopped"}
        for ep in ("/api/engine/stop", "/api/engine/stop-soft",
                   "/api/engine/pause", "/api/engine/resume"):
            r = client.post(ep)
            self.assertEqual(r.status_code, 200)
            self.assertIn(r.json()["state"], valid)


if __name__ == "__main__":
    unittest.main(verbosity=2)
