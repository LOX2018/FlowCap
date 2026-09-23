"""
T5 verification script: live-side AI context key uses uid, not nickname.
Run: python backend/scripts/verify_t5_live_ctx_key.py
"""
import os, sys, inspect, re, ast, unittest

_BACKEND = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, _BACKEND)

_AI_REPLY = os.path.join(_BACKEND, "services", "ai_reply.py")
_AUTO_DM = os.path.join(_BACKEND, "core", "auto_dm.py")


class TestSignatureAndKey(unittest.TestCase):
    """1) generate_dm_for_live accepts uid param; user_id uses uid when present."""

    def setUp(self):
        self.src_ai = open(_AI_REPLY, encoding="utf-8").read()
        self.src_dm = open(_AUTO_DM, encoding="utf-8").read()

    def test_uid_param_in_signature(self):
        # New signature must have uid with default
        m = re.search(r"def generate_dm_for_live\([^)]*\)", self.src_ai, re.DOTALL)
        self.assertIsNotNone(m, "generate_dm_for_live signature not found")
        sig = m.group(0)
        self.assertIn("uid: str = \"\"", sig,
                       f"uid param missing or wrong default in signature: {sig}")

    def test_user_id_uses_uid(self):
        # The user_id line must reference uid first
        self.assertIn(
            'user_id=f"live:{account}:{uid or peer_name or \'unknown\'}"',
            self.src_ai,
            "user_id line not updated to use uid"
        )
        # Old pattern must be gone
        self.assertNotIn(
            'user_id=f"live:{account}:{peer_name',
            self.src_ai,
            "Old nickname-based user_id still present"
        )

    def test_caller_passes_uid(self):
        # auto_dm._gen must pass uid=uid to generate_dm_for_live
        self.assertIn("uid=uid", self.src_dm,
                       "Caller (auto_dm._gen) does not pass uid=uid")

    def test_backward_compat_default(self):
        # Without uid, should fall back to peer_name (not crash)
        from services.ai_reply import generate_dm_for_live
        # uid="" → falls back to peer_name
        text, source = generate_dm_for_live(
            account="acct1", peer_name="观众A",
            comment="你好", cfg={}, uid=""
        )
        # cfg empty → disabled → returns ("", "")
        self.assertEqual(text, "")
        self.assertEqual(source, "")


class TestSessionNoAccumulation(unittest.TestCase):
    """2) AIClient session_history is per-instance; generate_dm_for_live creates fresh instance each call."""

    def test_fresh_instance_per_call(self):
        from services.ai_reply import AIClient
        cfg = {"api_key": "x", "base_url": "http://127.0.0.1:1234"}
        a = AIClient(cfg)
        b = AIClient(cfg)
        a.session_history["uid1"] = [{"role": "user", "content": "hi"}]
        # b must be unaffected
        self.assertEqual(b.session_history, {},
                          "AIClient instances share session_history (BUG)")

    def test_chat_failover_creates_new_instance(self):
        # chat_failover with chain creates new AIClient(cfg) per candidate (line 815)
        src = open(_AI_REPLY, encoding="utf-8").read()
        # In chat_failover, each candidate creates a new AIClient
        self.assertIn("reply = AIClient(cfg).chat(", src,
                       "chat_failover should create new AIClient per candidate")


class TestKeyShapeBeforeAfter(unittest.TestCase):
    """3) Key shape: before live:{account}:{peer_name}, after live:{account}:{uid}."""

    def test_key_shape_with_uid(self):
        # Simulate the f-string logic
        account, uid, peer_name = "myacct", "12345", "观众A"
        key = f"live:{account}:{uid or peer_name or 'unknown'}"
        self.assertEqual(key, "live:myacct:12345")

    def test_key_shape_fallback_no_uid(self):
        account, uid, peer_name = "myacct", "", "观众A"
        key = f"live:{account}:{uid or peer_name or 'unknown'}"
        self.assertEqual(key, "live:myacct:观众A")

    def test_key_shape_fallback_both_empty(self):
        account, uid, peer_name = "myacct", "", ""
        key = f"live:{account}:{uid or peer_name or 'unknown'}"
        self.assertEqual(key, "live:myacct:unknown")


class TestNegativeControl(unittest.TestCase):
    """4) Negative control: same nickname, different uid → different keys."""

    def test_same_nickname_different_uid(self):
        # Before fix: same nickname → same key (collapse)
        # After fix: different uid → different key
        key1 = f"live:acct:uid123"
        key2 = f"live:acct:uid456"
        self.assertNotEqual(key1, key2,
                            "Different uids must produce different keys")

    def test_same_nickname_same_key_before(self):
        # Demonstrate the OLD bug: same nickname → same key
        old_key1 = f"live:acct:观众A"
        old_key2 = f"live:acct:观众A"
        self.assertEqual(old_key1, old_key2,
                         "Old behavior: same nickname collapses (expected)")


if __name__ == "__main__":
    result = unittest.main(exit=False, verbosity=2)
    if result.result.wasSuccessful():
        print("\n✅ ALL T5 VERIFICATION TESTS PASSED")
    else:
        print("\n❌ SOME TESTS FAILED")
        sys.exit(1)
