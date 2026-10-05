# -*- coding: utf-8 -*-
"""直播间登记表（房间层，ADR-003）回归守卫。

## 覆盖的验收判据（ADR-003 §5，逐条对应）

| # | 判据 | 本文件对应测试 |
|---|---|---|
| 1 | 新增房间 → `live_rooms` 有记录且字段齐全；**`live_room_configs` 不被污染** | `TestSaveRoom` |
| 2 | 策略删除 → 引用它的房间 `strategy_id` 置空，`unbound >= 1` | `TestDeleteStrategyUnbinds` |
| 3 | 同一策略被 2 个房间引用 → 删除时**两个**都解绑 | `TestDeleteStrategyUnbinds` |
| 4 | `allow_desensitized` 持久化且重启后保持 | `TestDesensitizedPersistence` |
| 5 | 旧「992931212705」房间形记录迁移后**信息不丢** | `TestMigration` |
| 6 | 前端 `tsc -b` 通过 + 真机渲染 | 由构建/实机步骤覆盖（此处只守源码契约）|

## D-07 纪律（判据必须自证「失败态会变红」）

本文件刻意**不 mock 任何被测行为**：每个断言先造出「旧实现会失败」的输入
（如空房间、悬空引用、未迁移的旧键），再验证现实现能收敛。运行方式：

    cd backend && python -m unittest test_live_rooms -v
"""
from __future__ import annotations

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# 复用项目既有的隔离层（把 DY_APP_ROOT 钉到独立临时根，防污染真实库）
import test_config_isolation as iso  # noqa: E402,F401

from api import live_config  # noqa: E402
from api import live_rooms  # noqa: E402
from database import get_kv_json, set_kv_json  # noqa: E402


def _reset() -> None:
    """每个用例前清空两个 kv，保证互不干扰（真源就在 kv，无隐藏状态）。"""
    set_kv_json(live_config._KV_KEY, {})
    set_kv_json(live_rooms._KV_KEY, {})


class TestSaveRoom(unittest.TestCase):
    """判据 1：新增房间字段齐全；策略层不被身份字段污染。"""

    def setUp(self) -> None:
        _reset()

    def test_save_and_list_fields_complete(self):
        import asyncio

        # P2-8（2026-09-23）：写入侧现在校验引用完整性，故先建被引用的策略。
        # 修前本用例直接用不存在的 "lc_1"，恰好演示了「可写悬空引用」这个缺陷本身。
        asyncio.run(live_config.save_strategy(live_config.StrategyBody(
            id="lc_1", name="被引用策略")))
        r = asyncio.run(live_rooms.save_room(live_rooms.RoomBody(
            room_id="992931212705",
            live_url="https://live.douyin.com/992931212705",
            name="自营-工伤咨询",
            strategy_id="lc_1",
            allow_desensitized=True,
        )))
        self.assertTrue(r["ok"])
        room = r["room"]
        for k in ("id", "room_id", "live_url", "name", "strategy_id",
                  "allow_desensitized", "updated_at"):
            self.assertIn(k, room, f"房间记录缺字段 {k}")
        self.assertTrue(room["id"].startswith("lr_"), "房间 id 应为 lr_<epoch_ms> 形态")
        self.assertTrue(room["allow_desensitized"])

        items = asyncio.run(live_rooms.list_rooms())["items"]
        self.assertEqual(len(items), 1)

    def test_strategy_kv_not_polluted_by_identity(self):
        """🔴 核心分离判据：写房间**不得**往策略层塞身份字段。"""
        import asyncio

        before = get_kv_json(live_config._KV_KEY, {})
        asyncio.run(live_rooms.save_room(live_rooms.RoomBody(
            room_id="777666555", name="另一房", strategy_id="", )))
        after = get_kv_json(live_config._KV_KEY, {})
        self.assertEqual(before, after,
                         "写房间污染了 live_room_configs —— 两层必须物理分离")

    def test_empty_identity_rejected(self):
        """失败态自证：无 room_id 且无 live_url 时**必须显式失败**，不得静默建空记录。"""
        import asyncio

        r = asyncio.run(live_rooms.save_room(live_rooms.RoomBody(name="只有备注")))
        self.assertFalse(r["ok"], "空房间应被拒（否则会留下无身份的脏记录）")
        self.assertEqual(get_kv_json(live_rooms._KV_KEY, {}), {})

    def test_update_keeps_existing_when_field_omitted(self):
        """更新不该把未提交的字段清空（防「改备注顺手清掉策略绑定」）。"""
        import asyncio

        # P2-8：先建被引用的策略（写入侧现在拒绝悬空引用）
        asyncio.run(live_config.save_strategy(live_config.StrategyBody(
            id="lc_keep", name="保留策略")))
        first = asyncio.run(live_rooms.save_room(live_rooms.RoomBody(
            room_id="111", strategy_id="lc_keep", allow_desensitized=True)))
        rid = first["room"]["id"]
        second = asyncio.run(live_rooms.save_room(live_rooms.RoomBody(
            id=rid, name="改名而已")))
        self.assertEqual(second["room"]["strategy_id"], "lc_keep")
        self.assertTrue(second["room"]["allow_desensitized"])
        self.assertEqual(second["room"]["room_id"], "111")


