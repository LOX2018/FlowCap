"""拆子卡片的作用域门禁（2026-10-04 独立审查抓到的真缺陷固化）。

## 缺陷（真实发生，本门禁因它而建）
配置中心的卡片原先是「一张卡 = 一个 section」，「恢复默认」= 清整个 section。
本次把 `dm_pool` / `danmaku_pool` 拆成**子卡片**后，子卡与主卡共享同一 section，
而前端 `onReset` 仍只传 section ⇒ 在「私信词库」子卡点一下「恢复默认」，会
**连带清空整个 send 分区**（全部风控/闸门/额度）。

同源缺陷：`onReset` 也**未传 scope** ⇒ 选中标签时误重置**全局**。

## 判据
  R1 `reset_section(sec, fields=[...])` 只清指定字段，其余字段保留
  R2 `reset_section(sec)`（不传 fields）仍清整个 section —— **零回归**
  R3 清空全部字段后，该 section 键应被移除（不留空 dict）
  R4 `reset_section(sec, scope=tag)` 只作用于该标签，不动全局
  R5 字段名不存在时安全（不抛错、不影响其他字段）

跑法（backend 为 cwd，用项目解释器）：
  "C:/Users/LOX/AppData/Local/Programs/Python/Python314/python.exe" -m pytest test_reset_section_scope.py -q
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_TMP = os.path.join(os.environ.get("TEMP", "/tmp"), "dyautodm_reset_scope_test")
os.makedirs(_TMP, exist_ok=True)
os.environ["DY_APP_ROOT"] = _TMP

import services.app_config as ac  # noqa: E402


class TestResetSectionScope(unittest.TestCase):
    def setUp(self):
        for s in ac.SECTIONS:
            ac.reset_section(s)

    def tearDown(self):
        for s in ac.SECTIONS:
            ac.reset_section(s)

    # R1：字段级重置只清本卡字段
    def test_fields_reset_only_clears_those_fields(self):
        """子卡作用域：清 dm_pool 不得动同分区的 min_interval。"""
        ac.save_section("send", {"dm_pool": "词库A", "min_interval": 12.0})
        self.assertEqual(ac.get("send", "dm_pool"), "词库A")
        self.assertEqual(ac.get("send", "min_interval"), 12.0)

        ac.reset_section("send", fields=["dm_pool"])

        # dm_pool 回默认（空串）
        self.assertEqual(ac.get("send", "dm_pool"), "")
        # ★ 关键：同分区其他字段**必须保留**（这正是原缺陷会清掉的东西）
        self.assertEqual(ac.get("send", "min_interval"), 12.0,
                         "字段级重置清掉了同分区其他字段（缺陷复发）")

    # R2：不传 fields ⇒ 清整个 section（零回归）
    def test_no_fields_still_clears_whole_section(self):
        ac.save_section("send", {"dm_pool": "词库A", "min_interval": 12.0})
        ac.reset_section("send")
        self.assertEqual(ac.get("send", "dm_pool"), "")
        self.assertEqual(ac.get("send", "min_interval"), 8.0, "整节重置失效（零回归破坏）")

    # R3：清空全部字段后不留空 dict
    def test_clearing_all_fields_removes_section_key(self):
        ac.save_section("live", {"danmaku_pool": "弹幕A"})
        ac.reset_section("live", fields=["danmaku_pool"])
        data = ac._load(ac.scope_key(None))
        self.assertNotIn("live", data,
                         "字段清空后残留空 dict（应在无字段时移除该 section 键）")

    # R4：scope 作用域正确（标签重置不动全局）
    def test_scope_reset_does_not_touch_global(self):
        ac.save_section("send", {"min_interval": 30.0})            # 全局
        ac.save_section("send", {"min_interval": 45.0}, scope="tg_x")  # 标签

        ac.reset_section("send", scope="tg_x")

        self.assertEqual(ac.get("send", "min_interval", scope="tg_x"), 30.0,
                         "标签重置后应回落到全局值")
        self.assertEqual(ac.get("send", "min_interval"), 30.0,
                         "★ 标签重置动了全局（作用域错配）")

    # R5：不存在的字段名安全
    def test_unknown_field_is_safe(self):
        ac.save_section("send", {"min_interval": 12.0})
        ac.reset_section("send", fields=["no_such_field"])
        self.assertEqual(ac.get("send", "min_interval"), 12.0,
                         "重置未知字段时误伤了其他字段")


if __name__ == "__main__":
    unittest.main(verbosity=2)
