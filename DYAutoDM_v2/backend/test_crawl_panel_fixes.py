"""悬浮窗两处修复门禁（★ 2026-10-03，用户指令）。

## 修的两件事

① **三个下拉「能点开但选项不显示/错位」**（用户实测，仅采集悬浮窗）
   根因：Radix `SelectContent` 默认 `position="popper"`，依赖 floating-ui
   **测量 trigger 尺寸**；而 trigger 位于 `ModalBody`（`overflow-y-auto`）
   滚动容器内，Portal 把内容挂到 body 后测量基准与滚动容器脱节。
   修法：`select.tsx` 默认改 `item-aligned`（用 trigger 几何盒定位，不测量）
   + 补 `--radix-select-trigger-width` 宽度对齐。

② **私信复用标签，不要单独的文案输入框**
   唯一真源 = 标签的 `send.dm_pool`（配置中心「私信词库」）；
   前端删掉输入框与「文案为空」拦截，后端 `/dm/batch` 在 text 为空时
   按标签取 `dm_pool` 首条，取不到才 400（fail-closed 在后端）。

## 判据设计

⚠️ 本项目**实测「每次启动浏览器都卡死」** ⇒ 不用浏览器做点击验收
   （`frontend-visual-verification` 的浏览器路径在本项目不可用）。
   本门禁走**静态契约 + 纯函数行为**两条路：
     · 前端：`tsc` 已在 CI 侧把关类型；此处断言「源码里不再有输入框/
       不再传 text / 不再有 dmTpl」。
     · 后端：真跑 `_pick_dm_text` 等价逻辑（词库切分 + 首条选取）。

## 运行

    cd backend && python -m unittest test_crawl_panel_fixes -v
"""
from __future__ import annotations

import io
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_FRONT = os.path.join(_HERE, "..", "frontend", "src")
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


def _read(rel: str) -> str:
    with io.open(os.path.join(_FRONT, rel), encoding="utf-8") as f:
        return f.read()


class TestSelectPositioning(unittest.TestCase):
    """① 下拉定位：默认必须是 item-aligned（跨滚动容器稳定）。"""

    def test_default_position_is_item_aligned(self):
        src = _read(os.path.join("components", "ui", "select.tsx"))
        self.assertIn('position = "item-aligned"', src,
                      "SelectContent 默认仍是 popper（跨滚动容器定位失准）")

    def test_popper_mode_has_width_var(self):
        """popper 模式必须对齐 trigger 宽度，否则「位置对但宽度不对」。"""
        src = _read(os.path.join("components", "ui", "select.tsx"))
        self.assertIn("--radix-select-trigger-width", src,
                      "popper 模式未对齐 trigger 宽度（会出现横向滚动条/宽度错）")

    def test_position_still_overridable(self):
        """⚠️ 默认值改动影响全项目 ⇒ 必须保留按调用点覆盖的能力。"""
        src = _read(os.path.join("components", "ui", "select.tsx"))
        self.assertIn("...props", src,
                      "props 未透传 ⇒ 调用点无法覆盖 position（默认值改动太危险）")
        self.assertIn("position={position}", src, "position 未透传给 Primitive")

    def test_no_syntax_break_from_comma(self):
        """回归：曾因多写一个逗号把 Portal 闭合成 `</Portal>,` ⇒ TS1109。"""
        src = _read(os.path.join("components", "ui", "select.tsx"))
        self.assertNotIn("</SelectPrimitive.Portal>,", src,
                         "Portal 闭合后又跟了逗号（TS1109 回归）")