class TestDeleteStrategyUnbinds(unittest.TestCase):
    """判据 2 · 3：删除策略必须自动解绑引用房间，禁止悬空引用。"""

    def setUp(self) -> None:
        _reset()

    def _two_rooms_one_strategy(self) -> str:
        import asyncio

        asyncio.run(live_config.save_strategy(live_config.StrategyBody(
            id="lc_shared", name="共用策略")))
        for n, room in (("房A", "1001"), ("房B", "1002")):
            asyncio.run(live_rooms.save_room(live_rooms.RoomBody(
                room_id=room, name=n, strategy_id="lc_shared")))
        return "lc_shared"

    def test_delete_returns_unbound_count(self):
        import asyncio

        sid = self._two_rooms_one_strategy()
        r = asyncio.run(live_config.delete_strategy(sid))
        self.assertTrue(r["ok"])
        self.assertEqual(r["unbound"], 2, "同一策略被 2 房引用 → 两房都必须解绑")
        rooms = asyncio.run(live_rooms.list_rooms())["items"]
        self.assertEqual(len(rooms), 2, "房间本身不得被删掉")
        for room in rooms:
            self.assertEqual(room["strategy_id"], "",
                             f"房间 {room['name']} 仍指向已删策略 = 悬空引用")

    def test_no_dangling_reference_after_delete(self):
        """失败态自证：删除后全库不得再有 strategy_id 指向不存在的策略。"""
        import asyncio

        self._two_rooms_one_strategy()
        asyncio.run(live_config.delete_strategy("lc_shared"))
        alive = set((get_kv_json(live_config._KV_KEY, {}) or {}).keys())
        for room in (get_kv_json(live_rooms._KV_KEY, {}) or {}).values():
            sid = str(room.get("strategy_id") or "")
            if sid:
                self.assertIn(sid, alive,
                              f"悬空引用：房间指向不存在的策略 {sid}")

    def test_unbound_zero_when_no_reference(self):
        """没有房间引用时解绑数必须为 0（不许虚报）。"""
        import asyncio

        asyncio.run(live_config.save_strategy(live_config.StrategyBody(
            id="lc_lonely", name="无人引用")))
        r = asyncio.run(live_config.delete_strategy("lc_lonely"))
        self.assertEqual(r["unbound"], 0)


class TestDesensitizedPersistence(unittest.TestCase):
    """判据 4：allow_desensitized 必须落盘（换一个「进程」读回仍在）。"""

    def setUp(self) -> None:
        _reset()

    def test_persisted_and_read_back(self):
        import asyncio

        saved = asyncio.run(live_rooms.save_room(live_rooms.RoomBody(
            room_id="2001", name="脱敏统计房", allow_desensitized=True)))
        rid = saved["room"]["id"]
        # 直接读 kv（绕过内存状态）＝ 模拟重启后读回
        raw = get_kv_json(live_rooms._KV_KEY, {}) or {}
        self.assertTrue(raw[rid]["allow_desensitized"])
        self.assertTrue(asyncio.run(live_rooms.get_room(rid))["room"]["allow_desensitized"])

    def test_default_is_false(self):
        """默认必须是 false（ADR-003 §3.2）—— 默认不盯无解密权的房间。"""
        import asyncio

        saved = asyncio.run(live_rooms.save_room(live_rooms.RoomBody(
            room_id="2002", name="默认房")))
        self.assertFalse(saved["room"]["allow_desensitized"])


