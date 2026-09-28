# -*- coding: utf-8 -*-
"""T6-b 门禁：Model Hub 提供商 api_key **读侧脱敏**，且**写侧零回归**。

背景（台账 §一·乙 T6「API Key 明文落库」的第一半）：
  `services/model_hub.py:overview()` 原样 `[dict(p) for p in providers]`
  返回 Provider 全字段，含 `api_key` 明文 ⇒ `GET /api/modelhub/overview`
  把真实密钥透传给前端。本批只做 **A 类读侧脱敏**（T6-c 加密存储另议）。

判据（K1~K5，K5 为负控）：
  K1  overview() 输出中不存在任何与注入真实 key 相同的明文串
  K2  脱敏串保留前 4 后 4、长度与原文一致
  K3  api_key_set：已配置 True / 空串 False
  K4  写侧不回归：save_provider 写明文后走存储层读回仍是明文原文
  K5  负控：临时摘掉 overview 的脱敏 ⇒ K1 必须变红；还原后复绿

隔离：DY_APP_ROOT 指向 tempfile.mkdtemp()，并在每个用例前重导
`database` + `services.*`（与 test_config_isolation / test_model_hub_v2
同款手法，防止 unittest 单进程复用别人的 DB 固化路径）。
**绝不写入 C:/temp/dyautodm_design**，也不写源码树 backend/data/。
"""
import importlib
import os
import sys
import tempfile
import unittest

_ROOT = tempfile.mkdtemp(prefix="dyautodm_t6b_mask_")
os.makedirs(_ROOT, exist_ok=True)
os.environ["DY_APP_ROOT"] = _ROOT
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REAL_KEY = "sk-TESTKEY-AAAABBBBCCCC1234"  # 28 字符，明显可辨识的样本
SHORT_KEY = "sk-short"                    # 8 字符，走「全 *」分支


def _reimport():
    """返回绑定到本模块隔离根的 (model_hub, database)。

    M-17 修复（2026-09-28）：原实现 `del sys.modules["database"/"services.*"]`
    会制造**重复模块对象** —— 此后 `from services import X` 得到新对象并绑定
    新的 `database`，而先前已导入的模块仍持旧对象 ⇒ 两边连的不是同一个 SQLite
    文件。实测（全量 1108 项）：本模块 setUp 删掉 `services.reply_kb` 后，
    `test_reply_kb_generality` 的 `R`（旧对象，写 d_old）与 `audit_legacy_auto`
    内 `from services import reply_kb`（新对象，读 d_new）分裂 ⇒ 写入丢失、
    `scanned=0` 假失败。改为对象身份不变：
      · env 钉回本模块 `_ROOT` + `database.reset_connection()`
      · `importlib.reload(model_hub)` 清模块级缓存
    """
    import database as _db
    os.environ["DY_APP_ROOT"] = _ROOT
    _db.reset_connection()
    import services.model_hub as _hub
    importlib.reload(_hub)
    return _hub, _db


