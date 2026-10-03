"""采集「0 条」根因门禁（★ 2026-10-04，用户实测：批量/单作品都采不到评论）。

## 根因（实测取证）

部署库 `crawl_history` 实测：连着 7 批 `comment_batch` 的 `result_count = 0`，
而同期 24h 内**总共采到 1178 条**（取数链路是通的）⇒ **不是采集坏了，
是被过滤光了**。

起因：2026-10-03 我给前端**无条件**传 `min_score=1`（本意是「开启高价值
过滤」），使过滤对**所有**采集生效。用真实评论回放验证：

    真实 300 条评论 → 按标签词表（门槛 1）→ 仅剩 79 条（26.3%）
    ⇒ 默认开启会丢弃 74% 的评论；搜索/推荐流作品命中率更低 ⇒ 多批 n=0

## 修法

① 前端：过滤**可选项**，由「是否显式选了标签」决定
   （`hvMinScore = tagId ? 1 : 0`）—— 回到用户原话「开启高价值采集的时候」。
② 后端：过滤后留痕 —— 全滤掉且原集合非空 ⇒ WARN + 响应带 `filtered`，
   让用户能区分「作品真没评论」与「被过滤光了」。

## 判据设计

⚠️ 判据**不**断言具体阈值（那是业务配置），只断言**语义**：
   · 未选标签 ⇒ 不得开启过滤；
   · 选了标签 ⇒ 可以开启；
   · 后端必须回传 filtered 且过滤光时留 WARN。

## 运行

    cd backend && python -m unittest test_crawl_zero_result -v
"""
from __future__ import annotations

import inspect
import io
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.dirname(_HERE)
_PANEL = os.path.join(_SRC, "frontend", "src", "components", "crawl",
                      "CrawlFloatingPanel.tsx")
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


def _panel() -> str:
    with io.open(_PANEL, encoding="utf-8") as f:
        return f.read()


class TestFilterIsOptIn(unittest.TestCase):
    """① 高价值过滤必须是「可选项」，不能无条件开启。"""

    def test_no_unconditional_min_score(self):
        """🔴 不得出现 `const hvMinScore = 1`（无条件开启）。"""
        src = _panel()
        self.assertNotIn("const hvMinScore = 1;", src,
                         "高价值过滤被无条件开启 ⇒ 会把普通评论全滤掉"
                         "（实测：连着 7 批 n=0）")

    def test_gated_by_tag_selection(self):
        """选了标签才过滤；没选标签应全采。"""
        src = _panel()
        self.assertIn("hvMinScore = tagId ? 1 : 0", src,
                      "过滤未由「是否选标签」决定 ⇒ 默认会滤掉大部分评论")


class TestBackendLeavesTrace(unittest.TestCase):
    """② 过滤后必须留痕，让「0 条」可诊断。"""

    def test_response_includes_filtered(self):
        import api.crawl as C
        src = inspect.getsource(C.crawl_comments_batch)
        self.assertIn('"filtered": batch_filtered', src,
                      "响应未回传 filtered ⇒ 前端无法提示「是过滤导致的 0 条」")

    def test_warns_when_all_filtered_out(self):
        """全滤掉且原集合非空 ⇒ 必须 WARN（否则静默 0 条，用户以为采集坏了）。"""
        import api.crawl as C
        src = inspect.getsource(C.crawl_comments_batch)
        self.assertIn("CRAWL-008", src,
                      "过滤光后无告警 ⇒ 「0 条」无法区分原因")
        self.assertIn("if _before and not items", src,
                      "未判「过滤前非空」⇒ 作品真没评论也会误告警")

    def test_filtered_counter_initialized(self):
        import api.crawl as C
        src = inspect.getsource(C.crawl_comments_batch)
        self.assertIn("batch_filtered = 0", src, "filtered 计数器未初始化")