class TestMigration(unittest.TestCase):
    """判据 5：存量房间形旧记录迁移后信息不丢（ADR-003 §3.5）。"""

    def setUp(self) -> None:
        _reset()
        # 造出实测到的真实脏数据形态（键 = 房间号，带 room_id/live_url/name）
        set_kv_json(live_config._KV_KEY, {
            "992931212705": {
                "room_id": "992931212705", "updated_at": 1789806657, "name": "21",
                "live_url": "992931212705", "max_target": 100, "interval": 60.0,
                "delay": "50,120", "force_rescan": False, "dm_pool": [],
                "auto_link_mic": False, "link_mic_mode": "audio",
            },
            "lc_1789899197973": {
                "id": "lc_1789899197973", "updated_at": 1789960787, "name": "常用",
                "max_target": 10, "interval": 60.0, "delay": "50,120",
                "dm_pool": [], "auto_link_mic": False, "link_mic_mode": "audio",
            },
        })

    def test_dry_run_writes_nothing(self):
        """干跑必须零写入（否则「可复现」无从谈起）。"""
        before_cfg = get_kv_json(live_config._KV_KEY, {})
        before_rooms = get_kv_json(live_rooms._KV_KEY, {})
        r = live_rooms.migrate_from_room_configs(dry_run=True)
        self.assertTrue(r["ok"])
        self.assertEqual(r["found"], ["992931212705"])
        self.assertEqual(get_kv_json(live_config._KV_KEY, {}), before_cfg,
                         "干跑改了策略 kv")
        self.assertEqual(get_kv_json(live_rooms._KV_KEY, {}), before_rooms,
                         "干跑改了房间 kv")

    def test_detects_only_room_shaped(self):
        """不得把正常策略（lc_*）误当房间形记录迁移。"""
        r = live_rooms.migrate_from_room_configs(dry_run=True)
        self.assertNotIn("lc_1789899197973", r["found"],
                         "正常策略被误判为房间形记录")

    def test_no_information_loss(self):
        """迁移后 room_id / live_url / name 均可从 live_rooms 读到。"""
        r = live_rooms.migrate_from_room_configs(dry_run=False)
        self.assertEqual(len(r["migrated_rooms"]), 1)
        rooms = (get_kv_json(live_rooms._KV_KEY, {}) or {})
        rec = list(rooms.values())[0]
        self.assertEqual(rec["room_id"], "992931212705", "房间号丢失")
        self.assertEqual(rec["name"], "21", "备注丢失")
        self.assertTrue(rec["live_url"].startswith("http"),
                        "live_url 应归一化为可点链接（旧值只是裸房间号）")

    def test_migrated_room_keeps_params_via_strategy(self):
        """旧记录带的发送参数必须被承接（不得因迁移而丢发送参数）。"""
        r = live_rooms.migrate_from_room_configs(dry_run=False)
        self.assertEqual(len(r["created_strategies"]), 1,
                         "旧记录含发送参数，应新建一条策略承接")
        sid = r["created_strategies"][0]
        strat = (get_kv_json(live_config._KV_KEY, {}) or {}).get(sid)
        self.assertIsNotNone(strat, "承接策略未写入")
        self.assertEqual(strat["max_target"], 100)
        rec = list((get_kv_json(live_rooms._KV_KEY, {}) or {}).values())[0]
        self.assertEqual(rec["strategy_id"], sid, "房间未引用承接策略")

    def test_source_room_shaped_key_removed(self):
        """迁移后身份字段不得继续留在策略层（否则读取路径会再 pop 一次）。"""
        live_rooms.migrate_from_room_configs(dry_run=False)
        cfgs = get_kv_json(live_config._KV_KEY, {}) or {}
        self.assertNotIn("992931212705", cfgs, "房间形旧键未清理")
        for k, v in cfgs.items():
            self.assertFalse(v.get("room_id") or v.get("live_url"),
                             f"策略层仍残留身份字段：{k}")

    def test_idempotent(self):
        """重复迁移必须幂等：第二次**不再产生新房间**（房源总数不变）。

        注意判据是「第二次零新增」，不是「第二次返回同一个列表」——
        迁移是一次性动作，源键已在第一次被清理，第二次本就无事可做。
        """
        first = live_rooms.migrate_from_room_configs(dry_run=False)
        self.assertEqual(len(first["migrated_rooms"]), 1)
        second = live_rooms.migrate_from_room_configs(dry_run=False)
        self.assertEqual(second["migrated_rooms"], [],
                         "第二次迁移又建了房间 = 不幂等（会累积重复房间）")
        self.assertEqual(second["found"], [], "源键已在第一次清理")
        self.assertEqual(len(get_kv_json(live_rooms._KV_KEY, {}) or {}), 1,
                         "房间数被重复迁移放大")


