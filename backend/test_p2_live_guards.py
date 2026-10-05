# -*- coding: utf-8 -*-
"""P2 直播配置 / 判据失真回归（2026-09-23，逐项对应审计实测复现）。

| 编号 | 缺陷 | 本文件对应类 |
|---|---|---|
| P2-4 | `is_placeholder_name('12345')=True` 而 `is_uid_placeholder('12345')=False` | `TestVerdictSingleSource` |
| P2-5 | `kernel/truth.py`「单一事实来源」零消费者 + kernel/ 无 `__init__.py` | `TestKernelTruthWired` |
| P2-7 | 显式清空（解绑）失败：置空后仍是旧 strategy_id | `TestExplicitClear` |
| P2-8 | 写入侧不校验 strategy_id → 可写悬空引用 | `TestReferentialIntegrity` |
| P2-9 | 三个 `timeout_*` 声明 apply:"hot" 却缺 min/max | `TestTimeoutSchemaBounds` |
| P2-10 | 删除策略先删后解绑；解绑失败仍 ok=True → 悬空引用 | `TestDeleteStrategyOrdering` |
| P4 | `live_rooms._FIELDS` 白名单零消费（死代码） | `TestWhitelistIsRealGate` |

## 负控（D-07）

每个「正向断言」旁都配一条**旧形态必失败**的断言：把旧表达式内联重算，
证明它确实会出错；若哪天有人改回旧写法，正向断言变红、负控保持绿。

## 运行

    cd backend
    FLOWCAP_APP_ROOT="$LOCALAPPDATA/Temp/fixA" python -m unittest test_p2_live_guards -v
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

_ROOT = os.path.join(os.environ.get("TEMP", "."), "fixA")
os.makedirs(_ROOT, exist_ok=True)
os.environ["FLOWCAP_APP_ROOT"] = _ROOT

from api import live_config, live_rooms  # noqa: E402
from database import get_kv_json, set_kv_json  # noqa: E402
from services import verdicts  # noqa: E402
from services import app_config_schema as schema  # noqa: E402


def _reset() -> None:
    set_kv_json(live_rooms._KV_KEY, {})
    set_kv_json(live_config._KV_KEY, {})
    live_rooms._last_id_ms = 0
    live_config._last_id_ms = 0


class TestVerdictSingleSource(unittest.TestCase):
    """P2-4：同一概念只能有一条判据，两条函数对任意输入必须同结论。"""

    CASES = ["12345", "123", "3887506227210423", "小张", "", "   ", None,
             12345, 3887506227210423]

    def test_two_functions_never_disagree(self):
        for v in self.CASES:
            a = verdicts.is_placeholder_name(v)
            b = verdicts.is_uid_placeholder(v)
            self.assertEqual(a, b,
                             f"同一概念两条判据对 {v!r} 结论不同："
                             f"is_placeholder_name={a} vs is_uid_placeholder={b}")

    def test_threshold_comes_from_kernel_truth(self):
        """阈值必须是 SSOT 常量，不得在 verdicts 里另写一个字面量。"""
        from kernel.truth import UID_MIN_DIGITS
        self.assertEqual(verdicts.UID_MIN_DIGITS, UID_MIN_DIGITS)

    def test_short_numeric_nickname_is_real(self):
        """'12345'（5 位）应判**真实**：用户可以把昵称设成纯数字短串。"""
        self.assertFalse(verdicts.is_placeholder("12345"))
        self.assertFalse(verdicts.is_uid_placeholder("12345"))

    def test_real_uid_is_placeholder(self):
        self.assertTrue(verdicts.is_placeholder("3887506227210423"))
        self.assertTrue(verdicts.is_uid_placeholder("3887506227210423"))

    def test_peer_id_pollution(self):
        """对端 uid 被填进昵称字段 = 占位（必须是 True）。"""
        self.assertTrue(verdicts.is_placeholder("111", peer_id="111"))
        self.assertTrue(verdicts.is_placeholder_name("111", peer_id="111"))
        self.assertTrue(verdicts.is_uid_placeholder("111", peer_id="111"))

    # ---- 负控：旧的双判据形态必分叉 ----
    def test_negative_control_old_two_rules_disagreed(self):
        s = "12345"
        old_name_rule = bool(s) and s.isdigit()                     # 旧 is_placeholder_name
        old_uid_rule = bool(s) and s.isdigit() and len(s) >= 6      # 旧 is_uid_placeholder
        self.assertTrue(old_name_rule)
        self.assertFalse(old_uid_rule)
        self.assertNotEqual(old_name_rule, old_uid_rule,
                            "负控失效：旧两套规则居然一致（P2-4 根因需重审）")


class TestKernelTruthWired(unittest.TestCase):
    """P2-5：kernel 必须是真包，truth 常量必须真有消费者。"""

    def test_kernel_is_real_package(self):
        import kernel
        self.assertIsNotNone(getattr(kernel, "__file__", None),
                             "kernel/ 仍是隐式 namespace package（缺 __init__.py）")

    def test_truth_constants_intact(self):
        from kernel import truth
        self.assertEqual(truth.NICKNAME_SOURCE, "indexeddb:<uid>_user")
        self.assertEqual(truth.NICKNAME_SOURCES_ORDER,
                         ("indexeddb", "dom", "active_batch"))
        self.assertIsInstance(truth.UID_MIN_DIGITS, int)

    def test_truth_has_a_real_consumer(self):
        """消费者判据（机械）：verdicts 必须真的从 kernel.truth 取阈值。"""
        src = open(os.path.join(_HERE, "services", "verdicts.py"),
                   encoding="utf-8", errors="replace").read()
        self.assertIn("from kernel.truth import", src,
                      "verdicts 未引用 kernel.truth —— truth 又变回零消费者的假 SSOT")

    def test_docs_point_to_existing_module(self):
        """文档里引用的 SSOT 模块必须真实存在（删/移模块会让文档悬空）。"""
        p = os.path.join(_HERE, "kernel", "truth.py")
        self.assertTrue(os.path.isfile(p), "docs 引用的 backend/kernel/truth.py 不存在")


class TestExplicitClear(unittest.TestCase):
    """P2-7：显式清空（解绑）必须生效。"""

    def setUp(self) -> None:
        _reset()
        set_kv_json(live_config._KV_KEY, {"lc_shared": {"id": "lc_shared",
                                                        "name": "共用"}})

    def test_strategy_unbind_via_empty_string(self):
        import asyncio
        saved = asyncio.run(live_rooms.save_room(live_rooms.RoomBody(
            room_id="333", name="房C", strategy_id="lc_shared")))
        rid = saved["room"]["id"]
        self.assertEqual(saved["room"]["strategy_id"], "lc_shared")
        cleared = asyncio.run(live_rooms.save_room(live_rooms.RoomBody(
            id=rid, room_id="333", strategy_id="")))
        self.assertTrue(cleared["ok"])
        self.assertEqual(cleared["room"]["strategy_id"], "",
                         "显式置空被当成「未提交」并回落旧值 = 解绑永远失败")
        # 读回（真落库）
        rec = (get_kv_json(live_rooms._KV_KEY, {}) or {})[rid]
        self.assertEqual(rec["strategy_id"], "")

    def test_name_can_be_cleared(self):
        import asyncio
        saved = asyncio.run(live_rooms.save_room(live_rooms.RoomBody(
            room_id="334", name="有备注")))
        rid = saved["room"]["id"]
        r = asyncio.run(live_rooms.save_room(live_rooms.RoomBody(
            id=rid, room_id="334", name="")))
        self.assertEqual(r["room"]["name"], "")

    def test_omitted_field_keeps_old_value(self):
        """反向守卫：**根本没带**该字段时不得清空（改备注不该解绑策略）。"""
        import asyncio
        saved = asyncio.run(live_rooms.save_room(live_rooms.RoomBody(
            room_id="335", strategy_id="lc_shared", name="原备注")))
        rid = saved["room"]["id"]
        r = asyncio.run(live_rooms.save_room(live_rooms.RoomBody(
            id=rid, room_id="335", name="改个名")))
        self.assertEqual(r["room"]["strategy_id"], "lc_shared",
                         "未提交 strategy_id 却被清空 = 误伤绑定")

    def test_room_id_not_clearable(self):
        """身份锚点不可清空：空 room_id 必须回落旧值（否则破坏身份契约）。"""
        import asyncio
        saved = asyncio.run(live_rooms.save_room(live_rooms.RoomBody(
            room_id="336", name="房")))
        rid = saved["room"]["id"]
        r = asyncio.run(live_rooms.save_room(live_rooms.RoomBody(
            id=rid, room_id="", name="房")))
        self.assertEqual(r["room"]["room_id"], "336")

    # ---- 负控：旧表达式必失败 ----
    def test_negative_control_old_expression_kept_old_value(self):
        old = str("" or "").strip() or str("lc_shared" or "")
        self.assertEqual(old, "lc_shared",
                         "负控失效：旧表达式居然支持清空（根因需重审）")


class TestReferentialIntegrity(unittest.TestCase):
    """P2-8：写入侧不得产生悬空策略引用。"""

    def setUp(self) -> None:
        _reset()

    def test_dangling_reference_rejected(self):
        import asyncio
        r = asyncio.run(live_rooms.save_room(live_rooms.RoomBody(
            room_id="444", name="房D", strategy_id="lc_nope")))
        self.assertFalse(r["ok"], "绑定了不存在的策略却写成功 = 悬空引用")
        self.assertEqual(get_kv_json(live_rooms._KV_KEY, {}) or {}, {},
                         "被拒的写请求不得留下任何记录")

    def test_existing_reference_accepted(self):
        import asyncio
        set_kv_json(live_config._KV_KEY, {"lc_ok": {"id": "lc_ok", "name": "真策略"}})
        r = asyncio.run(live_rooms.save_room(live_rooms.RoomBody(
            room_id="445", name="房E", strategy_id="lc_ok")))
        self.assertTrue(r["ok"])
        self.assertEqual(r["room"]["strategy_id"], "lc_ok")

    def test_empty_reference_still_allowed(self):
        """「未绑定」= 空串，必须照常可写（不能把解绑路径也堵死）。"""
        import asyncio
        r = asyncio.run(live_rooms.save_room(live_rooms.RoomBody(
            room_id="446", name="房F", strategy_id="")))
        self.assertTrue(r["ok"])


class TestDeleteStrategyOrdering(unittest.TestCase):
    """P2-10：解绑失败不得留下悬空引用、不得谎报 ok。"""

    def setUp(self) -> None:
        _reset()

    def _one_room_refers(self) -> str:
        import asyncio
        set_kv_json(live_config._KV_KEY, {"lc_ref": {"id": "lc_ref", "name": "被引用"}})
        asyncio.run(live_rooms.save_room(live_rooms.RoomBody(
            room_id="555", name="房E", strategy_id="lc_ref")))
        return "lc_ref"

    def test_unbind_failure_aborts_delete_and_reports_not_ok(self):
        import asyncio
        self._one_room_refers()
        with mock.patch.object(live_rooms, "unbind_strategy",
                               side_effect=RuntimeError("模拟解绑失败")):
            r = asyncio.run(live_config.delete_strategy("lc_ref"))
        self.assertFalse(r["ok"], "解绑失败却回 ok=True = 前端只判 ok 就看不到问题")
        # 策略必须还在（宁可删不掉，也不留悬空引用）
        self.assertIn("lc_ref", get_kv_json(live_config._KV_KEY, {}) or {})
        room = list((get_kv_json(live_rooms._KV_KEY, {}) or {}).values())[0]
        self.assertEqual(room["strategy_id"], "lc_ref")
        # 有引用 ⇒ 有策略（不变量）
        self.assertTrue(live_rooms._strategy_exists(room["strategy_id"]))

    def test_happy_path_deletes_and_unbinds(self):
        import asyncio
        self._one_room_refers()
        r = asyncio.run(live_config.delete_strategy("lc_ref"))
        self.assertTrue(r["ok"])
        self.assertEqual(r["unbound"], 1)
        self.assertNotIn("lc_ref", get_kv_json(live_config._KV_KEY, {}) or {})
        room = list((get_kv_json(live_rooms._KV_KEY, {}) or {}).values())[0]
        self.assertEqual(room["strategy_id"], "")

    def test_missing_strategy_is_not_ok(self):
        import asyncio
        r = asyncio.run(live_config.delete_strategy("lc_ghost"))
        self.assertFalse(r["ok"])


class TestTimeoutSchemaBounds(unittest.TestCase):
    """P2-9：timeout_* 必须带 min/max，且默认值能过自身校验。"""

    FIELDS = ("timeout_bcc_http", "timeout_fast_probe", "timeout_http_req")

    def test_bounds_declared(self):
        for f in self.FIELDS:
            m = schema.SECTIONS["general"]["fields"][f]
            self.assertIn("min", m, f"{f} 缺 min（apply:hot 却无范围保护）")
            self.assertIn("max", m, f"{f} 缺 max")
            self.assertIsInstance(m["min"], (int, float))
            self.assertIsInstance(m["max"], (int, float))
            self.assertLess(m["min"], m["max"])
            self.assertTrue(m["min"] <= m["default"] <= m["max"],
                            f"{f} 默认值不在自身范围内")

    def test_out_of_range_value_rejected_by_coerce(self):
        """行为证明：越界值必须被 app_config 丢弃（回落默认）。"""
        import services.app_config as ac
        for f in self.FIELDS:
            m = schema.SECTIONS["general"]["fields"][f]
            self.assertIsNone(ac._coerce(-1, m["type"], m),
                              f"{f} 负值未被拒 = 范围保护没生效")
            self.assertIsNone(ac._coerce(m["max"] * 1e3, m["type"], m),
                              f"{f} 超上限未被拒")
            self.assertIsNotNone(ac._coerce(m["default"], m["type"], m))

    def test_all_numeric_fields_have_bounds(self):
        """同类问题一网打尽：全 schema 数值字段都必须声明 min/max。"""
        missing = []
        for sec, body in schema.SECTIONS.items():
            for k, m in (body.get("fields") or {}).items():
                if m.get("type") in ("int", "float") and ("min" not in m or "max" not in m):
                    missing.append(f"{sec}.{k}")
        # 已知遗留：dm 分组三个昵称兜底阈值（他文件/另批），此处显式登记不掩盖
        allowed_legacy = {
            "dm.nickname_fallback_min_interval_sec",
            "dm.nickname_fallback_max_per_run",
            "dm.nickname_fallback_daily_cap",
        }
        unexpected = [x for x in missing if x not in allowed_legacy]
        self.assertEqual(unexpected, [], f"数值字段缺边界：{unexpected}")


class TestWhitelistIsRealGate(unittest.TestCase):
    """P4：`_FIELDS` 必须是真门禁（摘掉一项 → 该字段落不了库）。"""

    def setUp(self) -> None:
        _reset()

    def test_removing_field_from_whitelist_blocks_write(self):
        import asyncio
        orig = set(live_rooms._FIELDS)
        live_rooms._FIELDS = {k for k in orig if k != "name"}
        try:
            r = asyncio.run(live_rooms.save_room(live_rooms.RoomBody(
                room_id="666", name="不该落库")))
            rec = (get_kv_json(live_rooms._KV_KEY, {}) or {})[r["room"]["id"]]
            self.assertNotIn("name", rec,
                             "_FIELDS 被摘掉一项却照样落库 = 白名单没人消费（死代码）")
        finally:
            live_rooms._FIELDS = orig

    def test_all_written_keys_are_whitelisted(self):
        import asyncio
        r = asyncio.run(live_rooms.save_room(live_rooms.RoomBody(
            room_id="667", name="房G", strategy_id="",
            allow_desensitized=True)))
        rec = (get_kv_json(live_rooms._KV_KEY, {}) or {})[r["room"]["id"]]
        # 服务端托管字段（id 以键名为准 / updated_at 由时钟生成）不受客户端白名单管辖
        server_managed = {"id", "updated_at"}
        client_keys = set(rec) - server_managed
        self.assertTrue(client_keys.issubset(live_rooms._FIELDS),
                        f"落库的客户端字段超出白名单：{client_keys - live_rooms._FIELDS}")
        self.assertIn("updated_at", rec, "服务端托管字段 updated_at 必须写入")


if __name__ == "__main__":
    unittest.main(verbosity=2)
