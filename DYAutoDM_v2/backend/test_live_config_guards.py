# -*- coding: utf-8 -*-
"""直播监听配置三条链路的回归守卫（2026-09-18 事故固化，v0.43.91）。

## 为什么要这三条（全部为实测复现的真实缺陷）

| # | 缺陷 | 用户可见症状 |
|---|---|---|
| 1 | `services.dm_dispatch` 未在 `build_sidecar.py` 声明 hidden-import | 打包后 `No module named 'services.dm_dispatch'` → 直播/采集**一条私信都发不出**，日志仅一行 `SEND-037` |
| 2 | 新建配置草稿用「页面当前直播间」预填 `live_url` | 新建房间的配置里存的是**另一个直播间的地址**（实测 room_id=777666555 → live_url=992931212705） |
| 3 | 配置页保存后不通知父页 | 保存成功但直播页下拉**不刷新**（需整页重载才出现） |
| 4 | 唯一可写入口无「私信词库」编辑 UI | 页面提示「请到配置管理补充」但那里没有入口 → `dm_pool` 恒空 → 私信发不出 |

判据来源均为**真实浏览器 + 真实后端**实测（见 `工作记忆/cases/`），不是代码推断。

## 运行

    cd backend && python -m unittest test_live_config_guards -v
"""
from __future__ import annotations

import os
import re
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
# 2026-09-19：配置管理弹窗 → 整页视图「配置标签」（参数唯一可写入口不变）
_ROOMCFG = os.path.join(_ROOT, "frontend", "src", "components", "live",
                        "RoomConfigPage.tsx")
_TARGETPAGE = os.path.join(_ROOT, "frontend", "src", "components", "live",
                           "TargetRoomPage.tsx")
_LIVEPAGE = os.path.join(_ROOT, "frontend", "src", "components", "live",
                         "live-page.tsx")
_BUILD = os.path.join(_ROOT, "scripts", "build_sidecar.py")