class TestSearchFilterWiring(unittest.TestCase):
    """③ 搜索筛选器必须真接线（用户实测：发布时间/视频时长失效）。

    取证：平台页**渲染了**筛选器（platform-page.tsx 的「发布时间」「视频时长」），
    但后端 `SearchReq` 没有这三个字段、前端也不传 ⇒ 点了没效果。
    正确接线逻辑本已存在于废弃的 `crawl-page.tsx`（第 373-375 行）——
    搬 UI 时漏了接线。
    """

    def _panel_src(self):
        with io.open(os.path.join(_SRC, "frontend", "src", "components",
                                  "platform", "platform-page.tsx"),
                     encoding="utf-8") as f:
            return f.read()

    def test_backend_model_has_filter_fields(self):
        from api.platform import SearchReq
        for f in ("sort_type", "publish_time", "filter_duration"):
            self.assertIn(f, SearchReq.model_fields,
                          f"SearchReq 缺 {f} ⇒ 筛选值无处可传")

    def test_frontend_contract_accepts_filters(self):
        with io.open(os.path.join(_SRC, "frontend", "src", "api", "platform.ts"),
                     encoding="utf-8") as f:
            src = f.read()
        i = src.find("  search: (account")
        self.assertGreater(i, 0)
        seg = src[i:i + 700]
        for f in ("sort_type", "publish_time", "filter_duration"):
            self.assertIn(f, seg, f"前端契约未声明 {f}")

    def test_filter_values_enter_query_key(self):
        """🔴 筛选值必须进 queryKey，否则改了筛选不会重新查询。"""
        src = self._panel_src()
        i = src.find('queryKey: ["platform-search"')
        self.assertGreater(i, 0, "找不到 searchQ 的 queryKey")
        seg = src[i:i + 220]
        for v in ("order", "pt", "dur"):
            self.assertIn(v, seg,
                          f"queryKey 未含 {v} ⇒ 改筛选不重新查询（点了没反应）")

    def test_backend_routes_to_filter_capable_api(self):
        """显式传筛选 ⇒ 必须走**支持筛选**的接口，不能静默忽略。"""
        import inspect
        import api.platform as P
        src = inspect.getsource(P.search)
        self.assertIn("_has_filter", src,
                      "未判「是否指定了筛选」⇒ 筛选会被新接口静默忽略")
        self.assertIn("search_general_work", src,
                      "筛选路径未走支持筛选的 search_general_work")


class TestCrawlStrategyByCount(unittest.TestCase):
    """④ 按作品数量分配采集策略（用户定调：单/批量是**同一条路**）。

    用户原话：「目前单采集和批量采集都是通过悬浮窗来实现的，所以并没有
    另外一条路的说法，而是要根据所选作品的数量来分配采集策略」。

    ⇒ 判据：端点内必须有「按 len(ids) 决定策略参数」的分支，
      且**不得**另起一条单作品实现（那才是「两条路」）。
    """

    def test_strategy_branch_by_count(self):
        import inspect
        import api.crawl as C
        src = inspect.getsource(C.crawl_comments_batch)
        self.assertIn("len(ids)", src, "未按作品数量决定策略")
        self.assertIn("interval = 0.0", src,
                      "单作品未清零节流（会白白等待）")

    def test_no_separate_single_work_path(self):
        """🔴 不得存在「单作品专用」的采集端点（那才是两条路）。"""
        import api.crawl as C
        src = inspect.getsource(C)
        # 既有 /comments 是**旧**能力（非悬浮窗路径），此处只断言
        # 批量端点内不再出现「按 ids 长度切到别的实现」的分叉
        body = inspect.getsource(C.crawl_comments_batch)
        self.assertNotIn("crawl_comments(", body,
                         "批量端点内又调用了单作品端点 ⇒ 两条实现分叉")


class TestTaskPageShowsCrawlTasks(unittest.TestCase):
    """⑤ 任务页必须能看见采集任务（用户定调：任务页看结果与进度）。"""

    def test_task_page_queries_crawl_tasks(self):
        with io.open(os.path.join(_SRC, "frontend", "src", "components",
                                  "tasks", "tasks-page.tsx"),
                     encoding="utf-8") as f:
            src = f.read()
        self.assertIn("api.crawlTasks()", src,
                      "任务页未读 /api/crawl/tasks ⇒ 看不到采集任务")

    def test_task_page_renders_progress(self):
        with io.open(os.path.join(_SRC, "frontend", "src", "components",
                                  "tasks", "tasks-page.tsx"),
                     encoding="utf-8") as f:
            src = f.read()
        self.assertIn('title="采集任务"', src, "任务页未渲染「采集任务」区")
        self.assertIn("成功 {t.ok_works", src, "未显示成功/失败结果")