class TestRegistryContract(unittest.TestCase):
    """源码级契约守卫：路由已挂载、身份字段在房间层白名单内。"""

    def test_router_mounted(self):
        src = open(os.path.join(_ROOT, "backend", "main.py"),
                   encoding="utf-8", errors="replace").read()
        self.assertIn('live_rooms_api.router, prefix="/api/live/rooms"', src,
                      "直播间登记表路由未挂载到 /api/live/rooms")

    def test_identity_fields_in_room_whitelist(self):
        self.assertIn("room_id", live_rooms._FIELDS)
        self.assertIn("live_url", live_rooms._FIELDS)
        self.assertNotIn("room_id", live_config._FIELDS,
                         "策略层白名单不得含身份字段（ADR-003 分离铁律）")
        self.assertNotIn("live_url", live_config._FIELDS)

    def test_old_assertion_comment_updated(self):
        """旧断言「没有目标直播间体系」必须已被标注逆转（否则后续会话读旧注释分叉）。"""
        src = open(os.path.join(_ROOT, "backend", "api", "live_config.py"),
                   encoding="utf-8", errors="replace").read()
        self.assertIn("ADR-003", src, "live_config.py 未标注房间层的引入")
        self.assertIn("部分逆转", src, "未显式标注 2026-09-19 定调已被部分逆转")


class TestFrontendRoomManagePage(unittest.TestCase):
    """前端契约（ADR-003 §4）：新页存在、按钮与「管理策略」并排、旧注释已更新。"""

    @classmethod
    def setUpClass(cls):
        cls.page = open(os.path.join(_ROOT, "frontend", "src", "components",
                                     "live", "RoomManagePage.tsx"),
                        encoding="utf-8", errors="replace").read()
        cls.livepage = open(os.path.join(_ROOT, "frontend", "src", "components",
                                         "live", "live-page.tsx"),
                            encoding="utf-8", errors="replace").read()
        cls.roomcfg = open(os.path.join(_ROOT, "frontend", "src", "components",
                                        "live", "RoomConfigPage.tsx"),
                           encoding="utf-8", errors="replace").read()

    def test_page_has_required_fields(self):
        for k in ("直播间链接", "备注", "绑定参数标签", "解析出的房间号"):
            self.assertIn(k, self.page, f"直播间管理页缺少「{k}」控件")

    def test_room_level_strategy_field_removed(self):
        """2026-10-02（用户定调「策略以标签为主」）：房间级策略下拉必须已下线。

        原字段名不得再出现 —— 策略唯一真源 = 参数标签。
        （注意：断言串只能出现在本处，不得写进被测文件的注释，否则 grep 判据失效。）
        """
        self.assertNotIn("绑定直播策略", self.page,
                         "房间级策略下拉已下线（策略真源 = 参数标签），不得复活")

    def test_desensitized_wording_is_honest(self):
        """🔴 脱敏开关文案不得暗示「能拿到昵称」（ADR-003 §3.3 诚实标注）。"""
        self.assertIn("仅统计", self.page)
        self.assertIn("房间归属", self.page,
                      "必须写明解密权取决于房间归属，不是凭证")

    def test_mounted_and_button_side_by_side(self):
        self.assertIn("<RoomManagePage", self.livepage, "直播页未挂载直播间管理弹窗")
        self.assertIn("直播间管理", self.livepage, "缺少「直播间管理」按钮文案")
        self.assertIn("data-od-id=\"live-room-registry\"", self.livepage)
        # 两个入口必须并排（都在「直播间」板块的同一 actions 块内）
        # 2026-10-02：策略入口由 `live-room-configs`（RoomConfigPage）改为
        # `live-tag-select`（选配置标签，策略以标签为主）。
        i_cfg = self.livepage.index("data-od-id=\"live-tag-select\"")
        i_reg = self.livepage.index("data-od-id=\"live-room-registry\"")
        self.assertLess(i_cfg, i_reg, "「直播间管理」应排在标签下拉右侧（并排）")
        # 用**结构性**判据（同一 <Section> 块）替代脆弱的字符距离：
        # 2026-10-02 起该 actions 行含 3 个控件（标签下拉/配置标签/直播间管理），
        # 字符距离随控件增减漂移，不是「并排」的可靠判据。
        i_sec_cfg = self.livepage.rfind("<Section", 0, i_cfg)
        i_sec_reg = self.livepage.rfind("<Section", 0, i_reg)
        self.assertEqual(i_sec_cfg, i_sec_reg,
                         "两个入口不在同一个 Section（并排组）内")

    def test_old_assertion_comment_updated_frontend(self):
        """RoomConfigPage 头部旧断言（「没有目标直播间体系」）必须已被标注逆转。"""
        self.assertIn("ADR-003", self.roomcfg,
                      "RoomConfigPage.tsx 未标注房间层引入，后续会话读旧注释会分叉")


if __name__ == "__main__":
    unittest.main(verbosity=2)
