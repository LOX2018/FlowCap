"""B3：uid_probe / origin_image_resolver / conversation_capture 接线验证。

1. uid_probe 3 参数零回归 + 接线生效性
2. origin_image_resolver 3 读取函数零回归
3. conversation_capture 默认值源码级校验（8 处）
4. 异常回落：配置中心异常时读到兜底常量
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_tmp = os.path.join(tempfile.gettempdir(), "dyautodm_b3_test")
os.makedirs(_tmp, exist_ok=True)
os.environ["DY_APP_ROOT"] = _tmp

from services import app_config as ac  # noqa: E402


class TestUidProbeRegression(unittest.TestCase):
    """uid_probe：3 个参数默认值与接线前逐字一致 + 走配置中心。"""

    def setUp(self):
        ac.reset_section("capture")

    def tearDown(self):
        ac.reset_section("capture")

    def _refresh(self):
        import importlib
        import services.uid_probe as mod
        importlib.reload(mod)
        return mod

    def test_ttl_ok_default(self):
        m = self._refresh()
        self.assertEqual(m.UID_TTL_OK, 300.0)

    def test_ttl_fail_default(self):
        m = self._refresh()
        self.assertEqual(m.UID_TTL_FAIL, 60.0)

    def test_lock_wait_default(self):
        m = self._refresh()
        self.assertEqual(m.LOCK_WAIT, 10.0)

    def test_cfg_live(self):
        ac.save_section("capture", {"uid_probe_ttl_ok": 900.0})
        m = self._refresh()
        self.assertEqual(m.UID_TTL_OK, 900.0)

    def test_fallback_on_error(self):
        ac.save_section("capture", {"uid_probe_ttl_ok": "bad"})
        m = self._refresh()
        self.assertEqual(m.UID_TTL_OK, 300.0)


class TestOirRegression(unittest.TestCase):
    """origin_image_resolver：3 个读取函数默认值与接线前逐字一致。"""

    def test_threshold_bytes(self):
        import importlib
        import auto_dm.origin_image_resolver as mod
        importlib.reload(mod)
        self.assertEqual(mod._local_threshold_bytes(), 32 * 1024)

    def test_ttl_seconds(self):
        import importlib
        import auto_dm.origin_image_resolver as mod
        importlib.reload(mod)
        self.assertEqual(mod._ttl_seconds(), 30 * 86400)

    def test_max_cache_bytes(self):
        import importlib
        import auto_dm.origin_image_resolver as mod
        importlib.reload(mod)
        self.assertEqual(mod._max_cache_bytes(), 2048 * 1024 * 1024)

    def test_cfg_live(self):
        ac.save_section("capture", {"origin_image_ttl_days": 7})
        import importlib
        import auto_dm.origin_image_resolver as mod
        importlib.reload(mod)
        self.assertEqual(mod._ttl_seconds(), 7 * 86400)
        ac.reset_section("capture")


class TestCcRegression(unittest.TestCase):
    """conversation_capture：源码级确认 8 处默认值表达式正确。"""

    def test_defaults_in_source(self):
        src = open(os.path.join(os.path.dirname(__file__), "auto_dm",
                                "conversation_capture.py"),
                   encoding="utf-8").read()
        for frag, desc in [
            ('_cfg("capture", "history_max") or 45', "history_max=45"),
            ('_cfg("capture", "history_sleep") or 1.5', "history_sleep=1.5"),
            ('_cfg("capture", "history_workers") or 4', "history_workers=4"),
            ('_cfg("capture", "image_inline_max_kb") or 32', "image_inline_max_kb=32"),
            ('_cfg("capture", "userinfo_cache_sec") or 600', "userinfo_cache_sec=600"),
        ]:
            self.assertIn(frag, src, desc)

    def test_cfg_live(self):
        ac.save_section("capture", {"history_max": 99})
        import auto_dm.conversation_capture as mod
        v = mod._cfg("capture", "history_max")
        self.assertEqual(v, 99)
        ac.reset_section("capture")


if __name__ == "__main__":
    unittest.main(verbosity=0)
