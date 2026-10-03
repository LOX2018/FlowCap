# -*- coding: utf-8 -*-
"""玻璃模糊「搬家」等价性验证（★ 2026-10-03，纯 CSS 层，无需浏览器）。

## 为什么需要

我把 `backdrop-filter` 从宿主搬进 `::before`。这类「视觉等价」的改动
**肉眼才能确认**，但本项目浏览器不可用（实测启动就卡死）。

本脚本用**最小复现页 + 计算样式推理**做等价性检查：把 tokens.css 里
相关规则抽出来，验证三件事：

  ① 伪元素的模糊值与宿主原值**一致**（视觉不退化）；
  ② 宿主最终**没有** backdrop-filter（不会成为包含块）；
  ③ 宿主的 Tailwind 定位类**不被覆盖**（tokens.css 里不再声明 position）。

## ⚠️ 诚实标注

这是**静态等价性检查**，不是像素比对。真正的验证仍需浏览器实机渲染 ——
但它能挡住「值被写错 / 规则被清掉 / position 被覆盖」这三类回归，
而这三类正是我本轮实际犯的错。
"""
from __future__ import annotations

import io
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.dirname(_HERE)          # 本脚本在 backend/scripts/ 下
_SRC_ROOT = os.path.dirname(_BACKEND)      # DYAutoDM_v2
_CSS = os.path.join(_SRC_ROOT, "frontend", "src", "styles", "tokens.css")
# ⚠️ **不得** import `test_glass_blur_container`（★ 2026-10-03 实测踩到）：
#   门禁 `test_entry_module_identity_guard` 判「双栖入口」—— 测试模块一旦
#   被 scripts/ 里的脚本 import，就成了「既被 unittest 发现、又被脚本复用」的
#   双宿主，与 REG-01 / P0-4 的敞口同形。
#   正解：脚本**自持**解析逻辑（下方 `_rule_bodies`），与门禁那份**同源但独立**
#   （门禁是判据、脚本是工具，各自可执行；靠「约定相同」保持一致，
#   由 verify 脚本的输出与门禁交叉核对来防漂移）。

#: 宿主 → 期望的模糊值（实测自 0.46.39 之前的源码，视觉基线）
#: ⚠️ modal/popover 的模糊值**直写在伪元素**上；玻璃类则走
#:    `var(--glass-blur)`（真值存在宿主的自定义属性里）—— 两种形态都对，
#:    直写是为了「弹窗不依赖变量」，变量是为了「一处改到处生效 + 变体可覆盖」。
EXPECT_BLUR = {
    ".modal-surface": "blur(48px)",
    ".popover-surface": "blur(28px)",
}
#: 玻璃类：伪元素读 var(--glass-blur)，真值在宿主上
EXPECT_GLASS_BLUR = {
    ".glass-premium": "blur(48px)",
    ".glass-panel": "blur(24px)",
    ".card-surface": "blur(18px)",
}
#: 宿主 → 变体的期望值
EXPECT_BLUR_VARIANT = {
    "html:not([data-glass-frost]) .glass-panel": "blur(14px)",
    "html:not([data-glass-frost]) .glass-premium": "blur(14px)",
}


def _rule_bodies(src: str):
    """切出每个规则的 (选择器, 声明体)。与门禁那份同源但**独立实现**。

    ⚠️ 三个必做的处理（与 test_glass_blur_container._rule_bodies 一致）：
       ① 剥注释（注释会被并进「选择器」）；
       ② 排除 @keyframes 内部（0%,100%{transform} 是动画关键帧，非宿主）；
       ③ 逗号分组逐个拆开（.a,
.b { } 里每个选择器独立生效）。
    """
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    src = re.sub(r"@keyframes\s+[\w-]+\s*\{(?:[^{}]|\{[^{}]*\})*\}", "", src)
    out = []
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", src):
        body = m.group(2)
        for raw in m.group(1).split(","):
            sel = " ".join(raw.split())
            if sel and not sel.startswith("@") and not sel.startswith("from"):
                out.append((sel, body))
    return out


def _css() -> str:
    with io.open(_CSS, encoding="utf-8") as f:
        return f.read()