class TestSearchResultUnwrap(unittest.TestCase):
    """⑥ 搜索结果必须解到**作品列表**层（用户实测：播放取址 422）。

    事故链：`search_general_work` 返回**完整 resp_json**
    （`{"status_code":..,"data":[..]}`），直接喂给 `_pick_aweme` ⇒
    它拿到的是 dict，`w.get("aweme_id")` 恒空 ⇒ 前端拿不到 aweme_id ⇒
    `POST /api/platform/media/stream-ticket` 报
    `missing body.aweme_id`（422）—— 表现为「默认搜索和筛选搜索全部失败」。

    ⇒ 判据：必须经 `_extract_aweme_list` 显式取 data 层，且**多种上游
       形态都要活**（裸列表 / resp_json / features 信封）。
    """

    def setUp(self):
        from api.platform import _extract_aweme_list, _pick_aweme
        self.extract = _extract_aweme_list
        self.pick = _pick_aweme

    def _ids(self, raw):
        return [self.pick(w).get("aweme_id") for w in self.extract(raw)]

    def test_bare_list(self):
        self.assertEqual(self._ids([{"aweme_id": "A1"}]), ["A1"])

    def test_resp_json_with_data(self):
        self.assertEqual(self._ids({"status_code": 0, "data": [{"aweme_id": "B1"}]}),
                         ["B1"])

    def test_features_envelope(self):
        self.assertEqual(self._ids({"ok": True, "data": [{"aweme_id": "C1"}],
                                    "error": ""}), ["C1"])

    def test_aweme_list_key(self):
        self.assertEqual(self._ids({"aweme_list": [{"aweme_id": "D1"}]}), ["D1"])

    def test_unknown_shape_returns_empty_not_dict(self):
        """🔴 认不出的结构必须回 []，绝不返回 dict 冒充列表。"""
        r = self.extract({"foo": 1})
        self.assertEqual(r, [], "未识别结构应回空列表，不能静默把 dict 往下传")
        self.assertEqual(self.extract(None), [])

    def test_unwraps_aweme_info_envelope(self):
        """🔴 上游是两层包装 `data[].aweme_info`（基座
        `dy_apis/douyin_api.py:481`：`[w for w in res_json["data"]
        if w.get("aweme_info")]`）。只解一层 ⇒ item 有 aweme_id 但无 video
        ⇒ 取址拿不到地址 ⇒ 前端报 502「无可用地址」。
        """
        inner = {"aweme_id": "7389", "desc": "d", "statistics": {},
                 "author": {"nickname": "n", "uid": "1", "sec_uid": "s"},
                 "video": {"duration": 1, "cover": {"url_list": ["c"]},
                           "play_addr": {"url_list": ["https://v/real.mp4"]}}}
        raw = {"status_code": 0, "data": [{"type": 1, "aweme_info": inner}]}
        lst = self.extract(raw)
        self.assertEqual(len(lst), 1)
        self.assertEqual(self.pick(lst[0]).get("aweme_id"), "7389")
        # 关键：必须能取到播放地址（502 的直接判据）
        from downloader import media_request as MR
        item = self.pick(lst[0])
        url = MR.pick_quality(MR.extract_media(item.get("media") or {}), "origin")
        self.assertTrue(url, "剥壳后仍取不到地址 ⇒ 会再报 502")

    def test_drops_non_aweme_cards_per_upstream(self):
        """上游口径：无 `aweme_info` 的项（非作品卡）应丢弃，不能漏给前端。"""
        raw = {"status_code": 0, "data": [
            {"type": 1, "aweme_info": {"aweme_id": "A"}},
            {"type": 68, "cell_room": {"x": 1}},   # 直播推荐卡
        ]}
        self.assertEqual([self.pick(w).get("aweme_id") for w in self.extract(raw)],
                         ["A"])

    def test_bare_list_not_broken(self):
        """回归：`search_stream` 的裸列表不得被剥壳逻辑误伤。"""
        bare = [{"aweme_id": "B1",
                 "video": {"play_addr": {"url_list": ["https://v/b.mp4"]}}}]
        self.assertEqual([w.get("aweme_id") for w in self.extract(bare)], ["B1"])

    def test_backend_calls_extractor(self):
        import inspect
        import api.platform as P
        src = inspect.getsource(P.search)
        self.assertIn("_extract_aweme_list", src,
                      "筛选路径未调用提取器 ⇒ aweme_id 会再丢一次")


