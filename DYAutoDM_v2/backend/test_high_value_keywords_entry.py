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
    """关键词权重表 Section 是否**真实挂载**（判据：设置页 JSX）。

    ## 判据沿革
      · 初版（`8c7436e`）：只在设置页挂载 ⇒ 判据看 settings-page。
      · 2026-10-01（台账 L-18）：入口迁到直播页弹窗 ⇒ 放宽为「设置页 **或**
        直播页两跳」。
      · **2026-10-04（用户指令「移除直播页入口」）**：入口收敛回**设置页**
        （配置中心 → 采集策略 页直接挂载 `<HighValueKeywordsSection />`），
        直播页链路（live-page → Modal → Section）**已下线**。
        ⇒ 判据收敛为「设置页分支」，且**直播页不得再有入口**（见 G6b）。

    保留 live_src / modal_src 入参是为了让负控能继续构造样本，
    以及将来若再迁入口时判据可以平滑演进。
    """
    return bool(_JSX_SECTION.search(settings_src))


def _drop_lines_matching(src: str, pattern: re.Pattern) -> str:
    """按行摘掉命中 pattern 的行（用于负控构造「未挂载」的源码样本）。"""
    return "\n".join(ln for ln in src.splitlines() if not pattern.search(ln))


def test_g6_section_mounted_in_settings():
    """G6（决定性）：组件必须**真实挂载**到设置页 —— 光有组件文件等于不存在。

    H-26 的教训：能力层交付但未挂载 ⇒ 用户零感知。

    ## 判据沿革（勿再写回「或直播页」）
      · `8c7436e` 初版：只在设置页挂载。
      · 2026-10-01（台账 L-18）：入口迁到直播页弹窗 ⇒ 放宽为「设置页或直播页」。
      · **2026-10-04（用户指令「移除直播页入口」）**：入口收敛回设置页
        （配置中心 → 采集策略 页直接挂载），直播页链路已下线
        ⇒ 判据收敛为**只认设置页**，并由 G6b 断言直播页不得再有入口。

    **不删除本门禁**的原因：该组件必须对用户可见；删门禁 = 放弃 H-26 的机械防线。
    """
    assert SECTION.is_file(), "HighValueKeywordsSection.tsx 不存在"
    assert SETTINGS_PAGE.is_file(), "settings-page.tsx 不存在"

    assert _section_is_mounted(_src(SETTINGS_PAGE), "", ""), (
        "高价值关键词表在设置页**未挂载**（settings-page.tsx 的 JSX 中无 "
        "<HighValueKeywordsSection）⇒ 用户看不见（H-26 同型缺口）"
    )


def _strip_comments(src: str) -> str:
    """剥掉 JSX 块注释与 `//` 行注释，供「不得出现某标识」类判据使用。

    🔴 为什么必须剥（本仓已踩两次）：源码契约类判据的模式串一旦出现在**注释**里
    （例如本文件刻意留下的「入口已下线」说明），`in` / `grep -c` 会把注释算作命中
    ⇒ 门禁与文档自相矛盾、且**判据当场失效**。G6b 首版正是如此误报。
    """
    src = re.sub(r"\{/\*.*?\*/\}", "", src, flags=re.S)
    src = re.sub(r"(?m)^\s*//.*$", "", src)
    return src


def test_g6b_live_page_entry_removed():
    """G6b（2026-10-04 用户指令「移除直播页入口」）：直播页**不得**再有入口。

    判据：剥掉注释后，`live-page.tsx` 里不得出现 `<HighValueKeywordsModal`，
    也不得再有 `live-high-value-keywords` 按钮锚点。

    ⚠️ **必须先剥注释**：live-page 刻意留了「入口已下线」的说明注释（其中会
    提及这两个标识符）。不剥注释会让门禁与文档自相矛盾（G6b 首版实测误报）。
    """
    live = _strip_comments(_src(LIVE_PAGE))
    assert not _JSX_MODAL.search(live), (
        "live-page.tsx 仍有 <HighValueKeywordsModal 挂载 ⇒ 直播页入口未移除"
        "（用户 2026-10-04 要求单一入口：配置中心 → 采集策略）"
    )
    assert "live-high-value-keywords" not in live, (
        "live-page.tsx 仍残留 live-high-value-keywords 锚点（入口按钮未删干净）"
    )


def test_g6_negative_control_unmounted_turns_red():
    """G6 负控：摘掉设置页挂载后门禁必须变红（否则门禁是摆设）。

    全部在内存中构造源码样本喂给同一个纯函数 `_section_is_mounted`，
    **不碰真实文件**（避免与并行工作线争文件）。

    断言：
      · 真实设置页源码 ⇒ True（基线，证明门禁当前是绿的）
      · 摘掉设置页的 `<HighValueKeywordsSection …/>` ⇒ False
      · 只有 import、没有 JSX ⇒ False（防「字符串命中即绿」的假绿）
      · **回归防护**：即便直播页弹窗链路完整，设置页缺失也必须判红
        （这正是 2026-10-04 判据收敛要守住的性质）
    """
    settings_src = _src(SETTINGS_PAGE)
    live_src = _src(LIVE_PAGE)
    modal_src = _src(MODAL) if MODAL.is_file() else ""

    # 基线：真实源码必须绿，否则下面的负控没有意义
    assert _section_is_mounted(settings_src, live_src, modal_src) is True, \
        "基线失败：真实源码都没判绿，负控无从谈起"

    # 负控 A：摘掉设置页挂载 ⇒ 必须红
    settings_no_section = _drop_lines_matching(settings_src, _JSX_SECTION)
    assert settings_no_section != settings_src, "负控 A 没摘掉任何行（自造假绿）"
    assert _section_is_mounted(settings_no_section, live_src, modal_src) is False, \
        "负控 A 失败：摘掉设置页挂载后门禁仍绿 ⇒ 判据没在守设置页"

    # 判别力：光有 import、没有 JSX ⇒ 必须红
    import_only = 'import HighValueKeywordsSection from "./HighValueKeywordsSection";\n'
    assert _section_is_mounted(import_only, live_src, modal_src) is False, \
        "判别力失败：只有 import 无 JSX 却判绿 ⇒ 判据退化为字符串匹配"

    # 回归防护：直播页链路完整也不能替代设置页挂载
    if modal_src:
        assert _section_is_mounted(settings_no_section, live_src, modal_src) is False, \
            "回归失败：直播页链路完整就判绿 ⇒ 判据又退回「或直播页」了"

    # 正控：设置页分支仍有效（避免将来把设置页分支写死成 False）
    settings_mounted = settings_no_section + "\n<HighValueKeywordsSection />\n"
    assert _section_is_mounted(settings_mounted, live_src, modal_src) is True, \
        "正控失败：设置页 JSX 挂载后仍判红 ⇒ 设置页分支失效"
