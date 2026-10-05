# -*- coding: utf-8 -*-
"""守卫：消息时间格式 SSOT（2026-09-30 缺陷防复发）。

## 缺陷背景（真实事故）
聊天记录出现「无意义的时间分割线」，且日期和时间**不固定**：
- 前端：`mt: m.time || nowHM()` —— `nowHM()` 返回 `"HH:MM"`（**无日期**），
  被 `slice(0,10)` 当成日期 ⇒ 渲染出 `01:01` 这类分割线；每次渲染现取
  当前时刻 ⇒ 表现为随机漂移。
- 后端导出：`chat_render._fmt_ts` 用 `float(ts or 0)` ⇒ ts 缺失被映射成
  **1970-01-01**，导出图/HTML 里出现「1970 年」假分割线。

## 守卫判据（断言**机制**，不是字面量）
1. ts 缺失/为 0/非法 ⇒ **空串**（零信息），绝不能是 1970-01-01 这类具体值；
2. 有效 ts ⇒ 严格 `YYYY-MM-DD HH:MM:SS`；
3. 不合契约的串 ⇒ `mt_date` / `mt_time` 返回空（不插分割线、不显假时间）；
4. 前端不得再用 `nowHM()` 兜底 mt（形如 `m.time || nowHM()`）。
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
FRONTEND = BACKEND.parent / "frontend" / "src"
sys.path.insert(0, str(BACKEND))

MT_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")


class TestMessageTimeSSOT(unittest.TestCase):
    def setUp(self):
        from services.message_time import fmt_mt, mt_date, mt_time, is_valid_mt
        self.fmt_mt = fmt_mt
        self.mt_date = mt_date
        self.mt_time = mt_time
        self.is_valid_mt = is_valid_mt

    # ---- 1. 缺失/非法 ⇒ 空串（零信息，绝不谎报） ----
    def test_missing_ts_returns_empty_not_1970(self):
        for bad in (0, 0.0, None, "", "abc", [], {}, float("nan")):
            out = self.fmt_mt(bad)
            self.assertEqual(out, "", f"fmt_mt({bad!r}) 应为空串，实得 {out!r}")
            self.assertNotIn("1970", out)

    def test_valid_ts_strict_format(self):
        out = self.fmt_mt(1694832000.0)  # 2023-09-16 10:40:00 (CST)
        self.assertRegex(out, MT_RE)
        self.assertEqual(len(out), 19)

    # ---- 2. 契约校验 ----
    def test_is_valid_mt(self):
        self.assertTrue(self.is_valid_mt("2026-09-16 10:40:00"))
        for bad in ("", None, "01:01", "2026-09-16", "2026-09-16 10:40",
                    "2026-09-16 10:40:00:00", "2026/09/16 10:40:00"):
            self.assertFalse(self.is_valid_mt(bad), f"{bad!r} 不应判为合法")

    # ---- 3. 不合契约 ⇒ 空（不插线、不显假时间） ----
    def test_bad_shape_yields_empty_parts(self):
        for bad in ("", None, "01:01", "10:40", "2026-09-16"):
            self.assertEqual(self.mt_date(bad), "", f"mt_date({bad!r}) 应为空")
            self.assertEqual(self.mt_time(bad), "", f"mt_time({bad!r}) 应为空")

    def test_good_shape_yields_parts(self):
        self.assertEqual(self.mt_date("2026-09-16 10:40:00"), "2026-09-16")
        self.assertEqual(self.mt_time("2026-09-16 10:40:00"), "10:40")

    # ---- 4. 前端不得再用 nowHM() 兜底 mt（守卫机制，非字面量计数） ----
    def test_frontend_has_no_nowhm_fallback_for_mt(self):
        p = FRONTEND / "components" / "messages" / "messages-page.tsx"
        self.assertTrue(p.exists(), f"找不到前端文件 {p}")
        src = p.read_text(encoding="utf-8")
        # 去掉注释（行注释 + 块注释 + JSX 注释 {/* ... */}）后再判定代码层
        import re as _re
        stripped = _re.sub(r"/\*.*?\*/", "", src, flags=_re.S)
        stripped = _re.sub(r"//[^\n]*", "", stripped)
        stripped = _re.sub(r"\{/\*.*?\*/\}", "", stripped, flags=_re.S)
        self.assertNotIn("nowHM", stripped,
                         "前端代码层不得再出现 nowHM()（会产生无日期的假分割线）")

    def test_frontend_uses_ssot_helpers(self):
        p = FRONTEND / "components" / "messages" / "messages-page.tsx"
        src = p.read_text(encoding="utf-8")
        self.assertIn("mtDate(", src)
        self.assertIn("mtTime(", src)
        # 日期分割线不得再裸切片
        self.assertNotIn("(m.mt || \"\").slice(0, 10)", src)

    # ---- 5. 后端三条链路共用 SSOT（不再各自内联 strftime） ----
    def test_backend_delegates_to_ssot(self):
        cr = (BACKEND / "services" / "chat_render.py").read_text(encoding="utf-8")
        png = (BACKEND / "services" / "chat_render_png.py").read_text(encoding="utf-8")
        # 导出链路不得再出现无秒的旧格式
        self.assertNotIn('"%Y-%m-%d %H:%M"', cr,
                         "chat_render 不得再用无秒格式（会产生 1970-01-01 外的口径漂移）")
        # `float(ts or 0)` 只许出现在注释/docstring 里记录历史，代码层不得再有。
        # （`float(r["ts"] or 0)` 是取 ts 原始值，不是格式化，不受此限。）
        cr_code = re.sub(r'"""[\s\S]*?"""', "", cr)
        cr_code = re.sub(r"#[^\n]*", "", cr_code)
        self.assertNotIn("float(ts or 0)", cr_code,
                         "chat_render 代码层不得再用 float(ts or 0)")
        for name, src in (("chat_render.py", cr), ("chat_render_png.py", png)):
            self.assertIn("from services.message_time import", src,
                          f"{name} 必须委托时间 SSOT")
            # 只查代码层（剥掉 docstring 与注释），避免注释里记录历史造成假红
            code = re.sub(r'"""[\s\S]*?"""', "", src)
            code = re.sub(r"#[^\n]*", "", code)
            self.assertNotIn('["time"] or "")[:10]', code,
                             f"{name} 代码层不得再裸切片取日期")
            self.assertNotIn('["time"] or "")[11:16]', code,
                             f"{name} 代码层不得再裸切片取时分")

    # ---- 6. 写入侧：ts 缺失时从 extra.created_at_us 回补（不落 0） ----
    def test_resolve_message_ts_falls_back_to_created_at_us(self):
        from services.message_schema import resolve_message_ts
        # ts 有效 ⇒ 原样
        self.assertEqual(resolve_message_ts(1694832000.0, {}), 1694832000.0)
        # ts=0 ⇒ 用 created_at_us（微秒→秒）
        self.assertEqual(
            resolve_message_ts(0, {"created_at_us": 1694832000000000}), 1694832000.0)
        # None / 空串 / NaN 同样回补
        for bad in (None, "", float("nan")):
            self.assertEqual(
                resolve_message_ts(bad, {"created_at_us": 1694832000000000}),
                1694832000.0, f"ts={bad!r} 应回补")
        # extra 是 JSON 字符串（DB 读路径形态）也要支持
        self.assertEqual(
            resolve_message_ts(0, '{"created_at_us": 1694832000000000}'),
            1694832000.0)

    def test_resolve_message_ts_returns_zero_not_now(self):
        """两者都没有 ⇒ 返回 0（零信息），**绝不能**兜底成当前时间。"""
        from services.message_schema import resolve_message_ts
        import time as _t
        now = _t.time()
        for args in ((0, {}), (None, None), (0, {"created_at_us": 0}),
                     (0, {"created_at_us": "abc"})):
            got = resolve_message_ts(*args)
            self.assertEqual(got, 0.0, f"{args!r} 应返回 0，实得 {got!r}")
            # 反向断言：返回值**不得**落在当前时刻附近（那意味着兜底成 now）
            self.assertGreater(abs(got - now), 60,
                               "不得把缺失时间兜底成当前时刻（会掩盖脏数据）")

    def test_message_record_tuple_applies_fallback(self):
        """唯一出口 `MessageRecord.tuple` 必须应用到 ts（4 个写入点因此受益）。"""
        from services.message_schema import MessageRecord
        rec = MessageRecord.build(text="你好", msg_type="7",
                                  extra={"created_at_us": 1694832000000000})
        tup = rec.tuple("acct", "c1", ts=0, role="them")
        # ★ 2026-10-03 P5：8 列 → **9 列**（新增 msg_code）。
        #   列序：account, conv_id, role, text, msg_type, msg_code,
        #         extra, ts, msg_id
        self.assertEqual(len(tup), 9)
        self.assertEqual(tup[7], 1694832000.0,
                         "tuple 出口未回补 ts（脏数据会复发）")

    def test_no_writer_emits_bare_zero_ts(self):
        """4 个写入点不得**绕过出口**把 ts=0 直接落库。

        判据：`MessageRecord`-based 写入必须走 `.tuple(`；
        代码层不得出现把字面 0 当 ts 交给 INSERT 的写法。
        """
        import re as _re
        for f in ("daemon/recv_daemon.py", "auto_dm/conversation_capture.py",
                  "daemon/wp_recv.py"):
            src = (BACKEND / f).read_text(encoding="utf-8")
            code = _re.sub(r'"""[\s\S]*?"""', "", src)
            code = _re.sub(r"#[^\n]*", "", code)
            # 允许 `ts=0.0` 作为**默认参数**（tuple 签名里），
            # 不允许在 INSERT 元组构造里传 ts 为 0 而不经 resolve。
            self.assertNotIn("ts=0)", code.replace("ts: float = 0.0)", ""),
                             f"{f} 疑似把 ts 写死为 0")


if __name__ == "__main__":
    unittest.main(verbosity=2)