class TestModelHubKeyMasking(unittest.TestCase):
    def setUp(self):
        self.hub, self.db = _reimport()
        conn = self.db.get_db()
        conn.execute("DELETE FROM kv_store WHERE key IN "
                     "('model_hub','model_hub.migrated')")
        conn.commit()
        # 标记已迁移 → _load 不再把 ai_reply 默认配置迁进来污染用例
        self.db.set_kv("model_hub.migrated", True)
        self.db.set_kv_json(self.hub._KV_KEY, {"migrated_v1": True})

    # ── K1：overview 输出中不存在明文真实 key ────────────────────────────────
    def test_k1_overview_leaks_no_plaintext_key(self):
        self.hub.save_provider({"name": "P1", "base_url": "http://a/v1",
                                "api_protocol": "openai", "api_key": REAL_KEY})
        blob = repr(self.hub.overview()) + str(self.hub.overview())
        self.assertNotIn(REAL_KEY, blob)
        # 逐 provider 再确认一次（避免 repr 折叠造成的假绿）
        for p in self.hub.overview()["providers"]:
            self.assertNotIn(REAL_KEY, str(p.get("api_key")))
            self.assertNotEqual(p.get("api_key"), REAL_KEY)

    # ── K2：脱敏串保留前 4 后 4、长度与原文一致 ──────────────────────────────
    def test_k2_mask_shape_keeps_first_and_last_4(self):
        p = self.hub._public_provider({"id": "x", "api_key": REAL_KEY})
        m = p["api_key"]
        self.assertEqual(len(m), len(REAL_KEY))
        self.assertTrue(m.startswith(REAL_KEY[:4]))
        self.assertTrue(m.endswith(REAL_KEY[-4:]))
        self.assertEqual(m[4:-4], "*" * (len(REAL_KEY) - 8))
        # 短串（<=8）全 *；空串仍空串
        self.assertEqual(self.hub.mask_secret(SHORT_KEY), "*" * len(SHORT_KEY))
        self.assertEqual(self.hub.mask_secret(""), "")
        # 中间段不许残留原文片段
        self.assertNotIn("AAAABBBBCCCC", m)

    # ── K3：api_key_set 区分已配置 / 未配置 ──────────────────────────────────
    def test_k3_api_key_set_flag(self):
        a = self.hub.save_provider({"name": "set", "base_url": "http://a/v1",
                                    "api_protocol": "openai",
                                    "api_key": REAL_KEY})
        b = self.hub.save_provider({"name": "unset", "base_url": "http://b/v1",
                                    "api_protocol": "openai", "api_key": ""})
        ov = {p["id"]: p for p in self.hub.overview()["providers"]}
        self.assertIs(ov[a["id"]]["api_key_set"], True)
        self.assertIs(ov[b["id"]]["api_key_set"], False)
        self.assertEqual(ov[b["id"]]["api_key"], "")

    # ── K4：写侧不回归 —— 存储层读回仍是明文原文 ─────────────────────────────
    def test_k4_write_path_still_plaintext(self):
        p = self.hub.save_provider({"name": "P", "base_url": "http://a/v1",
                                    "api_protocol": "openai",
                                    "api_key": REAL_KEY})
        # ① 走存储层（_load / list_providers），不是 overview
        raw = self.hub._load()
        stored = next(x for x in raw["providers"] if x["id"] == p["id"])
        self.assertEqual(stored["api_key"], REAL_KEY)
        # ② 再存一次（模拟前端「编辑但不改 key」提交空串）→ 原值必须保住
        self.hub.save_provider({"id": p["id"], "name": "P",
                                "base_url": "http://a/v1",
                                "api_protocol": "openai", "api_key": ""})
        stored2 = next(x for x in self.hub._load()["providers"]
                       if x["id"] == p["id"])
        self.assertEqual(stored2["api_key"], REAL_KEY)
        # ③ 消费侧（resolve_chain → _candidate）拿到的必须是明文
        m = self.hub.add_model(p["id"], "m1")["model"]
        self.hub.save_route("llm", [m["id"]])
        self.hub.set_consumer("ai_main", "route", route="llm")
        cands = self.hub.resolve_chain("ai_main")["candidates"]
        self.assertEqual(cands[0]["api_key"], REAL_KEY)
        # ④ 存储里没有被掩码污染
        for x in self.hub._load()["providers"]:
            if x["id"] == p["id"]:
                self.assertNotIn("*", x["api_key"])

    # ── K5 负控：摘掉脱敏 ⇒ K1 必须变红；还原后复绿 ───────────────────────────
    def test_k5_negative_control_removing_mask_turns_k1_red(self):
        self.hub.save_provider({"name": "P1", "base_url": "http://a/v1",
                                "api_protocol": "openai", "api_key": REAL_KEY})
        hub = self.hub
        orig = hub._public_provider
        try:
            # 负控：把脱敏摘掉（identity 视图 = 修复前的行为）
            hub._public_provider = lambda p: dict(p)
            leaked = repr(hub.overview())
            self.assertIn(REAL_KEY, leaked,
                          "负控失效：摘掉脱敏后 K1 仍未变红 ⇒ K1 是假绿")
            print(f"[K5-NEG] 摘掉脱敏后明文泄漏复现: "
                  f"REAL_KEY in overview() = {REAL_KEY in leaked}")
        finally:
            hub._public_provider = orig  # 必须还原
        # 复绿读数
        clean = repr(hub.overview())
        self.assertNotIn(REAL_KEY, clean)
        self.assertEqual(hub._public_provider, orig)
        print(f"[K5-RESTORE] 还原后 K1 复绿: "
              f"REAL_KEY in overview() = {REAL_KEY in clean}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
