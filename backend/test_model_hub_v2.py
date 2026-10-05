# -*- coding: utf-8 -*-
"""model_hub v2（提供商/模型/避障链路/兜底/消费方）单元测试（隔离 DB）。"""
import importlib
import os
import sys
import tempfile
import unittest

_ROOT = os.path.join(tempfile.gettempdir(), "flowcap_hubtest_root")
os.makedirs(_ROOT, exist_ok=True)
os.environ["FLOWCAP_APP_ROOT"] = _ROOT
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── 测试隔离（2026-09-22 修复；与 test_config_isolation 同款手法）──────────────
# 背景：`unittest discover` 把全部 test_*.py 导入**同一进程**，而每个测试文件都在
# 模块顶部改 `os.environ["FLOWCAP_APP_ROOT"]`。
# 若本模块复用了先前测试模块建立的 `database` 模块/连接（或其缓存），
# 本用例会去读写**别人**的库 → 「单独跑全绿、整体跑红」。
# 实测症状（本机全量 536 项）：本模块 3 项失败，报 `caps=['llm']` 缺 `vision`
# 与 `providers` 计数 2≠1 —— 即读到了别的库留下的残留状态。
#
# #### M-17 修复（2026-09-28）：把 `del sys.modules[...]` 换成 reload
#
# 原实现 ① `del sys.modules["database"/"services.*"]` ② 重导入 ③ 重置连接。
# 但 ① 会制造**重复模块对象**：重导入得到 d1，而此前已 `import database` 的模块
# （如 `services.ai_agent`）仍持有 d0 ⇒ 两边各写各的连接。本函数的调用点在
# **模块导入期**（`:30`）⇒ 污染面覆盖整个进程 —— 实测 `test_ai_agent`
# `TestZeroRegression` 2 项假失败：`reset_all()` 删的是 d1 的 kv，而
# `ai_agent` 读的是 d0，残留绑定（账号A→ag1 的 knowledge_base）永不被清掉。
#
# 正解（对象身份不变）：
#   · `os.environ["FLOWCAP_APP_ROOT"] = _ROOT` + `database.reset_connection()`
#     —— 连接按本模块隔离根重建（等价于旧「重导入」的净化效果，无对象分裂）
#   · `importlib.reload(model_hub)` —— 重跑模块体清掉模块级缓存
def _reimport():
    import database as _db
    os.environ["FLOWCAP_APP_ROOT"] = _ROOT
    _db.reset_connection()
    import services.model_hub as _hub
    importlib.reload(_hub)
    return _hub, _db


hub, _db = _reimport()
set_kv_json = _db.set_kv_json
set_kv = _db.set_kv
get_kv = _db.get_kv
get_db = _db.get_db


def _wipe():
    """清 hub kv + 迁移标记，防止 ai_reply 默认配置被迁入污染用例。"""
    conn = get_db()
    conn.execute("DELETE FROM kv_store WHERE key IN ('model_hub','model_hub.migrated')")
    conn.commit()


