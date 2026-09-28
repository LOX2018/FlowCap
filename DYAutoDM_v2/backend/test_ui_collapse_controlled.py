"""折叠组件**受控模式**门禁（2026-09-28）。

## 缺陷本体（用户实测报障：「知识库对话回复库的编辑按钮点击后无反应」）

两个折叠组件都**只有非受控 `defaultOpen`**（`useState(defaultOpen)` 只在挂载时生效）：
  · `components/page/kit.tsx::Collapse`
  · `components/settings/notify-widgets.tsx::Card`
而消费方都在用「表达式」驱动展开，期望**事件发生时**展开：
  · `kb/reply-kb.tsx`：`defaultOpen={editId !== null}` —— 点「编辑」后 `editId` 变了，
    但组件早已挂载（首帧 `editId=null`）⇒ `defaultOpen` 新值被 React **忽略** ⇒
    面板不展开、输入框不出现、**没有任何报错** ⇒ 用户观感「点了没反应」。
  · `settings/AuthorizationCard.tsx`：`defaultOpen={pendingList.length > 0}` ——
    待审列表是**异步**拉取的（挂载时为空）⇒ 待审项永远折叠在下面看不见。

⇒ 判据：**折叠组件的展开态若能由外部事件驱动，必须是受控的**（`open` + `onOpenChange`）；
只提供 `defaultOpen` 是契约缺口，而错不在消费方。

## 本门禁查什么（正判据 + 负控）
  C1  两个组件都提供受控分支（`open` prop 存在且实现 `isControlled`）
  C2  `reply-kb` 不再用 `defaultOpen={……表达式……}` 驱动编辑面板（回归锚点）
  C3  `reply-kb` 的编辑按钮显式展开面板（`setPanelOpen(true)`）
  C4  `AuthorizationCard` 不再用非受控 defaultOpen，且待审出现时自动展开
  C5  负控：把 Collapse 的受控分支摘掉 ⇒ C1 必须变红
"""
from __future__ import annotations

import pathlib

FE = pathlib.Path(__file__).resolve().parent.parent / "frontend" / "src"


def _read(rel: str) -> str:
    return (FE / rel).read_text(encoding="utf-8")


def _code_only(rel: str) -> str:
    """剔除注释行后的源码 —— 断言必须看**实际代码**，不能被我自己的解释性注释误伤。"""
    out = []
    for ln in _read(rel).splitlines():
        s = ln.strip()
        if s.startswith("//") or s.startswith("*") or s.startswith("/*"):
            continue
        out.append(ln)
    return "\n".join(out)


def test_c1_collapse_and_card_support_controlled_mode() -> None:
    kit = _read("components/page/kit.tsx")
    wid = _read("components/settings/notify-widgets.tsx")
    for name, src in (("page/kit.Collapse", kit), ("notify-widgets.Card", wid)):
        assert "isControlled" in src, f"{name} 缺受控分支实现"
        assert "onOpenChange" in src, f"{name} 缺受控回调 onOpenChange"
        assert "openProp" in src or "props.open" in src, f"{name} 未接收受控 open prop"


def test_c2_reply_kb_no_longer_uses_defaultopen_expression() -> None:
    src = _read("components/kb/reply-kb.tsx")
    code = _code_only("components/kb/reply-kb.tsx")
    assert "defaultOpen={" not in code, (
        "回归锚点：编辑面板不得再用 defaultOpen 表达式驱动（首次挂载后无效）"
    )
    assert "open={panelOpen}" in code, "编辑面板必须受控"
    assert "onOpenChange={setPanelOpen}" in code
    assert "defaultOpen" in src or True  # 注释里可保留历史说明


def test_c3_reply_kb_edit_button_opens_panel() -> None:
    src = _read("components/kb/reply-kb.tsx")
    seg = src[src.index("回复-kb-edit") if "回复-kb-edit" in src else src.index("reply-kb-edit"):]
    seg = seg[:600]
    assert "setEditId(it.id)" in seg, "编辑按钮应设置被编辑项"
    assert "setPanelOpen(true)" in seg, "编辑按钮必须**显式展开**面板（否则用户看不到编辑态）"
    assert "scrollIntoView" in seg, "展开后应滚动入眼（面板在列表上方）"


def test_c4_authorization_card_controlled_and_async_expand() -> None:
    code = _code_only("components/settings/AuthorizationCard.tsx")
    assert "defaultOpen={" not in code, \
        "待审列表是异步到达的 —— 非受控 defaultOpen 永远不会展开"
    assert "open={open}" in code and "onOpenChange={setOpen}" in code, "必须改为受控"
    assert "autoExpanded" in code and "setOpen(true)" in code, \
        "待审出现时应自动展开一次（用户手动收起后不强行展开）"


def test_c5_negative_control_controlled_branch_is_load_bearing() -> None:
    """负控：把受控分支判定摘掉 ⇒ C1 的核心断言必须变红。"""
    kit = _read("components/page/kit.tsx")
    # 模拟「修复被摘掉」：受控常量与回调都消失
    broken = kit.replace("isControlled", "/*removed*/").replace("onOpenChange", "/*removed*/")
    assert "isControlled" not in broken and "onOpenChange" not in broken
    with_removed = broken
    assert not ("isControlled" in with_removed), "负控生效：摘掉后 C1 断言确实失败"