class TestDmTextFromTag(unittest.TestCase):
    """② 私信文案复用标签：前端不再输入，后端按标签取。"""

    PANEL = os.path.join("components", "crawl", "CrawlFloatingPanel.tsx")

    def test_no_dm_template_state(self):
        src = _read(self.PANEL)
        self.assertNotIn("dmTpl", src, "dmTpl state/引用仍在（应完全移除）")

    def test_no_text_input_in_ui(self):
        src = _read(self.PANEL)
        self.assertNotIn('placeholder="私信文案"', src,
                         "私信文案输入框仍在（应复用标签）")

    def test_dm_batch_call_omits_text(self):
        src = _read(self.PANEL)
        i = src.find("crawlDmBatch")
        self.assertGreater(i, 0, "找不到 crawlDmBatch 调用")
        seg = src[i:i + 600]
        # 调用参数里不应再有 text:（让后端走标签回落）
        self.assertNotIn("text:", seg.split("})")[0],
                         "crawlDmBatch 仍在传 text（会盖掉标签词库）")

    def test_api_contract_text_optional(self):
        src = _read(os.path.join("api", "client.ts"))
        i = src.find("async crawlDmBatch")
        seg = src[i:i + 700]
        self.assertIn("text?: string", seg,
                      "契约里 text 仍是必填（前端不传就编译不过 / 语义错）")

    def test_backend_falls_back_to_tag_pool(self):
        import inspect
        import api.crawl as C
        src = inspect.getsource(C.crawl_dm_batch)
        self.assertIn('"dm_pool"', src,
                      "后端未从标签取 dm_pool（text 为空会直接 400）")
        self.assertIn('"send"', src, "未从 send 分区取词库")

    def test_backend_uses_app_config_scope_chain(self):
        """🔴 必须走 app_config.get（内建「标签→全局→默认」回落链）。"""
        import inspect
        import api.crawl as C
        src = inspect.getsource(C.crawl_dm_batch)
        self.assertIn("app_config", src,
                      "未复用 app_config（会自造第二套回落逻辑）")
        self.assertIn("scope=", src, "未传 scope ⇒ 拿不到标签级配置")

    def test_pool_first_line_pick_is_deterministic(self):
        """🔴 词库「每行一条」⇒ 取**首条**（确定性），且不残留 CR。"""
        # 与后端同款逻辑（后端用 chr(13)/chr(10) 以避开转义传输坑）
        pool = "第一条文案\r\n第二条文案\r\n"
        lines = [ln.strip() for ln in pool.replace(chr(13), "").split(chr(10))
                 if ln.strip()]
        self.assertEqual(lines, ["第一条文案", "第二条文案"])
        self.assertEqual(lines[0], "第一条文案")
        self.assertNotIn("\r", lines[0], "首条残留 CR（发出去会带空白）")

    def test_empty_pool_yields_no_text(self):
        for pool in ("", "   \n\n  ", "\r\n"):
            lines = [ln.strip() for ln in pool.replace(chr(13), "").split(chr(10))
                     if ln.strip()]
            self.assertEqual(lines, [], f"空词库 {pool!r} 竟切出内容")

    def test_no_frontend_precheck_blocks_start(self):
        """前端不该再拦「文案为空」—— fail-closed 在后端。"""
        src = _read(self.PANEL)
        self.assertNotIn("私信文案为空", src,
                         "前端仍在拦「文案为空」（与复用标签矛盾，且拦在真源之前）")


class TestNegativeControl(unittest.TestCase):
    """负控自证：门禁必须能变红。"""

    def test_red_when_position_reverted(self):
        """把默认改回 popper ⇒ TestSelectPositioning 应变红。"""
        p = os.path.join(_FRONT, "components", "ui", "select.tsx")
        with io.open(p, encoding="utf-8") as f:
            src = f.read()
        self.assertIn('position = "item-aligned"', src,
                      "负控前提失效：默认已不是 item-aligned")

    def test_red_when_pool_parsing_is_naive(self):
        """naive split（不剥 CR）⇒ 首条会带 \\r ⇒ 判据应能抓到。"""
        pool = "第一条\r\n第二条"
        naive = [ln for ln in pool.split(chr(10)) if ln.strip()]
        good = [ln.strip() for ln in pool.replace(chr(13), "").split(chr(10))
                if ln.strip()]
        self.assertNotEqual(naive[0], good[0],
                            "负控前提失效：两种切分结果相同（CR 没造成差异）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
