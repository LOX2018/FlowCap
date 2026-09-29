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


def test_g6_section_mounted_in_settings():
    """G6（决定性）：组件必须**挂进设置页** —— 光有组件文件等于不存在。

    H-26 的教训：能力层交付但未挂载 ⇒ 用户零感知。
    """
    assert SECTION.is_file(), "HighValueKeywordsSection.tsx 不存在"
    page = _src(SETTINGS_PAGE)
    assert "HighValueKeywordsSection" in page, (
        "组件未在 settings-page 中 import/挂载 ⇒ 用户看不见（H-26 同型缺口）")
    # 必须真的出现在 JSX 里（不只是 import）
    assert re.search(r"<\s*HighValueKeywordsSection\s*/?>", page), (
        "HighValueKeywordsSection 只被 import、未在 JSX 中渲染")
