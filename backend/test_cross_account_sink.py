"""跨账号沉淀池测试（ADR-002 §5.5(B)）。"""
import os
import sys
import time
import unittest
import tempfile

_TMP = os.path.join(os.environ.get("TEMP", "/tmp"), "flowcap_crosssink_test")
os.makedirs(_TMP, exist_ok=True)
os.environ["FLOWCAP_APP_ROOT"] = _TMP
# 读 live_orchestration 配置 by 绕过 DB
os.environ["_TEST_CROSS_COOL_DAYS"] = "0.001"  # 约 86.4 秒 → 测试冷却短

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import services.dm_dispatch as dd
from database import get_db


def _fresh() -> dd.CrossAccountSink:
    """建新实例（每次清表、重置加载状态）。"""
    conn = get_db()
    conn.execute("DELETE FROM dm_cross_sink")
    conn.commit()
    s = dd.CrossAccountSink()
    s._loaded = False
    s._cache.clear()
    return s


class TestCrossAccountSink(unittest.TestCase):
    """ADR-002 §5.5(B) 跨账号沉淀池。"""

    def setUp(self):
        dd.CrossAccountSink._DEFAULT_COOL = 86.4  # 极短冷却

    def test_01_fresh_pass(self):
        """无历史：应放行。"""
        s = _fresh()
        ok, reason = s.should_send("3447676528502142")
        self.assertTrue(ok, reason)

    def test_02_after_sent_rejected(self):
        """mark_sent 后应拒绝（冷却期内）。"""
        s = _fresh()
        s.mark_sent("3447676528502142", "尚进工伤小助理", source="live")
        ok, reason = s.should_send("3447676528502142")
        self.assertFalse(ok, f"应拒绝但放行: {reason}")

    def test_03_cross_account_same_uid(self):
        """另一账号查同 uid 也应拒绝（跨账号去重）。"""
        s = _fresh()
        s.mark_sent("3447676528502142", "尚进工伤小助理", source="live")
        ok, reason = s.should_send("3447676528502142")
        self.assertFalse(ok, f"跨账号应拒绝但放行: {reason}")

    def test_04_different_uid_ok(self):
        """不同 uid 不受影响。"""
        s = _fresh()
        s.mark_sent("3447676528502142", "尚进工伤小助理")
        ok, reason = s.should_send("9999999999")
        self.assertTrue(ok, f"不同 uid 应放行: {reason}")


if __name__ == "__main__":
    unittest.main(verbosity=2)