class TestModelHubV2(unittest.TestCase):
    def setUp(self):
        # 每用例都从「绑定本模块隔离根的干净 database」开始（防跨模块串库）
        global hub, set_kv_json, set_kv, get_kv, get_db
        hub, _db = _reimport()
        set_kv_json = _db.set_kv_json
        set_kv = _db.set_kv
        get_kv = _db.get_kv
        get_db = _db.get_db
        _wipe()
        # 标记已迁移 → _load 不再触发 ai_reply 默认配置迁入
        set_kv("model_hub.migrated", True)
        set_kv_json(hub._KV_KEY, {"migrated_v1": True})

    def test_provider_crud_and_models(self):
        p = hub.save_provider({"name": "测试A", "base_url": "http://x/v1/",
                               "api_protocol": "openai", "api_key": "sk-1"})
        self.assertEqual(p["base_url"], "http://x/v1")  # 尾斜杠剥离
        r = hub.add_model(p["id"], "glm-4v-plus")
        self.assertTrue(r["ok"])
        mid1 = r["model"]["id"]
        # 同名拒重
        self.assertFalse(hub.add_model(p["id"], "glm-4v-plus")["ok"])
        # 拉取预分类
        self.assertEqual(hub.list_models()[0]["caps"], ["llm", "vision"])
        r2 = hub.add_model(p["id"], "nvidia/nemotron-3-embed-1b")
        self.assertEqual(r2["model"]["caps"], ["sem"])
        # caps 手改
        self.assertTrue(hub.set_model_caps(mid1, ["llm"])["ok"])
        ms = {m["id"]: m for m in hub.list_models()}
        self.assertEqual(ms[mid1]["caps"], ["llm"])

    def test_route_and_fallback_rules(self):
        p = hub.save_provider({"name": "A", "base_url": "http://a/v1",
                               "api_protocol": "openai", "api_key": "k"})
        m_llm = hub.add_model(p["id"], "model-a")["model"]
        m_v = hub.add_model(p["id"], "vl-model")["model"]  # llm+vision
        m_se = hub.add_model(p["id"], "embed-1")["model"]
        # 链保存
        self.assertTrue(hub.save_route("llm", [m_llm["id"], m_v["id"]])["ok"])
        self.assertTrue(hub.save_route("vision", [m_v["id"]])["ok"])
        self.assertTrue(hub.save_route("sem", [m_se["id"]])["ok"])
        # >6 拒绝
        ids = [hub.add_model(p["id"], f"m{i}")["model"]["id"] for i in range(7)]
        self.assertFalse(hub.save_route("llm", ids)["ok"])
        # 兜底必须 llm+vision
        self.assertFalse(hub.set_fallback(m_llm["id"])["ok"])
        self.assertFalse(hub.set_fallback(m_se["id"])["ok"])
        self.assertTrue(hub.set_fallback(m_v["id"])["ok"])
        # sem 链不附兜底
        ch = hub.resolve_chain("ai_sem")
        self.assertIsNone(ch["fallback"])
        # 链里已含兜底模型则不重复（此时 llm 链=[model-a, vl-model]）
        ch = hub.resolve_chain("ai_main")
        self.assertEqual([c["model"] for c in ch["candidates"]],
                         ["model-a", "vl-model"])
        self.assertIsNone(ch["fallback"])
        # 链不含兜底模型时附加在末尾
        self.assertTrue(hub.save_route("llm", [m_llm["id"]])["ok"])
        ch = hub.resolve_chain("ai_main")
        self.assertEqual(ch["fallback"]["model_id"], m_v["id"])
        self.assertEqual([c["model"] for c in ch["candidates"]], ["model-a"])
        # 模型 caps 改掉后兜底自动失效
        self.assertTrue(hub.set_model_caps(m_v["id"], ["llm"])["ok"])
        self.assertEqual(hub.overview()["fallback"]["model_id"], "")

    def test_consumer_fixed_and_route(self):
        p = hub.save_provider({"name": "A", "base_url": "http://a/v1",
                               "api_protocol": "openai", "api_key": ""})
        m1 = hub.add_model(p["id"], "ma")["model"]
        m2 = hub.add_model(p["id"], "mb")["model"]
        hub.save_route("llm", [m1["id"], m2["id"]])
        # route 模式
        self.assertTrue(hub.set_consumer("ai_main", "route", route="llm")["ok"])
        ch = hub.resolve_chain("ai_main")
        self.assertEqual(ch["mode"], "route")
        self.assertEqual([c["model"] for c in ch["candidates"]], ["ma", "mb"])
        # fixed 模式
        self.assertTrue(hub.set_consumer("ai_main", "fixed", model_id=m2["id"])["ok"])
        ch = hub.resolve_chain("ai_main")
        self.assertEqual(ch["mode"], "fixed")
        self.assertEqual(ch["candidates"][0]["model"], "mb")
        # resolve() 兼容签名
        r = hub.resolve("ai_main")
        self.assertEqual(r["model"], "mb")
        # 未绑定消费方默认 llm 链
        ch = hub.resolve_chain("notify_cmd")
        self.assertEqual([c["model"] for c in ch["candidates"]], ["ma", "mb"])
        # fixed 模型删掉 → 自动解绑回落默认 llm 链（服务连续性优先）
        hub.delete_model(m2["id"])
        ch = hub.resolve_chain("ai_main")
        self.assertEqual(ch["mode"], "route")
        self.assertEqual([c["model"] for c in ch["candidates"]], ["ma"])

    def test_provider_delete_cascades(self):
        p = hub.save_provider({"name": "A", "base_url": "http://a/v1",
                               "api_protocol": "openai", "api_key": ""})
        m1 = hub.add_model(p["id"], "ma")["model"]
        hub.save_route("llm", [m1["id"]])
        hub.set_consumer("ai_main", "fixed", model_id=m1["id"])
        r = hub.delete_provider(p["id"])
        self.assertEqual(r["removed_models"], 1)
        o = hub.overview()
        self.assertEqual(o["models"], [])
        self.assertEqual(o["routes"]["llm"]["models"], [])
        self.assertNotIn("ai_main", o["consumers"])

    def test_migrate_from_v1(self):
        _wipe()  # 本用例要真实触发迁移
        v1 = {
            "endpoints": [{"id": "ep1", "name": "旧链路", "base_url": "http://old/v1",
                           "api_protocol": "openai", "api_key": "kk"}],
            "consumers": {
                "ai_main": {"endpoint_id": "ep1", "model": "old-model"},
                "ai_vision": {"endpoint_id": "ep1", "model": "vl-old"},
            },
        }
        set_kv_json(hub._KV_KEY, v1)
        data = hub._load()
        self.assertTrue(data["migrated_v1"])
        self.assertEqual(len(data["providers"]), 1)
        names = {m["model"] for m in data["models"]}
        self.assertEqual(names, {"old-model", "vl-old"})
        # ai_main 的模型进 llm 链；vision 消费方的模型进 vision 链
        self.assertEqual(len(data["routes"]["llm"]["models"]), 1)
        self.assertEqual(len(data["routes"]["vision"]["models"]), 1)
        self.assertEqual(len(data["routes"]["sem"]["models"]), 0)
        # 二次调用幂等（migrated 标记已写）
        data2 = hub._load()
        self.assertEqual(len(data2["providers"]), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
