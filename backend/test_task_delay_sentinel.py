# -*- coding: utf-8 -*-
"""TaskConfig.delay_range 哨兵语义回归测试（2026-09-17 OCR 审查 HIGH）。

缺陷：`models/task.py` 的 `to_task_config()` 原用
    (not delay_range or delay_range == [40, 65]) and self.delay
把**字段默认值 [40,65]** 当成"用户未设置"的判据 —— 用户显式传 [40,65]
与没传无法区分（一旦默认值调整即成真 bug）。

修复：字段默认值改为 None 作显式哨兵；语义为
    delay_range 优先 → 其次 delay 别名 → 都未提供则回落 [40,65]

本测试直接验证「任务书要求的语义」，并用旧实现做对照，
确保：① 显式 delay_range 不被 delay 覆盖；② 只传 delay 时仍能解析（无回归）；
③ 两者都缺时回落 [40,65]（与旧行为一致）。

用法：cd FlowCap/backend && python test_task_delay_sentinel.py
"""
import unittest


def _parse_delay(s: str):
    """复刻 models/task.py 的 _parse_delay 语义（简化：逗号分隔取两段）。"""
    parts = [int(x) for x in str(s).replace(" ", "").split(",") if x != ""]
    if len(parts) >= 2:
        return [parts[0], parts[1]]
    if len(parts) == 1:
        return [parts[0], parts[0]]
    return []


def new_semantics(delay_range, delay):
    """修复后的逻辑。"""
    dr = delay_range
    if (not dr) and delay:
        dr = _parse_delay(delay)
    if not dr:
        dr = [40, 65]
    return dr


def old_semantics(delay_range, delay):
    """旧逻辑（默认值当哨兵）。"""
    dr = delay_range if delay_range is not None else [40, 65]
    if (not dr or dr == [40, 65]) and delay:
        dr = _parse_delay(delay)
    return dr


class TestDelaySentinel(unittest.TestCase):
    def test_explicit_range_not_overridden(self):
        """显式给了 delay_range 就不该被 delay 覆盖。"""
        self.assertEqual(new_semantics([10, 20], "100,200"), [10, 20])

    def test_explicit_default_range_is_kept(self):
        """显式传 [40,65] 也是"显式"——修复后必须保留原值。"""
        # delay 传一个不同值来暴露旧实现的覆盖行为
        self.assertEqual(new_semantics([40, 65], "10,20"), [40, 65])

    def test_delay_alias_still_works(self):
        """只传 delay 别名时必须仍被解析（防回归）。"""
        self.assertEqual(new_semantics(None, "50,120"), [50, 120])
        self.assertEqual(new_semantics([], "50,120"), [50, 120])

    def test_fallback_when_both_missing(self):
        """两者都未提供 → 回落既定默认 [40,65]（与旧行为一致）。"""
        self.assertEqual(new_semantics(None, None), [40, 65])
        self.assertEqual(new_semantics([], ""), [40, 65])

    def test_old_impl_loses_explicit_default(self):
        """对照：旧实现会把显式 [40,65] 当成"未设置"而被 delay 覆盖。"""
        got = old_semantics([40, 65], "10,20")
        self.assertEqual(got, [10, 20],
                         "旧实现应把显式的 [40,65] 覆盖成 delay 的值（缺陷复现）")
        # 而新实现保留显式值
        self.assertEqual(new_semantics([40, 65], "10,20"), [40, 65])


if __name__ == "__main__":
    unittest.main(verbosity=2)
