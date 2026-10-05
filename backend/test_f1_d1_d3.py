# -*- coding: utf-8 -*-
"""门禁：ADR-018 F1-D1（房间级标签绑定）+ F1-D3（采集策略 schema）（2026-09-27）。

## 这两项在防什么

**F1-D1**：`scope_of` 的优先级链若被改错，会让「房间级标签」静默失效 ——
用户以为给直播间绑了标签，实际读的还是账号级参数（本项目定义为「假成功」家族）。
三道判据：① 房间级最高优先 ② 无房间级时逐级回落 ③ 悬空引用回落（不返回空 scope）。

**F1-D3**：新增的采集策略层若出现「写入成功但读不到」（`_FIELDS` 白名单漏字段）、
「非法枚举被静默改写」（用户以为设 A 实际跑 B）、或「模块未挂载」（孤儿模块，
本项目吃过多次亏）—— 都是同类静默失效。

## 判据（G1~G8）

  G1  房间级优先于板块级、板块级优先于整账号级（三级链序正确）
  G2  房间级为空串 ⇒ 回落板块级（不是返回空串）
  G3  悬空引用（标签已被删）⇒ **回落**而非返回该 id（防静默失效）
  G4  不传 room_tag / section ⇒ 与改造前逐字一致（零回归负控）
  G5  采集策略写入后**读得回**（白名单不漏字段）
  G6  非法枚举 **拒绝写入**（不静默改写为默认值）
  G7  策略数值收敛在边界内（num 1..50 / max_rounds 1..100）
  G8  路由**真的挂载了**（孤儿模块检测：查 app.openapi，非查源码文本）
  G9  读取出口（`_normalize_room`）不得吞 `tag_id` + 旧记录补默认值
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

_BACKEND = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _BACKEND)
# 隔离根（**先 mkdir**：app_root() 会忽略不存在的路径，见 test_uid_sink_ext.py:17）
_ROOT = tempfile.mkdtemp(prefix="flowcap_f1d13_")
os.makedirs(_ROOT, exist_ok=True)
os.environ["FLOWCAP_APP_ROOT"] = _ROOT


class TestF1D1ScopePriority(unittest.TestCase):
    """F1-D1：scope_of 的三级优先级链。"""

    def setUp(self):
        import database
        database.reset_connection()
        from services import config_tag
        self.ct = config_tag
        # 清空本测试用到的 kv（隔离库，安全）
        conn = database.get_db()
        for k in ("config_tag_bind", "config_tag_bind_section", "config_tags"):
            conn.execute("DELETE FROM kv_store WHERE key=?", (k,))
        conn.commit()

    def tearDown(self):
        import database
        database.reset_connection()

    def _mk_tag(self, tid: str, name: str) -> None:
        """直写标签库（避开 save_tag 的其它副作用）。"""
        import database
        from database import get_kv_json, set_kv_json
        tags = get_kv_json("config_tags", {}) or {}
        tags[tid] = {"id": tid, "name": name, "sections": ["send", "live", "capture"]}
        set_kv_json("config_tags", tags)

    def test_g1_room_beats_section_beats_account(self):
        """G1：房间级 > 板块级 > 整账号级。"""
        import database
        from database import set_kv_json
        self._mk_tag("t_account", "账号级")
        self._mk_tag("t_section", "板块级")
        self._mk_tag("t_room", "房间级")
        set_kv_json("config_tag_bind", {"acc1": "t_account"})
        set_kv_json("config_tag_bind_section", {"acc1": {"live": "t_section"}})

        self.assertEqual(self.ct.scope_of("acc1"), "t_account", "仅账号级")
        self.assertEqual(self.ct.scope_of("acc1", "live"), "t_section", "板块级应盖账号级")
        self.assertEqual(self.ct.scope_of("acc1", "live", "t_room"), "t_room",
                         "房间级应盖板块级")

    def test_g2_empty_room_tag_falls_back_to_section(self):
        """G2：room_tag 为空串 ⇒ 回落板块级（不是返回空串）。"""
        from database import set_kv_json
        self._mk_tag("t_account", "账号级")
        self._mk_tag("t_section", "板块级")
        set_kv_json("config_tag_bind", {"acc1": "t_account"})
        set_kv_json("config_tag_bind_section", {"acc1": {"live": "t_section"}})

        self.assertEqual(self.ct.scope_of("acc1", "live", ""), "t_section",
                         "空 room_tag 必须回落，不得返回空串")
        self.assertEqual(self.ct.scope_of("acc1", "live", "   "), "t_section",
                         "空白 room_tag 同空串")

    def test_g3_dangling_room_tag_falls_back(self):
        """G3：悬空引用（标签已删）⇒ 回落，**不得**返回该 id（防静默失效）。"""
        from database import set_kv_json
        self._mk_tag("t_section", "板块级")
        set_kv_json("config_tag_bind_section", {"acc1": {"live": "t_section"}})

        got = self.ct.scope_of("acc1", "live", "t_deleted_ghost")
        self.assertEqual(got, "t_section",
                         f"悬空标签应回落，实测返回 {got!r}（静默失效）")

    def test_g4_zero_regression_without_new_args(self):
        """G4 零回归：不传 room_tag / section ⇒ 与改造前逐字一致。

        改造前语义：`scope_of(account)` == `tag_of(account)`。
        """
        from database import set_kv_json
        self._mk_tag("t_account", "账号级")
        set_kv_json("config_tag_bind", {"acc1": "t_account"})
        set_kv_json("config_tag_bind_section", {"acc1": {"live": "t_section"}})

        # 只传 account：必须等于 tag_of（**忽略板块级**，这是改造前的既有语义）
        self.assertEqual(self.ct.scope_of("acc1"), self.ct.tag_of("acc1"))
        self.assertEqual(self.ct.scope_of("acc1"), "t_account",
                         "不传 section 时不得取板块级（会让既有调用方行为漂移）")


class TestF1D3CrawlPolicy(unittest.TestCase):
    """F1-D3：采集策略 schema 的读写与校验。"""

    def setUp(self):
        import database
        database.reset_connection()
        conn = database.get_db()
        conn.execute("DELETE FROM kv_store WHERE key='crawl_policies'")
        conn.commit()
        from api import crawl_policy as cp
        self.cp = cp

    def tearDown(self):
        import database
        database.reset_connection()

    def test_g5_write_then_read_back(self):
        """G5：写入后必须读得回（白名单不漏字段）—— 防「写成功读不到」。"""
        import asyncio
        r = asyncio.run(self.cp.save_policy(self.cp.PolicyBody(
            name="保守-慢速", kind="video", num=8, sort_type="2",
            publish_time="7", max_rounds=5)))
        self.assertTrue(r["ok"], r)
        pid = r["id"]

        got = asyncio.run(self.cp.get_policy(pid))
        it = got["item"]
        # 逐个字段核对（任一处丢失 = 白名单漏字段的静默失效）
        self.assertEqual(it["name"], "保守-慢速")
        self.assertEqual(it["kind"], "video")
        self.assertEqual(it["num"], 8)
        self.assertEqual(it["sort_type"], "2")
        self.assertEqual(it["publish_time"], "7")
        self.assertEqual(it["max_rounds"], 5)
        self.assertIn("updated_at", it, "审计字段 updated_at 必须落库")

    def test_g6_illegal_enum_rejected_not_silently_rewritten(self):
        """G6：非法枚举**拒绝写入**（不得静默改写为默认值）。"""
        import asyncio
        # kind 非法
        r = asyncio.run(self.cp.save_policy(self.cp.PolicyBody(kind="nonsense")))
        self.assertFalse(r["ok"], "非法 kind 竟被接受")
        self.assertIn("error", r)
        # sort_type 非法
        r2 = asyncio.run(self.cp.save_policy(self.cp.PolicyBody(kind="video", sort_type="9")))
        self.assertFalse(r2["ok"], "非法 sort_type 竟被接受")
        # 确认**没有**落库（拒绝必须是真拒绝，不是「写个默认值」）
        lst = asyncio.run(self.cp.list_policies())
        self.assertEqual(lst["total"], 0, "拒绝写入后不应留下任何条目")

    def test_g7_numeric_clamp(self):
        """G7：数值收敛在边界内。"""
        import asyncio
        r = asyncio.run(self.cp.save_policy(self.cp.PolicyBody(
            kind="video", num=999, max_rounds=-5)))
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["item"]["num"], 50, "num 须收敛到上限 50")
        self.assertEqual(r["item"]["max_rounds"], 1, "max_rounds 须收敛到下限 1")

    def test_g9_read_path_does_not_strip_tag_id(self):
        """G9：**读取出口**不得吞掉 `tag_id`（ADR-018 F1-D1 实测踩到的静默失效）。

        ## 为什么单独立一条断言

        G1~G8 都过了，真实实例验证却抓到 `tag_id` 丢失 —— 因为那些断言走的是
        `save_room` / 直调函数，**不经过 `_normalize_room`**。而 `_normalize_room`
        的历史残留清理名单里恰好有 `tag_id`（旧废弃字段时代留下的），
        于是：**写进去了，读出来没有**。单测的「自证实盲区」就在这 ——
        只测写入侧，测不到读取出口。

        ⇒ 本断言必须走**读取出口**（`_normalize_room`），而不是直读 kv。
        """
        from api.live_rooms import _normalize_room
        rec = {"id": "lr_x", "room_id": "123", "name": "房", "tag_id": "t_1",
               "strategy_id": "lc_1", "force_rescan": True}
        out = _normalize_room(rec, "lr_x")
        self.assertEqual(out.get("tag_id"), "t_1",
                         "读取出口把 tag_id 抹掉了（写入成功、读取丢失 = 静默失效）")
        # 该清的残留仍需清（不是把整份名单删掉，而是把 tag_id 移出名单）
        self.assertNotIn("force_rescan", out, "既有残留清理不得失效（防过度修复）")

    def test_g9b_legacy_record_without_tag_id_gets_default(self):
        """G9b：旧记录（无 tag_id）读取时补空串 —— 前端类型是必填，缺键会 undefined。"""
        from api.live_rooms import _normalize_room
        out = _normalize_room({"room_id": "123"}, "lr_old")
        self.assertIn("tag_id", out, "旧记录未补 tag_id 默认值")
        self.assertEqual(out["tag_id"], "")

    def test_g8_router_is_mounted(self):
        """G8：路由**真的挂载了**（孤儿模块检测 —— 查 OpenAPI schema）。

        ## ⚠️ 为什么不能查 `app.routes` 的 `.path`

        本项目 `main.py` 用**自定义** `_IncludedRouter` 承载 `include_router`，
        `app.routes` 里这些对象的 `.path` 属性取不到（实测为 `?`）——
        于是直接查 `app.routes` 会**误报「未挂载」**（本项目实测踩到）。
        `app.openapi()["paths"]` 才是本项目可信的路由真源（实测 221 条）。
        """
        import main
        paths = set(main.app.openapi().get("paths", {}).keys())
        self.assertIn("/api/crawl/policies", paths,
                      "未挂载 /api/crawl/policies —— 模块成为孤儿（能力在位但不可达）")
        # 三个端点逐条核实（只查前缀会漏掉子路径注册失败）
        for sub in ("/api/crawl/policies/{pid}", "/api/crawl/policies/{pid}/resolve"):
            self.assertIn(sub, paths, f"端点 {sub} 未注册")


if __name__ == "__main__":
    unittest.main(verbosity=2)
