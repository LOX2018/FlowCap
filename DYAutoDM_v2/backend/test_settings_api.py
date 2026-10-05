"""settings 配置网关集成测试。

用 TestClient 真发 HTTP（不遍历 app.routes —— fastapi 0.141 会把
include_router 包装成 _IncludedRouter，遍历会 AttributeError，见知识库 12.4）。

这里挂一个**只含 settings 路由**的独立 app，绕开会员鉴权中间件，
专测网关自身的读写与重启计算逻辑。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_TMP = os.path.join(os.environ.get("TEMP", "/tmp"), "dyautodm_settings_test")
os.makedirs(_TMP, exist_ok=True)
os.environ["DY_APP_ROOT"] = _TMP

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api import settings as settings_api  # noqa: E402
from services import app_config as ac  # noqa: E402

app = FastAPI()
app.include_router(settings_api.router, prefix="/api/settings")
client = TestClient(app)


class TestSettingsGateway(unittest.TestCase):
    def setUp(self):
        for s in ac.SECTIONS:
            ac.reset_section(s)

    # ---- GET ----
    def test_get_returns_config_and_schema(self):
        r = client.get("/api/settings")
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertTrue(d["ok"])
        self.assertIn("config", d)
        self.assertIn("schema", d)
        self.assertIn("send", d["config"])
        self.assertEqual(d["schema"]["send"]["fields"]["min_interval"]["apply"],
                         "restart_daemon")

    def test_schema_endpoint(self):
        r = client.get("/api/settings/schema")
        self.assertEqual(r.status_code, 200)
        self.assertIn("live", r.json()["schema"])

    # ---- POST ----
    def test_post_saves_and_reports_restart(self):
        r = client.post("/api/settings",
                        json={"sections": {"send": {"min_interval": 12.0}}})
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertTrue(d["ok"])
        self.assertIn("send", d["saved_sections"])
        # min_interval apply=restart_daemon
        self.assertIn("daemon", d["restart_required"])
        self.assertEqual(ac.get("send", "min_interval"), 12.0)

    def test_post_hot_field_no_restart(self):
        r = client.post("/api/settings",
                        json={"sections": {"live": {"max_target": 9}}})
        d = r.json()
        self.assertEqual(d["restart_required"], [])
        self.assertEqual(ac.get("live", "max_target"), 9)

    def test_post_backend_field_restart(self):
        r = client.post("/api/settings",
                        json={"sections": {"general": {"bcc_on_start": True}}})
        d = r.json()
        self.assertIn("backend", d["restart_required"])

    # ---- 边界 ----
    def test_post_rejects_out_of_range(self):
        r = client.post("/api/settings",
                        json={"sections": {"send": {"min_interval": 0.5}}})
        self.assertEqual(r.status_code, 200)
        # 越界被丢弃，回落到默认 8.0
        self.assertEqual(ac.get("send", "min_interval"), 8.0)

    def test_post_ignores_unknown_section(self):
        r = client.post("/api/settings", json={"sections": {"evil": {"x": 1}}})
        self.assertEqual(r.json()["saved_sections"], [])

    def test_post_ignores_unknown_field(self):
        r = client.post("/api/settings",
                        json={"sections": {"live": {"__hack": 1}}})
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(ac.get("live", "__hack"))

    # ---- reset ----
    def test_reset_restores_default(self):
        client.post("/api/settings", json={"sections": {"live": {"max_target": 42}}})
        self.assertEqual(ac.get("live", "max_target"), 42)
        r = client.post("/api/settings/reset", json={"sections": ["live"]})
        self.assertEqual(r.status_code, 200)
        self.assertIn("live", r.json()["reset_sections"])
        self.assertEqual(ac.get("live", "max_target"), 3)

    # ---- 持久化 ----
    def test_persists_across_reload(self):
        client.post("/api/settings", json={"sections": {"live": {"interval": 123.0}}})
        # 模拟重启：重新读（app_config 每次 get 都从 kv 读，无内存缓存）
        self.assertEqual(ac.get("live", "interval"), 123.0)
        self.assertEqual(client.get("/api/settings").json()["config"]["live"]["interval"],
                         123.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