def _final_props(sel: str, prop: str):
    """按出现顺序取该选择器上 prop 的**最终**值（后者胜）。"""
    final = None
    for s, body in _rule_bodies(_css()):
        if s.strip() != sel:
            continue
        m = re.search(r"(?<![\w-])" + re.escape(prop) + r"\s*:\s*([^;]+)", body)
        if m:
            final = m.group(1).strip()
    return final


def main() -> int:
    fails = []
    print("=" * 68)
    print("玻璃模糊搬家 · 等价性验证")
    print("=" * 68)

    # ① 伪元素上的模糊值 = 期望值
    print("\n① 伪元素模糊值（视觉不退化）")
    for host, want in EXPECT_BLUR.items():
        pse = f"{host}::before"
        got = _final_props(pse, "backdrop-filter")
        ok = got == want
        print(f"   {'OK ' if ok else 'FAIL'} {pse:<42} {got}  (期望 {want})")
        if not ok:
            fails.append(f"{pse} 模糊值 {got} ≠ {want}")

    # ①b 玻璃类：伪元素读 var(--glass-blur)，真值在宿主上
    print("\n①b 玻璃类：伪元素读变量 + 宿主提供真值")
    for host, want in EXPECT_GLASS_BLUR.items():
        pse = f"{host}::before"
        got = _final_props(pse, "backdrop-filter")
        var = _final_props(host, "--glass-blur")
        ok = got == "var(--glass-blur)" and var == want
        print(f"   {'OK ' if ok else 'FAIL'} {pse:<42} {got} / "
              f"--glass-blur={var}  (期望 {want})")
        if not ok:
            fails.append(f"{pse} 玻璃值 {got}/{var} ≠ var()/{want}")

    for host, want in EXPECT_BLUR_VARIANT.items():
        var = _final_props(host, "--glass-blur")
        ok = var == want
        print(f"   {'OK ' if ok else 'FAIL'} {host:<42} --glass-blur={var}  "
              f"(期望 {want})")
        if not ok:
            fails.append(f"{host} 变体 --glass-blur={var} ≠ {want}")

    # ② 宿主自身无 backdrop-filter（不成为包含块）
    print("\n② 宿主自身无 backdrop-filter（不成为包含块）")
    all_hosts = list(EXPECT_BLUR) + list(EXPECT_GLASS_BLUR) + list(EXPECT_BLUR_VARIANT)
    for host in all_hosts:
        got = _final_props(host, "backdrop-filter")
        ok = got in (None, "none")
        print(f"   {'OK ' if ok else 'FAIL'} {host:<42} {got}")
        if not ok:
            fails.append(f"{host} 自身仍带 backdrop-filter={got}")

    # ③ 宿主无 position 声明（不覆盖 Tailwind 定位类）
    print("\n③ 宿主无 position 声明（不覆盖 Tailwind fixed）")
    for host in all_hosts:
        got = _final_props(host, "position")
        ok = got is None
        print(f"   {'OK ' if ok else 'FAIL'} {host:<42} {got}")
        if not ok:
            fails.append(f"{host} 声明了 position={got}（会覆盖 Tailwind）")

    # ④ 伪元素必备三件套
    print("\n④ 伪元素三件套（content / absolute / pointer-events）")
    for host in list(EXPECT_BLUR) + list(EXPECT_GLASS_BLUR):
        pse = f"{host}::before"
        c = _final_props(pse, "content")
        p = _final_props(pse, "position")
        pe = _final_props(pse, "pointer-events")
        ok = (c == '""') and p == "absolute" and pe == "none"
        print(f"   {'OK ' if ok else 'FAIL'} {pse:<42} "
              f"content={c} position={p} pointer-events={pe}")
        if not ok:
            fails.append(f"{pse} 三件套不全")

    print("\n" + "=" * 68)
    if fails:
        print(f"✗ 等价性验证失败（{len(fails)} 项）:")
        for f in fails:
            print("   -", f)
        return 1
    print("✓ 等价性验证通过：模糊值未退化、宿主不再是包含块、定位类未被覆盖")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