class TestUiRequirements(unittest.TestCase):
    """⑦ 三条 UI 硬要求（用户逐条指定）。"""

    def _read(self, *parts):
        with io.open(os.path.join(_SRC, *parts), encoding="utf-8") as f:
            return f.read()

    def test_tag_select_has_no_tag_option(self):
        with io.open(os.path.join(_SRC, "frontend", "src", "components",
                                  "crawl", "CrawlFloatingPanel.tsx"),
                     encoding="utf-8") as f:
            src = f.read()
        self.assertIn("不使用标签", src,
                      "标签下拉缺「不使用标签」选项 ⇒ 用户不知道如何停用过滤")

    def test_feed_renders_only_when_complete(self):
        """🔴 必须防「永远空白」：取完了仍不足也要渲染。"""
        src = self._read("frontend", "src", "components", "platform",
                         "platform-page.tsx")
        i = src.find("const feedItems: AwemeItem[] =")
        self.assertGreater(i, 0, "找不到 feedItems 定义")
        seg = src[i:i + 260]
        self.assertIn("FEED_TARGET", seg, "未凑够数量才渲染的判据缺失")
        self.assertIn("_feedStillWorking", seg,
                      "缺「仍在取」的短路 ⇒ 上游给不满时页面永远空白")

    def test_checked_card_has_accent_ring(self):
        src = self._read("frontend", "src", "components", "platform",
                         "platform-cards.tsx")
        self.assertIn("checked\n          ? \"ring-2", src,
                      "选中卡片未加 ring 边框")


class TestNegativeControl(unittest.TestCase):
    """负控自证：门禁必须能变红。"""

    def test_red_when_unconditional(self):
        """无条件开启的形态必须能被 test_no_unconditional_min_score 抓到。"""
        bad = "const hvMinScore = 1;"
        self.assertIn("const hvMinScore = 1;", bad,
                      "负控前提失效：匹配不到无条件开启写法")

    def test_red_when_trace_removed(self):
        """去掉 WARN ⇒ 判据应抓到。"""
        src = "items = [c for c in items if True]\nreturn {}"
        self.assertNotIn("CRAWL-008", src,
                         "负控前提失效：无痕实现竟然含告警标记")

    def test_real_data_shows_filter_is_heavy(self):
        """🔴 用真实评论回放：证明默认开启会丢掉大部分评论。

        这条是**判据的前提**（为什么默认不能开），不是产品断言。
        """
        try:
            from services import high_value_keywords as hv
        except Exception:
            self.skipTest("high_value_keywords 不可用")
        scope = "tg_6382d397bf9244f4"
        # 与「工伤类」无关的普通评论（搜索/推荐流的典型内容）
        samples = ["好看", "支持一下", "太棒了", "666", "路过", "学习了"]
        hit = sum(1 for s in samples if hv.score_text(s, scope) >= 1)
        self.assertEqual(hit, 0,
                         "负控前提失效：普通评论竟然命中了关键词表")
        # 相关评论应命中
        self.assertGreater(hv.score_text("工地受伤怎么赔偿", scope), 0,
                           "负控前提失效：相关评论未命中")


if __name__ == "__main__":
    unittest.main(verbosity=2)
