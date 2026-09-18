# -*- coding: utf-8 -*-
"""直播监听配置三条链路的回归守卫（2026-09-18 事故固化，v0.43.91）。

## 为什么要这三条（全部为实测复现的真实缺陷）

| # | 缺陷 | 用户可见症状 |
|---|---|---|
| 1 | `services.dm_dispatch` 未在 `build_sidecar.py` 声明 hidden-import | 打包后 `No module named 'services.dm_dispatch'` → 直播/采集**一条私信都发不出**，日志仅一行 `SEND-037` |
| 2 | 新建配置草稿用「页面当前直播间」预填 `live_url` | 新建房间的配置里存的是**另一个直播间的地址**（实测 room_id=777666555 → live_url=992931212705） |
| 3 | `RoomConfigManager` 保存后不通知父页 | 保存成功但直播页下拉**不刷新**（需整页重载才出现） |
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
_ROOMCFG = os.path.join(_ROOT, "frontend", "src", "components", "live",
                        "RoomConfigManager.tsx")
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
            "RoomConfigManager 的新建草稿又在预填 live_url —— 保存时该值会"
            "覆盖按 room_id 推导的地址，导致配置指向别的直播间（实测 777666555 "
            "存成 992931212705）。草稿只应带 room_id。")


class TestParentRefreshWiring(unittest.TestCase):
    """缺陷 3：保存/删除/重启后必须通知父页刷新，否则下拉不更新。"""

    def test_component_has_onchanged_prop(self):
        src = _read(_ROOMCFG)
        self.assertIn("onChanged", src,
                      "RoomConfigManager 未提供 onChanged 回调 → 保存后父页"
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
                      "RoomConfigManager 缺少「私信词库」编辑入口 —— 直播页会提示"
                      "「请到配置管理补充」，但那里没有入口 → dm_pool 恒空 → "
                      "pick_dm_message() 返回 None → 私信永远发不出。")
        self.assertIn("dm_pool", src)
        self.assertIn("新增一条文案", src, "缺少「新增一条文案」按钮")


if __name__ == "__main__":
    unittest.main(verbosity=2)
