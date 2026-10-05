"""批量采集「评论日期范围过滤」契约测试（★ 2026-10-03）。

## 为什么必须有（设计意图）

用户需求：「只筛选指定日期的评论」。该能力一旦**静默失效**，表现是
「日期填了但结果没变」——用户无从察觉，只会以为是自己日期填错。
⇒ 必须有机械门禁，且门禁要**能变红**（含负控）。

## 六段契约

- design  ：悬浮窗选日期 → 只采该日期区间的评论（时区按本地日切 +08:00）
- contract：① 区间含首尾**当日**；② 时区不是 UTC；③ 非法日期回 400 而非 500/静默
- deviation：曾用 `timezone.utc`，边界偏 8 小时；`strptime` 失败会 500
- chain   ：前端 date input → `crawlCommentsBatch(start_date,end_date)`
            → `CrawlCommentsBatchRequest` → `_day_range_ts` → 过滤 `items`
- root    ：日期↔时间戳换算未复用项目既有 `tz` 日切约定
- verify  ：本文件（单测 + 负控）
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ⚠️ 本文件在 `backend/` 下 ⇒ 目标包 `api.crawl`。
#
# 🔴 门禁必须真跑：本文件早先两版都是**假绿/假红**形态 ——
#   ① 用 `spec_from_file_location` 按**路径**加载 ⇒ 模块名不含点 ⇒ 走**顶层模块**分支，
#      `from .xxx import` 相对导入失效，模块被塞进空壳 ⇒ 符号全缺 ⇒ setUp 报
#      `has no attribute`（假红）；且首次版本路径还多退一级 ⇒ 9 项全 skip（假绿）。
#   ② 正确形态：**按包名** `import api.crawl`，让包的相对导入正常解析。
_HERE = os.path.dirname(os.path.abspath(__file__))
_mod = None
_load_error = ""
try:
    if not os.path.exists(os.path.join(_HERE, "api", "crawl.py")):
        raise FileNotFoundError(os.path.join(_HERE, "api", "crawl.py"))
    from api import crawl as _mod  # noqa: PLC0415 —— 必须在 sys.path 设置之后
except Exception as _e:  # noqa: BLE001 —— 导入失败必须**显式**暴露，不静默通过
    _load_error = f"{type(_e).__name__}: {_e}"


class TestDayRangeTs(unittest.TestCase):
    """`_day_range_ts` 纯函数契约。"""

    def setUp(self):
        if _mod is None:
            self.fail(f"api/crawl.py 导入失败（门禁必须真跑）：{_load_error}")
        self.f = _mod._day_range_ts

    def test_empty_means_no_filter(self):
        """留空 = 不限 ⇒ 全集区间（用 lo<=hi 判定「不启用过滤」）。"""
        lo, hi = self.f("", "")
        self.assertEqual(lo, 0)
        self.assertGreater(hi, 2 ** 31 - 2)

    def test_single_day_inclusive_both_ends(self):
        """单日区间含当日 00:00:00 ~ 23:59:59（+86399）。"""
        lo, hi = self.f("2026-10-01", "2026-10-01")
        self.assertEqual(hi - lo, 86399)

    def test_uses_local_tz_not_utc(self):
        """🔴 时区铁律：+08:00 日切，不是 UTC。

        负控：若有人改回 `timezone.utc`，lo 会比本地日切小 8h ⇒ 本例变红。
        """
        from datetime import datetime, timedelta, timezone
        want = int(datetime(2026, 10, 1, 0, 0, 0,
                            tzinfo=timezone(timedelta(hours=8))).timestamp())
        lo, _ = self.f("2026-10-01", "")
        self.assertEqual(lo, want)

    def test_utc_would_differ_negative_control(self):
        """负控自证：UTC 解析值确实 ≠ 本地时区值（否则上面那条是假门禁）。"""
        from datetime import datetime, timezone
        utc_lo = int(datetime(2026, 10, 1, tzinfo=timezone.utc).timestamp())
        local_lo, _ = self.f("2026-10-01", "")
        self.assertNotEqual(utc_lo, local_lo)

    def test_range_span(self):
        """跨月区间跨度正确。"""
        lo, hi = self.f("2026-09-30", "2026-10-02")
        self.assertEqual(hi - lo, 3 * 86400 - 1)

    def test_bad_date_returns_sentinel_not_raise(self):
        """🔴 非法日期**不抛异常**，返回哨兵 (0,0) ⇒ 调用方回 400 而非 500。"""
        for bad in ("2026-13-01", "not-a-date", "2026/10/01", "20261001"):
            with self.subTest(bad=bad):
                lo, hi = self.f(bad, "")
                self.assertEqual((lo, hi), (0, 0))

    def test_open_ended_bounds(self):
        """只给一端时，另一端取全开。"""
        lo, hi = self.f("2026-10-01", "")
        self.assertGreater(lo, 0)
        self.assertGreater(hi, 2 ** 31 - 2)
        lo2, hi2 = self.f("", "2026-10-01")
        self.assertEqual(lo2, 0)
        self.assertGreater(hi2, 0)

    def test_tz_clamped(self):
        """tz 越界夹取（与项目既有 [-12,14] 约定一致）。"""
        lo, _ = self.f("2026-10-01", "", tz_hours=99)
        self.assertGreater(lo, 0)
        lo2, _ = self.f("2026-10-01", "", tz_hours=-99)
        self.assertGreater(lo2, 0)


class TestRequestModelHasDateFields(unittest.TestCase):
    """模型层契约：字段存在且默认空串（不传即不限）。"""

    def test_fields_exist(self):
        if _mod is None:
            self.fail(f"api/crawl.py 导入失败（门禁必须真跑）：{_load_error}")
        f = getattr(_mod, "CrawlCommentsBatchRequest", None)
        self.assertIsNotNone(f, "CrawlCommentsBatchRequest 应存在")
        fields = getattr(f, "model_fields", {})
        for name in ("start_date", "end_date"):
            self.assertIn(name, fields)
            self.assertEqual(fields[name].default, "")

    def test_unknown_field_is_rejected(self):
        """🔴 禁假成功：多余字段必须**报错**而非静默丢弃。

        机理：Pydantic 默认 `extra='ignore'` ⇒ 旧 sidecar 会把新前端的
        `start_date` 悄悄丢掉并返回 200，用户填了日期却毫无效果、无任何报错。
        ⇒ `extra='forbid'` 让版本错配**响亮地 422**。
        """
        f = getattr(_mod, "CrawlCommentsBatchRequest", None)
        if f is None:
            self.fail(f"api/crawl.py 导入失败（门禁必须真跑）：{_load_error}")
        with self.assertRaises(Exception):
            f(account="a", totally_unknown_field_xyz=1)

    def test_known_fields_still_accepted(self):
        """负控自证：`forbid` 没有误伤合法字段（否则上面那条是假门禁）。"""
        f = getattr(_mod, "CrawlCommentsBatchRequest", None)
        if f is None:
            self.fail(f"api/crawl.py 导入失败（门禁必须真跑）：{_load_error}")
        m = f(account="a", aweme_ids=["1"], start_date="2026-10-01",
              end_date="2026-10-02", min_score=3)
        self.assertEqual(m.start_date, "2026-10-01")
        self.assertEqual(m.end_date, "2026-10-02")


if __name__ == "__main__":
    unittest.main(verbosity=2)
