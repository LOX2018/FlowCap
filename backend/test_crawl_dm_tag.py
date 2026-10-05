"""批量私信「标签系统接入」契约测试（★ 2026-10-03，方案A）。

## 为什么必须有（这是本轮修掉的缺陷本身）

实测发现悬浮窗里三个控件全是**假接线**：
  · `selectedTag`（标签下拉）只进 UI state，**从未发给后端** → 选标签毫无效果；
  · `maxSend`（发送条数）只进 UI state，**从未使用**；
  · `crawlDmBatch` **从未被调用**。
三者共同特征 =「UI 有控件、后端无接线」的**静默失效**，用户无从察觉
（符合项目「禁止假成功」红线）。本门禁守住修复后的契约不被回退。

## 六段契约

- design  ：悬浮窗选标签 → 本次运行按该标签读 `crawl.*` 参数（优先级高于账号默认绑定）
- contract：① `tag_id` 存在于 `CrawlDmBatchRequest`；② 空串 = 不覆盖（回落账号绑定）；
            ③ 该模型 `extra="forbid"`（否则 tag_id 被静默丢弃 = 缺陷复发）；
            ④ `_crawl_cfg` 的覆盖标签优先级 > 账号绑定 > 全局 > 缺省
- deviation：修复前 `selectedTag` 未发往���端；`CrawlDmBatchRequest` 无 `extra` 约束
- chain   ：前端 Select → `crawlDmBatch({tag_id})` → `_crawl_cfg(k, acct, scope_override)`
            → `app_config.get("crawl", k, scope=标签)`
- root    ：标签机制（`config_tag.scope_of`）早已存在于后端，缺的是**前端接线**与
            「本次显式覆盖」入口
- verify  ：本文件
"""
import importlib.util
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

# 🔴 数据隔离（2026-10-04 修复 R1 违规）：本测试会经 app_config 写库，
# 若不设 FLOWCAP_APP_ROOT，`database._db_path()` 会回退到**源码树**（M-26/M-27 同型），
# 在 FlowCap/data/ 落一个 flowcap.db ⇒ 被 check_iron_rules.py 的 R1 拦下
# （提交直接失败）。用临时目录隔离，与其他测试同范式。
_TMP = os.path.join(os.environ.get("TEMP", "/tmp"), "flowcap_crawl_dm_tag_test")
os.makedirs(_TMP, exist_ok=True)
os.environ["FLOWCAP_APP_ROOT"] = _TMP
os.environ["DY_DATA_DIR"] = _TMP

_mod = None
_load_error = ""
try:
    if not os.path.exists(os.path.join(_HERE, "api", "crawl.py")):
        raise FileNotFoundError(os.path.join(_HERE, "api", "crawl.py"))
    from api import crawl as _mod  # noqa: PLC0415
except Exception as _e:  # noqa: BLE001 —— 导入失败必须显式失败，不静默通过
    _load_error = f"{type(_e).__name__}: {_e}"


class TestDmBatchTagWiring(unittest.TestCase):
    """模型层：批量私信的标签字段契约。"""

    def setUp(self):
        if _mod is None:
            self.fail(f"api/crawl.py 导入失败（门禁必须真跑）：{_load_error}")

    def test_tag_id_field_exists(self):
        """① `tag_id` 必须真实存在于模型（否则前端发了也被丢）。"""
        f = _mod.CrawlDmBatchRequest
        self.assertIn("tag_id", f.model_fields)
        self.assertEqual(f.model_fields["tag_id"].default, "")

    def test_extra_forbid_prevents_silent_drop(self):
        """🔴③ `extra='forbid'`：tag_id 被静默丢弃正是本轮修的缺陷，必须有约束。"""
        f = _mod.CrawlDmBatchRequest
        with self.assertRaises(Exception):
            f(account="a", text="t", unknown_xyz=1)

    def test_tag_id_accepted(self):
        """负控自证：`forbid` 没误伤合法字段。"""
        m = _mod.CrawlDmBatchRequest(account="a", text="t", tag_id="tag_1",
                                     max_send=5, min_score=3)
        self.assertEqual(m.tag_id, "tag_1")
        self.assertEqual(m.max_send, 5)
        self.assertEqual(m.min_score, 3)

    def test_empty_tag_means_no_override(self):
        """② 空串 = 不覆盖（回落账号默认绑定），不得被当成「选中某标签」。"""
        m = _mod.CrawlDmBatchRequest(account="a", text="t", tag_id="")
        self.assertEqual((m.tag_id or "").strip() or None, None)
        m2 = _mod.CrawlDmBatchRequest(account="a", text="t", tag_id="   ")
        self.assertEqual((m2.tag_id or "").strip() or None, None)


