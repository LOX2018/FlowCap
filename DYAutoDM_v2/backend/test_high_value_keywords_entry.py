# coding=utf-8
"""高价值关键词表「入口完备性」门禁（2026-09-29）。

## 要防住的缺陷（实测取证）

`services/high_value_keywords.py`（数据层，134 行 + 27 种子词）与消费点
`services/dm_dispatch.py:843`（在用）都存在，但 **API 与前端零暴露**
（全仓 grep：`high_value` 在 `backend/api/`、`main.py`、`frontend/src`
**均零命中**）⇒ 用户无法调整关键词权重。

这与 **H-26**（MCP 工具族「只交付能力层、丢呈现层」）是**同型故障**，
且**已是第三次出现**（H-30 `login_remote.py` 孤儿模块、H-26 MCP 呈现层、
本条关键词表）。故必须机械固化，不能只靠人工自查。

判据分三关（缺任何一关 ⇒ 入口不完整）：
  ① 数据层：service 模块存在且可读写
  ② 可达层：后端有 HTTP 端点（供前端调用）
  ③ 呈现层：前端有调用点（用户真能看见并操作）

教训依据：H-26 的验收标准「全是后端判据（serve 可启动/Bearer 生效），
无一条问『用户能否看见并使用』」⇒ 只交付能力层即判"完成"。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # 仓库根（DYAutoDM_v2/）
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend" / "src"
SERVICE = BACKEND / "services" / "high_value_keywords.py"
API_AI = BACKEND / "api" / "ai.py"
CLIENT = FRONTEND / "api" / "client.ts"
SECTION = FRONTEND / "components" / "settings" / "HighValueKeywordsSection.tsx"
SETTINGS_PAGE = FRONTEND / "components" / "settings" / "settings-page.tsx"
# 直播页链路（2026-10-01 · 台账 L-18）：live-page → HighValueKeywordsModal → Section
LIVE_PAGE = FRONTEND / "components" / "live" / "live-page.tsx"
MODAL = FRONTEND / "components" / "live" / "HighValueKeywordsModal.tsx"


def _src(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# ===========================================================================
# G1 · 数据层
# ===========================================================================

def test_g1_service_exists_and_has_seed():
    """G1：service 模块存在，且有种子词表与打分能力。"""
    assert SERVICE.is_file(), "high_value_keywords.py 不存在"
    s = _src(SERVICE)
    assert "DEFAULT_KEYWORDS" in s, "缺少种子词表 DEFAULT_KEYWORDS"
    assert "def score_text" in s, "缺少 score_text"
    assert "def put_keywords" in s, "缺少 put_keywords（写入口）"


def test_g2_empty_table_not_relazily_seeded():
    """G2（关键语义）：用户显式清空的空表**不得**被懒初始化塞回种子词。

    旧判据 `if not isinstance(raw, dict) or not raw` 会把 `{}` 当缺失
    ⇒ 用户永远无法清空（有 UI 入口后这是真实诉求）。现须以「键是否存在」
    （`raw is None`）为判据。

    判据用 AST 读**真实代码**（不能用字符串匹配 —— 本判据首版即在注释里
    复述的旧代码 `or not raw` 上误命中，属自造假红，实测踩到）。
    """
    import ast

    tree = ast.parse(_src(SERVICE))
    fn = None
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef) and n.name == "get_keywords":
            fn = n
            break
    assert fn is not None, "找不到 get_keywords"

    # 找到 `if <cond>:` 里对 raw 的判定，断言条件形如 `raw is None or ...`
    found_none_check = False
    uses_bare_truthiness = False
    for n in ast.walk(fn):
        if not isinstance(n, ast.If):
            continue
        seg = ast.get_source_segment(_src(SERVICE), n.test) or ""
        if "raw" not in seg:
            continue
        if "is None" in seg:
            found_none_check = True
        # 裸真值判断 `not raw` 出现在条件里 ⇒ 会把 {} 当缺失
        for sub in ast.walk(n.test):
            if (isinstance(sub, ast.UnaryOp) and isinstance(sub.op, ast.Not)
                    and isinstance(sub.operand, ast.Name)
                    and sub.operand.id == "raw"):
                uses_bare_truthiness = True

    assert found_none_check, "get_keywords 未以 `raw is None` 判定「从未初始化」"
    assert not uses_bare_truthiness, (
        "get_keywords 仍用裸真值判断 `not raw` ⇒ 空表被当缺失、用户无法清空")


# ===========================================================================
# G3-G4 · 可达层（后端 HTTP 端点）
# ===========================================================================

def test_g3_api_endpoints_exist():
    """G3：后端必须有读/写/重置三个端点（缺任一 ⇒ 入口不完整）。"""
    s = _src(API_AI)
    assert '"/high-value-keywords"' in s, "缺少 GET/POST /high-value-keywords"
    assert '"/high-value-keywords/reset"' in s, "缺少 reset 端点"
    assert "def hv_keywords_get" in s and "def hv_keywords_put" in s, \
        "端点处理函数缺失"


def test_g4_reset_actually_writes_seed():
    """G4：reset 必须**写回种子表**，不能只 invalidate 内存缓存。

    只 `invalidate()` 时下次 get 重读 kv 仍是旧值 ⇒ 假"重置"（实测语义陷阱）。
    """
    s = _src(API_AI)
    i = s.find("async def hv_keywords_reset")
    assert i > 0, "找不到 hv_keywords_reset"
    block = s[i: i + 600]
    assert "put_keywords" in block, "reset 未写回种子表（只清了缓存 ⇒ 假重置）"


# ===========================================================================
# G5-G6 · 呈现层（前端真能看见并操作）
# ===========================================================================

def test_g5_frontend_client_has_calls():
    """G5：前端 API 客户端必须有三个方法的调用点。"""
    s = _src(CLIENT)
    for name in ("aiHighValueKeywords(", "aiHighValueKeywordsSave(",
                 "aiHighValueKeywordsReset("):
        assert name in s, f"client.ts 缺少 {name}"


# ---------------------------------------------------------------------------
# G6 · 判定纯函数（可被负控直接喂源码文本，无需动真实文件）
# ---------------------------------------------------------------------------

#: JSX 元素开标签（`<Xxx` / `< Xxx`），**不含** import / 注释里出现的裸标识符
_JSX_SECTION = re.compile(r"<\s*HighValueKeywordsSection\b")
_JSX_MODAL = re.compile(r"<\s*HighValueKeywordsModal\b")


def _section_is_mounted(settings_src: str, live_src: str, modal_src: str) -> bool:
    """关键词权重表 Section 是否真实挂载在「设置页」**或**「直播页」。

    判据（两分支任一成立即通过；两分支都要求**JSX 真实渲染**，只 import 不算）：

    · 设置页分支：`settings-page.tsx` 的 JSX 里出现 `<HighValueKeywordsSection`
    · 直播页分支（**两跳**，缺一跳即不算）：
        ① `live-page.tsx` 的 JSX 里出现 `<HighValueKeywordsModal`
        ② `HighValueKeywordsModal.tsx` 的 JSX 里出现 `<HighValueKeywordsSection`

    抽成纯函数是为了让负控能**在内存里**构造"摘掉挂载"的源码文本喂进来，
    证明门禁真会红（而不是把负控写在注释里）。
    """
    if bool(_JSX_SECTION.search(settings_src)):
        return True
    return bool(_JSX_MODAL.search(live_src)) and bool(_JSX_SECTION.search(modal_src))


def _drop_lines_matching(src: str, pattern: re.Pattern) -> str:
    """按行摘掉命中 pattern 的行（用于负控构造「未挂载」的源码样本）。"""
    return "\n".join(ln for ln in src.splitlines() if not pattern.search(ln))


def test_g6_section_mounted_in_settings_or_live():
    """G6（决定性）：组件必须**真实挂载**到设置页**或**直播页 —— 光有组件文件等于不存在。

    H-26 的教训：能力层交付但未挂载 ⇒ 用户零感知。

    ## 为什么判据从「只在设置页」改成「设置页 **或** 直播页」（2026-10-01 · 台账 L-18）

    提交 `7592727`「高价值关键词权重表**只保留直播页入口**（消除设置页重复入口）」
    是**有意重构**，不是缺陷：设置页的入口按钮会跳到直播页弹窗，保留两处会形成
    重复入口。而本门禁（源自更早的 `8c7436e`）没跟着改 ⇒ 门禁过时报错。

    有效入口链（实测）：
        `live-page.tsx:705` 渲染 `<HighValueKeywordsModal open={kwOpen} … />`
        → `HighValueKeywordsModal.tsx:68` 渲染 `<HighValueKeywordsSection embedded />`

    因此判据改为「设置页 **或** 直播页任一挂载即可」，且直播页分支必须**两跳都真**
    （只认 import 字符串会假绿 —— 见负控 `test_g6_negative_control_...`）。

    **不删除本门禁**的原因：该组件确实必须对用户可见，只是换了宿主页；
    换成"删门禁"等于放弃 H-26 的机械防线。
    """
    assert SECTION.is_file(), "HighValueKeywordsSection.tsx 不存在"
    assert LIVE_PAGE.is_file(), "live-page.tsx 不存在"
    assert MODAL.is_file(), "HighValueKeywordsModal.tsx 不存在"

    assert _section_is_mounted(_src(SETTINGS_PAGE), _src(LIVE_PAGE), _src(MODAL)), (
        "高价值关键词表在设置页与直播页**都未挂载**：\n"
        "  ① settings-page.tsx 的 JSX 中无 <HighValueKeywordsSection\n"
        "  ② 或 live-page.tsx 的 JSX 中无 <HighValueKeywordsModal\n"
        "  ③ 或 HighValueKeywordsModal.tsx 的 JSX 中无 <HighValueKeywordsSection\n"
        "⇒ 用户看不见（H-26 同型缺口）"
    )


def test_g6_negative_control_unmounted_everywhere_turns_red():
    """G6 负控：把挂载从**两处**都摘掉，门禁必须变红（否则门禁是摆设）。

    全部在内存中构造源码样本喂给同一个纯函数 `_section_is_mounted`，
    **不碰真实文件**（避免与并行工作线争文件）。

    三条断言：
      · 真实三份源码 ⇒ True（基线，证明门禁当前是绿的）
      · 摘掉 live-page 的 `<HighValueKeywordsModal …/>` ⇒ False
      · 摘掉 modal 的 `<HighValueKeywordsSection …/>` ⇒ False
      · 两处都摘 ⇒ False（"两处都摘掉必须变红"的可执行形态）
    """
    settings_src = _src(SETTINGS_PAGE)
    live_src = _src(LIVE_PAGE)
    modal_src = _src(MODAL)

    # 基线：真实源码必须绿，否则下面的负控没有意义
    assert _section_is_mounted(settings_src, live_src, modal_src) is True, \
        "基线失败：真实源码都没判绿，负控无从谈起"

    # 负控 A：摘掉 live-page 里的 <HighValueKeywordsModal …/>
    live_no_modal = _drop_lines_matching(live_src, _JSX_MODAL)
    assert live_no_modal != live_src, "负控 A 没摘掉任何行（自造假绿）"
    assert _section_is_mounted(settings_src, live_no_modal, modal_src) is False, \
        "负控 A 失败：摘掉 live-page 的 Modal 挂载后门禁仍绿 ⇒ 判据不判直播页那一跳"

    # 负控 B：摘掉 modal 里的 <HighValueKeywordsSection …/>
    modal_no_section = _drop_lines_matching(modal_src, _JSX_SECTION)
    assert modal_no_section != modal_src, "负控 B 没摘掉任何行（自造假绿）"
    assert _section_is_mounted(settings_src, live_src, modal_no_section) is False, \
        "负控 B 失败：摘掉 Modal 内的 Section 挂载后门禁仍绿 ⇒ 判据不判第二跳"

    # 负控 C：两处都摘 ⇒ 必须红
    assert _section_is_mounted(settings_src, live_no_modal, modal_no_section) is False, \
        "负控 C 失败：两处挂载都摘掉后门禁仍绿 ⇒ 门禁完全不设防"

    # 正控：设置页分支仍有效（避免将来只留直播页分支而把设置页分支写死成 False）
    settings_mounted = settings_src + "\n<HighValueKeywordsSection />\n"
    assert _section_is_mounted(settings_mounted, live_no_modal, modal_no_section) is True, \
        "正控失败：设置页 JSX 挂载后仍判红 ⇒ 设置页分支失效"

    # 判别力：光有 import、没有 JSX ⇒ 必须红（防"字符串命中即绿"的假绿）
    live_import_only = "import HighValueKeywordsModal from \"./HighValueKeywordsModal\";\n"
    assert _section_is_mounted(settings_src, live_import_only, modal_src) is False, \
        "判别力失败：live-page 只有 import 无 JSX 却判绿 ⇒ 判据退化为字符串匹配"
