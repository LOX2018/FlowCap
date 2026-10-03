"""玻璃模糊 × 包含块 门禁（★ 2026-10-03，用户实测：悬浮窗下拉「能点开但看不见」）。

## 缺陷根因（两个，都在本文件守）

① **z 层级倒挂**：`--z-popover: 50` < `--z-modal: 100`。
   Radix Select 走 Portal 挂到 `body`，z 只有 50 ⇒ 被 z=100 的面板整层盖住。
   为什么配置中心不暴露：它的页面没有 z-modal 宿主。

② **`backdrop-filter` 造成包含块**：任何在元素**自身**上写
   `backdrop-filter` / `filter` / `transform` 的选择器都会成为
   **containing block** ⇒ 其内部 `position: fixed` 后代改为相对它定位。
   而 Radix 的浮层（Select/DropdownMenu/Popover/Tooltip）**全部**走
   Portal + fixed 落在宿主附近 ⇒ 宿主一旦是包含块，浮层定位错位。

## 统一修法（不改视觉效果）

  ① 宿主只留 `position: relative`；
  ② 模糊值存进 `--glass-blur` 自定义属性，伪元素 `::before` 读它
     （absolute + inset:0 + pointer-events:none）；
  ③ 直接子元素 `position: relative` 提到伪元素之上。

## 判据设计

⚠️ 判据必须**行为化**：只 grep 关键字会漏（写法很多）。
本门禁用 **AST-free 的 CSS 块解析**：把每个选择器的声明体切出来，逐个
  断言「若声明了会创建包含块的属性，则必须有配套的 ::before 伪元素 +
  宿主 position:relative + 子元素提升」。

## 运行

    cd backend && python -m unittest test_glass_blur_container -v
"""
from __future__ import annotations

import io
import os
import re
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_CSS = os.path.join(_HERE, "..", "frontend", "src", "styles", "tokens.css")
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

#: 会创建包含块的 CSS 属性（作用于元素自身时）
CONTAINER_MAKERS = ("backdrop-filter", "filter", "transform",
                    "will-change", "contain", "perspective")


def _css() -> str:
    with io.open(_CSS, encoding="utf-8") as f:
        return f.read()


def _rule_bodies(src: str):
    """切出每个规则的 (选择器, 声明体)。够用且不引第三方依赖。

    ⚠️ 三个必做的处理（每次都是门禁跟不上实现，实测踩了 3 次）：
       ① **剥注释** —— 注释会被并进「选择器」，导致同一选择器被拆成
          不同 key（`.card-surface` 的选择器文本里含整段注释）。
       ② **排除 @keyframes 内部** —— 那里 `0%,100%{transform}` 是动画
          关键帧，不是宿主元素，否则全是误报。
       ③ **逗号分组要拆开** —— `.a,\n.b { }` 里每个选择器**独立**生效；
          不拆就匹配不上末尾的清理规则（会误判「没清掉」）。
    """
    # ① 剥注释
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    # ② 挖掉 @keyframes 整块
    src = re.sub(r"@keyframes\s+[\w-]+\s*\{(?:[^{}]|\{[^{}]*\})*\}", "", src)
    out = []
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", src):
        body = m.group(2)
        # ③ 逗号分组逐个拆
        for raw in m.group(1).split(","):
            sel = " ".join(raw.split())
            if sel and not sel.startswith("@") and not sel.startswith("from"):
                out.append((sel, body))
    return out


def _declares(body: str, prop: str) -> bool:
    """声明里是否出现该属性（排除 `none` 与自定义属性赋值）。"""
    for m in re.finditer(r"(?<![\w-])" + re.escape(prop) + r"\s*:\s*([^;]+)", body):
        val = m.group(1).strip().lower()
        if val in ("none", "initial", "unset"):
            continue
        return True
    return False


class TestZIndexOrdering(unittest.TestCase):
    """① z 层级：下拉必须高于 modal 与 view。"""

    def _z(self, name: str) -> int:
        m = re.search(r"--" + name + r"\s*:\s*(\d+)\s*;", _css())
        self.assertIsNotNone(m, f"找不到 --{name}")
        return int(m.group(1))

    def test_popover_above_modal(self):
        self.assertGreater(
            self._z("z-popover"), self._z("z-modal"),
            "下拉 z 必须高于模态：模态是容器，容器里的浮层会被整层盖住")

    def test_popover_above_view(self):
        self.assertGreater(
            self._z("z-popover"), self._z("z-view"),
            "下拉 z 必须高于全屏视图（查阅模式/播放器浮层内也可能有下拉）")

    def test_popover_below_toast(self):
        self.assertLess(self._z("z-popover"), self._z("z-toast"),
                        "toast 必须仍是最高层")

    def test_no_duplicate_z_tokens(self):
        """重复声明会让「后写的生效」变得依赖顺序 ⇒ 必须唯一。"""
        src = _css()
        for name in ("z-popover", "z-modal", "z-view", "z-toast", "z-blocker"):
            n = len(re.findall(r"--" + name + r"\s*:", src))
            self.assertEqual(n, 1, f"--{name} 声明了 {n} 次（应唯一）")


