# -*- coding: utf-8 -*-
"""T3 / ADR-007 / C-06 验证：dm_uid_sink 扩字段 + 窗口 + 高价值 + 按人聚合。

独立沙箱（DY_APP_ROOT 指向临时目录），**不碰生产库**。
C-06 §6 要求的机械判据 + 零回归负控。

运行： python -m unittest test_uid_sink_ext -v
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

# 模块级隔离说明（不再写成 unittest 用例 —— 实测教训 2026-09-24）：
#   1) 必须先 mkdir：`vbrowser.app_root()` 对**不存在的** DY_APP_ROOT 会打
#      [BCC-039] 并**忽略**它 → 回落相对路径 `data/` = 污染仓库
#      `DYAutoDM_v2/data/`（gitignored，不进 git status，极隐蔽）。已实测踩到。
#   2) **不要**在测试里断言「DB 必须落在本模块的 root」：`database.get_db()`
#      每次调用都做会员一致性校验（`member_ctx.db_path()`），而
#      `test_config_isolation` 会在导入期先设 DY_APP_ROOT；两模块同进程跑时
#      路径由**先导入者**固化 ⇒ 断言写死自己的 root 会**顺序相关假失败**
#      （M-12 同类）。隔离的正确判据是「不落到仓库相对路径」，由
#      `test_config_isolation` 守护；本模块只负责 T3 逻辑。
#   验证方式（人工，非门禁）：`cd backend && python -c "...database._db_path()..."` 后
#   确认 `git status` 无 `DYAutoDM_v2/data/`。 
# 🔴 每次运行独立根（2026-09-24 实测修复）——
#   原实现用**固定**临时目录，导致沙箱 DB **跨运行持久**（实测该库 mtime 跨小时
#   残留，且 `acct__delays_send/w1` 行留着上次的 `window_end_ts`）。而 `mark_seen`
#   是 `INSERT OR IGNORE`（冲突不覆盖）+ **仅高价值(hv=1)才刷新窗口**的 CASE，
#   于是 `test_window_delays_send` 重跑时沿用旧窗口值：
#     · 旧值未过期 → 断言本该通过，却因「窗口未写入」中间断言语义漂移而假失败；
#     · 旧值已过期 → `should_send` 放行 → `assertFalse(ok)` 直接失败（本次复现）。
#   ⇒ 单跑必失败、全量偶发（取决于上一次跑留下的时间戳），是**测试自身缺陷**。
#   每次 mkdtemp 同时消除「同进程内与其它模块抢 DY_APP_ROOT」的顺序相关假失败。
_ROOT = tempfile.mkdtemp(prefix="uid_sink_ext_test_")
os.makedirs(_ROOT, exist_ok=True)          # ← 必须先建（见上方模块级说明 1）
os.environ["DY_APP_ROOT"] = _ROOT
os.environ["DY_MEMBER"] = ""
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import database  # noqa: E402
from services import dm_dispatch as dd  # noqa: E402


def _seq_cfg(**over):
    """构造一个 cfg() 替身：只覆盖本测试关心的键，其余走原始实现。"""
    base = {
        "UID_SINK_COOLDOWN": 604800.0,
        "UID_SINK_STRICT": True,
        "UID_SINK_WINDOW": 0.0,
        "HIGH_VALUE_WINDOW": 60.0,
        "HIGH_VALUE_THRESHOLD": 0,
        "HIGH_VALUE_LLM": False,
        "AGGREGATE_MAX_CHARS": 2000,
    }
    base.update(over)

    def _cfg(name, account=""):
        if name in base:
            return base[name]
        return _orig_cfg(name, account)
    return _cfg


# N1_M28_PIN_ROOT：模块级**强引用**原始 cfg（所有 import 必先于任何用例执行 ⇒ 此刻必为原始实现）。
# 🔴 M-28 毒源修复（2026-09-30）：原实现把 `dd.cfg` 存成**类属性**（`cls._orig = dd.cfg`）
#   再在 tearDown 里 `dd.cfg = self._orig` —— 经**描述符协议**取回时函数已被绑成
#   `bound method cfg of <TestCase>`，于是所谓「还原」实际把 `dd.cfg` **永久换掉**：
#   之后任何 `dd.cfg("X")` 的 name 位置收到的是 TestCase 实例 ⇒ `KeyError: <TestCase ...>`。
#   改法：存进**非描述符容器**（模块级变量 `_orig_cfg` 本身），还原时直接用它。
_orig_cfg = dd.cfg


class T3UidSinkExtTest(unittest.TestCase):
    def setUp(self):
        # N1_M28_PIN_ROOT：执行期重钉本模块根 —— 防被别的模块改写 DY_APP_ROOT（M-28 ② 号根因）。
        os.environ["DY_APP_ROOT"] = _ROOT
        # 每个用例用独立账号，避免相互干扰
        self.acct = "acct_" + self._testMethodName[-12:]
        database.reset_connection()
        conn = database.get_db()
        # 显式清零本账号残留（2026-09-24）——
        #   组合运行时 `member_ctx` 的**盘上会话回退**会把 get_db() 重定向到
        #   带上次跑残留的**固定**库文件（`acct__delays_send/w1` 留着旧
        #   `window_end_ts`）；而 `mark_seen` 是 `INSERT OR IGNORE` + 仅高价值
        #   才刷新窗口 ⇒ 残留旧值会让窗口断言依赖历史运行。
        #   这里与「DB 到底是哪个文件」解耦：无论解析到哪，起点都确定干净。
        conn.execute("DELETE FROM dm_uid_sink WHERE account=?", (self.acct,))
        conn.commit()
        dd.cfg = _seq_cfg()          # 默认：窗口/阈值关闭（零回归态）
        self.sink = dd.UidSink()

    def tearDown(self):
        # N1_M28_PIN_ROOT：归还**模块级**原始函数（不得经类属性/描述符往返 —— 见上方毒源说明）。
        dd.cfg = _orig_cfg

    # ---------- ① 零回归（最关键）----------
    def test_zero_regression_defaults_off(self):
        """窗口/阈值默认关闭时：should_send 放行且不做任何窗口延迟。"""
        ok, reason = self.sink.should_send(self.acct, "u1")
        self.assertTrue(ok, "默认关闭时必须放行")
        self.assertEqual(reason, "", "默认关闭时不应有任何拒绝理由")

    def test_zero_regression_cooldown_still_works(self):
        """冷却逻辑未被破坏：mark_sent 后同 UID 在冷静期内被拦。"""
        self.sink.mark_sent(self.acct, "u2")
        sink2 = dd.UidSink()
        ok, reason = sink2.should_send(self.acct, "u2")
        self.assertFalse(ok)
        self.assertIn("冷却期内", reason)

    # ---------- ② 表结构（C-06 §3 字段规范）----------
    def test_schema_has_new_columns(self):
        conn = database.get_db()
        cols = [r[1] for r in conn.execute("PRAGMA table_info(dm_uid_sink)")]
        for c in ("keyword_score", "is_high_value", "high_value_reason",
                  "window_end_ts", "aggregate_text"):
            self.assertIn(c, cols, f"缺列 {c}")
        idx = [r[1] for r in conn.execute("PRAGMA index_list(dm_uid_sink)")]
        self.assertIn("idx_uid_sink_window", idx)

    # ---------- ③ 关键词打分 ----------
    def test_keyword_score(self):
        from services import high_value_keywords as hv
        hv.invalidate()
        s = hv.score_text("我在工地上班摔了，腰椎骨折能定几级？")
        self.assertGreater(s, 0, "工伤域文本应得正分")
        self.assertEqual(hv.score_text(""), 0)
        self.assertEqual(hv.score_text("今天天气不错"), 0, "无关文本应为 0")

    # ---------- ④ 窗口延迟（D2-C）----------
    def test_window_delays_send(self):
        dd.cfg = _seq_cfg(UID_SINK_WINDOW=300.0)
        sink = dd.UidSink()
        sink.mark_seen(self.acct, "w1", "张三", "live", "工伤能赔多少")
        # 中间断言：窗口必须真被写上（失败时能定位是「没写窗口」还是「写了没拒」）
        conn = database.get_db()
        r = conn.execute("SELECT window_end_ts FROM dm_uid_sink WHERE account=? "
                         "AND peer_uid=?", (self.acct, "w1")).fetchone()
        self.assertIsNotNone(r, "mark_seen 未落行")
        self.assertIsNotNone(r["window_end_ts"],
                             f"窗口未写入（cfg(UID_SINK_WINDOW)="
                             f"{dd.cfg('UID_SINK_WINDOW')}）")
        ok, reason = sink.should_send(self.acct, "w1")
        self.assertFalse(ok, f"窗口未到期必须拒绝（实际 reason={reason!r}）")
        self.assertIn("仍在聚合窗口", reason)
        self.assertGreater(sink.window_remaining(self.acct, "w1"), 0)

    def test_window_zero_disables(self):
        """窗口=0 → 不设 window_end_ts，不延迟。"""
        sink = dd.UidSink()
        sink.mark_seen(self.acct, "w0", "张三", "live", "工伤")
        conn = database.get_db()
        r = conn.execute("SELECT window_end_ts FROM dm_uid_sink WHERE account=? "
                         "AND peer_uid=?", (self.acct, "w0")).fetchone()
        self.assertIsNone(r["window_end_ts"], "窗口关闭时 window_end_ts 必须为 NULL")
        ok, _ = sink.should_send(self.acct, "w0")
        self.assertTrue(ok)

    # ---------- ⑤ 高价值过滤（D1-C 纯关键词路径）----------
    def test_high_value_filter_rejects_low(self):
        dd.cfg = _seq_cfg(HIGH_VALUE_THRESHOLD=50, HIGH_VALUE_WINDOW=60.0)
        sink = dd.UidSink()
        sink.mark_seen(self.acct, "h1", "张三", "live", "今天天气真好啊")
        ok, reason = sink.should_send(self.acct, "h1")
        self.assertFalse(ok, "低分目标在阈值开启 + 严格模式下必须拒绝")
        self.assertIn("非高价值", reason)

    def test_high_value_filter_passes_high(self):
        dd.cfg = _seq_cfg(HIGH_VALUE_THRESHOLD=10, HIGH_VALUE_WINDOW=60.0)
        sink = dd.UidSink()
        sink.mark_seen(self.acct, "h2", "张三", "live", "工伤骨折能定几级赔偿多少")
        ok, _ = sink.should_send(self.acct, "h2")
        self.assertTrue(ok, "高分目标应放行")
        conn = database.get_db()
        r = conn.execute("SELECT is_high_value, keyword_score FROM dm_uid_sink "
                         "WHERE account=? AND peer_uid=?",
                         (self.acct, "h2")).fetchone()
        self.assertEqual(r["is_high_value"], 1)
        self.assertGreater(r["keyword_score"], 0)

    # ---------- ⑥ 按人聚合（Phase 5）----------
    def test_aggregate_accumulates(self):
        sink = dd.UidSink()
        sink.mark_seen(self.acct, "a1", "张三", "live", "第一条")
        sink.mark_seen(self.acct, "a1", "张三", "live", "第二条")
        agg = sink.get_aggregate(self.acct, "a1")
        self.assertIn("第一条", agg)
        self.assertIn("第二条", agg)

    def test_aggregate_truncated(self):
        dd.cfg = _seq_cfg(AGGREGATE_MAX_CHARS=20)
        sink = dd.UidSink()
        sink.mark_seen(self.acct, "a2", "张三", "live", "x" * 50)
        agg = sink.get_aggregate(self.acct, "a2")
        self.assertLessEqual(len(agg), 20, "必须遵守 aggregate_max_chars 截断")

    def test_mark_sent_keeps_aggregate(self):
        """C-06 Q2：mark_sent 不得清空 aggregate_text / is_high_value。"""
        sink = dd.UidSink()
        sink.mark_seen(self.acct, "a3", "张三", "live", "工伤骨折")
        sink.mark_sent(self.acct, "a3")
        conn = database.get_db()
        r = conn.execute("SELECT aggregate_text, sent_ts FROM dm_uid_sink "
                         "WHERE account=? AND peer_uid=?",
                         (self.acct, "a3")).fetchone()
        self.assertIn("工伤", r["aggregate_text"])
        self.assertIsNotNone(r["sent_ts"])

    # ---------- ⑦ 幂等（C-06 I6）----------
    def test_idempotent_single_row(self):
        sink = dd.UidSink()
        for i in range(5):
            sink.mark_seen(self.acct, "i1", "张三", "live", f"弹幕{i}")
        conn = database.get_db()
        n = conn.execute("SELECT COUNT(*) c FROM dm_uid_sink WHERE account=? "
                         "AND peer_uid=?", (self.acct, "i1")).fetchone()["c"]
        self.assertEqual(n, 1, "多次 mark_seen 只能有一行")

    # ---------- ⑧ mark_seen 永不写 sent_ts（I2）----------
    def test_mark_seen_never_sets_sent_ts(self):
        sink = dd.UidSink()
        sink.mark_seen(self.acct, "s1", "张三", "live", "工伤")
        conn = database.get_db()
        r = conn.execute("SELECT sent_ts FROM dm_uid_sink WHERE account=? "
                         "AND peer_uid=?", (self.acct, "s1")).fetchone()
        self.assertIsNone(r["sent_ts"], "mark_seen 绝不能写 sent_ts")


if __name__ == "__main__":
    unittest.main(verbosity=2)
