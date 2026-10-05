# -*- coding: utf-8 -*-
"""门禁：评论采集效率与「多作品串行批量」（★ 2026-09-30，v0.45.125）。

## 为什么需要本门禁（防复发）

三件事同时被钉死，任何一件回退都会**变红**：

  ① **单页条数不得再被硬编码为 5**（原实现 `count="5"` ⇒ 采 100 条发 20 次请求）。
     实测服务端支持 20~50 条/页；本门禁断言「请求的 count 真的传到了基座」。
  ② **末级取数链路必须单一**：`/comments` 与 `/batch` 曾各写一套翻页
     （`cases/2026-09-28_内容中心评论区恒加载_末级链路分叉修复` 的反模式）。
     本门禁断言两条路径都经 `features.work_comments`。
  ③ **多作品批量必须串行 + 间隔**（同端点高频并发是账号级限流高发区）：
     断言两作品之间真的 `sleep(interval)`，且顺序不重叠。

## 负控（真实变红）
把 `_fetch_work_comments` 里的 count 改回固定 `"5"` ⇒ E1/E2 红；
把 `/batch` 改回直连 `DouyinAPI` ⇒ E4 红（直连哨兵抛出）；
去掉 `asyncio.sleep(interval)` ⇒ E5 红（间隔用时 < 阈值）。

## 安全性
全程打桩，**零真实抖音请求**；DY_APP_ROOT 指向临时目录，不碰仓库 data/。
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
import unittest

_BACKEND = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _BACKEND)
_ROOT = tempfile.mkdtemp(prefix="dyautodm_crawleff_")
os.makedirs(_ROOT, exist_ok=True)
os.environ["DY_APP_ROOT"] = _ROOT

import services.app_config as ac  # noqa: E402


class _StubAuth:
    pass


class _Feed:
    """记录调用并按预置页序列返回的 features.work_comments 桩。

    pages: [(comments, has_more, cursor), ...]；用尽后返回空批次。
    """

    def __init__(self, pages=None, fail=False):
        self.calls: list[tuple] = []
        self._pages = list(pages or [])
        self._fail = fail

    def __call__(self, auth, url, cursor="0", count="20"):
        self.calls.append((auth, url, cursor, count))
        if self._fail:
            return {"ok": False, "error": "被风控"}
        if self._pages:
            comments, has_more, cur = self._pages.pop(0)
        else:
            comments, has_more, cur = [], 0, ""
        return {"ok": True, "data": {"comments": comments, "has_more": has_more,
                                     "cursor": cur}}


def _c(cid):
    return {"cid": cid, "text": "t", "user": {"uid": "u" + cid, "nickname": "n"}}


class TestCommentEfficiency(unittest.TestCase):
    def setUp(self):
        self.features = sys.modules.get("features") or __import__("features")
        from api import crawl as c
        self.crawl = c
        self._saved_load = c._load_auth
        c._load_auth = lambda account: _StubAuth()
        # 2026-10-02 修补（实测 503 假失败）：`crawl_search` 已改走
        # `services.auth_policy.get_auth_for(...)`（fail-closed），只桩
        # `c._load_auth` 打不到真实取值路径 ⇒ 撞 503，与被测判据无关。
        from services import auth_policy
        self._saved_get_auth = auth_policy.get_auth_for
        auth_policy.get_auth_for = lambda endpoint, account: _StubAuth()
        self._saved_wc = self.features.work_comments
        # 间隔置 0，避免测试真等；间隔断言单独用例显式设置
        ac.reset_section("crawl")
        ac.save_section("crawl", {"batch_interval": 0.0})

    def tearDown(self):
        self.features.work_comments = self._saved_wc
        self.crawl._load_auth = self._saved_load
        from services import auth_policy
        auth_policy.get_auth_for = self._saved_get_auth
        ac.reset_section("crawl")

    # ---------------- E1：count 真实传导到基座 ----------------
    def test_e1_page_count_reaches_base(self):
        feed = _Feed()
        self.features.work_comments = feed
        body = self.crawl.CrawlCommentsRequest(
            account="acc", aweme_id="123", limit=100, count=50)
        asyncio.run(self.crawl.crawl_comments(body))
        self.assertTrue(feed.calls, "features.work_comments 未被调用")
        got = feed.calls[0][3]
        self.assertEqual(int(got), 50,
                         f"单页条数未传导到基座（得到 {got!r}）—— 是否又硬编码回 5？")

    # ---------------- E2：页数收敛（100 条 / 每页 50 ⇒ ≤3 页）----------------
    def test_e2_pages_collapse_not_twenty(self):
        # 语料：前两页各 50 条（has_more=1），第三页空 → 至多 3 次请求
        feed = _Feed(pages=[
            ([_c(str(i)) for i in range(50)], 1, "50"),
            ([_c(str(50 + i)) for i in range(50)], 0, "100"),
        ])
        self.features.work_comments = feed
        body = self.crawl.CrawlCommentsRequest(
            account="acc", aweme_id="123", limit=100, count=50)
        r = asyncio.run(self.crawl.crawl_comments(body))
        self.assertLessEqual(len(feed.calls), 3,
                             f"请求数 {len(feed.calls)} 未收敛（旧行为=20 次）")
        self.assertEqual(r["total"], 100)

    # ---------------- E3：派生上限与 clamp ----------------
    def test_e3_clamp_and_config_read(self):
        ac.save_section("crawl", {"comment_page_count": 50})
        self.assertEqual(self.crawl._page_count(0), 50, "未读配置中心值")
        self.assertEqual(self.crawl._page_count(999), 50, "上限未 clamp 到 50")
        self.assertEqual(self.crawl._page_count(1), 5, "下限未 clamp 到 5")
        self.assertEqual(self.crawl._page_count(20), 20, "显式入参被忽略")

    # ---------------- E4：/batch 也走 features（单一链路，无直连）----------------
    def test_e4_batch_uses_features_not_direct(self):
        from dy_apis.douyin_api import DouyinAPI
        saved = DouyinAPI.get_work_out_comment

        def _blocked(*a, **k):
            raise AssertionError("[负控] /batch 直连 DouyinAPI —— 末级链路又分叉了")
        DouyinAPI.get_work_out_comment = staticmethod(_blocked)

        feed = _Feed()
        self.features.work_comments = feed
        try:
            body = self.crawl.CrawlBatchRequest(
                account="acc", aweme_id="123", text="hi", limit=10, count=20)
            # max_send=0 ⇒ 不发送；但会先采评论
            asyncio.run(self.crawl.crawl_batch(body))
        finally:
            DouyinAPI.get_work_out_comment = saved
        self.assertTrue(feed.calls, "/batch 未走 features.work_comments")

    # ---------------- E5：多作品串行 + 间隔 ----------------
    def test_e5_multi_work_is_serial_with_interval(self):
        ac.save_section("crawl", {"batch_interval": 0.3})
        feed = _Feed()
        self.features.work_comments = feed
        body = self.crawl.CrawlCommentsBatchRequest(
            account="acc", aweme_ids=["a1", "a2", "a3"], limit=10, count=20)
        t0 = time.time()
        r = asyncio.run(self.crawl.crawl_comments_batch(body))
        dt = time.time() - t0
        self.assertEqual(r["works"], 3)
        # 3 个作品 ⇒ 2 个间隔
        self.assertGreaterEqual(dt, 0.55,
                                f"批量未按间隔串行（用时 {dt:.2f}s < 2×0.3s）")
        self.assertEqual(len(feed.calls), 3, "作品数≠取数次数")

    # ---------------- E6：失败隔离（单作品失败不拖垮整批）----------------
    def test_e6_single_failure_does_not_abort_batch(self):
        ac.save_section("crawl", {"batch_interval": 0.0})
        calls = {"n": 0}

        def _flaky(auth, url, cursor="0", count="20"):
            calls["n"] += 1
            if calls["n"] == 1:
                return {"ok": False, "error": "被风控"}
            return {"ok": True, "data": {"comments": [_c("x")], "has_more": 0,
                                         "cursor": "1"}}
        self.features.work_comments = _flaky
        body = self.crawl.CrawlCommentsBatchRequest(
            account="acc", aweme_ids=["bad", "good"], limit=10, count=20)
        r = asyncio.run(self.crawl.crawl_comments_batch(body))
        st = [w["status"] for w in r["per_work"]]
        self.assertEqual(st, ["failed", "ok"],
                         f"失败未隔离或被吞（{st}）—— 不得把失败静默成成功")
        self.assertEqual(r["ok_works"], 1)
        self.assertEqual(r["total_comments"], 1)

    # ---------------- E7：作品数上限（防误点长跑）----------------
    def test_e7_batch_work_cap(self):
        from fastapi import HTTPException
        ac.save_section("crawl", {"batch_max_works": 2})
        self.features.work_comments = _Feed()
        body = self.crawl.CrawlCommentsBatchRequest(
            account="acc", aweme_ids=["a", "b", "c"], limit=10)
        with self.assertRaises(HTTPException) as cm:
            asyncio.run(self.crawl.crawl_comments_batch(body))
        self.assertEqual(cm.exception.status_code, 400)


class TestAnonPreview(unittest.TestCase):
    """C 方案匿名预览门禁（★ 2026-09-30）。

    判据：
      A1 匿名预览**经 features**（走 `work_comments_anon`，不外泄到直连）
      A2 匿名预览**不需要 account**（请求体无 account 字段）
      A3 匿名预览**串行 + 间隔**（两作品之间真的 sleep）
      A4 匿名预览**失败隔离**（单作品失败不拖垮整批）
      A5 端点走**零凭证**基座（`get_work_out_comment_anon` 用 iesdouyin + 无 auth）
    """

    def setUp(self):
        self.features = sys.modules.get("features") or __import__("features")
        from api import crawl as c
        self.crawl = c
        self._saved = self.features.work_comments_anon
        ac.reset_section("crawl")
        ac.save_section("crawl", {"anon_preview_interval": 0.0})

    def tearDown(self):
        self.features.work_comments_anon = self._saved
        ac.reset_section("crawl")

    def test_a1_a2_a3_anon_preview_serial_no_account(self):
        """A1 经 features / A2 无 account / A3 **并发默认**（串行仅当 conc=1）。"""
        calls = []

        def _stub(aid):
            calls.append(aid)
            return {"ok": True, "data": {"comments": [{"cid": "c" + aid, "text": "t",
                                                        "user": {"nickname": "n"}}]}}
        self.features.work_comments_anon = _stub
        # A2：请求体**无 account** 字段（匿名端点不接收账号）
        self.assertNotIn("account", self.crawl.CrawlAnonPreviewRequest.model_fields)

        # --- A3a：默认并发（conc=6）⇒ 3 个作品耗时接近单次，而非 2×interval ---
        ac.save_section("crawl", {"anon_preview_concurrency": 6,
                                  "anon_preview_interval": 0.3})
        t0 = time.time()
        r = asyncio.run(self.crawl.crawl_comments_anon_preview(
            self.crawl.CrawlAnonPreviewRequest(aweme_ids=["a1", "a2", "a3"])))
        dt_concurrent = time.time() - t0
        self.assertEqual(sorted(calls), ["a1", "a2", "a3"],
                         "匿名预览未逐作品走 features 桩（A1）")
        self.assertTrue(r["anonymous"])
        self.assertEqual(r["total_comments"], 3)
        self.assertLess(dt_concurrent, 0.6,
                        f"默认并发未生效（{dt_concurrent:.2f}s ≥ 0.6s）—— "
                        "是否又退回串行？")

        # --- A3b：conc=1 时回退串行 + 真间隔（配置可回到串行） ---
        calls.clear()
        ac.save_section("crawl", {"anon_preview_concurrency": 1,
                                  "anon_preview_interval": 0.25})
        t0 = time.time()
        asyncio.run(self.crawl.crawl_comments_anon_preview(
            self.crawl.CrawlAnonPreviewRequest(aweme_ids=["a1", "a2", "a3"])))
        dt_serial = time.time() - t0
        self.assertGreaterEqual(dt_serial, 0.45,
                                f"conc=1 未按间隔串行（{dt_serial:.2f}s < 2×0.25s）")
        self.assertGreater(dt_serial, dt_concurrent,
                           "串行应比并发慢 —— 并发开关无判别力")

    def test_a4_failure_isolation(self):
        ac.save_section("crawl", {"anon_preview_interval": 0.0})
        n = {"i": 0}

        def _flaky(aid):
            n["i"] += 1
            if n["i"] == 1:
                return {"ok": False, "error": "网络" }
            return {"ok": True, "data": {"comments": [{"cid": "c", "text": "t",
                                                        "user": {"nickname": "n"}}]}}
        self.features.work_comments_anon = _flaky
        body = self.crawl.CrawlAnonPreviewRequest(aweme_ids=["bad", "good"])
        r = asyncio.run(self.crawl.crawl_comments_anon_preview(body))
        self.assertEqual([w["status"] for w in r["per_work"]], ["failed", "ok"])
        self.assertEqual(r["ok_works"], 1)

    def test_a5_base_method_is_credential_free(self):
        """基座 `get_work_out_comment_anon`：签名**不含 auth**，且指向 iesdouyin。"""
        import inspect
        from dy_apis.douyin_api import DouyinAPI
        sig = inspect.signature(DouyinAPI.get_work_out_comment_anon)
        self.assertNotIn("auth", sig.parameters,
                         "匿名方法不应接收 auth（零凭证契约）")
        from dy_apis import client_comments as cc
        self.assertIn("iesdouyin.com", cc._ANON_COMMENT_API,
                      "匿名端点应指向 iesdouyin（实测可达的零凭证端点）")


class TestPolicyWiring(unittest.TestCase):
    """★ 2026-09-30 门禁：「采集策略 → 采集链路」必须真的接通。

    ## 为什么需要
    接线前实测：`crawl_policies` 写入 num=50，而采集实际发的仍是 app_config 的值
    —— 参数层**写进去读不到**，前端契约 `resolveCrawlPolicy` 也无人调用，
    属「已建未用」家族（本项目反复踩到的形态）。

    判据：
      W1 搜索端点的排序/时段/时长/条数**留空即取策略**（不再恒用写死默认）
      W2 策略的 num 真的进入搜索调用（打桩捕获传参）
      W3 `crawl` 在 `config_tag.MANAGED_SECTIONS` 内（标签采集板块有消费者）
      W4 采集端点的 count 按**账号标签 scope** 读取（`_page_count(count, account)`）
      W5 `is_default` 白名单可写（否则第 ③ 级永远取不到）
    """

    def setUp(self):
        import api.crawl as c
        from api import crawl_policy as cp
        self.c = c
        self.cp = cp
        self._saved_load = c._load_auth
        c._load_auth = lambda account: _StubAuth()
        # 同上：补桩 auth_policy（crawl_search 的真实取值路径）
        from services import auth_policy
        self._saved_get_auth = auth_policy.get_auth_for
        auth_policy.get_auth_for = lambda endpoint, account: _StubAuth()
        from database import set_kv_json
        set_kv_json("crawl_policies", {})
        ac.reset_section("crawl")

    def tearDown(self):
        from database import set_kv_json
        set_kv_json("crawl_policies", {})
        ac.reset_section("crawl")
        self.c._load_auth = self._saved_load
        from services import auth_policy
        auth_policy.get_auth_for = self._saved_get_auth

    def test_w1_w2_policy_num_enters_search_call(self):
        """策略 num 必须进入真实搜索调用（打桩捕获）。"""
        from dy_apis.douyin_api import DouyinAPI
        asyncio.run(self.cp.save_policy(self.cp.PolicyBody(
            id="polA", name="保守", kind="video", num=37,
            sort_type="2", publish_time="7", is_default=True)))

        captured = {}

        def _stub_search(auth, q, num, sort_type, publish_time, filter_duration,
                         *a, **k):
            captured.update(num=num, sort_type=sort_type,
                            publish_time=publish_time,
                            filter_duration=filter_duration)
            return []
        saved = DouyinAPI.search_some_general_work
        DouyinAPI.search_some_general_work = staticmethod(_stub_search)
        try:
            body = self.c.CrawlSearchRequest(account="acc", query="kw")
            asyncio.run(self.c.crawl_search(body))
        finally:
            DouyinAPI.search_some_general_work = saved

        self.assertEqual(captured.get("num"), 37,
                         "策略 num 未进入搜索调用 —— 策略层又被旁路了")
        self.assertEqual(captured.get("sort_type"), "2", "策略 sort_type 未生效")
        self.assertEqual(captured.get("publish_time"), "7", "策略 publish_time 未生效")

    def test_w1b_explicit_body_overrides_policy(self):
        """显式传值必须覆盖策略（策略是默认，不是强制）。"""
        from dy_apis.douyin_api import DouyinAPI
        asyncio.run(self.cp.save_policy(self.cp.PolicyBody(
            id="polB", kind="video", num=40, sort_type="1", is_default=True)))
        captured = {}

        def _stub(auth, q, num, sort_type, publish_time, filter_duration, *a, **k):
            captured.update(num=num, sort_type=sort_type)
            return []
        saved = DouyinAPI.search_some_general_work
        DouyinAPI.search_some_general_work = staticmethod(_stub)
        try:
            body = self.c.CrawlSearchRequest(account="acc", query="kw",
                                             num=11, sort_type="0")
            asyncio.run(self.c.crawl_search(body))
        finally:
            DouyinAPI.search_some_general_work = saved
        self.assertEqual(captured.get("num"), 11, "显式 num 未覆盖策略")
        self.assertEqual(captured.get("sort_type"), "0", "显式 sort_type 未覆盖策略")

    def test_w3_crawl_is_tag_managed(self):
        """`crawl` 必须在标签受管分区内 —— 否则标签的采集板块永远无消费者。"""
        from services import config_tag
        self.assertIn("crawl", config_tag.MANAGED_SECTIONS,
                      "crawl 未纳入 MANAGED_SECTIONS ⇒ 采集参数无法按账号隔离")

    def test_w4_page_count_reads_with_account(self):
        """`_page_count(body_count, account)` 必须按账号解析（标签 scope 可生效）。"""
        import inspect
        sig = inspect.signature(self.c._page_count)
        self.assertIn("account", sig.parameters,
                      "_page_count 未接 account ⇒ 标签 scope 无法影响评论每页条数")

    def test_w5_is_default_writable(self):
        """`is_default` 必须在写入白名单内，且「全局默认」唯一。"""
        self.assertIn("is_default", self.cp._FIELDS,
                      "is_default 不在 _FIELDS => 写不进去（第③级永远取不到）")
        asyncio.run(self.cp.save_policy(self.cp.PolicyBody(
            id="d1", kind="video", is_default=True)))
        asyncio.run(self.cp.save_policy(self.cp.PolicyBody(
            id="d2", kind="video", is_default=True)))
        data = self.cp._load_all()
        flagged = [k for k, v in data.items() if isinstance(v, dict) and v.get("is_default")]
        self.assertEqual(flagged, ["d2"], f"全局默认策略不唯一：{flagged}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