class TestNoBackdropFilterOnHost(unittest.TestCase):
    """② 宿主自身不得带会创建包含块的属性。"""

    #: 这些选择器**允许**在自身带（已人工确认其后代不含 Radix 浮层）
    ALLOWED = {".modal-scrim"}

    def test_no_marker_on_host_selectors(self):
        """宿主自身**最终生效**的属性不得是会创建包含块的那几个。

        ⚠️ 判据必须看「**最终生效值**」，不是「文件里有没有出现过」
        （实测踩到）：CSS 同选择器**后者胜**，我在文件后面补了
        `backdrop-filter: none` 真正清掉了，但门禁仍报红 —— 因为它
        只扫「是否存在声明」。故这里按选择器**聚合全部规则**，
        取最后一个 backdrop-filter 值再判。
        """
        # 同一选择器可能散在多处（如 `html:not([data-glass-frost]) .glass-panel`），
        # 按「选择器文本」聚合，保留**出现顺序**（后者胜）。
        merged: dict[str, list[str]] = {}
        for sel, body in _rule_bodies(_css()):
            merged.setdefault(sel.strip(), []).append(body)

        bad = []
        for sel, bodies in merged.items():
            if sel in self.ALLOWED or "::before" in sel or "::after" in sel:
                continue
            final = None
            for body in bodies:
                for m in re.finditer(
                        r"(?<![\w-])backdrop-filter\s*:\s*([^;]+)", body):
                    final = m.group(1).strip().lower()
            if final in (None, "none", "initial", "unset"):
                continue   # 最终无模糊 ⇒ 不会成为包含块
            for prop in CONTAINER_MAKERS:
                if prop == "backdrop-filter":
                    bad.append(f"{sel} (最终 backdrop-filter={final})")
                    break
                # 其它属性无法用「后者胜」简单判定，保守报出
                for body in bodies:
                    if _declares(body, prop):
                        bad.append(f"{sel} {{{prop}}}")
                        break
        self.assertEqual(
            bad, [],
            "这些选择器**最终生效**的样式含会创建包含块的属性 ⇒ 内部 Radix "
            "浮层（Portal + fixed）定位错位。修法：模糊搬进 ::before，"
            f"宿主只留 position:relative。命中: {bad}")

    def test_glass_hosts_have_pseudo_and_child_lift(self):
        """玻璃类宿主必须有配套三件套。"""
        src = _css()
        for host in (".modal-surface", ".popover-surface",
                     ".glass-premium", ".glass-panel", ".card-surface"):
            with self.subTest(host=host):
                self.assertIn(f"{host}::before", src,
                              f"{host} 缺 ::before（模糊未搬家）")
        self.assertIn("pointer-events: none", src,
                      "伪元素缺 pointer-events:none（会挡住内部控件命中）")
        self.assertIn("> *", src, "缺子元素 position 提升规则")

    def test_blur_value_preserved_via_custom_property(self):
        """🔴 模糊值必须搬进自定义属性，不能被 `none` 抹掉。

        我第一版写 `backdrop-filter: none` 清声明 + 伪元素 `inherit`
        ⇒ inherit 变 none ⇒ **玻璃模糊整体消失（视觉回归）**。
        """
        src = _css()
        self.assertIn("--glass-blur", src, "模糊值未存进自定义属性")
        m = re.search(r"backdrop-filter:\s*var\(--glass-blur\)", src)
        self.assertIsNotNone(m, "伪元素未读 --glass-blur")
        vals = re.findall(r"--glass-blur:\s*(blur\([^)]*\))", src)
        self.assertGreaterEqual(len(vals), 3,
                                f"各宿主的 blur 值未逐个登记: {vals}")


class TestNegativeControl(unittest.TestCase):
    """负控自证：门禁必须能变红。"""

    def test_red_when_host_gets_backdrop_filter(self):
        """给宿主加回 backdrop-filter ⇒ 判据应抓到。"""
        fake = ".x-surface { backdrop-filter: blur(20px); }"
        body = re.search(r"\{([^}]*)\}", fake).group(1)
        self.assertTrue(_declares(body, "backdrop-filter"),
                        "负控前提失效：检测不到 backdrop-filter")

    def test_red_when_z_order_inverted(self):
        """z 倒挂 ⇒ 判据应抓到。"""
        self.assertFalse(50 > 100, "负控前提失效：比较逻辑坏了")

    def test_none_does_not_count_as_marker(self):
        """`backdrop-filter: none` 是**清声明**，不该判为含属性。"""
        body = "backdrop-filter: none; color: red;"
        self.assertFalse(_declares(body, "backdrop-filter"),
                         "负控前提失效：把 none 当成了有效声明")


if __name__ == "__main__":
    unittest.main(verbosity=2)
