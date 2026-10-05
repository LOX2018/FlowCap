"""
前后端联调测试套件（零外部依赖，仅用 fastapi.testclient + pytest 风格 unittest）

覆盖三层：
  1. 后端 API 契约：所有路由可达 + 响应结构正确
  2. 前后端契约一致性：前端 client.ts 调用路径 == 后端路由；前端页面依赖的响应字段对齐
  3. 引擎状态机：start/pause/resume/stop 状态枚举

运行：
  cd DYAutoDM_v2
  $env:PYTHONPATH=backend
  python -m unittest discover -s tests_integration -v
"""
import json
import unittest
from fastapi.testclient import TestClient

from main import app


class IntegrationTest(unittest.TestCase):
    # 用上下文触发 lifespan，确保 app.state.adm 初始化
    def _client(self):
        return TestClient(app)

    # ===== 1. 后端 API 契约：路由可达 =====
    def test_status_ok(self):
        with self._client() as c:
            r = c.get("/api/status")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn("ok", body)
        self.assertIn("running", body)

    def test_overview_ok(self):
        with self._client() as c:
            r = c.get("/api/overview")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        # 前端 Overview 页依赖扁平字段（OverviewResponse）
        for k in ("running", "paused", "queue", "browserDaemon", "recvDaemon", "engine_state"):
            self.assertIn(k, body, f"Overview 前端依赖字段缺失: {k}")

    def test_stats_ok(self):
        with self._client() as c:
            r = c.get("/api/stats")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        for k in ("total", "sent", "list"):
            self.assertIn(k, body, f"Stats 缺少字段 {k}")

    def test_accounts_list_ok(self):
        with self._client() as c:
            r = c.get("/api/accounts")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn("ok", body)
        self.assertIn("accounts", body)
        self.assertIsInstance(body["accounts"], list)

    def test_live_stream_ok(self):
        with self._client() as c:
            r = c.get("/api/live/stream")
        self.assertEqual(r.status_code, 200)

    def test_messages_conversations_ok(self):
        with self._client() as c:
            r = c.get("/api/messages/conversations?account=默认账号")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn("ok", body)
        self.assertIn("conversations", body)

    def test_tasks_get_ok(self):
        with self._client() as c:
            r = c.get("/api/tasks")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn("ok", body)

    def test_settings_get_ok(self):
        with self._client() as c:
            r = c.get("/api/settings")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn("ok", body)

    def test_logs_get_ok(self):
        with self._client() as c:
            r = c.get("/api/logs?limit=100")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn("ok", body)
        self.assertIn("lines", body)
        self.assertIsInstance(body["lines"], list)

    # ===== 2. 前端页面依赖的响应字段对齐 =====
    def test_overview_frontend_fields(self):
        with self._client() as c:
            body = c.get("/api/overview").json()
        bd = body.get("browserDaemon") or {}
        rd = body.get("recvDaemon") or {}
        self.assertIn("alive", bd, "Overview.browserDaemon.alive 缺失")
        self.assertIn("alive", rd, "Overview.recvDaemon.alive 缺失")

    def test_accounts_frontend_fields(self):
        with self._client() as c:
            body = c.get("/api/accounts").json()
        accts = body.get("accounts", [])
        if not accts:
            self.skipTest("无账号，跳过字段对齐（需真实账号数据）")
        a = accts[0]
        for k in ("name", "uid", "level", "label", "loggedIn",
                  "isMonitor", "isSender", "browserDaemonAlive",
                  "recvDaemonAlive", "wpEngine", "dmEngine"):
            self.assertIn(k, a, f"账号字段缺失: {k}")
        wp = a.get("wpEngine") or {}
        dm = a.get("dmEngine") or {}
        for k in ("level", "label"):
            self.assertIn(k, wp, "wpEngine 缺 level/label")
            self.assertIn(k, dm, "dmEngine 缺 level/label")

    def test_tasks_frontend_fields(self):
        """Tasks 页配置区依赖的字段（前端 tasks.tsx 期望扁平结构）"""
        with self._client() as c:
            body = c.get("/api/tasks").json()
        for k in ("dmPool", "maxTarget", "interval", "delay",
                 "forceRescan", "liveUrl", "enableDanmaku",
                 "enableConsole", "enableSend"):
            self.assertIn(k, body, f"Tasks 配置字段缺失: {k}")
        self.assertIsInstance(body["dmPool"], list, "dmPool 应为数组")

    def test_tasks_save_config_roundtrip(self):
        """Tasks 页 saveTaskConfig 按钮：发送 enable* 字段不应 422"""
        payload = {
            "dmPool": [{"text": "你好", "enabled": True}],
            "maxTarget": 5,
            "interval": 50.0,
            "delay": "40,65",
            "forceRescan": False,
            "liveUrl": "123456",
            "enableDanmaku": True,
            "enableConsole": True,
            "enableSend": True,
        }
        with self._client() as c:
            r = c.post("/api/tasks/config", json=payload)
        self.assertEqual(r.status_code, 200, f"saveTaskConfig 应 200，实际 {r.status_code}: {r.text}")
        self.assertTrue(r.json().get("ok"))

    def test_tasks_save_dm_pool(self):
        """Tasks 页保存词库按钮：发送 [{text,enabled}] 数组不应 422"""
        with self._client() as c:
            r = c.post("/api/tasks/dm-pool",
                       json=[{"text": "你好", "enabled": True}, {"text": "在吗", "enabled": False}])
        self.assertEqual(r.status_code, 200, f"saveDmPool 应 200，实际 {r.status_code}: {r.text}")

    def test_messages_request_route_exists(self):
        """Messages 页新建会话按钮 requestDm(name) -> POST /api/messages/request"""
        with self._client() as c:
            r = c.post("/api/messages/request", json={"name": "测试用户"})
        self.assertNotEqual(r.status_code, 404, "缺少 /api/messages/request 路由")
        self.assertIn(r.status_code, (200, 400, 422))

    def test_messages_send_contract(self):
        with self._client() as c:
            r = c.post("/api/messages/send",
                       json={"account": "默认账号", "conv_id": "x", "text": "hi"})
        self.assertIn(r.status_code, (200, 400, 422), f"sendDm 状态码异常 {r.status_code}")
        try:
            body = r.json()
            self.assertIsInstance(body, dict)
        except Exception:
            self.fail("sendDm 未返回 json")

    def test_live_resolve_contract(self):
        with self._client() as c:
            r = c.post("/api/live/resolve", json={"url": "123456789"})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn("ok", body)
        if body.get("ok"):
            self.assertIn("liveId", body)

    def test_engine_start_empty_url(self):
        """Engine 页开始按钮：空 live_url 应 400（而非 200 后崩溃）"""
        with self._client() as c:
            r = c.post("/api/engine/start", json={"live_url": ""})
        self.assertEqual(r.status_code, 400, "空 live_url 应被拒绝(400)")
        self.assertIn("detail", r.json())

    def test_engine_state_machine(self):
        """引擎状态机冒烟（不依赖真实直播，仅验证状态枚举接口可达）"""
        with self._client() as c:
            c.post("/api/engine/stop")
            r1 = c.post("/api/engine/pause")
            self.assertIn(r1.status_code, (200, 400), f"pause 异常 {r1.status_code}")
            r2 = c.post("/api/engine/resume")
            self.assertIn(r2.status_code, (200, 400), f"resume 异常 {r2.status_code}")
            r3 = c.post("/api/engine/stop-soft")
            self.assertIn(r3.status_code, (200, 400), f"stop-soft 异常 {r3.status_code}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