class TestCrawlCfgOverridePriority(unittest.TestCase):
    """④ `_crawl_cfg` 的四级回落链 + 覆盖标签优先级。"""

    def setUp(self):
        if _mod is None:
            self.fail(f"api/crawl.py 导入失败（门禁必须真跑）：{_load_error}")

    def test_signature_accepts_scope_override(self):
        import inspect
        sig = inspect.signature(_mod._crawl_cfg)
        self.assertIn("scope_override", sig.parameters,
                      "_crawl_cfg 必须支持 scope_override（方案A 的入口）")

    def test_override_scope_wins_over_account(self):
        """🔴 覆盖标签的值必须**优先于**账号默认绑定。

        ⚠️ 必须用 `_CRAWL_FALLBACK` 里**真实存在**的 key（否则回落分支
        会 `KeyError`，把「优先级错」伪装成「测试写错」——第一版就是这么红的）。
        打桩 `app_config.get` / `config_tag.scope_of`，不碰真实配置与数据库。
        """
        KEY = "batch_min_score"          # 存在于 _CRAWL_FALLBACK
        calls = []

        class _FakeAppConfig:
            @staticmethod
            def get(section, key, default=None, scope=None):
                calls.append((key, scope))
                return {("T_OVR", KEY): "99",      # 覆盖标签下的值
                        ("T_ACCT", KEY): "11"}.get((scope, key), default)

        class _FakeConfigTag:
            @staticmethod
            def scope_of(account, section=""):
                return "T_ACCT"

        import services.app_config as real_ac
        import services.config_tag as real_ct
        old_ac, old_ct = real_ac.get, real_ct.scope_of
        real_ac.get = _FakeAppConfig.get
        real_ct.scope_of = _FakeConfigTag.scope_of
        try:
            v = _mod._crawl_cfg(KEY, "some_account", "T_OVR")
        finally:
            real_ac.get = old_ac
            real_ct.scope_of = old_ct

        self.assertEqual(str(v), "99", "显式覆盖标签必须优先于账号绑定")
        self.assertIn((KEY, "T_OVR"), calls,
                      "必须真的按覆盖标签 scope 读了一次")

    def test_no_override_falls_back_to_account_scope(self):
        """空 override ⇒ 读账号绑定标签（原有行为不被破坏）。"""
        KEY = "batch_min_score"
        calls = []

        class _FakeAppConfig:
            @staticmethod
            def get(section, key, default=None, scope=None):
                calls.append((key, scope))
                return {("T_ACCT2", KEY): "11"}.get((scope, key), default)

        class _FakeConfigTag:
            @staticmethod
            def scope_of(account, section=""):
                return "T_ACCT2"

        import services.app_config as real_ac
        import services.config_tag as real_ct
        old_ac, old_ct = real_ac.get, real_ct.scope_of
        real_ac.get = _FakeAppConfig.get
        real_ct.scope_of = _FakeConfigTag.scope_of
        try:
            v = _mod._crawl_cfg(KEY, "acct", None)
            v2 = _mod._crawl_cfg(KEY, "acct", "")   # 空串 = 不覆盖
        finally:
            real_ac.get = old_ac
            real_ct.scope_of = old_ct

        self.assertEqual(str(v), "11", "无覆盖时必须沿用账号绑定标签")
        self.assertEqual(str(v2), "11", "空串覆盖必须等同于不覆盖")
        # 负控自证：两次都没用覆盖标签去读
        self.assertNotIn((KEY, ""), calls)

    def test_falls_back_to_fallback_default(self):
        """读不到任何配置 ⇒ 回落 `_CRAWL_FALLBACK`（不是 None、不是崩）。"""
        class _FakeNone:
            @staticmethod
            def get(section, key, default=None, scope=None):
                return None

        import services.app_config as real_ac
        old = real_ac.get
        real_ac.get = _FakeNone.get
        try:
            v = _mod._crawl_cfg("batch_min_score", "acct", "T_X")
        finally:
            real_ac.get = old
        self.assertEqual(
            v, _mod._CRAWL_FALLBACK["batch_min_score"],
            "覆盖标签读不到时必须继续回落，不得返回 None")


if __name__ == "__main__":
    unittest.main(verbosity=2)