def _read(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


class TestDmDispatchHiddenImport(unittest.TestCase):
    """缺陷 1：发送调度模块必须声明 hidden-import（否则打包后发送全废）。"""

    def test_declared(self):
        src = _read(_BUILD)
        self.assertIn(
            "services.dm_dispatch", src,
            "build_sidecar.py 未声明 services.dm_dispatch —— 该模块只在函数体内被 "
            "import（core/dispatch.py），打包后必然 ModuleNotFoundError，"
            "直播/采集私信将一条都发不出（SEND-037）。")

    def test_whitelist_injection_is_valid_python(self):
        """🔴 真根因守卫：白名单注入产物**任何 WL 下都必须是合法 Python**。

        空白名单时旧实现产出 `_TEST_WHITELIST = {\\n,\\n}`（SyntaxError），
        而注入发生在 PyInstaller 分析之前 ⇒ 整个 `services/dm_dispatch.py`
        被**静默丢弃**（日志连 "Analyzing hidden import" 都不打印）⇒
        backend 调它时 `No module named 'services.dm_dispatch'` ⇒
        直播/采集私信一条都发不出。构建末尾 restore 又把源码改回合法空壳，
        把问题完全掩盖。故此处直接对生成函数做语法断言。
        """
        import importlib.util

        spec = importlib.util.spec_from_file_location("_bs_guard", _BUILD)
        bs = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        spec.loader.exec_module(bs)  # type: ignore[union-attr]
        self.assertTrue(hasattr(bs, "build_whitelist_block"),
                        "缺少 build_whitelist_block 纯函数（注入体不可单测）")
        for wl in ({}, {"acctA": "123"}, {"a": "1", "b": "2"}):
            _body, block = bs.build_whitelist_block(wl)
            try:
                compile(block, "<whitelist-block>", "exec")
            except SyntaxError as e:  # noqa: PERF203
                self.fail(
                    f"白名单注入体语法非法（WL={wl}）：{e.msg} @ {e.text!r} —— "
                    f"会导致 services.dm_dispatch 被 PyInstaller 静默丢弃。")

    def test_scan_script_exists(self):
        """静态守卫脚本必须存在（把「加模块要声明」变成机器可检查）。"""
        p = os.path.join(_ROOT, "scripts", "diag", "scan_lazy_imports.py")
        self.assertTrue(os.path.isfile(p), f"缺少守卫脚本 {p}")

    def test_archive_reader_exists(self):
        """产物权威判据脚本必须存在（不是 grep 二进制）。"""
        p = os.path.join(_ROOT, "scripts", "diag", "list_archive_modules.py")
        self.assertTrue(os.path.isfile(p), f"缺少产物判据脚本 {p}")


class TestNoLiveUrlPrefill(unittest.TestCase):
    """缺陷 2(A)：新建草稿不得预填 live_url（否则会串到别的直播间）。"""

    def test_draft_has_no_live_url(self):
        src = _read(_ROOMCFG)
        bad = re.findall(
            r"setDraft\(\{\s*\.\.\.EMPTY_DRAFT[^)]*live_url", src)
        self.assertEqual(
            bad, [],
            "配置标签页的新建草稿又在预填 live_url —— 保存时该值会"
            "覆盖按 room_id 推导的地址，导致配置指向别的直播间（实测 777666555 "
            "存成 992931212705）。草稿只应带 room_id。")


class TestParentRefreshWiring(unittest.TestCase):
    """缺陷 3：保存/删除/重启后必须通知父页刷新，否则下拉不更新。"""

    def test_component_has_onchanged_prop(self):
        src = _read(_ROOMCFG)
        self.assertIn("onChanged", src,
                      "配置标签页未提供 onChanged 回调 → 保存后父页"
                      "（直播页下拉）不刷新，用户看到「保存了但不显示」。")

    def test_calls_onchanged_after_writes(self):
        src = _read(_ROOMCFG)
        n = len(re.findall(r"onChanged\?\.\(\)", src))
        self.assertGreaterEqual(
            n, 3,
            f"onChanged 调用点只有 {n} 处，应至少覆盖 保存/删除/重启 三条写路径。")

    def test_live_page_passes_callback(self):
        src = _read(_LIVEPAGE)
        self.assertIn("onChanged={loadRoomCfgs}", src,
                      "直播页没有把 loadRoomCfgs 作为 onChanged 传下去 → "
                      "配置管理里保存后直播页列表不会刷新。")


class TestDmPoolEditorExists(unittest.TestCase):
    """缺陷 4：唯一可写入口必须有「私信词库」编辑 UI（否则 dm_pool 恒空）。"""

    def test_editor_present(self):
        src = _read(_ROOMCFG)
        self.assertIn("私信词库", src,
                      "配置标签页缺少「私信词库」编辑入口 —— 直播页会提示"
                      "「请到配置管理补充」，但那里没有入口 → dm_pool 恒空 → "
                      "pick_dm_message() 返回 None → 私信永远发不出。")
        self.assertIn("dm_pool", src)
        self.assertIn("新增一条文案", src, "缺少「新增一条文案」按钮")


class TestConfigTagAndTargetRoomSeparation(unittest.TestCase):
    """2026-09-19 用户定调（两次修正后的**最终**契约）。

    第一次原话：「配置管理修改只能对现有的配置反复覆盖，没有实现多配置标签的功能。
    目标直播间可以管理，但别放到配置管理中，单独加一个目标直播间管理，
    在该页面中选择是否绑定配置。」

    第二次修正（覆盖第一次，最终生效，本类以此为准）：
    「把直播策略放到直播监听页面中『直播间』板块的配置标签中，该编辑页面中只保留
      直播策略，策略以外的全部删除。直播间管理也是一样，不需要『监听 / 目标直播间 /
      配置标签』tab 切换栏也不需要子 tab 页面。还有『强制重扫』策略早就废弃了，
      给彻底移除。」

    最终判据：
      1. 策略编辑页 = 参数唯一可写入口，且**只含策略**（身份字段与强制重扫已删）
      2. 无子 tab 切换栏、无「目标直播间」页（直播间号在「直播间」板块直接填）
      3. 策略以弹窗提供，入口在「直播间」板块的策略下拉旁
      4. 直播页保存后仍通知父页刷新（onChanged）
    """

    def test_target_room_page_removed(self):
        """用户明确要求：不要子 tab / 不要「目标直播间」页。防止它被无意复活。"""
        self.assertFalse(os.path.isfile(_TARGETPAGE),
                         "「目标直播间」页已被用户明确要求删除，不得复活")
        self.assertFalse(
            os.path.isfile(os.path.join(_ROOT, "backend", "api", "target_rooms.py")),
            "后端 target_rooms 模块已被用户明确要求删除，不得复活")

    def test_no_subview_tabs_in_live_page(self):
        """直播页不得出现「监听 / 目标直播间 / 配置标签」三 tab 子视图。"""
        src = _read(_LIVEPAGE)
        self.assertNotIn("subView", src, "直播页又出现了子 tab 状态（用户明确不要）")
        self.assertNotIn("目标直播间", src, "直播页又出现了「目标直播间」tab 文案")

    def test_config_page_is_single_writable_entry(self):
        """策略编辑页 = 参数唯一可写入口，且**只保留策略**（身份/废弃字段已删除）。"""
        src = _read(_ROOMCFG)
        for k in ("发送上限", "间隔（秒）", "延迟抖动（秒）", "私信词库",
                  "自动申请连麦", "监听账号"):
            self.assertIn(k, src, f"策略编辑页缺少「{k}」编辑控件")
        jsx = src.split("export default function RoomConfigPage")[-1]
        for k in ("备注名", "直播间号或 URL", "直播链接", "强制重扫"):
            self.assertNotIn(k, jsx, f"策略编辑页 JSX 仍含非策略字段「{k}」")

    def test_live_page_mounts_strategy_modal(self):
        """策略弹窗挂在直播页；入口在「直播间」板块的策略下拉旁（无子 tab）。"""
        src = _read(_LIVEPAGE)
        self.assertIn("<RoomConfigPage", src, "直播页未挂载策略弹窗")
        self.assertIn("管理策略", src, "「直播间」板块缺少策略管理入口")
        self.assertIn("选择直播策略", src, "「直播间」板块缺少策略下拉")

    def test_explicit_new_strategy_entry(self):
        """策略弹窗必须有**显式的新建入口** —— 用户反馈「没有新增入口」。

        仅有「清空表单 + 保存」时，用户无法知道那就是新建；且编辑态下也无从
        退出到新建态。判据：存在 data-od-id="strategy-new" 的按钮。
        """
        src = _read(_ROOMCFG)
        self.assertIn('data-od-id="strategy-new"', src,
                      "策略弹窗缺少显式「新建策略」入口（用户明确反馈过没有）")
        self.assertIn("新建策略", src, "新建入口的文案应为「新建策略」")
        # 用户指定版式：放在页脚里、且在「保存/更新策略」按钮**左侧**
        footer = src.split("flex justify-end gap-2")[-1]
        self.assertIn('data-od-id="strategy-new"', footer,
                      "「新建策略」必须位于弹窗页脚（用户指定位置）")
        self.assertLess(footer.index('data-od-id="strategy-new"'), footer.index("save}"),
                        "「新建策略」必须排在「保存/更新策略」按钮左侧")
        # 点击必须有**可见变化**（用户两次反馈"点击没变化"）
        # 判据：列表区必须有一条可见草稿行 —— 这是用户指明要变的元素位置
        self.assertIn('data-od-id="strategy-draft-row"', src,
                      "点「新建策略」必须在**列表区**产生可见草稿行"
                      "（否则默认态下与打开时无差别，用户判定为无反应）")
        self.assertIn("newMode", src, "缺少新建态状态位（无法让列表区显式变化）")
        self.assertIn("const startNew", src, "缺少统一的新建入口处理函数")

    def test_force_rescan_fully_removed(self):
        """「强制重扫」策略已废弃，全仓业务代码不得再有该配置项的读写。"""
        for rel in ("api/tasks.py", "core/auto_dm.py", "services/app_config_schema.py",
                    "models/task.py"):
            src = _read(os.path.join(_ROOT, "backend", rel))
            self.assertNotIn("force_rescan", src, f"{rel} 仍有 force_rescan 残留")
            self.assertNotIn("forceRescan", src, f"{rel} 仍有 forceRescan 残留")

    def test_old_modal_component_removed(self):
        p = os.path.join(_ROOT, "frontend", "src", "components", "live",
                         "RoomConfigManager.tsx")
        self.assertFalse(os.path.isfile(p),
                         "旧弹窗 RoomConfigManager.tsx 仍在 —— 两个配置入口并存会让"
                         "测试与用户都分不清哪个是真源")


if __name__ == "__main__":
    unittest.main(verbosity=2)
