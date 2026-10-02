# coding=utf-8
"""门禁：搜索域风控必须被如实上报，不得静默变成「无结果」（v0.46.18）。

背景
----
2026-10-02 实测事故：平台对搜索域下发业务层风控时，响应是
**HTTP 200 + status_code=0 + data=[] + search_nil_info.search_nil_type=
"verify_check"**。传输层完全正常 ⇒ 旧代码里「读 `_transport`」那套逻辑
**完全测不到**它 ⇒ `crawl_search` 返回 `{ok:true, items:[], total:0}`
⇒ 前端显示「未命中「工伤」」= 把平台风控说成「你的关键词没有作品」
（违反项目铁律：禁止假成功）。

本门禁覆盖 G1-G6，每条都**先有负控**（去掉修复必须变红）。
"""

import asyncio
import sys
import unittest
from pathlib import Path
from unittest import mock

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from services import search_probe  # noqa: E402
from services.search_probe import _classify  # noqa: E402

# 探针内部是**延迟 import** `dy_apis.douyin_api`（避免 import 期就拉起整条
# 基座链路）。故测试要先把它装进 sys.modules，mock 才有对象可打。
import dy_apis.douyin_api as _dy_api_mod  # noqa: E402


def _fake_api(payload):
    """构造一个只提供 search_general_work 的 DouyinAPI 替身（记录调用次数）。"""
    calls = []

    class _FakeAPI:
        @staticmethod
        def search_general_work(auth, query, st, pt, off, fd, sr, ct, **kw):
            calls.append(query)
            return payload

    _FakeAPI.calls = calls
    return _FakeAPI


class TestSearchDomainClassify(unittest.TestCase):
    """G1：七类响应必须映射到对应结论（不得把风控折成「无结果」）。"""

    def test_g1_classify_matrix(self):
        cases = [
            ("transport 403", None, {"status": 403, "bytes": 46}, 0, "fail"),
            ("transport 200 正常", None, {"status": 200, "bytes": 900}, 3, "ok"),
            ("verify_check", "verify_check", None, 0, "warn"),
            ("nil=other", "other_reason", None, 0, "warn"),
            ("nil=normal 且空", "normal", None, 0, "empty"),
            ("nil=normal 有数据", "normal", None, 25, "ok"),
            ("无 nil 有数据", None, None, 10, "ok"),
        ]
        for name, nt, tp, dl, expect in cases:
            with self.subTest(name):
                self.assertEqual(_classify(nt, tp, dl)[0], expect)


class TestSearchProbeContract(unittest.TestCase):
    """G2/G3：探针必须真发请求并保留 raw 事实（不得吞、不得空转）。"""

    def test_g2_probe_reports_nil_type(self):
        api = _fake_api({"status_code": 0, "data": [],
                         "search_nil_info": {"search_nil_type": "verify_check"}})
        search_probe.clear_cache()
        with mock.patch.object(sys.modules["dy_apis.douyin_api"],
                               "DouyinAPI", api):
            r = search_probe.probe_search_domain("acct_probe", auth=object())
        self.assertEqual(r["level"], "warn")
        self.assertEqual(r["nil_type"], "verify_check")
        self.assertFalse(r["ok"])
        self.assertIn("verify_check", r["label"])
        self.assertTrue(api.calls, "探针必须真的发请求（不得空转返回常量）")

    def test_g3_probe_ok_when_data_present(self):
        api = _fake_api({"status_code": 0,
                         "data": [{"aweme_info": {"aweme_id": "1"}}] * 3})
        search_probe.clear_cache()
        with mock.patch.object(sys.modules["dy_apis.douyin_api"],
                               "DouyinAPI", api):
            r = search_probe.probe_search_domain("acct_probe", auth=object())
        self.assertEqual(r["level"], "ok")
        self.assertTrue(r["ok"])
        self.assertEqual(r["data_len"], 3)


class TestCrawlSearchBlockedContract(unittest.TestCase):
    """G4/G5/G6：端点层必须把风控透出为 blocked，且**不落历史**。"""

    def setUp(self):
        from api import crawl as this_crawl
        self.crawl_mod = this_crawl
        self._orig_save = this_crawl._save_history
        self._orig_reason = this_crawl._search_blocked_reason
        self.saved = []

        async def _fake_save(account, kind, keyword, target, items):
            self.saved.append((kind, keyword, len(items)))

        self.crawl_mod._save_history = _fake_save

    def tearDown(self):
        self.crawl_mod._save_history = self._orig_save
        self.crawl_mod._search_blocked_reason = self._orig_reason

    def _body(self):
        class B:
            account = "acct_probe"
            query = "工伤"
            kind = "video"
            num = 10
            sort_type = None
            publish_time = None
            filter_duration = None
            policy_id = None
        return B()

    def _run_with_fakes(self, data):
        feat = mock.MagicMock()
        feat.search_work.return_value = {"ok": True, "data": data}
        policy = mock.MagicMock()
        policy.get_auth_for.return_value = object()
        with mock.patch.dict(sys.modules, {
            "features": feat,
            "services.auth_policy": policy,
        }):
            return asyncio.run(self.crawl_mod.crawl_search(self._body()))

    def test_g4_empty_result_triggers_blocked(self):
        self.crawl_mod._search_blocked_reason = lambda account, auth: "被平台风控拦截"
        r = self._run_with_fakes([])
        self.assertTrue(r.get("blocked"), "风控时必须 blocked=True")
        self.assertIn("风控", r.get("blocked_reason", ""))
        self.assertEqual(r.get("total"), 0)
        self.assertEqual(r.get("items"), [])
        self.assertEqual(self.saved, [], "风控空结果不得落历史（会污染采集统计）")

    def test_g5_real_empty_is_not_blocked(self):
        """负控：搜索域有权但真无结果 ⇒ **不得**报 blocked。"""
        self.crawl_mod._search_blocked_reason = lambda account, auth: None
        r = self._run_with_fakes([])
        self.assertFalse(r.get("blocked"), "真无结果不得误报风控（否则误导排查方向）")
        self.assertEqual(len(self.saved), 1, "真无结果应照常落历史")

    def test_g6_results_present_skips_probe(self):
        """负控：有结果时**不得**调探针（避免每次成功搜索都多打一个请求）。"""
        self.crawl_mod._search_blocked_reason = lambda account, auth: "不应被调用"
        r = self._run_with_fakes([{"aweme_info": {"aweme_id": "1",
                                                  "statistics": {}, "author": {}}}])
        self.assertFalse(r.get("blocked"))
        self.assertEqual(r.get("total"), 1)


class TestMatrixWiring(unittest.TestCase):
    """G7：能力矩阵必须有 search 面，且用途推导不得被 identity 掩盖。"""

    def test_g7_search_purpose_independent_of_identity(self):
        from auto_dm.accounts import derive_purposes

        p = derive_purposes({"static": "ok", "session": "ok", "identity": "ok",
                             "im_write": "ok", "live_read": "ok", "search": "fail"})
        self.assertFalse(p["search"]["usable"],
                         "identity=ok 不得把 search=fail 折成可用（单值掩盖分面失效）")
        self.assertTrue(p["danmaku_decrypt"]["usable"], "其它面不应受影响")
        # 三态纪律：unknown ≠ fail
        p2 = derive_purposes({"search": "unknown"})
        self.assertIsNone(p2["search"]["usable"], "unknown 必须折成待定，不得折成不可用")


if __name__ == "__main__":
    unittest.main(verbosity=2)
